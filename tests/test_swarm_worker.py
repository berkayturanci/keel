"""Unit tests for the live swarm worker's pure decisions (:mod:`keel.swarm_worker`, #1400).

Consent: an approved contract becomes a delegation that records who, which scopes, which
run and clusters, and when; a worker is handed exactly the parent's scopes, and nothing for
a cluster the delegation does not name. Seat: the cluster's resolved implementer seat is
planned as ``keel delegate run --role implement`` would plan it, and a seat keel cannot run
as a worker is refused with the reason. Writing: brief, commit and pull request text.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from keel import consent, delegate, swarm_worker
from keel.swarm import IssueScope, SwarmCluster

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _contract(scopes=("filesystem", "git", "github"), *, operator="ops", mode="explicit"):
    return consent.build_consent_contract(
        command="swarm-run",
        side_effects=swarm_worker.WORKER_SIDE_EFFECTS,
        dry_run=False,
        approved_scopes=scopes,
        approval_source="flag",
        mode=mode,
        operator=operator,
        target="repo",
        now=NOW,
    )


def _assignment(provider, *, model=None, effort=None, kind="provider", source="team.x"):
    return {
        "implementer": {
            "provider": provider,
            "name": provider,
            "kind": kind,
            "model": model,
            "effort": effort,
            "source": source,
        }
    }


def _cluster(*issues, scope=("src/a.py",)):
    return SwarmCluster(cluster_id="c1", issues=issues, role="core", combined_scope=scope)


def _plan():
    plan, _ = swarm_worker.plan_implementer(
        _assignment("codex"), config=None, registry=None, prompt_path="/b.md", cwd="/wt"
    )
    assert plan is not None
    return plan


class TheOperatorsConsentIsDelegatedExplicitly(unittest.TestCase):
    def test_an_approved_contract_records_who_what_and_when(self):
        delegation, reason = swarm_worker.delegate_consent(
            _contract(), swarm_id="s1", cluster_ids=["c1", "c2"]
        )
        self.assertEqual(reason, "")
        self.assertEqual(
            delegation.to_dict(),
            {
                "swarm_id": "s1",
                "clusters": ["c1", "c2"],
                "scopes": ["filesystem", "git", "github"],
                "operator": "ops",
                "source": "flag",
                "mode": "explicit",
                "delegated_at": "2026-09-30T12:00:00Z",
                "consent_record": _contract()["consent_record"],
            },
        )

    def test_a_scope_the_operator_did_not_approve_refuses_the_delegation(self):
        delegation, reason = swarm_worker.delegate_consent(
            _contract(("filesystem", "git")), swarm_id="s1", cluster_ids=["c1"]
        )
        self.assertIsNone(delegation)
        self.assertIn("operator consent required", reason)
        self.assertIn("github", reason)

    def test_an_extra_approved_scope_is_not_handed_down(self):
        """The parent's scopes are the ones this command needs; `secrets` stays behind."""
        delegation, _ = swarm_worker.delegate_consent(
            _contract(("filesystem", "git", "github", "secrets")), swarm_id="s", cluster_ids=[]
        )
        self.assertEqual(delegation.scopes, ("filesystem", "git", "github"))

    def test_agent_mode_approves_nothing_a_worker_can_be_handed(self):
        delegation, reason = swarm_worker.delegate_consent(
            _contract((), mode="agent"), swarm_id="s1", cluster_ids=["c1"]
        )
        self.assertIsNone(delegation)
        self.assertIn("'agent-delegated'", reason)
        self.assertIn("--approve-scope filesystem,git,github --operator NAME", reason)

    def test_an_anonymous_consent_is_not_delegated(self):
        delegation, reason = swarm_worker.delegate_consent(
            _contract(operator=None), swarm_id="s1", cluster_ids=["c1"]
        )
        self.assertIsNone(delegation)
        self.assertIn("--operator NAME", reason)

    def test_for_run_names_the_run_and_keeps_the_scopes(self):
        delegation, _ = swarm_worker.delegate_consent(_contract(), swarm_id="", cluster_ids=())
        run = delegation.for_run("s9", iter(["c1"]))
        self.assertEqual(
            (run.swarm_id, run.clusters, run.scopes), ("s9", ("c1",), delegation.scopes)
        )


