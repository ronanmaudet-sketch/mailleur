"""API contract and solver protocol, with isolated transport fixtures.

The fixture tests exercise orchestration only. NativeApiIntegration separately
executes real Rust/Gmsh meshing; its solver callback remains an explicit mock.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from atelier.api import (API_VERSION, INDICATOR_SCHEMA, JsonFileSolverAdapter,
                         MeshingClient, MeshResult, load_result)


def project():
    return {"schema_version": 1, "name": "API structured P2", "mode": "planar",
            "geometry": {"kind": "plate", "width": 4, "height": 3, "holes": []},
            "units": {"input": "mm", "internal": "m"},
            "mesh": {"size": 1, "min_size": .1, "order": 2, "element_shape": "quadrilateral",
                     "quadratic_layout": "serendipity", "recombine": False,
                     "structured": {"divisions": [2, 2, 1]}, "max_elements": 10000},
            "quality_policy": {"minimum_quality": .05, "poor_threshold": .2, "strict": True}}


def feedback(iteration, *, converged=False):
    return {"schema": INDICATOR_SCHEMA, "solver": iteration.solver, "physics": iteration.physics,
            "units": iteration.units, "mesh_id": iteration.mesh_id,
            "converged": converged, "regions": [] if converged else [
                {"center": [2, 1.5, 0], "radius": 1, "error": .2, "target_size": .5}]}


class FixtureSession:
    """No numerical work: deterministic file/transport fixture."""

    def __init__(self, workspace, timeout=300):
        self.workspace, self.timeout = Path(workspace), timeout
        self.calls, self.protocol = [], []
        self.cancelled = False
        self.responses = []

    def cancel(self):
        self.cancelled = True

    def generate(self, project, source=None, use_advisor=False):
        self.calls.append(("generate", deepcopy(project), source, use_advisor))
        directory = self.workspace / f"fixture-{len(self.calls)}"
        directory.mkdir(parents=True)
        (directory / "mesh.msh").write_text(json.dumps(project, sort_keys=True))
        report = {"quality": {"accepted": True}, "elements": 1}
        (directory / "report.json").write_text(json.dumps(report))
        (directory / "session.json").write_text(json.dumps({"project": project, "source": None}))
        return {"directory": str(directory), "report": report, "preview": {}}

    def inspect(self, project, source=None):
        return self.generate(project, source)

    def learn(self, project, source=None, **search):
        result = self.generate(project, source)
        result["report"]["selected_project"] = deepcopy(project)
        result["report"]["selected_project"]["mesh"]["size"] = .75
        return result

    def remesh(self, project, source=None, previous_directory=None):
        self.calls.append(("remesh-reference", previous_directory))
        return self.generate(project, source)

    def export(self, directory, output, format_name, units="mm", planar=False):
        output.parent.mkdir(parents=True, exist_ok=True)
        path = output.with_suffix(".msh")
        path.write_bytes((directory / "mesh.msh").read_bytes())
        return {"file": str(path), "units": units, "format": format_name}

    def _call(self, request, directory, needs_gmsh=True):
        self.protocol.append((deepcopy(request), needs_gmsh))
        response = self.responses.pop(0) if self.responses else {"api_version": API_VERSION}
        (directory / "report.json").write_text(json.dumps(response))


class ScriptApiContract(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        with patch("atelier.api.NativeSession", FixtureSession):
            self.client = MeshingClient(self.workspace)

    def test_generate_and_reload_preserve_selected_settings_and_content_identity(self):
        p = project()
        mesh = self.client.generate(p)
        self.assertEqual(mesh.mesh_id, hashlib.sha256(mesh.mesh_file.read_bytes()).hexdigest())
        self.assertEqual(load_result(mesh.directory).project, mesh.project)
        self.assertEqual(mesh.to_dict()["api_version"], API_VERSION)
        p["mesh"]["size"] = 9
        self.assertEqual(mesh.project["mesh"]["size"], 1)
        learned = self.client.learn(project())
        self.assertEqual(learned.project["mesh"]["size"], .75)

    def test_capabilities_and_adaptation_are_rust_protocol_requests(self):
        self.assertEqual(self.client.capabilities(), {"api_version": API_VERSION})
        mesh = self.client.generate(project())
        indicators = {"schema": INDICATOR_SCHEMA, "mesh_id": mesh.mesh_id, "regions": []}
        self.client.plan_adaptation(mesh, indicators, tolerance=.037, max_regions=17)
        request, needs_gmsh = self.client._session.protocol[-1]
        self.assertEqual(request["operation"], "adapt_plan")
        self.assertEqual(request["project"], mesh.project)
        self.assertEqual(request["indicators"], indicators)
        self.assertEqual(request["source_mesh_id"], mesh.mesh_id)
        self.assertEqual(request["adaptation"], {"tolerance": .037, "max_regions": 17})
        self.assertFalse(needs_gmsh)

    def test_reload_accepts_a_direct_core_result_without_python_session(self):
        mesh = self.client.generate(project())
        (mesh.directory / "project.normalized.json").write_text(json.dumps(mesh.project))
        (mesh.directory / "session.json").unlink()
        loaded = load_result(mesh.directory)
        self.assertEqual(loaded.mesh_id, mesh.mesh_id)
        self.assertEqual(loaded.project, mesh.project)
        self.assertIsNone(loaded.source)

    def test_local_remesh_keeps_the_previous_directory_explicit(self):
        previous = self.client.generate(project())
        self.client.remesh(project(), previous=previous)
        self.assertIn(("remesh-reference", previous.directory), self.client._session.calls)
        with self.assertRaises(TypeError):
            self.client.remesh(project(), previous=str(previous.directory))

    def test_file_adapter_roundtrip_and_two_solver_calls(self):
        refined = project()
        refined["mesh"]["structured"]["divisions"] = [4, 4, 1]
        self.client._session.responses = [
            {"requires_remesh": True, "converged": False, "selected_project": refined,
             "strategy": "global_structured_refinement"},
            {"requires_remesh": False, "converged": True, "selected_project": refined}]
        calls = []
        def wrapper(request_path, response_path):
            value = json.loads(request_path.read_text(encoding="utf-8"))
            calls.append(value)
            response = {"schema": INDICATOR_SCHEMA, "solver": value["solver"],
                        "physics": value["physics"], "units": value["units"],
                        "mesh_id": value["mesh_id"], "converged": len(calls) == 2,
                        "regions": []}
            response_path.write_text(json.dumps(response))
        report = self.client.adapt_loop(project(), JsonFileSolverAdapter(wrapper),
                                       solver_name="fixture-only", physics="static", max_iterations=3)
        self.assertEqual(report["stop_reason"], "converged")
        self.assertEqual(report["solver_evaluations"], 2)
        self.assertTrue(report["converged"])
        self.assertNotEqual(calls[0]["mesh_id"], calls[1]["mesh_id"])
        self.assertTrue(all(Path(c["export"]["file"]).is_file() for c in calls))
        self.assertEqual(report["iterations"][0]["plan"]["strategy"], "global_structured_refinement")
        self.assertTrue((Path(report["directory"]) / "adaptation_run.json").is_file())

    def test_stale_or_wrong_solver_feedback_is_rejected_before_plan(self):
        for change in ({"mesh_id": "old-mesh"}, {"solver": "other"}, {"physics": "other"},
                       {"units": "m"}, {"schema": "wrong-schema"}):
            with self.subTest(change=change):
                def callback(iteration):
                    return {**feedback(iteration), **change}
                before = len(self.client._session.protocol)
                with self.assertRaises(ValueError):
                    self.client.adapt_loop(project(), callback, solver_name="fixture", physics="static")
                self.assertEqual(len(self.client._session.protocol), before)

    def test_no_progress_stops_without_an_extra_mesh_or_solver_call(self):
        normalized = self.client.generate(project()).project
        self.client._session.responses = [{"requires_remesh": True, "converged": False,
                                           "selected_project": normalized}]
        report = self.client.adapt_loop(project(), feedback, solver_name="fixture", physics="thermal")
        self.assertEqual(report["stop_reason"], "no_progress")
        self.assertEqual(report["solver_evaluations"], 1)

    def test_callback_does_not_mutate_the_internal_project(self):
        self.client._session.responses = [{"requires_remesh": False, "converged": True}]
        def callback(iteration):
            iteration.mesh.project["mesh"]["order"] = 1
            return feedback(iteration, converged=True)
        report = self.client.adapt_loop(project(), callback, solver_name="fixture", physics="static")
        request = self.client._session.protocol[-1][0]
        self.assertEqual(request["project"]["mesh"]["order"], 2)
        self.assertEqual(report["mesh"]["project"]["mesh"]["order"], 2)

    def test_callback_cannot_replace_the_original_mesh_without_an_error(self):
        def callback(iteration):
            iteration.mesh.mesh_file.write_text("modified solver input")
            return feedback(iteration)
        with self.assertRaisesRegex(ValueError, "modifié"):
            self.client.adapt_loop(project(), callback, solver_name="fixture", physics="static")

    def test_iteration_budget_does_not_generate_an_unused_last_mesh(self):
        refined = project()
        refined["mesh"]["size"] = .5
        self.client._session.responses = [{"requires_remesh": True, "converged": False,
                                           "selected_project": refined}]
        report = self.client.adapt_loop(project(), feedback, solver_name="fixture", physics="static",
                                       max_iterations=1)
        self.assertEqual(report["stop_reason"], "iteration_budget")
        generated = [x for x in self.client._session.calls if x[0] == "generate"]
        self.assertEqual(len(generated), 1)

    def test_cancellation_from_callback_is_retained_until_reset(self):
        def callback(iteration):
            self.client.cancel()
            return feedback(iteration)
        with self.assertRaisesRegex(RuntimeError, "annulé"):
            self.client.adapt_loop(project(), callback, solver_name="fixture", physics="static")
        with self.assertRaisesRegex(RuntimeError, "annulé"):
            self.client.generate(project())
        self.client.reset_cancel()
        self.assertTrue(self.client.generate(project()).mesh_file.is_file())
        reports = list((self.workspace / "adaptations").glob("*/adaptation_run.json"))
        self.assertEqual(json.loads(reports[0].read_text(encoding="utf-8"))["stop_reason"], "cancelled")

    def test_elapsed_callback_budget_is_reported(self):
        def callback(iteration):
            self.client._deadline = time.monotonic() - 1
            return feedback(iteration)
        with self.assertRaises(TimeoutError):
            self.client.adapt_loop(project(), callback, solver_name="fixture", physics="static")
        reports = list((self.workspace / "adaptations").glob("*/adaptation_run.json"))
        self.assertEqual(json.loads(reports[0].read_text(encoding="utf-8"))["stop_reason"], "time_budget")

    def test_missing_file_response_fails_with_its_path(self):
        with self.assertRaisesRegex(FileNotFoundError, "solver_feedback.json"):
            self.client.adapt_loop(project(), JsonFileSolverAdapter(), solver_name="fixture", physics="static")

    def test_invalid_controls_are_rejected_before_computation(self):
        for seconds in (0, -1, float("inf"), True):
            with self.assertRaises(ValueError):
                MeshingClient(self.workspace, timeout=seconds)
        for count in (0, -1, 1.5, True, 1001):
            with self.assertRaises(ValueError):
                self.client.adapt_loop(project(), feedback, solver_name="fixture", physics="static",
                                       max_iterations=count)
        with self.assertRaises(ValueError):
            self.client.adapt_loop(project(), feedback, solver_name="", physics="static")


class NativeApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atelier.native import core_path, gmsh_library
        core_path()
        gmsh_library()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.client = MeshingClient(self.workspace, timeout=60)

    def test_native_capabilities_and_direct_second_order_generation(self):
        capabilities = self.client.capabilities()
        self.assertEqual(capabilities["api_version"], API_VERSION)
        self.assertIn(2, capabilities["element_orders"])
        self.assertIn("adapt_plan", capabilities["operations"])
        mesh = self.client.generate(project())
        self.assertTrue(mesh.mesh_file.is_file())
        self.assertTrue(mesh.report["quality"]["accepted"])
        self.assertEqual(load_result(mesh.directory).mesh_id, mesh.mesh_id)

    def test_real_mesh_export_solver_protocol_and_native_adaptation(self):
        seen = []
        def callback(iteration):
            # Protocol fixture, not a physical solver or convergence proof.
            seen.append(iteration.mesh.report["elements"])
            return feedback(iteration, converged=iteration.index == 1)
        result = self.client.adapt_loop(project(), callback, solver_name="protocol-fixture-only",
                                       physics="static", max_iterations=2, max_seconds=60)
        self.assertTrue(result["converged"])
        self.assertEqual(result["solver_evaluations"], 2)
        self.assertGreater(seen[1], seen[0])
        self.assertEqual(result["iterations"][0]["plan"]["strategy"], "global_structured_refinement")

    def test_native_plan_rejects_a_stale_mesh_identity(self):
        mesh = self.client.generate(project())
        indicators = {"schema": INDICATOR_SCHEMA, "solver": "fixture", "physics": "static",
                      "units": "mm", "mesh_id": "stale", "converged": False, "regions": []}
        with self.assertRaises(RuntimeError):
            self.client.plan_adaptation(mesh, indicators)


if __name__ == "__main__":
    unittest.main()
