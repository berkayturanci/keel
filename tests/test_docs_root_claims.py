"""The root pages and the swarm pages, held to what the code and the build files say.

The 2026-09-29 docs audit found each of these sentences out of step with the thing it
describes. Every test reads the fact from its source — ``pyproject.toml``, the Makefile,
``scripts/find_python.sh``, the files a real ``keel init`` writes, the plugin manifests on
disk, the worker command ``swarm-run`` builds — and holds the page to it, so the page
fails here the next time the two drift apart.
"""

from __future__ import annotations

import contextlib
import io
import re
import shlex
import tempfile
import tomllib
import unittest
from pathlib import Path

from keel import cli, swarm_runtime
from keel.runner import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Whitespace collapsed, so a sentence wrapped across lines still matches."""
    return " ".join(text.split())


def _pyproject() -> dict:
    return tomllib.loads(_read("pyproject.toml"))


def _requirement_name(spec: str) -> str:
    return re.match(r"[A-Za-z0-9_.-]+", spec).group(0)


def _cli(argv: list[str]) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return cli.main(argv)


def _written(argv: list[str]) -> set[str]:
    """The files a real CLI run writes into an empty directory, relative to it."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        rc = _cli([*argv, "--root", d])
        written = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert rc == 0, f"keel {' '.join(argv)} exited {rc}"
    assert written, f"keel {' '.join(argv)} wrote nothing"
    return written


class TheSecurityNotesSayWhatTheCliWrites(unittest.TestCase):
    """SECURITY.md said the CLI, `init` and `install-adapter` included, "only reads" the
    config and ships PyYAML alone — `init` writes the config it reads, `install-adapter`
    writes the adapters, and Windows installs `tzdata` too."""

    @classmethod
    def setUpClass(cls):
        text = _read("SECURITY.md")
        notes = text.split("## Security Notes", 1)[1].split("\nBe aware that:", 1)[0]
        cls.notes = _flat(notes)

    def test_it_names_every_file_init_writes(self):
        for path in sorted(_written(["init"])):
            with self.subTest(path=path):
                self.assertIn(f"`{path}`", self.notes)

    def test_it_names_where_install_adapter_writes(self):
        for agent in ("claude", "skills"):
            # The directory every adapter of that agent lands in: `.claude/commands/keel/`
            # for one, `.agents/skills/` (one `keel-<command>/` each) for the other.
            written = sorted(_written(["install-adapter", agent]))
            common = Path(written[0]).parent
            while not all(Path(p).is_relative_to(common) for p in written):
                common = common.parent
            where = common.as_posix() + "/"
            with self.subTest(agent=agent, where=where):
                self.assertIn(f"`{where}`", self.notes)

    def test_it_names_every_runtime_dependency(self):
        deps = [_requirement_name(d) for d in _pyproject()["project"]["dependencies"]]
        self.assertIn("tzdata", deps, "fixture: the Windows dependency is declared")
        for name in deps:
            with self.subTest(dependency=name):
                self.assertIn(name, self.notes)

    def test_it_no_longer_says_the_writing_commands_only_read(self):
        self.assertNotRegex(self.notes, r"`install-adapter`\)? only reads")


class ContributingMatchesTheBuildFiles(unittest.TestCase):
    """CONTRIBUTING.md named `build` in the dev extra (it holds `bandit`), created a venv
    the resolver does not look for, and left `.keel/project.yaml` out of `make validate`."""

    @classmethod
    def setUpClass(cls):
        cls.text = _flat(_read("CONTRIBUTING.md"))

    def test_the_dev_tools_are_the_dev_extra(self):
        listed = re.search(r"Dev-only tools \(([^)]*)\) live in the `dev` extra", self.text)
        self.assertIsNotNone(listed, "the dev-extra sentence moved")
        named = set(re.findall(r"`([^`]+)`", listed.group(1)))
        extra = {
            _requirement_name(d) for d in _pyproject()["project"]["optional-dependencies"]["dev"]
        }
        self.assertEqual(extra, named)

    def test_the_venv_it_creates_is_the_one_the_resolver_tries_first(self):
        created = re.search(r"python3 -m venv (\S+) && source (\S+)/bin/activate", self.text)
        self.assertIsNotNone(created, "the venv step moved")
        self.assertEqual(created.group(1), created.group(2))
        resolver = re.search(
            r'venv="\$root/([^"]+)/bin/python"', _read("scripts", "find_python.sh")
        )
        self.assertIsNotNone(resolver, "find_python.sh no longer names a repo venv")
        self.assertEqual(resolver.group(1), created.group(1))

    def test_make_validate_is_described_with_every_file_it_validates(self):
        recipe = re.search(r"(?m)^validate:\n\t(.+)$", _read("Makefile")).group(1)
        targets = shlex.split(recipe.split("keel validate", 1)[1])
        self.assertIn(".keel/project.yaml", targets, "fixture: make validate checks the dogfood")
        described = re.search(r"make validate\s+# ([^`]*)`", self.text)
        described = described.group(1) if described else ""
        for target in targets:
            with self.subTest(target=target):
                self.assertIn(target, described)


