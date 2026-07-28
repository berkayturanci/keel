"""Unit tests for extension parsing, validation, and loading."""

import tempfile
import unittest
from pathlib import Path

from keel import config as cfg
from keel import extensions as ext

AGENTIC = """---
id: design-parity
slot: tester
kind: agentic
agent: inherit
anchorable: true
---
Compare rendered screens against the Figma baseline and report deltas.
"""

COMMAND = """---
id: golden
slot: tester
kind: command
run: flutter test --tags golden
---
"""

HARD_GATE = """---
id: design-parity-gate
slot: pre-merge
kind: command
on_fail: block
run: ./scripts/check-design-parity.sh
---
"""


class TestFrontmatter(unittest.TestCase):
    def test_no_frontmatter(self):
        meta, body = ext.split_frontmatter("just a body")
        self.assertEqual(meta, {})
        self.assertEqual(body, "just a body")

    def test_unterminated(self):
        with self.assertRaises(ext.ExtensionError):
            ext.split_frontmatter("---\nid: x\nstill going")


class TestParse(unittest.TestCase):
    def test_agentic_ok(self):
        e = ext.parse_extension(AGENTIC, source="design-parity.md")
        self.assertEqual(e.id, "design-parity")
        self.assertEqual(e.slot, "tester")
        self.assertTrue(e.anchorable)
        self.assertIn("Figma", e.body)

    def test_command_ok(self):
        e = ext.parse_extension(COMMAND, source="golden.md")
        self.assertEqual(e.kind, "command")
        self.assertEqual(e.mode, "deterministic")
        self.assertEqual(e.run, "flutter test --tags golden")
        self.assertEqual(e.required_capabilities, ())
        self.assertEqual(e.optional_capabilities, ())

    def test_mode_parse(self):
        text = """---
id: hybrid-check
slot: after:ci
kind: command
mode: hybrid
run: ./tools/report
---
"""
        e = ext.parse_extension(text, source="hybrid.md")
        self.assertEqual(e.mode, "hybrid")

    def test_invalid_mode_rejected(self):
        text = "---\nid: x\nslot: after:ci\nkind: command\nmode: maybe\nrun: y\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("invalid mode", str(c.exception))

    def test_capabilities_parse(self):
        text = """---
id: external-report
slot: tester
kind: command
run: ./tools/report
required_capabilities: [shell]
optional_capabilities: [browser]
---
"""
        e = ext.parse_extension(text, source="report.md")
        self.assertEqual(e.required_capabilities, ("shell",))
        self.assertEqual(e.optional_capabilities, ("browser",))

    def test_unknown_capability_rejected(self):
        text = """---
id: bad
slot: tester
kind: command
run: ./tools/report
required_capabilities: [bogus]
---
"""
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="bad.md")
        self.assertIn("unknown capability", str(c.exception))

    def test_hard_gate_ok_in_pre_merge(self):
        e = ext.parse_extension(HARD_GATE, source="g.md")
        self.assertEqual(e.on_fail, "block")

    def test_guard_can_block(self):
        text = """---
id: guard-check
slot: guard
kind: command
on_fail: block
run: ./tools/guard
---
"""
        e = ext.parse_extension(text, source="guard.md")
        self.assertEqual(e.slot, "guard")
        self.assertEqual(e.on_fail, "block")

    def test_missing_frontmatter(self):
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension("no spec here", source="x.md")
        self.assertIn("missing frontmatter", str(c.exception))

    def test_unknown_slot(self):
        text = "---\nid: x\nslot: bogus\nkind: command\nrun: y\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("unknown slot", str(c.exception))

    def test_slot_mismatch_with_registration(self):
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(COMMAND, source="x.md", expected_slot="pre-merge")
        self.assertIn("does not match", str(c.exception))

    def test_block_only_in_blocking_slots(self):
        text = "---\nid: x\nslot: after:test\nkind: command\non_fail: block\nrun: y\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("only allowed in blocking slots", str(c.exception))

    def test_command_requires_run(self):
        text = "---\nid: x\nslot: tester\nkind: command\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("requires a 'run'", str(c.exception))

    def test_agentic_requires_prompt_or_body(self):
        text = "---\nid: x\nslot: tester\nkind: agentic\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("requires a 'prompt'", str(c.exception))

    def test_invalid_kind(self):
        text = "---\nid: x\nslot: tester\nkind: magic\nrun: y\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("invalid kind", str(c.exception))

    def test_missing_id(self):
        text = "---\nslot: tester\nkind: command\nrun: y\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("missing 'id'", str(c.exception))

    def test_missing_slot(self):
        text = "---\nid: x\nkind: command\nrun: y\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("missing 'slot'", str(c.exception))

    def test_invalid_on_fail(self):
        text = "---\nid: x\nslot: tester\nkind: command\non_fail: maybe\nrun: y\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("invalid on_fail", str(c.exception))

    def test_non_dict_frontmatter(self):
        text = "---\n- a\n- b\n---\nbody"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("missing frontmatter", str(c.exception))


