"""The four reference documents say what the code does (docs audit 2026-09-29).

`docs/keel/configuration.md`, `parameter-reference.md`, `extensions.md` and `models.md` are
the pages an operator configures keel from. The audit found them wrong in sixteen places,
and every one was a claim about something the code can answer:

* `parameter-reference.md` left out flags on ten commands (`plan --tier`, `merge
  --no-checkpoint-gate`, `ship --gate-result`, …), documented a `--tier` on `ship`, which
  has none, listed `activity --status` without `merged`, said `run-gates` has no `--json`,
  and gave "accepted by" lists for shared flags that missed `review`, `doctor`, `sync`,
  `guard`, `merge` and the swarm commands.
* `configuration.md` called the security presets "strictly fail-soft" while a missing
  `gitleaks` blocks the run, placed `gitleaks` at s8 as well as s3, said a delegate profile's
  `vendor` is "only `cli` today", described the gitignored run ledger as committed, let a
  committed `knobs.team` name a `~/.keel/providers.yaml` entry that `keel validate`
  refuses, and showed a `policy_pack` example without its required `name`.
* `extensions.md` promised that a broken `on_fail: block` extension surfaces as a blocking
  finding; one that fails to load is skipped and `run-gates` exits 0.
* `models.md` showed an Aider profile whose `--message-file` swallowed `--model`, a Cursor
  profile with no `-p` that reviewed with `--force`, and `KEEL_ALLOW_REMOTE_ENDPOINT=1` as
  the only form of the remote opt-in.

So each check here reads the claim out of the document and compares it with the parser,
the schema, or the function that decides — never with a list retyped into the test.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import subprocess
import tempfile
import unittest
import unittest.mock
import urllib.parse
from pathlib import Path

import yaml

from keel import (
    classify,
    cli,
    delegate,
    extensions,
    findings,
    gates,
    ledger,
    model,
    runner,
    workspace,
)
from keel import config as cfg

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs" / "keel"
PARAM_DOC = DOCS / "parameter-reference.md"
CONFIG_DOC = DOCS / "configuration.md"
EXTENSIONS_DOC = DOCS / "extensions.md"
MODELS_DOC = DOCS / "models.md"

_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
_TICKED = re.compile(r"`([^`]+)`")

#: The smallest config the schema accepts; a documented example is merged over it.
_BASE_CONFIG = {
    "extends": "keel",
    "core_version": "^1.0",
    "base_branch": "main",
    "knobs": {},
    "gates": ["build"],
    "extensions": {},
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = cli.build_parser()
    out: dict[str, argparse.ArgumentParser] = {}
    for action in parser._subparsers._group_actions:  # noqa: SLF001 - argparse has no public API
        out.update(getattr(action, "choices", {}) or {})
    return out


def _actions(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    """Every ``--long`` option of ``parser`` and of its nested subcommands (``fixloop brief``)."""
    out: dict[str, argparse.Action] = {}
    stack = [parser]
    while stack:
        current = stack.pop()
        for action in current._actions:  # noqa: SLF001
            for option in action.option_strings:
                if option.startswith("--") and option != "--help":
                    out.setdefault(option, action)
            nested = getattr(action, "choices", None)
            if isinstance(nested, dict):
                stack.extend(nested.values())
    return out


def _flags_by_command() -> dict[str, set[str]]:
    return {name: set(_actions(sub)) for name, sub in _subparsers().items()}


def _sections(text: str, level: str) -> list[str]:
    """The document split at every heading of exactly ``level`` (``##`` or ``###``)."""
    return re.split(rf"(?m)^{level} ", text)[1:]


def _command_sections() -> dict[str, str]:
    """``{subcommand: section text}`` for every ``## `keel <cmd>``` section."""
    out: dict[str, str] = {}
    for section in _sections(_read(PARAM_DOC), "##"):
        match = re.match(r"`keel ([a-z][a-z0-9-]*)`", section)
        if match:
            out[match.group(1)] = section
    return out


def _table_rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        if line.startswith("| `-"):
            rows.append([cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]])
    return rows


