"""Unit tests for distribution assets (Homebrew tap formula and curl installer)."""

import hashlib
import os
import re
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestHomebrewFormula(unittest.TestCase):
    def test_formula_exists_and_has_required_fields(self):
        formula_path = ROOT / "Formula" / "keel.rb"
        self.assertTrue(formula_path.exists(), "Formula/keel.rb must exist in root repo")
        content = formula_path.read_text(encoding="utf-8")
        self.assertIn("class Keel < Formula", content)
        self.assertIn('homepage "https://github.com/berkayturanci/keel"', content)
        self.assertIn('depends_on "python@3.12"', content)
        self.assertIn("def install", content)
        self.assertIn("test do", content)

    def test_the_formula_url_names_the_current_version(self):
        """`release-bump` moves this url; nothing moved the digest with it.

        Offline half of the guard. It cannot see a stale digest — 64 hex
        characters look alike — but it does catch a formula left behind by a
        release entirely.
        """
        from keel import __version__

        formula = (ROOT / "Formula" / "keel.rb").read_text(encoding="utf-8")
        url = re.search(r'url "(https://\S+)"', formula).group(1)
        self.assertIn(
            f"v{__version__}.tar.gz",
            url,
            f"the formula points at {url}, not at v{__version__}",
        )

    def test_the_url_hashes_to_the_declared_digest(self):
        """The check `brew install` performs, run before a user does.

        Network-gated like the rest of `external promises`. A 404 **fails**
        rather than skips: a url pointing at a tag that does not exist is
        exactly the failure this exists for, and skipping would pass it.
        """
        if os.environ.get("KEEL_CHECK_EXTERNAL") != "1":
            self.skipTest("set KEEL_CHECK_EXTERNAL=1 to fetch the artifact")

        formula = (ROOT / "Formula" / "keel.rb").read_text(encoding="utf-8")
        url = re.search(r'url "(https://\S+)"', formula).group(1)
        digest = re.search(r'sha256 "([0-9a-f]{64})"', formula).group(1)
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.fail(f"the formula points at {url}, which does not exist")
            # `raise`, though skipTest raises on its own: it makes the control
            # flow explicit to a reader and to CodeQL, which otherwise reads
            # `payload` below as possibly unbound.
            raise self.skipTest(f"cannot fetch the artifact: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise self.skipTest(f"cannot fetch the artifact: {exc}") from exc

        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            digest,
            "brew install would refuse: the declared digest is not this artifact's",
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
