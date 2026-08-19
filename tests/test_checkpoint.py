"""Tests for resumable checkpoints."""

import tempfile
import unittest
from pathlib import Path

from keel import checkpoint
from keel import config as cfg


def _config(*, checkpoint_path: str | None = None) -> cfg.ProjectConfig:
    reports = {"checkpoint": checkpoint_path} if checkpoint_path is not None else {}
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.7",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true"),
        policy_pack={"name": "test", "reports": reports},
    )


def _record(**overrides):
    values = {
        "run_id": "RUN-149",
        "command": "ship",
        "current_step": "s6",
        "base_branch": "main",
        "target": "issue #149",
        "issue_queue": [149, 146],
        "active_issue": 149,
        "branch": "feat/issue-149-resume",
        "worktree": "worktrees/issue-149",
        "pull_request": 170,
        "head_sha": "abc123",
        "completed_steps": ["s0", "s1", "s2", "s3", "s4", "s5"],
        "last_gate": "build",
        "last_review": None,
        "last_check": "ci",
        "merge_state": "not-started",
        "capture_state": "not-started",
        "close_state": "not-started",
        "stop_reason": "waiting on CI",
    }
    values.update(overrides)
    return checkpoint.build_checkpoint_record(**values)


class TestCheckpointContract(unittest.TestCase):
    def test_default_contract_and_override(self):
        default = checkpoint.checkpoint_contract_as_dict(_config())
        self.assertEqual(default["schema_version"], checkpoint.CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(default["path"], ".keel/state/checkpoint.json")
        self.assertEqual(default["path_source"], "default")
        self.assertTrue(default["consumer_neutral"])
        self.assertIn("resume", default["resume_command"])
        self.assertIn("work-block", default["write_owner"])
        self.assertEqual(default["steps"][0]["step_id"], "s0")

        config = _config(checkpoint_path="state/resume.json")
        override = checkpoint.checkpoint_contract_as_dict(config)
        self.assertEqual(override["path_source"], "policy_pack.reports.checkpoint")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                checkpoint.resolve_path(directory, config),
                Path(directory).resolve() / "state" / "resume.json",
            )

    def test_resolve_path_rejects_absolute_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(checkpoint.CheckpointError, "must be relative"):
                checkpoint.resolve_path(directory, _config(checkpoint_path="/tmp/resume.json"))

            with self.assertRaisesRegex(checkpoint.CheckpointError, "escapes"):
                checkpoint.resolve_path(directory, _config(checkpoint_path="../resume.json"))

    def test_resolve_path_allows_normalized_path_inside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                checkpoint.resolve_path(directory, _config(checkpoint_path="state/../resume.json")),
                Path(directory).resolve() / "resume.json",
            )