def _yaml_blocks(text: str) -> list[tuple[int, str]]:
    return [
        (text.count("\n", 0, m.start()) + 1, m.group(1))
        for m in re.finditer(r"```ya?ml\n(.*?)```", text, re.DOTALL)
    ]


class TestEveryCommandSectionListsItsFlags(unittest.TestCase):
    """K8/K6: a command's section documents every flag it has, and only flags it has."""

    def test_the_sections_and_the_parser_are_both_found(self):
        # Guards the guard: an empty sweep would pass every check below.
        self.assertGreater(len(_command_sections()), 30)
        self.assertGreater(len(_subparsers()), 30)

    def test_every_section_names_a_real_command(self):
        unknown = sorted(set(_command_sections()) - set(_subparsers()))
        self.assertEqual(unknown, [], "parameter-reference.md documents commands that do not exist")

    def test_every_flag_of_a_documented_command_is_in_its_section(self):
        flags = _flags_by_command()
        missing = {}
        for name, section in _command_sections().items():
            absent = sorted(flags[name] - set(_FLAG.findall(section)))
            if absent:
                missing[name] = absent
        self.assertEqual(
            missing,
            {},
            "flags `keel <cmd> --help` lists that the command's section in "
            "parameter-reference.md never mentions — a reader treats an absent flag as "
            "a flag that does not exist",
        )

    def test_every_flag_a_table_row_names_exists_on_its_command(self):
        flags = _flags_by_command()
        phantom = []
        for name, section in _command_sections().items():
            for row in _table_rows(section):
                for flag in _FLAG.findall(row[0]):
                    if flag not in flags[name]:
                        phantom.append(f"{name} {flag}")
        self.assertEqual(phantom, [], "a flag row documents a flag the command does not take")

    def test_every_documented_value_list_is_the_parsers(self):
        wrong = []
        checked = 0
        for name, section in _command_sections().items():
            actions = _actions(_subparsers()[name])
            for row in _table_rows(section):
                named = _FLAG.findall(row[0])
                if len(named) != 1 or "\\|" not in row[1]:
                    continue
                action = actions.get(named[0])
                if action is None or not action.choices or isinstance(action.choices, dict):
                    continue
                checked += 1
                documented = set(_TICKED.findall(row[1]))
                real = {str(choice) for choice in action.choices}
                if documented != real:
                    wrong.append(f"{name} {named[0]}: doc {sorted(documented)} != {sorted(real)}")
        self.assertGreater(checked, 20)
        self.assertEqual(wrong, [], "a documented value list disagrees with argparse choices")


