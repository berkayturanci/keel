"""Tests for operator-facing progress status snapshots."""

import unittest
from types import SimpleNamespace

from keel import checkpoint, ledger
from keel import config as cfg
from keel import status as progress


def _config() -> cfg.ProjectConfig:
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.9",
        base_branch="main",
        repo="keel",
        timezone="Europe/Istanbul",
        knobs=cfg.Knobs(build_gate_cmd="true"),
        policy_pack={"name": "test"},
    )


def _checkpoint(**overrides):
    values = {
        "run_id": "RUN-148",
        "command": "overnight",
        "current_step": "s6",
        "base_branch": "main",
        "target": "work block",
        "issue_queue": [148, 146],
        "active_issue": 148,
        "branch": "feature/issue-148-progress-status",
        "worktree": "worktrees/issue-148",
        "pull_request": 168,
        "head_sha": "abc123",
        "completed_steps": ["s0", "s1", "s2", "s3", "s4", "s5"],
    }
    values.update(overrides)
    return checkpoint.build_checkpoint_record(**values)


def _ledger_record(*, issue, pr, action="merge", blocked=False, capture_status="applied"):
    outcome = SimpleNamespace(
        gate="build", ok=not blocked, skipped=False, timed_out=False, error=None, findings=[]
    )
    verdict = SimpleNamespace(blocked=blocked, counts={"blocker": int(blocked)})
    merge = SimpleNamespace(action=action, reason=f"{action} reason")
    assessment = SimpleNamespace(
        tier=2,
        reviewers=2,
        window_open=True,
        ci_ok=True,
        merge=merge,
        halted=False,
        bypassed_window=False,
    )
    return ledger.build_ship_run_record(
        command="ship",
        base_branch="main",
        changed_files=["src/keel/status.py"],
        outcomes=[outcome],
        verdict=verdict,
        assessment=assessment,
        issue_number=issue,
        pr_number=pr,
        capture_status=capture_status,
    )


