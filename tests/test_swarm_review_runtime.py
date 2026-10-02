"""Unit tests for swarm-review's runtime and its CLI (#1423): each cluster pull request is
reviewed by its own seats, read-only, in a checkout of the head removed afterwards, and their
approvals posted through keel review pinned to that head — with every outside call faked."""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from keel import cli as cli_mod
from keel import evidence, runtime, ship
from keel import swarm_review as sr
from keel import swarm_review_runtime as rt
from keel.cli import main
from keel.runner import CommandResult
from keel.swarm import (
    Difficulty,
    SwarmCluster,
    SwarmPlan,
    SwarmPlanError,
    SwarmRunState,
    SwarmWave,
    SwarmWorkerStatus,
    load_swarm_state,
    save_swarm_plan,
    save_swarm_state,
)
from keel.swarm_landing import PullRequestLookup

REPO_ROOT = Path(__file__).resolve().parent.parent
KEEL_YAML = str(REPO_ROOT / "projects" / "keel.yaml")
SWARM = "swarm-rv"
HEAD = "a" * 40
SCOPE = "Checked `src/keel/swarm_review.py` and `swarm_review.read_seat_verdict()`."
CONSENT = ("--approve-scope", "filesystem,git,github", "--operator", "tester")


def _seat(provider, slot, kind="provider"):
    return {
        "provider": provider,
        "name": provider,
        "kind": kind,
        "model": None,
        "effort": None,
        "source": "team.review",
        "slot": slot,
    }


def _assignment(*providers):
    slots = ("A", "C", "B")
    return {
        "implementer": _seat("agy", None),
        "reviewers": [_seat(p, slots[i]) for i, p in enumerate(providers)],
        "review_panel": "reviewers",
    }


DIFFICULTY = Difficulty(score=1, band="easy", tier=2, file_count=1, dependency_depth=0)


def _plan(*clusters):
    clusters = clusters or (
        SwarmCluster(
            "cluster-1-7",
            (7,),
            "core",
            ("src/a.py",),
            difficulty=DIFFICULTY,
            assignment=_assignment("claude", "codex"),
        ),
    )
    wave = SwarmWave(1, "orthogonal_parallel", True, tuple(clusters))
    return SwarmPlan(SWARM, len(clusters), (wave,))


def _save_state(root, prs):
    workers = tuple(
        SwarmWorkerStatus(cluster_id=cid, issue=7, role="core", pull_request=number)
        for cid, number in prs.items()
    )
    save_swarm_state(SwarmRunState(SWARM, len(workers), workers=workers), root=root)


def _answer(verdict="APPROVE", scope=SCOPE, findings=()):
    body = {"verdict": verdict, "scope": scope, "findings": list(findings), "testing": "ok"}
    return {"ok": True, "text": f"Reviewed.\n{json.dumps(body)}\n"}


def _result(ok=True, output=""):
    return CommandResult(ok, 0 if ok else 1, output, stdout=output)


class _Git:
    """git as swarm-review meets it: a head that may need fetching, worktrees made and
    removed on disk, and a config the tamper check reads."""

    def __init__(self, *, have=True, fetch_brings=True, add_ok=True, snapshot_ok=True):
        self.have = have
        self.fetch_brings = fetch_brings
        self.add_ok = add_ok
        self.snapshot_ok = snapshot_ok
        self.config = "local\tfile:.git/config\tcore.bare=false"
        self.commands: list[list[str]] = []
        self.lock = threading.Lock()

    def __call__(self, cmd, cwd):
        with self.lock:
            self.commands.append(list(cmd))
        if cmd[:3] == ["git", "cat-file", "-e"]:
            return _result(self.have)
        if cmd[:2] == ["git", "fetch"]:
            self.have = self.fetch_brings
            return _result(self.fetch_brings, "" if self.fetch_brings else "no route to host")
        if cmd[:3] == ["git", "worktree", "add"]:
            if self.add_ok:
                Path(cmd[4]).mkdir(parents=True)
            return _result(self.add_ok, "" if self.add_ok else "already exists")
        if cmd[:3] == ["git", "worktree", "remove"]:
            shutil.rmtree(cmd[-1], ignore_errors=True)
            return _result()
        if cmd[:2] == ["git", "rev-parse"]:
            return _result(self.snapshot_ok, f"{cwd}/.git\n{cwd}/.git\nhooks")
        if cmd[:2] == ["git", "config"]:
            return _result(True, self.config)
        return _result(False, "unexpected")


