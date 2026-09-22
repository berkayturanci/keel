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


# The real-README guards, as functions so the guards themselves can be tested
# (#1294). Each mirrors a rule the script applies — any scheme or `//host` is
# external, a `[^…]` label is a footnote, only a quoted title may follow a
# definition's destination, and only an `<a>` tag's `href` is a link — but is
# written independently, so it judges the output rather than re-running the
# rewrite. A guard that disagreed with the script failed on forms the script
# correctly leaves alone, and blamed the script for it.
_EXTERNAL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*:|//|#")
_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*:|//")
_DEFINITION = re.compile(
    r"(?m)^[ \t]{0,3}\[(?!\^)[^\]]+\]:[ \t]*(\S+)"
    r"(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?[ \t]*$"
)
_DESCRIPTOR = re.compile(r"\d+(?:\.\d+)?[wx]")


def relative_markdown_links(text: str) -> list[str]:
    return [t for t in re.findall(r"\]\(([^)]+)\)", text) if not _EXTERNAL.match(t)]


def relative_image_sources(text: str) -> list[str]:
    return [v for v in re.findall(r'\bsrc="([^"]+)"', text) if not _SCHEME.match(v)]


def relative_srcset_candidates(text: str) -> list[str]:
    """Each candidate URL is a whitespace-delimited token, so a `data:` URL keeps its
    comma; a trailing comma ends a candidate; `1x` / `300w` are descriptors."""
    remaining = []
    for value in re.findall(r'\bsrcset="([^"]+)"', text):
        for token in value.split():
            token = token.rstrip(",")
            if token and not _DESCRIPTOR.fullmatch(token) and not _SCHEME.match(token):
                remaining.append(token)
    return remaining


def relative_reference_definitions(text: str) -> list[str]:
    return [t for t in _DEFINITION.findall(text) if not _EXTERNAL.match(t)]


def relative_anchor_hrefs(text: str) -> list[str]:
    """An `<a>` tag's relative `href`, including one on a later line of the tag.

    Bounded the way CommonMark bounds an inline tag: inline code is text, not HTML,
    so code spans are dropped first; a tag cannot span a blank line; and a tag's
    attributes cannot contain `<`, so the match cannot reach into the next tag. Unbounded,
    this guard shared the script's leak exactly — an `<a` mentioned in prose made
    the next `<link href>` an anchor — so the two failed together (#1300 review).
    """
    text = re.sub(r"(`+)(?:(?!\1).)+?\1", "", text)
    return [
        v
        for v in re.findall(r'<a\b(?:(?!\n[ \t]*\n)[^><])*?(?<![-\w])href="([^"]+)"', text)
        if not _EXTERNAL.match(v)
    ]


def mangled_targets(text: str) -> list[str]:
    """A scheme or a second `/` straight after a rewritten host means an absolute or
    protocol-relative target was prefixed as if it were a path."""
    return re.findall(
        r"berkayturanci/keel/(?:blob/main|tree/main|main)/([a-zA-Z][a-zA-Z0-9+.\-]*:|/)", text
    )


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
        remaining = relative_markdown_links(self.out)
        self.assertEqual(remaining, [], f"relative links remain: {remaining}")

    def test_no_relative_image_source_survives(self):
        remaining = relative_image_sources(self.out)
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


