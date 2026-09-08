"""Unit tests for the website integrations and ecosystem catalog."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = REPO_ROOT / "website"


class TestWebsiteIntegrations(unittest.TestCase):
    def test_integrations_js_present_and_valid(self):
        js_file = WEBSITE_DIR / "integrations.js"
        self.assertTrue(js_file.exists(), "website/integrations.js must exist")
        content = js_file.read_text(encoding="utf-8")

        # Check key integrations present
        self.assertIn("Claude Code", content)
        self.assertIn("Cursor", content)
        self.assertIn("Gemini CLI", content)
        self.assertIn("Google Antigravity", content)
        self.assertIn("OpenAI Codex", content)
        self.assertIn("Devin", content)
        self.assertIn("Ollama (Local / Offline)", content)
        self.assertIn("DeepSeek V3 / R1", content)
        self.assertIn("Addy Osmani Agent Skills", content)
        self.assertIn("Official GitHub Action", content)
        self.assertIn("Homebrew Tap", content)
        self.assertIn("VS Code & Cursor Extension", content)

        # Check categories present
        self.assertIn('"assistants"', content)
        self.assertIn('"backends"', content)
        self.assertIn('"skills"', content)
        self.assertIn('"platforms"', content)

    def test_all_logos_exist_on_disk(self):
        js_file = WEBSITE_DIR / "integrations.js"
        content = js_file.read_text(encoding="utf-8")
        logos = re.findall(r'logo:\s*"(logos/[^"]+)"', content)
        self.assertGreaterEqual(len(logos), 25, "Must have at least 25 integration logos")

        for rel_path in logos:
            logo_file = WEBSITE_DIR / rel_path
            self.assertTrue(
                logo_file.exists(),
                f"Logo file {rel_path} must exist in website directory",
            )
            self.assertGreater(
                logo_file.stat().st_size, 0, f"Logo file {rel_path} must not be empty"
            )

    def test_index_html_has_integrations_view(self):
        html_file = WEBSITE_DIR / "index.html"
        self.assertTrue(html_file.exists(), "website/index.html must exist")
        content = html_file.read_text(encoding="utf-8")

        self.assertIn('data-view="integrations"', content)
        self.assertIn('id="view-integrations"', content)
        self.assertIn('id="integrations-grid"', content)
        self.assertIn('id="integrations-search"', content)
        self.assertIn('src="integrations.js"', content)

    def test_styles_css_has_integration_classes(self):
        css_file = WEBSITE_DIR / "styles.css"
        self.assertTrue(css_file.exists(), "website/styles.css must exist")
        content = css_file.read_text(encoding="utf-8")

        self.assertIn(".integ-grid", content)
        self.assertIn(".integ-card", content)
        self.assertIn(".integ-pill", content)
        self.assertIn(".integ-search", content)
        self.assertIn(".integ-icon-img", content)


class TheLiveRegionIsTheOnlyOneAndEveryUpdaterUsesIt(unittest.TestCase):
    """One live region per page, and it is the dedicated off-screen one.

    `aria-live` on the results grid made a container full of interactive
    elements announce itself — the anti-pattern this replaced. What makes the
    replacement work is that the page has exactly *one* live region and it is
    `#sr-live-region`: two of them compete, and none leaves a screen-reader user
    with no feedback at all. `app.js` and `docs.js` already wrote to it; this
    pins the arrangement rather than leaving it to the next edit.
    """

    PAGES = ("index.html", "docs.html")
    UPDATERS = ("app.js", "docs.js", "integrations.js")

    def _read(self, name: str) -> str:
        return (REPO_ROOT / "website" / name).read_text(encoding="utf-8")

    def test_each_page_has_exactly_one_live_region(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                markup = self._read(page)
                regions = re.findall(r'<[^>]*aria-live="[^"]*"[^>]*>', markup)

                self.assertEqual(len(regions), 1, f"live regions found: {regions}")
                self.assertIn('id="sr-live-region"', regions[0])
                self.assertIn('class="sr-only"', regions[0])
                self.assertIn('aria-live="polite"', regions[0])

    def test_the_results_grid_carries_no_live_region(self):
        """The anti-pattern, named so it cannot come back quietly."""
        markup = self._read("index.html")
        grid = re.search(r'<div[^>]*id="integrations-grid"[^>]*>', markup)

        self.assertIsNotNone(grid, "the integrations grid is gone")
        self.assertNotIn("aria-live", grid.group(0))

    def test_every_dynamic_updater_announces_through_it(self):
        for script in self.UPDATERS:
            with self.subTest(script=script):
                self.assertIn('getElementById("sr-live-region")', self._read(script))

    def test_it_announces_the_two_states_the_change_promises(self):
        """The strings themselves, which the mechanism tests do not read."""
        source = self._read("integrations.js")

        self.assertIn("'Showing ' + items.length + ' integrations'", source)
        self.assertIn("'No integrations found matching \"' + searchQuery + '\"'", source)
        self.assertIn("items.length === 0", source)

    def test_it_stays_quiet_until_the_reader_has_searched(self):
        """`renderGrid` also runs from `init()`, with this grid hidden.

        Announcing "Showing 32 integrations" there interrupts a reader who is
        still on the overview and has not opened the catalogue.
        """
        source = self._read("integrations.js")

        self.assertIn(
            'var sr = searchQuery ? document.getElementById("sr-live-region") : null;', source
        )

    def test_the_strict_directive_is_the_first_statement(self):
        """A `var` above it ends the Directive Prologue and un-stricts the IIFE.

        That is not a style point: the string stays, the mode does not, and
        every accidental global in 400 lines stops throwing. Caught here after
        being introduced by the live-region timer.
        """
        source = self._read("integrations.js")
        body = source.split("(function () {", 1)[1].lstrip()

        self.assertTrue(body.startswith('"use strict";'), body[:60])

    def test_the_search_announcement_can_repeat_itself(self):
        """A live region announces a *change*, so an identical string is silence.

        Two searches can produce the same count — "cla" and "clau" both showing
        three — and leaving the text alone says nothing while the results moved.
        The region is cleared before the message is set, on a timer the next
        keystroke cancels.
        """
        source = self._read("integrations.js")

        self.assertIn('sr.textContent = "";', source)
        self.assertIn("clearTimeout(srTimer)", source)
        self.assertRegex(source, r"srTimer = setTimeout\(")


if __name__ == "__main__":
    unittest.main()
