"""Every command that resolves a review contract must resolve the *same* one (#1014).

Before `knobs.team` the reviewer bench came from the risk tier alone, so six commands
deriving it independently all reached the same answer by accident. `knobs.team` removed
the accident: on a project whose `review.by_tier."3"` is `jury`, `keel ship` publishes
zero reviewer slots and a gating jury while a site that still fell back to
`reviewer_count(3)` demands `review-verdict-1..3` — a gate no run of that project could
satisfy, because the two halves of one contract disagreed about who the reviewers were.

These tests hold the two halves together from the outside (same required evidence out of
`plan`, `ship`, `review`, `evidence-verify` and `step-verify`) and from the inside (an AST
sweep, so a *new* call site added later cannot quietly reintroduce the fallback).

#1043 closed the last residual divergence, which was jury-only rather than bench: `keel
review` defined none of `--jury` / `--no-jury` / `--jury-advisory` and hardcoded them
false, so with a jury flag on the run it resolved a *different jury line* from every other
surface — permissive-only (its `--verify` could require a `jury-verdict` the
`keel ship --no-jury` run was told never to post), but a divergence all the same. The
cross-product below now drives `keel review` with the flags too and compares the whole
`jury` block, and `TestEverySurfaceDefinesTheSameJuryFlags` pins the sixth surface
(`keel merge`) at the parser.

**Offline and deterministic**, per AGENTS.md: every subprocess these commands can reach
sits behind an injectable seam, and `setUpModule` fills all of them. Three of the five
legs shell out on a real host — `plan`, `ship` and `review` each probe `gh auth status`
through `runtime.detect`, and `ship` also runs `git diff` — so without the stubs this
module validated a live GitHub token on every command and crashed on a runner with no `gh`
credentials (`keel review` exits 1 on the missing capability, printing nothing to stdout,
so the JSON parse below raised `JSONDecodeError`). `_recording_run_argv` raises on any
argv the module has no canned answer for, so a *new* subprocess cannot appear unnoticed.
"""

import argparse
import ast
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from keel import cli
from keel.runner import CommandResult

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Every argv the stubbed seams were asked for, so a test can assert on the whole set.
SPAWN_ATTEMPTS: list[list[str]] = []

#: Tools `runtime.detect` may find on PATH. Deliberately not "everything": reporting a
#: `jury` binary would let the built-in jury gate dispatch a real cross-vendor review.
_ON_PATH = {"git": "/usr/bin/git", "gh": "/usr/bin/gh"}


def _recording_run_argv(argv, **_kwargs):
    """Every subprocess these commands can reach, answered from memory.

    ``git`` is answered as "not a repository", which is the state the fixtures below rely
    on: an unreadable diff classifies fail-closed at `classify.UNKNOWN_TIER` (3), the tier
    the whole cross-product is written against. Anything else **raises** rather than
    running — a test that starts shelling out to a real tool should fail loudly here, not
    quietly depend on the machine it runs on.
    """
    argv = list(argv)
    SPAWN_ATTEMPTS.append(argv)
    if argv[:1] == ["git"]:
        return CommandResult(False, 128, "fatal: not a git repository", stdout="")
    raise AssertionError("this module must stay offline; a command tried to run: " + " ".join(argv))


def _detect_offline(root=".", **kwargs):
    """`runtime.detect` with both of its seams filled — no PATH scan, no `gh auth`.

    `keel review` requires `gh` **and** `gh-auth`, so the stub reports them available:
    the point of the review leg is that its reviewer bench matches the other commands',
    which is unobservable if the command exits early on a missing capability. That is
    also why this differs from `tests/test_cli.py`, which reports `gh auth` *failing* —
    there the probe is noise, here the answer is load-bearing.
    """
    kwargs.setdefault("which", _ON_PATH.get)
    kwargs.setdefault(
        "run", lambda *_a, **_kw: CommandResult(True, 0, "Logged in", stdout="Logged in")
    )
    return _REAL_DETECT(root, **kwargs)


_REAL_DETECT = None
_PATCHES: list = []


def setUpModule():
    global _REAL_DETECT
    from keel import runtime

    _REAL_DETECT = runtime.detect
    _PATCHES.extend(
        [
            patch("keel.runtime.detect", _detect_offline),
            patch("keel.jury.available", return_value=False),
            # `run_argv` is bound at import in each of these, so the name has to be
            # patched where it is used, not only where it is defined.
            patch("keel.cli.run_argv", _recording_run_argv),
            patch("keel.git.run_argv", _recording_run_argv),
            patch("keel.github.run_argv", _recording_run_argv),
        ]
    )
    for entry in _PATCHES:
        entry.start()


