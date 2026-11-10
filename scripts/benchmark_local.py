"""Mesurer la génération sur la machine courante, sans toucher aux preuves livrées.

Depuis la racine : .venv/bin/python scripts/benchmark_local.py
Le chronomètre comprend le pont Python, le processus Rust et les fichiers.
Les exports sont mesurés séparément. Aucun modèle neuronal n'est activé.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from atelier.native import NativeSession, core_path, gmsh_library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "verification/local_m3/meshing.json")
    parser.add_argument("--workspace", type=Path, default=ROOT / "output/benchmark_local")
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 20:
        parser.error("repetitions doit être entre 1 et 20")
    session = NativeSession(args.workspace, timeout=120)
    report = {"date_utc": datetime.now(timezone.utc).isoformat(),
              "environment": {"platform": platform.platform(), "architecture": platform.machine(),
                              "python": platform.python_version(), "core": str(core_path()),
                              "gmsh_library": str(gmsh_library()),
                              "packages": {p: importlib.metadata.version(p) for p in ("gmsh", "meshio", "numpy", "h5py", "PySide6-Essentials")}},
              "scope": "Temps génération depuis NativeSession, chargement rapport/aperçu compris ; export MSH séparé ; sans conseiller.",
              "cases": []}
    for name in ("atelier_plate", "atelier_solid", "atelier_shell"):
        project = json.loads((ROOT / "examples" / (name + ".json")).read_text())
        times, exports = [], []
        for repetition in range(args.repetitions):
            started = time.perf_counter()
            result = session.generate(project)
            times.append(time.perf_counter() - started)
            started = time.perf_counter()
            exported = session.export(Path(result["directory"]), args.workspace / "exports" / f"{name}-{repetition}.msh", "msh",
                                      units=project["units"]["input"], planar=project["mode"] == "planar")
            exports.append(time.perf_counter() - started)
            if exported.get("status") != "ok":
                raise RuntimeError(f"Export refusé : {exported}")
        row = {"name": name, "project": project, "seconds": times, "median_seconds": statistics.median(times),
               "export_msh_seconds": exports, "export_msh_median_seconds": statistics.median(exports),
               "report": result["report"]}
        report["cases"].append(row)
        print(f"{name} : génération {row['median_seconds']:.3f} s ; export MSH {row['export_msh_median_seconds']:.3f} s", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
