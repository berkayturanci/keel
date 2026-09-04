"""Probe the panel before s7 dispatches it; fall back, or refuse, on the record (#1066).

On a tier whose `knobs.team.review.by_tier` names `jury`, the panel *is* the review and
#1014 round 3 made it so no flag can take it off. That is right while the panel can run.
When it cannot — an agent CLI missing, unauthenticated, or out of quota — the tier has no
way forward at all, and a single-maintainer project hits that routinely.

These tests hold the three answers the issue asks for, and the line between them:

* every jury agent available → **nothing changes**, the ballots are the review;
* unstaffable + `on_unavailable: fallback` → a host bench of the *same size*, the same
  evidence contract, and the reason recorded everywhere a reader looks;
* unstaffable + `on_unavailable: block` → the run refuses, naming the seats.

The last one is what keeps this from being ai-jury #682 in a new place: a panel that
quietly collapsed and still reported success. Nothing here is allowed to be silent.

Offline and deterministic, per AGENTS.md: the probe is the one machine-dependent input to
the reviewer bench, so every test supplies it from memory. A test that let the real probe
run would resolve one bench on a laptop with three agent CLIs installed and a different
one on a CI runner with none.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from keel import cli, closure, evidence, juryavail, ledger, providerprobe, runtime, ship
from keel import config as cfg
from keel import team as team_policy

PANEL_CONFIG = """
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
    jury:
      mode: gating
      min_vendors: 2%s
"""

#: A tier that names host seats, so the probe must never fire for it.
BENCH_CONFIG = """
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
"""


def _row(name, vendor, available, reason):
    return {"name": name, "vendor": vendor, "available": available, "reason": reason}


def _report(*rows):
    return {
        "schema_version": "keel.providers.v1",
        "providers": list(rows),
        "available": sum(1 for row in rows if row["available"]),
        "total": len(rows),
    }


#: Two vendors on PATH — the panel can be convened, so behaviour is unchanged.
STAFFED = _report(
    _row("claude", "claude", True, "/usr/bin/claude"),
    _row("codex", "codex", True, "/usr/bin/codex"),
)

#: One agent CLI installed and the rest missing or unauthenticated — the machine the
#: issue is about.
UNSTAFFED = _report(
    _row("claude", "claude", True, "/usr/bin/claude"),
    _row("codex", "codex", False, "codex not found on PATH"),
    _row("anthropic-api", "anthropic-api", False, "ANTHROPIC_API_KEY is not set"),
)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _capable():
    return runtime.CapabilityReport(
        (
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", True, "ok", "test"),
            runtime.Capability("gh-auth", True, "ok", "test"),
        )
    )


class TestTheProbeVerdict(unittest.TestCase):
    """`juryavail.assess` — the pure half, reading `keel doctor --providers`' own report."""

    def test_two_available_vendors_staff_the_panel(self):
        verdict = juryavail.assess(STAFFED, min_vendors=2)

        self.assertTrue(verdict.staffable)
        self.assertEqual(verdict.decision, juryavail.DECISION_AVAILABLE)
        self.assertEqual(verdict.available_vendors, ("claude", "codex"))
        self.assertIn("staffable", verdict.reason)

    def test_one_available_vendor_does_not(self):
        verdict = juryavail.assess(UNSTAFFED, min_vendors=2)

        self.assertFalse(verdict.staffable)
        self.assertEqual(verdict.decision, juryavail.DECISION_FALLBACK)
        self.assertEqual(
            [seat.provider for seat in verdict.unavailable], ["codex", "anthropic-api"]
        )
        # The reason names the seats, because "the panel is unavailable" without them
        # sends the operator back to `keel doctor --providers` to learn what this run
        # already measured.
        self.assertIn("codex not found on PATH", verdict.reason)
        self.assertIn("ANTHROPIC_API_KEY is not set", verdict.reason)

    def test_a_raised_min_vendors_can_make_a_two_vendor_machine_short(self):
        self.assertFalse(juryavail.assess(STAFFED, min_vendors=3).staffable)

    def test_the_policy_decides_what_unstaffable_means(self):
        blocking = juryavail.assess(UNSTAFFED, min_vendors=2, policy="block")

        self.assertEqual(blocking.decision, juryavail.DECISION_BLOCK)
        self.assertEqual(blocking.policy, "block")

    def test_an_unknown_policy_reads_as_unset(self):
        self.assertEqual(juryavail.assess(STAFFED, policy="maybe").policy, "fallback")

    def test_two_entries_on_one_vendor_are_one_opinion(self):
        """A panel spans *vendors*: two profiles shelling out to one CLI is one voice."""
        report = _report(
            _row("claude", "claude", True, "/usr/bin/claude"),
            _row("claude-review", "claude", True, "/usr/bin/claude"),
        )

        verdict = juryavail.assess(report, min_vendors=2)

        self.assertEqual(verdict.available_vendors, ("claude",))
        self.assertFalse(verdict.staffable)

    def test_a_malformed_report_is_not_staffable_rather_than_an_exception(self):
        for report in (None, {}, {"providers": "nonsense"}, {"providers": ["nope"]}):
            with self.subTest(report=report):
                verdict = juryavail.assess(report, min_vendors=2)

                self.assertFalse(verdict.staffable)
                self.assertEqual(verdict.available_vendors, ())
                self.assertIn("none", verdict.reason)

    def test_a_row_with_no_name_still_reports_a_seat(self):
        verdict = juryavail.assess(_report({"available": False, "vendor": "  "}), min_vendors=2)

        seat = verdict.unavailable[0]
        self.assertEqual(seat.provider, "(unnamed provider)")
        self.assertEqual(seat.vendor, "(unnamed provider)")
        self.assertEqual(seat.reason, "no reason reported")

    def test_a_row_named_only_by_its_vendor_falls_back_to_that(self):
        verdict = juryavail.assess(_report({"available": True, "vendor": "codex"}), min_vendors=1)

        self.assertEqual(verdict.available_vendors, ("codex",))

    def test_min_vendors_never_drops_below_one(self):
        """Zero required vendors would make an empty machine "staffable"."""
        self.assertEqual(juryavail.assess(STAFFED, min_vendors=0).required_vendors, 1)

    def test_the_record_is_json_stable(self):
        record = juryavail.assess(UNSTAFFED, min_vendors=2).as_dict()

        json.dumps(record)
        self.assertEqual(record["decision"], "fallback")
        self.assertTrue(record["probed"])
        self.assertEqual(record["required_vendors"], 2)
        self.assertEqual(record["unavailable"][0]["provider"], "codex")


