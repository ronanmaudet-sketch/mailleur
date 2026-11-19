"""STL P2 keeps its piecewise-linear geometry and positive elements."""
import math
from pathlib import Path
import tempfile
import unittest

from atelier.formats import read_mesh
from atelier.native import NativeSession


class QuadraticDiscreteSurface(unittest.TestCase):
    def test_order_two_defaults_preserve_stl_facets_and_written_mid_edge_nodes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "open.stl"
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
            project = {"name": "Discrete P2 metre regression", "mode": "surface",
                       "geometry": {"kind": "import"},
                       "units": {"input": "m", "internal": "m"},
                       "mesh": {"element_shape": "triangle", "size": .6, "min_size": .1,
                                "max_elements": 10000}}
            session = NativeSession(root / "work", timeout=30)
            result = session.generate(project, source=source)
            report = result["report"]
            self.assertEqual(report["mesh_settings"]["order"], 2)
            self.assertIn("Triangle 6", report["cell_counts"])
            self.assertTrue(report["quality"]["accepted"])
            self.assertGreater(report["quality"]["min_jacobian_determinant"], 0)
            self.assertEqual(report["quality"]["invalid_elements"], 0)
            self.assertEqual(report["generation"]["high_order_geometry"], "piecewise_linear_discrete")
            self.assertIs(report["generation"]["high_order_optimization_applied"], False)
            vtu = session.export(Path(result["directory"]), root / "surface", "vtu", units="m")
            for path in (Path(result["directory"]) / "mesh.msh", Path(vtu["file"])):
                with self.subTest(format=path.suffix):
                    mesh = read_mesh(path)
                    triangles = [cell for cell in mesh.cells if cell.dim == 2]
                    self.assertTrue(triangles)
                    self.assertEqual({cell.type for cell in triangles}, {"triangle6"})
                    for block in triangles:
                        self.assertEqual(block.data.shape[1], 6)
                        for row in block.data:
                            points = [mesh.points[int(index)] for index in row]
                            for midpoint, (a, b) in enumerate(((0, 1), (1, 2), (2, 0)), 3):
                                expected = [(points[a][axis] + points[b][axis]) / 2 for axis in range(3)]
                                self.assertLessEqual(math.dist(points[midpoint], expected), 1e-10)


if __name__ == "__main__":
    unittest.main()