class LatentFormsTheOldGuardWouldHaveMangled(unittest.TestCase):
    """#1261, from #1260's tier-3 review. None of these forms is in today's README,
    so none was a merge blocker — and none would have been *caught* either, which is
    the point: the real-README guard asks whether a target still looks relative, and
    a mangled `…/blob/main/tel:+1` starts with `https://` like any correct rewrite.
    """

    def test_a_non_http_scheme_is_left_alone(self):
        """The old guard named only `https?://`, `#` and `mailto:`, so every other
        scheme was treated as a relative path and prefixed."""
        for target in ("tel:+15551234", "ftp://example.org/x", "irc://example.org"):
            with self.subTest(target=target):
                self.assertEqual(absolutize(f"[x]({target})\n"), f"[x]({target})\n")

    def test_a_data_uri_image_is_left_alone(self):
        src = '<img src="data:image/svg+xml;base64,AAAA">\n'
        self.assertEqual(absolutize(src), src)

    def test_a_protocol_relative_target_is_left_alone(self):
        for line in ("[x](//cdn.example.org/a.png)\n", '<img src="//cdn.example.org/a.png">\n'):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_every_srcset_candidate_is_rewritten(self):
        """A single prefix left the second candidate relative, so a 2x display fell
        back to a 404."""
        out = absolutize('<img srcset="docs/a.svg 1x, docs/b.svg 2x">\n')
        self.assertEqual(out, f'<img srcset="{_RAW}docs/a.svg 1x, {_RAW}docs/b.svg 2x">\n')

    def test_a_srcset_candidate_that_is_already_absolute_is_left_alone(self):
        line = '<img srcset="https://cdn/a.svg 1x, docs/b.svg 2x">\n'
        self.assertEqual(
            absolutize(line), f'<img srcset="https://cdn/a.svg 1x, {_RAW}docs/b.svg 2x">\n'
        )

    def test_a_tilde_fence_does_not_close_a_backtick_block(self):
        """The class #1218 fixed in `test_docs_links`: tracking only "inside a fence
        or not" let the wrong marker flip the state, leaving the rest of the file
        relative — here, silently shipping a 404 to PyPI."""
        text = "```\n~~~\n[x](docs/a.md)\n```\n[y](docs/b.md)\n"
        expected = f"```\n~~~\n[x](docs/a.md)\n```\n[y]({_BLOB}docs/b.md)\n"
        self.assertEqual(absolutize(text), expected)

    def test_a_backtick_fence_does_not_close_a_tilde_block(self):
        text = "~~~\n```\n[x](docs/a.md)\n~~~\n[y](docs/b.md)\n"
        expected = f"~~~\n```\n[x](docs/a.md)\n~~~\n[y]({_BLOB}docs/b.md)\n"
        self.assertEqual(absolutize(text), expected)

    def test_a_reference_style_definition_is_rewritten(self):
        self.assertEqual(absolutize("[label]: docs/a.md\n"), f"[label]: {_BLOB}docs/a.md\n")

    def test_a_reference_style_definition_keeps_its_title(self):
        self.assertEqual(
            absolutize('[label]: docs/a.md "A title"\n'),
            f'[label]: {_BLOB}docs/a.md "A title"\n',
        )

    def test_an_absolute_reference_style_definition_is_left_alone(self):
        line = "[label]: https://example.org/a\n"
        self.assertEqual(absolutize(line), line)

    def test_a_relative_html_href_is_rewritten(self):
        self.assertEqual(
            absolutize('<a href="docs/a.md">x</a>\n'), f'<a href="{_BLOB}docs/a.md">x</a>\n'
        )

    def test_a_directory_href_uses_tree(self):
        self.assertEqual(absolutize('<a href="docs/">x</a>\n'), f'<a href="{_TREE}docs/">x</a>\n')

    def test_the_new_forms_are_idempotent(self):
        text = (
            "[label]: docs/a.md\n"
            '<a href="docs/b.md">x</a>\n'
            '<img srcset="docs/c.svg 1x, docs/d.svg 2x">\n'
            "[x](tel:+15551234)\n"
        )
        once = absolutize(text)
        self.assertEqual(absolutize(once), once)


class TheRealReadmeGuardSeesTheNewForms(unittest.TestCase):
    """The guard that missed all of this asked only whether a target still *looks*
    relative. These add the forms it could not see at all."""

    def setUp(self):
        self.out = absolutize((_REPO_ROOT / "README.md").read_text(encoding="utf-8"))

    def test_no_relative_reference_definition_survives(self):
        remaining = relative_reference_definitions(self.out)
        self.assertEqual(remaining, [], f"relative reference definitions remain: {remaining}")

    def test_no_relative_html_href_survives(self):
        remaining = relative_anchor_hrefs(self.out)
        self.assertEqual(remaining, [], f"relative hrefs remain: {remaining}")

    def test_no_srcset_candidate_stays_relative(self):
        remaining = relative_srcset_candidates(self.out)
        self.assertEqual(remaining, [], f"relative srcset candidates remain: {remaining}")

    def test_no_target_was_mangled_into_a_scheme_bearing_path(self):
        """The assertion the old guard could not make: a rewritten target must not
        carry a scheme or a second `/` after the host."""
        bad = mangled_targets(self.out)
        self.assertEqual(bad, [], f"an absolute target was prefixed as if it were a path: {bad}")


