"""Qt widget checks; offscreen tests are not macOS/Windows acceptance tests."""
from __future__ import annotations

import importlib.util
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class IdleSession:
    """No computation should run just because a window is opened."""
    def __init__(self):
        self.calls = []

    def cancel(self):
        self.calls.append("cancel")

    def inspect(self, *args, **kwargs):
        self.calls.append("inspect")
        raise AssertionError("Opening the GUI must not mesh a model")

    generate = inspect
    remesh = inspect
    export = inspect


class DesktopWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("PySide6") is None or importlib.util.find_spec("atelier.desktop") is None:
            raise unittest.SkipTest("Installer PySide6 et les modules d'interface avant cette vérification")
        try:
            from PySide6.QtWidgets import QApplication
            from atelier.desktop import AtelierWindow
        except (ImportError, OSError) as error:
            raise unittest.SkipTest(f"Dépendance d'interface absente : {error}") from error
        cls.application = QApplication.instance() or QApplication([])
        cls.window_type = AtelierWindow

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session = IdleSession()
        self.window = self.window_type(workspace=Path(self.temp.name), session=self.session)
        self.window.resize(1360, 900)
        self.window.show()
        self.application.processEvents()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        self.application.processEvents()

    def test_three_pages_are_navigable_without_starting_a_calculation(self):
        from PySide6.QtWidgets import QStackedWidget, QTabBar
        tabs = self.window.findChild(QTabBar, "workflowTabs")
        pages = self.window.findChild(QStackedWidget, "propertyPages")
        self.assertIsNotNone(tabs)
        self.assertIsNotNone(pages)
        self.assertEqual(tabs.count(), 3)
        self.assertEqual(pages.count(), 3)
        for index, word in enumerate(("géométrie", "maillage", "export")):
            self.assertIn(word, tabs.tabText(index).lower())
            tabs.setCurrentIndex(index)
            self.application.processEvents()
            self.assertEqual(pages.currentIndex(), index)
        self.assertEqual(self.session.calls, [])

    def test_units_have_distinct_import_and_export_controls(self):
        from PySide6.QtWidgets import QComboBox
        import_units = self.window.findChild(QComboBox, "inputUnits")
        export_units = self.window.findChild(QComboBox, "exportUnits")
        self.assertIsNotNone(import_units)
        self.assertIsNotNone(export_units)
        self.assertIsNot(import_units, export_units)
        self.assertGreaterEqual(import_units.count(), 4)
        self.assertGreaterEqual(export_units.count(), 4)
        project = self.window.project_from_form()
        self.assertIn(project["units"]["input"], ("m", "mm", "cm", "in"))
        self.assertEqual(project["units"]["internal"], "m")

    def test_native_project_is_loaded_without_loss_of_holes_or_units(self):
        from tests.test_native import plate
        project = plate()
        self.window.load_project(project)
        loaded = self.window.project_from_form()
        self.assertEqual(loaded["geometry"]["kind"], "plate")
        self.assertEqual(loaded["geometry"]["holes"], project["geometry"]["holes"])
        self.assertEqual(loaded["units"], project["units"])
        self.assertEqual(loaded["mesh"]["order"], project["mesh"]["order"])
        self.assertEqual(self.session.calls, [])

    def _receive_result(self, operation):
        self.window._pending_project = deepcopy(self.window.project_from_form())
        self.window._pending_operation = operation
        self.window._calculation_succeeded({
            "directory": self.temp.name,
            "report": {"operation": operation, "display_only": operation == "inspect",
                       "nodes": 4, "elements": 2, "entity_catalog": []},
            "preview": {},
        })

    def test_display_and_stale_results_are_blocked_before_the_export_dialog(self):
        with patch("atelier.desktop.QFileDialog.getExistingDirectory", return_value="") as dialog:
            self._receive_result("inspect")
            self.window.export_mesh()
            dialog.assert_not_called()
            self._receive_result("mesh")
            self.window.mesh_size.setValue(self.window.mesh_size.value() * 2)
            self.window.export_mesh()
            dialog.assert_not_called()
        self.assertEqual(self.session.calls, [])

    def test_failed_inspection_keeps_the_previous_analysis_result_classification(self):
        self._receive_result("mesh")
        self.window._pending_operation = "inspect"
        self.window._calculation_failed("Examen refusé pour le test")
        with patch("atelier.desktop.QFileDialog.getExistingDirectory", return_value="") as dialog:
            self.window.export_mesh()
            dialog.assert_called_once()
        self.assertEqual(self.session.calls, [])

    def test_training_result_preserves_the_mesh_and_disables_an_unproven_guide(self):
        self._receive_result("mesh")
        original_result = self.window.result
        original_project = deepcopy(self.window.result_project)
        self.window.use_guide.setChecked(True)
        self.window._training_succeeded({"kind": "training", "report": {
            "training_cases": 1, "validation_cases": 1,
            "validation_guide_reward": .5, "validation_fixed_reward": .6,
            "improvement": -.1, "deploy_enabled": False,
        }})
        self.assertIs(self.window.result, original_result)
        self.assertEqual(self.window.result_project, original_project)
        self.assertFalse(self.window.use_guide.isChecked())
        self.assertEqual(self.session.calls, [])


if __name__ == "__main__":
    unittest.main()