class TestTheRefusalMessage(unittest.TestCase):
    def test_it_names_every_unavailable_seat(self):
        message = juryavail.refusal_message(
            juryavail.assess(UNSTAFFED, min_vendors=2, policy="block").as_dict(),
            source="team.review.by_tier.3",
        )

        self.assertIn("team.review.by_tier.3", message)
        self.assertIn("codex: codex not found on PATH", message)
        self.assertIn("anthropic-api: ANTHROPIC_API_KEY is not set", message)
        self.assertIn("on_unavailable: fallback", message)

    def test_a_record_with_nothing_probed_still_reads(self):
        message = juryavail.refusal_message({}, source="team.review.default")

        self.assertIn("(no provider was probed)", message)
        self.assertIn("none", message)

    def test_malformed_fields_degrade_rather_than_raise(self):
        message = juryavail.refusal_message(
            {"unavailable": ["not a seat", {"provider": "codex", "reason": "gone"}],
             "available_vendors": "claude"},
            source="team.review.default",
        )

        self.assertIn("codex: gone", message)
        self.assertNotIn("not a seat", message)
        # A bare string is not a vendor list; it reads as none rather than as five.
        self.assertIn("0 vendor(s) available (none)", message)


class TestTheConfiguredAllowance(unittest.TestCase):
    """`knobs.team.jury.on_unavailable` — parsed, validated, canonical, hashed."""

    def test_it_parses_and_defaults(self):
        self.assertIsNone(team_policy.parse_team({}).jury_on_unavailable)
        self.assertEqual(
            team_policy.parse_team({"jury": {"on_unavailable": "block"}}).jury_on_unavailable,
            "block",
        )
        self.assertEqual(team_policy.jury_on_unavailable(None), "fallback")
        self.assertEqual(team_policy.jury_on_unavailable("block"), "block")
        self.assertEqual(team_policy.jury_on_unavailable("shrug"), "fallback")

    def test_an_unset_setting_leaves_the_canonical_block_untouched(self):
        """The `config_hash` guarantee: a project that never names it is unchanged."""
        without = team_policy.canonical(team_policy.parse_team({"jury": {"mode": "gating"}}))
        with_it = team_policy.canonical(
            team_policy.parse_team({"jury": {"mode": "gating", "on_unavailable": "fallback"}})
        )

        self.assertEqual(without, {"team": {"jury": {"mode": "gating"}}})
        self.assertNotEqual(without, with_it)
        self.assertEqual(with_it["team"]["jury"]["on_unavailable"], "fallback")

    def test_an_unknown_policy_is_a_validation_error(self):
        issues = team_policy.team_issues(
            {"review": {"by_tier": {"3": "jury"}}, "jury": {"on_unavailable": "shrug"}},
            source="knobs.team",
        )

        self.assertTrue(
            [issue for issue in issues if "jury.on_unavailable: unknown policy 'shrug'" in issue],
            issues,
        )