class ThePluginPageCountsTheManifests(unittest.TestCase):
    """plugin.md said "both JSON manifests"; the repo ships four."""

    def test_it_names_every_json_manifest_the_repo_ships(self):
        manifests = sorted(
            p.relative_to(REPO_ROOT).as_posix() for p in REPO_ROOT.glob(".*-plugin/*.json")
        )
        self.assertEqual(4, len(manifests), manifests)
        text = _flat(_read("docs", "keel", "plugin.md"))
        self.assertIn("all four JSON manifests", text)
        self.assertNotIn("both JSON manifests", text)
        for manifest in manifests:
            with self.subTest(manifest=manifest):
                self.assertIn(f"`{manifest}`", text)


class TheOverviewStatesKeelVisualsFloor(unittest.TestCase):
    """overview.md gave keel-visual's core floor as 1.6.0; its pyproject says 1.15.0."""

    def test_the_floor_is_the_one_keel_visual_declares(self):
        pinned = tomllib.loads(_read("keel-visual", "pyproject.toml"))["project"]["dependencies"]
        floor = next(
            re.fullmatch(r"keel-workflow>=(\S+)", d) for d in pinned if "keel-workflow" in d
        )
        self.assertIsNotNone(floor)
        stated = re.findall(r"`keel-workflow >= ([0-9.]+)`", _read("docs", "keel", "overview.md"))
        self.assertEqual([floor.group(1)], stated)


class TheSwarmPagesDescribeTheSwarmThatShipped(unittest.TestCase):
    """swarm.md described #1268's skipped wave and #1272's `LockError` as current after
    both were fixed; commands.md described team leads, vendor routing and a per-cluster
    panel as what `/keel:swarm` does; keel-visual.md's `swarm` row did not say
    experimental."""

    @classmethod
    def setUpClass(cls):
        cls.swarm = _flat(_read("docs", "keel", "swarm.md"))

    #: The sentences that described the fixed defects as the current behaviour.
    _FIXED_AS_CURRENT = re.compile(
        r"the next, unrelated wave is skipped entirely"
        r"|is a no-op that skips the next wave"
        r"|a second writer raises `LockError`",
    )

    def test_the_fixed_defects_are_not_described_as_current(self):
        found = self._FIXED_AS_CURRENT.search(self.swarm)
        self.assertIsNone(found, found and found.group(0))

    def test_each_fix_is_named_where_the_defect_was(self):
        for pr in ("#1310", "#1312", "#1272"):
            with self.subTest(fix=pr):
                self.assertIn(f"[{pr}]", self.swarm)
        self.assertIn("reported `held`", self.swarm)

    def _swarm_row(self) -> str:
        row = next(
            line
            for line in _read("docs", "keel", "commands.md").splitlines()
            if line.startswith("| **`/keel:swarm`** |")
        )
        return _flat(row)

    def test_commands_md_says_the_worker_is_a_dry_ship(self):
        seen: list[list[str]] = []

        def runner(cmd, cwd):
            seen.append(list(cmd))
            return CommandResult(ok=True, code=0, output="")

        with tempfile.TemporaryDirectory() as d:
            swarm_runtime.execute_cluster_worker(
                ".keel/project.yaml", 7, Path(d), Path(d) / "absent", runner=runner
            )
        self.assertEqual(["keel", "ship"], seen[0][2:4])
        self.assertIn("--dry-run", seen[0])
        self.assertIn("`keel ship --dry-run --json`", self._swarm_row())

    def test_commands_md_labels_the_unbuilt_parts_as_design(self):
        row = self._swarm_row()
        self.assertIn("**Design, not built:**", row)
        design = row.split("**Design, not built:**", 1)[1]
        for part in ("team lead", "AI vendors", "review panel"):
            with self.subTest(part=part):
                self.assertIn(part, design)
        today = row.split("**Design, not built:**", 1)[0]
        for claim in ("spawns one **team lead**", "routes across diverse AI vendors"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, today)

    def test_the_keel_visual_swarm_row_says_experimental(self):
        row = next(
            line
            for line in _read("docs", "keel", "keel-visual.md").splitlines()
            if line.startswith("| `swarm` |")
        )
        self.assertIn("experimental", row.lower())


if __name__ == "__main__":
    unittest.main()
