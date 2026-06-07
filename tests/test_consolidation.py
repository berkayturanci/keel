"""Consolidation gate (#94): tests that restore 100% line+branch coverage on the
pure core. Each test names the source location it pins.

The CLI helpers mirror ``tests/test_cli.py`` (a ``run(argv)`` wrapper capturing
stdout/stderr, plus ``_write_raw`` for ad-hoc configs).
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from keel import cli, consent, contracts, install, runtime

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
REPO_ROOT = PROJECTS.parent


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _write_raw(text):
    fd = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    fd.write(text)
    fd.close()
    return fd.name


def _mcp_only_report() -> runtime.CapabilityReport:
    """A report whose only GitHub transport is MCP -> github_transport.resolve degrades."""
    return runtime.CapabilityReport((
        runtime.Capability("shell", True, "ok", "test"),
        runtime.Capability("git", True, "ok", "test"),
        runtime.Capability("worktree", True, "ok", "test"),
        runtime.Capability("filesystem-write", True, "ok", "test"),
        runtime.Capability("gh", False, "missing", "test"),
        runtime.Capability("gh-auth", False, "missing", "test"),
        runtime.Capability("github-mcp", True, "ok", "test"),
    ))


class TestCliPlanConsent(unittest.TestCase):
    def test_plan_bad_approve_scope_raises_value_error(self):
        # cli.py 106-108: _approved_scopes -> consent.normalize_scopes ValueError path.
        rc, _, err = run(
            ["plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
             "--approve-scope", "bogus"]
        )
        self.assertEqual(rc, 1)
        self.assertIn("unknown consent scope", err)


class TestCliRunGatesCapabilities(unittest.TestCase):
    def test_missing_required_capability_blocks(self):
        # cli.py 168-169: run-gates with a missing REQUIRED capability -> rc 1.
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: x\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "  required_capabilities: [browser]\n"
        )
        rc, _, err = run(["run-gates", p, "--root", "."])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", err)

    def test_missing_optional_capability_degrades_but_passes(self):
        # cli.py 171: run-gates with a missing OPTIONAL capability -> prints degraded, rc 0.
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: x\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "  optional_capabilities: [browser]\n"
        )
        rc, out, err = run(["run-gates", p, "--root", "."])
        self.assertEqual(rc, 0)
        self.assertIn("degraded optional", err)
        self.assertIn("ok", out)


class TestCliShipConsent(unittest.TestCase):
    def test_ship_bad_approve_scope_raises_value_error(self):
        # cli.py 238-240: ship --approve-scope <bad> ValueError path.
        with tempfile.TemporaryDirectory() as d:
            p = _write_raw(
                "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
                "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            )
            rc, _, err = run(["ship", p, "--root", d, "--approve-scope", "bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown consent scope", err)

    def test_ship_live_human_missing_consent_prints_message(self):
        # cli.py 265: ship --live (non-json) with missing consent -> prints message, rc 1.
        with tempfile.TemporaryDirectory() as d:
            p = _write_raw(
                "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
                "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            )
            rc, _, err = run(["ship", p, "--root", d, "--live", "--target", "issue #82"])
        self.assertEqual(rc, 1)
        self.assertIn("operator consent", err)

    def test_ship_human_degraded_transport_reports_degraded(self):
        # cli.py 318: ship human output with a DEGRADED (mcp) transport.
        with tempfile.TemporaryDirectory() as d, patch(
            "keel.cli.runtime.detect", return_value=_mcp_only_report()
        ):
            p = _write_raw(
                "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
                "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            )
            rc, out, _ = run(["ship", p, "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("github degraded:", out)
        self.assertIn("github        : mcp", out)

    def test_ship_human_non_degraded_transport(self):
        # cli.py 317->319: ship human output with a NON-degraded (gh) transport, so the
        # `if transport.degraded` branch is skipped.
        report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", True, "ok", "test"),
            runtime.Capability("worktree", True, "ok", "test"),
            runtime.Capability("filesystem-write", True, "ok", "test"),
            runtime.Capability("gh", True, "ok", "test"),
            runtime.Capability("gh-auth", True, "ok", "test"),
        ))
        with tempfile.TemporaryDirectory() as d, patch(
            "keel.cli.runtime.detect", return_value=report
        ):
            p = _write_raw(
                "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
                "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            )
            rc, out, _ = run(["ship", p, "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("github        : gh", out)
        self.assertNotIn("github degraded:", out)


class TestCliShipJsonFindings(unittest.TestCase):
    def test_ship_json_failing_gate_exposes_findings(self):
        # contracts.py 270 (_finding_as_dict) + locks the ship --json result path (#94 C).
        with tempfile.TemporaryDirectory() as d:
            p = _write_raw(
                "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
                "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'false'\n"
            )
            rc, out, _ = run(["ship", p, "--root", d, "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        verdict = data["result"]["verdict"]
        self.assertTrue(verdict["blocked"])
        self.assertTrue(verdict["findings"])
        finding = verdict["findings"][0]
        for key in ("severity", "message", "source", "path", "line", "anchorable"):
            self.assertIn(key, finding)


class TestCliCapabilities(unittest.TestCase):
    def test_capabilities_missing_config_returns_one(self):
        # cli.py 344-346: capabilities --project /nope.yaml -> rc 1.
        rc, _, err = run(["capabilities", "--project", str(PROJECTS / "nope.yaml")])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_capabilities_malformed_config_returns_one(self):
        # cli.py 347-349: capabilities --project <malformed> -> rc 1.
        bad = _write_raw("extends: keel\n")  # missing required keys
        rc, _, err = run(["capabilities", "--project", bad])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_capabilities_human_reports_extension_problem(self):
        # cli.py 366: capabilities --project <cfg with ghost extension> non-json.
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: x\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "extensions:\n  tester: [ghost.md]\nextensions_dir: .keel/extensions\n"
        )
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = run(["capabilities", "--project", p, "--root", d])
        # extension problem is warned regardless of capability rc
        self.assertIn("extension not loaded", err)


class TestCliCapabilityRequirementGateCaps(unittest.TestCase):
    def test_plan_merges_per_gate_capabilities(self):
        # cli.py 484: an extension gate carrying required/optional capabilities flows
        # through the per-spec merge loop in _capability_requirement.
        with tempfile.TemporaryDirectory() as d:
            ext_dir = Path(d) / ".keel" / "extensions"
            ext_dir.mkdir(parents=True)
            (ext_dir / "report.md").write_text(
                "---\nid: external-report\nslot: tester\nkind: command\n"
                "run: ./tools/report\nrequired_capabilities: [shell]\n"
                "optional_capabilities: [browser]\n---\n",
                encoding="utf-8",
            )
            cfg_path = Path(d) / "project.yaml"
            cfg_path.write_text(
                "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: x\n"
                "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
                "extensions:\n  tester: [report.md]\nextensions_dir: .keel/extensions\n",
                encoding="utf-8",
            )
            rc, out, _ = run(["plan", str(cfg_path), "--root", d])
        self.assertEqual(rc, 0)
        self.assertIn("runtime capabilities", out)


class TestConsentNormalize(unittest.TestCase):
    def test_empty_middle_part_is_skipped(self):
        # consent.py 100: empty scope part between commas is skipped.
        self.assertEqual(
            consent.normalize_scopes(["filesystem,,git"]),
            ("filesystem", "git"),
        )


class TestContractsPure(unittest.TestCase):
    def test_command_graph_unknown_command_is_empty(self):
        # contracts.py 251: _adapter_steps for a missing adapter file -> [].
        self.assertEqual(contracts.command_graph("does-not-exist"), [])


class TestInstallSplitMarker(unittest.TestCase):
    def test_meta_token_without_equals_is_ignored(self):
        # install.py 79->78: a meta token lacking '=' is skipped (loop continues).
        text = "body line\n<!-- keel-generated: source=keel orphan hash=abc -->\n"
        body, meta = install._split_marker(text)
        self.assertEqual(body, "body line\n")
        self.assertEqual(meta, {"source": "keel", "hash": "abc"})
        self.assertNotIn("orphan", meta)


class TestRuntimeRenderBranch(unittest.TestCase):
    def test_render_with_required_missing_and_no_optional(self):
        # runtime.py 137->139: missing_required present while missing_optional is empty.
        report = runtime.CapabilityReport((
            runtime.Capability("git", False, "missing", "test"),
        ))
        req = runtime.CapabilityRequirement(required=("git",))
        evaluation = runtime.evaluate(req, report)
        rendered = evaluation.render()
        self.assertIn("missing required", rendered)
        self.assertNotIn("degraded optional", rendered)

    @unittest.skipIf(shutil.which("gh"), "gh present: gh auth status would run a subprocess")
    def test_detect_default_runner_import_path(self):
        # runtime.py default-import path (run is None) stays deterministic only when gh
        # is absent, so no live `gh auth status` subprocess fires.
        with tempfile.TemporaryDirectory() as d:
            report = runtime.detect(d)
        self.assertFalse(report.available("gh"))
        self.assertFalse(report.available("gh-auth"))


if __name__ == "__main__":
    unittest.main()
