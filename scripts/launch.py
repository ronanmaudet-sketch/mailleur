"""Lancer l'interface de bureau PySide6 avec l'environnement du projet.

Les arguments sont transmis à `atelier desktop`.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def environment_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def environment_ready(executable: Path) -> bool:
    if not executable.is_file():
        return False
    check = subprocess.run(
        [str(executable), "-c", "import PySide6; from atelier.native import core_path; core_path()"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return check.returncode == 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    executable = environment_python()
    try:
        if not environment_ready(executable):
            bootstrap = str(executable) if executable.is_file() else sys.executable
            if not executable.is_file() and not (3, 11) <= sys.version_info[:2] < (3, 14):
                print("Installer Python 3.11, 3.12 ou 3.13, puis relancer.\n"
                      f"Interpréteur actuel : {sys.version.split()[0]}.", file=sys.stderr)
                return 2
            print("Installation ou mise à jour de l'interface et du noyau natif.\n"
                  "Une connexion Internet peut être nécessaire pendant cette étape.", flush=True)
            installed = subprocess.run([bootstrap, str(ROOT / "scripts/install.py")], cwd=ROOT)
            if installed.returncode:
                return installed.returncode
        print("Démarrage de l'interface de bureau Atelier Mailleur.\n"
              "Les calculs sont exécutés par le noyau Rust natif.", flush=True)
        return subprocess.run([str(executable), "-m", "atelier", "desktop", *args], cwd=ROOT).returncode
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"Lancement impossible : {error}\nRelancer python scripts/install.py "
              "pour vérifier l'installation.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
