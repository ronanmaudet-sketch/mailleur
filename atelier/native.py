"""Pont d'interface vers le noyau Rust ; aucun repli de calcul en Python."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile

from mailleur.io import atomic_write, write_json
from .core_locator import find_core
from .projects import normalize_project, safe_filename, strict_json

ROOT = Path(__file__).resolve().parents[1]
EXPERIENCE_DIRECTORY = "experience_quad_hex"
UNITS = {"m": 1.0, "cm": .01, "mm": .001, "in": .0254}


def file_digest(path: Path) -> bytes:
    """Compare source files without loading entire CAD files into the GUI process."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def core_path() -> Path:
    candidate = find_core(ROOT)
    if candidate is None:
        raise FileNotFoundError("Noyau Rust direct absent ou incompatible. Relancer scripts/install.py.")
    return candidate


def gmsh_library() -> Path:
    if os.environ.get("GMSH_LIBRARY"):
        candidate = Path(os.environ["GMSH_LIBRARY"]).expanduser()
        if candidate.is_file(): return candidate.resolve()
        raise FileNotFoundError("La bibliothèque indiquée par GMSH_LIBRARY n'existe pas")
    try:
        distribution = importlib.metadata.distribution("gmsh")
        for entry in distribution.files or []:
            name = entry.name.lower()
            if (name.startswith("libgmsh") and (".so" in name or ".dylib" in name)) or name == "gmsh.dll":
                candidate = Path(distribution.locate_file(entry))
                if candidate.is_file(): return candidate.resolve()
    except importlib.metadata.PackageNotFoundError:
        pass
    for folder in (Path(sys.prefix) / "lib", Path(sys.prefix) / "Library" / "bin", Path(sys.prefix) / "DLLs"):
        if folder.exists():
            for candidate in folder.glob("*gmsh*"):
                if candidate.is_file() and any(s in candidate.name for s in (".so", ".dylib", ".dll")):
                    return candidate.resolve()
    raise FileNotFoundError("Bibliothèque native Gmsh introuvable. Relancer l'installation des dépendances.")


def native_project(project):
    data = normalize_project(project)
    units = data.setdefault("units", {"input": "mm", "internal": "m"})
    if not isinstance(units, dict) or units.get("input", "mm") not in UNITS:
        raise ValueError("Unité d'entrée attendue : mm, cm, m ou in")
    units.setdefault("input", "mm")
    units["internal"] = "m"
    return data


