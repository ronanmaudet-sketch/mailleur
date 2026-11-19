"""Sélection du noyau direct, commune au lanceur et au client Python."""
from functools import lru_cache
import os
from pathlib import Path
import platform
import subprocess
import sys


ENGINE_ID = "mailleur.structured-direct.quad-hex.v1"


@lru_cache(maxsize=16)
def _identifies_as_direct(path, identity):
    # Le stat dans la clé invalide le résultat après un remplacement du binaire.
    probe = subprocess.run([str(path), "--engine-id"], capture_output=True, timeout=5)
    return probe.returncode == 0 and probe.stdout.strip() == ENGINE_ID.encode("ascii")


def find_core(root: Path) -> Path | None:
    override = os.environ.get("ATELIER_CORE")
    name = "mailleur-core.exe" if sys.platform == "win32" else "mailleur-core"
    system = f"{sys.platform}-{platform.machine().lower()}"
    candidates = [Path(override).expanduser()] if override else [
        root / "rust_core" / "target" / "release" / name,
        root / "rust_core" / "bin" / system / name,
    ]
    for path in candidates:
        try:
            path = path.resolve(strict=True)
            stat = path.stat()
            if _identifies_as_direct(path, (stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_size)):
                return path
        except (OSError, subprocess.TimeoutExpired):
            pass
    if override:
        raise ValueError(f"ATELIER_CORE doit désigner le noyau à génération directe compatible : {override}")
    return None
