"""Every link into this repository resolves (#1217).

A heading rename changes its GitHub anchor, and nothing noticed: #1175 added
`[--transport auto|graphql|rest]` to the `keel verify-merge` heading, and the link to it
in `docs/keel/cli.md` kept pointing at the old anchor. The site's Vision article cited
`docs/keel/vision.md` for months after #215 deleted it. Both land a reader somewhere that
is not what the link said, and the only anchor test covered the per-agent install pages.

This checks the whole surface a reader follows: every relative link and ``#anchor`` in
`README.md`, `AGENTS.md` and `docs/**/*.md`, and every link from `website/` into this
repository's ``blob/main`` or ``tree/main``. External URLs are out of scope.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/berkayturanci/keel"

#: `[text](target)` — not an image, not an autolink. A title after the target is allowed.
LINK = re.compile(r'(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
#: `![alt](src)`, blanked out before LINK runs so a linked badge `[![alt](src)](target)`
#: reads as `[xxxx](target)` and yields its target. Two linear patterns, not one with an
#: image alternative inside the link text: there `!` and `[` could match either branch, and
#: CodeQL's py/redos is right that such a pattern backtracks exponentially.
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
EXPLICIT_ANCHOR = re.compile(r'<a\s+(?:name|id)="([^"]+)"')
SITE_LINK = re.compile(re.escape(REPO_URL) + r"/(?:blob|tree)/main/([^\s\"'<>)#]+)(?:#([\w\-]+))?")


def slug(heading: str) -> str:
    """GitHub's heading anchor: lower-case, punctuation dropped, spaces to hyphens.

    Code spans keep their text — `<project.yaml>` inside backticks is literal and
    contributes `projectyaml` — so nothing that looks like a tag is stripped.
    """
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _outside_fences(text: str):
    """Lines outside fenced code, numbered. A fence opens with ``` or ~~~ and only the same
    marker closes it, so a ~~~ line inside a ``` block is content, not a fence."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = line.lstrip()[:3]
        if marker in ("```", "~~~") and fence in (None, marker):
            fence = marker if fence is None else None
            continue
        if fence is None:
            yield number, line


def _paragraphs(text: str):
    """Runs of non-blank lines outside code fences, each with the line it starts on.

    Links are matched per paragraph, not per line: a link's text may wrap, as
    `[merge\\nwindow](cli.md#init-wizard)` does in onboarding.md, and a line-by-line
    match sees neither half.
    """
    start, lines, previous = 0, [], 0
    for number, line in _outside_fences(text):
        if lines and (not line.strip() or number != previous + 1):
            yield start, "\n".join(lines)
            lines = []
        if line.strip():
            start = start if lines else number
            lines.append(line)
        previous = number
    if lines:
        yield start, "\n".join(lines)


def link_matches(paragraph: str):
    """LINK's matches in ``paragraph``, images blanked first.

    Each image becomes as many ``x`` as it had characters, newlines kept, so a match's
    offset — and the line number read from it — is the same as in the paragraph itself.
    """
    blank = IMAGE.sub(lambda m: re.sub(r"[^\n]", "x", m.group(0)), paragraph)
    return LINK.finditer(blank)


def anchors(path: Path) -> set[str]:
    """Every anchor a document offers: heading slugs (duplicates numbered) and explicit ids."""
    text = path.read_text(encoding="utf-8")
    found: set[str] = set(EXPLICIT_ANCHOR.findall(text))
    seen: dict[str, int] = {}
    for _, line in _outside_fences(text):
        match = HEADING.match(line)
        if match:
            base = slug(match.group(2))
            count = seen.get(base, 0)
            found.add(base if count == 0 else f"{base}-{count}")
            seen[base] = count + 1
    return found


def documents() -> list[Path]:
    docs = sorted((REPO_ROOT / "docs").rglob("*.md"))
    return [REPO_ROOT / "README.md", REPO_ROOT / "AGENTS.md", *docs]


