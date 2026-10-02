"""Unit tests for swarm-review's decisions (#1423): who reviews, what each seat is told, how
its answer is read, and whether anything is posted."""

from __future__ import annotations

import json
import unittest

from keel import config as cfg
from keel import consent, delegate
from keel import swarm_review as sr
from keel.swarm import Difficulty, SwarmCluster, SwarmPlan, SwarmWave

SCOPE = "Checked `src/keel/swarm_review.py` and `swarm_review.read_seat_verdict()`."


def _seat(provider, *, slot="A", kind="provider", model=None, source="team.review"):
    return {
        "provider": provider,
        "name": provider.removeprefix("subagent:"),
        "kind": kind,
        "model": model,
        "effort": None,
        "source": source,
        "slot": slot,
    }


def _assignment(*reviewers, implementer="agy", panel="reviewers"):
    return {
        "implementer": _seat(implementer, slot=None, source="flag:--delegate"),
        "reviewers": list(reviewers),
        "review_panel": panel,
    }


def _seats(*reviewers, implementer_vendor="agy", config=None):
    return sr.review_seats(
        _assignment(*reviewers),
        config=config,
        registry=None,
        implementer_vendor=implementer_vendor,
        brief_path=lambda slot: f"/briefs/{slot}.md",
        checkout_path=lambda slot: f"/checkouts/{slot}",
        timeout=60,
    )


def _one(provider="claude", **kwargs):
    (seat,) = _seats(_seat(provider, **kwargs))
    return seat


def _contract(count=2, *, distinct=False, focuses=None, additions=(), sections=()):
    return {
        "reviewers": {
            "count": count,
            "require_distinct_vendors": distinct,
            "focuses": focuses if focuses is not None else [],
            "project_additions": list(additions),
            "required_sections": list(sections),
        }
    }


def _facts(diff="--- a/x\n+++ b/x\n", **kwargs):
    return sr.PullRequestFacts("abc123", "Fix the thing", 2, _contract(**kwargs), diff)


def _contract_for(scopes, *, mode="explicit", operator="me"):
    return consent.build_consent_contract(
        command="swarm-review",
        side_effects=sr.REVIEW_SIDE_EFFECTS,
        dry_run=False,
        approved_scopes=scopes,
        approval_source="flag",
        mode=mode,
        operator=operator,
        target="x",
    )


def _profile_config(**profile):
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^1.0",
        base_branch="main",
        knobs=cfg.Knobs(
            build_gate_cmd="true",
            delegate_profiles={"cursor": cfg.DelegateProfile(vendor="cli", **profile)},
        ),
    )


class TheOperatorApprovesALiveReview(unittest.TestCase):
    def test_the_scopes_are_the_checkouts_and_the_posts(self):
        self.assertEqual(sr.required_scopes(), ("filesystem", "git", "github"))

    def test_an_approved_contract_starts(self):
        self.assertEqual(sr.consent_refusal(_contract_for(sr.required_scopes())), "")

    def test_a_missing_scope_refuses(self):
        why = sr.consent_refusal(_contract_for(("filesystem",)))
        self.assertIn("operator consent required", why)

    def test_consent_left_to_a_host_agent_refuses(self):
        why = sr.consent_refusal(_contract_for((), mode="agent", operator=None))
        self.assertIn("'agent-delegated'", why)
        self.assertIn("--approve-scope filesystem,git,github", why)


