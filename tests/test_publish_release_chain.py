"""A release is exactly one write to `main`, and the formula is measured, not copied.

This file replaces `test_publish_formula_followup.py`, which asserted the
mechanism that is now gone. That mechanism existed because `Formula/keel.rb`
named a url and a sha256 that cannot both be correct at once: the url is bumped
in the release commit, and the digest belongs to the archive GitHub builds *from
the tag* — created from that very commit. So the file was stale on every release
by construction, and every release therefore needed a **second write to `main`**
afterwards.

That second write was built up over five issues (#805, #842, #982, #984, #986)
and still ended in a pull request a human had to merge, because the evidence gate
has no bot exemption. When nobody merged it, the tap refused every sync for a day
(#981).

#1023 removed the requirement rather than the latest symptom, as #990 proposed:
nothing in this repository names a digest, so nothing here can be stale. The
formula is rendered during the release from the archive the tag actually
produced, verified against it, and attached to the GitHub Release.

These tests pin *the absence* of the old mechanism and the shape of the new one.
They assert over the **code** in each step, with comment lines removed first —
the workflow's prose discusses the pull request it no longer opens, and a plain
grep would match that discussion and report the removed behaviour as still
present.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"

#: The step that produces the formula, selected by what it *does* — it is the one
#: that reads the template — rather than by its name, which has changed twice.
TEMPLATE = "packaging/homebrew/keel.rb.template"

#: The install that waits for the index it resolves through, rather than for the
#: JSON API it does not (#1111). Its own shell runs in test_pypi_install_retry.py.
RETRY_SCRIPT = ".github/scripts/pip-install-with-retry.sh"

#: Every way the retired design wrote to this repository after the tag. Each was
#: a real step at some point in #842/#984/#986.
SECOND_WRITE = (
    "gh pr create",
    "gh pr merge",
    "git commit",
    "git push",
    "git checkout -b",
    "HEAD:main",
    "HEAD:refs/heads/",
)


def code_of(run: str) -> str:
    """`run` with whole-line `#` comments dropped.

    Deliberately line-based rather than a strip to end-of-line: a `#` can appear
    inside a quoted string, and mangling those would make the assertions below
    depend on quoting rather than on behaviour.
    """
    return "\n".join(line for line in run.splitlines() if not line.strip().startswith("#"))


class TheWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.jobs = cls.workflow["jobs"]
        cls.runs = {
            (job, step.get("name", "<unnamed>")): code_of(step["run"])
            for job, body in cls.jobs.items()
            for step in body["steps"]
            if step.get("run")
        }


class NothingAfterTheTagWritesToMain(TheWorkflow):
    def test_there_are_run_bodies_to_examine(self):
        """Vacuity: an empty mapping satisfies every `assertNotIn` below."""
        self.assertGreater(len(self.runs), 5)
        self.assertTrue(any("sha256sum" in code for code in self.runs.values()))

    def test_the_comment_stripper_leaves_the_scripts_behind(self):
        for where, code in self.runs.items():
            with self.subTest(step=where):
                self.assertEqual(
                    [line for line in code.splitlines() if line.strip().startswith("#")],
                    [],
                    "a comment line survived the stripper",
                )

    def test_no_step_pushes_commits_or_opens_a_pull_request(self):
        for where, code in self.runs.items():
            for phrase in SECOND_WRITE:
                with self.subTest(step=where, phrase=phrase):
                    self.assertNotIn(phrase, code)

    def test_no_job_may_open_a_pull_request(self):
        """The permission is the capability; removing only the call is cosmetic."""
        for job, body in self.jobs.items():
            with self.subTest(job=job):
                self.assertNotIn("pull-requests", body.get("permissions") or {})

    def test_the_release_can_still_be_created(self):
        """Vacuity for the test above: `contents: write` must survive it."""
        self.assertEqual(self.jobs["build-n-publish"]["permissions"]["contents"], "write")


class TheFormulaIsRenderedFromWhatTheTagProduced(TheWorkflow):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        steps = [
            step
            for body in cls.jobs.values()
            for step in body["steps"]
            if TEMPLATE in (step.get("run") or "")
        ]
        assert len(steps) == 1, f"expected one render step, found {len(steps)}"
        cls.step = steps[0]
        cls.code = code_of(cls.step["run"])

    def test_it_renders_the_committed_template(self):
        self.assertIn("sed", self.code)
        self.assertIn("@URL@", self.code)
        self.assertIn("@SHA256@", self.code)
        self.assertIn("@VERSION@", self.code)

    def test_the_digest_is_measured_from_the_archive_it_names(self):
        """Not read from anywhere. A copied digest is the whole defect (#805)."""
        self.assertIn("curl", self.code)
        self.assertIn("sha256sum", self.code)
        self.assertIn("archive/refs/tags/", self.code)

    def test_the_wait_for_the_archive_is_bounded(self):
        """An unbounded loop hangs the release; an unbounded absence is silent."""
        env = self.step.get("env") or {}
        self.assertIn("ARCHIVE_WAIT_ATTEMPTS", env)
        self.assertIn("ARCHIVE_WAIT_SECONDS", env)
        self.assertGreater(int(env["ARCHIVE_WAIT_ATTEMPTS"]), 1)

    def test_an_unfetchable_archive_fails_the_step(self):
        """`sed` over a missing digest renders a formula nobody can install."""
        self.assertIn("::error title=No tag archive::", self.code)
        self.assertIn("exit 1", self.code)

    def test_an_unrendered_placeholder_fails_the_step(self):
        """`sed` reports success whether or not it substituted anything."""
        self.assertIn("::error title=Unrendered placeholder::", self.code)

    def test_the_url_must_be_this_projects_archive_for_this_tag(self):
        """#990's load-bearing guard: a digest that matches the wrong tarball
        is a correct description of the wrong thing."""
        self.assertIn("::error title=Wrong url::", self.code)
        self.assertIn("::error title=No digest::", self.code)

    def test_the_formula_is_covered_by_the_release_checksums(self):
        self.assertIn("sha256sum keel.rb >> SHA256SUMS", self.code)

    def test_the_release_carries_the_formula(self):
        """The asset is the tap's only source now; a release without it is inert."""
        release = [
            step
            for body in self.jobs.values()
            for step in body["steps"]
            if "action-gh-release" in (step.get("uses") or "")
        ]
        self.assertEqual(len(release), 1)
        files = release[0]["with"]["files"].split()
        self.assertIn("release/keel.rb", files)

    def test_it_runs_before_anything_irreversible(self):
        """Position is load-bearing, and invisible in a diff that only moves it.

        PyPI files are immutable and the upload uses `skip-existing: true`, so a
        render that failed *after* the upload would leave a published version
        whose formula can only be repaired by cutting another release — the
        fix-forward shape this whole change exists to end. Nothing in the render
        needs the upload: its inputs are the tag, the repository, the committed
        template and `release/SHA256SUMS`.
        """
        steps = self.jobs["build-n-publish"]["steps"]
        rendered = next(i for i, s in enumerate(steps) if TEMPLATE in (s.get("run") or ""))
        for uses in ("gh-action-pypi-publish", "action-gh-release"):
            with self.subTest(after=uses):
                consumer = next(i for i, s in enumerate(steps) if uses in (s.get("uses") or ""))
                self.assertLess(rendered, consumer)


class TheVerifyJobWaitsOnTheSurfaceItAsserts(TheWorkflow):
    """The wait and the install read different surfaces of PyPI (#1111).

    The wait polls the JSON API; `pip` resolves through the simple index, which
    is a different cache and lags behind it. Cutting v1.21.0 both distributions
    were listed on attempt 1 and both digests matched, and the install failed
    twenty-five seconds later against a version list ending at 1.20.0 — so the
    five-minute budget had been spent on a surface that was already ready and
    the surface that was not ready got one attempt. The job filed
    `release-broken: v1.21.0` (#1110) against a release a plain re-run then
    verified unchanged.

    These pin the shape that fixes it and the two things it must not cost: the
    digest cross-check against the JSON API, which is useful and was never the
    broken part, and a failure that is still loud once the budget is spent.
    `tests/test_pypi_install_retry.py` runs the wrapper's own shell.
    """

    #: A direct resolve of the published requirement — what the wrapper replaced.
    DIRECT_INSTALL = re.compile(r'\binstall\b[^\n]*"\$req"')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job = cls.jobs["verify"]
        steps = [step for step in cls.job["steps"] if "verify-venv" in (step.get("run") or "")]
        assert len(steps) == 1, f"expected one install step, found {len(steps)}"
        cls.step = steps[0]
        cls.code = code_of(cls.step["run"])

    def test_the_install_goes_through_the_retrying_wrapper(self):
        self.assertIn(RETRY_SCRIPT, self.code)
        self.assertTrue(
            (REPO_ROOT / RETRY_SCRIPT).is_file(),
            f"the step calls {RETRY_SCRIPT}, which is not in the tree",
        )

    def test_the_wrapper_is_told_which_pip_and_which_requirement(self):
        self.assertIn('INSTALLER="${RUNNER_TEMP}/verify-venv/bin/pip"', self.code)
        self.assertIn('REQUIREMENT="$req"', self.code)

    def test_nothing_in_the_job_resolves_the_requirement_without_it(self):
        """A reintroduced bare install is the whole defect, back again."""
        for (job, name), code in self.runs.items():
            if job != "verify":
                continue
            for line in code.splitlines():
                with self.subTest(step=name, line=line.strip()):
                    self.assertIsNone(self.DIRECT_INSTALL.search(line))

    def test_the_pattern_would_have_caught_the_line_that_was_there(self):
        """Vacuity: the sweep above passes on a job it cannot read."""
        self.assertIsNotNone(
            self.DIRECT_INSTALL.search(
                '"${RUNNER_TEMP}/verify-venv/bin/pip" install --no-cache-dir "$req"'
            )
        )

    def test_both_waits_read_one_pair_of_variables(self):
        """Named in the job, so one edit moves the poll and the install together."""
        env = self.job["env"]
        self.assertIn("PYPI_WAIT_ATTEMPTS", env)
        self.assertIn("PYPI_WAIT_SECONDS", env)
        self.assertGreater(int(env["PYPI_WAIT_ATTEMPTS"]), 1)
        self.assertGreater(int(env["PYPI_WAIT_SECONDS"]), 0)
        self.assertIn("PYPI_WAIT_ATTEMPTS", (REPO_ROOT / RETRY_SCRIPT).read_text("utf-8"))
        self.assertNotIn(
            "PYPI_WAIT_ATTEMPTS=",
            self.code,
            "the step gives the install a budget of its own",
        )

    def test_the_json_wait_still_holds_out_for_both_distributions(self):
        """Kept: it is what finds the digests, and half a release passes without it."""
        self.assertIn("bdist_wheel,sdist", self.code)
        self.assertIn("::error title=PyPI never served both distributions::", self.code)

    def test_the_digest_cross_check_against_pypi_survives(self):
        """The genuinely useful half, and not the part that was broken."""
        self.assertIn("digests.sha256", self.code)
        self.assertIn("::error title=Corrupt artifact::", self.code)
        self.assertIn("::error title=Nothing compared::", self.code)

    def test_the_release_broken_report_can_only_run_after_a_failure(self):
        """So the budget is spent before anything is filed, by construction."""
        filing = [
            step
            for step in self.job["steps"]
            if "gh issue create" in code_of(step.get("run") or "")
        ]
        self.assertEqual(len(filing), 1)
        self.assertEqual(filing[0]["if"], "failure()")

    def test_the_report_comes_after_the_install_it_reports_on(self):
        steps = self.job["steps"]
        installed = steps.index(self.step)
        filed = next(
            i for i, s in enumerate(steps) if "gh issue create" in code_of(s.get("run") or "")
        )
        self.assertLess(installed, filed)


class TheTapReportCannotFailTheRelease(TheWorkflow):
    """What is left of `publish-formula`: a report, holding no write anywhere.

    A tap a few minutes behind still installs — it pulls on a schedule by design
    (#774) — so failing the release on it would make every release briefly red
    for something nobody should act on.
    """

    def test_the_job_exists_and_only_reads(self):
        job = self.jobs["tap-status"]
        self.assertEqual(job["permissions"], {"contents": "read"})
        self.assertEqual(job["needs"], "build-n-publish")

    def test_it_holds_no_token(self):
        for step in self.jobs["tap-status"]["steps"]:
            with self.subTest(step=step.get("name")):
                self.assertEqual(step.get("env") or {}, {})

    def test_every_path_through_it_succeeds(self):
        code = code_of(self.jobs["tap-status"]["steps"][0]["run"])
        self.assertNotIn("exit 1", code)
        self.assertIn("::notice::", code)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
