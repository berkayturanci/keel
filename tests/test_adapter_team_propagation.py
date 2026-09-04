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

from keel import cli, swarm, team, workblock

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
#:
#: ``ship`` and ``plan`` are in this list because they are the *children*. The published
#: `child_args` named `--effort` and `--team`, the adapters told every batch to append
#: them verbatim to `/keel:ship`, and `keel ship`'s own parser answered
#: `unrecognized arguments: --effort high --team hardening` — a promise in the contract
#: the parser could not keep, and the sweep passed because it only ever asked the parents.
_STAFFED_COMMANDS = (
    "work-block",
    "overnight",
    "swarm-plan",
    "swarm-run",
    "swarm-land",
    "ship",
    "plan",
)


def _surfaces(name: str) -> list[Path]:
    return [
        path
        for pattern in _SURFACE_PATTERNS
        for path in [REPO_ROOT / pattern.format(name=name)]
        if path.exists()
    ]


def _flags(argv) -> set[str]:
    """The option strings in an emitted argv fragment."""
    return {token for token in argv if token.startswith("--")}


def _fully_staffed_assignment() -> dict:
    """An assignment with every seat and both bench selectors filled in.

    Built through the real resolver rather than hand-written, so a field renamed in
    `keel.team` fails here instead of quietly shrinking what this sweep checks.
    """
    return team.resolve_assignment(
        team.parse_team(
            {
                "implement": {"default": {"provider": "codex"}},
                "review": {"default": [{"provider": "claude"}]},
            }
        ),
        tier=2,
        role="core",
        difficulty="hard",
        team_profile="night-shift",
        effort="high",
    )


def _fully_staffed_child_args() -> tuple[str, ...]:
    """What a work block appends when the operator passed every staffing flag."""
    return workblock.child_ship_args(
        delegate="codex",
        review_delegates=("claude",),
        effort="high",
        team_profile="night-shift",
        reviewer_override=2,
    )


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

    def test_both_handoffs_carry_the_whole_bench_choice(self):
        """Pinned, not derived — and deliberately so.

        The sweep below asks "is everything the emitter emits named in the adapter",
        which cannot notice an emitter that stopped emitting something: dropping a flag
        shrinks the expectation too and the check goes quiet. This is the other
        direction. The four flags below are the ones that carry *which bench ran this
        change*; a handoff missing any of them silently re-staffs the child from config.

        The two emitters differ past that, and legitimately: a work block passes the
        operator's `--reviewers` through, while a lead lets the child derive the count
        from its own tier; a lead resolved a per-cluster `--role`, while a work block
        has not looked at the issue yet.
        """
        bench_choice = {"--delegate", "--review-delegate", "--effort", "--team"}
        lead = _flags(swarm.ship_handoff_args(_fully_staffed_assignment()))
        block = _flags(_fully_staffed_child_args())

        self.assertEqual(sorted(bench_choice - lead), [])
        self.assertEqual(sorted(bench_choice - block), [])
        self.assertIn("--role", lead)
        self.assertIn("--reviewers", block)
        self.assertEqual(sorted(set(workblock.DELEGATION_FLAGS) - (lead | block)), [])

    def test_each_adapter_names_every_flag_its_own_core_helper_emits(self):
        """The two families disagreed about the child handoff: `work-block`/`overnight`
        told children to append the full set while `swarm` named only
        `--delegate`/`--review-delegate`/`--role`, so a lead and a work block handed the
        same child two different teams.

        The expected set is *derived from the emitting code*, not written down here. A
        list in a test drifts from the code the same way the two adapters drifted from
        each other — and it was a hard-coded expectation that let the contradiction pass.
        """
        expected = {
            "swarm": _flags(swarm.ship_handoff_args(_fully_staffed_assignment())),
            **{name: _flags(_fully_staffed_child_args()) for name in _BATCH_ADAPTERS},
        }
        for name, flags in expected.items():
            self.assertTrue(flags, f"{name}: the helper emitted no flags to check")
            for path in _surfaces(name):
                text = path.read_text(encoding="utf-8")
                for flag in sorted(flags):
                    with self.subTest(path=str(path.relative_to(REPO_ROOT)), flag=flag):
                        self.assertIn(flag, text)

    def test_every_flag_the_helpers_emit_is_one_the_child_parses(self):
        """The end of the chain: whatever core hands a child, `keel ship` must accept."""
        emitted = _flags(swarm.ship_handoff_args(_fully_staffed_assignment())) | _flags(
            _fully_staffed_child_args()
        )
        ship_flags = _parser_flags("ship")

        self.assertEqual(sorted(emitted - ship_flags), [])

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
