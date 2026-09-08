"""`keel-onboard` reaches every agent, including the ones that read no manifest (#1137).

`agy plugin install https://github.com/berkayturanci/keel` imported 17 commands and
**zero** skills:

    [ok]    keel
            - skills      : skipped (not found)
            ✔ commands    : 17 processed (converted to skills)

`.claude-plugin/plugin.json` declared `"skills": "./skill"` and it was correct — agy
simply does not read it. It discovers components **only by root-level directory
convention**: root `skills/`, root `commands/`, root `agents/`, root `mcp_config.json`,
root `hooks.json`, and nothing else. Verified with throwaway plugins fed to
`agy plugin validate` (agy 2026.09.x, macOS arm64): `skill/demo/SKILL.md` with
`"skills": "./skill"` is skipped, `skills/demo/SKILL.md` is processed, and
`.agents/skills/demo/SKILL.md` is skipped.

So `keel-onboard` — the one skill a new user needs first — was missing on Antigravity,
and undiagnosable: they install keel, see `keel-cmd-ship`, and have no guided setup path.

**Why this move is safe, which was the open question.** Adding a root `skills/` beside
`commands/` normally doubles the surface: agy converts each command into a skill named
`keel-cmd-<name>`, so a skill *and* a command of the same name give a reader two entries
for one job with different descriptions (#1138). That does not happen here, and the
reason is structural rather than lucky: `skills/` holds exactly `keel-onboard`, and
`onboard` is **not** one of the 17 commands. Measured on the real tree —

===========================  ==========================  =====================
layout                       ``skills``                  ``commands``
===========================  ==========================  =====================
``skill/`` + ``./skill``     ``skipped (not found)``     ``17 processed``
``skills/`` + ``./skills``   ``1 processed``             ``17 processed``
===========================  ==========================  =====================

— 18 entries, no name appearing twice. `test_no_skill_shares_a_name_with_a_command` is
what keeps it that way: the day someone adds `skills/keel-ship/`, this fails and #1138's
decision has to be made rather than discovered by a user.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from keel import install

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The name agy discovers by convention. Not configurable, and not read from a manifest —
#: that is exactly the defect this pins.
CONVENTIONAL_SKILLS_DIR = "skills"
MANIFESTS = (install.PLUGIN_MANIFEST, install.CODEX_PLUGIN_MANIFEST)


class TheSkillDirectoryIsFoundWithoutReadingAManifest(unittest.TestCase):
    def _manifests(self):
        for name in MANIFESTS:
            path = REPO_ROOT / name
            self.assertTrue(path.is_file(), f"{name} is missing")
            yield name, json.loads(path.read_text(encoding="utf-8"))

    def _skills(self) -> list[Path]:
        return sorted((REPO_ROOT / CONVENTIONAL_SKILLS_DIR).glob("*/SKILL.md"))

    def test_the_directory_is_at_the_root_under_the_conventional_name(self):
        self.assertTrue(
            (REPO_ROOT / CONVENTIONAL_SKILLS_DIR).is_dir(),
            f"{CONVENTIONAL_SKILLS_DIR}/ is not at the repository root",
        )
        self.assertTrue(self._skills(), f"{CONVENTIONAL_SKILLS_DIR}/ holds no <name>/SKILL.md")

    def test_the_onboarding_skill_is_the_one_in_there(self):
        """It is the skill whose absence a first-time Antigravity user cannot diagnose."""
        self.assertIn("keel-onboard", [path.parent.name for path in self._skills()])

    def test_the_old_singular_directory_is_gone(self):
        """Two directories would be worse than one wrong one: agy would read the
        conventional one and the manifest-reading agents the other, and they could drift
        without anything noticing."""
        self.assertFalse((REPO_ROOT / "skill").exists(), "skill/ and skills/ both exist")

    def test_every_manifest_still_names_that_same_directory(self):
        """Claude Code and Codex resolve the declared path; the convention is not enough
        for them, just as the declaration was not enough for agy."""
        for name, manifest in self._manifests():
            with self.subTest(manifest=name):
                declared = manifest.get("skills")
                self.assertIsInstance(declared, str, f"{name} declares no skills path")
                self.assertEqual(
                    (REPO_ROOT / declared.lstrip("./")).resolve(),
                    (REPO_ROOT / CONVENTIONAL_SKILLS_DIR).resolve(),
                )

    def test_no_skill_shares_a_name_with_a_command(self):
        """agy converts every root command into a skill named ``keel-cmd-<name>``, so a
        root skill with a command's name puts two entries for one job in front of a
        reader — the trap #1138 describes. This move avoids it because `keel-onboard` has
        no command twin; the day that stops being true, decide #1138 first."""
        commands = {
            path.stem for path in (REPO_ROOT / install.PLUGIN_COMMANDS_DIR).glob("*.md")
        }
        self.assertTrue(commands, "no commands found — the check is vacuous")
        skills = {path.parent.name for path in self._skills()}
        prefixed = {f"{install.SKILL_PREFIX}{command}" for command in commands}

        self.assertEqual(set(), skills & commands, "a skill and a command share a name")
        self.assertEqual(set(), skills & prefixed, "a skill duplicates a command's skill name")

    def test_the_curated_skills_stay_where_the_agents_standard_reads_them(self):
        """`.agents/skills/` is not moved by this: it is the cross-agent standard's path,
        read by Codex and Gemini, and agy ignores it either way (measured)."""
        curated = sorted((REPO_ROOT / install.SKILLS_DIR).glob("keel-*/SKILL.md"))

        self.assertTrue(curated, f"{install.SKILLS_DIR} is empty")
        self.assertNotIn("keel-onboard", [path.parent.name for path in curated])


if __name__ == "__main__":
    unittest.main()