def tearDownModule():
    for entry in reversed(_PATCHES):
        entry.stop()
    _PATCHES.clear()


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

#: No `knobs.team` at all — the pre-#1014 shape, and the one the jury flags actually move.
#: On a jury *panel* tier the panel outranks every flag, so a config with a panel can never
#: show the divergence #1043 closed; this one can, because at tier-3 the auto-jury is on by
#: default and `--no-jury` genuinely turns it off.
PLAIN_TIER3_CONFIG = """
extends: keel
core_version: "^1.0"
base_branch: main
owner: acme
repo: widget
knobs:
  build_gate_cmd: "true"
  tier3_globs: ["src/**"]
"""


#: A tier-3 bench of three seats, plus a `--team` profile that staffs **one**. The two
#: numbers differ on purpose: a profile with fewer reviewers than the tier is the case
#: that makes the gate unsatisfiable if one surface honours it and another does not —
#: ship dispatches one reviewer while evidence-verify demands three verdicts nobody will
#: ever post (#1017).
PROFILE_CONFIG = """
extends: keel
core_version: "^1.0"
base_branch: main
owner: acme
repo: widget
knobs:
  build_gate_cmd: "true"
  tier3_globs: ["src/**"]
  team:
    implement:
      default: { provider: codex }
    review:
      by_tier:
        "3":
          - { provider: claude }
          - { provider: codex }
          - { provider: "subagent:opus-reviewer" }
    profiles:
      hardening:
        implement: { provider: codex, effort: high }
        review:
          - { provider: codex }
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

    def _evidence_required(self, config, *flags, pr_comments=None):
        payload = self._evidence_payload(config, *flags, pr_comments=pr_comments)
        return [result["id"] for result in payload["verification"]["results"]]

    def _evidence_payload(self, config, *flags, pr_comments=None):
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
                str(pr_comments or self.root / "empty.json"),
                "--issue-comments-json",
                str(self.root / "empty.json"),
                "--pr-reviews-json",
                str(self.root / "empty.json"),
                "--json",
                *flags,
            ]
        )
        self.assertIn(rc, (0, 1), err)
        payload = json.loads(out)
        self.assertTrue(payload["verification"]["enforced"])
        return payload

    def _step_verify_s7(self, *extra):
        return self._step_verify_step("s7", *extra)

    def _step_verify_step(self, step, *extra):
        handoff = self.root / "handoff.json"
        handoff.write_text(json.dumps({"step_id": step}), encoding="utf-8")
        report = self.root / "report.json"
        report.write_text(json.dumps({"results": []}), encoding="utf-8")
        rc, out, _ = run(
            [
                "step-verify",
                "--step",
                step,
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
        return next(entry for entry in steps if entry["step_id"] == step)["required_evidence"]

    def test_a_jury_panel_config_requires_the_panel_everywhere(self):
        """Every surface requires the same panel: its ballots *and* its verdict (#1015).

        The panel is the review, so its ballots are the s7 evidence — one
        head-pinned `keel.review-verdict.v1` per panelist, posted by
        `keel review --from-jury` — and the jury verdict stays as the separate
        consensus record. Until a posted verdict declares the panel size, the
        required ballot count is the jury's minimum vendor floor, which is the
        answer that fails closed.
        """
        config = self._config(JURY_PANEL_CONFIG)

        plan_required, plan_contract = self._plan_required(config)
        ship_required, ship_data = self._ship_required(config)
        evidence_required = self._evidence_required(config)

        expected = [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "review-verdict-2",
            "jury-verdict",
        ]
        self.assertEqual(plan_required, expected)
        self.assertEqual(ship_required, expected)
        self.assertEqual(sorted(evidence_required), sorted(expected))
        reviewers = plan_contract["review_merge_contract"]["reviewers"]
        self.assertEqual(reviewers["count"], 2)
        self.assertEqual(reviewers["source"], "jury")
        self.assertEqual(reviewers["panel"], "jury")
        # …and no host reviewer slot is staffed for a panel keel does not run.
        self.assertEqual(reviewers["slots"], [])
        self.assertEqual(
            ship_data["result"]["assessment"]["review_merge_contract"]["reviewers"]["count"], 2
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

    def test_a_team_profile_moves_the_bench_on_every_surface_at_once(self):
        """`--team` selects a bench, so it changes what the gate requires.

        Every command that resolves this contract has to see the same profile. When only
        the dispatching half did, a `--team` naming a one-seat bench dispatched one
        reviewer while `evidence-verify` and `keel merge` still demanded the tier's three
        verdicts — a gate no run of that project could satisfy, which is the #1014 defect
        wearing a #1017 flag.
        """
        config = self._config(PROFILE_CONFIG)
        flags = ("--team", "hardening")

        plan_required, _ = self._plan_required(config, *flags)
        ship_required, _ = self._ship_required(config, *flags)
        evidence_required = self._evidence_required(config, *flags)
        step_required = self._step_verify_s7("--project", config, "--tier", "3", *flags)

        expected = ["closure-comment-pr", "closure-comment-issue", "review-verdict-1"]
        self.assertEqual(plan_required, expected)
        self.assertEqual(ship_required, expected)
        self.assertEqual(sorted(evidence_required), sorted(expected))
        self.assertEqual(step_required, ["review-verdict-1"])

    def test_without_the_profile_the_same_config_keeps_the_tiers_three_seats(self):
        """The other half of the pair: the profile has to be what moved the bench."""
        config = self._config(PROFILE_CONFIG)

        plan_required, _ = self._plan_required(config)
        ship_required, _ = self._ship_required(config)

        self.assertEqual(
            plan_required,
            [
                "closure-comment-pr",
                "closure-comment-issue",
                "review-verdict-1",
                "review-verdict-2",
                "review-verdict-3",
            ],
        )
        self.assertEqual(ship_required, plan_required)

    def test_plan_and_ship_publish_the_same_assignment_for_the_bench_flags(self):
        """`plan` accepted `--effort`/`--team` and threw them away.

        It parsed both, never passed them to the resolver, and published
        `effort: null` / `team_profile: null` / `bench: []` with no unknown-profile
        warning — while `keel ship` on the identical command line published all four.
        Two answers to the one question this contract exists to answer once.
        """
        config = self._config(PROFILE_CONFIG)
        flags = ("--effort", "high", "--team", "hardening")

        _, plan_contract = self._plan_required(config, *flags)
        _, ship_data = self._ship_required(config, *flags)
        ship_assignment = ship_data["contract"]["assignment"]

        for field in ("effort", "team_profile", "bench", "lead", "implementer"):
            with self.subTest(field=field):
                self.assertEqual(plan_contract["assignment"][field], ship_assignment[field])
        self.assertEqual(plan_contract["assignment"]["effort"], "high")
        self.assertEqual(plan_contract["assignment"]["team_profile"], "hardening")
        self.assertEqual(plan_contract["assignment"]["bench"], ["team.profiles.hardening"])

    def test_an_unknown_profile_warns_on_plan_exactly_as_it_does_on_ship(self):
        config = self._config(PROFILE_CONFIG)
        flags = ("--team", "nope")

        _, plan_contract = self._plan_required(config, *flags)
        _, ship_data = self._ship_required(config, *flags)

        for contract in (plan_contract["assignment"], ship_data["contract"]["assignment"]):
            with self.subTest():
                self.assertTrue([w for w in contract["warnings"] if "--team 'nope'" in w])

    def test_the_ship_preflight_contract_agrees_with_the_assessed_one(self):
        """`keel ship` publishes a contract *before* it assesses and replaces it after.

        The preflight one is the only contract an operator sees when the run halts before
        gates — a consent gap, a contradictory ledger flag pair. It was built without the
        bench flags, so a halted run printed a contract naming no team while a completed
        run printed one naming a team, from the identical command line.

        The halt used here is the `--capture-status not-run` / `--capture-artifact`
        contradiction: it is offline, deterministic, and reached before any assessment.
        """
        config = self._config(PROFILE_CONFIG)
        flags = ("--effort", "high", "--team", "hardening")

        rc, out, err = run(
            [
                "ship",
                config,
                "--root",
                str(self.root),
                "--capture-status",
                "not-run",
                "--capture-artifact",
                "report.md",
                "--json",
                *flags,
            ]
        )
        self.assertEqual(rc, 1, err)
        halted = json.loads(out)
        self.assertIn("contradicts", halted["error"])
        preflight = halted["contract"]["assignment"]

        _, ship_data = self._ship_required(config, *flags)
        assessed = ship_data["result"]["assessment"]["assignment"]

        for field in ("effort", "team_profile", "bench", "implementer"):
            with self.subTest(field=field):
                self.assertEqual(preflight[field], assessed[field])
        self.assertEqual(preflight["effort"], "high")
        self.assertEqual(preflight["team_profile"], "hardening")

    def test_step_verify_reads_the_projects_team_when_given_one(self):
        config = self._config(JURY_PANEL_CONFIG)

        with_project = self._step_verify_s7("--project", config, "--tier", "3")

        # The panel is the review at this tier, so s7's verdicts are its ballots —
        # two of them until a posted jury verdict declares a larger panel (#1015).
        self.assertEqual(with_project, ["review-verdict-1", "review-verdict-2"])
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
            [
                "closure-comment-pr",
                "closure-comment-issue",
                "review-verdict-1",
                "review-verdict-2",
                "jury-verdict",
            ],
        )
        s7 = next(
            step for step in contract["step_verification"]["steps"] if step["step_id"] == "s7"
        )
        self.assertEqual(
            [item for item in s7["required_evidence"] if "review-verdict" in item],
            ["review-verdict-1", "review-verdict-2"],
        )

    def _review_result(self, config, supplied, *flags):
        """Drive `keel review` and return its whole `--json` payload.

        It is handed exactly the verdicts the other surfaces say are required: this is the
        command that *posts* the evidence, so if it under-posts, it refuses (and the
        assertions below fail) rather than leaving a PR that can never clear its own gate.
        Since #1043 it also takes the jury flags, so it can be driven through the same
        cross-product as everything else and its `review_contract` compared directly.
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
        rc, out, err = run(
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
                *flags,
            ]
        )
        self.assertIn(rc, (0, 1), err)
        return json.loads(out)

    def _review_required_count(self, config, supplied, *flags):
        return self._review_result(config, supplied, *flags)["plan"]["required_count"]

    def test_the_bench_is_identical_across_every_no_jury_state(self):
        """The cross-product the lead pinned: the flag must not move the contract.

        keel's CI passes `--no-jury` to `evidence-verify` on every run and to `ship`/`plan`
        on none. If the bench moved with the flag, CI would demand three verdicts the ship
        run told the adapter never to produce. Every surface accepts the flags since #1043,
        which is why `keel review` is driven *with* them here rather than bare.
        """
        for name, config_text, expected in (
            (
                "jury panel",
                JURY_PANEL_CONFIG,
                # The panel's ballots are the required review verdicts (#1015); before a
                # posted verdict declares the panel size that count is the min_vendors
                # floor. The jury verdict stays, as the consensus record.
                [
                    "closure-comment-pr",
                    "closure-comment-issue",
                    "review-verdict-1",
                    "review-verdict-2",
                    "jury-verdict",
                ],
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
            for flags in ((), ("--jury",), ("--no-jury",), ("--jury-advisory",)):
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
                    # `keel review` gets the same flags and must reach the same place:
                    # it posts exactly the verdicts the evidence gate will then require,
                    # and resolves the same jury line as `plan`/`ship` (#1043).
                    review_payload = self._review_result(config, ship_reviewers["count"], *flags)
                    self.assertEqual(
                        review_payload["plan"]["required_count"], ship_reviewers["count"]
                    )
                    self.assertEqual(
                        review_payload["review_contract"]["jury"],
                        plan_contract["review_merge_contract"]["jury"],
                    )
                    self.assertEqual(
                        review_payload["review_contract"]["reviewers"]["slots"],
                        ship_reviewers["slots"],
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

    def test_the_posted_panel_count_governs_and_the_contract_publishes_the_floor(self):
        """The one place the surfaces legitimately differ, pinned rather than assumed.

        `keel plan` / `keel ship` / `keel step-verify` publish the **floor** on a
        panel tier — `jury.min_vendors` ballots — while `keel review`,
        `keel evidence-verify` and `keel merge` read the posted
        `keel.jury-verdict.v1` and require the panel that actually sat.

        `plan` is offline by construction and has no pull request to read. `ship
        --pr N` does, and could read the count beside its CI reads; it
        deliberately does not, because the floor is provably conservative
        (`_jury_panel_size` only ever raises, so a planning surface can
        under-state what will be required and never over-state it) and because
        `ship` without `--pr`, and every dry run, has to resolve the same contract
        with no verdict in reach anyway. Convergent, never contradictory: every
        surface that *can* see the panel agrees with every other one. The adapter
        is told the same thing in s7: post one verdict per ballot the panel
        returned, not `reviewers.count` verdicts.
        """
        config = self._config(JURY_PANEL_CONFIG)
        verdict = self.root / "jury-comments.json"
        verdict.write_text(
            json.dumps(
                [
                    {
                        "body": (
                            "keel.jury-verdict.v1\nhead: abc\nvendors: 2\npanelists: 3\n\n"
                            "AI Jury verdict: LGTM.\n"
                        ),
                        "author_association": "OWNER",
                        "user": {"login": "orchestrator"},
                    }
                ]
            ),
            encoding="utf-8",
        )

        plan_required, _ = self._plan_required(config)
        ship_required, _ = self._ship_required(config)
        with_verdict = self._evidence_required(config, pr_comments=verdict)

        # No panel has been measured here: the contract publishes the floor.
        floor = ["review-verdict-1", "review-verdict-2"]
        self.assertEqual([item for item in plan_required if "review-verdict" in item], floor)
        self.assertEqual([item for item in ship_required if "review-verdict" in item], floor)
        self.assertEqual(self._step_verify_s7("--project", config, "--tier", "3"), floor)
        # …and the posted verdict raises it to the panel that actually sat.
        self.assertEqual(
            [item for item in with_verdict if "review-verdict" in item],
            ["review-verdict-1", "review-verdict-2", "review-verdict-3"],
        )
        # Convergent, not contradictory: the gate only ever asks for more.
        self.assertTrue(set(floor) < set(with_verdict))

    def test_a_declared_count_below_the_floor_cannot_lower_the_requirement(self):
        """`min_vendors` is a floor: a short panel's own verdict may not relax it."""
        config = self._config(JURY_PANEL_CONFIG)
        verdict = self.root / "short-jury-comments.json"
        verdict.write_text(
            json.dumps(
                [
                    {
                        "body": (
                            "keel.jury-verdict.v1\nhead: abc\nvendors: 1\npanelists: 1\n\n"
                            "AI Jury verdict: LGTM.\n"
                        ),
                        "author_association": "OWNER",
                        "user": {"login": "orchestrator"},
                    }
                ]
            ),
            encoding="utf-8",
        )

        required = self._evidence_required(config, pr_comments=verdict)

        self.assertEqual(
            [item for item in required if "review-verdict" in item],
            ["review-verdict-1", "review-verdict-2"],
        )
        # …and the short panel does not drop the verdict it is short on, either.
        self.assertIn("jury-verdict", required)

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
        self.assertEqual(contract["review_merge_contract"]["reviewers"]["count"], 2)
        self.assertEqual(contract["review_merge_contract"]["reviewers"]["source"], "jury")
        self.assertEqual(contract["review_merge_contract"]["jury"]["mode"], "gating")
        self.assertIn("--no-jury does not apply", contract["assignment"]["warnings"][0])
        required = [item["id"] for item in contract["evidence"]["required"]]
        self.assertEqual(
            required,
            [
                "closure-comment-pr",
                "closure-comment-issue",
                "review-verdict-1",
                "review-verdict-2",
                "jury-verdict",
            ],
        )

    def test_keel_review_resolves_the_same_jury_line_as_every_other_surface(self):
        """The #1043 divergence, on the only config that can show it.

        A jury *panel* tier can never expose it — the panel outranks every flag — and the
        810-evaluation sweep that found this reported it as jury-only and permissive-only
        for exactly that reason. On a plain, teamless tier-3 project the flags bite: the
        tier-3 auto-jury is on by default and `--no-jury` turns it off. Before #1043
        `keel review` defined none of the three, so on `--no-jury` it kept resolving
        `mode: gating` while `plan`, `ship`, `evidence-verify` and `step-verify` all
        resolved `off` — and its `--verify` re-check then demanded a `jury-verdict` the
        run had been told never to post.

        The bench is asserted alongside on purpose: this closes a jury divergence, and it
        would be a poor trade to close it by making the flags move the reviewer count.
        """
        config = self._config(PLAIN_TIER3_CONFIG)

        for flags, expected_mode in (
            ((), "gating"),
            (("--jury",), "gating"),
            (("--no-jury",), "off"),
            (("--jury-advisory",), "advisory"),
        ):
            with self.subTest(flags=flags):
                plan_required, plan_contract = self._plan_required(config, *flags)
                ship_required, ship_data = self._ship_required(config, *flags)
                evidence_required = self._evidence_required(config, *flags)
                ship_contract = ship_data["result"]["assessment"]["review_merge_contract"]
                review_payload = self._review_result(
                    config, ship_contract["reviewers"]["count"], *flags
                )
                review_contract = review_payload["review_contract"]

                # One jury line, resolved four times from the same inputs.
                self.assertEqual(
                    plan_contract["review_merge_contract"]["jury"]["mode"], expected_mode
                )
                self.assertEqual(ship_contract["jury"], review_contract["jury"])
                self.assertEqual(
                    plan_contract["review_merge_contract"]["jury"], review_contract["jury"]
                )
                # …and the gate derived from it agrees on both remaining surfaces: a
                # `jury-verdict` is required exactly when the resolved mode gates.
                gating = expected_mode == "gating"
                self.assertEqual("jury-verdict" in plan_required, gating)
                self.assertEqual("jury-verdict" in ship_required, gating)
                self.assertEqual("jury-verdict" in evidence_required, gating)
                self.assertEqual(
                    self._step_verify_step("s8", "--project", config, "--tier", "3", *flags)
                    == ["jury-verdict"],
                    gating,
                )
                # The bench did not move to buy that agreement.
                self.assertEqual(review_contract["reviewers"]["count"], 3)
                self.assertEqual(ship_contract["reviewers"]["count"], 3)
                self.assertEqual(review_payload["plan"]["required_count"], 3)

    def _jury_report(self, vendors):
        """An ai-jury JSON report (schema 1.1) with one ballot per entry in `vendors`."""
        path = self.root / "jury-report.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.1",
                    "findings": [],
                    "consensus": [],
                    "reviewers": [
                        {
                            "name": f"panelist-{position}",
                            "vendor": vendor,
                            "model": f"{vendor}-model",
                            "verdict": "LGTM",
                            "findings": [],
                            "round1_ok": True,
                            "verified_count": 0,
                        }
                        for position, vendor in enumerate(vendors, start=1)
                    ],
                }
            ),
            encoding="utf-8",
        )
        return str(path)

    def _review_from_jury(self, config, report, *flags):
        rc, out, err = run(
            [
                "review",
                config,
                "--root",
                str(self.root),
                "--pr",
                "1",
                "--from-jury",
                report,
                "--changed-file",
                "src/a.py",
                "--head-sha",
                "abc",
                "--dry-run",
                "--json",
                *flags,
            ]
        )
        self.assertIn(rc, (0, 1), err)
        return json.loads(out)

    def test_a_short_panel_downgrades_the_same_on_review_as_on_evidence_verify(self):
        """`--from-jury` must hand over the panel's *vendor span*, not only its size.

        On a non-panel tier a jury that spans fewer than `min_vendors` vendors is
        downgraded `gating -> advisory`, which drops `jury-verdict` from the required
        evidence: it sits beside a host bench that reviewed the change anyway.
        `evidence-verify` performs that downgrade from the `vendors: N` line the posted
        jury verdict carries. `keel review` holds the panel itself and so can measure the
        same number directly — and when it passed only `jury_panel_size` it measured
        nothing: a three-ballot single-vendor panel had this surface resolving `gating`
        with `jury-verdict` required while the gate reading the verdict it had just
        written resolved `advisory` and required no such thing.

        The two-vendor case is asserted alongside so this pins the *agreement*, not
        merely the downgrade — a surface that always reported `advisory` would satisfy
        half of it.
        """
        config = self._config(PLAIN_TIER3_CONFIG)

        for vendors, expected_mode in (
            (["claude", "claude", "claude"], "advisory"),
            (["claude", "codex", "claude"], "gating"),
        ):
            with self.subTest(vendors=vendors):
                report = self._jury_report(vendors)
                distinct = len(dict.fromkeys(vendors))
                comments = self.root / f"jury-comments-{distinct}.json"
                comments.write_text(
                    json.dumps(
                        [
                            {
                                "body": (
                                    "keel.jury-verdict.v1\nhead: abc\n"
                                    f"vendors: {distinct}\npanelists: {len(vendors)}\n\n"
                                    "AI Jury verdict: LGTM.\n"
                                ),
                                "author_association": "OWNER",
                                "user": {"login": "orchestrator"},
                            }
                        ]
                    ),
                    encoding="utf-8",
                )

                review_payload = self._review_from_jury(config, report)
                evidence_payload = self._evidence_payload(config, pr_comments=comments)
                evidence_ids = [
                    result["id"] for result in evidence_payload["verification"]["results"]
                ]

                review_jury = review_payload["review_contract"]["jury"]
                self.assertEqual(review_jury["mode"], expected_mode)
                self.assertEqual(review_jury["participating_vendors"], distinct)
                # The gate reading back the verdict this run would post agrees, and the
                # requirement derived from the mode agrees with it on both surfaces.
                gating = expected_mode == "gating"
                self.assertEqual(review_jury["downgraded"], not gating)
                self.assertEqual("jury-verdict" in evidence_ids, gating)
                self.assertEqual(
                    evidence_payload["contract"]["required"][-1]["id"] == "jury-verdict", gating
                )
                # The bench is untouched by any of it: a plain tier-3 bench is three.
                self.assertEqual(review_payload["plan"]["required_count"], 3)

    def test_the_jury_flags_used_to_be_rejected_by_keel_review(self):
        """Non-vacuity: the argv this suite now drives was an *argparse error* before.

        Without this, every assertion above could be satisfied by a `keel review` that
        still ignores the flags — the sweep would pass while proving nothing. Pinned two
        ways: the three flags parse (they did not, pre-#1043), and a flag that really does
        not exist still fails, so this is not asserting that the parser accepts anything.
        """
        parser = cli.build_parser()

        for flag in ("--jury", "--no-jury", "--jury-advisory"):
            with self.subTest(flag=flag):
                args = parser.parse_args(
                    ["review", "p.yaml", "--pr", "1", "--reviews", "r.json", flag]
                )
                self.assertTrue(getattr(args, flag[2:].replace("-", "_")))

        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(
                    ["review", "p.yaml", "--pr", "1", "--reviews", "r.json", "--jury-blind"]
                )

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


