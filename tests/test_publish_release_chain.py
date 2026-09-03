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

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"

#: The step that produces the formula, selected by what it *does* — it is the one
#: that reads the template — rather than by its name, which has changed twice.
TEMPLATE = "packaging/homebrew/keel.rb.template"

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

    def test_it_runs_before_the_release_is_created(self):
        """A formula rendered after the upload cannot be attached to it."""
        steps = self.jobs["build-n-publish"]["steps"]
        rendered = next(i for i, s in enumerate(steps) if TEMPLATE in (s.get("run") or ""))
        released = next(
            i for i, s in enumerate(steps) if "action-gh-release" in (s.get("uses") or "")
        )
        self.assertLess(rendered, released)


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
