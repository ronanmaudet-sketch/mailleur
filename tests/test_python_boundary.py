"""The application must never fall back to Python numerical algorithms."""
from __future__ import annotations

import ast
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def rust_metrics():
    return {"computation_backend": "rust", "points": 4, "used_points": 4,
            "unused_preview_points": 0, "top_dimension": 2, "top_dimension_elements": 1,
            "cell_counts": {"quad": 1, "line": 4},
            "top_dimension_quality": {"minimum": .75, "mean": .8},
            "edges": {"maximum": 1.1}, "stored_surface_area": .9,
            "topology": {"duplicate_cells": 0, "nonmanifold_surface_edges": 0,
                         "open_surface_edges": 4}}


class PythonNumericalBoundary(unittest.TestCase):
    def test_retired_geometry_meshing_and_training_cannot_execute(self):
        from mailleur.geometry import LegacyNumericalAPIError, polygon_area, prepare_polygon
        from mailleur.meshing import generate_mesh
        from mailleur.learning import Guide, collect_corpus, extract_features, train_guide
        for callback, args in ((polygon_area, ([(0, 0), (1, 0), (0, 1)],)),
                               (prepare_polygon, ([(0, 0), (1, 0), (0, 1)],)),
                               (generate_mesh, ([(0, 0), (1, 0), (0, 1)], .5)),
                               (Guide.load, ("guide.json",)), (collect_corpus, ([],)),
                               (extract_features, ([], .5)), (train_guide, ([],))):
            with self.subTest(callback=callback), self.assertRaisesRegex(LegacyNumericalAPIError, "Rust"):
                callback(*args)

    def test_no_numerical_dependency_or_old_algorithm_remains_in_facades(self):
        for name in ("atelier/backend.py", "atelier/advisor.py", "mailleur/geometry.py",
                     "mailleur/meshing.py", "mailleur/learning.py"):
            with self.subTest(path=name):
                tree = ast.parse((ROOT / name).read_text())
                imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import): imports.update(alias.name.split(".")[0] for alias in node.names)
                    if isinstance(node, ast.ImportFrom) and node.module: imports.add(node.module.split(".")[0])
                self.assertFalse(imports & {"gmsh", "numpy", "random", "math", "heapq"})
                definitions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
                self.assertFalse(definitions & {"_triangulate", "_safe_triangulate", "_refine", "_smooth", "_quad_quality", "_fit", "_forward"})

    def test_backend_is_transport_and_preserves_input_and_legacy_units(self):
        from atelier.backend import run_project
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); native = root / "native_result"; native.mkdir()
            (native / "mesh.msh").write_text("native mesh")
            (native / "preview.json").write_text('{}')
            report = {"quality": {"minimum": .5}, "native_kernel": {"language": "rust"},
                      "geometry_diagnostics": {"entity_counts": {"3": 0}}, "algorithms": {"surface": 2, "volume": None}}
            project = {"geometry": {"kind": "plate"}}; before = deepcopy(project)
            with patch("atelier.native.NativeSession") as session:
                session.return_value.generate.return_value = {"directory": str(native), "report": report}
                result = run_project(project, root / "result")
            self.assertEqual(project, before)
            called = session.return_value.generate.call_args.args[0]
            self.assertEqual(called["units"]["input"], "m")
            self.assertEqual(result["native_kernel"]["language"], "rust")
            self.assertEqual((root / "result/mesh.msh").read_text(), "native mesh")

    def test_backend_does_not_erase_a_source_on_output_collision(self):
        from atelier.backend import run_project
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "model.brep"; source.write_text("original")
            with self.assertRaisesRegex(ValueError, "source doit être distinct"):
                run_project({"geometry": {"kind": "import"}}, Path(folder), source)
            self.assertEqual(source.read_text(), "original")

    def test_backend_failure_does_not_leave_a_prior_exportable_mesh(self):
        from atelier.backend import run_project
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder); (directory / "mesh.msh").write_text("old")
            with patch("atelier.native.NativeSession") as session:
                session.return_value.generate.side_effect = RuntimeError("native failure")
                with self.assertRaisesRegex(ValueError, "native failure"):
                    run_project({"geometry": {"kind": "plate"}}, directory)
            self.assertFalse((directory / "mesh.msh").exists())

    def test_legacy_quality_and_validation_only_use_native_metrics(self):
        from mailleur.meshing import Mesh, quality_summary, validate_mesh
        mesh = Mesh([(0, 0), (1, 0), (1, 1), (0, 1)], [(0, 1, 2, 3)], [(0, 1), (1, 2), (2, 3), (3, 0)])
        with patch("atelier.native.NativeSession.query", return_value=rust_metrics()) as query:
            self.assertEqual(quality_summary(mesh)["area"], .9)
            self.assertTrue(validate_mesh(mesh)["valid"])
        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args.args, ("metrics",))
        self.assertEqual(query.call_args.kwargs["preview"]["cells"][0]["data"], mesh.quads)

    def test_native_rejection_is_not_replaced_by_python_validation(self):
        from mailleur.meshing import Mesh, validate_mesh
        mesh = Mesh([(0, 0)], [(0, 0, 0, 0)], [])
        with patch("atelier.native.NativeSession.query", side_effect=RuntimeError("Sommet répété")):
            with self.assertRaisesRegex(ValueError, "Sommet répété"):
                validate_mesh(mesh)

    def test_legacy_cli_forwards_without_running_prototype_algorithms(self):
        from mailleur.__main__ import main
        with patch("atelier.__main__.main", return_value=7) as native_main, patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["doctor"]), 7)
        native_main.assert_called_once_with(["doctor"])
        with patch("sys.stderr", new_callable=io.StringIO) as messages:
            self.assertEqual(main(["benchmark", "input.json"]), 2)
        self.assertIn("retirée", messages.getvalue())


if __name__ == "__main__":
    unittest.main()
