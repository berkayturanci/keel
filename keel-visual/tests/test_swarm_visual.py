"""Unit tests for keel-visual swarm 2D DAG and 3D multi-wave topology visualizer."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from keel_visual.cli import load_swarm_template, main
from keel_visual.render import render_swarm_html


class TestSwarmVisual(unittest.TestCase):
    def test_load_swarm_template(self):
        tpl = load_swarm_template()
        self.assertIn("<!doctype html>", tpl)
        self.assertIn("__KEEL_SWARM__", tpl)
        self.assertIn("__TITLE__", tpl)

    def test_render_swarm_html(self):
        tpl = (
            "<html><head><title>__TITLE__</title></head>"
            "<body><script>__KEEL_SWARM__</script></body></html>"
        )
        data = {"swarm_id": "swarm-test", "plan": {"waves": []}}
        html = render_swarm_html(tpl, data, title="My Swarm")
        self.assertIn("<title>My Swarm</title>", html)
        self.assertIn('"swarm_id": "swarm-test"', html)

    def test_cmd_swarm_missing_and_invalid_config(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = main(["swarm", "nonexistent.yaml"])
        self.assertEqual(code, 1)
        self.assertIn("no such config", buf.getvalue())

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write("invalid_content: true\n")
            path = tf.name

        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                code = main(["swarm", path])
            self.assertEqual(code, 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_cmd_swarm_json_and_offline_fixture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fix_path = Path(tmpdir) / "swarm.json"
            fix_data = {
                "swarm_id": "swarm-fixture",
                "plan": {
                    "waves": [{"wave_index": 1, "eligible_direct_landing": True, "clusters": []}]
                },
                "state": {
                    "workers": [
                        {"cluster_id": "c1", "issue": 101, "role": "core", "status": "passed"}
                    ]
                },
            }
            fix_path.write_text(json.dumps(fix_data), encoding="utf-8")

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["swarm", "--swarm-json", str(fix_path), "--json"])
            self.assertEqual(code, 0)
            parsed = json.loads(buf.getvalue())
            self.assertEqual(parsed["swarm_id"], "swarm-fixture")

    def test_cmd_swarm_live_state_and_out(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_tmp = Path(tmpdir)
            state_dir = p_tmp / ".keel" / "state" / "swarm"
            state_dir.mkdir(parents=True)
            st_file = state_dir / "swarm-run-1.json"
            st_file.write_text(
                json.dumps({
                    "swarm_id": "swarm-run-1",
                    "total_workers": 1,
                    "workers": [
                        {"cluster_id": "c1", "issue": 714, "role": "docs", "status": "passed"}
                    ],
                }),
                encoding="utf-8",
            )

            out_html = p_tmp / "out.html"
            code = main([
                "swarm",
                ".keel/project.yaml",
                "--root",
                tmpdir,
                "--out",
                str(out_html),
            ])
            self.assertEqual(code, 0)
            self.assertTrue(out_html.exists())
            content = out_html.read_text(encoding="utf-8")
            self.assertIn("swarm-run-1", content)

            # Test explicit --swarm-id
            out_html2 = p_tmp / "out2.html"
            code2 = main([
                "swarm",
                "--root",
                tmpdir,
                "--swarm-id",
                "swarm-run-1",
                "--out",
                str(out_html2),
            ])
            self.assertEqual(code2, 0)
            self.assertTrue(out_html2.exists())

            # Test explicit --swarm-id that does not exist in state_dir
            code_missing_id = main([
                "swarm",
                "--root",
                tmpdir,
                "--swarm-id",
                "nonexistent-id",
                "--json",
            ])
            self.assertEqual(code_missing_id, 0)

            # Corrupt state file
            bad_file = state_dir / "bad.json"
            bad_file.write_text("{corrupt json", encoding="utf-8")
            code_bad = main(["swarm", "--root", tmpdir, "--swarm-id", "bad", "--json"])
            self.assertEqual(code_bad, 0)

            # State dir exists but no .json files
            for f in state_dir.glob("*.json"):
                f.unlink()
            code_no_json = main(["swarm", "--root", tmpdir, "--json"])
            self.assertEqual(code_no_json, 0)

    def test_cmd_swarm_serve_mocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_httpd = MagicMock()
            mock_httpd.server_address = ("127.0.0.1", 8766)
            mock_httpd.serve_forever.side_effect = KeyboardInterrupt()

            def mock_make_server(provider_fn, page, host, port):
                # Call provider function to test provider closure
                data = provider_fn()
                self.assertIn("swarm_id", data)
                return mock_httpd

            with patch("keel_visual.serve.make_server", side_effect=mock_make_server):
                out_html = Path(tmpdir) / "serve_out.html"
                code = main([
                    "swarm",
                    "--root",
                    tmpdir,
                    "--out",
                    str(out_html),
                    "--serve",
                ])
                self.assertEqual(code, 0)
                mock_httpd.serve_forever.assert_called_once()
                mock_httpd.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
