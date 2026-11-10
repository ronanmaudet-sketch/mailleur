"""Desktop boundary: corpus calculations run through the native session only."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class CorpusSession:
    def __init__(self):
        self.calls = []

    def _record(self, name, options):
        self.calls.append((name, options, threading.get_ident()))
        return {"operation": name, "planned_total": 1000000, "completed_cases": 20,
                "backend_evaluations": 80, "accepted_actions": 75, "failed_actions": 5,
                "split_counts": {"train": 12, "validation": 4, "test": 4}}

    def corpus_plan(self, **kwargs):
        result = self._record("corpus_plan", kwargs)
        result.update(completed_cases=0, backend_evaluations=0, accepted_actions=0, failed_actions=0,
                      split_counts={"train": 0, "validation": 0, "test": 0})
        return result

    def corpus_run(self, **kwargs):
        return self._record("corpus_run", kwargs)

    def corpus_status(self, **kwargs):
        return self._record("corpus_status", kwargs)

    def corpus_train(self, **kwargs):
        self._record("corpus_train", kwargs)
        return {"schema": "atelier.corpus.training_report.v1", "training_cases": 12,
                "validation_cases": 4, "test_cases": 4, "epochs_completed": 2,
                "epochs_requested": 3, "batches_completed": 3, "warm_started": True,
                "stopped_by_budget": True, "deploy_enabled": False, "test": {"cases": 4}}

    def cancel(self):
        self.calls.append(("cancel", {}, threading.get_ident()))


class DesktopCorpus(unittest.TestCase):
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
        self.session = CorpusSession()
        self.window = self.window_type(workspace=self.temp.name, session=self.session)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.close_window)

    def close_window(self):
        if self.window.calculation_thread is not None:
            self.window.calculation_thread.wait(2000)
            self.application.processEvents()
        self.window.close()

    def test_plan_controls_are_inert_and_collapsed_on_open(self):
        from PySide6.QtWidgets import QToolButton
        self.assertEqual(self.window.corpus_structured.value(), 250000)
        self.assertEqual(self.window.corpus_random.value(), 750000)
        self.assertTrue(self.window.corpus_body.isHidden())
        toggle = self.window.findChild(QToolButton, "corpusToggle")
        toggle.setChecked(True)
        self.assertFalse(self.window.corpus_body.isHidden())
        self.assertEqual(self.session.calls, [])
        self.assertIn("objectif", self.window.corpus_summary.text())

    def test_plan_delegates_without_launching_meshing(self):
        with patch.object(self.window, "_launch") as launch:
            self.window.plan_corpus()
            self.assertTrue(launch.call_args.kwargs["corpus"])
            result = launch.call_args.args[0]()
            self.window._corpus_succeeded(result)
        name, options, _ = self.session.calls[0]
        self.assertEqual(name, "corpus_plan")
        self.assertEqual(options, {"corpus_dir": Path(self.temp.name) / "corpus", "structured": 250000,
                                  "random": 750000, "seed": 42, "strategies": 4, "shard_size": 1000})
        self.assertEqual(len(self.session.calls), 1)
        self.assertIn("Traitées : 0", self.window.corpus_summary.text())
        self.assertIn("ne démarre", self.window.corpus_summary.text())

    def test_run_and_training_forward_the_requested_budgets(self):
        self.window.corpus_directory.setText(str(Path(self.temp.name) / "shared"))
        self.window.corpus_batch_cases.setValue(7)
        self.window.corpus_seconds.setValue(37)
        self.window.corpus_keep_meshes.setChecked(True)
        self.window.corpus_epochs.setValue(2)
        self.window.corpus_training_batch.setValue(64)
        self.window.corpus_training_records.setValue(1234)
        with patch.object(self.window, "_launch") as launch:
            self.window.run_corpus_batch()
            launch.call_args.args[0]()
            self.window.train_corpus_batches()
            launch.call_args.args[0]()
        run, training = self.session.calls
        self.assertEqual(run[:2], ("corpus_run", {"corpus_dir": Path(self.temp.name) / "shared",
                              "limit": 7, "max_seconds": 37, "retain_meshes": True}))
        self.assertEqual(training[:2], ("corpus_train", {"corpus_dir": Path(self.temp.name) / "shared",
                                   "epochs": 2, "batch_size": 64, "max_records": 1234, "max_seconds": 37}))

    def test_corpus_uses_worker_thread_and_keeps_the_displayed_mesh(self):
        from PySide6.QtCore import QEventLoop, QTimer
        original = {"directory": self.temp.name, "report": {"quality": {"accepted": True}}}
        self.window.result = original
        original_project = deepcopy(self.window.project_from_form())
        self.window.result_project = original_project
        main_thread = threading.get_ident()
        self.window.show_corpus_status()
        loop = QEventLoop()
        self.window.calculation_thread.finished.connect(loop.quit)
        QTimer.singleShot(2000, loop.quit)
        loop.exec()
        self.application.processEvents()
        self.assertFalse(self.window.calculation_thread.isRunning())
        self.assertNotEqual(self.session.calls[0][2], main_thread)
        self.assertIs(self.window.result, original)
        self.assertEqual(self.window.result_project, original_project)
        self.assertIn("Traitées : 20", self.window.corpus_summary.text())
        self.assertEqual(self.window.corpus_partitions.item(0, 1).text(), "12")
        self.assertEqual(self.window.corpus_partitions.item(1, 1).text(), "4")
        self.assertEqual(self.window.corpus_partitions.item(2, 0).text(), "Test final")

    def test_training_keeps_test_separate_and_reports_an_inactive_model(self):
        self.window._pending_corpus_operation = "corpus_train"
        self.window._corpus_succeeded(self.session.corpus_train())
        summary = self.window.corpus_summary.text()
        self.assertIn("Classement du réseau inactif", summary)
        self.assertIn("Test final distinct : 4", summary)
        self.assertIn("Budget atteint", summary)
        self.assertIn("point de reprise", summary)
        self.assertEqual(self.window.corpus_partitions.rowCount(), 3)

    def test_directory_selection_is_file_io_only_and_empty_path_is_rejected(self):
        with patch("atelier.desktop.QFileDialog.getExistingDirectory", return_value=self.temp.name):
            self.window.choose_corpus_directory()
        self.assertEqual(self.window.corpus_directory.text(), self.temp.name)
        self.assertEqual(self.session.calls, [])
        self.window.corpus_directory.setText(" ")
        self.window.plan_corpus()
        self.assertEqual(self.session.calls, [])
        self.assertIn("Choisissez un dossier", self.window.statusBar().currentMessage())

    def test_old_network_migration_is_explicit_without_discarding_recipes(self):
        self.window._update_experience({"executed_trials": 1,
            "learning": {"deploy_enabled": False, "previous_model_rejected": "schema v1"},
            "representation_migration": {"message": "Anciennes observations conservées ; nouvel encodage spatial."}})
        summary = self.window.experience_summary.text()
        self.assertIn("incompatible", summary)
        self.assertIn("recettes restent conservées", summary)
        self.assertIn("nouvel encodage spatial", summary)


if __name__ == "__main__":
    unittest.main()