class TestCheckpointRecords(unittest.TestCase):
    def test_schema_stability_and_io(self):
        record = _record()
        self.assertEqual(list(record), [
            "schema_version",
            "record_type",
            "run_id",
            "command",
            "target",
            "queue",
            "position",
            "identifiers",
            "state",
            "resume",
        ])
        self.assertEqual(record["position"]["current_step"], "s6")
        self.assertEqual(record["resume"]["action"], "recheck-ci")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".keel" / "state" / "checkpoint.json"
            self.assertIsNone(checkpoint.read_checkpoint(path))
            checkpoint.write_checkpoint(path, record)
            self.assertEqual(checkpoint.read_checkpoint(path), record)
            self.assertEqual(checkpoint.parse_checkpoint(checkpoint.encode_checkpoint(record)),
                             record)

    def test_write_checkpoint_cleanup_on_replace_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cp.json"
            with unittest.mock.patch("os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    checkpoint.write_checkpoint(path, _record())
            self.assertEqual(list(Path(directory).glob(".*")), [])

    def test_jury_mode_recorded_in_state(self):
        self.assertIsNone(_record()["state"]["jury_mode"])
        record = _record(jury_mode="gating", current_step="s8")
        self.assertEqual(record["state"]["jury_mode"], "gating")
        # survives the JSON round-trip (live consumers read it from the file)
        self.assertEqual(
            checkpoint.parse_checkpoint(checkpoint.encode_checkpoint(record))["state"]["jury_mode"],
            "gating",
        )

    def test_validation_errors(self):
        with self.assertRaisesRegex(checkpoint.CheckpointError, "invalid JSON"):
            checkpoint.parse_checkpoint("{")
        with self.assertRaisesRegex(checkpoint.CheckpointError, "checkpoint must be an object"):
            checkpoint.parse_checkpoint("[]")
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported schema_version"):
            checkpoint.validate_checkpoint(dict(_record(), schema_version="other"))
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported record_type"):
            checkpoint.validate_checkpoint(dict(_record(), record_type="other"))
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported command"):
            checkpoint.build_checkpoint_record(
                run_id="x",
                command="other",
                current_step="s0",
                base_branch="main",
            )
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported current_step"):
            checkpoint.build_checkpoint_record(
                run_id="x",
                command="ship",
                current_step="s99",
                base_branch="main",
            )
        bad_state = _record()
        bad_state["state"] = "not-an-object"
        with self.assertRaisesRegex(checkpoint.CheckpointError, "state must be an object"):
            checkpoint.validate_checkpoint(bad_state)
        for field, message in (
            ("queue", "queue must be an object"),
            ("identifiers", "identifiers must include base_branch"),
            ("resume", "resume must be an object"),
        ):
            record = _record()
            del record[field]
            with self.assertRaisesRegex(checkpoint.CheckpointError, message):
                checkpoint.validate_checkpoint(record)
        bad_completed = _record()
        bad_completed["position"]["completed_steps"] = ["s99"]
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported completed_steps"):
            checkpoint.validate_checkpoint(bad_completed)
        # A non-list completed_steps (e.g. a bare string) must be rejected.
        not_a_list = _record()
        not_a_list["position"]["completed_steps"] = "s0"
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported completed_steps"):
            checkpoint.validate_checkpoint(not_a_list)
        # An unhashable entry (e.g. a nested list) passes the isinstance(list)
        # guard but is not hashable, so the frozenset.issuperset() membership
        # test raises TypeError — that must surface as CheckpointError, not crash.
        unhashable_completed = _record()
        unhashable_completed["position"]["completed_steps"] = [["s0"]]
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported completed_steps"):
            checkpoint.validate_checkpoint(unhashable_completed)
        bad_action = _record()
        bad_action["resume"]["action"] = "other"
        with self.assertRaisesRegex(checkpoint.CheckpointError, "resume action"):
            checkpoint.validate_checkpoint(bad_action)
        bad_repeat = _record()
        bad_repeat["resume"]["repeat_policy"] = "other"
        with self.assertRaisesRegex(checkpoint.CheckpointError, "repeat_policy"):
            checkpoint.validate_checkpoint(bad_repeat)
        for field, value, message in (
            ("merge", "bad", "unsupported merge state"),
            ("capture", "bad", "unsupported capture state"),
            ("close", "bad", "unsupported close state"),
        ):
            record = _record()
            record["state"][field] = value
            with self.assertRaisesRegex(checkpoint.CheckpointError, message):
                checkpoint.validate_checkpoint(record)


class TestResumePlan(unittest.TestCase):
    def test_missing_checkpoint(self):
        plan = checkpoint.resume_plan_as_dict(None)
        self.assertEqual(plan["status"], "no-checkpoint")
        self.assertFalse(plan["can_resume"])

    def test_before_pr_creation_resume(self):
        plan = checkpoint.resume_plan_as_dict(_record(current_step="s2", pull_request=None))
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["next_step"], "s2")
        self.assertEqual(plan["resume_action"], "ensure-branch-and-worktree")

    def test_after_pr_creation_resume(self):
        plan = checkpoint.resume_plan_as_dict(_record(current_step="s7"), live_pr_state="open")
        self.assertEqual(plan["status"], "pr-open")
        self.assertTrue(plan["can_resume"])
        self.assertEqual(plan["next_step"], "s7")

    def test_waiting_on_ci_resume(self):
        plan = checkpoint.resume_plan_as_dict(_record(current_step="s6"), live_pr_state="open")
        self.assertEqual(plan["status"], "waiting-on-ci")
        self.assertEqual(plan["resume_action"], "recheck-ci")

    def test_after_merge_before_capture_resume(self):
        plan = checkpoint.resume_plan_as_dict(
            _record(current_step="s10", merge_state="merged"),
            live_pr_state="merged",
        )
        self.assertEqual(plan["status"], "needs-capture")
        self.assertEqual(plan["next_step"], "s11")
        self.assertEqual(plan["resume_action"], "run-or-verify-capture")

    def test_after_capture_before_close_and_complete(self):
        close = checkpoint.resume_plan_as_dict(
            _record(current_step="s11", merge_state="merged", capture_state="applied"),
            live_pr_state="merged",
        )
        self.assertEqual(close["status"], "needs-close")
        self.assertEqual(close["next_step"], "s12")

        complete = checkpoint.resume_plan_as_dict(
            _record(
                current_step="s12",
                merge_state="merged",
                capture_state="skipped",
                close_state="closed",
            ),
            live_pr_state="merged",
        )
        self.assertEqual(complete["status"], "complete")
        self.assertFalse(complete["can_resume"])

    def test_ambiguous_live_state(self):
        missing_worktree = checkpoint.resume_plan_as_dict(
            _record(worktree="worktrees/issue-149"),
            live_worktree_state="missing",
        )
        self.assertEqual(missing_worktree["status"], "ambiguous")
        self.assertFalse(missing_worktree["can_resume"])

        missing_pr = checkpoint.resume_plan_as_dict(
            _record(pull_request=170),
            live_pr_state="missing",
        )
        self.assertEqual(missing_pr["status"], "ambiguous")

    def test_closed_unmerged_pr_is_ambiguous(self):
        plan = checkpoint.resume_plan_as_dict(
            _record(pull_request=170),
            live_pr_state="closed",
        )
        self.assertEqual(plan["status"], "ambiguous")
        self.assertFalse(plan["can_resume"])
        self.assertIn("closed", plan["reason"])

    def test_merged_pr_ignores_missing_worktree_for_capture(self):
        plan = checkpoint.resume_plan_as_dict(
            _record(
                current_step="s10",
                worktree="worktrees/issue-149",
                pull_request=170,
                merge_state="merged",
            ),
            live_pr_state="merged",
            live_worktree_state="missing",
        )
        self.assertEqual(plan["status"], "needs-capture")
        self.assertEqual(plan["next_step"], "s11")

    def test_a_merged_checkpoint_contradicted_by_live_state_is_ambiguous(self):
        # A checkpoint claiming `merge: merged` used to win over *any* live state, so a
        # checkpoint written optimistically before the merge landed sent every later
        # resume straight to capture and close — closing the issue for a merge that
        # never happened. `closeorder` cannot catch it either: it attests the merge
        # *decision*, not the merge.
        for live in ("open", "closed", "missing"):
            with self.subTest(live_pr_state=live):
                plan = checkpoint.resume_plan_as_dict(
                    _record(current_step="s10", pull_request=170, merge_state="merged"),
                    live_pr_state=live,
                )
                self.assertEqual(plan["status"], "ambiguous")
                self.assertFalse(plan["can_resume"])
                self.assertIn(live, plan["reason"])
                self.assertTrue(plan["warnings"])

    def test_an_unreported_live_state_leaves_the_merged_jump_intact(self):
        # `unknown` is the default when the adapter volunteered nothing — absence of
        # evidence, not evidence of contradiction. Treating it as ambiguous would make
        # every resume unresumable.
        plan = checkpoint.resume_plan_as_dict(
            _record(current_step="s10", pull_request=170, merge_state="merged"),
        )
        self.assertEqual(plan["status"], "needs-capture")
        self.assertEqual(plan["next_step"], "s11")

    def test_live_state_validation(self):
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported live_pr_state"):
            checkpoint.resume_plan_as_dict(_record(), live_pr_state="bad")
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported live_worktree_state"):
            checkpoint.resume_plan_as_dict(_record(), live_worktree_state="bad")


