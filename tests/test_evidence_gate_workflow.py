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
    """Pass, waiting and fail must publish three *different* things."""

    #: (case label, expected `status=`, expected `conclusion=` or None, expected exit)
    ARMS = (
        ("0)", "completed", "success", "exit 0"),
        ("2)", "in_progress", None, "exit 0"),
        ("*)", "completed", "failure", "exit 1"),
    )

    def test_each_arm_publishes_its_own_status_and_conclusion(self):
        for label, status, conclusion, _ in self.ARMS:
            arm = _case_arm(label)
            with self.subTest(arm=label):
                self.assertIn(
                    f" {status} ",
                    arm,
                    f"the {label} arm does not publish status={status}",
                )
                if conclusion is None:
                    # The waiting arm must publish NO conclusion. An incomplete
                    # check blocks a merge; every conclusion GitHub accepts as
                    # "not failing" — success, skipped, neutral — satisfies a
                    # required check, which is the #928 defect by another route.
                    self.assertNotRegex(
                        arm,
                        r"publish_or_fail\s+\w+\s+(success|neutral|skipped)",
                        "the waiting arm publishes a conclusion branch protection "
                        "treats as passing — this is the #928 regression",
                    )
                else:
                    self.assertRegex(
                        arm,
                        rf"publish_or_fail\s+{status}\s+{conclusion}\b",
                        f"the {label} arm does not publish conclusion={conclusion}",
                    )

    def test_no_two_arms_report_the_same_verdict(self):
        """Kills a wholesale swap, and a hardcoded conclusion for every state."""
        published = []
        for label, _, _, _ in self.ARMS:
            arm = _case_arm(label)
            match = re.search(r"publish_or_fail\s+(\S+)\s*(\S*)", arm)
            self.assertIsNotNone(match, f"the {label} arm publishes nothing")
            published.append((match.group(1), match.group(2).strip('"')))
        self.assertEqual(
            len(set(published)),
            len(published),
            f"two case arms report the same verdict: {published}",
        )

    def test_each_arm_exits_as_its_verdict_requires(self):
        for label, _, _, expected_exit in self.ARMS:
            with self.subTest(arm=label):
                self.assertIn(expected_exit, _case_arm(label))


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

    def test_a_failed_publish_fails_the_step_in_every_arm(self):
        """Fail-closed, uniformly. A fork PR's token is read-only in any state."""
        run = _evidence_run()
        helper = re.search(r"publish_or_fail\(\)\s*\{(.*?)\n\}", run, re.DOTALL)
        self.assertIsNotNone(helper, "the fail-closed wrapper is gone")
        self.assertIn("exit 1", helper.group(1), "a failed publish does not fail the step")
        for label, _, _, _ in TestEachArmReportsItsOwnVerdict.ARMS:
            with self.subTest(arm=label):
                self.assertIn(
                    "publish_or_fail",
                    _case_arm(label),
                    f"the {label} arm publishes without the fail-closed wrapper",
                )

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
