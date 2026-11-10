#!/usr/bin/env python3
"""Inventorier les octets du projet sans dépendance ni chargement du corpus.

Les caches, environnements, compilations et sorties restent dans des totaux
distincts. Les liens symboliques ne sont pas suivis. La mesure porte sur la
longueur des fichiers, pas sur les blocs physiques occupés sur le disque.
"""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path


ENVIRONMENTS = {".venv", "venv", ".bootstrap", ".runtime"}
BUILDS = {"target", "build", "dist", "*.egg-info"}
CACHES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache"}
OUTPUTS = {"output", "outputs"}


def runtime_category(parts):
    if ".git" in parts:
        return "historique_git"
    if any(p in ENVIRONMENTS for p in parts):
        return "environnements_python"
    if ".toolchain" in parts:
        return "chaine_de_compilation_locale"
    if any(p in BUILDS or p.endswith(".egg-info") for p in parts):
        return "compilation_et_paquets"
    if any(p in CACHES for p in parts):
        return "caches"
    if any(p in OUTPUTS for p in parts):
        return "sorties_locales"
    return None


def project_category(path):
    parts = path.parts
    top = parts[0]
    if top == "verification":
        if "reproduction" in parts:
            return "verification_sources_de_reference"
        return "verification_rapports_et_donnees"
    if top == "rust_core":
        if "bin" in parts:
            return "binaires_rust_livres"
        if path.suffix == ".rs":
            return "sources_rust"
        return "configuration_rust"
    if top in {"atelier", "mailleur"}:
        if path.suffix == ".py":
            return "application_python"
        return "ressources_interface"
    if top == "scripts":
        return "scripts_utilitaires"
    if top == "tests":
        return "tests_et_references"
    if top == "examples":
        return "exemples_et_geometries"
    if top == "docs" or path.suffix == ".md":
        return "documentation"
    return "configuration_et_lanceurs"


def inventory(root):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Le dossier du projet est introuvable : " + str(root))
    project = defaultdict(lambda: {"files": 0, "bytes": 0})
    runtime = defaultdict(lambda: {"files": 0, "bytes": 0})
    top_level = defaultdict(lambda: {"files": 0, "bytes": 0})
    symlinks = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        # Un lien peut pointer vers un environnement extérieur au projet.
        symlinks += sum((Path(directory) / d).is_symlink() for d in dirs)
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in names:
            path = Path(directory) / name
            if path.is_symlink():
                symlinks += 1
                continue
            relative = path.relative_to(root)
            size = path.stat().st_size
            excluded = runtime_category(relative.parts)
            group = runtime[excluded] if excluded else project[project_category(relative)]
            group["files"] += 1
            group["bytes"] += size
            if not excluded:
                top_level[relative.parts[0]]["files"] += 1
                top_level[relative.parts[0]]["bytes"] += size

    def total(groups):
        return {key: sum(row[key] for row in groups.values()) for key in ("files", "bytes")}

    return {
        "schema": "atelier.inventory_sizes.v1",
        "root": str(root),
        "units": {"ko": 1000, "Mo": 1000000, "Mio": 1048576},
        "project_total_excluding_runtime": total(project),
        "project_categories": dict(sorted(project.items())),
        "project_top_level": dict(sorted(top_level.items())),
        "runtime_total_separate": total(runtime),
        "runtime_categories": dict(sorted(runtime.items())),
        "symlinks_not_followed": symlinks,
        "measurement": "logical_file_bytes; generated data in verification are included",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="Imprimer le relevé JSON complet")
    args = parser.parse_args()
    try:
        report = inventory(args.root)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("Dossier :", report["root"])
    print("Tailles logiques ; 1 Mo = 1 000 000 octets ; 1 Mio = 1 048 576 octets.")
    for title, key in (("Projet, hors environnement et sorties", "project_categories"),
                       ("Environnement, compilation et sorties, séparément", "runtime_categories")):
        print("\n" + title)
        print("{:<45} {:>8} {:>14} {:>10}".format("Catégorie", "Fichiers", "Octets", "Mio"))
        for name, row in report[key].items():
            print("{:<45} {:>8} {:>14} {:>10.3f}".format(name, row["files"], row["bytes"], row["bytes"] / 1048576))
    print("\nTotal projet :", report["project_total_excluding_runtime"])
    print("Total environnement/sorties :", report["runtime_total_separate"])
    print("Liens symboliques non suivis :", report["symlinks_not_followed"])


if __name__ == "__main__":
    main()