class TestCoveringCheckpoint(unittest.TestCase):
    def test_covered_when_current_step_is_the_expected_step(self):
        record = _record(current_step="s10", completed_steps=["s0", "s1"])
        result = checkpoint.covering_checkpoint(record, "RUN-149", "s10")
        self.assertEqual(result["status"], "covered")
        self.assertTrue(result["covered"])
        self.assertEqual(result["checkpoint_step"], "s10")

    def test_covered_when_expected_step_already_completed_and_run_advanced(self):
        record = _record(
            current_step="s11",
            completed_steps=["s0", "s10"],
        )
        result = checkpoint.covering_checkpoint(record, "RUN-149", "s10")
        self.assertEqual(result["status"], "covered")
        self.assertTrue(result["covered"])

    def test_missing_when_no_checkpoint(self):
        result = checkpoint.covering_checkpoint(None, "RUN-149", "s10")
        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["covered"])
        self.assertIsNone(result["checkpoint_run_id"])
        self.assertIn("no current checkpoint for run RUN-149 at step s10", result["reason"])

    def test_missing_when_checkpoint_is_for_another_run(self):
        record = _record(run_id="RUN-OTHER", current_step="s10")
        result = checkpoint.covering_checkpoint(record, "RUN-149", "s10")
        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["covered"])
        self.assertIn("checkpoint is for run RUN-OTHER", result["reason"])

    def test_stale_step_when_run_has_not_reached_the_expected_step(self):
        record = _record(current_step="s6")
        result = checkpoint.covering_checkpoint(record, "RUN-149", "s10")
        self.assertEqual(result["status"], "stale-step")
        self.assertFalse(result["covered"])
        self.assertIn("run is at s6", result["reason"])

    def test_invalid_expected_step_is_rejected(self):
        with self.assertRaisesRegex(checkpoint.CheckpointError, "backbone step id"):
            checkpoint.covering_checkpoint(_record(), "RUN-149", "s99")


