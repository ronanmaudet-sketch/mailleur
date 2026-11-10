"""Behavioral integration checks for cumulative learning and spatial quality.

All geometry evaluation and training in these checks uses the compiled Rust
kernel. Python only prepares fixtures and inspects the resulting artifacts.
Native skips must be reported separately by a release verification.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atelier.projects import load_portable_project, normalize_project, save_portable_project


def plate():
    return {
        "schema_version": 1, "name": "Nouvelle pièce à apprendre", "mode": "planar",
        "units": {"input": "mm", "internal": "m"},
        "geometry": {"kind": "plate", "width": 20, "height": 10,
                     "corner_radius": 1, "holes": [{"x": 10, "y": 5, "radius": 2}]},
        "mesh": {"size": 2, "min_size": .4, "order": 1, "algorithm": "automatic",
                 "threads": 1, "max_elements": 10000, "recombine": False},
        "repair": {"enabled": False},
        "quality_policy": {"poor_threshold": .2, "minimum_quality": .05, "strict": True},
        "critical_zones": [],
    }


class ContinuousPortableProjects(unittest.TestCase):
    def test_zones_learning_and_refinement_survive_portable_roundtrip(self):
        project = plate()
        project["critical_zones"] = [{"name": "Contact déclaré", "kind": "contact",
                                      "dimension": 1, "tags": [1], "buffer": .25,
                                      "minimum_quality": .35}]
        project["learning"] = {"max_trials": 8, "epochs": 30, "use_memory": True}
        project["refinement_regions"] = [{"center": [3, 3, 0], "radius": 1.5, "size": .5}]
        before = deepcopy(project)
        with tempfile.TemporaryDirectory() as directory:
            restored = load_portable_project(save_portable_project(project), Path(directory))
        self.assertEqual(restored["project"], normalize_project(project))
        self.assertEqual(restored["project"]["critical_zones"], project["critical_zones"])
        for key, value in project["learning"].items():
            self.assertEqual(restored["project"]["learning"][key], value)
        self.assertEqual(restored["project"]["refinement_regions"], project["refinement_regions"])
        self.assertEqual(project, before)

    def test_invalid_zone_and_learning_controls_are_rejected(self):
        invalid = [
            ("critical_zones", [{"name": "Invalide", "kind": "contact", "dimension": 4,
                                  "tags": [1], "buffer": 0, "minimum_quality": .3}]),
            ("critical_zones", [{"name": "Invalide", "kind": "stress", "dimension": 2,
                                  "tags": [], "buffer": 0, "minimum_quality": .3}]),
            ("critical_zones", [{"name": "Invalide", "kind": "interface", "dimension": 2,
                                  "tags": [1], "buffer": -1, "minimum_quality": .3}]),
            ("quality_policy", {"poor_threshold": 1.1}),
            ("quality_policy", {"max_poor_fraction": -.1}),
            ("quality_policy", {"max_poor_elements": True}),
            ("learning", {"max_trials": 0}),
            ("learning", {"epochs": 201}),
            ("learning", {"use_memory": "yes"}),
            ("refinement_regions", [{"center": [0, 0], "radius": 1, "size": 1}]),
        ]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                project = plate()
                project[key] = value
                with self.assertRaises(ValueError):
                    normalize_project(project)


class ContinuousNativeIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from atelier.native import NativeSession, core_path, gmsh_library
            core_path()
            gmsh_library()
        except (ImportError, OSError) as error:
            raise unittest.SkipTest(f"Dépendance du noyau Rust absente : {error}") from error
        if not hasattr(NativeSession, "learn"):
            raise unittest.SkipTest("Le pont d'apprentissage continu rc3 doit être installé")
        cls.session_type = NativeSession

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.session_type(self.root / "workspace", timeout=40)
        self.addCleanup(self.session.cancel)

    def learn(self, project, **options):
        options.setdefault("max_trials", 2)
        options.setdefault("epochs", 8)
        return self.session.learn(project, **options)

    def assert_analysis_mesh(self, result):
        directory = Path(result["directory"])
        self.assertTrue((directory / "mesh.msh").is_file())
        self.assertTrue((directory / "model.brep").is_file())
        self.assertGreater(result["report"]["elements"], 0)
        self.assertTrue(result["report"]["quality"]["accepted"])
        self.assertGreater(result["report"]["quality"]["minimum"], 0)
        self.assertTrue(result["preview"]["points"])
        self.assertEqual(result["report"]["native_kernel"]["language"].lower(), "rust")

    def test_unseen_geometry_is_really_meshed_and_a_second_session_revalidates_recipe(self):
        project = plate()
        before = deepcopy(project)
        with patch("atelier.backend.run_project", side_effect=AssertionError("Python meshing forbidden")), \
             patch("atelier.advisor.train_advisor", side_effect=AssertionError("Python training forbidden")):
            first = self.learn(project)
            self.assert_analysis_mesh(first)
            self.assertFalse(first["report"]["experience"]["memory_reused"])
            info_before = self.session.memory_info()
            other_session = self.session_type(self.session.workspace, timeout=40)
            self.addCleanup(other_session.cancel)
            second = other_session.learn(project, max_trials=1, epochs=8)
            self.assert_analysis_mesh(second)
        self.assertTrue(second["report"]["experience"]["memory_reused"])
        self.assertEqual(second["report"]["experience"]["executed_trials"], 1)
        self.assertNotEqual(first["directory"], second["directory"])
        self.assertEqual(first["report"]["experience"]["context_id"],
                         second["report"]["experience"]["context_id"])
        self.assertGreater(other_session.memory_info()["total_trials"], info_before["total_trials"])
        self.assertEqual(project, before)
        saved = json.loads((Path(second["directory"]) / "session.json").read_text())
        self.assertEqual(saved["project"], second["report"]["experience"]["chosen_project"])

    def test_critical_context_change_cannot_reuse_an_unqualified_recipe(self):
        project = plate()
        first = self.learn(project, max_trials=1)
        changed = deepcopy(project)
        changed["critical_zones"] = [{"name": "Raccord", "kind": "interface", "dimension": 2,
                                      "tags": [1], "buffer": 0, "minimum_quality": .05}]
        second = self.learn(changed, max_trials=1)
        self.assertFalse(second["report"]["experience"]["memory_reused"])
        self.assertNotEqual(first["report"]["experience"]["context_id"],
                            second["report"]["experience"]["context_id"])
        self.assertEqual(first["report"]["experience"]["geometry_id"],
                         second["report"]["experience"]["geometry_id"])
        zone = second["report"]["quality"]["critical_zones"][0]
        self.assertEqual(zone["elements"], second["report"]["elements"])
        self.assertEqual(zone["below_minimum"], 0)

    def test_a_new_shape_extends_history_instead_of_erasing_previous_experiences(self):
        first = self.learn(plate(), max_trials=1)
        info_before = self.session.memory_info()
        different = plate()
        different["geometry"] = {"kind": "polygon", "outer": [[0, 0], [16, 0], [16, 4],
                                                                         [6, 4], [6, 12], [0, 12]]}
        second = self.learn(different, max_trials=1)
        info_after = self.session.memory_info()
        self.assertNotEqual(first["report"]["experience"]["geometry_id"],
                            second["report"]["experience"]["geometry_id"])
        self.assertGreater(info_after["geometry_count"], info_before["geometry_count"])
        self.assertGreater(info_after["observations"], info_before["observations"])
        self.assertGreater(info_after["model_versions"], info_before["model_versions"])
        self.assertTrue(info_after["model"]["warm_started"])
        self.assertGreater(info_after["model"]["total_examples"], info_before["model"]["total_examples"])
        for result in (first, second):
            self.assertIn(result["report"]["experience"]["geometry_id"],
                          info_after["model"]["examples_by_group"])
        again = self.learn(plate(), max_trials=1)
        self.assertTrue(again["report"]["experience"]["memory_reused"])

    def test_timed_out_search_releases_process_and_memory_for_next_run(self):
        self.session.timeout = 0
        with self.assertRaisesRegex(RuntimeError, "(?i)(délai|timeout)"):
            self.learn(plate(), max_trials=64)
        self.assertIsNone(self.session._process)
        self.session.timeout = 40
        result = self.learn(plate(), max_trials=1)
        self.assert_analysis_mesh(result)

    def test_imported_step_is_part_of_learning_and_source_content_is_fingerprinted(self):
        import gmsh
        source = self.root / "unseen.step"
        gmsh.initialize(["fixture", "-nopopup"], readConfigFiles=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("new-shape")
            gmsh.model.occ.addBox(0, 0, 0, 6, 4, 3)
            gmsh.model.occ.synchronize()
            gmsh.write(str(source))
        finally:
            gmsh.finalize()
        project = plate()
        project["mode"] = "volume"
        project["geometry"] = {"kind": "import"}
        project["mesh"].update(size=1.5, min_size=.5)
        first = self.learn(project, source=source, max_trials=1)
        self.assert_analysis_mesh(first)
        renamed = self.root / "same_bytes_other_name.step"
        renamed.write_bytes(source.read_bytes())
        second = self.learn(project, source=renamed, max_trials=1)
        self.assertTrue(second["report"]["experience"]["memory_reused"])
        self.assertEqual(first["report"]["experience"]["geometry_id"],
                         second["report"]["experience"]["geometry_id"])
        self.assertAlmostEqual(max(p[0] for p in first["preview"]["points"]), .006, places=9)

    def test_impossible_quality_is_recorded_and_has_no_public_mesh_to_export(self):
        project = plate()
        project["quality_policy"].update(minimum_quality=.999999999, poor_threshold=.999999999,
                                         max_poor_elements=0)
        with self.assertRaises((RuntimeError, ValueError)):
            self.learn(project, max_trials=2)
        info = self.session.memory_info()
        self.assertGreaterEqual(info["failed_observations"], 1)
        reports = [path for path in (self.session.workspace / "native").glob("*/report.json")
                   if json.loads(path.read_text()).get("operation") == "learn"]
        self.assertEqual(len(reports), 1)
        failure = json.loads(reports[0].read_text())
        self.assertFalse(failure["experience"]["accepted"])
        self.assertLessEqual(failure["experience"]["executed_trials"], 2)
        self.assertFalse((reports[0].parent / "mesh.msh").exists())
        target = self.root / "forbidden.msh"
        with self.assertRaises((RuntimeError, ValueError)):
            self.session.export(reports[0].parent, target, "msh")
        self.assertFalse(target.exists())

    def test_unknown_critical_entity_is_not_silently_treated_as_an_empty_safe_zone(self):
        project = plate()
        project["critical_zones"] = [{"name": "Contact absent", "kind": "contact", "dimension": 2,
                                      "tags": [999999], "buffer": 0, "minimum_quality": .1}]
        with self.assertRaises((RuntimeError, ValueError)):
            self.session.generate(project)

    def test_diagnostic_quality_counts_every_element_and_rejected_mesh_cannot_be_exported(self):
        project = plate()
        project["quality_policy"].update(poor_threshold=1, max_poor_elements=0, strict=False)
        generated = self.session.generate(project)
        report = generated["report"]
        quality = report["quality"]
        self.assertFalse(quality["accepted"])
        self.assertGreater(quality["poor_count"], 0)
        self.assertAlmostEqual(quality["poor_fraction"], quality["poor_count"] / report["elements"])
        target = self.root / "unqualified.msh"
        with self.assertRaises((RuntimeError, ValueError)):
            self.session.export(Path(generated["directory"]), target, "msh")
        self.assertFalse(target.exists())

    def test_frontal_volume_with_spatial_size_field_is_rejected_explicitly(self):
        project = plate()
        project["mode"] = "volume"
        project["geometry"] = {"kind": "box", "width": 6, "height": 4, "depth": 3}
        project["mesh"].update(size=1.5, min_size=.4, algorithm="frontal")
        project["refinement_regions"] = [{"center": [3, 2, 1.5], "radius": 1, "size": .5}]
        with self.assertRaisesRegex((RuntimeError, ValueError), "(?i)(frontal|champ|field)"):
            self.session.generate(project)

    def test_boundary_zone_checks_adjacent_top_dimensional_elements(self):
        project = plate()
        inspected = self.session.inspect(project)
        curve = next(row for row in inspected["report"]["entity_catalog"] if row["dimension"] == 1)
        project["critical_zones"] = [{"name": "Bord en contact", "kind": "contact", "dimension": 1,
                                      "tags": [curve["tag"]], "buffer": .2, "minimum_quality": .05}]
        generated = self.session.generate(project)
        zone = generated["report"]["quality"]["critical_zones"][0]
        self.assertGreater(zone["elements"], 0)
        self.assertLessEqual(zone["elements"], generated["report"]["elements"])
        self.assertGreaterEqual(zone["minimum"], .05)

    def test_protected_boundary_can_refuse_mesh_despite_good_global_quality(self):
        project = plate()
        project["quality_policy"].update(poor_threshold=.01, minimum_quality=.01, strict=False)
        inspected = self.session.inspect(project)
        curve = next(row for row in inspected["report"]["entity_catalog"] if row["dimension"] == 1)
        project["critical_zones"] = [{"name": "Contact exigeant", "kind": "contact", "dimension": 1,
                                      "tags": [curve["tag"]], "buffer": 0, "minimum_quality": .99999999}]
        result = self.session.generate(project)
        quality = result["report"]["quality"]
        self.assertEqual(quality["poor_count"], 0)
        self.assertGreater(quality["minimum"], .01)
        self.assertGreater(quality["critical_zones"][0]["below_minimum"], 0)
        self.assertFalse(quality["accepted"])

    def test_inspection_remains_available_to_reselect_an_obsolete_protected_entity(self):
        project = plate()
        project["quality_policy"].update(minimum_quality=.99999999)
        project["critical_zones"] = [{"name": "Ancien contact", "kind": "contact", "dimension": 2,
                                      "tags": [999999], "buffer": 0, "minimum_quality": .99999999}]
        result = self.session.inspect(project)
        self.assertTrue(result["report"]["display_only"])
        self.assertTrue(result["report"]["quality"]["diagnostic_only"])
        self.assertTrue(result["report"]["entity_catalog"])

    def test_local_spherical_field_actually_produces_smaller_elements(self):
        project = plate()
        project["geometry"].update(corner_radius=0, holes=[])
        project["mesh"].update(size=2, min_size=.2, algorithm="delaunay")
        project["refinement_regions"] = [{"center": [10, 5, 0], "radius": 2.5, "size": .35}]
        result = self.session.generate(project)
        points = result["preview"]["points"]
        nearby, far = [], []
        seen = set()
        for face in result["preview"]["surface_triangles"]:
            for i, j in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
                edge = tuple(sorted((i, j)))
                if edge in seen:
                    continue
                seen.add(edge)
                a, b = points[i], points[j]
                center = [(x + y) / 2 for x, y in zip(a, b)]
                radius = math.hypot(center[0] - .01, center[1] - .005)
                length = math.dist(a, b)
                if radius < .0015:
                    nearby.append(length)
                elif radius > .006:
                    far.append(length)
        self.assertGreater(len(nearby), 5)
        self.assertGreater(len(far), 5)
        self.assertLess(sum(nearby) / len(nearby), .6 * sum(far) / len(far))


if __name__ == "__main__":
    unittest.main()
