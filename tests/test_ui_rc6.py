"""Portable I/O controls for direct mesh requests; no numerical meshing here."""
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atelier.projects import default_examples, normalize_project


def project(mode="planar", shape="quadrilateral"):
    return {"name": "Maillage direct", "mode": mode,
            "geometry": {"kind": "box", "width": 4, "height": 3, "depth": 2}
                        if mode == "volume" else {"kind": "plate", "width": 4, "height": 3, "holes": []},
            "mesh": {"size": .5, "min_size": .1, "element_shape": shape}}


class DirectProjectControls(unittest.TestCase):
    def test_new_project_uses_order_two_and_preserves_explicit_order_one(self):
        self.assertEqual(normalize_project(project())["mesh"]["order"], 2)
        linear = project()
        linear["mesh"]["order"] = 1
        self.assertEqual(normalize_project(linear)["mesh"]["order"], 1)
        self.assertTrue(all(row["project"]["mesh"]["order"] == 2 for row in default_examples()))

    def test_shape_is_explicit_and_dimension_compatible(self):
        for mode, shapes in (("planar", ("triangle", "quadrilateral")),
                             ("surface", ("triangle", "quadrilateral")),
                             ("volume", ("tetrahedron", "hexahedron", "prism", "pyramid"))):
            for shape in shapes:
                with self.subTest(mode=mode, shape=shape):
                    self.assertEqual(normalize_project(project(mode, shape))["mesh"]["element_shape"], shape)
        for mode, shape in (("planar", "hexahedron"), ("volume", "quadrilateral"), ("surface", "pyramid")):
            with self.subTest(mode=mode, shape=shape), self.assertRaisesRegex(ValueError, "forme"):
                normalize_project(project(mode, shape))

    def test_recombination_is_rejected_even_in_automatic_mode(self):
        for shape in ("automatic", "triangle", "quadrilateral"):
            candidate = project(shape=shape)
            candidate["mesh"]["recombine"] = True
            with self.subTest(shape=shape), self.assertRaisesRegex(ValueError, "recombinaison"):
                normalize_project(candidate)

    def test_structured_controls_roundtrip_without_mutating_the_input(self):
        candidate = project("volume", "hexahedron")
        candidate["mesh"].update(quadratic_layout="complete",
                                 structured={"divisions": [12, 8, 4], "grading": [1.2, .8, 1.]})
        previous = deepcopy(candidate)
        actual = normalize_project(candidate)
        self.assertEqual(actual["mesh"]["structured"], candidate["mesh"]["structured"])
        self.assertEqual(candidate, previous)

    def test_invalid_structured_controls_are_refused(self):
        for value in (None, [], {"divisions": [1, 2]}, {"divisions": [1, True, 3]},
                      {"divisions": [1, 0, 3]}, {"divisions": [1, 2001, 3]},
                      {"grading": [1, 1]}, {"grading": [1, False, 1]}, {"grading": [1, 0, 1]}):
            candidate = project()
            candidate["mesh"]["structured"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_project(candidate)
        for shape in ("cube", "quad", None):
            with self.subTest(shape=shape), self.assertRaises(ValueError):
                normalize_project(project(shape=shape))
        candidate = project()
        candidate["mesh"]["quadratic_layout"] = "truncated"
        with self.assertRaises(ValueError):
            normalize_project(candidate)


class DirectDesktopControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("PySide6") is None:
            raise unittest.SkipTest("PySide6 doit être installé pour vérifier les contrôles Qt")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from tests import desktop_test_application
        from atelier.desktop import AtelierWindow
        cls.application = desktop_test_application()
        cls.window_type = AtelierWindow

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.window = self.window_type(workspace=Path(self.temporary.name))
        self.addCleanup(self.window.close)

    def test_default_order_two_and_strict_shape_controls(self):
        self.assertEqual(self.window.order.currentData(), 2)
        self.window.element_shape.setCurrentIndex(self.window.element_shape.findData("quadrilateral"))
        self.assertTrue(self.window.structured_group.isEnabled())
        self.assertFalse(self.window.algorithm.isEnabled())
        self.assertFalse(self.window.recombine.isEnabled())
        self.assertFalse(self.window.project_from_form()["mesh"]["recombine"])

    def test_direct_settings_survive_loading_and_form_collection(self):
        candidate = project("volume", "hexahedron")
        candidate["mesh"].update(order=2, quadratic_layout="complete", smoothing=5,
                                 structured={"divisions": [5, 3, 2], "grading": [1.2, .8, 1.]})
        self.window.load_project(candidate)
        actual = self.window.project_from_form()
        for key in ("order", "element_shape", "quadratic_layout", "structured", "smoothing"):
            self.assertEqual(actual["mesh"][key], candidate["mesh"][key])
        self.assertTrue(self.window.use_divisions.isChecked())
        self.assertTrue(self.window.structured_group.isEnabled())

    def test_order_one_is_preserved_and_volume_disables_surface_shapes(self):
        candidate = project("volume", "hexahedron")
        candidate["mesh"]["order"] = 1
        self.window.load_project(candidate)
        self.assertEqual(self.window.project_from_form()["mesh"]["order"], 1)
        self.assertFalse(self.window.quadratic_layout.isEnabled())
        item = self.window.element_shape.model().item(self.window.element_shape.findData("quadrilateral"))
        self.assertFalse(item.isEnabled())

    def test_mode_change_keeps_the_automatic_direct_grid_available(self):
        self.window.load_project(project("planar", "quadrilateral"))
        self.window.mode.setCurrentIndex(self.window.mode.findData("volume"))
        self.assertEqual(self.window.element_shape.currentData(), "automatic")
        self.assertTrue(self.window.structured_group.isEnabled())
        self.assertFalse(self.window.algorithm.isEnabled())
        self.window.use_divisions.setChecked(True)
        self.assertIn("divisions", self.window.project_from_form()["mesh"]["structured"])

    def test_corpus_profile_is_forwarded_and_loaded_from_its_native_report(self):
        with patch.object(self.window, "_start_corpus_operation") as start:
            self.window.plan_corpus()
            self.assertEqual(start.call_args.kwargs["profile"], "direct_quad_hex")
        self.window._corpus_succeeded({"operation": "corpus_status", "profile": "general"})
        self.assertEqual(self.window.corpus_profile.currentData(), "direct_quad_hex")
        self.assertIn("historique en consultation : general", self.window.corpus_summary.text())

    def test_switching_to_free_meshing_drops_the_previous_direct_grid(self):
        candidate = project()
        candidate["mesh"]["structured"] = {"divisions": [5, 3, 1]}
        self.window.load_project(candidate)
        self.window.element_shape.setCurrentIndex(self.window.element_shape.findData("triangle"))
        self.assertNotIn("structured", self.window.project_from_form()["mesh"])
        candidate["mesh"]["element_shape"] = "triangle"
        self.window.load_project(candidate)
        self.assertEqual(self.window.project_from_form()["mesh"]["structured"], candidate["mesh"]["structured"])


if __name__ == "__main__":
    unittest.main()