class TheContractIsReadAsKeelReviewReadsIt(unittest.TestCase):
    def test_the_count_and_the_distinct_vendor_rule(self):
        self.assertEqual(sr.required_count(_contract(3)), 3)
        self.assertEqual(sr.required_count({"reviewers": {"count": True}}), 0)
        self.assertEqual(sr.required_count({"reviewers": "junk"}), 0)
        self.assertTrue(sr.distinct_vendors_required(_contract(distinct=True)))
        self.assertFalse(sr.distinct_vendors_required({}))

    def test_a_slot_gets_its_own_focus_then_its_position_then_everything(self):
        contract = _contract(
            focuses=[
                {"slot": "A", "focus": ["correctness", " ", 3]},
                "junk",
                {"slot": "C", "focus": ["tests"]},
            ]
        )
        self.assertEqual(sr.focus_for(contract, "C", 0), ("tests",))
        self.assertEqual(sr.focus_for(contract, "B", 1), ("tests",))
        self.assertEqual(sr.focus_for(contract, "Z", 5), ("correctness", "tests"))
        self.assertEqual(sr.focus_for({"reviewers": {"focuses": [{"focus": "x"}]}}, "Q", 0), ())


class TheBenchMayBeRestaffedButNotTheImplementer(unittest.TestCase):
    def test_the_implementer_that_ran_is_kept(self):
        resolved = _assignment(_seat("codex"), implementer="claude")
        staffed = sr.restaffed(_assignment(implementer="agy"), resolved)
        self.assertEqual(staffed["implementer"]["provider"], "agy")
        self.assertEqual(staffed["reviewers"], resolved["reviewers"])
        self.assertEqual(sr.restaffed(None, resolved)["implementer"]["provider"], "claude")

    def test_each_cluster_is_re_resolved_unless_the_resolver_cannot(self):
        difficulty = Difficulty(score=1, band="easy", tier=1, file_count=1, dependency_depth=0)
        kept = SwarmCluster("c1", (1,), "core", ("a",), assignment=_assignment(_seat("claude")))
        moved = SwarmCluster(
            "c2", (2,), "core", ("b",), difficulty=difficulty, assignment=_assignment()
        )
        plan = SwarmPlan("s", 2, (SwarmWave(1, "orthogonal_parallel", True, (kept, moved)),))
        seen = []

        def resolve(cluster):
            seen.append(cluster.cluster_id)
            if cluster.difficulty is None:
                return None
            return _assignment(_seat("codex"), implementer="claude")

        out = sr.restaff_plan(plan, resolve)
        self.assertEqual(seen, ["c1", "c2"])
        c1, c2 = out.waves[0].clusters
        self.assertIs(c1, kept)
        self.assertEqual(c2.assignment["reviewers"][0]["provider"], "codex")
        self.assertEqual(c2.assignment["implementer"]["provider"], "agy")


class TheImplementersVendor(unittest.TestCase):
    def test_a_provider_seat_resolves_and_a_subagent_is_the_host(self):
        self.assertEqual(sr.seat_vendor(None, config=None, registry=None, host_agent="h"), "")
        self.assertEqual(
            sr.seat_vendor(_seat("codex"), config=None, registry=None, host_agent="h"), "codex"
        )
        subagent = _seat("subagent:backend-developer", kind="subagent")
        self.assertEqual(sr.seat_vendor(subagent, config=None, registry=None, host_agent="h"), "h")
        self.assertEqual(
            sr.seat_vendor(_seat("nope"), config=None, registry=None, host_agent="h"), "nope"
        )

    def test_a_profile_resolves_to_its_label_vendor(self):
        config = _profile_config(command="cursor-agent", vendor_label="grok")
        seat = _seat("cursor")
        self.assertEqual(sr.seat_vendor(seat, config=config, registry=None, host_agent="h"), "grok")