class TestTheBenchTheProbeSeats(unittest.TestCase):
    """`team.resolve_assignment` — the same count, different reviewers, on the record."""

    def _policy(self, **jury):
        return team_policy.parse_team({"review": {"by_tier": {"3": "jury"}}, "jury": jury})

    def _resolve(self, report, *, policy=None, default_count=3, **kwargs):
        availability = (
            None
            if report is None
            else juryavail.assess(report, min_vendors=2, policy=policy).as_dict()
        )
        return team_policy.resolve_assignment(
            self._policy(mode="gating"),
            tier=3,
            default_count=default_count,
            jury_availability=availability,
            **kwargs,
        )

    def test_a_staffable_panel_changes_nothing(self):
        assignment = self._resolve(STAFFED)

        self.assertEqual(assignment["review_panel"], "jury")
        self.assertEqual(assignment["reviewer_count"], 0)
        self.assertTrue(assignment["jury"]["panel_is_review"])
        self.assertEqual(assignment["warnings"], [])

    def test_no_probe_at_all_changes_nothing_either(self):
        self.assertEqual(self._resolve(None)["review_panel"], "jury")

    def test_an_unstaffable_panel_seats_the_tiers_own_count(self):
        assignment = self._resolve(UNSTAFFED)

        self.assertEqual(assignment["review_panel"], "reviewers")
        self.assertEqual(assignment["reviewer_count"], 3)
        self.assertFalse(assignment["jury"]["panel_is_review"])
        # …but the tier still *asked* for a panel, and the record says so.
        self.assertTrue(assignment["jury"]["panel_configured"])
        self.assertEqual(assignment["reviewer_source"], "jury-fallback")
        self.assertEqual(
            [seat["provider"] for seat in assignment["reviewers"]], ["claude"] * 3
        )
        self.assertEqual(
            [seat["slot"] for seat in assignment["reviewers"]], ["A", "B", "C"]
        )

    def test_the_fallback_is_never_silent(self):
        assignment = self._resolve(UNSTAFFED)

        warning = "\n".join(assignment["warnings"])
        self.assertIn("cannot be staffed here", warning)
        self.assertIn("host bench of 3 seat(s)", warning)
        self.assertIn("codex not found on PATH", warning)
        # The pad warning's advice ("name the extra seats") is wrong on a fallback bench:
        # this tier *did* name its reviewers — it named the panel.
        self.assertNotIn("name the extra seats", warning)
        self.assertEqual(assignment["jury"]["availability"]["decision"], "fallback")

    def test_block_keeps_the_panel_and_the_run_is_refused_above(self):
        assignment = self._resolve(UNSTAFFED, policy="block")

        self.assertEqual(assignment["review_panel"], "jury")
        self.assertEqual(assignment["reviewer_count"], 0)
        self.assertEqual(assignment["jury"]["availability"]["decision"], "block")
        self.assertEqual(assignment["jury"]["on_unavailable"], "fallback")

    def test_reviewers_cannot_shrink_the_fallback_bench(self):
        """The guarantee: a fallback changes *who* sat, never *how many*.

        `--reviewers` is inert on a panel tier — the panel is the review, so there are no
        host slots to size — and a failed probe may not turn that inert flag into a live
        one. If it could, the same operator flag would go from ignored to policy-lowering
        purely because an agent CLI was missing: `--reviewers 2` on a tier-3 fallback would
        seat two reviewers and publish a two-verdict evidence requirement where the tier
        asks for three.
        """
        for override in (2, 1):
            with self.subTest(reviewers=override):
                assignment = self._resolve(UNSTAFFED, reviewer_override=override)

                self.assertEqual(assignment["reviewer_count"], 3)
                self.assertEqual(assignment["reviewer_source"], "jury-fallback")
                warning = "\n".join(assignment["warnings"])
                self.assertIn("host bench of 3 seat(s)", warning)
                self.assertIn(f"--reviewers {override} ignored", warning)
                self.assertIn("not how many", warning)

    def test_reviewers_cannot_raise_it_either(self):
        """Ignored is ignored: the tier's own count stands in both directions."""
        assignment = self._resolve(UNSTAFFED, reviewer_override=3, default_count=2)

        self.assertEqual(assignment["reviewer_count"], 2)
        self.assertIn("--reviewers 3 ignored", "\n".join(assignment["warnings"]))

    def test_the_flag_is_ignored_whether_or_not_the_panel_can_sit(self):
        available = self._resolve(STAFFED, reviewer_override=2)
        fell_back = self._resolve(UNSTAFFED, reviewer_override=2)

        self.assertIn("--reviewers 2 ignored", "\n".join(available["warnings"]))
        self.assertIn("--reviewers 2 ignored", "\n".join(fell_back["warnings"]))
        # A panel tier publishes no host slots at all; the fallback publishes the tier's
        # three. Neither publishes the two the flag asked for.
        self.assertEqual(available["reviewer_count"], 0)
        self.assertEqual(fell_back["reviewer_count"], 3)

    def test_a_probe_record_with_no_reason_still_warns(self):
        assignment = team_policy.resolve_assignment(
            self._policy(mode="gating"),
            tier=3,
            default_count=3,
            jury_availability={"decision": "fallback"},
        )

        self.assertIn("the probe reported no detail", "\n".join(assignment["warnings"]))

    def test_a_non_mapping_availability_is_ignored(self):
        assignment = team_policy.resolve_assignment(
            self._policy(mode="gating"), tier=3, default_count=3, jury_availability="nope"
        )

        self.assertEqual(assignment["review_panel"], "jury")


