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
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"


def environment_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def supported_python() -> bool:
    return (3, 11) <= sys.version_info[:2] < (3, 14)


def native_binary() -> Path | None:
    """Même convention de recherche que le pont atelier.native."""
    override = os.environ.get("ATELIER_CORE")
    if override:
        candidate = Path(override).expanduser().resolve()
        if not candidate.is_file():
            raise ValueError(f"ATELIER_CORE désigne un fichier absent : {candidate}")
        if not binary_runs(candidate):
            raise ValueError(f"ATELIER_CORE ne peut pas être exécuté sur cet ordinateur : {candidate}")
        return candidate
    filename = "mailleur-core.exe" if sys.platform == "win32" else "mailleur-core"
    system = f"{sys.platform}-{platform.machine().lower()}"
    candidates = [ROOT / "rust_core" / "target" / "release" / filename,
                  ROOT / "rust_core" / "bin" / system / filename]
    # Le pont natif utilisera le premier fichier existant dans ce même ordre.
    # Un binaire copié depuis une autre plateforme doit être recompilé.
    candidate = next((path for path in candidates if path.is_file()), None)
    return candidate if candidate is not None and binary_runs(candidate) else None


def binary_runs(candidate: Path) -> bool:
    try:
        result = subprocess.run([str(candidate), "--version"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        return result.returncode == 0 and result.stdout.startswith("mailleur-core ")
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_core() -> Path:
    existing = native_binary()
    if existing:
        print(f"Noyau Rust disponible : {existing}", flush=True)
        return existing
    cargo = shutil.which("cargo")
    if not cargo:
        home_cargo = Path.home() / ".cargo" / "bin" / ("cargo.exe" if sys.platform == "win32" else "cargo")
        if home_cargo.is_file():
            cargo = str(home_cargo)
    if not cargo:
        raise ValueError("Aucun noyau Rust compilé pour cet ordinateur et Cargo est absent. "
                         "Installer la chaîne Rust depuis https://rustup.rs, ouvrir un nouveau "
                         "terminal puis relancer ce script. La chaîne Rust n'est pas installée automatiquement.")
    manifest = ROOT / "rust_core" / "Cargo.toml"
    if not manifest.is_file():
        raise ValueError("Les sources rust_core/Cargo.toml sont absentes. "
                         "Extraire l'archive source complète avant l'installation.")
    print("Compilation du noyau Rust. Cargo peut télécharger ses dépendances lors de ce premier calcul.", flush=True)
    run([cargo, "build", "--release", "--locked", "--manifest-path", str(manifest),
         "--target-dir", str(ROOT / "rust_core" / "target")])
    compiled = native_binary()
    if compiled is None:
        raise ValueError("La compilation s'est terminée sans produire rust_core/target/release/mailleur-core.")
    return compiled


def run(command: list[str]) -> None:
    print("\n> " + " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


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
