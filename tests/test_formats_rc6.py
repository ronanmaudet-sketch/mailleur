"""Reference-node ordering checks for the quadratic prism/pyramid I/O bridge.

The reference edge lists below are independent of the Rust generator. Gmsh:
https://gmsh.info/doc/texinfo/gmsh.html#Node-ordering ; canonical VTK cells:
https://vtk.org/doc/nightly/html/classvtkQuadraticWedge.html
https://vtk.org/doc/nightly/html/classvtkQuadraticPyramid.html
"""
import builtins
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from atelier.formats import convert_mesh, export_bundle, read_mesh


_REFERENCE = {
    "wedge15": {
        "gmsh_type": 18,
        "corners": [[0, 0, 0], [2, 0, 0], [0, 3, 0], [0, 0, 4], [2, 0, 4], [0, 3, 4]],
        "gmsh_edges": [(0, 1), (0, 2), (0, 3), (1, 2), (1, 4), (2, 5), (3, 4), (3, 5), (4, 5)],
        "canonical_edges": [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)],
    },
    "pyramid13": {
        "gmsh_type": 19,
        "corners": [[0, 0, 0], [2, 0, 0], [2, 3, 0], [0, 3, 0], [.8, 1.2, 4]],
        "gmsh_edges": [(0, 1), (0, 3), (0, 4), (1, 2), (1, 4), (2, 3), (2, 4), (3, 4)],
        "canonical_edges": [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4)],
    },
}


def reference_msh(path, name):
    """Write one ASCII MSH2 element directly in documented Gmsh order."""
    spec = _REFERENCE[name]
    corners = np.asarray(spec["corners"], dtype=float)
    points = np.vstack([corners, [(corners[a] + corners[b]) / 2 for a, b in spec["gmsh_edges"]]])
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", "1", '3 1 "DOMAIN"',
             "$EndPhysicalNames", "$Nodes", str(len(points))]
    lines.extend(f"{i} {x:.17g} {y:.17g} {z:.17g}" for i, (x, y, z) in enumerate(points, 1))
    lines.extend(["$EndNodes", "$Elements", "1",
                  f"1 {spec['gmsh_type']} 2 1 1 " + " ".join(map(str, range(1, len(points) + 1))),
                  "$EndElements", ""])
    path.write_text("\n".join(lines))
    return path


class QuadraticPrismPyramidFormats(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def assert_order(self, mesh, name):
        cell = mesh.cells_dict[name][0]
        spec = _REFERENCE[name]
        corners = np.asarray(spec["corners"], dtype=float)
        expected = np.vstack([corners, [(corners[a] + corners[b]) / 2 for a, b in spec["canonical_edges"]]])
        self.assertEqual(len(cell), len(expected))
        np.testing.assert_allclose(mesh.points[cell], expected, rtol=0, atol=1e-13)

    def test_read_gmsh_reference_nodes_in_correct_physical_order_and_restore_registry(self):
        from meshio._mesh import topological_dimension
        before = dict(topological_dimension)
        for name in _REFERENCE:
            source = reference_msh(self.root / (name + ".msh"), name)
            mesh = read_mesh(source)
            self.assertEqual(mesh.cells[0].dim, 3)
            self.assert_order(mesh, name)
            self.assertEqual(topological_dimension, before)
        broken = self.root / "broken.msh"
        broken.write_text("invalid")
        with self.assertRaises(ValueError):
            read_mesh(broken)
        self.assertEqual(topological_dimension, before)

    def test_vtu_and_med_preserve_all_nodes_order_and_domain_groups(self):
        for name in _REFERENCE:
            source = reference_msh(self.root / (name + ".msh"), name)
            for format_name in ("vtu", "med"):
                with self.subTest(element=name, format=format_name):
                    target = self.root / (name + "." + format_name)
                    result = convert_mesh(source, target, format_name)
                    self.assertTrue(result["verification"]["readback"])
                    self.assertTrue(result["verification"]["groups_readback"])
                    self.assertEqual(result["groups"]["elements"]["DOMAIN"], 1)
                    self.assert_order(read_mesh(target), name)

    def test_inp_wedge15_uses_c3d15_and_preserves_every_edge_node(self):
        source = reference_msh(self.root / "wedge15.msh", "wedge15")
        target = self.root / "wedge15.inp"
        convert_mesh(source, target, "inp")
        self.assertIn("*ELEMENT, TYPE=C3D15", target.read_text())
        self.assert_order(read_mesh(target), "wedge15")

    def test_unsupported_pyramid13_inp_does_not_replace_existing_target(self):
        source = reference_msh(self.root / "pyramid13.msh", "pyramid13")
        target = self.root / "existing.inp"
        target.write_text("previous export")
        with self.assertRaisesRegex(ValueError, "pyramid13"):
            convert_mesh(source, target, "inp")
        self.assertEqual(target.read_text(), "previous export")

    def test_native_msh_copy_does_not_require_meshio_or_numpy(self):
        source = reference_msh(self.root / "pyramid13.msh", "pyramid13")
        target = self.root / "copy.msh"
        original_import = builtins.__import__
        def without_conversion_libraries(name, *args, **kwargs):
            if name.split(".")[0] in ("meshio", "numpy"):
                raise ImportError("conversion libraries deliberately unavailable")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=without_conversion_libraries):
            result = convert_mesh(source, target, "msh")
        self.assertEqual(source.read_bytes(), target.read_bytes())
        self.assertTrue(result["verification"]["byte_identical"])
        self.assertFalse(result["verification"]["readback"])
        self.assertIsNone(result["nodes"])

    def test_bundle_keeps_native_and_supported_formats_when_pyramid_inp_is_refused(self):
        source = reference_msh(self.root / "pyramid13.msh", "pyramid13")
        result = export_bundle(source, self.root / "bundle", ["msh", "vtu", "inp"])
        self.assertFalse(result["complete"])
        self.assertEqual([row["status"] for row in result["results"]], ["ok", "ok", "error"])
        self.assertTrue((self.root / "bundle" / "mesh.msh").is_file())
        self.assertTrue((self.root / "bundle" / "groups.json").is_file())


if __name__ == "__main__":
    unittest.main()
