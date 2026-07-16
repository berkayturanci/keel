"""Bridge to the JS-level template tests in ``tests/js/*.test.mjs``.

The JS suite uses only node's built-in test runner (``node --test``) — zero npm
dependencies, fully offline — and exercises the inline scripts of the three web
templates (runviz.html, board.html, dashboard.html) against a stub DOM.

Running it from unittest means the existing CI step
(``coverage run -m unittest discover -s keel-visual/tests``) picks it up with no
workflow changes; GitHub's ubuntu/macos/windows runners all ship node. When node
is not installed locally the bridge skips with an explicit reason.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
import unittest.mock as mock
from pathlib import Path

JS_TEST_DIR = Path(__file__).resolve().parent / "js"
NODE_TIMEOUT = 300  # seconds — the suite itself finishes in a few seconds


def js_test_files() -> list[str]:
    """The *.test.mjs files, passed explicitly — node 21+ no longer accepts a bare
    directory argument to --test, and an explicit list is portable across OSes."""
    return sorted(str(p) for p in JS_TEST_DIR.glob("*.test.mjs"))


def run_node_suite() -> subprocess.CompletedProcess[str]:
    """Run ``node --test`` over tests/js; skip the caller when node is absent."""
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node executable not found — skipping JS template tests")
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [node, "--test", *js_test_files()],
        capture_output=True,
        text=True,
        timeout=NODE_TIMEOUT,
        check=False,
    )


class TestJsTemplates(unittest.TestCase):
    def test_js_test_files_exist(self) -> None:
        files = js_test_files()
        self.assertGreaterEqual(len(files), 4, f"expected JS test files in {JS_TEST_DIR}")
        self.assertTrue(all(f.endswith(".test.mjs") for f in files))

    def test_js_template_suite_passes(self) -> None:
        proc = run_node_suite()  # raises SkipTest (→ skipped test) without node
        self.assertEqual(
            proc.returncode,
            0,
            msg=(
                "node --test reported failures:\n"
                f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            ),
        )

    def test_skips_with_clear_reason_when_node_is_absent(self) -> None:
        with mock.patch("shutil.which", return_value=None) as which:
            with self.assertRaises(unittest.SkipTest) as ctx:
                run_node_suite()
        which.assert_called_once_with("node")
        self.assertIn("node executable not found", str(ctx.exception))

    def test_runs_node_against_the_js_test_dir_when_node_is_present(self) -> None:
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            mock.patch("shutil.which", return_value="/usr/bin/node"),
            mock.patch("subprocess.run", return_value=fake) as run,
        ):
            proc = run_node_suite()
        self.assertIs(proc, fake)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "/usr/bin/node")
        self.assertEqual(argv[1], "--test")
        self.assertEqual(argv[2:], js_test_files())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
