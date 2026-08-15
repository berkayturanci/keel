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


if __name__ == "__main__":
    unittest.main()
