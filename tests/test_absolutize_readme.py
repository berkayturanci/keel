"""`scripts/absolutize_readme.py` — README links must be absolute on PyPI.

`pyproject.toml` ships `README.md` as the PyPI long description, but
`readme_renderer` cannot resolve a relative `](docs/…)` href or `<img src="docs/…">`:
every in-repo link 404s and the hero image breaks. The publish workflow rewrites
the working-tree README to absolute GitHub URLs right before `python -m build`,
and never commits it (the committed README stays relative for GitHub and for
`tests/test_docs_links.py`).

These tests assert the transform's properties on small fixtures and on the real
README, and that `publish.yml` actually runs it before the build. `scripts/` is
outside the coverage gate, so this file is what holds it.
"""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "absolutize_readme.py"
_spec = importlib.util.spec_from_file_location("absolutize_readme", _SCRIPT)
absolutize_readme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(absolutize_readme)

absolutize = absolutize_readme.absolutize

_BLOB = "https://github.com/berkayturanci/keel/blob/main/"
_TREE = "https://github.com/berkayturanci/keel/tree/main/"
_RAW = "https://raw.githubusercontent.com/berkayturanci/keel/main/"


class TransformProperties(unittest.TestCase):
    def test_a_relative_file_link_becomes_a_blob_url(self):
        self.assertEqual(
            absolutize("See [the docs](docs/keel/cli.md)."),
            f"See [the docs]({_BLOB}docs/keel/cli.md).",
        )

    def test_a_link_anchor_is_preserved(self):
        self.assertEqual(
            absolutize("[team](docs/keel/configuration.md#team)"),
            f"[team]({_BLOB}docs/keel/configuration.md#team)",
        )

    def test_a_trailing_slash_target_uses_tree(self):
        self.assertEqual(absolutize("[dir](docs/keel/)"), f"[dir]({_TREE}docs/keel/)")

    def test_a_relative_html_image_becomes_a_raw_url(self):
        self.assertEqual(
            absolutize('<img src="docs/assets/hero.svg" alt="hero">'),
            f'<img src="{_RAW}docs/assets/hero.svg" alt="hero">',
        )

    def test_a_relative_srcset_becomes_a_raw_url(self):
        self.assertEqual(
            absolutize('<source srcset="docs/assets/hero.svg">'),
            f'<source srcset="{_RAW}docs/assets/hero.svg">',
        )

    def test_a_relative_markdown_image_becomes_a_raw_url(self):
        self.assertEqual(
            absolutize("![hero](docs/assets/hero.svg)"),
            f"![hero]({_RAW}docs/assets/hero.svg)",
        )

    def test_a_badge_linking_to_a_file_moves_only_the_outer_target(self):
        # `[![alt](absolute-badge)](LICENSE)`: the badge image stays absolute, the
        # LICENSE link becomes a blob URL.
        src = "[![License](https://img.shields.io/badge/x.svg)](LICENSE)"
        self.assertEqual(
            absolutize(src),
            f"[![License](https://img.shields.io/badge/x.svg)]({_BLOB}LICENSE)",
        )

    def test_absolute_links_are_untouched(self):
        src = "[home](https://github.com/berkayturanci/keel) and [x](http://e.com)"
        self.assertEqual(absolutize(src), src)

    def test_same_page_anchors_and_mailto_are_untouched(self):
        src = "[jump](#install) and [mail](mailto:a@b.com)"
        self.assertEqual(absolutize(src), src)

    def test_links_inside_fenced_code_blocks_are_untouched(self):
        for fence in ("```", "~~~"):
            src = f"{fence}\nsee [x](docs/y.md)\n{fence}\n"
            self.assertEqual(absolutize(src), src, f"fence {fence!r} not respected")

    def test_it_is_idempotent(self):
        src = '<img src="docs/assets/hero.svg">\n[a](docs/a.md) [b](#anchor) [c](https://x.com)\n'
        once = absolutize(src)
        self.assertEqual(absolutize(once), once)


class OnTheRealReadme(unittest.TestCase):
    def setUp(self):
        self.readme = _REPO_ROOT / "README.md"
        self.original = self.readme.read_text(encoding="utf-8")
        self.out = absolutize(self.original)

    def test_no_relative_markdown_link_survives(self):
        remaining = [
            t
            for t in re.findall(r"\]\(([^)]+)\)", self.out)
            if not t.startswith(("https://", "http://", "#", "mailto:"))
        ]
        self.assertEqual(remaining, [], f"relative links remain: {remaining}")

    def test_no_relative_image_source_survives(self):
        remaining = [
            v
            for v in re.findall(r'\bsrc(?:set)?="([^"]+)"', self.out)
            if not v.startswith(("https://", "http://"))
        ]
        self.assertEqual(remaining, [], f"relative image sources remain: {remaining}")

    def test_the_transform_does_not_touch_the_file_on_disk(self):
        # `absolutize` is pure; only the publish workflow (never a test, never a
        # commit) writes the rewritten bytes.
        self.assertEqual(self.readme.read_text(encoding="utf-8"), self.original)

    def test_it_is_idempotent_on_the_real_readme(self):
        self.assertEqual(absolutize(self.out), self.out)


class PublishWorkflowWiring(unittest.TestCase):
    def test_the_workflow_runs_the_script_before_building(self):
        workflow = (_REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(
            encoding="utf-8"
        )
        # Match the actual `run:` command lines, not prose mentions in comments.
        script_at = workflow.find("run: python scripts/absolutize_readme.py")
        build_at = workflow.find(
            "run: python -m build --no-isolation --sdist --wheel --outdir dist/"
        )
        self.assertNotEqual(script_at, -1, "publish.yml never runs absolutize_readme.py")
        self.assertNotEqual(build_at, -1, "publish.yml has no dist build step")
        self.assertLess(
            script_at,
            build_at,
            "absolutize_readme.py must run before the dist build so the rewritten "
            "README reaches the sdist and wheel long-description",
        )


class MainEntrypoint(unittest.TestCase):
    def test_main_rewrites_the_named_file_in_place(self):
        # Exercises the file read/write wrapper `publish.yml` actually calls.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "README.md"
            path.write_text('[x](docs/y.md)\n<img src="docs/z.svg">\n', encoding="utf-8")
            rc = absolutize_readme.main(["absolutize_readme.py", str(path)])
            self.assertEqual(rc, 0)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                f'[x]({_BLOB}docs/y.md)\n<img src="{_RAW}docs/z.svg">\n',
            )


if __name__ == "__main__":
    unittest.main()