class NativeSession:
    def __init__(self, workspace: Path, timeout=300):
        self.workspace = Path(workspace).expanduser().resolve()
        (self.workspace / "native").mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._process = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self.last_directory = None
        self.last_attempt_directory = None

    def cancel(self):
        self._cancelled.set()

    def _call(self, request, directory, *, needs_gmsh=True):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Un traitement est déjà en cours dans cet atelier")
        try:
            self._cancelled.clear()
            if needs_gmsh:
                request["gmsh_library"] = str(gmsh_library())
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            request["response_mode"] = "summary"
            file = atomic_write(directory / "native_request.json",
                                json.dumps(request, ensure_ascii=False, allow_nan=False,
                                           separators=(",", ":")) + "\n")
            started = time.monotonic()
            with (directory / "native.log").open("w", encoding="utf-8") as stream:
                process = subprocess.Popen([str(core_path()), "--request", str(file)], stdout=stream, stderr=subprocess.STDOUT)
                self._process = process
                while process.poll() is None:
                    if self._cancelled.is_set() or time.monotonic() - started > self.timeout:
                        process.terminate()
                        try: process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait(timeout=3)
                        if self._cancelled.is_set(): raise RuntimeError("Traitement annulé")
                        raise RuntimeError("Délai maximal atteint. Réduire la densité du maillage ou simplifier la sélection.")
                    try:
                        process.wait(timeout=.02)
                    except subprocess.TimeoutExpired:
                        pass
            if process.returncode:
                message = "Le noyau Rust a refusé le traitement"
                for line in reversed((directory / "native.log").read_text(encoding="utf-8", errors="replace").splitlines()):
                    try:
                        payload = strict_json(line)
                        if isinstance(payload, dict) and payload.get("error"):
                            message = payload["error"]; break
                    except ValueError: pass
                raise RuntimeError(message)
        finally:
            self._process = None
            self._lock.release()

    def _run(self, operation, project, source=None, previous_directory=None, use_advisor=False, extra_request=None):
        project = native_project(project)
        directory = self.workspace / "native" / uuid.uuid4().hex
        directory.mkdir()
        request = {"operation": operation, "project": project, "output_dir": str(directory)}
        if extra_request:
            request.update(extra_request)
        if use_advisor:
            model = self.workspace / "models" / "guide.json"
            if model.is_file(): request["advisor_model"] = str(model)
        if previous_directory is not None:
            previous = Path(previous_directory).resolve()
            metadata = strict_json((previous / "session.json").read_bytes())
            previous_report = strict_json((previous / "report.json").read_bytes())
            if previous_report.get("display_only"):
                raise ValueError("Générer d'abord un maillage de calcul ; la tessellation d'examen ne sert qu'à la visualisation")
            old_project = metadata["project"]
            for key in ("geometry", "units", "mode"):
                if project.get(key) != old_project.get(key):
                    raise ValueError("La géométrie, les unités et le type de domaine doivent être inchangés pour un remaillage local. Générer un nouveau maillage après modification.")
            if source is None and metadata.get("source"):
                source = previous / metadata["source"]
            elif source is not None and metadata.get("source"):
                saved_source = previous / metadata["source"]
                supplied_source = Path(source).expanduser().resolve()
                if (saved_source.stat().st_size != supplied_source.stat().st_size
                        or file_digest(saved_source) != file_digest(supplied_source)):
                    raise ValueError("La source géométrique a changé ; générer un nouveau maillage avant un remaillage local")
            request.update(previous_mesh=str(previous / "mesh.msh"), previous_model=str(previous / "model.brep"),
                           previous_project=old_project, previous_report=str(previous / "report.json"))
        relative_source = None
        if source is not None:
            source = Path(source).expanduser().resolve()
            filename = safe_filename(source.name)
            if not source.is_file(): raise ValueError("Fichier source introuvable")
            source_dir = directory / "source"
            source_dir.mkdir()
            copied = source_dir / filename
            shutil.copy2(source, copied)
            relative_source = "source/" + filename
            request["source"] = str(copied)
        if project["geometry"]["kind"] == "import" and not relative_source:
            raise ValueError("Joindre une source CAO pour la géométrie importée")
        write_json(directory / "session.json", {"project": project, "source": relative_source, "operation": operation,
                                                  "created_at": datetime.now(timezone.utc).isoformat()})
        self.last_attempt_directory = directory
        self._call(request, directory)
        report = strict_json((directory / "report.json").read_bytes())
        preview = strict_json((directory / "preview.json").read_bytes())
        if operation == "learn":
            chosen = report.get("experience", {}).get("chosen_project")
            if not isinstance(chosen, dict):
                raise RuntimeError("Le noyau n'a pas fourni la recette de maillage retenue")
            chosen = native_project(chosen)
            report["selected_project"] = chosen
            write_json(directory / "session.json", {"project": chosen, "source": relative_source,
                       "operation": operation, "created_at": datetime.now(timezone.utc).isoformat()})
            write_json(directory / "report.json", report)
        result = {"report": report, "preview": preview, "directory": str(directory)}
        self.last_directory = directory
        return result

    def _convert(self, source, target, format_name, planar, directory):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Un traitement est déjà en cours")
        try:
            self._cancelled.clear()
            request = write_json(directory / "conversion_request.json", {"source": str(source), "target": str(target),
                                    "format": format_name, "planar": bool(planar), "directory": str(directory)})
            environment = os.environ.copy()
            environment.update(ATELIER_NATIVE_EXPORT="1", ATELIER_CORE=str(core_path()), GMSH_LIBRARY=str(gmsh_library()))
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            started = time.monotonic()
            separate_group = os.name != "nt" and os.environ.get("ATELIER_WORKER_PROCESS_GROUP") != "1"
            with (directory / "conversion.log").open("wb") as log:
                process = subprocess.Popen([sys.executable, "-m", "atelier.convert_worker", str(request)],
                                           stdout=log, stderr=subprocess.STDOUT, env=environment,
                                           start_new_session=separate_group)
                self._process = process
                while process.poll() is None:
                    if self._cancelled.is_set() or time.monotonic() - started > self.timeout:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                        elif separate_group:
                            try: os.killpg(process.pid, signal.SIGTERM)
                            except ProcessLookupError: pass
                        else:
                            process.terminate()
                        try: process.wait(timeout=3)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
                        raise RuntimeError("Export annulé" if self._cancelled.is_set() else "Délai maximal d'export atteint")
                    try:
                        process.wait(timeout=.02)
                    except subprocess.TimeoutExpired:
                        pass
            if process.returncode:
                error_file = directory / "conversion_error.json"
                message = strict_json(error_file.read_bytes()).get("error") if error_file.is_file() else f"Le processus de conversion a échoué ({process.returncode}) ; l'interface est conservée."
                raise RuntimeError(message)
            return strict_json((directory / "conversion.json").read_bytes())
        finally:
            self._process = None
            self._lock.release()

    def inspect(self, project, source=None):
        return self._run("inspect", project, source)

    def generate(self, project, source=None, use_advisor=False):
        learning = project.get("learning", {}) if isinstance(project, dict) else {}
        if not isinstance(learning, dict):
            raise ValueError("learning doit être un objet")
        if learning.get("enabled", False):
            return self.learn(project, source, max_trials=learning.get("max_trials", 12),
                              epochs=learning.get("epochs", 100), use_memory=learning.get("use_memory", True),
                              max_seconds=learning.get("max_seconds", 240))
        result = self._run("mesh", project, source, use_advisor=use_advisor)
        if use_advisor and "advisor" not in result["report"]:
            result["report"]["warnings"] = list(result["report"].get("warnings", [])) + [
                "Aucun guide validé n'est installé pour le noyau Rust ; l'algorithme explicitement choisi est conservé."]
            write_json(Path(result["directory"]) / "report.json", result["report"])
        return result

    def learn(self, project, source=None, *, max_trials=12, epochs=100, seed=42,
              use_memory=True, max_seconds=240, memory_dir=None):
        """Search and learn in Rust on this actual geometry, retaining validated recipes."""
        for name, value, maximum in (("max_trials", max_trials, 64), ("epochs", epochs, 200),
                                      ("max_seconds", max_seconds, 86400)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} doit être un entier de 1 à {maximum}")
        if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
            raise ValueError("seed doit être un entier de 0 à 4294967295")
        if type(use_memory) is not bool:
            raise ValueError("use_memory doit être vrai ou faux")
        project = native_project(project)
        project.setdefault("quality_policy", {"minimum_quality": .05, "poor_threshold": .2, "strict": True})
        memory = Path(memory_dir).expanduser().resolve() if memory_dir is not None else self.workspace / EXPERIENCE_DIRECTORY
        extra = {
            "memory_dir": str(memory),
            "search": {"max_trials": max_trials, "epochs": epochs, "seed": seed,
                       "use_memory": use_memory, "max_seconds": max_seconds},
        }
        corpus_model = self.workspace / "models" / "corpus.json"
        if corpus_model.is_file():
            extra["corpus_model"] = str(corpus_model)
        return self._run("learn", project, source, extra_request=extra)

    def memory_info(self, memory_dir=None):
        memory = Path(memory_dir).expanduser().resolve() if memory_dir is not None else self.workspace / EXPERIENCE_DIRECTORY
        directory = self.workspace / "native" / uuid.uuid4().hex
        directory.mkdir()
        self._call({"operation": "memory_info", "memory_dir": str(memory),
                    "output_dir": str(directory)}, directory, needs_gmsh=False)
        return strict_json((directory / "report.json").read_bytes())

    def query(self, operation, **payload):
        """Transport JSON only: numerical operations execute in the Rust process."""
        allowed = {"metrics", "advisor_features", "advisor_choose", "advisor_default_cases",
                   "corpus_plan", "corpus_status", "corpus_preview", "corpus_run", "corpus_train"}
        if operation not in allowed:
            raise ValueError("Opération de requête native inconnue")
        directory = self.workspace / "native" / uuid.uuid4().hex
        directory.mkdir()
        request = dict(payload)
        request.update(operation=operation, output_dir=str(directory))
        self.last_attempt_directory = directory
        self._call(request, directory, needs_gmsh=operation == "corpus_run")
        report = strict_json((directory / "report.json").read_bytes())
        return report

    def _corpus_directory(self, corpus_dir):
        return Path(corpus_dir).expanduser().resolve() if corpus_dir is not None else self.workspace / "corpus"

    def corpus_plan(self, corpus_dir=None, *, structured=250000, random=750000,
                    seed=42, strategies=4, shard_size=1000, profile="direct_quad_hex"):
        return self.query("corpus_plan", corpus_dir=str(self._corpus_directory(corpus_dir)),
                          plan={"profile": profile, "structured": structured, "random": random, "seed": seed,
                                "strategies": strategies, "shard_size": shard_size})

    def corpus_status(self, corpus_dir=None):
        return self.query("corpus_status", corpus_dir=str(self._corpus_directory(corpus_dir)))

    def corpus_preview(self, corpus_dir=None, *, start_index=0, limit=20):
        return self.query("corpus_preview", corpus_dir=str(self._corpus_directory(corpus_dir)),
                          start_index=start_index, limit=limit)

    def corpus_run(self, corpus_dir=None, *, limit=20, max_seconds=240, retain_meshes=False):
        return self.query("corpus_run", corpus_dir=str(self._corpus_directory(corpus_dir)),
                          run={"limit": limit, "max_seconds": max_seconds, "retain_meshes": retain_meshes})

    def corpus_train(self, corpus_dir=None, *, epochs=3, batch_size=128, max_records=10000, max_seconds=240):
        corpus = self._corpus_directory(corpus_dir)
        report = self.query("corpus_train", corpus_dir=str(corpus),
                            training={"epochs": epochs, "batch_size": batch_size,
                                      "max_records": max_records, "max_seconds": max_seconds})
        model = corpus / "models" / "current.json"
        if model.is_file() and report.get("element_policy") == "direct_quad_hex":
            destination = self.workspace / "models" / "corpus.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name("corpus_" + uuid.uuid4().hex + ".tmp")
            shutil.copyfile(model, temporary)
            temporary.replace(destination)
        return report

    def remesh(self, project, source=None, previous_directory=None):
        previous = previous_directory or self.last_directory
        if previous is None: raise ValueError("Générer d'abord le maillage à reprendre")
        return self._run("remesh", project, source, previous_directory=previous)

    def train(self, project=None, *, epochs=200, seed=42, cases=None):
        directory = self.workspace / "native" / uuid.uuid4().hex
        directory.mkdir()
        request = {"operation": "train", "output_dir": str(directory), "epochs": epochs, "seed": seed}
        if project is not None:
            normalized = native_project(project)
            request["training_mesh"] = normalized["mesh"]
            request["training_units"] = normalized["units"]["input"]
        if cases is not None: request["cases"] = cases
        self._call(request, directory)
        report = strict_json((directory / "report.json").read_bytes())
        destination = self.workspace / "models" / "guide.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name("guide_" + uuid.uuid4().hex + ".tmp")
        shutil.copyfile(directory / "guide.json", temporary)
        temporary.replace(destination)
        return {"kind": "training", "report": report, "directory": str(directory)}

    def export(self, directory: Path, output: Path, format_name: str, units="mm", planar=False):
        from .formats import FORMATS
        if units not in UNITS: raise ValueError("Unité d'export inconnue")
        directory, output = Path(directory).resolve(), Path(output).expanduser().resolve()
        source = directory / "mesh.msh"
        if not source.is_file(): raise ValueError("Ce traitement ne contient pas de maillage à exporter")
        report = strict_json((directory / "report.json").read_bytes())
        if report.get("display_only"):
            raise ValueError("Générer un maillage avant l'export ; l'examen géométrique produit seulement une tessellation d'affichage")
        if report.get("accepted") is False or report.get("quality", {}).get("accepted") is False:
            raise ValueError("Le maillage ne respecte pas les critères de qualité ; corriger ou relancer l'apprentissage avant l'export")
        export_dir = directory / ("export_" + uuid.uuid4().hex)
        export_dir.mkdir()
        scaled = export_dir / "scaled.msh"
        self._call({"operation": "rescale", "source_mesh": str(source), "output_mesh": str(scaled), "factor": 1.0 / UNITS[units]}, export_dir)
        if format_name in ("bundle", "all", "zip"):
            result = self._convert(scaled, export_dir / "formats", "bundle", planar, export_dir)
            if output.suffix.lower() != ".zip": output = output / "maillage_exports.zip"
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(export_dir / "formats" / result["archive"], output)
            with zipfile.ZipFile(output, "a", zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("units.json", json.dumps({"internal": "m", "export": units, "coordinate_factor": 1.0 / UNITS[units], "conversion_backend": "rust"}, indent=2))
                bundle.write(directory / "report.json", "quality_report_internal_SI.json")
            result["file"] = str(output)
        else:
            if format_name not in FORMATS: raise ValueError("Format d'export inconnu")
            extension = FORMATS[format_name]["extension"]
            target = output if output.suffix.lower() == extension else output / ("mesh" + extension)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.resolve() == source: raise ValueError("L'export doit être distinct du maillage interne")
            result = self._convert(scaled, target, format_name, planar, export_dir)
            result["file"] = str(target)
            write_json(target.with_name(target.name + ".units.json"), {"internal": "m", "export": units, "coordinate_factor": 1.0 / UNITS[units], "conversion_backend": "rust"})
        result["units"] = units
        result["coordinate_conversion_backend"] = "rust"
        write_json(export_dir / "export_report.json", result)
        return result