class AWorkerHoldsExactlyWhatItWasHanded(unittest.TestCase):
    def setUp(self):
        self.delegation, _ = swarm_worker.delegate_consent(
            _contract(), swarm_id="s1", cluster_ids=["c1"]
        )

    def test_a_delegated_cluster_gets_the_parents_scopes(self):
        self.assertEqual(
            swarm_worker.worker_scopes(self.delegation, "c1"), ("filesystem", "git", "github")
        )

    def test_a_cluster_outside_the_delegation_gets_nothing(self):
        self.assertEqual(swarm_worker.worker_scopes(self.delegation, "c2"), ())

    def test_may_answers_each_mutation_from_the_held_scopes(self):
        held = ("filesystem", "git")
        self.assertEqual(swarm_worker.may(held, "git_commit"), (True, ""))
        allowed, reason = swarm_worker.may(held, "pull_request")
        self.assertFalse(allowed)
        self.assertIn("pull_request needs the github consent scope", reason)
        self.assertIn("holds: filesystem, git", reason)
        self.assertIn("holds: none", swarm_worker.may((), "git_worktree")[1])

    def test_a_worker_short_of_any_scope_may_not_start(self):
        self.assertEqual(swarm_worker.consent_refusal(("filesystem", "git", "github")), "")
        self.assertIn(
            "pull_request needs the github", swarm_worker.consent_refusal(("filesystem", "git"))
        )
        self.assertIn("git_worktree needs", swarm_worker.consent_refusal(()))

    def test_the_required_scopes_are_the_three_a_worker_uses(self):
        self.assertEqual(swarm_worker.required_scopes(), ("filesystem", "git", "github"))

    def test_the_childrens_environment_carries_no_consent(self):
        env = {"PATH": "/bin", "KEEL_APPROVE_SCOPE": "github", "KEEL_OPERATOR": "o"}
        env["KEEL_CONSENT_MODE"] = "standing"
        self.assertEqual(swarm_worker.child_env(env), {"PATH": "/bin"})


class TheResolvedSeatIsWhatIsPlanned(unittest.TestCase):
    def test_a_provider_seat_is_planned_as_delegate_run_plans_it(self):
        plan, reason = swarm_worker.plan_implementer(
            _assignment("codex", model="gpt-5", effort="high"),
            config=None,
            registry=None,
            prompt_path="/b.md",
            cwd="/wt",
            timeout=99,
        )
        self.assertEqual(reason, "")
        expected = delegate.plan_run(
            delegate.resolve_provider(None, None, "codex").provider,
            "implement",
            "/b.md",
            "/wt",
            99,
            "high",
            "gpt-5",
        )
        self.assertEqual(plan, expected)
        self.assertEqual((plan.provider, plan.role, plan.cwd), ("codex", "implement", "/wt"))

    def test_no_assignment_is_refused(self):
        plan, reason = swarm_worker.plan_implementer(
            None, config=None, registry=None, prompt_path="/b", cwd="/wt"
        )
        self.assertIsNone(plan)
        self.assertIn("no resolved assignment", reason)

    def test_a_host_subagent_seat_is_refused(self):
        plan, reason = swarm_worker.plan_implementer(
            _assignment("subagent:backend-developer", kind="subagent", source="team.implement"),
            config=None,
            registry=None,
            prompt_path="/b",
            cwd="/wt",
        )
        self.assertIsNone(plan)
        self.assertIn("host subagent", reason)
        self.assertIn("--delegate", reason)

    def test_an_unknown_provider_is_refused_with_the_delegate_error(self):
        plan, reason = swarm_worker.plan_implementer(
            _assignment("nope"), config=None, registry=None, prompt_path="/b", cwd="/wt"
        )
        self.assertIsNone(plan)
        self.assertIn("(unknown-provider)", reason)

    def test_a_transport_that_cannot_edit_a_worktree_is_refused(self):
        plan, reason = swarm_worker.plan_implementer(
            _assignment("ollama", model="qwen2.5"),
            config=None,
            registry=None,
            prompt_path="/b",
            cwd="/wt",
        )
        self.assertIsNone(plan)
        self.assertIn("'ollama' transport", reason)


