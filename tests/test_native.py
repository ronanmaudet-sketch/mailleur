"""Independent integration checks for the Rust kernel / Python bridge.

Skipped tests mean that the native delivery has not been verified. The release
report must list such skips separately rather than counting them as successes.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def native_binary():
    # Use production discovery so an extracted delivery can be tested using its
    # bundled platform binary without a developer's target/ directory.
    try:
        from atelier.native import core_path
        return core_path()
    except (ImportError, OSError):
        return None


def plate():
    return {
        "schema_version": 1, "name": "Plaque native indépendante", "mode": "planar",
        "units": {"input": "mm", "internal": "m"},
        "geometry": {"kind": "plate", "width": 20, "height": 10,
                     "corner_radius": 1, "holes": [{"x": 10, "y": 5, "radius": 2}]},
        "mesh": {"size": 2, "min_size": .5, "order": 1, "algorithm": "automatic",
                 "threads": 1, "max_elements": 20000, "recombine": False},
        "repair": {"enabled": False},
    }


class NativeKernelIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("atelier.native") is None:
            raise unittest.SkipTest("Le pont Rust atelier.native n'est pas encore disponible")
        cls.binary = native_binary()
        if cls.binary is None:
            raise unittest.SkipTest("Compiler rust_core avant la vérification native")
        try:
            import meshio
            from atelier.native import NativeSession
        except (ImportError, OSError) as error:
            raise unittest.SkipTest(f"Dépendance native absente : {error}") from error
        cls.meshio = meshio
        cls.session_type = NativeSession

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # These small fixtures should complete quickly; a native stall must
        # fail the integration check rather than block a release indefinitely.
        self.session = self.session_type(self.root / "workspace", timeout=30)
        self.addCleanup(self.session.cancel)

    @staticmethod
    def directory(result):
        return Path(result["directory"])

    def assert_real_rust_result(self, result):
        self.assertIsInstance(result["report"], dict)
        self.assertIsInstance(result["preview"], dict)
        report = result["report"]
        provenance = [report.get("native_kernel", {}), report.get("backend", {})]
        self.assertTrue(any(str(item.get("language", "")).lower() == "rust" for item in provenance),
                        "Le rapport doit identifier le noyau Rust réellement utilisé")
        self.assertTrue((self.directory(result) / "mesh.msh").is_file())
        self.assertGreater(len(result["preview"]["points"]), 0)

    def test_generation_calls_rust_and_keeps_input_immutable(self):
        project = plate()
        original = deepcopy(project)
        with patch("atelier.backend.run_project", side_effect=AssertionError("Python meshing is forbidden here")):
            result = self.session.generate(project)
        self.assert_real_rust_result(result)
        self.assertEqual(project, original)
        report = result["report"]
        numerical = report.get("rust_metrics", report.get("metrics", {}))
        self.assertEqual(numerical.get("computation_backend"), "rust")
        self.assertGreater(report["elements"], 0)
        self.assertGreater(report["quality"]["minimum"], 0)
        # A declared hole must remain empty after the Rust CAD/mesh pipeline.
        points = result["preview"]["points"]
        for face in result["preview"]["surface_triangles"]:
            x = sum(points[index][0] for index in face[:3]) / 3
            y = sum(points[index][1] for index in face[:3]) / 3
            self.assertGreaterEqual((x - .01) ** 2 + (y - .005) ** 2, (.002 * .99) ** 2)

    def test_input_millimetres_are_meshed_in_si_and_exported_back(self):
        result = self.session.generate(plate())
        points = result["preview"]["points"]
        self.assertAlmostEqual(max(point[0] for point in points), .02, places=10)
        self.assertAlmostEqual(max(point[1] for point in points), .01, places=10)
        exported = self.root / "roundtrip.med"
        self.session.export(self.directory(result), exported, "med", units="mm", planar=True)
        mesh = self.meshio.read(exported)
        self.assertAlmostEqual(float(mesh.points[:, 0].max()), 20, places=7)
        self.assertAlmostEqual(float(mesh.points[:, 1].max()), 10, places=7)

    def test_inspection_and_explicit_surface_selection(self):
        project = plate()
        project.update(mode="surface")
        project["geometry"] = {"kind": "box", "width": 20, "height": 12, "depth": 8}
        inspected = self.session.inspect(project)
        faces = [entity for entity in inspected["report"]["entity_catalog"]
                 if entity.get("dimension", entity.get("dim")) == 2]
        self.assertEqual(len(faces), 6)
        selected = faces[0]["tag"]
        project["selection"] = {"scope": "entities", "dimension": 2, "tags": [selected]}
        result = self.session.generate(project)
        self.assert_real_rust_result(result)
        tags = result["preview"]["face_tags"]
        self.assertGreater(len(tags), 0)
        self.assertEqual(set(tags), {selected})
        self.assertEqual(len(tags), len(result["preview"]["surface_triangles"]))

    def test_local_surface_remesh_preserves_the_other_surfaces(self):
        project = plate()
        project["mode"] = "surface"
        project["geometry"] = {"kind": "box", "width": 20, "height": 12, "depth": 8}
        first = self.session.generate(project)
        faces = [entity for entity in first["report"]["entity_catalog"]
                 if entity.get("dimension", entity.get("dim")) == 2]
        tag = faces[0]["tag"]
        original = self.meshio.read(self.directory(first) / "mesh.msh")
        project["selection"] = {"scope": "entities", "dimension": 2, "tags": [tag]}
        project["mesh"]["size"] = 1
        project["mesh"]["min_size"] = .25
        changed = self.session.remesh(project, previous_directory=self.directory(first))
        remesh = changed["report"]["remesh"]
        self.assertIs(remesh["unselected_elements_unchanged"], True)
        updated = self.meshio.read(self.directory(changed) / "mesh.msh")

        def signatures(mesh, excluded):
            result = {}
            tags = mesh.cell_data["gmsh:geometrical"]
            for block, entity_tags in zip(mesh.cells, tags):
                if not block.type.startswith(("triangle", "quad")):
                    continue
                for connectivity, entity in zip(block.data, entity_tags):
                    entity = int(entity)
                    if entity == excluded:
                        continue
                    vertices = tuple(sorted(tuple(round(float(x), 12) for x in mesh.points[index])
                                            for index in connectivity))
                    result.setdefault(entity, []).append((block.type, vertices))
            return {key: sorted(value) for key, value in result.items()}

        before = signatures(original, tag)
        self.assertGreater(len(before), 0)
        self.assertEqual(before, signatures(updated, tag))

    def test_invalid_entity_and_unit_are_rejected(self):
        project = plate()
        project["selection"] = {"scope": "entities", "dimension": 2, "tags": [999999]}
        with self.assertRaises((ValueError, RuntimeError)):
            self.session.generate(project)
        project = plate()
        project["units"]["input"] = "invalid-unit"
        with self.assertRaises((ValueError, RuntimeError)):
            self.session.generate(project)

    def test_three_dimensional_units_apply_to_every_axis(self):
        project = plate()
        project["mode"] = "volume"
        project["geometry"] = {"kind": "box", "width": 20, "height": 12, "depth": 8}
        box = self.session.generate(project)
        for axis, expected in enumerate((.02, .012, .008)):
            values = [point[axis] for point in box["preview"]["points"]]
            self.assertAlmostEqual(min(values), 0, places=10)
            self.assertAlmostEqual(max(values), expected, places=10)
        project["geometry"] = {"kind": "sphere", "radius": 3}
        project["mesh"].update(size=.75, min_size=.25)
        sphere = self.session.generate(project)
        for axis in range(3):
            values = [point[axis] for point in sphere["preview"]["points"]]
            self.assertGreater(max(values) - min(values), .0058)
            self.assertLessEqual(max(values) - min(values), .0060002)
            self.assertLessEqual(max(abs(value) for value in values), .0030001)

    def test_periodic_volume_faces_have_actual_translated_node_pairs(self):
        project = plate()
        project["mode"] = "volume"
        project["geometry"] = {"kind": "box", "width": 20, "height": 12, "depth": 8}
        project["mesh"].update(size=4, min_size=1)
        inspected = self.session.inspect(project)
        faces = [entity for entity in inspected["report"]["entity_catalog"]
                 if entity.get("dimension", entity.get("dim")) == 2]
        left = min(faces, key=lambda entity: entity["center"][0])
        right = max(faces, key=lambda entity: entity["center"][0])
        project["correspondences"] = [{"dimension": 2, "master": left["tag"],
                                       "slave": right["tag"], "translation": [20, 0, 0]}]
        result = self.session.generate(project)
        matches = result["report"]["correspondences"]
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual(match["master"], left["tag"])
        self.assertEqual(match["slave"], right["tag"])
        self.assertGreater(match["matched_nodes"], 0)
        self.assertEqual(match["matched_nodes"], len(match["node_pairs"]))
        points = dict(zip(result["preview"]["node_tags"], result["preview"]["points"]))
        for slave, master in match["node_pairs"]:
            for axis, expected in enumerate((.02, 0, 0)):
                self.assertAlmostEqual(points[slave][axis] - points[master][axis], expected, places=9)
        # Units also apply to the affine metadata, not just node coordinates.
        exported = self.root / "periodic_mm.msh"
        self.session.export(self.directory(result), exported, "msh", units="mm")
        reread = self.meshio.read(exported)
        links = reread.gmsh_periodic or []
        faces = [link for link in links if int(link[0]) == 2
                 and list(map(int, link[1])) == [right["tag"], left["tag"]]]
        self.assertEqual(len(faces), 1)
        affine = faces[0][2].reshape(-1)
        for index, expected in ((3, 20), (7, 0), (11, 0)):
            self.assertAlmostEqual(float(affine[index]), expected, places=8)

    def test_display_tessellation_is_not_exportable_as_analysis_mesh(self):
        project = plate()
        project["mode"] = "surface"
        project["geometry"] = {"kind": "box", "width": 20, "height": 12, "depth": 8}
        inspected = self.session.inspect(project)
        self.assertIs(inspected["report"]["display_only"], True)
        destination = self.root / "not_an_analysis_mesh.med"
        with self.assertRaises((ValueError, RuntimeError)):
            self.session.export(self.directory(inspected), destination, "med", units="mm")
        self.assertFalse(destination.exists())

    def test_display_tessellation_is_not_a_local_remeshing_source(self):
        project = plate()
        project["mode"] = "surface"
        project["geometry"] = {"kind": "box", "width": 20, "height": 12, "depth": 8}
        inspected = self.session.inspect(project)
        face = next(entity for entity in inspected["report"]["entity_catalog"]
                    if entity.get("dimension", entity.get("dim")) == 2)
        project["selection"] = {"scope": "entities", "dimension": 2, "tags": [face["tag"]]}
        with self.assertRaises((ValueError, RuntimeError)):
            self.session.remesh(project, previous_directory=self.directory(inspected))

    def test_step_header_units_are_preserved_for_different_work_units(self):
        # Python is used only to write the CAD fixture. Every inspected/meshed
        # model below still goes through the compiled Rust executable.
        import gmsh
        source_mm = self.root / "one_by_two_by_three_mm.step"
        gmsh.initialize(["fixture", "-nopopup"], readConfigFiles=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("unit-fixture")
            gmsh.model.occ.addBox(0, 0, 0, 1, 2, 3)
            gmsh.model.occ.synchronize()
            gmsh.write(str(source_mm))
        finally:
            gmsh.finalize()
        text = source_mm.read_text(encoding="utf-8")
        self.assertIn("SI_UNIT(.MILLI.,.METRE.)", text)
        source_m = self.root / "one_by_two_by_three_m.step"
        source_m.write_text(text.replace("SI_UNIT(.MILLI.,.METRE.)", "SI_UNIT($,.METRE.)"), encoding="utf-8")
        for source, physical_scale in ((source_mm, .001), (source_m, 1)):
            for work_unit, unit_scale in (("mm", .001), ("cm", .01), ("m", 1), ("in", .0254)):
                with self.subTest(source=source.name, work_unit=work_unit):
                    project = plate()
                    project["mode"] = "volume"
                    project["geometry"] = {"kind": "import"}
                    project["units"]["input"] = work_unit
                    project["mesh"].update(size=.5 * physical_scale / unit_scale,
                                           min_size=.1 * physical_scale / unit_scale)
                    result = self.session.generate(project, source=source)
                    for axis, dimension in enumerate((1, 2, 3)):
                        values = [point[axis] for point in result["preview"]["points"]]
                        self.assertAlmostEqual(min(values), 0, places=9)
                        self.assertAlmostEqual(max(values), dimension * physical_scale, places=9)

    def test_stl_import_accepts_the_gui_identity_transform(self):
        source = self.root / "surface.stl"
        source.write_text("""solid open_surface
