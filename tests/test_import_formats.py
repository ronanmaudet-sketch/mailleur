"""Contrats de lecture, passerelles locales et remaillage réel des nouveaux formats."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from atelier.imports import (create_cadpack, import_capabilities, load_converters,
                             prepare_source, read_3mf, read_mesh_file, unpack_cadpack)
from atelier.projects import safe_filename, save_portable_project, load_portable_project

ROOT = Path(__file__).resolve().parents[1]
POINTS = [[0.,0.,0.], [2.,0.,0.], [0.,2.,0.], [0.,0.,2.]]
TRIANGLES = [[0,2,1], [0,1,3], [1,2,3], [2,0,3]]


def model_xml(*, unit="millimeter", components=False, transform=None):
    vertices = "".join(f'<vertex x="{p[0]}" y="{p[1]}" z="{p[2]}"/>' for p in POINTS)
    triangles = "".join(f'<triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>' for t in TRIANGLES)
    obj = f'<object id="1" type="model" name="Tetra"><mesh><vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object>'
    if components:
        obj += '<object id="2"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 3 0 0"/></components></object>'
    attribute = f' transform="{transform}"' if transform else ""
    return (f'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="{unit}">'
            f'<resources>{obj}</resources><build><item objectid="{2 if components else 1}"{attribute}/></build></model>')


def write_3mf(path, xml=None, extra=()):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", xml or model_xml())
        for name, data in extra:
            archive.writestr(name, data)


def imported_project(mode="volume"):
    return {"schema_version": 1, "name": "Import ouvert", "mode": mode,
            "geometry": {"kind": "import"}, "units": {"input": "mm", "internal": "m"},
            "mesh": {"size": .6, "min_size": .2, "order": 1, "threads": 1, "max_elements": 10000}}


class ImportContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_capabilities_separate_builtin_and_conditional_formats(self):
        with patch.dict(os.environ, {"MAILLEUR_CAD_CONVERTERS": ""}):
            rows = {row["extension"]: row for row in import_capabilities()["formats"]}
            self.assertTrue(rows[".obj"]["available"])
            self.assertTrue(rows[".3mf"]["available"])
            self.assertTrue(rows[".sldprt"]["conditional"])
            self.assertFalse(rows[".sldprt"]["available"])
            self.assertEqual(safe_filename("piece.prt.12"), "piece.prt.12")
            source = self.root / "piece.sldprt"; source.write_bytes(b"proprietary")
            with self.assertRaisesRegex(ValueError, "convertisseur local"):
                prepare_source(source, self.root / "conversion")
            self.assertEqual(source.read_bytes(), b"proprietary")
        for name in ("exploit.py", "script.geo", "body.exe", "../body.obj"):
            with self.assertRaises(ValueError): safe_filename(name)

    def test_converter_is_trusted_configuration_argv_and_does_not_overwrite(self):
        source = self.root / "piece $test.sldprt"; source.write_bytes(b"native data")
        script = self.root / "trusted_converter.py"
        script.write_text('import pathlib,sys\nassert pathlib.Path(sys.argv[1]).read_bytes()==b"native data"\npathlib.Path(sys.argv[2]).write_text("ISO-10303-21;\\nEND-ISO-10303-21;\\n")\n')
        config = self.root / "converters.json"
        config.write_text(json.dumps({"schema": "atelier.cad_converters.v1", "converters": [{
            "name": "Test de contrat, sans lecteur SolidWorks", "extensions": [".sldprt"],
            "command": [sys.executable, str(script), "{input}", "{output}"], "output_extension": ".step"}]}))
        with patch.dict(os.environ, {"MAILLEUR_CAD_CONVERTERS": str(config)}):
            result = prepare_source(source, self.root / "prepared")
            self.assertEqual(result["kind"], "direct")
            self.assertTrue(Path(result["file"]).read_bytes().startswith(b"ISO-10303-21"))
            self.assertTrue(import_capabilities()["formats"])
            with self.assertRaisesRegex(ValueError, "existe déjà"):
                prepare_source(source, self.root / "prepared")
        # The test is for the converter protocol, never a real native CAD reader.

    def test_converter_configuration_errors_do_not_hide_builtin_formats(self):
        config = self.root / "bad.json";config.write_text('{"schema":"wrong"}')
        with patch.dict(os.environ, {"MAILLEUR_CAD_CONVERTERS": str(config)}):
            info = import_capabilities()
            self.assertTrue(info["converter_configuration_error"])
            self.assertTrue(next(r for r in info["formats"] if r["extension"] == ".step")["available"])
            with self.assertRaises(ValueError): load_converters()

    def test_native_assembly_pack_preserves_assets_and_integrity(self):
        folder = self.root / "assembly";folder.mkdir()
        (folder / "main.sldasm").write_bytes(b"assembly")
        (folder / "pieces").mkdir();(folder / "pieces/body.sldprt").write_bytes(b"part")
        (folder / "private.txt").write_text("must not be included")
        output = self.root / "assembly.cadpack"
        result = create_cadpack("main.sldasm", folder, output)
        self.assertEqual(result["files"], 2)
        main = unpack_cadpack(output, self.root / "unpacked")
        self.assertEqual(main.read_bytes(), b"assembly")
        self.assertEqual((main.parent / "pieces/body.sldprt").read_bytes(), b"part")
        project = imported_project()
        portable = save_portable_project(project, output)
        restored = load_portable_project(portable, self.root / "portable")
        self.assertEqual(restored["source"].read_bytes(), output.read_bytes())
        with self.assertRaises(ValueError): create_cadpack("main.sldasm", folder, output)
        broken = self.root / "broken.cadpack"
        with zipfile.ZipFile(output) as source, zipfile.ZipFile(broken, "w") as target:
            for name in source.namelist():
                target.writestr(name, b"changed" if name.endswith(".sldprt") else source.read(name))
        with self.assertRaisesRegex(ValueError, "altéré"):
            unpack_cadpack(broken, self.root / "bad")

    def test_archive_path_traversal_links_and_case_collisions_are_rejected(self):
        manifest = json.dumps({"schema": "atelier.cadpack.v1", "source": "body.step"})
        link = zipfile.ZipInfo("link.step");link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for index, bad in enumerate(("../escape.step", "/escape.step", "C:/escape.step", "BODY.step", "bad. /body.step", link)):
            archive = self.root / f"bad{index}.cadpack"
            with zipfile.ZipFile(archive, "w") as file:
                file.writestr("cadpack.json", manifest);file.writestr("body.step", "cad")
                file.writestr(bad, "bad")
            with self.assertRaises(ValueError): unpack_cadpack(archive, self.root / f"bad{index}")
        self.assertFalse((self.root / "escape.step").exists())

    def test_3mf_build_instances_transforms_and_declared_units_are_decoded(self):
        source = self.root / "parts.3mf"
        write_3mf(source, model_xml(unit="meter", components=True, transform="1 0 0 0 1 0 0 0 1 0 4 0"))
        data = read_3mf(source, "mm")
        self.assertEqual(data["coordinate_scale"], 1000)
        self.assertEqual(len(data["objects"]), 1)
        transforms = data["objects"][0]["transforms"]
        self.assertEqual(transforms[0][3], 3)  # local component translation x
        self.assertEqual(transforms[1][7], 4)  # parent build translation y
        self.assertTrue(data["reject_overlapping_objects"])
        # No coordinate transform is computed by the Python file reader.
        self.assertEqual(data["objects"][0]["points"], POINTS)

    def test_3mf_required_extensions_cycles_external_paths_and_xml_entities_refused(self):
        cases = [model_xml().replace('<model xmlns=', '<model requiredextensions="p" xmlns='),
                 model_xml(components=True).replace('objectid="1" transform=', 'objectid="2" transform='),
                 model_xml(components=True).replace('<component objectid=', '<component path="other.model" objectid='),
                 '<!DOCTYPE model [<!ENTITY x "text">]>' + model_xml(),
                 model_xml(components=True).replace('<components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 3 0 0"/></components>', '<components/>')]
        for index, xml in enumerate(cases):
            source = self.root / f"bad{index}.3mf";write_3mf(source, xml)
            with self.assertRaises((ValueError, KeyError)): read_3mf(source, "mm")

    def test_external_abaqus_includes_and_hdf5_links_are_not_opened(self):
        source = self.root / "body.inp";source.write_text('*INCLUDE, INPUT=/private/file.inp\n')
        with patch("meshio.read", side_effect=AssertionError("must refuse before reader")):
            with self.assertRaisesRegex(ValueError, "INCLUDE"):
                read_mesh_file(source, "abaqus")
        import h5py
        med = self.root / "linked.med"
        with h5py.File(med, "w") as file:
            file["external"] = h5py.ExternalLink("private.h5", "/data")
        with self.assertRaisesRegex(ValueError, "HDF5"):
            read_mesh_file(med, "med")


class ImportNativeIntegration(unittest.TestCase):
    def setUp(self):
        from atelier.native import NativeSession, core_path, gmsh_library
        core_path();gmsh_library()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.session = NativeSession(self.root / "workspace", timeout=30)

    def tearDown(self):
        self.temp.cleanup()

    def test_standard_surface_and_volume_formats_really_remesh_without_modifying_source(self):
        import meshio
        cases = [("obj", ".obj", "triangle"), ("ply", ".ply", "triangle"), ("off", ".off", "triangle"),
                 ("vtu", ".vtu", "tetra"), ("vtk", ".vtk", "tetra"), ("abaqus", ".inp", "tetra"),
                 ("med", ".med", "tetra"), ("nastran", ".bdf", "tetra"), ("gmsh22", ".msh", "tetra"),
                 ("medit", ".mesh", "tetra"), ("netgen", ".vol", "tetra")]
        for reader, extension, kind in cases:
            with self.subTest(reader=reader):
                source = self.root / ("tetra" + extension)
                cells = TRIANGLES if kind == "triangle" else [[0,1,2,3]]
                kwargs = {"binary": False} if reader == "gmsh22" else {}
                meshio.write(source, meshio.Mesh(POINTS, [(kind, cells)]), file_format=reader, **kwargs)
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                result = self.session.generate(imported_project(), source)
                self.assertTrue(result["report"]["quality"]["accepted"])
                self.assertGreater(result["report"]["quality"]["evaluated_elements"], 0)
                self.assertIn("native_preparation", result["report"]["import_preparation"])
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
                directory = Path(result["directory"])
                metadata = json.loads((directory / "session.json").read_text())
                self.assertTrue((directory / metadata["source"]).is_file())
                self.assertTrue((directory / metadata["prepared_source"]).is_file())
                self.assertFalse((directory / "prepared/mesh_input.json").exists())

    def test_quad_surface_and_quadratic_volume_are_declared_reconstructions(self):
        import meshio
        source = self.root / "quad.obj"
        meshio.write(source, meshio.Mesh([[0,0,0],[2,0,0],[2,2,0],[0,2,0]], [("quad", [[0,1,2,3]])]), file_format="obj")
        result = self.session.generate(imported_project("planar"), source)
        self.assertTrue(result["report"]["quality"]["accepted"])
        # Curved/high-order input is reconstructed through corners, never
        # represented as a lossless high-order mesh conversion.
        points = POINTS + [[1,0,0],[1,1,0],[0,1,0],[0,0,1],[0,1,1],[1,0,1]]
        source = self.root / "quadratic.vtu"
        meshio.write(source, meshio.Mesh(points, [("tetra10", [list(range(10))])]), file_format="vtu")
        result = self.session.generate(imported_project(), source)
        self.assertTrue(result["report"]["import_preparation"]["native_preparation"]["higher_order_geometry_linearized"])

    def test_3mf_transform_and_file_unit_reach_the_real_native_mesh(self):
        source = self.root / "translated.3mf"
        write_3mf(source, model_xml(components=True, transform="1 0 0 0 1 0 0 0 1 0 4 0"))
        result = self.session.generate(imported_project(), source)
        points = result["preview"]["points"]
        self.assertAlmostEqual(min(p[0] for p in points), .003, places=8)
        self.assertAlmostEqual(min(p[1] for p in points), .004, places=8)
        self.assertAlmostEqual(max(p[0] for p in points), .005, places=8)

    def test_unv_volume_is_read_natively_and_reconstructed(self):
        project = imported_project();project["geometry"] = {"kind": "box", "width": 2, "height": 2, "depth": 2}
        first = self.session.generate(project)
        source = self.root / "volume.unv"
        self.session.export(Path(first["directory"]), source, "unv", units="mm")
        result = self.session.generate(imported_project(), source)
        self.assertTrue(result["report"]["quality"]["accepted"])
        report = result["report"]["import_preparation"]
        self.assertEqual(report["kind"], "native_mesh")
        self.assertGreater(report["native_preparation"]["source_cells"], 0)

    def test_altered_canonical_source_is_not_reused_for_remeshing(self):
        import meshio
        source = self.root / "body.vtu"
        meshio.write(source, meshio.Mesh(POINTS, [("tetra", [[0,1,2,3]])]), file_format="vtu")
        first = self.session.generate(imported_project(), source)
        directory = Path(first["directory"])
        metadata = json.loads((directory / "session.json").read_text())
        (directory / metadata["prepared_source"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "altérée"):
            self.session.remesh(imported_project(), previous_directory=directory)


if __name__ == "__main__":
    unittest.main()
