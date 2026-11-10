"""Campagne native bornée : cas réels, propriétés et stockage/entraînement.

Cette campagne sépare les maillages acceptés, les refus et les données
synthétiques. Elle ne constitue ni une certification industrielle ni une
validation mécanique. Les calculs sont réalisés par le noyau Rust.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from atelier.native import core_path, gmsh_library
from mailleur.io import write_json


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def all_finite(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(all_finite(x) for x in value.values())
    if isinstance(value, list):
        return all(all_finite(x) for x in value)
    return True


def scaled_fixture(project, unit):
    """Independent test fixture: describe the same physical CAD in m or mm."""
    result = copy.deepcopy(project)
    if unit == "mm":
        return result
    if unit != "m" or project["units"]["input"] != "mm":
        raise ValueError("La fixture ne convertit que mm vers m")
    factor = 0.001
    geometry = result["geometry"]
    for key in ("width", "height", "depth", "thickness", "radius", "wall",
                "corner_radius", "chamfer", "throughhole_radius"):
        if key in geometry:
            geometry[key] *= factor
    if geometry["kind"] == "plate":
        for hole in geometry.get("holes", []):
            for key in ("x", "y", "radius"):
                hole[key] *= factor
    if geometry["kind"] == "polygon":
        for ring in [geometry["outer"], *geometry.get("holes", [])]:
            for point in ring:
                point[:] = [x * factor for x in point]
    for key in ("size", "min_size"):
        result["mesh"][key] *= factor
    result["repair"]["tolerance"] *= factor
    for local in result.get("local_sizes", []):
        local["size"] *= factor
    for region in result.get("refinement_regions", []):
        region["center"] = [x * factor for x in region["center"]]
        for key in ("radius", "size"):
            region[key] *= factor
    for zone in result.get("critical_zones", []):
        zone["buffer"] *= factor
    result["units"]["input"] = unit
    return result


def quality_signature(report):
    quality = report["quality"]
    return {"nodes": report["nodes"], "elements": report["elements"],
            "cell_counts": report["cell_counts"], "minimum": quality["minimum"],
            "mean": quality["mean"], "accepted": quality["accepted"],
            "bounds": report["geometry_diagnostics"]["bounds"],
            "bounds_approximate": report["geometry_diagnostics"].get("bounds_approximate", False),
            "bounds_relative_deflection": report["geometry_diagnostics"].get("bounds_tessellation", {}).get("relative_linear_deflection", 0.) if report["geometry_diagnostics"].get("bounds_tessellation") else 0.,
            "target_size_internal": report["mesh_settings"]["size"] * report["units"]["input_to_internal"],
            "minimum_size_internal": report["mesh_settings"]["min_size"] * report["units"]["input_to_internal"]}


def check_quality(report):
    if not all_finite(report):
        raise AssertionError("Un nombre non fini apparaît dans le rapport")
    quality = report["quality"]
    if not (report["nodes"] > 0 and report["elements"] > 0
            and quality["evaluated_elements"] == report["elements"]
            and quality["invalid_elements"] == 0
            and 0 < quality["minimum"] <= 1.000001
            and 0 < quality["min_jacobian_determinant"]):
        raise AssertionError("Comptage ou qualité numérique incohérent")
    if quality["accepted"] and quality["violations"]:
        raise AssertionError("Une acceptation contredit les violations de qualité")
    return quality_signature(report)


def same_physics(a, b):
    if any(a[key] != b[key] for key in ("nodes", "elements", "cell_counts", "accepted")):
        return False
    if any(not math.isclose(a[key], b[key], rel_tol=1e-10, abs_tol=1e-12)
           for key in ("minimum", "mean")):
        return False
    return all(math.isclose(x, y, rel_tol=1e-10, abs_tol=1e-12)
               for row_a, row_b in zip(a["bounds"], b["bounds"])
               for x, y in zip(row_a, row_b))


def same_physical_geometry_and_size(a, b):
    """A unit change must preserve physical input, not one unstructured mesh.

    OCC/Gmsh can select different unstructured meshes after floating-point
    unit changes. Counts and quality are retained as sensitivity observations;
    each mesh is independently subject to the complete native quality checks.
    """
    # Tessellation extrema are approximate, with a reported default relative
    # linear deflection of 0.001. This regression allowance is four deflections
    # of the model extent; it is not an exact envelope guarantee for arbitrary
    # CAD. Target/minimum sizes remain checked at floating-point precision.
    bbox_tolerance = bounds_comparison_tolerance(a, b)
    same_bounds = all(math.isclose(x, y, rel_tol=1e-10, abs_tol=bbox_tolerance)
                      for row_a, row_b in zip(a["bounds"], b["bounds"])
                      for x, y in zip(row_a, row_b))
    same_sizes = all(math.isclose(a[key], b[key], rel_tol=1e-10, abs_tol=1e-12)
                     for key in ("target_size_internal", "minimum_size_internal"))
    return same_bounds and same_sizes


def bounds_comparison_tolerance(a, b):
    extent = max(box[1][d] - box[0][d] for box in (a["bounds"], b["bounds"]) for d in range(3))
    deflection = max(row.get("bounds_relative_deflection", 0.)
                     if row.get("bounds_approximate") else 0. for row in (a, b))
    return max(1e-12, 4. * deflection * extent)


def native_rejection_kind(expected_invalid, measurement, message):
    """Keep expected bad inputs, invalid meshes, optimizer errors and crashes distinct."""
    if measurement.get("timed_out"):
        return "native_time_budget_exceeded"
    if measurement.get("exit_code", 0) < 0:
        return "native_crashed"
    if expected_invalid:
        return "geometry_rejected_expected"
    if "Éléments inversés ou dégénérés détectés" in message:
        return "invalid_elements_rejected"
    if "Optimisation quadratique Gmsh refusée" in message:
        return "optimizer_rejected"
    return "native_rejected_review"


class NativeFailure(RuntimeError):
    def __init__(self, message, measurement=None):
        super().__init__(message)
        self.measurement = measurement or {}


class Campaign:
    def __init__(self, output):
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.core = core_path()
        self.library = gmsh_library()
        self.counter = 0
        self.started = time.monotonic()
        self.result = {"schema": "atelier.extended_core_validation.v1",
                       "started_utc": datetime.now(timezone.utc).isoformat(),
                       "environment": {"platform": platform.platform(), "python": platform.python_version(),
                                       "core": subprocess.check_output([self.core, "--version"], text=True).strip(),
                                       "core_sha256": digest(self.core)},
                       "scope": "Bounded native integration; real CAD meshes and synthetic capacity fixtures remain distinct",
                       "sections": {}, "invariant_failures": []}

    def save(self):
        self.result["seconds"] = time.monotonic() - self.started
        write_json(self.output / "summary.json", self.result)

    def operation(self, label, request, *, timeout=90, rss=False):
        self.counter += 1
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:90]
        directory = self.output / "operations" / f"{self.counter:05}-{name}"
        directory.mkdir(parents=True)
        payload = dict(request, output_dir=str(directory / "native"), response_mode="summary")
        if request["operation"] in ("mesh", "inspect", "remesh") or "source_mesh" in request:
            payload["gmsh_library"] = str(self.library)
        path = write_json(directory / "request.json", payload)
        command = [str(self.core), "--request", str(path)]
        if rss:
            # macOS `time -l` also calls sysctl(kern.clockrate), which can be
            # forbidden in a sandbox after a perfectly successful child run.
            # A fresh lightweight parent has exactly one Rust child, so its
            # RUSAGE_CHILDREN maximum describes that native process alone.
            shim = ("import resource,subprocess,sys; "
                    "r=subprocess.run(sys.argv[1:]); "
                    "v=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss; "
                    "print('ATELIER_CHILD_MAX_RSS_BYTES='+str(v if sys.platform=='darwin' else v*1024),file=sys.stderr); "
                    "print('ATELIER_CHILD_EXIT_CODE='+str(r.returncode),file=sys.stderr); "
                    "sys.exit(r.returncode)")
            command = [sys.executable, "-c", shim, *command]
        started = time.monotonic()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=os.name != "nt")
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            (directory / "stdout.txt").write_text(stdout)
            (directory / "stderr.txt").write_text(stderr)
            measure = {"seconds": time.monotonic() - started,
                       "directory": str(directory.relative_to(self.output)),
                       "exit_code": process.returncode, "timed_out": True,
                       "timeout_seconds": timeout, "termination_source": "verification_harness"}
            write_json(directory / "measurement.json", measure)
            raise NativeFailure(f"Délai maximal de {timeout}s atteint", measure)
        elapsed = time.monotonic() - started
        (directory / "stdout.txt").write_text(stdout)
        (directory / "stderr.txt").write_text(stderr)
        measure = {"seconds": elapsed, "directory": str(directory.relative_to(self.output)),
                   "exit_code": process.returncode}
        match = re.search(r"ATELIER_CHILD_MAX_RSS_BYTES=(\d+)", stderr)
        if match:
            measure["max_rss_bytes"] = int(match[1])
            measure["rss_scope"] = "Rust child alone, measured by a fresh parent via RUSAGE_CHILDREN"
        child_exit = re.search(r"ATELIER_CHILD_EXIT_CODE=(-?\d+)", stderr)
        if child_exit:
            measure["wrapper_exit_code"] = measure["exit_code"]
            measure["exit_code"] = int(child_exit[1])
        write_json(directory / "measurement.json", measure)
        if measure["exit_code"]:
            raise NativeFailure(stderr[-6000:] or f"Code de sortie {process.returncode}", measure)
        report = json.loads((directory / "native/report.json").read_text())
        return report, measure

    def matrix(self, seeds):
        result = {"real_mesh_generations": True, "seeds": seeds, "orders": [1, 2],
                  "units": ["mm", "m"], "mode": "report_only avoids bulky artifacts, meshes are actually generated",
                  "cases": [], "unit_comparisons": [], "determinism": []}
        self.result["sections"]["matrix"] = result
        references = []
        for seed in seeds:
            corpus = self.output / "plans" / f"matrix-{seed}"
            self.operation(f"plan-{seed}", {"operation": "corpus_plan", "corpus_dir": str(corpus),
                                          "plan": {"structured": 24, "random": 24, "seed": seed, "strategies": 1}})
            start = 0 if seed == 42 else 24
            preview, _ = self.operation(f"preview-{seed}", {"operation": "corpus_preview", "corpus_dir": str(corpus),
                                                          "start_index": start, "limit": 24})
            for case in preview["cases"]:
                for order in (1, 2):
                    pair = {}
                    for unit in ("mm", "m"):
                        project = scaled_fixture(case["project"], unit)
                        project["mesh"].update(order=order, threads=1)
                        label = f"{seed}-{case['family']}-o{order}-{unit}"
                        row = {"seed": seed, "family": case["family"], "origin": case["origin"],
                               "split": case["split"], "mode": project["mode"], "order": order, "unit": unit,
                               "expected_invalid": case["expected_invalid"]}
                        try:
                            report, measure = self.operation(label, {"operation": "mesh", "project": project, "report_only": True})
                            signature = check_quality(report)
                            row.update(measure, signature=signature,
                                       outcome="accepted" if signature["accepted"] else "quality_rejected")
                            pair[unit] = signature
                            if case["expected_invalid"]:
                                row["outcome"] = "invalid_geometry_unexpectedly_meshed"
                                self.result["invariant_failures"].append({"case": label, "error": row["outcome"]})
                            if unit == "mm" and order == 1 and seed == seeds[0] and case["family_index"] % 3 == 0:
                                references.append((label, project, signature))
                        except NativeFailure as error:
                            crashed=error.measurement.get("exit_code",0)<0 and not error.measurement.get("timed_out")
                            row.update(error.measurement,
                                       outcome=native_rejection_kind(case["expected_invalid"], error.measurement, str(error)),
                                       error=str(error))
                            if error.measurement.get("timed_out"):
                                self.result["invariant_failures"].append({"case": label, "error": "native_time_budget_exceeded", **error.measurement})
                            if crashed:
                                self.result["invariant_failures"].append({"case":label,"error":"native_crashed",**error.measurement})
                        except (AssertionError, KeyError, TypeError, ValueError) as error:
                            row.update(outcome="invariant_failure", error=str(error))
                            self.result["invariant_failures"].append({"case": label, "error": str(error)})
                        result["cases"].append(row)
                    if len(pair) == 2:
                        equal = same_physical_geometry_and_size(pair["mm"], pair["m"])
                        result["unit_comparisons"].append({"seed": seed, "family": case["family"], "order": order,
                                                           "same_physical_geometry_and_target_sizes": equal,
                                                           "bounds_absolute_comparison_tolerance": bounds_comparison_tolerance(pair["mm"], pair["m"]),
                                                           "same_mesh_counts_and_quality":same_physics(pair["mm"],pair["m"]),
                                                           "same_acceptance_decision":pair["mm"]["accepted"]==pair["m"]["accepted"]})
                        if not equal:
                            self.result["invariant_failures"].append({"case": label, "error": "unit_invariance"})
                result["outcomes"] = dict(Counter(row["outcome"] for row in result["cases"]))
                self.save()
                if len(result["cases"]) % 24 == 0:
                    print("matrix", len(result["cases"]), result["outcomes"], flush=True)
        for label, project, signature in references:
            report, measure = self.operation(label + "-repeat", {"operation": "mesh", "project": project, "report_only": True})
            same = quality_signature(report) == signature
            result["determinism"].append({"case": label, "exact_report_signature": same, **measure})
            if not same:
                self.result["invariant_failures"].append({"case": label, "error": "determinism"})
        self.save()

    def properties(self):
        rng = random.Random(20261107)
        result = {"scope": "Synthetic mesh connectivity fixtures, no industrial CAD claim", "checks": []}
        self.result["sections"]["properties"] = result
        fixtures = []
        tetra = [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
        shapes = {
            "triangle": (tetra[:3], 3), "quad": ([[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]],4),
            "tetra": (tetra,4),
            "hexahedron": ([[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.],[0.,0.,1.],[1.,0.,1.],[1.,1.,1.],[0.,1.,1.]],8),
            "wedge": (tetra[:3]+[[0.,0.,1.],[1.,0.,1.],[0.,1.,1.]],6),
            "pyramid": ([[-1.,-1.,0.],[1.,-1.,0.],[1.,1.,0.],[-1.,1.,0.],[0.,0.,1.]],5),
        }
        variants = {"triangle": [3,6], "quad":[4,8,9], "tetra":[4,10],
                    "hexahedron":[8,20,27], "wedge":[6,15,18], "pyramid":[5,13,14]}
        for name, (corners, primary) in shapes.items():
            for width in variants[name]:
                points = copy.deepcopy(corners)
                while len(points)<width:
                    points.append([sum(p[axis] for p in corners)/primary + (len(points)-primary)*0.001 for axis in range(3)])
                kind = name if width == primary else name + str(width)
                fixtures.append((kind, {"schema":"atelier.mesh_import.v1", "points": points,
                                       "cells":[{"type":kind,"data":[list(range(width))]}]}, True))
        for index in range(24):
            angle=rng.uniform(-math.pi,math.pi); c=math.cos(angle); s=math.sin(angle)
            scale=10**rng.uniform(-3,3); mirror=-1 if index%3==0 else 1
            transform=[c*scale*mirror,-s*scale,0.,rng.uniform(-5,5),s*scale*mirror,c*scale,0.,rng.uniform(-5,5),
                       0.,0.,scale,rng.uniform(-5,5),0.,0.,0.,1.]
            fixtures.append((f"affine-tetra-{index}", {"schema":"atelier.mesh_import.v1", "objects":[{
                "points":tetra,"cells":[{"type":"tetra","data":[[0,1,2,3]]}],"transforms":[transform]}]}, True))
        bad = [
            ("inverted-tetra", tetra, "tetra", [[0,2,1,3]]),
            ("duplicate-tetra", tetra, "tetra", [[0,1,2,3],[0,1,2,3]]),
            ("bad-index", tetra, "tetra", [[0,1,2,99]]),
            ("unknown-polyhedron", tetra, "polyhedron", [[0,1,2,3]]),
            ("degenerate-triangle", [[0.,0.,0.],[1.,0.,0.],[2.,0.,0.]], "triangle", [[0,1,2]]),
            ("crossed-quad", shapes["quad"][0], "quad", [[0,1,3,2]]),
            ("edge-nonmanifold", tetra+[[0.,-1.,0.]], "triangle", [[0,1,2],[1,0,3],[0,1,4]]),
            ("vertex-nonmanifold", tetra+[[0.,-1.,0.]], "triangle", [[0,1,2],[0,3,4]]),
        ]
        for name, points, kind, data in bad:
            fixtures.append((name,{"schema":"atelier.mesh_import.v1","points":points,"cells":[{"type":kind,"data":data}]},False))
        mixed_points=tetra+[[0.,0.,-1.]]+[[p[0]+3,p[1],p[2]] for p in shapes["hexahedron"][0]]
        fixtures.append(("mixed-tetra-hexa",{"schema":"atelier.mesh_import.v1","points":mixed_points,
            "cells":[{"type":"tetra","data":[[0,1,2,3],[0,2,1,4]]},
                     {"type":"hexahedron","data":[list(range(5,13))]}]},True))
        for name, fixture, expected in fixtures:
            path=write_json(self.output/"fixtures"/(name+".json"),fixture); before=digest(path)
            target=path.with_suffix(".msh")
            row={"case":name,"expected":"accepted_reconstruction" if expected else "rejected"}
            try:
                report,measure=self.operation(name,{"operation":"prepare_mesh_import","mesh_data":str(path),"output_mesh":str(target)})
                row.update(measure,outcome="accepted_reconstruction",report=report)
                okay=expected and report["output_triangles"]>0 and report["source_file_modified"] is False
                if name=="mixed-tetra-hexa":
                    okay=okay and report["internal_faces_removed"]==1 and report["output_triangles"]==18
                if not okay:
                    self.result["invariant_failures"].append({"case":name,"error":"unexpected_reconstruction"})
            except NativeFailure as error:
                try:
                    response = json.loads(str(error))
                    structured_refusal = (error.measurement.get("exit_code") == 2
                                          and response.get("ok") is False
                                          and bool(response.get("error")))
                except (ValueError, AttributeError):
                    structured_refusal = False
                outcome = "rejected" if structured_refusal else native_rejection_kind(False, error.measurement, str(error))
                row.update(error.measurement, outcome=outcome, error=str(error))
                okay=not expected and structured_refusal
                if not okay:
                    self.result["invariant_failures"].append({"case":name,"error":str(error)})
            row["source_checksum_unchanged"]=digest(path)==before
            row["expected_outcome_observed"]=okay and row["source_checksum_unchanged"]
            if not row["source_checksum_unchanged"]:
                self.result["invariant_failures"].append({"case":name,"error":"source_checksum_changed"})
            result["checks"].append(row)
        result["outcomes"]=dict(Counter(row["outcome"] for row in result["checks"]))
        self.save(); print("properties",result["outcomes"],flush=True)

    def corpus(self):
        result={}; self.result["sections"]["corpus"]=result
        million=self.output/"plans/million"
        report,measure=self.operation("million-metadata",{"operation":"corpus_plan","corpus_dir":str(million)})
        result["million_plan"]={**measure,"report":report,"files":len(list(million.rglob("*"))),
                                "bytes":sum(p.stat().st_size for p in million.rglob("*") if p.is_file()),
                                "mesh_files":len(list(million.rglob("*.msh")))}
        if report["completed_cases"]!=0 or report["planned_total"]!=1_000_000 or result["million_plan"]["mesh_files"]:
            raise AssertionError("La planification a exécuté des cas")
        corpus=self.output/"real_corpus"
        self.operation("recovery-plan",{"operation":"corpus_plan","corpus_dir":str(corpus),
                       "plan":{"structured":48,"random":48,"seed":17,"strategies":2,"shard_size":7}})
        directory=self.output/"interrupted-run";directory.mkdir()
        request=write_json(directory/"request.json",{"operation":"corpus_run","corpus_dir":str(corpus),
                           "output_dir":str(directory/"native"),"gmsh_library":str(self.library),"response_mode":"summary",
                           "run":{"limit":96,"max_seconds":120,"retain_meshes":False}})
        with (directory/"process.log").open("w") as log:
            process=subprocess.Popen([self.core,"--request",request],stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+30; durable=0
            while process.poll() is None and time.monotonic()<deadline:
                durable=sum(p.read_bytes().count(b"\n") for p in (corpus/"shards").glob("*.jsonl"))
                if durable>=3:break
                time.sleep(0.02)
            interrupted=process.poll() is None
            if interrupted:process.terminate()
            process.wait(timeout=10)
        result["interruption"]={"requested_after_complete_jsonl_lines":durable,"process_interrupted":interrupted,
                                "exit_code":process.returncode}
        recovered,_=self.operation("recovery-status",{"operation":"corpus_status","corpus_dir":str(corpus)})
        report,measure=self.operation("recovery-resume",{"operation":"corpus_run","corpus_dir":str(corpus),
                                     "gmsh_library":str(self.library),"run":{"limit":96,"max_seconds":120,"retain_meshes":False}},timeout=150)
        rows=[json.loads(line) for shard in sorted((corpus/"shards").glob("*.jsonl")) for line in shard.read_text().splitlines()]
        result["recovery"]={"initial_recovered_cases":recovered["completed_cases"],"report":report,**measure,
                            "record_count":len(rows),"unique_case_ids":len({r["case_id"] for r in rows}),
                            "contiguous_indices":[r["index"] for r in rows]==list(range(96))}
        if not (len(rows)==96 and result["recovery"]["contiguous_indices"] and result["recovery"]["unique_case_ids"]==96):
            raise AssertionError("Reprise du corpus non contiguë ou dupliquée")
        shard=sorted((corpus/"shards").glob("*.jsonl"))[0]; original=shard.read_bytes()
        checkpoint=corpus/"checkpoint.json"; checkpoint_before=digest(checkpoint)
        index=original.index(b'"case_id":"')+len(b'"case_id":"')
        changed=bytearray(original);changed[index]=ord("0") if changed[index]!=ord("0") else ord("1")
        shard.write_bytes(changed)
        try:
            self.operation("tampered-prefix",{"operation":"corpus_status","corpus_dir":str(corpus)})
            result["tamper"]={"refused":False};raise AssertionError("Un préfixe altéré a été accepté")
        except NativeFailure as error:
            result["tamper"]={"refused":True,"error":str(error),"checkpoint_unchanged":digest(checkpoint)==checkpoint_before}
        finally:
            shard.write_bytes(original)
        report,_=self.operation("restored-status",{"operation":"corpus_status","corpus_dir":str(corpus)})
        result["restored_completed_cases"]=report["completed_cases"]
        train,measure=self.operation("real-corpus-train",{"operation":"corpus_train","corpus_dir":str(corpus),
                                     "training":{"epochs":3,"batch_size":16,"max_records":0,"max_seconds":90}},timeout=120)
        result["training"]={**measure,"report":train}
        splits={key:{r["family"] for r in rows if r["split"]==key} for key in ("train","validation","test")}
        result["family_partitions_disjoint"]=all(not splits[a]&splits[b] for a,b in (("train","validation"),("train","test"),("validation","test")))
        if not result["family_partitions_disjoint"]:raise AssertionError("Fuite entre partitions de familles")
        self.save();print("corpus",report["completed_cases"],"cases, cancellation/recovery/tamper checked",flush=True)

    def streaming(self, count):
        directory=self.output/"synthetic_streaming";directory.mkdir()
        fixture=directory/"synthetic.jsonl"
        started=time.monotonic()
        with fixture.open("w") as out:
            for i in range(count):
                split="validation" if i%5==1 else "test" if i%5==2 else "train"
                actions=[]
                for action in range(2):
                    features=[float(i%2),float(action)]+[float((i+j)%2) for j in range(126)]
                    actions.append({"strategy_id":"a"+str(action),"features":features,
                                    "target":0.9 if action==i%2 else 0.1,"accepted":True,"success":True})
                out.write(json.dumps({"index":i,"case_id":str(i),"geometry_id":str(i),"family":split,"split":split,
                                      "feature_schema":"atelier.geometry_action_features.v2","actions":actions},separators=(",",":"))+"\n")
        result={"synthetic":True,"real_meshes_generated":0,"records":count,"strategies_per_record":2,
                "features":128,"fixture_bytes":fixture.stat().st_size,"fixture_sha256":digest(fixture),
                "fixture_write_seconds":time.monotonic()-started,"fixture_retained":False}
        self.result["sections"]["streaming"]=result
        settings={"epochs":2,"batch_size":128,"hidden_count":32,"max_records":0,"max_seconds":120,
                  "seed":42,"warm_start":False,"resume":True,"checkpoint_batches":16}
        corpora=[]
        try:
            for name in ("resumed","continuous"):
                corpus=directory/name;(corpus/"shards").mkdir(parents=True)
                shard=corpus/"shards/cases-000000.jsonl";os.link(fixture,shard);corpora.append(corpus)
            paused,measure=self.operation("stream-paused",{"operation":"corpus_train","corpus_dir":str(corpora[0]),
                                         "training":dict(settings,max_batches=7)},timeout=180,rss=True)
            result["paused"]={**measure,"report":paused}
            resumed,measure=self.operation("stream-resume",{"operation":"corpus_train","corpus_dir":str(corpora[0]),
                                          "training":settings},timeout=180,rss=True)
            result["resumed"]={**measure,"report":resumed}
            continuous,measure=self.operation("stream-continuous",{"operation":"corpus_train","corpus_dir":str(corpora[1]),
                                             "training":settings},timeout=180,rss=True)
            result["continuous"]={**measure,"report":continuous}
            models=[json.loads((p/"models/current.json").read_text()) for p in corpora]
            keys=("network","dataset_fingerprint","baseline_strategy","deploy_enabled","validation")
            result["exact_resumed_weights_validation_and_decision"]=all(models[0][key]==models[1][key] for key in keys)
            result["exact_final_partition_metrics"]=all(resumed[key]==continuous[key] for key in ("training","validation","test"))
            if not (paused["stopped_by_budget"] and resumed["resumed"] and resumed["epochs_completed"]==2
                    and result["exact_resumed_weights_validation_and_decision"] and result["exact_final_partition_metrics"]):
                raise AssertionError("La reprise des mini-lots ne reproduit pas l'entraînement continu")
        finally:
            for corpus in corpora:
                (corpus/"shards/cases-000000.jsonl").unlink(missing_ok=True)
            fixture.unlink(missing_ok=True)
        self.save();print("streaming",count,"synthetic records, exact resume",flush=True)

    def large(self):
        project=json.loads((ROOT/"examples/atelier_solid.json").read_text())
        project.update(name="Stress borné boîte 20×16×8mm",geometry={"kind":"box","width":20,"height":16,"depth":8})
        project["mesh"].update(size=0.42,min_size=0.12,order=1,threads=1,max_elements=300000,algorithm="delaunay")
        project["quality_policy"]={"minimum_quality":0.03,"poor_threshold":0.2,"strict":True}
        report,measure=self.operation("large-100k-300k",{"operation":"mesh","project":project},timeout=120,rss=True)
        signature=check_quality(report)
        result={**measure,"project":project,"signature":signature,"scope":"One bounded case; not an industrial memory ceiling"}
        if not 100000<=report["elements"]<=300000:
            self.result["invariant_failures"].append({"case":"large","error":"outside_target_100k_300k","elements":report["elements"]})
        self.result["sections"]["large"]=result;self.save()
        print("large",report["elements"],"elements",measure,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"verification/extended_20261107/core")
    parser.add_argument("--sections",default="matrix,properties,corpus,streaming,large")
    parser.add_argument("--seeds",default="7,42,2026")
    parser.add_argument("--synthetic-cases",type=int,default=100000)
    parser.add_argument("--reanalyze",type=Path,help="réexaminer un rapport existant depuis ses artefacts, sans recalculer les maillages")
    args=parser.parse_args()
    if args.reanalyze:
        reanalyze(args.reanalyze)
        return 0
    if not 100<=args.synthetic_cases<=100000:parser.error("La fixture doit contenir 100 à 100000 cas")
    stamp=datetime.now().strftime("%Y%m%dT%H%M%S%f")
    campaign=Campaign(args.output/f"run-{stamp}")
    for section in args.sections.split(","):
        try:
            if section=="matrix":campaign.matrix([int(x) for x in args.seeds.split(",")])
            elif section=="properties":campaign.properties()
            elif section=="corpus":campaign.corpus()
            elif section=="streaming":campaign.streaming(args.synthetic_cases)
            elif section=="large":campaign.large()
            else:raise ValueError("Section inconnue : "+section)
        except Exception as error:
            campaign.result["invariant_failures"].append({"section":section,"error":str(error)})
            print("SECTION FAILURE",section,str(error),flush=True)
        campaign.save()
    campaign.result["finished_utc"]=datetime.now(timezone.utc).isoformat();campaign.save()
    write_json(args.output/"summary.json",campaign.result)
    print(args.output/"summary.json",flush=True)
    return bool(campaign.result["invariant_failures"])


def reanalyze(path):
    """Reapply corrected analysis criteria while preserving the first report."""
    report=json.loads(path.read_text());root=path.parent
    original=path.with_name("summary.initial_criteria.json")
    if not original.exists():original.write_bytes(path.read_bytes())
    matrix=report.get("sections",{}).get("matrix")
    if not matrix:raise ValueError("Rapport de matrice absent")
    pairs={}
    report["invariant_failures"]=[row for row in report["invariant_failures"]
                                  if row.get("error") not in ("unit_invariance","native_crashed")]
    for row in matrix["cases"]:
        label=f"{row['seed']}-{row['family']}-o{row['order']}-{row['unit']}"
        if not row.get("directory"):
            matches=list((root/"operations").glob("*-"+label))
            if len(matches)==1:
                row.update(json.loads((matches[0]/"measurement.json").read_text()))
        if "error" in row and row["outcome"] != "invariant_failure":
            row["outcome"] = native_rejection_kind(row["expected_invalid"], row, row["error"])
        if row.get("exit_code",0)<0 and not row.get("timed_out"):
            row["outcome"]="native_crashed"
            report["invariant_failures"].append({"case":label,"error":"native_crashed","directory":row["directory"],"exit_code":row["exit_code"]})
        if "signature" in row:
            native=json.loads((root/row["directory"]/"native/report.json").read_text())
            row["signature"]=check_quality(native)
            pairs.setdefault((row["seed"],row["family"],row["order"]),{})[row["unit"]]=row["signature"]
    comparisons=[]
    for (seed,family,order),pair in pairs.items():
        if len(pair)!=2:continue
        same=same_physical_geometry_and_size(pair["mm"],pair["m"])
        comparisons.append({"seed":seed,"family":family,"order":order,
                            "same_physical_geometry_and_target_sizes":same,
                            "bounds_absolute_comparison_tolerance":bounds_comparison_tolerance(pair["mm"],pair["m"]),
                            "same_mesh_counts_and_quality":same_physics(pair["mm"],pair["m"]),
                            "same_acceptance_decision":pair["mm"]["accepted"]==pair["m"]["accepted"]})
        if not same:report["invariant_failures"].append({"case":f"{seed}-{family}-o{order}","error":"physical_unit_invariance"})
    matrix["unit_comparisons"]=comparisons
    matrix["outcomes"]=dict(Counter(row["outcome"] for row in matrix["cases"]))
    report["analysis_note"]="Inter-unit unstructured mesh identity is an observation, not an invariant; SI geometry/target sizes and independent native quality are checked. Original analysis retained separately."
    write_json(path,report);write_json(root.parent/"summary.json",report)
    print(json.dumps({"outcomes":matrix["outcomes"],"unit_pairs":len(comparisons),"physical_unit_failures":sum(not x["same_physical_geometry_and_target_sizes"] for x in comparisons),"invariant_failures":report["invariant_failures"]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    raise SystemExit(main())
