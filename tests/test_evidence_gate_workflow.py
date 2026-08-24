"""The evidence gate's *reported* verdict must match the verdict it computed.

#849 gave ``keel evidence-verify`` a third status and wired the workflow to
``exit 0`` for it. A job that exits 0 concludes green, so a check named for the
evidence gate reported success while zero verdicts existed — worse than the red
it replaced, because red at least said "not yet" (#928).

The library half of that lifecycle is covered by ``test_evidence.py``. Nothing
covered the half a human looks at: the check GitHub shows.

These parse the workflow and slice each ``case`` arm, then assert on **that
arm's** status and conclusion. An earlier draft asserted only that each
conclusion string appeared *somewhere* in the step, which a review showed was
no guard at all: swapping the pass and fail arms, or hardcoding every
conclusion to ``success``, left the whole suite green. Every assertion below is
per-arm for that reason.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/keel-ship.yml"
DOC = REPO / "docs/keel/evidence.md"

#: The check-run the gate publishes. This is the name that belongs in branch
#: protection, and it must differ from every job name in the file — Actions
#: names a job's own check after the job, and branch protection matches by name.
CHECK_NAME = "keel evidence (required)"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _evidence_run() -> str:
    """The shell body of the evidence job's verification step."""
    steps = _workflow()["jobs"]["evidence"]["steps"]
    step = next((s for s in steps if s.get("name") == "Verify posted ship evidence"), None)
    assert step is not None, "the evidence-verification step is gone"
    return step["run"]


def _case_arm(label: str) -> str:
    """One arm of the `case "$RC"` block, sliced on line-anchored labels.

    Anchoring to a line that is only the label plus ``)`` keeps a comment that
    happens to contain ``2)`` or ``*)`` from silently shifting the slice — the
    previous substring search could produce an empty arm and a passing test.
    """
    run = _evidence_run()
    labels = ["0)", "2)", "*)"]
    bounds = {}
    for name in labels:
        match = re.search(rf"^\s*{re.escape(name)}\s*$", run, re.MULTILINE)
        assert match, f"the case arm {name!r} is gone"
        bounds[name] = (match.start(), match.end())
    start = bounds[label][1]
    later = [s for (s, _) in (bounds[n] for n in labels) if s > bounds[label][0]]
    return run[start : min(later)] if later else run[start:]


class TestEachArmReportsItsOwnVerdict(unittest.TestCase):
    """Pass, waiting and fail must set three *different* verdicts."""

    #: (case label, STATUS, CONCLUSION or None, FALLBACK_EXIT)
    ARMS = (
        ("0)", "completed", "success", "0"),
        ("2)", "in_progress", None, "1"),
        ("*)", "completed", "failure", "1"),
    )

    def test_each_arm_sets_its_own_status_and_conclusion(self):
        for label, status, conclusion, _ in self.ARMS:
            arm = _case_arm(label)
            with self.subTest(arm=label):
                self.assertIn(f"STATUS={status}", arm)
                if conclusion is None:
                    # The waiting arm must set NO conclusion. An incomplete check
                    # blocks a merge; every conclusion GitHub accepts as "not
                    # failing" — success, skipped, neutral — satisfies a required
                    # check, which is the #928 defect by another route.
                    self.assertIn('CONCLUSION=""', arm)
                    self.assertNotRegex(arm, r"CONCLUSION=(success|neutral|skipped)")
                else:
                    self.assertIn(f"CONCLUSION={conclusion}", arm)

    def test_no_two_arms_report_the_same_verdict(self):
        """Kills a wholesale swap, and one verdict hardcoded for every state."""
        seen = []
        for label, _, _, _ in self.ARMS:
            arm = _case_arm(label)
            status = re.search(r"STATUS=(\S+)", arm)
            conclusion = re.search(r"CONCLUSION=(\S*)", arm)
            self.assertIsNotNone(status, f"the {label} arm sets no status")
            seen.append((status.group(1), (conclusion.group(1) if conclusion else "")))
        self.assertEqual(len(set(seen)), len(seen), f"two arms report the same: {seen}")

    def test_only_the_waiting_arm_lets_the_job_stay_green_without_a_check(self):
        """FALLBACK_EXIT is the fork path's verdict; it must not be 0 while waiting."""
        for label, _, _, fallback in self.ARMS:
            with self.subTest(arm=label):
                self.assertIn(f"FALLBACK_EXIT={fallback}", _case_arm(label))


