"""Every job carries a ceiling, and the release job's ceiling is above its own waits.

`publish.yml` contained **zero** `timeout-minutes` keys, and so did every other
workflow in this repository: 16 jobs, none bounded, all inheriting GitHub's
six-hour default. The sibling repository bounds all 16 of its own and wraps every
network call besides (#1116).

The exposure is small in ordinary running and unbounded in the case that matters.
A release is the workflow nobody opens until it is late; a hung publish job holds
the runner, delays the GitHub Release and leaves the tap without a formula, while
reporting nothing — because a hang is not a failure.

Three things are pinned here, in increasing order of what they know:

* every job in every workflow has a ceiling — the invariant that was missing;
* every network call in `publish.yml` carries a bound of its own, because a job
  ceiling cannot say *what* stalled and a per-call bound can;
* the verify job's ceiling exceeds the sum of its own bounds, **recomputed from
  the file**. That job is cancelled when it hits its ceiling, and a cancelled job
  does not run the `if: failure()` step that files `release-broken` — so a
  ceiling below the waits would silence the report for a release that is already
  public. The sum is computed rather than restated so a wait added later cannot
  quietly outgrow it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PUBLISH = WORKFLOWS / "publish.yml"

#: What each network tool needs before this file calls it bounded.
#:
#: `curl` must carry **both**: `--connect-timeout` bounds reaching the peer and
#: leaves a peer that connects and then goes quiet running forever.
#:
#: `pip` and `gh` get an empty tuple, meaning the only acceptable bound is a
#: `timeout` wrapper. pip's own `--timeout` is its *socket* timeout — a bound on
#: one quiet read, not on the call — and `gh` has no request timeout at all,
#: neither a flag nor an environment variable.
REQUIRED_FLAGS = {
    "curl": ("--connect-timeout", "--max-time"),
    "pip": (),
    "gh": (),
}

#: Commands that take another command as their argument, so the *next* token is
#: what is really being run.
WRAPPERS = frozenset({"timeout", "env", "command", "exec", "nice", "stdbuf"})

#: Tokens that carry a duration this file can add up.
BOUND_TOKENS = frozenset({"timeout", "sleep", "--max-time", "--connect-timeout"})


def code_of(run: str) -> str:
    """`run` with whole-line `#` comments dropped and continuations joined.

    Comments are removed line by line rather than to end of line: a `#` inside a
    quoted string is not a comment, and mangling those would make these
    assertions depend on quoting rather than on behaviour.

    Backslash continuations are joined because a command is one command however
    it is wrapped. Without this, `timeout 1500 env \\` and the script name on the
    next line read as two chunks, and the scanner reports the wrapped call as
    unwrapped — which is how this function earned its own test.
    """
    kept = "\n".join(line for line in run.splitlines() if not line.strip().startswith("#"))
    return re.sub(r"\s*\\\n\s*", " ", kept)


def workflows() -> list[Path]:
    found = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert found, "no workflows found; this test would pass by reading nothing"
    return found


def _int(token: str, env: dict[str, str]) -> int | None:
    """A literal, or an env var this job defines. `"${VAR}"` and `$VAR` both."""
    token = token.strip().strip('"').strip("'")
    if token.isdigit():
        return int(token)
    name = token.lstrip("$").strip("{}")
    value = env.get(name)
    return int(value) if value is not None and str(value).isdigit() else None


def bounded_seconds(code: str, env: dict[str, str]) -> int:
    """Every bound in `code`, in seconds, each multiplied by its loop's trip count.

    A `for … in $(seq 1 "$N")` opens a loop worth `N` trips; the `do`/`done`
    depth decides where it closes. Nested loops multiply. A trip count this
    function cannot resolve raises rather than being silently read as one — an
    unreadable budget must not shrink the sum that a ceiling is checked against.
    """
    total = 0
    trips: list[int] = []
    for raw in code.splitlines():
        line = raw.strip()
        seq = re.search(r"for\s+\w+\s+in\s+\$\(seq\s+1\s+(\S+?)\)", line)
        if seq:
            count = _int(seq.group(1), env)
            if count is None:
                raise AssertionError(f"unreadable loop count in: {line}")
            trips.append(count)
        elif re.match(r"while\s+read\b", line):
            # A `while read` consumes a finite input, so it is not the unbounded
            # form — but the sum still needs its trip count. The only honest
            # source is the guard the step itself enforces on the count
            # afterwards (`[ "$checked" -ne 2 ]`), which is what makes the loop
            # finite in the first place. Without such a guard the loop is
            # unreadable and refused, rather than counted as one trip.
            pinned = re.search(r"-ne\s+(\d+)\s*\]", code)
            if not pinned:
                raise AssertionError(f"`while read` with no pinned count: {line}")
            trips.append(int(pinned.group(1)))
        elif re.match(r"(while|until)\b", line):
            raise AssertionError(f"unbounded loop form in publish.yml: {line}")
        multiplier = 1
        for t in trips:
            multiplier *= t
        # `sums="$(timeout 60 gh …` hides the wrapper inside one token. Under-
        # counting is the unsafe direction for a ceiling check, so the assignment
        # and the substitution are cleared away before the tokens are read.
        tokens = line.replace("=", " ").replace('"$(', " ").replace("$(", " ").split()
        for i, tok in enumerate(tokens):
            if tok in BOUND_TOKENS and i + 1 < len(tokens):
                seconds = _int(tokens[i + 1], env)
                if seconds is not None:
                    total += seconds * multiplier
        # `done < <(jq …)` closes a loop as surely as a bare `done`. Comparing
        # for equality left the artifact loop open, so every bound after it was
        # multiplied by two and the sum read 5280s instead of 2820s.
        if re.match(r"done\b", line) and trips:
            trips.pop()
    return total


def network_calls(code: str) -> list[list[str]]:
    """Each command in `code` whose head is a tool in REQUIRED_FLAGS, wrappers kept."""
    calls = []
    for chunk in re.split(r"[;&|]{1,2}|\n", code):
        # `sums="$(timeout 60 gh release view …` puts the assignment, the command
        # substitution and the wrapper in one token. Opening a substitution is a
        # new command, so the prefix up to it is dropped rather than parsed.
        chunk = re.sub(r'^\s*\w+="?\$\(', "", chunk)
        tokens = chunk.split()
        if not tokens:
            continue
        head = 0
        while head < len(tokens) and (
            tokens[head] in WRAPPERS
            or tokens[head].isdigit()
            or "=" in tokens[head]
            or tokens[head].startswith("-")
        ):
            head += 1
        if head >= len(tokens):
            continue
        name = Path(tokens[head].strip('"$(){}')).name
        if name.startswith("pip") or name.endswith("pip"):
            name = "pip"
        if name in REQUIRED_FLAGS:
            calls.append(tokens)
    return calls


class EveryJobCarriesACeiling(unittest.TestCase):
    """The invariant that was absent from all eight workflows."""

    def test_every_job_in_every_workflow(self):
        unbounded = []
        seen = 0
        for path in workflows():
            jobs = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("jobs") or {}
            for name, body in jobs.items():
                seen += 1
                if "timeout-minutes" not in body:
                    unbounded.append(f"{path.name}:{name}")
        self.assertGreaterEqual(seen, 16, "the scan found fewer jobs than exist")
        self.assertEqual(unbounded, [], f"jobs with no ceiling: {unbounded}")

    def test_no_ceiling_is_the_six_hour_default(self):
        for path in workflows():
            jobs = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("jobs") or {}
            for name, body in jobs.items():
                with self.subTest(job=f"{path.name}:{name}"):
                    self.assertIsInstance(body["timeout-minutes"], int)
                    self.assertLessEqual(
                        body["timeout-minutes"], 60, "no job here legitimately runs an hour"
                    )


class ThePublishWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))
        cls.jobs = cls.workflow["jobs"]


class EveryNetworkCallInAReleaseIsBounded(ThePublishWorkflow):
    """A job ceiling cannot name what stalled; a per-call bound can."""

    def test_each_call_carries_its_own_bound(self):
        unbounded = []
        for job, body in self.jobs.items():
            for step in body["steps"]:
                if not step.get("run"):
                    continue
                for tokens in network_calls(code_of(step["run"])):
                    name = next(
                        n for n in REQUIRED_FLAGS if n in " ".join(tokens[:4]).replace("/", " ")
                    )
                    wanted = REQUIRED_FLAGS[name]
                    if wanted:
                        missing = [f for f in wanted if f not in tokens]
                        if missing:
                            unbounded.append(f"{job}: {' '.join(tokens)[:70]} lacks {missing}")
                    elif "timeout" not in tokens:
                        unbounded.append(f"{job}: {' '.join(tokens)[:70]} has no timeout wrapper")
        self.assertEqual(unbounded, [], f"unbounded network calls: {unbounded}")


class TheVerifyCeilingIsAboveItsOwnWaits(ThePublishWorkflow):
    """Its ceiling *cancels* the job, and a cancelled job files no report.

    `release-broken` is filed by an `if: failure()` step. A ceiling below this
    job's waits would stop the job before that step, for a release that is
    already on PyPI — turning the loudest signal in the release chain into
    silence at exactly the moment it is needed.
    """

    @staticmethod
    def _spent(job: dict) -> int:
        """This job's bounds, in seconds, with step env merged over job env.

        A step may define its own budget — `ARCHIVE_WAIT_ATTEMPTS` is declared
        on the step that fetches the tag archive, not on the job — so reading
        only the job's env leaves a loop count unresolvable.
        """
        job_env = {k: str(v) for k, v in (job.get("env") or {}).items()}
        total = 0
        for step in job["steps"]:
            if not step.get("run"):
                continue
            env = {**job_env, **{k: str(v) for k, v in (step.get("env") or {}).items()}}
            total += bounded_seconds(code_of(step["run"]), env)
        return total

    def test_every_job_here_fits_under_its_own_ceiling(self):
        """Checked for all three, not only the one that motivated the rule."""
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                spent = self._spent(job)
                ceiling = job["timeout-minutes"] * 60
                self.assertLess(
                    spent,
                    ceiling,
                    f"{name} can spend {spent}s of its own bounds under a "
                    f"{ceiling}s ceiling, so a job spending them honestly is "
                    f"cancelled rather than failed",
                )

    def test_the_verify_sum_is_large_enough_to_be_worth_checking(self):
        """A scan that found nothing would pass the assertion above silently."""
        self.assertGreater(self._spent(self.jobs["verify"]), 1800)

    def test_the_budget_variables_are_what_the_sum_reads(self):
        """The sum is only meaningful if it read the job's real knobs."""
        env = self.jobs["verify"].get("env") or {}
        self.assertEqual(env["PYPI_WAIT_ATTEMPTS"], "20")
        self.assertEqual(env["PYPI_WAIT_SECONDS"], "15")

    def test_an_unreadable_loop_count_is_refused_rather_than_read_as_one(self):
        with self.assertRaises(AssertionError):
            bounded_seconds('for attempt in $(seq 1 "$MYSTERY"); do\nsleep 5\ndone', {})

    def test_a_while_loop_is_refused_outright(self):
        with self.assertRaises(AssertionError):
            bounded_seconds("while true; do\nsleep 5\ndone", {})

    def test_a_while_read_without_a_pinned_count_is_refused(self):
        """It is finite, but nothing here can say how finite."""
        with self.assertRaises(AssertionError):
            bounded_seconds("while read -r a; do\nsleep 5\ndone < <(x)", {})

    def test_a_while_read_counts_the_trips_its_guard_pins(self):
        code = 'while read -r a; do\nsleep 5\ndone < <(x)\nif [ "$n" -ne 2 ]; then\nexit 1\nfi'
        self.assertEqual(bounded_seconds(code, {}), 10)

    def test_a_bound_inside_a_command_substitution_is_counted(self):
        """Under-counting is the unsafe direction: it makes a ceiling look roomy."""
        self.assertEqual(bounded_seconds('x="$(timeout 60 gh release view)"', {}), 60)

    def test_a_loop_closed_with_a_redirect_still_closes(self):
        """`done < <(…)` is a close. Missing it multiplied the whole tail by two."""
        code = (
            "while read -r a; do\nsleep 5\ndone < <(x)\n"
            'sleep 7\nif [ "$n" -ne 2 ]; then\nexit 1\nfi'
        )
        self.assertEqual(bounded_seconds(code, {}), 5 * 2 + 7)

    def test_a_continuation_does_not_hide_a_wrapper(self):
        joined = code_of("timeout 60 env \\\n  gh issue list --state open")
        self.assertIn("timeout 60 env gh issue list", joined)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
