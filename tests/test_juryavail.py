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

import argparse
import ast
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
        self.assertIn(
            "jury: jury not found on PATH",
            juryavail.refusal_message(verdict.as_dict(), source="team.review.by_tier.3"),
        )
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
            {
                "unavailable": ["not a seat", {"provider": "codex", "reason": "gone"}],
                "available_vendors": "claude",
            },
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
            None if report is None else _assess(report, min_vendors=2, policy=policy).as_dict()
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
        self.assertEqual([seat["provider"] for seat in assignment["reviewers"]], ["claude"] * 3)
        self.assertEqual([seat["slot"] for seat in assignment["reviewers"]], ["A", "B", "C"])

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

    def test_block_refuses_here_rather_than_seating_anything(self):
        """The resolver is where the refusal lives (#1068), because it is where the panel is.

        Under `block` the panel stays the panel and there is no bench to seat, so the one
        place that would have seated one is the one place that can refuse — and it refuses
        naming the config path *it* resolved the panel from.
        """
        with self.assertRaises(juryavail.JuryUnavailableError) as raised:
            self._resolve(UNSTAFFED, policy="block")

        self.assertIn("team.review.by_tier.3", str(raised.exception))
        self.assertIn("codex: codex not found on PATH", str(raised.exception))

    def test_the_refusal_class_is_one_class_under_two_names(self):
        """`keel.team` owns it because `_review_seats` cannot import `keel.juryavail`."""
        self.assertIs(juryavail.JuryUnavailableError, team_policy.JuryUnavailableError)
        self.assertIs(juryavail.refusal_message, team_policy.refusal_message)
        self.assertEqual(juryavail.JURY_RUNNER_COMMAND, team_policy.JURY_RUNNER_COMMAND)

    def test_a_blocked_record_does_not_refuse_a_tier_that_never_wanted_the_panel(self):
        """The #1068 rule: the record travels everywhere, the refusal fires where it applies."""
        assignment = team_policy.resolve_assignment(
            self._policy(mode="gating"),
            tier=1,
            default_count=2,
            jury_availability=_assess(UNSTAFFED, min_vendors=2, policy="block").as_dict(),
        )

        self.assertEqual(assignment["review_panel"], "reviewers")
        self.assertEqual(assignment["reviewer_count"], 2)
        self.assertFalse(assignment["jury"]["panel_configured"])
        self.assertEqual(assignment["jury"]["availability"]["decision"], "block")

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

    def test_a_many_tier_surface_finds_the_panel_wherever_it_is_named(self):
        """The swarm scores each cluster's tier itself, so it cannot name one up front."""
        result = providerprobe.jury_availability_for_any_tier(
            self._config(PANEL_CONFIG % ""),
            _probe=lambda _c: UNSTAFFED,
            _runner_probe=_runner_probe(),
        )

        self.assertEqual(result["decision"], "fallback")

    def test_a_many_tier_surface_probes_nothing_on_a_project_with_no_panel(self):
        result = providerprobe.jury_availability_for_any_tier(
            self._config(BENCH_CONFIG),
            _probe=lambda _c: self.fail("probed a project that names no panel"),
            _runner_probe=lambda: self.fail("probed the runner for a project with no panel"),
        )

        self.assertIsNone(result)