facet normal 0 0 1
  outer loop
    vertex 0 0 0
    vertex 20 0 0
    vertex 0 10 0
  endloop
endfacet
endsolid open_surface
""", encoding="ascii")
        project = plate()
        project["mode"] = "surface"
        project["geometry"] = {"kind": "import", "transform": {
            "translation": [0, 0, 0], "rotation_axis": [0, 0, 1],
            "rotation_degrees": 0, "scale": 1,
        }}
        result = self.session.generate(project, source=source)
        self.assert_real_rust_result(result)
        self.assertAlmostEqual(max(point[0] for point in result["preview"]["points"]), .02, places=9)
        self.assertAlmostEqual(max(point[1] for point in result["preview"]["points"]), .01, places=9)

    def test_all_export_formats_complete_without_crashing_the_interface_process(self):
        project = plate()
        project["mode"] = "volume"
        project["geometry"] = {"kind": "box", "width": 20, "height": 12, "depth": 8}
        native = self.session.generate(project)
        destination = self.root / "all_formats.zip"
        # Isolate this regression so a native converter fault is a recorded test
        # failure rather than terminating the complete verification suite.
        program = """import json,sys
from pathlib import Path
from atelier.native import NativeSession
result=NativeSession(Path(sys.argv[3]),timeout=20).export(Path(sys.argv[1]),Path(sys.argv[2]),'bundle',units='mm')
print(json.dumps(result,allow_nan=False))
"""
        process = subprocess.run([sys.executable, "-c", program, str(self.directory(native)),
                                  str(destination), str(self.root / "export_workspace")],
                                 cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr[-3000:] or process.stdout[-3000:])
        report = json.loads(process.stdout.splitlines()[-1])
        self.assertIs(report["complete"], True)
        from atelier.formats import FORMATS
        self.assertEqual({row["format"] for row in report["results"]}, set(FORMATS))
        self.assertTrue(all(row["status"] == "ok" for row in report["results"]))
        with zipfile.ZipFile(destination) as archive:
            names = set(archive.namelist())
            self.assertTrue({"mesh.msh", "mesh.med", "mesh.inp", "mesh.vtu", "mesh.unv",
                             "mesh.dat", "import_cast3m.dgibi", "export_manifest.json"}.issubset(names))

    def test_training_and_advice_execute_in_rust_without_python_training_fallback(self):
        first = plate()
        second = plate()
        second["name"] = "Plaque carrée à congés"
        second["geometry"] = {"kind": "plate", "width": 14, "height": 14,
                              "corner_radius": 2, "holes": []}
        cases = [{"family": "qa_perforated", "project": first},
                 {"family": "qa_rounded", "project": second}]
        with patch("atelier.advisor.train_advisor", side_effect=AssertionError("Python training is forbidden here")), \
             patch("atelier.backend.run_project", side_effect=AssertionError("Python meshing is forbidden here")):
            trained = self.session.train(epochs=10, seed=42, cases=cases)
            selected = self.session.generate(first, use_advisor=True)
        self.assertEqual(trained["kind"], "training")
        report = trained["report"]
        self.assertGreater(report["training_cases"], 0)
        self.assertGreater(report["validation_cases"], 0)
        self.assertIsInstance(report["deploy_enabled"], bool)
        self.assertTrue(report.get("computation_backend") == "rust"
                        or report.get("native_kernel", {}).get("language") == "rust")
        model_path = self.directory(trained) / "guide.json"
        model = json.loads(model_path.read_text(encoding="utf-8"))
        self.assertTrue(model["input_weights"])
        self.assertTrue(model["output_weights"])
        for matrix in (model["input_weights"], model["output_weights"]):
            self.assertTrue(all(type(value) in (int, float) and math.isfinite(value)
                                for row in matrix for value in row))
        self.assertTrue((self.session.workspace / "models" / "guide.json").is_file())
        training_ids = set(model["training_geometry_ids"])
        reserved_ids = set(model["validation_geometry_ids"])
        self.assertTrue(training_ids)
        self.assertTrue(reserved_ids)
        self.assertFalse(training_ids & reserved_ids)
        advice = selected["report"]["advisor"]
        self.assertEqual(advice["computation_backend"], "rust")
        self.assertIn(advice["algorithm"], ("automatic", "delaunay", "frontal"))
        self.assertIsInstance(advice["use_model"], bool)
        self.assertTrue(advice["reason"])


if __name__ == "__main__":
    unittest.main()
