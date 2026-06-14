"""Tests for the pure HTML render substitution."""

import json
import unittest

from keel_visual import render


class TestRenderHtml(unittest.TestCase):
    def test_substitutes_run_and_title(self):
        tmpl = "<title>__TITLE__</title><script>window.KEEL_RUN = __KEEL_RUN__;</script>"
        out = render.render_html(tmpl, {"a": 1, "b": [2, 3]}, title="ship")
        self.assertIn("<title>ship</title>", out)
        self.assertIn('window.KEEL_RUN = {"a": 1, "b": [2, 3]};', out)

    def test_run_state_is_sorted_for_determinism(self):
        tmpl = "__KEEL_RUN__"
        a = render.render_html(tmpl, {"b": 1, "a": 2}, title="t")
        b = render.render_html(tmpl, {"a": 2, "b": 1}, title="t")
        self.assertEqual(a, b)
        self.assertEqual(json.loads(a), {"a": 2, "b": 1})

    def test_title_is_escaped(self):
        out = render.render_html("<h1>__TITLE__</h1>", {}, title="<x> & y")
        self.assertIn("&lt;x&gt; &amp; y", out)
        self.assertNotIn("<x>", out)

    def test_default_title(self):
        out = render.render_html("__TITLE__", {})
        self.assertEqual(out, "keel run")


if __name__ == "__main__":
    unittest.main()
