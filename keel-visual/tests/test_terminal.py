"""Tests for the pure terminal frame renderer."""

import unittest

from keel_visual import runstate as rs
from keel_visual import terminal as t


def _state(*, action="defer", checkpoint=None, minor=0, major=0, critical=0, jury_mode=None):
    rec = {
        "record_type": "ship_run",
        "issue": {"number": 351},
        "pull_request": {"number": 361},
        "assessment": {"merge": {"action": action, "reason": "r"}},
        "verdict": {"counts": {"critical": critical, "major": major, "minor": minor, "nit": 0}},
    }
    if jury_mode is not None:
        rec["run_context"] = {"jury_mode": jury_mode}
    return rs.build_run_state(rec, checkpoint_step=checkpoint)


class TestPaint(unittest.TestCase):
    def test_disabled_is_plain(self):
        self.assertEqual(t.paint("x", "green", enable=False), "x")

    def test_enabled_wraps(self):
        out = t.paint("x", "green", enable=True)
        self.assertTrue(out.startswith("\x1b["))
        self.assertTrue(out.endswith(t.RESET))

    def test_unknown_color_is_plain(self):
        self.assertEqual(t.paint("x", "chartreuse", enable=True), "x")

    def test_none_color_is_plain(self):
        self.assertEqual(t.paint("x", None, enable=True), "x")


class TestDisplayStatus(unittest.TestCase):
    def test_before_after_and_on(self):
        gate = {"kind": "gate"}
        self.assertEqual(t.display_status(gate, 2, 5), "done")
        self.assertEqual(t.display_status(gate, 8, 5), "pending")
        self.assertEqual(t.display_status(gate, 5, 5), "gate")

    def test_loop_and_normal_on_playhead(self):
        self.assertEqual(t.display_status({"kind": "loop"}, 3, 3), "loop")

    def test_blocked_marks_the_playhead_step(self):
        """keel#636: reached and not passed must not paint as still running."""
        gate = {"kind": "gate"}
        self.assertEqual(t.display_status(gate, 5, 5, blocked=True), "blocked")
        # It outranks the intrinsic kind — the old rendering showed only "gate".
        self.assertNotEqual(t.display_status(gate, 5, 5, blocked=True), "gate")
        self.assertEqual(t.display_status({"kind": "loop"}, 3, 3, blocked=True), "blocked")

    def test_blocked_leaves_other_steps_alone(self):
        self.assertEqual(t.display_status({"kind": "gate"}, 2, 5, blocked=True), "done")
        self.assertEqual(t.display_status({"kind": "gate"}, 8, 5, blocked=True), "pending")

    def test_blocked_has_its_own_glyph_and_colour(self):
        # A status the renderer does not know would fall back to a hollow dim dot —
        # quieter than the gate it replaced, which is the wrong direction.
        self.assertIn("blocked", t._GLYPH)
        self.assertEqual(t._STATUS_COLOR["blocked"], "red")
        self.assertEqual(t.display_status({"kind": "normal"}, 3, 3), "active")
        self.assertEqual(t.display_status({"kind": "merge"}, 3, 3), "gate")


