"""UI contracts for persistent learning and spatial quality; no numerical fallback."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class RecordingSession:
    def __init__(self):
        self.calls = []

    def learn(self, project, **kwargs):
        self.calls.append((deepcopy(project), kwargs))
        return {"report": {"operation": "learn"}, "preview": {}, "directory": "unused"}

    def memory_info(self):
        return {"contexts": 2, "observations": 17, "models": 3}


class DesktopExperience(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("PySide6") is None:
            raise unittest.SkipTest("PySide6 absent")
        from PySide6.QtWidgets import QApplication
        from atelier.desktop import AtelierWindow
        cls.application = QApplication.instance() or QApplication([])
        cls.window_type = AtelierWindow

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = RecordingSession()
        self.window = self.window_type(workspace=self.temp.name, session=self.session)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.window.close)

    def _result(self, project=None, quality=None, experience=None):
        self.window._pending_project = deepcopy(project or self.window.project_from_form())
        self.window._pending_operation = "mesh"
        report = {"operation": "mesh", "nodes": 8, "elements": 3,
                  "quality": quality or {"minimum": .6, "accepted": True}, "entity_catalog": []}
        if experience is not None:
            report["experience"] = experience
        result = {"directory": self.temp.name, "report": report, "preview": {}}
        self.window._calculation_succeeded(result)
        return result

    def test_zones_from_mixed_selection_round_trip_without_losing_metadata(self):
        from PySide6.QtCore import Qt
        self.window.zone_name.setText("Raccord principal")
        self.window.zone_kind.setCurrentIndex(self.window.zone_kind.findData("interface"))
        self.window.zone_minimum.setValue(.42)
        self.window.zone_buffer.setValue(.15)
        with patch.object(self.window, "selected_entities", return_value=[(2, 4), (2, 8), (3, 2)]):
            self.window.add_critical_zone()
        project = self.window.project_from_form()
        self.assertEqual(len(project["critical_zones"]), 2)
        self.assertEqual(project["critical_zones"][0]["tags"], [4, 8])
        self.assertEqual(project["critical_zones"][1]["dimension"], 3)
        self.assertEqual(project["critical_zones"][0]["minimum_quality"], .42)
        project["critical_zones"][0]["description"] = "Metadata retained"
        self.window.load_project(project)
        self.assertEqual(self.window.project_from_form()["critical_zones"], project["critical_zones"])
        self.assertEqual(self.window.zone_table.item(0, 0).data(Qt.UserRole)["buffer"], .15)
        self.assertEqual(self.session.calls, [])

    def test_quality_limits_and_search_budget_are_saved_in_project(self):
        self.window.limit_poor_count.setChecked(True)
        self.window.max_poor_count.setValue(3)
        self.window.limit_poor_fraction.setChecked(True)
        self.window.max_poor_fraction.setValue(2.5)
        self.window.learning_trials.setValue(7)
        self.window.use_memory.setChecked(False)
        saved = self.window.project_from_form()
        self.assertEqual(saved["quality_policy"]["max_poor_elements"], 3)
        self.assertEqual(saved["quality_policy"]["max_poor_fraction"], .025)
        self.window.load_project(saved)
        self.assertEqual(self.window.project_from_form()["quality_policy"], saved["quality_policy"])
        self.assertEqual(self.window.project_from_form()["learning"], saved["learning"])

    def test_learn_uses_open_import_source_and_runs_as_a_mesh_operation(self):
        project = self.window.project_from_form()
        project["geometry"] = {"kind": "import"}
        project["learning"]["max_seconds"] = 37
        source = Path(self.temp.name) / "piece.step"
        source.write_text("source passed unchanged to native session")
        self.window.load_project(project, source)
        self.window.learning_trials.setValue(9)
        self.window.learning_epochs.setValue(30)
        self.window.use_memory.setChecked(False)
        with patch.object(self.window, "_launch") as launch:
            self.window.learn_current_piece()
            launch.assert_called_once()
            self.assertFalse(launch.call_args.kwargs.get("training", False))
            launch.call_args.args[0]()
        supplied_project, kwargs = self.session.calls[0]
        self.assertEqual(supplied_project["geometry"]["kind"], "import")
        self.assertEqual(kwargs, {"source": source, "max_trials": 9, "epochs": 30, "use_memory": False, "max_seconds": 37})
        self.assertEqual(self.window._pending_operation, "learn")

    def test_chosen_recipe_updates_form_and_export_identity(self):
        chosen = self.window.project_from_form()
        chosen["mesh"]["size"] = .31
        chosen["mesh"]["algorithm"] = "delaunay"
        chosen["local_sizes"] = [{"dimension": 2, "tag": 1, "size": .2, "source": "critical_zone"}]
        chosen["refinement_regions"] = [{"dimension": 1, "tags": [4], "size": .2, "distance": .3}]
        self._result(experience={"chosen_project": chosen, "executed_trials": 2, "memory_reused": True,
                                 "trials": [{"index": 1, "algorithm": "delaunay", "size": .31,
                                             "quality": {"minimum": .6}, "success": True, "accepted": True}]})
        self.assertAlmostEqual(self.window.mesh_size.value(), .31)
        self.assertEqual(self.window.algorithm.currentData(), "delaunay")
        self.assertEqual(self.window.result_project, chosen)
        self.assertEqual(self.window.project_from_form()["local_sizes"], chosen["local_sizes"])
        self.assertEqual(self.window.project_from_form()["refinement_regions"], chosen["refinement_regions"])
        self.assertTrue(self.window._result_matches_current_project())
        self.assertEqual(self.window.experience_history.rowCount(), 1)
        self.assertIn("réévaluée", self.window.experience_summary.text())
        self.window.learning_trials.setValue(5)
        self.assertTrue(self.window._result_matches_current_project())
        self.window.minimum_quality.setValue(.7)
        self.assertFalse(self.window._result_matches_current_project())

    def test_quality_failure_is_visible_and_blocks_export(self):
        self._result(quality={"minimum": .08, "poor_count": 2, "poor_fraction": .2, "accepted": False,
                              "critical_zones": [{"name": "Contact", "elements": 2, "minimum": .08, "below_minimum": 1}]})
        self.assertIn("Critères non respectés", self.window.quality_label.text())
        self.assertEqual(self.window.zone_quality_table.rowCount(), 1)
        with patch("atelier.desktop.QFileDialog.getExistingDirectory") as dialog:
            self.window.export_mesh()
            dialog.assert_not_called()

    def test_memory_read_keeps_existing_mesh(self):
        result = self._result()
        with patch.object(self.window, "_launch") as launch:
            self.window.show_memory()
            launch.assert_called_once()
            self.assertTrue(launch.call_args.kwargs["memory"])
            self.window._memory_succeeded(launch.call_args.args[0]())
        self.assertIs(self.window.result, result)
        self.assertIn("17 essais", self.window.experience_summary.text())


if __name__ == "__main__":
    unittest.main()
