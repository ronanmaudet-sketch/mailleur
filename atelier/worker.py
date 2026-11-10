"""Processus de calcul isolé, lancé par la CLI et l'application locale."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import traceback
import zipfile

from mailleur.io import write_json
from .projects import normalize_project, strict_json


def _mirror_native(directory: Path, result: dict):
    """Expose native artifacts where the HTTP job download routes expect them."""
    source = Path(result["directory"]).resolve()
    if source == directory:
        return
    for name in ("report.json", "preview.json", "mesh.msh", "model.brep", "guide.json",
                 "project.normalized.json", "session.json", "experience.json", "native.log"):
        file = source / name
        if file.is_file() and not file.is_symlink():
            shutil.copyfile(file, directory / name)
    if (source / "source").is_dir():
        shutil.copytree(source / "source", directory / "source", dirs_exist_ok=True)


def _native_exports(session, result, directory, project, formats):
    from .formats import FORMATS
    output = directory / "exports"
    output.mkdir(exist_ok=True)
    unit = project.get("units", {}).get("input", "mm")
    planar = project["mode"] == "planar"
    native_directory = Path(result["directory"])
    if formats is None:
        exports = session.export(native_directory, output / "exports.zip", "bundle", units=unit, planar=planar)
        with zipfile.ZipFile(output / "exports.zip") as archive:
            for info in archive.infolist():
                target = (output / info.filename).resolve()
                if not target.is_relative_to(output.resolve()) or info.is_dir():
                    raise ValueError("Chemin d'export inattendu dans le paquet natif")
                archive.extract(info, output)
    else:
        if not isinstance(formats, list) or not formats or any(name not in FORMATS for name in formats):
            raise ValueError("Sélection de formats vide ou inconnue")
        results = []
        for name in dict.fromkeys(formats):
            try:
                converted = session.export(native_directory, output, name, units=unit, planar=planar)
                converted["file"] = Path(converted["file"]).name
                results.append(converted)
            except (OSError, ValueError, RuntimeError) as error:
                results.append({"format": name, "status": "error", "error": str(error)})
        exports = {"schema": "atelier.exports.v1", "results": results,
                   "complete": all(item.get("status") == "ok" for item in results), "archive": "exports.zip",
                   "units": unit, "coordinate_conversion_backend": "rust"}
        write_json(output / "export_manifest.json", exports)
        with zipfile.ZipFile(output / "exports.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for file in output.iterdir():
                if file.is_file() and file.name != "exports.zip": archive.write(file, file.name)
    with zipfile.ZipFile(output / "exports.zip", "a", zipfile.ZIP_DEFLATED) as archive:
        archive.write(directory / "report.json", "quality_report.json")
        archive.writestr("project.json", json.dumps(project, ensure_ascii=False, indent=2, allow_nan=False))
    exports["archive"] = "exports.zip"
    return exports


def execute(request: dict) -> dict:
    directory = Path(request["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    def progress(message):
        write_json(directory / "progress.json", {"message": message})
    from .native import NativeSession
    workspace = Path(request.get("workspace") or directory / "_native_session").resolve()
    session = NativeSession(workspace, timeout=request.get("timeout", 300))
    operation = request.get("operation", "mesh")
    if operation == "train":
        progress("Évaluation des formes de démonstration et entraînement dans le noyau Rust…")
        native = session.train(project=request.get("project"), epochs=request.get("epochs", 200), seed=request.get("seed", 42),
                               cases=request.get("cases"))
        _mirror_native(directory, native)
        report = native["report"]
        if request.get("model_path"):
            destination = Path(request["model_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if (directory / "guide.json").resolve() != destination.resolve():
                temporary = destination.with_suffix(".worker.tmp")
                shutil.copyfile(directory / "guide.json", temporary)
                temporary.replace(destination)
        result = {"report": report, "kind": "training"}
    elif operation in ("mesh", "learn"):
        project = normalize_project(request["project"])
        progress("Préparation de la géométrie et génération du maillage…")
        source = Path(request["source"]) if request.get("source") else None
        if operation == "learn":
            progress("Recherche bornée sur la pièce et apprentissage cumulatif dans le noyau Rust…")
            search = request.get("search", {})
            native = session.learn(project, source=source, max_trials=search.get("max_trials", 12),
                                   epochs=search.get("epochs", 100), seed=search.get("seed", 42),
                                   use_memory=search.get("use_memory", True), max_seconds=search.get("max_seconds", 240),
                                   memory_dir=request.get("memory_dir"))
        else:
            native = session.generate(project, source=source, use_advisor=bool(request.get("use_advisor")))
        _mirror_native(directory, native)
        report = native["report"]
        project = report.get("selected_project", project)
        write_json(directory / "project.normalized.json", project)
        if report.get("quality", {}).get("accepted") is False:
            raise ValueError("Le maillage ne respecte pas les critères de qualité ; aucun export n'est proposé")
        progress("Contrôle de la qualité et conversion native des unités avant export…")
        exports = _native_exports(session, native, directory, project, request.get("formats"))
        result = {"report": report, "exports": exports, "advisor": report.get("advisor"),
                  "kind": "learning" if operation == "learn" else "mesh"}
    else:
        raise ValueError("Opération de calcul inconnue")
    write_json(directory / "result.json", result)
    progress("Calcul terminé")
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage interne : python -m atelier.worker requete.json", file=sys.stderr)
        return 2
    request = strict_json(Path(args[0]).read_bytes())
    try:
        execute(request)
        return 0
    except Exception as error:
        directory = Path(request["directory"])
        write_json(directory / "error.json", {"message": str(error), "type": type(error).__name__})
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