class TestTheGateCannotReportSilently(unittest.TestCase):
    def test_the_check_is_published_and_pinned_to_the_head(self):
        run = _evidence_run()
        self.assertIn("check-runs", run, "the step no longer creates a check-run")
        self.assertIn('name=${CHECK_NAME}"', run.replace("'", '"'))
        self.assertIn('head_sha=${HEAD_SHA}', run, "the check must be pinned to the head")

    def test_the_published_name_differs_from_every_job_name(self):
        """Two same-named checks on one commit are indistinguishable to protection.

        Actions names a job's own check after the job, and that check is driven
        by the exit code — it can only ever say "the job ran". If the job the
        gate lives in were also called `keel evidence (required)`, the green
        exit-code check #928 reports would still be sitting there under the
        gating name.
        """
        workflow = _workflow()
        job_names = {job.get("name") for job in workflow["jobs"].values()}
        self.assertNotIn(
            CHECK_NAME,
            job_names,
            f"a job is named {CHECK_NAME!r}, colliding with the published check-run",
        )

    def test_the_step_declares_the_check_name_it_publishes(self):
        env = next(
            s for s in _workflow()["jobs"]["evidence"]["steps"]
            if s.get("name") == "Verify posted ship evidence"
        )["env"]
        self.assertEqual(env.get("CHECK_NAME"), CHECK_NAME)

    def test_the_check_is_upserted_never_blindly_created(self):
        """`POST /check-runs` has no upsert, so every run would stack another.

        Branch protection matches by name; several same-named checks on one
        commit — at least one permanently incomplete — is the exact ambiguity
        the job rename above exists to avoid. Reproducing it against ourselves
        would be no improvement.
        """
        body = self._publisher()
        self.assertIn("-X PATCH", body, "an existing check-run is never updated")
        self.assertIn("-X POST", body, "a first check-run is never created")
        self.assertRegex(
            body,
            r'if\s+\[\s+-n\s+"\$existing"\s+\]',
            "the publisher does not branch on whether a check-run already exists",
        )

    def test_the_lookup_is_a_GET(self):
        """`-f` alone switches gh to POST, which 404s on this endpoint.

        Verified against the live API: without `--method GET` the lookup returns
        "Not Found", so `existing` is always empty and every run POSTs a new
        check — silently restoring the stacking this replaces, with no error.
        """
        body = self._publisher()
        # On the invocation, not merely somewhere in the block: the comment above
        # it explains the flag, so a bare `in` check passes with the flag removed.
        self.assertRegex(
            body,
            r"existing=\$\(gh api --method GET",
            "the check-run lookup is not forced to GET, so gh will POST and 404",
        )
        self.assertIn("check_name=${CHECK_NAME}", body)

    def test_a_fork_falls_back_to_the_exit_code_instead_of_dying(self):
        """A fork PR's token is read-only whatever `permissions:` declares.

        Exiting 1 unconditionally would leave every fork contribution red with
        no route forward — including one whose evidence verified. The job's exit
        code carries the verdict instead, which is green only for a real pass.
        """
        run = _evidence_run()
        self.assertIn('IS_FORK', run, "the fork case is not detected")
        self.assertIn('exit "$FALLBACK_EXIT"', run, "the fork path ignores the verdict")
        fork_branch = run[run.index('if [ "$IS_FORK"') :]
        self.assertIn("::warning", fork_branch, "the fork path fails silently")

    def test_a_non_fork_that_cannot_publish_still_fails(self):
        """Fail-closed survives the fork escape hatch."""
        run = _evidence_run()
        tail = run[run.index('if [ "$IS_FORK"') :]
        after = tail[tail.index("fi") :]
        self.assertIn("::error", after)
        self.assertIn("exit 1", after, "a non-fork publish failure no longer fails")

    def _publisher(self) -> str:
        body = re.search(r"publish_check\(\)\s*\{(.*?)\n\}", _evidence_run(), re.DOTALL)
        self.assertIsNotNone(body, "the publisher is gone")
        return body.group(1)

    def test_the_publisher_does_not_swallow_its_own_failure(self):
        """`|| true` anywhere in the publisher defeats every guard above."""
        for swallow in ("|| true", "|| :", "; true"):
            with self.subTest(swallow=swallow):
                self.assertNotIn(swallow, self._publisher())

    def test_the_publisher_forwards_the_verdict_it_was_given(self):
        """The per-arm tests read call sites; this reads what actually goes out.

        Hardcoding ``conclusion=success`` inside the publisher deletes the
        whole behaviour of this change while every call site still reads
        correctly — a review caught exactly that mutation surviving.
        """
        body = self._publisher()
        self.assertIn(
            'conclusion=${conclusion}',
            body,
            "the publisher sends a literal conclusion instead of the one it was given",
        )
        self.assertIn(
            'status=${status}',
            body,
            "the publisher sends a literal status instead of the one it was given",
        )
        self.assertNotRegex(
            body,
            r'conclusion=(success|neutral|failure|skipped)\b',
            "the publisher hardcodes a conclusion",
        )
        # The conditional is what lets the waiting arm publish no conclusion at
        # all. Without it an incomplete run would carry one and complete itself.
        self.assertRegex(
            body,
            r'if\s+\[\s+-n\s+"\$conclusion"\s+\]',
            "the conclusion is no longer optional, so the waiting arm cannot stay incomplete",
        )

    def test_dispatch_runs_do_not_stamp_the_default_branch(self):
        """`workflow_dispatch` has no pull_request payload, and check-runs are permanent.

        Falling back to `$GITHUB_SHA` would write PR N's verdict onto whatever
        ref the run was dispatched from — normally the default branch.
        """
        run = _evidence_run()
        self.assertNotIn("github.sha", run, "the head falls back to the dispatched ref")
        self.assertIn("gh pr view", run, "no fallback resolves the PR's real head")
        self.assertIn("headRefOid", run)

    def test_the_workflow_holds_the_permission_it_needs(self):
        self.assertEqual(_workflow()["permissions"].get("checks"), "write")