class TestTheProbeAsksWhatTheResolverAsks(unittest.TestCase):
    """The probe's predicate is the resolver's overlay, not `review.by_tier` alone (#1068).

    `_review_seats` resolves the review from the tier's policy *overlaid* by
    `TeamPolicy.benches_for` — a `--team` profile, else the difficulty band — and both
    overlays may name the panel (`team_issues` accepts them as panel policy). A probe that
    read `review_for` alone was narrower than the resolver it guards: with
    `profiles.strict.review: jury` over a host-bench tier, `jury_availability` returned
    `None`, the resolver published `review_panel: jury`, and the assignment carried
    `availability: null` — #1066's bug reached by a second route, and invisible to the
    call-site sweep because that site *was* handed a measurement.
    """

    OVERLAY_PANEL = (
        BENCH_CONFIG
        + """    profiles:
      strict:
        review: jury
    by_difficulty:
      hard:
        review: jury
    jury:
      mode: gating
      min_vendors: 2
"""
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = pathlib.Path(self._tmp.name) / "project.yaml"
        path.write_text(self.OVERLAY_PANEL, encoding="utf-8")
        self.config = cfg.load_config(str(path))

    def _availability(self, **kwargs):
        return providerprobe.jury_availability(
            self.config,
            tier=3,
            _probe=lambda _c: UNSTAFFED,
            _runner_probe=_runner_probe(NO_RUNNER),
            **kwargs,
        )

    def test_the_profile_route_is_measured(self):
        record = self._availability(profile="strict")

        self.assertEqual(record["decision"], "fallback")
        self.assertFalse(record["staffable"])

    def test_the_difficulty_route_is_measured(self):
        record = self._availability(difficulty="hard")

        self.assertEqual(record["decision"], "fallback")

    def test_neither_route_selected_leaves_the_host_bench_tier_unprobed(self):
        """The tier's own review is host seats, so with no overlay there is nothing to ask."""
        result = providerprobe.jury_availability(
            self.config,
            tier=3,
            _probe=lambda _c: self.fail("probed a run whose review is a host bench"),
            _runner_probe=lambda: self.fail("probed the runner for a host-bench run"),
        )

        self.assertIsNone(result)

    def test_the_bench_it_seats_agrees_with_what_was_measured(self):
        """The whole point: the resolver and the probe answer the same question."""
        for kwargs, resolved in (
            ({"profile": "strict"}, {"team_profile": "strict"}),
            ({"difficulty": "hard"}, {"difficulty": "hard"}),
        ):
            with self.subTest(**kwargs):
                record = self._availability(**kwargs)
                assignment = team_policy.resolve_assignment(
                    self.config.knobs.team,
                    tier=3,
                    default_count=3,
                    jury_availability=record,
                    **resolved,
                )

                self.assertEqual(assignment["review_panel"], "reviewers")
                self.assertTrue(assignment["jury"]["panel_configured"])
                self.assertEqual(assignment["jury"]["availability"]["decision"], "fallback")
                self.assertEqual(assignment["reviewer_count"], 3)

    def test_a_blocking_project_refuses_through_either_overlay(self):
        """…and names the overlay's own config path, not the tier's."""
        path = pathlib.Path(self._tmp.name) / "block.yaml"
        path.write_text(
            self.OVERLAY_PANEL.replace(
                "      min_vendors: 2\n", "      min_vendors: 2\n      on_unavailable: block\n"
            ),
            encoding="utf-8",
        )
        config = cfg.load_config(str(path))
        for kwargs, resolved, named in (
            ({"profile": "strict"}, {"team_profile": "strict"}, "team.profiles.strict.review"),
            ({"difficulty": "hard"}, {"difficulty": "hard"}, "team.by_difficulty.hard.review"),
        ):
            with self.subTest(**kwargs):
                record = providerprobe.jury_availability(
                    config,
                    tier=3,
                    _probe=lambda _c: UNSTAFFED,
                    _runner_probe=_runner_probe(NO_RUNNER),
                    **kwargs,
                )
                self.assertEqual(record["decision"], "block")

                with self.assertRaises(juryavail.JuryUnavailableError) as raised:
                    team_policy.resolve_assignment(
                        config.knobs.team,
                        tier=3,
                        default_count=3,
                        jury_availability=record,
                        **resolved,
                    )

                self.assertIn(named, str(raised.exception))

    def test_a_many_tier_surface_cannot_name_the_band_so_it_asks_about_every_one(self):
        """The swarm scores difficulty inside the partition this record is measured for."""
        record = providerprobe.jury_availability_for_any_tier(
            self.config, _probe=lambda _c: UNSTAFFED, _runner_probe=_runner_probe(NO_RUNNER)
        )

        self.assertEqual(record["decision"], "fallback")

    def test_a_many_tier_surface_still_probes_nothing_when_no_route_names_the_panel(self):
        path = pathlib.Path(self._tmp.name) / "bench.yaml"
        path.write_text(BENCH_CONFIG, encoding="utf-8")

        result = providerprobe.jury_availability_for_any_tier(
            cfg.load_config(str(path)),
            profile="strict",
            _probe=lambda _c: self.fail("probed a project that names no panel"),
            _runner_probe=lambda: self.fail("probed the runner for a project with no panel"),
        )

        self.assertIsNone(result)

    def test_the_profile_outranks_the_band_exactly_as_the_resolver_orders_them(self):
        """A profile that names host seats wins over a band that names the panel."""
        path = pathlib.Path(self._tmp.name) / "seated.yaml"
        path.write_text(
            self.OVERLAY_PANEL.replace(
                """      strict:
        review: jury
""",
                """      strict:
        review:
          - { provider: claude }
          - { provider: codex }
""",
            ),
            encoding="utf-8",
        )

        result = providerprobe.jury_availability(
            cfg.load_config(str(path)),
            tier=3,
            difficulty="hard",
            profile="strict",
            _probe=lambda _c: self.fail("probed a run the profile seats from the host"),
            _runner_probe=lambda: self.fail("probed the runner for a host-bench run"),
        )

        self.assertIsNone(result)


class TestEverySiteThatResolvesABenchSeesTheProbe(unittest.TestCase):
    """The seventh resolver was missed once; this is the rule, not the site (#1066).

    `_review_seats` is reached from exactly one function — `team.resolve_assignment` — so
    the call sites of *that* are the complete list of places a bench is resolved. Each has
    to see the measurement or it publishes a panel the machine beside it cannot convene.
    """

    #: The one documented exception: `keel fixloop --no-project` resolves against an
    #: *empty* `TeamPolicy`, which has no `review.by_tier` and so can never name the panel.
    #: There is nothing for a probe to measure, and running one would spend subprocesses on
    #: a question whose answer cannot matter.
    UNCONFIGURED_POLICY = "TeamPolicy"

    RESOLVER = "resolve_assignment"

    @staticmethod
    def _is_call_to(node, name, bound=()):
        """Does this call reach ``name`` — through an attribute, or through a bound name?

        Matching `attr` alone was a hole: `from keel.team import resolve_assignment as
        resolve` then `resolve(policy, tier=3)` is an `ast.Name` call, invisible to a sweep
        that only reads attributes — so a future resolver could omit the measurement with
        this guard still green (#1068 round 2). `bound` is the set of local names an
        `import ... as ...` in *this module* tied to the target; a bare `ast.Name` spelling
        the target itself counts too, so a direct `from ... import resolve_assignment` is
        seen whether or not the sweep could follow the binding.
        """
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr == name
        if isinstance(func, ast.Name):
            return func.id == name or func.id in bound
        return False

    def _bound_names(self, tree):
        """Local names this module ties to `team.resolve_assignment`, aliases included."""
        return {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name == self.RESOLVER
        }

    def _call_sites(self, root=None):
        root = pathlib.Path(cli.__file__).resolve().parent if root is None else root
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            bound = self._bound_names(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and self._is_call_to(node, self.RESOLVER, bound):
                    yield path, node

    def _offenders(self, root=None):
        offenders = []
        sites = 0
        for path, node in self._call_sites(root):
            sites += 1
            if any(keyword.arg == "jury_availability" for keyword in node.keywords):
                continue
            policy = node.args[0] if node.args else None
            unconfigured = (
                isinstance(policy, ast.Call)
                and self._is_call_to(policy, self.UNCONFIGURED_POLICY)
                and not policy.args
                and not policy.keywords
            )
            if not unconfigured:
                offenders.append(f"{path.name}:{node.lineno}")
        return offenders, sites

    def test_every_resolver_is_handed_the_measurement(self):
        offenders, sites = self._offenders()

        self.assertEqual([], offenders, "a bench is resolved without the panel probe")
        self.assertGreaterEqual(sites, 5, "the sweep found no call sites to sweep")

    def test_the_sweep_sees_an_aliased_import(self):
        """The guard the guard needed: an alias must not walk past it."""
        root = pathlib.Path(tempfile.mkdtemp())
        (root / "aliased.py").write_text(
            "from keel.team import resolve_assignment as resolve\n"
            "\n"
            "def staff(policy):\n"
            "    return resolve(policy, tier=3)\n",
            encoding="utf-8",
        )

        offenders, sites = self._offenders(root)

        self.assertEqual(1, sites, "the aliased call site was invisible to the sweep")
        self.assertEqual(["aliased.py:4"], offenders)

    def test_the_sweep_sees_a_direct_import_and_accepts_the_measurement(self):
        """A direct-name call is a call site; handed the probe, it is not an offender."""
        root = pathlib.Path(tempfile.mkdtemp())
        (root / "direct.py").write_text(
            "from keel.team import resolve_assignment\n"
            "from keel.team import TeamPolicy\n"
            "\n"
            "def staff(policy, availability):\n"
            "    return resolve_assignment(policy, tier=3, jury_availability=availability)\n"
            "\n"
            "def unconfigured():\n"
            "    return resolve_assignment(TeamPolicy(), tier=3)\n",
            encoding="utf-8",
        )

        offenders, sites = self._offenders(root)

        self.assertEqual(2, sites)
        self.assertEqual([], offenders)


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
                    "jury_panel": _assess(UNSTAFFED, min_vendors=2, policy="block").as_dict()
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

    def _swarm_plan(self, config):
        rc, out, err = run(
            [
                "swarm-plan",
                config,
                "--issue",
                "1",
                "--issue-title",
                "fix core",
                "--declared-file",
                "src/a.py",
                "--json",
            ]
        )
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def test_the_swarm_seats_the_same_bench_the_child_ship_will(self):
        """Round 2's third finding: the swarm was the seventh resolver, and it was missed.

        `swarm-plan` published `review_panel: jury` with `reviewer_count: 0` and
        `availability: null` for a tier-3 cluster, while `keel ship` on the same machine,
        from the same config, seated three host reviewers and recorded why — the same
        in-process disagreement this issue closed one layer down.
        """
        config = self._config(PANEL_CONFIG % "")
        with self._probe(UNSTAFFED):
            plan = self._swarm_plan(config)
            ship_data = self._ship(config)

        cluster = plan["waves"][0]["clusters"][0]["assignment"]
        self.assertEqual(cluster["review_panel"], "reviewers")
        self.assertEqual(cluster["reviewer_count"], 3)
        self.assertEqual(cluster["reviewer_source"], "jury-fallback")
        self.assertEqual(cluster["jury"]["availability"]["decision"], "fallback")
        # The point of the finding: the two agree, seat for seat.
        child = ship_data["result"]["assessment"]["assignment"]
        self.assertEqual(cluster["review_panel"], child["review_panel"])
        self.assertEqual(cluster["reviewer_count"], child["reviewer_count"])

    def test_the_swarm_keeps_the_panel_when_the_panel_can_sit(self):
        config = self._config(PANEL_CONFIG % "")
        with self._probe(STAFFED):
            plan = self._swarm_plan(config)

        cluster = plan["waves"][0]["clusters"][0]["assignment"]
        self.assertEqual(cluster["review_panel"], "jury")
        self.assertTrue(cluster["jury"]["availability"]["staffable"])

    def test_the_swarm_refuses_under_block_like_every_other_surface(self):
        config = self._config(PANEL_CONFIG % "\n      on_unavailable: block")
        with self._probe(UNSTAFFED):
            rc, _out, err = run(
                ["swarm-plan", config, "--issue", "1", "--declared-file", "src/a.py", "--json"]
            )

        self.assertEqual(rc, 1)
        self.assertIn("cannot be staffed here", err)

    def test_a_swarm_of_non_panel_clusters_plans_under_block(self):
        """The #1068 major: the any-tier sweep refused work the panel never touches.

        `jury_availability_for_any_tier` walks `(None, *TIERS)` with `any_difficulty=True`
        and stops at the first route that names the panel — which on this project is
        tier 3, a tier none of these clusters is. Refusing on that measurement refused a
        swarm of entirely docs-tier work on a host with no panel installed, before the
        partition had scored a single cluster. The measurement is still taken once and
        still travels to every cluster; only the refusal moved to the cluster it is about.
        """
        config = self._config(PANEL_CONFIG % "\n      on_unavailable: block")
        with self._probe(UNSTAFFED):
            rc, out, err = run(
                [
                    "swarm-plan",
                    config,
                    "--issue",
                    "1",
                    "--issue-title",
                    "tidy the docs",
                    "--declared-file",
                    "docs/keel/configuration.md",
                    "--json",
                ]
            )

        self.assertEqual(rc, 0, err)
        cluster = json.loads(out)["waves"][0]["clusters"][0]["assignment"]
        self.assertEqual(cluster["review_panel"], "reviewers")
        self.assertFalse(cluster["jury"]["panel_configured"])
        # Measured once and carried, exactly as before — the refusal is what moved.
        self.assertEqual(cluster["jury"]["availability"]["decision"], "block")

    def test_a_swarm_that_mixes_them_refuses_on_the_panel_cluster(self):
        """One blocked cluster refuses the plan; the probe is still taken only once."""
        config = self._config(PANEL_CONFIG % "\n      on_unavailable: block")
        with self._probe(UNSTAFFED):
            rc, _out, err = run(
                [
                    "swarm-plan",
                    config,
                    "--issue",
                    "1",
                    "--declared-file",
                    "docs/keel/configuration.md",
                    "--issue",
                    "2",
                    "--declared-file",
                    "src/a.py",
                    "--json",
                ]
            )

        self.assertEqual(rc, 1)
        self.assertIn("cannot be staffed here", err)

    def test_a_project_with_no_panel_is_untouched(self):
        """The probe never fires, so nothing about this project's runs can move."""
        config = self._config(BENCH_CONFIG)
        with patch("keel.providerprobe.collect", side_effect=AssertionError("probed")):
            plan = self._plan(config)

        self.assertEqual(plan["assignment"]["review_panel"], "reviewers")
        self.assertIsNone(plan["review_merge_contract"]["jury"]["availability"])


class TestTheContractIsPinnedToTheShipsMeasurement(unittest.TestCase):
    """A surface that only *verifies* must not re-measure the panel (#1066 round 2).

    Every surface re-called the probe, so the same pull request resolved a different
    required-evidence set depending on which machine asked. A panel project whose ship
    measured two available vendors requires the panel verdict; the same PR checked on a bare
    runner measured one, so `review-verdict-1..3` was required instead and the panel item
    disappeared — a workstation-juried change held to a host bench nobody sat.

    **Every test here stubs the probe per surface**, not once for the whole run: a fixture
    that shares one stub across plan, ship and verify cannot see the divergence, and a test
    that cannot see it cannot guard against it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "empty.json").write_text("[]", encoding="utf-8")
        (self.root / "body.md").write_text("Closes #1", encoding="utf-8")
        path = self.root / "project.yaml"
        path.write_text(PANEL_CONFIG % "", encoding="utf-8")
        self.config = str(path)

    @contextlib.contextmanager
    def _probe(self, report):
        with (
            patch("keel.providerprobe.collect", lambda *_a, **_kw: dict(report)),
            patch("keel.providerprobe.probe_jury_runner", lambda **_kw: RUNNER),
        ):
            yield

    def _ledger(self, availability, *, head_sha="abc"):
        """A `ship_run` record for PR 1 carrying what that run measured, as ship writes it.

        Written against `head_sha` — the head the verify below is checking — because the pin
        is head-scoped: a record from another head is not this head's measurement (#1068).
        """
        record = ledger.build_ship_run_record(
            command="ship",
            run_id="r1",
            base_branch="main",
            changed_files=["src/a.py"],
            declared_files=None,
            issue_intake=None,
            outcomes=(),
            verdict=_verdict(),
            assessment=_assessment(),
            pr_number=1,
            head_sha=head_sha,
            jury_panel=availability,
        )
        path = self.root / "ledger.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        return str(path)

    def _comments(self, *bodies):
        path = self.root / "pr-comments.json"
        path.write_text(
            json.dumps([{"body": body, "author_association": "MEMBER"} for body in bodies]),
            encoding="utf-8",
        )
        return str(path)

    def _required(self, *, report, ledger_jsonl=None, comments=None):
        argv = [
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
            "--pr-label",
            "keel:ship",
            "--pr-body-file",
            str(self.root / "body.md"),
            "--pr-comments-json",
            comments or str(self.root / "empty.json"),
            "--issue-comments-json",
            str(self.root / "empty.json"),
            "--pr-reviews-json",
            str(self.root / "empty.json"),
            "--json",
        ]
        if ledger_jsonl is not None:
            argv += ["--ledger-jsonl", ledger_jsonl]
        with self._probe(report):
            rc, out, err = run(argv)
        self.assertIn(rc, (0, 1), err)
        payload = json.loads(out)
        return [item["id"] for item in payload["contract"]["required"]]

    def _shipped_required(self, report):
        """What the *ship* published as required, measured on the ship's own machine."""
        with self._probe(report):
            rc, out, err = run(["ship", self.config, "--root", str(self.root), "--json"])
        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["result"]["assessment"]["review_merge_contract"]
        return [item.id for item in evidence.required_items(contract)], contract

    def test_a_panel_ship_checked_on_a_bare_runner_keeps_its_panel_requirement(self):
        shipped, contract = self._shipped_required(STAFFED)
        self.assertIn("jury-verdict", shipped)

        # …and now the *other* machine: one vendor, where the ship saw two.
        required = self._required(
            report=UNSTAFFED,
            ledger_jsonl=self._ledger(contract["jury"]["availability"]),
        )

        self.assertIn("jury-verdict", required)
        self.assertNotIn("review-verdict-3", required)
        self.assertEqual(sorted(shipped), sorted(required))

    def test_a_posted_panel_verdict_pins_it_with_no_ledger_at_all(self):
        """The route a hosted runner actually has: `.keel/state/` is not readable there."""
        required = self._required(
            report=UNSTAFFED,
            comments=self._comments("keel.jury-verdict.v1\nhead: abc\npanelists: 2\nAI Jury LGTM"),
        )

        self.assertIn("jury-verdict", required)

    def test_a_fallback_shipped_change_is_not_held_to_a_panel_it_never_ran(self):
        """The other direction, which fails the same way: the ship fell back, CI did not."""
        shipped, contract = self._shipped_required(UNSTAFFED)
        self.assertNotIn("jury-verdict", shipped)

        required = self._required(
            report=STAFFED,
            ledger_jsonl=self._ledger(contract["jury"]["availability"]),
        )

        self.assertNotIn("jury-verdict", required)
        self.assertIn("review-verdict-3", required)
        self.assertEqual(sorted(shipped), sorted(required))

    def test_with_nothing_to_pin_to_the_surface_still_probes(self):
        """The documented residue: no posted verdict and no readable ledger."""
        self.assertIn("jury-verdict", self._required(report=STAFFED))
        self.assertNotIn("jury-verdict", self._required(report=UNSTAFFED))

    def test_a_ledger_record_that_says_nothing_about_the_panel_pins_nothing(self):
        for availability in (None, "nonsense", {"decision": "block"}):
            with self.subTest(availability=availability):
                required = self._required(report=UNSTAFFED, ledger_jsonl=self._ledger(availability))

                self.assertNotIn("jury-verdict", required)

    def test_the_merge_gate_reads_the_same_pin(self):
        record = {
            "git": {"head_sha": "abc"},
            "run_context": {"jury_panel": _assess(STAFFED, min_vendors=2).as_dict()},
        }
        artifacts = {"pr_comments": [], "pr_reviews": [], "head_sha": "abc"}

        pinned = cli._shipped_jury_availability(artifacts, record)

        self.assertEqual(pinned["decision"], "available")
        self.assertEqual(pinned["source"], "run-ledger")
        self.assertFalse(pinned["probed"], "a pin is not a measurement and must not claim to be")

    def test_a_ship_record_from_an_earlier_head_does_not_answer_for_this_one(self):
        """A pull request outlives its heads; a stale run may not weaken a live contract."""
        record = {
            "git": {"head_sha": "old-head"},
            "run_context": {"jury_panel": _assess(UNSTAFFED, min_vendors=2).as_dict()},
        }
        artifacts = {"pr_comments": [], "pr_reviews": [], "head_sha": "current-head"}

        self.assertIsNone(cli._shipped_jury_availability(artifacts, record))

    def test_a_record_with_no_readable_head_pins_nothing(self):
        """Fail closed on every unreadable shape, rather than pick a permissive reading."""
        panel = {"run_context": {"jury_panel": _assess(UNSTAFFED, min_vendors=2).as_dict()}}
        for git, head_sha in (
            (None, "abc"),
            ({"head_sha": None}, "abc"),
            ("not a mapping", "abc"),
            ({"head_sha": "abc"}, ""),
            ({"head_sha": "abc"}, None),
        ):
            with self.subTest(git=git, head_sha=head_sha):
                record = {**panel, "git": git}

                self.assertIsNone(juryavail.shipped(record, head_sha=head_sha))

    def test_dropping_the_pin_fails_closed_on_the_surface_that_verifies(self):
        """`None` does not waive the panel: the surface measures, and both ways refuse.

        The ship fell back at an earlier head, so its record says `fallback`. Held to that
        stale record, this head would be checked against a host bench. Unpinned, the bare
        runner measures for itself — and requires what *it* resolves, which is the answer
        the change has to satisfy rather than one an old run chose for it.
        """
        _shipped, contract = self._shipped_required(UNSTAFFED)
        stale = self._ledger(contract["jury"]["availability"], head_sha="old-head")

        required = self._required(report=STAFFED, ledger_jsonl=stale)

        self.assertIn("jury-verdict", required)
        self.assertNotIn("review-verdict-3", required)

    def test_a_posted_verdict_outranks_the_ledger(self):
        record = {"run_context": {"jury_panel": _assess(UNSTAFFED, min_vendors=2).as_dict()}}
        artifacts = {
            "pr_comments": [
                {
                    "body": "keel.jury-verdict.v1\nhead: abc\nAI Jury LGTM",
                    "author_association": "MEMBER",
                }
            ],
            "pr_reviews": [],
            "head_sha": "abc",
        }

        pinned = cli._shipped_jury_availability(artifacts, record)

        self.assertEqual(pinned["source"], "pull-request")
        self.assertFalse(pinned["probed"], "a pin is not a measurement and must not claim to be")

    def test_a_verdict_pinned_to_another_head_is_not_this_changes_panel(self):
        artifacts = {
            "pr_comments": [
                {
                    "body": "keel.jury-verdict.v1\nhead: stale\nAI Jury LGTM",
                    "author_association": "MEMBER",
                }
            ],
            "pr_reviews": [],
            "head_sha": "abc",
        }

        self.assertIsNone(cli._shipped_jury_availability(artifacts, None))

    def test_an_unreadable_ledger_refuses_evidence_verify_but_not_merge(self):
        """The record is *evidence* on one surface and only a hint on the other."""
        path = self.root / "broken.jsonl"
        path.write_text("{not json\n", encoding="utf-8")
        args = argparse.Namespace(ledger_jsonl=str(path), root=str(self.root), pr=1)
        config = cfg.load_config(self.config)

        with self.assertRaises(ledger.LedgerError):
            cli._evidence_ledger_record(args, config)
        self.assertIsNone(cli._merge_ledger_record(args, config))


if __name__ == "__main__":
    unittest.main()
