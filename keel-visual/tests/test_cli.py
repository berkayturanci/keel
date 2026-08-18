"""Tests for the keel-visual CLI (I/O thin layer)."""

import io
import types
import unittest
from argparse import Namespace
from pathlib import Path

from keel_visual import cli

FIX = Path(__file__).resolve().parent / "fixtures"
PROJECT = str(FIX / "project.yaml")
SAMPLE = str(FIX / "sample-run.jsonl")


def _args(**kw):
    base = dict(
        path=PROJECT,
        root=str(FIX),
        pr=361,
        ledger_jsonl=SAMPLE,
        checkpoint_step=None,
        command="ship",
        out=None,
        style="flow",
        fps=2,
        step=None,
        once=False,
        no_clear=True,
        color="never",
        loop=False,
        follow=False,
        interval=1.0,
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

    def test_live_empty_root_returns_none(
        self,
    ):
        import tempfile

        from keel import config as cfg

        with tempfile.TemporaryDirectory() as d:
            rec = cli._resolve_record(
                _args(ledger_jsonl=None, root=d, pr=None), cfg.load_config(PROJECT)
            )
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
        rc = cli.cmd_play(
            _args(command="overnight", no_clear=False), sleep=lambda s: calls.append(s), out=out
        )
        self.assertEqual(rc, 0)
        # overnight's flow is 5 phases -> 4 inter-frame sleeps, and clears between.
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
            rc = cli.cmd_play(
                _args(ledger_jsonl=None, root=d, pr=None), sleep=lambda s: None, out=out
            )
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
        step, state, run_id = cli._resolve_checkpoint(_args(checkpoint_step="s5"), self._config())
        self.assertEqual(step, "s5")
        self.assertEqual(state, {})  # explicit step skips the file -> no live state
        self.assertIsNone(run_id)

    def test_missing_checkpoint_is_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            args = _args(root=d, checkpoint_step=None)
            step, state, run_id = cli._resolve_checkpoint(args, self._config())
        self.assertIsNone(step)
        self.assertEqual(state, {})
        self.assertIsNone(run_id)

    def test_corrupt_checkpoint_is_none(self):
        import tempfile

        from keel import checkpoint

        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            cp = checkpoint.resolve_path(d, config)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text("{not json", encoding="utf-8")  # parse -> CheckpointError
            step, state, run_id = cli._resolve_checkpoint(
                _args(root=d, checkpoint_step=None), config
            )
        self.assertIsNone(step)
        self.assertEqual(state, {})
        self.assertIsNone(run_id)

    def test_unreadable_checkpoint_path_is_failsoft(self):
        # A directory where the checkpoint file should be raises OSError, not
        # CheckpointError — must still degrade to (None, {}, None), not crash.
        import tempfile

        from keel import checkpoint

        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            cp = checkpoint.resolve_path(d, config)
            cp.mkdir(parents=True, exist_ok=True)  # path is a dir -> IsADirectoryError
            step, state, run_id = cli._resolve_checkpoint(
                _args(root=d, checkpoint_step=None), config
            )
        self.assertIsNone(step)
        self.assertEqual(state, {})
        self.assertIsNone(run_id)

    def test_reads_step_and_state_from_checkpoint_file(self):
        import tempfile

        from keel import checkpoint

        config = self._config()
        record = checkpoint.build_checkpoint_record(
            run_id="r1",
            command="ship",
            current_step="s10",
            base_branch="main",
            merge_state="merged",
        )
        with tempfile.TemporaryDirectory() as d:
            cp = checkpoint.resolve_path(d, config)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(checkpoint.encode_checkpoint(record), encoding="utf-8")
            step, state, run_id = cli._resolve_checkpoint(
                _args(root=d, checkpoint_step=None), config
            )
        self.assertEqual(step, "s10")
        self.assertEqual(state.get("merge"), "merged")
        self.assertEqual(run_id, "r1")


class TestLoadJuryVerdict(unittest.TestCase):
    """The `.keel/state/jury/<run-id>.json` discovery loader (#576) — fail-soft."""

    _OUTCOME = {
        "reviews": [{"agent": "claude"}, {"agent": "codex"}],
        "groups": [{"severity": "major", "status": "verified"}],
    }

    def _write(self, root, run_id, text):
        path = Path(root) / ".keel" / "state" / "jury" / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_and_summarises_outcome(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            self._write(d, "r1", json.dumps(self._OUTCOME))
            out = cli._load_jury_verdict(d, "r1")
        self.assertEqual(out["verdict"], "REQUEST CHANGES")
        self.assertEqual(out["counts"]["major"], 1)
        self.assertEqual(out["reviewers"], 2)

    def test_missing_file_is_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cli._load_jury_verdict(d, "r1"))

    def test_bad_json_is_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            self._write(d, "r1", "{not json")
            self.assertIsNone(cli._load_jury_verdict(d, "r1"))

    def test_unrecognised_shape_is_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            self._write(d, "r1", '{"schema_version": 1, "metadata": {}}')
            self.assertIsNone(cli._load_jury_verdict(d, "r1"))

    def test_oversized_file_is_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            self._write(d, "r1", "[" + " " * cli._MAX_JURY_OUTCOME_BYTES + "]")
            self.assertIsNone(cli._load_jury_verdict(d, "r1"))

    def test_non_utf8_file_is_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".keel" / "state" / "jury" / "r1.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xff\xfe{}")
            self.assertIsNone(cli._load_jury_verdict(d, "r1"))

    def test_unusable_run_ids_are_none(self):
        # Non-string / blank / path-escaping run ids never touch the filesystem.
        for run_id in (None, 7, "", "a/b", "../r1", "a\\b", "r 1"):
            self.assertIsNone(cli._load_jury_verdict(".", run_id))

    def test_board_run_state_picks_up_jury_verdict(self):
        # End-to-end wiring: a worktree checkpoint (run_id "r") + the saved
        # outcome at .keel/state/jury/r.json → the board RunState carries the
        # verdict block. No file → today's mode-only display (None).
        import json
        import tempfile
        from unittest.mock import patch

        from keel import config as cfg
        from keel.runner import CommandResult

        config = cfg.load_config(PROJECT)
        with tempfile.TemporaryDirectory() as d:
            _write_checkpoint(d, config, step="s8", pr=361)
            self._write(d, "r", json.dumps(self._OUTCOME))
            with patch(
                "keel_visual.cli.git.worktree_list",
                return_value=CommandResult(True, 0, f"worktree {d}\nHEAD abc\n\n"),
            ):
                pairs = cli._run_records_in_repo(d, config)
        ((run_state, _identity),) = (p for p in pairs)
        self.assertEqual(run_state["jury_verdict"]["verdict"], "REQUEST CHANGES")


