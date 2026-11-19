"""API de scripts et échanges solveurs ; les décisions numériques restent en Rust.

Le client est synchrone. Une instance possède une session native, ses sorties et
son annulation ; plusieurs calculs indépendants utilisent plusieurs instances.
Les adaptateurs solveurs sont fournis par l'appelant. Aucun logiciel de calcul
n'est lancé et aucun état physique n'est transféré implicitement par ce module.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from typing import Callable, Protocol
import uuid

from mailleur.io import write_json
from .native import NativeSession, native_project
from .projects import strict_json


API_VERSION = "1.0"
INDICATOR_SCHEMA = "atelier.solver.indicators.v1"


def _json_copy(value):
    """Copy interface data while rejecting non-finite/unserializable JSON."""
    return strict_json(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _positive_seconds(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} doit être une durée finie strictement positive")
    return value


def _file_id(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class MeshResult:
    """Résultat persistant ; coordonnées internes et rapports en mètres SI."""

    directory: Path
    project: dict
    report: dict
    source: Path | None = None

    @property
    def mesh_file(self) -> Path:
        return self.directory / "mesh.msh"

    @property
    def mesh_id(self) -> str:
        """Empreinte de contenu, utilisable pour refuser un retour solveur périmé."""
        return _file_id(self.mesh_file)

    def to_dict(self) -> dict:
        return {"schema": "atelier.api.mesh_result.v1", "api_version": API_VERSION,
                "directory": str(self.directory), "mesh_file": str(self.mesh_file),
                "project": _json_copy(self.project), "report": _json_copy(self.report),
                "source": str(self.source) if self.source is not None else None}


@dataclass(frozen=True)
class SolverIteration:
    """Fichiers du maillage courant et contexte transmis à un adaptateur."""

    index: int
    mesh: MeshResult
    mesh_id: str
    export: dict
    exchange_directory: Path
    solver: str
    physics: str
    units: str

    def to_dict(self) -> dict:
        return {"schema": "atelier.solver.request.v1", "api_version": API_VERSION,
                "iteration": self.index, "mesh_id": self.mesh_id,
                "mesh_file_internal_si": str(self.mesh.mesh_file),
                "report_file_internal_si": str(self.mesh.directory / "report.json"),
                "export": _json_copy(self.export), "solver": self.solver,
                "physics": self.physics, "units": self.units,
                "feedback_schema": INDICATOR_SCHEMA,
                "feedback_file": str(self.exchange_directory / "solver_feedback.json"),
                "state_transfer": "solver_adapter_responsibility"}


class SolverAdapter(Protocol):
    def __call__(self, iteration: SolverIteration) -> dict:
        """Exécuter le calcul externe et retourner des indicateurs du maillage courant."""
        ...


class JsonFileSolverAdapter:
    """Échange neutre par fichiers, utilisable avec un solveur quelconque.

    ``run`` reçoit les chemins de demande et de réponse. Il peut être un wrapper
    fourni par l'utilisateur autour de son solveur. Sans callback, le fichier de
    réponse doit déjà exister. Aucun shell, script ou commande provenant d'une
    réponse JSON n'est exécuté. Le wrapper est responsable de son délai maximal,
    de son annulation, des conditions limites et du transfert de champs.
    """

    def __init__(self, run: Callable[[Path, Path], None] | None = None):
        if run is not None and not callable(run):
            raise TypeError("run doit être une fonction ou None")
        self.run = run

    def __call__(self, iteration: SolverIteration) -> dict:
        request_path = iteration.exchange_directory / "solver_request.json"
        response_path = iteration.exchange_directory / "solver_feedback.json"
        write_json(request_path, iteration.to_dict())
        if self.run is not None:
            self.run(request_path, response_path)
        if not response_path.is_file():
            raise FileNotFoundError(f"Retour solveur absent : {response_path}")
        if response_path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Retour solveur trop volumineux : limite de 8 Mio")
        value = strict_json(response_path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("Le retour solveur doit être un objet JSON")
        return value


class MeshingClient:
    """Façade stable vers le noyau Rust, pour scripts et adaptateurs solveurs.

    Les appels publics sont sérialisés par instance. ``cancel()`` peut être
    appelé d'un autre thread pour arrêter le processus natif courant. Il faut
    ``reset_cancel()`` avant de réutiliser cette instance après une annulation.
    """

    api_version = API_VERSION

    def __init__(self, workspace: Path | str, *, timeout=300):
        self.workspace = Path(workspace).expanduser().resolve()
        self.timeout = _positive_seconds(timeout, "timeout")
        self._session = NativeSession(self.workspace, timeout=self.timeout)
        self._cancelled = threading.Event()
        self._lock = threading.RLock()
        self._deadline = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cancel()

    def cancel(self):
        self._cancelled.set()
        self._session.cancel()

    def reset_cancel(self):
        with self._lock:
            self._cancelled.clear()

    def _check_budget(self):
        if self._cancelled.is_set():
            raise RuntimeError("Traitement API annulé")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise TimeoutError("Budget de temps de la boucle d'adaptation atteint")

    def _invoke(self, function, *args, **kwargs):
        self._check_budget()
        previous = self._session.timeout
        try:
            remaining = self.timeout if self._deadline is None else self._deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Budget de temps de la boucle d'adaptation atteint")
            self._session.timeout = min(self.timeout, remaining)
            result = function(*args, **kwargs)
            self._check_budget()
            return result
        finally:
            self._session.timeout = previous

    def _protocol(self, operation: str, **payload) -> dict:
        """Transport d'une opération JSON Rust sans calcul géométrique Python."""
        directory = self.workspace / "native" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        request = _json_copy(payload)
        request.update(operation=operation, output_dir=str(directory))
        self._session.last_attempt_directory = directory
        self._invoke(self._session._call, request, directory, needs_gmsh=False)
        report = strict_json((directory / "report.json").read_bytes())
        if not isinstance(report, dict):
            raise RuntimeError("Réponse native JSON invalide")
        return report

    def capabilities(self) -> dict:
        """Retourner les capacités déclarées par ce binaire natif."""
        with self._lock:
            return self._protocol("capabilities")

    @staticmethod
    def _result(result, project, source=None) -> MeshResult:
        report = result["report"]
        selected = report.get("selected_project", project)
        return MeshResult(Path(result["directory"]).resolve(), _json_copy(selected),
                          _json_copy(report), Path(source).expanduser().resolve() if source is not None else None)

    def inspect(self, project: dict, *, source: Path | str | None = None) -> MeshResult:
        with self._lock:
            project = native_project(project)
            return self._result(self._invoke(self._session.inspect, project, source), project, source)

    def generate(self, project: dict, *, source: Path | str | None = None,
                 use_advisor=False) -> MeshResult:
        with self._lock:
            project = native_project(project)
            return self._result(self._invoke(self._session.generate, project, source,
                                             use_advisor=use_advisor), project, source)

    def learn(self, project: dict, *, source: Path | str | None = None, **search) -> MeshResult:
        with self._lock:
            project = native_project(project)
            return self._result(self._invoke(self._session.learn, project, source, **search), project, source)

    def remesh(self, project: dict, *, previous: MeshResult,
               source: Path | str | None = None) -> MeshResult:
        """Reprendre localement si le noyau peut préserver l'extérieur sélectionné.

        Le noyau refuse les cas incompatibles, notamment une reprise locale
        structurée non fournie. Utiliser ``generate`` pour un nouveau maillage
        global ; cette méthode ne masque jamais un tel remplacement.
        """
        if not isinstance(previous, MeshResult):
            raise TypeError("previous doit être un MeshResult")
        with self._lock:
            project = native_project(project)
            source = source if source is not None else previous.source
            return self._result(self._invoke(self._session.remesh, project, source,
                                             previous_directory=previous.directory), project, source)

    def export(self, mesh: MeshResult, output: Path | str, *, format_name="msh", units=None) -> dict:
        if not isinstance(mesh, MeshResult):
            raise TypeError("mesh doit être un MeshResult")
        with self._lock:
            units = units if units is not None else mesh.project.get("units", {}).get("input", "mm")
            return self._invoke(self._session.export, mesh.directory, Path(output), format_name,
                                units=units, planar=mesh.project["mode"] == "planar")

    def plan_adaptation(self, mesh: MeshResult, indicators: dict, *, tolerance=.05,
                        max_regions=256) -> dict:
        """Faire interpréter les indicateurs et les tailles uniquement par Rust."""
        if not isinstance(mesh, MeshResult):
            raise TypeError("mesh doit être un MeshResult")
        if not isinstance(indicators, dict):
            raise ValueError("Les indicateurs doivent être un objet JSON")
        with self._lock:
            return self._plan_adaptation(mesh, indicators, mesh.mesh_id, tolerance, max_regions)

    def _plan_adaptation(self, mesh, indicators, verified_mesh_id, tolerance, max_regions):
        return self._protocol("adapt_plan", project=mesh.project, indicators=indicators,
                              source_mesh_id=verified_mesh_id,
                              adaptation={"tolerance": tolerance, "max_regions": max_regions})

    def adapt_loop(self, project: dict, solver: SolverAdapter, *, solver_name: str,
                   physics: str, source: Path | str | None = None, format_name="msh",
                   units=None, tolerance=.05, max_regions=256, max_iterations=5,
                   max_seconds=3600) -> dict:
        """Boucle maillage → callback solveur → plan Rust → nouveau maillage.

        ``max_iterations`` borne les évaluations solveur, pas seulement les
        remaillages. Le budget de temps inclut la génération et les exports ;
        il est vérifié entre les callbacks, qui gèrent leur propre timeout.
        Une adaptation structurée peut être globale. Le rapport Rust décrit la
        stratégie ; aucun maintien d'état physique n'est supposé.
        """
        if not callable(solver):
            raise TypeError("solver doit être un adaptateur appelable")
        for label, value in (("solver_name", solver_name), ("physics", physics)):
            if not isinstance(value, str) or not value.strip() or len(value) > 160:
                raise ValueError(f"{label} doit être une chaîne non vide de 1 à 160 caractères")
        if type(max_iterations) is not int or not 1 <= max_iterations <= 1000:
            raise ValueError("max_iterations doit être un entier de 1 à 1000")
        _positive_seconds(max_seconds, "max_seconds")
        with self._lock:
            if self._deadline is not None:
                raise RuntimeError("Une boucle d'adaptation est déjà active sur ce client")
            self._check_budget()
            started = time.monotonic()
            self._deadline = started + max_seconds
            run_directory = self.workspace / "adaptations" / uuid.uuid4().hex
            run_directory.mkdir(parents=True)
            records = []
            result = {"schema": "atelier.api.adaptation_run.v1", "api_version": API_VERSION,
                      "solver": solver_name, "physics": physics, "iterations": records,
                      "converged": False, "stop_reason": "iteration_budget",
                      "directory": str(run_directory), "state_transfer": "solver_adapter_responsibility"}
            try:
                current = self.generate(project, source=source)
                previous_project = _json_copy(current.project)
                for index in range(max_iterations):
                    self._check_budget()
                    directory = run_directory / f"iteration-{index:04d}"
                    directory.mkdir()
                    export = self.export(current, directory / "mesh", format_name=format_name, units=units)
                    export_units = export.get("units", units or current.project.get("units", {}).get("input", "mm"))
                    callback_mesh = MeshResult(current.directory, _json_copy(current.project),
                                               _json_copy(current.report), current.source)
                    iteration = SolverIteration(index, callback_mesh, current.mesh_id, _json_copy(export), directory,
                                                solver_name, physics, export_units)
                    write_json(directory / "solver_request.json", iteration.to_dict())
                    indicators = _json_copy(solver(iteration))
                    self._check_budget()
                    if not isinstance(indicators, dict):
                        raise ValueError("Le callback doit retourner un objet d'indicateurs JSON")
                    if indicators.get("schema") != INDICATOR_SCHEMA:
                        raise ValueError(f"Schéma d'indicateurs attendu : {INDICATOR_SCHEMA}")
                    if indicators.get("solver") != solver_name or indicators.get("physics") != physics:
                        raise ValueError("Le solveur et la physique du retour ne correspondent pas à la demande")
                    if indicators.get("units") != export_units:
                        raise ValueError("L'unité du retour solveur doit correspondre à l'export demandé")
                    if indicators.get("mesh_id") != iteration.mesh_id:
                        raise ValueError("Le retour solveur ne correspond pas au maillage courant")
                    verified_mesh_id = current.mesh_id
                    if verified_mesh_id != iteration.mesh_id:
                        raise ValueError("Le fichier de maillage a été modifié pendant le calcul solveur")
                    write_json(directory / "solver_feedback.json", indicators)
                    # Reuse the checksum just verified after the callback,
                    # avoiding a third full read of a potentially large mesh.
                    plan = self._plan_adaptation(current, indicators, verified_mesh_id, tolerance, max_regions)
                    write_json(directory / "adaptation_plan.json", plan)
                    if type(plan.get("requires_remesh")) is not bool or type(plan.get("converged")) is not bool:
                        raise RuntimeError("Plan natif d'adaptation incomplet")
                    if plan["converged"] and plan["requires_remesh"]:
                        raise RuntimeError("Plan natif d'adaptation contradictoire")
                    records.append({"iteration": index, "mesh_id": iteration.mesh_id,
                                    "mesh_directory": str(current.directory), "export": export,
                                    "exchange_directory": str(directory), "plan": plan})
                    result["mesh"] = current.to_dict()
                    if plan["converged"]:
                        result.update(converged=True, stop_reason="converged")
                        break
                    if not plan["requires_remesh"]:
                        result["stop_reason"] = "no_adaptation_requested"
                        break
                    next_project = native_project(plan.get("selected_project"))
                    if next_project == previous_project:
                        result["stop_reason"] = "no_progress"
                        break
                    if index + 1 == max_iterations:
                        break
                    current = self.generate(next_project, source=source)
                    previous_project = _json_copy(current.project)
                result["solver_evaluations"] = len(records)
                return result
            except BaseException as error:
                expired = self._deadline is not None and time.monotonic() >= self._deadline
                result["stop_reason"] = ("cancelled" if self._cancelled.is_set() else
                                         "time_budget" if isinstance(error, TimeoutError) or expired else "error")
                result["error"] = str(error)
                raise
            finally:
                self._deadline = None
                result["seconds"] = time.monotonic() - started
                result["solver_evaluations"] = len(records)
                write_json(run_directory / "adaptation_run.json", result)


def load_result(directory: Path | str) -> MeshResult:
    """Rouvrir un résultat persistant sans recalculer le maillage."""
    directory = Path(directory).expanduser().resolve()
    session_file = directory / "session.json"
    if session_file.is_file():
        session = strict_json(session_file.read_bytes())
    else:
        # Direct ``mailleur-core --request`` results have no Python session
        # envelope. Their normalized project is still a valid adaptation input.
        session = {"project": strict_json((directory / "project.normalized.json").read_bytes()),
                   "source": None}
    report = strict_json((directory / "report.json").read_bytes())
    project = native_project(report.get("selected_project", session["project"]))
    source = directory / session["source"] if session.get("source") else None
    return MeshResult(directory, project, report, source)
