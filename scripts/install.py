"""Installer un environnement Python local sans droits administrateur.

Usage : python scripts/install.py
La première installation télécharge les dépendances et, si le noyau compilé
de la plateforme est absent, compile ses sources avec Cargo. Rust est alors un
prérequis : ce script ne télécharge pas automatiquement sa chaîne d'outils.
Aucun paquet système ni interpréteur global n'est modifié.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from atelier.core_locator import find_core

VENV = ROOT / ".venv"


def environment_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def supported_python() -> bool:
    return (3, 11) <= sys.version_info[:2] < (3, 14)


def ensure_core() -> Path:
    existing = find_core(ROOT)
    if existing:
        print(f"Noyau Rust disponible : {existing}", flush=True)
        return existing
    cargo = shutil.which("cargo")
    build_env = os.environ.copy()
    if not cargo:
        home_cargo = Path.home() / ".cargo" / "bin" / ("cargo.exe" if sys.platform == "win32" else "cargo")
        if home_cargo.is_file():
            cargo = str(home_cargo)
    if not cargo:
        tools = ROOT / ".toolchain"
        private_cargo = tools / "cargo" / "bin" / ("cargo.exe" if sys.platform == "win32" else "cargo")
        if private_cargo.is_file() and (tools / "rustup").is_dir():
            cargo = str(private_cargo)
            build_env.update(CARGO_HOME=str(tools / "cargo"), RUSTUP_HOME=str(tools / "rustup"),
                             PATH=str(private_cargo.parent) + os.pathsep + build_env.get("PATH", ""))
    if not cargo:
        raise ValueError("Aucun noyau Rust compilé pour cet ordinateur et Cargo est absent. "
                         "Installer la chaîne Rust depuis https://rustup.rs, ouvrir un nouveau "
                         "terminal puis relancer ce script. La chaîne Rust n'est pas installée automatiquement.")
    manifest = ROOT / "rust_core" / "Cargo.toml"
    if not manifest.is_file():
        raise ValueError("Les sources rust_core/Cargo.toml sont absentes. "
                         "Extraire l'archive source complète avant l'installation.")
    print("Compilation du noyau Rust. Cargo peut télécharger ses dépendances lors de ce premier calcul.", flush=True)
    run([cargo, "build", "--locked", "--release", "--manifest-path", str(manifest),
         "--target-dir", str(ROOT / "rust_core" / "target")], env=build_env)
    compiled = find_core(ROOT)
    if compiled is None:
        raise ValueError("La compilation s'est terminée sans produire rust_core/target/release/mailleur-core.")
    return compiled


def run(command: list[str], *, env=None) -> None:
    print("\n> " + " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Installer Atelier Mailleur dans son dossier .venv.")
    parser.add_argument("--no-doctor", action="store_true", help="ne pas lancer le diagnostic final")
    args = parser.parse_args(argv)
    if not supported_python():
        print("Python 3.11, 3.12 ou 3.13 est nécessaire. Interpréteur actuel : "
              f"{sys.version.split()[0]} ({sys.executable}).", file=sys.stderr)
        return 2
    executable = environment_python()
    try:
        ensure_core()
        if not executable.is_file():
            if VENV.exists():
                raise ValueError("Le dossier .venv existe mais son interpréteur est absent. "
                                 "Renommer ce dossier avant de relancer l'installation.")
            print("Création d'un environnement isolé dans le dossier du projet.", flush=True)
            run([sys.executable, "-m", "venv", str(VENV)])
        print("Installation de l'interface PySide6 et de ses dépendances : une connexion Internet est nécessaire.\n"
              "Les pièces et résultats de calcul ne sont pas envoyés par cette installation.", flush=True)
        run([str(executable), "-m", "pip", "install", "--upgrade", "pip>=23", "setuptools>=77"])
        run([str(executable), "-m", "pip", "install", "-e", str(ROOT)])
        if not args.no_doctor:
            run([str(executable), "-m", "atelier", "doctor"])
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"\nInstallation incomplète : {error}\n"
              "Consulter README.md, puis relancer ce script après correction. "
              "L'environnement existant est conservé.", file=sys.stderr)
        return 1
    print("\nInstallation terminée. Lancer : python scripts/launch.py", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
