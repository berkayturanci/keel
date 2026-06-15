"""Tests for the repository-local Codex adapter."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from keel.install import adapter_names

REPO_ROOT = Path(__file__).resolve().parents[1]
CODEX_DIR = REPO_ROOT / ".codex"
HOOK = CODEX_DIR / "hooks/deny-dangerous-shell.sh"


class TestCodexAdapter(unittest.TestCase):
    def test_config_enables_hooks_without_unsafe_runtime_defaults(self):
        config = tomllib.loads((CODEX_DIR / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["features"]["hooks"], True)
        self.assertNotIn("sandbox_mode", config)
        self.assertNotIn("approval_policy", config)
        self.assertNotEqual(config.get("sandbox"), "danger-full-access")
        self.assertNotEqual(config.get("approval"), "never")

    def test_hooks_json_points_to_repo_local_deny_hook(self):
        hooks = json.loads((CODEX_DIR / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for block in hooks["hooks"]["PreToolUse"]
            for hook in block["hooks"]
        ]
        self.assertIn("./.codex/hooks/deny-dangerous-shell.sh", commands)
        mode = HOOK.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)

    def test_readme_maps_every_packaged_adapter_to_a_skill(self):
        readme = (CODEX_DIR / "README.md").read_text(encoding="utf-8")
        for adapter in adapter_names():
            command = adapter.removesuffix(".md")
            with self.subTest(command=command):
                self.assertIn(f"`{command}` or `/keel:{command}`", readme)
                self.assertIn(f".agents/skills/keel-{command}/SKILL.md", readme)
        self.assertIn("keel plan --command <cmd>", readme)
        self.assertIn("operator_consent", readme)

    @unittest.skipIf(
        sys.platform == "win32",
        "the deny hook is a POSIX shell script; Windows cannot exec it directly",
    )
    def test_deny_hook_blocks_dangerous_shell_and_allows_safe_read(self):
        env = {**os.environ, "PATH": os.environ.get("PATH", "")}
        blocked_command = "git reset " + "--hard"
        dangerous = subprocess.run(
            [str(HOOK)],
            input=json.dumps({"tool_input": {"cmd": blocked_command}}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertNotEqual(dangerous.returncode, 0)
        self.assertIn("BLOCKED", dangerous.stderr)

        safe = subprocess.run(
            [str(HOOK)],
            input=json.dumps({"tool_input": {"cmd": "git status --short"}}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(safe.returncode, 0, safe.stderr)


if __name__ == "__main__":
    unittest.main()
