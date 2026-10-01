"""keel-ship.dev claims only what keel does (docs audit 2026-09-29).

The audit of 2026-09-29 found the site ahead of, or behind, the thing it describes in a
dozen places: a config key the schema rejects (`core` for `core_version`), a
`/keel:ship --issue 128` the adapter does not take, a coverage gate credited to the s8
step that never runs it, a swarm simulator whose waves the planner would never produce,
a jury gate still called "a fail-soft no-op" after #1369 made a lone one block, and an
incident article that said "a day" for eighty-four minutes.

Each check compares a sentence on the site to the thing it is about — the JSON schema,
the adapter frontmatter, the workflow files, `keel.model`, `keel.swarm`'s own planner,
the argparse parser, the CHANGELOG, the git history — never to a list retyped here.
Stdlib-only and offline, like the rest of the suite; the one git-history check skips on
a shallow clone, where the commits it reads are absent.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from datetime import datetime
from pathlib import Path

from keel import cli, gates, model, swarm
from keel.gates import GateOutcome, GateSpec

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE = REPO_ROOT / "website"
COMMANDS_DIR = REPO_ROOT / "src" / "keel" / "adapters" / "commands"


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _site_pages() -> dict[str, str]:
    """Every page, script and text file the site serves, by file name."""
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(SITE.iterdir())
        if p.suffix in {".html", ".js", ".txt"}
    }


def _prose(text: str) -> str:
    """Tags stripped, whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


class TheConfigReferenceNamesSchemaKeys(unittest.TestCase):
    """K67: the site's config table and docs summary named a top-level `core` key.

    The schema calls it `core_version` and rejects unknown keys, so a reader who copied
    the reference got a config keel refuses.
    """

    def setUp(self):
        schema = json.loads(_read("src/keel/schema/project.schema.json"))
        self.keys = set(schema["properties"])
        self.content = _read("website/content.js")

    def _table_keys(self) -> list[str]:
        block = re.search(r"\n  config: \[\n(.*?)\n  \],", self.content, re.S)
        self.assertIsNotNone(block, "content.js has no `config: [` table")
        names = re.findall(r'^\s*\["([^"]+)",', block.group(1), re.M)
        # "timezone · merge_window" is two keys; "knobs.team" is the `knobs` key.
        return [part.split(".")[0] for name in names for part in name.split(" · ")]

    def test_every_config_table_row_is_a_schema_key(self):
        keys = self._table_keys()
        self.assertGreaterEqual(len(keys), 8, "the config table parsed to almost nothing")
        self.assertEqual([], [k for k in keys if k not in self.keys])
        self.assertIn("core_version", keys)

    def test_the_configuration_article_summary_names_schema_keys(self):
        found = re.search(r'summary: "project\.yaml fields: ([^"]+?)\."', self.content)
        self.assertIsNotNone(found, "the configuration article summary moved")
        named = [k.strip() for part in found.group(1).split(",") for k in part.split("/")]
        self.assertGreaterEqual(len(named), 5)
        self.assertEqual([], [k for k in named if k not in self.keys])


class EverySlashCommandOnTheSiteTakesItsFlags(unittest.TestCase):
    """K70: the ship scene captioned itself `$ /keel:ship --issue 128`.

    `tests/test_site_command_examples.py` holds the `cmdExample` map to each adapter's
    `argument-hint`, and nothing else on the site. `/keel:ship` takes its issue numbers
    positionally, so the flagship animation showed an invocation the command rejects.
    This reads every `/keel:<command> …` on every page, script and text file.
    """

    _INVOCATION = re.compile(r"/keel:([a-z][a-z0-9-]*)((?: [^\"'<`\n·;)]*)?)")
    _FLAG = re.compile(r"--[a-z][a-z0-9-]*")
    _HINT = re.compile(r'^argument-hint:\s*"(.*)"\s*$', re.M)

    def _hint_flags(self, command: str) -> set[str] | None:
        source = COMMANDS_DIR / f"{command}.md"
        if not source.exists():
            return None
        found = self._HINT.search(source.read_text(encoding="utf-8"))
        return set(self._FLAG.findall(found.group(1))) if found else None

    def test_every_flag_is_declared_by_that_commands_adapter(self):
        seen, unknown = 0, []
        for page, text in _site_pages().items():
            for command, rest in self._INVOCATION.findall(text):
                flags = set(self._FLAG.findall(rest))
                declared = self._hint_flags(command)
                if not flags or declared is None:
                    continue
                seen += 1
                unknown += [
                    f"{page}: /keel:{command}{rest} ({f})" for f in sorted(flags - declared)
                ]
        self.assertGreaterEqual(seen, 10, "no flagged /keel: invocation found on the site")
        self.assertEqual([], unknown)


