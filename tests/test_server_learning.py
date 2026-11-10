"""Native web-worker routing, persistent-learning API and child cancellation."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

from atelier.projects import strict_json
from atelier.server import JobManager, create_server
from atelier.worker import execute
from tests.test_server import tiny_plate


class NativeWorkerRouting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        parent = self

        class FakeNative:
            def __init__(self, workspace, timeout):
                parent.calls.append(("session", str(workspace), timeout))

            def _result(self, kind, project=None, **kwargs):
                parent.calls.append((kind, deepcopy(project), kwargs))
                directory = parent.root / "native" / kind
                directory.mkdir(parents=True, exist_ok=True)
                report = {"operation": kind, "quality": {"accepted": True, "minimum": .6}, "native_kernel": {"language": "rust"}}
                if kind == "learn":
                    report["selected_project"] = deepcopy(project)
                for name, data in (("report.json", report), ("preview.json", {"points": []}),
                                   ("session.json", {"project": project})):
                    (directory / name).write_text(json.dumps(data))
                (directory / "mesh.msh").write_text("native mesh")
                (directory / "guide.json").write_text("{}")
                return {"directory": str(directory), "report": report, "preview": {"points": []}}

            def generate(self, project, **kwargs): return self._result("mesh", project, **kwargs)
            def learn(self, project, **kwargs): return self._result("learn", project, **kwargs)
            def train(self, project=None, **kwargs): return self._result("train", project, **kwargs)

            def export(self, source, target, fmt, **kwargs):
                parent.calls.append(("export", fmt, kwargs))
                target = Path(target)
                with zipfile.ZipFile(target, "w") as archive:
                    archive.writestr("mesh.msh", "native converted mesh")
                return {"archive": "exports.zip", "results": [{"format": "msh", "status": "ok", "files": ["mesh.msh"]}]}

        self.patch = patch("atelier.native.NativeSession", FakeNative)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_mesh_and_learning_workers_use_native_session_and_mirror_artifacts(self):
        for operation in ("mesh", "learn"):
            with self.subTest(operation=operation):
                directory = self.root / operation
                result = execute({"directory": str(directory), "workspace": str(self.root / "workspace"),
                                  "operation": operation, "project": tiny_plate(), "search": {"max_trials": 3, "epochs": 5},
                                  "memory_dir": str(self.root / "experience")})
                self.assertEqual(result["report"]["native_kernel"]["language"], "rust")
                self.assertTrue((directory / "mesh.msh").is_file())
                self.assertTrue((directory / "preview.json").is_file())
                self.assertTrue((directory / "exports" / "mesh.msh").is_file())
                self.assertEqual(strict_json((directory / "result.json").read_bytes())["kind"], "learning" if operation == "learn" else "mesh")
        learned = next(call for call in self.calls if call[0] == "learn")
        self.assertEqual(learned[2]["max_trials"], 3)
        self.assertEqual(learned[2]["memory_dir"], str(self.root / "experience"))

    def test_legacy_training_worker_also_uses_rust_bridge(self):
        directory = self.root / "training"
        result = execute({"directory": str(directory), "operation": "train", "epochs": 2})
        self.assertEqual(result["kind"], "training")
        self.assertTrue(any(call[0] == "train" for call in self.calls))
        self.assertTrue((directory / "guide.json").is_file())


class LearningApi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server("127.0.0.1", 0, self.temp.name, timeout=30)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, path, data=None):
        payload = None if data is None else json.dumps(data).encode()
        request = Request(self.base + path, data=payload,
                          headers={"Content-Type": "application/json", "X-Atelier-Token": self.server.token})
        try: response = urlopen(request, timeout=20)
        except HTTPError as error: response = error
        with response:
            return response.status, json.loads(response.read())

    def test_learning_rejects_invalid_budget_before_enqueuing(self):
        for search in ({"max_trials": 0}, {"epochs": True}, {"use_memory": "yes"}, {"unknown": 1}):
            status, result = self.request("/api/learning", {"project": tiny_plate(), "search": search})
            self.assertEqual(status, 400, result)
        self.assertFalse(self.server.manager.jobs)

    def test_actual_learning_api_saves_native_experiences(self):
        from atelier.server import backend_info
        if not backend_info()["available"]: self.skipTest("Native mesher unavailable")
        status, created = self.request("/api/learning", {"project": tiny_plate(),
                                                           "search": {"max_trials": 2, "epochs": 5, "max_seconds": 10}})
        self.assertEqual(status, 202, created)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            _, job = self.request("/api/jobs/" + created["id"])
            if job["status"] in ("completed", "failed", "cancelled"): break
            time.sleep(.05)
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["kind"], "learning")
        self.assertIn("experience", job["report"])
        self.assertGreater(len(job["preview"]["points"]), 3)
        status, memory = self.request("/api/memory")
        self.assertEqual(status, 200, memory)
        self.assertGreater(memory["observations"], 0)


class NativeChildCancellation(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX process-group behaviour")
    def test_cancellation_kills_native_child_even_after_worker_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            child_pid = root / "child.pid"
            ready = root / "child.ready"
            child_code = "import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(" + repr(str(ready)) + ").write_text('ready'); time.sleep(25)"
            parent_code = "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); pathlib.Path(" + repr(str(child_pid)) + ").write_text(str(p.pid)); time.sleep(25)"
            manager = JobManager(root, timeout=15)
            original = subprocess.Popen
            def launch(command, *args, **kwargs):
                return original([sys.executable, "-c", parent_code], *args, **kwargs)
            try:
                with patch("atelier.server.subprocess.Popen", side_effect=launch):
                    job = manager.submit(tiny_plate())
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline: time.sleep(.02)
                    self.assertTrue(ready.exists())
                    pid = int(child_pid.read_text())
                    manager.cancel(job["id"])
                    manager.close()
                deadline = time.monotonic() + 3
                state = ""
                while time.monotonic() < deadline:
                    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
                    if not state or state.startswith("Z"): break
                    time.sleep(.02)
                self.assertTrue(not state or state.startswith("Z"), f"Native child {pid} still alive: {state}")
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
