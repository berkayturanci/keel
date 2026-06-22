"""Tests for the pure multi-run dashboard."""

import unittest

from keel_visual import dash
from keel_visual import runstate as rs


def _run_state(*, action="merge", checkpoint=None, merge_state=None):
    rec = {
        "record_type": "ship_run",
        "issue": {"number": 351}, "pull_request": {"number": 361},
        "assessment": {"merge": {"action": action, "reason": "r"}},
        "verdict": {"counts": {"critical": 0, "major": 0, "minor": 0, "nit": 0}},
    }
    cps = {"merge": merge_state} if merge_state else None
    return rs.build_run_state(rec, checkpoint_step=checkpoint, checkpoint_state=cps)


class TestParseWorktrees(unittest.TestCase):
    def test_extracts_paths(self):
        text = "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\nworktree /repo/wt-1\nHEAD def\n"
        self.assertEqual(dash.parse_worktrees(text), ["/repo", "/repo/wt-1"])

    def test_skips_blank_path(self):
        self.assertEqual(dash.parse_worktrees("worktree \nworktree /a\n"), ["/a"])

    def test_empty(self):
        self.assertEqual(dash.parse_worktrees(""), [])


class TestIdentity(unittest.TestCase):
    def test_full(self):
        rec = {"command": "ship", "queue": {"active_issue": 351},
               "identifiers": {"pull_request": 361}}
        self.assertEqual(
            dash.identity_from_checkpoint(rec),
            {"issue": 351, "pr": 361, "command": "ship"},
        )

    def test_malformed(self):
        self.assertEqual(
            dash.identity_from_checkpoint(None),
            {"issue": None, "pr": None, "command": None},
        )
        self.assertEqual(
            dash.identity_from_checkpoint({"queue": "x", "identifiers": 5, "command": ""}),
            {"issue": None, "pr": None, "command": None},
        )

    def test_int_or_none_rejects_bool(self):
        self.assertIsNone(dash._int_or_none(True))
        self.assertEqual(dash._int_or_none(7), 7)
        self.assertIsNone(dash._int_or_none("7"))


class TestStatusOf(unittest.TestCase):
    def test_merged(self):
        self.assertEqual(dash._status_of(_run_state(action="merge")), "merged")

    def test_done_is_not_merged(self):
        # a closed-out activity run (status=done) is "done", never "merged"
        self.assertEqual(dash._status_of({"done": True, "steps": [], "active_index": 0}), "done")
        # an actual merge still wins over done if both are set
        self.assertEqual(dash._status_of({"merged": True, "done": True}), "merged")

    def test_blocked_gate(self):
        st = rs.build_run_state(
            {"record_type": "ship_run", "assessment": {"merge": {"action": "defer"}},
             "verdict": {"counts": {"major": 1}}},
            checkpoint_step="s8",
        )
        self.assertEqual(dash._status_of(st), "blocked")

    def test_gate_running(self):
        st = rs.build_run_state(
            {"record_type": "ship_run", "assessment": {"merge": {"action": "defer"}}},
            checkpoint_step="s8",
        )
        self.assertEqual(dash._status_of(st), "gate")

    def test_running(self):
        st = rs.build_run_state(
            {"record_type": "ship_run", "assessment": {"merge": {"action": "defer"}}},
            checkpoint_step="s4",
        )
        self.assertEqual(dash._status_of(st), "running")

    def test_active_out_of_range_is_running(self):
        self.assertEqual(dash._status_of({"steps": [], "active_index": 0}), "running")


