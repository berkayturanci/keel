"""Unit tests for the keel CLI."""

import atexit
import contextlib
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from keel import cli, install, ledger, model, runtime, ship, stepverifier
from keel.runner import CommandResult

# Module-level scratch directory backing the path-returning helpers below.
# Everything written here vanishes when the process exits, so the test suite
# leaves no stray temp files behind.
_TMP = tempfile.TemporaryDirectory()
atexit.register(_TMP.cleanup)
_TMP_COUNTER = itertools.count()


PROJECTS = Path(__file__).resolve().parent.parent / "projects"
REPO_ROOT = PROJECTS.parent


#: Realistic object names for the git fakes. The wrappers validate the shape of a
#: parsed SHA (see ``git._SHA_RE``), so a placeholder like ``"tip"`` no longer
#: passes for one.
SHA_TIP = "1f2e3d4c5b6a79887766554433221100ffeeddcc"
SHA_HEAD = "0c4589650d0f129271ca84779442d1046ceb8482"


def _proc(output="", *, ok=True):
    """A fake ``run_argv`` return whose **stdout** carries ``output``.

    Parsers read ``.stdout`` alone, never the concatenated ``.output`` (#629), so a
    fake that populates only ``output`` would read back as an empty stream.
    """
    return CommandResult(ok, 0 if ok else 1, output, stdout=output)


def _trusted_comment(body):
    return {"body": body, "author_association": "MEMBER"}


def _write_json_fixture(path, value):
    Path(_write_raw(json.dumps(value))).replace(path)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


#: Held between ``setUpModule`` and ``tearDownModule``.
_NO_REAL_JURY = None
_NO_GH_AUTH_PROBE = None

#: The real ``runtime.detect``, called by the stub below with its ``run`` seam filled in.
_REAL_DETECT = runtime.detect


def _detect_without_probing_gh(root=".", **kwargs):
    """``runtime.detect`` with its one subprocess replaced by a fixed answer.

    ``detect`` shells out exactly once — ``gh auth status``, for the ``gh-auth``
    capability — and takes a ``run`` seam for precisely this. Everything else it
    reports (``shutil.which`` lookups, env flags, a write probe) is local and cheap,
    so only the seam is filled and the rest runs for real.
    """
    kwargs.setdefault("run", lambda *_a, **_kw: _proc("gh auth status not probed", ok=False))
    return _REAL_DETECT(root, **kwargs)


def setUpModule():
    """Keep this module off the two external CLIs the code under test can reach.

    **jury.** ``ship`` and ``run-gates`` run the ``jury`` built-in for real, so any
    test here that produces a **non-empty** diff with ``jury`` in ``gates:`` shells out
    to the CLI whenever it happens to be on PATH. CI never installs it, so such a test
    is instant and green there and, on a developer machine, spends ~85 s running a
    billed cross-vendor review of a throwaway diff — and fails outright if that review
    returns anything blocking, because ``ship`` exits 1 on a blocking verdict.

    **gh auth.** Eight-plus CLI commands call ``runtime.detect``, which probes
    ``gh auth status`` for the ``gh-auth`` capability. Driving those commands through
    ``cli.main`` ran the probe **111 times** in one pass of this module. On a machine
    with an authenticated ``gh`` each probe validates the token against the API, which
    was ~55 s of the module's ~65 s runtime — 85 % of it, spent on 111 requests that
    tell the tests nothing.

    Both stubs report the state CI actually runs in — no ``jury`` installed, and no
    ``GH_TOKEN``, so the probe fails there too — which is why nothing had to change to
    accommodate them. Each CLI's own behaviour is covered where it belongs:
    ``tests/test_jury.py`` and ``tests/test_runtime.py`` both drive the real functions
    through their injection seams.
    """
    global _NO_REAL_JURY, _NO_GH_AUTH_PROBE
    _NO_REAL_JURY = patch("keel.jury.available", return_value=False)
    _NO_REAL_JURY.start()
    _NO_GH_AUTH_PROBE = patch("keel.runtime.detect", _detect_without_probing_gh)
    _NO_GH_AUTH_PROBE.start()


def tearDownModule():
    _NO_GH_AUTH_PROBE.stop()
    _NO_REAL_JURY.stop()


class TestVersion(unittest.TestCase):
    def test_version_subcommand(self):
        rc, out, _ = run(["version"])
        self.assertEqual(rc, 0)
        self.assertIn("keel", out)


class TestNoCommand(unittest.TestCase):
    def test_prints_help_and_returns_2(self):
        rc, out, _ = run([])
        self.assertEqual(rc, 2)
        self.assertIn("usage", out.lower())


class TestValidate(unittest.TestCase):
    def test_valid_configs(self):
        rc, out, _ = run(["validate", str(PROJECTS / "keel.yaml"),
                          str(PROJECTS / "example-android.yaml")])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("OK"), 2)

    def test_missing_file(self):
        rc, out, _ = run(["validate", str(PROJECTS / "nope.yaml")])
        self.assertEqual(rc, 1)
        self.assertIn("MISSING", out)

    def test_invalid_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")  # missing required keys
            bad = f.name
        self.addCleanup(os.unlink, bad)
        rc, out, _ = run(["validate", bad])
        self.assertEqual(rc, 1)
        self.assertIn("INVALID", out)

    def test_strict_extensions_missing_root(self):
        # example-flutter references extension files not present in this repo -> strict fail.
        rc, out, _ = run(["validate", str(PROJECTS / "example-flutter.yaml"),
                          "--root", str(REPO_ROOT)])
        self.assertEqual(rc, 1)
        self.assertIn("extensions", out)


class TestPlan(unittest.TestCase):
    def test_plan_renders_backbone(self):
        rc, out, err = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("s10  merge", out)
        self.assertIn("gate: build", out)
        self.assertIn("runtime capabilities", out)

    def test_plan_json_includes_capabilities(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT), "--json"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("contract", data)
        self.assertIn("capabilities", data)
        self.assertIn("github_transport", data)
        self.assertIn("plan", data)
        self.assertEqual(data["contract"]["schema_version"], "keel.command-contract.v1")
        self.assertEqual(data["contract"]["command"], "ship")
        self.assertIn("review_merge_contract", data["contract"])
        self.assertEqual(data["contract"]["capture"]["schema_version"],
                         "keel.capture.v1")
        self.assertTrue(data["contract"]["capture"]["fail_soft"]["enabled"])
        self.assertEqual(data["contract"]["run_ledger"]["schema_version"],
                         "keel.run-ledger.v1")
        self.assertEqual(data["contract"]["checkpoint"]["schema_version"],
                         "keel.checkpoint.v1")
        self.assertEqual(data["contract"]["evidence"]["schema_version"], "keel.evidence.v1")
        self.assertIn("pull_request_body", data["contract"]["evidence"]["not_accepted"])

    def test_plan_json_resolves_review_jury_flags(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--command", "ship", "--reviewers", "2", "--review-comments", "summary",
             "--jury", "--jury-advisory", "--json"]
        )
        self.assertEqual(rc, 0)
        review = json.loads(out)["contract"]["review_merge_contract"]
        self.assertEqual(review["reviewers"]["count"], 2)
        self.assertEqual(review["reviewers"]["source"], "override")
        self.assertEqual(review["posting"]["mode"], "summary")
        self.assertEqual(review["jury"]["mode"], "advisory")

    def test_plan_json_exposes_evidence_requirements_from_review_flags(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--command", "ship", "--reviewers", "2", "--jury", "--json"]
        )
        self.assertEqual(rc, 0)
        evidence_contract = json.loads(out)["contract"]["evidence"]
        ids = [item["id"] for item in evidence_contract["required"]]
        self.assertEqual(ids, [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "review-verdict-2",
            "jury-verdict",
        ])

    def test_plan_json_includes_issue_intake(self):
        body = (
            "## Problem\nAgents need issue readiness.\n\n"
            "## Deliverable\nExpose a structured intake record.\n\n"
            "## Acceptance criteria\n"
            "- Dry-run JSON includes readiness.\n"
        )
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--command", "ship", "--issue-title", "Add intake", "--issue-body", body,
             "--issue-label", "enhancement,workflow", "--json"]
        )
        self.assertEqual(rc, 0)
        intake = json.loads(out)["contract"]["issue_intake"]
        self.assertEqual(intake["status"], "ready")
        self.assertTrue(intake["provided"])
        self.assertEqual(intake["ledger_record"]["readiness"], "ready")

    def test_plan_json_can_expose_other_command_graph(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--command", "morning", "--json"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "morning")
        self.assertTrue(data["contract"]["graph"])
        self.assertIn("gh", data["contract"]["optional_capabilities"])

    def test_plan_live_json_blocks_when_consent_missing(self):
        rc, out, err = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--live", "--json", "--target", "issue #82"]
        )
        self.assertEqual(rc, 1)
        data = json.loads(out)
        consent = data["contract"]["operator_consent"]
        self.assertEqual(consent["status"], "missing")
        self.assertTrue(consent["requires_operator_consent"])
        self.assertIn("filesystem", consent["missing_scope"])
        self.assertIn("operator consent", err)

    def test_plan_live_json_accepts_approved_scope(self):
        rc, out, _ = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--live", "--json", "--target", "issue #82",
             "--approve-scope", "filesystem,git,github", "--operator", "tester"]
        )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        consent = data["contract"]["operator_consent"]
        self.assertEqual(consent["status"], "approved")
        self.assertEqual(consent["consent_record"]["operator"], "tester")
        self.assertFalse(consent["consent_record"]["secret_values_recorded"])

    def test_plan_missing_config(self):
        rc, _, err = run(["plan", str(PROJECTS / "nope.yaml")])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_plan_reports_extension_problems_on_stderr(self):
        # example-flutter's extension files are not in this repo -> fail-soft warnings.
        rc, out, err = run(["plan", str(PROJECTS / "example-flutter.yaml"),
                            "--root", str(REPO_ROOT)])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)

    def test_plan_invalid_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")
            bad = f.name
        self.addCleanup(os.unlink, bad)
        rc, _, err = run(["plan", bad])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)


def _write_raw(text):
    path = Path(_TMP.name) / f"cfg-{next(_TMP_COUNTER)}.yaml"
    path.write_text(text)
    return str(path)


def _write_config(build_cmd):
    return _write_raw(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        f"repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: {build_cmd}\n"
    )


def _write_config_with_ledger(
    build_cmd,
    ledger_path="state/runs.jsonl",
    extra_policy_pack_lines: list[str] | None = None,
):
    extra = "\n".join(extra_policy_pack_lines or [])
    if extra:
        extra = f"\n{extra}\n"
    return _write_raw(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        f"repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: {build_cmd}\n"
        "policy_pack:\n  name: tmp\n  reports:\n"
        f"    run_ledger: {ledger_path!r}\n"
        f"{extra}"
    )


def _write_config_with_checkpoint(build_cmd, checkpoint_path="state/checkpoint.json"):
    return _write_raw(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        f"repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: {build_cmd}\n"
        "policy_pack:\n  name: tmp\n  reports:\n"
        f"    checkpoint: {checkpoint_path!r}\n"
    )


def _write_config_with_state_paths(
    build_cmd,
    ledger_path="state/runs.jsonl",
    checkpoint_path="state/checkpoint.json",
):
    return _write_raw(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        f"repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: {build_cmd}\n"
        "policy_pack:\n  name: tmp\n  reports:\n"
        f"    run_ledger: {ledger_path!r}\n"
        f"    checkpoint: {checkpoint_path!r}\n"
    )


def _abs_state_path(name):
    """An absolute path on the current OS, forward-slashed.

    A leading-slash path like ``/tmp/x`` is absolute on POSIX but *not* on Windows
    (which needs a drive), so it would trip the "escapes the project root" branch
    there instead of "must be relative". Building the path with ``os.path.abspath``
    keeps it absolute on every OS; replacing the separator with ``/`` keeps it
    YAML-safe (single-quoted backslashes would double).
    """
    return os.path.abspath(name).replace(os.sep, "/")


