"""Unit tests for the backbone model + schema/model slot consistency."""

import json
import unittest

from keel import config as cfg
from keel import model


class TestBackbone(unittest.TestCase):
    def test_step_ids_unique_and_ordered(self):
        ids = model.step_ids()
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[0], "s0")
        self.assertEqual(ids[-1], "s12")

    def test_slots_cover_every_backbone_step(self):
        slots_by_step = {step.id: model.slots_for_step(step.id) for step in model.BACKBONE}
        self.assertIn("after:config", [slot.name for slot in slots_by_step["s0"]])
        for step in model.BACKBONE[1:]:
            self.assertTrue(slots_by_step[step.id], f"{step.id} has no extension hooks")
        self.assertIn("after-implement", model.SLOTS)
        self.assertIn("reviewers", model.SLOTS)
        self.assertIn("tester", model.SLOTS)
        self.assertIn("pre-merge", model.SLOTS)
        self.assertIn("post-merge", model.SLOTS)

    def test_each_slot_maps_to_one_step(self):
        for slot in model.SLOTS:
            step = model.step_for_slot(slot)
            self.assertEqual(step.id, model.slot_meta(slot).step_id)

    def test_blocking_slots_are_limited(self):
        blocking = {slot.name for slot in model.SLOT_DEFINITIONS if slot.may_block}
        self.assertEqual(blocking, {"guard", "tester", "test", "pre-merge"})

    def test_get_step_and_unknown(self):
        self.assertEqual(model.get_step("s4").name, "implement")
        with self.assertRaises(KeyError):
            model.get_step("s99")

    def test_step_for_unknown_slot_raises(self):
        with self.assertRaises(KeyError):
            model.step_for_slot("nope")

    def test_agentic_steps(self):
        agentic = {s.name for s in model.BACKBONE if s.agentic}
        self.assertEqual(agentic, {"implement", "classify", "review"})

    def test_invariants_present(self):
        for inv in ("merge_lock", "window_gate", "fail_soft",
                    "orchestrator_only_writes", "attribution"):
            self.assertIn(inv, model.INVARIANTS)


class TestSchemaModelConsistency(unittest.TestCase):
    """The schema's extension slots must equal model.SLOTS (guard against drift)."""

    def test_schema_slots_match_model(self):
        schema = json.loads(cfg.SCHEMA_PATH.read_text(encoding="utf-8"))
        schema_slots = tuple(schema["properties"]["extensions"]["properties"].keys())
        self.assertEqual(schema_slots, model.SLOTS)

    def test_config_reexports_model_slots(self):
        self.assertIs(cfg.SLOTS, model.SLOTS)


if __name__ == "__main__":
    unittest.main()
