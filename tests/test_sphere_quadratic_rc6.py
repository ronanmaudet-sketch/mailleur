"""Analytic sphere P2 edge nodes must follow the curved CAD after transforms."""
import math
from pathlib import Path
import tempfile
import unittest

from atelier.formats import read_mesh
from atelier.native import NativeSession


class QuadraticAnalyticSphere(unittest.TestCase):
    def test_surface_mid_edge_nodes_follow_the_sphere_without_collapsing_to_vertices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = NativeSession(root / "work", timeout=60)
            for transformed in (False, True):
                with self.subTest(transformed=transformed):
                    geometry = {"kind": "sphere", "radius": 3}
                    scale, center = 1., [0., 0., 0.]
                    if transformed:
                        geometry["transform"] = {"scale": 1.3, "rotation_degrees": 37,
                                                 "rotation_axis": [1, 2, 3],
                                                 "translation": [2.4, -1.1, 4.7]}
                        scale, center = 1.3, [.0024, -.0011, .0047]
                    radius = .003 * scale
                    tolerance = 1e-12
                    p = {"name": "Analytic sphere quadratic regression", "mode": "surface",
                         "geometry": geometry, "units": {"input": "mm", "internal": "m"},
                         "mesh": {"element_shape": "triangle", "order": 2, "size": .8,
                                  "min_size": .1, "max_elements": 10000}}
                    result = session.generate(p)
                    report = result["report"]
                    self.assertEqual(report["mesh_settings"]["order"], 2)
                    self.assertIn("Triangle 6", report["cell_counts"])
                    self.assertTrue(report["quality"]["accepted"])
                    self.assertEqual(report["quality"]["invalid_elements"], 0)
                    self.assertGreater(report["quality"]["min_jacobian_determinant"], 0)
                    generation = report["generation"]
                    self.assertEqual(generation["high_order_geometry"], "analytic_sphere_edge_midpoint_projection")
                    self.assertIs(generation["high_order_optimization_applied"], False)
                    projection = generation["sphere_curving"]
                    self.assertEqual(projection["method"], "primary_edge_midpoint_radial_projection")
                    self.assertGreater(projection["projected_nodes"], 0)
                    self.assertAlmostEqual(projection["radius"], radius, delta=tolerance)
                    self.assertLessEqual(projection["maximum_radius_error"], tolerance)
                    for actual, expected in zip(projection["center"], center):
                        self.assertAlmostEqual(actual, expected, delta=tolerance)
                    export = session.export(Path(result["directory"]), root / f"sphere-{transformed}",
                                            "vtu", units="m")
                    for path in (Path(result["directory"]) / "mesh.msh", Path(export["file"])):
                        with self.subTest(format=path.suffix):
                            mesh = read_mesh(path)
                            triangles = [cell for cell in mesh.cells if cell.dim == 2]
                            self.assertTrue(triangles)
                            self.assertEqual({cell.type for cell in triangles}, {"triangle6"})
                            for block in triangles:
                                for row in block.data:
                                    self.assertEqual(len(set(int(i) for i in row)), 6)
                                    points = [mesh.points[int(index)] for index in row]
                                    for point in points:
                                        self.assertAlmostEqual(math.dist(point, center), radius, delta=tolerance)
                                    for midpoint, (a, b) in enumerate(((0, 1), (1, 2), (2, 0)), 3):
                                        # The midpoint of the short spherical arc
                                        # lies on the radial projection of its chord.
                                        radial = [points[a][axis] + points[b][axis] - 2 * center[axis]
                                                  for axis in range(3)]
                                        length = math.hypot(*radial)
                                        self.assertGreater(length, 0)
                                        expected = [center[axis] + radius * radial[axis] / length
                                                    for axis in range(3)]
                                        self.assertLessEqual(math.dist(points[midpoint], expected), tolerance)
                                        self.assertGreater(min(math.dist(points[midpoint], vertex)
                                                               for vertex in points[:3]), radius * 1e-8)


    def test_volume_curves_only_boundary_nodes_and_keeps_interior_edge_midpoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = NativeSession(root / "work", timeout=60)
            p = {"name": "Analytic sphere volume quadratic regression", "mode": "volume",
                 "geometry": {"kind": "sphere", "radius": 3},
                 "units": {"input": "mm", "internal": "m"},
                 "mesh": {"element_shape": "tetrahedron", "size": 1., "min_size": .2,
                          "max_elements": 20000}}
            result = session.generate(p)
            report = result["report"]
            self.assertEqual(report["mesh_settings"]["order"], 2)
            self.assertIn("Tetrahedron 10", report["cell_counts"])
            self.assertIn("Triangle 6", report["cell_counts"])
            self.assertTrue(report["quality"]["accepted"])
            self.assertEqual(report["quality"]["invalid_elements"], 0)
            self.assertGreater(report["quality"]["min_jacobian_determinant"], 0)
            generation = report["generation"]
            self.assertEqual(generation["high_order_geometry"], "analytic_sphere_edge_midpoint_projection")
            self.assertIs(generation["high_order_optimization_applied"], False)
            projection = generation["sphere_curving"]
            self.assertEqual(projection["interior_nodes_modified"], 0)
            self.assertGreater(projection["boundary_node_count"], 0)
            self.assertGreater(projection["minimum_boundary_quality"], 0)
            self.assertGreater(projection["minimum_boundary_jacobian"], 0)
            exported = session.export(Path(result["directory"]), root / "solid-sphere", "vtu", units="m")
            for path in (Path(result["directory"]) / "mesh.msh", Path(exported["file"])):
                with self.subTest(format=path.suffix):
                    mesh = read_mesh(path)
                    faces = [cell for cell in mesh.cells if cell.dim == 2]
                    volumes = [cell for cell in mesh.cells if cell.dim == 3]
                    self.assertTrue(faces)
                    self.assertTrue(volumes)
                    self.assertEqual({cell.type for cell in faces}, {"triangle6"})
                    self.assertEqual({cell.type for cell in volumes}, {"tetra10"})
                    boundary_mids = {int(index) for cell in faces for row in cell.data for index in row[3:]}
                    self.assertTrue(boundary_mids)
                    for index in boundary_mids:
                        self.assertAlmostEqual(math.hypot(*mesh.points[index]), .003, delta=1e-12)
                    interior_edges = 0
                    # read_mesh gives meshio/VTK canonical TET10 edge order,
                    # including the permutation from the native Gmsh file.
                    edges = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
                    for cell in volumes:
                        for row in cell.data:
                            for midpoint, (a, b) in enumerate(edges, 4):
                                node = int(row[midpoint])
                                if node in boundary_mids:
                                    continue
                                interior_edges += 1
                                first, second = mesh.points[int(row[a])], mesh.points[int(row[b])]
                                expected = [(first[axis] + second[axis]) / 2 for axis in range(3)]
                                self.assertLessEqual(math.dist(mesh.points[node], expected), 1e-12)
                    self.assertGreater(interior_edges, 0)


if __name__ == "__main__":
    unittest.main()
