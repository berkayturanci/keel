"""Consumer-neutrality checks for packaged workflow surfaces."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CORE_WORKFLOW_SURFACES = (
    REPO_ROOT / "src/keel/adapters/commands",
    REPO_ROOT / "adapters",
    REPO_ROOT / "commands",
    REPO_ROOT / "GEMINI.md",
    REPO_ROOT / "docs/keel/commands.md",
    REPO_ROOT / "docs/keel/configuration.md",
    REPO_ROOT / "docs/keel/consumer-neutrality.md",
)

CONSUMER_SPECIFIC_TERMS = (
    "smartinventory",
    "eventoid",
    "firebase",
    "realm",
    "billing",
    "crashlytics",
    "play console",
    "adb",
    "espresso",
    "kover",
    "gradle",
    "flutter",
)


def _files_to_scan() -> list[Path]:
    files: list[Path] = []
    for surface in CORE_WORKFLOW_SURFACES:
        if surface.is_dir():
            files.extend(p for p in surface.rglob("*") if p.is_file())
        else:
            files.append(surface)
    return sorted(files)


class TestConsumerNeutrality(unittest.TestCase):
    def test_packaged_workflow_surfaces_do_not_embed_consumer_policy(self):
        pattern = re.compile(
            "|".join(rf"\b{re.escape(term)}\b" for term in CONSUMER_SPECIFIC_TERMS),
            re.IGNORECASE,
        )
        offenders: list[str] = []
        for path in _files_to_scan():
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if match := pattern.search(line):
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(f"{rel}:{line_no}: {match.group(0)}")

        self.assertEqual(
            offenders,
            [],
            "Consumer-specific literals belong in project config/extensions, not core "
            "workflow surfaces.",
        )

    def test_gemini_entrypoint_points_to_shared_skill_surface(self):
        text = (REPO_ROOT / "GEMINI.md").read_text(encoding="utf-8")
        self.assertIn(".agents/skills/keel-<cmd>/SKILL.md", text)
        self.assertIn(".agents/skills/keel-ship/SKILL.md", text)
        self.assertNotIn("adapters/gemini", text)
        self.assertNotIn("adapters/agy", text)


if __name__ == "__main__":
    unittest.main()
