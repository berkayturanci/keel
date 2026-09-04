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

#: The `jury` binary s7 dispatches, present and runnable but reporting no panel of its own
#: — an ai-jury too old for `--doctor --json`, so the verdict reads keel's inventory. That
#: is the shape most of these tests want: they are about the *vendor* half.
RUNNER = juryavail.Runner(True, "/usr/bin/jury (no readable --doctor report)")

#: …and the host the second review round is about: agent CLIs installed, no panel runner.
NO_RUNNER = juryavail.Runner(False, "jury not found on PATH")


def _assess(report, *, runner=RUNNER, **kwargs):
    """`juryavail.assess` with the runner half supplied, since most tests vary the other."""
    return juryavail.assess(report, runner=runner, **kwargs)


def _runner_probe(runner=RUNNER):
    return lambda: runner


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


#: What `jury --doctor --json` prints: ai-jury's own readiness document, naming the panel
#: it would actually convene. The rows carry the same `name`/`vendor`/`available`/`reason`
#: shape keel's provider report does, which is why one reader serves both.
def _doctor(*agents, version="1.16.0"):
    return {
        "schema_version": "ai-jury.doctor.v1",
        "tool_version": version,
        "ready": any(agent["available"] for agent in agents),
        "agents": list(agents),
    }


DOCTOR_STAFFED = _doctor(
    _row("claude", "anthropic", True, None),
    _row("codex", "openai", True, None),
)

DOCTOR_UNSTAFFED = _doctor(
    _row("claude", "anthropic", True, None),
    _row("codex", "openai", False, "codex not found on PATH"),
)


class TestTheRunnerIsPartOfTheQuestion(unittest.TestCase):
    """The panel s7 dispatches is the `jury` binary, not keel's delegate inventory (#1066).

    Round 2's finding: feeding `keel doctor --providers` into `assess` answered "are some
    agent CLIs usable here", which is not the question. On a host with `claude` and `codex`
    installed and no `jury` on PATH the panel was reported *available*, the panel bench was
    published, and s7 failed at the invocation instead of taking the project's configured
    fallback or block path.
    """

    def test_no_jury_binary_is_not_a_staffable_panel(self):
        verdict = _assess(STAFFED, runner=NO_RUNNER, min_vendors=2)

        self.assertFalse(verdict.staffable)
        self.assertEqual(verdict.decision, juryavail.DECISION_FALLBACK)
        # The vendor half was fine — which is exactly why the numbers alone misled.
        self.assertEqual(verdict.available_vendors, ("claude", "codex"))
        self.assertIn("runner s7 dispatches is not usable here", verdict.reason)

    def test_the_missing_runner_is_the_first_seat_a_reader_sees(self):
        verdict = _assess(STAFFED, runner=NO_RUNNER, min_vendors=2)

        seat = verdict.unavailable[0]
        self.assertEqual(seat.provider, "jury")
        self.assertEqual(seat.vendor, "ai-jury")
        self.assertEqual(seat.reason, "jury not found on PATH")
        # …so every surface that renders the seats names it without extra plumbing.
        self.assertIn("jury: jury not found on PATH", juryavail.refusal_message(
            verdict.as_dict(), source="team.review.by_tier.3"
        ))
        self.assertIn(
            "jury: jury not found on PATH",
            closure.render_closure_comment({"run_context": {"jury_panel": verdict.as_dict()}}),
        )

    def test_an_unprobed_runner_reads_as_unusable_not_as_fine(self):
        """A caller that forgets to probe must not get a staffable panel by default."""
        verdict = juryavail.assess(STAFFED, min_vendors=2)

        self.assertFalse(verdict.staffable)
        self.assertFalse(verdict.as_dict()["runner"]["usable"])

    def test_the_runners_own_report_is_the_inventory_when_it_has_one(self):
        """ai-jury is the authority on the panel ai-jury would convene."""
        runner = juryavail.Runner(True, "/usr/bin/jury (ai-jury 1.16.0)", DOCTOR_STAFFED)

        # keel's own report is deliberately the *unstaffed* one: it must not be consulted.
        verdict = _assess(UNSTAFFED, runner=runner, min_vendors=2)

        self.assertTrue(verdict.staffable)
        self.assertEqual(verdict.available_vendors, ("anthropic", "openai"))
        self.assertEqual(verdict.inventory, "jury --doctor")
        self.assertEqual(verdict.as_dict()["inventory"], "jury --doctor")

    def test_a_short_panel_in_the_runners_own_report_is_still_short(self):
        runner = juryavail.Runner(True, "/usr/bin/jury", DOCTOR_UNSTAFFED)

        verdict = _assess(STAFFED, runner=runner, min_vendors=2)

        self.assertFalse(verdict.staffable)
        self.assertEqual([seat.provider for seat in verdict.unavailable], ["codex"])

    def test_a_runner_with_no_report_falls_back_to_keels_inventory(self):
        """An ai-jury too old for `--doctor --json` still convenes panels."""
        verdict = _assess(STAFFED, runner=RUNNER, min_vendors=2)

        self.assertTrue(verdict.staffable)
        self.assertEqual(verdict.inventory, "keel doctor --providers")

    def test_a_malformed_agents_list_reads_as_no_report(self):
        for doctor in ({"agents": "nonsense"}, {"schema_version": "ai-jury.doctor.v1"}):
            with self.subTest(doctor=doctor):
                runner = juryavail.Runner(True, "/usr/bin/jury", doctor)

                verdict = _assess(STAFFED, runner=runner, min_vendors=2)

                self.assertEqual(verdict.inventory, "keel doctor --providers")
                self.assertTrue(verdict.staffable)


