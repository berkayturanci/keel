"""The CLI, evidence and release pages, checked against the code (docs audit 2026-09-29).

Each list a page states is read here from the thing it describes — a real `keel ship`
run on a throwaway repository, the argparse parser, the source's own return statements
and error codes, the release-surface table, the workflow file — and never retyped in
this file, which would drift the same way the prose did. Everything is offline except
what `keel ship` itself probes (`gh auth status`), which the existing CLI tests do too.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import io
import json
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
import unittest.mock
from pathlib import Path, PurePosixPath

from keel import cli, evidence, runtime, ship
from keel import config as cfg

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs" / "keel"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import release_surfaces  # noqa: E402  (path-inserted maintenance script)

#: Number words the pages use for a count, so "six bodies" can be checked against six.
_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}

#: A minimal consumer config: two command gates that always pass, no merge window (so
#: the dry assessment does not depend on the clock), and no GitHub owner.
_FIXTURE_CONFIG = (
    "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: tmp\n"
    "gates: [build, lint]\nknobs:\n  build_gate_cmd: 'true'\n  lint_cmd: 'true'\n"
)


def _page(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _between(text: str, start: str, end: str) -> str:
    """The text after ``start`` up to ``end``; fails loudly when either anchor is gone."""
    if start not in text:
        raise AssertionError(f"anchor {start!r} is gone")
    tail = text.split(start, 1)[1]
    if end not in tail:
        raise AssertionError(f"anchor {end!r} is gone after {start!r}")
    return tail.split(end, 1)[0]


def _bullet_names(text: str, opening: str) -> list[str]:
    """The backticked names heading each bullet of the list that follows ``opening``."""
    if opening not in text:
        raise AssertionError(f"anchor {opening!r} is gone")
    names: list[str] = []
    for line in text.split(opening, 1)[1].splitlines():
        found = re.match(r"- `([a-z_]+)`", line)
        if found:
            names.append(found.group(1))
        elif names and not line.startswith("  "):
            break
    return names


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _subparser(name: str) -> argparse.ArgumentParser:
    parser = cli.build_parser()
    for action in parser._subparsers._group_actions:  # noqa: SLF001 - no public API
        if name in (action.choices or {}):
            return action.choices[name]
    raise AssertionError(f"no subcommand {name!r}")


def _options(name: str) -> set[str]:
    return {opt for action in _subparser(name)._actions for opt in action.option_strings}


def _git(root: str, *argv: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *argv],
        cwd=root,
        check=True,
        capture_output=True,
    )


class _Fixture:
    """A throwaway repository on a branch with one committed change, and its config."""

    def __init__(self) -> None:
        self._dirs = [tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()]
        self.root = self._dirs[0].name
        self.config = str(Path(self._dirs[1].name) / "project.yaml")
        Path(self.config).write_text(_FIXTURE_CONFIG, encoding="utf-8")
        _git(self.root, "init", "-q", "-b", "main")
        (Path(self.root) / "README.md").write_text("one\n", encoding="utf-8")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "init")
        _git(self.root, "checkout", "-qb", "fix/docs")
        (Path(self.root) / "README.md").write_text("two\n", encoding="utf-8")
        _git(self.root, "commit", "-qam", "change")

    def cleanup(self) -> None:
        for directory in self._dirs:
            directory.cleanup()


class _WithARealShipRun(unittest.TestCase):
    """One real `keel ship` dry assessment, human and `--json`, shared by the class."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = _Fixture()
        rc, cls.human, _ = _run(["ship", cls.fixture.config, "--root", cls.fixture.root])
        if rc != 0:
            raise AssertionError(cls.human)
        rc, out, _ = _run(["ship", cls.fixture.config, "--root", cls.fixture.root, "--json"])
        if rc != 0:
            raise AssertionError(out)
        cls.document = json.loads(out)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()


# --- command-contracts.md -------------------------------------------------------------