class TestSharedFlagListsNameEveryCommand(unittest.TestCase):
    """K5/K9: the shared-flag sections say which commands take each flag, completely."""

    @staticmethod
    def _shared_section() -> str:
        for section in _sections(_read(PARAM_DOC), "##"):
            if section.startswith("Shared flags"):
                return section
        raise AssertionError("parameter-reference.md has no Shared flags section")

    @staticmethod
    def _claims() -> list[tuple[tuple[str, ...], set[str], set[str]]]:
        """``(flags, commands named, flags it defers to)`` per "accepted by" statement."""
        commands = set(_subparsers())
        out = []
        for sub in _sections(TestSharedFlagListsNameEveryCommand._shared_section(), "###"):
            heading, _, body = sub.partition("\n")
            statements = []
            for match in re.finditer(r"(?ms)^- \*\*Accepted by:\*\*(.*?)(?=^- \*\*|^\S|\Z)", body):
                statements.append((tuple(_FLAG.findall(heading)), match.group(1)))
            for match in re.finditer(
                r"(?ms)^- \*\*((?:`--[a-z-]+`(?: / )?)+) accepted by:\*\*(.*?)(?=^- \*\*|^\S|\Z)",
                body,
            ):
                statements.append((tuple(_FLAG.findall(match.group(1))), match.group(2)))
            for flags, text in statements:
                ticked = set(_TICKED.findall(text))
                out.append((flags, ticked & commands, {t for t in ticked if t.startswith("--")}))
        return out

    def test_the_statements_are_found(self):
        flags = {flag for claim in self._claims() for flag in claim[0]}
        for flag in ("--consent-mode", "--target", "--issue-title", "--jury", "--reviewers"):
            self.assertIn(flag, flags)

    def test_every_command_that_takes_a_shared_flag_is_named(self):
        real = _flags_by_command()
        claims = self._claims()
        by_flag = {flag: named for flags, named, _ in claims for flag in flags if named}
        missing = {}
        for flags, named, defers in claims:
            if not named:  # "the same set as `--consent-mode`"
                named = set().union(*(by_flag.get(flag, set()) for flag in defers))
            for flag in flags:
                if flag in ("--root", "--json"):
                    continue  # stated in prose; `--json` is pinned exactly below
                takes = {name for name, have in real.items() if flag in have}
                if takes - named:
                    missing[flag] = sorted(takes - named)
        self.assertEqual(
            missing, {}, "an 'Accepted by' list leaves out commands that take the flag"
        )

    def test_a_command_with_project_does_not_claim_to_read_no_config(self):
        section = re.sub(r"\s+", " ", _command_sections()["step-verify"])
        self.assertIn("--project", _flags_by_command()["step-verify"])
        self.assertNotIn(" No project config is read", section)
        self.assertIn("Without `--project` no project config is read", section)

    def test_every_named_command_takes_one_of_the_flags(self):
        real = _flags_by_command()
        phantom = {}
        for flags, named, _ in self._claims():
            extra = {name for name in named if not set(flags) & real[name]}
            if extra and flags not in (("--root",), ("--json",)):
                phantom[flags] = sorted(extra)
        self.assertEqual(phantom, {}, "an 'Accepted by' list names a command without the flag")

    def test_the_json_absence_list_is_exactly_the_commands_without_it(self):
        section = self._shared_section()
        match = re.search(r"Not present on(.*?)\n- \*\*", section, re.DOTALL)
        self.assertIsNotNone(match, "the --json section no longer lists where it is absent")
        documented = set(_TICKED.findall(match.group(1)))
        real = {name for name, have in _flags_by_command().items() if "--json" not in have}
        self.assertEqual(documented, real)


class TestPresetTableIsThePresetDefinitions(unittest.TestCase):
    """K2: each preset's step, `on_fail`, and what a missing tool does, from gates.py."""

    @staticmethod
    def _rows() -> dict[str, list[str]]:
        section = CONFIG_DOC.read_text(encoding="utf-8").split("### `policy_pack.presets`")[1]
        section = section.split("\n### ")[0]
        rows = {}
        for line in section.splitlines():
            match = re.match(r"\| `([a-z]+)` \|", line)
            if match:
                rows[match.group(1)] = [c.strip() for c in line.split("|")[1:-1]]
        return rows

    def test_every_preset_has_a_row(self):
        self.assertEqual(set(self._rows()), set(gates.POLICY_PACK_PRESETS))

    def test_each_row_states_the_real_step_on_fail_and_missing_tool_outcome(self):
        config = cfg.parse_config(
            {
                **_BASE_CONFIG,
                "gates": [],
                "policy_pack": {"name": "t", "presets": sorted(gates.POLICY_PACK_PRESETS)},
            },
            source="t",
        )
        specs = {spec.id: spec for spec in gates.plan_gates(config, {})}
        missing_tool = subprocess.CompletedProcess("x", 127, "", "sh: tool: command not found")
        gate_runner = runner.command_gate_runner(_run=lambda *a, **k: missing_tool)
        for preset, row in self._rows().items():
            gate_id = gates.POLICY_PACK_PRESETS[preset][0]
            spec = specs[gate_id]
            step = model.step_for_slot(spec.phase)
            ok, found, _timed_out, _not_run = gate_runner(spec)
            self.assertFalse(ok)
            severity = found[0].severity
            blocks = findings.decision_for(severity) == "block"
            with self.subTest(preset=preset):
                self.assertEqual(row[2], f"`{step.id} {step.name}`")
                self.assertEqual(row[3], f"`{spec.on_fail}`")
                self.assertIn(f"`{severity}`", row[4])
                self.assertEqual("**blocks**" in row[4], blocks, row[4])

    def test_the_prose_does_not_call_the_presets_fail_soft(self):
        section = CONFIG_DOC.read_text(encoding="utf-8").split("### `policy_pack.presets`")[1]
        section = re.sub(r"\s+", " ", section.split("\n### ")[0])
        self.assertNotRegex(section, r"strictly fail-soft|degrades cleanly|degraded / skipped")
        self.assertIn("**not** a fail-soft one", section)


