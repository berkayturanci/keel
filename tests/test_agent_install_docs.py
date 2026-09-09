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
        """The drift this file exists for: one page gains an agent, the other does not.

        The **whole** anchor set on each side, not the intersection with `AGENTS`.
        Intersecting was a tautology: the two tests above already assert
        `AGENTS <= anchors`, so `anchors & AGENTS` is `AGENTS` on both sides and
        the comparison reduced to `AGENTS == AGENTS`. A fifth agent added to one
        page and not the other — exactly the drift named in the docstring — passed.
        """
        self.assertEqual(set(anchors(self.readme)), set(anchors(self.install)))

    def test_a_fifth_agent_on_one_page_only_is_caught(self):
        """The mutation the intersecting version survived."""
        readme = self.readme + '\n<a id="zed"></a>\n'
        self.assertNotEqual(set(anchors(readme)), set(anchors(self.install)))

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
        """Each agent's prose: from its anchor to the next one, or to its own end.

        "Or to the end of the file" was wrong for the **last** agent. `cursor` is
        last in the README, so its section ran to the end of the document and any
        later `**update` — in eight hundred lines of unrelated prose — would have
        satisfied the check for a box that had none. The last section stops at the
        `</details>` that closes its box, or at the next horizontal rule on a page
        that does not use them.
        """
        found = {}
        positions = [(m.group(1), m.start()) for m in ANCHOR.finditer(text)]
        for index, (name, start) in enumerate(positions):
            if index + 1 < len(positions):
                end = positions[index + 1][1]
            else:
                tail = text[start:]
                for closer in ("</details>", "\n---\n", "\n## "):
                    at = tail.find(closer)
                    if at != -1:
                        end = start + at
                        break
                else:
                    end = len(text)
            found[name] = text[start:end]
        return found

    def test_the_last_section_stops_at_its_own_box(self):
        """Vacuity, on the one section that had none: it used to run to the file end."""
        for text, where in ((self.readme, "README.md"), (self.install, "docs/keel/install.md")):
            with self.subTest(document=where):
                last = self.sections(text)[AGENTS[-1]]
                self.assertLess(len(last), 2000, f"{where}: the last section runs on")

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


#: Every page that gives somebody an install instruction. The sibling change in
#: ai-jury had the stale claim fixed in one page and left standing in a third, and
#: a link into a renamed heading go quietly dead. Both are checked here.
INSTRUCTION_PAGES = (
    "README.md",
    "docs/keel/install.md",
    "docs/keel/plugin.md",
    "docs/keel/editors.md",
)

#: A markdown link into another document's anchor, as `](target.md#anchor)`.
#: Case-insensitive on the filename: matching only lowercase made
#: `](../../README.md#install)` — this page's own cross-links — invisible to the
#: check that exists to catch exactly that kind of link.
DOC_ANCHOR_LINK = re.compile(r"\]\((?:\.\.?/)*([A-Za-z0-9./_-]+\.md)#([a-z0-9-]+)\)")


class EveryCrossDocumentAnchorResolves(unittest.TestCase):
    """A link into a heading breaks silently when the heading is reworded.

    That is not hypothetical: renaming a heading in the sibling repository left a
    link pointing at the old slug, and nothing said so. Anchors are cheap to check
    and the failure mode is a reader landing at the top of a page wondering which
    part they were sent to.
    """

    def pages(self):
        return {name: (REPO_ROOT / name).read_text(encoding="utf-8") for name in INSTRUCTION_PAGES}

    def test_the_pages_were_read(self):
        for name, text in self.pages().items():
            with self.subTest(document=name):
                self.assertGreater(len(text.splitlines()), 20)

    def test_the_pattern_sees_the_links_that_are_there(self):
        """Vacuity: a pattern matching nothing passes the check below."""
        found = DOC_ANCHOR_LINK.findall((REPO_ROOT / "docs" / "keel" / "install.md").read_text())
        self.assertTrue(any(target.endswith("README.md") for target, _ in found), found)

    def test_every_anchor_a_page_links_to_exists(self):
        for name, text in self.pages().items():
            base = (REPO_ROOT / name).parent
            for target, anchor in DOC_ANCHOR_LINK.findall(text):
                path = (base / target).resolve()
                if not path.is_file():
                    continue
                with self.subTest(document=name, link=f"{target}#{anchor}"):
                    body = path.read_text(encoding="utf-8")
                    headings = {
                        re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower()).replace(
                            " ", "-"
                        )
                        for line in body.splitlines()
                        if line.startswith("#")
                    }
                    self.assertTrue(
                        f'<a id="{anchor}"></a>' in body or anchor in headings,
                        f"{name} links to {target}#{anchor}, which is not there",
                    )


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
