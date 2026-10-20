import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.fetch_engine import fetch_engine
from tools.redot_api import IdentityError, load_lock


class FetchEngineTests(unittest.TestCase):
    def test_archive_checksum_binary_checksum_and_exact_executable_are_required(self):
        lock = load_lock()
        for profile in lock["certified_engines"]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                asset = lock["certified_engines"][profile]["assets"]["windows-x86_64"]
                executable = asset["executable"]
                content = b"official editor fixture"
                payload = io.BytesIO()
                with zipfile.ZipFile(payload, "w") as package:
                    package.writestr(executable, content)
                data = payload.getvalue()
                asset["sha256"] = hashlib.sha256(data).hexdigest().upper()
                asset["binaries"] = {executable: hashlib.sha256(content).hexdigest().upper()}
                with patch("tools.fetch_engine.urllib.request.urlopen", return_value=io.BytesIO(data)):
                    binary = fetch_engine(lock, profile, "windows-x86_64", root)
                self.assertEqual(binary.name, executable)
                self.assertEqual(binary.read_bytes(), content)
                asset["binaries"][executable] = "0" * 64
                with self.assertRaisesRegex(IdentityError, "binary SHA-256"):
                    fetch_engine(lock, profile, "windows-x86_64", root)
                asset["sha256"] = "0" * 64
                with patch("tools.fetch_engine.urllib.request.urlopen", return_value=io.BytesIO(data)), self.assertRaisesRegex(IdentityError, "archive SHA-256"):
                    fetch_engine(lock, profile, "windows-x86_64", root)
