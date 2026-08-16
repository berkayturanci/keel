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


if __name__ == "__main__":
    unittest.main()