class WhatAWorkerWrites(unittest.TestCase):
    scopes = {
        7: IssueScope(issue=7, title="Fix the thing", body="It is broken."),
        8: IssueScope(issue=8),
    }

    def test_the_brief_names_the_issues_the_scope_and_the_limits(self):
        brief = swarm_worker.render_brief(
            _cluster(7, 8, 9), self.scopes, swarm_id="s1", branch="swarm/s1/c1", base_branch="dev"
        )
        self.assertIn("cluster c1 of keel swarm s1", brief)
        self.assertIn("on branch swarm/s1/c1, cut from dev", brief)
        self.assertIn("within the cluster's scope: src/a.py.", brief)
        self.assertIn("Do not commit, push, open a pull request", brief)
        self.assertIn("## Issue #7: Fix the thing\n\nIt is broken.", brief)
        self.assertIn("## Issue #8: (title unavailable)", brief)
        self.assertIn("## Issue #9: (title unavailable)", brief)
        self.assertIn("could not be read", brief)

    def test_a_cluster_without_a_scope_is_briefed_as_anywhere(self):
        brief = swarm_worker.render_brief(
            _cluster(7, scope=()), {}, swarm_id="s", branch="b", base_branch="main"
        )
        self.assertIn("scope: *.", brief)

    def test_the_commit_names_the_issues_and_the_seat(self):
        message = swarm_worker.commit_message(_cluster(7, 8), swarm_id="s1", plan=_plan())
        self.assertTrue(message.startswith("feat(swarm): implement #7, #8 (s1/c1)\n"))
        self.assertIn("implementer seat codex through keel swarm-run --live", message)
        self.assertIn("Refs #7\nRefs #8", message)

    def test_a_one_issue_cluster_is_titled_after_its_issue(self):
        title = swarm_worker.pull_request_title(_cluster(7), self.scopes, swarm_id="s1")
        self.assertEqual(title, "Fix the thing (#7, swarm s1/c1)")

    def test_other_clusters_are_titled_after_the_cluster(self):
        for cluster in (_cluster(8), _cluster(7, 8)):
            with self.subTest(issues=cluster.issues):
                title = swarm_worker.pull_request_title(cluster, self.scopes, swarm_id="s1")
                self.assertTrue(title.startswith("swarm s1/c1: implement #"), title)

    def test_the_body_refs_never_closes_and_records_the_consent(self):
        delegation, _ = swarm_worker.delegate_consent(
            _contract(), swarm_id="s1", cluster_ids=["c1"]
        )
        body = swarm_worker.pull_request_body(
            _cluster(7),
            swarm_id="s1",
            branch="swarm/s1/c1",
            base_branch="main",
            commit="abc123",
            plan=_plan(),
            seat_source=None,
            delegation=delegation,
        )
        self.assertIn("Refs #7", body)
        self.assertNotIn("Closes", body)
        self.assertIn("`swarm/s1/c1`, cut from `main`, at `abc123`", body)
        self.assertIn("seat from `unknown`", body)
        self.assertIn("delegated by `ops` (flag, 2026-09-30T12:00:00Z)", body)
        self.assertIn("no review evidence yet", body)


if __name__ == "__main__":
    unittest.main()