class TestFindOrphans(unittest.TestCase):
    def _ledger_record(self, *, branch=None, pr=None):
        record = {"git": {"branch": branch}}
        if pr is not None:
            record["pull_request"] = {"number": pr}
        return record

    def test_no_live_state_yields_no_orphans(self):
        result = checkpoint.find_orphans()
        self.assertEqual(result["orphan_count"], 0)
        self.assertEqual(result["branches"], [])
        self.assertEqual(result["pull_requests"], [])

    def test_branch_and_pr_covered_by_checkpoint_are_not_orphans(self):
        record = _record(branch="feat/issue-149-resume", pull_request=170)
        result = checkpoint.find_orphans(
            live_branches=["feat/issue-149-resume"],
            live_pull_requests=[170],
            checkpoint_record=record,
        )
        self.assertEqual(result["orphan_count"], 0)
        self.assertIn("feat/issue-149-resume", result["known_branches"])
        self.assertIn(170, result["known_pull_requests"])

    def test_branch_and_pr_covered_by_ledger_are_not_orphans(self):
        result = checkpoint.find_orphans(
            live_branches=["feat/shipped"],
            live_pull_requests=[42],
            ledger_records=[self._ledger_record(branch="feat/shipped", pr=42)],
        )
        self.assertEqual(result["orphan_count"], 0)

    def test_uncovered_live_branch_and_pr_are_flagged(self):
        record = _record(branch="feat/issue-149-resume", pull_request=170)
        result = checkpoint.find_orphans(
            live_branches=["feat/issue-149-resume", "feat/orphan"],
            live_pull_requests=[170, 999],
            checkpoint_record=record,
            ledger_records=[self._ledger_record(branch=None)],
        )
        self.assertEqual(result["branches"], ["feat/orphan"])
        self.assertEqual(result["pull_requests"], [999])
        self.assertEqual(result["orphan_count"], 2)

    def test_checkpoint_without_branch_or_pr_contributes_no_references(self):
        record = _record(branch=None, pull_request=None)
        result = checkpoint.find_orphans(
            live_branches=["feat/orphan"],
            live_pull_requests=[999],
            checkpoint_record=record,
            ledger_records=[{"git": {"branch": None}, "pull_request": {"number": None}}],
        )
        self.assertEqual(result["branches"], ["feat/orphan"])
        self.assertEqual(result["pull_requests"], [999])
        self.assertEqual(result["known_branches"], [])
        self.assertEqual(result["known_pull_requests"], [])

    def test_non_dict_identifiers_and_records_handled_safely(self):
        result = checkpoint.find_orphans(
            live_branches=["feat/orphan"],
            live_pull_requests=[999],
            checkpoint_record={"identifiers": None},
            ledger_records=["not-a-dict", {"git": "not-a-dict"}],
        )
        self.assertEqual(result["branches"], ["feat/orphan"])
        self.assertEqual(result["pull_requests"], [999])
        self.assertEqual(result["orphan_count"], 2)


