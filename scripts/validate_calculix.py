"""Real CalculiX tests of exported INP meshes, independent of Abaqus.

Example: python scripts/validate_calculix.py --solver /path/to/ccx
The test adds an elastic material and boundary conditions to the unmodified
mesh export. No CalculiX installation is changed by this script.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_dat(path):
    sections = {"displacements": [], "stresses": [], "forces": []}
    current = None
    for line in path.read_text(errors="replace").splitlines():
        text = line.strip().lower()
        if text.startswith("displacements ("):
            current = "displacements"
        elif text.startswith("stresses ("):
            current = "stresses"
        elif text.startswith("forces ("):
            current = "forces"
        elif text and text[0].isalpha():
            current = None
        elif current and re.match(r"^\s*\d+\s", line):
            sections[current].append([float(x.replace("D", "E")) for x in line.split()])
    return sections


def project(planar, order):
    return {
        "schema_version": 1, "name": "CalculiX elastic patch", "mode": "planar" if planar else "volume",
        "units": {"input": "mm", "internal": "m"},
        "geometry": {"kind": "plate", "width": 10, "height": 5, "holes": []} if planar else
                    {"kind": "box", "width": 10, "height": 5, "depth": 3},
        "mesh": {"size": 1.5, "min_size": .3, "order": order, "algorithm": "automatic",
                 "threads": 1, "max_elements": 20000, "recombine": False},
        "repair": {"enabled": False},
        "quality_policy": {"minimum_quality": .01, "poor_threshold": .2, "strict": True},
    }


def run_case(solver, output, planar, order, units):
    import numpy as np
    from atelier.formats import _inp_real
    from atelier.native import NativeSession

    case = output / f"{'plane' if planar else 'solid'}_order{order}_{units}"
    case.mkdir(parents=True, exist_ok=True)
    session = NativeSession(case / "workspace", timeout=90)
    t0 = time.monotonic()
    try:
        generated = session.generate(project(planar, order))
        mesh_seconds = time.monotonic() - t0
        exported = session.export(Path(generated["directory"]), case / "mesh.inp", "inp", units=units, planar=planar)
    finally:
        session.cancel()
    mesh_path = Path(exported["file"])
    # Inspect the actual exported records: meshio 5.3.5 cannot read CPS6 even
    # though CalculiX supports it. The real solver remains the external reader.
    nodes, cell_counts, section = [], {}, None
    for line in mesh_path.read_text().splitlines():
        if line.startswith("**"):
            continue
        if line.startswith("*"):
            section = "nodes" if line.upper() == "*NODE" else None
            if line.upper().startswith("*ELEMENT"):
                section = re.search(r"TYPE\s*=\s*(\w+)", line, re.I).group(1)
                cell_counts.setdefault(section, 0)
            continue
        if section == "nodes":
            fields = line.split(",")
            if int(fields[0]) != len(nodes) + 1 or any(len(x) > 20 for x in fields[1:]):
                raise AssertionError("Invalid exported INP node numbering or numeric field width")
            nodes.append([float(x) for x in fields[1:4]])
        elif section:
            cell_counts[section] += 1
    points = np.asarray(nodes)
    lengths = points.max(axis=0) - points.min(axis=0)
    factor = 1.0 if units == "mm" else .001
    if not np.allclose(lengths[:2], [10 * factor, 5 * factor], rtol=1e-12, atol=1e-14):
        raise AssertionError("Export coordinate scale differs from declared units")
    E = 210000.0 if units == "mm" else 210e9
    nu, strain = .3, .001
    thickness = factor
    ne = sum(cell_counts.values())
    nn = len(points)
    lines = [mesh_path.read_text(), "*NSET,NSET=VERIFY_ALL_NODES,GENERATE", f"1,{nn},1",
             "*ELSET,ELSET=VERIFY_ALL_ELEMENTS,GENERATE", f"1,{ne},1",
             "*MATERIAL,NAME=VERIFY_STEEL", "*ELASTIC", f"{_inp_real(E)},{nu}",
             "*SOLID SECTION,ELSET=VERIFY_ALL_ELEMENTS,MATERIAL=VERIFY_STEEL", _inp_real(thickness) if planar else "",
             "*BOUNDARY"]
    # Homogeneous uniaxial extension: all other faces remain traction-free.
    # These conditions leave interior unknowns to be solved by the actual code.
    right_nodes = set()
    for i, p in enumerate(points, 1):
        if abs(p[0]) <= 1e-9 * factor:
            lines.append(f"{i},1,1,0")
        elif abs(p[0] - 10 * factor) <= 1e-9 * factor:
            lines.append(f"{i},1,1,{_inp_real(strain * 10 * factor)}")
            right_nodes.add(i)
        if abs(p[1]) <= 1e-9 * factor:
            lines.append(f"{i},2,2,0")
        if not planar and abs(p[2]) <= 1e-9 * factor:
            lines.append(f"{i},3,3,0")
    lines.extend(["*STEP", "*STATIC", "*NODE PRINT,NSET=VERIFY_ALL_NODES", "U,RF",
                  "*EL PRINT,ELSET=VERIFY_ALL_ELEMENTS", "S", "*END STEP", ""])
    deck = case / "elastic.inp"
    deck.write_text("\n".join(lines), encoding="ascii")
    environment = dict(os.environ, OMP_NUM_THREADS="1", CCX_NPROC_RESULTS="1", CCX_NPROC_STIFFNESS="1",
                       OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    t0 = time.monotonic()
    run = subprocess.run([str(solver), "-i", "elastic"], cwd=case, env=environment,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
    solver_seconds = time.monotonic() - t0
    (case / "solver.log").write_text(run.stdout)
    if run.returncode or "Job finished" not in run.stdout or "*ERROR" in run.stdout:
        raise AssertionError(f"Solver failed; inspect {case / 'solver.log'}")
    data = parse_dat(case / "elastic.dat")
    if len(data["displacements"]) != nn or not data["stresses"] or len(data["forces"]) != nn:
        raise AssertionError("Missing displacement, stress or reaction output")
    error_u = 0.0
    for row in data["displacements"]:
        p = points[int(row[0]) - 1]
        expected = np.array([strain * p[0], -nu * strain * p[1], 0 if planar else -nu * strain * p[2]])
        error_u = max(error_u, float(np.max(np.abs(np.asarray(row[1:4]) - expected))))
    expected_sx = E * strain
    stress = np.asarray([row[2:8] for row in data["stresses"]])
    stress_error = float(np.max(np.abs(stress - [expected_sx, 0, 0, 0, 0, 0])) / expected_sx)
    reaction_right = sum(row[1] for row in data["forces"] if int(row[0]) in right_nodes)
    force_balance = max(abs(sum(row[i] for row in data["forces"])) for i in range(1, 4))
    area = (5 * factor) * (thickness if planar else 3 * factor)
    reaction_expected = expected_sx * area
    # .dat uses seven significant digits; shell expansion of plane elements has
    # a slightly larger numerical tolerance than the genuinely 3D patch.
    u_relative = error_u / (strain * 10 * factor)
    reaction_error = abs(reaction_right - reaction_expected) / reaction_expected
    balance_relative = force_balance / reaction_expected
    checks = {"displacement_relative_error": u_relative, "stress_relative_error": stress_error,
              "right_reaction_relative_error": reaction_error, "force_balance_relative_error": balance_relative}
    tolerance = 2e-4 if planar else 2e-5
    if max(checks.values()) > tolerance:
        raise AssertionError(f"Elastic solution differs from analytical uniaxial tension: {checks}")
    return {"case": case.name, "status": "passed", "units": units, "order": order,
            "nodes": nn, "elements": ne, "cell_counts": cell_counts,
            "lengths": lengths.tolist(), "material_E": E, "poisson": nu, "strain_x": strain,
            "expected_stress_x": expected_sx, "expected_right_reaction_N": reaction_expected,
            "actual_right_reaction_N": reaction_right, "errors": checks, "tolerance": tolerance,
            "mesh_seconds": mesh_seconds, "solver_seconds": solver_seconds,
            "mesh_sha256": digest(mesh_path), "deck_sha256": digest(deck),
            "stress_integration_point_count": len(stress), "mesh_export": exported,
            "scope": "CalculiX execution only; does not certify Abaqus"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "verification/extended_20261107/calculix")
    args = parser.parse_args()
    solver = args.solver.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([str(solver), "-v"], capture_output=True, text=True, timeout=15)
    from atelier.native import core_path
    core = core_path()
    core_version = subprocess.run([str(core), "--version"], capture_output=True, text=True, timeout=10)
    summary = {"schema": "atelier.calculix_validation.v1", "utc": datetime.now(timezone.utc).isoformat(),
               "platform": platform.platform(), "machine": platform.machine(), "python": sys.version,
               "solver": str(solver), "solver_sha256": digest(solver),
               "native_kernel_version": core_version.stdout.strip(), "native_kernel_sha256": digest(core),
               "solver_version": (version.stdout + version.stderr).strip(), "cases": [],
               "reference": "https://www.dhondt.de/ccx_2.23.pdf",
               "not_tested": ["Abaqus", "all possible INP elements and material laws"]}
    for planar in (True, False):
        for order in (1, 2):
            for units in ("mm", "m"):
                try:
                    case = run_case(solver, output, planar, order, units)
                except Exception as error:
                    case = {"case": f"{'plane' if planar else 'solid'}_order{order}_{units}",
                            "status": "failed", "error": str(error)}
                summary["cases"].append(case)
                (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
                print(json.dumps(case, ensure_ascii=False), flush=True)
    summary["passed"] = all(c["status"] == "passed" for c in summary["cases"])
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