class TestTimeoutFrontmatter(unittest.TestCase):
    """A single gate that is legitimately slower than the rest (#622)."""

    def test_absent_timeout_defaults_to_none(self):
        # None ⇒ inherit knobs.gate_timeout_s at plan time.
        self.assertIsNone(ext.parse_extension(COMMAND, source="golden.md").timeout)

    def test_timeout_is_parsed(self):
        text = "---\nid: x\nslot: tester\nkind: command\nrun: y\ntimeout: 3600\n---\n"
        self.assertEqual(ext.parse_extension(text, source="x.md").timeout, 3600)

    def test_rejects_zero_and_negative(self):
        for value in (0, -30):
            text = f"---\nid: x\nslot: tester\nkind: command\nrun: y\ntimeout: {value}\n---\n"
            with self.assertRaises(ext.ExtensionError) as c:
                ext.parse_extension(text, source="x.md")
            self.assertIn("invalid timeout", str(c.exception))

    def test_rejects_non_integer(self):
        for value in ("soon", "60.5"):
            text = f"---\nid: x\nslot: tester\nkind: command\nrun: y\ntimeout: {value}\n---\n"
            with self.assertRaises(ext.ExtensionError) as c:
                ext.parse_extension(text, source="x.md")
            self.assertIn("invalid timeout", str(c.exception))

    def test_rejects_boolean(self):
        # bool is an int subclass — `timeout: true` is a typo, not a limit.
        text = "---\nid: x\nslot: tester\nkind: command\nrun: y\ntimeout: true\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("invalid timeout", str(c.exception))

    def test_rejects_timeout_on_an_agentic_piece(self):
        text = "---\nid: x\nslot: tester\nkind: agentic\nprompt: p\ntimeout: 60\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("only applies to a 'command' extension", str(c.exception))

    def test_both_timeout_problems_reported_together(self):
        # Every validator in parse_extension accumulates, so the author sees the value
        # problem and the kind problem in one pass rather than one per fix.
        text = "---\nid: x\nslot: tester\nkind: agentic\nprompt: p\ntimeout: 0\n---\n"
        with self.assertRaises(ext.ExtensionError) as c:
            ext.parse_extension(text, source="x.md")
        self.assertIn("invalid timeout", str(c.exception))
        self.assertIn("only applies to a 'command' extension", str(c.exception))


def _config_with(tester=(), pre_merge=()):
    data = {
        "extends": "keel",
        "core_version": "^0.1",
        "base_branch": "main",
        "knobs": {"build_gate_cmd": "make test"},
        "extensions": {"tester": list(tester), "pre-merge": list(pre_merge)},
        "extensions_dir": ".keel/extensions",
    }
    return cfg.parse_config(data)


class TestLoad(unittest.TestCase):
    def test_happy_path(self):
        with tempfile.TemporaryDirectory() as d:
            ed = Path(d) / ".keel" / "extensions"
            ed.mkdir(parents=True)
            (ed / "design-parity.md").write_text(AGENTIC, encoding="utf-8")
            (ed / "gate.md").write_text(HARD_GATE, encoding="utf-8")
            config = _config_with(tester=["design-parity.md"], pre_merge=["gate.md"])
            loaded, problems = ext.load_extensions(config, d)
            self.assertEqual(problems, [])
            self.assertEqual(loaded["tester"][0].id, "design-parity")
            self.assertEqual(loaded["pre-merge"][0].on_fail, "block")

    def test_strict_raises_on_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            config = _config_with(tester=["nope.md"])
            with self.assertRaises(ext.ExtensionError):
                ext.load_extensions(config, d)

    def test_failsoft_returns_problems(self):
        with tempfile.TemporaryDirectory() as d:
            config = _config_with(tester=["nope.md"])
            loaded, problems = ext.load_extensions(config, d, strict=False)
            self.assertEqual(loaded["tester"], [])
            self.assertEqual(len(problems), 1)
            self.assertIn("cannot read", problems[0])

    def test_failsoft_skips_malformed_keeps_good(self):
        with tempfile.TemporaryDirectory() as d:
            ed = Path(d) / ".keel" / "extensions"
            ed.mkdir(parents=True)
            (ed / "good.md").write_text(COMMAND, encoding="utf-8")
            (ed / "bad.md").write_text("no frontmatter", encoding="utf-8")
            config = _config_with(tester=["good.md", "bad.md"])
            loaded, problems = ext.load_extensions(config, d, strict=False)
            self.assertEqual(len(loaded["tester"]), 1)
            self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
