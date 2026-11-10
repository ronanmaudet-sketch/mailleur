"""Application locale : API HTTP, projets portables et calculs bornés."""
from __future__ import annotations

import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import mimetypes
import os
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import uuid
import zipfile

from mailleur.io import write_json
from . import __version__
from .projects import MAX_UPLOAD, default_examples, load_portable_project, normalize_project, safe_filename, save_portable_project, strict_json


def _now():
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def backend_info():
    result = {"available": False, "version": None, "error": None}
    try:
        import meshio
        import h5py
        from .native import core_path, gmsh_library
        core_path()
        gmsh_library()
        result.update(available=True, version=importlib.metadata.version("gmsh"), meshio=meshio.__version__,
                      h5py=h5py.__version__, numerical_backend="rust", python_numerical_backend=False)
    except (ImportError, OSError, importlib.metadata.PackageNotFoundError) as error:
        result["error"] = f"Moteur indisponible : {error}. Relancer l'installation puis le diagnostic."
    return result


class JobManager:
    def __init__(self, workspace: Path, max_workers=1, timeout=300):
        if type(max_workers) is not int or not 1 <= max_workers <= 8:
            raise ValueError("Le nombre de calculs simultanés doit être entre 1 et 8")
        if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 86400:
            raise ValueError("Le délai maximal doit être compris entre 1 et 86400 secondes")
        self.workspace = Path(workspace).expanduser().resolve()
        for folder in ("jobs", "uploads", "models", "experience"):
            (self.workspace / folder).mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_workers = max_workers
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="atelier-calcul")
        self.jobs = {}
        self.processes = {}
        self.cancellations = {}
        self.closed = False
        for manifest in (self.workspace / "jobs").glob("*/job.json"):
            try:
                job = strict_json(manifest.read_bytes())
                identifier = manifest.parent.name
                self._check_id(identifier)
                if job.get("id") != identifier:
                    continue
                if job.get("status") in ("running", "queued"):
                    job.update(status="failed", message="Calcul interrompu lors de la fermeture précédente.")
                    write_json(manifest, job)
                self.jobs[identifier] = job
            except (ValueError, OSError):
                continue

    @staticmethod
    def _check_id(identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Identifiant invalide")

    def directory(self, identifier):
        self._check_id(identifier)
        return self.workspace / "jobs" / identifier

    def _persist(self, job):
        write_json(self.directory(job["id"]) / "job.json", job)

    def upload(self, filename, payload):
        filename = safe_filename(filename)
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_UPLOAD:
            raise ValueError("Le fichier doit contenir entre 1 octet et 50 Mio")
        identifier = uuid.uuid4().hex
        directory = self.workspace / "uploads" / identifier
        directory.mkdir()
        (directory / filename).write_bytes(payload)
        info = {"id": identifier, "filename": filename, "bytes": len(payload)}
        write_json(directory / "upload.json", info)
        return info

    def source(self, identifier):
        if identifier is None:
            return None
        self._check_id(identifier)
        directory = self.workspace / "uploads" / identifier
        try:
            info = strict_json((directory / "upload.json").read_bytes())
            filename = safe_filename(info["filename"])
            source = directory / filename
            if not source.is_file() or source.is_symlink():
                raise ValueError("Source absente")
            return source
        except (OSError, KeyError) as error:
            raise ValueError("Source CAO introuvable ; importer à nouveau le fichier") from error

    def submit(self, project=None, upload_id=None, use_advisor=False, *, kind="mesh", search=None):
        if self.closed:
            raise ValueError("Application en cours de fermeture")
        if kind not in ("mesh", "training", "learning"):
            raise ValueError("Type de calcul inconnu")
        if kind in ("mesh", "learning"):
            project = normalize_project(project)
            source = self.source(upload_id)
            if project["geometry"]["kind"] == "import" and source is None:
                raise ValueError("Importer un fichier CAO avant de lancer le maillage")
        else:
            source = None
            if project is not None: project = normalize_project(project)
        search = {} if search is None else search
        if not isinstance(search, dict): raise ValueError("search doit être un objet JSON")
        unknown = set(search) - {"max_trials", "epochs", "seed", "max_seconds", "use_memory"}
        if unknown: raise ValueError("Réglage d'apprentissage inconnu : " + ", ".join(sorted(unknown)))
        if kind == "learning":
            defaults = project.get("learning", {})
            search = {"max_trials": defaults.get("max_trials", 12), "epochs": defaults.get("epochs", 100),
                      "max_seconds": defaults.get("max_seconds", 240), "seed": 42,
                      "use_memory": defaults.get("use_memory", True), **search}
            for name, maximum in (("max_trials", 64), ("epochs", 200), ("max_seconds", 86400)):
                if type(search[name]) is not int or not 1 <= search[name] <= maximum:
                    raise ValueError(f"{name} doit être un entier de 1 à {maximum}")
            if type(search["seed"]) is not int or not 0 <= search["seed"] <= 2**32 - 1:
                raise ValueError("seed doit être un entier de 0 à 4294967295")
            if type(search["use_memory"]) is not bool: raise ValueError("use_memory doit être vrai ou faux")
        with self.lock:
            pending = sum(job.get("status") in ("running", "queued") for job in self.jobs.values())
            if pending >= 16:
                raise ValueError("La file contient déjà 16 calculs ; attendre ou annuler un calcul")
            if kind == "training" and any(j.get("kind") == "training" and j.get("status") in ("queued", "running") for j in self.jobs.values()):
                raise ValueError("Un entraînement est déjà en cours")
            identifier = uuid.uuid4().hex
            directory = self.directory(identifier)
            directory.mkdir()
            request = {"directory": str(directory), "operation": {"training": "train", "learning": "learn", "mesh": "mesh"}[kind],
                       "project": project, "source": str(source) if source else None,
                       "workspace": str(self.workspace), "timeout": self.timeout, "search": search,
                       "memory_dir": str(self.workspace / "experience"),
                       "use_advisor": bool(use_advisor), "model_path": str(self.workspace / "models" / "guide.json")}
            write_json(directory / "request.json", request)
            job = {"id": identifier, "kind": kind, "status": "queued", "message": "En attente d'un créneau de calcul…",
                   "created_at": _now(), "project_name": project["name"] if project else "Entraînement du guide"}
            self.jobs[identifier] = job
            self.cancellations[identifier] = threading.Event()
            self._persist(job)
            self.executor.submit(self._run, identifier)
            return dict(job)

    @staticmethod
    def _stop_process_tree(process):
        """Stop the worker and every native child, including an unresponsive one."""
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            process.wait(timeout=3)
        finally:
            # The group leader may exit before a child that ignores SIGTERM.
            if os.name != "nt":
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def _run(self, identifier):
        directory = self.directory(identifier)
        event = self.cancellations[identifier]
        with self.lock:
            if event.is_set(): return
            job = self.jobs[identifier]
            job.update(status="running", message="Démarrage du calcul…", started_at=_now())
            self._persist(job)
        started = time.monotonic()
        process = None
        try:
            environment = os.environ.copy()
            # Keep every native/export child inside the HTTP worker's group.
            environment["ATELIER_WORKER_PROCESS_GROUP"] = "1"
            package_root = str(Path(__file__).resolve().parents[1])
            environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
            with (directory / "calculation.log").open("wb") as log:
                process = subprocess.Popen([sys.executable, "-m", "atelier.worker", str(directory / "request.json")],
                                           cwd=package_root, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=os.name != "nt")
                with self.lock: self.processes[identifier] = process
                timed_out = False
                while process.poll() is None:
                    timed_out = time.monotonic() - started > self.timeout
                    if event.is_set() or timed_out:
                        self._stop_process_tree(process)
                        break
                    event.wait(.12)
            with self.lock:
                if event.is_set():
                    job.update(status="cancelled", message="Calcul annulé")
                elif timed_out:
                    job.update(status="failed", message=f"Délai maximal de {self.timeout:g} secondes atteint. Augmenter la taille des éléments ou le délai.")
                elif process.returncode == 0 and (directory / "result.json").is_file():
                    job.update(status="completed", message="Calcul terminé")
                else:
                    message = "Le calcul a échoué ; consulter le diagnostic ou modifier les réglages."
                    if (directory / "error.json").is_file():
                        message = strict_json((directory / "error.json").read_bytes()).get("message", message)
                    job.update(status="failed", message=message)
                job.update(finished_at=_now(), elapsed_seconds=time.monotonic() - started)
                self._persist(job)
        except Exception as error:
            with self.lock:
                job.update(status="failed", message=str(error), finished_at=_now())
                self._persist(job)
        finally:
            # A converter can fail after starting native children, or exit
            # successfully while leaving one behind. This manager owns the
            # worker's process group, so clean it at every terminal outcome.
            if process is not None:
                self._stop_process_tree(process)
            with self.lock: self.processes.pop(identifier, None)

    def get(self, identifier, *, include_preview=True):
        self._check_id(identifier)
        with self.lock:
            if identifier not in self.jobs: raise KeyError(identifier)
            job = dict(self.jobs[identifier])
        directory = self.directory(identifier)
        if job["status"] == "running" and (directory / "progress.json").is_file():
            try: job.update(strict_json((directory / "progress.json").read_bytes()))
            except (OSError, ValueError): pass
        if job["status"] == "completed":
            result = strict_json((directory / "result.json").read_bytes())
            job.update(result)
            if result.get("kind") in ("mesh", "learning"):
                if include_preview:
                    job["preview"] = strict_json((directory / "preview.json").read_bytes())
                job["files"] = result.get("exports", {}).get("results", [])
        return job

    def recent_jobs(self, limit=30):
        """Copier l'historique sous verrou pendant que les calculs évoluent."""
        with self.lock:
            return [dict(job) for job in sorted(self.jobs.values(),
                    key=lambda job: job.get("created_at", ""), reverse=True)[:limit]]

    def memory_info(self):
        from .native import NativeSession
        return NativeSession(self.workspace, timeout=min(self.timeout, 15)).memory_info(self.workspace / "experience")

    def cancel(self, identifier):
        self._check_id(identifier)
        with self.lock:
            if identifier not in self.jobs: raise KeyError(identifier)
            job = self.jobs[identifier]
            if job["status"] in ("queued", "running"):
                self.cancellations[identifier].set()
                job.update(status="cancelled", message="Annulation demandée…")
                self._persist(job)
        return self.get(identifier)

    def close(self):
        self.closed = True
        with self.lock:
            for identifier in list(self.cancellations):
                if self.jobs[identifier].get("status") in ("running", "queued"):
                    self.cancellations[identifier].set()
                    self.jobs[identifier].update(status="cancelled", message="Calcul annulé à la fermeture")
                    self._persist(self.jobs[identifier])
        self.executor.shutdown(wait=True, cancel_futures=True)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def server_close(self):
        if hasattr(self, "manager"): self.manager.close()
        super().server_close()


def create_server(host="127.0.0.1", port=8765, workspace=None, max_workers=1, timeout=300):
    manager = JobManager(Path(workspace) if workspace else Path.home() / "AtelierMailleur", max_workers, timeout)
    try:
        server = LocalServer((host, port), Handler)
    except Exception:
        manager.close()
        raise
    server.manager = manager
    server.token = secrets.token_urlsafe(32)
    server.local_only = host in ("127.0.0.1", "localhost", "::1")
    return server


class Handler(BaseHTTPRequestHandler):
    server_version = "AtelierMailleur/1.0"
    def log_message(self, fmt, *args):
        # Les requêtes ne contiennent pas de données du modèle dans le journal.
        pass

    def _host_ok(self):
        try:
            host = urlsplit("//" + self.headers.get("Host", "")).hostname
        except ValueError:
            return False
        return not self.server.local_only or host in ("127.0.0.1", "localhost", "::1")

    def _send(self, payload, status=200, content_type="application/json; charset=utf-8", filename=None):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(len(payload), status, content_type, filename)
        self.wfile.write(payload)

    def _send_file(self, file, *, content_type="application/octet-stream", filename=None):
        # Un gros export ne doit pas être dupliqué en mémoire par le serveur.
        with file.open("rb") as stream:
            self._send_stream(stream, os.fstat(stream.fileno()).st_size, content_type, filename or file.name)

    def _send_stream(self, stream, length, content_type, filename):
        self._headers(length, 200, content_type, filename)
        shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)

    def _headers(self, length, status, content_type, filename):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            safe = "".join(c if c.isascii() and (c.isalnum() or c in "._-") else "_" for c in filename)
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.end_headers()

    def _error(self, error, status=400):
        self._send({"error": str(error)}, status)

    def do_GET(self):
        try:
            if not self._host_ok(): return self._error("Hôte non autorisé", 403)
            request = urlsplit(self.path)
            path = unquote(request.path)
            if path == "/api/info":
                from .formats import FORMATS
                from .imports import import_capabilities
                return self._send({"version": __version__, "token": self.server.token, "backend": backend_info(),
                                   "formats": FORMATS, "import_formats": import_capabilities(), "examples": default_examples(), "max_upload_bytes": MAX_UPLOAD,
                                   "workers": self.server.manager.max_workers,
                                   "continuous_learning": True, "numerical_backend": "rust",
                                   "advisor_available": (self.server.manager.workspace / "models" / "guide.json").is_file(),
                                   "jobs": self.server.manager.recent_jobs()})
            if path == "/api/formats":
                from .formats import FORMATS
                from .imports import import_capabilities
                return self._send({"version": __version__, "imports": import_capabilities(), "exports": FORMATS})
            if path == "/api/memory":
                return self._send(self.server.manager.memory_info())
            parts = path.strip("/").split("/")
            if len(parts) in (3, 4) and parts[:2] == ["api", "jobs"]:
                identifier = parts[2]
                job = self.server.manager.get(identifier, include_preview=len(parts) == 3)
                directory = self.server.manager.directory(identifier)
                if len(parts) == 3: return self._send(job)
                if job["status"] != "completed": raise ValueError("Le calcul n'est pas terminé")
                if parts[3] == "bundle":
                    archive = directory / "exports" / "exports.zip"
                    if not archive.is_file(): raise ValueError("Ce calcul ne contient pas de paquet d'export")
                    return self._send_file(archive, content_type="application/zip", filename="maillage_exports.zip")
                if parts[3] == "download":
                    fmt = parse_qs(request.query).get("format", ["msh"])[0]
                    item = next((r for r in job.get("files", []) if r.get("format") == fmt and r.get("status") == "ok"), None)
                    if not item: raise ValueError("Format indisponible ; consulter le rapport d'export")
                    filenames = item.get("files") or [item["file"]]
                    files = []
                    for name in filenames:
                        name = Path(name).name
                        file = directory / "exports" / name
                        if file.is_file() and not file.is_symlink(): files.append(file)
                    if not files: raise ValueError("Export introuvable")
                    if len(files) > 1:
                        with tempfile.TemporaryFile() as stream:
                            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as out:
                                for file in files: out.write(file, file.name)
                            length = stream.tell()
                            stream.seek(0)
                            return self._send_stream(stream, length, "application/zip", f"maillage_{fmt}.zip")
                    return self._send_file(files[0])
                if parts[3] == "report":
                    return self._send(job["report"], filename="rapport_qualite.json")
            if path.startswith("/api/"): return self._error("Ressource introuvable", 404)
            asset = "index.html" if path == "/" else path.lstrip("/")
            if asset not in ("index.html", "app.js", "app.css"):
                return self._error("Ressource introuvable", 404)
            file = Path(__file__).parent / "web" / asset
            if not file.is_file(): return self._error("Interface indisponible", 404)
            return self._send(file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        except KeyError:
            self._error("Calcul introuvable", 404)
        except (ValueError, OSError, RuntimeError) as error:
            self._error(error)

    def _json_body(self):
        length = self.headers.get("Content-Length")
        try: length = int(length)
        except (TypeError, ValueError): raise ValueError("Taille de requête invalide") from None
        if not 0 < length <= int(MAX_UPLOAD * 1.4) + 2 * 1024 * 1024:
            raise ValueError("Requête vide ou trop volumineuse")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Envoyer une requête JSON")
        result = strict_json(self.rfile.read(length))
        if not isinstance(result, dict): raise ValueError("Objet JSON attendu")
        return result

    @staticmethod
    def _decode(value, *, project=False):
        if not isinstance(value, str): raise ValueError("Données de fichier absentes")
        try: payload = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error): raise ValueError("Encodage du fichier invalide") from None
        if not payload or len(payload) > MAX_UPLOAD + (1024 * 1024 if project else 0):
            raise ValueError("Fichier vide ou dépassant 50 Mio")
        return payload

    def do_POST(self):
        try:
            if not self._host_ok(): return self._error("Hôte non autorisé", 403)
            token = self.headers.get("X-Atelier-Token", "")
            if not secrets.compare_digest(token, self.server.token): return self._error("Session invalide ; recharger l'application", 403)
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self._error("Origine non autorisée", 403)
            data = self._json_body()
            path = urlsplit(self.path).path
            if path == "/api/uploads":
                return self._send(self.server.manager.upload(data.get("filename"), self._decode(data.get("content_base64"))), 201)
            if path == "/api/jobs":
                return self._send(self.server.manager.submit(data.get("project"), data.get("upload_id"), data.get("use_advisor", False)), 202)
            if path == "/api/training":
                return self._send(self.server.manager.submit(kind="training"), 202)
            if path == "/api/learning":
                search = data.get("search", {key: data[key] for key in ("max_trials", "epochs", "seed", "max_seconds", "use_memory") if key in data})
                return self._send(self.server.manager.submit(data.get("project"), data.get("upload_id"),
                                                             kind="learning", search=search), 202)
            if path == "/api/projects/save":
                payload = save_portable_project(data.get("project"), self.server.manager.source(data.get("upload_id")))
                return self._send(payload, content_type="application/zip", filename="projet_mailleur.amp")
            if path == "/api/projects/open":
                payload = self._decode(data.get("content_base64"), project=True)
                folder = self.server.manager.workspace / "uploads" / uuid.uuid4().hex
                try:
                    result = load_portable_project(payload, folder)
                    upload = self.server.manager.upload(result["source"].name, result["source"].read_bytes()) if result["source"] else None
                    return self._send({"project": result["project"], "upload_id": upload["id"] if upload else None,
                                       "filename": upload["filename"] if upload else None})
                finally:
                    shutil.rmtree(folder, ignore_errors=True)
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                return self._send(self.server.manager.cancel(parts[2]))
            return self._error("Action introuvable", 404)
        except KeyError:
            self._error("Calcul introuvable", 404)
        except (ValueError, OSError, UnicodeError, RuntimeError) as error:
            self._error(error)
