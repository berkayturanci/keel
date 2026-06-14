"""Tests for the keel-visual CLI (I/O thin layer)."""

import io
import unittest
from argparse import Namespace
from pathlib import Path

from keel_visual import cli

FIX = Path(__file__).resolve().parent / "fixtures"
PROJECT = str(FIX / "project.yaml")
SAMPLE = str(FIX / "sample-run.jsonl")


def _args(**kw):
    base = dict(
        path=PROJECT, root=str(FIX), pr=361, ledger_jsonl=SAMPLE,
        checkpoint_step=None, command="ship", out=None,
        style="flow", fps=2, step=None, once=False, no_clear=True, color="never",
        loop=False, follow=False, interval=1.0,
    )
    base.update(kw)
    return Namespace(**base)


class TestTemplate(unittest.TestCase):
    def test_load_template_has_placeholders(self):
        tmpl = cli.load_template()
        self.assertIn("__KEEL_RUN__", tmpl)
        self.assertIn("__TITLE__", tmpl)


class TestResolveRecord(unittest.TestCase):
    def test_fixture_by_pr(self):
        from keel import config as cfg
        rec = cli._resolve_record(_args(), cfg.load_config(PROJECT))
        self.assertEqual(rec["pull_request"]["number"], 361)

    def test_fixture_latest_when_no_pr(self):
        from keel import config as cfg
        rec = cli._resolve_record(_args(pr=None), cfg.load_config(PROJECT))
        self.assertEqual(rec["record_type"], "ship_run")

    def test_pr_no_match_returns_none(self):
        from keel import config as cfg
        self.assertIsNone(cli._resolve_record(_args(pr=999), cfg.load_config(PROJECT)))

    def test_live_empty_root_returns_none(self, ):
        import tempfile

        from keel import config as cfg
        with tempfile.TemporaryDirectory() as d:
            rec = cli._resolve_record(_args(ledger_jsonl=None, root=d, pr=None),
                                      cfg.load_config(PROJECT))
        self.assertIsNone(rec)


