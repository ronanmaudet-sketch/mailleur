"""Reproduce a bounded real quad/hex campaign and export its three model files.

The output workspace retains all raw diagnostics locally. Public evidence only
contains synthetic demonstration geometries, model weights and relative names.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import meshio

from atelier.native import NativeSession, core_path
from scripts.share_model import validate_model


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new local workspace for raw diagnostics")
    parser.add_argument("--evidence", type=Path, required=True, help="new directory for public summary and weights")
    args = parser.parse_args(argv)
    output, public = args.output.resolve(), args.evidence.resolve()
    if output.exists() or public.exists():
        parser.error("use two new directories; previous verification results are never overwritten")
    output.mkdir(parents=True)
    public.mkdir(parents=True)
    session = NativeSession(output / "workspace", timeout=180)
    started = time.perf_counter()
    base = {"schema_version": 1, "name": "Démonstration quadrilatères",
            "mode": "planar", "units": {"input": "mm", "internal": "m"},
            "geometry": {"kind": "plate", "width": 4, "height": 2},
            "mesh": {"size": .65, "min_size": .2, "order": 1, "threads": 1,
                     "max_elements": 10000, "algorithm": "automatic", "element_policy": "quad_hex_only"}}
    summary = {"schema": "atelier.quad_hex_verification.v1", "date": "2026-11-18",
               "version": "1.0.0rc9", "element_policy": "quad_hex_only",
               "core_sha256": hashlib.sha256(core_path().read_bytes()).hexdigest(),
               "cases": [], "models": {}, "passed": False}
    try:
        for mode in ("planar", "volume"):
            for order in (1, 2):
                project = deepcopy(base)
                project["mode"] = mode
                project["mesh"]["order"] = order
                if mode == "volume":
                    project["geometry"] = {"kind": "box", "width": 2, "height": 1, "depth": 1}
                before = time.perf_counter()
                result = session.generate(project)
                elapsed = time.perf_counter() - before
                report = result["report"]
                mesh = meshio.read(Path(result["directory"]) / "mesh.msh")
                counts = {}
                for block in mesh.cells:
                    counts[block.type] = counts.get(block.type, 0) + len(block.data)
                allowed = {"vertex", "line", "line3", "quad", "quad8", "quad9"}
                domain = ("quad" if order == 1 else "quad8")
                if mode == "volume":
                    allowed |= {"hexahedron", "hexahedron20", "hexahedron27"}
                    domain = "hexahedron" if order == 1 else "hexahedron20"
                assert report["element_policy"]["verified"] is True
                assert report["quality"]["accepted"] and counts.get(domain, 0) > 0
                assert all(not count or kind in allowed for kind, count in counts.items())
                summary["cases"].append({"mode": mode, "order": order, "passed": True,
                    "nodes": report["nodes"], "elements": report["elements"],
                    "minimum_quality": report["quality"]["minimum"], "elapsed_seconds": elapsed,
                    "native_timings": report["timings"], "cell_counts_readback": counts,
                    "element_policy": report["element_policy"]})

        cases = []
        for family, geometry, mode in (
            ("plate_plain", {"kind": "plate", "width": 4, "height": 2}, "planar"),
            ("plate_round", {"kind": "plate", "width": 3, "height": 3, "corner_radius": .3}, "planar"),
            ("box_long", {"kind": "box", "width": 2, "height": 1, "depth": 1}, "volume"),
            ("box_wide", {"kind": "box", "width": 2, "height": 2, "depth": 1}, "volume")):
            project = deepcopy(base)
            project.update(geometry=geometry, mode=mode)
            cases.append({"family": family, "project": project})
        guide = session.train(epochs=12, seed=42, cases=cases)
        summary["guide_training"] = {key: guide["report"][key] for key in
            ("training_cases", "validation_cases", "deploy_enabled", "executed_actions")
            if key in guide["report"]}

        for width in (4, 5):
            project = deepcopy(base)
            project["geometry"]["width"] = width
            learned = session.learn(project, max_trials=2, epochs=8, max_seconds=45)
            assert learned["report"]["element_policy"]["verified"] is True
        summary["experience"] = {key: learned["report"]["experience"][key] for key in
            ("accepted", "executed_trials", "history_contexts", "history_observations")}

        session.corpus_plan(structured=24, random=0, seed=42, strategies=2, shard_size=24)
        corpus = session.corpus_run(limit=24, max_seconds=120, retain_meshes=False)
        assert corpus["completed_cases"] == 24
        trained = session.corpus_train(epochs=3, batch_size=16, max_records=1000, max_seconds=60)
        summary["corpus"] = {key: corpus[key] for key in
            ("completed_cases", "meshed_cases", "admissible_cases", "backend_evaluations") if key in corpus}
        summary["corpus_training"] = {key: trained[key] for key in
            ("training_cases", "validation_cases", "test_cases", "training_examples", "deploy_enabled",
             "native_campaign_integrity_verified") if key in trained}
        corpus_dir = session.workspace / "corpus"
        successful_actions = failed_actions = 0
        for shard in sorted((corpus_dir / "shards").glob("cases-*.jsonl")):
            for line in shard.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                assert row["element_policy"] == "quad_hex_only"
                for action in row["actions"]:
                    if action["success"]:
                        assert action["element_policy"]["verified"] is True
                        successful_actions += 1
                    else:
                        assert not action["accepted"]
                        failed_actions += 1
        summary["corpus_action_evidence"] = {"successful_verified_actions": successful_actions,
                                             "failed_actions": failed_actions}

        for kind, source in {
            "historique": session.workspace / "models/guide.json",
            "experience": session.workspace / "experience/model.json",
            "corpus": session.workspace / "models/corpus.json",
        }.items():
            model = json.loads(source.read_text(encoding="utf-8"))
            architecture = validate_model(kind, model)
            data = source.read_bytes()
            assert b"/Users/" not in data and b"/private/tmp" not in data
            destination = public / "models" / (kind + ".json")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            summary["models"][kind] = {"file": "models/" + destination.name,
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "element_policy": model["element_policy"], "deploy_enabled": model["deploy_enabled"],
                "architecture": architecture}
        summary["passed"] = True
    finally:
        session.cancel()
        summary["elapsed_seconds"] = time.perf_counter() - started
        write_json(public / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