class TestTheDocsDescribeWhatShips(unittest.TestCase):
    """#928's third point: the table documented the ask, not the behaviour."""

    def test_the_docs_state_that_the_check_is_actually_published(self):
        """Naming the check is not enough — the prose has to assert it exists.

        A doc that merely mentions the name can be reworded to describe it as
        aspirational without failing anything, which is how #928's table came
        to document the ask rather than the behaviour.
        """
        text = DOC.read_text(encoding="utf-8")
        self.assertIn(CHECK_NAME, text)
        self.assertRegex(
            text,
            rf"publishes these as a real check-run named\s*\n?\*\*`{re.escape(CHECK_NAME)}`\*\*",
            "the docs no longer state that the check-run is actually published",
        )

    def test_the_docs_do_not_repeat_the_neutral_claim(self):
        """`neutral` satisfies a required check; saying otherwise is the bug.

        GitHub: "Required status checks must have a `successful`, `skipped`, or
        `neutral` status before collaborators can make changes to a protected
        branch." #829 assumed the opposite and this doc inherited it.
        """
        text = DOC.read_text(encoding="utf-8").lower()
        self.assertNotRegex(
            text,
            r"neutral[^.\n]{0,80}(blocks|does not satisfy|still blocked)",
            "the docs claim a neutral conclusion blocks a merge; it does not",
        )

    def test_the_docs_state_the_fail_closed_rule(self):
        text = DOC.read_text(encoding="utf-8")
        bullet = next(
            (line for line in text.splitlines() if "unable to report" in line.lower()), ""
        )
        self.assertTrue(bullet, "the fail-closed rule is no longer documented")
        self.assertRegex(
            bullet,
            r"is not a pass|never a pass",
            "the fail-closed bullet no longer says that not reporting is not a pass",
        )


if __name__ == "__main__":  # pragma: no cover - manual entry point
    unittest.main()
