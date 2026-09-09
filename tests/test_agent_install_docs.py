"""The README's install boxes and `docs/keel/install.md` name the same agents.

`docs/keel/install.md` exists because the README churns and a release note cannot link
into it stably. Two documents covering one subject is how #781's `@v1` came about:
three pages agreed with each other and none of them agreed with the repository. So
the pair is checked against each other *and* against the surfaces they describe —
an agent added to one page and not the other is the drift this file refuses, and a
box with no update instructions is the half of #783 that is not guessable by
analogy.

Read as text, with no Markdown parser: these are documents, and the properties
worth pinning — which agents appear, whether a badge resolves, whether a box has
both halves — are all visible in the text. A parser would add a dependency to
assert things a regex already answers.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
INSTALL_DOC = REPO_ROOT / "docs" / "keel" / "install.md"
PLUGIN_DOC = REPO_ROOT / "docs" / "keel" / "plugin.md"

#: The anchor each agent's box and section carries. The README puts it in the
#: `<summary>` so the badge above links into the collapsed box; `install.md` puts
#: it under the heading. One spelling, so a badge cannot point at nothing.
ANCHOR = re.compile(r'<a id="([a-z0-9-]+)"></a>')

#: `[![Name](badge-url)](#anchor)` — the badge row that doubles as the index.
BADGE = re.compile(r"\[!\[[^\]]+\]\([^)]+\)\]\(#([a-z0-9-]+)\)")

#: The agents both documents must cover. Named here on purpose: this is the one
#: fact the two pages cannot derive from each other, and adding a fifth agent
#: should be a deliberate edit to this line rather than something a page silently
#: drops.
AGENTS = ("claude-code", "codex", "antigravity", "cursor")


def anchors(text: str) -> list[str]:
    return ANCHOR.findall(text)


class TheDocumentsWereRead(unittest.TestCase):
    """Vacuity: an empty read satisfies every set comparison below."""

    @classmethod
    def setUpClass(cls):
        cls.readme = README.read_text(encoding="utf-8")
        cls.install = INSTALL_DOC.read_text(encoding="utf-8")

    def test_both_files_exist_and_are_not_empty(self):
        self.assertGreater(len(self.readme.splitlines()), 100)
        self.assertGreater(len(self.install.splitlines()), 50)

    def test_the_anchor_pattern_matches_what_the_files_write(self):
        self.assertGreaterEqual(len(anchors(self.readme)), len(AGENTS))
        self.assertGreaterEqual(len(anchors(self.install)), len(AGENTS))


class EveryAgentIsInBothDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = README.read_text(encoding="utf-8")
        cls.install = INSTALL_DOC.read_text(encoding="utf-8")

    def test_the_readme_boxes_cover_every_agent(self):
        self.assertLessEqual(set(AGENTS), set(anchors(self.readme)))

    def test_the_install_page_covers_every_agent(self):
        self.assertLessEqual(set(AGENTS), set(anchors(self.install)))

    def test_the_two_documents_cover_the_same_agents(self):
        """The drift this file exists for: one page gains an agent, the other does not."""
        self.assertEqual(
            set(anchors(self.readme)) & set(AGENTS),
            set(anchors(self.install)) & set(AGENTS),
        )

    def test_every_badge_points_at_an_anchor_that_exists(self):
        """context-mode's badges are `href="#"` and go nowhere; these must not.

        A badge row is only an index if clicking a badge lands on that agent's box.
        """
        targets = BADGE.findall(self.readme)
        self.assertEqual(set(targets), set(AGENTS), targets)
        for target in targets:
            with self.subTest(badge=target):
                self.assertIn(f'<a id="{target}"></a>', self.readme)


class EveryBoxSaysHowToUpdate(unittest.TestCase):
    """Install is guessable by analogy; update is not, and it differs on all four.

    This is the half of #783 a reader cannot work out for themselves: `claude
    plugin install` is a no-op on an installed plugin, Cursor has no install
    command at all, and `agy install` overwrites in place. A box with only an
    install command sends somebody to a version they cannot move off.
    """

    @classmethod
    def setUpClass(cls):
        cls.readme = README.read_text(encoding="utf-8")
        cls.install = INSTALL_DOC.read_text(encoding="utf-8")

    def sections(self, text: str) -> dict[str, str]:
        """Each agent's prose: from its anchor to the next one, or to the end."""
        found = {}
        positions = [(m.group(1), m.start()) for m in ANCHOR.finditer(text)]
        for index, (name, start) in enumerate(positions):
            end = positions[index + 1][1] if index + 1 < len(positions) else len(text)
            found[name] = text[start:end]
        return found

    def test_the_split_returns_a_body_per_agent(self):
        for text, where in ((self.readme, "README.md"), (self.install, "docs/keel/install.md")):
            with self.subTest(document=where):
                bodies = self.sections(text)
                for agent in AGENTS:
                    self.assertGreater(len(bodies[agent]), 120, f"{where}: {agent}")

    def test_every_agent_box_has_an_install_and_an_update(self):
        for text, where in ((self.readme, "README.md"), (self.install, "docs/keel/install.md")):
            bodies = self.sections(text)
            for agent in AGENTS:
                with self.subTest(document=where, agent=agent):
                    body = bodies[agent].lower()
                    self.assertIn("**install", body, f"{where}: {agent} has no Install")
                    self.assertIn("**update", body, f"{where}: {agent} has no Update")


class ThePluginPageAgreesWithTheInstallPage(unittest.TestCase):
    """`plugin.md` is titled for one agent; the install page covers four.

    `docs/keel/plugin.md` describes *what the plugin contains*. It opened with
    "keel as a Claude Code plugin" and gave only Claude's commands, which is how a
    reader concluded the plugin was a Claude-only artifact. The two pages have to
    point at each other or the four-agent story lives in one of them and the
    contents in the other.
    """

    def test_the_plugin_page_points_at_the_install_page(self):
        self.assertIn("install.md", PLUGIN_DOC.read_text(encoding="utf-8"))

    def test_the_install_page_points_back(self):
        self.assertIn("plugin.md", INSTALL_DOC.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
