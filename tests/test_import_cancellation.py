"""Budget cumulé et arrêt des descendants des passerelles d'import."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from atelier.native import NativeSession
from mailleur.io import write_json


def imported_project():
    return {"schema_version": 1, "name": "Import borné", "mode": "volume",
            "geometry": {"kind": "import"}, "units": {"input": "mm", "internal": "m"}}


class ImportOperationBudget(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "part.obj"
        self.source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")

    def decoded(self, request_path, directory):
        directory = Path(directory)
        dto = write_json(directory / "mesh_input.json", {"schema": "atelier.mesh_import.v1"})
        write_json(directory / "import.json", {"kind": "mesh_dto", "file": str(dto)})

    def test_cancellation_between_parser_and_rust_is_not_cleared(self):
        session = NativeSession(self.root / "workspace")
        def worker(module, request, directory, **kwargs):
            self.decoded(request, directory)
            session.cancel()
        with patch.object(session, "_run_file_worker", side_effect=worker), patch("atelier.native.core_path") as core:
            with self.assertRaisesRegex(RuntimeError, "annulé"):
                session.generate(imported_project(), self.source)
        core.assert_not_called()
        self.assertIsNone(session.last_directory)

    def test_global_deadline_is_shared_by_parser_and_normalizer(self):
        session = NativeSession(self.root / "workspace", timeout=1)
        clock = [10.]
        def worker(module, request, directory, **kwargs):
            self.decoded(request, directory)
            clock[0] += .6
        def launch(arguments, **kwargs):
            request = json.loads(Path(arguments[-1]).read_text())
            self.assertEqual(request["operation"], "prepare_mesh_import")
            Path(request["output_mesh"]).write_text("native prepared mesh")
            write_json(Path(request["output_dir"]) / "report.json", {"warnings": []})
            clock[0] += .6
            process = Mock(pid=99999999, returncode=0)
            process.poll.return_value = 0
            return process
        with patch("atelier.native.time.monotonic", side_effect=lambda: clock[0]), \
             patch.object(session, "_run_file_worker", side_effect=worker), \
             patch("atelier.native.core_path", return_value=Path(sys.executable)), \
             patch("atelier.native.subprocess.Popen", side_effect=launch) as process, \
             patch.object(session, "_stop_process_tree"):
            with self.assertRaisesRegex(RuntimeError, "Délai maximal"):
                session.generate(imported_project(), self.source)
            self.assertEqual(process.call_count, 1)  # Le maillage final n'a pas commencé.
            self.assertIsNone(session._deadline)
            session.cancel()
            def second_operation(request, directory, **kwargs):
                self.assertFalse(session._cancelled.is_set())
                write_json(Path(directory) / "report.json", {"operation": request["operation"]})
            with patch.object(session, "_call", side_effect=second_operation):
                self.assertEqual(session.query("metrics", preview={})["operation"], "metrics")

    def test_concurrent_request_cannot_reset_the_active_cancellation(self):
        session = NativeSession(self.root / "workspace")
        entered, release = threading.Event(), threading.Event()
        errors = []
        def worker(module, request, directory, **kwargs):
            self.decoded(request, directory)
            entered.set()
            release.wait(5)
        def generate():
            try: session.generate(imported_project(), self.source)
            except Exception as error: errors.append(error)
        with patch.object(session, "_run_file_worker", side_effect=worker):
            thread = threading.Thread(target=generate)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                session.cancel()
                with self.assertRaisesRegex(RuntimeError, "déjà en cours"):
                    session.query("metrics", preview={})
                self.assertTrue(session._cancelled.is_set())
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertIn("annulé", str(errors[0]))

    def test_source_size_is_checked_before_copy(self):
        source = self.root / "large.step"
        with source.open("wb") as stream:
            stream.truncate(200 * 1024 * 1024 + 1)
        with patch("atelier.native.shutil.copy2") as copy:
            with self.assertRaisesRegex(ValueError, "200 Mio"):
                NativeSession(self.root / "workspace").generate(imported_project(), source)
        copy.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Autorité du groupe POSIX partagé par le worker HTTP")
    def test_shared_http_group_is_never_killed_by_a_native_session(self):
        process = Mock(returncode=2, pid=12345)
        process.poll.return_value = 2
        with patch("atelier.native.os.killpg") as kill_group:
            NativeSession._stop_process_tree(process, separate_group=False)
        kill_group.assert_not_called()


@unittest.skipIf(os.name == "nt", "Vérification POSIX des groupes et des petits-enfants")
class ConverterDescendants(unittest.TestCase):
    def run_case(self, cancel):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ready, pidfile = root / "ready", root / "child.pid"
            child = ("import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                     f"pathlib.Path({str(ready)!r}).write_text('ready'); time.sleep(30)")
            converter = root / "converter.py"
            converter.write_text("import pathlib,subprocess,sys,time\n"
                                 f"process=subprocess.Popen([sys.executable,'-c',{child!r}])\n"
                                 f"pathlib.Path({str(pidfile)!r}).write_text(str(process.pid))\n"
                                 "time.sleep(30)\n")
            config = write_json(root / "converters.json", {"schema": "atelier.cad_converters.v1", "converters": [{
                "name": "Test borné", "extensions": [".sldprt"], "timeout": 20 if cancel else 1,
                "command": [sys.executable, str(converter), "{input}", "{output}"], "output_extension": ".step"}]})
            source = root / "part.sldprt"
            source.write_bytes(b"synthetic CAD fixture")
            session, errors = NativeSession(root / "workspace", timeout=10), []
            def run():
                try: session.generate(imported_project(), source)
                except Exception as error: errors.append(error)
            environment = os.environ.copy()
            environment.pop("ATELIER_WORKER_PROCESS_GROUP", None)
            environment["MAILLEUR_CAD_CONVERTERS"] = str(config)
            with patch.dict(os.environ, environment, clear=True):
                thread = threading.Thread(target=run)
                thread.start()
                pid = None
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline: time.sleep(.02)
                    self.assertTrue(ready.exists(), "Le descendant de la passerelle n'a pas démarré")
                    pid = int(pidfile.read_text())
                    if cancel: session.cancel()
                    thread.join(6)
                    self.assertFalse(thread.is_alive(), "L'import n'a pas répondu à son délai/annulation")
                    self.assertTrue(errors)
                    self.assertIn("annulé" if cancel else "dépassé", str(errors[0]))
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
                        if not state or state.startswith("Z"): break
                        time.sleep(.02)
                    self.assertTrue(not state or state.startswith("Z"), f"Descendant encore actif : {pid}")
                finally:
                    session.cancel()
                    thread.join(3)
                    if pid is not None:
                        try: os.kill(pid, 9)
                        except ProcessLookupError: pass

    def test_cancellation_kills_child_even_when_worker_exits_on_sigterm(self):
        self.run_case(cancel=True)

    def test_converter_timeout_cleans_up_grandchild_after_worker_failure(self):
        self.run_case(cancel=False)


if __name__ == "__main__":
    unittest.main()
