"""Contrats de formats solveur, sans prétendre exécuter les solveurs cibles."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

from atelier.formats import convert_mesh, _read_bdf_sets, _read_key_subset, _write_apdl_structural


@unittest.skipUnless(importlib.util.find_spec("meshio"), "meshio non disponible")
class SolverExportContracts(unittest.TestCase):
    def setUp(self):
        import meshio
        import numpy as np
        self.meshio, self.np = meshio, np
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, kind, count, dimension, points=None):
        np = self.np
        points = np.asarray(points if points is not None else
                            [[index / 7., -index / 11., index / 13.] for index in range(count)])
        mesh = self.meshio.Mesh(points, [(kind, np.arange(count).reshape(1, count))],
                                cell_data={"gmsh:physical": [np.array([1])], "gmsh:geometrical": [np.array([1])]},
                                field_data={"DOMAIN": np.array([1, dimension])})
        path = self.root / f"{kind}.msh"
        self.meshio.write(path, mesh, file_format="gmsh22", binary=False)
        return path

    def test_bdf_linear_and_quadratic_connectivity_and_real_cards(self):
        fixtures = {"triangle": (3, 2, "CTRIA3"), "triangle6": (6, 2, "CTRIA6"),
                    "quad": (4, 2, "CQUAD4"), "quad8": (8, 2, "CQUAD8"),
                    "tetra": (4, 3, "CTETRA"), "tetra10": (10, 3, "CTETRA"),
                    "hexahedron": (8, 3, "CHEXA"), "hexahedron20": (20, 3, "CHEXA"),
                    "wedge": (6, 3, "CPENTA")}
        for kind, (nodes, dimension, card) in fixtures.items():
            with self.subTest(kind=kind):
                source = self.source(kind, nodes, dimension)
                target = self.root / f"{kind}.bdf"
                result = convert_mesh(source, target, "nastran")
                restored = self.meshio.read(target, file_format="nastran")
                self.np.testing.assert_array_equal(restored.cells_dict[kind], self.np.arange(nodes).reshape(1, nodes))
                text = target.read_text()
                self.assertIn(card.ljust(8), text)
                self.assertNotIn(card + "_", text)
                self.assertEqual(_read_bdf_sets(target), {("ELEM", "DOMAIN"): [1]})
                self.assertTrue(result["verification"]["groups_readback"])
                self.assertFalse(result["verification"]["solver_executed"])

    def test_bdf_high_order_edge_numbering_matches_nastran_convention(self):
        source = self.source("hexahedron20", 20, 3)
        target = self.root / "numbering.bdf"
        convert_mesh(source, target, "nastran")
        lines = target.read_text().splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("CHEXA"))
        values = []
        for line in lines[index:index + 3]:
            values.extend(int(line[start:start + 8]) for start in range(8, len(line), 8))
        self.assertEqual(values[2:], [1,2,3,4,5,6,7,8,9,10,11,12,17,18,19,20,13,14,15,16])

    def test_bdf_coordinate_precision_handles_negative_and_extreme_scales(self):
        points = [[-3.141592653589793, 1.2345678901234567e12, 1.2345678901234567e-120],
                  [0, -1/3, 2/7], [1.2345678901234567e120, -1.2345678901234567e-120, 3], [1,2,3]]
        source = self.source("tetra", 4, 3, points)
        target = self.root / "precise.bdf"
        result = convert_mesh(source, target, "nastran")
        restored = self.meshio.read(target, file_format="nastran")
        self.np.testing.assert_allclose(restored.points, points, rtol=5e-10, atol=1e-300)
        self.assertLessEqual(result["verification"]["coordinate_rtol"], 5e-10)

    def test_inp_real_fields_fit_solver_limits_at_extreme_scales(self):
        # The former .17g output exceeds CalculiX/Abaqus' 20-character limit
        # for these coordinates. Test the emitted records, not only a reader
        # with more permissive free-format parsing.
        points = [[-1.2345678901234567e-120, 1.0000000000000001e-5, -1/3],
                  [1.2345678901234567e120, -1.2345678901234567e120, 2/7],
                  [0, 1, 0], [1, 2, 3]]
        source = self.source("tetra", 4, 3, points)
        target = self.root / "bounded.inp"
        result = convert_mesh(source, target, "inp")
        rows = target.read_text().split("*NODE\n", 1)[1].split("*ELEMENT", 1)[0].splitlines()
        coordinates = [row.split(",")[1:] for row in rows]
        self.assertTrue(all(len(value) <= 20 for row in coordinates for value in row))
        self.np.testing.assert_allclose([[float(v) for v in row] for row in coordinates],
                                        points, rtol=5e-13, atol=1e-300)
        self.assertEqual(result["verification"]["numeric_field_max_characters"], 20)

    def test_keyword_linear_shapes_have_documented_node_repetitions(self):
        expected = {"triangle": [1,2,3,3], "quad": [1,2,3,4],
                    "tetra": [1,2,3,4,4,4,4,4], "wedge": [1,2,3,4,5,5,6,6],
                    "hexahedron": [1,2,3,4,5,6,7,8]}
        for kind, ids in expected.items():
            with self.subTest(kind=kind):
                nodes = len(set(ids))
                source = self.source(kind, nodes, 2 if kind in ("triangle", "quad") else 3)
                target = self.root / f"{kind}.key"
                result = convert_mesh(source, target, "lsdyna")
                lines = target.read_text().splitlines()
                first = next(index for index, line in enumerate(lines) if line.startswith("*ELEMENT_"))
                self.assertEqual(list(map(int, lines[first + 1].split(",")))[2:], ids)
                restored, sets = _read_key_subset(target)
                self.np.testing.assert_array_equal(restored.cells_dict[kind], self.np.arange(nodes).reshape(1, nodes))
                self.assertEqual(sets, {("ELEM", "DOMAIN"): [1]})
                self.assertTrue(result["verification"]["readback"])

    def test_keyword_does_not_reduce_quadratic_elements_or_replace_target(self):
        source = self.source("tetra10", 10, 3)
        target = self.root / "existing.key"
        target.write_text("original")
        with self.assertRaisesRegex(ValueError, "tetra10"):
            convert_mesh(source, target, "lsdyna")
        self.assertEqual(target.read_text(), "original")

    def test_structural_apdl_solid_types_and_no_silent_quadratic_loss(self):
        for kind, count, element in (("tetra", 4, 285), ("tetra10", 10, 187), ("hexahedron", 8, 185)):
            with self.subTest(kind=kind):
                target = self.root / f"{kind}.apdl"
                result = convert_mesh(self.source(kind, count, 3), target, "ansys_structural")
                text = target.read_text()
                self.assertIn(f"ET,1,{element}\n", text)
                self.assertNotIn("ET,1,200", text)
                if kind == "tetra10":
                    self.assertIn("EN,1,1,2,3,4,5,6,7,8\nEMORE,9,10", text)
                self.assertFalse(result["verification"]["solver_executed"])
        with self.assertRaisesRegex(ValueError, "hexahedron20"):
            convert_mesh(self.source("hexahedron20", 20, 3), self.root / "unsupported.apdl", "ansys_structural")

    def test_structural_apdl_planar_triangle_and_quad_shell(self):
        source = self.source("triangle", 3, 2, [[0,0,0],[1,0,0],[0,1,0]])
        target = self.root / "plane.apdl"
        convert_mesh(source, target, "ansys_structural", planar=True)
        text = target.read_text()
        self.assertIn("ET,1,182\nKEYOPT,1,3,0", text)
        self.assertIn("EN,1,1,2,3,3", text)
        with self.assertRaisesRegex(ValueError, "triangle"):
            convert_mesh(source, self.root / "tri_shell.apdl", "ansys_structural", planar=False)
        quad = self.source("quad", 4, 2, [[0,0,0],[1,0,0],[1,1,.1],[0,1,0]])
        convert_mesh(quad, self.root / "quad_shell.apdl", "ansys_structural", planar=False)
        self.assertIn("ET,1,181", (self.root / "quad_shell.apdl").read_text())
        with self.assertRaisesRegex(ValueError, "plan XY"):
            convert_mesh(quad, self.root / "not_planar.apdl", "ansys_structural", planar=True)

    def test_structural_components_share_a_unique_namespace(self):
        mesh = self.meshio.Mesh([[0,0,0],[1,0,0],[0,1,0]], [("triangle", [[0,1,2]])],
                                cell_sets={"N_EDGE": [self.np.array([0])]}, point_sets={"EDGE": self.np.array([0,1])})
        _, verification, _ = _write_apdl_structural(self.root / "components.apdl", mesh, planar=True)
        names = [item["component"] for item in verification["component_name_map"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, ["N_EDGE", "N_EDGE_2"])


if __name__ == "__main__":
    unittest.main()
