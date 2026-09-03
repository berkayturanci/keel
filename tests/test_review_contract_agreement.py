"""Every command that resolves a review contract must resolve the *same* one (#1014).

Before `knobs.team` the reviewer bench came from the risk tier alone, so six commands
deriving it independently all reached the same answer by accident. `knobs.team` removed
the accident: on a project whose `review.by_tier."3"` is `jury`, `keel ship` publishes
zero reviewer slots and a gating jury while a site that still fell back to
`reviewer_count(3)` demands `review-verdict-1..3` — a gate no run of that project could
satisfy, because the two halves of one contract disagreed about who the reviewers were.

These tests hold the two halves together from the outside (same required evidence out of
`plan`, `ship`, `evidence-verify` and `step-verify`) and from the inside (an AST sweep, so
a *new* call site added later cannot quietly reintroduce the fallback).
"""

import ast
import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from keel import cli

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

JURY_PANEL_CONFIG = """
extends: keel
core_version: "^1.0"
base_branch: main
owner: acme
repo: widget
knobs:
  build_gate_cmd: "true"
  tier3_globs: ["src/**"]
  team:
    review:
      by_tier:
        "3": jury
    jury: { mode: gating, min_vendors: 2 }
"""

THREE_SEAT_CONFIG = """
extends: keel
core_version: "^1.0"
base_branch: main
owner: acme
repo: widget
knobs:
  build_gate_cmd: "true"
  tier3_globs: ["src/**"]
  team:
    review:
      by_tier:
        "3":
          - { provider: claude }
          - { provider: codex }
          - { provider: "subagent:opus-reviewer" }
    jury: { mode: advisory }
"""


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestEveryCommandResolvesTheSameBench(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "empty.json").write_text("[]", encoding="utf-8")
        (self.root / "body.md").write_text("Closes #1", encoding="utf-8")

    def _config(self, text):
        path = self.root / "project.yaml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _plan_required(self, config, *flags):
        rc, out, err = run(
            [
                "plan",
                config,
                "--root",
                str(REPO_ROOT),
                "--command",
                "ship",
                "--tier",
                "3",
                "--json",
                *flags,
            ]
        )
        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["contract"]
        return [item["id"] for item in contract["evidence"]["required"]], contract

    def _ship_required(self, config, *flags):
        # A non-git root cannot be diffed, which classifies fail-closed at TIER-3 — the
        # tier both other surfaces are pinned to below.
        rc, out, err = run(["ship", config, "--root", str(self.root), "--json", *flags])
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["result"]["assessment"]["tier"], 3)
        return [item["id"] for item in data["contract"]["evidence"]["required"]], data

    def _evidence_required(self, config, *flags):
        rc, out, err = run(
            [
                "evidence-verify",
                config,
                "--root",
                str(self.root),
                "--pr",
                "1",
                "--changed-file",
                "src/a.py",
                "--head-sha",
                "abc",
                "--pr-label",
                "keel:ship",
                "--pr-body-file",
                str(self.root / "body.md"),
                "--pr-comments-json",
                str(self.root / "empty.json"),
                "--issue-comments-json",
                str(self.root / "empty.json"),
                "--pr-reviews-json",
                str(self.root / "empty.json"),
                "--json",
                *flags,
            ]
        )
        self.assertIn(rc, (0, 1), err)
        verification = json.loads(out)["verification"]
        self.assertTrue(verification["enforced"])
        return [result["id"] for result in verification["results"]]

    def _step_verify_s7(self, *extra):
        handoff = self.root / "handoff.json"
        handoff.write_text(json.dumps({"step_id": "s7"}), encoding="utf-8")
        report = self.root / "report.json"
        report.write_text(json.dumps({"results": []}), encoding="utf-8")
        rc, out, _ = run(
            [
                "step-verify",
                "--step",
                "s7",
                "--handoff-file",
                str(handoff),
                "--evidence-report",
                str(report),
                "--json",
                *extra,
            ]
        )
        self.assertIn(rc, (0, 1))
        steps = json.loads(out)["contract"]["steps"]
        return next(step for step in steps if step["step_id"] == "s7")["required_evidence"]

    def test_a_jury_panel_config_requires_the_panel_everywhere(self):
        config = self._config(JURY_PANEL_CONFIG)

        plan_required, plan_contract = self._plan_required(config)
        ship_required, ship_data = self._ship_required(config)
        evidence_required = self._evidence_required(config)

        expected = ["closure-comment-pr", "closure-comment-issue", "jury-verdict"]
        self.assertEqual(plan_required, expected)
        self.assertEqual(ship_required, expected)
        self.assertEqual(sorted(evidence_required), sorted(expected))
        # …and no surface asks for a host review verdict that will never be posted.
        for required in (plan_required, ship_required, evidence_required):
            self.assertFalse([item for item in required if item.startswith("review-verdict")])
        self.assertEqual(plan_contract["review_merge_contract"]["reviewers"]["count"], 0)
        self.assertEqual(
            ship_data["result"]["assessment"]["review_merge_contract"]["reviewers"]["count"], 0
        )

    def test_a_three_seat_config_requires_three_verdicts_everywhere(self):
        config = self._config(THREE_SEAT_CONFIG)

        plan_required, plan_contract = self._plan_required(config)
        ship_required, _ = self._ship_required(config)
        evidence_required = self._evidence_required(config)

        expected = [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "review-verdict-2",
            "review-verdict-3",
        ]
        self.assertEqual(plan_required, expected)
        self.assertEqual(ship_required, expected)
        self.assertEqual(sorted(evidence_required), sorted(expected))
        self.assertEqual(plan_contract["review_merge_contract"]["reviewers"]["count"], 3)
        # An advisory jury is not a required verdict.
        self.assertNotIn("jury-verdict", plan_required)

    def test_step_verify_reads_the_projects_team_when_given_one(self):
        config = self._config(JURY_PANEL_CONFIG)

        with_project = self._step_verify_s7("--project", config, "--tier", "3")

        # The panel is the review at this tier, so s7 has no host verdict to require.
        self.assertEqual(with_project, [])
        # Without a project there is no team to read, and the tier-derived bench stands —
        # which is exactly the answer that disagreed with `keel ship` before `--project`.
        self.assertEqual(self._step_verify_s7(), ["review-verdict-1", "review-verdict-2"])

    def test_ship_recomputes_the_blocks_derived_from_the_review_contract(self):
        """`evidence` and `step_verification` are derived, so they move with the tier.

        `build_command_contract` builds both from the review contract at the *unresolved*
        tier, before the diff has been read. Overwriting only `review_merge_contract`
        published one document holding `reviewers.count: 0` beside an evidence block
        demanding two review verdicts — and the adapters read the evidence block.
        """
        _, data = self._ship_required(self._config(JURY_PANEL_CONFIG))

        contract = data["contract"]
        review = data["result"]["assessment"]["review_merge_contract"]
        self.assertEqual(contract["review_merge_contract"], review)
        self.assertEqual(
            [item["id"] for item in contract["evidence"]["required"]],
            ["closure-comment-pr", "closure-comment-issue", "jury-verdict"],
        )
        s7 = next(
            step for step in contract["step_verification"]["steps"] if step["step_id"] == "s7"
        )
        self.assertFalse([item for item in s7["required_evidence"] if "review-verdict" in item])

    def _review_required_count(self, config, supplied):
        """`keel review` has no jury flags at all — the bench must not need them.

        It is handed exactly the verdicts the other surfaces say are required: this is the
        command that *posts* the evidence, so if it under-posts, it refuses (and the
        assertion below fails) rather than leaving a PR that can never clear its own gate.
        """
        reviews = self.root / "reviews.json"
        reviews.write_text(
            json.dumps(
                [
                    {"reviewer": chr(ord("a") + i), "verdict": "LGTM", "findings": []}
                    for i in range(supplied)
                ]
            ),
            encoding="utf-8",
        )
        rc, out, _ = run(
            [
                "review",
                config,
                "--root",
                str(self.root),
                "--pr",
                "1",
                "--reviews",
                str(reviews),
                "--changed-file",
                "src/a.py",
                "--head-sha",
                "abc",
                "--dry-run",
                "--json",
            ]
        )
        self.assertIn(rc, (0, 1))
        return json.loads(out)["plan"]["required_count"]

    def test_the_bench_is_identical_across_every_no_jury_state(self):
        """The cross-product the lead pinned: the flag must not move the contract.

        `keel review` never sees a jury flag, keel's CI passes `--no-jury` to
        `evidence-verify` on every run and to `ship`/`plan` on none. If the bench moved
        with the flag, CI would demand three verdicts the ship run told the adapter never
        to produce.
        """
        for name, config_text, expected in (
            (
                "jury panel",
                JURY_PANEL_CONFIG,
                ["closure-comment-pr", "closure-comment-issue", "jury-verdict"],
            ),
            (
                "three seats",
                THREE_SEAT_CONFIG,
                [
                    "closure-comment-pr",
                    "closure-comment-issue",
                    "review-verdict-1",
                    "review-verdict-2",
                    "review-verdict-3",
                ],
            ),
        ):
            config = self._config(config_text)
            for flags in ((), ("--no-jury",), ("--jury-advisory",)):
                with self.subTest(config=name, flags=flags):
                    plan_required, plan_contract = self._plan_required(config, *flags)
                    ship_required, ship_data = self._ship_required(config, *flags)
                    evidence_required = self._evidence_required(config, *flags)

                    self.assertEqual(plan_required, expected)
                    self.assertEqual(ship_required, expected)
                    self.assertEqual(sorted(evidence_required), sorted(expected))
                    ship_reviewers = ship_data["result"]["assessment"]["review_merge_contract"][
                        "reviewers"
                    ]
                    self.assertEqual(
                        plan_contract["review_merge_contract"]["reviewers"]["count"],
                        ship_reviewers["count"],
                    )
                    self.assertEqual(
                        plan_contract["review_merge_contract"]["reviewers"]["slots"],
                        ship_reviewers["slots"],
                    )
                    # `keel review` takes no jury flag at all, so it is the fixed point
                    # every flagged run has to match: it posts exactly the verdicts the
                    # evidence gate will then require.
                    self.assertEqual(
                        self._review_required_count(config, ship_reviewers["count"]),
                        ship_reviewers["count"],
                    )

    def test_step_verify_refuses_an_unreadable_project_rather_than_guessing(self):
        bad = self.root / "broken.yaml"
        bad.write_text("extends: keel\ncore_version: '^1.0'\n", encoding="utf-8")
        handoff = self.root / "handoff.json"
        handoff.write_text(json.dumps({"step_id": "s7"}), encoding="utf-8")
        report = self.root / "report.json"
        report.write_text(json.dumps({"results": []}), encoding="utf-8")

        rc, _, err = run(
            [
                "step-verify",
                "--step",
                "s7",
                "--project",
                str(bad),
                "--handoff-file",
                str(handoff),
                "--evidence-report",
                str(report),
                "--json",
            ]
        )

        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_no_jury_is_recorded_and_not_applied_on_a_jury_tier(self):
        """`--no-jury` must not remove the only review a jury tier has.

        Restaffing the tier's host reviewers instead (an earlier attempt at this) made the
        bench a function of a flag the six review-aware commands do not receive uniformly,
        which reopened the same disagreement on a new axis.
        """
        config = self._config(JURY_PANEL_CONFIG)

        rc, out, err = run(
            [
                "plan",
                config,
                "--root",
                str(REPO_ROOT),
                "--command",
                "ship",
                "--tier",
                "3",
                "--no-jury",
                "--json",
            ]
        )

        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["contract"]
        self.assertEqual(contract["assignment"]["review_panel"], "jury")
        self.assertEqual(contract["review_merge_contract"]["reviewers"]["count"], 0)
        self.assertEqual(contract["review_merge_contract"]["jury"]["mode"], "gating")
        self.assertIn("--no-jury does not apply", contract["assignment"]["warnings"][0])
        required = [item["id"] for item in contract["evidence"]["required"]]
        self.assertEqual(required, ["closure-comment-pr", "closure-comment-issue", "jury-verdict"])

    def test_no_jury_keeps_its_meaning_where_the_panel_is_not_the_review(self):
        config = self._config(THREE_SEAT_CONFIG)

        rc, out, err = run(
            [
                "plan",
                config,
                "--root",
                str(REPO_ROOT),
                "--command",
                "ship",
                "--tier",
                "3",
                "--no-jury",
                "--json",
            ]
        )

        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["contract"]
        self.assertFalse(contract["review_merge_contract"]["jury"]["enabled"])
        self.assertEqual(contract["review_merge_contract"]["reviewers"]["count"], 3)
        self.assertEqual(contract["assignment"]["warnings"], [])


class TestNoCallSiteFallsBackToTheTier(unittest.TestCase):
    """An AST sweep, so a *new* call site cannot quietly reintroduce the disagreement.

    Six commands resolve a review contract today. Catching the seventh by review is how
    this defect reached a PR in the first place.
    """

    SOURCES = ("src/keel/cli.py", "src/keel/contracts.py")

    def test_every_resolve_review_contract_call_passes_an_assignment(self):
        for relative in self.SOURCES:
            path = REPO_ROOT / relative
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "resolve_review_contract"
            ]
            self.assertTrue(calls, f"{relative}: no resolve_review_contract call found")
            for call in calls:
                with self.subTest(source=relative, line=call.lineno):
                    keywords = {kw.arg for kw in call.keywords}
                    self.assertIn(
                        "assignment",
                        keywords,
                        f"{relative}:{call.lineno} resolves a review contract without the "
                        "team assignment; it will disagree with `keel ship` on any project "
                        "using knobs.team",
                    )


if __name__ == "__main__":
    unittest.main()
