#!/usr/bin/env python3
"""Générer un seul exemple Mailleur -> UNV/Gibiane, sans exécuter Cast3M.

SPDX-License-Identifier: GPL-3.0-or-later
Adaptation du cas volume_p2_mm de Mailleur/scripts/validate_cast3m.py.
Mailleur doit être installé dans le Python qui exécute ce script.
"""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
GROUPS = ["BOUNDARY", "DOMAIN", "FACE_001", "FACE_002", "FACE_003", "FACE_004",
          "FACE_005", "FACE_006", "GEOMETRY_1D", "GEOMETRY_2D", "PART_001"]
CAST_TYPES = {"line3": "SEG3", "triangle6": "TRI6", "tetra10": "TE10"}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_mesh(path):
    # Lecture de référence seulement : la génération et la conversion
    # géométrique des unités ont déjà été faites par le noyau Rust.
    import meshio
    import numpy as np
    with redirect_stdout(io.StringIO()):
        mesh = meshio.read(path, file_format="gmsh")
    groups = {}
    for name in GROUPS:
        blocks = mesh.cell_sets[name]
        nodes = [cell.data[indices].ravel() for cell, indices in zip(mesh.cells, blocks)
                 if indices is not None and len(indices)]
        groups[name] = {"elements": sum(len(indices) for indices in blocks if indices is not None),
                        "nodes": int(len(np.unique(np.concatenate(nodes))))}
    counts = {}
    for cell in mesh.cells:
        kind = CAST_TYPES[cell.type]  # Cet exemple concerne uniquement l'ordre 2.
        counts[kind] = counts.get(kind, 0) + len(cell.data)
    return {"nodes": len(mesh.points), "elements": sum(counts.values()),
            "cell_counts": counts, "groups": groups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("resultats"),
                        help="dossier neuf ou vide ; défaut : resultats")
    args = parser.parse_args()
    target = args.output.expanduser().resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        parser.error("Choisir un dossier neuf ou vide pour conserver les résultats précédents")
    from atelier import __version__
    from atelier.native import NativeSession, core_path
    project = json.loads((HERE / "project.json").read_text(encoding="utf-8"))
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mailleur-cast3m-") as scratch:
        session = NativeSession(Path(scratch), timeout=60)
        started = time.perf_counter()
        native = session.generate(project)
        generation_seconds = time.perf_counter() - started
        if native["report"]["quality"]["accepted"] is not True:
            raise RuntimeError("Le maillage n'a pas été accepté")
        source = Path(native["directory"]) / "mesh.msh"
        expected = expected_mesh(source)
        started = time.perf_counter()
        session.export(source.parent, target / "import_cast3m.dgibi", "cast3m", units="mm")
        export_seconds = time.perf_counter() - started
    bridge = (target / "import_cast3m.dgibi").read_text(encoding="ascii")
    mechanics = (HERE / "mecanique.dgibi").read_text(encoding="ascii")
    (target / "traction.dgibi").write_text("OPTI 'DIME' 3 'ECHO' 0;\n" + bridge + "\n" + mechanics,
                                          encoding="ascii")
    shutil.copyfile(HERE / "project.json", target / "project.json")
    names = ["project.json", "import_cast3m.dgibi", "import_cast3m.unv",
             "import_cast3m.dgibi.units.json", "traction.dgibi"]
    report = {"schema": "mailleur.cast3m.example.mesh.v1",
              "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "versions": {"mailleur": __version__, "python": platform.python_version(),
                           "gmsh": native["report"]["backend"]["gmsh_version"],
                           "native_kernel": subprocess.run([str(core_path()), "--version"], check=True,
                               capture_output=True, text=True).stdout.strip(),
                           "native_kernel_sha256": sha256(core_path())},
              "units": {"input": "mm", "internal": "m", "export": "mm"},
              "expected": expected, "quality": native["report"]["quality"],
              "timing_seconds": {"generation": generation_seconds, "export": export_seconds},
              "files_sha256": {name: sha256(target / name) for name in names},
              "cast3m_executed": False}
    (target / "maillage.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    print(json.dumps({"output": target.name, "nodes": expected["nodes"],
                      "cell_counts": expected["cell_counts"], "export_units": "mm"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
