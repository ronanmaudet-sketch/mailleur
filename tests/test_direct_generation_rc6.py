"""End-to-end checks of native direct topology, quadratic nodes and exports."""
from pathlib import Path
import copy
import tempfile
import unittest

from atelier.native import NativeSession


def project(shape, *, complete=False, hole=False):
    volume = shape in ("hexahedron", "prism", "pyramid", "tetrahedron")
    geometry = ({"kind": "box", "width": 6, "height": 4, "depth": 2} if volume else
                {"kind": "plate", "width": 6, "height": 4, "corner_radius": 0, "chamfer": 0, "holes": []})
    if hole:
        geometry = {"kind": "plate", "width": 6, "height": 4, "thickness": 2,
                    "corner_radius": 0, "chamfer": 0, "holes": [{"x": 2.5, "y": 2, "radius": .5}]}
    return {"schema_version": 1, "name": "Contrôle génération directe", "mode": "volume" if volume else "planar",
            "units": {"input": "mm", "internal": "m"}, "geometry": geometry,
            "mesh": {"element_shape": shape, "order": 2, "quadratic_layout": "complete" if complete else "serendipity",
                     "size": 1, "min_size": .05, "structured": {"divisions": [2, 3, 2 if volume else 1]},
                     "algorithm": "automatic", "recombine": False, "threads": 1, "max_elements": 10000},
            "quality_policy": {"minimum_quality": .001, "poor_threshold": .1, "strict": True}}


class DirectNativeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = NativeSession(self.root)

    def read_mesh(self, result):
        from atelier.formats import read_mesh
        return read_mesh(Path(result["directory"]) / "mesh.msh")

    def assert_family(self, result, expected_type, dimension):
        mesh = self.read_mesh(result)
        from meshio._common import num_nodes_per_cell
        types = [cell.type for cell in mesh.cells if cell.dim == dimension]
        self.assertTrue(types)
        self.assertEqual(set(types), {expected_type})
        self.assertTrue(result["report"]["quality"]["accepted"])
        self.assertGreater(result["report"]["quality"]["min_jacobian_determinant"], 0)
        self.assertTrue(all(cell.data.shape[1] == num_nodes_per_cell[cell.type] for cell in mesh.cells))
        return mesh

    def test_quad8_and_hex20_are_generated_and_exported_without_order_loss(self):
        for shape, expected, dimension, count in (("quadrilateral", "quad8", 2, 6), ("hexahedron", "hexahedron20", 3, 12)):
            with self.subTest(shape=shape):
                result = self.session.generate(project(shape))
                self.assert_family(result, expected, dimension)
                self.assertEqual(result["report"]["elements"], count)
                for name in ("msh", "med", "vtu", "inp", "ansys"):
                    exported = self.session.export(Path(result["directory"]), self.root / (shape + name), name, units="mm", planar=dimension == 2)
                    self.assertTrue(Path(exported["file"]).is_file())

    def test_complete_quadratic_variants_are_preserved_or_explicitly_refused(self):
        for shape, expected, dimension in (("quadrilateral", "quad9", 2), ("hexahedron", "hexahedron27", 3)):
            with self.subTest(shape=shape):
                result = self.session.generate(project(shape, complete=True))
                self.assert_family(result, expected, dimension)
                for name in ("msh", "vtu"):
                    self.session.export(Path(result["directory"]), self.root / (shape + name), name, units="mm", planar=dimension == 2)
                with self.assertRaisesRegex((RuntimeError, ValueError), "(?i)(type|charge|ordre|quad9|hexahedron27)"):
                    self.session.export(Path(result["directory"]), self.root / (shape + "inp"), "inp", units="mm", planar=dimension == 2)

    def test_prisms_and_pyramids_are_actual_direct_quadratic_volume_cells(self):
        for shape, expected, multiplier in (("prism", "wedge15", 2), ("pyramid", "pyramid13", 6)):
            with self.subTest(shape=shape):
                result = self.session.generate(project(shape))
                self.assert_family(result, expected, 3)
                self.assertEqual(result["report"]["elements"], 12 * multiplier)
                self.session.export(Path(result["directory"]), self.root / shape, "msh", units="mm")

    def test_curved_hole_is_conforming_for_quads_and_extruded_hexes(self):
        for shape, expected, dimension in (("quadrilateral", "quad8", 2), ("hexahedron", "hexahedron20", 3)):
            with self.subTest(shape=shape):
                result = self.session.generate(project(shape, hole=True))
                mesh = self.assert_family(result, expected, dimension)
                self.assertEqual(result["report"]["elements"], 24 if dimension == 2 else 48)
                self.assertTrue(any(group["name"] == "BOUNDARY" for group in result["report"]["groups"]))
                rounded = {tuple(round(float(x), 13) for x in point) for point in mesh.points}
                self.assertEqual(len(rounded), len(mesh.points), "Shared block interfaces must reuse nodes")

    def test_incompatible_geometry_and_recombination_fail_without_silent_fallback(self):
        p = project("hexahedron")
        p["geometry"] = {"kind": "sphere", "radius": 3}
        with self.assertRaisesRegex((RuntimeError, ValueError), "(?i)(direct|structur|géométr|compatible)"):
            self.session.generate(p)
        for shape in ("automatic", "quadrilateral"):
            p = project("quadrilateral")
            p["mesh"].update(element_shape=shape, recombine=True)
            with self.assertRaisesRegex((RuntimeError, ValueError), "(?i)(recombin|retir|direct)"):
                self.session.generate(p)

    def test_direct_corpus_is_real_bounded_and_uses_current_feature_schema(self):
        corpus = self.root / "direct-corpus"
        plan = self.session.corpus_plan(corpus, structured=8, random=0, strategies=1, profile="direct_quad_hex")
        self.assertEqual(plan["family_count"], 6)
        preview = self.session.corpus_preview(corpus, limit=8)
        self.assertTrue(all(row["project"]["mesh"]["order"] == 2 for row in preview["cases"]))
        result = self.session.corpus_run(corpus, limit=8)
        self.assertEqual(result["completed_cases"], 8)
        self.assertEqual(result["meshed_cases"], 8)
        self.assertEqual(result["feature_schema"], "atelier.geometry_action_features.v3")
        self.assertFalse(list(corpus.rglob("mesh.msh")))


if __name__ == "__main__":
    unittest.main()
