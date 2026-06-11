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

    def test_live_state_validation(self):
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported live_pr_state"):
            checkpoint.resume_plan_as_dict(_record(), live_pr_state="bad")
        with self.assertRaisesRegex(checkpoint.CheckpointError, "unsupported live_worktree_state"):
            checkpoint.resume_plan_as_dict(_record(), live_worktree_state="bad")