class TheContractPageListsWhatShipEmits(_WithARealShipRun):
    """K24/K25: the rendered bodies, the bodiless renderers, the closure sections and the
    run-context fields are the ones a real `keel ship --json` emits."""

    def test_the_bodies_list_is_the_result(self):
        bodies = sorted(self.document["result"]["artifact_bodies"])
        opening = f"exposes {_WORDS[len(bodies)]} rendered bodies under `result.artifact_bodies`:"
        listed = _bullet_names(_page("command-contracts.md"), opening)
        self.assertEqual(bodies, sorted(listed))

    def test_the_bodiless_renderers_are_the_rest_of_the_contract(self):
        bodies = self.document["result"]["artifact_bodies"]
        renderers = self.document["contract"]["artifact_renderers"]["renderers"]
        rendered = {name.removesuffix("_template") for name in bodies}
        bodiless = sorted(set(renderers) - rendered)
        self.assertTrue(bodiless)
        opening = f"{_WORDS[len(bodiless)].capitalize()} more renderers are in the contract"
        self.assertEqual(bodiless, sorted(_bullet_names(_page("command-contracts.md"), opening)))

    def test_the_closure_sections_are_the_contract_in_order(self):
        closure = self.document["contract"]["closure_comment"]
        text = _page("command-contracts.md")
        sections = _flat(_between(text, "and the ordered `sections`:", "\n- "))
        self.assertEqual(closure["sections"], [name.strip() for name in sections.split(",")])
        fields = _between(text, "listed under `run_context_fields` (", ")")
        self.assertEqual(closure["run_context_fields"], re.findall(r"`([a-z_]+)`", fields))

    def test_the_evidence_row_names_the_provenance_marker(self):
        """K18: the row listed every arming signal except the one keel relies on."""
        row = next(
            line
            for line in _page("command-contracts.md").splitlines()
            if line.startswith("| `evidence` |")
        )
        self.assertIn(f"`{evidence.SHIP_PROVENANCE_MARKER}`", row)


# --- cli.md -----------------------------------------------------------------------------


class TheShipSampleIsARealRun(_WithARealShipRun):
    """K44: cli.md's `keel ship` sample printed nine of the lines a run prints."""

    #: Lines whose presence depends on the machine, not on the command.
    _ENVIRONMENTAL = {"github degraded", "degraded opt."}

    @classmethod
    def _labels(cls, block: str) -> list[str]:
        labels: list[str] = []
        for line in block.splitlines():
            text = line.strip()
            if not text:
                continue
            if text.startswith("keel ship —"):
                label = "header"
            elif text.startswith("gate "):
                label = "gate"
            elif " :" in text:
                label = text.split(" :", 1)[0].strip()
            else:
                label = text.split(":", 1)[0].strip()
            if label in cls._ENVIRONMENTAL or (labels and labels[-1] == label == "gate"):
                continue
            labels.append(label)
        return labels

    def test_the_sample_prints_the_lines_a_run_prints(self):
        text = _page("cli.md")
        block = _between(text, "The first line, run in this repository", "\n```\n\n")
        block = block.split("```\n", 1)[1]
        self.assertEqual(self._labels(self.human), self._labels(block))


