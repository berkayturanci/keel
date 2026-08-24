"""The evidence gate's *reported* verdict must match the verdict it computed.

#849 gave ``keel evidence-verify`` a third status and wired the workflow to
``exit 0`` for it. A job that exits 0 concludes green, so a check named for the
evidence gate reported success while zero verdicts existed — a worse signal
than the red it replaced, because red at least said "not yet" (#928).

The library half of that lifecycle is covered by ``test_evidence.py``. Nothing
covered the half that a human actually looks at: the check GitHub shows. These
are text assertions against the workflow, which is the only offline way to pin
a step's reporting contract — but they assert the *specific* strings that carry
the behaviour, so deleting the ``publish_check`` call or flipping a conclusion
fails here rather than passing quietly.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/keel-ship.yml"
CHECK_NAME = "keel evidence (required)"


def _evidence_step() -> str:
    """The body of the 'Verify posted ship evidence' step."""
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.find("- name: Verify posted ship evidence")
    assert start != -1, "the evidence-verification step is gone"
    rest = text[start + 1 :]
    end = rest.find("\n      - name: ")
    return rest if end == -1 else rest[:end]


class TestTheGatePublishesItsVerdict(unittest.TestCase):
    def test_the_step_creates_a_check_run(self):
        """Without this the only signal is the job's own exit code."""
        step = _evidence_step()
        self.assertIn("check-runs", step, "the step no longer creates a check-run")
        self.assertIn(f'name="{CHECK_NAME}"', step)
        self.assertIn('head_sha="$HEAD_SHA"', step, "the check must be pinned to the head")

    def test_each_exit_code_publishes_its_own_conclusion(self):
        """0 -> success, 2 -> neutral, anything else -> failure.

        The defect in #928 was exactly one of these mappings being absent, so
        each is pinned separately rather than as 'some conclusion is present'.
        """
        step = _evidence_step()
        for conclusion in ("success", "neutral", "failure"):
            with self.subTest(conclusion=conclusion):
                self.assertRegex(
                    step,
                    rf"publish_check\s+{conclusion}\b",
                    f"no branch publishes a {conclusion!r} conclusion",
                )

    def test_the_waiting_branch_is_neutral_and_never_success(self):
        """A pre-verdict state must not conclude green (#829, #928)."""
        step = _evidence_step()
        waiting = step[step.find('2)') : step.find('*)')]
        self.assertIn("publish_check neutral", waiting)
        self.assertNotIn(
            "publish_check success",
            waiting,
            "the waiting branch concludes success — this is the #928 regression",
        )

    def test_a_check_that_cannot_be_published_fails_instead_of_passing(self):
        """Fail-closed: not reporting a verdict is not the same as passing.

        A fork PR's token is read-only, so the API call can fail. Exiting 0
        anyway would reproduce the original defect by a different route.
        """
        step = _evidence_step()
        waiting = step[step.find('2)') : step.find('*)')]
        self.assertRegex(
            waiting,
            r"if\s+!\s+publish_check\s+neutral",
            "the waiting branch does not check whether publishing succeeded",
        )
        self.assertIn("exit 1", waiting, "a failed publish does not fail the step")

    def test_the_workflow_still_holds_the_permission_it_needs(self):
        """`checks: write` was added by #849 and left unused; it is used now."""
        text = WORKFLOW.read_text(encoding="utf-8")
        header = text[: text.find("jobs:")]
        self.assertIn("checks: write", header)


class TestTheDocsDescribeWhatShips(unittest.TestCase):
    """#928's third point: the table documented the ask, not the behaviour."""

    DOC = Path(__file__).resolve().parent.parent / "docs/keel/evidence.md"

    def test_the_lifecycle_table_names_all_three_conclusions(self):
        text = self.DOC.read_text(encoding="utf-8")
        table = text[text.find("Three-State Verification Lifecycle") :][:2000]
        for status, conclusion in (
            ("`waiting`", "neutral"),
            ("`pass`", "success"),
            ("`fail`", "failure"),
        ):
            with self.subTest(status=status):
                row = next(
                    (line for line in table.splitlines() if line.startswith(f"| **{status}**")),
                    None,
                )
                self.assertIsNotNone(row, f"no lifecycle row for {status}")
                self.assertIn(conclusion, row)

    def test_the_docs_state_that_forged_evidence_is_loud(self):
        """The fail row must keep naming tampering, not just SHA mismatch."""
        text = self.DOC.read_text(encoding="utf-8")
        fail_row = next(
            (line for line in text.splitlines() if line.startswith("| **`fail`**")), ""
        )
        self.assertTrue(
            re.search(r"forged|untrusted|tamper", fail_row, re.IGNORECASE),
            "the fail row no longer says that a forged verdict fails loudly",
        )


if __name__ == "__main__":  # pragma: no cover - manual entry point
    unittest.main()