class TestLoopAndFollow(unittest.TestCase):
    def test_loop_replays_until_max_cycles(self):
        out = io.StringIO()
        rc = cli.cmd_play(
            _args(command="overnight", loop=True), sleep=lambda s: None, out=out, max_cycles=2
        )
        self.assertEqual(rc, 0)
        # the first-phase pointer renders once per cycle -> 2 cycles.
        self.assertEqual(out.getvalue().count("config · config"), 2)

    def test_loop_keyboard_interrupt_exits_clean(self):
        out = io.StringIO()

        def boom(_):
            raise KeyboardInterrupt

        rc = cli.cmd_play(
            _args(command="overnight", loop=True), sleep=boom, out=out, max_cycles=None
        )
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().endswith("\n"))

    def test_follow_bounded_redraws_current_step(self):
        out = io.StringIO()
        calls = []
        rc = cli.cmd_play(
            _args(follow=True, no_clear=False),
            sleep=lambda s: calls.append(s),
            out=out,
            max_cycles=3,
        )
        self.assertEqual(rc, 0)
        # merged sample sits at s12; each tick clears + redraws that frame.
        self.assertEqual(out.getvalue().count("s12 · close"), 3)
        self.assertEqual(out.getvalue().count("\x1b[2J"), 3)
        self.assertEqual(len(calls), 3)

    def test_follow_unreadable_ledger_keeps_polling(self):
        # An OSError on the per-tick ledger read (e.g. path is a dir) must be
        # transient too — skip the tick, show waiting, don't crash.
        import tempfile

        from keel import ledger

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            ledger.resolve_path(d, config=self._cfg()).mkdir(parents=True, exist_ok=True)
            rc = cli.cmd_play(
                _args(follow=True, ledger_jsonl=None, root=d),
                sleep=lambda s: None,
                out=out,
                max_cycles=2,
            )
        self.assertEqual(rc, 0)
        self.assertIn("waiting for run state", out.getvalue())

    def _cfg(self):
        from keel import config as cfg

        return cfg.load_config(PROJECT)

    def test_follow_fatal_config_error_returns_code(self):
        rc = cli.cmd_play(
            _args(follow=True, path="no-such.yaml"),
            sleep=lambda s: None,
            out=io.StringIO(),
            max_cycles=2,
        )
        self.assertEqual(rc, 1)

    def test_follow_transient_ledger_error_keeps_polling(self):
        # A live run can leave a half-written ledger line; the follower must not
        # die — it skips the tick and shows a waiting line until a good read.
        import tempfile

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.jsonl"
            bad.write_text("{half-written", encoding="utf-8")
            rc = cli.cmd_play(
                _args(follow=True, ledger_jsonl=str(bad)),
                sleep=lambda s: None,
                out=out,
                max_cycles=2,
            )
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


def _jury_ledger(d, *, pr=42, mode="gating"):
    import json

    led = Path(d) / "l.jsonl"
    rec = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "command": "ship",
        "pull_request": {"number": pr},
        "run_context": {"jury_mode": mode},
    }
    led.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return str(led)


class TestTheaterSeams(unittest.TestCase):
    def test_theater_available(self):
        self.assertTrue(cli._theater_available(_which=lambda n: "/usr/bin/jury"))
        self.assertFalse(cli._theater_available(_which=lambda n: None))

    def test_is_interactive(self):
        self.assertTrue(cli._is_interactive(_TTY()))
        self.assertFalse(cli._is_interactive(io.StringIO()))

    def test_review_index(self):
        self.assertEqual(cli._review_index({"steps": [{"id": "s6"}, {"id": "s7"}]}), 1)
        self.assertIsNone(cli._review_index({"steps": [{"id": "poll"}]}))
        self.assertIsNone(cli._review_index({}))

    def test_theater_due_matrix(self):
        at = {"steps": [{"id": "s7"}], "active_index": 0, "jury": {"active": True}}
        self.assertTrue(cli._theater_due(at, enabled=True, done=False, interactive=True))
        self.assertFalse(cli._theater_due(at, enabled=False, done=False, interactive=True))
        self.assertFalse(cli._theater_due(at, enabled=True, done=True, interactive=True))
        self.assertFalse(cli._theater_due(at, enabled=True, done=False, interactive=False))
        self.assertFalse(
            cli._theater_due(
                {"steps": [{"id": "s7"}], "active_index": 0, "jury": {"active": False}},
                enabled=True,
                done=False,
                interactive=True,
            )
        )
        self.assertFalse(
            cli._theater_due(
                {"steps": [{"id": "s6"}], "active_index": 0, "jury": {"active": True}},
                enabled=True,
                done=False,
                interactive=True,
            )
        )
        self.assertFalse(
            cli._theater_due(
                {"steps": [], "active_index": 5, "jury": {"active": True}},
                enabled=True,
                done=False,
                interactive=True,
            )
        )

    def test_run_theater_success_and_failsoft(self):
        calls = []
        self.assertTrue(
            cli._run_theater(42, cwd="/x", _spawn=lambda a, c: calls.append((a, c)) or True)
        )
        self.assertEqual(calls[0][0], ["jury", "--pr", "42", "--theater"])

        def boom(_a, _c):
            raise OSError("no jury")

        self.assertFalse(cli._run_theater(42, cwd="/x", _spawn=boom))

    def test_spawn_theater_returncode(self):
        class _R:
            def __init__(self, rc):
                self.returncode = rc

        orig = cli.subprocess.run
        try:
            cli.subprocess.run = lambda *a, **k: _R(0)
            self.assertTrue(cli._spawn_theater(["jury"], None))
            cli.subprocess.run = lambda *a, **k: _R(1)
            self.assertFalse(cli._spawn_theater(["jury"], None))
        finally:
            cli.subprocess.run = orig


