"""Vérifications de portabilité sans installer ni télécharger de dépendance."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import install, launch


class NativeInstallation(unittest.TestCase):
    def test_binary_must_execute_on_the_current_computer(self):
        for failure in (OSError("format incompatible"), subprocess.TimeoutExpired("core", 10)):
            with self.subTest(failure=failure), patch.object(install.subprocess, "run", side_effect=failure):
                self.assertFalse(install.binary_runs(Path("core")))
        with patch.object(install.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "mailleur-core 1.0\n")):
            self.assertTrue(install.binary_runs(Path("core")))
        with patch.object(install.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "autre outil\n")):
            self.assertFalse(install.binary_runs(Path("core")))

    def test_installer_uses_the_same_priority_as_the_native_bridge(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            name = "mailleur-core.exe" if install.sys.platform == "win32" else "mailleur-core"
            compiled = root / "rust_core" / "target" / "release" / name
            packaged = root / "rust_core" / "bin" / f"{install.sys.platform}-{install.platform.machine().lower()}" / name
            for file in (compiled, packaged):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            with patch.object(install, "ROOT", root), patch.object(install, "binary_runs", return_value=True) as runnable:
                self.assertEqual(install.native_binary(), compiled)
                runnable.assert_called_once_with(compiled)
            with patch.object(install, "ROOT", root), patch.object(install, "binary_runs", return_value=False):
                self.assertIsNone(install.native_binary())

    def test_invalid_override_is_reported_instead_of_silently_replaced(self):
        with tempfile.NamedTemporaryFile() as binary, patch.dict(os.environ, {"ATELIER_CORE": binary.name}):
            with patch.object(install, "binary_runs", return_value=False):
                with self.assertRaisesRegex(ValueError, "ne peut pas être exécuté"):
                    install.native_binary()

    def test_launch_readiness_check_is_bounded(self):
        with tempfile.NamedTemporaryFile() as executable:
            with patch.object(launch.subprocess, "run", side_effect=subprocess.TimeoutExpired("python", 20)) as run:
                self.assertFalse(launch.environment_ready(Path(executable.name)))
                self.assertEqual(run.call_args.kwargs["timeout"], 20)
                self.assertIn("gmsh_library", run.call_args.args[0][2])
                self.assertIn("meshio", run.call_args.args[0][2])


if __name__ == "__main__":
    unittest.main()