class TheGuardsJudgeTheRightThings(unittest.TestCase):
    """#1294: the guards did not mirror the script's narrowings, so they failed on
    forms the script correctly leaves alone — a footnote in the README would have
    broken the publish tests with a message blaming the script. Each guard is held
    both ways: silent on what is correctly left alone, loud on what is relative."""

    def test_footnotes_and_prose_labels_are_not_definitions(self):
        text = "[^1]: Keel is a tool\n[NOTE]: remember to update docs/x\n"
        self.assertEqual(relative_reference_definitions(text), [])

    def test_a_footnote_shaped_like_a_definition_is_not_one(self):
        """The case the guard's `[^…]` exclusion is for, as in the script: a valid
        footnote whose body has the shape of a destination and a title."""
        self.assertEqual(relative_reference_definitions('[^1]: docs/a.md "A title"\n'), [])

    def test_a_relative_definition_is_caught(self):
        self.assertEqual(relative_reference_definitions("[l]: docs/a.md\n"), ["docs/a.md"])
        self.assertEqual(relative_reference_definitions("[l]:docs/a.md\n"), ["docs/a.md"])

    def test_non_anchor_hrefs_are_not_links(self):
        text = '<div data-href="docs/x"></div>\n<link rel="stylesheet" href="docs/a.css">\n'
        self.assertEqual(relative_anchor_hrefs(text), [])

    def test_a_relative_anchor_href_is_caught_across_lines(self):
        self.assertEqual(relative_anchor_hrefs('<a\n  href="docs/a.md">x</a>\n'), ["docs/a.md"])

    def test_a_data_image_is_not_relative(self):
        self.assertEqual(relative_image_sources('<img src="data:image/png;base64,AAAA">'), [])
        self.assertEqual(relative_image_sources('<img src="docs/a.png">'), ["docs/a.png"])

    def test_a_data_srcset_candidate_keeps_its_comma(self):
        self.assertEqual(
            relative_srcset_candidates('<img srcset="data:image/png;base64,AAAA 1x">'), []
        )
        self.assertEqual(
            relative_srcset_candidates('<img srcset="https://cdn/a.svg 1x, docs/b.svg 2x">'),
            ["docs/b.svg"],
        )

    def test_markdown_links_are_judged_both_ways(self):
        self.assertEqual(relative_markdown_links("[a](https://x) [b](#c) [d](mailto:e)"), [])
        self.assertEqual(relative_markdown_links("[a](docs/a.md)"), ["docs/a.md"])

    def test_an_anchor_mentioned_in_prose_does_not_make_a_link_an_anchor(self):
        text = 'Use `<a` tags\n<link rel="stylesheet" href="docs/a.css">\n'
        self.assertEqual(relative_anchor_hrefs(text), [])

    def test_an_anchor_does_not_reach_across_a_blank_line(self):
        self.assertEqual(relative_anchor_hrefs('<a\n\n<image href="docs/a.svg"/>\n'), [])

    def test_a_scheme_prefixed_as_a_path_is_caught(self):
        self.assertEqual(mangled_targets(f"[x]({_BLOB}tel:+1)"), ["tel:"])

    def test_a_protocol_relative_target_prefixed_as_a_path_is_caught(self):
        self.assertEqual(mangled_targets(f"[x]({_BLOB}/cdn/a.png)"), ["/"])
        self.assertEqual(mangled_targets(f"[x]({_BLOB}docs/a.md)"), [])