class TestTheRunnerProbe(unittest.TestCase):
    """`providerprobe.probe_jury_runner` — the thin-I/O half, every edge injected."""

    def _result(self, *, ok=True, code=0, stdout="", timed_out=False):
        from keel.runner import CommandResult

        return CommandResult(
            ok=ok, code=code, output=stdout, timed_out=timed_out, stdout=stdout, stderr=""
        )

    def _probe(self, *, which="/usr/bin/jury", result=None):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return result if result is not None else self._result()

        runner = providerprobe.probe_jury_runner(_which=lambda _c: which, _run=run)
        return runner, calls

    def test_a_missing_binary_is_reported_not_raised(self):
        runner, calls = self._probe(which=None)

        self.assertFalse(runner.usable)
        self.assertEqual(runner.reason, "jury not found on PATH")
        self.assertEqual(calls, [], "nothing to run when there is no binary")

    def test_a_readable_doctor_report_becomes_the_panel(self):
        runner, calls = self._probe(result=self._result(stdout=json.dumps(DOCTOR_STAFFED)))

        self.assertEqual(calls, [["jury", "--doctor", "--json"]])
        self.assertTrue(runner.usable)
        self.assertIn("ai-jury 1.16.0", runner.reason)
        self.assertEqual(len(runner.panel_rows), 2)

    def test_log_lines_after_the_report_do_not_lose_it(self):
        stdout = json.dumps(DOCTOR_STAFFED) + "\n[jury] done\n"

        runner, _calls = self._probe(result=self._result(stdout=stdout))

        self.assertIsNotNone(runner.panel_rows)

    def test_a_timeout_is_an_unusable_runner(self):
        runner, _calls = self._probe(result=self._result(ok=False, code=124, timed_out=True))

        self.assertFalse(runner.usable)
        self.assertIn("timed out after 30s", runner.reason)

    def test_a_crash_with_no_report_is_an_unusable_runner(self):
        runner, _calls = self._probe(result=self._result(ok=False, code=2, stdout="boom"))

        self.assertFalse(runner.usable)
        self.assertIn("failed (exit 2)", runner.reason)

    def test_unreadable_output_from_a_clean_exit_is_still_a_usable_runner(self):
        for stdout in (
            "",
            "not json",
            "[1, 2]",
            json.dumps({"schema_version": "something.else"}),
        ):
            with self.subTest(stdout=stdout):
                runner, _calls = self._probe(result=self._result(stdout=stdout))

                self.assertTrue(runner.usable)
                self.assertIsNone(runner.panel_rows)

    def test_a_report_with_no_version_still_names_the_path(self):
        stdout = json.dumps({"schema_version": "ai-jury.doctor.v1", "agents": []})

        runner, _calls = self._probe(result=self._result(stdout=stdout))

        self.assertIn("unknown version", runner.reason)