class TestStatusContract(unittest.TestCase):
    def test_contract_declares_snapshot_semantics(self):
        contract = progress.status_contract_as_dict(_config())

        self.assertEqual(contract["schema_version"], "keel.progress-status.v1")
        self.assertFalse(contract["realtime"])
        self.assertEqual(contract["snapshot_semantics"], "last-safe-boundary")
        self.assertTrue(contract["consumer_neutral"])
        self.assertEqual(contract["source"]["checkpoint"]["schema_version"], "keel.checkpoint.v1")
        self.assertEqual(contract["source"]["run_ledger"]["schema_version"], "keel.run-ledger.v1")
        self.assertEqual(contract["capture_health"]["schema_version"], "keel.capture-health.v1")

    def test_no_active_run_snapshot(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=None,
            ledger_records=[],
        )

        self.assertEqual(snapshot["status"], "no-active-run")
        self.assertIsNone(snapshot["current"])
        self.assertEqual(snapshot["history"]["counts"]["shipped"], 0)
        self.assertEqual(snapshot["capture_health"]["status"], "clean")
        self.assertEqual(snapshot["next"]["source"], "no-active-run")

    def test_waiting_snapshot_from_checkpoint(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(current_step="s6"),
            ledger_records=[],
        )

        self.assertEqual(snapshot["status"], "waiting")
        self.assertEqual(snapshot["current"]["issue"], 148)
        self.assertEqual(snapshot["current"]["step"], "s6")
        self.assertEqual(snapshot["current"]["wait_reason"], "ci")
        self.assertEqual(snapshot["next"]["issue"], 146)

    def test_active_snapshot_from_checkpoint(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(current_step="s4"),
            ledger_records=[],
        )

        self.assertEqual(snapshot["status"], "active")
        self.assertEqual(snapshot["current"]["wait_reason"], None)

    def test_interrupted_snapshot_uses_stop_reason(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(stop_reason="rate-limit"),
            ledger_records=[],
        )

        self.assertEqual(snapshot["status"], "interrupted")
        self.assertEqual(snapshot["current"]["wait_reason"], "rate-limit")

    def test_completed_checkpoint_reports_completed(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(
                current_step="s12",
                merge_state="merged",
                capture_state="skipped",
                close_state="closed",
            ),
            ledger_records=[],
        )

        self.assertEqual(snapshot["status"], "completed")

    def test_checkpoint_without_next_item_reports_none(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(issue_queue=[148], active_issue=148),
            ledger_records=[],
        )

        self.assertEqual(snapshot["next"], {"issue": None, "source": "checkpoint.queue"})

    def test_next_item_uses_item_after_active_issue(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(issue_queue=[140, 148, 146], active_issue=148),
            ledger_records=[],
        )

        self.assertEqual(snapshot["next"], {"issue": 146, "source": "checkpoint.queue"})

    def test_next_item_uses_first_issue_when_active_is_absent(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(issue_queue=[140, 146], active_issue=148),
            ledger_records=[],
        )

        self.assertEqual(snapshot["next"], {"issue": 140, "source": "checkpoint.queue"})

    def test_empty_queue_reports_no_next_item(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(issue_queue=[], active_issue=148),
            ledger_records=[],
        )

        self.assertEqual(snapshot["next"], {"issue": None, "source": "checkpoint.queue"})

    def test_completed_snapshot_summarizes_history(self):
        records = [
            _ledger_record(issue=1, pr=10),
            _ledger_record(issue=2, pr=20, action="defer"),
            _ledger_record(issue=3, pr=30, action="block", blocked=True),
            _ledger_record(issue=4, pr=40, capture_status="skipped"),
            _ledger_record(issue=5, pr=50, action="skip"),
        ]

        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=None,
            ledger_records=records,
        )

        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(
            snapshot["history"]["counts"],
            {
                "shipped": 2,
                "blocked": 1,
                "deferred": 1,
                "skipped": 1,
            },
        )
        self.assertEqual(snapshot["history"]["items"][0]["state"], "shipped")
        self.assertEqual(snapshot["capture_health"]["counts"]["skipped"], 1)

    def test_snapshot_surfaces_capture_gaps(self):
        record = _ledger_record(issue=1, pr=10)
        record["capture"]["marker"] = None

        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=None,
            ledger_records=[record],
        )

        self.assertEqual(snapshot["capture_health"]["status"], "needs-reconcile")
        self.assertEqual(snapshot["capture_health"]["counts"]["missing_marker"], 1)

    def test_render_status_orders_actionable_fields(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(current_step="s7"),
            ledger_records=[_ledger_record(issue=1, pr=10)],
        )

        rendered = progress.render_status(snapshot)

        self.assertIn("keel status", rendered)
        self.assertIn("wait          : review", rendered)
        self.assertIn("shipped       : 1", rendered)
        self.assertIn("capture       : clean", rendered)
        self.assertIn("capture gaps  : 0", rendered)
        self.assertIn("orphans       : 0", rendered)


class TestStatusOrphans(unittest.TestCase):
    def test_no_live_state_reports_no_orphans(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(current_step="s7"),
            ledger_records=[],
        )
        self.assertEqual(snapshot["orphans"]["orphan_count"], 0)

    def test_uncovered_live_branch_and_pr_flagged_advisorily(self):
        snapshot = progress.build_status_snapshot(
            config=_config(),
            checkpoint_record=_checkpoint(
                branch="feature/issue-148-progress-status",
                pull_request=168,
            ),
            ledger_records=[_ledger_record(issue=1, pr=10)],
            live_branches=["feature/issue-148-progress-status", "feature/orphan"],
            live_pull_requests=[168, 777],
        )
        orphans = snapshot["orphans"]
        self.assertEqual(orphans["branches"], ["feature/orphan"])
        self.assertEqual(orphans["pull_requests"], [777])

        rendered = progress.render_status(snapshot)
        self.assertIn("orphans       : 2", rendered)
        self.assertIn("orphan branch : feature/orphan", rendered)
        self.assertIn("orphan pr     : #777", rendered)
