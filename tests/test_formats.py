import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

from atelier.formats import FORMATS, convert_mesh, export_bundle


def _gmsh_available():
    try:
        import gmsh
        return True
    except (ImportError, OSError):
        return False


@unittest.skipUnless(_gmsh_available() and importlib.util.find_spec("meshio"), "Gmsh/meshio non disponibles")
class FormatIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gmsh
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.surface = cls.root / "source_surface.msh"
        cls.solid = cls.root / "source_tetra10.msh"
        for path, dimension, order in ((cls.surface, 2, 1), (cls.solid, 3, 2)):
            gmsh.initialize(["test-formats", "-nopopup"], readConfigFiles=False)
            try:
                gmsh.option.setNumber("General.Terminal", 0)
                gmsh.option.setNumber("Mesh.MeshSizeMin", .5)
                gmsh.option.setNumber("Mesh.MeshSizeMax", .5)
                if dimension == 2:
                    tag = gmsh.model.occ.addRectangle(0, 0, 0, 1, 1)
                else:
                    tag = gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
                gmsh.model.occ.synchronize()
                group = gmsh.model.addPhysicalGroup(dimension, [tag])
                gmsh.model.setPhysicalName(dimension, group, "DOMAIN")
                duplicate = gmsh.model.addPhysicalGroup(dimension, [tag])
                gmsh.model.setPhysicalName(dimension, duplicate, "BODY_ALIAS")
                boundaries = [v for _, v in gmsh.model.getBoundary([(dimension, tag)], oriented=False)]
                group = gmsh.model.addPhysicalGroup(dimension-1, boundaries)
                gmsh.model.setPhysicalName(dimension-1, group, "BOUNDARY")
                group = gmsh.model.addPhysicalGroup(dimension-1, boundaries[:1])
                gmsh.model.setPhysicalName(dimension-1, group, "LEFT EDGE")
                gmsh.model.mesh.generate(dimension)
                if order == 2:
                    gmsh.model.mesh.setOrder(order)
                gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
                gmsh.write(str(path))
            finally:
                gmsh.finalize()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_native_copy_and_source_protection(self):
        target = self.root / "copy.msh"
        result = convert_mesh(self.surface, target, "msh")
        self.assertEqual(self.surface.read_bytes(), target.read_bytes())
        self.assertTrue(result["verification"]["byte_identical"])
        with self.assertRaises(ValueError):
            convert_mesh(self.surface, self.surface, "msh")

    def test_med_retains_overlapping_families_and_mesh_dimension(self):
        import h5py
        import meshio
        target = self.root / "surface.med"
        result = convert_mesh(self.surface, target, "med")
        self.assertTrue(result["verification"]["groups_readback"])
        self.assertEqual(result["groups"]["elements"]["DOMAIN"], result["groups"]["elements"]["BODY_ALIAS"])
        restored = meshio.read(target)
        names = {name for family in restored.cell_tags.values() for name in family}
        self.assertTrue({"DOMAIN", "BODY_ALIAS", "BOUNDARY", "LEFT_EDGE"} <= names)
        with h5py.File(target) as file:
            self.assertEqual(file["ENS_MAA"]["mesh"].attrs["DIM"], 2)
            self.assertEqual(file["ENS_MAA"]["mesh"].attrs["ESP"], 3)

    def test_vtu_contains_all_group_masks(self):
        import meshio
        target = self.root / "surface.vtu"
        result = convert_mesh(self.surface, target, "vtu")
        restored = meshio.read(target)
        self.assertTrue(result["verification"]["readback"])
        self.assertTrue({"group:DOMAIN", "group:BODY_ALIAS", "group:BOUNDARY", "group:LEFT_EDGE"} <= restored.cell_data.keys())

    def test_new_solver_exports_preserve_overlapping_and_boundary_groups(self):
        from atelier.formats import _read_bdf_sets, _read_key_subset
        bdf = self.root / "surface.bdf"
        key = self.root / "surface.key"
        bdf_result = convert_mesh(self.surface, bdf, "nastran")
        key_result = convert_mesh(self.surface, key, "lsdyna")
        groups = _read_bdf_sets(bdf)
        _, key_groups = _read_key_subset(key)
        self.assertEqual(groups, key_groups)
        self.assertEqual(groups[("ELEM", "DOMAIN")], groups[("ELEM", "BODY_ALIAS")])
        self.assertIn(("NODE", "BOUNDARY"), groups)
        self.assertIn(("NODE", "LEFT_EDGE"), groups)
        self.assertGreaterEqual(len(groups[("NODE", "BOUNDARY")]), 8)
        self.assertEqual(bdf_result["groups"], key_result["groups"])

    def test_inp_planar_filters_boundary_elements_into_node_sets(self):
        import meshio
        target = self.root / "surface.inp"
        result = convert_mesh(self.surface, target, "inp", planar=True)
        text = target.read_text()
        self.assertIn("TYPE=CPS3", text)
        self.assertNotIn("TYPE=B31", text)
        self.assertIn("*NSET, NSET=BOUNDARY", text)
        self.assertIn("*ELSET, ELSET=DOMAIN", text)
        restored = meshio.read(target)
        self.assertEqual(set(restored.cells_dict), {"triangle"})
        self.assertEqual(len(restored.cells_dict["triangle"]), result["elements"])
        self.assertEqual(result["verification"]["formulation"], "plane_stress")

    def test_inp_solid_keeps_quadratic_tetra_connectivity(self):
        import meshio
        import numpy as np
        target = self.root / "volume.inp"
        result = convert_mesh(self.solid, target, "inp")
        self.assertIn("TYPE=C3D10", target.read_text())
        restored = meshio.read(target)
        original = meshio.read(self.solid)
        self.assertEqual(set(restored.cells_dict), {"tetra10"})
        np.testing.assert_array_equal(original.cells_dict["tetra10"], restored.cells_dict["tetra10"])
        self.assertEqual(result["verification"]["formulation"], "solid")

    def test_inp_hex20_uses_continuation_without_node_loss(self):
        import meshio
        import numpy as np
        corners = np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]], dtype=float)
        edges = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]
        points = np.vstack([corners, [(corners[a]+corners[b])/2 for a,b in edges]])
        source = self.root / "hex20.msh"
        original = meshio.Mesh(points, [("hexahedron20", np.arange(20).reshape(1,20))],
                               cell_data={"gmsh:physical": [np.array([1])], "gmsh:geometrical": [np.array([1])]},
                               field_data={"DOMAIN": np.array([1,3])})
        meshio.write(source, original, file_format="gmsh22", binary=False)
        target = self.root / "hex20.inp"
        convert_mesh(source, target, "inp")
        text = target.read_text()
        start = text.index("*ELEMENT, TYPE=C3D20\n") + len("*ELEMENT, TYPE=C3D20\n")
        records = text[start:].split("*", 1)[0].splitlines()
        self.assertEqual(len(records), 2)
        self.assertTrue(records[0].endswith(","))
        self.assertLessEqual(len(records[0].rstrip(",").split(",")), 16)
        restored = meshio.read(target)
        np.testing.assert_array_equal(restored.cells_dict["hexahedron20"], original.cells_dict["hexahedron20"])

    def test_apdl_is_mesh200_with_high_order_continuation(self):
        result = convert_mesh(self.solid, self.root / "ansys.dat", "ansys")
        text = (self.root / "ansys.dat").read_text()
        self.assertIn("ET,1,200", text)
        self.assertIn("KEYOPT,1,1,9", text)
        self.assertIn("EMORE,", text)
        self.assertIn("CM,DOMAIN,ELEM", text)
        self.assertIn("CM,N_BOUNDARY,NODE", text)
        self.assertEqual(result["verification"]["element_type"], "MESH200")
        self.assertTrue(any("degrés de liberté" in value for value in result["warnings"]))

    def test_unv_and_cast3m_bridge_read_back(self):
        result = convert_mesh(self.surface, self.root / "surface.unv", "unv")
        self.assertTrue(result["verification"]["readback"])
        bridge = convert_mesh(self.surface, self.root / "cast3m.dgibi", "cast3m")
        self.assertIn("LIRE 'UNV' 'cast3m.unv'", (self.root / "cast3m.dgibi").read_text())
        self.assertEqual(set(bridge["files"]), {"cast3m.dgibi", "cast3m.unv"})
        self.assertFalse(bridge["verification"]["cast3m_executed"])

    def test_bundle_manifest_includes_all_actual_files(self):
        result = export_bundle(self.surface, self.root / "bundle", planar=True)
        self.assertTrue(result["complete"], result["results"])
        self.assertEqual(len(result["results"]), len(FORMATS))
        with zipfile.ZipFile(self.root / "bundle" / "exports.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(result["files"]))
            self.assertIsNone(archive.testzip())

    def test_invalid_format_does_not_overwrite(self):
        target = self.root / "existing.inp"
        target.write_text("old")
        with self.assertRaises(ValueError):
            convert_mesh(self.surface, target, "invalid")
        self.assertEqual(target.read_text(), "old")

    def test_bundle_reports_unsupported_type_without_fake_success(self):
        import meshio
        import numpy as np
        points = np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[.5,0,0],[1,.5,0],[.5,1,0],[0,.5,0],[.5,.5,0]])
        source = self.root / "quad9.msh"
        meshio.write(source, meshio.Mesh(points, [("quad9", np.arange(9).reshape(1,9))],
                     cell_data={"gmsh:physical": [np.array([1])], "gmsh:geometrical": [np.array([1])]},
                     field_data={"DOMAIN": np.array([1,2])}), file_format="gmsh22", binary=False)
        folder = self.root / "partial"
        result = export_bundle(source, folder, ["vtu", "ansys"])
        self.assertFalse(result["complete"])
        self.assertEqual([v["status"] for v in result["results"]], ["ok", "error"])
        self.assertIn("quad9", result["results"][1]["error"])
        with zipfile.ZipFile(folder / "exports.zip") as archive:
            self.assertIn("mesh.vtu", archive.namelist())
            self.assertNotIn("mesh.dat", archive.namelist())


if __name__ == "__main__":
    unittest.main()