class TestResumeObservesReality(unittest.TestCase):
    """#635: core was *told* the live state and never looked."""

    def _rec(self, **kw):
        base = dict(run_id="ship-1", command="ship", current_step="s10",
                    base_branch="main", branch="b", worktree="/tmp/wt",
                    pull_request=7, head_sha="a" * 40)
        base.update(kw)
        return checkpoint.build_checkpoint_record(**base)

    def test_a_crash_mid_merge_with_unknown_live_state_is_ambiguous(self):
        """The genuinely ambiguous case used to resume as a plain pr-open."""
        plan = checkpoint.resume_plan_as_dict(self._rec(merge_state="pending"))
        self.assertEqual(plan["status"], "ambiguous")
        self.assertFalse(plan["can_resume"])
        self.assertIn("in-flight", plan["reason"])
        self.assertTrue(plan["warnings"])

    def test_live_evidence_resolves_a_pending_merge_either_way(self):
        merged = checkpoint.resume_plan_as_dict(
            self._rec(merge_state="pending"), live_pr_state="merged")
        self.assertEqual(merged["status"], "needs-capture")
        still_open = checkpoint.resume_plan_as_dict(
            self._rec(merge_state="pending"), live_pr_state="open")
        self.assertEqual(still_open["status"], "pr-open")
        self.assertTrue(still_open["can_resume"])

    def test_pending_without_a_pull_request_is_not_ambiguous(self):
        # Nothing to be ambiguous about: no PR was ever opened.
        plan = checkpoint.resume_plan_as_dict(
            self._rec(merge_state="pending", pull_request=None))
        self.assertNotEqual(plan["status"], "ambiguous")

    def test_a_moved_branch_head_warns(self):
        plan = checkpoint.resume_plan_as_dict(self._rec(), live_head_sha="b" * 40)
        self.assertEqual(len(plan["warnings"]), 1)
        self.assertIn("head moved", plan["warnings"][0])
        # A warning, not a block: the usual cause is a legitimate push that crashed
        # before the next checkpoint, and s10 fails closed on its own.
        self.assertTrue(plan["can_resume"])

    def test_a_matching_head_says_nothing(self):
        plan = checkpoint.resume_plan_as_dict(self._rec(), live_head_sha="a" * 40)
        self.assertEqual(plan["warnings"], [])

    def test_no_live_head_says_nothing(self):
        # Unreadable head is not evidence of a moved branch.
        self.assertEqual(checkpoint.resume_plan_as_dict(self._rec())["warnings"], [])

    def test_a_checkpoint_without_a_head_says_nothing(self):
        plan = checkpoint.resume_plan_as_dict(
            self._rec(head_sha=None), live_head_sha="b" * 40)
        self.assertEqual(plan["warnings"], [])