class TestAutoStamp(unittest.TestCase):
    """plan/run-gates/merge auto-stamp the activity board with no agent dependence."""

    def _config(self):
        from keel import config as cfg
        return cfg.load_config(str(PROJECTS / "example-android.yaml"))

    def _rec(self, d, run_id):
        from keel import activity
        return activity.read_activity(activity.record_path(d, self._config(), run_id))

    def _put(self, d, run_id, phase, status="running", command="ship"):
        from keel import activity
        rec = activity.build_activity_record(
            command=command, run_id=run_id, phase=phase, status=status)
        activity.write_activity(activity.record_path(d, self._config(), run_id), rec)

    def test_writes_at_phase(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cli._autostamp(self._config(), d, "ship", "r1", "s4", issue=4, pr=8)
            r = self._rec(d, "r1")
            self.assertEqual((r["phase"], r["issue"], r["pr"]), ("s4", 4, 8))

    def test_no_run_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cli._autostamp(self._config(), d, "ship", None, "s0")
            self.assertIsNone(self._rec(d, "x"))

    def test_carries_a_verdict_when_one_is_given(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cli._autostamp(self._config(), d, "ship", "rv", "s8", verdict="blocked")
            r = self._rec(d, "rv")
            # Position and outcome are separate facts: the run advanced to s8
            # (status stays running) and did not pass it.
            self.assertEqual((r["phase"], r["status"], r["verdict"]),
                             ("s8", "running", "blocked"))

    def test_a_stamp_without_a_verdict_records_none_not_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cli._autostamp(self._config(), d, "ship", "rn", "s4")
            self.assertIsNone(self._rec(d, "rn")["verdict"])

    def test_unknown_command(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cli._autostamp(self._config(), d, "nope", "r", "s0")
            self.assertIsNone(self._rec(d, "r"))

    def test_unknown_phase(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cli._autostamp(self._config(), d, "ship", "r", "s99")
            self.assertIsNone(self._rec(d, "r"))

    def test_advances_forward(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._put(d, "r", "s0")
            cli._autostamp(self._config(), d, "ship", "r", "s8")
            self.assertEqual(self._rec(d, "r")["phase"], "s8")

    def test_does_not_regress(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._put(d, "r", "s10")
            cli._autostamp(self._config(), d, "ship", "r", "s8")   # earlier → ignored
            self.assertEqual(self._rec(d, "r")["phase"], "s10")

    def test_overwrites_when_existing_phase_off_flow(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._put(d, "r", "health", command="morning")   # other command's phase
            cli._autostamp(self._config(), d, "ship", "r", "s8")
            r = self._rec(d, "r")
            self.assertEqual((r["command"], r["phase"]), ("ship", "s8"))

    def test_fail_soft_on_write_error(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            with patch("keel.cli.activity.write_activity", side_effect=OSError("x")):
                cli._autostamp(self._config(), d, "ship", "r", "s0")   # no raise
            self.assertIsNone(self._rec(d, "r"))

    def test_stamps_merged_status(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._put(d, "r", "s10")   # running at merge phase
            cli._autostamp(self._config(), d, "ship", "r", "s10", status="merged")
            r = self._rec(d, "r")
            self.assertEqual((r["phase"], r["status"]), ("s10", "merged"))

    def test_merged_is_terminal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._put(d, "r", "s10", status="merged")
            cli._autostamp(self._config(), d, "ship", "r", "s0")   # re-run start → ignored
            r = self._rec(d, "r")
            self.assertEqual((r["phase"], r["status"]), ("s10", "merged"))

    def test_plan_cli_stamps_the_run(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _out, _err = run([
                "plan", str(PROJECTS / "example-android.yaml"), "--root", d,
                "--command", "ship", "--run-id", "ship-7", "--issue", "7"])
            self.assertEqual(rc, 0)
            r = self._rec(d, "ship-7")
            self.assertEqual((r["command"], r["phase"], r["issue"]), ("ship", "s0", 7))

    def test_plan_wrapper_skips_unknown_command(self):
        import tempfile
        import types
        with tempfile.TemporaryDirectory() as d:
            args = types.SimpleNamespace(command_contract="nope", run_id="r",
                                         issue=None, pull_request=None, root=d)
            cli._plan_stamp_activity(args, self._config())
            self.assertIsNone(self._rec(d, "r"))


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


#: A gate command that outlives the 1s limit these tests use, driven by this interpreter
#: rather than a `sleep` binary: `shell=True` means cmd.exe on Windows, where `sleep` only
#: resolves via Git-for-Windows happening to be on PATH. Double quotes group correctly in
#: both sh and cmd.exe.
#:
#: 5s, not longer: on Windows the child is cmd.exe and the interpreter is a *grandchild*
#: holding the inherited pipes, so subprocess's timeout path kills cmd.exe and then blocks
#: in communicate() until the orphan exits. That bounds the real cost at this value per
#: test on the Windows matrix legs; a 4s margin over the 1s limit is ample. POSIX is
#: unaffected — sh execs the command, so the killed process is the sleeper itself.
_SLOW_CMD = f'"{sys.executable}" -c "import time; time.sleep(5)"'
#: Same command as a YAML scalar. json.dumps is valid YAML and escapes correctly, so an
#: interpreter path containing an apostrophe cannot break the document.
_SLOW_CMD_YAML = json.dumps(_SLOW_CMD)


class TestRunGates(unittest.TestCase):
    def test_passing_gate(self):
        rc, out, _ = run(["run-gates", _write_config("'true'"), "--root", "."])
        self.assertEqual(rc, 0)
        self.assertIn("ok", out)
        self.assertIn("build", out)

    def test_failing_gate_blocks(self):
        rc, out, _ = run(["run-gates", _write_config("'false'"), "--root", "."])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)
        self.assertIn("BLOCKED", out)

    def test_ship_also_renders_a_timeout_apart_from_a_failure(self):
        # #622 must land on the ship surface too, not only run-gates.
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: tmp\ngates: [build]\nknobs:\n"
                       f"  build_gate_cmd: {_SLOW_CMD_YAML}\n  gate_timeout_s: 1\n")
        with tempfile.TemporaryDirectory() as d:
            _, out, _ = run(["ship", p, "--root", d])
        self.assertIn("TIMEOUT", out)
        self.assertNotIn("FAIL", out)

    def test_timed_out_gate_renders_apart_from_a_failure_but_still_blocks(self):
        # End-to-end proof of #622: knob -> plan_gates -> runner -> outcome -> render.
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: tmp\ngates: [build]\nknobs:\n"
                       f"  build_gate_cmd: {_SLOW_CMD_YAML}\n  gate_timeout_s: 1\n")
        rc, out, _ = run(["run-gates", p, "--root", "."])
        self.assertEqual(rc, 1)             # a timeout blocks exactly as a failure does
        self.assertIn("TIMEOUT", out)       # ...but the operator can see which it is
        self.assertIn("BLOCKED", out)
        self.assertIn("timed out after 1s", out)
        self.assertNotIn("FAIL", out)

    def test_missing_config(self):
        rc, _, err = run(["run-gates", "/no/such.yaml"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config(self):
        rc, _, err = run(["run-gates", _write_raw("extends: keel\n")])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_unknown_builtin_gate(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        rc, _, err = run(["run-gates", p])
        self.assertEqual(rc, 1)
        self.assertIn("unknown built-in gate", err)

    def test_reports_extension_problem(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "extensions:\n  tester: [ghost.md]\nextensions_dir: .keel/extensions\n")
        rc, out, err = run(["run-gates", p, "--root", "/tmp"])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)


class TestPlanErrors(unittest.TestCase):
    def test_plan_unknown_builtin_gate(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        rc, _, err = run(["plan", p])
        self.assertEqual(rc, 1)
        self.assertIn("unknown built-in gate", err)

    def test_plan_invalid_config(self):
        rc, _, err = run(["plan", _write_raw("extends: keel\n")])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)


class TestStatePathErrors(unittest.TestCase):
    def assertFriendlyStateError(self, argv, expected):
        rc, out, err = run(argv)
        self.assertEqual(rc, 1)
        self.assertIn(expected, err)
        self.assertNotIn("Traceback", out + err)

    def test_plan_validates_configured_state_paths(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad_ledger = _write_config_with_state_paths(
                "'true'",
                ledger_path=_abs_state_path("runs.jsonl"),
            )
            self.assertFriendlyStateError(
                ["plan", bad_ledger, "--root", d, "--json"],
                "invalid ledger path: run ledger path must be relative",
            )

            bad_checkpoint = _write_config_with_state_paths(
                "'true'",
                checkpoint_path=_abs_state_path("checkpoint.json"),
            )
            self.assertFriendlyStateError(
                ["plan", bad_checkpoint, "--root", d, "--json"],
                "invalid checkpoint path: checkpoint path must be relative",
            )

    def test_ledger_backed_commands_report_invalid_paths_without_traceback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths(
                "'true'",
                ledger_path=_abs_state_path("runs.jsonl"),
            )
            cases = [
                ["ledger", config, "--root", d],
                ["capture-verify", config, "--root", d, "--merged-pr", "1"],
                ["capture-reconcile", config, "--root", d, "--merged-pr", "1"],
                ["status", config, "--root", d],
                [
                    "ship", config, "--root", d, "--dry-run", "--json",
                    "--capture-status", "applied",
                ],
            ]
            for argv in cases:
                with self.subTest(command=argv[0]):
                    self.assertFriendlyStateError(
                        argv,
                        "invalid ledger path: run ledger path must be relative",
                    )

    def test_checkpoint_backed_commands_report_invalid_paths_without_traceback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths(
                "'true'",
                checkpoint_path=_abs_state_path("checkpoint.json"),
            )
            cases = [
                ["checkpoint", config, "--root", d],
                ["resume", config, "--root", d],
                ["status", config, "--root", d],
            ]
            for argv in cases:
                with self.subTest(command=argv[0]):
                    self.assertFriendlyStateError(
                        argv,
                        "invalid checkpoint path: checkpoint path must be relative",
                    )


class TestWindow(unittest.TestCase):
    def test_configured(self):
        rc, out, _ = run(["window", str(PROJECTS / "example-android.yaml")])
        self.assertEqual(rc, 0)
        self.assertIn("merge window", out)
        self.assertIn("Etc/GMT-3", out)

    def test_reports_open_and_closed_from_the_configured_window(self):
        # The command previously asserted only that the words "merge window" and the
        # timezone appeared — never OPEN vs CLOSED, which is the operator's entire
        # question, so `is_open = True` was a green mutation (#633). Rather than adding
        # a `now` seam to production (an env-settable clock would be a way to walk
        # through a closed merge window), spy on the window predicate: this pins both
        # the verdict *and* the config values that reach it.
        for is_open, expected in ((True, "OPEN"), (False, "CLOSED")):
            with self.subTest(open=is_open):
                with patch("keel.cli.window.is_merge_open", return_value=is_open) as spy:
                    rc, out, _ = run(["window", str(PROJECTS / "example-android.yaml")])
                self.assertEqual(rc, 0)
                self.assertIn(expected, out)
                self.assertEqual(spy.call_args.args, ("Etc/GMT-3", "07:00-01:30"))

    def test_not_configured(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "knobs:\n  build_gate_cmd: 'true'\n")
        rc, out, _ = run(["window", p])
        self.assertEqual(rc, 0)
        self.assertIn("no merge window", out)

    def test_missing_config(self):
        rc, _, err = run(["window", "/no/such.yaml"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config(self):
        rc, _, err = run(["window", _write_raw("extends: keel\n")])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)


class TestShip(unittest.TestCase):
    def test_clean_merges(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, \
             patch("keel.git.changed_files", return_value=[]), \
             patch("keel.git.diff", return_value=""):
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel ship", out)
        self.assertIn("TIER-2", out)        # empty changeset -> default tier
        self.assertIn("DECISION", out.upper())
        self.assertIn("MERGE", out)
        self.assertIn("github        :", out)

    def test_docs_only_allowlist_reaches_the_ship_classification(self):
        # The knob was declared, parsed, hashed and echoed into the contract, and read
        # by nothing (#632). Pin the wire from config to the assessed tier: without the
        # allowlist the generated site file demotes a docs change to TIER-2; with it the
        # change stays TIER-1 (1 reviewer).
        import tempfile
        files = ["docs/keel/cli.md", "website/index.html"]
        base = ("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                "  docs_gate_paths: ['docs/**', '*.md']\n")
        without = _write_raw(base)
        with_allow = _write_raw(base + "  docs_only_allowlist: ['website/**']\n")

        def _tier(config):
            with tempfile.TemporaryDirectory() as d, \
                 patch("keel.git.changed_files", return_value=files), \
                 patch("keel.git.diff", return_value=""):
                rc, out, _ = run(["ship", config, "--root", d, "--json"])
            self.assertEqual(rc, 0)
            return json.loads(out)["result"]["assessment"]["tier"]

        self.assertEqual(_tier(without), 2)
        self.assertEqual(_tier(with_allow), 1)

    def test_an_unreadable_changeset_classifies_tier_3_not_tier_2(self):
        # A non-git root makes `git diff --name-only` fail. Reading that as an empty
        # changeset lands on the *default* tier, quietly dropping a reviewer and the
        # gating jury on a change nobody could see. Fail closed instead, and say so.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("changed files : UNREADABLE", out)
        self.assertIn("TIER-3", out)
        self.assertIn("3 reviewer(s)", out)
        self.assertIn("jury          : gating", out)

    def test_json_dry_run_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--dry-run", "--json", "--review-comments", "summary",
                              "--no-jury", "--reviewers", "1"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "ship")
        self.assertTrue(data["contract"]["dry_run"])
        self.assertFalse(data["contract"]["side_effects"]["mutates_in_dry_run"])
        self.assertEqual(data["contract"]["operator_consent"]["status"],
                         "not-required-dry-run")
        # A non-git root: the diff could not be read, which is not "0 files changed".
        self.assertIsNone(data["result"]["changed_file_count"])
        self.assertIsNone(data["result"]["changed_files"])
        self.assertTrue(data["result"]["changed_files_unreadable"])
        self.assertIsNone(
            data["result"]["run_ledger"]["record"]["changes"]["file_count"])
        self.assertTrue(data["result"]["run_ledger"]["record"]["changes"]["unreadable"])
        self.assertFalse(data["result"]["run_ledger"]["appended"])
        self.assertEqual(data["result"]["run_ledger"]["record"]["record_type"], "ship_run")
        self.assertEqual(data["result"]["assessment"]["merge"]["action"], "merge")
        self.assertEqual(data["result"]["issue_intake"]["status"], "needs-input")
        review = data["result"]["assessment"]["review_merge_contract"]
        self.assertEqual(review["reviewers"]["count"], 1)
        self.assertEqual(review["posting"]["mode"], "summary")
        self.assertEqual(review["jury"]["mode"], "off")

    def test_ship_live_blocks_non_ready_issue_before_gates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'false'"), "--root", d,
                              "--live", "--json", "--issue-title", "Ambiguous work",
                              "--issue-body", "Maybe improve this later.",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIn("contract", data)
        self.assertNotIn("result", data)
        intake = data["contract"]["issue_intake"]
        self.assertEqual(intake["status"], "needs-input")
        self.assertFalse(intake["can_mutate_code"])
        self.assertTrue(intake["questions"])

    def test_ship_live_human_blocks_non_ready_issue_before_gates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", _write_config("'false'"), "--root", d,
                              "--live", "--issue-title", "Ambiguous work",
                              "--issue-body", "Maybe improve this later.",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        self.assertIn("issue intake: needs-input", err)
        self.assertIn("question:", err)

    def test_ship_human_ready_issue_omits_question_count(self):
        import tempfile
        body = (
            "## Problem\nAgents need issue readiness.\n\n"
            "## Deliverable\nExpose intake status.\n\n"
            "## Acceptance criteria\n"
            "- Human output shows readiness.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--issue-title", "Add intake", "--issue-body", body])
        self.assertEqual(rc, 0)
        self.assertIn("intake        : ready", out)
        self.assertNotIn("questions", out)

    def test_ship_compound_json_dry_run_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--compound", "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "ship")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")
        self.assertEqual(data["contract"]["workflow_profile"]["inherits"], "ship")
        self.assertEqual(
            data["contract"]["workflow_profile"]["step_overrides"]["s7"]["step"],
            "review",
        )
        self.assertIn("review_merge_contract", data["contract"])
        self.assertIn("result", data)
        # The graph marks the four overridden backbone steps as compound.
        compound_steps = {
            row["step_id"]
            for row in data["contract"]["graph"]
            if row["profile_step"] == "compound"
        }
        self.assertEqual(compound_steps, {"s4", "s7", "s9", "s11"})

    def test_ship_profile_compound_alias_matches_compound_flag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--profile", "compound", "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")

    def test_ship_default_profile_is_standard(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "standard")
        self.assertTrue(all(
            row["profile_step"] == "standard"
            for row in data["contract"]["graph"]
        ))

    def test_ship_compound_composes_with_jury(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--compound", "--jury", "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")
        self.assertTrue(data["contract"]["review_merge_contract"]["jury"]["enabled"])

    def test_ship_v2_is_not_a_subcommand(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            cli.main(["ship-v2", "x.yaml", "--dry-run", "--json"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("invalid choice: 'ship-v2'", err.getvalue())

    def test_plan_compound_profile_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["plan", _write_config("'true'"), "--root", d,
                              "--command", "ship", "--profile", "compound", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "compound")

    def test_json_contract_matches_assessment_for_tier3_auto_jury(self):
        # The only test here with a non-empty diff *and* jury in ``gates:``, so the
        # only one that would reach a real ``jury`` install — see ``setUpModule``.
        # What is under test is the contract tier-3 resolves to, not the review.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _run_git(root, "init", "-b", "main")
            _run_git(root, "config", "user.email", "test@example.com")
            _run_git(root, "config", "user.name", "Test User")
            (root / "README.md").write_text("base\n", encoding="utf-8")
            _run_git(root, "add", "README.md")
            _run_git(root, "commit", "-m", "base")
            _run_git(root, "checkout", "-b", "feature")
            workflow = root / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            # Privileged on purpose. Since #845 the tier is read from the diff, so a
            # workflow whose change touches nothing privileged downgrades to TIER-2 —
            # which would leave this test asserting tier-3 against a tier-2 change and
            # quietly stop exercising the gating-jury path it exists for.
            workflow.write_text(
                "name: ci\npermissions:\n  contents: write\n", encoding="utf-8")
            _run_git(root, "add", ".github/workflows/ci.yml")
            _run_git(root, "commit", "-m", "change workflow")

            config = _write_raw(
                "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: tmp\n"
                "gates: [build, jury]\nknobs:\n  build_gate_cmd: 'true'\n"
                "  tier3_globs: ['.github/workflows/**']\n"
            )
            rc, out, _ = run(["ship", config, "--root", d, "--dry-run", "--json"])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        contract_review = data["contract"]["review_merge_contract"]
        assessment_review = data["result"]["assessment"]["review_merge_contract"]
        self.assertEqual(contract_review["reviewers"]["tier"], 3)
        self.assertEqual(contract_review["reviewers"]["count"], 3)
        self.assertEqual(contract_review["reviewers"]["source"], "risk-tier")
        self.assertEqual(contract_review["jury"]["mode"], "gating")
        self.assertEqual(contract_review, assessment_review)

    def test_ship_rejects_conflicting_live_and_dry_run_flags(self):
        rc, _, err = run(["ship", _write_config("'true'"), "--dry-run", "--live"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot be used together", err)

    def test_ship_live_json_blocks_before_running_gates_without_consent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'false'"), "--root", d,
                              "--live", "--json", "--target", "issue #82"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIn("contract", data)
        self.assertNotIn("result", data)
        self.assertEqual(data["contract"]["operator_consent"]["status"], "missing")
        self.assertIn("github", data["contract"]["operator_consent"]["missing_scope"])

    def test_ship_live_json_runs_after_consent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--live", "--json", "--target", "issue #82",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["mode"], "live")
        self.assertEqual(data["contract"]["operator_consent"]["status"], "approved")
        self.assertIn("result", data)

    def test_ship_live_can_append_structured_ledger_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--run-id", "RUN-140",
                              "--issue", "140", "--pull-request", "160",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--implementer", "codex:gpt-5",
                              "--reviewer-agent", "reviewer-a:gpt-5",
                              "--reviewer-agent", "reviewer-b:claude",
                              "--tester", "tester:gpt-5-mini",
                              "--host-agent", "claude",
                              "--transport", "mcp",
                              "--profile", "compound",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            ledger_path = Path(d) / "state" / "runs.jsonl"
            rc_read, out_read, _ = run(["ledger", config, "--root", d, "--json"])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["result"]["run_ledger"]["appended"])
        self.assertEqual(data["result"]["run_ledger"]["path"], str(ledger_path.resolve()))
        self.assertEqual(data["result"]["run_ledger"]["record"]["issue"]["number"], 140)
        self.assertEqual(rc_read, 0)
        read = json.loads(out_read)
        self.assertEqual(read["record_count"], 1)
        self.assertEqual(read["records"][0]["run_id"], "RUN-140")
        self.assertEqual(read["records"][0]["capture"]["status"], "skipped")
        self.assertEqual(read["records"][0]["capture"]["marker_reason"], "no-policy")
        self.assertEqual(read["records"][0]["capture"]["marker"],
                         "compound-learning: pr=160 status=skipped:no-policy")
        self.assertEqual(read["records"][0]["actors"]["implementer"], "codex:gpt-5")
        self.assertEqual(read["records"][0]["actors"]["reviewers"],
                         ["reviewer-a:gpt-5", "reviewer-b:claude"])
        run_context = read["records"][0]["run_context"]
        self.assertEqual(run_context["host_agent"], "claude")
        self.assertEqual(run_context["transport"], "mcp")
        self.assertEqual(run_context["profile"], "compound")
        # jury_mode is derived from the resolved review contract.
        self.assertIn(run_context["jury_mode"], ("off", "advisory", "gating"))
        # consent is derived from the resolved operator-consent contract.
        self.assertEqual(run_context["consent"]["status"], "approved")
        self.assertEqual(read["records"][0]["redaction"]["status"], "applied")

    def test_ship_ledger_transport_defaults_to_resolved_transport(self):
        # With no --transport flag, the record carries the resolved transport.
        # Patch capability detection so `gh` resolves deterministically offline.
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", True, "ok", "test"),
            runtime.Capability("gh-auth", True, "ok", "test"),
            runtime.Capability("github-mcp", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--run-id", "RUN-141",
                              "--issue", "141", "--pull-request", "161",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            rc_read, out_read, _ = run(["ledger", config, "--root", d, "--json"])

        data = json.loads(out)
        self.assertTrue(data["result"]["run_ledger"]["appended"])
        self.assertEqual(rc_read, 0)
        run_context = json.loads(out_read)["records"][0]["run_context"]
        self.assertEqual(run_context["transport"], "gh")
        self.assertIsNone(run_context["host_agent"])
        self.assertEqual(run_context["profile"], "standard")
        self.assertEqual(
            data["result"]["run_ledger"]["warnings"],
            ["missing host_agent in live run context"],
        )

    def test_ship_live_append_strict_run_context_blocks_missing_host_agent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--run-id", "RUN-264",
                              "--issue", "264", "--pull-request", "275",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--strict-run-context",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            ledger_path = Path(d) / "state" / "runs.jsonl"

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIn("missing host_agent", data["error"])
        self.assertFalse(ledger_path.exists())

    def test_ship_live_append_strict_run_context_human_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--live",
                              "--append-ledger", "--run-id", "RUN-264-H",
                              "--issue", "264", "--pull-request", "275",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--strict-run-context",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])

        self.assertEqual(rc, 1)
        self.assertIn("missing host_agent", err)

    def test_ship_human_append_warns_on_missing_host_agent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--live",
                              "--append-ledger", "--run-id", "RUN-265",
                              "--issue", "265", "--pull-request", "276",
                              "--capture-status", "skipped",
                              "--capture-reason", "no capture hook configured",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])

        self.assertEqual(rc, 0)
        self.assertIn("run context   : warning: missing host_agent", out)

    def test_ship_json_uses_learning_policy_in_ledger_record(self):
        import tempfile
        body = (
            "## Problem\nLearning policy must affect ship records.\n\n"
            "## Deliverable\nEmit the configured learning decision.\n\n"
            "## Acceptance criteria\n"
            "- JSON contains create-learning.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture:",
                    "    learning:",
                    "      enabled: true",
                    "      mode: create-learning",
                    "      reason: new invariant",
                ],
            )
            rc, out, _ = run([
                "ship", config, "--root", d, "--dry-run", "--json",
                "--issue-title", "Release invariant",
                "--issue-body", body,
                "--issue-label", "enhancement,release",
                "--pull-request", "181",
                "--capture-status", "applied",
            ])

        self.assertEqual(rc, 0)
        learning = json.loads(out)["result"]["run_ledger"]["record"]["capture"]["learning"]
        self.assertEqual(learning["decision"], "create-learning")
        self.assertEqual(learning["reason"], "new invariant")
        self.assertTrue(learning["durable_artifact"])

    def test_ship_json_uses_existing_ledger_and_issue_context_for_learning_dedupe(self):
        import tempfile
        body = (
            "## Problem\nLearning dedupe needs issue context.\n\n"
            "## Deliverable\nUse title and labels in the fingerprint.\n\n"
            "## Acceptance criteria\n"
            "- Same title and labels duplicate.\n"
            "- Different title does not duplicate.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture:",
                    "    learning:",
                    "      enabled: true",
                    "      mode: create-learning",
                ],
            )
            first = [
                "ship", config, "--root", d, "--live", "--append-ledger", "--json",
                "--run-id", "RUN-FIRST",
                "--issue-title", "Release invariant",
                "--issue-body", body,
                "--issue-label", "enhancement,release",
                "--pull-request", "181",
                "--capture-status", "applied",
                "--approve-scope", "filesystem,git,github",
                "--operator", "tester",
            ]
            rc_first, _, _ = run(first)
            second = [
                "ship", config, "--root", d, "--dry-run", "--json",
                "--issue-title", "Release invariant",
                "--issue-body", body,
                "--issue-label", "enhancement,release",
                "--pull-request", "182",
                "--capture-status", "applied",
            ]
            rc_second, out_second, _ = run(second)
            different_title = [
                "ship", config, "--root", d, "--dry-run", "--json",
                "--issue-title", "Different release invariant",
                "--issue-body", body,
                "--issue-label", "enhancement,release",
                "--pull-request", "183",
                "--capture-status", "applied",
            ]
            rc_third, out_third, _ = run(different_title)

        self.assertEqual(rc_first, 0)
        self.assertEqual(rc_second, 0)
        duplicate = json.loads(out_second)["result"]["run_ledger"]["record"]["capture"]["learning"]
        self.assertEqual(duplicate["decision"], "duplicate")
        self.assertEqual(duplicate["duplicate_of"], "RUN-FIRST")
        self.assertEqual(rc_third, 0)
        not_duplicate = json.loads(out_third)["result"]["run_ledger"]["record"]["capture"][
            "learning"
        ]
        self.assertEqual(not_duplicate["decision"], "create-learning")
        self.assertNotIn("duplicate_of", not_duplicate)

    def test_ship_json_blocks_on_malformed_existing_ledger_before_record_build(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            ledger_path = Path(d) / "state" / "runs.jsonl"
            ledger_path.parent.mkdir(parents=True)
            ledger_path.write_text("{", encoding="utf-8")
            rc, _, err = run([
                "ship", config, "--root", d, "--dry-run", "--json",
                "--capture-status", "applied",
            ])

        self.assertEqual(rc, 1)
        self.assertIn("invalid ledger", err)

    def test_ship_live_append_redacts_capture_record_before_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: private-host",
                    "        pattern: 'internal\\.example\\.test'",
                ],
            )
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--capture-status", "skipped",
                              "--capture-reason",
                              "Bearer abcdefghijklmnopqrstuvwxyz at internal.example.test",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            rc_read, out_read, _ = run(["ledger", config, "--root", d, "--json"])

        self.assertEqual(rc, 0)
        self.assertEqual(rc_read, 0)
        written = json.loads(out_read)["records"][0]
        serialized = json.dumps(written, sort_keys=True)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertNotIn("internal.example.test", serialized)
        self.assertEqual(written["redaction"]["redaction_count"], 2)
        self.assertEqual(json.loads(out)["result"]["run_ledger"]["record"], written)

    def test_ship_live_append_skips_write_when_redaction_policy_invalid(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: bad-regex",
                    "        pattern: '['",
                ],
            )
            rc, out, _ = run(["ship", config, "--root", d, "--live", "--json",
                              "--append-ledger", "--capture-status", "skipped",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
            ledger_path = Path(d) / "state" / "runs.jsonl"

        self.assertEqual(rc, 1)
        self.assertIn("capture redaction policy invalid", json.loads(out)["error"])
        self.assertFalse(ledger_path.exists())

    def test_ship_live_append_reports_invalid_redaction_policy_in_human_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: bad-regex",
                    "        pattern: '['",
                ],
            )
            rc, _, err = run(["ship", config, "--root", d, "--live",
                              "--append-ledger", "--capture-status", "skipped",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])

        self.assertEqual(rc, 1)
        self.assertIn("capture redaction policy invalid", err)

    def test_ship_json_invalid_redaction_policy_uses_default_redaction_for_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: bad-regex",
                    "        pattern: '['",
                ],
            )
            rc, out, _ = run(["ship", config, "--root", d, "--dry-run", "--json",
                              "--capture-status", "skipped",
                              "--capture-reason",
                              "Bearer abcdefghijklmnopqrstuvwxyz should not leak"])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        record = data["result"]["run_ledger"]["record"]
        serialized = json.dumps(record, sort_keys=True)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertEqual(record["redaction"]["status"], "partial")
        self.assertEqual(record["redaction"]["reason"], "invalid-policy")
        self.assertEqual(record["redaction"]["redaction_count"], 1)

    def test_ship_json_invalid_redaction_policy_keeps_valid_project_redactions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger(
                "'true'",
                extra_policy_pack_lines=[
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: private-host",
                    "        pattern: 'internal\\.example\\.test'",
                    "      - id: bad-regex",
                    "        pattern: '['",
                ],
            )
            rc, out, _ = run(["ship", config, "--root", d, "--dry-run", "--json",
                              "--capture-status", "skipped",
                              "--capture-reason",
                              "Bearer abcdefghijklmnopqrstuvwxyz at internal.example.test"])

        self.assertEqual(rc, 0)
        record = json.loads(out)["result"]["run_ledger"]["record"]
        serialized = json.dumps(record, sort_keys=True)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertNotIn("internal.example.test", serialized)
        self.assertEqual(record["redaction"]["status"], "partial")
        self.assertEqual(record["redaction"]["redaction_count"], 2)

    def test_ship_live_append_requires_capture_status(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--live", "--json",
                              "--append-ledger",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["error"],
                         "--capture-status is required when --live --append-ledger is used")

    def test_ship_live_append_requires_capture_status_human_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--live", "--append-ledger",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        self.assertIn("--capture-status is required", err)

    def test_ship_rejects_invalid_capture_status_before_command_runs(self):
        parser = cli.build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args([
                "ship",
                _write_config_with_ledger("'true'"),
                "--capture-status",
                "skipped:not-allowed",
            ])

    def test_ledger_reads_missing_file_as_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ledger", _write_config_with_ledger("'true'"),
                              "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["status"], "missing")
        self.assertEqual(data["records"], [])
        self.assertEqual(data["capture_health"]["status"], "clean")

    def test_ledger_human_output_and_limit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            # Distinct PRs: two capture markers on one PR is the invalid state
            # capture-verify refuses, and the append now declines to create it.
            for run_id, pr in (("RUN-1", "160"), ("RUN-2", "161")):
                rc, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                "--run-id", run_id,
                                "--pull-request", pr,
                                "--capture-status", "skipped",
                                "--approve-scope", "filesystem,git,github",
                                "--operator", "tester"])
                self.assertEqual(rc, 0)
            rc, out, _ = run(["ledger", config, "--root", d, "--limit", "1"])
            rc_json, out_json, _ = run(["ledger", config, "--root", d,
                                        "--limit", "1", "--json"])

        self.assertEqual(rc, 0)
        self.assertIn("keel ledger", out)
        self.assertIn("records       : 1", out)
        self.assertIn("capture       : clean", out)
        self.assertIn("capture gaps  : 0", out)
        self.assertEqual(rc_json, 0)
        read = json.loads(out_json)
        self.assertEqual(read["records"][0]["run_id"], "RUN-2")
        self.assertEqual(read["capture_health"]["counts"]["skipped"], 1)

    def test_repeating_the_ledger_append_does_not_duplicate_a_capture_marker(self):
        # Re-running the append is the natural retry after a crash mid-s11. A second
        # marker for the same PR makes capture-verify refuse the whole session
        # ("multiple capture markers found for merged PR") and capture-reconcile return
        # `blocked` with no actions — recoverable only by editing the ledger by hand.
        # So the retry must be a no-op, not the thing that bricks the run.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            argv = ["ship", config, "--root", d, "--live", "--append-ledger",
                    "--run-id", "ship-42", "--pull-request", "7",
                    "--capture-status", "skipped",
                    "--approve-scope", "filesystem,git,github", "--operator", "tester"]
            first_rc, first_out, _ = run(argv)
            second_rc, second_out, _ = run(argv)
            verify_rc, verify_out, _ = run(
                ["capture-verify", config, "--root", d, "--merged-pr", "7"])
            read_rc, read_json, _ = run(["ledger", config, "--root", d, "--json"])

        self.assertEqual((first_rc, second_rc, read_rc), (0, 0, 0))
        self.assertIn("ledger append : yes", first_out)
        self.assertIn("ledger append : skipped", second_out)
        self.assertIn("already has a capture marker (run ship-42)", second_out)
        self.assertEqual(len(json.loads(read_json)["records"]), 1)
        # …and the session stays verifiable, which is the whole point.
        self.assertEqual(verify_rc, 0)
        self.assertNotIn("multiple capture markers", verify_out)

    def test_capture_verify_reports_complete_from_ledger_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc_ship, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                 "--pull-request", "160",
                                 "--capture-status", "skipped",
                                 "--capture-reason", "no capture hook configured",
                                 "--approve-scope", "filesystem,git,github",
                                 "--operator", "tester"])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-pr", "160", "--json"])

        self.assertEqual(rc_ship, 0)
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "complete")
        self.assertEqual(data["verification"]["results"][0]["marker"],
                         "compound-learning: pr=160 status=skipped:no-policy")

    def test_capture_verify_reports_missing_marker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-pr", "160", "--json"])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "incomplete")
        self.assertEqual(data["verification"]["results"][0]["status"], "missing")

    def test_capture_verify_human_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc_ship, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                 "--pull-request", "160",
                                 "--capture-status", "applied",
                                 "--approve-scope", "filesystem,git,github",
                                 "--operator", "tester"])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-pr", "160"])

        self.assertEqual(rc_ship, 0)
        self.assertEqual(rc, 0)
        self.assertIn("keel capture-verify", out)
        self.assertIn("ok  PR #160", out)

    def test_capture_verify_reports_config_and_ledger_errors(self):
        import tempfile
        rc_missing, _, err_missing = run(["capture-verify", "/no/such.yaml",
                                          "--merged-pr", "160"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run(["capture-verify", _write_raw("extends: keel\n"),
                                          "--merged-pr", "160"])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            path = Path(d) / "state" / "runs.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            rc_bad, _, err_bad = run(["capture-verify", config, "--root", d,
                                      "--merged-pr", "160"])

        self.assertEqual(rc_bad, 1)
        self.assertIn("invalid ledger", err_bad)

    def test_capture_verify_requires_a_merged_pr_source(self):
        config = _write_config_with_ledger("'true'")
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["capture-verify", config, "--root", d])
        self.assertEqual(rc, 1)
        self.assertIn("provide --merged-pr or --from-transport", err)

    def _ship_applied(self, config, root, pr, *, artifact=None):
        argv = ["ship", config, "--root", root, "--live", "--append-ledger",
                "--pull-request", str(pr),
                "--capture-status", "applied",
                "--approve-scope", "filesystem,git,github",
                "--operator", "tester"]
        if artifact is not None:
            argv += ["--capture-artifact", artifact]
        rc, _, _ = run(argv)
        self.assertEqual(rc, 0)

    def test_capture_verify_back_compat_merged_pr_path_passes(self):
        # Legacy offline path: applied capture, no artifact, only --merged-pr.
        # Reconcile is not activated, so the historic pass/fail is preserved.
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160)
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-pr", "160", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["merged_pr_source"]["source"], "args")
        self.assertNotIn("reconcile", data)

    def test_capture_verify_transport_derivation_catches_omitted_pr(self):
        # The agent only passes PR 160, but the transport says 160 AND 161 merged.
        # 161 has no capture marker, so derivation surfaces the omission.
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs", return_value=_proc(
                    json.dumps([{"number": 160}, {"number": 161}]))):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["merged_pr_source"]["source"], "transport")
        types = [f["type"] for f in data["reconcile"]["findings"]]
        self.assertIn("missing-marker", types)
        self.assertIn(161, [f["pr"] for f in data["reconcile"]["findings"]])

    def test_capture_verify_applied_without_artifact_fails_under_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160)  # no artifact
            fixture = Path(d) / "merged.json"
            _write_json_fixture(fixture, [{"number": 160}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture), "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["merged_pr_source"]["source"], "transport-fixture")
        types = [f["type"] for f in data["reconcile"]["findings"]]
        self.assertEqual(types, ["applied-without-artifact"])

    def test_capture_verify_applied_with_artifact_passes_under_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            fixture = Path(d) / "merged.json"
            # A junk entry (no/invalid number) is ignored by the fixture reader.
            _write_json_fixture(fixture, [{"number": 160}, {"note": "no number"},
                                          {"number": 0}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture)])
        self.assertEqual(rc, 0)
        self.assertIn("reconcile: ok", out)

    def test_capture_verify_deferred_without_artifact_ok_under_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc_ship, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                 "--pull-request", "160",
                                 "--capture-status", "deferred",
                                 "--capture-reason", "queued for later",
                                 "--approve-scope", "filesystem,git,github",
                                 "--operator", "tester"])
            self.assertEqual(rc_ship, 0)
            fixture = Path(d) / "merged.json"
            _write_json_fixture(fixture, [{"number": 160}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["reconcile"]["ok"])

    def test_capture_verify_reviewer_count_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc_ship, _, _ = run(["ship", config, "--root", d, "--live", "--append-ledger",
                                 "--pull-request", "160",
                                 "--capture-status", "applied",
                                 "--capture-artifact", "artifacts/160.md",
                                 "--reviewer-agent", "agent-a",
                                 "--reviewer-agent", "agent-b",
                                 "--approve-scope", "filesystem,git,github",
                                 "--operator", "tester"])
            self.assertEqual(rc_ship, 0)
            fixture = Path(d) / "merged.json"
            _write_json_fixture(fixture, [{"number": 160}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture),
                              "--verdict-count", "160=1", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        types = [f["type"] for f in data["reconcile"]["findings"]]
        self.assertEqual(types, ["reviewer-count-mismatch"])

    def test_capture_verify_refuses_to_certify_when_the_transport_failed(self):
        # A gh failure empties the *derived* set, so the union degenerates to exactly
        # the agent's --merged-pr list and the anti-shrink defence this command exists
        # to provide evaporates. It must not render as a clean audit (#630).
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs",
                              return_value=_proc("gh offline", ok=False)):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["merged_pr_source"]["transport_failed"])
        self.assertEqual(data["status"], "transport-unavailable")
        self.assertFalse(data["certified"])

    def test_capture_verify_human_output_names_the_failed_transport(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs",
                              return_value=_proc("gh offline", ok=False)):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport"])
        self.assertEqual(rc, 1)
        self.assertIn("capture-verify — transport-unavailable", out)
        self.assertIn("transport     : FAILED", out)
        self.assertIn("cannot certify", out)

    def test_capture_verify_transport_empty_with_no_override_errors(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            with patch.object(cli.github, "merged_prs",
                              return_value=_proc("[]")):
                rc, _, err = run(["capture-verify", config, "--root", d, "--from-transport"])
        self.assertEqual(rc, 1)
        self.assertIn("no merged PRs derived", err)

    def test_capture_verify_transport_bad_json_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs",
                              return_value=_proc("not json")):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["merged_pr_source"]["transport_failed"])
        self.assertEqual(data["status"], "transport-unavailable")
        self.assertFalse(data["certified"])

    def test_capture_verify_transport_non_list_json_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs",
                              return_value=_proc("{}")):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--merged-pr", "160", "--from-transport", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["merged_pr_source"]["transport_failed"])
        self.assertEqual(data["status"], "transport-unavailable")
        self.assertFalse(data["certified"])

    def test_capture_verify_merged_since_narrows_search(self):
        captured = {}

        def fake_merged_prs(*, search=None, cwd=None):
            captured["search"] = search
            return _proc(json.dumps([{"number": 160}]))

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160, artifact="artifacts/160.md")
            with patch.object(cli.github, "merged_prs", side_effect=fake_merged_prs):
                rc, _, _ = run(["capture-verify", config, "--root", d,
                                "--from-transport", "--merged-since", "2026-06-01"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["search"], "merged:>=2026-06-01")

    def test_capture_verify_human_output_shows_reconcile_findings(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            self._ship_applied(config, d, 160)  # no artifact
            fixture = Path(d) / "merged.json"
            _write_json_fixture(fixture, [{"number": 160}])
            rc, out, _ = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture)])
        self.assertEqual(rc, 1)
        self.assertIn("merged-PR source: transport-fixture", out)
        self.assertIn("reconcile PR #160", out)
        self.assertIn("applied-without-artifact", out)

    def test_capture_verify_bad_merged_prs_json_errors(self):
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            fixture = Path(d) / "merged.json"
            fixture.write_text("{}", encoding="utf-8")
            rc, _, err = run(["capture-verify", config, "--root", d,
                              "--merged-prs-json", str(fixture)])
        self.assertEqual(rc, 1)
        self.assertIn("must contain a JSON array", err)

    def _config_owner_repo_ledger(self):
        return _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "owner: acme\nrepo: tmp\ngates: [build]\n"
            "knobs:\n  build_gate_cmd: 'true'\n"
            "policy_pack:\n  name: tmp\n  reports:\n"
            "    run_ledger: 'state/runs.jsonl'\n"
        )

    def _write_ledger_record(self, root, pr, *, reviewers):
        record = {
            "schema_version": "keel.run-ledger.v1",
            "record_type": "ship_run",
            "pull_request": {"number": pr},
            "capture": {
                "marker": f"compound-learning: pr={pr} status=applied",
                "artifact": f"artifacts/{pr}.md",
            },
            "actors": {"reviewers": reviewers},
        }
        path = Path(root) / "state" / "runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    def test_capture_verify_live_verdict_fetch_detects_mismatch(self):
        def fake_run(argv, **kwargs):
            endpoint = argv[-1]
            if endpoint.endswith("/issues/160/comments"):
                return _proc(json.dumps([[
                    {"body": "keel.review-verdict.v1\nreviewer: a\nLGTM",
                     "author_association": "MEMBER"},
                ]]))
            if endpoint.endswith("/pulls/160/reviews"):
                return _proc(json.dumps([[]]))
            return _proc("unexpected endpoint", ok=False)

        with tempfile.TemporaryDirectory() as d:
            config = self._config_owner_repo_ledger()
            self._write_ledger_record(d, 160, reviewers=["agent-a", "agent-b"])
            with patch.object(cli.github, "merged_prs",
                              return_value=_proc(json.dumps([{"number": 160}]))), \
                    patch("keel.cli.run_argv", side_effect=fake_run):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--from-transport", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        types = [f["type"] for f in data["reconcile"]["findings"]]
        self.assertEqual(types, ["reviewer-count-mismatch"])
        self.assertEqual(data["reconcile"]["results"][0]["posted_verdicts"], 1)

    def test_capture_verify_live_verdict_fetch_failure_is_advisory(self):
        def fake_run(argv, **kwargs):
            return _proc("gh offline", ok=False)

        with tempfile.TemporaryDirectory() as d:
            config = self._config_owner_repo_ledger()
            self._write_ledger_record(d, 160, reviewers=["agent-a", "agent-b"])
            with patch.object(cli.github, "merged_prs",
                              return_value=_proc(json.dumps([{"number": 160}]))), \
                    patch("keel.cli.run_argv", side_effect=fake_run):
                rc, out, _ = run(["capture-verify", config, "--root", d,
                                  "--from-transport", "--json"])
        # Verdict fetch failed for PR 160, so the reviewer cross-check is skipped.
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsNone(data["reconcile"]["results"][0]["posted_verdicts"])

    def test_verdict_count_arg_rejects_bad_values(self):
        config = _write_config_with_ledger("'true'")
        with tempfile.TemporaryDirectory() as d:
            for bad in ("noequals", "x=1", "1=y", "0=1", "1=-1"):
                with self.assertRaises(SystemExit) as ctx:
                    run(["capture-verify", config, "--root", d,
                         "--merged-pr", "1", "--verdict-count", bad])
                self.assertEqual(ctx.exception.code, 2, bad)

    def test_evidence_verify_passes_from_offline_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nReviewer A LGTM"),
                _trusted_comment("keel.jury-verdict.v1\nAI Jury LGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            _write_json_fixture(reviews, [
                _trusted_comment("keel.review-verdict.v1\nReviewer B LGTM"),
            ])
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:codex",
                "--reviewers", "2",
                "--jury",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["issue"], 212)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["counts"]["review_verdict"], 2)

    def test_evidence_verify_missing_attribution_label_fails_when_gated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nLGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--reviewers", "1",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertTrue(any(
            f["id"] == "attribution-label"
            for f in data["verification"]["findings"]
        ))

    def _run_distinct_vendor_verify(self, *, flag, vendor_b, config=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment(
                    "keel.review-verdict.v1\nreviewer: a\nvendor: claude\nLGTM"
                ),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            _write_json_fixture(reviews, [
                _trusted_comment(
                    f"keel.review-verdict.v1\nreviewer: b\nvendor: {vendor_b}\nLGTM"
                ),
            ])
            argv = [
                "evidence-verify", config or str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:claude",
                "--reviewers", "2",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--json",
            ]
            if flag:
                argv.append("--require-distinct-vendors")
            return run(argv)

    def test_evidence_verify_require_distinct_vendors_flag_blocks_duplicates(self):
        rc, out, _ = self._run_distinct_vendor_verify(flag=True, vendor_b="claude")
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertTrue(
            any(f["id"] == "review-vendor-distinctness"
                for f in data["verification"]["findings"])
        )

    def test_evidence_verify_require_distinct_vendors_flag_passes_distinct(self):
        rc, out, _ = self._run_distinct_vendor_verify(flag=True, vendor_b="codex")
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_duplicate_vendors_pass_without_flag(self):
        rc, out, _ = self._run_distinct_vendor_verify(flag=False, vendor_b="claude")
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_reads_distinct_vendors_from_config_not_only_the_flag(self):
        # `evidence_require_distinct_vendors: true` is a safety-*tightening* setting an
        # operator turns on deliberately, and its only path into the run was a wire no
        # test exercised — every fixture left it at the default, so `=False` was a green
        # mutation (#633). Drive it from config with no flag.
        config = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: develop\n"
            "repo: acme/example\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "  evidence_require_distinct_vendors: true\n"
        )
        rc, out, _ = self._run_distinct_vendor_verify(
            flag=False, vendor_b="claude", config=config)
        data = json.loads(out)

        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertTrue(
            any(f["id"] == "review-vendor-distinctness"
                for f in data["verification"]["findings"])
        )

    def test_evidence_verify_reads_the_gate_label_from_config_not_only_the_flag(self):
        # Same shape for `evidence_gate_label`: the tests proved the *default* reached
        # `evidence`, never that an operator's override did. A PR carrying only the
        # configured label must arm the gate, and one carrying only the built-in default
        # must not.
        config = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: develop\n"
            "repo: acme/example\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "  evidence_gate_label: 'acme:reviewed'\n"
        )

        def _gate(label):
            with tempfile.TemporaryDirectory() as d:
                empty = Path(d) / "empty.json"
                _write_json_fixture(empty, [])
                rc, out, _ = run([
                    "evidence-verify", config, "--root", str(REPO_ROOT), "--pr", "300",
                    "--pr-label", label, "--reviewers", "1",
                    "--pr-comments-json", str(empty),
                    "--issue-comments-json", str(empty),
                    "--pr-reviews-json", str(empty),
                    "--json",
                ])
            return json.loads(out)["gate"]["enforced"]

        self.assertTrue(_gate("acme:reviewed"))
        self.assertFalse(_gate("keel:ship"))

    def test_evidence_verify_enforces_closure_fidelity_against_ledger(self):
        from keel import closure, ledger

        record = {
            "schema_version": "keel.run-ledger.v1",
            "record_type": "ship_run",
            "target": "issue #212",
            "actors": {"implementer": "codex", "reviewers": [], "tester": "codex"},
            "pull_request": {"number": 300},
            "changes": {"file_count": 1, "files": ["src/keel/evidence.py"]},
            "capture": {"status": "applied"},
            "run_id": "RUN-212",
            "run_context": {
                "host_agent": "codex", "transport": "gh", "profile": "standard",
                "jury_mode": "off",
                "consent": {"status": "approved", "scopes": ["git"]},
            },
        }
        canonical = closure.render_closure_comment(record)
        tampered = canonical.replace("codex", "intruder")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            ledger_jsonl = root / "ledger.jsonl"
            ledger_jsonl.write_text(ledger.encode_record(record), encoding="utf-8")
            _write_json_fixture(pr_comments, [_trusted_comment(tampered)])
            _write_json_fixture(issue_comments, [_trusted_comment(canonical)])
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #212", encoding="utf-8")
            args = [
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:codex",
                "--reviewers", "1",
                "--deferral", "review",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--ledger-jsonl", str(ledger_jsonl),
                "--json",
            ]
            rc_fail, out_fail, _ = run(args)
            # The matching canonical body on the PR passes (fidelity satisfied).
            _write_json_fixture(pr_comments, [_trusted_comment(canonical)])
            rc_ok, out_ok, _ = run(args)

        data_fail = json.loads(out_fail)
        self.assertEqual(rc_fail, 1)
        self.assertEqual(data_fail["verification"]["status"], "fail")
        self.assertIn("closure-comment-pr", data_fail["verification"]["missing"])
        pr_result = next(
            item for item in data_fail["verification"]["results"]
            if item["id"] == "closure-comment-pr"
        )
        self.assertEqual(
            pr_result["reason"],
            "closure comment does not match the ship_run ledger record",
        )
        data_ok = json.loads(out_ok)
        self.assertEqual(rc_ok, 0)
        self.assertEqual(data_ok["verification"]["status"], "pass")

    def test_evidence_verify_invalid_ledger_jsonl_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bad = root / "ledger.jsonl"
            bad.write_text("{not json", encoding="utf-8")
            rc, _, err = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--reviewers", "1",
                "--pr-comments-json", _write_raw("[]"),
                "--issue-comments-json", _write_raw("[]"),
                "--pr-reviews-json", _write_raw("[]"),
                "--pr-body-file", _write_raw("Closes #212"),
                "--ledger-jsonl", str(bad),
                "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("invalid run ledger", err)

    def test_evidence_verify_rejects_body_and_assessment_as_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            pr_comments.write_text(json.dumps([
                {"body": "### \U0001f6a2 keel ship\nReviewer verdict LGTM"},
            ]), encoding="utf-8")
            issue_comments.write_text("[]", encoding="utf-8")
            reviews.write_text("[]", encoding="utf-8")
            body.write_text(
                "Closes #212\n<!-- keel.closure-comment.v1 -->\n"
                "keel.review-verdict.v1\nLGTM",
                encoding="utf-8",
            )
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--reviewers", "1",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertEqual(data["verification"]["counts"]["review_verdict"], 0)
        self.assertEqual(data["verification"]["missing"], [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
        ])

    def test_evidence_verify_ship_assessment_arms_gate_without_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                {
                    "author_association": "NONE",
                    "user": {"login": "github-actions[bot]"},
                    "body": "### \U0001f6a2 keel ship\nstatus: pass",
                },
            ])
            issue_comments.write_text("[]", encoding="utf-8")
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #322", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "324",
                "--reviewers", "2",
                "--head-ref", "issue-322-reveal",
                "--changed-file", "website/index.html",
                "--changed-file", "website/site.webmanifest",
                "--changed-file", "website/workspace.css",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["gate"]["reason"], "ship-assessment-comment")
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertEqual(data["verification"]["missing"], [
            "closure-comment-pr",
            "closure-comment-issue",
            "review-verdict-1",
            "review-verdict-2",
        ])

    def test_evidence_verify_human_output_and_dry_run(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--pr-label", "keel:ship",
            "--dry-run",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("keel evidence-verify", out)
        self.assertIn("required      : 0", out)

    def test_evidence_verify_waiting_human_output_has_wait_markers(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--pr-label", "keel:ship",
            "--pr-label", "agent:codex",
            "--reviewers", "2",
            "--phase", "pre-merge",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 2)
        self.assertIn("keel evidence-verify — waiting  PR #300", out)
        self.assertIn("WAIT  review-verdict-1", out)
        self.assertIn("WAIT  review-verdict-2", out)

    def test_evidence_verify_fail_human_output_has_fail_markers(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nLGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",  # Missing agent attribution label
                "--reviewers", "1",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
            ])

        self.assertEqual(rc, 1)
        self.assertIn("keel evidence-verify — fail  PR #300", out)
        self.assertIn("FAIL", out)

    def test_evidence_verify_enforces_from_ship_branch_without_label(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nLGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--reviewers", "1",
                "--pr-label", "agent:claude",
                "--head-ref", "fix/issue-266-evidence-arming",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["gate"]["reason"], "ship-branch")
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_waiver_label_is_the_disarm_path(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "fix/issue-266-evidence-arming",
            "--pr-label", "keel:evidence-waived",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
            "--json",
        ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertFalse(data["enforced"])
        self.assertTrue(data["gate"]["waived"])
        self.assertEqual(data["gate"]["reason"], "operator-waiver-label")
        self.assertEqual(data["verification"]["required_count"], 0)

    def test_evidence_verify_waiver_human_output_reports_note(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "fix/issue-266-evidence-arming",
            "--pr-label", "keel:evidence-waived",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("enforced      : false (operator-waiver-label)", out)
        self.assertIn("required      : 0", out)
        self.assertIn("note          : evidence gate disarmed by operator waiver label", out)

    def test_evidence_verify_hand_authored_pr_without_provenance_is_ungated(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "docs/readme-polish",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("enforced      : false (no-ship-provenance)", out)
        self.assertIn("required      : 0", out)
        self.assertIn("note          : evidence gate not enforced", out)

    def test_evidence_verify_require_armed_fails_an_unarmed_gate(self):
        # Without this the check exits 0 having verified nothing, so a green
        # required check cannot be told apart from one that never evaluated.
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "docs/readme-polish",
            "--require-armed",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 1)
        self.assertIn("enforced      : false (no-ship-provenance)", out)
        self.assertIn("FAIL  gate-unarmed", out)

    def test_evidence_verify_require_armed_still_honours_operator_waiver(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "fix/issue-266-evidence-arming",
            "--pr-label", "keel:evidence-waived",
            "--require-armed",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("enforced      : false (operator-waiver-label)", out)
        self.assertNotIn("gate-unarmed", out)

    def test_evidence_verify_pre_merge_phase_omits_closure_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr_comments = root / "pr.json"
            body = root / "body.md"
            # No closure comments anywhere: they are an s11 artifact, and this is
            # the gate that authorizes the s10 merge that precedes s11.
            _write_json_fixture(pr_comments, [
                _trusted_comment("keel.review-verdict.v1\nReviewer A LGTM"),
            ])
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:codex",
                "--reviewers", "1",
                "--no-jury",
                "--phase", "pre-merge",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", _write_raw("[]"),
                "--pr-reviews-json", _write_raw("[]"),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["phase"], "pre-merge")
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(
            [item["id"] for item in data["verification"]["results"]],
            ["review-verdict-1"],
        )

    def test_evidence_verify_phase_appears_in_human_output(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--head-ref", "docs/readme-polish",
            "--phase", "post-merge",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("phase         : post-merge", out)

    def test_evidence_verify_fixture_keeps_explicit_issue(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--issue", "99",
            "--dry-run",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
            "--pr-body-file", _write_raw("Closes #212"),
            "--json",
        ])

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["issue"], 99)

    def test_evidence_verify_fixture_uses_explicit_issue_without_body_infer(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nLGTM"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: b\nLGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--issue", "99",
                "--reviewers", "2",
                "--pr-label", "agent:claude",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["issue"], 99)
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_human_output_lists_missing_and_deferrals(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            issue_comments.write_text("[]", encoding="utf-8")
            reviews.write_text("[]", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--reviewers", "1",
                "--deferral", "review",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
            ])

        self.assertEqual(rc, 1)
        self.assertIn("missing       : closure-comment-issue", out)
        self.assertIn("review-verdict-1 (deferred)", out)

    def test_evidence_verify_reports_config_and_artifact_errors(self):
        rc_missing, _, err_missing = run([
            "evidence-verify", "/no/such.yaml", "--pr", "1", "--json",
        ])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run([
            "evidence-verify", _write_raw("extends: keel\n"), "--pr", "1", "--json",
        ])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            bad_json = Path(d) / "bad.json"
            bad_json.write_text("{}", encoding="utf-8")
            rc_bad, _, err_bad = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--pr", "1",
                "--pr-comments-json", str(bad_json),
            ])
        self.assertEqual(rc_bad, 1)
        self.assertIn("must contain a JSON array of objects", err_bad)

        rc_repo, _, err_repo = run([
            "evidence-verify", _write_config("'true'"), "--pr", "1",
        ])
        self.assertEqual(rc_repo, 1)
        self.assertIn("project config must define owner and repo", err_repo)

    def test_evidence_verify_live_fetch_uses_gh_artifacts(self):
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc(json.dumps({
                    "body": "Closes #212",
                    "head": {"sha": "abc123", "ref": "fix/issue-266-evidence-arming"},
                    "labels": [{"name": "keel:ship"}, {"name": "agent:claude"}],
                }))
            if endpoint.endswith("/pulls/300/files"):
                return _proc(json.dumps([
                    [{"filename": "src/keel/evidence.py"}],
                ]))
            if endpoint.endswith("/issues/300/comments"):
                return _proc(json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                    _trusted_comment("keel.review-verdict.v1\nreviewer: a\nhead: abc123\nLGTM"),
                ]))
            if endpoint.endswith("/pulls/300/reviews"):
                return _proc(json.dumps([
                    {
                        "body": "keel.review-verdict.v1\nreviewer: b\nLGTM",
                        "commit_id": "abc123",
                        "author_association": "MEMBER",
                    },
                ]))
            if endpoint.endswith("/issues/212/comments"):
                return _proc(json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                ]))
            return _proc("unexpected endpoint", ok=False)

        with patch("keel.cli.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--reviewers", "2",
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertTrue(data["enforced"])
        self.assertEqual(data["pr_labels"], ["keel:ship", "agent:claude"])
        self.assertEqual(data["head_sha"], "abc123")
        self.assertEqual(data["changed_files"], ["src/keel/evidence.py"])
        self.assertTrue(any(argv[:3] == ["gh", "api", "--paginate"] for argv in calls))

    def test_evidence_verify_live_fetch_derives_tier3_requirements(self):
        def fake_run(argv, **_kw):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc(json.dumps({
                    "body": "Closes #212",
                    "head": {"sha": "abc123"},
                    "labels": [{"name": "keel:ship"}],
                }))
            if endpoint.endswith("/pulls/300/files"):
                return _proc(json.dumps([
                    [{"filename": ".github/workflows/keel-ship.yml"}],
                ]))
            if endpoint.endswith("/issues/300/comments"):
                return _proc(json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                    _trusted_comment("keel.review-verdict.v1\nreviewer: a\nhead: abc123\nLGTM"),
                    _trusted_comment("keel.review-verdict.v1\nreviewer: b\nhead: abc123\nLGTM"),
                ]))
            if endpoint.endswith("/pulls/300/reviews"):
                return _proc("[]")
            if endpoint.endswith("/issues/212/comments"):
                return _proc(json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                ]))
            return _proc("unexpected endpoint", ok=False)

        with patch("keel.cli.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["changed_files"], [".github/workflows/keel-ship.yml"])
        self.assertEqual(data["verification"]["missing"], [
            "review-verdict-3",
            "jury-verdict",
        ])

    def test_evidence_verify_offline_changed_files_and_head_sha(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            body = root / "body.md"
            _write_json_fixture(pr_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: a\nhead: abc123\nLGTM"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: b\nhead: abc123\nLGTM"),
                _trusted_comment("keel.review-verdict.v1\nreviewer: c\nhead: abc123\nLGTM"),
                _trusted_comment("keel.jury-verdict.v1\nhead: abc123\nAI Jury LGTM"),
            ])
            _write_json_fixture(issue_comments, [
                _trusted_comment("<!-- keel.closure-comment.v1 -->"),
            ])
            reviews.write_text("[]", encoding="utf-8")
            body.write_text("Closes #212", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--pr-label", "keel:ship",
                "--pr-label", "agent:claude",
                "--changed-file", ".github/workflows/keel-ship.yml",
                "--head-sha", "abc123",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--pr-body-file", str(body),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["required_count"], 6)
        self.assertEqual(data["verification"]["status"], "pass")

    def test_evidence_verify_live_fetch_uses_explicit_issue_without_body_infer(self):
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc(json.dumps({
                    "body": "Closes #212",
                    "labels": [{"name": "keel:ship"}, {"name": "agent:claude"}],
                }))
            if endpoint.endswith("/pulls/300/files"):
                return _proc(json.dumps([
                    [{"filename": "src/keel/evidence.py"}],
                ]))
            if endpoint.endswith("/issues/300/comments"):
                return _proc(json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                    _trusted_comment("keel.review-verdict.v1\nReviewer A LGTM"),
                    _trusted_comment("keel.review-verdict.v1\nReviewer B LGTM"),
                ]))
            if endpoint.endswith("/pulls/300/reviews"):
                return _proc("[]")
            if endpoint.endswith("/issues/99/comments"):
                return _proc(json.dumps([
                    _trusted_comment("<!-- keel.closure-comment.v1 -->"),
                ]))
            return _proc("unexpected endpoint", ok=False)

        with patch("keel.cli.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--issue", "99",
                "--reviewers", "2",
                "--json",
            ])

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["issue"], 99)
        self.assertTrue(any("/issues/99/comments" in argv[-1] for argv in calls))
        self.assertFalse(any("/issues/212/comments" in argv[-1] for argv in calls))

    def test_evidence_verify_reports_live_gh_errors_and_bad_shapes(self):
        def failing_run(_argv, **_kw):
            return _proc("no auth", ok=False)

        with patch("keel.cli.run_argv", side_effect=failing_run):
            rc_fail, _, err_fail = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
            ])
        self.assertEqual(rc_fail, 1)
        self.assertIn("gh api repos/berkayturanci/example-android/pulls/300 failed", err_fail)

        def bad_object_run(argv, **_kw):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc("[]")
            return _proc("[]")

        with patch("keel.cli.run_argv", side_effect=bad_object_run):
            rc_obj, _, err_obj = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
            ])
        self.assertEqual(rc_obj, 1)
        self.assertIn("did not return a JSON object", err_obj)

        def bad_list_run(argv, **_kw):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc(json.dumps({"body": ""}))
            return _proc("{}")

        with patch("keel.cli.run_argv", side_effect=bad_list_run):
            rc_list, _, err_list = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
            ])
        self.assertEqual(rc_list, 1)
        self.assertIn("did not return a JSON array", err_list)

        def failing_paginated_run(argv, **_kw):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc(json.dumps({"body": ""}))
            return _proc("page failed", ok=False)

        with patch("keel.cli.run_argv", side_effect=failing_paginated_run):
            rc_page, _, err_page = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
            ])
        self.assertEqual(rc_page, 1)
        self.assertIn("page failed", err_page)

    def test_evidence_verify_live_fetch_allows_missing_linked_issue(self):
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc(json.dumps({
                    "body": "Refs #212",
                    "labels": [{"name": "keel:ship"}],
                }))
            if endpoint.endswith("/pulls/300/files"):
                return _proc(json.dumps([
                    [{"filename": "src/keel/evidence.py"}],
                ]))
            return _proc("[]")

        with patch("keel.cli.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--json",
            ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIsNone(data["issue"])
        self.assertFalse(any("/issues/212/comments" in argv[-1] for argv in calls))

    def test_evidence_verify_without_gate_label_is_not_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pr_comments = root / "pr-comments.json"
            issue_comments = root / "issue-comments.json"
            reviews = root / "reviews.json"
            pr_comments.write_text("[]", encoding="utf-8")
            issue_comments.write_text("[]", encoding="utf-8")
            reviews.write_text("[]", encoding="utf-8")
            rc, out, _ = run([
                "evidence-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT),
                "--pr", "300",
                "--reviewers", "2",
                "--pr-comments-json", str(pr_comments),
                "--issue-comments-json", str(issue_comments),
                "--pr-reviews-json", str(reviews),
                "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertFalse(data["enforced"])
        self.assertEqual(data["gate_label"], "keel:ship")
        self.assertEqual(data["pr_labels"], [])
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["required_count"], 0)

    def test_evidence_verify_without_provenance_human_output_reports_reason(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--reviewers", "1",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("enforced      : false", out)
        self.assertIn("no-ship-provenance", out)

    def test_evidence_verify_with_label_is_fail_closed(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--pr-label", "keel:ship",
            "--reviewers", "1",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
            "--json",
        ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertTrue(data["enforced"])
        self.assertEqual(data["verification"]["status"], "fail")
        # Fail for the right reason: the gate is enforced and reports the
        # missing evidence, not an unrelated error.
        self.assertGreaterEqual(data["verification"]["required_count"], 1)
        self.assertTrue(data["verification"]["missing"])

    def test_evidence_verify_gate_label_override(self):
        rc, out, _ = run([
            "evidence-verify", str(PROJECTS / "example-android.yaml"),
            "--root", str(REPO_ROOT),
            "--pr", "300",
            "--pr-label", "ship-me",
            "--gate-label", "ship-me",
            "--reviewers", "1",
            "--pr-comments-json", _write_raw("[]"),
            "--issue-comments-json", _write_raw("[]"),
            "--pr-reviews-json", _write_raw("[]"),
            "--json",
        ])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["gate_label"], "ship-me")
        self.assertTrue(data["enforced"])
        self.assertEqual(data["pr_labels"], ["ship-me"])

    def test_label_names_handles_absent_and_malformed_labels(self):
        # A live PR payload may omit `labels`, send null, or carry malformed
        # entries; _label_names must degrade to a clean list of name strings.
        self.assertEqual(cli._label_names(None), [])
        self.assertEqual(cli._label_names("nope"), [])
        self.assertEqual(
            cli._label_names([{"name": "keel:ship"}, {"no_name": 1}, "x", {"name": 7}]),
            ["keel:ship"],
        )


def _scope_ledger(declared_files, *, pr=300):
    record = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "pull_request": {"number": pr},
        "changes": {"file_count": 1, "files": ["a.py"]},
        "declared": {"file_count": len(declared_files), "files": list(declared_files)},
    }
    return ledger.encode_record(record)


class TestScopeVerify(unittest.TestCase):
    def _run(self, *, ledger_text, changed, deferral=None):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(ledger_text, encoding="utf-8")
            argv = [
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl), "--json",
            ]
            for path in changed:
                argv += ["--changed-file", path]
            if deferral is not None:
                argv += ["--deferral", deferral]
            return run(argv)

    def test_in_scope_diff_passes(self):
        rc, out, _ = self._run(
            ledger_text=_scope_ledger(["a.py"]), changed=["a.py"]
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["scope_creep"], [])

    def test_scope_creep_fails_and_lists_unexpected_file(self):
        rc, out, _ = self._run(
            ledger_text=_scope_ledger(["a.py"]), changed=["a.py", "unrelated.py"]
        )
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["status"], "fail")
        self.assertEqual(data["verification"]["scope_creep"], ["unrelated.py"])

    def test_docs_paths_are_exempt(self):
        rc, out, _ = self._run(
            ledger_text=_scope_ledger(["a.py"]),
            changed=["a.py", "docs/keel/cli.md"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["docs_exempt"], ["docs/keel/cli.md"])

    def test_no_declared_record_is_advisory_pass(self):
        rc, out, _ = self._run(ledger_text="", changed=["a.py", "x.py"])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(data["verification"]["advisory"])
        self.assertEqual(data["verification"]["note"], "no declared scope recorded")

    def test_scope_waived_deferral_is_honored(self):
        rc, out, _ = self._run(
            ledger_text=_scope_ledger(["a.py"]),
            changed=["a.py", "unrelated.py"],
            deferral="scope-waived",
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertTrue(data["verification"]["waived"])

    def test_human_output_lists_creep_and_docs(self):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_scope_ledger(["a.py"]), encoding="utf-8")
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl),
                "--changed-file", "a.py", "--changed-file", "docs/x.md",
                "--changed-file", "unrelated.py",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("keel scope-verify — fail", out)
        self.assertIn("scope-creep   : unrelated.py", out)
        self.assertIn("docs-exempt   : docs/x.md", out)

    def test_human_output_advisory_note(self):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text("", encoding="utf-8")
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl), "--changed-file", "a.py",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("note          : no declared scope recorded", out)

    def test_human_output_waived_note(self):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_scope_ledger(["a.py"]), encoding="utf-8")
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl),
                "--changed-file", "a.py", "--changed-file", "unrelated.py",
                "--deferral", "scope-waived",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("note          : scope creep waived by operator deferral", out)

    def test_human_output_clean_in_scope_pass(self):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_scope_ledger(["a.py"]), encoding="utf-8")
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(ledger_jsonl), "--changed-file", "a.py",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("keel scope-verify — pass", out)
        self.assertIn("in-scope      : 1 file(s)", out)
        self.assertNotIn("scope-creep", out)

    def test_reads_configured_ledger_under_root_when_no_fixture(self):
        # With no --ledger-jsonl, scope-verify reads the configured run ledger
        # under --root; a fresh root has no ledger → advisory pass.
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", d, "--pr", "300", "--dry-run",
                "--changed-file", "a.py", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(data["verification"]["advisory"])

    def test_invalid_ledger_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "ledger.jsonl"
            bad.write_text("{not json", encoding="utf-8")
            rc, _, err = run([
                "scope-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--dry-run",
                "--ledger-jsonl", str(bad), "--changed-file", "a.py", "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("invalid run ledger", err)

    def test_missing_config_reports_error(self):
        rc, _, err = run([
            "scope-verify", "no-such.yaml", "--pr", "300", "--dry-run",
            "--changed-file", "a.py",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_reports_error(self):
        bad = _write_raw("extends: keel\nbase_branch: main\ngates: [build]\n")
        rc, _, err = run([
            "scope-verify", bad, "--pr", "300", "--dry-run", "--changed-file", "a.py",
        ])
        self.assertEqual(rc, 1)

    def test_artifact_value_error_reports(self):
        # A live (non-dry-run, no-fixture) fetch on a config missing owner/repo
        # raises ValueError from _owner_repo and is surfaced cleanly.
        bad = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: \"true\"\n"
        )
        rc, _, err = run([
            "scope-verify", bad, "--root", str(REPO_ROOT), "--pr", "300", "--json",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("owner and repo", err)


def _consent_ledger(*, status="approved", scopes, pr=300):
    record = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "pull_request": {"number": pr},
        "run_context": {"consent": {"status": status, "scopes": list(scopes)}},
    }
    return ledger.encode_record(record)


class TestConsentVerify(unittest.TestCase):
    def _run_offline(self, *, ledger_text, effects, json_out=True):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(ledger_text, encoding="utf-8")
            argv = [
                "consent-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--offline",
                "--ledger-jsonl", str(ledger_jsonl),
            ]
            argv += effects
            if json_out:
                argv += ["--json"]
            return run(argv)

    def test_merged_pr_with_only_git_scope_fails_naming_merge(self):
        rc, out, _ = self._run_offline(
            ledger_text=_consent_ledger(scopes=["git"]),
            effects=["--pr-exists", "--merged"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["reconcile"]["verdict"], "fail")
        effects = {finding["effect"] for finding in data["reconcile"]["uncovered"]}
        self.assertIn("merged", effects)
        merged = next(f for f in data["reconcile"]["uncovered"] if f["effect"] == "merged")
        self.assertIn(
            "mutation merged not covered by approved consent scopes", merged["message"]
        )

    def test_merged_pr_with_git_and_github_passes(self):
        rc, out, _ = self._run_offline(
            ledger_text=_consent_ledger(scopes=["git", "github"]),
            effects=["--pr-exists", "--merged", "--commented", "--labeled"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "pass")
        self.assertEqual(data["reconcile"]["uncovered"], [])

    def test_no_consent_record_is_advisory(self):
        rc, out, _ = self._run_offline(
            ledger_text="", effects=["--pr-exists", "--merged"]
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "advisory")
        self.assertFalse(data["reconcile"]["has_consent_record"])

    def test_json_payload_includes_scope_effect_table(self):
        rc, out, _ = self._run_offline(
            ledger_text=_consent_ledger(scopes=["git", "github"]),
            effects=["--pr-exists"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["scope_effect_table"]["pr_exists"], ["git", "github"])
        self.assertEqual(data["scope_effect_table"]["merged"], ["github"])

    def test_human_output_fail_lists_uncovered(self):
        rc, out, _ = self._run_offline(
            ledger_text=_consent_ledger(scopes=["git"]),
            effects=["--pr-exists", "--merged"],
            json_out=False,
        )
        self.assertEqual(rc, 1)
        self.assertIn("keel consent-verify — fail", out)
        self.assertIn("consent record : present", out)
        self.assertIn("mutation merged not covered", out)

    def test_human_output_pass(self):
        rc, out, _ = self._run_offline(
            ledger_text=_consent_ledger(scopes=["git", "github"]),
            effects=["--pr-exists", "--merged"],
            json_out=False,
        )
        self.assertEqual(rc, 0)
        self.assertIn("keel consent-verify — pass", out)
        self.assertIn("all observed mutations covered", out)

    def test_human_output_advisory(self):
        rc, out, _ = self._run_offline(
            ledger_text="", effects=["--pr-exists"], json_out=False
        )
        self.assertEqual(rc, 0)
        self.assertIn("keel consent-verify — advisory", out)
        self.assertIn("consent record : absent (advisory)", out)

    def test_reads_configured_ledger_under_root_when_no_fixture(self):
        # No --ledger-jsonl: reads the configured ledger under --root; a fresh
        # root has no ledger → advisory.
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run([
                "consent-verify", str(PROJECTS / "example-android.yaml"),
                "--root", d, "--pr", "300", "--offline", "--pr-exists",
                "--merged", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "advisory")

    def test_live_transport_observation_fails_on_uncovered_merge(self):
        def fake_run(argv, **kwargs):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc(json.dumps(
                    {"merged": True, "labels": [{"name": "keel:ship"}]}
                ))
            if endpoint.endswith("/issues/300/comments"):
                return _proc(json.dumps([[{"body": "hi"}]]))
            return _proc("unexpected endpoint", ok=False)

        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_consent_ledger(scopes=["git"]), encoding="utf-8")
            with patch("keel.cli.run_argv", side_effect=fake_run):
                rc, out, _ = run([
                    "consent-verify", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--pr", "300",
                    "--ledger-jsonl", str(ledger_jsonl), "--json",
                ])
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["reconcile"]["verdict"], "fail")
        self.assertEqual(
            set(data["reconcile"]["observed_effects"]),
            {"pr_exists", "comment", "merged", "label"},
        )

    def test_live_transport_observation_passes_with_full_scopes(self):
        def fake_run(argv, **kwargs):
            endpoint = argv[-1]
            if endpoint.endswith("/pulls/300"):
                return _proc(json.dumps({"merged": False, "labels": []}))
            if endpoint.endswith("/issues/300/comments"):
                return _proc(json.dumps([[]]))
            return _proc("unexpected endpoint", ok=False)

        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(
                _consent_ledger(scopes=["git", "github"]), encoding="utf-8"
            )
            with patch("keel.cli.run_argv", side_effect=fake_run):
                rc, out, _ = run([
                    "consent-verify", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--pr", "300",
                    "--ledger-jsonl", str(ledger_jsonl), "--json",
                ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "pass")
        self.assertEqual(data["reconcile"]["observed_effects"], ["pr_exists"])

    def test_live_transport_failure_surfaces_error(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("keel.cli.run_argv",
                       return_value=_proc("gh offline", ok=False)):
                rc, _, err = run([
                    "consent-verify", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--pr", "300", "--json",
                ])
        self.assertEqual(rc, 1)
        self.assertIn("gh api", err)

    def test_invalid_ledger_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "ledger.jsonl"
            bad.write_text("{not json", encoding="utf-8")
            rc, _, err = run([
                "consent-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--pr", "300", "--offline",
                "--ledger-jsonl", str(bad), "--pr-exists", "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("invalid run ledger", err)

    def test_missing_config_reports_error(self):
        rc, _, err = run([
            "consent-verify", "no-such.yaml", "--pr", "300", "--offline", "--pr-exists",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_reports_error(self):
        bad = _write_raw("extends: keel\nbase_branch: main\ngates: [build]\n")
        rc, _, err = run([
            "consent-verify", bad, "--pr", "300", "--offline", "--pr-exists",
        ])
        self.assertEqual(rc, 1)

    def test_live_observation_missing_owner_repo_reports(self):
        bad = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: \"true\"\n"
        )
        rc, _, err = run([
            "consent-verify", bad, "--root", str(REPO_ROOT), "--pr", "300", "--json",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("owner and repo", err)


def _close_ledger(*, action="merge", issue=8):
    record = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "issue": {"number": issue},
        "pull_request": {"number": 300},
        "assessment": {"merge": {"action": action, "reason": "r"}},
    }
    return ledger.encode_record(record)


class TestCloseReconcile(unittest.TestCase):
    def _run_offline(self, *, ledger_text, flags, issues=("8",), json_out=True):
        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(ledger_text, encoding="utf-8")
            argv = [
                "close-reconcile", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--offline",
                "--ledger-jsonl", str(ledger_jsonl),
            ]
            for number in issues:
                argv += ["--issue", number]
            argv += flags
            if json_out:
                argv += ["--json"]
            return run(argv)

    def test_premature_close_is_flagged(self):
        rc, out, _ = self._run_offline(
            ledger_text=_close_ledger(action="defer"), flags=["--closed"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["reconcile"]["verdict"], "flagged")
        self.assertEqual(
            data["reconcile"]["findings"][0]["finding"], "premature-close"
        )

    def test_premature_status_done_is_flagged(self):
        rc, out, _ = self._run_offline(
            ledger_text="", flags=["--status-done"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(
            data["reconcile"]["findings"][0]["finding"], "premature-status-done"
        )

    def test_consistent_closed_with_merge_record(self):
        rc, out, _ = self._run_offline(
            ledger_text=_close_ledger(action="merge"),
            flags=["--closed", "--status-done"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "ok")
        self.assertEqual(data["reconcile"]["findings"], [])

    def test_open_not_done_is_ok(self):
        rc, out, _ = self._run_offline(ledger_text="", flags=[])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "ok")

    def test_json_payload_includes_done_label(self):
        rc, out, _ = self._run_offline(
            ledger_text=_close_ledger(action="merge"), flags=["--closed"],
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["done_label"], "status:done")

    def test_human_output_flag_lists_findings(self):
        rc, out, _ = self._run_offline(
            ledger_text=_close_ledger(action="defer"),
            flags=["--closed"], json_out=False,
        )
        self.assertEqual(rc, 1)
        self.assertIn("keel close-reconcile — flagged", out)
        self.assertIn("FLAG", out)
        self.assertIn("closed but no ship_run", out)

    def test_human_output_ok(self):
        rc, out, _ = self._run_offline(
            ledger_text=_close_ledger(action="merge"),
            flags=["--closed"], json_out=False,
        )
        self.assertEqual(rc, 0)
        self.assertIn("keel close-reconcile — ok", out)
        self.assertIn("all observed issues consistent", out)

    def test_reads_configured_ledger_under_root_when_no_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run([
                "close-reconcile", str(PROJECTS / "example-android.yaml"),
                "--root", d, "--offline", "--issue", "8", "--closed", "--json",
            ])
        data = json.loads(out)
        # Fresh root has no ledger → no record → premature-close flagged.
        self.assertEqual(rc, 1)
        self.assertEqual(data["reconcile"]["verdict"], "flagged")

    def test_live_observation_flags_premature_close(self):
        def fake_run(argv, **kwargs):
            endpoint = argv[-1]
            if endpoint.endswith("/issues/8"):
                return _proc(json.dumps(
                    {"state": "closed", "labels": [{"name": "status:done"}]}
                ))
            return _proc("unexpected endpoint", ok=False)

        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_close_ledger(action="defer"), encoding="utf-8")
            with patch("keel.cli.run_argv", side_effect=fake_run):
                rc, out, _ = run([
                    "close-reconcile", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--issue", "8",
                    "--ledger-jsonl", str(ledger_jsonl), "--json",
                ])
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["reconcile"]["verdict"], "flagged")
        self.assertTrue(data["reconcile"]["issues"][0]["closed"])
        self.assertTrue(data["reconcile"]["issues"][0]["status_done"])

    def test_live_observation_passes_with_merge_record(self):
        def fake_run(argv, **kwargs):
            endpoint = argv[-1]
            if endpoint.endswith("/issues/8"):
                return _proc(json.dumps(
                    {"state": "open", "labels": []}
                ))
            return _proc("unexpected endpoint", ok=False)

        with tempfile.TemporaryDirectory() as d:
            ledger_jsonl = Path(d) / "ledger.jsonl"
            ledger_jsonl.write_text(_close_ledger(action="merge"), encoding="utf-8")
            with patch("keel.cli.run_argv", side_effect=fake_run):
                rc, out, _ = run([
                    "close-reconcile", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--issue", "8",
                    "--ledger-jsonl", str(ledger_jsonl), "--json",
                ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "ok")

    def test_live_transport_failure_surfaces_error(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("keel.cli.run_argv",
                       return_value=_proc("gh offline", ok=False)):
                rc, _, err = run([
                    "close-reconcile", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--issue", "8", "--json",
                ])
        self.assertEqual(rc, 1)
        self.assertIn("gh api", err)

    def test_invalid_ledger_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "ledger.jsonl"
            bad.write_text("{not json", encoding="utf-8")
            rc, _, err = run([
                "close-reconcile", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--offline",
                "--ledger-jsonl", str(bad), "--issue", "8", "--closed", "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("invalid run ledger", err)

    def test_missing_config_reports_error(self):
        rc, _, err = run([
            "close-reconcile", "no-such.yaml", "--offline", "--issue", "8",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_reports_error(self):
        bad = _write_raw("extends: keel\nbase_branch: main\ngates: [build]\n")
        rc, _, _ = run([
            "close-reconcile", bad, "--offline", "--issue", "8",
        ])
        self.assertEqual(rc, 1)

    def test_live_observation_missing_owner_repo_reports(self):
        bad = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: \"true\"\n"
        )
        rc, _, err = run([
            "close-reconcile", bad, "--root", str(REPO_ROOT), "--issue", "8", "--json",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("owner and repo", err)

    def test_default_done_label_when_config_lacks_transition(self):
        # A config whose policy_pack has no status_transitions.done falls back to
        # the module default so the reconcile still has a label to check.
        raw = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "owner: o\nrepo: r\ngates: [build]\nknobs:\n  build_gate_cmd: \"true\"\n"
        )
        rc, out, _ = run([
            "close-reconcile", raw, "--root", str(REPO_ROOT), "--offline",
            "--issue", "8", "--status-done", "--json",
        ])
        data = json.loads(out)
        self.assertEqual(data["done_label"], "status:done")
        # status:done label present, no record → flagged.
        self.assertEqual(rc, 1)


class TestDryrunVerify(unittest.TestCase):
    def _run_offline(self, *, before, after, run_id="dry-8", issue="8", json_out=True):
        with tempfile.TemporaryDirectory() as d:
            before_path = Path(d) / "before.json"
            after_path = Path(d) / "after.json"
            before_path.write_text(json.dumps(before), encoding="utf-8")
            after_path.write_text(json.dumps(after), encoding="utf-8")
            argv = [
                "dryrun-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--run-id", run_id, "--issue", issue,
                "--before-json", str(before_path), "--after-json", str(after_path),
            ]
            if json_out:
                argv += ["--json"]
            return run(argv)

    def test_clean_dry_run_passes(self):
        rc, out, _ = self._run_offline(
            before={"ledger_run_ids": ["r1"], "branches": ["main"], "pr_numbers": [1]},
            after={"ledger_run_ids": ["r1"], "branches": ["main"], "pr_numbers": [1]},
        )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "clean")

    def test_violated_dry_run_is_flagged(self):
        rc, out, _ = self._run_offline(
            before={"ledger_run_ids": ["r1"], "branches": ["main"], "pr_numbers": []},
            after={
                "ledger_run_ids": ["r1", "dry-8"],
                "branches": ["main", "feature/issue-8"],
                "pr_numbers": [42],
            },
        )
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["reconcile"]["verdict"], "violated")
        kinds = sorted(f["finding"] for f in data["reconcile"]["findings"])
        self.assertEqual(kinds, ["new-branch", "new-ledger-record", "new-pr"])

    def test_human_output_clean(self):
        rc, out, _ = self._run_offline(
            before={"branches": ["main"]}, after={"branches": ["main"]}, json_out=False,
        )
        self.assertEqual(rc, 0)
        self.assertIn("keel dryrun-verify — clean", out)
        self.assertIn("left no new ledger record", out)

    def test_human_output_violated_lists_leaks(self):
        rc, out, _ = self._run_offline(
            before={"branches": ["main"]},
            after={"branches": ["main", "feature/issue-8"]},
            json_out=False,
        )
        self.assertEqual(rc, 1)
        self.assertIn("keel dryrun-verify — violated", out)
        self.assertIn("LEAK", out)

    def test_invalid_before_snapshot_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            before = Path(d) / "before.json"
            before.write_text("[1,2,3]", encoding="utf-8")  # not an object
            after = Path(d) / "after.json"
            after.write_text("{}", encoding="utf-8")
            rc, _, err = run([
                "dryrun-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--run-id", "dry-8", "--issue", "8",
                "--before-json", str(before), "--after-json", str(after), "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("invalid before snapshot", err)

    def test_invalid_after_snapshot_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            before = Path(d) / "before.json"
            before.write_text("{}", encoding="utf-8")
            after = Path(d) / "after.json"
            after.write_text("{not json", encoding="utf-8")
            rc, _, err = run([
                "dryrun-verify", str(PROJECTS / "example-android.yaml"),
                "--root", str(REPO_ROOT), "--run-id", "dry-8", "--issue", "8",
                "--before-json", str(before), "--after-json", str(after), "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("invalid after snapshot", err)

    def test_missing_config_reports_error(self):
        with tempfile.TemporaryDirectory() as d:
            before = Path(d) / "before.json"
            before.write_text("{}", encoding="utf-8")
            rc, _, err = run([
                "dryrun-verify", "no-such.yaml", "--run-id", "dry-8", "--issue", "8",
                "--before-json", str(before),
            ])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_reports_error(self):
        bad = _write_raw("extends: keel\nbase_branch: main\ngates: [build]\n")
        with tempfile.TemporaryDirectory() as d:
            before = Path(d) / "before.json"
            before.write_text("{}", encoding="utf-8")
            rc, _, _ = run([
                "dryrun-verify", bad, "--run-id", "dry-8", "--issue", "8",
                "--before-json", str(before),
            ])
        self.assertEqual(rc, 1)

    def test_live_after_snapshot_flags_leaked_branch(self):
        # Live: ledger read from a fresh root (empty), branches + PRs via gh/git.
        def fake_run(argv, **kwargs):
            if argv[:2] == ["git", "for-each-ref"]:
                return _proc("main\nfeature/issue-8\n")
            if argv[:3] == ["gh", "pr", "list"]:
                return _proc(json.dumps(
                    [{"number": 42, "headRefName": "feature/issue-8"},
                     {"number": 7, "headRefName": "feature/issue-9"}]
                ))
            return _proc("unexpected", ok=False)

        with tempfile.TemporaryDirectory() as d:
            before = Path(d) / "before.json"
            before.write_text(
                json.dumps({"branches": ["main"], "pr_numbers": []}), encoding="utf-8"
            )
            with patch("keel.cli.run_argv", side_effect=fake_run), \
                    patch("keel.git.run_argv", side_effect=fake_run), \
                    patch("keel.github.run_argv", side_effect=fake_run):
                rc, out, _ = run([
                    "dryrun-verify", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--run-id", "dry-8", "--issue", "8",
                    "--before-json", str(before), "--json",
                ])
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["reconcile"]["verdict"], "violated")
        # PR #42 (issue-8 head) flagged; PR #7 (issue-9 head) scoped out.
        prs = [f["artifact"] for f in data["reconcile"]["findings"] if f["finding"] == "new-pr"]
        self.assertEqual(prs, [42])

    def test_live_pr_transport_failure_fails_closed(self):
        # A gh failure on the AFTER snapshot must NOT report clean — an
        # unobservable PR set could hide a leaked PR. Fail closed: rc=1.
        def fake_run(argv, **kwargs):
            if argv[:2] == ["git", "for-each-ref"]:
                return _proc("main\n")
            if argv[:3] == ["gh", "pr", "list"]:
                return _proc("gh offline", ok=False)
            return _proc("unexpected", ok=False)

        with tempfile.TemporaryDirectory() as d:
            before = Path(d) / "before.json"
            before.write_text(
                json.dumps({"branches": ["main"], "pr_numbers": []}), encoding="utf-8"
            )
            with patch("keel.cli.run_argv", side_effect=fake_run), \
                    patch("keel.git.run_argv", side_effect=fake_run), \
                    patch("keel.github.run_argv", side_effect=fake_run):
                rc, _, err = run([
                    "dryrun-verify", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--run-id", "dry-8", "--issue", "8",
                    "--before-json", str(before), "--json",
                ])
        self.assertEqual(rc, 1)
        self.assertIn("after snapshot incomplete", err)
        self.assertIn("gh PR listing failed", err)

    def test_live_branch_transport_failure_fails_closed(self):
        # Symmetric to the gh case: a git branch-listing failure on the AFTER
        # snapshot must fail closed, not report clean.
        def fake_run(argv, **kwargs):
            if argv[:2] == ["git", "for-each-ref"]:
                return _proc("git offline", ok=False)
            return _proc("unexpected", ok=False)

        with tempfile.TemporaryDirectory() as d:
            before = Path(d) / "before.json"
            before.write_text(
                json.dumps({"branches": ["main"], "pr_numbers": []}), encoding="utf-8"
            )
            with patch("keel.cli.run_argv", side_effect=fake_run), \
                    patch("keel.git.run_argv", side_effect=fake_run), \
                    patch("keel.github.run_argv", side_effect=fake_run):
                rc, _, err = run([
                    "dryrun-verify", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--run-id", "dry-8", "--issue", "8",
                    "--before-json", str(before), "--json",
                ])
        self.assertEqual(rc, 1)
        self.assertIn("after snapshot incomplete", err)
        self.assertIn("git branch listing failed", err)

    def test_live_pr_list_skips_malformed_entries(self):
        def fake_run(argv, **kwargs):
            if argv[:2] == ["git", "for-each-ref"]:
                return _proc("main\n")
            if argv[:3] == ["gh", "pr", "list"]:
                return _proc(json.dumps(
                    ["nope", {"number": "x", "headRefName": "feature/issue-8"},
                     {"number": 42, "headRefName": "feature/issue-8"}]
                ))
            return _proc("unexpected", ok=False)

        with tempfile.TemporaryDirectory() as d:
            before = Path(d) / "before.json"
            before.write_text(
                json.dumps({"branches": ["main"], "pr_numbers": [42]}), encoding="utf-8"
            )
            with patch("keel.cli.run_argv", side_effect=fake_run), \
                    patch("keel.git.run_argv", side_effect=fake_run), \
                    patch("keel.github.run_argv", side_effect=fake_run):
                rc, out, _ = run([
                    "dryrun-verify", str(PROJECTS / "example-android.yaml"),
                    "--root", d, "--run-id", "dry-8", "--issue", "8",
                    "--before-json", str(before), "--json",
                ])
        data = json.loads(out)
        # Only the well-formed PR #42 is observed, and it pre-existed → clean.
        self.assertEqual(rc, 0)
        self.assertEqual(data["reconcile"]["verdict"], "clean")


class TestVerifyBranch(unittest.TestCase):
    BASE = [
        "verify-branch", str(PROJECTS / "example-android.yaml"),
        "--root", str(REPO_ROOT), "--pr", "300", "--offline",
    ]

    def test_clean_offline_passes(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "t", "--base-distance", "0",
            "--worktree-path", "/repo/worktrees/i", "--repo-root", "/repo",
            "--linked-worktree", "true",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["verdict"], "ok")
        self.assertEqual(data["verification"]["base_branch"], "develop")

    def test_stale_base_fails(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "old", "--base-distance", "9", "--tolerance", "5",
            "--linked-worktree", "true", "--worktree-path", "/repo/wt",
            "--repo-root", "/repo",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["verdict"], "stale")
        self.assertIn("base is stale", data["verification"]["note"])

    def test_allow_stale_base_downgrades_to_advisory_pass(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "old", "--base-distance", "9", "--tolerance", "5",
            "--allow-stale-base", "--linked-worktree", "true",
            "--worktree-path", "/repo/wt", "--repo-root", "/repo",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["verdict"], "stale")
        self.assertTrue(data["verification"]["allow_stale_base"])
        self.assertIn("advisory", data["verification"]["note"])

    def test_contaminated_primary_checkout_fails(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "t", "--base-distance", "0",
            "--worktree-path", "/repo", "--repo-root", "/repo",
            "--linked-worktree", "false",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["verification"]["verdict"], "contaminated")
        self.assertIn("primary checkout", data["verification"]["note"])

    def test_ci_no_worktree_skips_isolation(self):
        rc, out, _ = run(self.BASE + [
            "--json", "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "t", "--base-distance", "0",
        ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["isolation"]["verdict"], "n/a")

    def test_human_output_renders_summary(self):
        rc, out, _ = run(self.BASE + [
            "--head-sha", "h", "--base-tip-sha", "t",
            "--merge-base-sha", "old", "--base-distance", "9", "--tolerance", "5",
            "--worktree-path", "/repo/wt", "--repo-root", "/repo",
            "--linked-worktree", "true",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("keel verify-branch — fail", out)
        self.assertIn("base          : origin/develop", out)
        self.assertIn("verdict       : stale", out)
        self.assertIn("base-distance : 9 (tolerance 5)", out)
        self.assertIn("isolation     : ok", out)

    def test_live_path_gathers_facts_via_gh_and_git(self):
        # A non-offline run resolves the PR head via gh and the ancestry +
        # worktree facts via the git wrappers. We stub run_argv for both surfaces.
        def fake_run(argv, **kwargs):
            if argv[0] == "gh":
                body = json.dumps({"head": {"sha": SHA_HEAD, "ref": "feature/x"}})
                return _proc(body)
            if argv[:2] == ["git", "rev-parse"]:
                return _proc(SHA_TIP + "\n")
            if argv[:2] == ["git", "merge-base"]:
                return _proc(SHA_TIP + "\n")
            if argv[:2] == ["git", "rev-list"]:
                return _proc("0\n")
            if argv[:3] == ["git", "worktree", "list"]:
                porcelain = (
                    "worktree /repo\nHEAD aaa\nbranch refs/heads/develop\n\n"
                    "worktree /repo/worktrees/i\nHEAD bbb\nbranch refs/heads/feature/x\n"
                )
                return _proc(porcelain)
            raise AssertionError(f"unexpected argv {argv}")

        with patch("keel.cli.run_argv", side_effect=fake_run), \
             patch("keel.git.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "verify-branch", str(PROJECTS / "example-android.yaml"),
                "--root", "/repo", "--pr", "300", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["head_ref"], "feature/x")
        self.assertEqual(data["verification"]["verdict"], "ok")
        self.assertTrue(data["verification"]["isolation"]["is_linked_worktree"])

    def test_live_worktree_list_failure_skips_isolation(self):
        def fake_run(argv, **kwargs):
            if argv[0] == "gh":
                body = json.dumps({"head": {"sha": SHA_HEAD, "ref": "feature/x"}})
                return _proc(body)
            if argv[:2] == ["git", "rev-parse"]:
                return _proc(SHA_TIP + "\n")
            if argv[:2] == ["git", "merge-base"]:
                return _proc(SHA_TIP + "\n")
            if argv[:2] == ["git", "rev-list"]:
                return _proc("0\n")
            if argv[:3] == ["git", "worktree", "list"]:
                return _proc("", ok=False)
            raise AssertionError(f"unexpected argv {argv}")

        with patch("keel.cli.run_argv", side_effect=fake_run), \
             patch("keel.git.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "verify-branch", str(PROJECTS / "example-android.yaml"),
                "--root", "/repo", "--pr", "300", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["isolation"]["verdict"], "n/a")

    def test_live_branch_not_checked_out_skips_isolation(self):
        def fake_run(argv, **kwargs):
            if argv[0] == "gh":
                body = json.dumps({"head": {"sha": SHA_HEAD, "ref": "feature/absent"}})
                return _proc(body)
            if argv[:2] == ["git", "rev-parse"]:
                return _proc(SHA_TIP + "\n")
            if argv[:2] == ["git", "merge-base"]:
                return _proc(SHA_TIP + "\n")
            if argv[:2] == ["git", "rev-list"]:
                return _proc("0\n")
            if argv[:3] == ["git", "worktree", "list"]:
                return _proc("worktree /repo\n")
            raise AssertionError(f"unexpected argv {argv}")

        with patch("keel.cli.run_argv", side_effect=fake_run), \
             patch("keel.git.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "verify-branch", str(PROJECTS / "example-android.yaml"),
                "--root", "/repo", "--pr", "300", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["isolation"]["verdict"], "n/a")

    def test_live_head_without_ref_skips_isolation(self):
        # gh returns a head SHA but no ref → no branch to locate locally.
        def fake_run(argv, **kwargs):
            if argv[0] == "gh":
                return _proc(json.dumps({"head": {"sha": SHA_HEAD}}))
            if argv[:2] == ["git", "rev-parse"]:
                return _proc(SHA_TIP + "\n")
            if argv[:2] == ["git", "merge-base"]:
                return _proc(SHA_TIP + "\n")
            if argv[:2] == ["git", "rev-list"]:
                return _proc("0\n")
            raise AssertionError(f"unexpected argv {argv}")

        with patch("keel.cli.run_argv", side_effect=fake_run), \
             patch("keel.git.run_argv", side_effect=fake_run):
            rc, out, _ = run([
                "verify-branch", str(PROJECTS / "example-android.yaml"),
                "--root", "/repo", "--pr", "300", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["verification"]["isolation"]["verdict"], "n/a")
        self.assertEqual(data["verification"]["ancestry"]["verdict"], "ok")

    def test_live_missing_owner_repo_reports_cleanly(self):
        bad = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: \"true\"\n"
        )
        rc, _, err = run([
            "verify-branch", bad, "--root", str(REPO_ROOT), "--pr", "300", "--json",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("owner and repo", err)

    def test_missing_config_reports_error(self):
        rc, _, err = run([
            "verify-branch", "no-such.yaml", "--pr", "300", "--offline",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_reports_error(self):
        bad = _write_raw("extends: keel\nbase_branch: main\ngates: [build]\n")
        rc, _, _ = run([
            "verify-branch", bad, "--pr", "300", "--offline",
        ])
        self.assertEqual(rc, 1)

    def test_negative_tolerance_rejected(self):
        with self.assertRaisesRegex(cli.argparse.ArgumentTypeError, "non-negative"):
            cli._nonnegative_int("-1")
        self.assertEqual(cli._nonnegative_int("0"), 0)

    def test_human_output_fully_clean_has_no_note(self):
        # All facts resolved + ok → no note line printed (the no-note branch).
        rc, out, _ = run(self.BASE + [
            "--head-sha", "h", "--base-tip-sha", "t", "--merge-base-sha", "t",
            "--base-distance", "0", "--worktree-path", "/repo/wt",
            "--repo-root", "/repo", "--linked-worktree", "true",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("keel verify-branch — pass", out)
        self.assertIn("base-distance : 0 (tolerance 5)", out)
        self.assertNotIn("note", out)

    def test_human_output_omits_distance_when_base_unresolved(self):
        # No base facts → base_distance is None, so no distance line is printed.
        rc, out, _ = run(self.BASE + ["--head-sha", "h"])
        self.assertEqual(rc, 0)
        self.assertNotIn("base-distance", out)
        self.assertIn("isolation     : n/a", out)


class TestVerifyBranchFactGathering(unittest.TestCase):
    """Direct unit coverage for the live fact-gathering seam branches."""

    def _args(self, **kw):
        base = dict(
            path=str(PROJECTS / "example-android.yaml"), root="/repo", pr=300,
            offline=False, head_sha=None, head_ref=None, base_tip_sha=None,
            merge_base_sha=None, base_distance=None, worktree_path=None,
            repo_root=None, linked_worktree=None,
        )
        base.update(kw)
        return Namespace(**base)

    def test_supplied_facts_short_circuit_live_calls(self):
        # Ancestry facts pre-supplied + no head_ref → every `is None` guard takes
        # its False side and the worktree lookup is skipped, so no git/gh call runs.
        def boom(*a, **k):
            raise AssertionError("no live call expected")

        args = self._args(
            head_sha="h", base_tip_sha="t", merge_base_sha="t", base_distance=0,
            worktree_path="/repo/wt", repo_root="/repo", linked_worktree="true",
        )
        with patch("keel.cli._gh_json", side_effect=boom), \
             patch("keel.git.run_argv", side_effect=boom):
            facts = cli._gather_branch_facts(args, "develop")
        self.assertEqual(facts["head_sha"], "h")
        self.assertEqual(facts["base_distance"], 0)
        self.assertTrue(facts["is_linked_worktree"])

    def test_head_without_ref_skips_local_worktree_lookup(self):
        # head_ref stays None → the `head_ref is not None` guard is False and the
        # worktree lookup is skipped without calling git for it.
        def fake_run(argv, **kwargs):
            if argv[:2] == ["git", "rev-parse"]:
                return _proc(SHA_TIP + "\n")
            raise AssertionError(f"unexpected {argv}")

        args = self._args(head_sha=SHA_HEAD, merge_base_sha=SHA_TIP, base_distance=0)
        with patch("keel.git.run_argv", side_effect=fake_run):
            facts = cli._gather_branch_facts(args, "develop")
        self.assertIsNone(facts["is_linked_worktree"])
        self.assertEqual(facts["base_tip_sha"], SHA_TIP)

    def test_local_facts_do_not_override_supplied_worktree_path(self):
        # worktree_path/is_linked supplied as flags; a live worktree lookup still
        # runs (head_ref set) but the supplied values win the `or`/`is None` guards.
        def fake_run(argv, **kwargs):
            if argv[:3] == ["git", "worktree", "list"]:
                porcelain = (
                    "worktree /repo\nbranch refs/heads/develop\n\n"
                    "worktree /repo/wt-auto\nbranch refs/heads/feature/x\n"
                )
                return _proc(porcelain)
            raise AssertionError(f"unexpected {argv}")

        args = self._args(
            head_sha="h", head_ref="feature/x", base_tip_sha="t",
            merge_base_sha="t", base_distance=0,
            worktree_path="/supplied/wt", repo_root="/supplied",
            linked_worktree="false",
        )
        with patch("keel.git.run_argv", side_effect=fake_run):
            facts = cli._gather_branch_facts(args, "develop")
        self.assertEqual(facts["worktree_path"], "/supplied/wt")
        self.assertEqual(facts["repo_root"], "/supplied")
        self.assertFalse(facts["is_linked_worktree"])

    def test_empty_porcelain_returns_none(self):
        self.assertEqual(cli._parse_worktree_porcelain(""), [])
        with patch("keel.git.run_argv",
                   side_effect=lambda *a, **k: _proc("\n")):
            self.assertIsNone(cli._local_worktree_facts("feature/x", cwd="/repo"))


class TestStepVerifyAndRunControls(unittest.TestCase):
    def test_step_verify_passes_with_handoff_and_evidence(self):
        handoff = stepverifier.build_handoff(
            step_id="s7",
            evidence_ids=["review-verdict-1"],
        )
        evidence_report = {
            "results": [{"id": "review-verdict-1", "ok": True}],
        }
        rc, out, _ = run([
            "step-verify",
            "--step", "s7",
            "--handoff-file", _write_raw(json.dumps(handoff)),
            "--evidence-report", _write_raw(json.dumps(evidence_report)),
            "--reviewers", "1",
            "--json",
        ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["status"], "pass")
        self.assertEqual(data["verification"]["required_evidence"], ["review-verdict-1"])

    def test_step_verify_human_pass_without_missing(self):
        handoff = stepverifier.build_handoff(step_id="s4")
        evidence_report = {"results": []}
        rc, out, _ = run([
            "step-verify",
            "--step", "s4",
            "--handoff-file", _write_raw(json.dumps(handoff)),
            "--evidence-report", _write_raw(json.dumps(evidence_report)),
        ])

        self.assertEqual(rc, 0)
        self.assertIn("keel step-verify", out)
        self.assertIn("required      : 0", out)

    def test_step_verify_fails_closed_for_bad_handoff_and_bad_json_shape(self):
        rc_bad, out_bad, _ = run([
            "step-verify",
            "--step", "s7",
            "--handoff-file", _write_raw(json.dumps({"step_id": "s7"})),
            "--evidence-report", _write_raw(json.dumps({"results": []})),
            "--reviewers", "1",
            "--json",
        ])
        rc_shape, _, err_shape = run([
            "step-verify",
            "--step", "s7",
            "--handoff-file", _write_raw("[]"),
            "--evidence-report", _write_raw(json.dumps({"results": []})),
            "--reviewers", "1",
        ])

        self.assertEqual(rc_bad, 1)
        self.assertEqual(json.loads(out_bad)["verification"]["status"], "fail")
        self.assertEqual(rc_shape, 1)
        self.assertIn("must contain a JSON object", err_shape)

    def test_runcontrols_appends_event_and_halts_on_cap(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            rc_first, out_first, _ = run([
                "runcontrols", str(events),
                "--slot", "fixloop",
                "--action", "fix",
                "--json",
            ])
            rc_halt, out_halt, _ = run([
                "runcontrols", str(events),
                "--slot", "fixloop",
                "--action", "fix",
                "--step-cap", "fixloop=1",
                "--json",
            ])

            stored = json.loads(events.read_text(encoding="utf-8"))

        self.assertEqual(rc_first, 0)
        self.assertEqual(json.loads(out_first)["run_controls"]["status"], "pass")
        self.assertEqual(rc_halt, 1)
        self.assertEqual(json.loads(out_halt)["run_controls"]["status"], "halt")
        self.assertEqual(len(stored), 2)

    def test_runcontrols_dry_run_and_invalid_step_cap(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            rc_dry, out_dry, _ = run([
                "runcontrols", str(events),
                "--slot", "tester",
                "--dry-run",
                "--json",
            ])
            rc_bad, _, err_bad = run([
                "runcontrols", str(events),
                "--step-cap", "bad",
            ])

            exists = events.exists()

        self.assertEqual(rc_dry, 0)
        self.assertFalse(json.loads(out_dry)["appended"])
        self.assertFalse(exists)
        self.assertEqual(rc_bad, 1)
        self.assertIn("--step-cap must use SLOT=N", err_bad)

    def test_runcontrols_human_halt_event_json_and_step_cap_errors(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            event = Path(d) / "event.json"
            events.write_text(json.dumps([
                {"slot": "fixloop", "action": "fix"},
            ]), encoding="utf-8")
            event.write_text(json.dumps({"slot": "fixloop", "action": "fix"}), encoding="utf-8")
            rc_human, out_human, _ = run([
                "runcontrols", str(events),
                "--event-json", str(event),
                "--step-cap", "fixloop=1",
            ])
            rc_bad_int, _, err_bad_int = run([
                "runcontrols", str(events),
                "--step-cap", "fixloop=nope",
            ])
            rc_bad_empty, _, err_bad_empty = run([
                "runcontrols", str(events),
                "--step-cap", "=0",
            ])

        self.assertEqual(rc_human, 1)
        self.assertIn("halt", out_human)
        self.assertEqual(rc_bad_int, 1)
        self.assertIn("positive integer", err_bad_int)
        self.assertEqual(rc_bad_empty, 1)
        self.assertIn("N > 0", err_bad_empty)

    def test_runcontrols_records_soft_failure_event(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            rc, out, _ = run([
                "runcontrols", str(events),
                "--slot", "tester",
                "--soft-failure",
                "--json",
            ])

        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["event"]["soft_failure"])

    def test_runcontrols_human_pass_without_event(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            events.write_text("[]", encoding="utf-8")
            rc, out, _ = run(["runcontrols", str(events)])

        self.assertEqual(rc, 0)
        self.assertIn("keel runcontrols", out)
        self.assertIn("events        : 0", out)

    def test_ship_ledger_stamps_run_controls_and_blocks_on_halt(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            events.write_text(json.dumps([
                {"slot": "fixloop", "action": "fix"},
                {"slot": "fixloop", "action": "fix"},
            ]), encoding="utf-8")
            rc, out, _ = run([
                "ship", _write_config("'true'"),
                "--root", d,
                "--run-events-file", str(events),
                "--max-rounds", "1",
                "--json",
            ])

        self.assertEqual(rc, 1)
        record = json.loads(out)["result"]["run_ledger"]["record"]
        self.assertEqual(record["run_controls"]["status"], "halt")
        self.assertEqual(record["run_controls"]["summary"]["event_count"], 2)

    def test_ship_invalid_run_events_file_and_human_halt(self):
        with tempfile.TemporaryDirectory() as d:
            events = Path(d) / "events.json"
            events.write_text("{}", encoding="utf-8")
            rc_bad, _, err_bad = run([
                "ship", _write_config("'true'"),
                "--root", d,
                "--run-events-file", str(events),
            ])
            events.write_text(json.dumps([
                {"slot": "fixloop", "action": "fix"},
                {"slot": "fixloop", "action": "fix"},
            ]), encoding="utf-8")
            rc_halt, out_halt, _ = run([
                "ship", _write_config("'true'"),
                "--root", d,
                "--run-events-file", str(events),
                "--max-rounds", "1",
            ])

        self.assertEqual(rc_bad, 1)
        self.assertIn("must contain a JSON array", err_bad)
        self.assertEqual(rc_halt, 1)
        self.assertIn("run controls  : halt", out_halt)

    def test_step_verify_human_missing_and_unknown_step(self):
        handoff = stepverifier.build_handoff(step_id="s7")
        evidence_report = {"results": []}
        rc_missing, out_missing, _ = run([
            "step-verify",
            "--step", "s7",
            "--handoff-file", _write_raw(json.dumps(handoff)),
            "--evidence-report", _write_raw(json.dumps(evidence_report)),
            "--reviewers", "1",
        ])
        rc_unknown, _, err_unknown = run([
            "step-verify",
            "--step", "s404",
            "--handoff-file", _write_raw(json.dumps(handoff)),
            "--evidence-report", _write_raw(json.dumps(evidence_report)),
        ])

        self.assertEqual(rc_missing, 1)
        self.assertIn("missing", out_missing)
        self.assertIn("required      : 1", out_missing)
        self.assertEqual(rc_unknown, 1)
        self.assertIn("unknown backbone step", err_unknown)


class TestCoreMerge(unittest.TestCase):
    def test_claim_and_release_cli(self):
        with tempfile.TemporaryDirectory() as d:
            rc_claim, out_claim, _ = run([
                "claim", "--root", d, "--owner", "agent-a", "--json", "merge",
            ])
            rc_release, out_release, _ = run([
                "release", "--root", d, "--owner", "agent-a", "--json", "merge",
            ])

        self.assertEqual(rc_claim, 0)
        self.assertEqual(json.loads(out_claim)["status"], "granted")
        self.assertEqual(rc_release, 0)
        self.assertEqual(json.loads(out_release)["status"], "released")

    def test_claim_denial_human_output_reports_holder(self):
        with tempfile.TemporaryDirectory() as d:
            rc_success, out_success, _ = run(["claim", "--root", d, "--owner", "agent-a", "ci"])
            run(["claim", "--root", d, "--owner", "agent-a", "merge"])
            rc, out, _ = run(["claim", "--root", d, "--owner", "agent-b", "merge"])

        self.assertEqual(rc_success, 0)
        self.assertIn("granted", out_success)
        self.assertEqual(rc, 1)
        self.assertIn("denied", out)
        self.assertIn("holder: agent-a", out)

    def test_release_non_owner_human_output_reports_holder(self):
        with tempfile.TemporaryDirectory() as d:
            run(["claim", "--root", d, "--owner", "agent-a", "ci"])
            rc_success, out_success, _ = run(["release", "--root", d, "--owner", "agent-a", "ci"])
            run(["claim", "--root", d, "--owner", "agent-a", "merge"])
            rc, out, _ = run(["release", "--root", d, "--owner", "agent-b", "merge"])

        self.assertEqual(rc_success, 0)
        self.assertIn("released", out_success)
        self.assertEqual(rc, 1)
        self.assertIn("not-owner", out)
        self.assertIn("holder: agent-a", out)

    def test_claim_and_release_human_output_omit_empty_holder(self):
        claim_result = Namespace(
            status="granted",
            resource="merge",
            owner="agent-a",
            holder=None,
            path="/tmp/merge.lock",
            granted=True,
            as_dict=lambda: {},
        )
        release_result = Namespace(
            status="missing",
            resource="merge",
            owner="agent-a",
            holder=None,
            path="/tmp/merge.lock",
            granted=False,
            as_dict=lambda: {},
        )
        with patch("keel.cli.lock.claim_resource", return_value=claim_result):
            rc_claim, out_claim, _ = run(["claim", "--owner", "agent-a", "merge"])
        with patch("keel.cli.lock.release_resource", return_value=release_result):
            rc_release, out_release, _ = run(["release", "--owner", "agent-a", "merge"])

        self.assertEqual(rc_claim, 0)
        self.assertNotIn("holder:", out_claim)
        self.assertEqual(rc_release, 0)
        self.assertNotIn("holder:", out_release)

    def test_worktree_remove_rejects_unregistered_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worktree = root / "worktrees" / "issue-1"
            worktree.mkdir(parents=True)
            with patch("keel.cli.git.worktree_list",
                       return_value=_proc(f"worktree {root}\n")):
                rc, _, err = run(["worktree-remove", "--root", d, str(worktree)])

        self.assertEqual(rc, 1)
        self.assertIn("not a registered git worktree", err)

    def test_worktree_remove_reports_list_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worktree = root / "worktrees" / "issue-1"
            worktree.mkdir(parents=True)
            with patch("keel.cli.git.worktree_list",
                       return_value=_proc("bad list", ok=False)):
                rc, _, err = run(["worktree-remove", "--root", d, str(worktree)])

        self.assertEqual(rc, 1)
        self.assertIn("bad list", err)

    def test_worktree_remove_rejects_repo_root(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["worktree-remove", "--root", d, d])

        self.assertEqual(rc, 1)
        self.assertIn("nested under the repository root", err)

    def test_worktree_remove_calls_git_after_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worktree = root / "worktrees" / "issue-1"
            worktree.mkdir(parents=True)
            with (
                patch("keel.cli.git.worktree_list",
                      return_value=_proc(f"worktree {worktree}\n")),
                patch("keel.cli.git.worktree_remove",
                      return_value=_proc("")),
            ):
                rc, out, _ = run(["worktree-remove", "--root", d, str(worktree)])

        self.assertEqual(rc, 0)
        self.assertIn("removed", out)

    def test_worktree_remove_json_and_failed_git_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worktree = root / "worktrees" / "issue-1"
            worktree.mkdir(parents=True)
            with (
                patch("keel.cli.git.worktree_list",
                      return_value=_proc(f"worktree {worktree}\n")),
                patch("keel.cli.git.worktree_remove",
                      return_value=_proc("remove failed", ok=False)),
            ):
                rc_json, out_json, _ = run([
                    "worktree-remove", "--root", d, "--json", str(worktree),
                ])
                rc_human, out_human, _ = run(["worktree-remove", "--root", d, str(worktree)])

        self.assertEqual(rc_json, 1)
        self.assertFalse(json.loads(out_json)["removed"])
        self.assertEqual(rc_human, 1)
        self.assertIn("remove failed", out_human)

    def test_merge_reports_missing_and_invalid_config(self):
        rc_missing, _, err_missing = run([
            "merge", str(Path("missing.yaml")), "--pr", "1",
            "--approve-scope", "filesystem,git,github",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")
            bad = f.name
        self.addCleanup(os.unlink, bad)
        rc_bad, _, err_bad = run([
            "merge", bad, "--pr", "1", "--approve-scope", "filesystem,git,github",
        ])

        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)
        self.assertEqual(rc_bad, 1)
        self.assertIn("missing required", err_bad)

    def test_merge_blocks_missing_capability_and_missing_consent(self):
        missing_report = runtime.CapabilityReport((
            runtime.Capability("git", False, "missing", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=missing_report):
            rc_cap, _, err_cap = run([
                "merge", str(PROJECTS / "keel.yaml"), "--pr", "123",
            ])
        with patch("keel.cli.runtime.detect", return_value=_merge_capability_report()):
            rc_consent, _, err_consent = run([
                "merge", str(PROJECTS / "keel.yaml"), "--pr", "123",
            ])

        self.assertEqual(rc_cap, 1)
        self.assertIn("missing required", err_cap)
        self.assertEqual(rc_consent, 1)
        self.assertIn("operator consent required", err_consent)

    def test_merge_blocks_when_escalation_scope_is_not_approved(self):
        fake_report = _merge_capability_report()
        argv = _merge_args(json_out=True)
        argv.extend([
            "--risk-tier", "tier-3",
            "--trust-signal", "low",
            "--escalation-side-effect", "secret_access",
        ])
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.lock.claim_resource") as claim_mock,
        ):
            rc, out, _ = run(argv)

        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["reason"], "operator escalation required")
        self.assertEqual(data["missing_scope"], ["secrets"])
        self.assertTrue(data["escalation"]["operator_required"])
        claim_mock.assert_not_called()

    def test_merge_human_blocks_when_escalation_scope_is_not_approved(self):
        fake_report = _merge_capability_report()
        argv = _merge_args()
        argv.extend(["--escalation-side-effect", "secret_access"])
        with patch("keel.cli.runtime.detect", return_value=fake_report):
            rc, _, err = run(argv)

        self.assertEqual(rc, 1)
        self.assertIn("operator escalation required", err)
        self.assertIn("secrets", err)

    def test_merge_reports_extension_problem_and_consent_env_error(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.load_extensions", return_value=({}, ["broken-extension"])),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "FAILURE"}],
                  })),
        ):
            rc_ext, _, err_ext = run(_merge_args())
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch.dict("os.environ", {"KEEL_APPROVE_SCOPE": "filesystem,git,github"}, clear=True),
        ):
            rc_env, _, err_env = run([
                "merge", str(PROJECTS / "keel.yaml"), "--pr", "123",
                "--consent-mode", "standing",
            ])

        self.assertEqual(rc_ext, 1)
        self.assertIn("extension not loaded", err_ext)
        self.assertEqual(rc_env, 1)
        self.assertIn("KEEL_OPERATOR", err_env)

    def test_merge_blocks_red_ci_before_evidence(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "FAILURE"}],
                  })),
            patch("keel.cli._verify_merge_evidence") as evidence_mock,
        ):
            rc, out, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["ci"]["state"], "fail")
        evidence_mock.assert_not_called()

    def test_merge_blocks_an_empty_check_set_on_a_non_docs_pr(self):
        # An empty statusCheckRollup is "CI never ran", not "CI passed". Reading it as a
        # pass merged code no workflow ever built (#627). The carve-out is docs-only PRs,
        # where no workflow is expected to trigger — so a code PR with zero checks blocks.
        fake_report = _merge_capability_report()
        snapshot = _json_result({
            "headRefOid": "abc",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        })
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot", return_value=snapshot),
            patch("keel.cli._verify_merge_evidence", return_value={"docs_only": False}),
        ):
            rc, out, _ = run(_merge_args(json_out=True))

        payload = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(payload["ci"]["state"], "no-checks")
        self.assertFalse(payload["ci_no_checks_docs_only"])
        self.assertIn("empty check set", payload["reason"])

    def test_merge_allows_an_empty_check_set_on_a_docs_only_pr(self):
        fake_report = _merge_capability_report()
        snapshot = _json_result({
            "headRefOid": "abc",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [],
        })
        evidence = {
            "docs_only": True,
            "enforced": True,
            "verification": {"status": "pass", "missing": []},
        }
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot", return_value=snapshot),
            patch("keel.cli._verify_merge_evidence", return_value=evidence),
            patch("keel.cli.ledger.gates_pass_for_head", return_value=(False, None)),
        ):
            rc, out, _ = run(_merge_args(json_out=True))

        payload = json.loads(out)
        # It gets past the CI check (the next gate, gates-for-head, is what stops it) —
        # which is the point: the empty check set alone did not block a docs-only PR.
        self.assertEqual(rc, 1)
        self.assertTrue(payload["ci_no_checks_docs_only"])
        self.assertIn("no gates-pass recorded", payload["reason"])

    def _merge_with_changed_files(self, changed):
        """Drive `keel merge` with a real evidence load, so the docs-only carve-out is
        computed by production code instead of handed in by the test.

        Patching `_verify_merge_evidence` (as the two tests above do) pins the *branch*
        in `_cmd_merge` but not the *value* that drives it — with that seam,
        `docs_only = True` is a green mutation and #627 is quietly re-opened. Patching
        one level deeper, at the artifact load, leaves `classify.is_docs_only` in the
        path.
        """
        artifacts = {
            "pr_body": "Closes #1", "pr_comments": [], "issue_comments": [],
            "pr_reviews": [], "issue": 1, "head_sha": "abc", "head_ref": "feature/x",
            "changed_files": changed, "pr_labels": ["keel:ship"],
        }
        snapshot = _json_result({
            "headRefOid": "abc", "mergeStateStatus": "CLEAN", "statusCheckRollup": [],
        })
        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot", return_value=snapshot),
            patch("keel.cli._load_evidence_artifacts", return_value=artifacts),
            patch("keel.cli.ledger.gates_pass_for_head", return_value=(False, None)),
        ):
            rc, out, _ = run(_merge_args(json_out=True))
        return rc, json.loads(out)

    def test_the_docs_only_carve_out_is_computed_from_the_real_changed_files(self):
        # Every changed path is a docs path -> the empty check set is tolerated, and the
        # run proceeds to the next gate (gates-for-head, which is what stops it here).
        rc_docs, docs = self._merge_with_changed_files(["docs/keel/cli.md", "README.md"])
        # One code path is enough to withdraw the carve-out.
        rc_code, code = self._merge_with_changed_files(["docs/keel/cli.md", "src/keel/cli.py"])

        self.assertTrue(docs["ci_no_checks_docs_only"])
        # It cleared the CI check and was stopped by a later gate — which is the point.
        self.assertNotIn("empty check set", docs["reason"])
        self.assertFalse(code["ci_no_checks_docs_only"])
        self.assertIn("empty check set", code["reason"])
        self.assertEqual((rc_docs, rc_code), (1, 1))

    def test_an_unreadable_or_empty_changeset_never_earns_the_carve_out(self):
        # The carve-out is the one place an empty CI check set is tolerated, so "we could
        # not see what changed" must not buy it.
        for changed in ([], None):
            with self.subTest(changed=changed):
                rc, payload = self._merge_with_changed_files(changed)
                self.assertEqual(rc, 1)
                self.assertFalse(payload["ci_no_checks_docs_only"])
                self.assertIn("empty check set", payload["reason"])

    def test_the_merge_gate_reads_the_evidence_knobs_from_config(self):
        # #633 pinned these two wires on the `evidence-verify` path but not on the copy
        # inside `_verify_merge_evidence` — the merge-gate one, i.e. the safety-critical
        # of the two. An operator who tightens either knob in config had no proof it
        # reached `keel merge`.
        config = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: acme/example\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "  evidence_gate_label: 'acme:reviewed'\n"
            "  evidence_require_distinct_vendors: true\n"
        )
        args = Namespace(
            path=config, root=str(REPO_ROOT), pr=300, reviewers=None,
            review_comments="inline", jury=False, no_jury=True, jury_advisory=False,
            jury_vendors=None, require_distinct_vendors=False, gate_label=None,
            waiver_label=None, deferral=[], phase="pre-merge", require_armed=False,
            dry_run=False, issue=None,
        )
        # Two LGTMs from the *same* vendor: only a live `require_distinct_vendors`
        # turns that into a finding, so the finding is the knob's observable effect.
        same_vendor = [
            _trusted_comment("keel.review-verdict.v1\nreviewer: a\nvendor: claude\n"
                             "head: abc\nLGTM"),
            _trusted_comment("keel.review-verdict.v1\nreviewer: b\nvendor: claude\n"
                             "head: abc\nLGTM"),
        ]
        artifacts = {
            "pr_body": "Closes #1", "pr_comments": same_vendor, "issue_comments": [],
            "pr_reviews": [], "issue": 1, "head_sha": "abc", "head_ref": "feature/x",
            "changed_files": ["src/keel/cli.py"], "pr_labels": ["acme:reviewed"],
        }
        with patch("keel.cli._load_evidence_artifacts", return_value=artifacts):
            payload = cli._verify_merge_evidence(args, cli.cfg.load_config(config))

        # The configured label armed the gate — the built-in default would not have.
        self.assertEqual(payload["gate_label"], "acme:reviewed")
        self.assertTrue(payload["enforced"])
        self.assertTrue(
            any(f["id"] == "review-vendor-distinctness"
                for f in payload["verification"]["findings"]),
            payload["verification"]["findings"],
        )

    def test_merge_blocks_closed_window_snapshot_error_and_dirty_state(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=False),
        ):
            rc_window, out_window, _ = run(_merge_args(json_out=True))
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_proc("gh failed", ok=False)),
        ):
            rc_snapshot, out_snapshot, _ = run(_merge_args(json_out=True))
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "DIRTY",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
        ):
            rc_dirty, out_dirty, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc_window, 1)
        self.assertIn("merge window is closed", json.loads(out_window)["reason"])
        self.assertEqual(rc_snapshot, 1)
        self.assertIn("unable to read", json.loads(out_snapshot)["reason"])
        self.assertEqual(rc_dirty, 1)
        self.assertIn("DIRTY", json.loads(out_dirty)["reason"])

    def test_merge_hotfix_records_window_bypass(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.github.issue_facts",
                  return_value=_proc(json.dumps(
                      {"title": "hotfix: patch the boot loop", "labels": []}))),
        ):
            argv = _merge_args(json_out=True, dry_run=True)
            argv += ["--hotfix", "--blocker-rule", "blocker-title-regex",
                     "--issue", "42"]
            rc, out, _ = run(argv)

        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(payload["window"]["bypassed"])
        self.assertEqual(payload["hotfix_justification"]["kind"], "matched-rule")
        self.assertEqual(payload["hotfix_justification"]["rule_id"], "blocker-title-regex")

    def test_merge_hotfix_matched_rule_refused_without_issue(self):
        # Security-load-bearing: an agent omitting --issue and passing a
        # fabricated --issue-title must NOT be able to self-justify a window
        # bypass — the matched-rule path requires host-authoritative facts.
        fake_report = _merge_capability_report()
        with patch("keel.cli.runtime.detect", return_value=fake_report):
            argv = _merge_args(dry_run=True)
            argv += ["--hotfix", "--blocker-rule", "blocker-title-regex",
                     "--issue-title", "hotfix: x"]
            rc, _, err = run(argv)
        self.assertEqual(rc, 1)
        self.assertIn("host-authoritative", err)
        self.assertIn("--operator-override", err)

    def test_merge_hotfix_matched_rule_refused_when_fetch_not_authoritative(self):
        # --issue given but the live fetch fails (non-dict / error) → facts are
        # not host-authoritative → matched-rule refused.
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.issue_facts",
                  return_value=_proc("offline", ok=False)),
        ):
            argv = _merge_args(dry_run=True)
            argv += ["--hotfix", "--blocker-rule", "blocker-title-regex",
                     "--issue", "42", "--issue-title", "hotfix: x"]
            rc, _, err = run(argv)
        self.assertEqual(rc, 1)
        self.assertIn("host-authoritative", err)

    def test_merge_hotfix_refused_without_justification(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
        ):
            argv = _merge_args(json_out=True, dry_run=True)
            argv.append("--hotfix")
            rc, out, _ = run(argv)
        payload = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(payload["reason"], "hotfix justification required")
        self.assertIsNone(payload["hotfix_justification"])

    def test_merge_hotfix_refused_for_non_matching_rule(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.issue_facts",
                  return_value=_proc(json.dumps(
                      {"title": "routine docs tidy", "labels": []}))),
        ):
            argv = _merge_args(dry_run=True)
            argv += ["--hotfix", "--blocker-rule", "blocker-title-regex",
                     "--issue", "42"]
            rc, _, err = run(argv)
        self.assertEqual(rc, 1)
        self.assertIn("did not match", err)

    def test_merge_hotfix_refused_for_unknown_rule(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.issue_facts",
                  return_value=_proc(json.dumps(
                      {"title": "hotfix: x", "labels": []}))),
        ):
            argv = _merge_args(dry_run=True)
            argv += ["--hotfix", "--blocker-rule", "no-such-rule",
                     "--issue", "42"]
            rc, _, err = run(argv)
        self.assertEqual(rc, 1)
        self.assertIn("unknown blocker rule", err)

    def test_merge_hotfix_operator_override(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
        ):
            argv = _merge_args(json_out=True, dry_run=True)
            argv += ["--hotfix", "--operator-override"]
            rc, out, _ = run(argv)
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["hotfix_justification"]["kind"], "operator-override")
        self.assertEqual(payload["hotfix_justification"]["operator"], "tester")

    def test_merge_hotfix_refused_on_invalid_blocker_rules(self):
        fake_report = _merge_capability_report()
        bad = _write_raw(
            "extends: keel\ncore_version: 1.0.0\nbase_branch: main\nrepo: tmp\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "policy_pack:\n  name: x\n  blocker_rules:\n"
            "    - id: bad\n      kind: title-regex\n      pattern: '('\n"
        )
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
        ):
            argv = [
                "merge", bad, "--root", str(REPO_ROOT), "--pr", "123",
                "--approve-scope", "filesystem,git,github", "--operator", "tester",
                "--json", "--dry-run", "--hotfix",
                "--blocker-rule", "bad", "--issue-title", "hotfix: x",
            ]
            rc, out, _ = run(argv)
        payload = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(payload["reason"], "hotfix justification required")

    def test_merge_operator_override_requires_named_operator(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
        ):
            argv = [
                "merge", str(PROJECTS / "keel.yaml"),
                "--root", str(REPO_ROOT), "--pr", "123",
                "--approve-scope", "filesystem,git,github",
                "--json", "--dry-run", "--hotfix", "--operator-override",
            ]
            rc, out, _ = run(argv)
        payload = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(payload["reason"], "hotfix justification required")

    def test_merge_blocks_pending_ci_and_non_enforced_evidence(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"status": "IN_PROGRESS"}],
                  })),
        ):
            rc_pending, out_pending, _ = run(_merge_args(json_out=True))
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": False,
                "verification": {"status": "pass", "missing": []},
            }),
        ):
            rc_evidence, out_evidence, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc_pending, 1)
        self.assertEqual(json.loads(out_pending)["ci"]["state"], "pending")
        self.assertEqual(rc_evidence, 1)
        self.assertIn("not enforced", json.loads(out_evidence)["reason"])

    def test_merge_blocks_missing_evidence(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "fail", "missing": ["review-verdict-1"]},
            }),
        ):
            rc, out, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc, 1)
        self.assertIn("missing evidence", json.loads(out)["reason"])

    def test_merge_allows_projects_without_configured_window(self):
        fake_report = _merge_capability_report()
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(
                "extends: keel\n"
                "core_version: '^1.0'\n"
                "base_branch: main\n"
                "knobs:\n"
                "  build_gate_cmd: make test\n"
            )
            path = f.name
        self.addCleanup(os.unlink, path)
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
        ):
            rc, out, _ = run([
                "merge", path,
                "--root", str(REPO_ROOT),
                "--pr", "123",
                "--approve-scope", "filesystem,git,github",
                "--operator", "tester",
                "--dry-run",
                "--json",
            ])

        self.assertEqual(rc, 0)
        self.assertIsNone(json.loads(out)["window"])

    def test_merge_blocks_when_lock_is_already_claimed(self):
        fake_report = _merge_capability_report()
        with tempfile.TemporaryDirectory() as d:
            cli.lock.claim_resource(cli._lock_root(d), "merge", owner="other")
            with patch("keel.cli.runtime.detect", return_value=fake_report):
                rc, out, _ = run(_merge_args(root=d, json_out=True))

        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["lock"]["status"], "denied")

    def test_merge_dry_run_passes_after_lock_window_ci_and_evidence(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
            patch("keel.cli.github.merge_pr") as merge_mock,
        ):
            rc, out, _ = run(_merge_args(json_out=True, dry_run=True))

        self.assertEqual(rc, 0)
        self.assertFalse(json.loads(out)["merged"])
        self.assertTrue(json.loads(out)["gates_sha"]["matched"])
        merge_mock.assert_not_called()

    def test_merge_executes_and_reports_gh_merge_failure(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
            patch("keel.cli.github.merge_pr",
                  return_value=_proc("merge failed", ok=False)),
        ):
            rc_fail, out_fail, _ = run(_merge_args(json_out=True))
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
            patch("keel.cli.github.merge_pr",
                  return_value=_proc("merged")),
        ):
            rc_ok, out_ok, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc_fail, 1)
        self.assertEqual(json.loads(out_fail)["reason"], "gh merge failed")
        self.assertEqual(rc_ok, 0)
        self.assertTrue(json.loads(out_ok)["merged"])

    def _gates_record(self, *, pr, head_sha, ok=True, blocked=False, error=None):
        return {
            "schema_version": ledger.LEDGER_SCHEMA_VERSION,
            "record_type": ledger.RECORD_TYPE_SHIP_RUN,
            "run_id": f"RUN-{pr}",
            "pull_request": {"number": pr},
            "git": {"head_sha": head_sha},
            "verdict": {"blocked": blocked},
            "gates": [{"gate": "build", "ok": ok, "skipped": False, "error": error}],
        }

    def _merge_with_gates(self, records):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "head-new",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=records),
            patch("keel.cli.github.merge_pr",
                  return_value=_proc("merged")),
        ):
            return run(_merge_args(json_out=True))

    def test_merge_requires_gates_pass_for_current_head(self):
        records = [self._gates_record(pr=123, head_sha="head-new")]
        rc, out, _ = self._merge_with_gates(records)

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["merged"])
        self.assertTrue(data["gates_sha"]["matched"])
        self.assertEqual(data["gates_sha"]["head_sha"], "head-new")

    def test_merge_refuses_when_gates_pass_is_for_stale_head(self):
        records = [self._gates_record(pr=123, head_sha="head-old")]
        rc, out, _ = self._merge_with_gates(records)

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertFalse(data["merged"])
        self.assertFalse(data["gates_sha"]["matched"])
        self.assertIn("no gates-pass recorded for the current head head-new", data["reason"])

    def test_merge_refuses_when_no_gates_record_exists(self):
        rc, out, _ = self._merge_with_gates([])

        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertIn("no gates-pass recorded for the current head head-new", data["reason"])

    def test_merge_refuses_when_gates_record_failed(self):
        records = [
            self._gates_record(pr=123, head_sha="head-new", ok=False, blocked=True),
        ]
        rc, out, _ = self._merge_with_gates(records)

        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["gates_sha"]["matched"])

    def test_merge_hotfix_bypasses_gates_sha_requirement(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "head-new",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records") as read_mock,
            patch("keel.cli.github.merge_pr",
                  return_value=_proc("merged")),
            patch("keel.cli.github.issue_facts",
                  return_value=_proc(json.dumps(
                      {"title": "boot loop", "labels": [{"name": "blocker"}]}))),
        ):
            argv = _merge_args(json_out=True)
            argv += ["--hotfix", "--blocker-rule", "blocker-label",
                     "--issue", "42"]
            rc, out, _ = run(argv)

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["merged"])
        self.assertTrue(data["gates_sha"]["bypassed"])
        self.assertEqual(data["gates_sha"]["reason"], "hotfix")
        self.assertEqual(data["hotfix_justification"]["kind"], "matched-rule")
        read_mock.assert_not_called()

    def test_merge_reports_invalid_ledger_during_gates_check(self):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "head-new",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records",
                  side_effect=ledger.LedgerError("bad line")),
        ):
            rc, out, _ = run(_merge_args(json_out=True))

        self.assertEqual(rc, 1)
        self.assertIn("invalid run ledger", json.loads(out)["reason"])

    def test_merge_human_output_prints_gates_sha(self):
        args = Namespace(json=False, pr=7)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._finish_merge(args, {"gates_sha": {"bypassed": False, "matched": True}},
                              "merged", code=0)
        self.assertIn("gates-sha: matched", out.getvalue())

        out_bypass = io.StringIO()
        with contextlib.redirect_stdout(out_bypass):
            cli._finish_merge(args, {"gates_sha": {"bypassed": True}}, "merged", code=0)
        self.assertIn("gates-sha: bypassed (hotfix)", out_bypass.getvalue())

        out_nomatch = io.StringIO()
        with contextlib.redirect_stdout(out_nomatch):
            cli._finish_merge(args, {"gates_sha": {"bypassed": False, "matched": False}},
                              "blocked", code=1)
        self.assertIn("gates-sha: no-match", out_nomatch.getvalue())

        out_just = io.StringIO()
        with contextlib.redirect_stdout(out_just):
            cli._finish_merge(
                args,
                {"hotfix_justification": {"kind": "matched-rule", "rule_id": "blocker-label"}},
                "merged", code=0,
            )
        self.assertIn("hotfix : matched-rule blocker-label", out_just.getvalue())

        out_override = io.StringIO()
        with contextlib.redirect_stdout(out_override):
            cli._finish_merge(
                args,
                {"hotfix_justification": {
                    "kind": "operator-override", "operator": "tester"}},
                "merged", code=0,
            )
        self.assertIn("hotfix : operator-override tester", out_override.getvalue())

    def test_merge_human_output_prints_ci_and_evidence(self):
        args = Namespace(json=False, pr=7)
        payload = {
            "lock": {"status": "granted"},
            "ci": {"state": "pass"},
            "evidence": {"verification": {"status": "pass"}},
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli._finish_merge(args, payload, "ok", code=0)

        self.assertEqual(rc, 0)
        self.assertIn("evidence: pass", out.getvalue())

    def test_merge_human_output_handles_sparse_payload(self):
        args = Namespace(json=False, pr=7)
        payload = {"lock": None, "ci": None, "evidence": {"verification": "bad"}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli._finish_merge(args, payload, "blocked", code=1)

        self.assertEqual(rc, 1)
        self.assertIn("blocked", out.getvalue())

    def test_merge_snapshot_and_ci_rollup_edges(self):
        with patch(
            "keel.cli.github.pr_merge_snapshot",
            return_value=_proc("{"),
        ):
            with self.assertRaisesRegex(ValueError, "not JSON"):
                cli._merge_snapshot(1, cwd=".")
        pending = cli._ci_rollup_state(["bad", {"status": "PENDING"}])
        empty = cli._ci_rollup_state([])

        self.assertEqual(pending["state"], "pending")
        self.assertEqual(empty["reason"], "no-checks")

    def test_merge_ci_rollup_skipped_matches_ship_assessment(self):
        rollup = cli._ci_rollup_state([{"conclusion": "SKIPPED"}])

        self.assertTrue(ship.ci_passing("SKIPPED"))
        self.assertEqual(rollup["state"], "pass")

    def test_ci_rollup_state_ignores_superseded_failure_from_rerun(self):
        # Same check name reran to green; the stale FAILURE entry must not block.
        rollup = [
            {"name": "lint", "conclusion": "FAILURE", "completedAt": "2026-07-10T09:16:26Z"},
            {"name": "lint", "conclusion": "SUCCESS", "completedAt": "2026-07-10T09:17:35Z"},
        ]

        self.assertEqual(cli._ci_rollup_state(rollup)["state"], "pass")

    def test_ci_rollup_state_still_fails_on_current_failure(self):
        rollup = [
            {"name": "lint", "conclusion": "SUCCESS", "completedAt": "2026-07-10T09:16:26Z"},
            {"name": "lint", "conclusion": "FAILURE", "completedAt": "2026-07-10T09:17:35Z"},
        ]

        self.assertEqual(cli._ci_rollup_state(rollup)["state"], "fail")

    def test_dedupe_rollup_keys_by_context_and_skips_non_dicts(self):
        rollup = [
            "not-a-dict",
            {
                "context": "legacy-status", "conclusion": "FAILURE",
                "startedAt": "2026-07-10T09:00:00Z",
            },
            {
                "context": "legacy-status", "conclusion": "SUCCESS",
                "startedAt": "2026-07-10T09:05:00Z",
            },
            {"name": "unrelated-check", "conclusion": "SUCCESS"},
        ]

        deduped = cli._dedupe_rollup(rollup)

        by_key = {(item.get("context") or item.get("name")): item for item in deduped}
        self.assertEqual(len(deduped), 2)
        self.assertEqual(by_key["legacy-status"]["conclusion"], "SUCCESS")
        self.assertEqual(by_key["unrelated-check"]["conclusion"], "SUCCESS")

    def test_dedupe_rollup_keeps_latest_when_later_list_entry_is_older(self):
        # Rollup order is not guaranteed chronological; an out-of-order older
        # duplicate must not overwrite an already-newer entry for the same check.
        rollup = [
            {"name": "lint", "conclusion": "SUCCESS", "completedAt": "2026-07-10T09:17:35Z"},
            {"name": "lint", "conclusion": "FAILURE", "completedAt": "2026-07-10T09:16:26Z"},
        ]

        deduped = cli._dedupe_rollup(rollup)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["conclusion"], "SUCCESS")

    def test_dedupe_rollup_pending_requeue_beats_older_completed_entry(self):
        # A rerun freshly requeued has no conclusion/timestamp yet; it must not
        # lose to an older *completed* run of the same check just because it
        # lacks a timestamp — a new run can't be queued before the previous one
        # concluded, so "no conclusion yet" always outranks any concluded entry.
        rollup = [
            {"name": "lint", "conclusion": "SUCCESS", "completedAt": "2026-07-10T09:16:26Z"},
            {"name": "lint", "conclusion": None, "status": "QUEUED"},
        ]

        deduped = cli._dedupe_rollup(rollup)

        self.assertEqual(len(deduped), 1)
        self.assertIsNone(deduped[0]["conclusion"])
        self.assertEqual(cli._ci_rollup_state(rollup)["state"], "pending")

    def test_dedupe_rollup_pending_requeue_wins_regardless_of_list_order(self):
        rollup = [
            {"name": "lint", "conclusion": None, "status": "QUEUED"},
            {"name": "lint", "conclusion": "SUCCESS", "completedAt": "2026-07-10T09:16:26Z"},
        ]

        deduped = cli._dedupe_rollup(rollup)

        self.assertEqual(len(deduped), 1)
        self.assertIsNone(deduped[0]["conclusion"])

    def test_dedupe_rollup_unrecognized_pending_shape_does_not_mask_real_failure(self):
        # A no-conclusion entry only outranks a concluded one when its own
        # `status` is a recognized pending state — not merely because
        # `conclusion` is absent. A malformed/unexpected shape (no
        # conclusion, no recognized status) must fall back to timestamp
        # comparison so a genuine later FAILURE is never masked.
        rollup = [
            {"name": "flaky-check", "conclusion": None, "startedAt": "2026-07-01T09:00:00Z"},
            {"name": "flaky-check", "conclusion": "FAILURE", "completedAt": "2026-07-10T09:00:00Z"},
        ]

        deduped = cli._dedupe_rollup(rollup)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["conclusion"], "FAILURE")
        self.assertEqual(cli._ci_rollup_state(rollup)["state"], "fail")

    def test_verify_merge_evidence_uses_live_artifacts_and_tier(self):
        config = cli.cfg.load_config(PROJECTS / "keel.yaml")
        args = Namespace(
            pr=123,
            issue=265,
            root=str(REPO_ROOT),
            reviewers=1,
            review_comments="summary",
            jury=False,
            no_jury=True,
            jury_advisory=False,
            gate_label=None,
        )
        artifact = {
            "pr_body": "Closes #265",
            "pr_comments": [
                {
                    "body": "<!-- keel.review-verdict.v1 -->\nreviewer: a\nhead: abc\nLGTM",
                    "author_association": "OWNER",
                },
                {
                    "body": "<!-- keel.closure-comment.v1 -->",
                    "author_association": "OWNER",
                },
            ],
            "issue_comments": [
                {"body": "<!-- keel.closure-comment.v1 -->", "author_association": "OWNER"},
            ],
            "pr_reviews": [],
            "issue": 265,
            "head_sha": "abc",
            "changed_files": ["src/keel/cli.py"],
            "pr_labels": ["keel:ship", "agent:claude"],
        }
        with patch("keel.cli._load_evidence_artifacts", return_value=artifact):
            report = cli._verify_merge_evidence(args, config)

        self.assertTrue(report["enforced"])
        self.assertEqual(report["verification"]["status"], "pass")


    def test_capture_reconcile_plans_missing_marker_actions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run([
                "capture-reconcile", config, "--root", d,
                "--merged-pr", "160", "--linked-issue", "160=42", "--json",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        reconcile = data["reconcile"]
        self.assertEqual(data["contract"]["schema_version"], "keel.capture-reconcile.v1")
        self.assertTrue(data["no_mutations"])
        self.assertEqual(reconcile["status"], "actionable")
        self.assertEqual(reconcile["results"][0]["marker"],
                         "compound-learning: pr=160 status=skipped:no-policy")
        self.assertEqual(reconcile["results"][0]["actions"][-1]["type"],
                         "close-linked-issue")

    def test_capture_reconcile_human_output_lists_dry_run_actions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            rc, out, _ = run(["capture-reconcile", config, "--root", d,
                              "--merged-pr", "160", "--linked-issue", "160=42"])

        self.assertEqual(rc, 0)
        self.assertIn("DRY-RUN: emit-capture-marker", out)
        self.assertIn("close-linked-issue:issue-42:pr-160", out)

    def test_capture_reconcile_human_output_and_blocked_exit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            path = Path(d) / "state" / "runs.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"schema_version":"keel.run-ledger.v1","record_type":"ship_run",'
                '"pull_request":{"number":160},"capture":{"marker":"not a marker"}}\n',
                encoding="utf-8",
            )
            rc, out, _ = run(["capture-reconcile", config, "--root", d,
                              "--merged-pr", "160"])

        self.assertEqual(rc, 1)
        self.assertIn("keel capture-reconcile", out)
        self.assertIn("PR #160  invalid", out)

    def test_capture_reconcile_reports_bad_mapping_and_reconcile_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            with patch("keel.cli.capture.reconcile_session",
                       side_effect=cli.capture.CaptureError("bad reconcile")):
                rc_error, _, err_error = run([
                    "capture-reconcile", config, "--root", d,
                    "--merged-pr", "160",
                ])

        with self.assertRaisesRegex(cli.argparse.ArgumentTypeError, "linked issue mapping"):
            cli._parse_pr_issue_mapping("bad")
        with self.assertRaisesRegex(cli.argparse.ArgumentTypeError, "linked issue mapping"):
            cli._parse_pr_issue_mapping("160=0")
        self.assertEqual(rc_error, 1)
        self.assertIn("bad reconcile", err_error)

    def test_capture_reconcile_reports_config_and_ledger_errors(self):
        import tempfile
        rc_missing, _, err_missing = run(["capture-reconcile", "/no/such.yaml",
                                          "--merged-pr", "160"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run(["capture-reconcile", _write_raw("extends: keel\n"),
                                          "--merged-pr", "160"])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            path = Path(d) / "state" / "runs.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            rc_bad, _, err_bad = run(["capture-reconcile", config, "--root", d,
                                      "--merged-pr", "160"])

        self.assertEqual(rc_bad, 1)
        self.assertIn("invalid ledger", err_bad)

    def test_ledger_reports_missing_invalid_config_and_bad_file(self):
        import tempfile
        rc_missing, _, err_missing = run(["ledger", "/no/such.yaml"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run(["ledger", _write_raw("extends: keel\n")])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_ledger("'true'")
            path = Path(d) / "state" / "runs.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            rc_bad, _, err_bad = run(["ledger", config, "--root", d])
        self.assertEqual(rc_bad, 1)
        self.assertIn("invalid ledger", err_bad)

    def test_status_json_reads_checkpoint_and_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            rc_checkpoint, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-148",
                "--checkpoint-command", "overnight",
                "--step", "s6",
                "--issue-queue", "148",
                "--issue-queue", "146",
                "--active-issue", "148",
                "--pull-request", "168",
                "--branch", "feature/issue-148-progress-status",
                "--worktree", "worktrees/issue-148",
            ])
            rc_ship, _, _ = run([
                "ship", config, "--root", d, "--live", "--append-ledger",
                "--issue", "147",
                "--pull-request", "167",
                "--capture-status", "applied",
                "--approve-scope", "filesystem,git,github",
                "--operator", "tester",
            ])
            rc, out, _ = run(["status", config, "--root", d, "--json"])

        self.assertEqual(rc_checkpoint, 0)
        self.assertEqual(rc_ship, 0)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["contract"]["schema_version"], "keel.progress-status.v1")
        self.assertEqual(payload["snapshot"]["status"], "waiting")
        self.assertEqual(payload["snapshot"]["current"]["wait_reason"], "ci")
        self.assertEqual(payload["snapshot"]["history"]["counts"]["shipped"], 1)
        self.assertEqual(payload["snapshot"]["capture_health"]["status"], "clean")
        self.assertEqual(payload["snapshot"]["next"]["issue"], 146)

    def test_status_human_output_no_active_run(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            rc, out, _ = run(["status", config, "--root", d])

        self.assertEqual(rc, 0)
        self.assertIn("keel status", out)
        self.assertIn("no-active-run", out)
        self.assertIn("capture       : clean", out)
        self.assertIn("capture gaps  : 0", out)
        self.assertIn("orphans       : 0", out)
        self.assertIn("next          : -", out)

    def test_status_flags_live_branch_and_pr_orphans(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-148", "--checkpoint-command", "overnight",
                "--step", "s6", "--issue-queue", "148", "--active-issue", "148",
                "--pull-request", "168", "--branch", "feature/issue-148",
            ])
            rc, out, _ = run([
                "status", config, "--root", d, "--json",
                "--live-branch", "feature/issue-148",
                "--live-branch", "feature/orphan",
                "--live-pr", "168", "--live-pr", "999",
            ])
        self.assertEqual(rc, 0)
        orphans = json.loads(out)["snapshot"]["orphans"]
        self.assertEqual(orphans["branches"], ["feature/orphan"])
        self.assertEqual(orphans["pull_requests"], [999])

    def test_status_human_output_renders_orphans(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            rc, out, _ = run([
                "status", config, "--root", d,
                "--live-branch", "feature/orphan", "--live-pr", "999",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("orphans       : 2", out)
        self.assertIn("orphan branch : feature/orphan", out)
        self.assertIn("orphan pr     : #999", out)

    def test_status_reports_missing_invalid_config_checkpoint_and_ledger(self):
        import tempfile
        rc_missing, _, err_missing = run(["status", "/no/such.yaml"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_invalid, _, err_invalid = run(["status", _write_raw("extends: keel\n")])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            checkpoint_path = Path(d) / "state" / "checkpoint.json"
            checkpoint_path.parent.mkdir(parents=True)
            checkpoint_path.write_text("{", encoding="utf-8")
            rc_bad_checkpoint, _, err_bad_checkpoint = run(["status", config, "--root", d])

        self.assertEqual(rc_bad_checkpoint, 1)
        self.assertIn("invalid checkpoint", err_bad_checkpoint)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_state_paths("'true'")
            ledger_path = Path(d) / "state" / "runs.jsonl"
            ledger_path.parent.mkdir(parents=True)
            ledger_path.write_text("{", encoding="utf-8")
            rc_bad_ledger, _, err_bad_ledger = run(["status", config, "--root", d])

        self.assertEqual(rc_bad_ledger, 1)
        self.assertIn("invalid ledger", err_bad_ledger)

    def test_checkpoint_write_read_and_resume_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc, out, _ = run([
                "checkpoint", config, "--root", d, "--write", "--json",
                "--run-id", "RUN-149",
                "--checkpoint-command", "ship",
                "--step", "s6",
                "--target", "issue #149",
                "--issue-queue", "149",
                "--active-issue", "149",
                "--branch", "feat/issue-149-resume",
                "--worktree", "worktrees/issue-149",
                "--pull-request", "170",
                "--head-sha", "abc123",
                "--completed-step", "s0",
                "--completed-step", "s1",
                "--last-check", "ci",
                "--jury-mode", "gating",
                "--stop-reason", "waiting on CI",
            ])
            checkpoint_path = Path(d) / "state" / "checkpoint.json"
            rc_read, out_read, _ = run(["checkpoint", config, "--root", d, "--json"])
            rc_resume, out_resume, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-pr-state", "open",
                "--live-worktree-state", "present",
            ])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["path"], str(checkpoint_path.resolve()))
        self.assertEqual(data["checkpoint"]["position"]["current_step"], "s6")
        self.assertEqual(data["checkpoint"]["resume"]["action"], "recheck-ci")
        self.assertEqual(rc_read, 0)
        read = json.loads(out_read)
        self.assertEqual(read["status"], "present")
        self.assertEqual(read["checkpoint"]["run_id"], "RUN-149")
        self.assertEqual(read["checkpoint"]["state"]["jury_mode"], "gating")
        self.assertEqual(rc_resume, 0)
        plan = json.loads(out_resume)["resume_plan"]
        self.assertEqual(plan["status"], "waiting-on-ci")
        self.assertEqual(plan["next_step"], "s6")
        self.assertEqual(plan["resume_action"], "recheck-ci")

    def test_resume_after_merge_before_capture(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s10",
                "--pull-request", "170",
                "--merge-state", "merged",
                "--capture-state", "not-started",
                "--close-state", "not-started",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-pr-state", "merged",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 0)
        plan = json.loads(out)["resume_plan"]
        self.assertEqual(plan["status"], "needs-capture")
        self.assertEqual(plan["next_step"], "s11")
        self.assertEqual(plan["resume_action"], "run-or-verify-capture")

    def test_resume_after_merge_ignores_missing_worktree(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s10",
                "--worktree", "worktrees/issue-149",
                "--pull-request", "170",
                "--merge-state", "merged",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-pr-state", "merged",
                "--live-worktree-state", "missing",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 0)
        plan = json.loads(out)["resume_plan"]
        self.assertEqual(plan["status"], "needs-capture")
        self.assertEqual(plan["next_step"], "s11")

    def test_resume_ambiguous_live_state_returns_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s7",
                "--worktree", "worktrees/issue-149",
                "--pull-request", "170",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-worktree-state", "missing",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 1)
        plan = json.loads(out)["resume_plan"]
        self.assertEqual(plan["status"], "ambiguous")
        self.assertFalse(plan["can_resume"])
        self.assertTrue(plan["warnings"])

    def test_resume_closed_unmerged_pr_returns_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s7",
                "--pull-request", "170",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d, "--json",
                "--live-pr-state", "closed",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 1)
        plan = json.loads(out)["resume_plan"]
        self.assertEqual(plan["status"], "ambiguous")
        self.assertIn("closed", plan["reason"])

    def test_checkpoint_human_missing_present_and_resume_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_missing, out_missing, _ = run(["checkpoint", config, "--root", d])
            rc_resume_missing, out_resume_missing, _ = run(["resume", config, "--root", d])
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s2",
            ])
            rc_present, out_present, _ = run(["checkpoint", config, "--root", d])
            rc_resume, out_resume, _ = run(["resume", config, "--root", d])

        self.assertEqual(rc_missing, 0)
        self.assertIn("missing", out_missing)
        self.assertEqual(rc_resume_missing, 0)
        self.assertIn("no-checkpoint", out_resume_missing)
        self.assertEqual(rc_write, 0)
        self.assertEqual(rc_present, 0)
        self.assertIn("RUN-149", out_present)
        self.assertIn("safe boundary", out_present)
        self.assertEqual(rc_resume, 0)
        self.assertIn("ready", out_resume)
        self.assertIn("ensure-branch-and-worktree", out_resume)

    def test_checkpoint_reports_missing_invalid_config_and_bad_file(self):
        import tempfile
        rc_missing, _, err_missing = run(["checkpoint", "/no/such.yaml"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)

        rc_checkpoint_invalid, _, err_checkpoint_invalid = run([
            "checkpoint", _write_raw("extends: keel\n")
        ])
        self.assertEqual(rc_checkpoint_invalid, 1)
        self.assertIn("invalid keel config", err_checkpoint_invalid)

        rc_resume_missing, _, err_resume_missing = run(["resume", "/no/such.yaml"])
        self.assertEqual(rc_resume_missing, 1)
        self.assertIn("no such config", err_resume_missing)

        rc_invalid, _, err_invalid = run(["resume", _write_raw("extends: keel\n")])
        self.assertEqual(rc_invalid, 1)
        self.assertIn("invalid keel config", err_invalid)

        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            path = Path(d) / "state" / "checkpoint.json"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            rc_bad_checkpoint, _, err_bad_checkpoint = run(["checkpoint", config, "--root", d])
            rc_bad_resume, _, err_bad_resume = run(["resume", config, "--root", d])
            with patch("keel.cli.checkpoint.build_checkpoint_record",
                       side_effect=cli.checkpoint.CheckpointError("cannot checkpoint")):
                rc_bad_write, _, err_bad_write = run([
                    "checkpoint", config, "--root", d, "--write",
                    "--run-id", "RUN-149",
                    "--step", "s0",
                ])

        self.assertEqual(rc_bad_checkpoint, 1)
        self.assertIn("invalid checkpoint", err_bad_checkpoint)
        self.assertEqual(rc_bad_resume, 1)
        self.assertIn("invalid checkpoint", err_bad_resume)
        self.assertEqual(rc_bad_write, 1)
        self.assertIn("cannot checkpoint", err_bad_write)

    def test_resume_human_ambiguous_prints_warning(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            config = _write_config_with_checkpoint("'true'")
            rc_write, _, _ = run([
                "checkpoint", config, "--root", d, "--write",
                "--run-id", "RUN-149",
                "--step", "s7",
                "--worktree", "worktrees/issue-149",
            ])
            rc, out, _ = run([
                "resume", config, "--root", d,
                "--live-worktree-state", "missing",
            ])

        self.assertEqual(rc_write, 0)
        self.assertEqual(rc, 1)
        self.assertIn("ambiguous", out)
        self.assertIn("warning", out)

    def test_ship_human_append_ledger_dry_run_message(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config_with_ledger("'true'"),
                              "--root", d, "--append-ledger"])
        self.assertEqual(rc, 0)
        self.assertIn("run ledger", out)
        self.assertIn("ledger append : dry-run/no-live", out)

    def test_ship_live_json_allows_ready_issue_after_consent(self):
        import tempfile
        body = (
            "## Problem\nAgents need issue readiness.\n\n"
            "## Deliverable\nExpose intake status.\n\n"
            "## Acceptance criteria\n"
            "- Live preflight continues for ready issues.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'true'"), "--root", d,
                              "--live", "--json", "--issue-title", "Add intake",
                              "--issue-body", body,
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["issue_intake"]["status"], "ready")
        self.assertIn("result", data)

    def test_failing_gate_blocks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", _write_config("'false'"), "--root", d])
        self.assertEqual(rc, 1)
        self.assertIn("BLOCK", out)

    def test_missing_config(self):
        rc, _, err = run(["ship", "/no/such.yaml"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config(self):
        rc, _, err = run(["ship", _write_raw("extends: keel\n")])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_bogus_gate_errors(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", p, "--root", d])
        self.assertEqual(rc, 1)
        self.assertTrue(err)

    def test_missing_required_capability_blocks_before_ship(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", p, "--root", d])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", err)

    def test_pr_ci_requires_transport_check_runs(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
            runtime.Capability("github-mcp", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, _, err = run(["ship", _write_config("'true'"), "--root", d, "--pr", "7"])
        self.assertEqual(rc, 1)
        self.assertIn("missing required GitHub transport capability: check_runs", err)

    def test_unloadable_extension_warned(self):
        import tempfile
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "extensions:\n  tester: [missing.md]\nextensions_dir: .keel/extensions\n")
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", p, "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)


class TestShipCiVisibility(unittest.TestCase):
    """The ship line must show what CI actually did, not just whether it was red (#675)."""

    GH_UP = runtime.CapabilityReport((
        runtime.Capability("shell", True, "ok", "test"),
        runtime.Capability("git", True, "ok", "test"),
        runtime.Capability("worktree", True, "ok", "test"),
        runtime.Capability("gh", True, "ok", "test"),
        runtime.Capability("gh-auth", True, "ok", "test"),
        runtime.Capability("github-mcp", False, "missing", "test"),
    ))

    def _ship(self, conclusion, names, *, config=None, workflows=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d, \
                patch("keel.cli.runtime.detect", return_value=self.GH_UP), \
                patch("keel.cli.github.ci_conclusion", return_value=conclusion), \
                patch("keel.cli.github.ci_check_names", return_value=names), \
                patch("keel.cli.github.ci_workflow_names", return_value=workflows):
            return run(["ship", config or _write_config("'true'"), "--root", d, "--pr", "7"])

    def test_zero_checks_is_printed_as_zero_not_as_passing(self):
        rc, out, _ = self._ship("", [])
        # Blocking reaches the exit code too, so a script driving ship cannot
        # treat "nothing ran" as a clean run either.
        self.assertEqual(rc, 1)
        self.assertIn("NO CHECKS RAN", out)
        self.assertIn("nothing verified this commit", out)
        self.assertIn("BLOCK", out.upper())

    def test_passing_prints_the_check_count(self):
        # "passing" alone was indistinguishable from "0 checks"; the count is the fact.
        rc, out, _ = self._ship("SUCCESS", ["CI", "CodeQL"])
        self.assertEqual(rc, 0)
        self.assertIn("passing (2 checks)", out)

    def test_one_check_is_singular(self):
        rc, out, _ = self._ship("SUCCESS", ["CI"])
        self.assertEqual(rc, 0)
        self.assertIn("passing (1 check)", out)

    def test_count_is_omitted_when_the_names_could_not_be_read(self):
        # The conclusion call succeeded and the names call did not. Print no count
        # rather than a wrong one — "0 checks" here would be a fact about gh, not
        # about the PR, which is the confusion this whole change removes.
        rc, out, _ = self._ship("SUCCESS", None)
        self.assertEqual(rc, 0)
        self.assertIn("ci            : passing", out)
        self.assertNotIn("check)", out)
        self.assertNotIn("checks)", out)

    def test_a_declared_workflow_that_never_ran_is_named(self):
        config = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "  ci_workflows:\n    CI: '**'\n    CodeQL: '**'\n"
        )
        # Job names reported, workflow "CI" ran, "CodeQL" never did.
        rc, out, _ = self._ship("SUCCESS", ["test (py3.13 / ubuntu-latest)"],
                                config=config, workflows=["CI"])
        self.assertEqual(rc, 1)
        self.assertIn("ci MISSING", out)
        self.assertIn("CodeQL", out)
        self.assertIn("declared, never ran", out)


class TestResumeObservation(unittest.TestCase):
    """`keel resume` observes git/gh instead of being told (#635)."""

    def _rec(self, **kw):
        from keel import checkpoint
        base = dict(run_id="ship-1", command="ship", current_step="s9",
                    base_branch="main", branch="b", worktree="/tmp/keel-gone-xyz",
                    pull_request=7, head_sha="a" * 40)
        base.update(kw)
        return checkpoint.build_checkpoint_record(**base)

    def _args(self, **kw):
        base = dict(root=".", live_pr_state=None, live_worktree_state=None,
                    no_observe=False)
        base.update(kw)
        return Namespace(**base)

    def test_an_explicit_flag_wins_over_observation(self):
        # The offline / fixture path stays available.
        with patch("keel.cli.github.pr_state", return_value="merged") as probe:
            observed = cli._observe_live_state(
                self._args(live_pr_state="open"), self._rec())
        self.assertEqual(observed["pr"], "open")
        probe.assert_not_called()

    def test_no_observe_reads_nothing(self):
        with patch("keel.cli.github.pr_state") as probe, \
                patch("keel.cli.git.rev_parse") as head:
            observed = cli._observe_live_state(self._args(no_observe=True), self._rec())
        probe.assert_not_called()
        head.assert_not_called()
        self.assertEqual((observed["pr"], observed["worktree"]), ("unknown", "unknown"))

    def test_an_unreadable_gh_is_unknown_not_missing(self):
        """The #675 confusion, not repeated: gh failing says nothing about the PR."""
        with patch("keel.cli.github.pr_state", return_value=None), \
                patch("keel.cli.git.rev_parse", return_value=None):
            observed = cli._observe_live_state(self._args(), self._rec())
        self.assertEqual(observed["pr"], "unknown")

    def test_a_deleted_worktree_is_observed_as_missing(self):
        with patch("keel.cli.github.pr_state", return_value="open"), \
                patch("keel.cli.git.rev_parse", return_value=None):
            observed = cli._observe_live_state(self._args(), self._rec())
        self.assertEqual(observed["worktree"], "missing")

    def test_an_existing_worktree_is_observed_as_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, \
                patch("keel.cli.github.pr_state", return_value="open"), \
                patch("keel.cli.git.rev_parse", return_value="b" * 40):
            observed = cli._observe_live_state(self._args(), self._rec(worktree=d))
        self.assertEqual(observed["worktree"], "present")
        self.assertEqual(observed["head_sha"], "b" * 40)

    def test_the_head_is_read_from_the_recorded_worktree_when_it_exists(self):
        # Reading it from the main checkout would compare against the wrong branch.
        import tempfile
        with tempfile.TemporaryDirectory() as d, \
                patch("keel.cli.github.pr_state", return_value="open"), \
                patch("keel.cli.git.rev_parse", return_value="b" * 40) as head:
            cli._observe_live_state(self._args(), self._rec(worktree=d))
        self.assertEqual(head.call_args.kwargs["cwd"], d)

    def test_a_missing_worktree_falls_back_to_the_root_for_the_head(self):
        with patch("keel.cli.github.pr_state", return_value="open"), \
                patch("keel.cli.git.rev_parse", return_value="b" * 40) as head:
            cli._observe_live_state(self._args(root="/some/root"), self._rec())
        self.assertEqual(head.call_args.kwargs["cwd"], "/some/root")

    def test_no_checkpoint_observes_nothing(self):
        with patch("keel.cli.github.pr_state") as probe:
            observed = cli._observe_live_state(self._args(), None)
        probe.assert_not_called()
        self.assertIsNone(observed["head_sha"])

    def test_a_checkpoint_without_identifiers_is_safe(self):
        with patch("keel.cli.github.pr_state") as probe, \
                patch("keel.cli.git.rev_parse") as head:
            observed = cli._observe_live_state(
                self._args(), self._rec(pull_request=None, worktree=None, branch=None))
        probe.assert_not_called()
        head.assert_not_called()
        self.assertEqual(observed["pr"], "unknown")


class TestVerifyMergeCommand(unittest.TestCase):
    """keel verify-merge: loud on the silent-revert shape, quiet otherwise (#561)."""

    WINDOW = {"branched_at": "2026-07-08T23:02:16Z", "merged_at": "2026-07-10T15:19:57Z",
              "base": "main", "merge_commit": "7c140f3"}

    def _run(self, *, window=WINDOW, others=None, files_by_pr=None, commit_files=None,
             argv_extra=()):
        files_by_pr = files_by_pr or {}
        with patch("keel.cli.github.pr_merge_window", return_value=window), \
                patch("keel.cli.github.prs_merged_between", return_value=others), \
                patch("keel.cli.github.commit_files", return_value=commit_files), \
                patch("keel.cli.github.pr_files",
                      side_effect=lambda pr, **kw: files_by_pr.get(pr)):
            return run(["verify-merge", _write_config("'true'"), "--root", ".",
                        "--pr", "543", *argv_extra])

    def test_the_historical_incident_is_flagged(self):
        """#543 merged after #550 and wrote to #550's files."""
        rc, out, _ = self._run(
            others=[550],
            files_by_pr={543: ["website/index.html", "src/keel/github.py"],
                         550: ["src/keel/github.py"]},
            commit_files=["website/index.html", "src/keel/github.py"],
        )
        self.assertEqual(rc, 1, "drift must be loud in the exit code too")
        self.assertIn("drift", out)
        self.assertIn("src/keel/github.py", out)
        self.assertIn("#550", out)

    def test_a_clean_merge_is_quiet(self):
        rc, out, _ = self._run(
            others=[],
            files_by_pr={543: ["website/index.html"]},
            commit_files=["website/index.html"],
        )
        self.assertEqual(rc, 0)
        self.assertIn("clean", out)

    def test_the_pr_never_overtakes_itself(self):
        # Its own number appearing in the window must not flag every file.
        rc, out, _ = self._run(
            others=[543],
            files_by_pr={543: ["a.py"]},
            commit_files=["a.py"],
        )
        self.assertEqual(rc, 0)
        self.assertIn("clean", out)

    def test_an_unmerged_pr_is_unknown_not_clean(self):
        rc, out, _ = self._run(window=None)
        self.assertEqual(rc, 0)
        self.assertIn("unknown", out)
        self.assertIn("no merge commit", out)

    def test_json_output(self):
        rc, out, _ = self._run(
            others=[550],
            files_by_pr={543: ["a.py"], 550: ["a.py"]},
            commit_files=["a.py"],
            argv_extra=("--json",),
        )
        payload = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(payload["status"], "drift")
        self.assertEqual(payload["overtaken"], {"a.py": 550})
        self.assertEqual(payload["pull_request"], 543)

    def test_an_explicit_merge_sha_skips_the_lookup(self):
        rc, out, _ = self._run(
            others=[], files_by_pr={543: ["a.py"]}, commit_files=["a.py"],
            argv_extra=("--merge-sha", "deadbeef"),
        )
        self.assertEqual(rc, 0)
        self.assertIn("clean", out)

    def test_an_unreadable_overtaking_list_does_not_claim_clean_falsely(self):
        # gh failing yields no overtaking data; the scope check still applies.
        rc, out, _ = self._run(
            others=None,
            files_by_pr={543: ["a.py"]},
            commit_files=["a.py", "b.py"],
        )
        self.assertEqual(rc, 0)
        self.assertIn("out-of-scope", out)

    def test_a_bad_config_path_fails_cleanly(self):
        rc, _, err = run(["verify-merge", "/nope/x.yaml", "--pr", "1"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_an_invalid_config_fails_cleanly(self):
        bad = _write_raw("extends: nope\ncore_version: '^0.1'\nbase_branch: main\n")
        rc, _, err = run(["verify-merge", bad, "--pr", "1"])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)


class TestMergeCheckpointGate(unittest.TestCase):
    """The s10 checkpoint gate in `keel merge` (audit GAP-13)."""

    def _run_merge(self, *, config, root, extra=(), run_id="RUN-1"):
        fake_report = _merge_capability_report()
        with (
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": run_id})),
            patch("keel.cli.github.merge_pr",
                  return_value=_proc("merged")),
        ):
            argv = [
                "merge", config, "--root", root, "--pr", "123",
                "--approve-scope", "filesystem,git,github", "--operator", "tester",
                "--json",
            ]
            argv += list(extra)
            return run(argv)

    def _write_checkpoint(self, root, config_path, *, run_id="RUN-1", step="s10"):
        config = cli.cfg.load_config(config_path)
        path = cli.checkpoint.resolve_path(root, config)
        record = cli.checkpoint.build_checkpoint_record(
            run_id=run_id, command="ship", current_step=step, base_branch="main",
            branch="feat/x", pull_request=123,
        )
        cli.checkpoint.write_checkpoint(path, record)

    def test_no_checkpoint_config_merges_advisory_skip(self):
        # keel.yaml (the default _merge_args config) has no reports.checkpoint.
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run_merge(config=str(PROJECTS / "keel.yaml"), root=d)
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(data["merged"])
        self.assertFalse(data["checkpoint_gate"]["enforced"])
        self.assertEqual(data["checkpoint_gate"]["status"], "advisory-skip")

    def test_merge_proceeds_with_covering_checkpoint_at_s10(self):
        config = _write_config_with_checkpoint("'true'")
        with tempfile.TemporaryDirectory() as d:
            self._write_checkpoint(d, config, run_id="RUN-1", step="s10")
            rc, out, _ = self._run_merge(config=config, root=d)
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(data["merged"])
        self.assertTrue(data["checkpoint_gate"]["enforced"])
        self.assertEqual(data["checkpoint_gate"]["status"], "covered")

    def test_merge_refused_when_checkpoint_missing(self):
        config = _write_config_with_checkpoint("'true'")
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run_merge(config=config, root=d)
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertFalse(data["merged"])
        self.assertEqual(data["checkpoint_gate"]["status"], "missing")
        self.assertIn("no current checkpoint for run RUN-1 at step s10", data["reason"])

    def test_merge_refused_when_checkpoint_is_stale_step(self):
        config = _write_config_with_checkpoint("'true'")
        with tempfile.TemporaryDirectory() as d:
            self._write_checkpoint(d, config, run_id="RUN-1", step="s6")
            rc, out, _ = self._run_merge(config=config, root=d)
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertFalse(data["merged"])
        self.assertEqual(data["checkpoint_gate"]["status"], "stale-step")
        self.assertIn("run is at s6", data["reason"])

    def test_merge_refused_when_no_run_id_available(self):
        config = _write_config_with_checkpoint("'true'")
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run_merge(config=config, root=d, run_id=None)
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["checkpoint_gate"]["status"], "missing")
        self.assertIn("no run-id is available", data["reason"])

    def test_explicit_run_id_overrides_gates_pass_run_id(self):
        config = _write_config_with_checkpoint("'true'")
        with tempfile.TemporaryDirectory() as d:
            self._write_checkpoint(d, config, run_id="RUN-XYZ", step="s10")
            rc, out, _ = self._run_merge(
                config=config, root=d, run_id="RUN-1", extra=["--run-id", "RUN-XYZ"]
            )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(data["checkpoint_gate"]["run_id"], "RUN-XYZ")
        self.assertEqual(data["checkpoint_gate"]["status"], "covered")

    def test_no_checkpoint_gate_bypass_records_operator(self):
        config = _write_config_with_checkpoint("'true'")
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run_merge(
                config=config, root=d, extra=["--no-checkpoint-gate"]
            )
        data = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(data["merged"])
        gate = data["checkpoint_gate"]
        self.assertEqual(gate["status"], "bypassed")
        self.assertTrue(gate["bypassed"])
        self.assertEqual(gate["operator"], "tester")

    def test_merge_without_run_id_autostamps_gates_run_id(self):
        config = _write_config_with_checkpoint("'true'")
        with tempfile.TemporaryDirectory() as d:
            self._write_checkpoint(d, config, run_id="RUN-FALLBACK", step="s10")
            with patch("keel.cli._autostamp") as mock_stamp:
                rc, out, _ = self._run_merge(config=config, root=d, run_id="RUN-FALLBACK")
            self.assertEqual(rc, 0)
            mock_stamp.assert_called_once()
            args, kwargs = mock_stamp.call_args
            # Positional arguments: (config, root, command, run_id, phase)
            self.assertEqual(args[3], "RUN-FALLBACK")
            self.assertEqual(kwargs.get("status"), "merged")

    def test_no_checkpoint_gate_bypass_requires_named_operator(self):
        config = _write_config_with_checkpoint("'true'")
        fake_report = _merge_capability_report()
        with (
            tempfile.TemporaryDirectory() as d,
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
        ):
            rc, out, _ = run([
                "merge", config, "--root", d, "--pr", "123",
                "--approve-scope", "filesystem,git,github",
                "--no-checkpoint-gate", "--json",
            ])
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["checkpoint_gate"]["status"], "bypass-refused")
        self.assertIn("requires a named --operator", data["reason"])

    def test_human_output_shows_checkpoint_gate_status(self):
        config = _write_config_with_checkpoint("'true'")
        fake_report = _merge_capability_report()
        with (
            tempfile.TemporaryDirectory() as d,
            patch("keel.cli.runtime.detect", return_value=fake_report),
            patch("keel.cli.window.is_merge_open", return_value=True),
            patch("keel.cli.github.pr_merge_snapshot",
                  return_value=_json_result({
                      "headRefOid": "abc",
                      "mergeStateStatus": "CLEAN",
                      "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                  })),
            patch("keel.cli._verify_merge_evidence", return_value={
                "enforced": True,
                "verification": {"status": "pass", "missing": []},
            }),
            patch("keel.cli.ledger.read_records", return_value=[]),
            patch("keel.cli.ledger.gates_pass_for_head",
                  return_value=(True, {"run_id": "RUN-1"})),
        ):
            rc, out, _ = run([
                "merge", config, "--root", d, "--pr", "123",
                "--approve-scope", "filesystem,git,github", "--operator", "tester",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("checkpoint: missing", out)

    def test_invalid_checkpoint_file_is_reported(self):
        config = _write_config_with_checkpoint("'true'")
        with tempfile.TemporaryDirectory() as d:
            cfg = cli.cfg.load_config(config)
            path = cli.checkpoint.resolve_path(d, cfg)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ not json", encoding="utf-8")
            rc, out, _ = self._run_merge(config=config, root=d)
        data = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual(data["checkpoint_gate"]["status"], "invalid")
        self.assertIn("invalid checkpoint", data["reason"])


class TestStandaloneCommands(unittest.TestCase):
    def test_implement_json_dry_run_contract(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--dry-run", "--json",
                              "--delegate", "codex"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "implement")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "standalone-step")
        self.assertEqual(data["contract"]["operator_consent"]["status"],
                         "not-required-dry-run")
        self.assertIn("git", data["contract"]["required_capabilities"])
        self.assertEqual(data["result"]["target"], "issue #76")
        self.assertFalse(data["result"]["handoff"]["merges"])
        self.assertEqual(data["result"]["implementer"]["selected"], "codex")

    def test_implement_live_blocks_without_consent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["contract"]["operator_consent"]["status"], "missing")
        self.assertIn("filesystem", data["contract"]["operator_consent"]["missing_scope"])
        self.assertIn("result", data)

    def test_implement_live_accepts_consent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live", "--json",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["mode"], "live")
        self.assertEqual(data["contract"]["operator_consent"]["status"], "approved")

    def test_implement_live_json_blocks_non_ready_issue(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live", "--json",
                              "--issue-title", "Ambiguous implement",
                              "--issue-body", "TBD.",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["contract"]["issue_intake"]["status"], "needs-input")
        self.assertIn("result", data)

    def test_implement_live_json_allows_ready_issue(self):
        import tempfile
        body = (
            "## Problem\nImplementers need intake context.\n\n"
            "## Deliverable\nExpose readiness to implement.\n\n"
            "## Acceptance criteria\n"
            "- Ready implement preflight succeeds.\n"
        )
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live", "--json",
                              "--issue-title", "Implement intake",
                              "--issue-body", body,
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["issue_intake"]["status"], "ready")
        self.assertIn("result", data)

    def test_implement_live_human_blocks_non_ready_issue(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live",
                              "--issue-title", "Ambiguous implement",
                              "--issue-body", "TBD.",
                              "--approve-scope", "filesystem,git,github",
                              "--operator", "tester"])
        self.assertEqual(rc, 1)
        self.assertIn("issue intake: needs-input", err)
        self.assertIn("question:", err)

    def test_ci_check_json_contract_is_read_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ci-check", _write_config("'true'"), "--root", d,
                              "--pr", "104", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "ci-check")
        self.assertEqual(
            data["contract"]["workflow_profile"]["profile"],
            "standalone-diagnostic",
        )
        self.assertEqual(data["contract"]["operator_consent"]["status"],
                         "not-required-read-only")
        self.assertFalse(data["contract"]["operator_consent"]["would_require_operator_consent"])
        self.assertEqual(data["result"]["target"], "PR #104")
        self.assertTrue(data["result"]["diagnostics"]["read_only"])
        self.assertTrue(data["result"]["routing"]["never_direct_merge"])

    def test_ci_check_does_not_inherit_project_mutation_requirements(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n"
                       "  ci_workflows:\n    ci: CI\n")
        rc, out, _ = run(["ci-check", p, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertNotIn("release-publish", data["contract"]["required_capabilities"])
        self.assertFalse(data["contract"]["operator_consent"]["would_require_operator_consent"])

    def test_morning_json_contract_surfaces_health_reports_and_deferrals(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", False, "missing", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["morning", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d, "--since", "yesterday", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "morning")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "daily-brief")
        self.assertEqual(data["result"]["target"], "since yesterday")
        self.assertEqual(data["result"]["brief"]["reports"]["morning"]["path"],
                         "reports/morning/")
        self.assertEqual(data["result"]["brief"]["health_providers"][0]["status"],
                         "unavailable")
        self.assertEqual(data["result"]["brief"]["missing_optional_policy"],
                         "unavailable-not-success")

    def test_morning_does_not_inherit_project_mutation_requirements(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n"
                       "policy_pack:\n  name: x\n  health_providers:\n"
                       "    status:\n      kind: project-command\n"
                       "      command: .keel/health/status\n"
                       "      optional_capabilities: [shell]\n")
        rc, out, _ = run(["morning", p, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertNotIn("release-publish", data["contract"]["required_capabilities"])
        self.assertIn("shell", data["contract"]["optional_capabilities"])
        self.assertFalse(data["result"]["execution"]["runs_project_health_commands"])

    def test_morning_requirement_ignores_non_map_health_provider(self):
        config = cli.cfg.ProjectConfig(
            extends="keel",
            core_version="^0.1",
            base_branch="main",
            knobs=cli.cfg.Knobs(build_gate_cmd="true"),
            policy_pack={
                "name": "edge",
                "health_providers": {
                    "invalid": "not-a-provider-map",
                    "valid": {
                        "kind": "external",
                        "required_capabilities": ["firebase"],
                    },
                },
            },
        )
        requirement = runtime.morning_capability_requirement(config)
        self.assertEqual(requirement.required, ("firebase",))

    def test_wrap_json_contract_surfaces_session_reports_and_worktree_guard(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["wrap", str(PROJECTS / "example-flutter.yaml"),
                              "feat: finish session", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "wrap")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "session-wrap")
        self.assertEqual(data["result"]["target"], "feat: finish session")
        self.assertTrue(
            data["result"]["session"]["wrap"]["workspace_preflight"]
            ["must_run_from_linked_worktree"]
        )
        self.assertFalse(data["result"]["execution"]["creates_prs"])

    def test_overnight_json_contract_uses_ship_window_and_handoff(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["overnight", str(PROJECTS / "example-flutter.yaml"),
                              "6", "--max", "3", "--root", d, "--review-comments",
                              "summary", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "overnight")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "session-overnight")
        self.assertEqual(data["result"]["target"], "6h session (max 3)")
        self.assertTrue(
            data["result"]["session"]["overnight"]["mode_source"]["shared_with_ship"]
        )
        self.assertTrue(
            data["result"]["session"]["overnight"]["ship_handoff"]
            ["passes_operator_consent_scope"]
        )
        self.assertFalse(data["result"]["execution"]["merges"])

    def test_work_block_json_contract_surfaces_shared_queue_primitive(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["work-block", str(PROJECTS / "example-flutter.yaml"),
                              "146", "172", "--max", "2", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "work-block")
        self.assertEqual(
            data["contract"]["workflow_profile"]["profile"],
            "session-work-block-daytime",
        )
        self.assertEqual(data["result"]["target"], "issues #146, #172 (max 2)")
        self.assertEqual(data["result"]["session"]["work_block"]["mode"], "daytime")
        self.assertTrue(
            data["result"]["session"]["daytime"]["ship_handoff"]
            ["refreshes_readiness_between_issues"]
        )
        self.assertIn("pr_open_not_merged",
                      data["result"]["session"]["work_block"]
                      ["final_report"]["outcome_buckets"])

    def test_work_block_queue_selector_target(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["work-block", str(PROJECTS / "example-flutter.yaml"),
                              "--queue", "priority", "--max", "3", "--target",
                              "daytime block", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["result"]["target"], "queue priority (max 3) (daytime block)")

    def test_work_block_queue_selector_without_max(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["work-block", str(PROJECTS / "example-flutter.yaml"),
                              "--queue", "priority", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["result"]["target"], "queue priority")

    def test_regression_json_contract_surfaces_scan_policy_and_issue_consent(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
            runtime.Capability("github-mcp", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["regression", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d, "--scope", "changed", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "regression")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "scan-and-file")
        self.assertIn("scan_contract", data["contract"])
        self.assertIn("worktree", data["contract"]["required_capabilities"])
        self.assertEqual(data["contract"]["operator_consent"]["status"],
                         "not-required-dry-run")
        self.assertEqual(data["result"]["target"], "scope changed")
        self.assertEqual(data["result"]["scan"]["areas"][0]["name"], "backend")
        self.assertTrue(
            data["result"]["scan"]["regression"]["scan_target"]["read_only_worktree"]
        )
        self.assertFalse(data["result"]["execution"]["writes_issues"])

    def test_review_all_day_json_contract_preserves_title_prefix_and_scope(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
            runtime.Capability("github-mcp", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["review-all-day", str(PROJECTS / "example-flutter.yaml"),
                              "7", "--root", d, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "review-all-day")
        self.assertEqual(data["contract"]["workflow_profile"]["profile"], "time-window-scan")
        self.assertEqual(data["result"]["target"], "7 day scan")
        self.assertEqual(
            data["result"]["scan"]["review_all_day"]["issue_creation"]["title_prefix"],
            "[review-all-day] ",
        )
        self.assertEqual(
            data["result"]["scan"]["review_all_day"]["span"]
            ["n_days_argument_covers_calendar_days"],
            "N+1",
        )
        self.assertFalse(data["result"]["execution"]["pushes"])

    def test_scan_commands_do_not_inherit_project_mutation_requirements(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n"
                       "policy_pack:\n  name: x\n  scan:\n"
                       "    areas:\n      core: ['src/**']\n")
        fake_report = runtime.CapabilityReport((
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=fake_report):
            rc, out, _ = run(["regression", p, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertNotIn("release-publish", data["contract"]["required_capabilities"])
        self.assertIn("git", data["contract"]["required_capabilities"])

    def test_standalone_commands_reject_non_positive_targets(self):
        with self.assertRaises(SystemExit) as raised:
            run(["implement", _write_config("'true'"), "0"])
        self.assertEqual(raised.exception.code, 2)

    def test_implement_rejects_conflicting_live_and_dry_run_flags(self):
        rc, _, err = run(["implement", _write_config("'true'"), "76",
                          "--dry-run", "--live"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot be used together", err)

    def test_implement_missing_and_invalid_config_errors(self):
        rc, _, err = run(["implement", "/no/such.yaml", "76"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

        rc, _, err = run(["implement", _write_raw("extends: keel\n"), "76"])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_implement_reports_extension_and_gate_errors(self):
        import tempfile
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "extensions:\n  tester: [missing.md]\nextensions_dir: .keel/extensions\n")
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["implement", p, "76", "--root", d, "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)

        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                       "repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        rc, _, err = run(["implement", p, "76"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown built-in gate", err)

    def test_implement_blocks_on_missing_required_capability_and_bad_scope(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n")
        rc, _, err = run(["implement", p, "76"])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", err)

        rc, _, err = run(["implement", _write_config("'true'"), "76",
                          "--live", "--approve-scope", "bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown consent scope", err)

    def test_implement_human_output_and_missing_consent_message(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--delegate", "codex"])
        self.assertEqual(rc, 0)
        self.assertIn("keel implement", out)
        self.assertIn("worktree", out)
        self.assertIn("delegate", out)
        self.assertIn("never in standalone implement", out)

        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76", "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel implement", out)
        self.assertNotIn("delegate      :", out)

        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live",
                              "--approve-scope", "filesystem,git,github",
                              "--target", "extra context"])
        self.assertEqual(rc, 0)
        self.assertIn("issue #76 (extra context)", out)
        self.assertIn("live preflight contract", out)

        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["implement", _write_config("'true'"), "76",
                              "--root", d, "--live"])
        self.assertEqual(rc, 1)
        self.assertIn("Missing approved scope", err)

    def test_ci_check_human_output_with_optional_degradation_and_target(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["ci-check", _write_config("'true'"), "--root", d,
                              "--target", "current branch"])
        self.assertEqual(rc, 0)
        self.assertIn("keel ci-check", out)
        self.assertIn("current branch", out)
        self.assertIn("degraded opt.", out)
        self.assertIn("read-only", out)

    def test_morning_human_output_with_optional_degradation(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", False, "missing", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["morning", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel morning", out)
        self.assertIn("reports", out)
        self.assertIn("health", out)
        self.assertIn("unavailable", out)
        self.assertIn("degraded opt.", out)

    def test_morning_human_output_without_unavailable_provider(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["morning", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("health", out)
        self.assertNotIn("unavailable   :", out)

    def test_wrap_work_block_and_overnight_human_output(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, wrap_out, _ = run(["wrap", str(PROJECTS / "example-flutter.yaml"),
                                   "--root", d])
            rc_work, work_out, _ = run(["work-block", str(PROJECTS / "example-flutter.yaml"),
                                        "146", "--root", d])
            rc2, overnight_out, _ = run(["overnight", str(PROJECTS / "example-flutter.yaml"),
                                         "2", "--root", d])
        self.assertEqual(rc, 0)
        self.assertEqual(rc_work, 0)
        self.assertEqual(rc2, 0)
        self.assertIn("keel wrap", wrap_out)
        self.assertIn("linked required=True", wrap_out)
        self.assertIn("ready PR", wrap_out)
        self.assertIn("keel work-block", work_out)
        self.assertIn("ship per issue", work_out)
        self.assertIn("needs-input", work_out)
        self.assertIn("keel overnight", overnight_out)
        self.assertIn("mode source", overnight_out)
        self.assertIn("no-night-merge", overnight_out)

    def test_scan_human_output(self):
        import tempfile
        fake_report = runtime.CapabilityReport((
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, out, _ = run(["regression", str(PROJECTS / "example-flutter.yaml"),
                              "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel regression", out)
        self.assertIn("areas", out)
        self.assertIn("issues only after consent", out)

        with tempfile.TemporaryDirectory() as d, patch("keel.cli.runtime.detect",
                                                       return_value=fake_report):
            rc, review_out, _ = run(["review-all-day", str(PROJECTS / "example-flutter.yaml"),
                                     "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("keel review-all-day", review_out)
        self.assertIn("[review-all-day] ", review_out)

    def test_standalone_target_combines_days_and_scope_when_present(self):
        target = cli._standalone_target(Namespace(
            issue=None,
            pr=None,
            since=None,
            scope="changed",
            days=7,
            target=None,
            title=None,
            hours=None,
        ))
        self.assertEqual(target, "7 day scan (scope changed)")

    def test_standalone_human_output_for_unknown_adapter_profile_falls_through(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            args = Namespace(
                dry_run=True,
                live=False,
                standalone_command="custom-adapter",
                path=_write_config("'true'"),
                root=d,
                pr=None,
                approve_scope=[],
                operator=None,
                target="custom target",
                json=False,
                delegate=None,
            )
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli._cmd_standalone(args)
        self.assertEqual(rc, 0)
        self.assertIn("keel custom-adapter", out.getvalue())
        self.assertIn("custom target", out.getvalue())


class TestCapabilities(unittest.TestCase):
    def test_prints_runtime_report(self):
        rc, out, _ = run(["capabilities", "--root", "."])
        self.assertEqual(rc, 0)
        self.assertIn("keel capabilities", out)
        self.assertIn("shell", out)

    def test_json_report(self):
        rc, out, _ = run(["capabilities", "--root", ".", "--json"])
        self.assertEqual(rc, 0)
        self.assertIn('"report"', out)
        self.assertIn('"github_transport"', out)
        self.assertIn('"capabilities"', out)

    def test_reports_mcp_transport_when_available(self):
        fake_report = runtime.CapabilityReport((
            runtime.Capability("github-mcp", True, "ok", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=fake_report):
            rc, out, _ = run(["capabilities"])
        self.assertEqual(rc, 0)
        self.assertIn("selected: mcp", out)
        self.assertIn("raw_actions_logs", out)

    def test_project_requirement_failure_returns_nonzero(self):
        p = _write_raw("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                       "knobs:\n  build_gate_cmd: 'true'\n"
                       "  required_capabilities: [release-publish]\n")
        rc, out, _ = run(["capabilities", "--project", p])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", out)


class TestInit(unittest.TestCase):
    def test_scaffolds_and_validates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pubspec.yaml").write_text("name: app\n")
            rc, out, _ = run(["init", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("flutter", out)
            written = Path(d) / ".keel" / "project.yaml"
            self.assertTrue(written.exists())
            # the generated config must validate
            vrc, _, _ = run(["validate", str(written)])
            self.assertEqual(vrc, 0)

    def test_refuses_existing_without_force(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            keel = Path(d) / ".keel"
            keel.mkdir()
            (keel / "project.yaml").write_text("x")
            rc, _, err = run(["init", "--root", d])
            self.assertEqual(rc, 1)
            self.assertIn("already exists", err)

    def test_force_overwrites(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            keel = Path(d) / ".keel"
            keel.mkdir()
            (keel / "project.yaml").write_text("old")
            ext = keel / "extensions/local.md"
            ext.parent.mkdir()
            ext.write_text("extension\n")
            rc, _, err = run(["init", "--root", d, "--force"])
            self.assertEqual(rc, 0)
            self.assertIn("extensions/ was not touched", err)
            self.assertIn("extends: keel", (keel / "project.yaml").read_text(encoding="utf-8"))
            self.assertEqual(ext.read_text(encoding="utf-8"), "extension\n")

    def test_wizard_mode(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("x")
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "explicit", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, out, _ = run(["init", "--root", d, "--wizard"])
            self.assertEqual(rc, 0)
            written = (Path(d) / ".keel" / "project.yaml").read_text(encoding="utf-8")
            import yaml

            config = yaml.safe_load(written)
            self.assertEqual(config["base_branch"], "develop")
            self.assertEqual(config["merge_window"], "09:00-18:00")
            self.assertEqual(config["consent_mode"], "explicit")
            # validates
            vrc, _, _ = run(["validate", str(Path(d) / ".keel" / "project.yaml")])
            self.assertEqual(vrc, 0)

    def test_wizard_rejects_invalid_consent_mode_before_write(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("x")
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "maybe", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, _, err = run(["init", "--root", d, "--wizard"])
            self.assertEqual(rc, 1)
            self.assertIn("unknown consent mode", err)
            self.assertFalse((Path(d) / ".keel" / "project.yaml").exists())

    def test_auto_mode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Cargo.toml").write_text("[package]\nname = 'demo'\n")
            git_dir = Path(d) / ".git"
            git_dir.mkdir()
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            rc, out, err = run(["init", "--root", d, "--auto"])
            self.assertEqual(rc, 0, err)
            self.assertIn("keel init --auto", out)
            self.assertIn("stack        : rust (rust)", out)
            self.assertIn("build gate   : cargo test", out)
            self.assertIn("lint gate    : cargo clippy", out)
            vrc, _, _ = run(["validate", str(Path(d) / ".keel" / "project.yaml")])
            self.assertEqual(vrc, 0)

    def test_auto_mode_generic_stack(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = run(["init", "--root", d, "--auto"])
            self.assertEqual(rc, 0, err)
            self.assertIn("keel init --auto", out)
            self.assertIn("stack        : generic (generic)", out)
            self.assertNotIn("lint gate", out)
            vrc, _, _ = run(["validate", str(Path(d) / ".keel" / "project.yaml")])
            self.assertEqual(vrc, 0)


class TestSetup(unittest.TestCase):
    def test_scaffolds_installs_validates_and_plans(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
            rc, out, err = run(["setup", "--root", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("keel setup", out)
            self.assertIn("detected stack: python", out)
            self.assertIn("validate     : OK", out)
            self.assertIn("plan         :", out)
            self.assertTrue((Path(d) / ".keel/project.yaml").exists())
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())
            self.assertTrue((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())
            # A fresh setup scaffolds the runtime gitignore and reports it.
            self.assertIn("gitignore    : scaffolded", out)
            self.assertTrue((Path(d) / ".keel/.gitignore").exists())

    def test_existing_runtime_gitignore_is_not_reported_again(self):
        import tempfile

        from keel import workspace
        with tempfile.TemporaryDirectory() as d:
            keel = Path(d) / ".keel"
            keel.mkdir()
            # Pre-seed a complete runtime gitignore so setup leaves it untouched.
            workspace.ensure_runtime_gitignore(keel)
            rc, out, err = run(["setup", "--root", d, "--adapter-target", "claude"])
            self.assertEqual(rc, 0, err)
            self.assertNotIn("gitignore    :", out)

    def test_reuses_existing_config_without_force(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".keel/project.yaml"
            target.parent.mkdir()
            text = (
                "extends: keel\ncore_version: '^0.6'\nrepo: existing\nbase_branch: develop\n"
                "knobs:\n  build_gate_cmd: 'true'\n"
            )
            target.write_text(text)
            rc, out, err = run(["setup", "--root", d, "--adapter-target", "claude"])
            self.assertEqual(rc, 0, err)
            self.assertIn("using existing", out)
            self.assertIn("extensions   : preserved", out)
            self.assertEqual(target.read_text(encoding="utf-8"), text)
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())
            self.assertFalse((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())

    def test_force_overwrites_config_and_adapters(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".keel/project.yaml"
            target.parent.mkdir()
            target.write_text("old")
            ext = Path(d) / ".keel/extensions/local.md"
            ext.parent.mkdir()
            ext.write_text("extension\n")
            adapter = Path(d) / ".claude/commands/keel/ship.md"
            adapter.parent.mkdir(parents=True)
            adapter.write_text("old")
            rc, out, err = run(["setup", "--root", d, "--adapter-target", "claude", "--force"])
            self.assertEqual(rc, 0, err)
            self.assertIn("overwrote", out)
            self.assertIn("extensions   : preserved", out)
            self.assertIn(".keel/extensions/ will not be touched", err)
            self.assertIn("extends: keel", target.read_text(encoding="utf-8"))
            self.assertEqual(ext.read_text(encoding="utf-8"), "extension\n")
            self.assertIn("keel-generated", adapter.read_text(encoding="utf-8"))

    def test_wizard_mode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("x")
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "explicit", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, out, err = run(["setup", "--root", d, "--wizard"])
            self.assertEqual(rc, 0, err)
            self.assertIn("keel setup wizard", out)
            written = (Path(d) / ".keel/project.yaml").read_text(encoding="utf-8")
            import yaml

            config = yaml.safe_load(written)
            self.assertEqual(config["base_branch"], "develop")
            self.assertEqual(config["merge_window"], "09:00-18:00")
            self.assertEqual(config["consent_mode"], "explicit")

    def test_wizard_rejects_invalid_consent_mode_before_setup_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("x")
            answers = ["develop", "Etc/GMT-3", "09:00-18:00", "maybe", "pytest", ""]
            with patch("builtins.input", side_effect=answers):
                rc, _, err = run(["setup", "--root", d, "--wizard"])
            self.assertEqual(rc, 1)
            self.assertIn("unknown consent mode", err)
            self.assertFalse((Path(d) / ".keel" / "project.yaml").exists())

    def test_invalid_existing_config_fails_validation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".keel/project.yaml"
            target.parent.mkdir()
            target.write_text("extends: keel\n")
            rc, out, err = run(["setup", "--root", d, "--adapter-target", "claude"])
            self.assertEqual(rc, 1)
            self.assertIn("using existing", out)
            self.assertIn("validate     : failed", err)


class TestShipHotfix(unittest.TestCase):
    def _cfg(self):
        return _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
            "timezone: Europe/Istanbul\nmerge_window: '07:00-01:30'\n"
            "merge_window_mode: pause\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )

    def _cfg_freeze(self):
        return _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
            "timezone: Europe/Istanbul\nmerge_window: '07:00-01:30'\n"
            "merge_window_mode: freeze\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )

    def _ship(self, config, *extra, closed):
        import tempfile
        # Spy on the window predicate rather than the wall clock: this fixes the verdict
        # *and* captures the (timezone, merge_window) the CLI actually wired into
        # `ship.assess`, so blanking either config value fails the test (#633).
        with tempfile.TemporaryDirectory() as d, \
             patch("keel.ship.is_merge_open", return_value=not closed) as spy:
            rc, out, _ = run(["ship", config, "--root", d, "--json", *extra])
        self.assertEqual(spy.call_args.args, ("Europe/Istanbul", "07:00-01:30"))
        return rc, json.loads(out)["result"]["assessment"]

    def test_pause_mode_halts_outside_the_window(self):
        _, open_run = self._ship(self._cfg(), closed=False)
        _, closed_run = self._ship(self._cfg(), closed=True)
        self.assertTrue(open_run["window_open"])
        self.assertFalse(open_run["halted"])
        self.assertFalse(closed_run["window_open"])
        self.assertTrue(closed_run["halted"])           # `pause` halts the pipeline

    def test_freeze_mode_blocks_the_merge_without_halting(self):
        _, closed_run = self._ship(self._cfg_freeze(), closed=True)
        self.assertFalse(closed_run["window_open"])
        self.assertFalse(closed_run["halted"])          # `freeze` only gates the merge
        self.assertEqual(closed_run["merge"]["action"], "defer")

    def test_hotfix_bypasses_a_closed_window(self):
        rc, assessed = self._ship(self._cfg(), "--hotfix", closed=True)
        self.assertEqual(rc, 0)
        self.assertFalse(assessed["window_open"])
        self.assertFalse(assessed["halted"])            # a blocker is not paused
        self.assertEqual(assessed["merge"]["action"], "merge")
        self.assertTrue(assessed["bypassed_window"])

    def test_hotfix_flag_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["ship", self._cfg(), "--root", d, "--hotfix"])
        # decision depends on the wall-clock window, but the flag must be accepted
        self.assertIn("keel ship", out)
        self.assertIn("DECISION", out.upper())


class TestRunIdMarkerRoundTrip(unittest.TestCase):
    """The marker must make a comment findable *and* leave closure fidelity intact.

    Those two requirements used to be mutually exclusive: `post-comment` matches on
    marker + run-id, but `evidence-verify` compares the posted closure body against the
    canonical render, so any run-id in the body failed fidelity. The fix splits them —
    the transport stamps, `evidence` strips — and it is the *interaction* that matters,
    so that is what this pins.
    """

    RECORD = {"run_id": "ship-123", "actors": {}, "pull_request": {"number": 7},
              "changes": {}, "capture": {}}

    def _body(self):
        from keel import closure
        return closure.render_closure_comment(self.RECORD)

    def test_a_bare_closure_body_is_not_findable(self):
        # The defect: the canonical render writes "- **Run id:** <id>", which the
        # idempotency matcher does not recognise, so every resume posted a duplicate.
        self.assertFalse(cli._comment_has_run_id(self._body(), "ship-123:closure"))

    def test_the_stamped_body_is_both_findable_and_verbatim(self):
        from keel import evidence
        posted = cli._with_run_id_marker(self._body(), "ship-123:closure")

        self.assertTrue(cli._comment_has_run_id(posted, "ship-123:closure"))
        self.assertTrue(evidence.closure_body_matches_record(posted, self.RECORD))

    def test_stamping_twice_does_not_stack_markers(self):
        once = cli._with_run_id_marker(self._body(), "ship-123:closure")
        self.assertEqual(cli._with_run_id_marker(once, "ship-123:closure"), once)

    def test_no_run_id_leaves_the_body_untouched(self):
        self.assertEqual(cli._with_run_id_marker(self._body(), None), self._body())

    def test_the_strip_cannot_launder_text_past_the_fidelity_check(self):
        from keel import evidence
        # An HTML comment ends at its first `-->`, so trailing text renders visibly on
        # the page. A permissive strip would normalize the whole line away and let a
        # trusted author contradict the record while still comparing equal.
        smuggled = self._body() + (
            "\n<!-- keel.run-id: r1 --> **THIS PR WAS NOT ACTUALLY MERGED** -->\n")
        self.assertFalse(evidence.closure_body_matches_record(smuggled, self.RECORD))


class TestGateResultFlag(unittest.TestCase):
    """`--gate-result` is the channel that makes a blocking agentic gate satisfiable.

    Without it, `command_gate_runner` reports every agentic gate `not_run`,
    `record_gates_passed` refuses the record for every head, and `keel merge` blocks
    forever with no way to record that the agent did dispatch the gate — a permanent
    merge block rather than a gate.
    """

    CFG = ("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
           "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
           "extensions:\n  pre-merge: [security-review.md]\n")
    EXT = ("---\nid: security-review\nslot: pre-merge\nkind: agentic\n"
           "on_fail: block\nagent: inherit\n---\nReview the diff.\n")

    def _root(self, stack):
        import tempfile
        d = stack.enter_context(tempfile.TemporaryDirectory())
        ext = Path(d) / ".keel" / "extensions"
        ext.mkdir(parents=True)
        (ext / "security-review.md").write_text(self.EXT, encoding="utf-8")
        return d

    def _ship(self, *extra):
        with contextlib.ExitStack() as stack:
            d = self._root(stack)
            rc, out, err = run(["ship", _write_raw(self.CFG), "--root", d, *extra])
        return rc, out, err

    def test_an_undispatched_blocking_gate_blocks_and_says_why(self):
        rc, out, _ = self._ship()
        self.assertEqual(rc, 1)
        self.assertIn("gate security-review NOT-RUN", out)
        self.assertIn("required gate(s) not run: security-review", out)
        self.assertIn("--gate-result", out)

    def test_a_recorded_pass_clears_it(self):
        rc, out, _ = self._ship("--gate-result", "security-review=pass")
        self.assertEqual(rc, 0)
        self.assertIn("gate security-review ok", out)
        self.assertIn("MERGE — clear to merge", out)

    def test_a_recorded_failure_blocks_on_findings(self):
        rc, out, _ = self._ship("--gate-result", "security-review=fail")
        self.assertEqual(rc, 1)
        self.assertIn("gate security-review FAIL", out)
        self.assertIn("blocking findings present", out)

    def test_a_result_for_a_gate_keel_executed_is_refused(self):
        # The flag records what keel *cannot* measure. Overriding what it did measure
        # would let a run whose gates failed certify a merge.
        cfg = ("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
               "gates: [build]\nknobs:\n  build_gate_cmd: 'false'\n")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["ship", _write_raw(cfg), "--root", d,
                              "--gate-result", "build=pass"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot override a gate keel executed: build", err)

    def test_a_result_for_an_unplanned_gate_is_refused(self):
        rc, _, err = self._ship("--gate-result", "no-such-gate=pass")
        self.assertEqual(rc, 1)
        self.assertIn("names no planned gate: no-such-gate", err)

    def test_argument_shape_is_validated(self):
        for bad in ("noequals", "=pass", "gate=maybe", "gate="):
            with self.subTest(value=bad):
                with self.assertRaises(cli.argparse.ArgumentTypeError):
                    cli._gate_result_arg(bad)

    def test_verdict_is_case_insensitive_and_trimmed(self):
        self.assertEqual(cli._gate_result_arg(" gate = PASS "), ("gate", "pass"))


class TestGateStatusLabel(unittest.TestCase):
    """`NOT-RUN` must never render as `ok` — that is what made an undispatched
    blocking review gate read as green on the operator's screen (#626)."""

    def _label(self, **kw):
        from keel.gates import GateOutcome
        return cli._gate_status(GateOutcome("g", kw.pop("ok", True), **kw))

    def test_labels(self):
        self.assertEqual(self._label(), "ok")
        self.assertEqual(self._label(ok=False), "FAIL")
        self.assertEqual(self._label(ok=False, timed_out=True), "TIMEOUT")
        self.assertEqual(self._label(not_run=True), "NOT-RUN")

    def test_not_run_wins_over_ok(self):
        # An undispatched gate carries ok=True so a soft gate does not spuriously fail
        # the run; the label must still say nobody ran it.
        self.assertEqual(self._label(ok=True, not_run=True), "NOT-RUN")


class TestGateTimeoutWiring(unittest.TestCase):
    """`knobs.gate_timeout_s` reaches `_gate_runner` from both commands that build one.

    `plan_gates` already resolves a timeout onto every command spec, and
    `command_gate_runner` prefers `spec.timeout`, so this kwarg is defence in depth for a
    spec built outside the planner — real, but unreachable from the CLI today, which is
    why deleting it stayed green under mutation (#633). Pinned so the redundancy cannot
    quietly become *wrong*.
    """

    CFG = ("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
           "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n  gate_timeout_s: 47\n")

    def _timeout_seen(self, command):
        import tempfile
        real = cli._gate_runner
        with tempfile.TemporaryDirectory() as d, \
             patch("keel.cli._gate_runner", side_effect=real) as spy, \
             patch("keel.git.changed_files", return_value=[]), \
             patch("keel.git.diff", return_value=""):
            run([command, _write_raw(self.CFG), "--root", d])
        return spy.call_args.kwargs["timeout"]

    def test_run_gates_passes_the_configured_budget(self):
        self.assertEqual(self._timeout_seen("run-gates"), 47)

    def test_ship_passes_the_configured_budget(self):
        self.assertEqual(self._timeout_seen("ship"), 47)


class TestGateRunner(unittest.TestCase):
    def test_command_branch_runs(self):
        from keel.gates import GateSpec
        run_gate = cli._gate_runner(".", "")
        ok, _, timed_out, not_run = run_gate(
            GateSpec("build", "command", "test", "block", run="true"))
        self.assertTrue(ok)
        self.assertFalse(timed_out)
        self.assertFalse(not_run)  # a command gate really did execute

    def test_project_timeout_is_threaded_to_specs_without_their_own(self):
        # plan_gates resolves a timeout onto every command spec, so this fallback is
        # only reachable for a spec built elsewhere — pin it, or the wiring is dead code
        # that nothing would notice being deleted or set to the wrong value.
        from keel.gates import GateSpec
        run_gate = cli._gate_runner(".", "", timeout=1)
        ok, findings, timed_out, not_run = run_gate(
            GateSpec("build", "command", "test", "block", run=_SLOW_CMD, timeout=None))
        self.assertFalse(ok)
        self.assertTrue(timed_out)
        self.assertFalse(not_run)
        self.assertIn("timed out after 1s", findings[0].message)

    def test_jury_budget_reaches_run_gate_from_the_spec(self):
        # plan_gates resolves knobs.jury_timeout_s onto the jury spec; this pins that the
        # runner passes it on. Without it the knob can be made inert (or wired to
        # gate_timeout_s) and the whole suite stays green — the dead-plumbing class #622
        # was caught on.
        from keel.gates import GateSpec
        run_gate = cli._gate_runner(".", "a diff")
        with patch("keel.jury.run_gate", return_value=(True, [], False)) as spy:
            run_gate(GateSpec("jury", "builtin", "test", "block", timeout=4242))
        self.assertEqual(spy.call_args.kwargs["timeout"], 4242)

    def test_jury_budget_falls_back_when_the_spec_carries_none(self):
        from keel.gates import GateSpec
        run_gate = cli._gate_runner(".", "a diff")
        with patch("keel.jury.run_gate", return_value=(True, [], False)) as spy:
            run_gate(GateSpec("jury", "builtin", "test", "block", timeout=None))
        self.assertEqual(spy.call_args.kwargs["timeout"], model.DEFAULT_JURY_TIMEOUT_S)

    def test_jury_branch_noop_without_diff(self):
        from keel.gates import GateSpec
        run_gate = cli._gate_runner(".", "")  # empty diff -> jury is a fail-soft no-op
        ok, findings, _ = run_gate(GateSpec("jury", "builtin", "test", "block"))
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    JURY_CFG = ("extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: x\n"
                "gates: [build, jury]\nknobs:\n  build_gate_cmd: 'true'\n")

    def test_run_gates_with_jury_gate(self):
        import tempfile
        p = _write_raw(self.JURY_CFG)
        with tempfile.TemporaryDirectory() as d, \
             patch("keel.git.diff", return_value=""):  # readable, empty -> jury no-op
            rc, out, _ = run(["run-gates", p, "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("jury", out)

    def test_run_gates_blocks_when_the_diff_cannot_be_read(self):
        # A non-git root makes `git diff` fail, so there is no diff to review. That is
        # not "nothing to review": passing would drop the review gate out of the merge
        # decision silently (#628). Gating mode must fail closed.
        import tempfile
        p = _write_raw(self.JURY_CFG)
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["run-gates", p, "--root", d])
        self.assertEqual(rc, 1)
        self.assertIn("BLOCKED", out)
        self.assertIn("could not be read", out)


class TestInstallAdapter(unittest.TestCase):
    def test_installs_claude_commands(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["install-adapter", "claude", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("/keel:", out)
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())

    def test_installs_skills(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["install-adapter", "skills", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("keel-<command>", out)
            self.assertTrue((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())

    def test_unknown_target(self):
        rc, _, err = run(["install-adapter", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)

    def test_force_reinstall(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run(["install-adapter", "claude", "--root", d])
            rc, out, _ = run(["install-adapter", "claude", "--root", d, "--force"])
            self.assertEqual(rc, 0)
            self.assertIn("installed", out)

    def test_second_run_skips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run(["install-adapter", "claude", "--root", d])
            rc, out, _ = run(["install-adapter", "claude", "--root", d])  # no --force
            self.assertEqual(rc, 0)
            self.assertIn("skipped", out)

    def test_install_all_surfaces(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["install-adapter", "all", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("/keel:", out)
            self.assertTrue((Path(d) / ".claude/commands/keel/ship.md").exists())
            self.assertTrue((Path(d) / ".agents/skills/keel-ship/SKILL.md").exists())

    def test_adapter_status_and_update_adapter(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run(["install-adapter", "all", "--root", d])
            ship = Path(d) / ".claude/commands/keel/ship.md"
            ship.unlink()

            rc, out, _ = run(["adapter-status", "all", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("missing", out)
            self.assertIn("ship.md", out)

            rc, out, _ = run(["update-adapter", "all", "--root", d, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertIn("would-update", out)
            self.assertIn("dry-run: no adapter files were written", out)
            self.assertFalse(ship.exists())

            rc, out, _ = run(["update-adapter", "all", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("updated", out)
            self.assertTrue(ship.exists())

    def test_installs_plugin_command_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["install-adapter", "plugin", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("plugin command file(s) written", out)
            self.assertIn("/plugin install keel", out)
            self.assertTrue((Path(d) / "commands" / "ship.md").exists())
            # second run is a no-op (idempotent generator).
            rc, out, _ = run(["install-adapter", "plugin", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("0 plugin command file(s) written", out)

    def test_install_adapter_unknown_target_lists_plugin(self):
        rc, _, err = run(["install-adapter", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)
        self.assertIn("plugin", err)

    def test_adapter_status_unknown_target(self):
        rc, _, err = run(["adapter-status", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)

    def test_update_adapter_unknown_target(self):
        rc, _, err = run(["update-adapter", "codex"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)

    def test_sync_alias_updates_generated_adapters(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run(["install-adapter", "all", "--root", d])
            ship = Path(d) / ".claude/commands/keel/ship.md"
            ship.unlink()

            rc, out, _ = run(["sync", "--root", d, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertIn("not upgraded by sync", out)
            self.assertIn("would-update", out)
            self.assertIn("dry-run: no adapter files were written", out)
            self.assertIn("keel validate .keel/project.yaml --root .", out)
            self.assertIn("keel plan .keel/project.yaml --root .", out)
            self.assertFalse(ship.exists())

            rc, out, _ = run(["sync", "--root", d, "--target", "claude"])
            self.assertEqual(rc, 0)
            self.assertIn("updated", out)
            self.assertTrue(ship.exists())

    def test_sync_failure_does_not_print_next_steps(self):
        out, err = io.StringIO(), io.StringIO()
        args = Namespace(target="codex", root=".", dry_run=False)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli._cmd_sync(args)
        self.assertEqual(rc, 1)
        self.assertIn("not upgraded by sync", out.getvalue())
        self.assertNotIn("keel validate", out.getvalue())
        self.assertIn("unknown target", err.getvalue())


class TestAdapterStatusOrphans(unittest.TestCase):
    def _seed_orphans(self, d):
        root = Path(d)
        install.install_all(root)
        # class (a): a stale-marker skill for a removed command.
        stale = root / ".agents/skills/keel-ship-v2"
        stale.mkdir(parents=True)
        ship = (install.ADAPTERS / "ship.md").read_text(encoding="utf-8")
        (stale / "SKILL.md").write_text(
            install._with_marker("skills", "ship-v2", ship, ship), encoding="utf-8")
        # class (b): a marker-less plugin command body.
        (root / "commands").mkdir(exist_ok=True)
        (root / "commands" / "mystery.md").write_text("# mystery\n", encoding="utf-8")
        return root

    def test_reports_stale_marker_orphan_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_orphans(d)
            rc, out, _ = run(["adapter-status", "all", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("orphan", out)
            self.assertIn("ship-v2", out)
            # marker-less surface stays silent without the opt-in flag.
            self.assertNotIn("mystery.md", out)
            self.assertIn("--include-unmanaged", out)

    def test_include_unmanaged_flag_reports_marker_less(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_orphans(d)
            rc, out, _ = run(["adapter-status", "all", "--root", d, "--include-unmanaged"])
            self.assertEqual(rc, 0)
            self.assertIn("unmanaged", out)
            self.assertIn("mystery.md", out)
            self.assertIn("advisory only", out)
            self.assertNotIn("pass --include-unmanaged", out)

    def test_json_output_includes_orphans(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_orphans(d)
            rc, out, _ = run(["adapter-status", "all", "--root", d,
                              "--include-unmanaged", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertIn("adapters", payload)
            self.assertIn("orphans", payload)
            cats = {o["category"] for o in payload["orphans"]}
            self.assertEqual(cats, {"orphan", "unmanaged"})

    def test_clean_project_reports_no_orphans(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(Path(d))
            rc, out, _ = run(["adapter-status", "all", "--root", d, "--include-unmanaged"])
            self.assertEqual(rc, 0)
            self.assertNotIn("orphan", out)
            self.assertNotIn("unmanaged", out)

    def test_declared_project_only_command_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            install.install_all(root)
            (root / "commands").mkdir(exist_ok=True)
            (root / "commands" / "house-rules.md").write_text("# house\n", encoding="utf-8")
            keel_dir = root / ".keel"
            keel_dir.mkdir()
            (keel_dir / "project.yaml").write_text(
                "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
                "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                "policy_pack:\n  name: tmp\n"
                "  project_commands:\n"
                "    house-rules:\n      command: .keel/commands/house-rules\n",
                encoding="utf-8",
            )
            rc, out, _ = run(["adapter-status", "all", "--root", d, "--include-unmanaged"])
            self.assertEqual(rc, 0)
            self.assertNotIn("house-rules.md", out)

    def test_project_only_invalid_config_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            install.install_all(root)
            (root / "commands").mkdir(exist_ok=True)
            (root / "commands" / "mystery.md").write_text("# mystery\n", encoding="utf-8")
            keel_dir = root / ".keel"
            keel_dir.mkdir()
            # parses as YAML but fails schema validation → ConfigError → fail-soft.
            (keel_dir / "project.yaml").write_text("not_a_keel: config\n", encoding="utf-8")
            rc, out, _ = run(["adapter-status", "all", "--root", d, "--include-unmanaged"])
            self.assertEqual(rc, 0)
            # invalid config → empty project-only set, so the marker-less file still surfaces.
            self.assertIn("mystery.md", out)

    def test_sync_prints_orphan_heads_up(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_orphans(d)
            rc, out, _ = run(["sync", "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("run keel adapter-status for details", out)

    def test_sync_clean_project_has_no_heads_up(self):
        with tempfile.TemporaryDirectory() as d:
            install.install_all(Path(d))
            rc, out, _ = run(["sync", "--root", d])
            self.assertEqual(rc, 0)
            self.assertNotIn("run keel adapter-status for details", out)


class TestInstallLegacyWrappers(unittest.TestCase):
    def test_installs_selected_legacy_wrapper(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = run([
                "install-legacy-wrappers",
                "all",
                "--root",
                d,
                "--command",
                "ship=ship",
            ])
            self.assertEqual(rc, 0, err)
            self.assertIn("legacy wrapper", out)
            self.assertTrue((Path(d) / ".claude/commands/ship.md").exists())
            self.assertTrue((Path(d) / ".agents/skills/source-command-ship/SKILL.md").exists())

    def test_rejects_unknown_legacy_target(self):
        rc, _, err = run(["install-legacy-wrappers", "codex", "--command", "ship=ship"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown target", err)

    def test_legacy_mapping_parser_rejects_malformed_values(self):
        with self.assertRaisesRegex(Exception, "use LEGACY=KEEL"):
            cli._parse_legacy_mapping("ship")
        with self.assertRaisesRegex(Exception, "must be non-empty"):
            cli._parse_legacy_mapping("ship=")

    def test_rejects_non_ready_mapping_from_parity_matrix(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            matrix = Path(d) / "matrix.md"
            matrix.write_text(
                "| Legacy command | Keel command | Status |\n"
                "|---|---|---|\n"
                "| `ship` | `/keel:ship` | `in-progress` |\n",
                encoding="utf-8",
            )
            rc, _, err = run([
                "install-legacy-wrappers",
                "claude",
                "--root",
                d,
                "--parity-matrix",
                str(matrix),
                "--command",
                "ship=ship",
            ])
            self.assertEqual(rc, 1)
            self.assertIn("not parity-ready", err)

    def test_missing_parity_matrix_is_a_blocker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run([
                "install-legacy-wrappers",
                "claude",
                "--root",
                d,
                "--parity-matrix",
                str(Path(d) / "missing.md"),
                "--command",
                "ship=ship",
            ])
            self.assertEqual(rc, 1)
            self.assertIn("parity matrix not found", err)


class TestParser(unittest.TestCase):
    def test_subcommands_present(self):
        parser = cli.build_parser()
        # argparse stores subparser choices on the subparsers action.
        actions = [a for a in parser._actions if a.dest == "command"]
        self.assertTrue(actions)
        self.assertGreaterEqual(set(actions[0].choices),
                                {"version", "validate", "plan", "run-gates", "window", "ship",
                                 "claim", "release", "merge", "worktree-remove",
                                 "implement", "ci-check", "morning", "capabilities",
                                 "wrap", "overnight", "init",
                                 "setup",
                                 "install-adapter",
                                 "adapter-status", "update-adapter", "sync", "project-commands",
                                 "install-legacy-wrappers", "post-comment"})
        # ship-v2 was removed in favour of the ship --compound profile flag.
        self.assertNotIn("ship-v2", set(actions[0].choices))


class TestPostComment(unittest.TestCase):
    def test_post_comment_reports_missing_and_invalid_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")
            bad_config = f.name
        self.addCleanup(os.unlink, bad_config)
        body = _body_file("<!-- keel.issue-update.v1 -->\n")

        rc_missing, _, err_missing = run([
            "post-comment", "missing.yaml",
            "--target", "issue:1",
            "--artifact", "issue-update",
            "--body-file", body,
        ])
        rc_bad, _, err_bad = run([
            "post-comment", bad_config,
            "--target", "issue:1",
            "--artifact", "issue-update",
            "--body-file", body,
        ])

        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)
        self.assertEqual(rc_bad, 1)
        self.assertIn("missing required", err_bad)

    def test_post_comment_rejects_invalid_target_and_unreadable_body(self):
        body = _body_file("<!-- keel.issue-update.v1 -->\n")
        rc_target, _, err_target = run([
            "post-comment", str(PROJECTS / "keel.yaml"),
            "--target", "comment:1",
            "--artifact", "issue-update",
            "--body-file", body,
        ])
        rc_body, _, err_body = run([
            "post-comment", str(PROJECTS / "keel.yaml"),
            "--target", "issue:1",
            "--artifact", "issue-update",
            "--body-file", "/tmp/keel-missing-body.md",
        ])

        self.assertEqual(rc_target, 1)
        self.assertIn("issue:<number> or pr:<number>", err_target)
        self.assertEqual(rc_body, 1)
        self.assertIn("cannot read --body-file", err_body)

    def test_post_comment_rejects_missing_marker(self):
        body = _body_file("## Missing marker\n")
        rc, _, err = run([
            "post-comment", str(PROJECTS / "keel.yaml"),
            "--target", "issue:247",
            "--artifact", "issue-update",
            "--body-file", body,
        ])
        self.assertEqual(rc, 1)
        self.assertIn("must contain marker", err)
        self.assertIn("keel.issue-update.v1", err)

    def test_post_comment_rejects_literal_body_file_placeholder(self):
        body = _body_file("@/tmp/report.md <!-- keel.issue-update.v1 -->")
        rc, _, err = run([
            "post-comment", str(PROJECTS / "keel.yaml"),
            "--target", "issue:247",
            "--artifact", "issue-update",
            "--body-file", body,
        ])
        self.assertEqual(rc, 1)
        self.assertIn("literal @/path", err)

    def test_post_comment_blocks_missing_capability_and_missing_owner_repo(self):
        body = _body_file("<!-- keel.issue-update.v1 -->\n")
        missing_report = runtime.CapabilityReport((
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=missing_report):
            rc_cap, _, err_cap = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:1",
                "--artifact", "issue-update",
                "--body-file", body,
            ])

        config = _write_raw(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with patch("keel.cli.runtime.detect", return_value=_merge_capability_report()):
            rc_owner, _, err_owner = run([
                "post-comment", config,
                "--target", "issue:1",
                "--artifact", "issue-update",
                "--body-file", body,
            ])

        self.assertEqual(rc_cap, 1)
        self.assertIn("missing required", err_cap)
        self.assertEqual(rc_owner, 1)
        self.assertIn("must define owner and repo", err_owner)

    def test_post_comment_reports_comment_list_failure(self):
        body = _body_file("<!-- keel.issue-update.v1 -->\n")

        def failing_list(argv, **_kwargs):
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return _proc("rate limited", ok=False)
            return _proc(f"unexpected {argv}", ok=False)

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=failing_list),
        ):
            rc, _, err = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
            ])
        self.assertEqual(rc, 1)
        self.assertIn("rate limited", err)

    def test_post_comment_posts_marked_body_via_selected_transport(self):
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return _proc("[]")
            if argv[:3] == ["gh", "api", "repos/berkayturanci/keel/issues/247/comments"]:
                self.assertIn("-f", argv)
                body_arg = next(item for item in argv if item.startswith("body="))
                self.assertIn("keel.issue-update.v1", body_arg)
                self.assertNotIn("--body", argv)
                return _proc(json.dumps({
                    "id": 42,
                    "html_url": "https://github.example/comment/42",
                }))
            return _proc(f"unexpected {argv}", ok=False)

        body = _body_file("<!-- keel.issue-update.v1 -->\n\nrun-id: abc\n")
        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, out, err = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
                "--json",
            ])

        self.assertEqual((rc, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(data["action"], "posted")
        self.assertEqual(data["transport"], "gh")
        self.assertEqual(data["comment_id"], 42)
        self.assertTrue(calls)

    def test_post_comment_reports_mutation_failure_and_non_json_success(self):
        body = _body_file("<!-- keel.issue-update.v1 -->\n")

        def failing_post(argv, **_kwargs):
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return _proc("[]")
            return _proc("", ok=False)

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=failing_post),
            patch("keel.github.run_argv", side_effect=failing_post),
        ):
            rc_fail, _, err_fail = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
            ])

        def text_post(argv, **_kwargs):
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return _proc("[]")
            return _proc("created")

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=text_post),
            patch("keel.github.run_argv", side_effect=text_post),
        ):
            rc_text, out_text, err_text = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
                "--json",
            ])

        def list_response_post(argv, **_kwargs):
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return _proc("[]")
            return _proc("[]")

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=list_response_post),
            patch("keel.github.run_argv", side_effect=list_response_post),
        ):
            rc_list, out_list, err_list = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "issue:247",
                "--artifact", "issue-update",
                "--body-file", body,
                "--json",
            ])

        self.assertEqual(rc_fail, 1)
        self.assertIn("gh comment mutation failed", err_fail)
        self.assertEqual((rc_text, err_text), (0, ""))
        self.assertEqual(json.loads(out_text)["action"], "posted")
        self.assertEqual((rc_list, err_list), (0, ""))
        self.assertEqual(json.loads(out_list)["action"], "posted")

    def test_post_comment_edits_existing_same_run_comment(self):
        existing = [{
            "id": 99,
            "body": "<!-- keel.closure-comment.v1 -->\nrun-id: run-1\nold",
        }]
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(argv)
            if argv[:4] == ["gh", "api", "--paginate", "--slurp"]:
                return _proc(json.dumps([existing]))
            if argv[:3] == ["gh", "api", "repos/berkayturanci/keel/issues/comments/99"]:
                self.assertIn("PATCH", argv)
                return _proc(json.dumps({"id": 99}))
            return _proc(f"unexpected {argv}", ok=False)

        body = _body_file("<!-- keel.closure-comment.v1 -->\n\nrun-id: run-1\nnew\n")
        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, out, err = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "pr:123",
                "--artifact", "closure-comment",
                "--body-file", body,
                "--run-id", "run-1",
                "--json",
            ])

        self.assertEqual((rc, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(data["action"], "edited")
        self.assertEqual(data["comment_id"], 99)

    def test_post_comment_dry_run_and_bad_existing_comment_id(self):
        body = _body_file("<!-- keel.closure-comment.v1 -->\n\nrun-id: run-1\n")
        existing = [{"id": "bad", "body": "<!-- keel.closure-comment.v1 -->\nrun-id: run-1"}]

        def fake_list(argv, **_kwargs):
            self.assertEqual(argv[:4], ["gh", "api", "--paginate", "--slurp"])
            return _proc(json.dumps([existing]))

        with (
            patch("keel.cli.runtime.detect", return_value=_merge_capability_report()),
            patch("keel.cli.run_argv", side_effect=fake_list),
        ):
            rc_dry, out_dry, err_dry = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "pr:123",
                "--artifact", "closure-comment",
                "--body-file", body,
                "--run-id", "run-1",
                "--dry-run",
            ])
            rc_bad, _, err_bad = run([
                "post-comment", str(PROJECTS / "keel.yaml"),
                "--target", "pr:123",
                "--artifact", "closure-comment",
                "--body-file", body,
                "--run-id", "run-1",
            ])

        self.assertEqual((rc_dry, err_dry), (0, ""))
        self.assertIn("keel post-comment", out_dry)
        self.assertIn("comment       : bad", out_dry)
        self.assertEqual(rc_bad, 1)
        self.assertIn("missing an integer id", err_bad)

    def test_post_comment_match_helpers_cover_skip_paths(self):
        comments = [
            {"id": 1, "body": ""},
            {"id": 2, "body": "<!-- keel.issue-update.v1 -->\nrun-id: other"},
            {"id": 3, "body": "<!-- keel.issue-update.v1 -->\n<!-- keel.run-id: abc -->"},
        ]
        self.assertIsNone(cli._find_comment_match(
            comments, marker="<!-- keel.issue-update.v1 -->", run_id=None
        ))
        match = cli._find_comment_match(
            comments, marker="<!-- keel.issue-update.v1 -->", run_id="abc"
        )
        self.assertEqual(match["id"], 3)
        payload = {"target": "raw", "artifact": "issue-update", "transport": "gh"}
        out, err = io.StringIO(), io.StringIO()
        args = Namespace(json=False)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli._finish_post_comment(args, payload, code=0)
        self.assertEqual(rc, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("raw", out.getvalue())


def _merge_capability_report():
    return runtime.CapabilityReport((
        runtime.Capability("shell", True, "ok", "test"),
        runtime.Capability("git", True, "ok", "test"),
        runtime.Capability("worktree", True, "ok", "test"),
        runtime.Capability("gh", True, "ok", "test"),
        runtime.Capability("gh-auth", True, "ok", "test"),
    ))


def _body_file(text: str) -> str:
    path = Path(_TMP.name) / f"body-{next(_TMP_COUNTER)}.md"
    path.write_text(text)
    return str(path)


def _merge_args(*, root: str | None = None, json_out: bool = False, dry_run: bool = False):
    argv = [
        "merge", str(PROJECTS / "keel.yaml"),
        "--root", root or str(REPO_ROOT),
        "--pr", "123",
        "--approve-scope", "filesystem,git,github",
        "--operator", "tester",
    ]
    if json_out:
        argv.append("--json")
    if dry_run:
        argv.append("--dry-run")
    return argv


def _json_result(payload: dict):
    return _proc(json.dumps(payload))


class TestGuardCommand(unittest.TestCase):
    def _cfg(self):
        return str(PROJECTS / "keel.yaml")

    def test_guard_reports_missing_and_invalid_config(self):
        rc_missing, _, err_missing = run(["guard", "/no/such.yaml"])
        self.assertEqual(rc_missing, 1)
        self.assertIn("no such config", err_missing)
        bad = _write_raw("repo: x\ngates: [bogus]\nknobs:\n  build_gate_cmd: 'true'\n")
        rc_bad, _, err_bad = run(["guard", bad])
        self.assertEqual(rc_bad, 1)
        self.assertIn("invalid keel config", err_bad)

    def test_guard_title_regex_matches(self):
        rc, out, _ = run([
            "guard", self._cfg(), "--issue-title", "hotfix: patch boot loop", "--json",
        ])
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(payload["is_blocker"])
        self.assertEqual(payload["matched"], ["blocker-title-regex"])

    def test_guard_label_matches_text_output(self):
        rc, out, _ = run([
            "guard", self._cfg(), "--issue-title", "anything", "--issue-labels", "blocker,chore",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("BLOCKER", out)
        self.assertIn("blocker-label", out)

    def test_guard_no_match_text_output(self):
        rc, out, _ = run(["guard", self._cfg(), "--issue-title", "tidy docs"])
        self.assertEqual(rc, 0)
        self.assertIn("not a blocker", out)
        self.assertIn("(none)", out)

    def test_guard_live_issue_fetch(self):
        facts = _proc(json.dumps({
            "title": "security: token leak",
            "labels": [{"name": "P0"}, {"name": "needs-fix"}],
        }))
        with patch("keel.cli.github.issue_facts", return_value=facts):
            rc, out, _ = run(["guard", self._cfg(), "--issue", "42", "--json"])
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["title"], "security: token leak")
        self.assertEqual(payload["matched"], ["blocker-title-regex"])

    def test_guard_live_fetch_failure_falls_back_to_args(self):
        with patch("keel.cli.github.issue_facts", return_value=_proc("offline", ok=False)):
            rc, out, _ = run([
                "guard", self._cfg(), "--issue", "42",
                "--issue-title", "hotfix: x", "--json",
            ])
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["title"], "hotfix: x")

    def test_guard_live_fetch_bad_json_falls_back(self):
        with patch("keel.cli.github.issue_facts", return_value=_proc("not-json")):
            rc, out, _ = run([
                "guard", self._cfg(), "--issue", "42",
                "--issue-title", "hotfix: x", "--json",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["title"], "hotfix: x")

    def test_guard_live_fetch_non_dict_payload_falls_back(self):
        with patch("keel.cli.github.issue_facts", return_value=_proc("[]")):
            rc, out, _ = run([
                "guard", self._cfg(), "--issue", "42",
                "--issue-title", "hotfix: x", "--json",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["title"], "hotfix: x")

    def test_guard_live_fetch_ignores_malformed_fields(self):
        facts = _proc(json.dumps({"title": 5, "labels": "nope"}))
        with patch("keel.cli.github.issue_facts", return_value=facts):
            rc, out, _ = run([
                "guard", self._cfg(), "--issue", "42",
                "--issue-title", "hotfix: x", "--json",
            ])
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["title"], "hotfix: x")
        self.assertEqual(payload["labels"], [])

    def test_guard_live_fetch_skips_malformed_labels(self):
        facts = _proc(json.dumps({
            "title": "tidy", "labels": [{"name": "blocker"}, "junk", {"x": 1}],
        }))
        with patch("keel.cli.github.issue_facts", return_value=facts):
            rc, out, _ = run(["guard", self._cfg(), "--issue", "42", "--json"])
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["labels"], ["blocker"])

    def test_guard_invalid_blocker_rules(self):
        bad = _write_raw(
            "extends: keel\ncore_version: 1.0.0\nbase_branch: main\n"
            "knobs:\n  build_gate_cmd: 'true'\n"
            "policy_pack:\n  name: x\n  blocker_rules:\n"
            "    - id: bad\n      kind: title-regex\n      pattern: '('\n"
        )
        rc, _, err = run(["guard", bad, "--issue-title", "x"])
        self.assertEqual(rc, 1)
        self.assertIn("invalid blocker rules", err)


class TestGatherIssueFacts(unittest.TestCase):
    def _args(self, **kw):
        base = {"issue": None, "issue_title": None, "issue_labels": None, "root": "."}
        base.update(kw)
        return Namespace(**base)

    def test_authoritative_true_on_live_fetch(self):
        facts = _proc(json.dumps({
            "title": "security: leak", "labels": [{"name": "P0"}],
        }))
        with patch("keel.cli.github.issue_facts", return_value=facts):
            title, labels, authoritative = cli._gather_issue_facts(self._args(issue=42))
        self.assertEqual(title, "security: leak")
        self.assertEqual(labels, ("P0",))
        self.assertTrue(authoritative)

    def test_authoritative_false_without_issue(self):
        title, labels, authoritative = cli._gather_issue_facts(
            self._args(issue_title="hotfix: x", issue_labels="blocker")
        )
        self.assertEqual(title, "hotfix: x")
        self.assertEqual(labels, ("blocker",))
        self.assertFalse(authoritative)

    def test_authoritative_false_on_failed_fetch(self):
        with patch("keel.cli.github.issue_facts", return_value=_proc("offline", ok=False)):
            title, _labels, authoritative = cli._gather_issue_facts(
                self._args(issue=42, issue_title="hotfix: x")
            )
        self.assertEqual(title, "hotfix: x")
        self.assertFalse(authoritative)


class TestActivityCli(unittest.TestCase):
    def _cfg(self):
        return _write_config_with_checkpoint("'true'")

    def test_write_read_done_clear_cycle(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._cfg()
            rc, out, _ = run(["activity", cfg_path, "--root", d, "--write",
                              "--command", "triage", "--run-id", "triage-2260",
                              "--phase", "classify", "--issue", "2260"])
            self.assertEqual(rc, 0)
            self.assertIn("triage-2260", out)
            self.assertIn("running", out)

            rc, out, _ = run(["activity", cfg_path, "--root", d])
            self.assertEqual(rc, 0)
            self.assertIn("1 record(s)", out)

            rc, out, _ = run(["activity", cfg_path, "--root", d, "--done",
                              "--run-id", "triage-2260"])
            self.assertEqual(rc, 0)
            self.assertIn("done", out)

            rc, out, _ = run(["activity", cfg_path, "--root", d, "--clear",
                              "--run-id", "triage-2260"])
            self.assertEqual(rc, 0)
            self.assertIn("cleared", out)

            rc, out, _ = run(["activity", cfg_path, "--root", d])
            self.assertIn("0 record(s)", out)

    def test_json_output(self):
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._cfg()
            run(["activity", cfg_path, "--root", d, "--write", "--command",
                 "morning", "--run-id", "m1", "--phase", "config"])
            rc, out, _ = run(["activity", cfg_path, "--root", d, "--json"])
            self.assertEqual(rc, 0)
            payload = _json.loads(out)
            self.assertEqual(len(payload["activity"]), 1)
            self.assertFalse(payload["contract"]["touches_checkpoint"])

    def test_write_with_verdict(self):
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg_path = self._cfg()
            rc, _, _ = run(["activity", cfg_path, "--root", d, "--write", "--command",
                            "ship", "--run-id", "s-1", "--phase", "s8", "--verdict", "pass"])
            self.assertEqual(rc, 0)
            rc, out, _ = run(["activity", cfg_path, "--root", d, "--json"])
            self.assertEqual(rc, 0)
            payload = _json.loads(out)
            self.assertEqual(payload["activity"][0]["verdict"], "pass")

    def test_done_missing_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["activity", self._cfg(), "--root", d, "--done",
                              "--run-id", "ghost"])
            self.assertEqual(rc, 1)
            self.assertIn("no activity record", err)

    def test_clear_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = run(["activity", self._cfg(), "--root", d, "--clear",
                              "--run-id", "ghost"])
            self.assertEqual(rc, 0)
            self.assertIn("nothing to clear", out)

    def test_bad_phase_is_friendly_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["activity", self._cfg(), "--root", d, "--write",
                              "--command", "triage", "--run-id", "x",
                              "--phase", "nope"])
            self.assertEqual(rc, 1)
            self.assertIn("not a triage flow phase", err)

    def test_missing_config(self):
        rc, _, err = run(["activity", "no-such.yaml", "--root", "."])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.yaml"
            bad.write_text("extends: keel\n", encoding="utf-8")
            rc, _, err = run(["activity", str(bad), "--root", d])
            self.assertEqual(rc, 1)
            self.assertTrue(err.strip())


class TestRenderReport(unittest.TestCase):
    def _payload(self, value):
        return _write_raw(json.dumps(value))

    def test_renders_coverage_to_stdout(self):
        path = self._payload({"codename": "COVERAGE-9-T",
                              "areas": [{"name": "core", "overall": {"base": 90.0, "head": 91.0}}]})
        rc, out, _ = run(["render-report", "--kind", "coverage", "--payload", path])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("COVERAGE-9-T\n"))
        self.assertIn("<!-- keel.coverage-delta.v1 -->", out)

    def test_deps_audit_json_carries_kind_and_marker(self):
        path = self._payload({"codename": "DEPS-T", "ecosystems": [],
                              "security_only": True})
        rc, out, _ = run(["render-report", "--kind", "deps-audit", "--payload", path, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["kind"], "deps-audit")
        self.assertEqual(data["marker"], "keel.deps-audit.v1")
        self.assertIn("DEPS-T", data["body"])

    def test_flake_audit_renders(self):
        path = self._payload({"codename": "FLAKE-T", "new_flakes": []})
        rc, out, _ = run(["render-report", "--kind", "flake-audit", "--payload", path])
        self.assertEqual(rc, 0)
        self.assertIn("_no new flakes above threshold_", out)

    def test_scan_finding_renders_issue_body(self):
        path = self._payload({"problem": "boom", "location": "a.py:1",
                              "severity": "major", "source": "regression"})
        rc, out, _ = run(["render-report", "--kind", "scan-finding", "--payload", path])
        self.assertEqual(rc, 0)
        self.assertIn("## Problem\n\nboom", out)
        self.assertIn("<!-- keel.scan-finding.v1 -->", out)

    def test_triage_audit_json_carries_marker(self):
        path = self._payload({"issue": 9, "role": "core", "tier": 2,
                              "rationale": "core path"})
        rc, out, _ = run(["render-report", "--kind", "triage-audit", "--payload", path, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["kind"], "triage-audit")
        self.assertEqual(data["marker"], "keel.triage-audit.v1")
        self.assertIn("keel triage — #9:", data["body"])

    def test_payload_must_be_object(self):
        path = self._payload(["not", "an", "object"])
        rc, _, err = run(["render-report", "--kind", "coverage", "--payload", path])
        self.assertEqual(rc, 1)
        self.assertIn("must be a JSON object", err)

    def test_missing_payload_file_errors(self):
        rc, _, err = run(["render-report", "--kind", "coverage",
                          "--payload", str(Path(_TMP.name) / "nope.json")])
        self.assertEqual(rc, 1)
        self.assertIn("cannot read --payload", err)

    def test_invalid_json_errors(self):
        path = Path(_TMP.name) / f"bad-report-{next(_TMP_COUNTER)}.json"
        path.write_text("{not json", encoding="utf-8")
        rc, _, err = run(["render-report", "--kind", "coverage", "--payload", str(path)])
        self.assertEqual(rc, 1)
        self.assertIn("is not valid JSON", err)

    def test_mismatched_fields_error(self):
        path = self._payload({"areas": []})  # missing required codename
        rc, _, err = run(["render-report", "--kind", "coverage", "--payload", path])
        self.assertEqual(rc, 1)
        self.assertIn("does not match the coverage report fields", err)


if __name__ == "__main__":
    unittest.main()
