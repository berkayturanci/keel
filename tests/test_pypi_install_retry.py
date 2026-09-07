"""The retrying install, executed against a stub installer (#1111).

Cutting `v1.21.0`, `publish.yml`'s *Verify the published release* job did this::

    == waiting for PyPI to serve both distributions of 1.21.0 ==
    both distributions listed on attempt 1
    == the published artifacts, against PyPI's own digests ==
      pypi declares / bytes hash to:  0573258…   (wheel, match)
      pypi declares / bytes hash to:  629b823…   (sdist,  match)
    == clean venv: install, keel version ==
    ERROR: Could not find a version that satisfies keel-workflow==1.21.0
      (from versions: …, 1.19.3, 1.20.0)

Twenty-five seconds after PyPI listed both distributions and vouched for both
digests, `pip` could not see the version. The wait polls the **JSON API**; the
install resolves through the **simple index**, which is a different cache and
lags. So the five-minute `PYPI_WAIT_*` budget was spent watching a surface that
was already ready, the surface that was not ready got one attempt, the job went
red, and `release-broken: v1.21.0` (#1110) was filed against a release that a
plain re-run then verified with nothing changed.

`.github/scripts/pip-install-with-retry.sh` gives the budget to the install
instead. A release can only be cut once, so the workflow itself cannot be
exercised here — but the shell it runs can be, and that is the whole of what
changed. Every test below runs the real script under real `bash`, against a stub
installer that fails the way the real one failed and then succeeds, and against
a stub `sleep` that records the wait instead of taking it. So both directions are
asserted rather than argued: that a miss is retried, and that a budget genuinely
spent still fails.

Hermetic and instant: no network, no PyPI, no pip, and no wall-clock sleeping —
the recorded sleeps are what proves the interval was honoured, which a clock
could only have proved by paying for it.

`publish.yml` runs on `ubuntu-latest` and nowhere else, so a Windows runner has
nothing to say about the shell in it; there Git Bash would be handed Windows
paths for the stubs, a failure of the harness rather than of the thing under
test. `TheScriptIsReadableWithoutAShell` reads the file as text and runs
everywhere.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "pip-install-with-retry.sh"

#: The requirement under test. A version that does not exist, so a test that
#: somehow escaped the stub would fail rather than quietly reach the real index.
REQUIREMENT = "keel-workflow==9.9.9"

POSIX_SHELL = sys.platform != "win32" and shutil.which("bash") is not None

#: `pip`'s own words on the day it happened. Kept verbatim so the assertion that
#: the installer's output survives into the log is about the text a maintainer
#: reading a `release-broken` issue would actually be looking for.
PIP_MISS = (
    f"ERROR: Could not find a version that satisfies the requirement {REQUIREMENT} "
    "(from versions: 1.19.3, 1.20.0)"
)

STUB_INSTALLER = f"""#!/usr/bin/env bash
# Fails its first $FAILURES calls the way pip failed cutting v1.21.0, then
# succeeds. Records every call so the harness can count attempts rather than
# infer them from timing.
set -u
calls="$(cat "$COUNTER" 2>/dev/null || echo 0)"
calls=$((calls + 1))
printf '%s\\n' "$calls" > "$COUNTER"
printf '%s\\n' "$*" >> "$ARGV"
if [ "$calls" -le "$FAILURES" ]; then
  echo "{PIP_MISS}"
  echo "ERROR: No matching distribution found for {REQUIREMENT}"
  exit 1