class EachSeatIsPlannedReadOnly(unittest.TestCase):
    def test_a_built_in_cli_is_planned_as_delegate_run_plans_a_review(self):
        seat = _one("codex", model="gpt-5")
        expected = delegate.plan_run(
            delegate.resolve_provider(None, None, "codex").provider,
            "review",
            "/briefs/A.md",
            "/checkouts/A",
            60,
            None,
            "gpt-5",
        )
        self.assertEqual(seat.plan, expected)
        self.assertTrue(seat.eligible)
        self.assertEqual(seat.reviewer, "swarm-review-a-codex")
        self.assertEqual(seat.provider, "codex:gpt-5")
        self.assertEqual(seat.vendor, "codex")
        self.assertEqual(
            seat.to_dict(),
            {
                "slot": "A",
                "reviewer": "swarm-review-a-codex",
                "provider": "codex:gpt-5",
                "source": "team.review",
                "vendor": "codex",
                "model": "gpt-5",
                "transport": "cli",
                "read_only_backed": True,
                "eligible": True,
                "refusal": "",
            },
        )

    def test_an_api_seat_has_no_tools_and_is_accepted(self):
        seat = _one("anthropic-api", model="claude-x")
        self.assertTrue(seat.eligible)
        self.assertEqual(seat.plan.transport, "api")

    def test_a_host_subagent_is_refused(self):
        seat = _one("subagent:opus-reviewer", kind="subagent")
        self.assertFalse(seat.eligible)
        self.assertIsNone(seat.vendor)
        self.assertIn("host subagent", seat.refusal)
        self.assertEqual(seat.reviewer, "swarm-review-a-opus-reviewer")
        self.assertIsNone(seat.to_dict()["transport"])

    def test_an_unplannable_seat_is_refused_with_the_delegate_error(self):
        self.assertIn("(unknown-provider)", _one("nope").refusal)

    def test_a_profile_without_review_args_is_not_read_only_and_is_refused(self):
        config = _profile_config(command="aider", args=("--yes-always",))
        (seat,) = _seats(_seat("cursor"), config=config)
        self.assertIsNone(seat.plan)
        self.assertIn("nothing makes the reviewer seat 'cursor' read-only", seat.refusal)

    def test_a_profile_with_review_args_runs(self):
        config = _profile_config(command="cursor-agent", review_args=("-p", "--read-only"))
        (seat,) = _seats(_seat("cursor"), config=config)
        self.assertTrue(seat.eligible)

    def test_the_implementers_vendor_never_reviews(self):
        seat = _one("agy")
        self.assertIsNotNone(seat.plan)
        self.assertFalse(seat.eligible)
        self.assertIn("'agy' is the implementer's", seat.refusal)
        (other,) = _seats(_seat("agy"), implementer_vendor="")
        self.assertTrue(other.eligible)

    def test_slots_default_by_position_and_junk_seats_are_skipped(self):
        seats = sr.review_seats(
            {"reviewers": ["junk", _seat("claude", slot=None), _seat("codex", slot=None)]},
            config=None,
            registry=None,
            implementer_vendor="agy",
            brief_path=str,
            checkout_path=str,
        )
        self.assertEqual([s.slot for s in seats], ["A", "B"])
        self.assertEqual(seats[0].plan.timeout, delegate.DEFAULT_TIMEOUT_S)
        self.assertEqual(
            sr.review_seats(
                None,
                config=None,
                registry=None,
                implementer_vendor="",
                brief_path=str,
                checkout_path=str,
            ),
            (),
        )


class AClusterIsRefusedBeforeAnythingRuns(unittest.TestCase):
    def test_too_few_eligible_seats_for_the_tier_refuses(self):
        seats = _seats(_seat("claude"), _seat("agy", slot="C"))
        why = sr.cluster_refusal(seats, panel="reviewers", required=2, require_distinct=False)
        self.assertIn("requires at least 2 review verdict(s)", why)
        self.assertIn("only 1 of the cluster's 2", why)
        self.assertIn("seat C (agy): its vendor 'agy' is the implementer's", why)
        self.assertIn("--review-delegate", why)

    def test_no_seat_at_all_still_needs_one(self):
        why = sr.cluster_refusal((), panel="reviewers", required=0, require_distinct=False)
        self.assertIn("requires at least 1", why)
        self.assertNotIn(" — ", why)

    def test_enough_seats_pass(self):
        seats = _seats(_seat("claude"), _seat("codex", slot="C"))
        self.assertEqual(
            sr.cluster_refusal(seats, panel="reviewers", required=2, require_distinct=True), ""
        )
        self.assertEqual(
            sr.cluster_refusal(seats, panel="reviewers", required=1, require_distinct=False), ""
        )

    def test_the_distinct_vendor_rule_is_enforced(self):
        seats = _seats(_seat("claude"), _seat("claude", slot="C"))
        self.assertEqual(
            sr.cluster_refusal(seats, panel="reviewers", required=2, require_distinct=False), ""
        )
        why = sr.cluster_refusal(seats, panel="reviewers", required=2, require_distinct=True)
        self.assertIn("require_distinct_vendors is on", why)
        self.assertIn("share a vendor: claude", why)

    def test_a_jury_panel_tier_is_refused(self):
        why = sr.cluster_refusal((), panel="jury", required=0, require_distinct=False)
        self.assertIn("keel review --from-jury", why)


