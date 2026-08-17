"""Unit tests for the website Swarm DAG simulator and client-side assets."""

from __future__ import annotations

import unittest
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

        # Multi-model and multi-vendor references
        self.assertIn("claude-3-7-sonnet", sim_js)
        self.assertIn("gemini-2.5-flash", sim_js)
        self.assertIn("codex", sim_js)
        self.assertIn("deepseek-r1", sim_js)

        # Styles present
        self.assertIn(".swarm-sandbox", styles_css)
        self.assertIn(".sim-dag-layout", styles_css)
        self.assertIn(".sim-metrics-bar", styles_css)


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


if __name__ == "__main__":
    unittest.main()
