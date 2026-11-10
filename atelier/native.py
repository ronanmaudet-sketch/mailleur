"""Pont d'interface vers le noyau Rust ; aucun repli de calcul en Python."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile

from mailleur.io import atomic_write, write_json
from .projects import normalize_project, safe_filename, strict_json
from .import_registry import CAD_EXTENSIONS, NATIVE_GMSH_EXTENSIONS, source_extension

ROOT = Path(__file__).resolve().parents[1]
UNITS = {"m": 1.0, "cm": .01, "mm": .001, "in": .0254}


def _bounded_operation(method):
    """Un budget et une annulation pour toute l'opération, pas par sous-processus."""
    @wraps(method)
    def execute(self, *args, **kwargs):
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("Un traitement est déjà en cours dans cet atelier")
        outermost = self._operation_depth == 0
        try:
            if outermost:
                self._cancelled.clear()
                self._deadline = time.monotonic() + self.timeout
            self._operation_depth += 1
            try:
                self._ensure_active()
                result = method(self, *args, **kwargs)
                self._ensure_active()
                return result
            finally:
                self._operation_depth -= 1
        finally:
            if outermost:
                self._deadline = None
            self._operation_lock.release()
    return execute


def file_digest(path: Path) -> bytes:
    """Compare source files without loading entire CAD files into the GUI process."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def platform_key():
    return f"{sys.platform}-{platform.machine().lower()}"


def core_path() -> Path:
    name = "mailleur-core.exe" if sys.platform == "win32" else "mailleur-core"
    candidates = []
    if os.environ.get("ATELIER_CORE"):
        candidates.append(Path(os.environ["ATELIER_CORE"]).expanduser())
    candidates += [ROOT / "rust_core" / "target" / "release" / name,
                   ROOT / "rust_core" / "bin" / platform_key() / name]
    for candidate in candidates:
        if candidate.is_file(): return candidate.resolve()
    raise FileNotFoundError("Noyau Rust absent. Lancer l'installation ou cargo build --release --manifest-path rust_core/Cargo.toml.")


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
        self._operation_lock = threading.RLock()
        self._operation_depth = 0
        self._deadline = None
        self.last_directory = None
        self.last_attempt_directory = None

    def cancel(self):
        self._cancelled.set()

    def _ensure_active(self, label="Traitement", deadline=None):
        if self._cancelled.is_set():
            raise RuntimeError(f"{label} annulé")
        limit = self._deadline if self._deadline is not None else deadline
        if limit is not None and time.monotonic() >= limit:
            raise RuntimeError(f"Délai maximal atteint ({label.lower()})")

    @staticmethod
    def _stop_process_tree(process, separate_group):
        """Nettoyer le groupe propre, même si son leader est déjà sorti.

        Le worker HTTP partage le groupe contrôlé par JobManager : seul ce
        gestionnaire peut tuer ce groupe, jamais un de ses enfants.
        """
        if process is None:
            return
        try:
            if os.name == "nt":
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            elif separate_group:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
            elif process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name != "nt" and separate_group:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                else:
                    process.kill()
                process.wait(timeout=3)
        finally:
            # Le leader peut avoir obéi à TERM alors qu'un descendant l'ignore.
            if os.name != "nt" and separate_group:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def _call(self, request, directory, *, needs_gmsh=True):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Un traitement est déjà en cours dans cet atelier")
        process = None
        separate_group = os.name != "nt" and os.environ.get("ATELIER_WORKER_PROCESS_GROUP") != "1"
        try:
            self._ensure_active()
            if needs_gmsh:
                request["gmsh_library"] = str(gmsh_library())
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            request["response_mode"] = "summary"
            file = atomic_write(directory / "native_request.json",
                                json.dumps(request, ensure_ascii=False, allow_nan=False,
                                           separators=(",", ":")) + "\n")
            deadline = self._deadline or (time.monotonic() + self.timeout)
            with (directory / "native.log").open("w", encoding="utf-8") as stream:
                process = subprocess.Popen([str(core_path()), "--request", str(file)], stdout=stream,
                                           stderr=subprocess.STDOUT, start_new_session=separate_group)
                self._process = process
                while process.poll() is None:
                    self._ensure_active(deadline=deadline)
                    try:
                        process.wait(timeout=.02)
                    except subprocess.TimeoutExpired:
                        pass
            self._ensure_active(deadline=deadline)
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
            try:
                self._stop_process_tree(process, separate_group)
            finally:
                self._process = None
                self._lock.release()

    @_bounded_operation
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
        prepared_source = None
        import_report = None
        if source is not None:
            source = Path(source).expanduser()
            if source.is_symlink():
                raise ValueError("La source ne doit pas être un lien symbolique")
            source = source.resolve()
            filename = safe_filename(source.name)
            from .imports import check_regular_source
            check_regular_source(source)
            source_dir = directory / "source"
            source_dir.mkdir()
            copied = source_dir / filename
            shutil.copy2(source, copied)
            relative_source = "source/" + filename
            request["source"] = str(copied)
            if source_extension(filename) not in CAD_EXTENSIONS | {".stl"}:
                if previous_directory is not None and metadata.get("prepared_source"):
                    prior_prepared = previous / metadata["prepared_source"]
                    if (not prior_prepared.is_file() or prior_prepared.is_symlink()
                            or file_digest(prior_prepared).hex() != metadata.get("prepared_source_sha256")):
                        raise ValueError("La source préparée du remaillage est absente ou altérée ; générer un nouveau maillage")
                    prepared_dir = directory / "prepared"
                    prepared_dir.mkdir()
                    canonical = prepared_dir / prior_prepared.name
                    shutil.copyfile(prior_prepared, canonical)
                    import_report = metadata.get("import_preparation")
                else:
                    canonical, import_report = self._prepare_import(copied, directory / "prepared", project["units"]["input"])
                request["source"] = str(canonical)
                prepared_source = str(canonical.relative_to(directory))
        if project["geometry"]["kind"] == "import" and not relative_source:
            raise ValueError("Joindre une source CAO pour la géométrie importée")
        session_metadata = {"project": project, "source": relative_source, "operation": operation,
                            "created_at": datetime.now(timezone.utc).isoformat()}
        if prepared_source is not None:
            session_metadata.update(prepared_source=prepared_source,
                                    prepared_source_sha256=file_digest(directory / prepared_source).hex(),
                                    import_preparation=import_report)
        write_json(directory / "session.json", session_metadata)
        self.last_attempt_directory = directory
        self._call(request, directory)
        report = strict_json((directory / "report.json").read_bytes())
        preview = strict_json((directory / "preview.json").read_bytes())
        if import_report is not None:
            report["import_preparation"] = import_report
            warnings = import_report.get("warnings", []) + import_report.get("metadata", {}).get("warnings", [])
            warnings += import_report.get("native_preparation", {}).get("warnings", [])
            report["warnings"] = list(dict.fromkeys(report.get("warnings", []) + warnings))
            write_json(directory / "report.json", report)
        if operation == "learn":
            chosen = report.get("experience", {}).get("chosen_project")
            if not isinstance(chosen, dict):
                raise RuntimeError("Le noyau n'a pas fourni la recette de maillage retenue")
            chosen = native_project(chosen)
            report["selected_project"] = chosen
            session_metadata["project"] = chosen
            write_json(directory / "session.json", session_metadata)
            write_json(directory / "report.json", report)
        result = {"report": report, "preview": preview, "directory": str(directory)}
        self.last_directory = directory
        return result

    def _prepare_import(self, source, directory, input_unit):
        """Lire/convertir un fichier isolément, puis préparer sa géométrie en Rust."""
        directory.mkdir(parents=True, exist_ok=True)
        if source_extension(source.name) == ".unv":
            canonical = directory / "source.msh"
            native_dir = directory / "normalization"
            native_dir.mkdir()
            self._call({"operation": "prepare_mesh_import", "source_mesh": str(source),
                        "output_mesh": str(canonical), "output_dir": str(native_dir)}, native_dir, needs_gmsh=True)
            report = {"schema": "atelier.import_preparation.v1", "source_format": ".unv", "kind": "native_mesh",
                      "original_preserved": True, "warnings": [],
                      "native_preparation": strict_json((native_dir / "report.json").read_bytes())}
            write_json(directory / "import.json", report)
            return canonical, report
        request = write_json(directory / "import_request.json", {
            "source": str(source), "directory": str(directory), "input_unit": input_unit})
        self._run_file_worker("atelier.import_worker", request, directory,
                              result_name="import.json", error_name="import_error.json", log_name="import.log")
        report = strict_json((directory / "import.json").read_bytes())
        prepared = Path(report["file"]).resolve()
        if report["kind"] == "mesh_dto":
            native_dir = directory / "normalization"
            native_dir.mkdir()
            canonical = directory / "source.msh"
            self._call({"operation": "prepare_mesh_import", "mesh_data": str(prepared),
                        "output_mesh": str(canonical), "output_dir": str(native_dir)}, native_dir, needs_gmsh=False)
            report["native_preparation"] = strict_json((native_dir / "report.json").read_bytes())
            prepared = canonical
        if not prepared.is_relative_to(directory.resolve()) or not prepared.is_file():
            raise ValueError("Le lecteur n'a pas produit un fichier préparé dans sa session")
        # Large decoded DTOs are temporary I/O. The original and canonical
        # source remain available; no million-node JSON copy is kept per job.
        dto = directory / "mesh_input.json"
        if dto.exists() and prepared != dto:
            dto.unlink()
            report["decoded_data_retained"] = False
        report["canonical_file"] = str(prepared)
        write_json(directory / "import.json", report)
        return prepared, report

    def _run_file_worker(self, module, request, directory, *, result_name, error_name, log_name):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Un traitement est déjà en cours")
        process = None
        separate_group = os.name != "nt" and os.environ.get("ATELIER_WORKER_PROCESS_GROUP") != "1"
        try:
            self._ensure_active("Import")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            deadline = self._deadline or (time.monotonic() + self.timeout)
            with (directory / log_name).open("wb") as log:
                process = subprocess.Popen([sys.executable, "-m", module, str(request)], env=environment,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=separate_group)
                self._process = process
                while process.poll() is None:
                    self._ensure_active("Import", deadline)
                    try: process.wait(timeout=.02)
                    except subprocess.TimeoutExpired: pass
            self._ensure_active("Import", deadline)
            if process.returncode:
                error = directory / error_name
                message = strict_json(error.read_bytes()).get("error") if error.is_file() else "Le lecteur de fichier a échoué ; consulter son journal"
                raise RuntimeError(message)
            if not (directory / result_name).is_file():
                raise RuntimeError("Le lecteur n'a pas produit son rapport")
        finally:
            try:
                self._stop_process_tree(process, separate_group)
            finally:
                self._process = None
                self._lock.release()

    def _convert(self, source, target, format_name, planar, directory):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Un traitement est déjà en cours")
        process = None
        separate_group = os.name != "nt" and os.environ.get("ATELIER_WORKER_PROCESS_GROUP") != "1"
        try:
            self._ensure_active("Export")
            request = write_json(directory / "conversion_request.json", {"source": str(source), "target": str(target),
                                    "format": format_name, "planar": bool(planar), "directory": str(directory)})
            environment = os.environ.copy()
            environment.update(ATELIER_NATIVE_EXPORT="1", ATELIER_CORE=str(core_path()), GMSH_LIBRARY=str(gmsh_library()))
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            deadline = self._deadline or (time.monotonic() + self.timeout)
            with (directory / "conversion.log").open("wb") as log:
                process = subprocess.Popen([sys.executable, "-m", "atelier.convert_worker", str(request)],
                                           stdout=log, stderr=subprocess.STDOUT, env=environment,
                                           start_new_session=separate_group)
                self._process = process
                while process.poll() is None:
                    self._ensure_active("Export", deadline)
                    try:
                        process.wait(timeout=.02)
                    except subprocess.TimeoutExpired:
                        pass
            self._ensure_active("Export", deadline)
            if process.returncode:
                error_file = directory / "conversion_error.json"
                message = strict_json(error_file.read_bytes()).get("error") if error_file.is_file() else f"Le processus de conversion a échoué ({process.returncode}) ; l'interface est conservée."
                raise RuntimeError(message)
            return strict_json((directory / "conversion.json").read_bytes())
        finally:
            try:
                self._stop_process_tree(process, separate_group)
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
        memory = Path(memory_dir).expanduser().resolve() if memory_dir is not None else self.workspace / "experience"
        extra = {
            "memory_dir": str(memory),
            "search": {"max_trials": max_trials, "epochs": epochs, "seed": seed,
                       "use_memory": use_memory, "max_seconds": max_seconds},
        }
        corpus_model = self.workspace / "models" / "corpus.json"
        if corpus_model.is_file():
            extra["corpus_model"] = str(corpus_model)
        return self._run("learn", project, source, extra_request=extra)

    @_bounded_operation
    def memory_info(self, memory_dir=None):
        memory = Path(memory_dir).expanduser().resolve() if memory_dir is not None else self.workspace / "experience"
        directory = self.workspace / "native" / uuid.uuid4().hex
        directory.mkdir()
        self._call({"operation": "memory_info", "memory_dir": str(memory),
                    "output_dir": str(directory)}, directory, needs_gmsh=False)
        return strict_json((directory / "report.json").read_bytes())

    @_bounded_operation
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
                    seed=42, strategies=4, shard_size=1000):
        return self.query("corpus_plan", corpus_dir=str(self._corpus_directory(corpus_dir)),
                          plan={"structured": structured, "random": random, "seed": seed,
                                "strategies": strategies, "shard_size": shard_size})

    def corpus_status(self, corpus_dir=None, *, adopt_legacy_integrity=False):
        if type(adopt_legacy_integrity) is not bool:
            raise ValueError("adopt_legacy_integrity doit être booléen")
        return self.query("corpus_status", corpus_dir=str(self._corpus_directory(corpus_dir)),
                          adopt_legacy_integrity=adopt_legacy_integrity)

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
        if model.is_file():
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

    @_bounded_operation
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

    @_bounded_operation
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