class TheBriefIsShipsReviewerBriefing(unittest.TestCase):
    def _brief(self, facts=None, focus=("correctness",), issues=((7, "Title", "Body"),)):
        return sr.render_review_brief(
            swarm_id="s1",
            cluster_id="c1",
            pull_request=9,
            facts=facts or _facts(),
            seat=_one("claude"),
            focus=focus,
            issues=issues,
        )

    def test_it_pins_the_head_and_carries_the_stance_the_issue_and_the_diff(self):
        brief = self._brief()
        self.assertIn("reviewer A of pull request #9 (cluster c1 of keel swarm s1)", brief)
        self.assertIn("at head abc123", brief)
        for line in sr.REVIEW_STANCE:
            self.assertIn(line, brief)
        self.assertIn("do not look for, read or wait on any other reviewer's output", brief)
        self.assertIn("- correctness", brief)
        self.assertIn("## Issue #7: Title\n\nBody", brief)
        self.assertIn("## The diff at abc123\n\n```diff\n--- a/x\n+++ b/x\n```", brief)
        self.assertIn('"verdict": "APPROVE or REQUEST_CHANGES"', brief)
        self.assertIn("never as an approval", brief)
        self.assertNotIn("Recurring shapes", brief)
        self.assertNotIn("Sections your scope", brief)

    def test_the_project_additions_and_required_sections_go_in_verbatim(self):
        brief = self._brief(_facts(additions=["stale cache keys"], sections=["Risk"]))
        self.assertIn("## Recurring shapes in this project", brief)
        self.assertIn("- stale cache keys", brief)
        self.assertIn("## Sections your scope must cover\n\n- Risk", brief)

    def test_no_focus_reviews_everything_and_an_unread_issue_says_so(self):
        brief = self._brief(focus=(), issues=((7, "", ""),))
        self.assertIn("- every dimension of the change", brief)
        self.assertIn("## Issue #7: (title unavailable)", brief)
        self.assertIn("could not be read; work from the title", brief)

    def test_a_long_diff_is_cut_and_a_fence_in_it_cannot_close_the_block(self):
        diff = "+```` fenced\n" + "x" * (sr.MAX_DIFF_CHARS + 10)
        brief = self._brief(_facts(diff=diff))
        self.assertIn("`````diff\n+```` fenced", brief)
        self.assertIn(f"cut at {sr.MAX_DIFF_CHARS} characters", brief)
        self.assertNotIn("x" * (sr.MAX_DIFF_CHARS + 1), brief)


def _answer(**fields):
    body = {"verdict": "APPROVE", "scope": SCOPE, "findings": [], "testing": "unit tests"}
    body.update(fields)
    return f"I reviewed it.\n\n```json\n{json.dumps(body)}\n```\n"


def _read(text, *, ok=True, seat=None, **kwargs):
    result = {"ok": ok, "text": text, "error_code": "timeout", "error": "boom"}
    return sr.read_seat_verdict(seat or _one("claude"), result, head_sha="abc123", **kwargs)


