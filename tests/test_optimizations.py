"""Public-path regressions for the rc5 performance changes.

These checks protect durable corpus data while exercising the optimized
checkpoint path. They do not infer numerical speed or mesh quality gains.
"""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest


class CorpusCheckpointIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from atelier.native import NativeSession, core_path, gmsh_library
            core_path()
            gmsh_library()
        except (ImportError, OSError) as error:
            raise unittest.SkipTest(f"Dépendance native absente : {error}") from error
        cls.session_type = NativeSession

    def test_same_length_edit_in_durable_prefix_is_refused_without_deleting_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            corpus = root / "corpus"
            session = self.session_type(root / "workspace", timeout=40)
            self.addCleanup(session.cancel)
            session.corpus_plan(corpus, structured=4, random=0, strategies=1)
            result = session.corpus_run(corpus, limit=2, max_seconds=30)
            self.assertEqual(result["completed_cases"], 2)

            shard = next((corpus / "shards").glob("cases-*.jsonl"))
            original = shard.read_bytes()
            first_line = original.split(b"\n", 1)[0]
            match = re.search(rb'"case_id"\s*:\s*"([0-9a-f])', first_line)
            self.assertIsNotNone(match)
            position = match.start(1)
            changed = bytearray(original)
            changed[position] = ord("0") if changed[position] != ord("0") else ord("1")
            changed = bytes(changed)
            self.assertEqual(len(changed), len(original))
            checkpoint_before = (corpus / "checkpoint.json").read_bytes()
            shard.write_bytes(changed)

            with self.assertRaisesRegex(RuntimeError, "(?i)(préfixe|empreinte|intégrité|modifi|corromp)"):
                session.corpus_run(corpus, limit=1, max_seconds=30)

            self.assertEqual(shard.read_bytes(), changed)
            self.assertEqual((corpus / "checkpoint.json").read_bytes(), checkpoint_before)


if __name__ == "__main__":
    unittest.main()