class TestRender(unittest.TestCase):
    def test_render_writes_html(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run.html"
            rc = cli.cmd_render(_args(out=str(out)))
        self.assertEqual(rc, 0)

    def test_render_writes_and_injects(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run.html"
            rc = cli.cmd_render(_args(out=str(out)))
            html = out.read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        self.assertIn("window.KEEL_RUN =", html)
        self.assertIn('"pr": 361', html)

    def test_render_missing_config(self):
        self.assertEqual(cli.cmd_render(_args(path="no-such.yaml")), 1)

    def test_render_invalid_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.jsonl"
            bad.write_text("{not json", encoding="utf-8")
            rc = cli.cmd_render(_args(ledger_jsonl=str(bad), out=str(Path(d) / "o.html")))
        self.assertEqual(rc, 1)

    def test_render_invalid_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "p.yaml"
            bad.write_text("extends: keel\nbase_branch: main\ngates: [build]\n", encoding="utf-8")
            rc = cli.cmd_render(_args(path=str(bad)))
        self.assertEqual(rc, 1)


class TestPlay(unittest.TestCase):
    def test_play_single_step(self):
        out = io.StringIO()
        rc = cli.cmd_play(_args(step=8), sleep=lambda s: None, out=out)
        self.assertEqual(rc, 0)
        self.assertIn("s8 · test", out.getvalue())

    def test_play_once(self):
        out = io.StringIO()
        rc = cli.cmd_play(_args(once=True, pr=361), sleep=lambda s: None, out=out)
        self.assertEqual(rc, 0)
        self.assertIn("s12 · close", out.getvalue())

    def test_play_full_animation_calls_sleep(self):
        out = io.StringIO()
        calls = []
        rc = cli.cmd_play(_args(command="review", no_clear=False),
                          sleep=lambda s: calls.append(s), out=out)
        self.assertEqual(rc, 0)
        # review exercises 5 steps -> 4 inter-frame sleeps, and clears between frames.
        self.assertEqual(len(calls), 4)
        self.assertIn("\x1b[2J", out.getvalue())

    def test_play_color_always(self):
        out = io.StringIO()
        cli.cmd_play(_args(step=8, color="always"), sleep=lambda s: None, out=out)
        self.assertIn("\x1b[38;5;", out.getvalue())

    def test_play_color_auto_non_tty(self):
        out = io.StringIO()
        cli.cmd_play(_args(step=12, color="auto"), sleep=lambda s: None, out=out)
        # io.StringIO is not a tty -> auto disables colour.
        self.assertNotIn("\x1b[38;5;", out.getvalue())

    def test_play_config_error(self):
        rc = cli.cmd_play(_args(path="no-such.yaml"), sleep=lambda s: None, out=io.StringIO())
        self.assertEqual(rc, 1)

    def test_play_invalid_config_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "p.yaml"
            bad.write_text("extends: keel\nbase_branch: main\ngates: [build]\n", encoding="utf-8")
            rc = cli.cmd_play(_args(path=str(bad)), sleep=lambda s: None, out=io.StringIO())
        self.assertEqual(rc, 1)

    def test_play_ledger_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.jsonl"
            bad.write_text("{nope", encoding="utf-8")
            rc = cli.cmd_play(_args(ledger_jsonl=str(bad)), sleep=lambda s: None, out=io.StringIO())
        self.assertEqual(rc, 1)

    def test_play_empty_run_uses_active_index(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            rc = cli.cmd_play(_args(ledger_jsonl=None, root=d, pr=None),
                              sleep=lambda s: None, out=out)
        self.assertEqual(rc, 0)
        self.assertIn("s0 · config", out.getvalue())


class _TTY(io.StringIO):
    def isatty(self):
        return True


class TestCheckpointResolve(unittest.TestCase):
    def _config(self):
        from keel import config as cfg
        return cfg.load_config(PROJECT)

    def test_explicit_step_wins(self):
        self.assertEqual(
            cli._resolve_checkpoint_step(_args(checkpoint_step="s5"), self._config()), "s5"
        )

    def test_missing_checkpoint_is_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                cli._resolve_checkpoint_step(_args(root=d, checkpoint_step=None), self._config())
            )

    def test_corrupt_checkpoint_is_none(self):
        import tempfile

        from keel import checkpoint
        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            cp = checkpoint.resolve_path(d, config)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text("{not json", encoding="utf-8")  # parse -> CheckpointError
            self.assertIsNone(
                cli._resolve_checkpoint_step(_args(root=d, checkpoint_step=None), config)
            )

    def test_reads_current_step_from_checkpoint_file(self):
        import tempfile

        from keel import checkpoint
        config = self._config()
        record = checkpoint.build_checkpoint_record(
            run_id="r1", command="ship", current_step="s6", base_branch="main",
        )
        with tempfile.TemporaryDirectory() as d:
            cp = checkpoint.resolve_path(d, config)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(checkpoint.encode_checkpoint(record), encoding="utf-8")
            step = cli._resolve_checkpoint_step(_args(root=d, checkpoint_step=None), config)
        self.assertEqual(step, "s6")


class TestLoopAndFollow(unittest.TestCase):
    def test_loop_replays_until_max_cycles(self):
        out = io.StringIO()
        rc = cli.cmd_play(_args(command="review", loop=True), sleep=lambda s: None,
                          out=out, max_cycles=2)
        self.assertEqual(rc, 0)
        # the s0 pointer ("▲ s0 · config") renders once per cycle -> 2 cycles.
        self.assertEqual(out.getvalue().count("s0 · config"), 2)

    def test_loop_keyboard_interrupt_exits_clean(self):
        out = io.StringIO()

        def boom(_):
            raise KeyboardInterrupt

        rc = cli.cmd_play(_args(command="review", loop=True), sleep=boom, out=out, max_cycles=None)
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().endswith("\n"))

    def test_follow_bounded_redraws_current_step(self):
        out = io.StringIO()
        calls = []
        rc = cli.cmd_play(_args(follow=True, no_clear=False), sleep=lambda s: calls.append(s),
                          out=out, max_cycles=3)
        self.assertEqual(rc, 0)
        # merged sample sits at s12; each tick clears + redraws that frame.
        self.assertEqual(out.getvalue().count("s12 · close"), 3)
        self.assertEqual(out.getvalue().count("\x1b[2J"), 3)
        self.assertEqual(len(calls), 3)

    def test_follow_fatal_config_error_returns_code(self):
        rc = cli.cmd_play(_args(follow=True, path="no-such.yaml"),
                          sleep=lambda s: None, out=io.StringIO(), max_cycles=2)
        self.assertEqual(rc, 1)

    def test_follow_transient_ledger_error_keeps_polling(self):
        # A live run can leave a half-written ledger line; the follower must not
        # die — it skips the tick and shows a waiting line until a good read.
        import tempfile
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.jsonl"
            bad.write_text("{half-written", encoding="utf-8")
            rc = cli.cmd_play(_args(follow=True, ledger_jsonl=str(bad)),
                              sleep=lambda s: None, out=out, max_cycles=2)
        self.assertEqual(rc, 0)  # survived, did not exit on the bad read
        self.assertIn("waiting for run state", out.getvalue())

    def test_follow_keyboard_interrupt_exits_clean(self):
        out = io.StringIO()

        def boom(_):
            raise KeyboardInterrupt

        rc = cli.cmd_play(_args(follow=True), sleep=boom, out=out, max_cycles=None)
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().endswith("\n"))

    def test_color_auto_on_tty_stream(self):
        out = _TTY()
        cli.cmd_play(_args(step=8, color="auto"), sleep=lambda s: None, out=out)
        self.assertIn("\x1b[38;5;", out.getvalue())


class TestMain(unittest.TestCase):
    def test_main_render(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.html"
            rc = cli.main([
                "render", PROJECT, "--root", str(FIX),
                "--ledger-jsonl", SAMPLE, "--pr", "361", "--out", str(out),
            ])
        self.assertEqual(rc, 0)

    def test_main_play(self):
        rc = cli.main([
            "play", PROJECT, "--root", str(FIX),
            "--ledger-jsonl", SAMPLE, "--pr", "361", "--step", "8", "--color", "never",
        ])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
