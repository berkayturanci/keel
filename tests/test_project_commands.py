"""Tests for project-provided command declarations."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from keel import cli, project_commands
from keel import config as cfg

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
REPO_ROOT = PROJECTS.parent


class TestProjectCommands(unittest.TestCase):
    def test_list_prefers_project_commands_and_keeps_legacy_routing(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        commands = project_commands.list_project_commands(config)
        by_name = {command.name: command for command in commands}
        self.assertIn("android-build", by_name)
        self.assertIn("web-check", by_name)
        self.assertIn("ui-test", by_name)
        self.assertIn("post-issue-1-regression", by_name)
        self.assertIn("device-smoke", by_name)
        self.assertEqual(by_name["ui-test"].required_capabilities, ("shell", "adb"))
        self.assertEqual(by_name["ui-test"].optional_capabilities, ("browser",))
        self.assertEqual(by_name["device-smoke"].source, "policy_pack.command_routing")

    def test_get_project_command(self):
        config = cfg.load_config(PROJECTS / "example-android.yaml")
        self.assertEqual(
            project_commands.get_project_command(config, "web-check").command,
            ".keel/commands/web-check",
        )
        self.assertIsNone(project_commands.get_project_command(config, "ship"))

    def test_non_map_declarations_are_ignored(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.6",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="make test"),
            policy_pack={
                "project_commands": ["bad"],
                "command_routing": {"valid": "bad", "smoke": {"required_capabilities": []}},
            },
        )
        commands = project_commands.list_project_commands(config)
        self.assertEqual([command.name for command in commands], ["smoke"])

    def test_project_command_overrides_legacy_routing_name(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.6",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="make test"),
            policy_pack={
                "project_commands": {"smoke": {"command": ".keel/commands/smoke"}},
                "command_routing": {"smoke": {"command": ".legacy/smoke"}},
            },
        )
        commands = project_commands.list_project_commands(config)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].command, ".keel/commands/smoke")


class TestProjectCommandsCli(unittest.TestCase):
    def test_human_list(self):
        out = _run(["project-commands", str(PROJECTS / "example-android.yaml")])
        self.assertIn("project commands:", out)
        self.assertIn("ui-test -> .keel/commands/ui-test", out)
        self.assertIn("required=shell,adb", out)

    def test_json_list(self):
        out = _run(["project-commands", str(PROJECTS / "example-android.yaml"), "--json"])
        data = json.loads(out)
        names = {command["name"] for command in data["project_commands"]}
        self.assertIn("android-build", names)
        self.assertIn("web-check", names)

    def test_plan_json_can_target_project_command(self):
        out = _run([
            "plan", str(PROJECTS / "example-android.yaml"), "--root", str(REPO_ROOT),
            "--command", "ui-test", "--json",
        ])
        data = json.loads(out)
        self.assertEqual(data["contract"]["command"], "ui-test")
        self.assertEqual(data["contract"]["graph"][0]["source"], "project_command")
        self.assertIn("adb", data["contract"]["required_capabilities"])
        self.assertIn("browser", data["contract"]["optional_capabilities"])

    def test_capabilities_can_evaluate_project_command(self):
        rc, out, _ = _run_raw([
            "capabilities", "--project", str(PROJECTS / "example-android.yaml"),
            "--for", "ui-test", "--json",
        ])
        self.assertIn(rc, (0, 1))
        data = json.loads(out)
        self.assertIn("adb", data["evaluation"]["required"])
        self.assertIn("browser", data["evaluation"]["optional"])

    def test_human_list_when_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.yaml"
            path.write_text(
                "\n".join([
                    "extends: keel",
                    "core_version: '^0.6'",
                    "owner: owner",
                    "repo: repo",
                    "base_branch: main",
                    "merge_window: '00:00-23:59'",
                    "timezone: UTC",
                    "knobs:",
                    "  build_gate_cmd: make test",
                    "gates: []",
                    "extensions: {}",
                    "policy_pack:",
                    "  name: empty",
                    "",
                ]),
                encoding="utf-8",
            )
            out = _run(["project-commands", str(path)])
        self.assertEqual(out.strip(), "project commands: none")

    def test_missing_config_reports_error(self):
        rc, _, err = _run_raw(["project-commands", "/no/such/project.yaml"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.yaml"
            path.write_text("project_id: broken\n", encoding="utf-8")
            rc, _, err = _run_raw(["project-commands", str(path)])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)

    def test_human_list_with_optional_only_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.yaml"
            path.write_text(
                _minimal_config_yaml([
                    "  project_commands:",
                    "    browser-smoke:",
                    "      command: .keel/commands/browser-smoke",
                    "      optional_capabilities: [browser]",
                ]),
                encoding="utf-8",
            )
            out = _run(["project-commands", str(path)])
        self.assertIn("browser-smoke -> .keel/commands/browser-smoke (optional=browser)", out)


def _run(argv: list[str]) -> str:
    rc, out, err = _run_raw(argv)
    if rc != 0:
        raise AssertionError(f"rc={rc}, stderr={err}")
    return out


def _run_raw(argv: list[str]) -> tuple[int, str, str]:
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _minimal_config_yaml(policy_pack_lines: list[str]) -> str:
    return "\n".join([
        "extends: keel",
        "core_version: '^0.6'",
        "owner: owner",
        "repo: repo",
        "base_branch: main",
        "merge_window: '00:00-23:59'",
        "timezone: UTC",
        "knobs:",
        "  build_gate_cmd: make test",
        "gates: []",
        "extensions: {}",
        "policy_pack:",
        "  name: test",
        *policy_pack_lines,
        "",
    ])


if __name__ == "__main__":
    unittest.main()
