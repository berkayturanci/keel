"""The README's install boxes and `docs/keel/install.md` name the same agents.

`docs/keel/install.md` exists because the README churns and a release note cannot
link into it stably. Two documents covering one subject is how ai-jury#781's `@v1`
came about: three pages agreed with each other and none of them agreed with the
repository.

**What these tests check is structure, not content.** Which agents appear on each
page, that every badge resolves to an anchor that exists, that every box carries
an Update as well as an Install, that every page printing an install command
points at the page that owns them, and that every cross-document anchor is real.
They do **not** verify a command against the tool it names — both pages could
agree on a wrong marketplace name and stay green. That check would have to run the
CLIs; the commands here were measured by hand instead, with the date and the
version written into the pages themselves.

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

#: An explicit anchor. The README needs them: a `<details>` box has no heading for
#: a slug to come from, and the anchor sits *before* the box because navigating to
#: an id inside `<summary>` scrolls to it without expanding it. `install.md` has
#: headings and uses those.
ANCHOR = re.compile(r'<a id="([a-z0-9-]+)"></a>')

#: `[![Name](badge-url)](#anchor)` — the badge row that doubles as the index.
BADGE = re.compile(r"\[!\[[^\]]+\]\([^)]+\)\]\(#([a-z0-9-]+)\)")

#: A `- [Claude Code](#claude-code)` line in a page's own Contents list.
CONTENTS_ENTRY = re.compile(r"^- \[[^\]]+\]\(#([a-z0-9-]+)\)", re.M)

#: The agents both documents must cover. Named here on purpose: this is the one
#: fact the two pages cannot derive from each other, and adding a fifth agent
#: should be a deliberate edit to this line rather than something a page silently
#: drops.
AGENTS = ("claude-code", "codex", "antigravity", "cursor")

#: The install page, as the failure messages name it.
INSTALL_NAME = "docs/keel/install.md"


#: A markdown heading, whose GitHub slug is an anchor as real as an explicit one.
HEADING = re.compile(r"^#{2,3} +(.+?)\s*$", re.M)


def heading_slug(title: str) -> str:
    """GitHub's heading anchor, near enough for the names these pages use."""
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).strip().replace(" ", "-")


def anchors(text: str) -> list[str]:
    """Every id this document offers — explicit anchors and heading slugs alike.

    Explicit anchors are what the README needs, because a `<details>` box has no
    heading. The install page has headings, and writing an `<a id>` under one gave
    the rendered page **two** elements with the same id — a hazard for anything
    that resolves an anchor by lookup. A heading is an anchor; both count.
    """
    return ANCHOR.findall(text) + [heading_slug(title) for title in HEADING.findall(text)]


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

    def declared(self, text: str) -> set[str]:
        """The agents a document *declares* — its badge targets, or its contents list.

        Both are explicit lists an author edits when adding an agent, which is what
        makes them comparable. Two earlier versions of this test compared anchor
        sets and then filtered them back down to `AGENTS`, which is the tautology
        it is named after wearing a different filter: a fifth host added to one
        page only was invisible both times. Nothing is filtered here.
        """
        badges = set(BADGE.findall(text))
        if badges:
            return badges
        return {m.group(1) for m in CONTENTS_ENTRY.finditer(text)}

    def test_each_document_declares_the_agents_it_covers(self):
        """Vacuity: two empty declarations are equal, and would prove nothing."""
        self.assertGreaterEqual(len(self.declared(self.readme)), len(AGENTS))
        self.assertGreaterEqual(len(self.declared(self.install)), len(AGENTS))

    def test_the_two_documents_cover_the_same_agents(self):
        """The drift this file exists for: one page gains an agent, the other does not.

        Whole sets, unfiltered. `anchors() & AGENTS` on both sides reduced to
        `AGENTS == AGENTS`; keeping "ids in AGENTS or starting with zed" was the
        same filter with a hole cut for one test's fixture. Both seats said so, in
        both repositories.
        """
        self.assertEqual(self.declared(self.readme), self.declared(self.install))

    def test_a_fifth_agent_on_one_page_only_is_caught(self):
        """The mutation both filtered versions survived, through the real assertion."""
        readme = self.readme.replace(
            "](#cursor)",
            "](#cursor)\n[![Zed](https://img.shields.io/badge/Zed-install-000?style=flat-square)](#zed)",
        )
        self.assertNotEqual(self.declared(readme), self.declared(self.install))

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
        """Each agent's prose, from its own boundary to the next one.

        A boundary is an explicit `<a id>` **or** a heading — the README uses the
        first because a `<details>` box has no heading, the install page uses the
        second, and writing both under one heading gave the rendered page two
        elements with the same id. Splitting on either keeps one implementation
        for two shapes.

        "Or to the end of the file" was wrong for the **last** agent: `cursor` is
        last in the README, so its section ran to the end of the document and any
        later `**update` would have satisfied the check for a box that had none.
        The last section stops at the `</details>` that closes its box, or at the
        next rule on a page that does not use them.
        """
        marks = [(m.group(1), m.start()) for m in ANCHOR.finditer(text)]
        marks += [(heading_slug(m.group(1)), m.start()) for m in HEADING.finditer(text)]
        marks.sort(key=lambda pair: pair[1])
        found = {}
        for index, (name, start) in enumerate(marks):
            end = marks[index + 1][1] if index + 1 < len(marks) else len(text)
            # A box ends at its own `</details>`, whatever the next mark is. With
            # headings among the marks, "the next mark" put the Cursor box's end
            # hundreds of lines away — so a missing `**Update**` was satisfied by
            # unrelated prose, which is the hole this bound exists to close.
            closer = text.find("</details>", start)
            if closer != -1 and closer < end:
                end = closer
            found[name] = text[start:end]
        return found

    def test_the_last_section_stops_at_its_own_box(self):
        """Vacuity, on the one section that had none: it used to run to the file end.

        The property, not a character budget. A budget failed the day the Cursor
        box legitimately grew a second update command, which is the wrong thing to
        refuse; what matters is that the split **cut** something rather than
        handing back the rest of the document.
        """
        for text, where in ((self.readme, "README.md"), (self.install, INSTALL_NAME)):
            with self.subTest(document=where):
                sections = self.sections(text)
                last = sections[AGENTS[-1]]
                self.assertIn(last, text)
                self.assertLess(
                    len(last), len(text) - text.index(last), f"{where}: the last section runs on"
                )
                self.assertNotIn("## See also", last)

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


