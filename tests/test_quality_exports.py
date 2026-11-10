"""Rejected diagnostic meshes cannot escape through direct format conversion."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from atelier.formats import convert_mesh, export_bundle


TRIANGLE = """$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
3
1 0 0 0
2 1 0 0
3 0 1 0
$EndNodes
$Elements
1
1 2 2 1 1 1 2 3
$EndElements
"""


class QualityExportGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source" / "mesh.msh"
        self.source.parent.mkdir()
        self.source.write_text(TRIANGLE, encoding="ascii")
        self.report = self.source.parent / "report.json"
        self.target = self.root / "output" / "converted.msh"

    def test_rejected_quality_blocks_direct_conversion_without_replacing_output(self):
        self.report.write_text(json.dumps({"quality": {"accepted": False}}))
        self.target.parent.mkdir()
        self.target.write_bytes(b"previous valid export")
        with self.assertRaisesRegex(ValueError, "qualit"):
            convert_mesh(self.source, self.target, "msh")
        self.assertEqual(self.target.read_bytes(), b"previous valid export")

    def test_rejected_quality_blocks_bundle_before_creating_destination(self):
        self.report.write_text(json.dumps({"quality": {"accepted": False}}))
        destination = self.root / "bundle"
        with self.assertRaisesRegex(ValueError, "qualit"):
            export_bundle(self.source, destination, formats=["msh"])
        self.assertFalse(destination.exists())

    def test_unreadable_quality_decision_is_not_ignored(self):
        for payload in ("{", "[]", '"bad"', '{"quality":[]}',
                        '{"quality":{"accepted":"false"}}',
                        '{"quality":{"accepted":false,"accepted":true}}',
                        '{"quality":{"minimum":NaN}}'):
            with self.subTest(payload=payload):
                self.report.write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError):
                    convert_mesh(self.source, self.target, "msh")
                with self.assertRaises(ValueError):
                    export_bundle(self.source, self.root / "bundle", formats=["msh"])
        self.assertFalse(self.target.exists())

    def test_display_tessellation_is_not_an_analysis_export(self):
        self.report.write_text(json.dumps({"display_only": True, "quality": {"accepted": True}}))
        with self.assertRaisesRegex(ValueError, "affichage"):
            convert_mesh(self.source, self.target, "msh")

    @unittest.skipUnless(importlib.util.find_spec("meshio"), "meshio absent")
    def test_external_mesh_without_report_is_still_convertible(self):
        result = convert_mesh(self.source, self.target, "msh")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.source.read_bytes(), self.target.read_bytes())

    @unittest.skipUnless(importlib.util.find_spec("meshio"), "meshio absent")
    def test_accepted_and_legacy_reports_do_not_prevent_conversion(self):
        for report in ({"quality": {"accepted": True}}, {"quality": {"minimum": .4}}):
            with self.subTest(report=report):
                self.report.write_text(json.dumps(report))
                self.assertEqual(convert_mesh(self.source, self.target, "msh")["status"], "ok")

    @unittest.skipUnless(importlib.util.find_spec("meshio"), "meshio absent")
    def test_destination_report_does_not_control_an_external_source(self):
        self.target.parent.mkdir()
        (self.target.parent / "report.json").write_text("malformed destination report")
        self.assertEqual(convert_mesh(self.source, self.target, "msh")["status"], "ok")


if __name__ == "__main__":
    unittest.main()
