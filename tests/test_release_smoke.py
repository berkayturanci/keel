"""`scripts/release_smoke.py` reports why a command failed and waits for a lagging index (#1526)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load()
PIP = Path("/venv/bin/pip")
NOT_FOUND = "ERROR: No matching distribution found for keel-workflow==9.9.9"


def _fail(stderr: str, stdout: str = "") -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(1, ["pip"], output=stdout, stderr=stderr)


class RunReportsFailures(unittest.TestCase):
    def test_a_failing_command_prints_argv_stdout_and_stderr(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(subprocess.CalledProcessError):
            smoke._run(
                [
                    smoke.sys.executable,
                    "-c",
                    "import sys; print('OUT-TEXT'); "
                    "print('ERR-TEXT', file=sys.stderr); sys.exit(3)",
                ]
            )
        text = err.getvalue()
        self.assertIn("exit 3", text)
        self.assertIn("OUT-TEXT", text)
        self.assertIn("ERR-TEXT", text)

    def test_a_passing_command_returns_stdout(self):
        self.assertEqual(smoke._run([smoke.sys.executable, "-c", "print('hi')"]).strip(), "hi")


class PipInstallRetry(unittest.TestCase):
    def _install(self, side_effect, **kw):
        sleeps: list[float] = []
        with (
            mock.patch.object(smoke, "_run", side_effect=side_effect) as run,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                smoke._pip_install(PIP, ["pkg==1"], sleep=sleeps.append, **kw)
            finally:
                self.run_mock = run
                self.sleeps = sleeps

    def test_not_found_then_success_retries_and_succeeds(self):
        self._install([_fail(NOT_FOUND), _fail(NOT_FOUND), ""], retry=True, interval=7)
        self.assertEqual(self.run_mock.call_count, 3)
        self.assertEqual(self.sleeps, [7, 7])
        self.assertIn("--no-cache-dir", self.run_mock.call_args[0][0])

    def test_could_not_find_a_version_wording_is_also_retried(self):
        msg = "ERROR: Could not find a version that satisfies the requirement pkg==1"
        self._install([_fail("", stdout=msg), ""], retry=True)
        self.assertEqual(self.run_mock.call_count, 2)

    def test_another_failure_is_not_retried(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self._install([_fail("ERROR: Hash mismatch")], retry=True)
        self.assertEqual(self.run_mock.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_the_cap_reports_a_timeout_and_does_not_sleep_after_the_last_attempt(self):
        with self.assertRaises(SystemExit) as ctx:
            self._install([_fail(NOT_FOUND)] * 3, retry=True, attempts=3, interval=5)
        self.assertIn("TIMEOUT", str(ctx.exception))
        self.assertEqual(self.run_mock.call_count, 3)
        self.assertEqual(self.sleeps, [5, 5])

    def test_a_local_wheel_install_is_never_retried(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self._install([_fail(NOT_FOUND)], retry=False)
        self.assertEqual(self.run_mock.call_count, 1)
        self.assertNotIn("--no-cache-dir", self.run_mock.call_args[0][0])


class WhichInstallsRetry(unittest.TestCase):
    def test_only_a_requirement_is_retried(self):
        parser = smoke.build_parser()
        self.assertTrue(smoke._installs_published(parser.parse_args(["--requirement", "p==1"])))
        self.assertFalse(smoke._installs_published(parser.parse_args(["--wheel", "p.whl"])))
        self.assertFalse(smoke._installs_published(parser.parse_args([])))


class SmokeRoutesTheInstall(unittest.TestCase):
    def _retry_flag(self, argv):
        args = smoke.build_parser().parse_args(argv)
        with (
            mock.patch.object(smoke, "_run", return_value="0"),
            mock.patch.object(smoke, "_pip_install") as install,
            self.assertRaises(AssertionError),  # stubbed sync output lacks the boundary note
        ):
            smoke.smoke(args)
        install.assert_called_once()
        return install.call_args.kwargs["retry"]

    def test_a_requirement_install_goes_through_the_retrying_path(self):
        self.assertIs(self._retry_flag(["--requirement", "p==1"]), True)

    def test_a_wheel_install_does_not(self):
        self.assertIs(self._retry_flag(["--wheel", "p.whl"]), False)


if __name__ == "__main__":
    unittest.main()
