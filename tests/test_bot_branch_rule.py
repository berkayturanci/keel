"""The bot-branch rule is stated, and its two copies name the same prefixes (#1127).

A pull-request branch opened by an automation is a read-only input: the bot pushes from
its own checkout, so its next push replaces the branch and silently reverts whatever
landed in between.

The rule exists because it happened. On #1125 a review found four defects in a Palette
change and the fixes were pushed onto the `jules-…` branch; while the last gate round ran,
the bot pushed *Acknowledge reviewer verdicts* and reverted all of them — 222 deletions,
including the 189-line test class that would have caught the worst of it (a `var` above
`"use strict"`, which ends the Directive Prologue and un-stricts a 420-line IIFE). The
work was re-landed as #1126.

The rule is written twice on purpose: `CONTRIBUTING.md` for a person and `AGENTS.md` for
the agents that actually open these pull requests. Two statements of one rule can
disagree, so the prefix list is compared rather than trusted — that is the whole of what
this file mechanically enforces. The prose around it is not asserted word for word; a
test that pins wording gets edited into agreement rather than read.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
AGENTS = REPO_ROOT / "AGENTS.md"

#: Every automation that opens pull requests here. `cursor` is deliberately absent:
#: those branches are driven from a working copy by a person, which is the distinction
#: the rule turns on — who holds the only copy, not who wrote the code.
EXPECTED_PREFIXES = frozenset(
    {"jules", "bolt", "palette", "sentinel", "dependabot", "copilot", "renovate"}
)

#: The backticked tokens in the sentence that lists them.
_PREFIX_LINE = re.compile(r"`(jules|bolt|palette|sentinel|dependabot|copilot|renovate)`")


class TheRuleIsStatedInBothPlaces(unittest.TestCase):
    def test_contributing_has_the_section(self):
        text = CONTRIBUTING.read_text(encoding="utf-8")

        self.assertIn("## Bot-owned branches are read-only", text)
        self.assertIn("read-only input", text)

    def test_agents_md_states_it_too(self):
        """The file the bots themselves are pointed at."""
        text = AGENTS.read_text(encoding="utf-8")

        self.assertIn("read-only input", text)

    def test_agents_md_links_the_full_rule(self):
        anchor = "CONTRIBUTING.md#bot-owned-branches-are-read-only"
        self.assertIn(anchor, AGENTS.read_text(encoding="utf-8"))

    def test_the_anchor_that_link_points_at_exists(self):
        """A heading rename would leave the link pointing at nothing."""
        heading = "## Bot-owned branches are read-only"
        slug = heading.lstrip("# ").lower().replace(" ", "-")

        self.assertIn(heading, CONTRIBUTING.read_text(encoding="utf-8"))
        self.assertEqual(slug, "bot-owned-branches-are-read-only")


class BothCopiesNameTheSamePrefixes(unittest.TestCase):
    """Two statements of one rule are two rules that can disagree."""

    def _prefixes(self, path: Path) -> set[str]:
        return {m.group(1) for m in _PREFIX_LINE.finditer(path.read_text(encoding="utf-8"))}

    def test_contributing_lists_every_automation(self):
        self.assertEqual(self._prefixes(CONTRIBUTING), set(EXPECTED_PREFIXES))

    def test_agents_lists_every_automation(self):
        self.assertEqual(self._prefixes(AGENTS), set(EXPECTED_PREFIXES))

    def test_a_person_driven_prefix_is_not_covered(self):
        """`claude/…`, `codex/…`, `cursor/…` are a working copy, not a bot's only copy."""
        self.assertNotIn("cursor", EXPECTED_PREFIXES)
        for name in ("claude/", "codex/", "cursor/"):
            with self.subTest(prefix=name):
                self.assertIn(name, CONTRIBUTING.read_text(encoding="utf-8"))

    def test_the_incident_is_named_so_the_rule_is_not_folklore(self):
        """A rule with no cost attached is one the next reader talks themselves out of."""
        text = CONTRIBUTING.read_text(encoding="utf-8")

        self.assertIn("1125", text)
        self.assertIn("1126", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
