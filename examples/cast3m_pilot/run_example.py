#!/usr/bin/env python3
"""Exemple public Mailleur -> Cast3M, une éprouvette et 43 comparaisons.

SPDX-License-Identifier: GPL-3.0-or-later
Auteur : Projet Mailleur.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--castem", required=True, help="lanceur Cast3M installé, chemin ou nom dans PATH")
    parser.add_argument("--output", type=Path, default=Path("output/cast3m_pilot"), help="dossier neuf ou vide")
    parser.add_argument("--memory-mib", type=int, default=1024,
                        help="budget mémoire Cast3M en Mio, défaut 1024")
    parser.add_argument("--reference", action="store_true",
                        help="réutiliser la référence quadrilatères/hexaèdres fournie, sans installation Mailleur")
    args = parser.parse_args()
    if args.memory_mib < 1:
        parser.error("--memory-mib doit être un entier positif")
    target = args.output.expanduser().resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        parser.error("Choisir un dossier neuf ou vide")
    if args.reference:
        target.mkdir(parents=True, exist_ok=True)
        names = ["project.json", "import_cast3m.dgibi", "import_cast3m.unv",
                 "import_cast3m.dgibi.units.json", "traction.dgibi", "maillage.json"]
        for name in names:
            shutil.copyfile(HERE / "reference_quad_hex" / name, target / name)
    else:
        generation = subprocess.run([sys.executable, str(HERE / "generer_maillage.py"), "--output", str(target)])
        if generation.returncode:
            return generation.returncode
    return subprocess.run([sys.executable, str(HERE / "verifier_calcul.py"), str(target),
                           "--castem", args.castem, "--memory-mib", str(args.memory_mib)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