class ASeatsAnswerIsReadOrFailed(unittest.TestCase):
    def test_an_approval_parses_with_keels_attribution(self):
        verdict = _read(_answer())
        self.assertEqual(verdict.outcome, sr.APPROVE)
        self.assertEqual(
            verdict.item,
            {
                "reviewer": "swarm-review-a-claude",
                "verdict": "APPROVE",
                "scope": SCOPE,
                "findings": [],
                "testing": "unit tests",
                "vendor": "claude",
                "model": None,
            },
        )
        self.assertEqual(sr.posted_items([verdict]), [verdict.item])

    def test_a_request_for_changes_keeps_its_findings(self):
        finding = {"severity": " Minor ", "message": "naming", "path": "a.py", "line": 3}
        verdict = _read(_answer(verdict="request-changes", findings=[finding]))
        self.assertEqual(verdict.outcome, sr.REQUEST_CHANGES)
        self.assertEqual(verdict.findings[0]["severity"], "minor")
        self.assertEqual(verdict.to_dict()["findings"][0]["path"], "a.py")
        # A change request is posted beside the approvals (#1426 made it evidence).
        self.assertEqual(sr.posted_items([verdict])[0]["verdict"], "REQUEST_CHANGES")

    def test_approval_is_the_evidence_gates_own_reading(self):
        """swarm-review and the gate read a verdict with one function, so they cannot
        disagree: every token in APPROVING_VERDICTS approves, and is posted as APPROVE."""
        from keel import evidence

        for token in sorted(evidence.APPROVING_VERDICTS):
            with self.subTest(token=token):
                verdict = _read(_answer(verdict=token.lower()))
                self.assertEqual(verdict.outcome, sr.APPROVE)
                self.assertTrue(evidence.verdict_approves(f"Verdict: {verdict.item['verdict']}"))
        for token in sorted(evidence.REQUEST_CHANGES_VERDICTS):
            with self.subTest(token=token):
                self.assertEqual(_read(_answer(verdict=token)).outcome, sr.REQUEST_CHANGES)

    def test_an_approval_with_a_blocking_finding_is_a_request_for_changes(self):
        verdict = _read(_answer(findings=[{"severity": "major", "message": "data loss"}]))
        self.assertEqual(verdict.outcome, sr.REQUEST_CHANGES)
        self.assertIn("posted as REQUEST_CHANGES", verdict.reason)
        self.assertEqual(verdict.item["verdict"], "REQUEST_CHANGES")
        self.assertEqual(verdict.item["findings"], [{"severity": "major", "message": "data loss"}])

    def test_an_approval_that_does_not_parse_is_failed_never_an_approval(self):
        cases = {
            "the seat did not answer (timeout): boom": _read("", ok=False),
            "carries no JSON object": _read("LGTM, ship it! {not json}"),
            "its verdict 1 names no verdict": _read(_answer(verdict=1)),
            "its verdict '**' names no verdict": _read(_answer(verdict="**")),
            "approval does not parse: review #1 'findings' must be a list": _read(
                _answer(findings="x")
            ),
            "no scope": _read(_answer(scope="  ")),
            "severity 'blocker'": _read(
                _answer(findings=[{"severity": "blocker", "message": "m"}])
            ),
            "finding #1 has no message": _read(_answer(findings=[{"severity": "nit"}])),
            "finding #1 must be a JSON object": _read(_answer(findings=["x"])),
            "names nothing concrete": _read(_answer(scope="Looks good.")),
        }
        for expected, verdict in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(verdict.outcome, sr.FAILED)
                self.assertIn(expected, verdict.reason)
                self.assertIsNone(verdict.item)
                self.assertEqual(sr.posted_items([verdict]), [])


