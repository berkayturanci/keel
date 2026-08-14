"""Tests for the canonical command-flow registry."""

import unittest

from keel import flows
from keel.model import BACKBONE


class TestShipFlow(unittest.TestCase):
    def test_ship_is_the_backbone(self):
        ship = flows.flow_for("ship")
        self.assertEqual([p.id for p in ship], [s.id for s in BACKBONE])
        self.assertEqual([p.name for p in ship], [s.name for s in BACKBONE])

    def test_ship_gate_loop_merge_kinds(self):
        kinds = {p.id: p.kind for p in flows.flow_for("ship")}
        self.assertEqual(kinds["s8"], "gate")
        self.assertEqual(kinds["s9"], "loop")
        self.assertEqual(kinds["s10"], "merge")
        self.assertEqual(kinds["s0"], "normal")


class TestRegistry(unittest.TestCase):
    def test_all_seventeen_commands_present(self):
        self.assertEqual(len(flows.FLOWS), 17)

    def test_command_names_sorted(self):
        names = flows.command_names()
        self.assertEqual(list(names), sorted(names))
        self.assertIn("overnight", names)
        self.assertIn("swarm", names)
        self.assertIn("wrap", names)

    def test_every_flow_is_nonempty_with_valid_kinds(self):
        for command, flow in flows.FLOWS.items():
            self.assertTrue(flow, command)
            for phase in flow:
                self.assertIn(phase.kind, flows.PHASE_KINDS, f"{command}/{phase.id}")
                self.assertTrue(phase.id and phase.name, command)

    def test_each_flow_ends_or_contains_a_terminal_phase(self):
        # Every non-ship command flow surfaces a report/merge/close-like ending.
        for command, flow in flows.FLOWS.items():
            if command == "ship":
                continue
            self.assertTrue(flow[-1].kind in ("report", "normal"), command)

    def test_known_commands(self):
        self.assertTrue(flows.is_known("overnight"))
        self.assertTrue(flows.is_known("swarm"))
        self.assertFalse(flows.is_known("nope"))

    def test_flow_for_unknown_falls_back_to_ship(self):
        self.assertEqual(flows.flow_for("nope"), flows.flow_for("ship"))

    def test_loop_kinds_present_in_loop_commands(self):
        for command in ("overnight", "work-block", "regression", "triage", "swarm"):
            kinds = [p.kind for p in flows.flow_for(command)]
            self.assertIn("loop", kinds, command)


if __name__ == "__main__":
    unittest.main()
