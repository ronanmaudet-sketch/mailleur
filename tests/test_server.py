"""Loopback API integration: actual worker, portable projects and errors."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import subprocess
import sys
import signal
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile
import uuid

from atelier.server import JobManager, create_server


def tiny_plate():
    return {"version": 1, "name": "Intégration API", "mode": "planar",
            "geometry": {"kind": "plate", "width": 2, "height": 1,
                         "holes": [{"x": 1, "y": .5, "radius": .16}]},
            "mesh": {"size": .3, "min_size": .1, "threads": 1, "max_elements": 5000}}


class LocalServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temporary.name)
        cls.server = create_server("127.0.0.1", 0, cls.workspace, max_workers=1, timeout=30)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        with urlopen(cls.base + "/api/info", timeout=10) as response:
            cls.info = json.load(response)
        cls.token = cls.info["token"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        manager = getattr(cls.server, "manager", None)
        if manager:
            manager.close()
        cls.thread.join(timeout=5)
        cls.temporary.cleanup()

    def request(self, path, data=None, *, token=True, raw=None, method=None, headers=None):
        headers = dict(headers or {})
        if token:
            headers["X-Atelier-Token"] = self.token
        payload = raw
        if data is not None:
            payload = json.dumps(data, allow_nan=False).encode()
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=payload, headers=headers,
                          method=method or ("POST" if payload is not None else "GET"))
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            contents = response.read()
            content_type = response.headers.get("Content-Type", "")
            parsed = json.loads(contents) if "application/json" in content_type else contents
            return response.status, parsed

    def wait_job(self, job_id, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, job = self.request("/api/jobs/" + job_id)
            self.assertEqual(status, 200)
            if job["status"] in {"completed", "failed", "cancelled"}:
                return job
            time.sleep(.05)
        self.fail(f"Job {job_id} did not finish within {timeout}s")

    def test_info_and_static_interface(self):
        self.assertTrue(self.info["version"])
        self.assertIn("available", self.info["backend"])
        self.assertGreaterEqual(len(self.info["examples"]), 3)
        self.assertIn("msh", self.info["formats"])
        status, contents = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Atelier", contents)

    def test_format_discovery_distinguishes_optional_converters(self):
        status, info = self.request("/api/formats")
        self.assertEqual(status, 200)
        self.assertIn("nastran", info["exports"])
        rows = {row["extension"]: row for row in info["imports"]["formats"]}
        self.assertTrue(rows[".obj"]["available"])
        self.assertTrue(rows[".sldprt"]["conditional"])
        self.assertIn("import_formats", self.info)

    def test_mutations_require_token_and_strict_json(self):
        status, _ = self.request("/api/jobs", {"project": tiny_plate()}, token=False)
        self.assertEqual(status, 403)
        for raw in (b"{", b'{"project": {"geometry":{"kind":"plate","width": NaN}}}',
                    b'{"project":{},"project":{}}'):
            with self.subTest(raw=raw):
                status, _ = self.request("/api/jobs", raw=raw)
                self.assertEqual(status, 400)

    def test_cross_origin_mutation_is_rejected_even_with_token(self):
        status, _ = self.request("/api/jobs", {"project": tiny_plate()},
                                 headers={"Origin": "https://unrelated.example"})
        self.assertEqual(status, 403)

    def test_missing_resources_and_paths_are_not_exposed(self):
        for path in ("/api/jobs/missing", "/api/uploads/../../etc/passwd",
                     "/../pyproject.toml", "/%2e%2e/pyproject.toml"):
            with self.subTest(path=path):
                status, _ = self.request(path)
                self.assertIn(status, (400, 403, 404))

    def test_upload_validation(self):
        content = base64.b64encode(b"CAD test data").decode()
        for filename in ("../bad.step", "..\\bad.step", "model.geo", "script.py", "/tmp/model.step"):
            with self.subTest(filename=filename):
                status, _ = self.request("/api/uploads", {"filename": filename, "content_base64": content})
                self.assertEqual(status, 400)
        status, _ = self.request("/api/uploads", {"filename": "piece.step", "content_base64": "not@@base64"})
        self.assertEqual(status, 400)
        status, upload = self.request("/api/uploads", {"filename": "piece.step", "content_base64": content})
        self.assertIn(status, (200, 201))
        self.assertEqual(upload["filename"], "piece.step")
        self.assertEqual(upload["bytes"], len(b"CAD test data"))

    def test_portable_project_api_roundtrip_with_source(self):
        content = b"ISO-10303-21;END-ISO-10303-21;"
        status, upload = self.request("/api/uploads", {"filename": "piece.step", "content_base64": base64.b64encode(content).decode()})
        self.assertIn(status, (200, 201))
        p = {"version": 1, "name": "Portable API", "mode": "volume", "geometry": {"kind": "import"}}
        status, amp = self.request("/api/projects/save", {"project": p, "upload_id": upload["id"]})
        self.assertEqual(status, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(amp)))
        status, restored = self.request("/api/projects/open", {"filename": "projet.amp", "content_base64": base64.b64encode(amp).decode()})
        self.assertEqual(status, 200)
        self.assertEqual(restored["project"]["name"], "Portable API")
        self.assertTrue(restored["upload_id"])

    def test_import_requires_registered_upload(self):
        for field in ("path", "filename", "source", "file", "script"):
            p = {"version": 1, "name": "Import non autorisé", "mode": "surface",
                 "geometry": {"kind": "import", field: "/etc/passwd"}}
            with self.subTest(field=field):
                status, _ = self.request("/api/jobs", {"project": p})
                self.assertEqual(status, 400)
        status, _ = self.request("/api/jobs", {"project": p, "upload_id": "../../etc/passwd"})
        self.assertIn(status, (400, 404))

    def test_downloads_stream_without_loading_exports_or_preview(self):
        identifier = uuid.uuid4().hex
        directory = self.server.manager.directory(identifier)
        exports = directory / "exports"
        exports.mkdir(parents=True)
        payload = b"mesh data\n" * 250000
        (exports / "mesh.msh").write_bytes(payload)
        (exports / "mesh.xdmf").write_bytes(b"metadata")
        (exports / "mesh.h5").write_bytes(payload)
        (exports / "exports.zip").write_bytes(payload)
        result = {"kind": "mesh", "report": {"elements": 42},
                  "exports": {"results": [{"format": "msh", "status": "ok", "file": "mesh.msh"},
                                          {"format": "xdmf", "status": "ok", "files": ["mesh.xdmf", "mesh.h5"]}]}}
        (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
        # Aucun preview.json : les routes d'artefacts n'en ont pas besoin.
        with self.server.manager.lock:
            self.server.manager.jobs[identifier] = {"id": identifier, "status": "completed"}
        original = Path.read_bytes
        def guarded_read(path):
            if path.parent == exports:
                raise AssertionError("Un téléchargement doit être lu par blocs")
            return original(path)
        try:
            with patch.object(Path, "read_bytes", guarded_read):
                for suffix in ("bundle", "download?format=msh"):
                    status, contents = self.request(f"/api/jobs/{identifier}/{suffix}")
                    self.assertEqual(status, 200)
                    self.assertEqual(contents, payload)
                status, contents = self.request(f"/api/jobs/{identifier}/download?format=xdmf")
                self.assertEqual(status, 200)
                with zipfile.ZipFile(io.BytesIO(contents)) as archive:
                    self.assertEqual(archive.read("mesh.h5"), payload)
                    self.assertEqual(archive.read("mesh.xdmf"), b"metadata")
                status, report = self.request(f"/api/jobs/{identifier}/report")
                self.assertEqual(status, 200)
                self.assertEqual(report, {"elements": 42})
                status, _ = self.request(f"/api/jobs/{identifier}/report/extra")
                self.assertEqual(status, 404)
        finally:
            with self.server.manager.lock:
                self.server.manager.jobs.pop(identifier)

    def test_actual_mesh_job_and_export_bundle(self):
        if not self.info["backend"]["available"]:
            self.skipTest(self.info["backend"].get("error", "Gmsh unavailable"))
        status, created = self.request("/api/jobs", {"project": tiny_plate(), "use_advisor": False})
        self.assertIn(status, (200, 201, 202))
        job = self.wait_job(created["id"])
        self.assertEqual(job["status"], "completed", job)
        self.assertGreater(job["report"]["elements"], 0)
        self.assertGreater(job["report"]["quality"]["minimum"], 0)
        self.assertGreater(len(job["preview"]["points"]), 3)
        status, bundle = self.request(f"/api/jobs/{created['id']}/bundle")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertTrue(any(name.endswith(".msh") for name in archive.namelist()))
            self.assertTrue(any(name.endswith(".json") for name in archive.namelist()))
        status, data = self.request(f"/api/jobs/{created['id']}/download?format=msh")
        self.assertEqual(status, 200)
        self.assertIn(b"$MeshFormat", data)

    def test_obj_upload_really_reconstructs_and_exports_nastran(self):
        if not self.info["backend"]["available"]:
            self.skipTest(self.info["backend"].get("error", "Gmsh unavailable"))
        obj = b"v 0 0 0\nv 2 0 0\nv 0 2 0\nv 0 0 2\nf 1 3 2\nf 1 2 4\nf 2 3 4\nf 3 1 4\n"
        status, upload = self.request("/api/uploads", {"filename": "tetra.obj", "content_base64": base64.b64encode(obj).decode()})
        self.assertEqual(status, 201)
        project = {"name": "OBJ via API", "mode": "volume", "geometry": {"kind": "import"},
                   "units": {"input": "mm"}, "mesh": {"size": .6, "min_size": .2}}
        status, created = self.request("/api/jobs", {"project": project, "upload_id": upload["id"]})
        self.assertEqual(status, 202)
        job = self.wait_job(created["id"])
        self.assertEqual(job["status"], "completed", job)
        self.assertIn("native_preparation", job["report"]["import_preparation"])
        status, bdf = self.request(f"/api/jobs/{created['id']}/download?format=nastran")
        self.assertEqual(status, 200)
        self.assertIn(b"CTETRA", bdf)

    def test_bad_geometry_is_visible_as_rejected_or_failed_job(self):
        if not self.info["backend"]["available"]:
            self.skipTest(self.info["backend"].get("error", "Gmsh unavailable"))
        project = tiny_plate()
        project["geometry"]["holes"] = [{"x": 0, "y": 0, "radius": 2}]
        status, created = self.request("/api/jobs", {"project": project})
        if status == 400:
            return
        self.assertIn(status, (200, 201, 202))
        job = self.wait_job(created["id"])
        self.assertEqual(job["status"], "failed")
        self.assertTrue(job.get("message") or job.get("error"))


class JobLifecycle(unittest.TestCase):
    """Actual subprocess termination without relying on an expensive mesh."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def wait_status(self, manager, identifier, statuses, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            job = manager.get(identifier)
            if job["status"] in statuses:
                return job
            time.sleep(.02)
        self.fail(f"Job did not reach {statuses}")

    def test_interrupted_job_becomes_failed_after_restart(self):
        identifier = "a" * 32
        directory = self.directory / "jobs" / identifier
        directory.mkdir(parents=True)
        (directory / "job.json").write_text(json.dumps({"id": identifier, "kind": "mesh", "status": "running"}), encoding="utf-8")
        manager = JobManager(self.directory)
        try:
            job = manager.get(identifier)
            self.assertEqual(job["status"], "failed")
            self.assertIn("interrompu", job["message"])
        finally:
            manager.close()

    def test_queued_job_can_be_cancelled_without_running(self):
        manager = JobManager(self.directory, max_workers=1)
        gate = threading.Event()
        manager.executor.submit(gate.wait, 5)
        try:
            job = manager.submit(tiny_plate())
            cancelled = manager.cancel(job["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertNotIn(job["id"], manager.processes)
            self.assertFalse((manager.directory(job["id"]) / "result.json").exists())
        finally:
            gate.set()
            manager.close()

    def slow_worker(self, original, processes):
        def launch(command, *args, **kwargs):
            process = original([sys.executable, "-c", "import time; time.sleep(20)"], *args, **kwargs)
            processes.append(process)
            return process
        return launch

    def test_timeout_terminates_worker_and_reports_failure(self):
        manager = JobManager(self.directory, timeout=1)
        original = subprocess.Popen
        processes = []
        try:
            with patch("atelier.server.subprocess.Popen", side_effect=self.slow_worker(original, processes)):
                created = manager.submit(tiny_plate())
                job = self.wait_status(manager, created["id"], {"failed"})
            self.assertIn("Délai maximal", job["message"])
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
        finally:
            manager.close()

    def test_running_worker_is_terminated_on_cancellation(self):
        manager = JobManager(self.directory, timeout=20)
        original = subprocess.Popen
        processes = []
        try:
            with patch("atelier.server.subprocess.Popen", side_effect=self.slow_worker(original, processes)):
                created = manager.submit(tiny_plate())
                deadline = time.monotonic() + 5
                while not processes and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(len(processes), 1)
                manager.cancel(created["id"])
                manager.close()
            self.assertEqual(manager.get(created["id"])["status"], "cancelled")
            self.assertIsNotNone(processes[0].poll())
        finally:
            manager.close()

    @unittest.skipIf(os.name == "nt", "Contrôle des groupes POSIX ; les jobs Windows demandent une validation distincte")
    def test_failed_worker_does_not_leave_a_sigterm_resistant_child(self):
        manager = JobManager(self.directory, timeout=10)
        original = subprocess.Popen
        pid_file, ready = self.directory / "child.pid", self.directory / "ready"
        child = ("import os,signal,time;from pathlib import Path;"
                 "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                 f"Path({str(ready)!r}).write_text('ready');time.sleep(20)")
        parent = ("import subprocess,sys,time;from pathlib import Path;"
                  f"p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
                  f"Path({str(pid_file)!r}).write_text(str(p.pid));"
                  f"deadline=time.monotonic()+3\nwhile not Path({str(ready)!r}).exists() and time.monotonic()<deadline:time.sleep(.01)\n"
                  "raise SystemExit(2)")
        def launch(command, *args, **kwargs):
            return original([sys.executable, "-c", parent], *args, **kwargs)
        try:
            with patch("atelier.server.subprocess.Popen", side_effect=launch):
                created = manager.submit(tiny_plate())
                self.wait_status(manager, created["id"], {"failed"})
                manager.close()  # wait for the finally block and its group cleanup
            self.assertTrue(pid_file.exists())
            pid = int(pid_file.read_text())
            state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
            self.assertTrue(state.returncode != 0 or state.stdout.strip().startswith("Z"), state.stdout)
        finally:
            manager.close()
            if pid_file.exists():
                try: os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError: pass


if __name__ == "__main__":
    unittest.main()