def _gate_holds(test, verdict, head="abc123"):
    """Post ``verdict`` as keel review renders it and run the real pre-merge gate on it."""
    from keel import artifacts, evidence, ship

    item = verdict.item
    body = artifacts.render_review_verdict(
        reviewer=item["reviewer"],
        head_sha=head,
        verdict=item["verdict"],
        scope=item["scope"],
        findings=item["findings"],
        testing=item["testing"],
        vendor=item["vendor"],
        model=item["model"],
    )
    report = evidence.verify(
        ship.resolve_review_contract(tier=1),
        pr_comments=[{"body": body, "author_association": "OWNER"}],
        head_sha=head,
        enforced=True,
        phase=evidence.PHASE_PRE_MERGE,
    )
    test.assertEqual(report["status"], evidence.STATUS_FAIL)
    test.assertIn(
        f"review-verdict-not-approved: {item['reviewer']} requests changes at {head}.",
        evidence.refusal_reason(report),
    )
    return body


class ASeatThatRejectsIsNeverDiscarded(unittest.TestCase):
    """Lead review of #1427: a rejection that failed validation used to become a failed seat,
    and the other seats' approvals then landed the change it rejected."""

    def test_a_thin_rejection_is_posted_and_the_gate_holds(self):
        verdict = _read(_answer(verdict="REQUEST_CHANGES", scope="Looks bad."))
        self.assertEqual(verdict.outcome, sr.REQUEST_CHANGES)
        self.assertEqual(verdict.item["scope"], "Looks bad.")
        _gate_holds(self, verdict)

    def test_a_rejection_with_an_invalid_severity_carries_it_as_major(self):
        bad = {"severity": "high", "message": "drops the lock"}
        good = {"severity": "minor", "message": "naming"}
        verdict = _read(_answer(verdict="REQUEST_CHANGES", findings=[bad, good, "loose"]))
        self.assertEqual(verdict.outcome, sr.REQUEST_CHANGES)
        carried, kept, loose = verdict.item["findings"]
        self.assertEqual(carried["severity"], "major")
        self.assertIn('"severity": "high"', carried["message"])
        self.assertIn("drops the lock", carried["message"])
        self.assertEqual(kept, good)
        self.assertEqual(loose["message"], "the seat's finding, as it wrote it: loose")
        body = _gate_holds(self, verdict)
        self.assertIn("- major: the seat's finding, as it wrote it:", body)

    def test_a_rejection_with_no_scope_gets_keels_sentence_and_holds(self):
        for scope in (None, "  ", 7):
            with self.subTest(scope=scope):
                verdict = _read(_answer(verdict="request-changes", scope=scope, testing=3))
                self.assertEqual(
                    verdict.item["scope"],
                    "swarm-review seat A (claude) requested changes at abc123; its answer "
                    "did not name what it checked",
                )
                self.assertIsNone(verdict.item["testing"])
                _gate_holds(self, verdict)

    def test_a_non_list_findings_value_is_carried_and_a_long_one_is_cut(self):
        verdict = _read(_answer(verdict="REQUEST_CHANGES", findings={"x": "y" * 900}))
        (carried,) = verdict.item["findings"]
        self.assertTrue(carried["message"].endswith("…"))
        self.assertLess(len(carried["message"]), sr.MAX_QUOTED_CHARS + 60)
        self.assertEqual(_read(_answer(verdict="REQUEST_CHANGES", findings=None)).findings, ())

    def test_any_word_that_does_not_approve_is_a_change_request(self):
        for word in ("COMMENT", "abstain", "BLOCK", "Reject — no"):
            with self.subTest(word=word):
                verdict = _read(_answer(verdict=word))
                self.assertEqual(verdict.outcome, sr.REQUEST_CHANGES)
                self.assertIn(
                    "does not approve, so it is posted as REQUEST_CHANGES", verdict.reason
                )
                self.assertEqual(verdict.item["verdict"], "REQUEST_CHANGES")
                _gate_holds(self, verdict)
        self.assertEqual(_read(_answer(verdict="REQUEST_CHANGES")).reason, "")

    def test_a_rejection_on_a_seat_with_no_plan_names_no_vendor(self):
        seat = _one("subagent:x", kind="subagent")
        verdict = sr.verdict_from_object(seat, {"verdict": "REQUEST_CHANGES"}, head_sha="h")
        self.assertIn("(unknown vendor)", verdict.item["scope"])
        self.assertIsNone(verdict.item["vendor"])

    def test_the_last_object_with_a_verdict_is_the_answer(self):
        text = '{"note": 1} first {"verdict": "REQUEST_CHANGES"} then ' + _answer()
        self.assertEqual(sr.extract_verdict_object(text)["verdict"], "APPROVE")
        self.assertIsNone(sr.extract_verdict_object('{"a": 1} [1]'))

    def test_a_null_scope_is_no_scope(self):
        self.assertIn("no scope", _read(_answer(scope=None)).reason)