fi
echo "Successfully installed {REQUIREMENT.replace("==", "-")}"
exit 0
"""

STUB_SLEEP = """#!/usr/bin/env bash
# Records the wait instead of taking it. The script under test is bounded in
# attempts *and* in the interval between them; recording is how the interval can
# be asserted without a test that pays for it.
set -u
printf '%s\\n' "$1" >> "$SLEEPS"
exit 0
"""


class Run:
    """One invocation of the script, with what the stubs saw."""

    def __init__(self, completed, calls: int, argv: list[str], sleeps: list[str]):
        self.completed = completed
        self.calls = calls
        self.argv = argv
        self.sleeps = sleeps

    @property
    def code(self) -> int:
        return self.completed.returncode

    @property
    def output(self) -> str:
        return self.completed.stdout + self.completed.stderr


@unittest.skipUnless(POSIX_SHELL, "needs bash on a POSIX platform")
class AgainstAStubInstaller(unittest.TestCase):
    """Runs the real script against scripted stubs. No tests in here."""

    def run_install(
        self,
        *,
        failures: int = 0,
        attempts: str | None = None,
        seconds: str = "15",
        installer: str | None = None,
        requirement: str | None = REQUIREMENT,
    ) -> Run:
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        binaries = workdir / "bin"
        binaries.mkdir()

        counter, argv, sleeps = workdir / "calls", workdir / "argv", workdir / "sleeps"
        stub = binaries / "stub-pip"
        stub.write_text(STUB_INSTALLER, encoding="utf-8")
        stub.chmod(0o755)
        # Prepended, not replacing: `date` and `seq` still resolve from the real
        # PATH, so only the wait is stubbed out.
        nap = binaries / "sleep"
        nap.write_text(STUB_SLEEP, encoding="utf-8")
        nap.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ.get('PATH', '')}",
            "COUNTER": str(counter),
            "ARGV": str(argv),
            "SLEEPS": str(sleeps),
            "FAILURES": str(failures),
            "PYPI_WAIT_SECONDS": seconds,
            "INSTALLER": str(stub) if installer is None else installer,
        }
        if requirement is not None:
            env["REQUIREMENT"] = requirement
        if attempts is not None:
            env["PYPI_WAIT_ATTEMPTS"] = attempts

        completed = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return Run(
            completed,
            int(counter.read_text(encoding="utf-8").strip()) if counter.exists() else 0,
            _lines(argv),
            _lines(sleeps),
        )


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


class AnIndexThatIsReadyIsInstalledFromOnce(AgainstAStubInstaller):
    def test_it_stops_at_the_attempt_that_resolves(self):
        run = self.run_install(failures=0, attempts="20")

        self.assertEqual(run.code, 0, run.output)
        self.assertEqual(run.calls, 1)
        self.assertEqual(run.sleeps, [], "it waited after an install that had succeeded")
        self.assertIn("installed keel-workflow==9.9.9 on attempt 1/20", run.completed.stdout)

    def test_the_requirement_reaches_the_installer_unmangled(self):
        """Vacuity guard for every count above: the stub is really being run."""
        run = self.run_install(failures=0, attempts="20")

        self.assertEqual(
            run.argv, [f"install --no-cache-dir --disable-pip-version-check {REQUIREMENT}"]
        )

    def test_every_attempt_bypasses_pips_http_cache(self):
        """Without `--no-cache-dir` the retry asks local disk, not PyPI.

        PyPI serves `/simple/<project>/` with `cache-control: max-age=600,
        public`, and pip's HTTP cache honours a fresh response without
        revalidating. Attempt 1 during index lag therefore records the version
        list that is *missing* the release, and every attempt inside the next
        ten minutes reads it back off disk. The whole budget is 285 s, so all
        twenty attempts would have sat inside that window — the loop would have
        retried its own answer.
        """
        run = self.run_install(failures=2, attempts="20")

        self.assertEqual(len(run.argv), 3)
        for call in run.argv:
            self.assertIn("--no-cache-dir", call)


class AnIndexThatLagsIsWaitedOutRatherThanFailed(AgainstAStubInstaller):
    """The v1.21.0 shape: the version is there, the index has not caught up."""

    def test_a_miss_is_retried_and_the_install_still_succeeds(self):
        run = self.run_install(failures=2, attempts="20")

        self.assertEqual(run.code, 0, run.output)
        self.assertEqual(run.calls, 3)
        self.assertIn("installed keel-workflow==9.9.9 on attempt 3/20", run.completed.stdout)

    def test_it_waits_the_configured_interval_between_attempts(self):
        """Recorded, not timed. Two misses buy two waits, and not a third."""
        run = self.run_install(failures=2, attempts="20", seconds="15")

        self.assertEqual(run.sleeps, ["15", "15"])

    def test_each_miss_says_which_attempt_it_was(self):
        run = self.run_install(failures=2, attempts="20")

        self.assertIn("(attempt 1/20); retrying in 15s", run.completed.stdout)
        self.assertIn("(attempt 2/20); retrying in 15s", run.completed.stdout)

    def test_a_zero_padded_attempt_count_is_read_as_decimal_and_not_octal(self):
        """`08` is a valid budget and an invalid octal literal."""
        run = self.run_install(failures=99, attempts="08")

        self.assertEqual(run.code, 1, run.output)
        self.assertEqual(run.calls, 8)


class ASpentBudgetStillFailsTheJob(AgainstAStubInstaller):
    """The half that must not be traded away: this is a wait, not a softener."""

    def test_it_fails_once_the_attempts_are_used_up(self):
        run = self.run_install(failures=99, attempts="3")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.calls, 3, "the budget was not spent in full")
        self.assertEqual(run.sleeps, ["15", "15"], "it waited after the last attempt")

    def test_the_failure_is_loud_and_names_what_it_waited_for(self):
        run = self.run_install(failures=99, attempts="3")

        self.assertIn("::error title=PyPI never served an installable", run.completed.stdout)
        self.assertIn(REQUIREMENT, run.completed.stdout)
        self.assertIn("in 3 attempts over", run.completed.stdout)

    def test_the_installers_own_words_survive_into_the_log(self):
        """The last 100 lines of this go into the `release-broken` issue.

        A wheel that really is broken retries for the whole budget and then has
        to be diagnosable from what the installer said, not from this script's
        summary of it.
        """
        run = self.run_install(failures=99, attempts="2")

        self.assertIn("Could not find a version that satisfies", run.completed.stdout)

    def test_a_one_attempt_budget_calls_once_and_never_waits(self):
        """The boundary the loop's `attempt -lt attempts` guard exists for."""
        run = self.run_install(failures=99, attempts="1")

        self.assertEqual(run.code, 1)
        self.assertEqual(run.calls, 1)
        self.assertEqual(run.sleeps, [])


