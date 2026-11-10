"""Contrôles du paquet de poids : contenu borné et validation des dimensions."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("share_model", ROOT / "scripts/share_model.py")
sharing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sharing)
MODELS = {
    "historique": ROOT / "verification/native_release/runs/native/07bab630e29344ddbfb69ea30c947b7d/guide.json",
    "experience": ROOT / "verification/continuous_learning/continuous_runs/20261104T091837738488/experience/model.json",
    "corpus": ROOT / "verification/corpus/pilot/20261104T091842739497/models/current.json",
}


class ShareModelTests(unittest.TestCase):
    def model(self, kind):
        return json.loads(MODELS[kind].read_text())

    def test_three_shipped_models_export_with_checksums_and_without_sibling_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "memory.json").write_text('{"private":"never include"}')
            (root / "secret.step").write_text("never include")
            for kind, model in MODELS.items():
                output = root / (kind + ".zip")
                result = sharing.share_model(kind, model, output)
                self.assertFalse(result["deploy_enabled"])
                with zipfile.ZipFile(output) as archive:
                    self.assertEqual(set(archive.namelist()), {"model.json", "manifest.json", "README.md"})
                    manifest = json.loads(archive.read("manifest.json"))
                    self.assertEqual(manifest["source_sha256"], hashlib.sha256(model.read_bytes()).hexdigest())
                    self.assertEqual(manifest["destination_relative_to_workspace"], sharing.DESTINATIONS[kind])
                    self.assertNotIn(str(ROOT), archive.read("manifest.json").decode())
                    for name, info in manifest["files"].items():
                        data = archive.read(name)
                        self.assertEqual(info["bytes"], len(data))
                        self.assertEqual(info["sha256"], hashlib.sha256(data).hexdigest())
                    packed = json.loads(archive.read("model.json"))
                    original = self.model(kind)
                    self.assertEqual(packed["deploy_enabled"], original["deploy_enabled"])
                    if kind == "historique":
                        self.assertIn("training_configs", original)
                        self.assertNotIn("training_configs", packed)
                        original.pop("training_configs")
                        self.assertEqual(manifest["omitted_model_fields"], ["training_configs"])
                    self.assertEqual(packed, original)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "existing.zip"
            output.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                sharing.share_model("corpus", MODELS["corpus"], output)
            self.assertEqual(output.read_bytes(), b"keep")

    def test_symlink_output_is_not_followed_and_input_name_does_not_control_zip_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "keep.txt"
            target.write_text("keep")
            output = root / "linked.zip"
            output.symlink_to(target)
            with self.assertRaises(FileExistsError):
                sharing.share_model("corpus", MODELS["corpus"], output)
            self.assertEqual(target.read_text(), "keep")
            model = root / "unusual name .. model.json"
            model.write_bytes(MODELS["corpus"].read_bytes())
            packed = root / "safe.zip"
            sharing.share_model("corpus", model, packed)
            with zipfile.ZipFile(packed) as archive:
                self.assertTrue(all("/" not in name and ".." not in name for name in archive.namelist()))

    def test_report_only_attached_when_selected_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "selected.json"
            report.write_text('{"metric":0.5}')
            output = Path(folder) / "model.zip"
            sharing.share_model("experience", MODELS["experience"], output, report)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(json.loads(archive.read("report.json")), {"metric": 0.5})
                self.assertIn("report.json", json.loads(archive.read("manifest.json"))["files"])

    def test_wrong_role_checkpoint_old_schema_and_incomplete_models_are_rejected(self):
        bad_cases = [("corpus", self.model("experience")), ("experience", {"deploy_enabled": False}),
                     ("corpus", {"schema": "atelier.corpus.training_checkpoint.v1", "deploy_enabled": False})]
        old = self.model("experience")
        old["schema"] = "atelier.experience_model.v1"
        old["input_count"] = 16
        bad_cases.append(("experience", old))
        for kind, model in bad_cases:
            with self.subTest(kind=kind, schema=model.get("schema")):
                with self.assertRaises(ValueError):
                    sharing.validate_model(kind, model)

    def test_invalid_weights_dimensions_counts_and_nonfinite_values_are_rejected(self):
        base = self.model("experience")
        mutations = [
            lambda m: m["network"]["input_weights"][0].pop(),
            lambda m: m["network"].__setitem__("output_bias", True),
            lambda m: m["network"].__setitem__("output_bias", float("inf")),
            lambda m: m["network"].__setitem__("output_bias", 10**1000),
            lambda m: m["network"].__setitem__("output_bias", 1001),
            lambda m: m.__setitem__("parameter_count", 217),
            lambda m: m.__setitem__("input_count", True),
            lambda m: m.__setitem__("validation_groups", m["training_groups"][:1]),
        ]
        for i, mutate in enumerate(mutations):
            model = copy.deepcopy(base)
            mutate(model)
            with self.subTest(mutation=i):
                with self.assertRaises(ValueError):
                    sharing.validate_model("experience", model)

    def test_forced_activation_without_recorded_gain_is_rejected(self):
        for kind in MODELS:
            model = self.model(kind)
            model["deploy_enabled"] = True
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    sharing.validate_model(kind, model)

    def test_bad_json_and_oversized_input_create_no_output(self):
        payloads = [b'{"deploy_enabled":false,"deploy_enabled":true}', b'{"x":NaN}',
                    b" " * (sharing.MAX_BYTES + 1)]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for payload in payloads:
                model = root / "bad.json"
                model.write_bytes(payload)
                output = root / "never_created.zip"
                with self.assertRaises(ValueError):
                    sharing.share_model("corpus", model, output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
