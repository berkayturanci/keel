"""The tier-3 jury is advisory here, and the disarm must stay that narrow.

#965's record: tier-3 auto-enables a *gating* jury verdict, and that requirement
blocked four PRs simultaneously — #919 (a two-line hash bump), #920 (six lines),
#967 (a mechanical reformat whose correctness argument is a syntax-tree diff),
and #958 (real parsing logic on untrusted input). Only the last is a change
where three independent cross-vendor readings can differ informatively.

A gate that cannot be afforded on routine changes is a gate that gets waived,
and the waiver habit is worse than the review it replaces. So `--no-jury` is
passed at the call site, and a jury is run deliberately per PR when the change
warrants it — which is what happened on #958.

The danger of that decision is not the decision; it is the next edit. `--no-jury`
sits one word away from `--reviewers 1`, `--jury-advisory` on top of an already
advisory jury, `--deferral all`, or `--dry-run`, each of which would hollow the
gate out while the line still reads like a gate. This file asserts the disarm
stays exactly one flag wide.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "keel-ship.yml"


def code_lines() -> list[str]:
    """The workflow's lines with whole-line comments removed.

    Every flag asserted on below is also *described* in a comment a few lines
    above it, so a substring search over the raw file passes whether or not the
    flag is configured. That is not hypothetical — it is how two mutations
    against the sibling test in ai-jury survived (#602).
    """
    return [
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class TheCommentStrippingWorks(unittest.TestCase):
    """The assertions below are only meaningful if this actually strips."""

    def test_comments_go_and_code_stays(self):
        raw = WORKFLOW.read_text(encoding="utf-8").splitlines()
        self.assertTrue(
            any(line.lstrip().startswith("#") for line in raw),
            "the workflow has no comment lines, so this guard proves nothing",
        )
        stripped = code_lines()
        self.assertFalse([line for line in stripped if line.lstrip().startswith("#")])
        self.assertGreater(len(stripped), 50)


class TheGateCall(unittest.TestCase):
    def setUp(self):
        self.code = code_lines()

    def _argv(self) -> str:
        """The line that builds `keel evidence-verify`'s arguments.

        Selected on `--pr`, never on a flag under test, so the selector cannot
        make the assertion circular — the `ship` job assembles an `ARGS=` array
        too, and picking the wrong one would silently assert nothing.
        """
        matches = [
            line
            for line in self.code
            if "ARGS=(.keel/project.yaml" in line and '--pr "$PR"' in line
        ]
        self.assertEqual(len(matches), 1, f"expected one gate ARGS line, saw {matches}")
        return matches[0]

    def test_the_jury_verdict_is_not_required(self):
        """#965's decision, at the call site rather than in prose."""
        self.assertIn("--no-jury", self._argv())

    def test_the_reviewer_requirement_is_untouched(self):
        """The narrow-disarm check: tier-3 still means three distinct verdicts.

        Nothing may override the count the tier derives, or the gate quietly
        becomes a formality on exactly the files `tier3_globs` marks riskiest.
        """
        argv = self._argv()
        self.assertNotIn("--reviewers", argv)

    def test_no_second_disarm_rides_along(self):
        """Each of these would hollow the gate out while the line still reads like one."""
        argv = self._argv()
        for flag in ("--jury-advisory", "--dry-run", "--deferral all"):
            with self.subTest(flag=flag):
                self.assertNotIn(flag, argv)

    def test_the_gate_still_refuses_an_unarmed_run(self):
        """Dropping the jury must not also drop the proof that anything ran."""
        self.assertIn("--require-armed", self._argv())

    def test_the_phase_is_still_the_satisfiable_one(self):
        self.assertIn("--phase pre-merge", self._argv())

    def test_a_deferral_stays_an_explicit_input(self):
        """`--deferral` may reach the gate only from a workflow_dispatch input.

        Baked into the argument line it would be a standing waiver; as an input
        it is a deliberate, logged act by whoever dispatched the run.
        """
        self.assertNotIn("--deferral", self._argv())
        self.assertTrue(
            any('ARGS+=(--deferral "$DEFERRAL")' in line for line in self.code),
            "the deferral path was removed entirely; it should stay available "
            "as an explicit input, just never as a default",
        )