class TestThisModuleStaysOffline(unittest.TestCase):
    """AGENTS.md: the suite is offline and deterministic. Prove it, do not assume it.

    This module drives five CLI commands through `cli.main`, three of which shell out on
    a real host. The stubs in `setUpModule` are what keep it offline; this test is what
    keeps the stubs honest — it fails if a command reaches for a tool the module has no
    canned answer for, and it fails if credentials ever become load-bearing again.
    """

    def setUp(self):
        SPAWN_ATTEMPTS.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        for name, text in (("empty.json", "[]"), ("body.md", "Closes #1")):
            (self.root / name).write_text(text, encoding="utf-8")
        (self.root / "reviews.json").write_text("[]", encoding="utf-8")
        (self.root / "handoff.json").write_text('{"step_id": "s7"}', encoding="utf-8")
        (self.root / "report.json").write_text('{"results": []}', encoding="utf-8")
        self.config = str(self.root / "project.yaml")
        (self.root / "project.yaml").write_text(JURY_PANEL_CONFIG, encoding="utf-8")

    def _drive_every_leg(self):
        fixture = str(self.root / "empty.json")
        run(["plan", self.config, "--root", str(REPO_ROOT), "--command", "ship", "--json"])
        run(["ship", self.config, "--root", str(self.root), "--json"])
        run(
            [
                "review",
                self.config,
                "--root",
                str(self.root),
                "--pr",
                "1",
                "--reviews",
                str(self.root / "reviews.json"),
                "--changed-file",
                "src/a.py",
                "--head-sha",
                "abc",
                "--dry-run",
                "--json",
            ]
        )
        run(
            [
                "evidence-verify",
                self.config,
                "--root",
                str(self.root),
                "--pr",
                "1",
                "--changed-file",
                "src/a.py",
                "--head-sha",
                "abc",
                "--pr-body-file",
                str(self.root / "body.md"),
                "--pr-comments-json",
                fixture,
                "--issue-comments-json",
                fixture,
                "--pr-reviews-json",
                fixture,
                "--json",
            ]
        )
        run(
            [
                "step-verify",
                "--step",
                "s7",
                "--project",
                self.config,
                "--handoff-file",
                str(self.root / "handoff.json"),
                "--evidence-report",
                str(self.root / "report.json"),
                "--json",
            ]
        )

    def test_no_leg_reaches_a_credentialed_or_networked_tool(self):
        self._drive_every_leg()

        # `git` is the only tool any leg may ask for, and it is answered from memory.
        # In particular: no `gh`, so no token is validated and no runner without GitHub
        # credentials can fail here.
        self.assertTrue(SPAWN_ATTEMPTS, "the seams recorded nothing — are they still wired?")
        self.assertEqual({argv[0] for argv in SPAWN_ATTEMPTS}, {"git"})

    def test_an_unexpected_subprocess_fails_loudly(self):
        # The guard itself, so a future edit cannot weaken it into a silent pass-through.
        with self.assertRaises(AssertionError) as ctx:
            _recording_run_argv(["gh", "api", "repos/acme/widget/pulls/1"])

        self.assertIn("must stay offline", str(ctx.exception))

    def test_the_review_leg_would_have_needed_real_gh_credentials(self):
        """The regression this module's stubs exist for.

        `keel review` requires `gh` + `gh-auth`. With the real probe answering "not
        logged in" — the state of any CI runner without GitHub credentials — it exits 1
        and prints nothing to stdout, which is the `JSONDecodeError` the gate reviewer
        hit. The bench comparison is unobservable in that state, so the capability answer
        has to be injected rather than inherited from the machine.
        """

        def _detect_unauthenticated(root=".", **kwargs):
            kwargs.setdefault("which", _ON_PATH.get)
            kwargs.setdefault(
                "run", lambda *_a, **_kw: CommandResult(False, 1, "not logged in", stdout="")
            )
            return _REAL_DETECT(root, **kwargs)

        with patch("keel.runtime.detect", _detect_unauthenticated):
            rc, out, err = run(
                [
                    "review",
                    self.config,
                    "--root",
                    str(self.root),
                    "--pr",
                    "1",
                    "--reviews",
                    str(self.root / "reviews.json"),
                    "--changed-file",
                    "src/a.py",
                    "--head-sha",
                    "abc",
                    "--dry-run",
                    "--json",
                ]
            )

        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("gh-auth", err)


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