class WhatIsPosted(unittest.TestCase):
    def _verdict(self, outcome, slot="A", **kwargs):
        kwargs.setdefault("item", {"r": slot})
        return sr.SeatVerdict(slot, f"r-{slot}", outcome, **kwargs)

    def test_enough_approvals_are_posted(self):
        verdicts = [self._verdict(sr.APPROVE), self._verdict(sr.APPROVE, "C")]
        self.assertEqual(sr.posting_decision(verdicts, required=2), "")

    def test_a_failed_seat_holds_the_approvals_fail_closed(self):
        verdicts = [
            self._verdict(sr.APPROVE),
            self._verdict(sr.APPROVE, "B"),
            self._verdict(sr.FAILED, "C", item=None),
        ]
        why = sr.posting_decision(verdicts, required=2)
        self.assertIn("seat(s) C did not return a readable verdict, so nothing is posted", why)
        self.assertIn("rerun swarm-review", why)

    def test_too_few_approvals_post_nothing(self):
        why = sr.posting_decision([self._verdict(sr.APPROVE)], required=2)
        self.assertIn("1 seat(s) approved and the tier requires at least 2", why)
        self.assertIn("at least 1", sr.posting_decision([], required=0))

    def test_mixed_verdicts_are_all_posted_and_the_rejection_names_the_status(self):
        verdicts = [self._verdict(sr.APPROVE), self._verdict(sr.REQUEST_CHANGES, "C")]
        self.assertEqual(sr.posting_decision(verdicts, required=2), "")
        self.assertEqual(sr.posted_status(verdicts), sr.POSTED_CHANGES_REQUESTED)
        self.assertEqual(sr.posted_items(verdicts), [{"r": "A"}, {"r": "C"}])
        self.assertEqual(sr.posted_status(verdicts[:1]), sr.POSTED)

    def test_beside_a_failed_seat_only_the_rejection_is_posted(self):
        verdicts = [
            self._verdict(sr.APPROVE),
            self._verdict(sr.REQUEST_CHANGES, "B"),
            self._verdict(sr.FAILED, "C", item=None),
        ]
        self.assertEqual(sr.posting_decision(verdicts, required=3), "")
        self.assertEqual(sr.posted_items(verdicts), [{"r": "B"}])
        self.assertEqual(sr.posted_status(verdicts), sr.POSTED_CHANGES_REQUESTED)

    def test_a_tampering_seat_holds_the_whole_cluster(self):
        verdicts = [self._verdict(sr.APPROVE), self._verdict(sr.FAILED, "C", tampered=True)]
        self.assertIn(
            "seat(s) C changed the repository's git setup",
            sr.posting_decision(verdicts, required=1),
        )

    def test_a_moved_head_posts_nothing(self):
        self.assertEqual(sr.head_moved("abc", "abc"), "")
        self.assertIn("moved from abc to def", sr.head_moved("abc", "def"))
        self.assertIn("to an unreadable head", sr.head_moved("abc", ""))

    def test_the_run_id_is_the_clusters_provenance_run_id(self):
        self.assertEqual(sr.review_run_id("s1", "c1"), "s1/c1")