class TestTheContractTheFallbackPublishes(unittest.TestCase):
    """`ship.resolve_review_contract` — same seat count, and no verdict nobody can post."""

    def _contract(self, report, *, policy=None, reviewer_override=None):
        availability = juryavail.assess(report, min_vendors=2, policy=policy).as_dict()
        assignment = team_policy.resolve_assignment(
            team_policy.parse_team(
                {"review": {"by_tier": {"3": "jury"}}, "jury": {"mode": "gating"}}
            ),
            tier=3,
            default_count=3,
            reviewer_override=reviewer_override,
            jury_availability=availability,
        )
        return ship.resolve_review_contract(
            tier=3, assignment=assignment, reviewer_override=reviewer_override
        )

    def test_a_staffable_panel_still_requires_its_verdict(self):
        contract = self._contract(STAFFED)

        self.assertEqual(contract["reviewers"]["panel"], "jury")
        self.assertEqual(contract["jury"]["mode"], "gating")
        self.assertFalse(contract["jury"]["panel_unavailable"])
        self.assertTrue(contract["jury"]["availability"]["staffable"])

    def test_the_fallback_requires_the_bench_and_not_the_panels_verdict(self):
        contract = self._contract(UNSTAFFED)

        self.assertEqual(contract["reviewers"]["panel"], "reviewers")
        self.assertEqual(contract["reviewers"]["count"], 3)
        self.assertEqual(len(contract["reviewers"]["focuses"]), 3)
        # The jury is off because there is no panel to produce a verdict — not because a
        # flag said so. Leaving tier-3's auto-jury standing would demand a `jury-verdict`
        # from a panel this machine just established it cannot convene.
        self.assertFalse(contract["jury"]["enabled"])
        self.assertEqual(contract["jury"]["mode"], "off")
        self.assertIn("host bench fallback", contract["jury"]["reason"])
        self.assertTrue(contract["jury"]["panel_unavailable"])

    def test_reviewers_cannot_shrink_the_published_evidence_requirement(self):
        """The end of the guarantee a caller can actually see: three verdicts, still."""
        contract = self._contract(UNSTAFFED, reviewer_override=2)

        self.assertEqual(contract["reviewers"]["count"], 3)
        self.assertEqual(contract["reviewers"]["minimum_lgtm"], 3)
        required = [item.id for item in evidence.required_items(contract)]
        self.assertIn("review-verdict-3", required)
        self.assertNotIn("jury-verdict", required)

    def test_a_contract_with_no_assignment_records_no_availability(self):
        contract = ship.resolve_review_contract(tier=3)

        self.assertIsNone(contract["jury"]["availability"])
        self.assertFalse(contract["jury"]["panel_unavailable"])

    def test_panel_fell_back_is_total(self):
        self.assertFalse(ship.panel_fell_back(None))
        self.assertFalse(ship.panel_fell_back({}))
        self.assertFalse(ship.panel_fell_back({"jury": {"availability": None}}))
        self.assertTrue(ship.panel_fell_back({"jury": {"availability": {"decision": "fallback"}}}))

    def test_resolve_jury_puts_unavailability_above_the_tier_3_auto(self):
        record = ship.resolve_jury(tier=3, panel_unavailable=True)

        self.assertFalse(record["enabled"])
        self.assertEqual(record["mode"], "off")
        self.assertIn("team.jury.on_unavailable", record["reason"])


