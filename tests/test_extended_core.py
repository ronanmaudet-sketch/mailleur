"""Regression for a native Gmsh/OpenMP exception found by the real campaign."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

from atelier.native import NativeSession, core_path, gmsh_library


class QuadraticOptimizerFailure(unittest.TestCase):
    def test_optimizer_failure_returns_json_instead_of_sigabrt(self):
        # This valid CAD sphere terminated the rc6 process with SIGABRT in the
        # direct high-order optimizer. A different Gmsh build may succeed; it
        # must then still pass the complete native quality assessment.
        project = {
            "schema_version": 1, "name": "Régression quadratique ScaledJac",
            "mode": "volume", "units": {"input": "mm", "internal": "m"},
            "geometry": {"kind": "sphere", "radius": 2.110967831892538},
            "mesh": {"algorithm": "automatic", "curvature_elements": 16,
                     "max_elements": 50000, "min_size": 0.1273866118630402,
                     "optimize": True, "order": 2, "recombine": False,
                     "size": 0.57903005392291, "smoothing": 1, "threads": 1},
            "repair": {"enabled": False, "make_solids": False, "sew": True,
                       "small_edges": True, "small_faces": False, "tolerance": 1e-7},
            "quality_policy": {"minimum_quality": 0.03, "poor_threshold": 0.2, "strict": False},
        }
        with tempfile.TemporaryDirectory(prefix="atelier-high-order-regression-") as folder:
            root = Path(folder)
            request = {"operation": "mesh", "project": project, "report_only": True,
                       "response_mode": "summary", "output_dir": str(root / "native"),
                       "gmsh_library": str(gmsh_library())}
            path = root / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            process = subprocess.run([core_path(), "--request", path], capture_output=True,
                                     text=True, timeout=90)
            self.assertIn(process.returncode, (0, 2), process.stderr)
            self.assertNotIn("libc++abi: terminating", process.stderr)
            if process.returncode == 2:
                error = json.loads(process.stderr)
                self.assertIs(error["ok"], False)
                self.assertTrue(error["error"])
                self.assertFalse((root / "native/mesh.msh").exists())
                self.assertFalse((root / "native/mesh.state.msh").exists())
            else:
                report = json.loads((root / "native/report.json").read_text())
                self.assertEqual(report["mesh_settings"]["order"], 2)
                self.assertEqual(report["quality"]["invalid_elements"], 0)
                self.assertGreater(report["quality"]["minimum"], 0)
                self.assertEqual(report["quality"]["evaluated_elements"], report["elements"])


class SphereBoundsUnits(unittest.TestCase):
    """Scaled OCC degenerate edges must not retain millimetre-sized boxes in SI."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atelier-sphere-bounds-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = NativeSession(self.root / "session", timeout=60)
        self.addCleanup(self.session.cancel)

    @staticmethod
    def project(unit, mode):
        factor = 1.0 if unit == "mm" else 0.001
        return {
            "schema_version": 1, "name": "Sphère unités et boîtes CAO", "mode": mode,
            "units": {"input": unit, "internal": "m"},
            "geometry": {"kind": "sphere", "radius": 2.9 * factor},
            "mesh": {"size": 0.8 * factor, "min_size": 0.2 * factor, "order": 1,
                     "algorithm": "automatic", "threads": 1, "max_elements": 20000,
                     "recombine": False},
            "repair": {"enabled": False},
            "quality_policy": {"minimum_quality": 0.03, "poor_threshold": 0.2, "strict": False},
        }

    def assert_geometry_bounds(self, report):
        radius = 0.0029
        bounds = report["geometry_diagnostics"]["bounds"]
        # A tessellation is an approximation: test a physical envelope rather
        # than demanding identical extrema from differently scaled OCC shapes.
        for low, high in zip(*bounds):
            self.assertLess(abs(low + radius), radius * 0.005)
            self.assertLess(abs(high - radius), radius * 0.005)
        self.assertEqual(report["geometry_diagnostics"]["bounds_source"],
                         "opencascade_stl_tessellation")
        self.assertTrue(report["geometry_diagnostics"]["bounds_approximate"])
        for entity in report["entity_catalog"]:
            self.assertEqual(entity["bbox_source"], "opencascade_stl_tessellation")
            self.assertTrue(entity["bbox_approximate"])
            self.assertTrue(all(abs(x) <= radius * 1.005 for x in entity["bbox"]))

    def test_inspection_boxes_stay_in_si_for_surface_and_volume(self):
        for mode in ("surface", "volume"):
            for unit in ("mm", "m"):
                with self.subTest(mode=mode, unit=unit):
                    result = self.session.inspect(self.project(unit, mode))
                    self.assert_geometry_bounds(result["report"])
                    self.assertTrue(all(abs(x) <= 0.0029 * 1.005
                                        for p in result["preview"]["points"] for x in p))

    def test_mesh_preview_and_real_export_keep_physical_coordinates(self):
        import meshio
        for mode in ("surface", "volume"):
            for unit in ("mm", "m"):
                with self.subTest(mode=mode, unit=unit):
                    result = self.session.generate(self.project(unit, mode))
                    report = result["report"]
                    self.assert_geometry_bounds(report)
                    self.assertEqual(report["quality"]["invalid_elements"], 0)
                    self.assertTrue(report["quality"]["accepted"])
                    preview = result["preview"]
                    self.assertEqual(preview["units"], "m")
                    self.assertTrue(all(abs(x) <= 0.0029 * 1.005
                                        for p in preview["points"] for x in p))
                    directory = Path(result["directory"])
                    internal = meshio.read(directory / "mesh.msh")
                    self.assertAlmostEqual(float(internal.points[:, 2].max()), .0029, places=10)
                    self.assertAlmostEqual(float(internal.points[:, 2].min()), -.0029, places=10)
                    # Exercise the production Rust rescaling + file I/O route,
                    # with the opposite coordinate unit in the exported file.
                    export_unit = "mm" if unit == "m" else "m"
                    output = self.root / f"sphere-{mode}-{unit}.med"
                    self.session.export(directory, output, "med", units=export_unit)
                    exported = meshio.read(output)
                    expected_radius = 2.9 if export_unit == "mm" else .0029
                    self.assertAlmostEqual(float(exported.points[:, 2].max()), expected_radius, places=9)
                    self.assertAlmostEqual(float(exported.points[:, 2].min()), -expected_radius, places=9)
                    self.assertTrue(all(math.isfinite(float(x)) for p in exported.points for x in p))


if __name__ == "__main__":
    unittest.main()
