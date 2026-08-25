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

    def test_script_breakout_is_neutralised(self):
        tmpl = "<script>window.KEEL_RUN = __KEEL_RUN__;</script>"
        out = render.render_html(tmpl, {"x": "</script><img src=x onerror=alert(1)>"}, title="t")
        # No literal </script> or angle brackets from the payload survive.
        self.assertNotIn("</script><img", out)
        self.assertIn("\\u003c", out)
        # Still valid JSON once the unicode escapes are parsed back.
        body = out[len("<script>window.KEEL_RUN = ") : out.index(";</script>")]
        self.assertEqual(json.loads(body), {"x": "</script><img src=x onerror=alert(1)>"})

    def test_ampersand_escaped(self):
        out = render.render_html("__KEEL_RUN__", {"a": "x&y"})
        self.assertIn("\\u0026", out)


class TestRenderBoardHtml(unittest.TestCase):
    def test_substitutes_board_and_title(self):
        tmpl = "<title>__TITLE__</title><script>window.KEEL_BOARD = __KEEL_BOARD__;</script>"
        out = render.render_board_html(tmpl, [{"project": "a", "label": "#1"}], title="board")
        self.assertIn("<title>board</title>", out)
        self.assertIn('window.KEEL_BOARD = [{"label": "#1", "project": "a"}];', out)

    def test_default_title_and_script_safe(self):
        self.assertEqual(render.render_board_html("__TITLE__", []), "keel board")
        out = render.render_board_html(
            "<script>__KEEL_BOARD__</script>", [{"project": "</script>"}]
        )
        self.assertNotIn("</script></script>", out)
        self.assertIn("\\u003c", out)


if __name__ == "__main__":
    unittest.main()