class TheJuryGateSentenceIsReadmes(unittest.TestCase):
    """K63/K69: the site still called a missing `jury` binary a fail-soft no-op.

    Since #1369 a jury that cannot run is `SKIPPED` beside another gate and a blocking
    `FAIL` when it is the only gate planned. The README says so in one parenthesis; the
    site's jury sentences must carry the same one.
    """

    def _readme_clause(self) -> str:
        found = re.search(
            r"Without the `jury` binary the s8 run is a no-op (\([^)]*\))", _read("README.md")
        )
        self.assertIsNotNone(found, "README.md lost its missing-binary sentence")
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", found.group(1))

    def test_gates_behave_as_the_sentence_says(self):
        jury_spec = GateSpec(gates.JURY_ID, "builtin", "test", "block")
        build_spec = GateSpec("build", "builtin", "test", "block")
        skipped = GateOutcome(gates.JURY_ID, True, skipped=True)
        alone = gates.lone_jury_cannot_judge((jury_spec,), (skipped,))
        self.assertFalse(alone[0].ok)
        beside = gates.lone_jury_cannot_judge(
            (build_spec, jury_spec), (GateOutcome("build", True), skipped)
        )
        self.assertTrue(beside[1].skipped)

    def test_the_site_carries_the_readme_clause(self):
        clause = self._readme_clause()
        self.assertIn("SKIPPED", clause)
        self.assertIn("blocks", clause)
        # index.html says it twice: the backbone's jury card and the Security view.
        for page, want in (("index.html", 2), ("content.js", 1)):
            with self.subTest(page=page):
                self.assertEqual(
                    want, _read(f"website/{page}").count(f"s8 run is a no-op {clause}")
                )

    def test_no_page_calls_the_absent_jury_fail_soft(self):
        for page, text in _site_pages().items():
            with self.subTest(page=page):
                found = re.search(
                    r"fail-soft no-op when it'?s absent|gate is a fail-soft no-op", _prose(text)
                )
                self.assertIsNone(found, found and found.group(0))


class TheCoveragePageNamesTheStepThatRunsTheGate(unittest.TestCase):
    """K68: coverage.html said keel runs the coverage gate "through the s8 test step".

    keel's own s8 build gate is `make test`, which runs the suite without coverage. The
    `fail_under = 100` gate is the CI step "Test + coverage gate" in the required `test`
    jobs, and the page now names that step.
    """

    def test_s8_does_not_run_coverage(self):
        build = re.search(r'^\s*build_gate_cmd:\s*"([^"]+)"', _read(".keel/project.yaml"), re.M)
        self.assertEqual("make test", build.group(1))
        target = re.search(r"^test:\n((?:\t.*\n)+)", _read("Makefile"), re.M)
        self.assertNotIn("coverage", target.group(1))

    def test_the_page_names_the_ci_step_that_runs_it(self):
        ci = _read(".github/workflows/ci.yml")
        step = re.search(r"- name: (Test \+ coverage gate)\n(.*?)(?=\n      - |\Z)", ci, re.S)
        self.assertIsNotNone(step, "ci.yml has no coverage gate step")
        self.assertIn("coverage report", step.group(2))
        page = _prose(_read("website/coverage.html"))
        self.assertIn(step.group(1), page)
        self.assertNotIn("through the s8 test step", page)