class TheExitCodeTableIsTheSource(unittest.TestCase):
    """K42: the table stopped at "2 — no command given"; four commands return 2 and one 3."""

    @staticmethod
    def _codes_beyond_one() -> set[tuple[str, int]]:
        """``(command, code)`` for every literal exit code above 1 a subcommand returns."""
        tree = ast.parse((REPO_ROOT / "src/keel/cli.py").read_text(encoding="utf-8"))
        found: set[tuple[str, int]] = set()
        for function in tree.body:
            if not (isinstance(function, ast.FunctionDef) and function.name.startswith("_cmd_")):
                continue
            command = function.name.removeprefix("_cmd_").replace("_", "-")
            for node in ast.walk(function):
                values: list[object] = []
                if isinstance(node, ast.Return) and node.value is not None:
                    values = [n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant)]
                elif isinstance(node, ast.Call):
                    values = [
                        kw.value.value
                        for kw in node.keywords
                        if kw.arg == "code" and isinstance(kw.value, ast.Constant)
                    ]
                found.update((command, v) for v in values if type(v) is int and v >= 2)
        return found

    def _rows(self) -> dict[int, str]:
        section = _page("cli.md").split("\n## Exit codes\n", 1)[1].split("\n## ", 1)[0]
        return {int(code): text for code, text in re.findall(r"(?m)^\| (\d+) \| (.*) \|$", section)}

    def test_every_code_a_command_returns_is_in_its_row(self):
        found = self._codes_beyond_one()
        self.assertIn(("merge", 3), found)
        rows = self._rows()
        self.assertEqual({0, 1, 2} | {code for _, code in found}, set(rows))
        for command, code in sorted(found):
            with self.subTest(command=command, code=code):
                self.assertIn(f"`keel {command}`", rows[code])

    def test_the_two_codes_main_returns_itself(self):
        rows = self._rows()
        self.assertEqual(2, _run([])[0])
        self.assertIn("no command given", rows[2])
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            cli.main(["ship", "--no-such-flag"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("usage error", rows[2])


class TheDelegateErrorCodesAreTheSource(unittest.TestCase):
    """K43: the `error_code` table left out four codes the delegate path returns."""

    _MODULES = ("delegate", "delegaterun", "api_delegate", "cli")

    @classmethod
    def _vocabulary(cls) -> set[str]:
        codes: set[str] = set()
        for module in cls._MODULES:
            tree = ast.parse((REPO_ROOT / f"src/keel/{module}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    first = node.args[0] if node.args else None
                    if name == "DelegateError" and isinstance(first, ast.Constant):
                        codes.add(first.value)
                    codes.update(
                        kw.value.value
                        for kw in node.keywords
                        if kw.arg in ("error_code", "code")
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    )
                elif (
                    isinstance(node, ast.Assign)
                    and [getattr(t, "id", None) for t in node.targets] == ["code"]
                    and isinstance(node.value, ast.Constant)
                ):
                    codes.add(node.value.value)
        return codes

    def test_the_table_is_the_vocabulary(self):
        vocabulary = self._vocabulary()
        self.assertLessEqual(
            {"bad-key", "unknown-vendor", "spawn-failed", "bad-run-id"}, vocabulary
        )
        table = _between(_page("cli.md"), "| `error_code` | meaning |", "\n\n")
        documented: set[str] = set()
        for line in table.splitlines():
            if line.startswith("| `"):
                documented.update(re.findall(r"`([a-z-]+)`", line.split("|")[1]))
        self.assertEqual(sorted(vocabulary), sorted(documented))


class TheCliSectionsSayWhatTheCommandsDo(unittest.TestCase):
    """K36, K37, K45, K46: four cli.md sections, each measured against the command."""

    def setUp(self):
        self.fixture = _Fixture()
        self.addCleanup(self.fixture.cleanup)
        self.text = _page("cli.md")

    def test_the_blocker_rule_refuses_an_offline_title(self):
        config = cfg.load_config(self.fixture.config)
        args = argparse.Namespace(
            blocker_rule="hotfix-label",
            issue=None,
            issue_title="urgent",
            issue_labels="hotfix",
            operator_override=False,
            root=self.fixture.root,
        )
        justification, error = cli._hotfix_justification(args, config, None)
        self.assertIsNone(justification)
        self.assertIn("--issue <N>", error)
        bullet = _flat(_between(self.text, "- `--blocker-rule <id>`", "\n- `--operator-override`"))
        self.assertIn("`--issue <N>`", bullet)
        self.assertIn("`--issue-title` / `--issue-labels` are refused", bullet)

    def test_every_activity_example_that_stamps_a_phase_records_it(self):
        section = _between(self.text, "## `keel activity ", "\n## ")
        examples = [
            line.split("#", 1)[0]
            for line in _between(section, "```bash\n", "```").splitlines()
            if line.startswith("keel activity") and "--phase" in line
        ]
        self.assertGreaterEqual(len(examples), 2)
        for example in examples:
            argv = shlex.split(example)[1:]
            argv[1] = self.fixture.config
            argv[argv.index("--root") + 1] = self.fixture.root
            run_id = argv[argv.index("--run-id") + 1]
            phase = argv[argv.index("--phase") + 1]
            with self.subTest(example=example):
                rc, _, err = _run(argv)
                self.assertEqual(0, rc, err)
                record = Path(self.fixture.root) / ".keel" / "activity" / f"{run_id}.json"
                self.assertTrue(record.is_file(), f"{example!r} recorded nothing")
                self.assertEqual(phase, json.loads(record.read_text(encoding="utf-8"))["phase"])

    def test_the_activity_status_choices_are_the_parser_s(self):
        (status,) = [a for a in _subparser("activity")._actions if "--status" in a.option_strings]
        heading = _between(self.text, "## `keel activity ", "\n")
        documented = re.search(r"\[--status ([a-z|]+)\]", heading).group(1).split("|")
        self.assertEqual(sorted(status.choices), sorted(documented))

    def test_the_review_dry_run_is_not_called_offline(self):
        reviews = Path(self.fixture.root) / "reviews.json"
        reviews.write_text('[{"reviewer": "a", "verdict": "LGTM", "findings": []}]', "utf-8")
        real_detect = runtime.detect
        argv = ["review", self.fixture.config, "--root", self.fixture.root, "--pr", "1"]
        with unittest.mock.patch.object(
            cli.runtime, "detect", lambda root: real_detect(root, which=lambda _name: None)
        ):
            rc, _, err = _run([*argv, "--reviews", str(reviews)])
        self.assertEqual(1, rc)
        self.assertIn("gh-auth", err)
        section = _flat(_between(self.text, "## `keel review ", "\n## `keel worktree-remove"))
        self.assertNotIn("fully offline", section)
        self.assertNotIn("WOULD post, no network", section)
        self.assertIn("`gh auth status`", section)

    def test_the_wizard_counts_its_own_rules(self):
        rules = _between(self.text, "rules the step will not let you break", "\n\nOn a machine")
        count = len(re.findall(r"(?m)^- ", rules))
        claim = f"{_WORDS[count].capitalize()} rules the step will not let you break"
        self.assertTrue(claim in self.text, f"cli.md does not say {claim!r}")

    def test_the_review_all_day_example_is_that_command_s_output(self):
        rc, out, _ = _run(["review-all-day", self.fixture.config])
        self.assertEqual(0, rc)
        banner = out.splitlines()[0].split("—", 1)[0]
        section = _between(self.text, "## `keel review-all-day ", "\n## ")
        example = section.split("Example output", 1)[1].split("```\n", 2)[1]
        self.assertTrue(example.startswith(banner), example.splitlines()[0])


# --- evidence.md, github-actions.md -----------------------------------------------------


class TheEvidencePagesMatchTheVerifier(unittest.TestCase):
    """K18–K23: what `evidence-verify` and `keel merge` check, and what the workflow runs."""

    def setUp(self):
        self.evidence = _page("evidence.md")
        self.actions = _page("github-actions.md")

    def test_the_workflow_step_names_the_flags_the_workflow_passes(self):
        workflow = (REPO_ROOT / ".github/workflows/keel-ship.yml").read_text(encoding="utf-8")
        args = re.search(r'ARGS=\(\.keel/project\.yaml --root \. --pr "\$PR" ([^)]*)\)', workflow)
        self.assertIsNotNone(args, "keel-ship.yml no longer builds evidence-verify's ARGS")
        step = _flat(_between(self.actions, "6. runs **`keel evidence-verify", "```yaml"))
        for flag in args.group(1).split():
            with self.subTest(flag=flag):
                self.assertIn(flag, step)

    def test_the_arming_section_names_the_provenance_marker(self):
        section = _between(self.actions, "## Gate arming and the operator waiver", "\n## ")
        self.assertIn(f"`{evidence.SHIP_PROVENANCE_MARKER}`", section)

    def test_the_waiver_is_not_said_to_take_an_operator(self):
        self.assertNotIn("--operator", _options("evidence-verify"))
        section = _flat(_between(self.evidence, "### 6. Transparent Deferrals", "### 7."))
        self.assertNotRegex(section, r"requires an explicit `--operator`")
        self.assertIn("takes no `--operator` and writes nothing", section)

    def test_the_verifier_is_not_said_to_read_gate_results(self):
        contract = {"reviewers": {"count": 3}, "jury": ship.resolve_jury(tier=3)}
        kinds = {item.kind for item in evidence.required_items(contract)}
        self.assertNotIn("gate", kinds)
        confirms = _between(self.evidence, "The verifier confirms:", "### Three-State")
        self.assertIsNone(re.search(r"(?m)^\d\. .*\bgates?\b", confirms))
        phase = _flat(_between(self.evidence, "### 5. Phase-Separated", "### 6."))
        self.assertIn("checks the verdicts and nothing else", phase)

    def test_the_merge_gate_names_the_hotfix_bypass(self):
        self.assertTrue(ship.HOTFIX_BYPASSES)
        bullet = _between(self.evidence, "* **Fail-Closed Evidence Gate**", "\n* ")
        self.assertNotIn("unconditionally", bullet)
        self.assertIn("`--hotfix`", bullet)

    def test_the_bot_rule_does_not_fix_the_reviewer_count(self):
        pre_merge = evidence.PHASE_PRE_MERGE
        counts = {
            len(evidence.required_items({"reviewers": {"count": n}}, phase=pre_merge))
            for n in (1, 2, 3)
        }
        self.assertEqual({1, 2, 3}, counts)
        section = _flat(_between(self.evidence, "### 3. No Bot Exemption", "Three reasons"))
        self.assertNotIn("three reviewer verdicts", section)
        self.assertIn("its risk tier requires", section)


# --- overview.md, runtime-capabilities.md, ship-baseline.md, comparison.md --------------


class TheOverviewAndReferencePagesMatchTheCode(unittest.TestCase):
    """K26, K28, K32–K35."""

    def test_the_jury_relaxation_names_every_route_resolve_jury_takes(self):
        routes = {
            "`--jury-advisory`": {"jury_advisory": True},
            "`knobs.team.jury.mode`": {"policy_mode": "advisory"},
            "`--jury-vendors`": {"participating_vendors": 1},
        }
        overview = _flat(_page("overview.md"))
        sentence = _between(overview, "it relaxes to advisory", ". Core resolves")
        for token, kwargs in routes.items():
            with self.subTest(route=token):
                self.assertEqual("advisory", ship.resolve_jury(tier=3, **kwargs)["mode"])
                self.assertEqual(
                    "gating", ship.resolve_jury(tier=3, panel_is_jury=True, **kwargs)["mode"]
                )
                self.assertIn(token, sentence)
        self.assertIn("None of the three relaxes a tier whose review is the panel", sentence)

    def test_the_keel_visual_pin_is_its_pyproject_s(self):
        pyproject = tomllib.loads((REPO_ROOT / "keel-visual/pyproject.toml").read_text("utf-8"))
        (pin,) = [d for d in pyproject["project"]["dependencies"] if d.startswith("keel-workflow")]
        floor = pin.split(">=", 1)[1]
        claim = f"(`keel-workflow >= {floor}`)"
        self.assertTrue(claim in _page("overview.md"), f"overview.md does not say {claim!r}")

    def test_the_capability_report_names_the_subprocess_it_runs(self):
        calls: list[list[str]] = []

        def run(argv, **_kwargs):
            calls.append(list(argv))
            return argparse.Namespace(ok=False)

        runtime.detect(".", env={}, which=lambda name: f"/bin/{name}", run=run)
        self.assertTrue(calls)
        text = _flat(_page("runtime-capabilities.md"))
        for argv in calls:
            with self.subTest(argv=argv):
                command = f"`{' '.join(argv)}`"
                self.assertTrue(command in text, f"runtime-capabilities.md never names {command}")

    def test_the_learning_verifier_row_names_the_shipped_command(self):
        self.assertIn("capture-verify", _subparser("capture-verify").prog)
        (row,) = [
            line
            for line in _page("ship-baseline.md").splitlines()
            if line.startswith("| Session-level learning verifier")
        ]
        self.assertIn("`keel capture-verify`", row)
        self.assertNotIn("deferred from core", row)

    def test_the_comparison_marks_what_shipped(self):
        shipped = {
            "Freeze vs. Pause": "merge_window_mode"
            in {f.name for f in dataclasses.fields(cfg.ProjectConfig)},
            "golden-path scaffolder": "--auto" in _options("init"),
            "Hotfix bypass": "--hotfix" in _options("merge"),
        }
        rows = _page("comparison.md").splitlines()
        for idea, is_shipped in shipped.items():
            with self.subTest(idea=idea):
                self.assertTrue(is_shipped)
                (row,) = [line for line in rows if line.startswith("| ") and idea in line]
                self.assertIn("**Shipped**", row)

    def test_the_comparison_does_not_call_keel_dependency_free(self):
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("pyyaml", " ".join(pyproject["project"]["dependencies"]).lower())
        text = _page("comparison.md")
        claims = ("stdlib-only", "zero-runtime-dependency", "no-runtime-dep", "dependency-free")
        for claim in claims:
            with self.subTest(claim=claim):
                self.assertFalse(claim in text, f"comparison.md still says {claim!r}")


# --- release.md, homebrew-release-chain.md ----------------------------------------------


class TheReleasePagesNameTheReleaseSurfaces(unittest.TestCase):
    """K27, K30, K31."""

    def test_the_bump_list_names_every_registered_surface(self):
        block = _between(_page("release.md"), "It updates:", "Historical version mentions")
        for path in sorted({surface.path for surface in release_surfaces.RELEASE_SURFACES}):
            # Surface paths are POSIX strings; `Path` would spell a nested parent with
            # backslashes on Windows and miss the docs' `plugin/.claude-plugin/`.
            candidates = (path, PurePosixPath(path).name, f"{PurePosixPath(path).parent}/")
            with self.subTest(surface=path):
                self.assertTrue(any(f"`{c}" in block for c in candidates), path)

    def test_the_chain_names_every_plugin_manifest(self):
        manifests = [
            surface.path
            for surface in release_surfaces.RELEASE_SURFACES
            if surface.path.endswith("plugin.json")
        ]
        text = _flat(_page("homebrew-release-chain.md"))
        claim = f"the {_WORDS[len(manifests)]} plugin manifests"
        self.assertTrue(claim in text, f"homebrew-release-chain.md does not say {claim!r}")
        for manifest in manifests:
            with self.subTest(manifest=manifest):
                self.assertIn(f"`{PurePosixPath(manifest).parent}/`", text)

    def test_no_release_page_cuts_a_lightweight_tag(self):
        release = _page("release.md")
        self.assertIn("Do not use a lightweight tag for production releases", release)
        for name in ("release.md", "homebrew-release-chain.md"):
            with self.subTest(page=name):
                self.assertIsNone(re.search(r"git tag (?!-[sa] )v", _page(name)))


if __name__ == "__main__":
    unittest.main()