#: The commands that make a page an install instruction, whatever it calls itself.
INSTALL_COMMANDS = (
    "/plugin install",
    "plugin marketplace add",
    "agy plugin install",
    "codex plugin add",
    "cursor-agent plugin marketplace add",
    "--install-extension",
)

#: Records of what shipped, not instructions to follow.
INSTRUCTION_EXEMPT = ("CHANGELOG.md",)

#: Directories with no documents of ours in them. `tests` included: this file
#: quotes the commands it looks for, and a walk that read it would report itself.
SKIPPED_DIRS = {".git", ".venv", "node_modules", "htmlcov", "__pycache__", "tests"}


def instruction_pages() -> dict[str, str]:
    """Every tracked document that tells somebody how to install keel into a host.

    **Discovered, not listed.** A hardcoded four-name tuple was the same defect
    this module exists to refuse, one level in — the sibling change in ai-jury had
    exactly that, and two pages printing an install-only recipe were invisible to
    the test that requires a pointer to the page owning them. A page that prints
    one of these commands is an install page whatever its title says.
    """
    found = {}
    for path in sorted(REPO_ROOT.rglob("*.md")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if SKIPPED_DIRS & set(Path(relative).parts) or relative in INSTRUCTION_EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        if any(command in text for command in INSTALL_COMMANDS):
            found[relative] = text
    return found


#: A markdown link into another document's anchor, as `](../target.md#anchor)`.
#:
#: The relative prefix is **inside** the capture. Left outside it, `../../README.md`
#: was captured as `README.md` and resolved against the linking page's own
#: directory — `docs/keel/README.md`, which does not exist — and the check then
#: skipped it silently. Every cross-link this test was written for was invisible to
#: it, and a deliberately broken anchor passed. Case-insensitive on the filename
#: for the same reason: `README.md` is not lowercase.
DOC_ANCHOR_LINK = re.compile(r"\]\(((?:\.\.?/)*[A-Za-z0-9./_-]+\.md)#([a-z0-9-]+)\)")


class TheSiteDoesNotPublishAOneAgentRecipe(unittest.TestCase):
    """`website/content.js` is a fifth place the recipe lives, and it is the public one.

    It titled its card "Claude Code plugin", embedded only Claude's two commands,
    and cited the very page this change stripped of that framing. A reader on
    keel-ship.dev met the same one-agent story the repository had just stopped
    telling — the failure class of ai-jury#781, on the surface most people see.
    """

    def setUp(self):
        self.site = (REPO_ROOT / "website" / "content.js").read_text(encoding="utf-8")

    def test_the_card_was_read(self):
        self.assertIn('slug: "plugin"', self.site)

    def test_the_card_is_not_titled_for_one_agent(self):
        self.assertNotIn('title: "Claude Code plugin"', self.site)

    def test_the_card_points_at_the_install_page(self):
        self.assertIn("docs/keel/install.md", self.site)

    def test_the_card_names_the_other_three_agents(self):
        for agent in ("Codex", "Antigravity", "Cursor"):
            with self.subTest(agent=agent):
                self.assertIn(agent, self.site)

    def test_no_copyable_line_comments_out_its_own_second_step(self):
        """`agy plugin install <url>   # then: agy plugin enable keel` — pasted, the
        enable is a comment.

        A `<pre>` on a docs page exists to be copied. Putting the required second
        command inside a `#` comment on the same line makes the copy do half the
        job, silently, which is the failure `agy plugin install` alone already
        produces: an installed plugin that is disabled.
        """
        for marker in ("# then: agy plugin enable", "# then: codex plugin add"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.site)
        self.assertIn(
            "agy plugin install https://github.com/berkayturanci/keel\\nagy plugin enable keel",
            self.site,
        )

    def test_the_card_offers_their_commands_and_not_only_a_caption(self):
        """A four-agent caption over one agent's copyable fence is still one recipe.

        The card was retitled and the other three named in prose, while the only
        `<pre>` a reader can copy stayed Claude's two slash commands.
        """
        for command in ("codex plugin add keel@keel", "agy plugin install", "cursor-agent plugin"):
            with self.subTest(command=command):
                self.assertIn(command, self.site)

    def test_the_landing_page_does_not_lead_with_one_agent_either(self):
        """The homepage is the surface most people see, and it had a copy button.

        `website/index.html` read "In Claude Code? Install it as a plugin" beside a
        one-click copy of `/plugin install keel`, and its command section offered
        "`keel install-adapter all` or the Claude Code plugin" — the same
        one-agent story the documents had stopped telling.
        """
        landing = (REPO_ROOT / "website" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("In Claude Code? Install it as a plugin", landing)
        self.assertNotIn("or the Claude Code plugin", landing)
        self.assertIn("docs/keel/install.md", landing)
        for agent in ("Codex", "Antigravity", "Cursor"):
            with self.subTest(agent=agent):
                self.assertIn(agent, landing)


class ThePluginPageListsEveryManifestThatExists(unittest.TestCase):
    """`What ships` is the inventory, so a manifest missing from it is invisible.

    `.codex-plugin/plugin.json` was on disk and absent from the table, under a
    page that had just been retitled to cover four agents. Discovered from the
    tree, so the fifth manifest arrives in the table rather than beside it.
    """

    def test_every_manifest_on_disk_is_in_the_table(self):
        page = (REPO_ROOT / "docs" / "keel" / "plugin.md").read_text(encoding="utf-8")
        manifests = sorted(
            p.relative_to(REPO_ROOT).as_posix() for p in REPO_ROOT.glob(".*/plugin.json")
        )
        self.assertGreaterEqual(len(manifests), 3, manifests)
        for manifest in manifests:
            with self.subTest(manifest=manifest):
                self.assertIn(manifest, page)


class EveryCrossDocumentAnchorResolves(unittest.TestCase):
    """A link into a heading breaks silently when the heading is reworded.

    That is not hypothetical: renaming a heading in the sibling repository left a
    link pointing at the old slug, and nothing said so. Anchors are cheap to check
    and the failure mode is a reader landing at the top of a page wondering which
    part they were sent to.
    """

    def pages(self):
        return instruction_pages()

    def test_the_pages_were_read(self):
        pages = self.pages()
        self.assertLessEqual(
            {"README.md", "docs/keel/install.md", "docs/keel/plugin.md", "docs/keel/cli.md"},
            set(pages),
            f"the walk did not reach the pages that carry a recipe: {sorted(pages)}",
        )
        for name, text in pages.items():
            with self.subTest(document=name):
                self.assertGreater(len(text.splitlines()), 20)

    def test_the_pattern_sees_the_links_that_are_there(self):
        """Vacuity: a pattern matching nothing passes the check below."""
        found = DOC_ANCHOR_LINK.findall((REPO_ROOT / "docs" / "keel" / "install.md").read_text())
        self.assertTrue(any(target.endswith("README.md") for target, _ in found), found)

    def test_every_instruction_page_sends_the_reader_to_the_install_page(self):
        """A page that gives its own recipe has to point at the one that owns them."""
        for name, text in self.pages().items():
            if name.endswith("install.md"):
                continue
            with self.subTest(document=name):
                self.assertIn("install.md", text, f"{name} never points at the install page")

    def test_every_anchor_a_page_links_to_exists(self):
        for name, text in self.pages().items():
            base = (REPO_ROOT / name).parent
            for target, anchor in DOC_ANCHOR_LINK.findall(text):
                path = (base / target).resolve()
                with self.subTest(document=name, link=f"{target}#{anchor}"):
                    # A missing file is a finding, not a reason to skip. Skipping is
                    # how a mis-resolved path turned this whole check into a no-op.
                    self.assertTrue(path.is_file(), f"{name} links to {target}, which is not there")
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
