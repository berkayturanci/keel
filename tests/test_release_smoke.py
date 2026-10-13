"""`scripts/release_smoke.py` reports why a command failed and waits for a lagging index (#1526)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load()
PIP = Path("/venv/bin/pip")
REQ = "keel-workflow==9.9.9"
NOT_FOUND = f"ERROR: No matching distribution found for {REQ}"
SATISFIES = (
    f"ERROR: Could not find a version that satisfies the requirement {REQ} (from versions: 1.0)"
)


def _fail(stderr: str, stdout: str = "") -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(1, ["pip"], output=stdout, stderr=stderr)


def _child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class RunReportsFailures(unittest.TestCase):
    def _failing(self, code: str) -> tuple[str, subprocess.CalledProcessError]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(subprocess.CalledProcessError) as c:
            smoke._run(_child(code))
        return err.getvalue(), c.exception

    def test_both_streams_are_printed_and_not_just_the_echoed_argv(self):
        # The markers are built at run time, so they are not in the argv text.
        code = "import sys; print('O'*3+'UT'); print('E'*3+'RR', file=sys.stderr); sys.exit(3)"
        text, _ = self._failing(code)
        self.assertIn("exit 3", text)
        self.assertIn("--- stdout ---\nOOOUT", text)
        self.assertIn("--- stderr ---\nEEERR", text)

    def test_empty_stdout_prints_no_stdout_section(self):
        text, _ = self._failing("import sys; print('E'*3+'RR', file=sys.stderr); sys.exit(1)")
        self.assertNotIn("--- stdout ---", text)
        self.assertIn("--- stderr ---\nEEERR", text)

    def test_empty_stderr_prints_no_stderr_section(self):
        text, _ = self._failing("import sys; print('O'*3+'UT'); sys.exit(1)")
        self.assertNotIn("--- stderr ---", text)
        self.assertIn("--- stdout ---\nOOOUT", text)

    def test_a_passing_command_returns_stdout(self):
        self.assertEqual(smoke._run(_child("print('hi')")).strip(), "hi")

    def test_the_real_exception_carries_the_streams_that_feed_the_not_found_check(self):
        _, exc = self._failing(f"import sys; print({NOT_FOUND!r}, file=sys.stderr); sys.exit(1)")
        self.assertTrue(smoke._is_not_found(exc, REQ))

    def test_a_real_not_found_child_is_retried_through_the_real_run(self):
        # Fails with the not-found sentence on the first call, succeeds on the second.
        calls = {"n": 0}
        real = smoke._run

        def flaky(argv, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return real(
                    _child(f"import sys; print({NOT_FOUND!r}, file=sys.stderr); sys.exit(1)")
                )
            return real(_child("pass"))

        sleeps: list[float] = []
        with mock.patch.object(smoke, "_run", flaky), contextlib.redirect_stderr(io.StringIO()):
            smoke._pip_install(PIP, [REQ], retry=True, requirement=REQ, sleep=sleeps.append)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(sleeps), 1)


class NotFoundNamesTheRequestedPackage(unittest.TestCase):
    def test_both_pip_sentences_for_the_requested_package_match(self):
        self.assertTrue(smoke._is_not_found(_fail(NOT_FOUND), REQ))
        self.assertTrue(smoke._is_not_found(_fail("", stdout=SATISFIES), REQ))

    def test_matching_ignores_case_and_dash_underscore_differences(self):
        msg = "ERROR: No matching distribution found for Keel_Workflow==9.9.9"
        self.assertTrue(smoke._is_not_found(_fail(msg), REQ))
        self.assertTrue(smoke._is_not_found(_fail(NOT_FOUND), "KEEL_WORKFLOW==9.9.9"))

    def test_a_missing_dependency_is_not_a_lagging_index(self):
        msg = "ERROR: No matching distribution found for some-dependency>=2"
        self.assertFalse(smoke._is_not_found(_fail(msg), REQ))

    def test_pips_two_line_missing_dependency_output_names_ours_only_as_the_parent(self):
        msg = (
            "INFO: pip is looking at multiple versions of keel-workflow to determine\n"
            "ERROR: Could not find a version that satisfies the requirement some-dep "
            f"(from {REQ}) (from versions: none)\n"
            f"ERROR: No matching distribution found for some-dep (from {REQ})\n"
        )
        self.assertFalse(smoke._is_not_found(_fail(msg), REQ))

    def test_a_requires_python_exclusion_of_our_own_version_is_permanent(self):
        msg = (
            "ERROR: Ignored the following versions that require a different python "
            "version: 9.9.9 Requires-Python >=4.0\n"
            f"ERROR: Could not find a version that satisfies the requirement {REQ} "
            "(from versions: 1.0)\n"
            f"ERROR: No matching distribution found for {REQ}\n"
        )
        self.assertFalse(smoke._is_not_found(_fail(msg), REQ))

    def test_a_bare_requires_python_mention_is_permanent(self):
        msg = f"Requires-Python >=4.0\n{NOT_FOUND}"
        self.assertFalse(smoke._is_not_found(_fail(msg), REQ))

    def test_a_blank_requirement_never_matches(self):
        self.assertFalse(smoke._is_not_found(_fail(NOT_FOUND), ""))
        self.assertFalse(smoke._is_not_found(_fail(NOT_FOUND), "   "))
        self.assertFalse(
            smoke._is_not_found(_fail("ERROR: No matching distribution found for "), "")
        )

    def test_the_name_must_be_separated_from_the_sentence(self):
        msg = "ERROR: No matching distribution found forkeel-workflow==9.9.9"
        self.assertFalse(smoke._is_not_found(_fail(msg), REQ))

    def test_a_longer_name_sharing_the_prefix_does_not_match(self):
        msg = "ERROR: No matching distribution found for keel-workflow-extra==1"
        self.assertFalse(smoke._is_not_found(_fail(msg), REQ))

    def test_a_longer_name_ending_in_ours_does_not_match(self):
        msg = "ERROR: No matching distribution found for not-keel-workflow==1"
        self.assertFalse(smoke._is_not_found(_fail(msg), REQ))

    def test_unrelated_output_does_not_match(self):
        self.assertFalse(smoke._is_not_found(_fail("ERROR: Hash mismatch"), REQ))
        self.assertFalse(smoke._is_not_found(_fail(""), REQ))


class PipInstallRetry(unittest.TestCase):
    def _install(self, side_effect, **kw):
        sleeps: list[float] = []
        err = io.StringIO()
        kw.setdefault("requirement", REQ)
        with (
            mock.patch.object(smoke, "_run", side_effect=side_effect) as run,
            contextlib.redirect_stderr(err),
        ):
            try:
                smoke._pip_install(PIP, [REQ], sleep=sleeps.append, **kw)
            finally:
                self.run_mock = run
                self.sleeps = sleeps
                self.stderr = err.getvalue()

    def test_the_defaults_are_the_numbers_the_docs_state(self):
        self.assertEqual(smoke.INSTALL_ATTEMPTS, 6)
        self.assertEqual(smoke.INSTALL_INTERVAL_SECONDS, 20)

    def test_immediate_success_makes_one_call_and_never_sleeps(self):
        self._install([""], retry=True)
        self.assertEqual(self.run_mock.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_not_found_then_success_retries_and_succeeds(self):
        self._install([_fail(NOT_FOUND), _fail(NOT_FOUND), ""], retry=True, interval=7)
        self.assertEqual(self.run_mock.call_count, 3)
        self.assertEqual(self.sleeps, [7, 7])
        self.assertIn("--no-cache-dir", self.run_mock.call_args[0][0])

    def test_the_retry_notice_goes_to_stderr(self):
        self._install([_fail(NOT_FOUND), ""], retry=True, interval=7)
        self.assertIn("attempt 1/6", self.stderr)
        self.assertIn("retrying in 7s", self.stderr)

    def test_could_not_find_a_version_wording_is_also_retried(self):
        self._install([_fail("", stdout=SATISFIES), ""], retry=True)
        self.assertEqual(self.run_mock.call_count, 2)

    def test_another_failure_is_not_retried(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self._install([_fail("ERROR: Hash mismatch")], retry=True)
        self.assertEqual(self.run_mock.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_a_not_found_for_another_package_is_not_retried(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self._install([_fail("ERROR: No matching distribution found for dep>=2")], retry=True)
        self.assertEqual(self.run_mock.call_count, 1)

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


class _StopHere(Exception):
    """Raised by the stub right after the install, to end smoke() early."""


class SmokeRoutesTheInstall(unittest.TestCase):
    def _install_kwargs(self, argv):
        args = smoke.build_parser().parse_args(argv)
        with (
            mock.patch.object(smoke, "_run", return_value="0"),
            mock.patch.object(smoke, "_pip_install", side_effect=_StopHere) as install,
            self.assertRaises(_StopHere),
        ):
            smoke.smoke(args)
        install.assert_called_once()
        return install.call_args.kwargs

    def test_a_requirement_install_goes_through_the_retrying_path(self):
        kw = self._install_kwargs(["--requirement", "p==1"])
        self.assertIs(kw["retry"], True)
        self.assertEqual(kw["requirement"], "p==1")

    def test_a_wheel_install_does_not(self):
        self.assertIs(self._install_kwargs(["--wheel", "p.whl"])["retry"], False)


if __name__ == "__main__":
    unittest.main()