class TestTheaterHandoff(unittest.TestCase):
    def test_hands_off_once_at_review_step(self):
        import tempfile

        out, calls = _TTY(), []
        with tempfile.TemporaryDirectory() as d:
            args = _args(
                follow=True,
                theater=True,
                ledger_jsonl=_jury_ledger(d),
                pr=42,
                checkpoint_step="s7",
                root=d,
            )
            rc = cli._play_follow(
                args,
                sleep=lambda s: None,
                out=out,
                max_cycles=3,
                theater_run=lambda pr, cwd: calls.append((pr, cwd)) or True,
                theater_available=lambda: True,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [(42, d)])  # one-shot, even across three ticks

    def test_pr_from_live_checkpoint_not_stale_ledger(self):
        # The trigger comes from the live checkpoint, so the PR must too: a live
        # run on PR 99 whose ship_run record hasn't landed must not open the
        # stale ledger's PR 42.
        import tempfile

        from keel import checkpoint as ckpt
        from keel import config as cfg

        out, calls = _TTY(), []
        with tempfile.TemporaryDirectory() as d:
            config = cfg.load_config(PROJECT)
            rec = ckpt.build_checkpoint_record(
                run_id="R",
                command="ship",
                current_step="s7",
                base_branch="main",
                pull_request=99,
                jury_mode="gating",
            )
            ckpt.write_checkpoint(ckpt.resolve_path(d, config), rec)
            args = _args(
                follow=True,
                theater=True,
                ledger_jsonl=_jury_ledger(d, pr=42),
                pr=42,
                checkpoint_step=None,
                root=d,
            )
            cli._play_follow(
                args,
                sleep=lambda s: None,
                out=out,
                max_cycles=2,
                theater_run=lambda pr, cwd: calls.append(pr) or True,
                theater_available=lambda: True,
            )
        self.assertEqual(calls, [99])  # live checkpoint PR wins over the stale ledger

    def test_skips_when_jury_cli_absent(self):
        import tempfile

        out, calls = _TTY(), []
        with tempfile.TemporaryDirectory() as d:
            args = _args(
                follow=True,
                theater=True,
                ledger_jsonl=_jury_ledger(d),
                pr=42,
                checkpoint_step="s7",
                root=d,
            )
            cli._play_follow(
                args,
                sleep=lambda s: None,
                out=out,
                max_cycles=2,
                theater_run=lambda pr, cwd: calls.append(pr),
                theater_available=lambda: False,
            )
        self.assertEqual(calls, [])  # absent jury -> no handoff, no error

    def test_skips_when_not_interactive(self):
        import tempfile

        out, calls = io.StringIO(), []  # not a tty
        with tempfile.TemporaryDirectory() as d:
            args = _args(
                follow=True,
                theater=True,
                ledger_jsonl=_jury_ledger(d),
                pr=42,
                checkpoint_step="s7",
                root=d,
            )
            cli._play_follow(
                args,
                sleep=lambda s: None,
                out=out,
                max_cycles=2,
                theater_run=lambda pr, cwd: calls.append(pr),
                theater_available=lambda: True,
            )
        self.assertEqual(calls, [])

    def test_skips_when_no_pr_resolvable(self):
        import tempfile

        from keel import checkpoint as ckpt
        from keel import config as cfg

        out, calls = _TTY(), []
        with tempfile.TemporaryDirectory() as d:
            # jury active but pull_request absent and --pr None -> nothing to hand to.
            # Also make the checkpoint path a directory so _live_pr hits its
            # fail-soft OSError branch and yields None.
            ckpt_path = ckpt.resolve_path(d, cfg.load_config(PROJECT))
            ckpt_path.parent.mkdir(parents=True, exist_ok=True)
            ckpt_path.mkdir()
            led = _jury_ledger(d)
            Path(led).write_text(
                '{"schema_version":"keel.run-ledger.v1","record_type":"ship_run",'
                '"command":"ship","run_context":{"jury_mode":"gating"}}\n',
                encoding="utf-8",
            )
            args = _args(
                follow=True, theater=True, ledger_jsonl=led, pr=None, checkpoint_step="s7", root=d
            )
            cli._play_follow(
                args,
                sleep=lambda s: None,
                out=out,
                max_cycles=2,
                theater_run=lambda pr, cwd: calls.append(pr),
                theater_available=lambda: True,
            )
        self.assertEqual(calls, [])

    def test_rearms_before_review_then_no_handoff(self):
        import tempfile

        out, calls = _TTY(), []
        with tempfile.TemporaryDirectory() as d:
            # parked before s7 (s4): re-arm branch runs, theater not due
            args = _args(
                follow=True,
                theater=True,
                ledger_jsonl=_jury_ledger(d),
                pr=42,
                checkpoint_step="s4",
                root=d,
            )
            cli._play_follow(
                args,
                sleep=lambda s: None,
                out=out,
                max_cycles=2,
                theater_run=lambda pr, cwd: calls.append(pr),
                theater_available=lambda: True,
            )
        self.assertEqual(calls, [])

    def test_command_without_review_step_no_handoff(self):
        import tempfile

        out, calls = _TTY(), []
        with tempfile.TemporaryDirectory() as d:
            args = _args(
                follow=True,
                theater=True,
                ledger_jsonl=_jury_ledger(d),
                pr=42,
                checkpoint_step=None,
                command="ci-check",
                root=d,
            )
            cli._play_follow(
                args,
                sleep=lambda s: None,
                out=out,
                max_cycles=2,
                theater_run=lambda pr, cwd: calls.append(pr),
                theater_available=lambda: True,
            )
        self.assertEqual(calls, [])


def _dash_args(**kw):
    base = dict(path=PROJECT, root=".", interval=2.0, once=True, no_clear=True, color="never")
    base.update(kw)
    return Namespace(**base)


def _write_checkpoint(wt, config, *, step, pr=None, issue=None, merge_state="not-started"):
    from keel import checkpoint

    rec = checkpoint.build_checkpoint_record(
        run_id="r",
        command="ship",
        current_step=step,
        base_branch="main",
        pull_request=pr,
        active_issue=issue,
        merge_state=merge_state,
    )
    cp = checkpoint.resolve_path(wt, config)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(checkpoint.encode_checkpoint(rec), encoding="utf-8")