class TheReport(unittest.TestCase):
    def test_the_wave_succeeds_only_when_every_cluster_is_clean(self):
        clean = sr.ClusterReview("c1", sr.POSTED)
        merged = sr.ClusterReview("c2", sr.MERGED)
        held = sr.ClusterReview("c3", sr.HELD)
        self.assertEqual(sr.SwarmReviewResult("s", 1, False).status, "failed")
        self.assertEqual(sr.SwarmReviewResult("s", 1, False, (clean, merged)).status, "success")
        self.assertEqual(sr.SwarmReviewResult("s", 1, False, (clean, held)).status, "failed")
        self.assertTrue(clean.posted)
        self.assertFalse(held.posted)
        rejected = sr.ClusterReview("c4", sr.POSTED_CHANGES_REQUESTED)
        self.assertTrue(rejected.posted)
        self.assertEqual(sr.SwarmReviewResult("s", 1, False, (clean, rejected)).status, "failed")

    def test_it_reports_as_json_and_text(self):
        claude, agy = _seats(_seat("claude"), _seat("agy", slot="C"))
        finding = {"severity": "minor", "message": "naming"}
        verdict = sr.SeatVerdict("A", claude.reviewer, sr.REQUEST_CHANGES, "why", (finding,))
        cluster = sr.ClusterReview(
            "c1",
            sr.HELD,
            "nothing is posted",
            9,
            "abc1234567890ff",
            2,
            2,
            (claude, agy),
            (verdict,),
            ("checkout left",),
        )
        planned = sr.with_status(sr.ClusterReview("c2", sr.ERROR, seats=(claude,)), sr.PLANNED, "")
        result = sr.SwarmReviewResult("s1", 1, False, (cluster, planned), ("wave warning",))
        payload = result.to_dict()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["clusters"][0]["verdicts"][0]["outcome"], sr.REQUEST_CHANGES)
        self.assertEqual(payload["clusters"][0]["seats"][1]["eligible"], False)
        self.assertEqual(payload["warnings"], ["wave warning"])
        text = sr.render_swarm_review_result(result)
        self.assertIn("keel swarm-review — live  swarm s1, wave 1: failed", text)
        self.assertIn("c1: PR #9 @ abc123456789 — held", text)
        self.assertIn("tier 2: requires 2 verdict(s)", text)
        self.assertIn("seat A claude over cli: REQUEST_CHANGES — why", text)
        self.assertIn("      - minor: naming", text)
        self.assertIn("seat C agy over cli: refused — its vendor 'agy'", text)
        self.assertIn("    nothing is posted", text)
        self.assertIn("    warning: checkout left", text)
        self.assertIn("c2: no PR — planned", text)
        self.assertIn("seat A claude over cli: would review as swarm-review-a-claude", text)
        self.assertIn("  warning: wave warning", text)
        approved = sr.SeatVerdict("A", claude.reviewer, sr.APPROVE)
        text = sr.render_swarm_review_result(
            sr.SwarmReviewResult(
                "s1",
                1,
                True,
                (sr.ClusterReview("c1", sr.POSTED, seats=(claude,), verdicts=(approved,)),),
            )
        )
        self.assertIn("dry-run", text)
        self.assertIn("seat A claude over cli: APPROVE\n", text + "\n")
        refused = _one("subagent:x", kind="subagent")
        self.assertIn(
            "seat A subagent:x: refused",
            sr.render_swarm_review_result(
                sr.SwarmReviewResult(
                    "s", 1, True, (sr.ClusterReview("c", sr.REFUSED, seats=(refused,)),)
                )
            ),
        )
        self.assertIn(
            "no cluster in this wave", sr.render_swarm_review_result(result.__class__("s", 1, True))
        )


if __name__ == "__main__":
    unittest.main()
