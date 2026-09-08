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

#: The clause that lists the prefixes, in either file: it opens at the first backticked
#: `jules` and closes at the "spelling" both sentences end on.
#:
#: Deliberately NOT an alternation of the expected names. The first cut was
#: ``re.compile(r"`(jules|bolt|…|renovate)`")``, which can only ever match the seven it
#: already knows — so it caught a *dropped* prefix and was blind to an *added* one, while
#: this file claimed to assert that `cursor` is not on the list. A closed pattern cannot
#: make that assertion. Found by the gate review of #1128.
_PREFIX_CLAUSE = re.compile(r"`jules`.*?spelling", re.S)
_BACKTICKED = re.compile(r"`([a-z][a-z0-9-]*)`")


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

    def test_the_anchor_resolves_to_the_heading_that_is_there(self):
        """Derived from the file, so a rename breaks it rather than sliding past.

        The first cut slugified a *hardcoded* heading and substring-checked the file for
        it — so `## Bot-owned branches are read-only (rule)` kept the test green while the
        fragment `AGENTS.md` links to no longer resolved.
        """
        headings = [
            line
            for line in CONTRIBUTING.read_text(encoding="utf-8").splitlines()
            if line.startswith("## ") and "read-only" in line
        ]
        self.assertEqual(len(headings), 1, f"expected one section heading, saw {headings}")

        slug = re.sub(r"[^a-z0-9]+", "-", headings[0][3:].strip().lower()).strip("-")
        link = re.search(r"CONTRIBUTING\.md#([a-z0-9-]+)", AGENTS.read_text(encoding="utf-8"))

        self.assertIsNotNone(link, "AGENTS.md does not link the section")
        self.assertEqual(link.group(1), slug)


class BothCopiesNameTheSamePrefixes(unittest.TestCase):
    """Two statements of one rule are two rules that can disagree."""

    def _prefixes(self, path: Path) -> set[str]:
        """Every backticked token in the clause, whatever it is."""
        clause = _PREFIX_CLAUSE.search(path.read_text(encoding="utf-8"))
        self.assertIsNotNone(clause, f"no prefix clause in {path.name}")
        return set(_BACKTICKED.findall(clause.group(0)))

    def test_contributing_lists_every_automation(self):
        self.assertEqual(self._prefixes(CONTRIBUTING), set(EXPECTED_PREFIXES))

    def test_agents_lists_every_automation(self):
        self.assertEqual(self._prefixes(AGENTS), set(EXPECTED_PREFIXES))

    def test_a_person_driven_prefix_is_not_covered(self):
        """`claude/…`, `codex/…`, `cursor/…` are a working copy, not a bot's only copy.

        Read off the documents, not off `EXPECTED_PREFIXES` — asserting `cursor` is
        absent from a constant this file writes proves nothing about what the rule says.
        """
        for path in (CONTRIBUTING, AGENTS):
            with self.subTest(file=path.name):
                self.assertNotIn("cursor", self._prefixes(path))
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
