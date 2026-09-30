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

from keel_visual import cli
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
                json.dumps(
                    {
                        "swarm_id": "swarm-run-1",
                        "total_workers": 1,
                        "workers": [
                            {"cluster_id": "c1", "issue": 714, "role": "docs", "status": "passed"}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            out_html = p_tmp / "out.html"
            code = main(
                [
                    "swarm",
                    ".keel/project.yaml",
                    "--root",
                    tmpdir,
                    "--out",
                    str(out_html),
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(out_html.exists())
            content = out_html.read_text(encoding="utf-8")
            self.assertIn("swarm-run-1", content)

            # Test explicit --swarm-id
            out_html2 = p_tmp / "out2.html"
            code2 = main(
                [
                    "swarm",
                    "--root",
                    tmpdir,
                    "--swarm-id",
                    "swarm-run-1",
                    "--out",
                    str(out_html2),
                ]
            )
            self.assertEqual(code2, 0)
            self.assertTrue(out_html2.exists())

            # Test explicit --swarm-id that does not exist in state_dir
            code_missing_id = main(
                [
                    "swarm",
                    "--root",
                    tmpdir,
                    "--swarm-id",
                    "nonexistent-id",
                    "--json",
                ]
            )
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

    def test_a_state_file_of_the_wrong_shape_does_not_kill_the_view(self):
        """#1273: keel-visual read the state file itself and raised on JSON of the
        wrong shape; it now draws what it can and skips the rest."""
        shapes = {
            "a worker that is not an object": '{"workers": [[]]}',
            "null workers": '{"workers": null}',
            "not an object": "[1, 2, 3]",
            "an issue that is not a number": '{"workers": [{"issue": "x"}, {"issue": 1e999}]}',
        }
        for label, text in shapes.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmpdir:
                state_dir = Path(tmpdir) / ".keel" / "state" / "swarm"
                state_dir.mkdir(parents=True)
                (state_dir / "odd.json").write_text(text, encoding="utf-8")
                out = io.StringIO()
                try:
                    with redirect_stdout(out):
                        code = main(["swarm", ".keel/project.yaml", "--root", tmpdir, "--json"])
                except Exception as exc:  # noqa: BLE001 - the defect is that it raises
                    self.fail(f"{type(exc).__name__} escaped: {exc}")
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out.getvalue())["swarm_id"], "odd")

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
                code = main(
                    [
                        "swarm",
                        "--root",
                        tmpdir,
                        "--out",
                        str(out_html),
                        "--serve",
                    ]
                )
                self.assertEqual(code, 0)
                mock_httpd.serve_forever.assert_called_once()
                mock_httpd.server_close.assert_called_once()


FIXTURE_PLAN = Path(__file__).parent / "fixtures" / "swarm-plan.json"


def _write(path: Path, data: object, mtime: float) -> None:
    """Write ``data`` as JSON and pin the file's mtime, so "newest" is not a race."""
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _plan_envelope(swarm_id: str = "swarm-fixture") -> dict:
    envelope = json.loads(FIXTURE_PLAN.read_text(encoding="utf-8"))
    envelope["plan"]["swarm_id"] = swarm_id
    return envelope


def _swarm_json(root: str, *extra: str) -> dict:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["swarm", "--root", root, "--json", *extra])
    assert code == 0
    return json.loads(out.getvalue())


class TestSwarmRunDiscovery(unittest.TestCase):
    """keel #1275: a run is found by its state file; a ``<id>.plan.json`` names no run."""

    def test_a_plan_written_after_its_state_is_not_the_newest_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / ".keel" / "state" / "swarm"
            state_dir.mkdir(parents=True)
            _write(state_dir / "swarm-a.json", {"swarm_id": "swarm-a", "workers": []}, 1000)
            # swarm-run writes the plan first today; a writer that saves it after the
            # state must not turn "swarm-a.plan" into the run the page shows.
            _write(state_dir / "swarm-a.plan.json", _plan_envelope("swarm-a"), 2000)
            data = _swarm_json(tmpdir)
            self.assertEqual(data["swarm_id"], "swarm-a")
            self.assertEqual(data["plan_status"], "persisted")
            self.assertEqual(len(data["plan"]["waves"]), 2)

    def test_the_newest_state_wins_whatever_order_the_files_were_written_in(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / ".keel" / "state" / "swarm"
            state_dir.mkdir(parents=True)
            _write(state_dir / "swarm-old.json", {"workers": []}, 1000)
            _write(state_dir / "swarm-new.json", {"workers": []}, 2000)
            # the older run's plan is the newest file of all
            _write(state_dir / "swarm-old.plan.json", _plan_envelope("swarm-old"), 3000)
            self.assertEqual(_swarm_json(tmpdir)["swarm_id"], "swarm-new")

    def test_latest_swarm_state_ignores_plans_and_answers_none_without_a_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            self.assertIsNone(cli.latest_swarm_state(state_dir))
            _write(state_dir / "swarm-a.plan.json", _plan_envelope("swarm-a"), 1000)
            self.assertIsNone(cli.latest_swarm_state(state_dir))
            _write(state_dir / "swarm-a.json", {}, 500)
            self.assertEqual(cli.latest_swarm_state(state_dir), state_dir / "swarm-a.json")

    def test_the_rule_matches_core(self):
        # The local copy exists only because the declared core floor predates core's;
        # where core has it, the two must agree on the same directory.
        from keel import swarm as core_swarm

        if not hasattr(core_swarm, "latest_swarm_id"):  # pragma: no cover - older core
            self.skipTest("the installed keel core predates latest_swarm_id")
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / ".keel" / "state" / "swarm"
            state_dir.mkdir(parents=True)
            _write(state_dir / "swarm-a.json", {}, 1000)
            _write(state_dir / "swarm-a.plan.json", _plan_envelope("swarm-a"), 2000)
            self.assertEqual(
                core_swarm.latest_swarm_id(tmpdir), cli.latest_swarm_state(state_dir).stem
            )


class TestSwarmPlanLoading(unittest.TestCase):
    """The page draws the plan ``swarm-run`` persisted, or says why it cannot."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.state_dir = Path(self.root) / ".keel" / "state" / "swarm"
        self.state_dir.mkdir(parents=True)
        _write(self.state_dir / "swarm-fixture.json", {"workers": []}, 1000)

    def tearDown(self):
        self._tmp.cleanup()

    def _plan(self, payload: object) -> dict:
        _write(self.state_dir / "swarm-fixture.plan.json", payload, 900)
        return _swarm_json(self.root)

    def test_the_fixture_is_a_plan_core_reads_back_unchanged(self):
        # The node tests draw this file; it must stay in the format core writes.
        from keel import swarm as core_swarm

        envelope = json.loads(FIXTURE_PLAN.read_text(encoding="utf-8"))
        plan = core_swarm.swarm_plan_from_payload(envelope)
        self.assertEqual(core_swarm.swarm_plan_payload(plan), envelope)
        modes = [w.mode for w in plan.waves]
        self.assertEqual(modes, ["orthogonal_parallel", "sequential_dependent"])

    def test_a_persisted_plan_is_drawn_as_it_was_written(self):
        data = self._plan(_plan_envelope())
        self.assertEqual(data["plan_status"], "persisted")
        self.assertEqual(data["plan_detail"], ".keel/state/swarm/swarm-fixture.plan.json")
        self.assertEqual(data["plan"], _plan_envelope()["plan"])
        wave2 = data["plan"]["waves"][1]
        self.assertEqual(wave2["mode"], "sequential_dependent")
        self.assertEqual(wave2["clusters"][0]["depends_on_issues"], [11])

    def test_a_plan_core_saved_for_a_real_run_is_the_one_drawn(self):
        """End to end with core's own writer: two waves and an edge, not one flat wave."""
        from keel import swarm as core_swarm

        if not hasattr(core_swarm, "save_swarm_plan"):  # pragma: no cover - older core
            self.skipTest("the installed keel core does not persist plans")
        scopes = [
            core_swarm.IssueScope(issue=1, predicted_files=("src/x.py",), scope_source="override"),
            core_swarm.IssueScope(issue=2, predicted_files=("src/x.py",), scope_source="override"),
        ]
        plan = core_swarm.build_swarm_plan(scopes, swarm_id="swarm-fixture")
        core_swarm.save_swarm_plan(plan, self.root)
        data = _swarm_json(self.root)
        self.assertEqual(data["plan"], plan.to_dict())
        self.assertEqual(len(data["plan"]["waves"]), 2)
        self.assertEqual(data["plan"]["waves"][1]["clusters"][0]["depends_on_issues"], [1])

    def test_no_plan_file_is_reported_and_nothing_is_rebuilt(self):
        data = _swarm_json(self.root)
        self.assertIsNone(data["plan"])
        self.assertEqual(data["plan_status"], "missing")
        self.assertIn(
            "no persisted plan at .keel/state/swarm/swarm-fixture.plan.json", data["plan_detail"]
        )

    def test_no_run_at_all_is_reported(self):
        with tempfile.TemporaryDirectory() as empty:
            data = _swarm_json(empty)
        self.assertEqual(data["swarm_id"], "swarm-empty")
        self.assertIsNone(data["plan"])
        self.assertEqual(data["plan_status"], "missing")
        self.assertEqual(data["plan_detail"], "no swarm run under .keel/state/swarm/")

    def test_an_unusable_plan_file_is_shown_as_unreadable(self):
        wrong_version = _plan_envelope()
        wrong_version["version"] = 2
        bool_version = _plan_envelope()
        bool_version["version"] = True
        wrong_schema = _plan_envelope()
        wrong_schema["schema"] = "keel.swarm-state"
        no_plan = _plan_envelope()
        no_plan["plan"] = []
        bad_mode = _plan_envelope()
        bad_mode["plan"]["waves"][0]["mode"] = "funnel"
        cases = {
            "not JSON": ("{torn", "not JSON"),
            "not an object": ("[1]", "expected a JSON object, found list"),
            "an unknown version": (wrong_version, "schema version 2 is not one"),
            "a boolean version": (bool_version, "schema version True is not one"),
            "another schema": (wrong_schema, "not a keel swarm plan"),
            "a plan that is not an object": (no_plan, "'plan' is not an object"),
            "a plan core refuses": (bad_mode, "'funnel' is not one of"),
            "another run's plan": (_plan_envelope("swarm-other"), "'swarm-other', not"),
        }
        for label, (payload, reason) in cases.items():
            with self.subTest(label):
                data = self._plan(payload)
                self.assertIsNone(data["plan"])
                self.assertEqual(data["plan_status"], "unreadable")
                self.assertTrue(
                    data["plan_detail"].startswith(".keel/state/swarm/swarm-fixture.plan.json: ")
                )
                self.assertIn(reason, data["plan_detail"])

    def test_a_plan_path_that_cannot_be_read_is_unreadable(self):
        (self.state_dir / "swarm-fixture.plan.json").mkdir()
        data = _swarm_json(self.root)
        self.assertEqual(data["plan_status"], "unreadable")
        self.assertIn("cannot be read", data["plan_detail"])

    def test_an_explicit_swarm_id_reads_that_runs_plan(self):
        _write(self.state_dir / "swarm-later.json", {"workers": []}, 5000)
        _write(self.state_dir / "swarm-fixture.plan.json", _plan_envelope(), 900)
        data = _swarm_json(self.root, "--swarm-id", "swarm-fixture")
        self.assertEqual(data["plan_status"], "persisted")
        self.assertEqual(_swarm_json(self.root)["plan_status"], "missing")  # swarm-later


class TestSwarmPlanWithAnOlderCore(unittest.TestCase):
    """A core without ``swarm_plan_from_payload`` (the declared floor): the local check."""

    def _parse(self, data: object, swarm_id: str = "swarm-fixture") -> dict:
        from keel import swarm as core_swarm

        with patch.object(core_swarm, "swarm_plan_from_payload", None, create=True):
            return cli.swarm_plan_from_payload(data, swarm_id)

    def test_a_valid_plan_is_read_as_written(self):
        envelope = _plan_envelope()
        self.assertEqual(self._parse(envelope), envelope["plan"])

    def test_a_plan_the_page_cannot_walk_is_refused(self):
        def envelope_with(**changes):
            envelope = _plan_envelope()
            envelope["plan"].update(changes)
            return envelope

        cases = {
            "no swarm id": (envelope_with(swarm_id=7), "no 'swarm_id' string"),
            "waves not a list": (envelope_with(waves={}), "no 'waves' list"),
            "a wave not an object": (envelope_with(waves=["x"]), "waves[0] is not a wave"),
            "clusters not a list": (
                envelope_with(waves=[{"clusters": "x"}]),
                "waves[0] is not a wave",
            ),
            "a cluster not an object": (
                envelope_with(waves=[{"clusters": [{}, 3]}]),
                "waves[0] is not a wave",
            ),
            "scopes not an object": (envelope_with(issue_scopes=[]), "'issue_scopes' is not"),
            "conflicts not an object": (envelope_with(conflict_map=None), "'conflict_map' is not"),
            "another run": (_plan_envelope("swarm-other"), "'swarm-other', not"),
        }
        for label, (payload, reason) in cases.items():
            with self.subTest(label), self.assertRaises(cli.SwarmPlanUnreadable) as caught:
                self._parse(payload)
            self.assertIn(reason, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