class TheSwarmSimulatorShowsWhatThePlannerWouldPlan(unittest.TestCase):
    """UNCONFIRMED → confirmed: two presets put issues in wave 2 that share no file with
    wave 1, and `keel swarm-plan` would put them in wave 1.

    `build_swarm_plan` forms waves from predicted-file conflicts alone. Each preset is
    fed to it as written, and the wave and `dependsOn` it shows must be the plan's.
    """

    _ISSUE = re.compile(
        r'\{ id: (\d+), title: "[^"]*", files: \[([^\]]*)\],[^}]*?wave: (\d+)'
        r"(?:, dependsOn: \[([^\]]*)\])? \}"
    )

    def _presets(self) -> dict[str, list[tuple[int, tuple[str, ...], int, list[int]]]]:
        text = _read("website/swarm-simulator.js")
        presets = {}
        for key, body in re.findall(r"\n    (\w+): \{\n(.*?)\n    \}", text, re.S):
            presets[key] = [
                (
                    int(i),
                    tuple(re.findall(r'"([^"]+)"', files)),
                    int(wave),
                    [int(d) for d in re.findall(r"\d+", deps or "")],
                )
                for i, files, wave, deps in self._ISSUE.findall(body)
            ]
        return presets

    def test_every_preset_is_the_plan_keel_would_make(self):
        presets = self._presets()
        self.assertEqual({"microservices", "fullstack", "conflict"}, set(presets))
        for key, issues in presets.items():
            self.assertGreaterEqual(len(issues), 3, key)
            plan = swarm.build_swarm_plan(
                [swarm.IssueScope(issue=i, predicted_files=files) for i, files, _, _ in issues],
                swarm_id="site",
            )
            planned = {
                c.issues[0]: (w.wave_index, sorted(c.depends_on_issues))
                for w in plan.waves
                for c in w.clusters
            }
            shown = {i: (wave, sorted(deps)) for i, _, wave, deps in issues}
            with self.subTest(preset=key):
                self.assertEqual(planned, shown)

    def test_no_worker_claims_a_closed_or_merged_pull_request(self):
        """K62, then #1287: the simulated landing is the real one — the pull request merged
        through `keel merge` — and no worker claims a closed or stamped PR."""
        text = _read("website/swarm-simulator.js")
        self.assertIsNone(re.search(r"PR closed|stamped|Merged locally", text))
        self.assertIn("Pull request merged through keel merge", text)
        self.assertIn("A simulation of the design, not a live run", text)

    def test_the_backbone_map_does_not_draw_swarm_to_the_merge(self):
        """UNCONFIRMED → confirmed: the coverage map drew `/keel:swarm` s0→s12, as long
        as `/keel:ship`'s full traversal. A live run now opens one pull request per cluster
        and lands it only through `keel merge` once reviewed (#1400, #1287), so the row says
        that — not the "lands nothing" it said before the live path was built."""
        row = re.search(r'\{ name: "/keel:swarm[^"]*"[^}]*\}', _read("website/home.js"))
        self.assertIsNotNone(row)
        end = int(re.search(r"\bb: (\d+)", row.group(0)).group(1))
        merge = next(i for i, s in enumerate(model.BACKBONE) if s.name == "merge")
        self.assertLess(end, merge)
        self.assertIn("experimental", row.group(0))
        self.assertIn("opens one PR per cluster, landed through keel merge", row.group(0))
        self.assertNotIn("lands nothing", row.group(0))


