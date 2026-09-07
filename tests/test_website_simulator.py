"""Unit tests for the website Swarm DAG simulator and client-side assets."""

from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestWebsiteSwarmSimulator(unittest.TestCase):
    def test_simulator_assets_and_markup_present(self):
        index_html = (REPO_ROOT / "website" / "index.html").read_text(encoding="utf-8")
        sim_js = (REPO_ROOT / "website" / "swarm-simulator.js").read_text(encoding="utf-8")
        styles_css = (REPO_ROOT / "website" / "styles.css").read_text(encoding="utf-8")

        # Container in view-swarm
        self.assertIn('id="swarm-simulator-container"', index_html)

        # Script inclusion
        self.assertIn('<script src="swarm-simulator.js"></script>', index_html)

        # Presets in JS
        self.assertIn("microservices", sim_js)
        self.assertIn("fullstack", sim_js)
        self.assertIn("conflict", sim_js)

        # Multi-model and multi-vendor references. Asserted by *vendor*, not by
        # model id: pinning two ids here made a model-id refresh (#1019) fail a
        # test whose subject is "the simulator shows more than one vendor", and
        # a vendor's catalogue is not this repo's to keep current.
        for vendor in ("Anthropic", "Google", "OpenAI", "DeepSeek"):
            self.assertIn(f'vendor: "{vendor}"', sim_js)
        self.assertIn("codex", sim_js)

        # Styles present
        self.assertIn(".swarm-sandbox", styles_css)
        self.assertIn(".sim-dag-layout", styles_css)
        self.assertIn(".sim-metrics-bar", styles_css)


class _AccessibleName(HTMLParser):
    """The text a screen reader announces for one element, by id.

    `aria-hidden` subtrees are skipped, because that is what the browser does:
    the `↻` glyph in the replay button is decorative and contributes nothing to
    the accessible name. Nothing else here is a general implementation of the
    accessible-name algorithm — it reads one button in one file.
    """

    def __init__(self, element_id: str):
        super().__init__()
        self.element_id = element_id
        self.attrs: dict[str, str] = {}
        self.text: list[str] = []
        self._depth = 0
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        got = dict(attrs)
        if self._depth:
            self._depth += 1
            if got.get("aria-hidden") == "true" or self._hidden:
                self._hidden += 1
        elif got.get("id") == self.element_id:
            self.attrs = got
            self._depth = 1

    def handle_endtag(self, tag):
        if self._depth:
            if self._hidden:
                self._hidden -= 1
            self._depth -= 1

    def handle_data(self, data):
        if self._depth and not self._hidden:
            self.text.append(data)

    @property
    def visible(self) -> str:
        return " ".join("".join(self.text).split())


class TestReplayButtonAccessibleName(unittest.TestCase):
    """WCAG 2.5.3 Label in Name: the accessible name must contain the visible text.

    The replay button carries an `aria-label` that adds the context its visible
    text leaves out — that what is replayed is the animation above it. An
    `aria-label` *overrides* the visible text as the accessible name, so the two
    can then diverge silently: change the button's words and a screen reader, and
    any speech-input user saying "click replay the run", still gets the old label.

    That divergence class is already in this repository's `.jules/palette.md`
    (2026-08-18), where a handler restored an `aria-label` to a captured value and
    left the button announcing "copied" while its visible text had recovered.
    Nothing pinned the relationship then. This pins it.
    """

    def _button(self, element_id: str) -> _AccessibleName:
        parser = _AccessibleName(element_id)
        parser.feed((REPO_ROOT / "website" / "index.html").read_text(encoding="utf-8"))
        self.assertTrue(parser.attrs, f"no element with id={element_id!r} in index.html")
        return parser

    def test_the_visible_text_survives_inside_the_accessible_name(self):
        button = self._button("replay")
        label = button.attrs.get("aria-label", "")

        self.assertTrue(label, "the replay button has no aria-label")
        self.assertIn(
            button.visible.lower(),
            label.lower(),
            f"aria-label {label!r} does not contain the visible text "
            f"{button.visible!r}, so speech input cannot activate this button",
        )

    def test_the_accessible_name_starts_with_the_visible_text(self):
        """Stronger than 2.5.3 requires, and the shape it recommends.

        A visible label at the *start* of the accessible name is what lets a
        speech-input user say the words they can see and be understood.
        """
        button = self._button("replay")

        self.assertTrue(button.attrs["aria-label"].lower().startswith(button.visible.lower()))

    def test_the_decorative_glyph_is_not_part_of_the_name(self):
        """If the `↻` ever loses `aria-hidden`, the assertions above stop holding."""
        self.assertNotIn("\u21bb", self._button("replay").visible)