class FormsThatOnlyLOOKLikeTheOnesWeRewrite(unittest.TestCase):
    """#1261 round 1, agy gate. Each new rule widened what the script matches, and
    each widening reached something it should not. Matching more is not the same as
    matching right."""

    def test_a_footnote_is_not_a_reference_definition(self):
        """`[^1]: Keel is a tool` is a GFM footnote. Reading an arbitrary tail after
        the destination made it one, and rewrote `Keel` into a blob URL."""
        for line in ("[^1]: Keel is a tool\n", "[^note]: see the docs directory\n"):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_a_footnote_shaped_like_a_definition_is_still_a_footnote(self):
        """The case the `[^…]` exclusion is actually for. Prose after the first word
        is already refused by the strict tail, so an ordinary footnote never reaches
        the exclusion — but `[^1]: docs/a.md "A title"` is a *valid* footnote whose
        body happens to have the shape of a destination and a title, and GFM resolves
        a `[^…]` label as a footnote definition, never a link one."""
        line = '[^1]: docs/a.md "A title"\n'
        self.assertEqual(absolutize(line), line)

    def test_a_label_followed_by_prose_is_not_a_reference_definition(self):
        """CommonMark §4.7: only an optional quoted title may follow the destination.
        Anything else and the line is ordinary paragraph text."""
        for line in ("[NOTE]: remember to update docs/x\n", "[TODO]: fix docs/y later\n"):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_a_reference_definition_with_each_title_form_is_still_rewritten(self):
        """The counterweight: refusing prose must not refuse the real thing."""
        for tail in ('"A title"', "'A title'", "(A title)"):
            with self.subTest(tail=tail):
                self.assertEqual(
                    absolutize(f"[label]: docs/a.md {tail}\n"),
                    f"[label]: {_BLOB}docs/a.md {tail}\n",
                )

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        """A ````markdown block exists to show ``` examples. Treating any three-char
        run as a boundary closed it on its own content and rewrote the example."""
        text = "````markdown\n```\n[x](docs/a.md)\n```\n````\n[y](docs/b.md)\n"
        expected = f"````markdown\n```\n[x](docs/a.md)\n```\n````\n[y]({_BLOB}docs/b.md)\n"
        self.assertEqual(absolutize(text), expected)

    def test_a_line_with_an_info_string_does_not_close_a_block(self):
        """CommonMark §4.5: a closing fence carries no info string."""
        text = "```\n```foo\n[x](docs/a.md)\n```\n"
        self.assertEqual(absolutize(text), text)

    def test_an_opening_fence_may_carry_an_info_string(self):
        """The counterweight: ```python must still open a block."""
        text = "```python\n[x](docs/a.md)\n```\n"
        self.assertEqual(absolutize(text), text)

    def test_a_data_attribute_ending_in_href_is_not_an_href(self):
        """`\\bhref=` matched `data-href=`, because a hyphen is not a word character."""
        for line in ('<div data-href="docs/x"></div>\n', '<div x-href="docs/x"></div>\n'):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_an_href_outside_an_anchor_is_left_alone(self):
        """`blob/` is a GitHub *page*. A stylesheet or an SVG `<image>` wants bytes,
        so those are not this script's to rewrite."""
        for line in (
            '<link rel="stylesheet" href="docs/a.css">\n',
            '<image href="docs/a.svg"/>\n',
        ):
            with self.subTest(line=line):
                self.assertEqual(absolutize(line), line)

    def test_a_tab_separated_srcset_descriptor_is_not_glued_to_the_url(self):
        """The HTML standard allows any ASCII whitespace between a candidate's URL
        and its descriptor; splitting on one space made `a.svg\\t1x` the URL."""
        self.assertEqual(
            absolutize('<img srcset="docs/a.svg\t1x">\n'),
            f'<img srcset="{_RAW}docs/a.svg 1x">\n',
        )

    def test_these_forms_are_idempotent_too(self):
        text = (
            "[^1]: Keel is a tool\n"
            "[NOTE]: remember to update\n"
            '<div data-href="docs/x"></div>\n'
            "````markdown\n```\n[x](docs/a.md)\n```\n````\n"
            '<img srcset="docs/a.svg\t1x">\n'
        )
        once = absolutize(text)
        self.assertEqual(absolutize(once), once)