class _IO:
    """The seams: the lookup, the reads, the seat and the post."""

    def __init__(
        self,
        git=None,
        *,
        lookup=None,
        facts=None,
        read_error=None,
        head=HEAD,
        head_error=None,
        answers=None,
        post_reason="",
        tamper=None,
    ):
        self.git = git or _Git()
        self.lookup = lookup or {}
        self.facts = facts or sr.PullRequestFacts(
            HEAD, "Fix it", 2, {"reviewers": {"count": 2}}, "--- a/x\n+++ b/x\n"
        )
        self.read_error = read_error
        self.head = head
        self.head_error = head_error
        self.answers = answers or {}
        self.post_reason = post_reason
        self.tamper = tamper
        self.found: list[tuple[str, int | None]] = []
        self.reads: list[tuple[int, bool]] = []
        self.seats: list[dict] = []
        self.posts: list[tuple[int, str, list, str]] = []

    def find(self, branch, recorded):
        self.found.append((branch, recorded))
        if isinstance(self.lookup, Exception):
            raise self.lookup
        return self.lookup.get(branch, PullRequestLookup(recorded or 9))

    def read_pr(self, number, with_diff):
        self.reads.append((number, with_diff))
        if self.read_error:
            raise ValueError(self.read_error)
        return self.facts

    def read_head(self, number):
        if self.head_error:
            raise ValueError(self.head_error)
        return self.head

    def read_issue(self, number):
        return (f"Issue {number}", f"Body of {number}")

    def post(self, number, head, items, run_id):
        self.posts.append((number, head, items, run_id))
        return self.post_reason

    def run_seat(self, plan, env):
        brief = Path(plan.prompt_path).read_text(encoding="utf-8")
        self.seats.append(
            {
                "provider": plan.provider,
                "role": plan.role,
                "cwd_exists": Path(plan.cwd).is_dir(),
                "brief": brief,
                "env": env,
            }
        )
        slot = Path(plan.cwd).name.rsplit("-", 1)[1]
        answer = self.answers.get(slot, self.answers.get(plan.provider, _answer()))
        if isinstance(answer, Exception):
            raise answer
        if self.tamper == plan.provider:
            self.git.config += "\nlocal\tfile:.git/config\tcore.hooksPath=/evil"
        return answer

    def review_io(self, *, runner=True):
        return rt.ReviewIO(
            find_pull_request=self.find,
            read_pull_request=self.read_pr,
            read_head=self.read_head,
            read_issue=self.read_issue,
            post_verdicts=self.post,
            run_seat=self.run_seat,
            runner=self.git if runner else None,
        )


