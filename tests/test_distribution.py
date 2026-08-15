"""Unit tests for distribution assets (Homebrew tap formula and curl installer)."""

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestHomebrewFormula(unittest.TestCase):
    def test_formula_exists_and_has_required_fields(self):
        formula_path = ROOT / "Formula" / "keel.rb"
        self.assertTrue(formula_path.exists(), "Formula/keel.rb must exist in root repo")
        content = formula_path.read_text(encoding="utf-8")
        self.assertIn("class Keel < Formula", content)
        self.assertIn("homepage \"https://github.com/berkayturanci/keel\"", content)
        self.assertIn("depends_on \"python@3.12\"", content)
        self.assertIn("def install", content)
        self.assertIn("test do", content)


class TestStandaloneInstaller(unittest.TestCase):
    def test_installer_script_exists_and_is_executable(self):
        script_path = ROOT / "scripts" / "install.sh"
        self.assertTrue(script_path.exists(), "scripts/install.sh must exist")
        self.assertTrue(os.access(script_path, os.X_OK), "scripts/install.sh must be executable")
        content = script_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("#!/usr/bin/env sh"))

    def test_installer_dry_run(self):
        script_path = ROOT / "scripts" / "install.sh"
        env = {
            **os.environ,
            "DRY_RUN": "1",
            "KEEL_INSTALL_DIR": "/tmp/custom-keel",
            "KEEL_BIN_DIR": "/tmp/custom-bin",
        }
        proc = subprocess.run(
            [str(script_path)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("⚓ Keel Standalone Installer", proc.stdout)
        self.assertIn("[DRY-RUN]", proc.stdout)
        self.assertIn("/tmp/custom-keel", proc.stdout)
        self.assertIn("/tmp/custom-bin", proc.stdout)


if __name__ == "__main__":
    unittest.main()
