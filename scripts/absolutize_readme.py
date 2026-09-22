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

# A fenced code block boundary: ``` or ~~~ at the start of a (possibly indented)
# line. The marker is captured because a `~~~` line inside a ``` block does not
# close it — tracking only "in a fence or not" let the wrong marker flip the
# state and leave the rest of the file relative (the class #1218 fixed in
# `test_docs_links`).
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
# Markdown image: ![alt](target) where target is not already absolute.
_MD_IMAGE = re.compile(rf"!\[([^\]]*)\]\({_REL}([^)]+)\)")
# Markdown link: [text](target), not an image itself (negative lookbehind for !),
# where target is relative. The text may be plain OR a nested image — the
# badge-in-link pattern `[![alt](badge)](LICENSE)` — so the alternation keeps the
# whole label intact while only the outer target moves.
_MD_LINK = re.compile(rf"(?<!!)\[(!\[[^\]]*\]\([^)]*\)|[^\]]*)\]\({_REL}([^)]+)\)")
# Reference-style definition: `[label]: docs/x "optional title"`, up to three
# leading spaces (CommonMark). The tail is preserved verbatim.
_MD_REFDEF = re.compile(rf"^([ \t]{{0,3}}\[[^\]]+\]:[ \t]+){_REL}(\S+)(.*)$")
# HTML src="…" with a relative value (the hero <picture>/<img>).
_HTML_SRC = re.compile(rf'\bsrc="{_REL}([^"]+)"')
# HTML srcset="…". Deliberately **not** guarded at the attribute level: a srcset is
# a list, and its candidates can mix. Guarding the whole attribute on its first
# candidate meant `srcset="https://cdn/a.svg 1x, docs/b.svg 2x"` matched nothing at
# all and the relative second candidate survived. Each candidate is guarded on its
# own instead.
_HTML_SRCSET = re.compile(r'\bsrcset="([^"]+)"')
# HTML href="…" with a relative value (<a href="docs/…">).
_HTML_HREF = re.compile(rf'\bhref="{_REL}([^"]+)"')


def _link_host(target: str) -> str:
    """Directory targets (trailing slash) resolve under `tree/`, files under `blob/`."""
    return _TREE if target.endswith("/") else _BLOB


def _absolutize_srcset(value: str) -> str:
    """Prefix every candidate in a `srcset`, not only the first.

    `srcset` is a comma-separated list of `url [descriptor]` candidates, so a
    single prefix left `docs/b.svg 2x` relative in `"docs/a.svg 1x, docs/b.svg 2x"`.
    """
    out = []
    for candidate in value.split(","):
        stripped = candidate.strip()
        if not stripped:
            out.append(candidate)
            continue
        url, _, descriptor = stripped.partition(" ")
        if re.match(_REL + r".", url) is None:
            out.append(candidate)
            continue
        rebuilt = f"{_RAW}{url}" + (f" {descriptor.strip()}" if descriptor.strip() else "")
        out.append(rebuilt)
    return ", ".join(part.strip() for part in out)


def absolutize(text: str) -> str:
    """Return *text* with relative README links/images made absolute (pure)."""
    out: list[str] = []
    fence_marker: str | None = None
    for line in text.splitlines(keepends=True):
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            out.append(line)
            continue
        if fence_marker is not None:
            out.append(line)
            continue
        # Images first, so a relative `![](…)` is not also seen as a link.
        line = _MD_IMAGE.sub(lambda m: f"![{m.group(1)}]({_RAW}{m.group(2)})", line)
        line = _MD_LINK.sub(lambda m: f"[{m.group(1)}]({_link_host(m.group(2))}{m.group(2)})", line)
        line = _MD_REFDEF.sub(
            lambda m: f"{m.group(1)}{_link_host(m.group(2))}{m.group(2)}{m.group(3)}", line
        )
        line = _HTML_SRC.sub(lambda m: f'src="{_RAW}{m.group(1)}"', line)
        line = _HTML_SRCSET.sub(lambda m: f'srcset="{_absolutize_srcset(m.group(1))}"', line)
        line = _HTML_HREF.sub(lambda m: f'href="{_link_host(m.group(1))}{m.group(1)}"', line)
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
