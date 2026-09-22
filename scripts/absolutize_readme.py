#!/usr/bin/env python3
"""Rewrite README relative links and images to absolute GitHub URLs, for PyPI.

Publish-only. `pyproject.toml` uses `README.md` as the PyPI long description, but
PyPI's `readme_renderer` cannot resolve a relative `](docs/…)` href or a relative
`<img src="docs/…">`: every in-repo link 404s and the hero image breaks. So the
publish workflow runs this against the working-tree README right before
`python -m build`, and never commits the result — the committed README stays
relative so GitHub renders it and `tests/test_docs_links.py` resolves it on disk.

What it rewrites (only outside fenced code blocks):

- relative Markdown links   `[text](docs/…)`        → `…/blob/main/docs/…`
  (a trailing-slash target → `…/tree/main/…`; any `#anchor` is preserved)
- relative reference defs   `[label]: docs/…`        → the same, title preserved
- relative Markdown images  `![alt](docs/…)`         → `raw.githubusercontent.com/…/main/docs/…`
- relative HTML `href`      `<a href="docs/…">`      → `…/blob/main/docs/…`
- relative HTML `src`/`srcset` (the hero `<picture>`) → the same raw host,
  every candidate in a multi-candidate `srcset`, not just the first

Left untouched: anything carrying a URL scheme (`https:`, `mailto:`, `tel:`,
`data:`, `ftp:`…), a protocol-relative `//host/…` target, and same-page
`#anchors`. The old guard named only `https?://`, `#` and `mailto:`, so a
future `[x](tel:…)` or `<img src="data:…">` would have been rewritten into
`…/blob/main/tel:…` — and the real-README guard would not have caught it,
because the mangled target still starts with `https://` (#1261).

The transform is deterministic and idempotent (a second run is a no-op), so the
reproducible-build rebuild in `publish.yml` sees the same bytes and digests match.

`scripts/` is maintenance tooling outside the coverage gate;
`tests/test_absolutize_readme.py` is what holds this.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_OWNER_REPO = "berkayturanci/keel"
_BLOB = f"https://github.com/{_OWNER_REPO}/blob/main/"
_TREE = f"https://github.com/{_OWNER_REPO}/tree/main/"
_RAW = f"https://raw.githubusercontent.com/{_OWNER_REPO}/main/"

# "Not already pointing somewhere of its own": any URL scheme (`https:`, `tel:`,
# `data:`, `mailto:`…), a protocol-relative `//host/…`, or a same-page `#anchor`.
# This is the same rule `tests/test_docs_links.py` calls external.
_REL = r"(?![a-zA-Z][a-zA-Z0-9+.\-]*:|//|#)"

# A fenced code block boundary. The marker is captured whole, because CommonMark
# §4.5 makes closing a fence stricter than opening one: a closer uses the same
# character, is **at least as long**, and carries no info string. Matching a fixed
# three characters broke both ways — a `~~~` line inside a ``` block closed it, and
# a ````markdown block showing ``` examples closed on its own content.
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})([^\n]*)$")
# Markdown image: ![alt](target) where target is not already absolute.
_MD_IMAGE = re.compile(rf"!\[([^\]]*)\]\({_REL}([^)]+)\)")
# Markdown link: [text](target), not an image itself (negative lookbehind for !),
# where target is relative. The text may be plain OR a nested image — the
# badge-in-link pattern `[![alt](badge)](LICENSE)` — so the alternation keeps the
# whole label intact while only the outer target moves.
_MD_LINK = re.compile(rf"(?<!!)\[(!\[[^\]]*\]\([^)]*\)|[^\]]*)\]\({_REL}([^)]+)\)")
# Reference-style definition: `[label]: docs/x "optional title"`, up to three
# leading spaces. CommonMark §4.7 allows **only** an optional quoted title after the
# destination; anything else makes the line ordinary paragraph text. Accepting an
# arbitrary tail turned every GFM footnote (`[^1]: Keel is a tool`) and every
# `[NOTE]: remember to …` line into a definition and rewrote its first word into a
# blob URL. A `[^…]` label is a footnote, never a definition. Whitespace after the
# colon is optional (`[l]:docs/a.md` is a definition too, #1294).
_MD_REFDEF = re.compile(
    rf"^([ \t]{{0,3}}\[(?!\^)[^\]]+\]:[ \t]*){_REL}(\S+)"
    r"((?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?[ \t]*)$"
)
# HTML src="…" with a relative value (the hero <picture>/<img>).
_HTML_SRC = re.compile(rf'\bsrc="{_REL}([^"]+)"')
# HTML srcset="…". Deliberately **not** guarded at the attribute level: a srcset is
# a list, and its candidates can mix. Guarding the whole attribute on its first
# candidate meant `srcset="https://cdn/a.svg 1x, docs/b.svg 2x"` matched nothing at
# all and the relative second candidate survived. Each candidate is guarded on its
# own instead.
_HTML_SRCSET = re.compile(r'\bsrcset="([^"]+)"')
# HTML href="…" with a relative value (<a href="docs/…">).
# `\bhref=` also matched `data-href=` (a hyphen is not a word character), and `blob/`
# is the wrong host for a `<link rel="stylesheet">` or an SVG `<image href>` — those
# want raw bytes, not a GitHub page — so the tag is named rather than assumed.
_HTML_HREF = re.compile(rf'(<a\b[^>]*?(?<![-\w])href="){_REL}([^"]+)"')
# The script reads a line at a time, so an `<a` whose attributes continue onto the
# next lines — the usual hand-formatted hero — had its `href` missed once the
# rewrite was scoped to `<a>` (#1294). An `<a` left open at the end of a line is
# carried to the next, whose `href` is rewritten up to the `>` that closes the tag.
# The carried state ends at that `>`, at a blank line and at a code fence: in
# CommonMark an inline tag cannot span a blank line (the paragraph ends there) or a
# code block. Without those bounds an `<a` mentioned in prose leaked forward and
# rewrote the next `<link href>` — and no guard saw it, because the result was a
# well-formed blob URL (#1300 review).
_OPEN_ANCHOR = re.compile(r"<a\b[^>]*$")
# Inline code is text, not HTML: a sentence documenting "`<a` tags" must not open an
# anchor. Spans on one line are dropped before looking for an open `<a`; a span that
# runs across lines is ended by the `<` rule below, like any other stray `<a` (#1300
# review). An `href` shown inside a code span may still be rewritten, as before #1294.
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
_CONTINUED_HREF = re.compile(rf'((?<![-\w])href="){_REL}([^"]+)"')


def _path(target: str) -> str:
    """The repo-relative path a target names. A root-relative `/docs/a.md` resolves
    from the repository root on GitHub, so its leading slash is dropped rather than
    doubled into `…/main//docs/a.md`. (`//host` never gets here: `_REL` refuses it.)"""
    return target[1:] if target.startswith("/") else target


def _link_host(target: str) -> str:
    """Directory targets (trailing slash) resolve under `tree/`, files under `blob/`."""
    return _TREE if target.endswith("/") else _BLOB


def _srcset_candidates(value: str) -> list[tuple[str, str]]:
    """The ``(url, descriptor)`` candidates of a `srcset`, parsed as the HTML standard does.

    A candidate's URL is the run of non-whitespace characters, and a comma ends it
    only at the end of that run. Splitting on every comma cut a `data:` URL — which
    legally contains one, `data:image/png;base64,AAAA` — in two and rewrote its tail
    as a relative path (#1294).
    """
    out: list[tuple[str, str]] = []
    i, n = 0, len(value)
    while i < n:
        while i < n and (value[i].isspace() or value[i] == ","):
            i += 1
        if i >= n:
            break
        j = i
        while j < n and not value[j].isspace():
            j += 1
        url, i = value[i:j], j
        descriptor = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            end = value.find(",", i)
            end = n if end == -1 else end
            descriptor, i = value[i:end].strip(), end + 1
        out.append((url, descriptor))
    return out


def _absolutize_srcset(value: str) -> str:
    """Prefix every relative candidate in a `srcset`, and only those.

    A single prefix left `docs/b.svg 2x` relative in `"docs/a.svg 1x, docs/b.svg 2x"`.
    Descriptors are separated from the URL by any ASCII whitespace, so a tab no longer
    glues one onto it.
    """
    rebuilt = []
    for url, descriptor in _srcset_candidates(value):
        if re.match(_REL + r".", url) is not None:
            url = f"{_RAW}{_path(url)}"
        rebuilt.append(f"{url} {descriptor}" if descriptor else url)
    return ", ".join(rebuilt)


def absolutize(text: str) -> str:
    """Return *text* with relative README links/images made absolute (pure)."""
    out: list[str] = []
    fence: str | None = None  # the opening marker, or None outside a block
    in_anchor = False  # an `<a` tag opened on an earlier line and not yet closed
    for line in text.splitlines(keepends=True):
        boundary = _FENCE.match(line.rstrip("\r\n"))
        # CommonMark §4.5: a backtick fence's info string cannot contain a backtick, so
        # a line opening with inline code (```x```) is text, not a fence (#1294).
        if boundary is not None and boundary.group(1)[0] == "`" and "`" in boundary.group(2):
            boundary = None
        if boundary is not None:
            in_anchor = False
            marker, info = boundary.group(1), boundary.group(2)
            if fence is None:
                # An opening fence may carry an info string (```python).
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not info.strip():
                fence = None
            out.append(line)
            continue
        if fence is not None:
            out.append(line)
            continue
        if not line.strip():
            in_anchor = False
        if in_anchor:
            head, close, tail = line.partition(">")
            if "<" in head:
                # A tag's attribute names and unquoted values cannot contain `<`, so a
                # new `<` before the `>` means the earlier `<a` was never a tag — prose
                # like "wrap it in an <a tag" — and this line's `href` belongs to
                # another element. A quoted value may hold `<` (or `>`); an anchor
                # with one before its `href` is left relative, as before #1294.
                in_anchor = False
            else:
                head = _CONTINUED_HREF.sub(
                    lambda m: f'{m.group(1)}{_link_host(m.group(2))}{_path(m.group(2))}"', head
                )
                line = head + close + tail
                in_anchor = not close
        # Images first, so a relative `![](…)` is not also seen as a link.
        line = _MD_IMAGE.sub(lambda m: f"![{m.group(1)}]({_RAW}{_path(m.group(2))})", line)
        line = _MD_LINK.sub(
            lambda m: f"[{m.group(1)}]({_link_host(m.group(2))}{_path(m.group(2))})", line
        )
        line = _MD_REFDEF.sub(
            lambda m: f"{m.group(1)}{_link_host(m.group(2))}{_path(m.group(2))}{m.group(3)}", line
        )
        line = _HTML_SRC.sub(lambda m: f'src="{_RAW}{_path(m.group(1))}"', line)
        line = _HTML_SRCSET.sub(lambda m: f'srcset="{_absolutize_srcset(m.group(1))}"', line)
        line = _HTML_HREF.sub(
            lambda m: f'{m.group(1)}{_link_host(m.group(2))}{_path(m.group(2))}"', line
        )
        if not in_anchor and _OPEN_ANCHOR.search(_CODE_SPAN.sub("", line)):
            in_anchor = True
        out.append(line)
    return "".join(out)


def main(argv: list[str]) -> int:
    paths = argv[1:] or ["README.md"]
    for name in paths:
        path = Path(name)
        path.write_text(absolutize(path.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"absolutized {name} for PyPI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