class TestDelegateProfileVendorsAreTheAcceptedOnes(unittest.TestCase):
    """K3: the `vendor` field row names every accepted vendor."""

    def test_the_vendor_row_lists_the_accepted_vendors(self):
        row = next(
            line for line in _read(CONFIG_DOC).splitlines() if line.startswith("| `vendor` |")
        )
        description = row.split("|")[4]
        documented = {t for t in _TICKED.findall(description) if " " not in t}
        self.assertEqual(documented, set(cfg.DELEGATE_PROFILE_VENDORS))

    def test_the_parameter_reference_names_every_profile_vendor(self):
        text = _read(PARAM_DOC)
        sentence = text[text.index("A **profile name**") :].split("See [")[0]
        for vendor in cfg.DELEGATE_PROFILE_VENDORS:
            self.assertIn(f"`{vendor}`", sentence)


class TestTheRunLedgerIsNotDescribedAsCommitted(unittest.TestCase):
    """K4: the ledger lives under `.keel/state/`, which keel's scaffold gitignores."""

    def test_the_ledger_is_gitignored_state(self):
        self.assertTrue(ledger.DEFAULT_LEDGER_PATH.startswith(".keel/state/"))
        self.assertIn("state/", workspace.RUNTIME_IGNORE_ENTRIES)

    def test_configuration_md_does_not_say_it_is_committed(self):
        text = re.sub(r"\s+", " ", _read(CONFIG_DOC))
        self.assertIsNone(
            re.search(r"ledger (?:\*is\*|is) \*?committed", text),
            "configuration.md says the run ledger is committed; .keel/state/ is gitignored",
        )


class TestGenericCliProfilesBuildAWorkingCommandLine(unittest.TestCase):
    """K7: the models.md profiles, run through keel's own argv builder.

    What each CLI does with a flag is the CLI's business, so the flags that take a value
    are stated here — the one list in this module that is not derived — and checked
    against the argv keel really builds.
    """

    #: Flags that consume the next argv element, per CLI.
    _VALUE_FLAGS = {
        "aider": {"--message", "--message-file", "--model", "--read", "--file"},
        "cursor-agent": {"--model", "--output-format", "--api-key"},
    }
    #: How each CLI receives a one-shot prompt: after one of these flags, or positionally.
    _PROMPT_FLAGS = {"aider": {"--message"}, "cursor-agent": None}
    #: A flag the CLI needs to answer and exit instead of opening an interactive session.
    _HEADLESS = {"aider": {"--message"}, "cursor-agent": {"-p", "--print"}}

    @staticmethod
    def _config() -> cfg.ProjectConfig:
        section = _read(MODELS_DOC).split("## 5. Generic CLI Profiles")[1].split("\n## ")[0]
        block = re.search(r"```yaml\n(.*?)```", section, re.DOTALL).group(1)
        return cfg.parse_config(
            {**_BASE_CONFIG, "knobs": yaml.safe_load(block)["knobs"]}, source="models.md"
        )

    def test_the_profiles_are_found(self):
        commands = {p.command for p in self._config().knobs.delegate_profiles.values()}
        self.assertEqual(commands, set(self._VALUE_FLAGS))

    def test_every_role_gets_a_command_line_the_cli_can_parse(self):
        config = self._config()
        for name in config.knobs.delegate_profiles:
            for role in ("implement", "review"):
                resolved = delegate.resolve_provider(config, None, name)
                plan = delegate.plan_run(
                    resolved.provider, role, "p.md", model=resolved.model, profile=resolved.profile
                )
                self.assertIsNone(plan.stdin_mode, "these profiles pass the prompt as an arg")
                argv = [*plan.argv, "<prompt>"]
                command = argv[0]
                with self.subTest(profile=name, role=role, argv=argv):
                    for index, token in enumerate(argv[1:-1], start=1):
                        if token in self._VALUE_FLAGS[command]:
                            self.assertFalse(
                                argv[index + 1].startswith("-"),
                                f"{token} would swallow {argv[index + 1]}",
                            )
                    prompt_flags = self._PROMPT_FLAGS[command]
                    if prompt_flags is not None:
                        self.assertIn(argv[-2], prompt_flags, "the prompt is not a message")
                    self.assertTrue(set(argv) & self._HEADLESS[command], "not headless")
                    if role == "review":
                        self.assertTrue(plan.read_only_backed, plan.warnings)
                        self.assertEqual(plan.warnings, ())


