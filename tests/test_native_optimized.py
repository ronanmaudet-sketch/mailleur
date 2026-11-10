"""Regression checks for transport and discarded corpus artifacts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


class OptimizedNativeTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from atelier.native import NativeSession, core_path, gmsh_library
        cls.session_type = NativeSession
        cls.binary = core_path()
        gmsh_library()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.session_type(self.root / "workspace", timeout=30)
        self.addCleanup(self.session.cancel)

    @staticmethod
    def project():
        return {"schema_version": 1, "name": "Transport quality parity", "mode": "planar",
                "units": {"input": "mm", "internal": "m"},
                "geometry": {"kind": "plate", "width": 6, "height": 4,
                             "holes": [{"x": 3, "y": 2, "radius": .6}]},
                "mesh": {"size": .55, "min_size": .15, "order": 1,
                         "algorithm": "delaunay", "threads": 1, "max_elements": 10000}}

    def test_summary_ack_keeps_the_authoritative_report_and_full_protocol(self):
        payload = {"operation": "metrics", "preview": {
            "points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "cells": [{"type": "triangle", "data": [[0, 1, 2]]}]}}
        reports = []
        for mode in ("full", "summary"):
            directory = self.root / mode
            directory.mkdir()
            request = {**payload, "output_dir": str(directory), "response_mode": mode}
            source = directory / "request.json"
            source.write_text(json.dumps(request))
            run = subprocess.run([str(self.binary), "--request", str(source)],
                                 capture_output=True, text=True, timeout=10, check=True)
            reply = json.loads(run.stdout)
            self.assertTrue(reply["ok"])
            report = json.loads((directory / "report.json").read_text())
            reports.append(report)
            if mode == "full":
                self.assertEqual(reply["result"], report)
            else:
                self.assertEqual(reply, {"ok": True})
        self.assertEqual(reports[0], reports[1])

    def test_report_only_preserves_quality_and_clears_previous_mesh_artifacts(self):
        from atelier.native import native_project
        full = self.session.generate(self.project())
        directory = Path(full["directory"])
        self.assertTrue((directory / "mesh.msh").is_file())
        request = {"operation": "mesh", "project": native_project(self.project()),
                   "report_only": True, "output_dir": str(directory)}
        self.session._call(request, directory)
        report = json.loads((directory / "report.json").read_text())
        for field in ("quality", "nodes", "elements", "cell_counts", "groups", "correspondences", "algorithms"):
            self.assertEqual(report[field], full["report"][field], field)
        self.assertFalse(report["artifacts_written"])
        for name in ("mesh.msh", "mesh.state.msh", "model.brep", "preview.json", "replay.json", "project.normalized.json"):
            self.assertFalse((directory / name).exists(), name)
        with self.assertRaises(ValueError):
            self.session.export(directory, self.root / "rejected.msh", "msh")
        rejected = deepcopy(request)
        rejected["project"]["quality_policy"] = {"minimum_quality": 1., "strict": True}
        with self.assertRaisesRegex(RuntimeError, "qualité"):
            self.session._call(rejected, directory)
        self.assertFalse((directory / "mesh.msh").exists())

    def test_streamed_source_digest_detects_a_late_byte_change(self):
        from atelier.native import file_digest
        path = self.root / "source.step"
        block = b"GEOMETRY\x00" * 10000
        with path.open("wb") as stream:
            for _ in range(30):
                stream.write(block)
        self.assertEqual(file_digest(path), hashlib.sha256(path.read_bytes()).digest())
        previous = file_digest(path)
        with path.open("r+b") as stream:
            stream.seek(-1, 2)
            stream.write(b"!")
        self.assertNotEqual(file_digest(path), previous)


if __name__ == "__main__":
    unittest.main()