class TestTheProbeVerdict(unittest.TestCase):
    """`juryavail.assess` — the pure half, reading `keel doctor --providers`' own report."""

    def test_two_available_vendors_staff_the_panel(self):
        verdict = _assess(STAFFED, min_vendors=2)

        self.assertTrue(verdict.staffable)
        self.assertEqual(verdict.decision, juryavail.DECISION_AVAILABLE)
        self.assertEqual(verdict.available_vendors, ("claude", "codex"))
        self.assertIn("staffable", verdict.reason)

    def test_one_available_vendor_does_not(self):
        verdict = _assess(UNSTAFFED, min_vendors=2)

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
        self.assertFalse(_assess(STAFFED, min_vendors=3).staffable)

    def test_the_policy_decides_what_unstaffable_means(self):
        blocking = _assess(UNSTAFFED, min_vendors=2, policy="block")

        self.assertEqual(blocking.decision, juryavail.DECISION_BLOCK)
        self.assertEqual(blocking.policy, "block")

    def test_an_unknown_policy_reads_as_unset(self):
        self.assertEqual(_assess(STAFFED, policy="maybe").policy, "fallback")

    def test_two_entries_on_one_vendor_are_one_opinion(self):
        """A panel spans *vendors*: two profiles shelling out to one CLI is one voice."""
        report = _report(
            _row("claude", "claude", True, "/usr/bin/claude"),
            _row("claude-review", "claude", True, "/usr/bin/claude"),
        )

        verdict = _assess(report, min_vendors=2)

        self.assertEqual(verdict.available_vendors, ("claude",))
        self.assertFalse(verdict.staffable)

    def test_a_malformed_report_is_not_staffable_rather_than_an_exception(self):
        for report in (None, {}, {"providers": "nonsense"}, {"providers": ["nope"]}):
            with self.subTest(report=report):
                verdict = _assess(report, min_vendors=2)

                self.assertFalse(verdict.staffable)
                self.assertEqual(verdict.available_vendors, ())
                self.assertIn("none", verdict.reason)

    def test_a_row_with_no_name_still_reports_a_seat(self):
        verdict = _assess(_report({"available": False, "vendor": "  "}), min_vendors=2)

        seat = verdict.unavailable[0]
        self.assertEqual(seat.provider, "(unnamed provider)")
        self.assertEqual(seat.vendor, "(unnamed provider)")
        self.assertEqual(seat.reason, "no reason reported")

    def test_a_row_named_only_by_its_vendor_falls_back_to_that(self):
        verdict = _assess(_report({"available": True, "vendor": "codex"}), min_vendors=1)

        self.assertEqual(verdict.available_vendors, ("codex",))

    def test_min_vendors_never_drops_below_one(self):
        """Zero required vendors would make an empty machine "staffable"."""
        self.assertEqual(_assess(STAFFED, min_vendors=0).required_vendors, 1)

    def test_the_record_is_json_stable(self):
        record = _assess(UNSTAFFED, min_vendors=2).as_dict()

        json.dumps(record)
        self.assertEqual(record["decision"], "fallback")
        self.assertTrue(record["probed"])
        self.assertEqual(record["required_vendors"], 2)
        self.assertEqual(record["unavailable"][0]["provider"], "codex")