class TestTheTier3ExceptionIsDocumented(unittest.TestCase):
    """K10: a tier3_globs match is not always TIER-3 (#801)."""

    def test_a_non_privileged_workflow_patch_does_not_raise_the_tier(self):
        path = ".github/workflows/ci.yml"
        patch = (
            f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n+      - run: make test\n"
        )
        glob = (".github/workflows/*",)
        self.assertEqual(classify.tier_for_files([path], tier3_globs=glob), 3)
        self.assertEqual(
            classify.tier_for_files(
                [path], tier3_globs=glob, patches=classify.split_unified_diff(patch)
            ),
            2,
        )

    def test_both_references_state_the_exception(self):
        config = _read(CONFIG_DOC).split("#### `tier3_globs`")[1].split("\n#### ")[0]
        param = _command_sections()["ship"].split("**Decision pipeline.**")[1].split("\n\n")[0]
        for name, text in (("configuration.md", config), ("parameter-reference.md", param)):
            with self.subTest(doc=name):
                self.assertIn(".github/workflows/", text)
                self.assertIn("privileged", text)


class TestTheImplementerChainIsTheSameInBothReferences(unittest.TestCase):
    """K11: parameter-reference.md's s4 precedence is configuration.md's chain."""

    _STEPS = (
        "--delegate",
        "team.profiles",
        "team.by_difficulty",
        "team.implement.by_role",
        "team.implement.default",
        "implementer_agents",
    )

    def _order(self, text: str) -> list[str]:
        positions = {step: text.find(step) for step in self._STEPS}
        self.assertNotIn(-1, positions.values(), positions)
        return sorted(self._STEPS, key=positions.__getitem__)

    def test_the_orders_agree(self):
        config = _read(CONFIG_DOC)
        chain = config[
            config.index("--delegate / --review-delegate / --effort   (per-run flags)") :
        ]
        chain = chain.split("```")[0]
        param = _read(PARAM_DOC)
        sentence = param[param.index("Implementer precedence at s4") :].split("HOST_AGENT")[0]
        self.assertEqual(self._order(sentence), self._order(chain))