class TheIntegrationCardsClaimOnlyWhatKeelDoes(unittest.TestCase):
    """K64, K71 and two UNCONFIRMED cards in `website/integrations.js`."""

    def setUp(self):
        self.cards = _read("website/integrations.js")

    def test_agents_skills_is_not_read_by_every_non_claude_host(self):
        """K64: Cursor's manifest names `./skills`, not `.agents/skills`."""
        manifest = json.loads(_read(".cursor-plugin/plugin.json"))
        self.assertNotIn(".agents/skills", json.dumps(manifest))
        self.assertNotIn("every non-Claude agent reads", self.cards)

    def test_keel_installs_no_git_hook(self):
        """K71: the "Pre-Commit Quality Gates" card promised gates "before any commit".

        No module writes a hook or points ``core.hooksPath`` at one with ``git config``.
        The revert-check gate's per-command ``-c core.hooksPath=<empty dir>`` is the
        opposite — it keeps the repository's hooks out of its scratch worktree — so the
        search is for the setting, not the name (#1289 merge).
        """
        setting = r"hooks/pre-commit|config[\"',\s]+core\.hooksPath"
        for path in (REPO_ROOT / "src" / "keel").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertIsNone(re.search(setting, text))
        self.assertIsNotNone(re.search(setting, "git config core.hooksPath .githooks"))
        self.assertIsNotNone(re.search(setting, '["git", "config", "core.hooksPath", d]'))
        self.assertNotIn("before any commit", self.cards)
        self.assertIn("keel installs no git hook", self.cards)

    def test_antigravity_card_names_what_agy_imports(self):
        """UNCONFIRMED → confirmed: "reactive message wakeups" and "AGENTS.md rules" are
        in no keel source; agy imports the root `commands/` and `skills/`."""
        self.assertTrue((REPO_ROOT / "commands").is_dir() and (REPO_ROOT / "skills").is_dir())
        self.assertIsNone(re.search(r"wakeup|AGENTS\.md rules", self.cards, re.I))

    def test_delegate_is_not_on_every_command(self):
        """UNCONFIRMED → confirmed: `--delegate` is on 8 of keel's subcommands."""
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
        takes = {
            name
            for name, p in sub.choices.items()
            if any("--delegate" in a.option_strings for a in p._actions)
        }
        self.assertLess(len(takes), len(sub.choices))
        self.assertNotIn("on any keel command", self.cards)
        named = re.search(r"takes --delegate \(([^)]*) among them\)", self.cards)
        self.assertIsNotNone(named, "the Hermes card no longer names the commands")
        for command in re.split(r", | and ", named.group(1)):
            with self.subTest(command=command):
                self.assertIn(command, takes)


class TheSiteProseMatchesTheRepository(unittest.TestCase):
    """K65, K66 and three UNCONFIRMED claims in `content.js` / `index.html` / `llms.txt`."""

    def setUp(self):
        self.pages = {name: _prose(text) for name, text in _site_pages().items()}

    def _nowhere(self, pattern: str) -> None:
        for page, text in self.pages.items():
            with self.subTest(page=page, pattern=pattern):
                found = re.search(pattern, text, re.I)
                self.assertIsNone(found, found and found.group(0))

    def test_the_curl_installer_needs_python(self):
        """K65: "curl installer with zero dependencies" — it needs Python 3.11+."""
        self.assertIn("Python 3.11 or newer", _read("scripts/install.sh"))
        self._nowhere(r"zero dependencies")

    def test_a_deferral_is_not_audited(self):
        """K66: `--deferral` takes no operator attribution and writes no audit record."""
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
        verify = sub.choices["evidence-verify"]
        options = {o for a in verify._actions for o in a.option_strings}
        self.assertIn("--deferral", options)
        self.assertNotIn("--operator", options)
        self._nowhere(r"audited deferral|audited exception")

    def test_testpypi_is_a_rehearsal_not_a_release_step(self):
        """UNCONFIRMED → confirmed: publish.yml publishes to TestPyPI only on a manual
        dispatch, and the runbook says TestPyPI does not carry keel-workflow."""
        self.assertIn("TestPyPI does not currently contain", _read("docs/keel/release.md"))
        self._nowhere(r"TestPyPI (→|\\u2192) PyPI")

    def test_stale_prs_refreshes_with_a_merge_commit(self):
        """UNCONFIRMED → confirmed: the adapter merges the base branch in; it never rebases."""
        self.assertIn(
            "with a no-fast-forward merge commit",
            _read(f"{COMMANDS_DIR.relative_to(REPO_ROOT)}/stale-prs.md"),
        )
        self._nowhere(r"optionally rebases?\b")

    def test_the_stated_invariants_are_keels(self):
        """UNCONFIRMED → confirmed: the extensions article listed six invariants, adding
        "evidence-bearing steps", which is a command contract, not a `model.INVARIANTS`."""
        content = _read("website/content.js")
        sentence = re.search(r"These invariants are always preserved: ([^.<]+)\.", content)
        self.assertIsNotNone(sentence)
        self.assertEqual(len(model.INVARIANTS), len(sentence.group(1).split(", ")))
        rows = re.search(r"\n  invariants: \[\n(.*?)\n  \],", content, re.S)
        self.assertEqual(len(model.INVARIANTS), len(re.findall(r"^\s*\[", rows.group(1), re.M)))

    def test_the_september_security_row_names_every_september_security_release(self):
        """UNCONFIRMED → confirmed: the 2026-09 row stopped at 1.24.0 and left out 1.24.3's
        keel-visual escaping fix (#1317)."""
        changelog = _read("CHANGELOG.md")
        releases = re.split(r"^## \[", changelog, flags=re.M)[1:]
        september = [
            r.split("]", 1)[0]
            for r in releases
            if re.match(r"[\d.]+\] [-—] 2026-09-", r) and "\n### Security" in r
        ]
        self.assertGreaterEqual(len(september), 3)
        row = re.search(r"<code>2026-09</code><span>(.*?)</span>", _read("website/index.html"))
        self.assertIsNotNone(row)
        for version in september:
            with self.subTest(version=version):
                self.assertIn(f"{version}:", row.group(1))


