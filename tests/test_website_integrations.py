"""Unit tests for the website integrations and ecosystem catalog."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
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


#: Drives the real `integrations.js` against a minimal DOM and reports what the
#: live region said at each step. Written out and run under node, because the
#: assertions above it read the file as text and a source grep cannot tell a
#: working announcement from a deleted one — the criticism that produced this.
_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
function el(id) {
  const node = { id, innerHTML: "", _attrs: {}, onclick: null, oninput: null, writes: [],
    classList: { add() {}, remove() {}, contains: () => false },
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k] ?? null; } };
  // Every write is recorded, not just the final value: "the region ends up
  // saying X" cannot tell an announcement that was re-set from one that was
  // never cleared, and clearing is what makes an identical repeat audible.
  let value = "";
  Object.defineProperty(node, "textContent", {
    get() { return value; },
    set(v) { value = v; node.writes.push(v); },
  });
  return node;
}
const nodes = {
  "sr-live-region": el("sr-live-region"),
  "integrations-grid": el("integrations-grid"),
  "integrations-search": el("integrations-search"),
  "integrations-count": el("integrations-count"),
};
const pill = el("pill"); pill._attrs["data-cat"] = "agents";
const document = {
  readyState: "complete",
  getElementById: (id) => nodes[id] || null,
  querySelectorAll: (sel) => (sel.includes("data-cat") ? [pill] : []),
  addEventListener() {},
};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"),
  { document, setTimeout, clearTimeout, console, window: {} });
const sr = nodes["sr-live-region"], search = nodes["integrations-search"];
const out = {}, tick = () => new Promise((r) => setTimeout(r, 5));
const since = () => { const w = sr.writes.slice(); sr.writes.length = 0; return w; };
(async () => {
  await tick();                       // let any pending timer fire first
  out.initial = since();
  search.oninput({ target: { value: "claude" } }); await tick();
  out.searched = since();
  search.oninput({ target: { value: "claude" } }); await tick();
  out.repeated = since();
  search.oninput({ target: { value: "ollama" } }); await tick();
  out.one_match = since();
  search.oninput({ target: { value: "zzzznope" } }); await tick();
  out.empty_result = since();
  search.oninput({ target: { value: "" } }); await tick();
  out.cleared = since();
  console.log(JSON.stringify(out));
})();
"""

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "needs node to execute the page script")
class TheAnnouncementIsExercisedRatherThanGrepped(unittest.TestCase):
    """The same script the site ships, run against a stub DOM.

    Every other assertion in this file reads `integrations.js` as text, which
    cannot tell a working announcement from a deleted one. This drives it.
    """

    @classmethod
    def setUpClass(cls):
        workdir = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, workdir, True)
        driver = Path(workdir) / "drive.js"
        driver.write_text(_DRIVER, encoding="utf-8")
        done = subprocess.run(
            [NODE, str(driver), str(REPO_ROOT / "website" / "integrations.js")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 0, done.stderr
        cls.said = json.loads(done.stdout)

    def test_the_initial_render_writes_nothing(self):
        """`init()` runs with this grid hidden behind the overview.

        Asserted on the writes, after the timers have run — reading the value
        synchronously passes whether or not a `setTimeout` is about to speak.
        """
        self.assertEqual(self.said["initial"], [])

    def test_a_search_announces_the_count(self):
        self.assertRegex(self.said["searched"][-1], r"^Showing \d+ integrations$")

    def test_a_single_match_is_announced_in_the_singular(self):
        """ "Showing 1 integrations" is the sentence a reader actually hears."""
        self.assertEqual(self.said["one_match"][-1], "Showing 1 integration")

    def test_an_identical_search_is_cleared_and_said_again(self):
        """A live region announces a change, so an unchanged value is silence.

        The clear is the mechanism, so the writes must show it: "" and then the
        message. Without it the second search leaves the same string in place.
        """
        self.assertEqual(self.said["repeated"][0], "")
        self.assertRegex(self.said["repeated"][-1], r"^Showing \d+ integrations$")
        self.assertGreaterEqual(len(self.said["repeated"]), 2)

    def test_no_matches_names_the_query(self):
        self.assertEqual(self.said["empty_result"][-1], 'No integrations found matching "zzzznope"')

    def test_clearing_the_box_announces_the_full_set(self):
        """Emptying the box is a result-set change, and a `searchQuery` gate
        silenced exactly that — the regression this test exists for."""
        self.assertRegex(self.said["cleared"][-1], r"^Showing \d+ integrations$")
        self.assertNotEqual(self.said["cleared"][-1], self.said["searched"][-1])


if __name__ == "__main__":
    unittest.main()