class TestCopyButtonFlash(unittest.TestCase):
    """`flashCopy` swaps a button's label to "copied" and restores it after 1400ms.

    It has to be re-entrant. Without the guard, a second click inside that window
    captures the *transient* label as the value to restore, and the later timer
    puts "copied" back permanently — leaving a button that no longer says what it
    copies. The visible text recovers on the next click because the next capture
    re-reads it; the aria-label does not, so a screen-reader user is left with a
    button announcing "copied" forever.
    """

    def _flash_copy(self) -> str:
        source = (REPO_ROOT / "website" / "app.js").read_text(encoding="utf-8")
        start = source.index("function flashCopy(")
        end = source.index("\n  }", start)
        return source[start:end]

    def test_flash_copy_returns_early_while_already_flashing(self):
        body = self._flash_copy()
        guard = 'if (btn.classList.contains("done")) return;'
        self.assertIn(guard, body)
        # The guard is only a guard if nothing is captured before it. `done` is
        # added and removed on exactly the flash window, so reading either label
        # above this line reads the transient value.
        self.assertLess(
            body.index(guard),
            body.index("getAttribute"),
            "the early return must come before the label is captured",
        )

    def test_flash_copy_restores_both_labels(self):
        body = self._flash_copy()
        self.assertIn('btn.setAttribute("aria-label"', body)
        self.assertIn("removeAttribute", body)  # no aria-label before: remove, not set ""
        self.assertIn("textContent = prev", body)

    def test_the_two_labels_do_not_disagree(self):
        # Sighted and screen-reader users never compare them, but a mismatch
        # reads as an oversight to the next person editing this.
        body = self._flash_copy()
        self.assertIn('textContent = "copied"', body)
        self.assertIn('setAttribute("aria-label", "copied")', body)


class TestSimulatorCopyButtonLabels(unittest.TestCase):
    """The simulator's copy button restores both labels to shipped constants.

    Re-reading the live ``aria-label`` inside the clipboard callback captures
    whatever is there *now*. A click landing inside the 2s flash window captures
    the transient "Copied to clipboard" and the later timer restores that, so the
    visible text returns to "Copy" while the accessible name stays "Copied to
    clipboard" until a re-render rebuilds the DOM. Sighted users see nothing
    wrong; a screen-reader user gets a button that lies about what it does.
    """

    def _handler(self) -> str:
        source = (REPO_ROOT / "website" / "swarm-simulator.js").read_text(encoding="utf-8")
        start = source.index('var copyBtn = document.getElementById("sim-copy-cli");')
        return source[start : source.index("\n    }", start)]

    def test_handler_does_not_re_read_the_live_aria_label(self):
        # The root cause. Any capture of the live label is the bug returning.
        self.assertNotIn('getAttribute("aria-label")', self._handler())

    def test_both_labels_restore_to_constants(self):
        handler = self._handler()
        self.assertIn("COPY_TEXT", handler)
        self.assertIn("COPY_ARIA", handler)
        self.assertIn("copyBtn.textContent = COPY_TEXT;", handler)
        self.assertIn('copyBtn.setAttribute("aria-label", COPY_ARIA);', handler)

    def test_pending_timer_is_cleared_per_click(self):
        # Otherwise an earlier timer fires mid-flash and cuts the later one short.
        handler = self._handler()
        self.assertIn("clearTimeout(copyResetTimer)", handler)
        self.assertLess(
            handler.index("clearTimeout(copyResetTimer)"),
            handler.index("copyResetTimer = setTimeout"),
            "the pending timer must be cleared before the next one is armed",
        )

    def test_constants_match_the_rendered_button(self):
        # The constants are only correct while they equal what the button renders
        # with. The button is emitted by this same file's template, so a reworded
        # label would otherwise silently disagree with what the timer restores.
        source = (REPO_ROOT / "website" / "swarm-simulator.js").read_text(encoding="utf-8")
        start = source.index('id="sim-copy-cli"')
        button = source[source.rindex("<button", 0, start) : source.index("</button>", start)]
        self.assertIn('aria-label="Copy CLI command"', button)
        self.assertTrue(button.endswith(">Copy"), f"unexpected button text: {button!r}")
        handler = self._handler()
        self.assertIn('var COPY_ARIA = "Copy CLI command";', handler)
        self.assertIn('var COPY_TEXT = "Copy";', handler)


if __name__ == "__main__":
    unittest.main()