class TestEverySurfaceDefinesTheSameJuryFlags(unittest.TestCase):
    """The sixth surface, pinned where it can be seen without a merge.

    `keel merge` verifies evidence rather than resolving a contract for a host to execute,
    so it is not one of the CLI legs driven above; what it shares with the other five is
    the flag group, which `cli._add_jury_flags` now defines once. This test is what makes
    that single definition load-bearing: a surface that re-spelled one of the three, or
    picked up only two, is a divergence again — which is how `keel review` came to have
    none of them at all.
    """

    SURFACES = ("plan", "ship", "review", "step-verify", "evidence-verify", "merge")
    GROUP = ("--jury", "--jury-advisory", "--no-jury")

    def _subparsers(self):
        parser = cli.build_parser()
        action = next(
            entry
            for entry in parser._actions
            if isinstance(entry, argparse._SubParsersAction)  # noqa: SLF001
        )
        return action.choices

    def test_all_six_review_aware_surfaces_accept_the_same_three_flags(self):
        subparsers = self._subparsers()

        for name in self.SURFACES:
            with self.subTest(surface=name):
                actions = {
                    option: entry
                    for entry in subparsers[name]._actions  # noqa: SLF001
                    for option in entry.option_strings
                    if option in self.GROUP
                }
                self.assertEqual(tuple(sorted(actions)), self.GROUP)
                for option, entry in actions.items():
                    self.assertIs(entry.default, False, option)
                    self.assertEqual(entry.nargs, 0, option)

    def test_the_group_is_defined_in_exactly_one_place(self):
        """An AST sweep, the same shape as the `resolve_review_contract` one below it.

        Six copies of one flag group is how the review parser ended up with zero: nothing
        failed when a surface was added without them. `--jury` may now be spelled only
        inside `_add_jury_flags`.
        """
        tree = ast.parse((REPO_ROOT / "src/keel/cli.py").read_text(encoding="utf-8"))
        definitions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in ("--jury", "--no-jury")
        ]
        helper = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_add_jury_flags"
        )
        span = range(helper.lineno, helper.end_lineno + 1)
        stray = [node.lineno for node in definitions if node.lineno not in span]
        self.assertEqual(stray, [], "jury flags declared outside cli._add_jury_flags")


if __name__ == "__main__":
    unittest.main()