class TheSiteHandoffNotesDescribeTheSite(unittest.TestCase):
    """K72: `website/README.md` described a version cache, a theme key, a module path, a
    report count and a "Tweaks" block that the site does not have."""

    def setUp(self):
        self.readme = _read("website/README.md")
        self.app = _read("website/app.js")

    def test_the_theme_key_is_the_one_app_js_stores(self):
        stored = re.search(r'store\.set\("([^"]+)", next\)', self.app)
        self.assertIsNotNone(stored)
        self.assertEqual([stored.group(1)], re.findall(r'localStorage\["([^"]+)"\]', self.readme))

    def test_no_version_cache_is_described(self):
        self.assertIn('localStorage.removeItem("keel-version")', self.app)
        self.assertIsNone(re.search(r"cached[^.]*in localStorage|cached value shown", self.readme))

    def test_every_named_source_path_exists(self):
        paths = re.findall(r"`(src/keel/[^`]+\.py)`", self.readme)
        self.assertGreaterEqual(len(paths), 1)
        self.assertEqual([], [p for p in paths if not (REPO_ROOT / p).exists()])

    def test_the_audit_report_count_is_the_number_of_reports(self):
        words = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
        stated = re.search(r"the (\w+) audit reports", self.readme)
        self.assertIsNotNone(stated)
        reports = list((REPO_ROOT / "docs" / "security").glob("*-security-audit.md"))
        self.assertEqual(len(reports), words[stated.group(1)])

    def test_no_cleanup_is_owed_for_a_block_the_page_does_not_have(self):
        self.assertNotIn("tweaks-root", _read("website/index.html"))
        self.assertNotIn("tweaks-root", self.readme)


class TheSilentRevertArticleIsTheHistory(unittest.TestCase):
    """K73: the article said 1.15.0 was gone from `main` "a day later".

    The release commit (#766) landed at 09:07:58 and the squash that reverted it (#765)
    at 10:32:01 the same morning, 2026-08-16. The elapsed time is read from git.
    """

    RELEASE, REVERT = "f1a89740", "3a5ec29e"

    def _committed(self, sha: str) -> datetime:
        out = subprocess.run(
            ["git", "show", "-s", "--format=%cI", sha],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            self.skipTest(f"{sha} is not in this clone (shallow checkout)")
        return datetime.fromisoformat(out.stdout.strip())

    def test_the_elapsed_time_is_the_commits(self):
        released, reverted = self._committed(self.RELEASE), self._committed(self.REVERT)
        diff = subprocess.run(
            ["git", "show", self.REVERT, "--", "pyproject.toml"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertIn('-version = "1.15.0"', diff)
        minutes = int((reverted - released).total_seconds() // 60)
        text = _read("website/silent-revert.html")
        self.assertIn(f"We published version 1.15.0. {minutes} minutes later", text)
        self.assertNotIn("A day later", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
