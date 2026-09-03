"""Unit tests for ``scripts/find_python.sh`` — the Makefile's interpreter resolver.

The resolver is what stops `make test` from running on whatever ``python3``
happens to be (#1022). It is exercised against a directory of stub interpreters:
each stub is a two-line shell script whose exit status *is* the answer to
"are you >= 3.11 with PyYAML?", which is the only thing the resolver asks. The
copy under test is placed in a temporary tree so the developer's own ``.venv``
cannot decide the outcome.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESOLVER = ROOT / "scripts" / "find_python.sh"
MAKEFILE = ROOT / "Makefile"

#: what a stub interpreter does with `-c 'import sys, yaml; assert ...'`.
USABLE = 0
UNUSABLE = 1


def _stub(path: Path, status: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # `#!/bin/sh`, not `env sh`: the stubs are exec'd with a PATH that holds
    # nothing but themselves, which is the point of the fixture.
    path.write_text(f"#!/bin/sh\nexit {status}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _sandbox(root: Path) -> Path:
    """Copy the resolver into ``root`` so its ``.venv`` probe is hermetic."""
    copy = root / "scripts" / "find_python.sh"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(RESOLVER.read_text(encoding="utf-8"), encoding="utf-8")
    copy.chmod(0o755)
    return copy


def _resolve(root: Path, path_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/sh", str(_sandbox(root))],
        capture_output=True,
        text=True,
        env={"PATH": str(path_dir)},
        check=False,
    )


class TestResolverShape(unittest.TestCase):
    """Assertions that hold on every platform, Windows included."""

    def test_the_resolver_exists_and_is_executable(self):
        self.assertTrue(RESOLVER.is_file(), "scripts/find_python.sh must exist")
        self.assertTrue(os.access(RESOLVER, os.X_OK), "scripts/find_python.sh must be executable")
        self.assertTrue(RESOLVER.read_text(encoding="utf-8").startswith("#!/usr/bin/env sh"))

    def test_the_makefile_uses_it_and_stays_overridable(self):
        makefile = MAKEFILE.read_text(encoding="utf-8")
        self.assertIn("ifeq ($(origin PY),undefined)", makefile)
        self.assertIn("PY := $(shell scripts/find_python.sh)", makefile)
        self.assertIn("doctor-python:", makefile)

    def test_it_asks_the_interpreter_for_both_requirements(self):
        # The version bar and PyYAML are one question asked of the candidate
        # itself; a resolver that only parsed `--version` would hand `make test`
        # an interpreter without keel's one runtime dependency.
        script = RESOLVER.read_text(encoding="utf-8")
        self.assertIn("import sys, yaml; assert sys.version_info >= (3, 11)", script)


@unittest.skipIf(
    sys.platform == "win32",
    "find_python.sh is the POSIX resolver; the Windows CI job runs the suite without make",
)
class TestResolverBehaviour(unittest.TestCase):
    def test_the_repository_venv_wins(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            venv = _stub(root / ".venv" / "bin" / "python", USABLE)
            bin_dir = root / "bin"
            _stub(bin_dir / "python3.13", USABLE)
            proc = _resolve(root, bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), str(venv))

    def test_an_unusable_venv_does_not_shadow_the_path(self):
        # A venv built on an interpreter that no longer satisfies the bar (or
        # that lost PyYAML) is skipped rather than reported as the answer.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _stub(root / ".venv" / "bin" / "python", UNUSABLE)
            bin_dir = root / "bin"
            good = _stub(bin_dir / "python3.12", USABLE)
            proc = _resolve(root, bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), str(good))

    def test_the_newest_usable_interpreter_wins(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bin_dir = root / "bin"
            _stub(bin_dir / "python3.11", USABLE)
            _stub(bin_dir / "python3.12", USABLE)
            newest = _stub(bin_dir / "python3.13", USABLE)
            proc = _resolve(root, bin_dir)
        self.assertEqual(proc.stdout.strip(), str(newest))

    def test_an_interpreter_without_pyyaml_is_skipped(self):
        # 3.13 is newer but cannot import yaml; 3.12 can, so 3.12 is the answer.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bin_dir = root / "bin"
            _stub(bin_dir / "python3.13", UNUSABLE)
            usable = _stub(bin_dir / "python3.12", USABLE)
            proc = _resolve(root, bin_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), str(usable))

    def test_a_plain_python3_is_accepted_when_it_qualifies(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bin_dir = root / "bin"
            fallback = _stub(bin_dir / "python3", USABLE)
            proc = _resolve(root, bin_dir)
        self.assertEqual(proc.stdout.strip(), str(fallback))

    def test_only_an_old_python3_fails_with_an_install_hint(self):
        # The #1022 machine: Xcode's 3.9 is the only `python3` on PATH.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bin_dir = root / "bin"
            _stub(bin_dir / "python3", UNUSABLE)
            proc = _resolve(root, bin_dir)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("no Python >= 3.11 with PyYAML found", proc.stderr)
        self.assertIn("python3.12", proc.stderr)
        self.assertIn('pip install -e ".[dev]"', proc.stderr)
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1, "one line, not a wall of text")

    def test_an_empty_path_fails_the_same_way(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            proc = _resolve(root, root / "empty")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no Python >= 3.11 with PyYAML found", proc.stderr)


if __name__ == "__main__":
    unittest.main()