class _Root(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        _save_state(self.root, {"cluster-1-7": 9})

    def tearDown(self):
        self._tmp.cleanup()

    def review(self, fake, plan=None, *, dry_run=False, wave=1, max_workers=3, runner=True):
        return rt.review_wave_clusters(
            plan or _plan(),
            wave,
            dry_run=dry_run,
            io=fake.review_io(runner=runner),
            root=self.root,
            host_agent="claude",
            seat_timeout=77,
            max_workers=max_workers,
        )

    def checkouts(self):
        base = self.root / ".keel" / "worktrees"
        return sorted(p.name for p in base.rglob("*")) if base.exists() else []


class ALiveReviewPostsTheApprovals(_Root):
    def test_each_seat_reviews_read_only_in_its_own_checkout_and_the_approvals_post(self):
        fake = _IO()
        with patch.dict(os.environ, {"GH_TOKEN": "secret", "KEEL_APPROVE_SCOPE": "github"}):
            result = self.review(fake)
        (cluster,) = result.clusters
        self.assertEqual(result.status, "success", cluster.reason)
        self.assertEqual(cluster.status, sr.POSTED)
        self.assertIn(f"2 verdict(s) posted on PR #9, pinned to {HEAD}", cluster.reason)
        self.assertEqual(fake.found, [(f"swarm/{SWARM}/cluster-1-7", 9)])
        self.assertEqual(fake.reads, [(9, True)])
        self.assertEqual(sorted(s["provider"] for s in fake.seats), ["claude", "codex"])
        for seat in fake.seats:
            self.assertEqual(seat["role"], "review")
            self.assertTrue(seat["cwd_exists"])
            self.assertIn(f"at head {HEAD}", seat["brief"])
            self.assertIn("## Issue #7: Issue 7\n\nBody of 7", seat["brief"])
            self.assertIn("+++ b/x", seat["brief"])
            env = seat["env"]
            self.assertNotIn("GH_TOKEN", env)
            self.assertNotIn("KEEL_APPROVE_SCOPE", env)
            self.assertIn("no-gh-login", env["GH_CONFIG_DIR"])
            self.assertEqual(env["GIT_CONFIG_VALUE_0"], "")
        ((number, head, items, run_id),) = fake.posts
        self.assertEqual((number, head, run_id), (9, HEAD, f"{SWARM}/cluster-1-7"))
        self.assertEqual(
            [(i["reviewer"], i["verdict"], i["vendor"]) for i in items],
            [
                ("swarm-review-a-claude", "APPROVE", "claude"),
                ("swarm-review-c-codex", "APPROVE", "codex"),
            ],
        )
        adds = [c for c in fake.git.commands if c[:3] == ["git", "worktree", "add"]]
        self.assertEqual(
            sorted(Path(c[4]).name for c in adds),
            ["cluster-1-7.review-A", "cluster-1-7.review-C"],
        )
        self.assertTrue(all(c[3] == "--detach" and c[5] == HEAD for c in adds))
        self.assertEqual(self.checkouts(), [], "every checkout is removed")
        self.assertFalse((self.root / ".keel" / "worktrees").exists())
        brief = self.root / ".keel" / "state" / "swarm" / SWARM / "cluster-1-7.review-A.brief.md"
        self.assertTrue(brief.is_file())
        self.assertEqual([c for c in fake.git.commands if c[:2] == ["git", "fetch"]], [])

    def test_a_head_pushed_since_is_fetched_from_the_pull_request(self):
        fake = _IO(_Git(have=False))
        (cluster,) = self.review(fake).clusters
        self.assertEqual(cluster.status, sr.POSTED)
        self.assertIn(
            ["git", "fetch", "--no-tags", "origin", "refs/pull/9/head"], fake.git.commands
        )

    def test_a_head_that_cannot_be_fetched_fails_the_cluster_and_runs_nothing(self):
        fake = _IO(_Git(have=False, fetch_brings=False))
        (cluster,) = self.review(fake).clusters
        self.assertEqual(cluster.status, sr.ERROR)
        self.assertIn("could not be fetched (no route to host)", cluster.reason)
        self.assertEqual((fake.seats, fake.posts), ([], []))

    def test_the_default_runner_runs_keels_git_when_none_is_injected(self):
        fake = _IO()
        with patch("keel.swarm_review_runtime.default_runner", fake.git):
            (cluster,) = self.review(fake, runner=False).clusters
        self.assertEqual(cluster.status, sr.POSTED, cluster.reason)


class WhatParsedIsPostedAndAnythingElseIsNot(_Root):
    def test_a_change_request_is_posted_beside_the_approval_with_its_findings(self):
        finding = {"severity": "major", "message": "loses data", "path": "a.py"}
        fake = _IO(answers={"codex": _answer("REQUEST_CHANGES", findings=[finding])})
        result = self.review(fake)
        (cluster,) = result.clusters
        self.assertEqual(cluster.status, sr.POSTED_CHANGES_REQUESTED)
        self.assertEqual(result.status, "failed")
        self.assertIn("seat(s) C request changes", cluster.reason)
        self.assertIn("review-verdict-not-approved", cluster.reason)
        ((_, head, items, _),) = fake.posts
        self.assertEqual(head, HEAD)
        self.assertEqual([i["verdict"] for i in items], ["APPROVE", "REQUEST_CHANGES"])
        self.assertEqual(items[1]["findings"][0]["message"], "loses data")
        self.assertEqual(self.checkouts(), [])

    def test_a_failed_seat_posts_nothing_and_a_lone_rejection_still_posts(self):
        fake = _IO(
            answers={
                "claude": {"ok": False, "error_code": "timeout", "error": "slow"},
                "codex": _answer("REQUEST_CHANGES"),
            }
        )
        (cluster,) = self.review(fake).clusters
        self.assertEqual(cluster.status, sr.POSTED_CHANGES_REQUESTED)
        ((_, _, items, _),) = fake.posts
        self.assertEqual([i["reviewer"] for i in items], ["swarm-review-c-codex"])

    def test_a_seat_whose_output_does_not_parse_is_failed_never_an_approval(self):
        fake = _IO(answers={"codex": {"ok": True, "text": "LGTM, ship it."}})
        (cluster,) = self.review(fake).clusters
        self.assertEqual(cluster.status, sr.HELD)
        self.assertIn("seat(s) C did not return a readable verdict", cluster.reason)
        self.assertEqual(
            {v.slot: v.outcome for v in cluster.verdicts}, {"A": "APPROVE", "C": "failed"}
        )
        self.assertEqual(fake.posts, [])

    def _three_seats(self):
        cluster = SwarmCluster(
            "cluster-1-7", (7,), "core", ("a",), assignment=_assignment("claude", "codex", "claude")
        )
        return _plan(cluster)

    def test_two_approvals_beside_an_unreadable_seat_post_nothing(self):
        fake = _IO(answers={"B": {"ok": True, "text": "no verdict here"}})
        (cluster,) = self.review(fake, self._three_seats()).clusters
        self.assertEqual(cluster.status, sr.HELD)
        self.assertIn("seat(s) B did not return a readable verdict", cluster.reason)
        self.assertIn("rerun swarm-review", cluster.reason)
        self.assertEqual(fake.posts, [])

    def test_beside_an_unreadable_seat_only_the_rejection_is_posted(self):
        fake = _IO(
            answers={
                "B": {"ok": False, "error_code": "timeout", "error": "slow"},
                "C": _answer("REQUEST_CHANGES", scope=""),
            }
        )
        (cluster,) = self.review(fake, self._three_seats()).clusters
        self.assertEqual(cluster.status, sr.POSTED_CHANGES_REQUESTED)
        ((_, _, items, _),) = fake.posts
        self.assertEqual(
            [(i["reviewer"], i["verdict"]) for i in items],
            [("swarm-review-c-codex", "REQUEST_CHANGES")],
        )
        self.assertIn("did not name what it checked", items[0]["scope"])

    def test_a_moved_head_posts_nothing(self):
        fake = _IO(head="b" * 40)
        (cluster,) = self.review(fake).clusters
        self.assertEqual(cluster.status, sr.HELD)
        self.assertIn(f"moved from {HEAD} to {'b' * 40}", cluster.reason)
        self.assertEqual(fake.posts, [])

    def test_an_unreadable_head_posts_nothing(self):
        fake = _IO(head_error="HTTP 502")
        (cluster,) = self.review(fake).clusters
        self.assertEqual(cluster.status, sr.HELD)
        self.assertIn("could not be re-read (HTTP 502)", cluster.reason)
        self.assertEqual(fake.posts, [])

    def test_a_post_keel_review_refuses_holds_the_cluster(self):
        fake = _IO(post_reason="tier requires at least 3")
        (cluster,) = self.review(fake).clusters
        self.assertEqual(cluster.status, sr.HELD)
        self.assertEqual(cluster.reason, "keel review did not post: tier requires at least 3")

    def test_a_seat_that_changed_the_git_setup_holds_the_cluster(self):
        fake = _IO(tamper="codex")
        (cluster,) = self.review(fake, max_workers=1).clusters
        self.assertEqual(cluster.status, sr.HELD)
        self.assertIn("changed the repository's git setup", cluster.reason)
        tampered = [v for v in cluster.verdicts if v.tampered]
        self.assertEqual([v.slot for v in tampered], ["C"])
        self.assertIn("core.hooksPath", tampered[0].reason)
        self.assertEqual(fake.posts, [])


class CheckoutsAreRemovedOnSuccessAndFailure(_Root):
    def test_a_seat_that_raises_fails_and_its_checkout_is_removed(self):
        fake = _IO(answers={"codex": RuntimeError("kaboom")})
        (cluster,) = self.review(fake).clusters
        verdicts = {v.slot: v for v in cluster.verdicts}
        self.assertEqual(verdicts["C"].outcome, sr.FAILED)
        self.assertIn("the seat raised RuntimeError: kaboom", verdicts["C"].reason)
        self.assertEqual(cluster.status, sr.HELD)
        self.assertEqual(self.checkouts(), [])

    def test_a_checkout_that_cannot_be_made_fails_the_seat(self):
        fake = _IO(_Git(add_ok=False))
        (cluster,) = self.review(fake).clusters
        self.assertTrue(all(v.outcome == sr.FAILED for v in cluster.verdicts))
        self.assertIn("could not be made", cluster.verdicts[0].reason)
        self.assertIn("swarm-status <project.yaml> --clean", cluster.verdicts[0].reason)
        self.assertEqual(fake.seats, [])
        self.assertFalse(any(c[:3] == ["git", "worktree", "remove"] for c in fake.git.commands))

    def test_an_unreadable_git_setup_fails_the_seat_and_removes_its_checkout(self):
        fake = _IO(_Git(snapshot_ok=False))
        (cluster,) = self.review(fake).clusters
        self.assertIn("could not be read", cluster.verdicts[0].reason)
        self.assertEqual(fake.seats, [])
        self.assertEqual(self.checkouts(), [])

    def test_a_removal_that_fails_is_a_warning(self):
        fake = _IO()
        with patch("keel.swarm_runtime.remove_swarm_worktree", return_value=False):
            (cluster,) = self.review(fake).clusters
        self.assertEqual(cluster.status, sr.POSTED)
        self.assertEqual(len(cluster.warnings), 2)
        self.assertIn("could not be removed", cluster.warnings[0])


class ADryRunSpawnsAndPostsNothing(_Root):
    def test_it_reads_the_pull_request_and_plans_the_seats(self):
        fake = _IO()
        result = self.review(fake, dry_run=True)
        (cluster,) = result.clusters
        self.assertEqual(result.status, "success")
        self.assertEqual(cluster.status, sr.PLANNED)
        self.assertIn(f"reviews PR #9 at {HEAD} with 2 seat(s)", cluster.reason)
        self.assertEqual(fake.reads, [(9, False)])
        self.assertEqual((fake.seats, fake.posts, fake.git.commands), ([], [], []))
        self.assertEqual([s.plan.timeout for s in cluster.seats], [77, 77])


class AClusterIsSkippedOrRefusedWithItsReason(_Root):
    def test_no_pull_request_skips_and_a_merged_one_is_done(self):
        branch = f"swarm/{SWARM}/cluster-1-7"
        fake = _IO(lookup={branch: PullRequestLookup(None, "no open pull request")})
        (cluster,) = self.review(fake).clusters
        self.assertEqual((cluster.status, cluster.reason), (sr.SKIPPED, "no open pull request"))
        fake = _IO(lookup={branch: PullRequestLookup(None, "PR #9 merged", merged=True)})
        result = self.review(fake)
        self.assertEqual(result.clusters[0].status, sr.MERGED)
        self.assertEqual(result.status, "success")
        self.assertEqual(fake.reads, [])

    def test_too_few_seats_for_the_tier_refuses_before_anything_runs(self):
        cluster = SwarmCluster(
            "cluster-1-7", (7,), "core", ("a",), assignment=_assignment("claude", "agy")
        )
        for dry_run in (True, False):
            fake = _IO()
            (review,) = self.review(fake, _plan(cluster), dry_run=dry_run).clusters
            self.assertEqual(review.status, sr.REFUSED)
            self.assertIn("requires at least 2", review.reason)
            self.assertEqual((fake.seats, fake.posts, fake.git.commands), ([], [], []))

    def test_the_distinct_vendor_rule_refuses_before_anything_runs(self):
        cluster = SwarmCluster(
            "cluster-1-7", (7,), "core", ("a",), assignment=_assignment("claude", "claude")
        )
        facts = sr.PullRequestFacts(
            HEAD, "t", 2, {"reviewers": {"count": 2, "require_distinct_vendors": True}}
        )
        fake = _IO(facts=facts)
        (review,) = self.review(fake, _plan(cluster)).clusters
        self.assertEqual(review.status, sr.REFUSED)
        self.assertIn("require_distinct_vendors", review.reason)
        self.assertEqual(fake.seats, [])

    def test_a_cluster_with_no_assignment_has_no_seat(self):
        cluster = SwarmCluster("cluster-1-7", (7,), "core", ("a",))
        (review,) = self.review(_IO(), _plan(cluster), dry_run=True).clusters
        self.assertEqual(review.status, sr.REFUSED)

    def test_an_unreadable_pull_request_fails_the_cluster(self):
        fake = _IO(read_error="HTTP 404")
        (cluster,) = self.review(fake).clusters
        self.assertEqual(
            (cluster.status, cluster.reason), (sr.ERROR, "PR #9 could not be read: HTTP 404")
        )

    def test_a_cluster_that_raises_fails_alone_and_the_wave_goes_on(self):
        other = SwarmCluster(
            "cluster-1-8", (8,), "core", ("b",), assignment=_assignment("claude", "codex")
        )
        fake = _IO(lookup=RuntimeError("gh exploded"))
        result = self.review(fake, _plan(_plan().waves[0].clusters[0], other), dry_run=True)
        self.assertEqual([c.status for c in result.clusters], [sr.ERROR, sr.ERROR])
        self.assertIn("reviewing it raised RuntimeError: gh exploded", result.clusters[0].reason)

    def test_an_unknown_wave_reviews_nothing(self):
        result = self.review(_IO(), wave=4)
        self.assertEqual((result.clusters, result.status), ((), "failed"))
        self.assertEqual(result.warnings, ("the plan has no wave 4",))

    def test_without_run_state_the_pull_request_is_looked_up_by_branch(self):
        shutil.rmtree(self.root / ".keel")
        fake = _IO()
        self.review(fake, dry_run=True)
        self.assertEqual(fake.found, [(f"swarm/{SWARM}/cluster-1-7", None)])

    def test_the_default_seat_is_keel_delegate_runs_executor(self):
        with patch("keel.swarm_runtime._default_implement", return_value={"ok": True}) as run:
            self.assertEqual(rt._default_seat("plan", {"A": "1"}), {"ok": True})
        run.assert_called_once_with("plan", {"A": "1"})


class TheRunStateRecordsTheReview(_Root):
    """#1440: a live review records each cluster's outcome where `swarm-status` reads it."""

    def _record(self):
        return load_swarm_state(SWARM, root=self.root).workers[0].review

    def test_a_live_review_records_the_cluster_and_swarm_status_shows_it(self):
        with patch("keel.swarm_runtime._now", return_value="2026-10-02T09:30:00+00:00"):
            result = self.review(_IO())
        self.assertEqual(result.warnings, ())
        record = self._record()
        self.assertEqual(record["status"], "posted")
        self.assertEqual(record["head_sha"], HEAD)
        self.assertEqual(record["run_id"], f"{SWARM}/cluster-1-7")
        self.assertEqual(record["reviewed_at"], "2026-10-02T09:30:00+00:00")
        self.assertEqual(
            [(s["slot"], s["vendor"], s["outcome"]) for s in record["seats"]],
            [("A", "claude", "APPROVE"), ("C", "codex", "APPROVE")],
        )
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = main(["swarm-status", KEEL_YAML, "--root", str(self.root), "--swarm-id", SWARM])
        self.assertEqual(code, 0)
        self.assertIn(f"posted 2/2 APPROVE @ {HEAD[:8]}", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            main(
                ["swarm-status", KEEL_YAML, "--root", str(self.root), "--swarm-id", SWARM, "--json"]
            )
        (worker,) = json.loads(out.getvalue())["workers"]
        self.assertEqual(worker["review"], record)

    def test_a_dry_run_records_nothing(self):
        self.review(_IO(), dry_run=True)
        self.assertIsNone(self._record())

    def test_a_second_review_replaces_the_first(self):
        self.review(_IO(answers={"codex": _answer("REQUEST_CHANGES")}))
        self.assertEqual(self._record()["status"], sr.POSTED_CHANGES_REQUESTED)
        self.review(_IO(head="b" * 40))
        record = self._record()
        self.assertEqual(record["status"], sr.HELD)
        self.assertIn("moved from", record["reason"])

    def test_a_refused_or_skipped_cluster_is_recorded_too(self):
        branch = f"swarm/{SWARM}/cluster-1-7"
        self.review(_IO(lookup={branch: PullRequestLookup(None, "PR #9 merged", merged=True)}))
        self.assertEqual(
            self._record(), {**self._record(), "status": "already-merged", "seats": []}
        )

    def test_no_state_or_no_worker_is_a_warning_and_nothing_is_written(self):
        other = SwarmCluster(
            "cluster-1-8", (8,), "core", ("b",), assignment=_assignment("claude", "codex")
        )
        result = self.review(_IO(), _plan(other))
        self.assertEqual(
            result.warnings,
            (f"cluster-1-8: the run state of {SWARM} has no worker cluster-1-8 to record it on",),
        )
        self.assertIsNone(self._record())
        shutil.rmtree(self.root / ".keel")
        result = self.review(_IO())
        self.assertIn("could not be read; the review is not recorded", result.warnings[0])
        self.assertIsNone(load_swarm_state(SWARM, root=self.root))


# --- the CLI -------------------------------------------------------------------------


def _capabilities():
    return runtime.CapabilityReport(
        tuple(
            runtime.Capability(name, True, "ok", "test")
            for name in ("shell", "git", "worktree", "gh", "gh-auth")
        )
    )


class _Host:
    """GitHub, the seats and git as `keel swarm-review` reaches them through the CLI."""

    def __init__(self, *, heads=(HEAD,), diff_ok=True, answers=None, head_now=HEAD, files=None):
        self.heads = list(heads)
        self.diff_ok = diff_ok
        self.answers = answers or {}
        self.head_now = head_now
        self.files = files or ["src/keel/swarm_review.py"]
        self.argv: list[list[str]] = []
        self.posted: list[tuple[int, str]] = []
        self.git = _Git()

    def run_argv(self, argv, cwd=None, **_kw):
        self.argv.append(list(argv))
        if argv[:3] == ["gh", "pr", "view"]:
            reply = {
                "number": int(argv[3]),
                "state": "OPEN",
                "headRefName": f"swarm/{SWARM}/cluster-1-{int(argv[3]) - 2}",
                "baseRefName": "main",
            }
            return _result(True, json.dumps(reply))
        if argv[:3] == ["gh", "pr", "diff"]:
            return _result(self.diff_ok, "+++ b/src/keel/swarm_review.py" if self.diff_ok else "")
        raise AssertionError(f"unexpected argv {argv}")

    def artifacts(self, args, config):
        head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
        return {
            "changed_files": list(self.files),
            "patches": {},
            "head_sha": head,
            "issue": 7,
            "pr_title": "Fix it",
        }

    def gh_json(self, argv, *, cwd):
        return {"head": {"sha": self.head_now}}

    def post_comment(self, owner_repo, number, body, *, cwd=None, _run=None):
        self.posted.append((int(number), body))
        return _result(True, json.dumps({"id": len(self.posted)}))

    def seat(self, plan, env):
        slot = Path(plan.cwd).name.rsplit("-", 1)[1]
        return self.answers.get(slot, self.answers.get(plan.provider, _answer()))


class SwarmReviewCommand(unittest.TestCase):
    def run_cli(self, host, *extra, plan=None, save_plan=True, issue=None):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            if save_plan:
                save_swarm_plan(plan or _plan(), root=tmp)
            _save_state(tmp, {"cluster-1-7": 9, "cluster-1-8": 10})
            for target, kwargs in (
                ("keel.cli.runtime.detect", {"return_value": _capabilities()}),
                ("keel.cli.run_argv", {"side_effect": host.run_argv}),
                ("keel.cli._load_evidence_artifacts", {"side_effect": host.artifacts}),
                ("keel.cli._gh_json", {"side_effect": host.gh_json}),
                ("keel.cli._gh_json_list", {"return_value": []}),
                ("keel.cli.github.post_issue_comment", {"side_effect": host.post_comment}),
                ("keel.cli._read_swarm_issue", {"return_value": issue or (None, "stubbed")}),
                ("keel.swarm_runtime._default_implement", {"side_effect": host.seat}),
                ("keel.swarm_review_runtime.default_runner", {"new": host.git}),
            ):
                stack.enter_context(patch(target, **kwargs))
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["swarm-review", KEEL_YAML, "--root", tmp, "--swarm-id", SWARM, *extra])
        return code, out.getvalue(), err.getvalue()

    def test_a_live_review_posts_each_approval_through_keel_review(self):
        host = _Host()
        code, out, err = self.run_cli(host, "--live", "--json", *CONSENT)
        payload = json.loads(out)
        self.assertEqual(code, 0, (payload, err))
        self.assertEqual(payload["clusters"][0]["status"], "posted")
        self.assertEqual(len(host.posted), 2)
        for number, body in host.posted:
            self.assertEqual(number, 9)
            self.assertIn("keel.review-verdict.v1", body)
            self.assertIn(f"head: {HEAD}", body)
            self.assertIn("Verdict: APPROVE", body)
        self.assertIn(["gh", "pr", "diff", "9"], host.argv)

    def _gate(self, host):
        """The real pre-merge evidence verification over what keel review posted."""
        contract = ship.resolve_review_contract(tier=2)
        comments = [{"body": body, "author_association": "OWNER"} for _, body in host.posted]
        report = evidence.verify(
            contract,
            pr_comments=comments,
            head_sha=HEAD,
            enforced=True,
            phase=evidence.PHASE_PRE_MERGE,
        )
        return report, evidence.refusal_reason(report)

    def test_a_posted_change_request_holds_keel_merge_with_the_named_reason(self):
        finding = {"severity": "major", "message": "drops the lock", "path": "src/keel/lock.py"}
        host = _Host(answers={"codex": _answer("REQUEST_CHANGES", findings=[finding])})
        code, out, err = self.run_cli(host, "--live", "--json", *CONSENT)
        cluster = json.loads(out)["clusters"][0]
        self.assertEqual(code, 1, err)
        self.assertEqual(cluster["status"], "posted-changes-requested")
        self.assertTrue(cluster["posted"])
        bodies = [body for _, body in host.posted]
        self.assertEqual(len(bodies), 2)
        self.assertIn("Verdict: REQUEST_CHANGES", bodies[1])
        self.assertIn("- major: drops the lock", bodies[1])
        self.assertIn(f"head: {HEAD}", bodies[1])
        report, reason = self._gate(host)
        self.assertEqual(report["status"], evidence.STATUS_FAIL)
        self.assertIn(
            f"review-verdict-not-approved: swarm-review-c-codex requests changes at {HEAD}.",
            reason,
        )

    def test_a_malformed_rejection_beside_an_unreadable_seat_is_posted_and_holds(self):
        """Lead review of #1427: one seat approves, one rejects with a thin scope and a finding
        of an unknown severity, one never answers. Only the rejection is posted — below the
        tier's count, which keel review accepts for a rejection — and the gate holds."""
        cluster = SwarmCluster(
            "cluster-1-7",
            (7,),
            "core",
            ("a",),
            difficulty=DIFFICULTY,
            assignment=_assignment("claude", "codex", "claude"),
        )
        bad = {"severity": "high", "message": "drops the lock"}
        host = _Host(
            answers={
                "B": {"ok": False, "error_code": "timeout", "error": "slow"},
                "C": _answer("REQUEST_CHANGES", scope="Bad.", findings=[bad]),
            }
        )
        code, out, err = self.run_cli(host, "--live", "--json", *CONSENT, plan=_plan(cluster))
        result = json.loads(out)["clusters"][0]
        self.assertEqual(code, 1, err)
        self.assertEqual(result["status"], "posted-changes-requested")
        ((number, body),) = host.posted
        self.assertEqual(number, 9)
        self.assertIn("Verdict: REQUEST_CHANGES", body)
        self.assertIn("- major: the seat's finding, as it wrote it:", body)
        report, reason = self._gate(host)
        self.assertIn(
            f"review-verdict-not-approved: swarm-review-c-codex requests changes at {HEAD}.",
            reason,
        )

    def test_the_approvals_alone_pass_the_same_gate(self):
        host = _Host()
        code, _, err = self.run_cli(host, "--live", *CONSENT)
        self.assertEqual(code, 0, err)
        report, _ = self._gate(host)
        self.assertEqual(report["counts"]["review_verdict"], 2)
        self.assertFalse(
            [f for f in report["findings"] if f["id"] == evidence.VERDICT_NOT_APPROVED_FINDING]
        )

    def test_a_head_that_moves_under_keel_review_is_not_posted_to(self):
        host = _Host(heads=(HEAD, "c" * 40))
        code, out, _ = self.run_cli(host, "--live", "--json", *CONSENT)
        cluster = json.loads(out)["clusters"][0]
        self.assertEqual(code, 1)
        self.assertEqual(cluster["status"], "held")
        self.assertIn(
            f"head is {'c' * 40}, not the {HEAD} the verdicts were written for", cluster["reason"]
        )
        self.assertEqual(host.posted, [])

    def test_a_head_that_reads_as_nothing_is_a_moved_head(self):
        host = _Host(head_now=None)
        code, out, _ = self.run_cli(host, "--live", "--json", *CONSENT)
        self.assertEqual(code, 1)
        self.assertIn("unreadable head", json.loads(out)["clusters"][0]["reason"])

    def test_a_silent_keel_review_failure_still_has_a_reason(self):
        host = _Host()
        with patch("keel.cli._cmd_review", return_value=1):
            code, out, _ = self.run_cli(host, "--live", "--json", *CONSENT)
        self.assertEqual(code, 1)
        self.assertIn("keel review exited 1", json.loads(out)["clusters"][0]["reason"])

    def test_a_dry_run_reads_and_plans_and_posts_nothing(self):
        host = _Host()
        code, out, err = self.run_cli(host)
        self.assertEqual(code, 0, err)
        self.assertIn("keel swarm-review — dry-run", out)
        self.assertIn("cluster-1-7: PR #9 @ aaaaaaaaaaaa — planned", out)
        self.assertIn("seat A claude over cli: would review as swarm-review-a-claude", out)
        self.assertEqual(host.posted, [])
        self.assertNotIn(["gh", "pr", "diff", "9"], host.argv)

    def test_a_pull_request_with_no_head_or_no_diff_fails_its_cluster(self):
        code, out, _ = self.run_cli(_Host(heads=("",)), "--json")
        self.assertEqual(code, 1)
        self.assertIn("names no head commit", json.loads(out)["clusters"][0]["reason"])
        code, out, _ = self.run_cli(_Host(diff_ok=False), "--live", "--json", *CONSENT)
        self.assertIn("gh pr diff 9 failed: exit 1", json.loads(out)["clusters"][0]["reason"])

    def test_the_issue_text_reaches_the_brief(self):
        host = _Host()
        briefs = []

        def seat(plan, env):
            briefs.append(Path(plan.prompt_path).read_text(encoding="utf-8"))
            return _answer()

        host.seat = seat
        code, _, err = self.run_cli(
            host, "--live", *CONSENT, issue=(("Real title", "Real body", ()), "")
        )
        self.assertEqual(code, 0, err)
        self.assertIn("## Issue #7: Real title\n\nReal body", briefs[0])

    def test_consent_missing_refuses_before_anything_is_read(self):
        host = _Host()
        code, _, err = self.run_cli(host, "--live")
        self.assertEqual(code, 1)
        self.assertIn("swarm-review --live is refused: operator consent required", err)
        self.assertEqual(host.argv, [])
        code, _, err = self.run_cli(host, "--live", "--consent-mode", "agent")
        self.assertIn("'agent-delegated'", err)
        code, _, err = self.run_cli(
            host, "--live", "--approve-scope", "nonsense", "--operator", "x"
        )
        self.assertEqual(code, 1)
        self.assertIn("swarm-review --live is refused", err)
        self.assertEqual(host.argv, [])

    def test_without_a_persisted_plan_there_is_nothing_to_review(self):
        code, _, err = self.run_cli(_Host(), save_plan=False)
        self.assertEqual(code, 1)
        self.assertIn("reviews the pull requests of the plan swarm-run persisted", err)

    def test_a_plan_keel_cannot_read_is_refused(self):
        with patch(
            "keel.cli.swarm.load_swarm_plan",
            side_effect=SwarmPlanError("bad"),
        ):
            code, _, err = self.run_cli(_Host())
        self.assertEqual(code, 1)
        self.assertIn("swarm-review: refusing the persisted plan: bad", err)

    def test_a_config_that_does_not_load_is_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(["swarm-review", "/nope/project.yaml"]), 1)
        self.assertIn("no such config", err.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "p.yaml"
            bad.write_text("extends: keel\nbase_branch: 3\n", encoding="utf-8")
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(["swarm-review", str(bad)]), 1)

    def test_the_reviewer_count_and_consent_mode_reach_keel_reviews_own_argv(self):
        seen = []
        real = cli_mod._review_tier_and_contract

        def contract(args, config, ctx, panel):
            seen.append((args.reviewers, args.consent_mode, args.live))
            return real(args, config, ctx, panel)

        with patch("keel.cli._review_tier_and_contract", side_effect=contract):
            code, out, err = self.run_cli(
                _Host(),
                "--json",
                "--consent-mode",
                "explicit",
                "--reviewers",
                "2",
                "--review-delegate",
                "claude",
                "--review-delegate",
                "codex",
            )
        cluster = json.loads(out)["clusters"][0]
        self.assertEqual(code, 0, err)
        self.assertEqual((cluster["status"], cluster["required"]), ("planned", 2))
        self.assertEqual([s["provider"] for s in cluster["seats"]], ["claude", "codex"])
        self.assertEqual(seen, [(2, "explicit", False)])

    def test_review_delegate_restaffs_the_bench_and_keeps_the_implementer(self):
        undifficult = SwarmCluster(
            "cluster-1-8", (8,), "core", ("b",), assignment=_assignment("claude", "codex")
        )
        plan = _plan(_plan().waves[0].clusters[0], undifficult)
        code, out, err = self.run_cli(
            _Host(),
            "--json",
            "--review-delegate",
            "codex",
            "--review-delegate",
            "claude",
            plan=plan,
        )
        clusters = json.loads(out)["clusters"]
        restaffed = [s["provider"] for s in clusters[0]["seats"]]
        self.assertEqual(restaffed[:2], ["codex", "claude"], err)
        self.assertEqual([s["provider"] for s in clusters[1]["seats"]], ["claude", "codex"])


if __name__ == "__main__":
    unittest.main()
