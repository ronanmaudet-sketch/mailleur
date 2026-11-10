"""Independent tests of self-contained projects and untrusted archive data."""
from pathlib import Path
import copy
import io
import json
import stat
import tempfile
import unittest
import warnings
import zipfile

from atelier.projects import (default_examples, load_portable_project,
                              normalize_project, safe_filename, save_portable_project)


def example_project(kind="plate"):
    return {"version": 1, "name": "Pièce à conserver", "mode": "planar",
            "geometry": {"kind": kind, "width": 6, "height": 4,
                         "holes": [{"x": 3, "y": 2, "radius": .6}]},
            "mesh": {"size": .8, "min_size": .2}}


def archive(entries):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, data in entries:
                bundle.writestr(name, data)
    return stream.getvalue()


class PortableProjects(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_normalization_detaches_and_rejects_nonfinite(self):
        source = example_project()
        before = copy.deepcopy(source)
        normalized = normalize_project(source)
        normalized["geometry"]["holes"][0]["x"] = 2
        self.assertEqual(source, before)
        for value in (float("nan"), float("inf"), float("-inf")):
            p = example_project()
            p["geometry"]["holes"][0]["x"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_project(p)

    def test_all_examples_are_complete_declarative_projects(self):
        examples = default_examples()
        self.assertGreaterEqual(len(examples), 3)
        self.assertEqual(len({row["id"] for row in examples}), len(examples))
        modes = set()
        for row in examples:
            self.assertTrue(row["name"])
            self.assertTrue(row["description"])
            p = normalize_project(row["project"])
            modes.add(p["mode"])
            self.assertNotEqual(p["geometry"]["kind"], "import")
        self.assertTrue({"planar", "surface", "volume"}.issubset(modes))

    def test_native_project_roundtrip(self):
        p = example_project()
        data = save_portable_project(p)
        self.assertIsInstance(data, bytes)
        restored = load_portable_project(data, self.directory / "restored")
        self.assertEqual(restored["project"], normalize_project(p))
        self.assertIsNone(restored["source"])

    def test_imported_geometry_is_embedded_and_restored(self):
        source = self.directory / "pièce originale.step"
        source.write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
        p = {"version": 1, "name": "Import", "mode": "volume",
             "geometry": {"kind": "import"}, "mesh": {"size": 1}}
        data = save_portable_project(p, source)
        source.unlink()
        destination = self.directory / "imported"
        restored = load_portable_project(data, destination)
        self.assertEqual(restored["project"]["geometry"]["kind"], "import")
        self.assertIsNotNone(restored["source"])
        path = Path(restored["source"])
        self.assertTrue(path.is_relative_to(destination))
        self.assertEqual(path.suffix.lower(), ".step")
        self.assertIn(b"ISO-10303-21", path.read_bytes())

    def test_executable_source_cannot_be_saved(self):
        p = {"version": 1, "name": "Import", "mode": "surface", "geometry": {"kind": "import"}}
        for suffix in (".geo", ".py", ".sh"):
            source = self.directory / ("source" + suffix)
            source.write_text("executable content", encoding="utf-8")
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                save_portable_project(p, source)

    def test_source_names_are_portable_across_supported_operating_systems(self):
        self.assertEqual(safe_filename("pièce 01.step"), "pièce 01.step")
        for name in ("C:evil.step", "CON.step", "nul.step", "COM1.step", "LPT9.step",
                     "pièce?.step", 'pièce".step', "bad\r\n.step", "piece.step "):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_filename(name)

    def test_archive_cannot_escape_destination(self):
        with zipfile.ZipFile(io.BytesIO(save_portable_project(example_project()))) as original:
            entries = [(name, original.read(name)) for name in original.namelist()]
        for name in ("../escaped.txt", "/tmp/escaped.txt", "..\\escaped.txt", "C:\\escaped.txt"):
            data = archive([*entries, (name, "bad")])
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_portable_project(data, self.directory / "destination")
        self.assertFalse((self.directory / "escaped.txt").exists())

    def test_archive_symlink_and_duplicate_entries_are_rejected(self):
        with zipfile.ZipFile(io.BytesIO(save_portable_project(example_project()))) as original:
            entries = [(name, original.read(name)) for name in original.namelist()]
        info = zipfile.ZipInfo("source.step")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        symlink = archive([*entries, (info, "/etc/passwd")])
        with self.assertRaises(ValueError):
            load_portable_project(symlink, self.directory / "link")
        duplicate = archive([*entries, entries[0]])
        with self.assertRaises(ValueError):
            load_portable_project(duplicate, self.directory / "duplicate")

    def test_missing_or_malformed_archive_is_rejected(self):
        for data in (b"not a zip", archive([]), archive([("project.json", "{}")]),
                     archive([("project.json", '{"geometry": {"kind":"plate"},"name":NaN}')])):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                load_portable_project(data, self.directory / "bad")

    def test_uncompressed_size_limit_is_enforced_before_extraction(self):
        with zipfile.ZipFile(io.BytesIO(save_portable_project(example_project()))) as original:
            entries = [(name, original.read(name)) for name in original.namelist()]
        data = archive([*entries, ("source.step", b"0" * (51 * 1024 * 1024))])
        destination = self.directory / "oversized"
        with self.assertRaises(ValueError):
            load_portable_project(data, destination)
        self.assertFalse((destination / "source.step").exists())


if __name__ == "__main__":
    unittest.main()
