"""Une ancienne installation ou un override ne doit pas changer de mailleur."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from atelier.core_locator import ENGINE_ID, find_core


class CoreIdentity(unittest.TestCase):
    def test_incompatible_override_fails_even_with_a_current_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "ancien"
            old.touch()
            reply = subprocess.CompletedProcess([], 0, stdout=b"mailleur-core 1.0.0-rc.9")
            with patch.dict(os.environ, ATELIER_CORE=str(old)), patch("subprocess.run", return_value=reply):
                with self.assertRaisesRegex(ValueError, "directe"):
                    find_core(root)

    def test_replaced_binary_is_checked_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "core"
            path.touch()
            replies = [subprocess.CompletedProcess([], 0, stdout=ENGINE_ID.encode()),
                       subprocess.CompletedProcess([], 2, stdout=b"")]
            with patch.dict(os.environ, ATELIER_CORE=str(path)), patch("subprocess.run", side_effect=replies):
                self.assertEqual(find_core(path.parent), path.resolve())
                path.write_bytes(b"another build")
                with self.assertRaises(ValueError):
                    find_core(path.parent)
