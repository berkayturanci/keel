"""The adapter must not carry a label-composition rule (issue #1013).

`keel.agents.attribution()` defines what a vendor/model pair is labelled. The ship
adapter used to restate that algorithm in prose — "drop a trailing numeric run on
non-hyphenated families" — and a host that followed the prose instead of calling the
CLI wrote `agent:gemini` / `model:gemini` for a run keel calls `agent:agy` /
`model:gemini-3`. Because the same host also wrote the ledger, the evidence
cross-check compared its guess to its own guess and passed.

These tests pin the rule that replaced it: **ask core, use the answer verbatim**. They
read the adapter *source* and every generated surface, because a stale generated copy
is what a host actually executes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The adapter source plus every surface `make plugin && make adapters` generates.
SHIP_SURFACES = (
    REPO_ROOT / "src/keel/adapters/commands/ship.md",
    REPO_ROOT / "commands/ship.md",
    REPO_ROOT / ".claude/commands/keel/ship.md",
    REPO_ROOT / ".agents/skills/keel-ship/SKILL.md",
)

#: Every adapter source plus its generated copies. The ban below covers them all: a
#: standalone adapter that restates the rule is followed exactly as the ship one was —
#: `/keel:implement` told its host that attribution "is `agent:<vendor>` plus a versionless
#: `model:<base>`" after the ship adapter had stopped saying it (docs audit 2026-09-29).
ADAPTER_NAMES = tuple(
    sorted(path.stem for path in (REPO_ROOT / "src/keel/adapters/commands").glob("*.md"))
)
ALL_SURFACES = tuple(
    path
    for name in ADAPTER_NAMES
    for path in (
        REPO_ROOT / f"src/keel/adapters/commands/{name}.md",
        REPO_ROOT / f"commands/{name}.md",
        REPO_ROOT / f".claude/commands/keel/{name}.md",
        REPO_ROOT / f".agents/skills/keel-{name}/SKILL.md",
    )
)

#: Fragments of the label-composition algorithm. Any of them in the adapter means a
#: host is being told to derive a label rather than to ask for one.
COMPOSITION_RULES = (
    "drop a trailing numeric run",
    "dropping a `.minor`",
    "versionless `model:<base>`",
    "`agent:<vendor>` (i.e.",
)


def _surfaces(paths=SHIP_SURFACES):
    return [(path, path.read_text(encoding="utf-8")) for path in paths if path.exists()]


class TheAdapterDoesNotComposeLabels(unittest.TestCase):
    def test_the_surfaces_exist(self):
        # If a surface is renamed, the greps below would silently check nothing.
        self.assertEqual(len(_surfaces()), len(SHIP_SURFACES))

    def test_no_label_composition_rule_remains(self):
        offenders = [
            f"{path.relative_to(REPO_ROOT)}: {rule!r}"
            for path, text in _surfaces()
            for rule in COMPOSITION_RULES
            if rule in text
        ]

        self.assertEqual(
            [],
            offenders,
            "the ship adapter restates keel's label algorithm in prose; a host that "
            "follows it instead of `keel attribution` will drift from the vocabulary "
            "the evidence gate enforces (#1013)",
        )

    def test_every_adapter_surface_exists(self):
        # A standalone adapter is a copy an agent executes too; if one went missing the
        # sweep below would pass over it silently.
        self.assertGreater(len(ADAPTER_NAMES), 10)
        self.assertEqual(len(_surfaces(ALL_SURFACES)), len(ALL_SURFACES))

    def test_no_adapter_composes_a_label(self):
        offenders = [
            f"{path.relative_to(REPO_ROOT)}: {rule!r}"
            for path, text in _surfaces(ALL_SURFACES)
            for rule in COMPOSITION_RULES
            if rule in text
        ]

        self.assertEqual(
            [],
            offenders,
            "an adapter restates keel's label algorithm in prose; point it at "
            "`keel attribution` instead (#1013, docs audit 2026-09-29)",
        )

    def test_an_adapter_that_records_attribution_asks_the_cli(self):
        # Any adapter that tells its host to label a run must say where the label comes
        # from — the CLI, not a rule the host applies.
        labelling = [
            (path, text)
            for path, text in _surfaces(ALL_SURFACES)
            if "`agent:" in text or "Attribution" in text
        ]
        # Guards the guard: `/keel:ship` and `/keel:implement` both label runs, so a
        # trigger that matches nothing would make the assertion below vacuous.
        self.assertGreaterEqual(len(labelling), 8)
        missing = [
            str(path.relative_to(REPO_ROOT))
            for path, text in labelling
            if "keel attribution" not in text
        ]
        self.assertEqual([], missing, "these adapters label a run without asking the CLI")

    def test_the_adapter_points_at_the_cli_instead(self):
        for path, text in _surfaces():
            with self.subTest(surface=path.name):
                self.assertIn("keel attribution", text)

    def test_the_adapter_tells_the_run_to_post_its_provenance(self):
        for path, text in _surfaces():
            with self.subTest(surface=path.name):
                self.assertIn("--artifact ship-provenance", text)
                self.assertIn("artifact_bodies.ship_provenance", text)


if __name__ == "__main__":
    unittest.main()
