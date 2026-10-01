"""The command adapters describe the flags, contracts and helpers keel actually has.

The 2026-09-29 docs audit (K47–K57) found adapter prose that a host would follow into a
failure: a `keel fixloop brief` example that left out the flags which pick the fixer, a
`--gate-result` re-run on a command that has no such flag, staffing argv that passed
`--effort ""` (an argparse error), hardcoded scan numbers and labels that the scan
contract publishes, a confidence filter looser than the contract's, and an overnight
stop rule that contradicted its own Night mode.

Each test below reads the claim's other side from keel itself — the parser, a contract
builder, or the code path — rather than restating it, so the prose and the code cannot
drift apart silently again. The generated copies are held to the sources by the adapter
drift check; these tests read the sources.
"""

from __future__ import annotations

import argparse
import inspect
import io
import json
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from keel import cli, contracts, ship, swarm_runtime, workblock
from keel import config as cfg
from keel.findings import summarize

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = REPO_ROOT / "src/keel/adapters/commands"
PROJECT = REPO_ROOT / "projects/keel.yaml"

#: Every fenced block, tagged or not: adapters put runnable handoff lines in untagged
#: fences too (the swarm lead's `keel ship …` line), and a host copies either.
_FENCE = re.compile(r"^\s*```([a-z]*)[^\n]*\n(.*?)^\s*```", re.DOTALL | re.MULTILINE)
#: One `keel <sub…>` invocation, after optional `VAR=value` assignments.
_INVOCATION = re.compile(r"^\s*(?:[A-Z_]+=\S+\s+)*keel\s+(.*)$")
#: A token that is only a shell variable: `"$X"`, `$X`, `"${X}"`.
_BARE_VARIABLE = re.compile(r'"?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"?')


def _source(name: str) -> str:
    return (ADAPTERS / f"{name}.md").read_text(encoding="utf-8")


def _prose(name: str) -> str:
    """The adapter with every whitespace run collapsed, so a wrapped sentence matches."""
    return re.sub(r"\s+", " ", _source(name))


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:  # noqa: SLF001 - argparse has no public API
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return dict(action.choices)
    return {}


def _resolve(tokens: list[str]) -> tuple[argparse.ArgumentParser, int]:
    """The deepest parser the leading tokens name (`fixloop brief` → its own parser)."""
    parser, used = cli.build_parser(), 0
    for token in tokens:
        children = _subparsers(parser)
        if token not in children:
            break
        parser, used = children[token], used + 1
    return parser, used


