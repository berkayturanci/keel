"""Unit tests for the VS Code and Cursor extension manifest and scripts."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from keel import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = REPO_ROOT / "editors" / "vscode"


class TestEditorExtension(unittest.TestCase):
    def test_package_json_structure_and_commands(self):
        pkg_path = EXT_DIR / "package.json"
        self.assertTrue(pkg_path.exists(), "editors/vscode/package.json must exist")

        data = json.loads(pkg_path.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "keel-vscode")
        self.assertEqual(data["publisher"], "berkayturanci")
        self.assertEqual(data["main"], "./extension.js")

        # Required commands present
        cmds = {cmd["command"]: cmd["title"] for cmd in data["contributes"]["commands"]}
        self.assertIn("keel.ship", cmds)
        self.assertIn("keel.swarm", cmds)
        self.assertIn("keel.window", cmds)
        self.assertIn("keel.gates", cmds)
        self.assertIn("keel.cost", cmds)
        self.assertIn("keel.visual", cmds)

        # Activation events
        activations = data["activationEvents"]
        self.assertIn("workspaceContains:.keel/project.yaml", activations)

    def test_extension_js_and_readme_present(self):
        ext_js = EXT_DIR / "extension.js"
        self.assertTrue(ext_js.exists(), "editors/vscode/extension.js must exist")
        content = ext_js.read_text(encoding="utf-8")
        self.assertIn("activate", content)
        self.assertIn("deactivate", content)
        self.assertIn("statusBarItem", content)

        readme = EXT_DIR / "README.md"
        self.assertTrue(readme.exists(), "editors/vscode/README.md must exist")
        self.assertIn("VS Code", readme.read_text(encoding="utf-8"))


#: Loads the real `extension.js` under node with a stub `vscode` module, activates it
#: against a workspace, and reports what the status bar settled on once the
#: `keel window` call it makes has answered. `keel.executablePath` points at *this*
#: tree's keel, so the extension's argv meets the real parser and the real output.
_DRIVER = r"""
const Module = require("module");
const [extPath, workspace, keelCmd] = process.argv.slice(2);
const item = { text: "", tooltip: "", shown: false,
  show() { this.shown = true; }, hide() { this.shown = false; } };
const watcher = { onDidChange() {}, onDidCreate() {}, onDidDelete() {} };
const vscode = {
  workspace: {
    workspaceFolders: [{ uri: { fsPath: workspace } }],
    getConfiguration: () => ({ get: (key) => (key === "executablePath" ? keelCmd : undefined) }),
    createFileSystemWatcher: () => watcher,
  },
  window: { createStatusBarItem: () => item, showInformationMessage() {} },
  commands: { registerCommand: () => ({}) },
  env: { openExternal() {} },
  Uri: { parse: (u) => u },
  StatusBarAlignment: { Left: 1 },
  ThemeColor: function (id) { this.id = id; },
};
const load = Module._load;
Module._load = function (request, ...rest) {
  return request === "vscode" ? vscode : load.call(this, request, ...rest);
};
const ext = require(extPath);
ext.activate({ subscriptions: [] });
const started = Date.now();
(function wait() {
  if (item.shown || Date.now() - started > 50000) {
    ext.deactivate();
    console.log(JSON.stringify({ text: item.text, tooltip: item.tooltip }));
    return;
  }
  setTimeout(wait, 50);
})();
"""

NODE = shutil.which("node")

_BASE_CONFIG = (
    "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: acme/example\n"
    "gates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
)


def _window_around_now(open_now: bool) -> str:
    """A UTC merge window two hours clear of the current minute on either side."""
    now = datetime.now(UTC)
    start, end = (now - timedelta(hours=2), now + timedelta(hours=2))
    if not open_now:
        start, end = (now + timedelta(hours=2), now + timedelta(hours=4))
    return f"{start:%H:%M}-{end:%H:%M}"


class TheWindowCallMeetsTheRealParser(unittest.TestCase):
    """The argv the extension builds, read out of the file and handed to keel's parser.

    The extension used to run `keel window .keel/project.yaml --json`; `keel window` has
    no `--json`, so every refresh exited 2 (docs audit 2026-09-29).
    """

    def test_the_window_argv_parses(self):
        source = (EXT_DIR / "extension.js").read_text(encoding="utf-8")
        found = re.search(r"^const WINDOW_ARGS = (\[.*?\]);$", source, re.MULTILINE)
        self.assertIsNotNone(found, "extension.js must declare WINDOW_ARGS as a literal")
        argv = json.loads(found.group(1))
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                args = cli.build_parser().parse_args(argv)
        except SystemExit:
            self.fail(f"keel rejects the extension's argv {argv}: {stderr.getvalue()}")
        self.assertIs(args.func, cli._cmd_window)
        self.assertEqual(args.path, ".keel/project.yaml")


@unittest.skipUnless(NODE, "needs node to execute the extension")
class TheStatusBarFollowsWhatKeelWindowSays(unittest.TestCase):
    """The real `extension.js`, activated under node against the real `keel window`.

    Before the fix every case below rendered `Keel: Open`: the `--json` call failed,
    and a failure fell back to "open".
    """

    def _status_bar(self, config: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            (workspace / ".keel").mkdir(parents=True)
            (workspace / ".keel" / "project.yaml").write_text(config, encoding="utf-8")
            driver = Path(tmp) / "drive.js"
            driver.write_text(_DRIVER, encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
            done = subprocess.run(
                [
                    NODE,
                    str(driver),
                    str(EXT_DIR / "extension.js"),
                    str(workspace),
                    f'"{sys.executable}" -m keel',
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=120,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_closed_window_shows_the_night_lock(self):
        config = f"{_BASE_CONFIG}timezone: UTC\nmerge_window: '{_window_around_now(False)}'\n"
        bar = self._status_bar(config)
        self.assertEqual(bar["text"], "$(lock) Keel: Night Lock")
        self.assertIn("merge window CLOSED", bar["tooltip"])

    def test_an_open_window_shows_open(self):
        config = f"{_BASE_CONFIG}timezone: UTC\nmerge_window: '{_window_around_now(True)}'\n"
        bar = self._status_bar(config)
        self.assertEqual(bar["text"], "$(git-merge) Keel: Open")
        self.assertIn("merge window OPEN", bar["tooltip"])

    def test_no_configured_window_says_so(self):
        bar = self._status_bar(_BASE_CONFIG)
        self.assertEqual(bar["text"], "$(git-merge) Keel: No Window")

    def test_a_failing_keel_is_unknown_not_open(self):
        bar = self._status_bar("not: [valid\n")
        self.assertEqual(bar["text"], "$(question) Keel: Window ?")
        self.assertIn("Could not read the merge window", bar["tooltip"])


if __name__ == "__main__":
    unittest.main()
