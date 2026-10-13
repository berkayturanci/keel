"""The `agy` worked example in docs/keel/cli.md names only flags the parser has (#1517)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from keel import cli

DOC = Path(__file__).resolve().parents[1] / "docs" / "keel" / "cli.md"
_START = "### Worked example: `agy` as a reviewer and as an implementer"
_END = "## `keel delegate run … --detach`"


def _section() -> str:
    text = DOC.read_text(encoding="utf-8")
    return text[text.index(_START) : text.index(_END)]


def _flags(subcommand: str) -> set[str]:
    parser = cli.build_parser()
    action = next(a for a in parser._subparsers._group_actions)  # noqa: SLF001
    delegate = action.choices["delegate"]
    sub = next(a for a in delegate._subparsers._group_actions)  # noqa: SLF001
    return {o for a in sub.choices[subcommand]._actions for o in a.option_strings}


class AgyDelegateDocsTest(unittest.TestCase):
    def test_every_documented_run_flag_exists(self):
        section = _section()
        lines = [
            ln for ln in section.splitlines() if "keel delegate run" in ln or ln.startswith("  --")
        ]
        used = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", "\n".join(lines)))
        used -= {
            "--sandbox",
            "--dangerously-skip-permissions",
            "--input-format",
            "--output-format",
            "--model",
            "--add-dir",
            "--print-timeout",
        }
        self.assertTrue(used)
        self.assertEqual(used - _flags("run"), set())

    def test_wait_and_status_flags_exist(self):
        section = _section()
        for sub in ("wait", "status"):
            for line in (
                ln for ln in section.splitlines() if ln.startswith(f"keel delegate {sub}")
            ):
                for flag in re.findall(r"--[a-z][a-z-]*", line):
                    self.assertIn(flag, _flags(sub), line)

    def test_the_documented_policy_matches_the_planner(self):
        from keel import delegate, providers

        agy = next(p for p in providers.builtin_providers() if p.name == "agy")
        review = delegate.plan_run(agy, "review", "p.md", cwd="/abs/wt")
        impl = delegate.plan_run(agy, "implement", "p.md", cwd="/abs/wt")
        self.assertIn("--sandbox", review.argv)
        self.assertTrue(review.read_only and review.read_only_backed)
        self.assertNotIn("--sandbox", impl.argv)
        self.assertFalse(impl.read_only or impl.read_only_backed)
        for plan in (review, impl):
            self.assertIn("--dangerously-skip-permissions", plan.argv)
            self.assertIn("--add-dir", plan.argv)
        section = _section()
        for flag in (
            "--sandbox",
            "--dangerously-skip-permissions",
            "--add-dir",
            *delegate.AGY_STREAM_ARGS,
        ):
            self.assertIn(flag, section)


if __name__ == "__main__":
    unittest.main()
