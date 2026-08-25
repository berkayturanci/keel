"""Every scanner this project's gates invoke must be installed where they run (#927).

The `bandit` preset was enabled in `.keel/project.yaml` and `bandit` was never
added to the `dev` extras, so in CI the gate ran `bandit: command not found` and
reported FAIL. It did that on **all 41 PRs merged in one week**, and because the
preset is `suggest`-severity it never blocked — so a security gate that scanned
nothing looked exactly like one that scanned and grumbled.

A permanently red gate is not a signal. The structural half of the fix is here:
enabling a preset without declaring its tool now fails a test rather than
producing a red column everyone learns to skip.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from keel import config as cfg
from keel.gates import POLICY_PACK_PRESETS

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Commands a runner is entitled to assume, so they need no dependency entry.
_ASSUMED_ON_PATH = frozenset({"make", "python", "python3", "git", "sh", "bash"})


def _dev_dependencies() -> set[str]:
    """Distribution names in the ``dev`` extra, normalised and version-stripped."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]["dev"]
    return {re.split(r"[<>=!~\[ ]", entry, maxsplit=1)[0].strip().lower() for entry in extras}


def _enabled_presets() -> list[str]:
    config = cfg.load_config(str(REPO_ROOT / ".keel" / "project.yaml"))
    pack = config.policy_pack if isinstance(config.policy_pack, dict) else {}
    presets = pack.get("presets") or []
    return [p for p in presets if isinstance(p, str)]


class EveryEnabledGateToolIsDeclared(unittest.TestCase):
    def test_each_enabled_preset_has_its_tool_in_dev_extras(self):
        declared = _dev_dependencies()
        enabled = _enabled_presets()

        missing = []
        for preset in enabled:
            entry = POLICY_PACK_PRESETS.get(preset)
            if entry is None:
                continue  # an unknown preset is a config error, not this test's job
            tool = entry[3].split()[0]
            if tool in _ASSUMED_ON_PATH or tool.lower() in declared:
                continue
            missing.append((preset, tool))

        self.assertEqual(
            [], missing,
            "a policy-pack preset is enabled whose tool is not in the dev extras; "
            "the gate will report `command not found` wherever it runs: "
            f"{missing}",
        )

    def test_the_check_has_something_to_check(self):
        """Keeps the assertion above from passing on an empty preset list."""
        self.assertIn("bandit", _enabled_presets())

    def test_the_knob_commands_name_tools_a_runner_has(self):
        """`build_gate_cmd`/`lint_cmd` are project knobs, not presets, but the same
        rule applies: a gate whose command is absent reports FAIL for a reason that
        has nothing to do with the code."""
        config = cfg.load_config(str(REPO_ROOT / ".keel" / "project.yaml"))
        declared = _dev_dependencies()

        for label, command in (
            ("build_gate_cmd", config.knobs.build_gate_cmd),
            ("lint_cmd", getattr(config.knobs, "lint_cmd", None)),
        ):
            if not command:
                continue
            tool = command.split()[0]
            with self.subTest(knob=label):
                self.assertTrue(
                    tool in _ASSUMED_ON_PATH or tool.lower() in declared,
                    f"{label} runs `{tool}`, which is neither assumed on PATH nor "
                    "declared in the dev extras",
                )


class NoStaleNosecSuppressions(unittest.TestCase):
    """A `# nosec` on a line bandit does not flag is what bandit warns about, and
    the warning is what made the gate non-zero on the version CI would install.

    Five of these had accumulated (`consent.py` ×3, `consentverify.py`,
    `contracts.py`), each on a boolean or a value B105 never flagged. Removing a
    suppression is safe only when the finding does not then appear, so that is
    what this asserts — per suppression, not in aggregate.
    """

    def test_every_remaining_nosec_suppresses_a_finding_that_exists(self):
        # Mutating this test faithfully is harder than it looks. bandit does not
        # register a `# nosec` on a line it never produced a finding for, so
        # appending one to an arbitrary statement changes nothing and the test
        # passes — twice, while this was being written. The real shape is the
        # historical one: `# nosec B105` on the `"secret_*": <value>` entries in
        # `consent.py`, which stopped being flagged when those values changed.
        # Restore those three and this goes red.
        import shutil
        import subprocess

        if shutil.which("bandit") is None:  # pragma: no cover - tool-present in CI
            self.skipTest("bandit is not installed")

        tracked = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.split()
        sources = [p for p in tracked if not p.startswith("tests/")]
        self.assertTrue(sources, "no sources to scan; the check would be vacuous")

        # Hand bandit the tracked sources rather than `-r .` (#961). The tree
        # version needed an exclusion per artefact directory — tests, .venv,
        # venv, node_modules, site-packages, and a post-filter for
        # .claude/worktrees — and still went red on the first one nobody had
        # listed: a local `build/`, gitignored, so invisible in CI and red only
        # for contributors who had built. That is a blocklist standing in for a
        # definition; `sources` is the definition, and it was already computed
        # here for the vacuity guard.
        probe = subprocess.run(
            ["bandit", "-ll", *sources],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )

        stale = [
            line.strip() for line in probe.stderr.splitlines()
            if "nosec encountered" in line
        ]
        self.assertEqual(
            [], stale,
            "a `# nosec` sits on a line bandit does not flag. Bandit warns about "
            "it and exits non-zero, which is how this gate stayed red for months: "
            f"{stale}",
        )


if __name__ == "__main__":
    unittest.main()