class TheFormsLeftOpenIn1294(unittest.TestCase):
    """#1294, from #1293's lead review: two defects in the fix itself, and two nits."""

    def test_a_data_srcset_candidate_is_not_split_on_its_own_comma(self):
        """Splitting on every comma cut `data:image/png;base64,AAAA` in two and
        rewrote `AAAA` as a relative path."""
        line = '<img srcset="data:image/png;base64,AAAA 1x">\n'
        self.assertEqual(absolutize(line), line)

    def test_a_data_candidate_beside_a_relative_one(self):
        out = absolutize('<img srcset="data:image/png;base64,AAAA 1x, docs/b.png 2x">\n')
        self.assertEqual(
            out, f'<img srcset="data:image/png;base64,AAAA 1x, {_RAW}docs/b.png 2x">\n'
        )

    def test_an_anchor_whose_href_is_on_a_later_line(self):
        """The usual hand-formatted hero. Scoping the rewrite to `<a>` lost it."""
        text = '<a\n  class="hero"\n  href="docs/a.md">x</a>\n'
        self.assertEqual(absolutize(text), f'<a\n  class="hero"\n  href="{_BLOB}docs/a.md">x</a>\n')

    def test_a_non_anchor_tag_across_lines_is_left_alone(self):
        """The counterweight: carrying an open tag must not make every href a link."""
        text = '<div\n  href="docs/x">\n<a href="docs/a.md">\n'
        self.assertEqual(absolutize(text), f'<div\n  href="docs/x">\n<a href="{_BLOB}docs/a.md">\n')

    def test_an_anchor_closed_on_its_own_line_does_not_carry(self):
        text = '<a href="docs/a.md">x</a>\n<div\n  href="docs/x">\n'
        self.assertEqual(
            absolutize(text), f'<a href="{_BLOB}docs/a.md">x</a>\n<div\n  href="docs/x">\n'
        )

    def test_a_multi_line_anchor_stops_carrying_where_its_tag_closes(self):
        """The carried state ends at the `>` that closes the `<a` tag; after it, a
        non-anchor tag's `href` is not a link."""
        text = '<a\n  href="docs/a.md">x</a>\n<div\n  href="docs/x">\n'
        self.assertEqual(
            absolutize(text), f'<a\n  href="{_BLOB}docs/a.md">x</a>\n<div\n  href="docs/x">\n'
        )

    def test_the_closing_bracket_ends_the_state_with_no_later_tag_to_help(self):
        """The same rule with nothing else to end the state: the next line is prose
        with an `href` and no `<`, so only the `>` that closed the `<a` stops it
        (#1300 gate, round 3 — the fixture above also trips the `<` rule)."""
        text = '<a\n  href="docs/a.md">x</a>\nSet href="docs/x.md" on the element.\n'
        self.assertEqual(
            absolutize(text),
            f'<a\n  href="{_BLOB}docs/a.md">x</a>\nSet href="docs/x.md" on the element.\n',
        )

    def test_inline_code_at_the_start_of_a_line_is_not_a_fence(self):
        """CommonMark §4.5: a backtick fence's info string cannot contain a backtick."""
        text = "```x```\n[y](docs/b.md)\n"
        self.assertEqual(absolutize(text), f"```x```\n[y]({_BLOB}docs/b.md)\n")

    def test_a_tilde_fence_may_carry_a_backtick_in_its_info_string(self):
        """The counterweight: the rule is for backtick fences only."""
        text = "~~~ `lang`\n[y](docs/b.md)\n~~~\n"
        self.assertEqual(absolutize(text), text)

    def test_a_definition_with_no_space_after_the_colon(self):
        self.assertEqual(absolutize("[l]:docs/a.md\n"), f"[l]:{_BLOB}docs/a.md\n")

    def test_these_forms_are_idempotent(self):
        text = (
            '<img srcset="data:image/png;base64,AAAA 1x, docs/b.png 2x">\n'
            '<a\n  href="docs/a.md">x</a>\n'
            "```x```\n[y](docs/b.md)\n"
            "[l]:docs/a.md\n"
        )
        once = absolutize(text)
        self.assertEqual(absolutize(once), once)


