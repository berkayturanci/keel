"""Unit tests for swarm landing: each cluster's pull request lands through keel merge (#1287)."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from keel import cli as cli_mod
from keel import runtime
from keel.cli import main
from keel.runner import CommandResult
from keel.swarm import (
    IssueScope,
    SwarmCluster,
    SwarmLandingResult,
    SwarmPlan,
    SwarmRunState,
    SwarmWave,
    SwarmWorkerStatus,
    build_swarm_plan,
    evaluate_wave_landing_mode,
    load_swarm_state,
    render_swarm_landing_result,
    save_swarm_plan,
    save_swarm_state,
)
from keel.swarm_landing import (
    FAILED,
    HELD,
    LANDED,
    ClusterMerge,
    PullRequestLookup,
    land_wave_clusters,
    merge_outcome,
    pull_request_from_list,
    pull_request_from_view,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
KEEL_YAML = str(REPO_ROOT / "projects" / "keel.yaml")

#: swarm-plan/-run/-land read every named issue with `gh issue view` (#1274). The suite
#: is offline (AGENTS.md), so every test gets an unreadable issue unless it patches its
#: own; the scope then comes from the flags, or is `*`.
_ISSUE_STUB = patch(
    "keel.github.issue_facts",
    return_value=CommandResult(False, 1, "stubbed: the suite never runs gh"),
)


def setUpModule():
    _ISSUE_STUB.start()


def tearDownModule():
    _ISSUE_STUB.stop()


def _two_cluster_plan(swarm_id: str = "swarm-t") -> SwarmPlan:
    scopes = [
        IssueScope(issue=101, predicted_files=("src/a.py",), scope_source="issue-body"),
        IssueScope(issue=102, predicted_files=("docs/b.md",), scope_source="issue-body"),
    ]
    plan = build_swarm_plan(scopes, swarm_id=swarm_id)
    assert [[c.cluster_id for c in w.clusters] for w in plan.waves] == [
        ["cluster-1-101", "cluster-1-102"]
    ], "fixture: one wave of two disjoint clusters"
    return plan


def _state(swarm_id: str, prs: dict[str, int | None]) -> SwarmRunState:
    workers = tuple(
        SwarmWorkerStatus(
            cluster_id=cid,
            issue=int(cid.rsplit("-", 1)[1]),
            role="core",
            step="s6",
            status="passed",
            pull_request=number,
        )
        for cid, number in prs.items()
    )
    return SwarmRunState(swarm_id=swarm_id, total_workers=len(workers), workers=workers)


class _Checkout:
    """A recording runner for the checkout postcondition: HEAD on ``branch`` throughout,
    unless ``moves_to`` says where the next read after the first finds it."""

    def __init__(self, branch: str = "feature", *, moves_to: str | None = None, back_ok=True):
        self.branch = branch
        self.moves_to = moves_to
        self.back_ok = back_ok
        self.commands: list[list[str]] = []
        self._reads = 0

    def __call__(self, cmd: list[str], cwd: Path) -> CommandResult:
        self.commands.append(list(cmd))
        if cmd[:2] == ["git", "symbolic-ref"]:
            self._reads += 1
            where = self.moves_to if self.moves_to and self._reads > 1 else self.branch
            return CommandResult(ok=True, code=0, output=where)
        if cmd[:2] == ["git", "checkout"]:
            return CommandResult(ok=self.back_ok, code=0 if self.back_ok else 1, output="nope")
        return CommandResult(ok=False, code=1, output="")


class _Recorder:
    """Recorded answers for the two seams: the lookup and keel merge."""

    def __init__(self, found=None, merged=None):
        self.found = found or {}
        self.merged = merged or {}
        self.lookups: list[tuple[str, int | None]] = []
        self.merges: list[tuple[int, str, bool]] = []

    def find(self, branch: str, recorded: int | None) -> PullRequestLookup:
        self.lookups.append((branch, recorded))
        return self.found.get(branch, PullRequestLookup(recorded))

    def merge(self, pr: int, cluster_id: str, dry_run: bool) -> ClusterMerge:
        self.merges.append((pr, cluster_id, dry_run))
        answer = self.merged.get(pr, ClusterMerge(LANDED, "merged"))
        if isinstance(answer, Exception):
            raise answer
        return answer


def _land(plan, rec, *, root, dry_run=False, wave=1, runner=None):
    return land_wave_clusters(
        plan,
        wave_index=wave,
        root=root,
        dry_run=dry_run,
        find_pull_request=rec.find,
        merge_pull_request=rec.merge,
        runner=runner or _Checkout(),
    )


class TestSwarmLandingPureLogic(unittest.TestCase):
    def test_evaluate_wave_landing_mode_single_and_disjoint(self):
        c1 = SwarmCluster(cluster_id="c1", issues=(101,), role="core", combined_scope=("src/a.py",))
        w_single = SwarmWave(
            wave_index=1, mode="orthogonal_parallel", eligible_direct_landing=True, clusters=(c1,)
        )
        dec_single = evaluate_wave_landing_mode(w_single, {})
        self.assertEqual((dec_single.mode, dec_single.reason), ("direct_batch", "single_cluster"))
        self.assertEqual(dec_single.to_dict()["mode"], "direct_batch")
        c2 = SwarmCluster(
            cluster_id="c2", issues=(102,), role="docs", combined_scope=("docs/a.md",)
        )
        w_multi = SwarmWave(
            wave_index=1,
            mode="orthogonal_parallel",
            eligible_direct_landing=True,
            clusters=(c1, c2),
        )
        dec_multi = evaluate_wave_landing_mode(w_multi, {"c1": ["src/a.py"], "c2": ["docs/a.md"]})
        self.assertEqual(
            (dec_multi.mode, dec_multi.reason), ("direct_batch", "orthogonal_diff_trees")
        )

    def test_evaluate_wave_landing_mode_overlapping_diffs(self):
        c1 = SwarmCluster(cluster_id="c1", issues=(101,), role="core", combined_scope=("x.py",))
        c2 = SwarmCluster(cluster_id="c2", issues=(102,), role="core", combined_scope=("x.py",))
        wave = SwarmWave(
            wave_index=1,
            mode="orthogonal_parallel",
            eligible_direct_landing=True,
            clusters=(c1, c2),
        )
        dec = evaluate_wave_landing_mode(wave, {"c1": ["x.py"], "c2": ["x.py"]})
        self.assertEqual((dec.mode, dec.reason), ("sequential_funnel", "overlapping_diff_trees"))

    def test_render_swarm_landing_result(self):
        out_ok = render_swarm_landing_result(
            SwarmLandingResult(
                swarm_id="swarm-ok",
                wave_index=1,
                mode="direct_batch",
                landed_clusters=("c1", "c2"),
                failed_clusters=(),
                status="success",
                pull_requests=(("c1", 10), ("c2", 11)),
            )
        )
        self.assertIn("keel swarm land — swarm-ok (wave 1)", out_ok)
        self.assertIn("status  : success ✓", out_ok)
        self.assertIn("landed  : c1, c2", out_ok)
        self.assertIn("PRs     : c1 #10, c2 #11", out_ok)
        self.assertNotIn("held", out_ok)
        self.assertNotIn("healed", out_ok)

        out_held = render_swarm_landing_result(
            SwarmLandingResult(
                swarm_id="swarm-held",
                wave_index=2,
                mode="direct_batch",
                landed_clusters=(),
                failed_clusters=("c3",),
                status="failed",
                held_clusters=(("c2", "PR #10: keel merge: merge window is closed"),),
                warnings=("c1: drift",),
            )
        )
        self.assertIn("status  : failed ✗", out_held)
        self.assertIn("failed  : c3", out_held)
        self.assertIn("held    : not landed", out_held)
        self.assertIn("c2: PR #10: keel merge: merge window is closed", out_held)
        self.assertIn("warning : c1: drift", out_held)
        self.assertNotIn("PRs", out_held)

    def test_the_landing_result_reports_its_pull_requests_as_json(self):
        result = SwarmLandingResult(
            swarm_id="s",
            wave_index=1,
            mode="direct_batch",
            landed_clusters=("c1",),
            failed_clusters=(),
            status="success",
            pull_requests=(("c1", 10),),
        )
        self.assertEqual(result.to_dict()["pull_requests"], {"c1": 10})
        self.assertNotIn("healed_clusters", result.to_dict())


class TheClustersPullRequestIsFound(unittest.TestCase):
    """The recorded pull request is confirmed open for the branch; the list is the fallback."""

    BRANCH = "swarm/s/cluster-1-101"

    def _view(self, **fields):
        reply = {
            "number": 10,
            "state": "OPEN",
            "headRefName": self.BRANCH,
            "baseRefName": "main",
            **fields,
        }
        return pull_request_from_view(10, reply, branch=self.BRANCH, base_branch="main")

    def test_an_open_recorded_pull_request_is_landed(self):
        self.assertEqual(self._view(), PullRequestLookup(10))

    def test_a_recorded_pull_request_that_is_not_open_holds(self):
        self.assertIn("already merged", self._view(state="MERGED").reason)
        closed = self._view(state="CLOSED")
        self.assertIsNone(closed.number)
        self.assertIn("PR #10 is closed; reopen it", closed.reason)
        self.assertIn("is not open", self._view(state="").reason)

    def test_a_recorded_pull_request_for_another_branch_or_base_holds(self):
        other = self._view(headRefName="feat/x")
        self.assertEqual(other.number, None)
        self.assertIn("is for branch feat/x", other.reason)
        base = self._view(baseRefName="develop")
        self.assertIn("targets develop, not the configured base branch main", base.reason)

    def test_an_unreadable_view_holds(self):
        for reply in (None, [], {"number": 11, "state": "OPEN"}):
            with self.subTest(reply=reply):
                found = pull_request_from_view(10, reply, branch=self.BRANCH, base_branch="main")
                self.assertEqual(found.number, None)
                self.assertIn("could not be read", found.reason)

    def test_the_list_fallback_takes_the_one_open_pull_request(self):
        reply = [{"number": 9, "state": "CLOSED"}, {"number": 10, "state": "OPEN"}]
        self.assertEqual(pull_request_from_list(reply, branch=self.BRANCH), PullRequestLookup(10))

    def test_the_list_fallback_holds_when_it_cannot_choose(self):
        two = [{"number": 10, "state": "OPEN"}, {"number": 11, "state": "OPEN"}]
        self.assertIn("ambiguous: 2 open PRs", pull_request_from_list(two, branch="b").reason)
        merged = [{"number": 7, "state": "MERGED"}]
        self.assertIn("PR #7 is already merged", pull_request_from_list(merged, branch="b").reason)
        for reply in ([], None, ["junk"], [{"number": "1", "state": "OPEN"}]):
            with self.subTest(reply=reply):
                none = pull_request_from_list(reply, branch="b")
                self.assertEqual(none.number, None)
                self.assertIn("no open pull request for b", none.reason)


class KeelMergesAnswerIsTheClustersOutcome(unittest.TestCase):
    def test_a_refusal_before_the_pull_request_holds(self):
        self.assertEqual(merge_outcome(1, None).outcome, HELD)
        self.assertIn("reason is on stderr", merge_outcome(1, None).reason)

    def test_a_merge_or_a_passing_dry_run_lands(self):
        self.assertEqual(merge_outcome(0, {"reason": "merged"}), ClusterMerge(LANDED, "merged", ""))
        dry = merge_outcome(0, {"reason": "dry-run: merge not performed"})
        self.assertEqual((dry.outcome, dry.reason), (LANDED, "dry-run: merge not performed"))

    def test_a_drifted_merge_lands_with_a_warning(self):
        drift = merge_outcome(3, {"reason": "merged, but drift detected", "pull_request": 10})
        self.assertEqual(drift.outcome, LANDED)
        self.assertIn("PR #10 merged, but keel merge detected drift", drift.warning)

    def test_a_failed_merge_call_fails_and_any_other_refusal_holds(self):
        failed = merge_outcome(1, {"reason": "gh merge failed", "merge_output": "409 conflict"})
        self.assertEqual(failed.outcome, FAILED)
        self.assertIn("gh merge failed: 409 conflict", failed.reason)
        held = merge_outcome(1, {"reason": "PR merge state is DIRTY"})
        self.assertEqual(held, ClusterMerge(HELD, "keel merge: PR merge state is DIRTY"))
        self.assertEqual(merge_outcome(1, {}).reason, "keel merge: no reason given")


class TheWaveLandsEachPullRequestInOrder(unittest.TestCase):
    def test_each_cluster_goes_to_keel_merge_with_its_recorded_pull_request(self):
        plan = _two_cluster_plan()
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            save_swarm_state(_state(plan.swarm_id, {"cluster-1-101": 10, "cluster-1-102": 11}), tmp)
            result = _land(plan, rec, root=tmp)
            state = load_swarm_state(plan.swarm_id, root=tmp)
        self.assertEqual(
            rec.lookups,
            [("swarm/swarm-t/cluster-1-101", 10), ("swarm/swarm-t/cluster-1-102", 11)],
        )
        self.assertEqual(rec.merges, [(10, "cluster-1-101", False), (11, "cluster-1-102", False)])
        self.assertEqual(
            (result.status, result.landed_clusters),
            ("success", tuple(["cluster-1-101", "cluster-1-102"])),
        )
        self.assertEqual(result.pull_requests, (("cluster-1-101", 10), ("cluster-1-102", 11)))
        self.assertEqual(
            [(w.status, w.step, w.pull_request, w.details) for w in state.workers],
            [
                ("merged", "s10", 10, "PR #10: merged"),
                ("merged", "s10", 11, "PR #11: merged"),
            ],
        )

    def test_a_cluster_without_a_record_is_looked_up_without_one(self):
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            _land(_two_cluster_plan(), rec, root=tmp)
        self.assertEqual([recorded for _, recorded in rec.lookups], [None, None])

    def test_a_held_cluster_does_not_stop_the_next_one(self):
        plan = _two_cluster_plan()
        rec = _Recorder(
            found={"swarm/swarm-t/cluster-1-101": PullRequestLookup(None, "no open pull request")},
            merged={11: ClusterMerge(HELD, "keel merge: missing evidence: review-verdict-1")},
        )
        with tempfile.TemporaryDirectory() as tmp:
            save_swarm_state(
                _state(plan.swarm_id, {"cluster-1-101": None, "cluster-1-102": 11}), tmp
            )
            result = _land(plan, rec, root=tmp)
            state = load_swarm_state(plan.swarm_id, root=tmp)
        self.assertEqual(rec.merges, [(11, "cluster-1-102", False)])
        self.assertEqual(
            result.held_clusters,
            (
                ("cluster-1-101", "no open pull request"),
                ("cluster-1-102", "PR #11: keel merge: missing evidence: review-verdict-1"),
            ),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual([w.status for w in state.workers], ["held", "held"])
        self.assertEqual([w.pull_request for w in state.workers], [None, 11])

    def test_a_failed_or_raising_merge_fails_that_cluster_only(self):
        plan = _two_cluster_plan()
        rec = _Recorder(merged={10: RuntimeError("boom"), 11: ClusterMerge(LANDED, "merged")})
        with tempfile.TemporaryDirectory() as tmp:
            save_swarm_state(_state(plan.swarm_id, {"cluster-1-101": 10, "cluster-1-102": 11}), tmp)
            try:
                result = _land(plan, rec, root=tmp)
            except RuntimeError as exc:
                self.fail(f"one cluster's keel merge raising stopped the wave: {exc}")
            else:
                state = load_swarm_state(plan.swarm_id, root=tmp)
                self.assertEqual(result.failed_clusters, ("cluster-1-101",))
                self.assertEqual(result.landed_clusters, ("cluster-1-102",))
                self.assertEqual(result.status, "partial_failure")
                self.assertEqual(state.workers[0].status, "failed")
                self.assertIn("keel merge raised RuntimeError: boom", state.workers[0].details)

    def test_a_drift_warning_is_carried_to_the_result(self):
        rec = _Recorder(merged={10: ClusterMerge(LANDED, "merged, but drift", "look at it")})
        plan = _two_cluster_plan()
        with tempfile.TemporaryDirectory() as tmp:
            save_swarm_state(_state(plan.swarm_id, {"cluster-1-101": 10, "cluster-1-102": 11}), tmp)
            result = _land(plan, rec, root=tmp)
        self.assertEqual(result.warnings, ("cluster-1-101: look at it",))

    def test_a_dry_run_asks_for_keel_merges_dry_run_and_writes_no_state(self):
        plan = _two_cluster_plan()
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            save_swarm_state(_state(plan.swarm_id, {"cluster-1-101": 10, "cluster-1-102": 11}), tmp)
            before = (Path(tmp) / ".keel/state/swarm/swarm-t.json").read_text(encoding="utf-8")
            result = _land(plan, rec, root=tmp, dry_run=True)
            after = (Path(tmp) / ".keel/state/swarm/swarm-t.json").read_text(encoding="utf-8")
        self.assertEqual([dry for _, _, dry in rec.merges], [True, True])
        self.assertEqual(result.landed_clusters, ("cluster-1-101", "cluster-1-102"))
        self.assertEqual(before, after)

    def test_an_unknown_wave_lands_nothing(self):
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            result = _land(_two_cluster_plan(), rec, root=tmp, wave=9)
        self.assertEqual((result.mode, result.status), ("none", "failed"))
        self.assertEqual((rec.lookups, rec.merges), ([], []))


class TheCheckoutIsLeftWhereItStarted(unittest.TestCase):
    """#1279's promise holds without a guard doing anything: landing merges on the host
    and checks nothing out. The postcondition is still checked after every wave."""

    def test_landing_runs_no_local_checkout_merge_or_rebase(self):
        runner = _Checkout()
        with tempfile.TemporaryDirectory() as tmp:
            _land(_two_cluster_plan(), _Recorder(), root=tmp, runner=runner)
        self.assertTrue(runner.commands, "the postcondition read HEAD")
        self.assertEqual({tuple(c[:2]) for c in runner.commands}, {("git", "symbolic-ref")})

    def test_a_checkout_that_moved_is_put_back(self):
        runner = _Checkout(moves_to="main")
        with tempfile.TemporaryDirectory() as tmp:
            result = _land(_two_cluster_plan(), _Recorder(), root=tmp, runner=runner)
        self.assertIn(["git", "checkout", "feature", "--"], runner.commands)
        self.assertEqual(result.warnings, ())

    def test_a_return_that_fails_is_reported(self):
        runner = _Checkout(moves_to="main", back_ok=False)
        with tempfile.TemporaryDirectory() as tmp:
            result = _land(_two_cluster_plan(), _Recorder(), root=tmp, runner=runner)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("could not return the checkout to feature (nope)", result.warnings[0])

    def test_a_detached_head_is_returned_detached_and_an_unreadable_one_is_left(self):
        def detached(cmd, cwd):
            if cmd[:2] == ["git", "rev-parse"]:
                return CommandResult(ok=True, code=0, output="abc123")
            return CommandResult(ok=False, code=1, output="")

        calls: list[list[str]] = []

        def unreadable(cmd, cwd):
            calls.append(cmd)
            return CommandResult(ok=False, code=1, output="")

        with tempfile.TemporaryDirectory() as tmp:
            result = _land(_two_cluster_plan(), _Recorder(), root=tmp, runner=detached)
            self.assertEqual(result.warnings, ())
            _land(_two_cluster_plan(), _Recorder(), root=tmp, runner=unreadable)
        self.assertFalse([c for c in calls if c[:2] == ["git", "checkout"]])

    def test_the_default_runner_is_used_when_none_is_given(self):
        with (
            patch("keel.swarm_landing.default_runner", side_effect=_Checkout()) as runner,
            tempfile.TemporaryDirectory() as tmp,
        ):
            land_wave_clusters(
                _two_cluster_plan(),
                wave_index=1,
                root=tmp,
                dry_run=True,
                find_pull_request=_Recorder().find,
                merge_pull_request=_Recorder().merge,
            )
        self.assertTrue(runner.called)


class ADependentWaveIsRefused(unittest.TestCase):
    """#1276, kept: a ``sequential_dependent`` wave's branches were cut before the wave it
    depends on landed, so ``swarm-land`` refuses it — no lookup, no merge — until the
    earlier wave lands and the rest is re-planned."""

    def _chain(self) -> SwarmPlan:
        scopes = [IssueScope(issue=n, title=f"T{n}", predicted_files=("src/a.py",)) for n in (1, 2)]
        plan = build_swarm_plan(scopes, swarm_id="swarm-dep")
        self.assertEqual(
            [(w.mode, w.clusters[0].depends_on_issues) for w in plan.waves],
            [("orthogonal_parallel", ()), ("sequential_dependent", (1,))],
            "fixture: wave 2 depends on wave 1",
        )
        return plan

    def test_a_dependent_wave_touches_nothing_live_or_dry(self):
        for dry_run in (False, True):
            rec, runner = _Recorder(), _Checkout()
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as tmp:
                result = _land(self._chain(), rec, root=tmp, wave=2, dry_run=dry_run, runner=runner)
                self.assertEqual((result.mode, result.status), ("refused", "failed"))
                self.assertIn(
                    "wave 2 depends on issues landed by an earlier wave (#1)", result.refused
                )
                self.assertIn("no pull request was merged", result.refused)
                self.assertEqual((rec.lookups, rec.merges, runner.commands), ([], [], []))
                self.assertIn("refused : wave 2 depends", render_swarm_landing_result(result))

    def test_wave_one_still_lands(self):
        rec = _Recorder(found={"swarm/swarm-dep/cluster-1-1": PullRequestLookup(5)})
        with tempfile.TemporaryDirectory() as tmp:
            result = _land(self._chain(), rec, root=tmp, wave=1)
        self.assertEqual((result.mode, result.status), ("direct_batch", "success"))
        self.assertEqual(result.landed_clusters, ("cluster-1-1",))


# --------------------------------------------------------------------------------------
# End to end: `keel swarm-land` runs keel merge's own code for every cluster.
# --------------------------------------------------------------------------------------


def _capabilities():
    return runtime.CapabilityReport(
        tuple(
            runtime.Capability(name, True, "ok", "test")
            for name in ("shell", "git", "worktree", "gh", "gh-auth")
        )
    )


def _json(payload) -> CommandResult:
    text = json.dumps(payload)
    return CommandResult(True, 0, text, stdout=text)


class _Host:
    """GitHub as keel merge and the lookup see it, per pull request."""

    def __init__(
        self, *, heads=None, merge_state=None, evidence_missing=(), merge_ok=True, lookup=None
    ):
        self.lookup = lookup
        self.heads = heads or {10: "sha-10", 11: "sha-11"}
        self.merge_state = merge_state or {}
        self.evidence_missing = set(evidence_missing)
        self.merge_ok = merge_ok
        self.cli_calls: list[list[str]] = []
        self.merges: list[tuple[int, str | None, str]] = []

    def run_argv(self, argv, cwd=None, **_kw):
        self.cli_calls.append(list(argv))
        if self.lookup is not None:
            return self.lookup
        if argv[:3] == ["gh", "pr", "view"]:
            number = int(argv[3])
            return _json(
                {
                    "number": number,
                    "state": "OPEN",
                    "headRefName": f"swarm/swarm-e2e/cluster-1-{number + 91}",
                    "baseRefName": "main",
                }
            )
        if argv[:3] == ["gh", "pr", "list"]:
            number = int(argv[4].rsplit("-", 1)[1]) - 91
            return _json([{"number": number, "state": "OPEN"}])
        return CommandResult(False, 1, "unexpected command")

    def snapshot(self, pr, *, cwd=None, _run=None):
        return _json(
            {
                "headRefOid": self.heads[int(pr)],
                "mergeStateStatus": self.merge_state.get(int(pr), "CLEAN"),
                "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            }
        )

    def evidence(self, args, config, *, phase):
        missing = ["review-verdict-1"] if args.pr in self.evidence_missing else []
        return {
            "head_sha": self.heads[args.pr],
            "enforced": True,
            "verification": {"status": "fail" if missing else "pass", "missing": missing},
        }

    def merge_pr(self, pr, *, method, head_sha, cwd=None):
        self.merges.append((int(pr), head_sha, method))
        return CommandResult(
            self.merge_ok, 0 if self.merge_ok else 1, "merged" if self.merge_ok else "409"
        )


class SwarmLandMergesThroughKeelMerge(unittest.TestCase):
    """#1287, the owner's decision: every cluster's pull request goes through `keel merge`'s
    own code — window, lock, CI, evidence, gates-pass, head pin — with nothing else faked
    but GitHub, the clock and the ledger."""

    SWARM = "swarm-e2e"
    CONSENT = ("--approve-scope", "filesystem,git,github", "--operator", "tester")

    def _run(self, host: _Host, *extra, window_open=True, record=True, gates=True):
        plan = _two_cluster_plan(self.SWARM)
        with tempfile.TemporaryDirectory() as tmp:
            save_swarm_plan(plan, root=tmp)
            prs = {"cluster-1-101": 10, "cluster-1-102": 11} if record else {}
            save_swarm_state(
                _state(self.SWARM, prs or {"cluster-1-101": None, "cluster-1-102": None}), tmp
            )
            out, err = io.StringIO(), io.StringIO()
            with (
                patch("keel.cli.runtime.detect", return_value=_capabilities()),
                patch("keel.cli.window.is_merge_open", return_value=window_open),
                patch("keel.cli.run_argv", side_effect=host.run_argv),
                patch("keel.cli.github.pr_merge_snapshot", side_effect=host.snapshot),
                patch("keel.cli._verify_merge_evidence", side_effect=host.evidence),
                patch("keel.cli.ledger.read_records", return_value=[]),
                patch(
                    "keel.cli.ledger.gates_pass_for_head",
                    return_value=(gates, {"run_id": "RUN-1"} if gates else None),
                ),
                patch("keel.cli._merge_drift_report", return_value={"status": "clean"}),
                patch("keel.cli.github.merge_pr", side_effect=host.merge_pr),
                patch("keel.cli.github.rest_merge_pr", side_effect=AssertionError("REST")),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                code = main(
                    [
                        "swarm-land",
                        KEEL_YAML,
                        "--root",
                        tmp,
                        "--swarm-id",
                        self.SWARM,
                        "--json",
                        *extra,
                    ]
                )
            state = load_swarm_state(self.SWARM, root=tmp)
        for own in ("keel.merge.v1", "keel merge — "):
            self.assertNotIn(own, out.getvalue(), "keel merge printed its own report")
        return code, json.loads(out.getvalue()), err.getvalue(), state

    def test_each_pull_request_is_merged_by_keel_merge_pinned_to_its_head(self):
        host = _Host()
        code, payload, err, state = self._run(host, "--live", *self.CONSENT)
        self.assertEqual(code, 0, err)
        self.assertEqual(host.merges, [(10, "sha-10", "squash"), (11, "sha-11", "squash")])
        self.assertEqual(payload["landed_clusters"], ["cluster-1-101", "cluster-1-102"])
        self.assertEqual(payload["pull_requests"], {"cluster-1-101": 10, "cluster-1-102": 11})
        self.assertEqual([w.status for w in state.workers], ["merged", "merged"])
        self.assertEqual(state.workers[0].details, "PR #10: merged")

    def test_the_recorded_pull_request_is_used_and_the_list_is_the_fallback(self):
        host = _Host()
        self._run(host, "--live", *self.CONSENT)
        self.assertEqual(
            [c[:4] for c in host.cli_calls],
            [["gh", "pr", "view", "10"], ["gh", "pr", "view", "11"]],
        )
        fallback = _Host()
        code, _, err, _ = self._run(fallback, "--live", *self.CONSENT, record=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(
            [c[:5] for c in fallback.cli_calls],
            [
                ["gh", "pr", "list", "--head", "swarm/swarm-e2e/cluster-1-101"],
                ["gh", "pr", "list", "--head", "swarm/swarm-e2e/cluster-1-102"],
            ],
        )
        self.assertEqual([pr for pr, _, _ in fallback.merges], [10, 11])

    def test_a_pull_request_without_evidence_is_held_and_the_other_lands(self):
        host = _Host(evidence_missing={10})
        code, payload, _, state = self._run(host, "--live", *self.CONSENT)
        self.assertEqual(code, 1)
        self.assertEqual([pr for pr, _, _ in host.merges], [11])
        self.assertEqual(
            payload["held_clusters"],
            [["cluster-1-101", "PR #10: keel merge: missing evidence: review-verdict-1"]],
        )
        self.assertEqual(payload["landed_clusters"], ["cluster-1-102"])
        self.assertEqual([w.status for w in state.workers], ["held", "merged"])

    def test_outside_the_merge_window_every_cluster_is_held(self):
        host = _Host()
        code, payload, _, _ = self._run(host, "--live", *self.CONSENT, window_open=False)
        self.assertEqual(code, 1)
        self.assertEqual(host.merges, [])
        self.assertEqual(
            [reason for _, reason in payload["held_clusters"]],
            [
                "PR #10: keel merge: merge window is closed",
                "PR #11: keel merge: merge window is closed",
            ],
        )

    def test_a_dirty_pull_request_is_reported_and_the_wave_goes_on(self):
        host = _Host(merge_state={10: "DIRTY"})
        code, payload, _, _ = self._run(host, "--live", *self.CONSENT)
        self.assertEqual(code, 1)
        self.assertEqual(
            payload["held_clusters"],
            [["cluster-1-101", "PR #10: keel merge: PR merge state is DIRTY"]],
        )
        self.assertEqual([pr for pr, _, _ in host.merges], [11])

    def test_no_gates_pass_for_the_head_holds(self):
        host = _Host()
        code, payload, _, _ = self._run(host, "--live", *self.CONSENT, gates=False)
        self.assertEqual(code, 1)
        self.assertEqual(host.merges, [])
        self.assertIn(
            "no gates-pass recorded for the current head sha-10", payload["held_clusters"][0][1]
        )

    def test_a_failed_merge_call_is_a_failed_cluster(self):
        host = _Host(merge_ok=False)
        code, payload, _, state = self._run(host, "--live", *self.CONSENT)
        self.assertEqual(code, 1)
        self.assertEqual(payload["failed_clusters"], ["cluster-1-101", "cluster-1-102"])
        self.assertEqual(state.workers[0].status, "failed")
        self.assertIn("gh merge failed: 409", state.workers[0].details)

    def test_a_dry_run_merges_nothing_and_reports_what_would_land(self):
        host = _Host(evidence_missing={11})
        code, payload, _, state = self._run(host, *self.CONSENT)
        self.assertEqual(code, 1, "a preview with a held cluster is not a pass")
        self.assertEqual(host.merges, [])
        self.assertEqual(payload["landed_clusters"], ["cluster-1-101"])
        self.assertEqual(
            payload["held_clusters"],
            [["cluster-1-102", "PR #11: keel merge: missing evidence: review-verdict-1"]],
        )
        self.assertEqual([w.status for w in state.workers], ["passed", "passed"])

    def test_without_consent_keel_merge_refuses_every_cluster(self):
        host = _Host()
        with patch.dict("os.environ", {}, clear=False) as env:
            for name in ("KEEL_APPROVE_SCOPE", "KEEL_OPERATOR", "KEEL_CONSENT_MODE"):
                env.pop(name, None)
            code, payload, err, _ = self._run(host, "--live")
        self.assertEqual(code, 1)
        self.assertEqual(host.merges, [])
        self.assertEqual(len(payload["held_clusters"]), 2)
        self.assertIn("refused before reaching the pull request", payload["held_clusters"][0][1])

    def test_a_held_merge_lock_holds_the_cluster(self):
        host = _Host()
        real_claim = cli_mod.lock.claim_resource
        owners: list[str] = []

        def contended(root, resource, *, owner):
            if resource != "merge":
                return real_claim(root, resource, owner=owner)
            owners.append(owner)
            if len(owners) > 1:
                return real_claim(root, resource, owner=owner)
            # Another merge holds the lock for exactly the first cluster's attempt.
            real_claim(root, resource, owner="someone-else")
            denied = real_claim(root, resource, owner=owner)
            cli_mod.lock.release_resource(root, resource, owner="someone-else")
            return denied

        with patch("keel.cli.lock.claim_resource", side_effect=contended):
            code, payload, _, _ = self._run(host, "--live", *self.CONSENT)
        self.assertEqual(code, 1)
        self.assertEqual(
            owners,
            [f"swarm-land-{self.SWARM}-cluster-1-101", f"swarm-land-{self.SWARM}-cluster-1-102"],
        )
        self.assertEqual(
            payload["held_clusters"],
            [["cluster-1-101", "PR #10: keel merge: resource lock is already held"]],
        )
        self.assertEqual([pr for pr, _, _ in host.merges], [11])

    def test_the_review_evidence_knob_no_longer_skips_anything(self):
        host = _Host(evidence_missing={10, 11})
        config = cli_mod.cfg.load_config(KEEL_YAML)
        off = replace(config, knobs=replace(config.knobs, swarm_review_evidence=False))
        with patch.object(cli_mod.cfg, "load_config", return_value=off):
            code, payload, err, _ = self._run(host, "--live", *self.CONSENT)
        self.assertEqual(code, 1)
        self.assertEqual(host.merges, [])
        self.assertIn("knobs.swarm_review_evidence: false has no effect", err)
        self.assertEqual(len(payload["held_clusters"]), 2)

    def test_a_lookup_that_fails_or_answers_junk_holds_every_cluster(self):
        for answer, reason in (
            (CommandResult(False, 1, "HTTP 502"), "PR lookup failed: HTTP 502"),
            (CommandResult(False, 4, ""), "PR lookup failed: exit 4"),
            (CommandResult(True, 0, "{", stdout="{"), "PR lookup returned invalid JSON"),
        ):
            host = _Host(lookup=answer)
            with self.subTest(reason=reason):
                code, payload, _, _ = self._run(host, "--live", *self.CONSENT)
                self.assertEqual(code, 1)
                self.assertEqual(host.merges, [])
                self.assertEqual([r for _, r in payload["held_clusters"]], [reason, reason])

    def test_the_consent_and_transport_flags_reach_keel_merge_as_its_own_argv(self):
        seen = []

        def fake_merge(merge_args):
            seen.append(merge_args)
            merge_args.merge_sink.append({"reason": "dry-run: merge not performed"})
            return 0

        with patch("keel.cli._cmd_merge", side_effect=fake_merge):
            code, _, err, _ = self._run(
                _Host(), *self.CONSENT, "--consent-mode", "explicit", "--transport", "rest"
            )
        self.assertEqual(code, 0, err)
        first = seen[0]
        self.assertEqual(
            (first.pr, first.transport, first.consent_mode, first.operator, first.dry_run),
            (10, "rest", "explicit", "tester", True),
        )
        self.assertEqual(first.approve_scope, [self.CONSENT[1]])
        self.assertEqual(
            (first.method, first.owner), ("squash", "swarm-land-swarm-e2e-cluster-1-101")
        )

    def test_without_a_persisted_plan_or_issues_there_is_nothing_to_land(self):
        err = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as tmp,
            redirect_stderr(err),
            redirect_stdout(io.StringIO()),
        ):
            code = main(["swarm-land", KEEL_YAML, "--root", tmp, "--swarm-id", "nothing"])
        self.assertEqual(code, 1)
        self.assertIn("swarm-land needs the wave's issues", err.getvalue())


class SwarmLandRefusesADependentWave(unittest.TestCase):
    """The #1276 refusal end to end: exit 1, the reason in ``--json``, and neither a
    lookup nor a merge."""

    def _main(self, tmpdir: str, *extra: str):
        out = io.StringIO()
        with (
            patch("keel.cli.run_argv", side_effect=AssertionError("looked up a PR")),
            patch("keel.cli._cmd_merge", side_effect=AssertionError("merged")),
            patch("keel.swarm_landing.default_runner", side_effect=_Checkout()),
            redirect_stdout(out),
            redirect_stderr(io.StringIO()),
        ):
            code = main(
                [
                    "swarm-land",
                    KEEL_YAML,
                    "--root",
                    tmpdir,
                    "--issues",
                    "1,2",
                    "--issue-scope",
                    "1=src/a.py",
                    "--issue-scope",
                    "2=src/a.py",
                    "--swarm-id",
                    "swarm-cli-dep",
                    "--json",
                    *extra,
                ]
            )
        return code, json.loads(out.getvalue())

    def test_a_dependent_wave_exits_1_live_and_dry(self):
        for extra in (("--live",), ()):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                code, payload = self._main(tmp, "--wave", "2", *extra)
                self.assertEqual(code, 1)
                self.assertEqual((payload["mode"], payload["status"]), ("refused", "failed"))
                self.assertIn(
                    "wave 2 depends on issues landed by an earlier wave (#1)", payload["refused"]
                )


class SwarmLandCLI(unittest.TestCase):
    def test_a_missing_or_invalid_config_is_refused(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(["swarm-land", "nonexistent.yaml"]), 1)
        self.assertIn("no such config", err.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "p.yaml"
            bad.write_text("invalid_root_key: true\n", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["swarm-land", str(bad)]), 1)

    def test_the_text_report_is_rendered_without_json(self):
        result = SwarmLandingResult(
            swarm_id="s",
            wave_index=1,
            mode="direct_batch",
            landed_clusters=("c",),
            failed_clusters=(),
            status="success",
        )
        out = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("keel.swarm_landing.land_wave_clusters", return_value=result),
            redirect_stdout(out),
            redirect_stderr(io.StringIO()),
        ):
            save_swarm_plan(_two_cluster_plan("s"), root=tmp)
            code = main(["swarm-land", KEEL_YAML, "--root", tmp, "--swarm-id", "s"])
        self.assertEqual(code, 0)
        self.assertIn("keel swarm land — s (wave 1)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
