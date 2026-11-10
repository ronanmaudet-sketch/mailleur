"""Real bounded checks for the native corpus pipeline.

Planning one million cases is tested separately from computing a small batch.
Python only prepares requests and inspects artifacts; it does not create CAD,
mesh, quality labels or model weights in these integration checks.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class PythonBoundary(unittest.TestCase):
    def test_product_cannot_import_python_mesher_outside_export_adapters(self):
        allowed = {ROOT / "atelier/formats.py", ROOT / "atelier/convert_worker.py"}
        forbidden = []
        for directory in (ROOT / "atelier", ROOT / "mailleur"):
            for path in directory.rglob("*.py"):
                if path in allowed:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
                    if isinstance(node, ast.ImportFrom) and node.module:
                        names.append(node.module)
                    if any(name == "gmsh" or name.startswith("gmsh.") for name in names):
                        forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(forbidden, [], "Numerical CAD/meshing must use the Rust process")


class CorpusNativeIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from atelier.native import NativeSession, core_path, gmsh_library
            core_path()
            gmsh_library()
        except (ImportError, OSError) as error:
            raise unittest.SkipTest(f"Dépendance native absente : {error}") from error
        if not all(hasattr(NativeSession, name) for name in
                   ("corpus_plan", "corpus_status", "corpus_run", "corpus_train")):
            raise unittest.SkipTest("Installer le pont natif de corpus rc4")
        cls.session_type = NativeSession

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / "corpus"
        self.session = self.session_type(self.root / "workspace", timeout=45)
        self.addCleanup(self.session.cancel)
        self._request_index = 0

    def plan(self, *, directory=None, structured=24, random=24, seed=42,
             strategies=1, shard_size=1000):
        return self.session.corpus_plan(corpus_dir=directory or self.corpus,
                                       structured=structured, random=random, seed=seed,
                                       strategies=strategies, shard_size=shard_size)

    def preview(self, *, directory=None, start=0, limit=24):
        return self.session.corpus_preview(corpus_dir=directory or self.corpus,
                                           start_index=start, limit=limit)["cases"]

    def records(self):
        rows = []
        for path in sorted((self.corpus / "shards").glob("cases-*.jsonl")):
            rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
        return rows

    def test_million_case_plan_is_metadata_and_does_not_generate_meshes(self):
        with patch("atelier.backend.run_project", side_effect=AssertionError("Python meshing forbidden")):
            report = self.session.corpus_plan(corpus_dir=self.corpus)
        self.assertEqual(report["structured"], 250000)
        self.assertEqual(report["random"], 750000)
        self.assertEqual(report["planned_total"], 1000000)
        self.assertEqual(report["completed_cases"], 0)
        self.assertEqual(report["backend_evaluations"], 0)
        files = [path for path in self.corpus.rglob("*") if path.is_file()]
        self.assertLessEqual(len(files), 10)
        self.assertLess(sum(path.stat().st_size for path in files), 100000)
        self.assertFalse(list(self.corpus.rglob("*.msh")))

    def test_generation_is_reproducible_and_random_seed_changes_cases(self):
        self.plan(structured=24, random=24, seed=42)
        first = self.preview(start=24, limit=8)
        self.assertEqual(first, self.preview(start=24, limit=8))
        other = self.root / "other-corpus"
        self.plan(directory=other, structured=24, random=24, seed=43)
        changed = self.preview(directory=other, start=24, limit=8)
        self.assertNotEqual([row["project"] for row in first], [row["project"] for row in changed])
        self.assertEqual(len({row["case_id"] for row in first}), len(first))
        self.assertTrue(all(row["origin"] == "random" for row in first))

    def test_incompatible_replanning_cannot_mix_or_overwrite_an_existing_corpus(self):
        self.plan(structured=24, random=0, seed=42)
        before = {path.relative_to(self.corpus): path.read_bytes()
                  for path in self.corpus.rglob("*.json")}
        with self.assertRaisesRegex(RuntimeError, "(?i)(incompatible|différent|existe|plan)"):
            self.plan(structured=24, random=0, seed=43)
        after = {path.relative_to(self.corpus): path.read_bytes()
                 for path in self.corpus.rglob("*.json")}
        self.assertEqual(before, after)

    def test_geometry_families_do_not_cross_training_validation_or_test(self):
        self.plan(structured=48, random=48)
        families = {}
        observed_splits = set()
        observed_modes = set()
        for start in range(0, 96, 24):
            for row in self.preview(start=start, limit=24):
                family, split = row["family"], row["split"]
                self.assertIn(split, ("train", "validation", "test"))
                self.assertEqual(families.setdefault(family, split), split)
                observed_splits.add(split)
                observed_modes.add(row["project"]["mode"])
        self.assertEqual(observed_splits, {"train", "validation", "test"})
        self.assertEqual(observed_modes, {"planar", "surface", "volume"})
        self.assertGreaterEqual(len(families), 15)

    def test_bounded_real_batch_resumes_without_duplicate_cases(self):
        self.plan(structured=24, random=0)
        with patch("atelier.backend.run_project", side_effect=AssertionError("Python meshing forbidden")), \
             patch("atelier.advisor.train_advisor", side_effect=AssertionError("Python training forbidden")):
            first = self.session.corpus_run(corpus_dir=self.corpus, limit=2, max_seconds=30)
            other_session = self.session_type(self.session.workspace, timeout=45)
            self.addCleanup(other_session.cancel)
            second = other_session.corpus_run(corpus_dir=self.corpus, limit=2, max_seconds=30)
        rows = self.records()
        self.assertEqual(first["processed_cases"], 2)
        self.assertEqual(second["processed_cases"], 2)
        self.assertEqual(second["completed_cases"], 4)
        self.assertEqual([row["index"] for row in rows], [0, 1, 2, 3])
        self.assertEqual(len({row["case_id"] for row in rows}), 4)
        self.assertTrue(any(action["success"] for row in rows for action in row["actions"]))
        for row in rows:
            for action in row["actions"]:
                self.assertEqual(len(action["features"]), 128)
                self.assertGreaterEqual(action["target"], 0)
                self.assertLessEqual(action["target"], 1)
        self.assertFalse(list(self.corpus.rglob("mesh.msh")), "Raw mesh retention must be explicit")

    def test_partial_tail_after_interruption_is_recovered_before_next_case(self):
        self.plan(structured=24, random=0)
        self.session.corpus_run(corpus_dir=self.corpus, limit=1, max_seconds=30)
        shard = next((self.corpus / "shards").glob("cases-*.jsonl"))
        with shard.open("ab") as stream:
            stream.write(b'{"interrupted": "\xc3')
        result = self.session.corpus_run(corpus_dir=self.corpus, limit=1, max_seconds=30)
        self.assertEqual(result["completed_cases"], 2)
        self.assertEqual([row["index"] for row in self.records()], [0, 1])

    def test_cancelled_batch_releases_lock_for_later_run(self):
        self.plan(structured=24, random=0)
        self.session.timeout = 0
        with self.assertRaisesRegex(RuntimeError, "(?i)(délai|timeout)"):
            self.session.corpus_run(corpus_dir=self.corpus, limit=24, max_seconds=30)
        self.assertIsNone(self.session._process)
        self.session.timeout = 45
        status = self.session.corpus_status(corpus_dir=self.corpus)
        before = status["completed_cases"]
        result = self.session.corpus_run(corpus_dir=self.corpus, limit=1, max_seconds=30)
        self.assertEqual(result["completed_cases"], before + 1)

    def test_invalid_geometry_has_an_explicit_failure_label_not_an_accepted_mesh(self):
        self.plan(structured=18, random=0)
        self.session.corpus_run(corpus_dir=self.corpus, limit=18, max_seconds=30)
        rows = self.records()
        invalid = next(row for row in rows if row["index"] == 17)
        self.assertTrue(invalid["expected_invalid"])
        self.assertTrue(invalid["actions"])
        for action in invalid["actions"]:
            self.assertFalse(action["accepted"])
            self.assertFalse(action["success"])
            self.assertEqual(action["target"], 0)
            self.assertEqual(action["failure_kind"], "invalid_geometry")

    def test_legacy_observations_are_reencoded_when_geometry_information_is_available(self):
        from tests.test_continuous import plate
        project = plate()
        first = self.session.learn(project, max_trials=1, epochs=3)
        memory_path = self.session.workspace / "experience/memory.json"
        memory = json.loads(memory_path.read_text())
        old_context = next(row for row in memory["contexts"]
                           if row["context_id"] == first["report"]["experience"]["context_id"])
        observation = old_context["observations"][0]
        observation["features"] = observation["features"][:16]
        observation.pop("legacy_features", None)
        for entity in old_context.get("last_geometry_report", {}).get("entity_catalog", []):
            entity.pop("boundaries", None)
        memory_path.write_text(json.dumps(memory))
        (self.session.workspace / "experience/model.json").unlink()
        changed = plate()
        changed["geometry"]["height"] = 12
        second = self.session.learn(changed, max_trials=1, epochs=3)
        migrated = second["report"]["experience"]["representation_migration"]
        self.assertGreaterEqual(migrated["legacy_examples_reencoded"], 1)
        updated = json.loads(memory_path.read_text())
        previous = next(row for row in updated["contexts"] if row["context_id"] == old_context["context_id"])
        row = previous["observations"][0]
        self.assertEqual(len(row["features"]), 128)
        self.assertEqual(len(row["legacy_features"]), 16)


class CorpusStreamingIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CorpusNativeIntegration.setUpClass.__func__(cls)

    def setUp(self):
        CorpusNativeIntegration.setUp(self)

    def fixture(self, directory, *, training=32, validation=8, testing=8, test_target=.4):
        shards = directory / "shards"
        shards.mkdir(parents=True)
        with (shards / "cases-000000.jsonl").open("w", encoding="utf-8") as stream:
            for index in range(training + validation + testing):
                split = "train" if index < training else "validation" if index < training + validation else "test"
                targets = (.2, .9) if split != "test" else (test_target, test_target)
                record = {"schema": "atelier.corpus.case.v1", "index": index,
                          "case_id": f"fixture-{index}", "geometry_id": f"geometry-{index}",
                          "family": "fixture-" + split, "split": split,
                          "feature_schema": "test.corpus.two_inputs.v1",
                          "actions": [{"strategy_id": name, "features": features, "target": target,
                                       "success": True, "accepted": True}
                                      for name, features, target in zip(
                                          ("automatic_base", "fine"), ([0., 1.], [1., 0.]), targets)]}
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")

    def train_direct(self, directory, **training):
        self._request_index += 1
        output = self.root / f"training-{self._request_index}"
        output.mkdir()
        config = {"epochs": 3, "batch_size": 8, "max_records": 100000,
                  "max_seconds": 30, "seed": 42, **training}
        self.session._call({"operation": "corpus_train", "corpus_dir": str(directory),
                            "output_dir": str(output), "training": config},
                           output, needs_gmsh=False)
        return json.loads((output / "report.json").read_text())

    def test_streaming_training_is_not_limited_to_the_interactive_memory_capacity(self):
        self.fixture(self.corpus, training=33010, validation=2, testing=2)
        report = self.train_direct(self.corpus, epochs=1, batch_size=64)
        self.assertTrue(report["streaming"])
        self.assertEqual(report["computation_backend"], "rust")
        self.assertEqual(report["training_cases"], 33010)
        self.assertEqual(report["training_examples"], 66020)
        self.assertEqual(report["validation_cases"], 2)
        self.assertEqual(report["test_cases"], 2)
        self.assertLessEqual(report["max_batch_observed"], 64)
        self.assertGreater(report["batches_completed"], 1000)
        self.assertFalse(report["test_used_for_gradients"])
        self.assertFalse(report["test_used_for_weight_selection"])
        self.assertTrue(Path(report["model_path"]).is_file())

    def test_modifying_test_targets_cannot_change_model_weights_or_activation(self):
        first_dir, second_dir = self.root / "first", self.root / "second"
        self.fixture(first_dir, test_target=.1)
        self.fixture(second_dir, test_target=.9)
        first = self.train_direct(first_dir)
        second = self.train_direct(second_dir)
        first_model = json.loads(Path(first["model_path"]).read_text())
        second_model = json.loads(Path(second["model_path"]).read_text())
        self.assertEqual(first_model["network"], second_model["network"])
        self.assertEqual(first["deploy_enabled"], second["deploy_enabled"])
        self.assertNotEqual(first["test"]["oracle_reward"], second["test"]["oracle_reward"])

    def test_partial_minibatch_checkpoint_resumes_the_same_training(self):
        self.fixture(self.corpus)
        first = self.train_direct(self.corpus, batch_size=4, max_batches=1)
        self.assertTrue(first["stopped_by_budget"])
        self.assertEqual(first["epochs_completed"], 0)
        checkpoint = json.loads(Path(first["checkpoint_path"]).read_text())
        self.assertFalse(checkpoint["complete"])
        second = self.train_direct(self.corpus, batch_size=4)
        self.assertTrue(second["resumed"])
        self.assertEqual(second["epochs_completed"], 3)
        self.assertTrue(json.loads(Path(second["checkpoint_path"]).read_text())["complete"])

    def test_family_leakage_is_rejected_before_training(self):
        self.fixture(self.corpus)
        path = self.corpus / "shards/cases-000000.jsonl"
        path.write_text(path.read_text().replace('"family":"fixture-validation"', '"family":"fixture-train"'))
        with self.assertRaisesRegex(RuntimeError, "(?i)(fuite|famille|partition)"):
            self.train_direct(self.corpus)


if __name__ == "__main__":
    unittest.main()
