"""Consolidation gate (#94): tests that restore 100% line+branch coverage on the
pure core. Each test names the source location it pins.

The CLI helpers mirror ``tests/test_cli.py`` (a ``run(argv)`` wrapper capturing
stdout/stderr, plus ``_write_raw`` for ad-hoc configs).
"""

from __future__ import annotations

import atexit
import contextlib
import io
import itertools
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from keel import cli, consent, contracts, install, runtime

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
REPO_ROOT = PROJECTS.parent

# Module-level scratch directory backing the path-returning ``_write_raw``
# helper. Cleaned at process exit so the suite leaves no stray temp files.
_TMP = tempfile.TemporaryDirectory()
atexit.register(_TMP.cleanup)
_TMP_COUNTER = itertools.count()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


#: Variables a process still needs after the environment is cleared. On Linux and
#: macOS an empty environment is survivable; on Windows, losing `SYSTEMROOT` and
#: `PATH` breaks subprocess creation and temp-file resolution, so a `--live` test
#: failed there for a reason unrelated to consent (#953).
_PLATFORM_ESSENTIALS = ("PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "PATHEXT")


def _isolated_env(**overrides: str) -> dict[str, str]:
    """Every ``KEEL_*`` removed and the given ones set, platform essentials kept.

    `clear=True` with only the overrides is what the surrounding tests do, and it
    is right about the intent — no ambient `KEEL_*` may leak in. It is wrong
    about the blast radius on Windows.
    """
    env = {name: os.environ[name] for name in _PLATFORM_ESSENTIALS if name in os.environ}
    env.update(overrides)
    return env


def _write_raw(text):
    path = Path(_TMP.name) / f"cfg-{next(_TMP_COUNTER)}.yaml"
    path.write_text(text)
    return str(path)


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

    def test_plan_live_env_standing_approval_records_source(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ,
            {"KEEL_APPROVE_SCOPE": "filesystem,git,github", "KEEL_OPERATOR": "automation:cron"},
            clear=False,
        ):
            rc, out, err = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
                "--target", "nightly queue", "--consent-mode", "standing",
            ])
        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["contract"]
        self.assertEqual(contract["operator_consent"]["status"], "approved")
        self.assertEqual(contract["operator_consent"]["approval_source"], "env")
        self.assertEqual(contract["operator_consent"]["consent_record"]["source"], "env")
        self.assertEqual(
            contract["operator_consent"]["consent_record"]["operator"],
            "automation:cron",
        )

    def test_plan_live_env_standing_approval_requires_operator(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ,
            {"KEEL_APPROVE_SCOPE": "filesystem,git,github"},
            clear=True,
        ):
            rc, _, err = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
                "--consent-mode", "standing",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("KEEL_OPERATOR is required", err)

    def test_plan_live_config_standing_approval_records_source(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: tmp\n"
            "consent_mode: standing\n"
            "automation:\n"
            "  approved_scopes: [filesystem, git, github]\n"
            "  operator: automation:nightly\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            rc, out, err = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
                "--target", "nightly queue",
            ])
        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["contract"]
        self.assertEqual(contract["operator_consent"]["status"], "approved")
        self.assertEqual(contract["operator_consent"]["approval_source"], "config")
        self.assertEqual(contract["operator_consent"]["consent_record"]["source"], "config")
        self.assertEqual(
            contract["operator_consent"]["consent_record"]["operator"],
            "automation:nightly",
        )

    def test_plan_live_config_standing_approval_requires_operator(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: tmp\n"
            "consent_mode: standing\n"
            "automation:\n"
            "  approved_scopes: [filesystem, git, github]\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            rc, _, err = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("automation.operator is required", err)

    def test_plan_live_flag_approval_overrides_env_and_config(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: tmp\n"
            "automation:\n"
            "  approved_scopes: [filesystem, git, github]\n"
            "  operator: automation:nightly\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ,
            {"KEEL_APPROVE_SCOPE": "filesystem,git,github", "KEEL_OPERATOR": "automation:cron"},
            clear=False,
        ):
            rc, out, err = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
                "--approve-scope", "filesystem,git,github", "--operator", "human",
            ])
        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["contract"]
        self.assertEqual(contract["operator_consent"]["approval_source"], "flag")
        self.assertEqual(contract["operator_consent"]["consent_record"]["source"], "flag")
        self.assertEqual(contract["operator_consent"]["consent_record"]["operator"], "human")

    def test_dry_run_ignores_invalid_env_standing_approval(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ,
            {"KEEL_APPROVE_SCOPE": "bogus"},
            clear=True,
        ):
            rc, out, err = run(["plan", p, "--root", d, "--command", "ship", "--json"])
        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["contract"]
        self.assertEqual(contract["operator_consent"]["approval_source"], "none")

    def test_plan_live_agent_mode_does_not_block_missing_scope(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
                "--consent-mode", "agent",
            ])
        self.assertEqual(rc, 0, err)
        consent_block = json.loads(out)["contract"]["operator_consent"]
        self.assertEqual(consent_block["mode"], "agent")
        self.assertEqual(consent_block["status"], "agent-delegated")
        self.assertFalse(consent_block["requires_operator_consent"])

    def test_plan_live_config_agent_mode_does_not_block_missing_scope(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\nconsent_mode: agent\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            rc, out, err = run(["plan", p, "--root", d, "--command", "ship", "--live", "--json"])
        self.assertEqual(rc, 0, err)
        consent_block = json.loads(out)["contract"]["operator_consent"]
        self.assertEqual(consent_block["mode"], "agent")
        self.assertEqual(consent_block["status"], "agent-delegated")

    def test_plan_live_env_consent_mode_selects_standing(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ,
            {
                "KEEL_CONSENT_MODE": "standing",
                "KEEL_APPROVE_SCOPE": "filesystem,git,github",
                "KEEL_OPERATOR": "automation:cron",
            },
            clear=True,
        ):
            rc, out, err = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
            ])
        self.assertEqual(rc, 0, err)
        consent_block = json.loads(out)["contract"]["operator_consent"]
        self.assertEqual(consent_block["mode"], "standing")
        self.assertEqual(consent_block["approval_source"], "env")

    def test_plan_live_standing_without_approval_still_reports_missing(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            rc, out, _ = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
                "--consent-mode", "standing",
            ])
        self.assertEqual(rc, 1)
        consent_block = json.loads(out)["contract"]["operator_consent"]
        self.assertEqual(consent_block["mode"], "standing")
        self.assertEqual(consent_block["approval_source"], "none")
        self.assertEqual(consent_block["status"], "missing")

    def test_plan_unknown_env_consent_mode_errors(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ, {"KEEL_CONSENT_MODE": "maybe"}, clear=True,
        ):
            rc, _, err = run([
                "plan", p, "--root", d, "--command", "ship", "--live", "--json",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("unknown consent mode", err)

    def test_read_only_ci_check_ignores_invalid_env_standing_approval(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ,
            {"KEEL_APPROVE_SCOPE": "bogus"},
            clear=True,
        ):
            rc, out, err = run(["ci-check", p, "--root", d, "--json"])
        self.assertEqual(rc, 0, err)
        contract = json.loads(out)["contract"]
        self.assertEqual(contract["operator_consent"]["status"], "not-required-read-only")

    def test_plan_live_read_only_ci_check_ignores_invalid_env_standing_approval(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ,
            {"KEEL_CONSENT_MODE": "standing", "KEEL_APPROVE_SCOPE": "bogus"},
            clear=True,
        ):
            rc, out, err = run([
                "plan", p, "--root", d, "--command", "ci-check", "--live", "--json",
            ])
        self.assertEqual(rc, 0, err)
        consent_block = json.loads(out)["contract"]["operator_consent"]
        self.assertEqual(consent_block["status"], "not-required-read-only")
        self.assertEqual(consent_block["approval_source"], "none")

    def test_plan_live_read_only_ci_check_ignores_invalid_config_standing_approval(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: tmp\n"
            "consent_mode: standing\n"
            "automation:\n"
            "  approved_scopes: [bogus]\n"
            "  operator: automation:nightly\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            rc, out, err = run([
                "plan", p, "--root", d, "--command", "ci-check", "--live", "--json",
            ])
        self.assertEqual(rc, 0, err)
        consent_block = json.loads(out)["contract"]["operator_consent"]
        self.assertEqual(consent_block["status"], "not-required-read-only")
        self.assertEqual(consent_block["approval_source"], "none")

    def test_plan_live_mutating_standing_config_rejects_invalid_scope(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\nrepo: tmp\n"
            "consent_mode: standing\n"
            "automation:\n"
            "  approved_scopes: [filesystem, bogus]\n"
            "  operator: automation:nightly\n"
            "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            rc, _, err = run(["plan", p, "--root", d, "--command", "ship", "--live", "--json"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown consent scope", err)

    def test_overnight_env_standing_approval_records_source_and_delegate_scope(self):
        p = _write_raw(
            "extends: keel\ncore_version: '^0.6'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            os.environ,
            _isolated_env(
                KEEL_APPROVE_SCOPE="filesystem,git,github", KEEL_OPERATOR="automation:cron"
            ),
            clear=True,
        ):
            rc, out, err = run([
                "overnight", p, "--root", d, "--live", "--json",
                "--consent-mode", "standing",
            ])
        self.assertEqual(rc, 0, err)
        consent_block = json.loads(out)["contract"]["operator_consent"]
        self.assertEqual(consent_block["status"], "approved")
        self.assertEqual(consent_block["approval_source"], "env")
        self.assertEqual(consent_block["consent_record"]["operator"], "automation:cron")
        self.assertEqual(
            consent_block["delegated_agent_scope"]["approved_mutation_scopes"],
            ["filesystem", "git", "github"],
        )


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
        self.assertEqual(
            finding["provenance"]["schema_version"],
            "keel.agent-output-provenance.v1",
        )
        self.assertFalse(finding["provenance"]["trusted_as_instructions"])


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
