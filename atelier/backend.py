"""Compatibility transport for projects; all numerical work runs in Rust.

Python validates form/JSON fields, copies files and launches the native process.
Rust prepares geometry, drives native Gmsh/OpenCASCADE, creates the mesh and
evaluates quality. There is no Python Gmsh fallback.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

from .projects import normalize_project as _normalize_project
from mailleur.io import write_json

MODES = {"planar": 2, "surface": 2, "volume": 3}


def normalize_project(project):
    """Read legacy JSON fields while keeping its historical metre default."""
    if not isinstance(project, dict):
        raise ValueError("Un projet doit être un objet JSON")
    data = deepcopy(project)
    version = data.get("version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("Version de projet non prise en charge ; version 1 attendue")
    data.setdefault("geometry", {"kind": "plate"})
    data.setdefault("units", {"input": "m", "internal": "m"})
    mesh = data.setdefault("mesh", {})
    if isinstance(mesh, dict) and "min_size" not in mesh:
        size = mesh.get("size", .5)
        if type(size) in (int, float) and size > 0:
            mesh["min_size"] = min(.1, size)
    result = _normalize_project(data)
    if result["mode"] == "planar" and result["geometry"]["kind"] in ("box", "sphere", "bracket"):
        raise ValueError("Choisir le mode surface ou volume pour cette géométrie 3D")
    if result["mode"] == "volume" and result["mesh"]["recombine"]:
        raise ValueError("La recombinaison quadrangulaire est réservée aux surfaces")
    return result


def _load_gmsh():
    raise RuntimeError("Le calcul Gmsh par Python a été retiré. Utiliser le noyau Rust via NativeSession.")


def run_project(project: dict, workdir: Path, source: Path | None = None) -> dict:
    """Launch the native kernel and expose its files at the compatibility path."""
    from .native import NativeSession
    normalized = normalize_project(project)
    directory = Path(workdir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = ("mesh.msh", "mesh.state.msh", "model.brep", "report.json", "preview.json",
                 "project.normalized.json", "replay.json")
    if source is not None and Path(source).expanduser().resolve() in {
            directory / name for name in artifacts}:
        raise ValueError("Le fichier source doit être distinct du dossier de résultats afin de préserver l'original")
    for name in artifacts:
        (directory / name).unlink(missing_ok=True)
    session = NativeSession(directory / "native_runtime")
    try:
        result = session.generate(normalized, source=source)
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    for file in Path(result["directory"]).iterdir():
        target = directory / file.name
        if file.is_dir():
            shutil.copytree(file, target, dirs_exist_ok=True)
        else:
            shutil.copy2(file, target)
    report = result["report"]
    # Rename metadata only; the counts themselves were computed by Rust.
    diagnostics = report.get("geometry_diagnostics", {})
    counts = diagnostics.get("entity_counts", {})
    diagnostics.setdefault("volumes", counts.get("3", 0))
    source_format = diagnostics.get("source_format")
    if source_format and not source_format.startswith("."):
        diagnostics["source_format"] = "." + source_format
    algorithms = report.get("algorithms", {})
    algorithms.setdefault("volume_name", {4: "Netgen frontal", 1: "Delaunay"}.get(algorithms.get("volume")))
    write_json(directory / "report.json", report)
    return report
