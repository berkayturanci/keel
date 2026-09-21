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
- relative Markdown images  `![alt](docs/…)`         → `raw.githubusercontent.com/…/main/docs/…`
- relative HTML `src`/`srcset` (the hero `<picture>`) → the same raw host

Absolute URLs, `mailto:`, and same-page `#anchors` are left untouched. The
transform is deterministic and idempotent (a second run is a no-op), so the
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

# A fenced code block boundary: ``` or ~~~ at the start of a (possibly indented) line.
_FENCE = re.compile(r"^[ \t]*(?:```|~~~)")
# Markdown image: ![alt](target) where target is not already absolute.
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((?!https?://)([^)]+)\)")
# Markdown link: [text](target), not an image itself (negative lookbehind for !),
# where target is not absolute, a same-page #anchor, or a mailto:. The text may be
# plain OR a nested image — the badge-in-link pattern `[![alt](badge)](LICENSE)` —
# so the alternation keeps the whole label intact while only the outer target moves.
_MD_LINK = re.compile(r"(?<!!)\[(!\[[^\]]*\]\([^)]*\)|[^\]]*)\]\((?!https?://|#|mailto:)([^)]+)\)")
# HTML src="…" / srcset="…" with a relative value (the hero <picture>/<img>).
_HTML_SRC = re.compile(r'\b(src|srcset)="(?!https?://)([^"]+)"')


def _link_host(target: str) -> str:
    """Directory targets (trailing slash) resolve under `tree/`, files under `blob/`."""
    return _TREE if target.endswith("/") else _BLOB


def absolutize(text: str) -> str:
    """Return *text* with relative README links/images made absolute (pure)."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        # Images first, so a relative `![](…)` is not also seen as a link.
        line = _MD_IMAGE.sub(lambda m: f"![{m.group(1)}]({_RAW}{m.group(2)})", line)
        line = _MD_LINK.sub(lambda m: f"[{m.group(1)}]({_link_host(m.group(2))}{m.group(2)})", line)
        line = _HTML_SRC.sub(lambda m: f'{m.group(1)}="{_RAW}{m.group(2)}"', line)
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