class TestACommittedTeamNeverNamesARegistryEntry(unittest.TestCase):
    """K12: `keel validate` refuses a `~/.keel/providers.yaml` name in `knobs.team`."""

    def test_validation_refuses_a_registry_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "providers.yaml"
            registry.write_text(
                "providers:\n  myreg:\n    transport: cli\n    command: echo\n", encoding="utf-8"
            )
            team = {"implement": {"default": {"provider": "myreg"}}}
            with unittest.mock.patch.dict(os.environ, {"KEEL_PROVIDERS": str(registry)}):
                with self.assertRaises(cfg.ConfigError) as caught:
                    cfg.parse_config({**_BASE_CONFIG, "knobs": {"team": team}}, source="t")
        self.assertIn("unknown provider 'myreg'", str(caught.exception))

    def test_neither_document_offers_one(self):
        config = _read(CONFIG_DOC).split("**Providers.**")[1].split("\n\n")[0]
        models = next(
            (p for p in _read(MODELS_DOC).split("\n\n") if "is the explicit spelling" in p), None
        )
        self.assertIsNotNone(models, "models.md no longer explains a team `provider`")
        for name, text in (("configuration.md", config), ("models.md", models)):
            flat = re.sub(r"\s+", " ", text)
            with self.subTest(doc=name):
                self.assertRegex(flat, r"\bnever names a machine-level `~/\.keel/providers\.yaml`")
                self.assertNotIn("same registry", flat)
                for sentence in re.split(r"(?<=[.:])\s", flat):
                    if "providers.yaml" in sentence:
                        self.assertRegex(sentence, r"\bnever\b", sentence)


class TestRemoteEndpointExamplesAllowOnlyTheirHost(unittest.TestCase):
    """K13: each remote example names its own host in `KEEL_ALLOW_REMOTE_ENDPOINT`."""

    def test_every_remote_example_is_permitted_by_a_host_allowlist(self):
        text = _read(MODELS_DOC)
        checked = 0
        for line, block in _yaml_blocks(text):
            data = yaml.safe_load(block) or {}
            profiles = (data.get("knobs") or {}).get("delegate_profiles") or {}
            for name, profile in profiles.items():
                endpoint = profile.get("endpoint")
                host = urllib.parse.urlsplit(endpoint or "").hostname
                if not host or host in cfg.LOOPBACK_HOSTS:
                    continue
                after = text[text.index(block) + len(block) :]
                after = after.split("```yaml")[0]
                match = re.search(r"export KEEL_ALLOW_REMOTE_ENDPOINT=(\S+)", after)
                with self.subTest(profile=name, line=line):
                    self.assertIsNotNone(match, "no opt-in shown for a remote endpoint")
                    env = {"KEEL_ALLOW_REMOTE_ENDPOINT": match.group(1)}
                    self.assertEqual(
                        cfg._remote_endpoint_status(env, host),  # noqa: SLF001
                        "permitted-listed",
                        "the example allows every remote host instead of naming this one",
                    )
                    self.assertEqual(cfg.endpoint_issues(endpoint, where=name, env=env), [])
                checked += 1
        self.assertGreaterEqual(checked, 4)

    def test_no_example_exports_the_allow_everything_form(self):
        exported = re.findall(
            r"export KEEL_ALLOW_REMOTE_ENDPOINT=(\S+?)`?(?:\s|$)", _read(MODELS_DOC)
        )
        self.assertGreaterEqual(len(exported), 5)
        broad = [value for value in exported if value.lower() in ("1", "true", "yes", "on")]
        self.assertEqual(broad, [], "an example opts in to every remote host, not the one it uses")


class TestAnUnknownGateNameFailsAtPlanTime(unittest.TestCase):
    """K14: `gates: [build, foo]` validates; plan/run-gates/ship refuse it."""

    def test_the_schema_accepts_it_and_planning_refuses_it(self):
        config = cfg.parse_config({**_BASE_CONFIG, "gates": ["build", "foo"]}, source="t")
        with self.assertRaisesRegex(gates.GateError, "unknown built-in gate 'foo'"):
            gates.plan_gates(config, {})

    def test_configuration_md_says_where_it_is_caught(self):
        text = re.sub(r"\s+", " ", _read(CONFIG_DOC).split("## `gates` vs `extensions`")[1])
        bullet = text.split("- **`extensions`**")[0]
        self.assertIn("not a `keel validate` one", bullet)
        self.assertIn("`keel plan`", bullet)


