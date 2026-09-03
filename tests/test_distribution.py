"""Unit tests for distribution assets (Homebrew tap formula and curl installer)."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestHomebrewFormula(unittest.TestCase):
    """What is committed is the template, not the formula (#1023, #990).

    A committed `Formula/keel.rb` had to name a url and a sha256 that cannot both
    be correct before the tag exists, so it was stale on every release and every
    release paid for it with a second write to `main`. `publish.yml` renders the
    template after the tag and attaches the result to the GitHub Release.
    """

    def test_template_exists_and_has_required_fields(self):
        template_path = ROOT / "packaging" / "homebrew" / "keel.rb.template"
        self.assertTrue(template_path.exists(), "packaging/homebrew/keel.rb.template must exist")
        content = template_path.read_text(encoding="utf-8")
        self.assertIn("class Keel < Formula", content)
        self.assertIn('homepage "https://github.com/berkayturanci/keel"', content)
        self.assertIn('depends_on "python@3.12"', content)
        self.assertIn("def install", content)
        self.assertIn("test do", content)

    def test_the_committed_formula_does_not_come_back(self):
        """The file, not the mechanism: restoring it restores the second write."""
        self.assertFalse(
            (ROOT / "Formula" / "keel.rb").exists(),
            "Formula/keel.rb is rendered during the release (see packaging/homebrew/README.md); "
            "a committed copy is stale on every release by construction",
        )


class TestStandaloneInstaller(unittest.TestCase):
    def test_installer_script_exists_and_is_executable(self):
        script_path = ROOT / "scripts" / "install.sh"
        self.assertTrue(script_path.exists(), "scripts/install.sh must exist")
        self.assertTrue(os.access(script_path, os.X_OK), "scripts/install.sh must be executable")
        content = script_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("#!/usr/bin/env sh"))

    @unittest.skipIf(
        sys.platform == "win32",
        "scripts/install.sh is the POSIX install path; Windows installs with pip",
    )
    def test_installer_dry_run(self):
        """Exercise the POSIX installer's dry run.

        Skipped on Windows deliberately, and not because it is inconvenient:
        `install.sh` resolves `$HOME/.local/share`, probes for `python3.x` on a
        POSIX `PATH`, and is documented as the `curl … | sh` path. Windows
        installs keel with `pip`. Running it under Git Bash was tried first —
        it exits non-zero there against a Windows environment — and a test that
        passes only by being run in a shell no user of that platform would use
        is not testing the installer, it is testing Git Bash.

        The shape assertion above (`#!/usr/bin/env sh`, the file is executable)
        still runs everywhere, so the script is not entirely unwatched on
        Windows (#953).
        """
        script_path = ROOT / "scripts" / "install.sh"
        env = {
            **os.environ,
            "DRY_RUN": "1",
            "KEEL_INSTALL_DIR": "/tmp/custom-keel",
            "KEEL_BIN_DIR": "/tmp/custom-bin",
        }
        # Invoked through bash rather than executed directly: a `.sh` is not a
        # program on every platform, and this keeps the invocation identical
        # wherever the test does run.
        proc = subprocess.run(
            ["bash", str(script_path)],
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