class AMistakeInTheCallIsDiagnosedAndNotPolled(AgainstAStubInstaller):
    """Exit 2, and before the loop.

    A budget spent waiting on something nobody asked for, ending in an error that
    blames PyPI, is the second way this step can lie about a healthy release.
    """

    def assertRefused(self, run: Run, phrase: str) -> None:
        self.assertEqual(run.code, 2, run.output)
        self.assertEqual(run.calls, 0, "it started installing before checking its inputs")
        self.assertIn("::error title=pip-install-with-retry.sh is misconfigured::", run.output)
        self.assertIn(phrase, run.output)

    def test_no_requirement_is_refused(self):
        self.assertRefused(self.run_install(requirement=None), "REQUIREMENT is empty")

    def test_no_installer_is_refused(self):
        self.assertRefused(self.run_install(installer=""), "INSTALLER is empty")

    def test_an_installer_that_cannot_run_is_not_reported_as_a_slow_index(self):
        """Undiagnosed it exits 127 from every attempt, which reads as a miss."""
        run = self.run_install(installer="/nonexistent/pip", attempts="20")

        self.assertRefused(run, "is not an executable this runner can find")
        self.assertEqual(run.sleeps, [], "it spent the budget on a missing installer")

    def test_a_budget_that_is_not_a_number_is_refused(self):
        self.assertRefused(
            self.run_install(attempts="lots"),
            "PYPI_WAIT_ATTEMPTS must be a whole number, not 'lots'",
        )

    def test_an_interval_that_is_not_a_number_is_refused(self):
        self.assertRefused(
            self.run_install(seconds="a while"),
            "PYPI_WAIT_SECONDS must be a whole number, not 'a while'",
        )

    def test_a_budget_of_no_attempts_is_refused_by_name(self):
        """It would fail anyway — with a message naming PyPI for a typo'd knob."""
        self.assertRefused(
            self.run_install(attempts="0"),
            "PYPI_WAIT_ATTEMPTS must be at least 1",
        )


class TheScriptIsReadableWithoutAShell(unittest.TestCase):
    """Static, so it runs on every leg of the matrix including Windows."""

    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_it_exists_and_is_executable(self):
        """`publish.yml` invokes it by path, the way the Makefile invokes its own."""
        self.assertTrue(SCRIPT.is_file())
        if sys.platform != "win32":
            self.assertTrue(os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable")

    def test_it_fails_on_the_first_error_and_on_an_unset_variable(self):
        self.assertIn("set -euo pipefail", self.text)

    def test_it_reads_the_budget_the_verify_job_sets(self):
        """Not a budget of its own: one edit has to move both waits (#1111)."""
        self.assertIn("PYPI_WAIT_ATTEMPTS", self.text)
        self.assertIn("PYPI_WAIT_SECONDS", self.text)

    def environment_names(self) -> set[str]:
        """Every `${NAME}` the script expands, which is its whole input surface."""
        return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", self.text))

    def test_it_reads_the_four_names_it_documents_and_no_others(self):
        self.assertEqual(
            self.environment_names(),
            {"INSTALLER", "REQUIREMENT", "PYPI_WAIT_ATTEMPTS", "PYPI_WAIT_SECONDS"},
        )

    def test_no_name_it_reads_lands_in_pips_own_configuration_space(self):
        """pip reads every `PIP_<OPTION>` in the environment as one of its flags,
        so a knob named there would be handed back to the thing it names."""
        self.assertEqual(
            sorted(name for name in self.environment_names() if name.startswith("PIP_")),
            [],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