class TestGateWord(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(t._gate_word({"gate": {"outcome": "fail"}}), "blocked")
        self.assertEqual(t._gate_word({"gate": {"outcome": "pass"}}), "passed")
        self.assertEqual(t._gate_word({"gate": {"outcome": "pending"}}), "pending")
        self.assertEqual(t._gate_word({"gate": None}), "")


class TestFrame(unittest.TestCase):
    def test_flow_plain_contains_pipeline_and_pointer(self):
        out = t.frame(_state(checkpoint="s8", major=1), 8, style="flow", color=False)
        self.assertIn("s8 · test", out)
        self.assertIn("gate: blocked", out)
        self.assertIn("regression", out)
        self.assertIn("major", out)
        self.assertIn("▲", out)

    def test_flow_gate_passed_branch(self):
        out = t.frame(_state(checkpoint="s8"), 8, style="flow", color=False)
        self.assertIn("gate: passed", out)

    def test_flow_loop_branch(self):
        out = t.frame(_state(checkpoint="s9"), 9, style="flow", color=False)
        self.assertIn("fix loop", out)

    def test_flow_merged_line(self):
        out = t.frame(_state(action="merge"), 12, style="flow", color=False)
        self.assertIn("merged — run is green", out)

    def test_flow_merge_gate_shows_merged_tag(self):
        out = t.frame(_state(action="merge"), 10, style="flow", color=False)
        self.assertIn("[merged]", out)

    def test_regression_hidden_off_gate(self):
        out = t.frame(_state(checkpoint="s4"), 4, style="flow", color=False)
        self.assertNotIn("regression ▕", out)

    def test_wave_style(self):
        out = t.frame(_state(checkpoint="s6"), 6, style="wave", color=False)
        self.assertIn("at s6 · ci", out)
        self.assertIn("●", out)

    def test_flow_long_ids_stay_aligned(self):
        # overnight has word-length phase ids; the caret and the active label must
        # land on the active node's column (adaptive cell width).
        st = rs.build_run_state(None, command="overnight")
        out = t.frame(st, 2, style="flow", color=False)
        lines = out.split("\n")
        pipeline = lines[2]
        pointer = next(line for line in lines if "▲" in line)
        cell_w = 10  # max(4, len("preflight")+1)
        self.assertEqual(pointer.index("▲"), 2 * cell_w)
        self.assertIn(pipeline[2 * cell_w], "●◉◆↻○")

    def test_wave_blocked_gate_glyph(self):
        out = t.frame(_state(checkpoint="s8", major=1), 8, style="wave", color=False)
        self.assertIn("at s8 · test", out)
        self.assertIn("◆", out)

    def test_color_adds_escapes(self):
        out = t.frame(_state(checkpoint="s8", critical=1), 8, style="flow", color=True)
        self.assertIn("\x1b[", out)

    def test_active_clamped(self):
        out = t.frame(_state(action="merge"), 99, style="flow", color=False)
        self.assertIn("s12 · close", out)

    def test_empty_state_is_safe(self):
        out = t.frame({"steps": []}, 0, style="flow", color=False)
        self.assertIn("keel-visual", out)


class TestHeaderBadgesAndGateStrip(unittest.TestCase):
    def test_header_tier_badge(self):
        self.assertIn("[T3]", t._header({"command": "ship", "tier": 3}, enable=False))
        self.assertIn("[T1]", t._header({"command": "ship", "tier": 1}, enable=False))

    def test_header_no_tier_badge_when_unknown(self):
        self.assertNotIn("[T", t._header({"command": "ship"}, enable=False))
        self.assertNotIn("[T", t._header({"command": "ship", "tier": "3"}, enable=False))

    def test_header_window_tag(self):
        self.assertIn("[win:open]", t._header({"window_open": True}, enable=False))
        self.assertIn("[win:closed]", t._header({"window_open": False}, enable=False))

    def test_header_no_window_tag_when_unknown(self):
        out = t._header({"command": "ship", "window_open": "yes"}, enable=False)
        self.assertNotIn("win:", out)

    def test_header_bypassed_window_tag(self):
        run = {"window_open": False, "bypassed_window": True}
        out = t._header(run, enable=False)
        self.assertIn("[win:closed]", out)
        self.assertIn("[win:bypassed]", out)
        self.assertNotIn("win:bypassed", t._header({"window_open": False}, enable=False))

    def test_header_badges_colored(self):
        run = {"tier": 3, "window_open": True, "bypassed_window": True}
        self.assertIn("\x1b[", t._header(run, enable=True))

    def test_gate_strip_glyphs(self):
        gates = [
            {"name": "build", "ok": True, "skipped": False},
            {"name": "evidence", "ok": False, "skipped": False},
            {"name": "jury", "ok": False, "skipped": True},
        ]
        out = t._gate_strip(gates, enable=False)
        self.assertEqual(out, "gates: build✓ evidence✗ jury–")

    def test_gate_strip_distinguishes_a_timeout_from_a_failure(self):
        # keel#622: a timed-out gate is still red, but must not read as a broken test.
        gates = [
            {"name": "build", "ok": False, "skipped": False, "timed_out": True},
            {"name": "lint", "ok": False, "skipped": False, "timed_out": False},
        ]
        self.assertEqual(t._gate_strip(gates, enable=False), "gates: build⧗ lint✗")

    def test_gate_strip_unnamed_gate_falls_back(self):
        self.assertEqual(t._gate_strip([{"ok": True}], enable=False), "gates: ?✓")

    def test_flow_renders_gate_strip(self):
        st = _state(checkpoint="s8")
        st["gates"] = [
            {"name": "build", "ok": True, "skipped": False},
            {"name": "lint", "ok": False, "skipped": False},
        ]
        out = t.frame(st, 8, style="flow", color=False)
        self.assertIn("gates: build✓ lint✗", out)

    def test_flow_no_gate_strip_when_empty(self):
        out = t.frame(_state(checkpoint="s8"), 8, style="flow", color=False)
        self.assertNotIn("gates:", out)

    def test_frame_header_carries_tier_and_window(self):
        st = _state(checkpoint="s8")
        st["tier"] = 2
        st["window_open"] = True
        out = t.frame(st, 8, style="flow", color=False)
        self.assertIn("[T2]", out)
        self.assertIn("[win:open]", out)


class TestHeaderAndHelpers(unittest.TestCase):
    def test_header_in_progress(self):
        out = t._header({"command": "ship"}, enable=False)
        self.assertIn("in progress", out)

    def test_header_minimal(self):
        out = t._header({}, enable=False)
        self.assertIn("keel-visual", out)

    def test_header_jury_chip(self):
        run = {"command": "ship", "jury": {"mode": "gating", "active": True}}
        self.assertIn("[jury]", t._header(run, enable=False))

    def test_header_no_jury_chip_when_inactive(self):
        out = t._header({"command": "ship", "jury": {"active": False}}, enable=False)
        self.assertNotIn("[jury]", out)

    def test_flow_jury_on_review_step(self):
        out = t.frame(_state(checkpoint="s7", jury_mode="gating"), 7, style="flow", color=False)
        self.assertIn("s7 · review", out)
        self.assertIn("jury: gating", out)

    def test_flow_no_jury_marker_off_review(self):
        out = t.frame(_state(checkpoint="s4", jury_mode="gating"), 4, style="flow", color=False)
        self.assertNotIn("jury: gating", out)

    def test_flow_jury_tag_carries_verdict(self):
        # With a saved ai-jury outcome the review tag extends to the verdict.
        st = _state(checkpoint="s7", jury_mode="gating")
        st["jury_verdict"] = {
            "verdict": "REQUEST CHANGES",
            "counts": {"critical": 0, "major": 1, "minor": 0, "nit": 0},
            "reviewers": 2,
        }
        out = t.frame(st, 7, style="flow", color=False)
        self.assertIn("jury: gating — REQUEST CHANGES", out)

    def test_header_jury_chip_carries_verdict(self):
        run = {
            "command": "ship",
            "jury": {"mode": "gating", "active": True},
            "jury_verdict": {
                "verdict": "APPROVE",
                "counts": {"critical": 0, "major": 0, "minor": 0, "nit": 0},
                "reviewers": 2,
            },
        }
        self.assertIn("[jury — APPROVE]", t._header(run, enable=False))

    def test_jury_verdict_word_failsoft(self):
        # No block / malformed block / blank verdict all fall back to mode-only.
        self.assertIsNone(t._jury_verdict_word({}))
        self.assertIsNone(t._jury_verdict_word({"jury_verdict": "x"}))
        self.assertIsNone(t._jury_verdict_word({"jury_verdict": {"verdict": ""}}))
        self.assertIsNone(t._jury_verdict_word({"jury_verdict": {"verdict": 5}}))

    def test_bar_fraction(self):
        out = t._bar(0.5, 10, "green", enable=False)
        self.assertEqual(out.count("█"), 5)
        self.assertEqual(out.count("░"), 5)

    def test_exercised_indices(self):
        # Every phase of a command's own flow is exercised.
        st = rs.build_run_state(None, command="overnight")
        idxs = t.exercised_indices(st)
        self.assertEqual(idxs, [0, 1, 2, 3, 4])

    def test_exercised_defaults_true(self):
        self.assertEqual(t.exercised_indices({"steps": [{"id": "s0"}]}), [0])


if __name__ == "__main__":
    unittest.main()