class TheCarriedAnchorStaysInItsTag(unittest.TestCase):
    """#1300 review (gate and lead, independently): the carried `<a` state was set by
    any `<a` token and cleared only by a `>`, so an `<a` mentioned in prose or inline
    code rewrote the next `<link href>` — past a blank line and past a whole code
    block — and no guard saw it, because the output was a well-formed blob URL."""

    def test_an_anchor_in_inline_code_opens_nothing(self):
        text = (
            "A sentence mentioning `<a` with no closing bracket.\n"
            '<link rel="stylesheet" href="docs/style.css">\n'
        )
        self.assertEqual(absolutize(text), text)

    def test_the_state_does_not_survive_a_blank_line(self):
        text = 'Use <a\n\n<image href="docs/a.svg"/>\n'
        self.assertEqual(absolutize(text), text)

    def test_the_state_does_not_survive_a_code_block(self):
        text = 'Mentioning <a\n```python\ncode\n```\n<div href="docs/leak.html">\n'
        self.assertEqual(absolutize(text), text)

    def test_a_raw_anchor_word_in_prose_does_not_reach_the_next_tag(self):
        """#1300 lead, round 2: an attribute list cannot contain `<`, so in
        "wrap it in an <a tag" followed by a `<link>` the `<a` was never a tag."""
        text = 'Wrap it in an <a tag\n<link rel="stylesheet" href="docs/a.css">\n'
        self.assertEqual(absolutize(text), text)
        self.assertEqual(relative_anchor_hrefs(text), [])

    def test_the_guard_does_not_blame_a_fence_the_script_respected(self):
        text = 'an <a tag\n```\ncode\n```\n<link href="docs/a.css">\n'
        self.assertEqual(absolutize(text), text)
        self.assertEqual(relative_anchor_hrefs(text), [])

    def test_nothing_that_opened_the_state_reaches_another_element(self):
        """#1300 gate, round 2: three more ways to open the state — a code span
        across lines, a raw `<a` followed by an element on the next line, an HTML
        comment. Whatever opened it, a new `<` before the `>` ends it, so none of
        them reaches the `<link>`."""
        cases = {
            "code span across lines": (
                'Mentioning `<a\ntag` and <link rel="stylesheet" href="docs/s.css">\n'
            ),
            "raw prose": (
                'The <a\nelement and its <link rel="stylesheet" href="docs/s.css"> usage.\n'
            ),
            "comment": (
                '<!-- We can write <a\nand <link rel="stylesheet" href="docs/s.css"> here -->\n'
            ),
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.assertEqual(absolutize(text), text)

    def test_the_hand_formatted_hero_still_works(self):
        """The counterweight: the bounds must not undo #1294."""
        text = '<a\n  class="hero"\n  href="docs/a.md">x</a>\n'
        self.assertEqual(absolutize(text), f'<a\n  class="hero"\n  href="{_BLOB}docs/a.md">x</a>\n')


class EachBoundHoldsOnItsOwn(unittest.TestCase):
    """#1300 lead, round 3: the `<` rule ends the carried state whenever the next
    line opens a tag, so a fixture whose next line starts with `<` pins that rule
    and nothing else. Here the `href` follows with no `<` before it, so only the
    bound under test can stop it."""

    def test_a_blank_line_ends_the_state(self):
        text = '<a\n\n  href="docs/a.md">x</a>\n'
        self.assertEqual(absolutize(text), text)

    def test_a_fence_ends_the_state(self):
        text = 'Mentioning <a\n```\ncode\n```\n  href="docs/a.md">x\n'
        self.assertEqual(absolutize(text), text)

    def test_an_anchor_in_a_code_span_opens_nothing(self):
        text = 'Use `<a` tags\n  href="docs/a.md">x\n'
        self.assertEqual(absolutize(text), text)

    def test_the_guard_does_not_cross_a_blank_line(self):
        self.assertEqual(relative_anchor_hrefs('<a\n\n  href="docs/a.md">x</a>\n'), [])

    def test_the_guard_ignores_an_anchor_in_a_code_span(self):
        self.assertEqual(relative_anchor_hrefs('Use `<a` tags\n  href="docs/a.md">x\n'), [])


class ARootRelativeTargetResolvesFromTheRepositoryRoot(unittest.TestCase):
    """`/docs/a.md` gained a second slash (`…/main//docs/a.md`); on GitHub a leading
    `/` in a README resolves from the repository root, so it is dropped (#1300 review)."""

    def test_a_link(self):
        self.assertEqual(absolutize("[x](/docs/a.md)\n"), f"[x]({_BLOB}docs/a.md)\n")

    def test_an_image(self):
        self.assertEqual(absolutize('<img src="/docs/a.svg">\n'), f'<img src="{_RAW}docs/a.svg">\n')

    def test_the_mangled_guard_stays_silent_on_it(self):
        self.assertEqual(mangled_targets(absolutize("[x](/docs/a.md)\n")), [])


if __name__ == "__main__":
    unittest.main()