class TestBoardRow(unittest.TestCase):
    def test_row_shows_issue_and_pr(self):
        # a run carrying both renders #<issue>→#<PR> so the row is unambiguous
        row = dash.board_row(_run_state(action="merge"), {"pr": 361, "issue": 351})
        self.assertEqual(row["label"], "#351→#361")
        self.assertEqual(row["issue"], 351)
        self.assertEqual(row["pr"], 361)
        self.assertEqual(row["status"], "merged")
        self.assertEqual(row["total"], 13)

    def test_row_pr_only(self):
        st = rs.build_run_state(None, checkpoint_step="s10")
        row = dash.board_row(st, {"pr": 361, "issue": None})
        self.assertEqual(row["label"], "#361")
        self.assertEqual(row["pr"], 361)
        self.assertIsNone(row["issue"])

    def test_row_falls_back_to_issue(self):
        st = rs.build_run_state(None, checkpoint_step="s4")
        row = dash.board_row(st, {"pr": None, "issue": 351})
        self.assertEqual(row["label"], "#351")

    def test_row_unknown_label(self):
        st = rs.build_run_state(None, checkpoint_step="s4")
        row = dash.board_row(st, {"pr": None, "issue": None})
        self.assertEqual(row["label"], "?")

    def test_row_derives_number_from_run_id(self):
        st = rs.build_run_state(None, checkpoint_step="s4")
        row = dash.board_row(
            st, {"pr": None, "issue": None, "run_id": "ship-585", "command": "ship"})
        self.assertEqual(row["label"], "#585")

    def test_row_derives_number_for_multiword_command(self):
        st = rs.build_run_state(None, checkpoint_step="s4", command="pr-loop")
        row = dash.board_row(
            st, {"pr": None, "issue": None, "run_id": "pr-loop-2253", "command": "pr-loop"})
        self.assertEqual(row["label"], "#2253")

    def test_row_counter_run_id_kept_raw(self):
        # an opaque counter (command name is not the run_id prefix) stays raw
        st = rs.build_run_state(None, checkpoint_step="config", command="morning")
        row = dash.board_row(
            st, {"pr": None, "issue": None, "run_id": "m-1", "command": "morning"})
        self.assertEqual(row["label"], "m-1")


class TestRenderBoard(unittest.TestCase):
    def test_empty_board(self):
        out = dash.render_board([], color=False)
        self.assertIn("0 active runs", out)
        self.assertIn("no active runs found", out)

    def test_single_run_singular(self):
        rows = [dash.board_row(_run_state(action="merge"), {"pr": 361})]
        out = dash.render_board(rows, color=False)
        self.assertIn("1 active run", out)
        self.assertNotIn("1 active runs", out)
        self.assertIn("#361", out)
        self.assertIn("merged", out)
        self.assertIn("█", out)

    def test_multiple_runs_colored(self):
        rows = [
            dash.board_row(_run_state(action="merge"), {"pr": 361}),
            dash.board_row(rs.build_run_state(None, checkpoint_step="s4"), {"issue": 360}),
        ]
        out = dash.render_board(rows, color=True)
        self.assertIn("2 active runs", out)
        self.assertIn("\x1b[", out)


class TestRenderProjectBoard(unittest.TestCase):
    def _row(self, project, **identity):
        # build from None so the identity's pr/issue drive the label (no baked-in pr)
        row = dash.board_row(rs.build_run_state(None, checkpoint_step="s8"), identity)
        row["project"] = project
        return row

    def test_empty(self):
        out = dash.render_project_board([], color=False)
        self.assertIn("0 runs across 0 projects", out)
        self.assertIn("no active runs found", out)

    def test_singular_one_run_one_project(self):
        out = dash.render_project_board([self._row("alpha", pr=42)], color=False)
        self.assertIn("1 run across 1 project", out)
        self.assertNotIn("1 runs", out)
        self.assertIn("alpha", out)
        self.assertIn("#42", out)

    def test_grouped_across_projects_colored(self):
        rows = [self._row("alpha", pr=42), self._row("beta", pr=7), self._row("alpha", issue=9)]
        out = dash.render_project_board(rows, color=True)
        self.assertIn("3 runs across 2 projects", out)  # alpha + beta
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertIn("\x1b[", out)

    def test_project_name_ansi_injection_stripped(self):
        # a directory literally named with an escape sequence must not reach the
        # terminal raw (no screen-clear / cursor spoof from a malicious dir name).
        # The ESC byte is dropped; the leftover printable "[2J" is inert text.
        rows = [self._row("ev\x1b[2Jil", pr=1)]
        out = dash.render_project_board(rows, color=False)
        self.assertNotIn("\x1b", out)       # the dangerous ESC is gone
        self.assertIn("ev[2Jil", out)       # printable chars kept, just defanged


if __name__ == "__main__":
    unittest.main()