def _resolve(source: Path, target: str) -> Path:
    # A plain join: `Path` takes `/` on every platform. Round-tripping through
    # `PurePosixPath` turned `D:/a/keel` into the drive-relative `D:a\keel` on
    # Windows under Python 3.11, and every link there read as missing.
    base = REPO_ROOT if target.startswith("/") else source.parent
    return base / target.lstrip("/")


class TestDocumentLinksResolve(unittest.TestCase):
    def test_every_relative_link_and_anchor_resolves(self):
        broken: list[str] = []
        checked = 0
        for document in documents():
            text = document.read_text(encoding="utf-8")
            for start, paragraph in _paragraphs(text):
                for match in link_matches(paragraph):
                    target = match.group(1)
                    if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("//"):
                        continue
                    checked += 1
                    number = start + paragraph.count("\n", 0, match.start())
                    path, _, anchor = target.partition("#")
                    resolved = _resolve(document, path).resolve() if path else document
                    where = f"{document.relative_to(REPO_ROOT)}:{number} -> {target}"
                    if not resolved.exists():
                        broken.append(f"{where} (no such path)")
                    elif anchor and resolved.suffix == ".md" and anchor not in anchors(resolved):
                        broken.append(f"{where} (no such heading)")
        # Vacuity: a pattern that matched nothing would pass every document.
        self.assertGreater(checked, 200)
        self.assertEqual(broken, [], "\n".join(broken))

    def test_the_slug_rule_matches_the_headings_this_repo_links_to(self):
        # Pinned on a real heading shape: a code span with <placeholders>, [flags] and |.
        self.assertEqual(
            slug(
                "`keel verify-merge <project.yaml> [--root DIR] --pr N "
                "[--transport auto|graphql|rest]`"
            ),
            "keel-verify-merge-projectyaml---root-dir---pr-n---transport-autographqlrest",
        )

    def test_a_linked_badge_yields_its_target_not_its_image(self):
        # README's license and install badges link into the repo; the image URL is external.
        self.assertEqual(
            [
                m.group(1)
                for m in link_matches(
                    "[![License](https://img.shields.io/x.svg)](LICENSE) [a](b.md#c)"
                )
            ],
            ["LICENSE", "b.md#c"],
        )

    def test_a_wrapped_link_is_matched_and_blocks_stay_apart(self):
        text = "see the [merge\nwindow](cli.md#init-wizard) rule\n\n```\n[x](gone.md)\n```\nend"
        paragraphs = list(_paragraphs(text))
        self.assertEqual(
            paragraphs, [(1, "see the [merge\nwindow](cli.md#init-wizard) rule"), (7, "end")]
        )
        self.assertEqual(
            [m.group(1) for m in link_matches(paragraphs[0][1])], ["cli.md#init-wizard"]
        )

    def test_a_tilde_fence_is_a_fence_and_only_its_own_marker_closes_it(self):
        text = "a\n~~~\n[x](gone.md)\n```\n[y](gone.md)\n~~~\nb [ok](ok.md)"
        self.assertEqual(list(_paragraphs(text)), [(1, "a"), (7, "b [ok](ok.md)")])


class TestSiteLinksIntoTheRepoResolve(unittest.TestCase):
    def test_every_site_link_into_this_repository_resolves(self):
        broken: list[str] = []
        checked = 0
        for page in sorted((REPO_ROOT / "website").glob("*")):
            if page.suffix not in {".js", ".html", ".txt"} or page.name.startswith("coverage"):
                continue
            for match in SITE_LINK.finditer(page.read_text(encoding="utf-8")):
                checked += 1
                path, anchor = match.group(1).rstrip("/"), match.group(2)
                target = REPO_ROOT / path
                where = f"website/{page.name} -> {match.group(0)}"
                if not target.exists():
                    broken.append(f"{where} (no such path)")
                elif anchor and target.suffix == ".md" and anchor not in anchors(target):
                    broken.append(f"{where} (no such heading)")
        self.assertGreater(checked, 20)
        self.assertEqual(broken, [], "\n".join(broken))


if __name__ == "__main__":
    unittest.main()