def _options(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {
        option: action
        for action in parser._actions  # noqa: SLF001
        for option in action.option_strings
    }


def _invocations():
    """``(adapter, command, parser, tokens-after-command)`` for every `keel` call."""
    for path in sorted(ADAPTERS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for _tag, block in _FENCE.findall(text):
            for line in block.replace("\\\n", " ").splitlines():
                for segment in re.split(r"\|\||&&|\||;|\$\(|\)|`", line):
                    match = _INVOCATION.match(segment)
                    if not match:
                        continue
                    tokens = match.group(1).split()
                    parser, used = _resolve(tokens)
                    if used:
                        yield path.stem, " ".join(tokens[:used]), parser, tokens[used:]


def _flag(token: str) -> str | None:
    token = token.strip("[]\"'(").split("=", 1)[0].rstrip("]).,")
    return token if token.startswith("--") and token != "--" else None


def _block_with(name: str, needle: str) -> str:
    for _tag, block in _FENCE.findall(_source(name)):
        if needle in block:
            return block
    return ""


class EveryAdapterFlagIsOneTheParserAccepts(unittest.TestCase):
    def test_the_sweep_finds_invocations(self):
        # Guards the guard: a fence regex that matched nothing would pass everything.
        self.assertGreater(len(list(_invocations())), 50)

    def test_every_flag_in_an_adapter_code_block_exists_on_its_command(self):
        unknown, checked = [], 0
        for adapter, command, parser, rest in _invocations():
            known = _options(parser)
            for token in rest:
                flag = _flag(token)
                if flag is None:
                    continue
                checked += 1
                if flag not in known:
                    unknown.append(f"{adapter}.md: keel {command} {flag}")
        self.assertGreater(checked, 100)
        self.assertEqual([], sorted(set(unknown)))


class OptionalStaffingIsBuiltFromSetValues(unittest.TestCase):
    """K54: an unset staffing value must be omitted, never passed empty."""

    #: The batch staffing flags, plus the ones `keel fixloop brief` resolves the fixer from.
    OPTIONAL = frozenset(workblock.DELEGATION_FLAGS) | {"--role", "--tier", "--host-agent"}

    def test_an_empty_value_is_a_parse_error(self):
        # Why the rule exists: the batch parsers reject an empty choice outright.
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["work-block", str(PROJECT), "--effort", ""])

    def test_no_invocation_hands_an_optional_flag_a_bare_variable(self):
        offenders = [
            f"{adapter}.md: keel {command} {token} {rest[index + 1]}"
            for adapter, command, _parser, rest in _invocations()
            for index, token in enumerate(rest[:-1])
            if token in self.OPTIONAL and _BARE_VARIABLE.fullmatch(rest[index + 1])
        ]
        self.assertEqual([], offenders)

    def test_each_batch_adapter_builds_every_delegation_flag_from_set_values(self):
        for name in ("work-block", "overnight"):
            with self.subTest(adapter=name):
                block = _block_with(name, '"${STAFF[@]}"')
                self.assertIn(f"keel {name} ", block)
                for flag in workblock.DELEGATION_FLAGS:
                    self.assertIn(f"STAFF+=({flag} ", block)


class TheFixBriefGetsTheInputsThatPickTheFixer(unittest.TestCase):
    """K48: `keel fixloop brief` resolves the fixer from the flags it is given."""

    def _fixloop_parser(self) -> argparse.ArgumentParser:
        parser, used = _resolve(["fixloop", "brief"])
        self.assertEqual(used, 2)
        return parser

    def _assignment_inputs(self) -> set[str]:
        # The dests `_review_assignment` reads, plus the tier it is handed — read from
        # the code, so a new resolver input shows up here without editing this test.
        read = set(re.findall(r'getattr\(args, "(\w+)"', inspect.getsource(cli._review_assignment)))
        return read | {"tier"}

    def test_the_example_passes_every_assignment_flag_the_parser_accepts(self):
        options = _options(self._fixloop_parser())
        inputs = sorted(
            option
            for option, action in options.items()
            if option.startswith("--") and action.dest in self._assignment_inputs()
        )
        self.assertEqual(inputs, ["--delegate", "--host-agent", "--role", "--tier"])
        block = _block_with("ship", "keel fixloop brief")
        for option in inputs:
            with self.subTest(flag=option):
                self.assertIn(f"FIX_ARGS+=({option} ", block)
        self.assertIn('"${FIX_ARGS[@]}"', block)

    def test_the_adapter_says_which_bench_flags_it_cannot_honour(self):
        options = _options(self._fixloop_parser())
        self.assertNotIn("--team", options)
        self.assertNotIn("--effort", options)
        self.assertIn("`keel fixloop brief` has **no `--team` or `--effort`**", _prose("ship"))


class AGateResultIsRecordedOnShip(unittest.TestCase):
    """K49: only `keel ship` takes `--gate-result`; `run-gates` re-runs reproduce NOT-RUN."""

    def test_ship_is_the_only_command_with_the_flag(self):
        taking = sorted(
            name
            for name, parser in _subparsers(cli.build_parser()).items()
            if "--gate-result" in _options(parser)
        )
        self.assertEqual(taking, ["ship"])

    def test_the_s8_paragraph_records_it_on_ship(self):
        prose = _prose("ship")
        self.assertIn("`keel ship` is the only command that accepts the flag", prose)
        self.assertNotIn("re-run the command with `--gate-result", prose)


class TheTierIsClassifiedUnderAReviewerOverride(unittest.TestCase):
    """K47: `--reviewers` replaces the count, never the tier or its jury trigger."""

    def test_an_override_keeps_the_tier_and_the_tier_3_jury(self):
        assessed = ship.assess(
            changed_files=["src/keel/model.py"],
            gate_verdict=summarize([]),
            tier3_globs=("src/keel/model.py",),
            reviewer_override=1,
        )
        self.assertEqual(assessed.tier, 3)
        self.assertEqual(assessed.reviewers, 1)
        self.assertEqual(assessed.review_contract["jury"]["mode"], "gating")

    def test_s5_does_not_say_the_tier_is_skipped(self):
        prose = _prose("ship")
        self.assertNotIn("the tier is not computed", prose)
        self.assertIn("The tier is classified from the diff whether or not `--reviewers`", prose)


class TheImplementAdapterAsksCore(unittest.TestCase):
    """K50 (and K56): implementer, attribution and worktree removal come from keel."""

    def test_plan_publishes_the_assignment_the_adapter_reads(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(
                ["plan", str(PROJECT), "--root", str(REPO_ROOT), "--command", "ship"]
                + ["--role", "core", "--delegate", "codex", "--json"]
            )
        self.assertEqual(code, 0)
        implementer = json.loads(out.getvalue())["contract"]["assignment"]["implementer"]
        self.assertEqual(
            (implementer["provider"], implementer["source"]), ("codex", "flag:--delegate")
        )
        prose = _prose("implement")
        self.assertIn("--command ship", prose)
        self.assertIn("`contract.assignment.implementer`", prose)
        self.assertNotIn("from `implementer_agents` keyed by", prose)

    def test_no_adapter_removes_a_worktree_by_hand(self):
        offenders = []
        for path in sorted(ADAPTERS.glob("*.md")):
            for sentence in re.split(r"(?<=[.!?])\s", _prose(path.stem)):
                if "git worktree remove" in sentence and "never" not in sentence.lower():
                    offenders.append(f"{path.name}: {sentence.strip()[:120]}")
        self.assertEqual([], offenders, "use `keel worktree-remove` instead")
        self.assertIn("keel worktree-remove", _source("implement"))

    def test_the_deprecated_routing_knob_is_only_named_as_deprecated(self):
        offenders, seen = [], 0
        for path in sorted(ADAPTERS.glob("*.md")):
            for sentence in re.split(r"(?<=[.!?])\s", _prose(path.stem)):
                if "implementer_agents" not in sentence:
                    continue
                seen += 1
                if "deprecated" not in sentence:
                    offenders.append(f"{path.name}: {sentence.strip()[:120]}")
        self.assertGreater(seen, 3)
        self.assertEqual([], offenders, "route through `team.implement.by_role`")


class TheScanAdaptersReadTheirContract(unittest.TestCase):
    """K51/K52: numbers, labels and filters come from `scan_contract`, not the prose."""

    @classmethod
    def setUpClass(cls):
        cls.config = cfg.load_config(str(PROJECT))

    def _scan(self, command: str) -> dict:
        return contracts.scan_contract_as_dict(command=command, config=self.config)

    def test_review_all_day_names_each_contract_value_it_uses(self):
        block = self._scan("review-all-day")["review_all_day"]
        prose = _prose("review-all-day")
        for dotted in (
            "review_all_day.span",
            "strategy.batch_threshold",
            "diff_truncation.max_bytes",
            "finding_filter",
            "issue_creation.title_prefix",
            "issue_creation.labels",
        ):
            with self.subTest(value=dotted):
                node = block
                for key in dotted.removeprefix("review_all_day.").split("."):
                    node = node[key]
                self.assertIn(dotted, prose)

    def test_review_all_day_does_not_ask_keel_window_for_the_span(self):
        # `keel window` takes a project path and answers OPEN/CLOSED for *now*; it has no
        # day count and prints no timestamps, so it cannot be where a span comes from.
        parser, _ = _resolve(["window"])
        self.assertEqual(set(_options(parser)), {"-h", "--help"})
        self.assertNotIn("via `keel window`", _prose("review-all-day"))

    def test_review_all_day_hardcodes_no_label_or_diff_cap(self):
        prose = _prose("review-all-day")
        labels = self._scan("review-all-day")["review_all_day"]["issue_creation"]["labels"]
        self.assertNotIn("review-finding", labels)
        self.assertNotIn("`review-finding`", prose)
        self.assertNotIn("~200 KB", prose)
        threshold = self._scan("review-all-day")["review_all_day"]["strategy"]["batch_threshold"]
        self.assertNotIn(f"count ≤ {threshold}`", prose)

    def test_regression_files_only_what_the_contract_files(self):
        confidence = self._scan("regression")["regression"]["confidence_filter"]
        prose = _prose("regression")
        self.assertIn("scan_contract.regression.confidence_filter", prose)
        self.assertIn(f"`{json.dumps(confidence['file_only_when'])}`", prose)
        self.assertNotIn("1. **Drop low-confidence** findings from the issue-creation set", prose)


class TheBatchAdaptersMatchTheirParsers(unittest.TestCase):
    """K53/K55: work-block's own flags, and overnight's one window rule."""

    def test_work_block_says_it_has_an_implementer_flag_and_no_jury_flag(self):
        parser, _ = _resolve(["work-block"])
        options = _options(parser)
        self.assertIn("--delegate", options)
        self.assertFalse({"--jury", "--no-jury", "--jury-advisory"} & set(options))
        prose = _prose("work-block")
        self.assertNotIn("no implementer or jury flag", prose)
        self.assertIn("Work-block has no jury flag of its own", prose)

    def test_overnight_defines_window_close_as_the_stop_it_publishes(self):
        config = cfg.load_config(str(PROJECT))
        stops = contracts.session_contract_as_dict(command="overnight", config=config)["overnight"][
            "stop_conditions"
        ]
        self.assertIn("merge-window-close", stops)
        prose = _prose("overnight")
        self.assertIn("it means one event: the window **closing during this session**", prose)
        self.assertIn("A session that starts `CLOSED` is in Night mode", prose)
        self.assertNotIn("Stop the loop at window close.", prose)
        self.assertNotIn("over the queue until the window closes", prose)


class TheSwarmAdapterDescribesTheDryRunItAllows(unittest.TestCase):
    """K57: no worktree in a dry run, and landing targets `base_branch`."""

    def test_worktrees_are_cut_only_outside_a_dry_run(self):
        source = inspect.getsource(swarm_runtime.run_swarm_orchestration)
        self.assertIn("if not dry_run:", source)
        # The `create_worktrees` switch beside `dry_run` is gone (#1280): the run's mode
        # alone decides, so the prose names no second condition.
        self.assertNotIn(
            "create_worktrees", inspect.signature(swarm_runtime.run_swarm_orchestration).parameters
        )
        prose = _prose("swarm")
        self.assertIn("only when the run is not dry", prose)
        self.assertNotIn("worktrees are enabled", prose)
        self.assertNotIn("Launch parallel workers per cluster in dedicated git worktrees", prose)
        self.assertNotIn("That cuts a worktree per cluster", prose)

    def test_landing_names_the_configured_base_branch(self):
        prose = _prose("swarm")
        self.assertNotIn("onto `main`", prose)
        self.assertNotIn("merged into main", prose)
        self.assertIn("through `keel merge` against the project's `base_branch`", prose)

    def test_the_live_run_is_described_as_it_now_is(self):
        """#1402/#1409/#1414: a live run implements and opens one PR per cluster, and
        `swarm-land` merges each through `keel merge`. The description and the body said
        "a live run lands nothing yet" and "swarm cannot do it" after that landed."""
        prose = _prose("swarm")
        description = re.search(r"^description: (.*)$", _source("swarm"), re.M).group(1)
        self.assertNotIn("lands nothing yet", prose)
        self.assertIn("opens one PR per cluster", description)
        self.assertIn("once its review verdicts are posted", description)
        self.assertNotIn("swarm cannot do it", prose)
        self.assertNotIn("do not use this to land work", prose)
        self.assertIn("it is still the proven path", prose)

    def test_the_one_live_landing_is_stated_without_overclaiming(self):
        """#1281's closing comment: one live landing has run, on a sandbox repository, with
        each pull request reviewed outside the swarm. The adapter said "no real landing has
        been exercised yet" after that ran. It may say the landing happened, but not that
        the swarm reviews its own pull requests (#1423) or that it stopped being
        experimental."""
        prose = _prose("swarm")
        description = re.search(r"^description: (.*)$", _source("swarm"), re.M).group(1)
        self.assertNotIn("no real landing has been exercised yet", prose)
        self.assertIn("One live landing has run end to end, once, on a sandbox repository", prose)
        self.assertIn("their review verdicts were posted from outside the swarm", prose)
        # #1422: the landing closes what it merged; the adapter said it left the issues open.
        self.assertNotIn("left the landed issues open", prose)
        self.assertIn("closes what it merged as `/keel:ship` does", prose)
        self.assertIn("then the issues closed (#1422)", prose)
        self.assertIn("Whether the swarm should review its own pull requests is still open", prose)
        self.assertIn("Swarm stays experimental", prose)
        self.assertIn("nothing in the swarm reviews the work it opens", prose)
        self.assertTrue(description.startswith("EXPERIMENTAL"))
        self.assertIn("nothing in the swarm reviews them", description)
        self.assertIn("one live landing has run, on a sandbox repository", description)
        self.assertIsNone(re.search(r"(?i)(?<!nothing in )the swarm (reviews|dispatches)", prose))


if __name__ == "__main__":
    unittest.main()
