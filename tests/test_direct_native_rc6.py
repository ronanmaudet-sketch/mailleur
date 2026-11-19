"""Native regressions for CAD classification and directly emitted P2 nodes."""
import json
import math
from pathlib import Path
import tempfile
import unittest

from atelier.native import NativeSession
from tests.test_direct_generation_rc6 import project


class DirectCadAndCurvatureIntegration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session = NativeSession(self.root, timeout=60)

    def assert_direct_order_two(self, result, shape):
        report = result["report"]
        generation = report["generation"]
        self.assertEqual(generation["method"], "rust_direct_structured")
        self.assertEqual(generation["requested_element_shape"], shape)
        self.assertEqual(generation["order"], 2)
        self.assertTrue(generation["direct_high_order_nodes"])
        for field in ("recombination", "recombination_used", "order_elevation_used",
                      "intermediate_mesh_generated", "intermediate_linear_mesh"):
            self.assertIs(generation[field], False, field)
        self.assertTrue(report["quality"]["accepted"])
        self.assertGreater(report["quality"]["min_jacobian_determinant"], 0)
        self.assertTrue(all(block["order"] == 2 for block in result["preview"]["cells"]))

    def test_unspecified_and_automatic_forms_emit_direct_quads_and_hexes(self):
        for shape in ("quadrilateral", "hexahedron"):
            for supplied in (False, True):
                with self.subTest(shape=shape, explicit_automatic=supplied):
                    p = project(shape)
                    p["mesh"].pop("order")
                    if supplied:
                        p["mesh"]["element_shape"] = "automatic"
                    else:
                        del p["mesh"]["element_shape"]
                    result = self.session.generate(p)
                    self.assert_direct_order_two(result, shape)
                    from atelier.formats import read_mesh
                    mesh = read_mesh(Path(result["directory"]) / "mesh.msh")
                    dimension, expected = (3, "hexahedron20") if shape == "hexahedron" else (2, "quad8")
                    self.assertEqual({cell.type for cell in mesh.cells if cell.dim == dimension}, {expected})

    def test_learning_refuses_other_explicit_families_before_recording_observations(self):
        p = project("prism")
        with self.assertRaisesRegex(RuntimeError, "uniquement les quadrilatères"):
            self.session.learn(p, max_trials=1)
        self.assertEqual(self.session.memory_info()["observations"], 0)

    def test_complete_quadratic_exports_keep_order_and_offer_a_usable_bundle(self):
        from atelier.formats import convert_mesh
        import zipfile

        for shape, expected in (("quadrilateral", {"msh", "vtu"}), ("hexahedron", {"msh", "vtu"}),
                                ("pyramid", {"msh"})):
            with self.subTest(shape=shape):
                result = self.session.generate(project(shape, complete=True))
                source = Path(result["directory"])
                bundle = self.session.export(source, self.root / f"{shape}.zip", "bundle")
                self.assertTrue(bundle["complete"], bundle["results"])
                self.assertEqual({r["format"] for r in bundle["results"]}, expected)
                with zipfile.ZipFile(bundle["file"]) as archive:
                    self.assertIn("mesh.msh", archive.namelist())
                for name in ("unv", "cast3m"):
                    with self.assertRaisesRegex((RuntimeError, ValueError), "UNV"):
                        self.session.export(source, self.root / "refused", name)
                target = self.root / "refused.unv"
                target.write_bytes(b"ancienne sortie")
                with self.assertRaisesRegex(ValueError, "UNV"):
                    convert_mesh(source / "mesh.msh", target, "unv")
                self.assertEqual(target.read_bytes(), b"ancienne sortie")

    def test_learning_keeps_an_unqualified_memory_untouched(self):
        directory = self.root / "ancienne_memoire"
        directory.mkdir()
        index = directory / "memory.json"
        previous = json.dumps({"schema": "atelier.experience.v1", "engine": "4.15.2",
                               "generation": 0, "contexts": []}).encode("utf-8")
        index.write_bytes(previous)
        with self.assertRaisesRegex(RuntimeError, "autre filière"):
            self.session.learn(project("quadrilateral"), memory_dir=directory, max_trials=1)
        self.assertEqual(index.read_bytes(), previous)
        self.assertFalse((directory / "model.json").exists())

    def test_critical_cad_vertices_edges_and_faces_remain_attached_to_hexes(self):
        p = project("hexahedron")
        inspected = self.session.inspect(p)
        catalog = inspected["report"]["entity_catalog"]
        p["critical_zones"] = [
            {"name": f"Protected {dimension}D", "kind": "contact", "dimension": dimension,
             "tags": [min(item["tag"] for item in catalog if item["dimension"] == dimension)],
             "minimum_quality": .1, "buffer": 0}
            for dimension in (0, 1, 2)]
        result = self.session.generate(p)
        self.assert_direct_order_two(result, "hexahedron")
        zones = result["report"]["quality"]["critical_zones"]
        self.assertEqual({zone["dimension"] for zone in zones}, {0, 1, 2})
        for zone in zones:
            with self.subTest(dimension=zone["dimension"]):
                self.assertTrue(zone["accepted"])
                self.assertGreater(zone["elements"], 0)
                self.assertEqual(zone["below_minimum"], 0)
        groups = {row["name"] for row in result["report"]["groups"]}
        self.assertTrue({"DOMAIN", "BOUNDARY", "PART_001"} <= groups)

    def test_explicit_structured_simplices_are_direct_quadratic_cells(self):
        for shape, name, preview_type, dimension in (("triangle", "Triangle 6", "triangle", 2),
                                                       ("tetrahedron", "Tetrahedron 10", "tetra", 3)):
            with self.subTest(shape=shape):
                result = self.session.generate(project(shape))
                self.assert_direct_order_two(result, shape)
                self.assertIn(name, result["report"]["cell_counts"])
                top = [block for block in result["preview"]["cells"] if block["dimension"] == dimension]
                self.assertTrue(top)
                self.assertEqual({block["type"] for block in top}, {preview_type})

    def test_quadratic_edge_nodes_remain_on_the_curved_hole_in_the_written_mesh(self):
        import meshio
        for complete in (False, True):
            with self.subTest(complete=complete):
                p = project("quadrilateral", complete=complete, hole=True)
                result = self.session.generate(p)
                self.assert_direct_order_two(result, "quadrilateral")
                # A circular CAD edge closes on its single seam vertex. Select it
                # from CAD topology, independently of generated node coordinates.
                closed_edges = [item for item in result["report"]["entity_catalog"]
                                if item["dimension"] == 1 and len(item["boundaries"]) == 2
                                and item["boundaries"][0] == item["boundaries"][1]]
                self.assertEqual(len(closed_edges), 1)
                edge = closed_edges[0]["tag"]
                exported = self.session.export(Path(result["directory"]), self.root / f"curved-{complete}",
                                               "vtu", units="m", planar=True)
                mesh = meshio.read(exported["file"])
                masks = mesh.cell_data[f"group:EDGE_{edge:03}"]
                circle_nodes = []
                curved_midpoint_found = False
                for cell, mask in zip(mesh.cells, masks):
                    if cell.type != "line3":
                        continue
                    for row, selected in zip(cell.data, mask):
                        if not selected:
                            continue
                        points = [mesh.points[int(index)] for index in row]
                        circle_nodes.extend(points)
                        chord_midpoint = [(points[0][axis] + points[1][axis]) / 2 for axis in range(3)]
                        curved_midpoint_found |= math.dist(points[2], chord_midpoint) > 1e-9
                self.assertTrue(circle_nodes)
                for point in circle_nodes:
                    self.assertAlmostEqual(math.hypot(point[0] - .0025, point[1] - .002), .0005, delta=1e-12)
                self.assertTrue(curved_midpoint_found, "P2 nodes must follow the circle, not its straight chords")


if __name__ == "__main__":
    unittest.main()