class TestTheProbeIsOnlyRunWhenItMatters(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)

    def _config(self, text):
        path = self.root / "project.yaml"
        path.write_text(text, encoding="utf-8")
        return cfg.load_config(str(path))

    def test_a_host_bench_tier_never_probes(self):
        calls = []

        result = providerprobe.jury_availability(
            self._config(BENCH_CONFIG), tier=3, _probe=calls.append
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_a_panel_tier_probes_and_returns_the_verdict(self):
        result = providerprobe.jury_availability(
            self._config(PANEL_CONFIG % ""), tier=3, _probe=lambda _c: UNSTAFFED
        )

        self.assertEqual(result["decision"], "fallback")
        self.assertEqual(result["required_vendors"], 2)

    def test_the_configured_min_vendors_raises_the_bar(self):
        result = providerprobe.jury_availability(
            self._config(PANEL_CONFIG % ""), tier=3, _probe=lambda _c: STAFFED
        )
        self.assertTrue(result["staffable"])

        raised = providerprobe.jury_availability(
            self._config(PANEL_CONFIG.replace("min_vendors: 2%s", "min_vendors: 3")),
            tier=3,
            _probe=lambda _c: STAFFED,
        )
        self.assertFalse(raised["staffable"])

    def test_the_default_probe_is_the_doctor_providers_one(self):
        """The seam defaults to `providerprobe.collect` — the machinery `doctor` prints."""
        with patch("keel.providerprobe.collect", return_value=STAFFED) as collect:
            result = providerprobe.jury_availability(self._config(PANEL_CONFIG % ""), tier=3)

        self.assertTrue(collect.called)
        self.assertTrue(result["staffable"])


class TestTheRecordAReaderSees(unittest.TestCase):
    """The ledger and the closure comment say plainly which review this was."""

    def test_the_closure_comment_names_the_fallback_and_the_seats(self):
        rendered = closure.render_closure_comment(
            {
                "run_context": {
                    "jury_mode": "off",
                    "jury_panel": juryavail.assess(UNSTAFFED, min_vendors=2).as_dict(),
                }
            }
        )

        self.assertIn("- **Jury panel:** panel unavailable", rendered)
        self.assertIn("a host bench of the same size reviewed instead", rendered)
        self.assertIn("codex: codex not found on PATH", rendered)

    def test_it_names_a_blocked_run_differently(self):
        rendered = closure.render_closure_comment(
            {
                "run_context": {
                    "jury_panel": juryavail.assess(
                        UNSTAFFED, min_vendors=2, policy="block"
                    ).as_dict()
                }
            }
        )

        self.assertIn("the run was refused", rendered)

    def test_a_seatless_record_still_renders_one_line(self):
        rendered = closure.render_closure_comment(
            {"run_context": {"jury_panel": {"decision": "fallback", "unavailable": ["junk"]}}}
        )

        self.assertIn("- **Jury panel:** panel unavailable", rendered)
        self.assertNotIn("junk", rendered)

    def test_a_staffable_panel_adds_no_line_at_all(self):
        """Every closure comment keel has ever posted stays byte-identical."""
        for context in (
            {"jury_mode": "gating"},
            {"jury_mode": "gating", "jury_panel": juryavail.assess(STAFFED).as_dict()},
            {"jury_mode": "gating", "jury_panel": "nonsense"},
        ):
            with self.subTest(context=context):
                rendered = closure.render_closure_comment({"run_context": context})

                self.assertNotIn("Jury panel", rendered)

    def test_the_ledger_carries_the_probe_and_degrades_to_none(self):
        record = ledger.build_ship_run_record(
            command="ship",
            run_id="r1",
            base_branch="main",
            changed_files=[],
            declared_files=None,
            issue_intake=None,
            outcomes=(),
            verdict=_verdict(),
            assessment=_assessment(),
            jury_panel=juryavail.assess(UNSTAFFED, min_vendors=2).as_dict(),
        )
        self.assertEqual(record["run_context"]["jury_panel"]["decision"], "fallback")

        bare = ledger.build_ship_run_record(
            command="ship",
            run_id="r1",
            base_branch="main",
            changed_files=[],
            declared_files=None,
            issue_intake=None,
            outcomes=(),
            verdict=_verdict(),
            assessment=_assessment(),
            jury_panel="nonsense",
        )
        self.assertIsNone(bare["run_context"]["jury_panel"])


def _verdict():
    from keel.findings import Verdict

    return Verdict(blocked=False, findings=(), counts={})


def _assessment():
    return ship.ShipAssessment(
        tier=3,
        reviewers=3,
        window_open=True,
        ci_ok=True,
        merge=ship.MergeDecision("merge", "clear to merge"),
        review_contract={},
        halted=False,
        bypassed_window=False,
    )


class TestTheWholeRunEndToEnd(unittest.TestCase):
    """Through the CLI: the three outcomes, and every surface agreeing on each."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "empty.json").write_text("[]", encoding="utf-8")
        (self.root / "body.md").write_text("Closes #1", encoding="utf-8")
        detect = patch("keel.cli.runtime.detect", return_value=_capable())
        detect.start()
        self.addCleanup(detect.stop)

    def _config(self, text):
        path = self.root / "project.yaml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    @contextlib.contextmanager
    def _probe(self, report):
        with patch("keel.providerprobe.collect", lambda *_a, **_kw: dict(report)):
            yield

    def _plan(self, config):
        rc, out, err = run(
            ["plan", config, "--root", str(self.root), "--command", "ship", "--tier", "3", "--json"]
        )
        self.assertEqual(rc, 0, err)
        return json.loads(out)["contract"]

    def _ship(self, config):
        rc, out, err = run(["ship", config, "--root", str(self.root), "--json"])
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["result"]["assessment"]["tier"], 3)
        return data

    def _evidence(self, config):
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
            ]
        )
        self.assertIn(rc, (0, 1), err)
        return json.loads(out)

    def test_with_every_agent_available_nothing_changes(self):
        config = self._config(PANEL_CONFIG % "")
        with self._probe(STAFFED):
            plan = self._plan(config)
            ship_data = self._ship(config)
            evidence = self._evidence(config)

        self.assertEqual(plan["assignment"]["review_panel"], "jury")
        self.assertEqual(
            [item["id"] for item in plan["evidence"]["required"]],
            [
                "closure-comment-pr",
                "closure-comment-issue",
                "review-verdict-1",
                "review-verdict-2",
                "jury-verdict",
            ],
        )
        self.assertEqual(
            plan["review_merge_contract"],
            ship_data["result"]["assessment"]["review_merge_contract"],
        )
        self.assertIn(
            "jury-verdict", [result["id"] for result in evidence["verification"]["results"]]
        )

    def test_an_unstaffable_panel_falls_back_to_three_host_seats(self):
        config = self._config(PANEL_CONFIG % "")
        with self._probe(UNSTAFFED):
            plan = self._plan(config)
            ship_data = self._ship(config)
            evidence = self._evidence(config)

        assignment = plan["assignment"]
        self.assertEqual(assignment["review_panel"], "reviewers")
        self.assertEqual(assignment["reviewer_count"], 3)
        self.assertEqual(assignment["jury"]["availability"]["decision"], "fallback")

        # The tier's own seat count, and its own evidence — no jury verdict, because no
        # panel sat. The requirement moved sideways, never down.
        required = [item["id"] for item in plan["evidence"]["required"]]
        self.assertEqual(
            required,
            [
                "closure-comment-pr",
                "closure-comment-issue",
                "review-verdict-1",
                "review-verdict-2",
                "review-verdict-3",
            ],
        )
        # …and all three surfaces say the same thing about it.
        self.assertEqual(
            plan["review_merge_contract"],
            ship_data["result"]["assessment"]["review_merge_contract"],
        )
        self.assertEqual(
            sorted(result["id"] for result in evidence["verification"]["results"]),
            sorted(required),
        )
        self.assertEqual(
            ship_data["result"]["assessment"]["assignment"]["reviewer_source"], "jury-fallback"
        )

    def test_block_refuses_the_run_and_names_the_seats(self):
        config = self._config(PANEL_CONFIG % "\n      on_unavailable: block")
        with self._probe(UNSTAFFED):
            rc, out, err = run(["ship", config, "--root", str(self.root), "--json"])

        self.assertEqual(rc, 1, out)
        self.assertIn("team.review.by_tier.3", err)
        self.assertIn("cannot be staffed here", err)
        self.assertIn("codex: codex not found on PATH", err)
        self.assertIn("anthropic-api: ANTHROPIC_API_KEY is not set", err)

    def test_block_refuses_every_review_aware_surface(self):
        config = self._config(PANEL_CONFIG % "\n      on_unavailable: block")
        with self._probe(UNSTAFFED):
            for argv in (
                [
                    "plan",
                    config,
                    "--root",
                    str(self.root),
                    "--command",
                    "ship",
                    "--tier",
                    "3",
                    "--json",
                ],
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
                ],
            ):
                with self.subTest(command=argv[0]):
                    rc, _out, err = run(argv)

                    self.assertEqual(rc, 1)
                    self.assertIn("cannot be staffed here", err)

    def test_a_staffable_panel_under_block_runs_normally(self):
        config = self._config(PANEL_CONFIG % "\n      on_unavailable: block")
        with self._probe(STAFFED):
            plan = self._plan(config)

        self.assertEqual(plan["assignment"]["review_panel"], "jury")
        self.assertEqual(plan["assignment"]["jury"]["on_unavailable"], "block")

    def test_a_project_with_no_panel_is_untouched(self):
        """The probe never fires, so nothing about this project's runs can move."""
        config = self._config(BENCH_CONFIG)
        with patch("keel.providerprobe.collect", side_effect=AssertionError("probed")):
            plan = self._plan(config)

        self.assertEqual(plan["assignment"]["review_panel"], "reviewers")
        self.assertIsNone(plan["review_merge_contract"]["jury"]["availability"])


if __name__ == "__main__":
    unittest.main()