class TestRunGatesFindingsFollowOnFail(unittest.TestCase):
    """K15: a failing command gate reports at its `on_fail` severity, sourced `<name>`."""

    def test_the_documented_mapping_is_the_runners(self):
        mapping = ", ".join(f"`{k}`→`{v}`" for k, v in runner._ON_FAIL_SEVERITY.items())  # noqa: SLF001
        details = re.sub(r"\s+", " ", _command_sections()["run-gates"].split("### Details")[1])
        self.assertIn(mapping, details)
        self.assertNotIn("gate:<name>", details)

    def test_the_finding_source_is_the_gate_name(self):
        failed = subprocess.CompletedProcess("x", 1, "", "boom")
        gate_runner = runner.command_gate_runner(_run=lambda *a, **k: failed)
        spec = gates.GateSpec(id="lint", kind="command", phase="test", on_fail="suggest", run="x")
        _ok, found, _t, _n = gate_runner(spec)
        self.assertEqual((found[0].source, found[0].severity), ("lint", "minor"))


class TestEveryConfigExampleValidates(unittest.TestCase):
    """K16: a YAML example of project config passes the same validation `keel validate` runs."""

    _CONFIG_KEYS = {"knobs", "policy_pack", "automation", "gates", "extensions"}

    def test_every_example_parses(self):
        checked = 0
        invalid = []
        env = {"KEEL_ALLOW_REMOTE_ENDPOINT": "1"}  # the remote examples say to set it
        with unittest.mock.patch.dict(os.environ, env):
            for doc in (CONFIG_DOC, MODELS_DOC, EXTENSIONS_DOC):
                for line, block in _yaml_blocks(_read(doc)):
                    data = yaml.safe_load(block)
                    if not isinstance(data, dict) or not set(data) & self._CONFIG_KEYS:
                        continue
                    checked += 1
                    try:
                        cfg.parse_config({**_BASE_CONFIG, **data}, source=f"{doc.name}:{line}")
                    except cfg.ConfigError as exc:
                        invalid.append(str(exc))
        self.assertGreater(checked, 20)
        self.assertEqual(invalid, [])


class TestAnExtensionThatFailsToLoadIsSkipped(unittest.TestCase):
    """K1: a broken `on_fail: block` extension does not block; `validate --root` catches it."""

    _BROKEN = "---\nslot: pre-merge\nkind: command\non_fail: block\n---\nno id, no run\n"

    def _repo(self, tmp: str) -> str:
        root = Path(tmp)
        (root / ".keel" / "extensions" / "pre-merge").mkdir(parents=True)
        (root / ".keel" / "extensions" / "pre-merge" / "parity.md").write_text(
            self._BROKEN, encoding="utf-8"
        )
        config = root / ".keel" / "project.yaml"
        config.write_text(
            "extends: keel\ncore_version: '^1.0'\nbase_branch: main\n"
            "knobs:\n  build_gate_cmd: 'true'\ngates: [build]\n"
            "extensions:\n  pre-merge: [pre-merge/parity.md]\n",
            encoding="utf-8",
        )
        return str(config)

    def test_the_loader_skips_it_so_no_gate_is_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = cfg.load_config(self._repo(tmp))
            loaded, problems = extensions.load_extensions(config, tmp, strict=False)
            self.assertEqual(loaded["pre-merge"], [])
            self.assertEqual(len(problems), 1)
            self.assertEqual([s.id for s in gates.plan_gates(config, loaded)], ["build"])
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli.main(
                    ["validate", str(Path(tmp) / ".keel" / "project.yaml"), "--root", tmp]
                )
            self.assertEqual(rc, 1)
            self.assertIn("INVALID", out.getvalue() + err.getvalue())

    def test_extensions_md_says_so(self):
        section = re.sub(
            r"\s+", " ", _read(EXTENSIONS_DOC).split("## Fail-soft")[1].split("\n## ")[0]
        )
        self.assertIn("! extension not loaded", section)
        self.assertIn("even when the file says `on_fail: block`", section)
        self.assertIn("keel validate <project.yaml> --root .", section)
        self.assertNotIn("its error surfaces as a blocking finding", section)


if __name__ == "__main__":
    unittest.main()
