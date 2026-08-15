"""Unit tests for the VS Code and Cursor extension manifest and scripts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