class TestTheRefusalMessage(unittest.TestCase):
    def test_it_names_every_unavailable_seat(self):
        message = juryavail.refusal_message(
            _assess(UNSTAFFED, min_vendors=2, policy="block").as_dict(),
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
            else _assess(report, min_vendors=2, policy=policy).as_dict()
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
        availability = _assess(report, min_vendors=2, policy=policy).as_dict()
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

    def _availability(self, config, *, report=STAFFED, runner=RUNNER, tier=3):
        return providerprobe.jury_availability(
            config, tier=tier, _probe=lambda _c: report, _runner_probe=_runner_probe(runner)
        )

    def test_a_host_bench_tier_never_probes(self):
        calls = []

        result = providerprobe.jury_availability(
            self._config(BENCH_CONFIG),
            tier=3,
            _probe=calls.append,
            _runner_probe=lambda: self.fail("probed the runner for a host-bench tier"),
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_a_panel_tier_probes_and_returns_the_verdict(self):
        result = self._availability(self._config(PANEL_CONFIG % ""), report=UNSTAFFED)

        self.assertEqual(result["decision"], "fallback")
        self.assertEqual(result["required_vendors"], 2)

    def test_a_panel_tier_with_no_runner_is_not_staffable(self):
        """The round-2 reproduction: two usable vendors, no `jury`, no panel."""
        result = self._availability(
            self._config(PANEL_CONFIG % ""), report=STAFFED, runner=NO_RUNNER
        )

        self.assertFalse(result["staffable"])
        self.assertEqual(result["decision"], "fallback")
        self.assertFalse(result["runner"]["usable"])
        self.assertEqual(result["unavailable"][0]["provider"], "jury")

    def test_the_configured_min_vendors_raises_the_bar(self):
        result = self._availability(self._config(PANEL_CONFIG % ""))
        self.assertTrue(result["staffable"])

        raised = self._availability(
            self._config(PANEL_CONFIG.replace("min_vendors: 2%s", "min_vendors: 3"))
        )
        self.assertFalse(raised["staffable"])

    def test_a_runner_that_names_its_own_panel_spares_keels_probe(self):
        """Two sweeps of the same agent CLIs is twice the cost for a second opinion."""
        runner = juryavail.Runner(True, "/usr/bin/jury", DOCTOR_STAFFED)

        result = providerprobe.jury_availability(
            self._config(PANEL_CONFIG % ""),
            tier=3,
            _probe=lambda _c: self.fail("collected keel's providers as well"),
            _runner_probe=_runner_probe(runner),
        )

        self.assertTrue(result["staffable"])
        self.assertEqual(result["inventory"], "jury --doctor")

    def test_the_default_probes_are_the_runner_and_the_doctor_providers_one(self):
        """The seams default to the real `jury --doctor` and `providerprobe.collect`."""
        with (
            patch("keel.providerprobe.probe_jury_runner", return_value=RUNNER) as runner,
            patch("keel.providerprobe.collect", return_value=STAFFED) as collect,
        ):
            result = providerprobe.jury_availability(self._config(PANEL_CONFIG % ""), tier=3)

        self.assertTrue(runner.called)
        self.assertTrue(collect.called)
        self.assertTrue(result["staffable"])


class TestTheRecordAReaderSees(unittest.TestCase):
    """The ledger and the closure comment say plainly which review this was."""

    def test_the_closure_comment_names_the_fallback_and_the_seats(self):
        rendered = closure.render_closure_comment(
            {
                "run_context": {
                    "jury_mode": "off",
                    "jury_panel": _assess(UNSTAFFED, min_vendors=2).as_dict(),
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
                    "jury_panel": _assess(
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
            {"jury_mode": "gating", "jury_panel": _assess(STAFFED).as_dict()},
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
            jury_panel=_assess(UNSTAFFED, min_vendors=2).as_dict(),
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
    def _probe(self, report, *, runner=RUNNER):
        """Both halves of the probe answered from memory: the runner, and the inventory."""
        with (
            patch("keel.providerprobe.collect", lambda *_a, **_kw: dict(report)),
            patch("keel.providerprobe.probe_jury_runner", lambda **_kw: runner),
        ):
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

    def test_a_host_with_every_vendor_but_no_jury_binary_falls_back(self):
        """Round 2's reproduction, end to end.

        `claude` and `codex` usable, no `jury` on PATH. The probe used to report the panel
        *available*, publish the panel bench, and leave s7 to fail at the invocation — past
        the point where the project's configured fallback or block could still apply.
        """
        config = self._config(PANEL_CONFIG % "")
        with self._probe(STAFFED, runner=NO_RUNNER):
            plan = self._plan(config)
            ship_data = self._ship(config)

        assignment = plan["assignment"]
        self.assertEqual(assignment["review_panel"], "reviewers")
        self.assertEqual(assignment["reviewer_count"], 3)
        self.assertFalse(assignment["jury"]["availability"]["runner"]["usable"])
        required = [item["id"] for item in plan["evidence"]["required"]]
        self.assertNotIn("jury-verdict", required)
        self.assertIn("review-verdict-3", required)
        self.assertEqual(
            plan["review_merge_contract"],
            ship_data["result"]["assessment"]["review_merge_contract"],
        )

    def test_a_missing_jury_binary_blocks_where_the_policy_says_block(self):
        config = self._config(PANEL_CONFIG % "\n      on_unavailable: block")
        with self._probe(STAFFED, runner=NO_RUNNER):
            rc, _out, err = run(["ship", config, "--root", str(self.root), "--json"])

        self.assertEqual(rc, 1)
        self.assertIn("jury: jury not found on PATH", err)

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
