"""A batch runner's staffing has to survive the handoff to its children (#1017).

Before this, `work-block`, `overnight` and `swarm` passed only
`operator_consent.delegated_agent_scope` down. An operator who launched a block with
`--delegate codex --effort high` got children that re-resolved from config and ran the
default team: the choice reached the parent and died there, silently, and the session
report said nothing about it.

Propagation is prose plus a flag list plus a parser, in three places each, so this sweep
holds the three together — every flag core publishes is one the command really accepts and
one the adapter really tells its children about. Every generated surface is swept, not just
the source, because the generated files are what an agent actually reads.
"""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path

from keel import cli, workblock

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every file an agent could read a handoff instruction out of, per adapter name.
_SURFACE_PATTERNS = (
    "src/keel/adapters/commands/{name}.md",
    "commands/{name}.md",
    ".claude/commands/keel/{name}.md",
    ".agents/skills/keel-{name}/SKILL.md",
)

#: Batch commands: they queue work and hand each item to a child `/keel:ship`.
_BATCH_ADAPTERS = ("work-block", "overnight")

#: The CLI subcommands that must accept what the adapters say they accept.
_STAFFED_COMMANDS = ("work-block", "overnight", "swarm-plan", "swarm-run", "swarm-land")


def _surfaces(name: str) -> list[Path]:
    return [
        path
        for pattern in _SURFACE_PATTERNS
        for path in [REPO_ROOT / pattern.format(name=name)]
        if path.exists()
    ]


def _parser_flags(command: str) -> set[str]:
    """Every option string the built parser accepts for one subcommand."""
    parser = cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions  # noqa: SLF001 - argparse exposes no public accessor
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )
    return {
        option
        for action in subparsers.choices[command]._actions  # noqa: SLF001
        for option in action.option_strings
    }


class StaffingFlagsReachTheChildren(unittest.TestCase):
    def test_the_sweep_has_files_to_sweep(self):
        """Keeps every rule below from passing on a tree it cannot read."""
        for name in (*_BATCH_ADAPTERS, "swarm"):
            with self.subTest(adapter=name):
                self.assertEqual(len(_surfaces(name)), len(_SURFACE_PATTERNS))

    def test_every_staffed_command_accepts_every_published_flag(self):
        """The contract names the flags; the parser has to be the same set.

        A published flag the command rejects is worse than a missing one: the adapter is
        told to pass it, the run dies on `unrecognized arguments`, and the failure reads
        as the operator's typo.
        """
        for command in _STAFFED_COMMANDS:
            flags = _parser_flags(command)
            for flag in workblock.DELEGATION_FLAGS:
                with self.subTest(command=command, flag=flag):
                    self.assertIn(flag, flags)

    def test_every_batch_adapter_names_every_flag_it_must_hand_down(self):
        for name in _BATCH_ADAPTERS:
            for path in _surfaces(name):
                text = path.read_text(encoding="utf-8")
                for flag in workblock.DELEGATION_FLAGS:
                    with self.subTest(path=str(path.relative_to(REPO_ROOT)), flag=flag):
                        self.assertIn(flag, text)

    def test_every_batch_adapter_carries_the_child_handoff_template(self):
        for name in _BATCH_ADAPTERS:
            for path in _surfaces(name):
                with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                    self.assertIn(
                        workblock.CHILD_HANDOFF_TEMPLATE,
                        path.read_text(encoding="utf-8"),
                    )

    def test_every_batch_adapter_records_the_effective_staffing_in_its_report(self):
        """A block whose report does not say which team ran it cannot be audited later."""
        for name in _BATCH_ADAPTERS:
            for path in _surfaces(name):
                text = path.read_text(encoding="utf-8").lower()
                with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                    self.assertIn("staffing", text)
                    self.assertIn("session report", text)

    def test_the_swarm_adapter_states_the_three_level_hierarchy(self):
        for path in _surfaces("swarm"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertIn("one team lead per cluster", text.lower())
                self.assertIn("worker status record", text.lower())
                # The coordinator clusters, launches leads and lands waves — nothing else.
                self.assertIn("You do not implement", text)

    def test_the_swarm_adapter_forbids_re_deriving_the_resolved_team(self):
        """Two agents deriving one team is the disagreement #1014 opened against."""
        for path in _surfaces("swarm"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertIn("do not re-derive either", text)
                self.assertIn("assignment.warnings", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