class TestDash(unittest.TestCase):
    def _config(self):
        from keel import config as cfg

        return cfg.load_config(PROJECT)

    def _porcelain(self, *paths):
        from keel.runner import CommandResult

        text = "".join(f"worktree {p}\nHEAD abc\n\n" for p in paths)
        return CommandResult(True, 0, text)

    def test_lists_active_runs(self):
        import tempfile
        from unittest.mock import patch

        config = self._config()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write_checkpoint(a, config, step="s8", pr=361, merge_state="pending")
            _write_checkpoint(b, config, step="s12", pr=360, merge_state="merged")
            with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain(a, b)):
                rc = cli.cmd_dash(_dash_args(), sleep=lambda s: None, out=out)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("2 active runs", text)
        self.assertIn("#361", text)
        self.assertIn("#360", text)
        self.assertIn("merged", text)

    def test_skips_worktree_without_checkpoint(self):
        import tempfile
        from unittest.mock import patch

        config = self._config()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write_checkpoint(a, config, step="s4", pr=361)
            with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain(a, b)):
                cli.cmd_dash(_dash_args(), sleep=lambda s: None, out=out)
        self.assertIn("1 active run", out.getvalue())

    def test_worktree_list_failure_falls_back_to_root(self):
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        config = self._config()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            _write_checkpoint(d, config, step="s4", pr=361)
            with patch(
                "keel_visual.cli.git.worktree_list",
                return_value=CommandResult(False, 1, "not a git repo"),
            ):
                cli.cmd_dash(_dash_args(root=d), sleep=lambda s: None, out=out)
        self.assertIn("1 active run", out.getvalue())

    def test_discover_finds_ledger_record(self):
        import tempfile
        from unittest.mock import patch

        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            _write_checkpoint(d, config, step="s12", pr=361, merge_state="merged")
            led = ledger_path(d, config)
            led.parent.mkdir(parents=True, exist_ok=True)
            led.write_text(Path(SAMPLE).read_text(encoding="utf-8"), encoding="utf-8")
            with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain(d)):
                rows = cli._discover_runs(_dash_args(root=d), config)
        self.assertEqual(len(rows), 1)
        # SAMPLE ledger carries both issue #351 and PR #361 → combined label
        self.assertEqual(rows[0]["label"], "#351→#361")

    def test_latest_ship_record_by_pr(self):
        import tempfile

        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            led = ledger_path(d, config)
            led.parent.mkdir(parents=True, exist_ok=True)
            led.write_text(Path(SAMPLE).read_text(encoding="utf-8"), encoding="utf-8")
            rec = cli._latest_ship_record(d, config, 361)
        self.assertEqual(rec["pull_request"]["number"], 361)

    def test_discover_skips_unreadable_checkpoint(self):
        # A worktree whose checkpoint path is a directory (OSError) must be
        # skipped, not crash the whole board.
        import tempfile
        from unittest.mock import patch

        from keel import checkpoint

        config = self._config()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write_checkpoint(a, config, step="s4", pr=361)
            checkpoint.resolve_path(b, config).mkdir(parents=True, exist_ok=True)
            with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain(a, b)):
                rows = cli._discover_runs(_dash_args(), config)
        self.assertEqual(len(rows), 1)

    def test_latest_ship_record_unreadable_ledger_is_none(self):
        import tempfile

        from keel import ledger

        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            ledger.resolve_path(d, config).mkdir(parents=True, exist_ok=True)  # OSError on read
            self.assertIsNone(cli._latest_ship_record(d, config, 361))

    def test_latest_ship_record_no_pr_is_none(self):
        import tempfile

        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            led = ledger_path(d, config)
            led.parent.mkdir(parents=True, exist_ok=True)
            led.write_text(Path(SAMPLE).read_text(encoding="utf-8"), encoding="utf-8")
            self.assertIsNone(cli._latest_ship_record(d, config, None))

    def test_discover_bad_ledger_is_failsoft(self):
        import tempfile
        from unittest.mock import patch

        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            _write_checkpoint(d, config, step="s4", issue=351, merge_state="not-started")
            led = ledger_path(d, config)
            led.parent.mkdir(parents=True, exist_ok=True)
            led.write_text("{bad json", encoding="utf-8")
            with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain(d)):
                rows = cli._discover_runs(_dash_args(root=d), config)
        # no pr in checkpoint -> ledger not consulted by pr; bad ledger tolerated.
        self.assertEqual(rows[0]["label"], "#351")

    def test_dash_config_error(self):
        rc = cli.cmd_dash(_dash_args(path="no-such.yaml"), sleep=lambda s: None, out=io.StringIO())
        self.assertEqual(rc, 1)

    def test_dash_loop_and_keyboard_interrupt(self):
        from unittest.mock import patch

        def boom(_):
            raise KeyboardInterrupt

        out = io.StringIO()
        with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain()):
            rc = cli.cmd_dash(
                _dash_args(once=False, no_clear=False), sleep=boom, out=out, max_cycles=None
            )
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().endswith("\n"))

    def test_dash_bounded_refresh(self):
        from unittest.mock import patch

        out = io.StringIO()
        calls = []
        with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain()):
            rc = cli.cmd_dash(
                _dash_args(once=False), sleep=lambda s: calls.append(s), out=out, max_cycles=3
            )
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().count("keel ·"), 3)
        self.assertEqual(len(calls), 3)

    def test_discover_skips_corrupt_checkpoint(self):
        import tempfile
        from unittest.mock import patch

        from keel import checkpoint

        config = self._config()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _write_checkpoint(a, config, step="s4", pr=361)
            cp = checkpoint.resolve_path(b, config)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text("{corrupt", encoding="utf-8")  # read -> CheckpointError -> skipped
            with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain(a, b)):
                rows = cli._discover_runs(_dash_args(), config)
        self.assertEqual(len(rows), 1)

    def test_main_dash(self):
        from unittest.mock import patch

        with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain()):
            rc = cli.main(["dash", PROJECT, "--root", ".", "--once", "--color", "never"])
        self.assertEqual(rc, 0)

    # ---- dash --all (multi-project board) ----
    def _make_keel_project(self, parent, name, *, step=None, pr=None):
        """A fake keel project dir (has .git + .keel/project.yaml), optionally with a
        live checkpoint. git.worktree_list is patched to fail in these tests, so a
        run is read from the project dir itself (the worktree-list fallback)."""
        import shutil

        from keel import config as cfg

        pdir = Path(parent) / name
        (pdir / ".keel").mkdir(parents=True)
        (pdir / ".git").mkdir()
        shutil.copy(PROJECT, pdir / ".keel" / "project.yaml")
        if step is not None:
            _write_checkpoint(
                str(pdir), cfg.load_config(str(pdir / ".keel" / "project.yaml")), step=step, pr=pr
            )
        return pdir

    def test_all_aggregates_projects(self):
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as parent:
            self._make_keel_project(parent, "alpha", step="s8", pr=42)
            self._make_keel_project(parent, "beta", step="s12", pr=7)
            (Path(parent) / "notkeel").mkdir()  # no .git/.keel -> skipped
            with patch(
                "keel_visual.cli.git.worktree_list", return_value=CommandResult(False, 1, "")
            ):
                rc = cli.cmd_dash(
                    _dash_args(all=True, root=parent, path=None), sleep=lambda s: None, out=out
                )
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("2 runs across 2 projects", text)
        self.assertIn("alpha", text)
        self.assertIn("beta", text)
        self.assertIn("#42", text)
        self.assertIn("#7", text)

    def test_all_skips_malformed_project_config(self):
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as parent:
            self._make_keel_project(parent, "good", step="s8", pr=42)
            bad = Path(parent) / "bad"
            (bad / ".keel").mkdir(parents=True)
            (bad / ".git").mkdir()
            (bad / ".keel" / "project.yaml").write_text("{not valid yaml", encoding="utf-8")
            with patch(
                "keel_visual.cli.git.worktree_list", return_value=CommandResult(False, 1, "")
            ):
                rc = cli.cmd_dash(
                    _dash_args(all=True, root=parent, path=None), sleep=lambda s: None, out=out
                )
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("1 run across 1 project", text)  # malformed 'bad' skipped
        self.assertIn("good", text)

    def test_discover_projects_filters_and_failsoft(self):
        import tempfile

        with tempfile.TemporaryDirectory() as parent:
            self._make_keel_project(parent, "keelproj")
            (Path(parent) / "justgit" / ".git").mkdir(parents=True)  # no .keel -> skip
            (Path(parent) / "justkeel" / ".keel").mkdir(parents=True)  # no .git -> skip
            (Path(parent) / "afile").write_text("x", encoding="utf-8")  # not a dir -> skip
            found = cli._discover_projects(parent)
        self.assertEqual(found, [("keelproj", str(Path(parent) / "keelproj"))])
        # unreadable parent (a file, not a dir) -> [] (no crash)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "afile"
            f.write_text("x", encoding="utf-8")
            self.assertEqual(cli._discover_projects(str(f)), [])

    def test_dash_no_path_no_all_errors(self):
        rc = cli.cmd_dash(_dash_args(path=None, all=False), sleep=lambda s: None, out=io.StringIO())
        self.assertEqual(rc, 1)

    def test_main_dash_all(self):
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as parent:
            self._make_keel_project(parent, "alpha", step="s8", pr=42)
            with patch(
                "keel_visual.cli.git.worktree_list", return_value=CommandResult(False, 1, "")
            ):
                rc = cli.main(["dash", "--all", "--root", parent, "--once", "--color", "never"])
        self.assertEqual(rc, 0)

    def test_all_bounded_refresh(self):
        import tempfile

        out, calls = io.StringIO(), []
        with tempfile.TemporaryDirectory() as parent:
            rc = cli.cmd_dash(
                _dash_args(all=True, root=parent, path=None, once=False),
                sleep=lambda s: calls.append(s),
                out=out,
                max_cycles=2,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().count("keel ·"), 2)
        self.assertEqual(len(calls), 2)  # slept between refreshes (no --once)

    def test_all_keyboard_interrupt(self):
        import tempfile

        def boom(_):
            raise KeyboardInterrupt

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as parent:
            rc = cli.cmd_dash(
                _dash_args(all=True, root=parent, path=None, once=False),
                sleep=boom,
                out=out,
                max_cycles=None,
            )
        self.assertEqual(rc, 0)
        self.assertTrue(out.getvalue().endswith("\n"))

    # ---- render --all (web board) ----
    def test_load_board_template(self):
        self.assertIn("__KEEL_BOARD__", cli.load_board_template())

    def test_board_entry_stale_only_when_old_and_unfinished(self):
        """The exact misread #823 documents: an abandoned record must not render
        as running, while a finished record may be arbitrarily old."""
        from keel_visual import runstate as rs

        def entry(age_seconds, *, done=False, merged=False):
            run_state = rs.build_run_state(
                {"command": "ship", "pull_request": {"number": 9}},
                checkpoint_step="s8",
            )
            if done:
                run_state["done"] = True
            if merged:
                run_state["merged"] = True
            run_state["record_age_seconds"] = age_seconds
            return cli._board_entry(run_state, {"pr": 9}, "myproj")

        fresh = entry(60.0)
        self.assertFalse(fresh["stale"])
        self.assertEqual(fresh["age_hours"], 0.0)

        abandoned = entry(cli.STALE_AFTER_SECONDS + 1)
        self.assertTrue(abandoned["stale"])

        # A finished run can be as old as it likes — done is done, not stale.
        old_done = entry(63 * 24 * 3600, done=True)
        self.assertFalse(old_done["stale"])
        old_merged = entry(63 * 24 * 3600, merged=True)
        self.assertFalse(old_merged["stale"])

        # No age signal (unreadable file) must not invent staleness.
        unknown = entry(None)
        self.assertFalse(unknown["stale"])
        self.assertIsNone(unknown["age_hours"])

        # Exactly AT the cutoff is not yet stale — the predicate is strict.
        boundary = entry(float(cli.STALE_AFTER_SECONDS))
        self.assertFalse(boundary["stale"])

    def test_record_age_of_a_missing_file_is_none(self):
        import tempfile
        from pathlib import Path as _P

        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cli._record_age_seconds(_P(d) / "missing.json"))

    def test_activity_age_survives_a_slug_transformed_run_id(self):
        """The writer stores the file under run_id_slug(run_id); reading the raw
        id silently reintroduces the #823 misread for ids like "ship 42"."""
        import json
        import os
        import tempfile

        from keel import activity as act
        from keel import config as cfg_mod

        with tempfile.TemporaryDirectory() as root:
            adir = os.path.join(root, ".keel", "activity")
            os.makedirs(adir)
            raw_id = "SHIP 42"  # slugs to ship-42
            rec = {
                "schema_version": "keel.activity.v1",
                "record_type": "command_activity",
                "run_id": raw_id,
                "command": "ship",
                "phase": "s4",
                "status": "running",
            }
            path = os.path.join(adir, f"{act.run_id_slug(raw_id)}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            old = os.path.getmtime(path) - 63 * 24 * 3600
            os.utime(path, (old, old))

            config = cfg_mod.ProjectConfig(
                extends="keel",
                core_version="^0.7",
                base_branch="main",
                knobs=cfg_mod.Knobs(build_gate_cmd="true"),
            )
            pairs = cli._activity_records_in_repo(root, config)
            self.assertEqual(len(pairs), 1)
            run_state, _ = pairs[0]
            self.assertIsNotNone(
                run_state.get("record_age_seconds"),
                "a slug-transformed run_id lost its age signal — the abandoned "
                "run would render as running forever",
            )

    def test_activity_records_carry_their_file_age(self):
        """The record has no timestamp field; the file mtime is the signal."""
        import json
        import os
        import tempfile

        from keel import config as cfg_mod

        with tempfile.TemporaryDirectory() as root:
            adir = os.path.join(root, ".keel", "activity")
            os.makedirs(adir)
            rec = {
                "schema_version": "keel.activity.v1",
                "record_type": "command_activity",
                "run_id": "ship-1",
                "command": "ship",
                "phase": "s4",
                "status": "running",
            }
            path = os.path.join(adir, "ship-1.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            two_days = 48 * 3600
            old = os.path.getmtime(path) - two_days
            os.utime(path, (old, old))

            config = cfg_mod.ProjectConfig(
                extends="keel",
                core_version="^0.7",
                base_branch="main",
                knobs=cfg_mod.Knobs(build_gate_cmd="true"),
            )
            pairs = cli._activity_records_in_repo(root, config)
            self.assertEqual(len(pairs), 1)
            run_state, identity = pairs[0]
            self.assertEqual(identity["run_id"], "ship-1")
            age = run_state.get("record_age_seconds")
            self.assertIsNotNone(age)
            self.assertGreater(age, cli.STALE_AFTER_SECONDS)

    def test_board_entry_is_slim(self):
        from keel_visual import runstate as rs

        run_state = rs.build_run_state(
            {"command": "ship", "pull_request": {"number": 9}}, checkpoint_step="s8"
        )
        e = cli._board_entry(run_state, {"pr": 9}, "myproj")
        self.assertEqual(e["project"], "myproj")
        self.assertEqual(e["label"], "#9")
        self.assertEqual(e["pr"], 9)
        self.assertIsNone(e["issue"])
        self.assertEqual(e["active_id"], "s8")
        self.assertTrue(e["steps"])
        self.assertIn("status", e)

    def test_board_entry_exposes_issue_and_pr(self):
        from keel_visual import runstate as rs

        run_state = rs.build_run_state(
            {"command": "ship", "pull_request": {"number": 9}}, checkpoint_step="s8"
        )
        e = cli._board_entry(run_state, {"pr": 9, "issue": 7}, "myproj")
        self.assertEqual(e["label"], "#7→#9")
        self.assertEqual(e["issue"], 7)
        self.assertEqual(e["pr"], 9)
        # branch/base/author come from the ledger record; absent here → None, key present
        self.assertIsNone(e["branch"])
        self.assertIsNone(e["author"])
        self.assertIsNone(e["title"])

    def test_board_entry_carries_branch_and_author(self):
        from keel_visual import runstate as rs

        rec = {
            "command": "ship",
            "pull_request": {"number": 9},
            "git": {"branch": "feat/x", "base_branch": "main"},
            "actors": {"implementer": "claude (opus)"},
        }
        run_state = rs.build_run_state(rec, checkpoint_step="s8")
        e = cli._board_entry(run_state, {"pr": 9}, "myproj")
        self.assertEqual(e["branch"], "feat/x")
        self.assertEqual(e["base"], "main")
        self.assertEqual(e["author"], "claude (opus)")

    def test_board_entry_carries_ledger_detail_fields(self):
        # Regression (#575 review): the drawer reads these from board.json, so
        # _board_entry must actually include them — a whitelist miss is silent.
        from keel_visual import runstate as rs

        rec = {
            "command": "ship",
            "pull_request": {"number": 9},
            "assessment": {
                "tier": 3,
                "window_open": False,
                "bypassed_window": True,
                "merge": {"action": "merge", "reason": "window bypassed"},
            },
            "gates": [
                {"gate": "build", "ok": True, "skipped": False, "error": None, "finding_count": 0},
                {
                    "gate": "evidence",
                    "ok": False,
                    "skipped": False,
                    "error": "missing verdict",
                    "finding_count": 2,
                },
            ],
            "actors": {
                "implementer": "anthropic-api (claude-sonnet-5)",
                "reviewers": ["claude", "codex"],
                "tester": "claude",
            },
            "run_context": {"host_agent": "claude"},
            "changes": {"file_count": 4},
        }
        run_state = rs.build_run_state(rec, checkpoint_step="s8")
        e = cli._board_entry(run_state, {"pr": 9}, "myproj")
        self.assertEqual(e["tier"], 3)
        self.assertIs(e["window_open"], False)
        self.assertIs(e["bypassed_window"], True)
        self.assertEqual(e["merge_reason"], "window bypassed")
        self.assertEqual(e["file_count"], 4)
        self.assertEqual(e["reviewers"], ["claude", "codex"])
        self.assertEqual(e["tester"], "claude")
        self.assertEqual(e["host_agent"], "claude")
        self.assertEqual(
            e["gates"],
            [
                {"name": "build", "ok": True, "skipped": False, "error": None, "finding_count": 0},
                {
                    "name": "evidence",
                    "ok": False,
                    "skipped": False,
                    "error": "missing verdict",
                    "finding_count": 2,
                },
            ],
        )

    def test_board_entry_carries_jury_verdict(self):
        # Regression guard (#576, same class of bug as the #575 whitelist miss):
        # the drawer reads jury_verdict from board.json, so _board_entry must
        # actually include it — a whitelist miss is silent.
        from keel_visual import runstate as rs

        summary = {
            "verdict": "REQUEST CHANGES",
            "counts": {"critical": 0, "major": 1, "minor": 2, "nit": 0},
            "reviewers": 3,
        }
        run_state = rs.build_run_state(
            {"command": "ship", "pull_request": {"number": 9}},
            checkpoint_step="s8",
            jury_verdict=summary,
        )
        e = cli._board_entry(run_state, {"pr": 9}, "myproj")
        self.assertEqual(e["jury_verdict"], summary)

    def test_board_entry_jury_verdict_defaults_none(self):
        from keel_visual import runstate as rs

        run_state = rs.build_run_state(
            {"command": "ship", "pull_request": {"number": 9}}, checkpoint_step="s8"
        )
        e = cli._board_entry(run_state, {"pr": 9}, "myproj")
        self.assertIsNone(e["jury_verdict"])

    def test_board_entry_detail_fields_default_empty(self):
        from keel_visual import runstate as rs

        run_state = rs.build_run_state(
            {"command": "ship", "pull_request": {"number": 9}}, checkpoint_step="s8"
        )
        e = cli._board_entry(run_state, {"pr": 9}, "myproj")
        self.assertIsNone(e["tier"])
        self.assertIsNone(e["window_open"])
        self.assertIsNone(e["merge_reason"])
        self.assertEqual(e["reviewers"], [])
        self.assertEqual(e["gates"], [])

    def test_fetch_title_success_and_cache(self):
        cli._TITLE_CACHE.clear()
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return types.SimpleNamespace(stdout="My Title\n", stderr="", returncode=0)

        self.assertEqual(cli._fetch_title("issue", 500, root=".", _run=run), "My Title")
        # second lookup is served from the per-process cache — no second gh call
        self.assertEqual(cli._fetch_title("issue", 500, root=".", _run=run), "My Title")
        self.assertEqual(len(calls), 1)

    def test_fetch_title_failsoft(self):
        cli._TITLE_CACHE.clear()

        def run(argv, **kw):
            return types.SimpleNamespace(stdout="", stderr="no gh", returncode=1)

        self.assertIsNone(cli._fetch_title("pr", 999, root=".", _run=run))

    def test_enrich_title_issue_then_pr_then_none(self):
        cli._TITLE_CACHE.clear()

        def run(argv, **kw):  # argv[3] is the issue/PR number
            return types.SimpleNamespace(stdout="T-" + argv[3], stderr="", returncode=0)

        e_issue = {"issue": 500, "pr": 501, "title": None}
        cli._enrich_title(e_issue, root=".", _run=run)
        self.assertEqual(e_issue["title"], "T-500")  # issue title preferred
        e_pr = {"issue": None, "pr": 501, "title": None}
        cli._enrich_title(e_pr, root=".", _run=run)
        self.assertEqual(e_pr["title"], "T-501")  # falls back to PR
        e_none = {"issue": None, "pr": None, "title": None}
        cli._enrich_title(e_none, root=".", _run=run)
        self.assertIsNone(e_none["title"])  # neither → untouched

    def test_single_repo_board_enriches_title(self):
        import tempfile
        from unittest.mock import patch

        cli._TITLE_CACHE.clear()
        config = self._config()

        def run(argv, **kw):
            return types.SimpleNamespace(stdout="Some Issue Title", stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as d:
            _write_checkpoint(d, config, step="s12", pr=361, merge_state="merged")
            led = ledger_path(d, config)
            led.parent.mkdir(parents=True, exist_ok=True)
            led.write_text(Path(SAMPLE).read_text(encoding="utf-8"), encoding="utf-8")
            with patch("keel_visual.cli.git.worktree_list", return_value=self._porcelain(d)):
                board = cli._single_repo_board(d, config, _run=run)
        self.assertEqual(board[0]["title"], "Some Issue Title")
        self.assertIn("branch", board[0])

    def test_aggregate_board_without_run_skips_titles(self):
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as parent:
            self._make_keel_project(parent, "alpha", step="s8", pr=42)
            with patch(
                "keel_visual.cli.git.worktree_list", return_value=CommandResult(False, 1, "")
            ):
                board = cli._aggregate_board(parent)  # no _run → no gh, titles left None
        self.assertTrue(board)
        self.assertIsNone(board[0]["title"])

    def test_render_all_writes_board(self):
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as parent:
            self._make_keel_project(parent, "alpha", step="s8", pr=42)
            self._make_keel_project(parent, "beta", step="s12", pr=7)
            out_html = Path(parent) / "board.html"
            with patch(
                "keel_visual.cli.git.worktree_list", return_value=CommandResult(False, 1, "")
            ):
                rc = cli.cmd_render(_args(all=True, root=parent, path=None, out=str(out_html)))
            self.assertEqual(rc, 0)
            html = out_html.read_text(encoding="utf-8")
            self.assertIn("window.KEEL_BOARD", html)
            self.assertIn("alpha", html)
            self.assertIn("beta", html)

    def test_render_no_path_no_all_errors(self):
        rc = cli.cmd_render(_args(path=None, all=False))
        self.assertEqual(rc, 1)

    def test_main_render_all(self):
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as parent:
            self._make_keel_project(parent, "alpha", step="s8", pr=42)
            out_html = Path(parent) / "b.html"
            with patch(
                "keel_visual.cli.git.worktree_list", return_value=CommandResult(False, 1, "")
            ):
                rc = cli.main(["render", "--all", "--root", parent, "--out", str(out_html)])
            self.assertEqual(rc, 0)
            self.assertTrue(out_html.exists())


def ledger_path(root, config):
    from keel import ledger

    return ledger.resolve_path(root, config)


class TestMain(unittest.TestCase):
    def test_main_render(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.html"
            rc = cli.main(
                [
                    "render",
                    PROJECT,
                    "--root",
                    str(FIX),
                    "--ledger-jsonl",
                    SAMPLE,
                    "--pr",
                    "361",
                    "--out",
                    str(out),
                ]
            )
        self.assertEqual(rc, 0)

    def test_main_play(self):
        rc = cli.main(
            [
                "play",
                PROJECT,
                "--root",
                str(FIX),
                "--ledger-jsonl",
                SAMPLE,
                "--pr",
                "361",
                "--step",
                "8",
                "--color",
                "never",
            ]
        )
        self.assertEqual(rc, 0)


class TestActivityBoard(unittest.TestCase):
    """The additive command-activity channel surfaces non-ship runs on the board."""

    def _project(self, parent):
        import shutil

        from keel import config as cfg

        pdir = Path(parent)
        (pdir / ".keel").mkdir(parents=True)
        shutil.copy(PROJECT, pdir / ".keel" / "project.yaml")
        return cfg.load_config(str(pdir / ".keel" / "project.yaml"))

    def _write(self, root, config, **kw):
        from keel import activity

        rec = activity.build_activity_record(**kw)
        activity.write_activity(activity.record_path(root, config, kw["run_id"]), rec)

    def test_running_and_done_records_become_runs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            config = self._project(d)
            self._write(
                d, config, command="triage", run_id="triage-2260", phase="classify", issue=2260
            )
            self._write(d, config, command="morning", run_id="m-1", phase="output", status="done")
            pairs = cli._activity_records_in_repo(d, config)
            by_cmd = {ident["command"]: (rs, ident) for rs, ident in pairs}
            self.assertEqual(set(by_cmd), {"triage", "morning"})
            triage_rs, _ = by_cmd["triage"]
            self.assertFalse(triage_rs["merged"])
            self.assertEqual(triage_rs["steps"][triage_rs["active_index"]]["id"], "classify")
            morning_rs, _ = by_cmd["morning"]
            self.assertTrue(morning_rs["done"])  # done → fades / filters as finished
            self.assertFalse(morning_rs.get("merged"))  # but NOT a merge claim

    def test_merged_record_marks_merged(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            config = self._project(d)
            self._write(
                d, config, command="ship", run_id="ship-585", phase="s10", status="merged", pr=585
            )
            pairs = cli._activity_records_in_repo(d, config)
            self.assertEqual(len(pairs), 1)
            ship_rs, _ = pairs[0]
            self.assertTrue(ship_rs["merged"])  # real merge → green "merged"
            self.assertFalse(ship_rs.get("done"))

    def test_records_join_the_board(self):
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as d:
            config = self._project(d)
            self._write(
                d, config, command="triage", run_id="triage-2260", phase="classify", issue=2260
            )
            with patch(
                "keel_visual.cli.git.worktree_list", return_value=CommandResult(False, 1, "")
            ):
                rows = cli._runs_in_repo(d, config)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["label"], "#2260")

    def test_fail_soft_on_bad_activity_dir(self):
        from keel import config as cfg

        bad = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.7",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true"),
            policy_pack={"name": "t", "reports": {"activity": "/abs/escape"}},
        )
        self.assertEqual(cli._activity_records_in_repo(".", bad), [])

    def test_board_row_label_falls_back_to_run_id(self):
        from keel_visual import dash, runstate

        rs = runstate.build_run_state(None, checkpoint_step="config", command="morning")
        row = dash.board_row(rs, {"command": "morning", "run_id": "m-1"})
        self.assertEqual(row["label"], "m-1")

    def test_no_op_when_core_lacks_activity(self):
        from unittest.mock import patch

        with patch("keel_visual.cli.activity", None):
            self.assertEqual(cli._activity_records_in_repo(".", object()), [])

    def test_activity_in_a_non_root_worktree_is_found(self):
        # An agent runs a non-ship command in its own worktree and stamps the
        # activity there — the board must read every worktree, not just root.
        import tempfile
        from unittest.mock import patch

        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as root:
            config = self._project(root)
            wt = Path(root) / "wt"
            (wt / ".keel").mkdir(parents=True)
            self._write(
                str(wt), config, command="stale-prs", run_id="stale-prs-20260615", phase="triage"
            )
            porcelain = CommandResult(
                True, 0, f"worktree {root}\nHEAD a\n\nworktree {wt}\nHEAD b\n\n"
            )
            with patch("keel_visual.cli.git.worktree_list", return_value=porcelain):
                pairs = cli._run_records_in_repo(root, config)
            cmds = [ident.get("command") for _, ident in pairs]
            self.assertIn("stale-prs", cmds)

    def test_ship_activity_deduped_against_checkpoint(self):
        # ship now stamps the activity channel AND writes a checkpoint for the same
        # run — the board must show that run once (the richer checkpoint), keyed by
        # the shared run-id, not twice.
        import tempfile
        from unittest.mock import patch

        from keel import checkpoint
        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as d:
            config = self._project(d)
            rec = checkpoint.build_checkpoint_record(
                run_id="ship-7",
                command="ship",
                current_step="s8",
                base_branch="main",
                pull_request=7,
                merge_state="pending",
            )
            cp = checkpoint.resolve_path(d, config)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(checkpoint.encode_checkpoint(rec), encoding="utf-8")
            self._write(d, config, command="ship", run_id="ship-7", phase="s8", pr=7)
            porcelain = CommandResult(True, 0, f"worktree {d}\nHEAD x\n\n")
            with patch("keel_visual.cli.git.worktree_list", return_value=porcelain):
                pairs = cli._run_records_in_repo(d, config)
            self.assertEqual(len(pairs), 1)  # one card, not two
            _rs, identity = pairs[0]
            self.assertIsNone(identity.get("run_id"))  # the checkpoint pair won
            self.assertEqual(identity.get("pr"), 7)

    def test_checkpoint_without_run_id_cannot_dedup(self):
        # A checkpoint with no usable run-id can't pair with an activity record, so
        # both still list (and the missing-run-id guard is exercised).
        import tempfile
        from unittest.mock import patch

        from keel import checkpoint
        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as d:
            config = self._project(d)
            rec = checkpoint.build_checkpoint_record(
                run_id="ship-9",
                command="ship",
                current_step="s4",
                base_branch="main",
                pull_request=9,
            )
            rec["run_id"] = ""  # no usable run-id (validate ignores run_id)
            cp = checkpoint.resolve_path(d, config)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(checkpoint.encode_checkpoint(rec), encoding="utf-8")
            self._write(d, config, command="triage", run_id="triage-1", phase="classify", issue=1)
            porcelain = CommandResult(True, 0, f"worktree {d}\nHEAD x\n\n")
            with patch("keel_visual.cli.git.worktree_list", return_value=porcelain):
                pairs = cli._run_records_in_repo(d, config)
            self.assertEqual(len(pairs), 2)  # nothing deduped


class _FakeHTTPD:
    server_address = ("127.0.0.1", 8765)

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


class TestServeCli(unittest.TestCase):
    def _run(self, argv):
        import contextlib

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_load_dashboard_template(self):
        self.assertIn("board.json", cli.load_dashboard_template())

    def test_single_repo_board(self):
        import tempfile
        from unittest.mock import patch

        from keel import activity
        from keel.runner import CommandResult

        with tempfile.TemporaryDirectory() as d:
            from keel import config as cfg

            (Path(d) / ".keel").mkdir(parents=True)
            import shutil

            shutil.copy(PROJECT, Path(d) / ".keel" / "project.yaml")
            config = cfg.load_config(str(Path(d) / ".keel" / "project.yaml"))
            activity.write_activity(
                activity.record_path(d, config, "m-1"),
                activity.build_activity_record(command="morning", run_id="m-1", phase="config"),
            )
            with patch(
                "keel_visual.cli.git.worktree_list", return_value=CommandResult(False, 1, "")
            ):
                board = cli._single_repo_board(d, config)
            self.assertEqual(len(board), 1)
            self.assertEqual(board[0]["command"], "morning")

    def test_serve_all_runs_until_interrupt(self):
        from unittest.mock import patch

        captured = {}

        def fake_make_server(provider, page, **kw):
            captured["board"] = provider()  # exercise the provider closure
            captured["page"] = page
            return _FakeHTTPD()

        with (
            patch("keel_visual.cli.serve.make_server", side_effect=fake_make_server),
            patch("keel_visual.cli._aggregate_board", return_value=[{"project": "x"}]),
        ):
            rc, out, _ = self._run(["serve", "--all", "--root", ".", "--port", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("live dashboard", out)
        self.assertEqual(captured["board"], [{"project": "x"}])
        self.assertIn("board.json", captured["page"])

    def test_serve_single_repo_success(self):
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as d:
            import shutil

            (Path(d) / ".keel").mkdir(parents=True)
            shutil.copy(PROJECT, Path(d) / ".keel" / "project.yaml")
            cfg_path = str(Path(d) / ".keel" / "project.yaml")
            seen = {}

            def fake(provider, page, **kw):
                seen["board"] = provider()  # exercise the single-repo provider closure
                return _FakeHTTPD()

            with (
                patch("keel_visual.cli.serve.make_server", side_effect=fake),
                patch("keel_visual.cli._single_repo_board", return_value=[{"project": "p"}]),
            ):
                rc, _, _ = self._run(["serve", cfg_path, "--root", d])
            self.assertEqual(rc, 0)
            self.assertEqual(seen["board"], [{"project": "p"}])

    def test_serve_single_requires_path(self):
        rc, _, err = self._run(["serve", "--root", "."])
        self.assertEqual(rc, 1)
        self.assertIn("provide a path", err)

    def test_serve_missing_config(self):
        rc, _, err = self._run(["serve", "no-such.yaml", "--root", "."])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_serve_invalid_config(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.yaml"
            bad.write_text("extends: keel\n", encoding="utf-8")
            rc, _, err = self._run(["serve", str(bad), "--root", d])
            self.assertEqual(rc, 1)
            self.assertTrue(err.strip())


if __name__ == "__main__":
    unittest.main()
