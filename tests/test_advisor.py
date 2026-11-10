"""Transport checks for the Rust-only legacy advisor facade."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class AdvisorTransport(unittest.TestCase):
    def test_features_cases_and_inference_are_routed_to_rust(self):
        from atelier.advisor import choose_algorithm, default_cases, extract_features
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / "guide.json"; model.write_text('{"schema":"model"}')
            with patch("atelier.advisor._query", side_effect=[{"features": [.1] * 8}, {"cases": [{"family": "rust"}]}, {"algorithm": "frontal", "use_model": False}]) as query:
                self.assertEqual(extract_features({"geometry": {}}), [.1] * 8)
                self.assertEqual(default_cases(), [{"family": "rust"}])
                self.assertEqual(choose_algorithm({"mesh": {}}, model)["algorithm"], "frontal")
            self.assertEqual([c.args[0] for c in query.call_args_list], ["advisor_features", "advisor_default_cases", "advisor_choose"])
            self.assertEqual(query.call_args.kwargs["model"], {"schema": "model"})

    def test_training_only_calls_native_session_and_copies_results(self):
        from atelier.advisor import train_advisor
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); native = root / "native"; native.mkdir()
            for name in ("guide.json", "report.json"): (native / name).write_text('{"native":true}')
            report = {"training_cases": 2, "native_kernel": {"language": "rust"}}
            with patch("atelier.native.NativeSession") as session:
                session.return_value.train.return_value = {"directory": str(native), "report": report}
                self.assertIs(train_advisor(root / "result", [{"project": {}}], epochs=3, seed=9), report)
            session.return_value.train.assert_called_once_with(cases=[{"project": {}}], epochs=3, seed=9)
            self.assertEqual(json.loads((root / "result/guide.json").read_text()), {"native": True})

    def test_python_fit_and_forward_are_not_available(self):
        from atelier.advisor import _fit, _forward
        for function in (_fit, _forward):
            with self.assertRaisesRegex(RuntimeError, "retirée"):
                function([], 1, 42)

    def test_real_native_feature_queries_need_no_python_gmsh(self):
        from atelier.advisor import default_cases, extract_features
        with patch.dict("sys.modules", {"gmsh": None}):
            cases = default_cases()
            self.assertEqual(len(cases), 16)
            features = extract_features(cases[0]["project"])
        self.assertEqual(len(features), 8)
        self.assertTrue(all(0 <= value <= 1 for value in features))


if __name__ == "__main__":
    unittest.main()
