"""Tests for the live-dashboard HTTP server."""

import json
import threading
import unittest
import urllib.request

from keel_visual import serve


def _provider():
    return [{"project": "acme", "label": "#1", "command": "morning"}]


class TestRoute(unittest.TestCase):
    def test_root_serves_page(self):
        status, ctype, body = serve.route(
            "/", board_provider=_provider, page_html="<html>DASH</html>")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertEqual(body, b"<html>DASH</html>")

    def test_index_alias(self):
        status, _, _ = serve.route(
            "/index.html", board_provider=_provider, page_html="x")
        self.assertEqual(status, 200)

    def test_board_json_reads_provider_fresh(self):
        calls = []

        def prov():
            calls.append(1)
            return [{"n": len(calls)}]

        status, ctype, body = serve.route("/board.json", board_provider=prov, page_html="x")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(body), [{"n": 1}])
        # a second request re-reads
        _, _, body2 = serve.route("/board.json?t=2", board_provider=prov, page_html="x")
        self.assertEqual(json.loads(body2), [{"n": 2}])

    def test_unknown_path_is_404(self):
        status, _, body = serve.route("/nope", board_provider=_provider, page_html="x")
        self.assertEqual(status, 404)
        self.assertEqual(body, b"not found")


class TestLiveServer(unittest.TestCase):
    def test_serves_page_and_json_over_http(self):
        httpd = serve.make_server(_provider, "<html>LIVE</html>", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            base = f"http://127.0.0.1:{port}"
            self.assertEqual(
                urllib.request.urlopen(base + "/").read(), b"<html>LIVE</html>")
            board = json.loads(urllib.request.urlopen(base + "/board.json").read())
            self.assertEqual(board, _provider())
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/missing")
            self.assertEqual(ctx.exception.code, 404)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
