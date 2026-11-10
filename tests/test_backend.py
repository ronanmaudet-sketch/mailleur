"""Real Gmsh integration tests; no generated mesh is replaced by a mock."""
from pathlib import Path
import copy
import importlib.util
import json
import math
import tempfile
import unittest

from atelier.backend import normalize_project, run_project


def plate(mode="planar", **geometry):
    return {"version": 1, "name": "Plaque test", "mode": mode,
            "geometry": {"kind": "plate", "width": 6, "height": 4,
                         "holes": [{"x": 3, "y": 2, "radius": .6}], **geometry},
            "mesh": {"size": .8, "min_size": .2, "threads": 1}}


class ProjectValidation(unittest.TestCase):
    def test_defaults_detach_input(self):
        source = {"geometry": {"kind": "plate"}}
        value = normalize_project(source)
        self.assertEqual(value["mode"], "planar")
        self.assertEqual(value["mesh"]["threads"], 1)
        self.assertNotIn("mesh", source)

    def test_invalid_controls(self):
        for mesh in ({"size": float("nan")}, {"size": 0}, {"size": True},
                     {"threads": 0}, {"threads": 65}, {"order": 3},
                     {"size": .1, "min_size": .5}, {"algorithm": "script"}):
            with self.subTest(mesh=mesh), self.assertRaises(ValueError):
                normalize_project({"geometry": {"kind": "plate"}, "mesh": mesh})
        with self.assertRaises(ValueError):
            normalize_project({"geometry": {"kind": "box"}, "mode": "planar"})
        with self.assertRaises(ValueError):
            normalize_project({"geometry": {"kind": "box"}, "mode": "volume", "mesh": {"recombine": True}})
        for bad in ({"mode": []}, {"geometry": {"kind": []}}, {"mesh": {"algorithm": {}}}):
            with self.assertRaises(ValueError):
                normalize_project(bad)


class GmshIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gmsh
            cls.gmsh = gmsh
        except (ImportError, OSError) as error:
            raise unittest.SkipTest(f"Gmsh integration unavailable: {error}")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def run_case(self, p, source=None):
        report = run_project(p, self.directory / "result", source)
        self.assertGreater(report["quality"]["minimum"], 0)
        self.assertGreater(report["quality"]["min_jacobian_determinant"], 0)
        self.assertEqual(report["quality"]["invalid_elements"], 0)
        self.assertGreater((self.directory / "result/mesh.msh").stat().st_size, 100)
        return report, json.loads((self.directory / "result/preview.json").read_text())

    def test_plate_hole_has_no_elements_in_hole(self):
        p = plate()
        report, preview = self.run_case(p)
        self.assertEqual(report["dimension"], 2)
        self.assertIn("DOMAIN", {g["name"] for g in report["groups"]})
        self.assertIn("BOUNDARY", {g["name"] for g in report["groups"]})
        for block in preview["cells"]:
            if block["type"] not in {"triangle", "quad"}:
                continue
            for row in block["data"]:
                coords = [preview["points"][i] for i in row]
                x, y = [sum(c[d] for c in coords)/len(coords) for d in (0, 1)]
                self.assertGreaterEqual(math.hypot(x-3, y-2), .6-1e-9)

    def test_real_rounding_chamfer_and_order_two(self):
        for geometry in ({"corner_radius": .4}, {"chamfer": .4}):
            with self.subTest(geometry=geometry):
                p = plate(**geometry)
                p["mesh"]["order"] = 2
                report, preview = self.run_case(p)
                self.assertIn("Triangle 6", report["cell_counts"])
                self.assertEqual(report["mesh_settings"]["order"], 2)
                self.assertTrue(any(c["order"] == 2 for c in preview["cells"]))

    def test_extruded_holes_make_positive_volume(self):
        report, preview = self.run_case(plate("volume", corner_radius=.4, thickness=1.2))
        self.assertEqual(report["dimension"], 3)
        self.assertTrue(any(c["type"] == "tetra" for c in preview["cells"]))
        self.assertTrue(preview["surface_triangles"])
        self.assertGreaterEqual(report["geometry_diagnostics"]["volumes"], 1)

    def test_frontal_volume_uses_netgen_not_delaunay_alias(self):
        p = plate("volume", thickness=1.2)
        p["mesh"]["algorithm"] = "frontal"
        report, _ = self.run_case(p)
        self.assertEqual(report["algorithms"]["surface"], 6)
        self.assertEqual(report["algorithms"]["volume"], 4)
        self.assertEqual(report["algorithms"]["volume_name"], "Netgen frontal")

    def test_surface_of_box_sphere_and_bracket(self):
        for kind, mode in [("box", "surface"), ("sphere", "volume"), ("bracket", "volume")]:
            with self.subTest(kind=kind):
                report, _ = self.run_case({"mode": mode, "geometry": {"kind": kind}, "mesh": {"size": 1.2}})
                self.assertEqual(report["dimension"], 2 if mode == "surface" else 3)

    def test_polygon_holes(self):
        p = {"geometry": {"kind": "polygon", "outer": [[0, 0], [5, 0], [5, 4], [0, 4]],
                          "holes": [[[1, 1], [2, 1], [2, 2], [1, 2]]]}, "mesh": {"size": .6}}
        report, preview = self.run_case(p)
        self.assertGreater(report["elements"], 0)
        bad = copy.deepcopy(p)
        bad["geometry"]["holes"] = [[[4, 3], [6, 3], [6, 5], [4, 5]]]
        with self.assertRaisesRegex(ValueError, "contours.*croisent|trous"):
            run_project(bad, self.directory / "invalid")

    def test_no_silent_geometric_repair_of_touching_holes(self):
        for p in [plate(corner_radius=.3, chamfer=.3),
                  plate(holes=[{"x": .1, "y": .1, "radius": .5}]),
                  plate(holes=[{"x": 2, "y": 2, "radius": 1}, {"x": 3, "y": 2, "radius": 1}])]:
            with self.subTest(project=p), self.assertRaises(ValueError):
                run_project(p, self.directory / "invalid")

    def test_step_roundtrip_import_and_surface_as_volume_rejected(self):
        gmsh = self.gmsh
        source = self.directory / "box.step"
        open_source = self.directory / "surface.step"
        gmsh.initialize(["test", "-nopopup"], readConfigFiles=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("test")
            gmsh.model.occ.addBox(0, 0, 0, 2, 2, 1)
            gmsh.model.occ.synchronize()
            gmsh.write(str(source))
            gmsh.clear()
            gmsh.model.add("surface")
            gmsh.model.occ.addRectangle(0, 0, 0, 2, 2)
            gmsh.model.occ.synchronize()
            gmsh.write(str(open_source))
        finally:
            gmsh.finalize()
        p = {"mode": "volume", "geometry": {"kind": "import"}, "mesh": {"size": .5}}
        report, _ = self.run_case(p, source)
        self.assertEqual(report["geometry_diagnostics"]["source_format"], ".step")
        with self.assertRaisesRegex(ValueError, "volume fermé"):
            run_project(p, self.directory / "open", open_source)
        p["mode"] = "surface"
        self.run_case(p, open_source)

    def test_closed_brep_faces_can_be_sewn_without_filling_hole(self):
        gmsh = self.gmsh
        source = self.directory / "shell.brep"
        gmsh.initialize(["test", "-nopopup"], readConfigFiles=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("shell")
            volume = gmsh.model.occ.addBox(0, 0, 0, 2, 2, 1)
            gmsh.model.occ.remove([(3, volume)], recursive=False)
            gmsh.model.occ.synchronize()
            gmsh.write(str(source))
        finally:
            gmsh.finalize()
        p = {"mode": "volume", "geometry": {"kind": "import"}, "mesh": {"size": .6}}
        with self.assertRaises(ValueError):
            run_project(p, self.directory / "unsewn", source)
        p["repair"] = {"enabled": True, "make_solids": True, "sew": True}
        report, _ = self.run_case(p, source)
        self.assertTrue(report["repair"]["applied"])
        self.assertFalse(report["repair"]["holes_capped"])
        self.assertGreater(report["geometry_diagnostics"]["volumes"], 0)

    def test_stl_open_surface_and_explicit_closed_volume_requirement(self):
        source = self.directory / "open.stl"
        source.write_text("""solid open
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 2 0 0
vertex 2 2 0
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 2 2 0
vertex 0 2 0
endloop
endfacet
endsolid open
""")
        p = {"mode": "surface", "geometry": {"kind": "import"}, "mesh": {"size": .6}}
        report, _ = self.run_case(p, source)
        self.assertEqual(report["geometry_diagnostics"]["source_format"], ".stl")
        self.assertGreater(report["rust_metrics"]["topology"]["open_surface_edges"], 0)
        p["mode"] = "volume"
        with self.assertRaisesRegex(ValueError, "ouvert"):
            run_project(p, self.directory / "openvolume", source)

    def test_closed_four_triangle_stl_is_classified_before_partitioning(self):
        source = self.directory / "tetra.stl"
        triangles = [
            [(0, 0, 0), (0, 1, 0), (1, 0, 0)],
            [(0, 0, 0), (1, 0, 0), (0, 0, 1)],
            [(0, 0, 0), (0, 0, 1), (0, 1, 0)],
            [(1, 0, 0), (0, 1, 0), (0, 0, 1)],
        ]
        source.write_text("solid tetra\n" + "".join(
            "facet normal 0 0 0\nouter loop\n" + "".join(
                "vertex " + " ".join(str(v) for v in point) + "\n" for point in triangle)
            + "endloop\nendfacet\n" for triangle in triangles) + "endsolid tetra\n")
        report, _ = self.run_case({"mode": "volume", "geometry": {"kind": "import"},
                                   "mesh": {"size": .3}}, source)
        self.assertEqual(report["geometry_diagnostics"]["source_format"], ".stl")
        self.assertEqual(report["rust_metrics"]["top_dimension"], 3)
        self.assertEqual(report["rust_metrics"]["topology"]["nonmanifold_volume_faces"], 0)
        self.assertFalse(report["repair"]["applied"])

    def test_element_limit_does_not_leave_success_artifacts(self):
        p = plate()
        p["mesh"]["max_elements"] = 10
        with self.assertRaisesRegex(ValueError, "limite"):
            run_project(p, self.directory / "limited")
        self.assertFalse((self.directory / "limited/mesh.msh").exists())
        self.assertFalse((self.directory / "limited/report.json").exists())

    def test_impractical_target_rejected_before_generation(self):
        p = plate()
        p["mesh"].update(size=1e-9, min_size=1e-10)
        with self.assertRaisesRegex(ValueError, "Taille cible trop petite"):
            run_project(p, self.directory / "too_fine")
        p["mesh"].update(size=.001, min_size=.001, max_elements=100)
        with self.assertRaisesRegex(ValueError, "estimation préalable"):
            run_project(p, self.directory / "estimated")

    def test_source_cannot_be_deleted_by_output_cleanup(self):
        directory = self.directory / "same"
        directory.mkdir()
        source = directory / "model.brep"
        source.write_text("original preserved")
        with self.assertRaisesRegex(ValueError, "source doit être distinct"):
            run_project({"geometry": {"kind": "import"}}, directory, source)
        self.assertEqual(source.read_text(), "original preserved")

    def test_unsafe_or_unknown_sources_are_rejected(self):
        source = self.directory / "script.geo"
        source.write_text('System "touch arbitrary";')
        with self.assertRaisesRegex(ValueError, "scripts ne sont pas acceptés"):
            run_project({"geometry": {"kind": "import"}}, self.directory / "unsafe", source)


if __name__ == "__main__":
    unittest.main()
