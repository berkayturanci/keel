"""What the docs and the site *claim* keel is, checked against keel (issue #1019).

`tests/test_documented_commands.py` already asks whether a documented `keel <cmd>`
is a real subcommand. Every defect this module pins slipped past it, because
every one of them is a claim of a different shape:

* Eleven subcommands had no section in `docs/keel/cli.md` at all — `gc`,
  `canary`, `rollback`, `cost-report`, `close-reconcile`, `dryrun-verify`,
  `scratch-dir`, `release`, `adapter-status`, `update-adapter`, `sync`. A
  reference is not a reference if reaching a command means reading `cli.py`.
* `knobs.swarm_review_evidence` — the knob that decided whether swarm landings
  enforced review at all (it no longer can, #1287) — was in the schema and in no
  configuration table.
* The website said **16** `/keel` commands in five places and **17** in five
  others. `swarm` made it 17; half the site was never updated.
* `website/integrations.js` promises "100% real Keel CLI commands" in its own
  header and showed `keel ship … --delegate cursor` six times. `keel ship` has
  no `--delegate` flag — it lives on `keel implement` and on the `/keel:ship`
  adapter. `keel cost-report .keel/project.yaml` and a bare
  `keel evidence-verify … --phase pre-merge` do not parse either.
* `keel swarm-plan … 714 715 716 717`, `keel swarm-land … --mode auto` and
  `keel window … --root .` appeared in the docs *and in the adapter bodies
  agents execute*. Every one of those is a real subcommand with flags that do
  not exist, which is exactly the gap `test_documented_commands.py` leaves.

So the checks here compare a claim to the thing it claims about: the argparse
parser, the JSON schema, and the adapter command set — never a list retyped in
a test, which would drift the same way the prose did.

Everything is offline: these are facts about this checkout.
"""

from __future__ import annotations

import ast
import collections
import contextlib
import html
import inspect
import io
import json
import re
import shlex
import tempfile
import tomllib
import unittest
import unittest.mock
from pathlib import Path

from keel import (
    api_delegate,
    cli,
    cost,
    doctor,
    evidence,
    extensions,
    gates,
    install,
    intake,
    model,
    providers,
    ship,
    swarm_landing,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"
CLI_DOC = REPO_ROOT / "docs" / "keel" / "cli.md"
CONFIG_DOC = REPO_ROOT / "docs" / "keel" / "configuration.md"
PARAM_DOC = REPO_ROOT / "docs" / "keel" / "parameter-reference.md"
MODELS_DOC = REPO_ROOT / "docs" / "keel" / "models.md"
SCHEMA = REPO_ROOT / "src" / "keel" / "schema" / "project.schema.json"

#: `## `keel <name> …`` / `### `keel <name> …`` — the signature headings cli.md uses.
_CLI_SECTION = re.compile(r"(?m)^#{2,3} `keel ([a-z][a-z0-9-]*)")

#: A `knobs` key as configuration.md spells it: a table row and a detail heading.
_KNOB_ROW = re.compile(r"(?m)^\| `([a-z0-9_]+)` \|")
_KNOB_HEADING = re.compile(r"(?m)^#### `([a-z0-9_]+)`")

EXTENSIONS_DOC = REPO_ROOT / "docs" / "keel" / "extensions.md"

#: Any backticked token, single or double — Markdown and reStructuredText in one pattern.
_TICKED = re.compile(r"`{1,2}([^`]+)`{1,2}")

#: A row of the slot table in extensions.md: `| slots | step | mode | may block? | use |`.
_SLOT_ROW = re.compile(r"(?m)^\|(?P<slots>[^|]*)\|[^|]*\|[^|]*\|(?P<blocks>[^|]*)\|[^|]*\|\s*$")

#: Every prose restatement of "which slots may declare `on_fail: block`", with the
#: pattern that captures the fragment naming them. The rule itself is
#: `keel.model.SLOT_DEFINITIONS`' `may_block` flag, which `extensions.parse_extension`
#: enforces; each entry here is a *claim about* that flag, and #1100 is what one costs
#: when it drifts — the module docstring said `pre-merge` alone, so an author who wanted
#: a blocking `guard` read a restriction the validator never had.
_BLOCKING_CLAIMS = (
    ("src/keel/extensions.py", re.compile(r"``on_fail: block``, valid only in([^)]+)\)")),
    ("AGENTS.md", re.compile(r"`on_fail: block` is permitted only in[^:]+:([^.]+)\.")),
    ("CONTRIBUTING.md", re.compile(r"`on_fail: block` is permitted only in[^:]+:([^.]+)\.")),
    ("docs/keel/extensions.md", re.compile(r"\*\*`block` is only allowed in([^*]+)\*\*")),
    (
        "docs/proposals/keel-architecture.md",
        re.compile(r"`on_fail: block`, only valid in([^)]+)\)"),
    ),
)


def _blocking_slots() -> set[str]:
    """The slots that may declare `on_fail: block`, from the backbone itself."""
    return {slot.name for slot in model.SLOT_DEFINITIONS if slot.may_block}


def _named_slots(fragment: str) -> set[str]:
    """The slot names a prose fragment backticks; anything else in it is ignored."""
    return {name for name in _TICKED.findall(fragment) if name in model.SLOTS}


def _subcommands() -> set[str]:
    """Every top-level subcommand, read from the parser rather than listed here."""
    parser = cli.build_parser()
    out: set[str] = set()
    for action in parser._subparsers._group_actions:  # noqa: SLF001 - argparse has no public API
        out.update(getattr(action, "choices", {}) or {})
    return out


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    """``keel <argv>`` in-process — no subprocess, no network, output captured."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _adapter_commands() -> list[Path]:
    return sorted((REPO_ROOT / "src/keel/adapters/commands").glob("*.md"))


class TestEverySubcommandHasAReferenceSection(unittest.TestCase):
    def test_the_parser_exposes_subcommands(self):
        # Guards the guard: an argparse shape change that returned nothing here
        # would make the assertion below pass while checking nothing.
        self.assertGreater(len(_subcommands()), 40)

    def test_cli_md_documents_every_subcommand(self):
        documented = set(_CLI_SECTION.findall(CLI_DOC.read_text(encoding="utf-8")))
        missing = sorted(_subcommands() - documented)
        self.assertEqual(
            [],
            missing,
            "these subcommands have no `## `keel <name> …`` section in docs/keel/cli.md — "
            f"add one (signature, flags, exit codes, one example): {missing}",
        )

    def test_cli_md_documents_nothing_that_is_not_a_subcommand(self):
        """The other direction: a removed command must not keep its section.

        `keel verify-evidence` (#803) was documented for months and never
        existed. That one was caught in a code block; a heading is the more
        prominent place to leave the same lie.
        """
        documented = set(_CLI_SECTION.findall(CLI_DOC.read_text(encoding="utf-8")))
        unknown = sorted(documented - _subcommands())
        self.assertEqual([], unknown, f"cli.md documents non-commands: {unknown}")


class TestEveryKnobIsDocumented(unittest.TestCase):
    """A knob in the schema and in no table is a knob nobody can find.

    `knobs.swarm_review_evidence` decided whether `keel swarm-land` enforced the
    ship s10 review-evidence contract at all (#828). It shipped documented only
    in its own schema `description`.
    """

    def _knobs(self) -> set[str]:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        return set(schema["properties"]["knobs"]["properties"])

    def test_the_schema_declares_knobs(self):
        self.assertGreater(len(self._knobs()), 10)

    def test_configuration_md_lists_every_knob_in_the_table(self):
        rows = set(_KNOB_ROW.findall(CONFIG_DOC.read_text(encoding="utf-8")))
        missing = sorted(self._knobs() - rows)
        self.assertEqual(
            [],
            missing,
            "these `knobs.properties` keys are in project.schema.json but have no row in "
            f"the `## knobs` table of docs/keel/configuration.md: {missing}",
        )

    def test_configuration_md_explains_every_knob(self):
        headings = set(_KNOB_HEADING.findall(CONFIG_DOC.read_text(encoding="utf-8")))
        missing = sorted(self._knobs() - headings)
        self.assertEqual(
            [],
            missing,
            "these knobs have a table row but no `#### `<knob>`` detail section in "
            f"docs/keel/configuration.md: {missing}",
        )


class TestTheStatedCommandCountIsReal(unittest.TestCase):
    """The count is spelled out in eighteen places and derived in none of them.

    Round 1 found 16 in five places and 17 in five others. Round 3's review found
    three more the first sweep had not enumerated — `coverage.html`'s sidebar badge,
    the README's "16 shipped commands", `keel-visual.md`'s see-also line — plus two
    in files nobody had looked at (`website/README.md`, `keel-visual/README.md`).
    That is the argument for listing every site *and* prose spot here rather than
    trusting a hand-kept list in a handoff note: `website/README.md` had one, it
    said "three static 16 spots", and it was wrong on both halves.

    Paths are repo-relative because the claim is not a website-only property.
    Historical `CHANGELOG.md` entries are deliberately absent: 16 was true when
    they were written, and a changelog that is edited to stay current is not one.

    `test_documented_commands.TestCommandCountClaims` covers one of these spots
    already. The overlap is kept on purpose: this list is meant to be the single
    register of *every* place the count appears, and a register with a hole in it
    where another test happens to look is the shape that let three spots survive
    round 1.
    """

    #: Every place the count is spelled out. A glob would sweep SVG `height="16"`
    #: and viewBox numbers, so the shapes are written out.
    _CLAIMS = (
        ("website/llms.txt", re.compile(r"> (\d+) `/keel` commands")),
        ("website/docs.html", re.compile(r"all (\d+) /keel commands")),
        ("website/docs.html", re.compile(r'Workflow commands <span class="badge">(\d+)</span>')),
        ("website/index.html", re.compile(r'Workflow commands <span class="badge">(\d+)</span>')),
        ("website/index.html", re.compile(r"(\d+) /keel commands, stdlib-first")),
        ("website/index.html", re.compile(r"All (\d+) <code>/keel:")),
        ("website/index.html", re.compile(r"(\d+) workflows · /keel:")),
        ("website/index.html", re.compile(r"<b>(\d+)<span class=\"u\"> cmds</span>")),
        (
            "website/coverage.html",
            re.compile(r'Workflow commands <span class="badge">(\d+)</span>'),
        ),
        ("website/content.js", re.compile(r"All (\d+) /keel:")),
        ("website/content.js", re.compile(r"keel ships <b>(\d+)</b> agentic workflow commands")),
        ("website/content.js", re.compile(r"Every one of the <b>(\d+)</b> commands")),
        ("website/home.js", re.compile(r"all (\d+) commands")),
        ("website/README.md", re.compile(r"Workflow commands \((\d+), each with an animated")),
        ("README.md", re.compile(r"any of the (\d+) command flows")),
        ("README.md", re.compile(r"\*\*(\d+) shipped commands\*\*")),
        ("docs/keel/keel-visual.md", re.compile(r"the (\d+) `/keel:<command>` workflows")),
        ("keel-visual/README.md", re.compile(r"\*\*all (\d+) keel commands\*\*")),
    )

    def test_the_adapters_are_findable(self):
        self.assertGreater(len(_adapter_commands()), 10)

    def test_every_stated_command_count_matches_the_adapters(self):
        expected = str(len(_adapter_commands()))
        wrong: dict[str, list[str]] = {}
        for name, pattern in self._CLAIMS:
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            found = pattern.findall(text)
            # A claim that vanished is drift too: the pattern is the record of
            # where the count is stated, so an empty match means the sentence
            # was rewritten and this list needs the new shape.
            self.assertTrue(found, f"{name} no longer states {pattern.pattern!r}")
            bad = [n for n in found if n != expected]
            if bad:
                wrong.setdefault(name, []).extend(bad)
        self.assertEqual(
            {},
            wrong,
            f"a documented /keel command count is not {expected} "
            f"(src/keel/adapters/commands/): {wrong}",
        )


class TestTheEnumeratedCommandsAreTheShippedOnes(unittest.TestCase):
    """Two files do not just count the commands — they list them, and both were short.

    `README.md` promised "16 shipped commands" and named sixteen, omitting `swarm`;
    `keel-visual/README.md` promised "all 16 keel commands" and named the same sixteen.
    A count check would have caught the number and left the list one command short,
    which is the more misleading half: a reader takes an enumeration as exhaustive.

    So the list is compared to the shipped set, not to its own length.
    """

    #: ``(path, the phrase that opens the list, the phrase that closes it)``. The
    #: openers carry no digit on purpose: the count is
    #: :class:`TestTheStatedCommandCountIsReal`'s job, and an anchor that moved
    #: with it would report a *missing list* whenever only the number was wrong.
    _ENUMERATIONS = (
        ("README.md", "shipped commands**", "Each is described in"),
        ("keel-visual/README.md", "`--command` accepts **all", "Each renders its own"),
        # `keel install-adapter`'s reference named fifteen and left out `swarm` and
        # `work-block` (docs audit 2026-09-29).
        ("docs/keel/cli.md", "shipped set:", "Existing files are skipped"),
    )

    def test_every_enumeration_names_every_shipped_command(self):
        shipped = {path.stem for path in _adapter_commands()}
        self.assertGreater(len(shipped), 10)
        for name, opening, closing in self._ENUMERATIONS:
            with self.subTest(file=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                # `assertTrue`, not `assertIn`: a failing `assertIn` on a file this
                # size prints the whole README as the diff.
                self.assertTrue(
                    opening in text and closing in text,
                    f"{name} no longer delimits its command list with {opening!r} … {closing!r}",
                )
                block = text.split(opening, 1)[1].split(closing, 1)[0]
                named = set(re.findall(r"[a-z][a-z0-9-]+", block))
                missing = sorted(shipped - named)
                self.assertEqual(
                    [],
                    missing,
                    f"{name} enumerates the shipped commands and omits {missing} — "
                    "a reader reads a list like this as exhaustive",
                )


class TestTheSiteStatesTheRealBackboneShape(unittest.TestCase):
    """`13 steps` and `28 extension slots` are printed in several places across the site.

    Both are hero numbers: they appear in the README's hero and in the site's body copy
    (the social-card alt text used to carry them too), and every one of them is
    hand-typed. The command count drifted exactly this way — 16 in five places, 17 in
    five others — and there is no reason the other two are safer. `model.BACKBONE` and
    `model.SLOTS` are the answer.
    """

    _PAGES = ("index.html", "docs.html", "coverage.html", "content.js")

    def test_the_backbone_is_readable(self):
        self.assertGreater(len(model.BACKBONE), 5)
        self.assertGreater(len(model.SLOTS), 5)

    def test_every_stated_step_and_slot_count_matches_the_backbone(self):
        # `(?<![A-Za-z])` keeps `the s4 step,` out of the step count.
        expected = {
            "steps": (re.compile(r"(?<![A-Za-z])(\d+) steps?\b"), str(len(model.BACKBONE))),
            "slots": (re.compile(r"(\d+) (?:extension|named) slots"), str(len(model.SLOTS))),
        }
        wrong: dict[str, list[str]] = {}
        seen = dict.fromkeys(expected, 0)
        for name in (*self._PAGES, "README.md"):
            path = REPO_ROOT / name if name == "README.md" else SITE / name
            text = path.read_text(encoding="utf-8")
            for label, (pattern, want) in expected.items():
                found = pattern.findall(text)
                seen[label] += len(found)
                bad = [n for n in found if n != want]
                if bad:
                    wrong.setdefault(f"{name}:{label}", []).extend(bad)
        # Guards the guard: a rewritten sentence that stops matching would make
        # the assertion below vacuous, which is how the count drifted in the
        # first place.
        self.assertTrue(all(seen.values()), f"a hero-number pattern matched nothing: {seen}")
        self.assertEqual(
            {},
            wrong,
            "the site states a backbone shape that disagrees with keel.model "
            f"({len(model.BACKBONE)} steps, {len(model.SLOTS)} slots): {wrong}",
        )


class TestTheSiteArgumentHintsAreTheAdapters(unittest.TestCase):
    """`website/params.js` opens with "generated from src/keel/adapters/commands frontmatter".

    For a long time no generator existed — the file was hand-maintained under a comment
    claiming it was not, which is the most reliable way to drift. It had: `/keel:swarm`
    advertising `--rebalance` and `--landing <batch|funnel|auto>` (the adapter body defines
    and acts on neither) plus a `--dry-run` the frontmatter never listed, while the two flags
    the body *does* branch on were absent; later a `--review-delegate` hint change never
    reached the site at all.

    `keel.install.site_params_files()` now renders the file (issue #1051, `make site-params`),
    and `tests/test_install.py::TestSiteParamsGenerator` locks the committed copy byte-for-byte
    against that generator. These checks are kept as the independent second opinion: they read
    the frontmatter with their own regexes rather than through the generator, so a bug in the
    generator cannot make them vacuous the way a shared helper would.
    """

    def _site_args(self) -> dict[str, dict]:
        text = (SITE / "params.js").read_text(encoding="utf-8")
        return json.loads(text.split("=", 1)[1].strip().rstrip(";"))

    @staticmethod
    def _frontmatter(path: Path) -> dict[str, str | None]:
        block = path.read_text(encoding="utf-8").split("---", 2)[1]
        hint = re.search(r'(?m)^argument-hint:\s*"(.*)"\s*$', block)
        desc = re.search(r"(?m)^description:\s*(.*)$", block)
        return {
            "hint": hint.group(1) if hint else None,
            "desc": desc.group(1).strip() if desc else None,
        }

    def test_the_site_publishes_argument_hints(self):
        self.assertGreater(len(self._site_args()), 10)

    def test_every_published_hint_matches_its_adapter(self):
        site = self._site_args()
        drift: dict[str, dict[str, str | None]] = {}
        for path in _adapter_commands():
            published = site.get(path.stem)
            if published is None:
                drift[path.stem] = {"site": None, "adapter": "present"}
                continue
            source = self._frontmatter(path)
            for field in ("hint", "desc"):
                if source[field] != published.get(field):
                    drift[f"{path.stem}.{field}"] = {
                        "adapter": source[field],
                        "site": published.get(field),
                    }
        self.assertEqual({}, drift, f"website/params.js has drifted from its source: {drift}")

    #: An innermost `[...]` pair: one with no bracket of its own inside it.
    _INNERMOST = re.compile(r"\[([^\[\]]*)\]")
    #: The stand-in an already-reduced group leaves behind. Never occurs in a hint.
    _SLOT = re.compile("\x00[0-9]+\x00")

    @classmethod
    def _reduce(cls, hint: str) -> tuple[str, dict[str, str]]:
        """Replace every `[...]` pair, innermost first, with a bracket-free stand-in.

        What survives in the returned skeleton is the hint's top level: the stand-ins for
        its own groups, plus whatever text sat outside every bracket. Reducing inwards-out
        is a deliberately different route to the same answer as `keel.install.hint_flags`,
        which walks the string once carrying a depth counter — so a bug in either shows up
        here as a disagreement rather than as two copies agreeing with each other.
        """
        groups: dict[str, str] = {}
        skeleton = hint
        while (pair := cls._INNERMOST.search(skeleton)) is not None:
            slot = f"\x00{len(groups)}\x00"
            groups[slot] = pair.group(1)
            skeleton = f"{skeleton[: pair.start()]}{slot}{skeleton[pair.end() :]}"
        return skeleton, groups

    @classmethod
    def _chips(cls, hint: str) -> list[str]:
        """The hint's own top-level groups, in order, scanned without the generator."""
        skeleton, groups = cls._reduce(hint)

        def expand(text: str) -> str:
            # Only descend into a stand-in this text actually holds; a group's content
            # can only name stand-ins made before it, so the walk terminates.
            for slot, inner in groups.items():
                if slot in text:
                    text = text.replace(slot, f"[{expand(inner)}]")
            return text

        found = [expand(groups[slot]).strip() for slot in cls._SLOT.findall(skeleton)]
        return [chip for chip in found if chip]

    def test_the_published_flags_are_exactly_the_hints_own_groups(self):
        """The card's flag chips are a split of the hint — in both directions.

        This asked only whether every published chip was somewhere in its hint, so an
        invented chip failed and a missing one did not. That is the wrong way round: the
        chips are what the generator computes, and a reviewer proved the gap by making
        the scanner drop one — the file regenerated without it and both suites stayed
        green. The `swarm` card's stale `--rebalance` is the failure this catches from
        the other side; a silently shortened chip row is what it catches now.

        The comparison is against this class's own scan of the *adapter's* hint, so the
        generator's `hint_flags` is never consulted, directly or by import.
        """
        site = self._site_args()
        drift: dict[str, dict[str, list[str]]] = {}
        for path in _adapter_commands():
            published = site.get(path.stem)
            if published is None:  # reported by the hint/desc test above
                continue
            expected = self._chips(self._frontmatter(path)["hint"] or "")
            if list(published.get("flags", ())) != expected:
                drift[path.stem] = {
                    "adapter": expected,
                    "site": list(published.get("flags", ())),
                }
        self.assertEqual({}, drift, f"params.js flag chips are not the hint's groups: {drift}")

    def test_no_hint_carries_text_outside_its_brackets(self):
        """The split keeps bracketed groups only, and says nothing about what it drops.

        Across all seventeen hints nothing is outside a bracket today, so the rule holds
        by luck rather than by construction — and an unbalanced bracket would silently
        swallow the rest of the line. Pin it here rather than teaching the generator to
        complain: the day a hint grows prose, this fails and the decision gets made.
        """
        loose = {}
        for path in _adapter_commands():
            hint = self._frontmatter(path)["hint"] or ""
            skeleton, _groups = self._reduce(hint)
            leftover = self._SLOT.sub("", skeleton).strip()
            if leftover:
                loose[path.stem] = leftover
        self.assertEqual({}, loose, f"hint text outside every bracket is dropped: {loose}")

    def test_the_site_publishes_nothing_that_is_not_a_command(self):
        extra = sorted(set(self._site_args()) - {p.stem for p in _adapter_commands()})
        self.assertEqual([], extra, f"params.js publishes non-commands: {extra}")


class TestTheDocumentedInstallTargetsAreTheAcceptedOnes(unittest.TestCase):
    """`keel install-adapter site` shipped with the parameter reference still listing four.

    `cli.md` gained the row and the example in the same commit; the value cell and the
    examples block in `parameter-reference.md` did not, while the command's own `--help`
    and its `unknown target …; valid: …` refusal named `site` from the first commit. That
    is the drift this PR removed from `website/params.js` in a second place: a hand-typed
    list of the same set the code already computes.

    The accepted set is read out of the dispatcher — the `args.agent == "…"` branches plus
    whichever `install.<TUPLE>` its fan-out branch tests — and never retyped here. Each
    member is then *run* against a temporary root, so a regex that matched the wrong thing
    fails instead of quietly redefining what "accepted" means.
    """

    #: `args.agent == "plugin"` — one accepted target, spelled as its own dispatch branch.
    _AGENT_LITERAL = re.compile(r'args\.agent == "([a-z-]+)"')
    #: `args.agent in install.TARGETS` — a tuple of targets the dispatcher fans over.
    _AGENT_TUPLE = re.compile(r"args\.agent in install\.([A-Z_]+)")
    #: The `## `keel install-adapter`` section, up to the next command heading.
    _SECTION = re.compile(r"(?ms)^## `keel install-adapter`\n(.*?)(?=^## `keel )")
    #: That section's `agent` row: the cell of accepted values is the second column.
    _AGENT_ROW = re.compile(r"(?m)^\| `agent` \| (.+?) \| ")
    #: A backticked value inside the cell — `all`, `plugin`, `site`, …
    _VALUE = re.compile(r"`([a-z-]+)`")
    #: A pasteable `keel install-adapter <target>` line in a fenced example.
    _INVOCATION = re.compile(r"(?m)^keel install-adapter ([a-z-]+)")

    def _accepted(self) -> set[str]:
        source = inspect.getsource(cli._cmd_install_adapter)
        targets = set(self._AGENT_LITERAL.findall(source))
        for name in self._AGENT_TUPLE.findall(source):
            targets |= set(getattr(install, name))
        return targets

    def _documented(self) -> set[str]:
        section = self._SECTION.search(PARAM_DOC.read_text(encoding="utf-8"))
        self.assertIsNotNone(section, "parameter-reference.md has no install-adapter section")
        row = self._AGENT_ROW.search(section.group(1))
        self.assertIsNotNone(row, "the `keel install-adapter` section has no `agent` row")
        return set(self._VALUE.findall(row.group(1)))

    def test_every_accepted_target_really_is_accepted(self):
        """Guards the guard: a scraped name the command rejects would be a regex artifact."""
        accepted = sorted(self._accepted())
        self.assertGreaterEqual(len(accepted), 4, f"the dispatcher scan found {accepted}")
        rejected = []
        with tempfile.TemporaryDirectory() as d:
            for target in accepted:
                rc, _out, err = _run_cli(["install-adapter", target, "--root", d])
                if rc != 0:
                    rejected.append(f"{target}: rc={rc} {err.strip()}")
            unknown_rc, _out, unknown_err = _run_cli(["install-adapter", "codex", "--root", d])
        self.assertEqual([], rejected, f"scanned targets the command refuses: {rejected}")
        self.assertEqual(1, unknown_rc)
        self.assertIn("unknown target", unknown_err)

    def test_the_reference_lists_exactly_the_accepted_targets(self):
        accepted, documented = self._accepted(), self._documented()
        self.assertEqual(
            accepted,
            documented,
            "parameter-reference.md's `keel install-adapter` values disagree with the CLI "
            f"(undocumented: {sorted(accepted - documented)}; "
            f"not accepted: {sorted(documented - accepted)})",
        )

    def test_the_examples_show_every_repo_level_target(self):
        """The examples are what a reader pastes; the value cell alone is not runnable.

        Both repo-level surfaces have to appear, because they are the two an example is
        the only place a reader meets — `install-adapter all` never writes either.
        """
        docs = PARAM_DOC.read_text(encoding="utf-8")
        shown = set(self._INVOCATION.findall(docs))
        self.assertEqual(
            set(),
            shown - self._accepted(),
            f"parameter-reference.md pastes targets the CLI refuses: {sorted(shown)}",
        )
        repo_level = self._accepted() - set(install.TARGETS) - {"all"}
        self.assertEqual(
            set(),
            repo_level - shown,
            f"repo-level targets with no example: {sorted(repo_level - shown)}",
        )


class TestEveryDocumentedInvocationParses(unittest.TestCase):
    """A documented flag that does not exist fails on the reader's first paste.

    `test_documented_commands.py` asks whether `keel <cmd>` is real and stops
    there, so `keel swarm-land … --mode auto`, `keel swarm-plan … 714 715 716`
    and `keel window … --root .` were documented for months — the last three
    inside adapter bodies an agent executes verbatim.

    The parser is asked, not called: `parse_args` runs no command and touches
    nothing. Lines carrying a `<placeholder>`, a `$VAR` or a glob are skipped —
    they are deliberately not runnable as written.
    """

    #: Only fenced blocks tagged as a shell — what a reader pastes. Prose and
    #: inline backticks mark emphasis as often as commands.
    _FENCE = re.compile(r"^```(?:bash|sh|shell|console|zsh)[^\n]*\n(.*?)^```", re.DOTALL | re.M)
    #: Shell continuations join into one command before parsing.
    _CONTINUATION = re.compile(r"\\\n\s*")
    #: Everything from the first pipe, redirect or comment is the shell's, not keel's.
    _SHELL_TAIL = re.compile(r"\s+[|>#]")

    _GLOBS = (
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/**/*.md",
        "keel-visual/*.md",
        "src/keel/adapters/commands/*.md",
    )

    def _invocations(self) -> list[tuple[str, str]]:
        """``[(file, command), …]`` for every runnable `keel …` line."""
        found: list[tuple[str, str]] = []
        for pattern in self._GLOBS:
            for path in sorted(REPO_ROOT.glob(pattern)):
                # Relative, not absolute: this checkout may itself *be* a
                # worktree under .keel/worktrees, and an absolute-path filter
                # would then skip every file (the #803 empty-sweep trap).
                relative = path.relative_to(REPO_ROOT)
                if not path.is_file() or relative.parts[0] == ".keel":
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for block in self._FENCE.findall(text):
                    for line in self._CONTINUATION.sub(" ", block).splitlines():
                        command = line.strip().removeprefix("$ ").strip()
                        if not command.startswith("keel "):
                            continue
                        command = self._SHELL_TAIL.split(f" {command}")[0].strip()
                        found.append((str(relative), command))
        return found

    @staticmethod
    def _runnable(argv: list[str]) -> bool:
        return not any(a.startswith(("<", "$")) or "*" in a for a in argv)

    def test_the_docs_contain_invocations(self):
        # Guards the guard: a regex matching nothing would pass vacuously.
        self.assertGreater(len(self._invocations()), 50)

    def test_every_invocation_parses(self):
        parser = cli.build_parser()
        failures: list[str] = []
        for where, command in self._invocations():
            try:
                argv = shlex.split(command)[1:]
            except ValueError:  # pragma: no cover - unbalanced quotes in prose
                continue
            if not self._runnable(argv):
                continue
            errors = io.StringIO()
            try:
                with (
                    contextlib.redirect_stderr(errors),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    parser.parse_args(argv)
            except SystemExit as exit_:
                if exit_.code == 0:  # `keel --version` prints and exits 0
                    continue
                detail = errors.getvalue().strip().splitlines()
                failures.append(f"{where}: {command} -> {detail[-1] if detail else 'rejected'}")
        self.assertEqual(
            [],
            failures,
            "documented `keel …` invocations the CLI would reject:\n" + "\n".join(failures),
        )


class TestTheIntegrationsCatalogRunsWhatItShows(unittest.TestCase):
    """`website/integrations.js` says "100% real Keel CLI commands" in its header.

    It showed `keel ship … --delegate <name>` on six cards; `--delegate` is a
    `keel implement` / `/keel:ship` flag and `keel ship` rejects it. The card
    text is the whole product for a reader who has not installed keel yet, so a
    command that cannot run is the worst place for a typo.

    Only `keel …` cards are parsed: the catalog legitimately shows `brew`,
    `pipx`, `curl`, `uses:` and `export` lines that are not keel's to validate.
    """

    _CMD = re.compile(r'cmd: "([^"]+)"')

    def _keel_commands(self) -> list[str]:
        text = (SITE / "integrations.js").read_text(encoding="utf-8")
        return [c for c in self._CMD.findall(text) if c.startswith("keel ")]

    def test_the_catalog_shows_keel_commands(self):
        self.assertGreater(len(self._keel_commands()), 5)

    def test_every_catalog_command_parses(self):
        parser = cli.build_parser()
        failures: list[str] = []
        for command in self._keel_commands():
            errors = io.StringIO()
            try:
                with (
                    contextlib.redirect_stderr(errors),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    parser.parse_args(shlex.split(command)[1:])
            except SystemExit as exit_:
                if exit_.code == 0:
                    continue
                detail = errors.getvalue().strip().splitlines()
                failures.append(f"{command} -> {detail[-1] if detail else 'rejected'}")
        self.assertEqual(
            [],
            failures,
            "website/integrations.js promises real CLI commands and shows these, "
            "which the CLI rejects:\n" + "\n".join(failures),
        )

    _DELEGATE = re.compile(r"--delegate\s+([A-Za-z0-9._:-]+)")
    _NOTE = re.compile(r'note: "((?:[^"\\]|\\.)*)"')

    def _cards(self) -> list[dict]:
        """Every catalogue card as ``{id, cmd, note}`` — note is ``""`` when absent.

        The closing brace's comma is **optional**: the last object in a JavaScript array
        literal has none, so requiring it silently dropped the final card. Both gate seats
        found it — 29 parsed against 30 defined — and the consequence is the one that
        matters: a `--delegate` card added at the end of the array would never be checked.
        `test_the_parser_sees_every_card` pins the count against the ids.
        """
        text = (SITE / "integrations.js").read_text(encoding="utf-8")
        cards = []
        for block in re.finditer(r'\n      id: "([^"]+)",(.*?)\n    \},?', text, re.S):
            body = block.group(2)
            cmd = re.search(r'cmd: "((?:[^"\\]|\\.)*)"', body)
            note = self._NOTE.search(body)
            cards.append(
                {
                    "id": block.group(1),
                    "cmd": cmd.group(1) if cmd else "",
                    "note": note.group(1) if note else "",
                }
            )
        self.assertTrue(cards, "no cards parsed — the catalogue's shape changed")
        return cards

    def test_a_card_naming_a_delegate_keel_ships_no_profile_for_says_so(self):
        """Parsing is not the whole check (#1132).

        The class above catches a command the CLI *rejects*. It cannot catch one that
        parses, dry-runs, reports `delegate: opencode` — and then resolves to nothing,
        because resolution needs a project and a registry no test has. Five cards were
        in that state: `opencode`, `trae`, `kimi`, `aider` and `hermes` appear nowhere
        in `src/` or `docs/`, which is the *point* of the generic `vendor: cli` profile
        — but the card showed only the half a reader can copy.

        So the rule is about the card, not about the name: a delegate that is not a
        built-in has to be one the card tells you to define first — and the note has to
        name **that** profile. Merely mentioning `delegate_profiles` is not enough: the
        first cut accepted any note, so renaming a card's delegate while leaving its
        note behind still passed, which is the same drift one level down.
        """
        builtin = {provider.name for provider in providers.builtin_providers()}
        silent = []
        for card in self._cards():
            for token in self._DELEGATE.findall(card["cmd"]):
                # `--delegate` is split on the first colon: the left half names the
                # provider or profile, the right half is a per-run model. The note names
                # the profile, so the model must come off before the lookup — the builtin
                # branch already split it and this one did not.
                name = token.split(":", 1)[0]
                if name in builtin:
                    continue
                if f"delegate_profiles.{name}" in card["note"]:
                    continue
                silent.append(f"{card['id']} -> --delegate {token}")
        self.assertEqual(
            [],
            silent,
            "these cards name a delegate keel ships no profile for and never say so:\n"
            + "\n".join(silent),
        )

    def test_the_parser_sees_every_card(self):
        """A parser that quietly returns a subset makes every check above narrower than it
        reads. Counted against the ids, which are one per card by construction."""
        text = (SITE / "integrations.js").read_text(encoding="utf-8")
        ids = re.findall(r'\n      id: "([^"]+)",', text)

        self.assertEqual([card["id"] for card in self._cards()], ids)

    def test_a_delegate_carrying_a_model_still_finds_its_note(self):
        """`--delegate cursor:grok-4.6` names the profile `cursor`, and the note says
        `delegate_profiles.cursor`. Comparing the whole token flagged a correct card."""
        token = self._DELEGATE.findall("keel implement p 1 --delegate cursor:grok-4.6")[0]

        self.assertEqual(token, "cursor:grok-4.6")
        self.assertEqual(token.split(":", 1)[0], "cursor")

    def test_that_note_points_at_a_section_that_exists(self):
        """A note whose link is wrong is the same defect one level down."""
        anchors = {
            "#" + re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
            for heading in re.findall(
                r"^#{2,3} (.+)$", MODELS_DOC.read_text(encoding="utf-8"), re.M
            )
        }
        links = [
            fragment
            for card in self._cards()
            for fragment in re.findall(r"models\.md(#[a-z0-9-]+)", card["note"])
        ]

        self.assertTrue(links, "no card links into models.md any more")
        for fragment in set(links):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, anchors)

    def test_the_python_distribution_is_named_correctly(self):
        """`pipx install keel` installs an unrelated PyPI project.

        The bare `keel` name was taken, which is why this project publishes as
        `keel-workflow` (docs/keel/release.md). The card said `keel`.
        """
        text = (SITE / "integrations.js").read_text(encoding="utf-8")
        self.assertNotRegex(
            text,
            r"(?:pipx|pip) install keel(?![-\w])",
            "the PyPI distribution is `keel-workflow`; a bare `keel` installs someone else's",
        )


class TestTheDocumentedBlockingSlotsAreTheBlockingSlots(unittest.TestCase):
    """Every place that names the blocking slots names `may_block`'s (#1100).

    `on_fail: block` is the one frontmatter value that changes what a failing Lego piece
    does to the run, so its restriction is the sentence a piece author reads hardest —
    and a *documented* restriction the code does not have is the expensive direction to
    be wrong in: the reader builds a workaround for nothing. Prose cannot compute the
    set, so the comparison lives here, against the flag rather than against a list
    retyped in a test.
    """

    def texts(self):
        """Each claim's label, the text carrying it, and the pattern that finds it."""
        for label, pattern in _BLOCKING_CLAIMS:
            if label == "src/keel/extensions.py":
                # The *imported* docstring, which is what `help(keel.extensions)` prints.
                yield label, extensions.__doc__, pattern
            else:
                yield label, (REPO_ROOT / label).read_text(encoding="utf-8"), pattern

    def test_every_prose_restatement_names_them(self):
        expected = _blocking_slots()
        for label, text, pattern in self.texts():
            with self.subTest(claim=label):
                match = pattern.search(text)
                self.assertIsNotNone(
                    match, f"{label}: no `on_fail: block` restriction found to check"
                )
                self.assertEqual(
                    _named_slots(match.group(1)),
                    expected,
                    f"{label} names the wrong blocking slots",
                )

    def test_the_slot_table_agrees_with_the_flag(self):
        """extensions.md's `may block?` column, row by row, against `may_block`."""
        documented: set[str] = set()
        tabled: set[str] = set()
        for row in _SLOT_ROW.finditer(EXTENSIONS_DOC.read_text(encoding="utf-8")):
            slots = _named_slots(row["slots"])
            if not slots:  # the header and its `---` separator name no slot
                continue
            answer = row["blocks"].strip()
            if answer == "no":
                blocking: set[str] = set()
            elif answer == "yes":
                blocking = set(slots)
            else:
                blocking = _named_slots(answer)
            with self.subTest(slots=sorted(slots)):
                self.assertLessEqual(
                    blocking, slots, f"the `may block?` answer {answer!r} names another row's slot"
                )
            tabled |= slots
            documented |= blocking
        self.assertEqual(tabled, set(model.SLOTS), "the slot table is missing a backbone slot")
        self.assertEqual(documented, _blocking_slots())


class TheStatedIntegrationCountIsTheNumberOfCards(unittest.TestCase):
    """The catalogue says how many integrations it has, in nine places (#1131).

    Nothing checked them. Removing the AWS Bedrock and Azure OpenAI cards — which
    named no keel code path and no documentation — left the header comment, the
    sidebar badge, the search placeholder and the status line all still saying
    32, and the whole suite stayed green.

    The first cut of this class matched only the three counts that edit touched,
    which is the same mistake one level up: the gate review then found the tab
    bar reading ``All 32`` directly above ``Showing 30 integrations``, and a
    ``LLM Backends 8`` pill that filters to six cards. So nothing here is a list
    of the places that were wrong — every count is *derived* from the catalogue,
    and every count the page states is found by its shape and checked.
    """

    _CARD = re.compile(
        r'\{\s*id: "(?P<id>[^"]+)",\s*\n\s*name: "[^"]*",\s*\n\s*category: "(?P<cat>[^"]+)"'
    )
    _SECTION = re.compile(r"// --- \d+\. (?P<title>.+?) \((?P<count>\d+)\) ---")
    _PILL = re.compile(
        r'data-cat="(?P<cat>[a-z]+)"[^>]*>[^<]*'
        r'<span class="integ-count-badge">(?P<count>\d+)</span>'
    )

    def _catalogue(self) -> str:
        return (SITE / "integrations.js").read_text(encoding="utf-8")

    def _cards(self) -> list[tuple[str, str]]:
        found = [(m.group("id"), m.group("cat")) for m in self._CARD.finditer(self._catalogue())]
        self.assertTrue(found, "no cards parsed — the catalogue's shape changed")
        return found

    def _by_category(self) -> collections.Counter:
        return collections.Counter(cat for _, cat in self._cards())

    def test_the_header_comment_counts_the_cards(self):
        stated = re.search(r"Interactive catalog of (\d+) AI coding agents", self._catalogue())

        self.assertIsNotNone(stated, "the header no longer states a count")
        self.assertEqual(int(stated.group(1)), len(self._cards()))

    def test_each_section_comment_counts_its_own_cards(self):
        """``// --- 2. Supported LLM Models & Backends (8) ---`` above six cards."""
        source = self._catalogue()
        sections = list(self._SECTION.finditer(source))
        self.assertEqual(len(sections), len(self._by_category()), "a section comment is missing")

        for i, section in enumerate(sections):
            end = sections[i + 1].start() if i + 1 < len(sections) else len(source)
            block = source[section.start() : end]
            with self.subTest(section=section.group("title")):
                self.assertEqual(int(section.group("count")), len(self._CARD.findall(block)))

    def test_every_filter_pill_states_the_size_of_what_it_filters_to(self):
        """The badge on a pill is what the reader sees before clicking it, and the
        row of cards is what they see after. The gate found those disagreeing."""
        markup = (SITE / "index.html").read_text(encoding="utf-8")
        counts = self._by_category()
        pills = {m.group("cat"): int(m.group("count")) for m in self._PILL.finditer(markup)}

        self.assertEqual(
            set(pills) - {"all"}, set(counts), "a category has no pill, or a pill no cards"
        )
        for cat, stated in pills.items():
            with self.subTest(category=cat):
                self.assertEqual(stated, sum(counts.values()) if cat == "all" else counts[cat])

    def test_the_prose_above_the_pills_counts_the_same_cards(self):
        markup = (SITE / "index.html").read_text(encoding="utf-8")
        counts = self._by_category()

        for pattern, category in (
            (r"(\d+) AI assistants", "assistants"),
            (r"(\d+) LLM backends", "backends"),
            (r"(\d+) engineering skill libraries", "skills"),
        ):
            with self.subTest(category=category):
                found = re.search(pattern, markup)
                self.assertIsNotNone(found, f"no count matched {pattern}")
                self.assertEqual(int(found.group(1)), counts[category])

    def test_the_status_line_states_the_whole_catalogue(self):
        markup = (SITE / "index.html").read_text(encoding="utf-8")
        found = re.search(r">Showing (\d+) integrations<", markup)

        self.assertIsNotNone(found, "the status line no longer states a count")
        self.assertEqual(int(found.group(1)), len(self._cards()))

    def test_no_rounded_claim_promises_more_than_the_catalogue_holds(self):
        """``30+`` is a floor, not an equality — so it is checked as a floor."""
        markup = (SITE / "index.html").read_text(encoding="utf-8")
        cards = len(self._cards())
        claims = [
            (p, re.search(p, markup))
            for p in (
                r'Integrations <span class="badge">(\d+)\+</span>',
                r'placeholder="Search (\d+)\+ integrations',
            )
        ]

        for pattern, found in claims:
            with self.subTest(pattern=pattern):
                self.assertIsNotNone(found, f"no count matched {pattern}")
                self.assertLessEqual(int(found.group(1)), cards)

    def test_the_removed_backends_are_gone(self):
        """#1131: neither had a code path, docs, or a way to verify the claim."""
        ids = [card for card, _ in self._cards()]

        self.assertNotIn("aws-bedrock", ids)
        self.assertNotIn("azure-openai", ids)


def _public_pages() -> dict[str, str]:
    """The prose a visitor reads: README, SECURITY, AGENTS, `docs/keel/`, and the site.

    Not the CHANGELOG, which quotes old wording to say what changed, and not
    `docs/security/`, whose reports are records of their day.
    """
    paths = [REPO_ROOT / "README.md", REPO_ROOT / "SECURITY.md", REPO_ROOT / "AGENTS.md"]
    paths += sorted((REPO_ROOT / "docs" / "keel").glob("*.md"))
    paths += sorted(p for p in SITE.glob("*") if p.suffix in {".html", ".js", ".txt"})
    return {
        p.relative_to(REPO_ROOT).as_posix(): " ".join(p.read_text(encoding="utf-8").split())
        for p in paths
    }


def _readme_first_screen() -> str:
    """The README down to its first `##` heading — what a visitor sees before scrolling."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    return readme[: readme.index("\n## ")]


class TestLaunchCopyClaimsOnlyWhatKeelDoes(unittest.TestCase):
    """The pre-launch audit of 2026-09-23 found public copy claiming more than keel does.

    Each test pins one claim to the fact it rests on, and fails on the wording it
    replaced.
    """

    def test_the_readme_first_screen_cites_no_statistic_and_promises_no_production(self):
        """#1318: an unsourced "over 70%… ~11-15%" and "lands safely in production".

        keel merges pull requests; it does not deploy. `100%` is keel's own coverage.
        """
        screen = _readme_first_screen()
        percentages = set(re.findall(r"\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?\s*%", screen))
        self.assertLessEqual(percentages, {"100%"}, "a statistic on the first screen")
        self.assertNotIn("production", screen.lower())

    def test_the_site_does_not_say_keel_delivers_to_production(self):
        """#1318, the site's copy of the same claim ("from backlog to production")."""
        for page in ("content.js", "index.html", "llms.txt"):
            with self.subTest(page=page):
                text = (SITE / page).read_text(encoding="utf-8").lower()
                self.assertNotIn("to production", text)
                self.assertNotIn("vision-to-production", text)

    def test_nothing_calls_the_evidence_chain_tamper_evident(self):
        """#1327: nothing is signed or hash-chained; the evidence is SHA-bound and auditable."""
        for where, text in _public_pages().items():
            with self.subTest(page=where):
                self.assertIsNone(
                    re.search(r"tamper[- ](evident|proof)", text, re.I),
                    "say 'commit-SHA-bound and auditable' — nothing here is signed",
                )

    def test_no_agent_session_wording_is_published(self):
        """#1323: sentences from inside a verification session leaked into the docs.

        Whitespace is collapsed first: install.md broke "from a CLI" and "session"
        across two lines.
        """
        leaked = re.compile(
            r"this session (did|could|was|has)|from a CLI session|did not establish", re.I
        )
        for where, text in _public_pages().items():
            with self.subTest(page=where):
                found = leaked.search(text)
                self.assertIsNone(found, found and found.group(0))

    def test_the_architecture_proposal_says_it_is_historical(self):
        """#1331: a June proposal, still naming `ai-infra` and `/ship`, linked as the design."""
        head = (REPO_ROOT / "docs" / "proposals" / "keel-architecture.md").read_text(
            encoding="utf-8"
        )[:1500]
        self.assertIn("Historical design proposal", head)
        self.assertIn("](../keel/)", head, "the banner must point at the current docs")

    def test_every_link_to_the_architecture_proposal_calls_it_historical(self):
        """#1331: README, AGENTS.md and the entry points introduced it as "full design"."""
        for name in ("README.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            paragraphs = [
                p for p in re.split(r"\n\s*\n|\n(?=- )", text) if "keel-architecture.md" in p
            ]
            with self.subTest(page=name):
                self.assertTrue(paragraphs, f"{name} no longer links the proposal")
                for paragraph in paragraphs:
                    self.assertIn("historical", paragraph, paragraph)
                    self.assertNotIn("full design", paragraph, paragraph)


class TestSecurityPageListsWhatKeelActuallyDoes(unittest.TestCase):
    """SECURITY.md and the site's Security view, held to `docs/security/` and the code."""

    @classmethod
    def setUpClass(cls):
        cls.security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        cls.index = (SITE / "index.html").read_text(encoding="utf-8")
        cls.content = (SITE / "content.js").read_text(encoding="utf-8")
        cls.reports = sorted((REPO_ROOT / "docs" / "security").glob("*-security-audit.md"))

    def _section(self, text: str, heading: str) -> str:
        start = text.index(heading)
        end = text.find("\n## ", start + 1)
        return text[start:] if end == -1 else text[start:end]

    def test_every_published_audit_report_is_listed_everywhere(self):
        """#1325: SECURITY.md and the site listed three of the five reports."""
        self.assertGreaterEqual(len(self.reports), 5)
        audits = self._section(self.security, "## Security Audits")
        view = self.index[self.index.index('id="view-security"') :]
        view = view[: view.index("</section>")]
        docs = self.content[self.content.index('slug: "security-audits"') :]
        docs = docs[: docs.index("source:")]
        for report in self.reports:
            date = report.name[: len("YYYY-MM-DD")]
            with self.subTest(report=report.name):
                self.assertIn(f"docs/security/{report.name}", audits)
                self.assertIn(f"<code>{date}</code>", view)
                self.assertIn(f"<h3>{date}", docs)

    def test_each_listed_report_names_what_produced_it_when_the_report_does(self):
        """#1325: three reports were written by AI models; the lists must say which."""
        audits = self._section(self.security, "## Security Audits")
        named = 0
        for report in self.reports:
            # "Conducted by Claude (Opus 4.8, `claude-opus-4-8`) acting as …" → "Opus 4.8".
            line = re.search(
                r"Conducted by [^(]+\(([^,)]+)[^)]*\) acting as", report.read_text(encoding="utf-8")
            )
            if line is None:
                continue
            producer = line.group(1)
            named += 1
            with self.subTest(report=report.name):
                self.assertIn(report.name, audits, "the report is not listed at all")
                entry = audits[audits.index(report.name) :].split("\n- ")[0]
                self.assertIn(producer, entry)
        self.assertGreaterEqual(named, 3, "the producer pattern no longer matches the reports")

    def test_the_ai_written_zero_findings_report_is_not_a_headline(self):
        """#1325: the site led with the swarm audit's "Zero critical…" in bold."""
        self.assertNotIn("Zero critical", self.content)
        self.assertNotIn("Zero critical", self.index)

    def test_the_outbound_list_names_every_hosted_api_host(self):
        """#1322: SECURITY.md said the core makes no network requests, and left out the
        hosted-API delegates that send the brief and the diff to a vendor."""
        from urllib.parse import urlsplit

        from keel import api_delegate

        hosts = {urlsplit(url).hostname for url, _key in api_delegate._VENDORS.values()}
        self.assertEqual(len(hosts), 3)
        self.assertIn("## Outbound network calls", self.security)
        outbound = self._section(self.security, "## Outbound network calls")
        for host in sorted(hosts):
            with self.subTest(host=host):
                self.assertIn(f"`{host}`", outbound)
        for vendor in sorted(api_delegate._VENDORS):
            with self.subTest(vendor=vendor):
                self.assertIn(f"<code>{vendor}:</code>", self.index)
        self.assertNotIn("makes no network requests", self.security)
        self.assertNotIn("makes <strong>no network calls</strong>", self.index)

    def test_the_outbound_list_names_the_jury_gate(self):
        """Round-1 review of #1322: a listed `jury` gate hands the diff to ai-jury, which
        sends it to its own reviewer providers — an outbound path the list left out."""
        self.assertIn("## Outbound network calls", self.security)
        outbound = self._section(self.security, "## Outbound network calls")
        self.assertIn("**The `jury` gate**", outbound)
        self.assertIn("reviewer", outbound[outbound.index("**The `jury` gate**") :])
        view = self.index[self.index.index('id="view-security"') :]
        self.assertIn("the <code>jury</code> gate", view[: view.index("</section>")])

    def test_the_august_report_is_not_called_swarm_only(self):
        """Round-1 review of #1325: the 2026-08-15 report's own scope re-checks
        redaction, the remote-endpoint gate, ReDoS and the merge lock."""
        for where, text in (
            ("SECURITY.md", self.security),
            ("index.html", self.index),
            ("content.js", self.content),
        ):
            with self.subTest(page=where):
                self.assertNotIn("swarm subsystem only", text)
                self.assertIn("re-check of core invariants", text)

    def test_no_telemetry_stays_said(self):
        """The owner's call on #1322: the CLI sends no telemetry, and both pages say so."""
        self.assertIn("no telemetry", self.security.lower())
        self.assertIn("no telemetry", self.index.lower())


class TestSwarmCopyOnTheSiteIsNotAFlagship(unittest.TestCase):
    """#1324: below its own "Experimental" warning the site stated unfinished swarm
    behaviour as fact, listed `/keel:swarm` beside `/keel:ship` as a flagship, and the
    hero line led with "multi-agent swarms"."""

    def test_swarm_is_not_a_flagship_command(self):
        content = (SITE / "content.js").read_text(encoding="utf-8")
        entry = re.search(r'\{\s*slug: "swarm",[^\n]*', content)
        self.assertIsNotNone(entry)
        self.assertNotIn("flagship: true", entry.group(0))
        self.assertNotIn('group: "Flagship"', entry.group(0))

    def test_every_hero_line_that_names_swarms_says_experimental(self):
        heroes = sorted((REPO_ROOT / "docs" / "assets").glob("hero*.svg"))
        heroes += sorted((SITE / "assets").glob("hero-*.svg"))
        self.assertGreaterEqual(len(heroes), 4)
        for hero in heroes:
            texts = re.findall(r"<t(?:ext|span)\b[^>]*>([^<]*)<", hero.read_text(encoding="utf-8"))
            for text in texts:
                if "swarm" in text.lower():
                    with self.subTest(hero=hero.name, text=text):
                        self.assertIn("experimental", text.lower())

    def test_the_swarm_view_states_no_unbuilt_guarantee(self):
        """#1278 (worktree lifecycle gaps, and a dry run has no worktrees) and #1287
        (landing never pushes) are open; these three sentences asserted the opposite."""
        index = (SITE / "index.html").read_text(encoding="utf-8")
        view = index[index.index('id="view-swarm"') :]
        view = view[: view.index("</section>")]
        for claim in ("Workers never collide", "No branch lands until", "lands batches safely"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, view)


#: Every place that describes what `swarm-land` does to the base branch, as
#: ``(path, start marker, end marker)``: the excerpt runs from the start marker to
#: the first end marker after it.
_SWARM_LANDING_SURFACES = (
    ("README.md", "`keel swarm-land --live`", "`keel worktree-remove`"),
    ("docs/keel/swarm.md", "> Landing is guarded", "> The rest is tracked"),
    ("docs/keel/swarm.md", "### What landing actually does", "### Review Evidence Gate"),
    ("docs/keel/cli.md", "## `keel swarm-land ", "## `keel-visual swarm"),
    ("docs/keel/overview.md", "**High-concurrency Swarm orchestration**", "Audit epic"),
    ("docs/keel/commands.md", "`swarm-land` checks", "**Design, not built:**"),
    ("docs/keel/comparison.md", "**Single-Writer Batch Landing**", "\n"),
    ("docs/keel/github-actions.md", "There is no `swarm` subcommand", "## Adopting"),
    ("src/keel/adapters/commands/swarm.md", "## Step 3", "## Step 4"),
    ("website/content.js", '["keel swarm-land', '"],'),
    ("website/content.js", "Landing is guarded", "</p>"),
    ("website/index.html", "<b>Single-Writer Batch Landing</b>", "</span>"),
    ("website/index.html", "<code>keel swarm-land", "</div>"),
)

#: The statement each surface has to make: the landing goes through `keel merge`...
_SAYS_KEEL_MERGE = re.compile(r"\bkeel merge\b")
#: ...and what it merges is a pull request.
_SAYS_PULL_REQUEST = re.compile(r"\bpull requests?\b|\bPRs?\b")
#: The wording of the local landing #1287 removed. Each alternative describes the local path
#: as the present behaviour; a sentence that negates a local merge ("merges nothing locally")
#: matches none of them.
_CLAIMS_LOCAL_LANDING = re.compile(
    r"(?i)merge --no-ff"
    r"|\blocal (?:copy of the )?base branch\b"
    r"|\blocal merge\b"
    r"|\bmerged locally\b"
    r"|\bpushes nothing\b"
    r"|\bnothing is pushed\b"
    r"|\bpushing is the operator's step\b"
)


def _swarm_landing_excerpt(path: str, start: str, end: str) -> str:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    begin = text.index(start)
    return " ".join(text[begin : text.index(end, begin + len(start))].split())


def _local_landing_claims(text: str) -> list[str]:
    """Every phrase of ``text`` that describes the removed local landing as current."""
    return [m.group(0) for m in _CLAIMS_LOCAL_LANDING.finditer(re.sub(r"<[^>]+>", "", text))]


class TestSwarmLandingMergesPullRequestsThroughKeelMerge(unittest.TestCase):
    """#1287: `swarm-land` lands each cluster's pull request through `keel merge`.

    The first slice (#1396) made every surface say the landing was a local
    `git merge --no-ff` that pushed nothing, because it was. The owner's decision
    replaced that path with `keel merge`'s own: the pull request is merged on GitHub
    under the window, the lock and the evidence gate. So every surface that describes
    the landing now has to name `keel merge` and the pull request, and none may still
    describe the local merge as what happens.
    """

    def test_the_landing_code_runs_keel_merge_and_no_local_merge(self):
        """The fact the docs rest on: when this fails, the docs below need rewriting."""
        landing = inspect.getsource(swarm_landing)
        for local in ('"--no-ff"', '"rebase"', '"merge", "--abort"'):
            with self.subTest(local=local):
                self.assertNotIn(local, landing)
        merge = inspect.getsource(cli._swarm_land_merge)  # noqa: SLF001
        self.assertIn("build_parser().parse_args(argv)", merge)
        self.assertIn("_cmd_merge(merge_args)", merge)
        self.assertIn('"merge",', merge)

    def test_every_landing_surface_names_keel_merge_and_the_pull_request(self):
        for path, start, end in _SWARM_LANDING_SURFACES:
            with self.subTest(path=path, start=start):
                excerpt = _swarm_landing_excerpt(path, start, end)
                self.assertRegex(excerpt, _SAYS_KEEL_MERGE)
                self.assertRegex(excerpt, _SAYS_PULL_REQUEST)

    def test_no_landing_surface_still_describes_the_local_merge(self):
        for path, start, end in _SWARM_LANDING_SURFACES:
            with self.subTest(path=path, start=start):
                self.assertEqual(
                    [], _local_landing_claims(_swarm_landing_excerpt(path, start, end))
                )

    def test_the_guide_banner_says_landing_goes_through_keel_merge(self):
        banner = _swarm_landing_excerpt("docs/keel/swarm.md", "> Landing is guarded", "> The rest")
        self.assertIn("Landing is guarded, and it goes through **`keel merge`**", banner)
        self.assertIn("`knobs.swarm_review_evidence: false` > no longer skips anything", banner)

    def test_the_claim_detector_catches_a_local_landing_claim(self):
        """The negative check is only worth something if it fires on the wording it bans."""
        for claim in (
            "swarm-land merges each branch with `git merge --no-ff`.",
            "It merges into the local base branch.",
            "The landing is a local merge only.",
            "A cluster reported merged is merged locally.",
            "It pushes nothing.",
            "Pushing is the operator's step.",
        ):
            with self.subTest(claim=claim):
                self.assertTrue(_local_landing_claims(claim), claim)
        for current in (
            "swarm-land checks out, rebases and merges nothing locally.",
            "Each cluster's pull request is merged through `keel merge`.",
        ):
            with self.subTest(current=current):
                self.assertEqual([], _local_landing_claims(current))


class TestTheDogfoodTerminalIsTheDryAssessment(unittest.TestCase):
    """#1335 follow-up: the site's "keel ships itself" section introduces the dry
    assessment, and its terminal ran `keel ship --issue 142` to "MERGED — issue #142
    closed". A dry assessment decides MERGE or BLOCK and changes nothing."""

    @classmethod
    def setUpClass(cls):
        scenes = (SITE / "scenes.js").read_text(encoding="utf-8")
        start = scenes.index("function termFor(")
        cls.reveal = scenes[start : scenes.index("function buildReveal(", start)]
        cls.render = scenes[scenes.index("function buildReveal(") :]
        index = (SITE / "index.html").read_text(encoding="utf-8")
        view = index[index.index('id="view-ship"') :]
        cls.view = view[: view.index("</section>")]

    def test_every_terminal_command_is_a_dry_run(self):
        # The whole `<span class="cmd">…</span>` source, across the JS concatenation.
        commands = re.findall(r'class="cmd">\$ keel ship.*?</span>', self.reveal)
        commands += re.findall(r'class="term-title">([^<]*)<', self.view)
        # render() overwrites that static title at runtime, so it is a command too.
        runtime_titles = re.findall(r'title\.textContent = ("[^;]*);', self.render)
        self.assertTrue(runtime_titles, "render() no longer sets the terminal title")
        commands += runtime_titles
        self.assertTrue(commands)
        for command in commands:
            with self.subTest(command=command):
                self.assertIn("--dry-run", command)

    def test_nothing_in_the_section_says_merged(self):
        for where, text in (
            ("scenes.js reveal", self.reveal + self.render),
            ("index.html", self.view),
        ):
            with self.subTest(where=where):
                self.assertNotIn("MERGED", text)
                self.assertNotIn("merged + closed", text)


def _prose(name: str, text: str) -> str:
    """A page's words, whitespace collapsed; a site page's tags are stripped first.

    Only the site's: a Markdown page's `<N>` or `<report.json>` would pair with a later
    `>` and swallow the sentences between them, which is how a check goes vacuous.
    """
    if name.startswith("website/"):
        text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _readme_sections() -> dict[str, str]:
    """The README's `##` sections by title, each running to the next `##` heading."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    parts = re.split(r"^## +(.+?)\s*$", readme, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _readme_heading_line(title: str) -> int | None:
    """The 1-based line of the README heading `## <title>`, or None when there is none."""
    lines = (REPO_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    return next((n for n, line in enumerate(lines, 1) if line == f"## {title}"), None)


#: The AST nodes that carry a docstring besides the module itself.
_DOCUMENTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

#: `gh <noun> <verb>` pairs that change something on GitHub. `gh api` is judged by its
#: `-X` method instead, so a REST merge or comment is caught however it is spelled.
_GH_WRITES = frozenset(
    {
        ("pr", "merge"),
        ("pr", "create"),
        ("pr", "comment"),
        ("pr", "close"),
        ("pr", "edit"),
        ("issue", "close"),
        ("issue", "comment"),
        ("issue", "edit"),
        ("label", "create"),
        ("label", "edit"),
        ("label", "delete"),
        ("release", "create"),
    }
)
#: `git <verb>`s that write refs, objects, the index or the work tree. `worktree` and
#: `hash-object` are judged by their next argument, since `worktree list` and a bare
#: `hash-object` only read.
_GIT_WRITES = frozenset(
    {
        "push",
        "commit",
        "commit-tree",
        "mktree",
        "fetch",
        "revert",
        "merge",
        "rebase",
        "checkout",
        "add",
        "update-ref",
        "reset",
        "tag",
        "branch",
    }
)


#: `gh api` options that send a request body. With one and no `-X`, gh sends a POST.
_GH_BODY_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})

_MUTATION = re.compile(r"\bmutation\b")


def _strings_in(node: ast.AST, consts: dict[str, str]) -> list[str]:
    """The string literals ``node`` carries, with module-level constants it names resolved."""
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
        elif isinstance(sub, ast.Name) and sub.id in consts:
            out.append(consts[sub.id])
    return out


def _argv_writes(node: ast.AST, consts: dict[str, str] | None = None) -> bool:
    """Is ``node`` a literal ``["git"|"gh", …]`` argv whose command writes?

    ``gh api`` writes with a non-GET ``-X``, or with a body flag and no ``-X`` (gh's
    default is then POST) — except ``gh api graphql``, which always POSTs, and writes only
    when its query is a ``mutation``. ``consts`` resolves a query held in a module-level
    string constant.
    """
    if not isinstance(node, ast.List) or not node.elts:
        return False
    toks = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
    if toks[0] == "gh":
        if toks[1:3] == ["api", "graphql"]:
            return any(_MUTATION.search(s) for s in _strings_in(node, consts or {}))
        if "-X" in toks:
            method = toks[toks.index("-X") + 1 :][:1]
            return method not in ([], [None], ["GET"])
        if toks[1:2] == ["api"]:
            return bool(_GH_BODY_FLAGS.intersection(toks))
        return tuple(toks[1:3]) in _GH_WRITES
    if toks[0] != "git":
        return False
    rest = toks[1:]
    while rest[:1] == ["-c"]:
        rest = rest[2:]
    if rest[:1] == ["worktree"]:
        return rest[1:2] in (["add"], ["remove"], ["prune"])
    if rest[:1] == ["hash-object"]:
        return "-w" in rest
    return bool(rest) and rest[0] in _GIT_WRITES


def _refused_calls(unit: ast.AST) -> set[int]:
    """The ``id()`` of each callee a flag refusal keeps in its dry run.

    A refusal is a top-level ``if args.<flag>: … return <nonzero>`` in a function's body.
    A later call that passes ``dry_run=not args.<flag>`` can then only run dry, so its
    callee is not counted as a write: `swarm-run --live` is refused before
    `run_swarm_orchestration(…, dry_run=not args.live)`, whose worktrees are
    ``not dry_run`` only (#1367 review). It relies on the callee honouring ``dry_run``,
    which is that keyword's contract.
    """
    if not isinstance(unit, ast.FunctionDef | ast.AsyncFunctionDef):
        return set()
    refused: dict[str, int] = {}
    for stmt in unit.body:
        test = stmt.test if isinstance(stmt, ast.If) else None
        last = stmt.body[-1] if isinstance(stmt, ast.If) else None
        if (
            isinstance(test, ast.Attribute)
            and isinstance(test.value, ast.Name)
            and test.value.id == "args"
            and isinstance(last, ast.Return)
            and isinstance(last.value, ast.Constant)
            and last.value.value not in (0, None, False)
        ):
            refused.setdefault(test.attr, stmt.lineno)
    skipped: set[int] = set()
    for call in ast.walk(unit):
        if not isinstance(call, ast.Call):
            continue
        for kw in call.keywords:
            value = kw.value
            if (
                kw.arg == "dry_run"
                and isinstance(value, ast.UnaryOp)
                and isinstance(value.op, ast.Not)
                and isinstance(value.operand, ast.Attribute)
                and isinstance(value.operand.value, ast.Name)
                and value.operand.value.id == "args"
                and refused.get(value.operand.attr, call.lineno) < call.lineno
            ):
                skipped.add(id(call.func))
    return skipped


def _write_graph(sources: dict[str, str]) -> tuple[dict[str, set[str]], set[str]]:
    """A reference graph over ``sources`` (module name → source), and its writing nodes.

    Nodes are top-level functions (``m.f``), classes (``m.C``, which reach their methods),
    methods (``m.C.f``) and module-level assignments (``m.TABLE``), so a dispatch table
    reaches the handlers it holds. Edges are every reference a node makes: ``mod.f``
    attributes — through ``from . import mod as alias`` — and bare names, which resolve
    to the node's own module or to a ``from .mod import f [as g]``. Inside a class,
    ``self.f`` / ``cls.f`` resolve to that class's method. A callee a flag refusal pins
    to its dry run (:func:`_refused_calls`) is not an edge.
    """
    graph: dict[str, set[str]] = {}
    writers: set[str] = set()
    for mod, source in sources.items():
        tree = ast.parse(source)
        consts = {
            t.id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        modules: dict[str, str] = {}  # alias -> module
        names: dict[str, str] = {}  # bare name -> "module.name"
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if node.module is None:
                        modules[bound] = alias.name
                    else:
                        names[bound] = f"{node.module}.{alias.name}"
        units: list[tuple[str, ast.AST, str | None]] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                units.append((node.name, node, None))
            elif isinstance(node, ast.ClassDef):
                methods = [
                    m for m in node.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
                ]
                graph[f"{mod}.{node.name}"] = {f"{mod}.{node.name}.{m.name}" for m in methods}
                units += [(f"{node.name}.{m.name}", m, node.name) for m in methods]
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                units += [(t.id, node, None) for t in targets if isinstance(t, ast.Name)]
        local = {name for name, _, owner in units if owner is None} | {
            n.name for n in tree.body if isinstance(n, ast.ClassDef)
        }
        for name, unit, owner in units:
            qualified, refs = f"{mod}.{name}", set()
            refused = _refused_calls(unit)
            for node in ast.walk(unit):
                if _argv_writes(node, consts):
                    writers.add(qualified)
                if id(node) in refused:
                    continue
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    base = node.value.id
                    if owner and base in ("self", "cls"):
                        refs.add(f"{mod}.{owner}.{node.attr}")
                    else:
                        refs.add(f"{modules.get(base, base)}.{node.attr}")
                elif isinstance(node, ast.Name) and node.id != name:
                    if node.id in local:
                        refs.add(f"{mod}.{node.id}")
                    elif node.id in names:
                        refs.add(names[node.id])
            graph[qualified] = refs
    return graph, writers


def _reaches(graph: dict[str, set[str]], writers: set[str], start: str) -> bool:
    seen: set[str] = set()
    todo = [start]
    while todo:
        name = todo.pop()
        if name in writers:
            return True
        if name not in seen:
            seen.add(name)
            todo.extend(graph.get(name, ()))
    return False


def _cli_write_commands() -> dict[str, str]:
    """Every `keel` subcommand that can reach a git or GitHub write, read from the code.

    :func:`_write_graph` over `src/keel/`; a subcommand writes when its
    ``set_defaults(func=…)`` handler reaches a writing node. It over-approximates — a
    dry-run arm counts — which is the right direction for a list that says what the CLI
    *may* write: the README says what turns each write on or off.
    """
    sources = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "src" / "keel").glob("*.py"))
    }
    graph, writers = _write_graph(sources)
    out: dict[str, str] = {}

    def walk(parser, prefix: str) -> None:
        for action in parser._subparsers._group_actions if parser._subparsers else ():  # noqa: SLF001
            for sub_name, sub in action.choices.items():
                func = sub.get_default("func")
                if func is None:
                    walk(sub, f"{prefix}{sub_name} ")
                    continue
                handler = f"{func.__module__.rsplit('.', 1)[-1]}.{func.__name__}"
                if _reaches(graph, writers, handler):
                    out[f"{prefix}{sub_name}"] = handler

    walk(cli.build_parser(), "")
    return out


class TestTheWriteGraphFindsHiddenWrites(unittest.TestCase):
    """The README's write list is only as complete as :func:`_write_graph` (#1367 review).

    Each case hides one write behind one resolution rule, in synthetic sources.
    """

    PUSH = "def push():\n    return ['git', 'push', 'origin', 'x']\n"

    def reaches(self, sources: dict[str, str], start: str) -> bool:
        return _reaches(*_write_graph(sources), start)

    def test_a_direct_argv(self):
        self.assertTrue(self.reaches({"w": self.PUSH}, "w.push"))
        self.assertFalse(self.reaches({"w": "def f():\n    return ['git', 'status']\n"}, "w.f"))

    def test_an_aliased_module_import(self):
        caller = "from . import w as ww\n\ndef h():\n    ww.push()\n"
        self.assertTrue(self.reaches({"w": self.PUSH, "c": caller}, "c.h"))

    def test_a_function_imported_by_name(self):
        caller = "from .w import push as p\n\ndef h():\n    p()\n"
        self.assertTrue(self.reaches({"w": self.PUSH, "c": caller}, "c.h"))

    def test_gh_api_with_a_body_and_no_method_is_a_post(self):
        post = "def f():\n    return ['gh', 'api', 'repos/o/r/labels', '-f', 'name=x']\n"
        get = "def f():\n    return ['gh', 'api', '-X', 'GET', 'repos/o/r', '-f', 'q=1']\n"
        bare = "def f():\n    return ['gh', 'api', 'repos/o/r/labels']\n"
        self.assertTrue(self.reaches({"w": post}, "w.f"))
        self.assertFalse(self.reaches({"w": get}, "w.f"))
        self.assertFalse(self.reaches({"w": bare}, "w.f"))

    def test_a_graphql_mutation_and_not_a_query(self):
        shape = "Q = {!r}\n\ndef f():\n    return ['gh', 'api', 'graphql', '-f', f'query={{Q}}']\n"
        mutation = shape.format("mutation { addComment(input: {}) { clientMutationId } }")
        self.assertTrue(self.reaches({"w": mutation}, "w.f"))
        self.assertFalse(self.reaches({"w": shape.format("query { viewer { login } }")}, "w.f"))

    def test_a_class_method(self):
        source = (
            "class K:\n"
            "    def run(self):\n        return self._w()\n"
            "    def _w(self):\n        return ['git', 'push']\n\n"
            "def h():\n    return K().run()\n"
        )
        self.assertTrue(self.reaches({"w": source}, "w.h"), "through the class")
        self.assertTrue(self.reaches({"w": source}, "w.K.run"), "through self._w")

    def test_a_module_level_dispatch_table(self):
        source = self.PUSH + "\nTABLE = {'x': push}\n\ndef h(key):\n    return TABLE[key]()\n"
        self.assertTrue(self.reaches({"w": source}, "w.h"))

    def test_a_call_a_refusal_keeps_dry_is_not_a_write(self):
        """`swarm-run --live` is refused before its only call, which passes
        ``dry_run=not args.live``: the call can only run dry (#1367 review)."""
        worker = "def run(*, dry_run=True):\n    if not dry_run:\n        return ['git', 'push']\n"
        refused = (
            "from . import w\n\ndef h(args):\n    if args.live:\n        return 1\n"
            "    return w.run(dry_run=not args.live)\n"
        )
        after = (
            "from . import w\n\ndef h(args):\n    w.run(dry_run=not args.live)\n"
            "    if args.live:\n        return 1\n"
        )
        unguarded = "from . import w\n\ndef h(args):\n    return w.run(dry_run=not args.live)\n"
        self.assertFalse(self.reaches({"w": worker, "c": refused}, "c.h"))
        self.assertTrue(self.reaches({"w": worker, "c": after}, "c.h"), "refused too late")
        self.assertTrue(self.reaches({"w": worker, "c": unguarded}, "c.h"))

    def test_the_cli_writes_from_exactly_twelve_commands(self):
        """The hardening found nothing new. `ship` and `run-gates` joined with the opt-in
        `revert-check` gate, whose scratch worktree is added, reset and removed through git
        (#1289). `swarm-run` joined when `--live` stopped being refused: a live worker
        commits, pushes and opens a pull request under delegated consent (#1400)."""
        self.assertEqual(
            {
                "ship",
                "run-gates",
                "merge",
                "capture-land",
                "post-comment",
                "review",
                "doctor",
                "swarm-land",
                "swarm-run",
                "worktree-remove",
                "rollback",
                "canary",
            },
            set(_cli_write_commands()),
        )


class TestTheReadmeFirstScreenDoesItsJob(unittest.TestCase):
    """#1330, #1321, #1337, #1329: the README buried Install at line 199.

    A newcomer met a metaphor, a statistics paragraph and a hundred lines of feature
    bullets first, and the Quickstart ended at `keel version` without saying that the agent
    host, not the CLI, does the work. These pin the order and the claims the new first
    screen makes, not its wording.
    """

    #: The launch order, top to bottom. Each title is a `##` heading.
    _ORDER = (
        "Built for long unattended runs",
        "Install",
        "Quickstart",
        "Requirements, cost and limits",
        "What you get",
    )

    def section(self, title: str) -> str:
        """A README section, or an assertion failure naming it — never a KeyError."""
        sections = _readme_sections()
        self.assertTrue(title in sections, f"the README has no `## {title}` section")
        return sections[title]

    def test_install_is_on_the_first_screen(self):
        line = _readme_heading_line("Install")
        self.assertIsNotNone(line, "the README has no `## Install` heading")
        self.assertLessEqual(line, 60, f"`## Install` is at line {line}")

    def test_the_sections_come_in_launch_order(self):
        lines = [_readme_heading_line(title) for title in self._ORDER]
        self.assertNotIn(None, lines, dict(zip(self._ORDER, lines, strict=True)))
        self.assertEqual(sorted(lines), lines, dict(zip(self._ORDER, lines, strict=True)))

    def test_the_first_screen_says_who_does_the_work(self):
        """#1321: the agent runs /keel:ship, the CLI enforces, `keel merge` merges.

        Round 1 of #1360: "the CLI itself never commits, pushes or opens a pull request"
        was false — `keel capture-land --write` builds a commit and pushes it onto the PR
        branch, and `keel merge` merges. The claim is narrowed to `keel ship`, and the
        CLI's own writes are named.
        """
        screen = " ".join(_readme_first_screen().split())
        for claim in (
            "/keel:ship",
            "`keel ship` is a dry assessment",
            "the agent commits, pushes and opens the pull request",
            "`keel capture-land`",
            "only through `keel merge`",
        ):
            with self.subTest(claim=claim):
                self.assertTrue(claim in screen, f"the first screen never says {claim!r}")
        self.assertIsNone(re.search(r"CLI itself never commits", screen))

    def test_the_first_screen_lists_every_write_the_cli_makes(self):
        """#1361: the list of the CLI's own writes left out `keel doctor --fix`.

        It named the merge, `capture-land`, `post-comment` and `review`. The code also
        creates labels (`doctor --fix`), and in the local checkout removes worktrees
        (`worktree-remove`), rebases and merges (`swarm-land --live`) and commits reverts
        (`rollback`, `canary --auto-revert`). `swarm-run --live` was refused before its
        worktrees were made (#1367 review); since #1400 its workers commit, push and open a
        pull request, so it is one.
        The list is compared with :func:`_cli_write_commands`, not with a list typed here.
        """
        screen = " ".join(_readme_first_screen().split())
        start = screen.find("The CLI's own writes")
        self.assertNotEqual(-1, start, "the first screen no longer lists the CLI's own writes")
        listed = screen[start : screen.index(" 3. ", start)]
        named = set(re.findall(r"`keel ([a-z][a-z0-9-]*)", listed))
        writes = _cli_write_commands()
        self.assertTrue({"merge", "doctor"} <= set(writes), writes)
        self.assertEqual({name.split()[0] for name in writes}, named)

    def test_the_first_screen_names_the_flag_behind_doctors_one_write(self):
        """`keel doctor` reads; only `--fix` creates labels, so the list must say `--fix`."""
        self.assertIn("if args.fix", inspect.getsource(cli._cmd_doctor))  # noqa: SLF001
        self.assertIn("doctor", _cli_write_commands())
        self.assertIn("`keel doctor --fix`", " ".join(_readme_first_screen().split()))

    def test_the_merge_claim_is_about_pull_requests(self):
        """`keel swarm-land --live` merges cluster branches locally, so "a merge happens
        only through `keel merge`" was too wide once the write list named it."""
        screen = " ".join(_readme_first_screen().split())
        self.assertIn("swarm-land", _cli_write_commands())
        self.assertIsNone(re.search(r"\bA merge happens only through", screen))
        self.assertIn("A pull request merges only through `keel merge`", screen)

    def test_every_intake_heading_the_quickstart_names_is_one_intake_reads(self):
        """Round 1 of #1360: the Quickstart said all three headings were required.

        `intake.assess_issue` falls back to the title for the objective, and accepts
        aliases for the other two. Every alias the README names must make an issue
        `ready` in its slot, and dropping either slot must not.
        """
        quickstart = self.section("Quickstart")
        self.assertTrue("**Your first issue.**" in quickstart, "no first-issue paragraph")
        para = quickstart.split("**Your first issue.**", 1)[1].split("Then open", 1)[0]
        groups = [re.findall(r"`(?:## )?([^`]+)`", g) for g in re.findall(r"\(([^()]*)\)", para)]
        self.assertGreaterEqual(len(groups), 2, groups)
        deliverables, acceptances = groups[0], groups[1]
        self.assertIn("Scope", deliverables)
        for heading in deliverables:
            with self.subTest(deliverable=heading):
                body = f"## {heading}\nA function.\n\n## Acceptance criteria\n- it returns 1\n"
                self.assertEqual("ready", intake.assess_issue(title="t", body=body)["status"])
        for heading in acceptances:
            with self.subTest(acceptance=heading):
                body = f"## Deliverable\nA function.\n\n## {heading}\n- it returns 1\n"
                self.assertEqual("ready", intake.assess_issue(title="t", body=body)["status"])
        for body in ("## Deliverable\nA function.\n", "## Acceptance criteria\n- it returns 1\n"):
            with self.subTest(missing=body[:20]):
                self.assertEqual("needs-input", intake.assess_issue(title="t", body=body)["status"])

    def test_every_command_the_long_run_table_names_exists(self):
        """#1337: each row was checked against the code; this keeps the names real."""
        section = self.section("Built for long unattended runs")
        rows = [line for line in section.splitlines() if line.startswith("| ")][1:]
        self.assertGreaterEqual(len(rows), 6, rows)
        knobs = json.loads(
            (REPO_ROOT / "src/keel/schema/project.schema.json").read_text(encoding="utf-8")
        )["properties"]["knobs"]["properties"]
        adapters = {path.stem for path in _adapter_commands()}
        for row in rows:
            with self.subTest(row=row[:60]):
                ticked = re.findall(r"`([^`]+)`", row)
                named = {
                    "slash": [t[len("/keel:") :] for t in ticked if t.startswith("/keel:")],
                    "cli": [t.split()[1] for t in ticked if t.startswith("keel ")],
                    "module": [t for t in ticked if t.endswith(".py")],
                    "knob": [t[len("knobs.") :] for t in ticked if t.startswith("knobs.")],
                }
                self.assertTrue(any(named.values()), "the row names nothing checkable")
                for name in named["slash"]:
                    self.assertIn(name, adapters)
                for name in named["cli"]:
                    self.assertIn(name, _subcommands())
                for name in named["module"]:
                    self.assertTrue((REPO_ROOT / "src/keel" / name).is_file(), name)
                for name in named["knob"]:
                    self.assertIn(name, knobs)

    def test_the_long_run_table_cites_its_source_as_independent(self):
        """#1337: a link rather than a reproduction, and no implied affiliation."""
        section = " ".join(self.section("Built for long unattended runs").split())
        self.assertIn("](https://claude.dev/blog/getting-the-most-out-of-opus-5-5/)", section)
        self.assertIn("map to", section)
        self.assertIn("keel is independent and not affiliated with Anthropic", section)
        self.assertIn("](https://keel-ship.dev/silent-revert.html)", section)

    def test_the_quickstart_reaches_a_first_issue(self):
        """#1321: set up, run the gates, then `/keel:ship` in the agent host."""
        quickstart = self.section("Quickstart")
        for step in ("keel setup", "keel run-gates", "build_gate_cmd", "/keel:ship"):
            with self.subTest(step=step):
                self.assertIn(step, quickstart)
        self.assertNotIn("keel ship --live", quickstart, "#1321: the CLI does not ship work")

    def test_the_requirements_section_covers_cost_limits_and_uninstall(self):
        """#1329, and the Python floor it states is the one the package declares."""
        section = self.section("Requirements, cost and limits")
        for fact in (
            "keel cost-report",
            "gh auth login",
            "### Uninstall",
            "`.keel/`",
            "`.claude/commands/keel/`",
            "`.agents/skills/keel-*/`",
            "GitHub only",
        ):
            with self.subTest(fact=fact):
                self.assertIn(fact, section)
        floor = re.search(
            r'requires-python = ">=(\d+\.\d+)"',
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(floor)
        self.assertIn(f"Python {floor.group(1)} or newer", section)

    def test_the_cost_report_caveat_matches_what_keel_records(self):
        """#1329, #1373: the README names the one keel writer of token counts — a
        hosted-API delegate run with `--activity-run-id` — and what is never recorded, and
        prices everything else at the placeholder. The modules that name `prompt_tokens`
        are pinned, so a new writer fails here and the README has to be revisited."""
        section = " ".join(self.section("Requirements, cost and limits").split())
        self.assertIn("1,500 prompt and 400 completion tokens", section)
        report = cost.calculate_cost_report([{}]).to_dict()
        self.assertEqual(
            (1500, 400), (report["total_prompt_tokens"], report["total_completion_tokens"])
        )
        for claim in (
            "keel delegate run --activity-run-id <run>",
            "An agent host's own tokens and a CLI delegate's are never recorded.",
            "never the provider's bill",
        ):
            with self.subTest(claim=claim):
                self.assertIn(claim, section)
        writers = sorted(
            path.name
            for path in (REPO_ROOT / "src/keel").rglob("*.py")
            if path.name != "cost.py" and "prompt_tokens" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["activity.py", "api_delegate.py", "cli.py", "delegaterun.py"],
            writers,
            "the set of modules that handle token counts changed; update the README",
        )

    def test_the_readme_names_what_the_report_itself_now_says(self):
        """#1359: the caveat is in the report's own output, and the README says where."""
        section = " ".join(self.section("Requirements, cost and limits").split())
        rendered = " ".join(cost.render_cost_report(cost.calculate_cost_report([{}])).split())
        claimed = "ESTIMATED at 1,500 prompt / 400 completion tokens per run"
        self.assertIn(claimed, section)
        self.assertIn(claimed, rendered)
        data = cost.calculate_cost_report([{}]).to_dict()
        for key in ("token_basis", "measured_runs", "estimated_runs", "assumed_tokens_per_run"):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", section)
                self.assertIn(key, data)


class TestTheCostReportDocsMatchItsOutput(unittest.TestCase):
    """#1359: `cli.md` shows the basis line the report prints, and names every field."""

    @staticmethod
    def cost_section() -> str:
        text = (REPO_ROOT / "docs/keel/cli.md").read_text(encoding="utf-8")
        start = text.index("## `keel cost-report")
        return text[start : text.index("\n## ", start + 1)]

    def test_the_documented_basis_lines_are_the_ones_the_report_prints(self):
        section = self.cost_section()
        fence = re.search(r"```text\n(.*?)```", section, re.S)
        self.assertIsNotNone(fence, "the cost-report section shows no sample output")
        rendered = cost.render_cost_report(cost.calculate_cost_report([{}, {}])).splitlines()
        for line in fence.group(1).splitlines():
            with self.subTest(line=line):
                self.assertIn(line, rendered)
        measured = {"prompt_tokens": 1, "completion_tokens": 1}
        mixed = cost.render_cost_report(cost.calculate_cost_report([measured] * 3 + [{}] * 2))
        full = cost.render_cost_report(cost.calculate_cost_report([measured] * 5))
        single = cost.render_cost_report(cost.calculate_cost_report([measured]))
        flat = " ".join(section.split())
        for quoted, output in (
            ("3 measured, 2 ESTIMATED at 1,500 prompt / 400 completion tokens per run", mixed),
            ("measured (all 5 runs carry token counts)", full),
            ("measured (the 1 run carries token counts)", single),
        ):
            with self.subTest(quoted=quoted):
                self.assertIn(f"`{quoted}`", flat)
                self.assertIn(quoted, output)

    def test_the_documented_scope_lines_are_printed_for_every_measured_report(self):
        """#1373: a measured count covers a hosted-API delegate's calls, not the host's."""
        fences = re.findall(r"```text\n(.*?)```", self.cost_section(), re.S)
        self.assertEqual(2, len(fences), "the scope lines are no longer shown")
        measured = {"prompt_tokens": 1, "completion_tokens": 1}
        for records in ([measured], [measured, {}]):
            rendered = cost.render_cost_report(cost.calculate_cost_report(records)).splitlines()
            for line in fences[1].splitlines():
                with self.subTest(records=len(records), line=line):
                    self.assertIn(line, rendered)

    def test_the_documented_usage_fields_are_the_ones_the_parser_reads(self):
        """#1373: each row of the vendor table, built into a response, is read as the sum
        the row says — so a field dropped from the parser or the table fails here."""
        rows = re.findall(
            r"^\| (`[a-z-]+`(?:, `[a-z-]+`)?) \| (.+?) \| (.+?) \|$",
            self.cost_section(),
            re.M,
        )
        self.assertEqual(3, len(rows), rows)
        documented = {
            name
            for _vendors, prompt, completion in rows
            for name in re.findall(r"`(?:[A-Za-z]+\.)?([A-Za-z_]+)`", prompt + completion)
        }
        read = set(
            re.findall(
                r'"([A-Za-z_]+(?:Count|_tokens))"', inspect.getsource(api_delegate.parse_usage)
            )
        )
        self.assertEqual(read, documented, "the table and the parser name different fields")
        for vendors, prompt, completion in rows:
            prompt_fields = re.findall(r"`(?:[A-Za-z]+\.)?([A-Za-z_]+)`", prompt)
            completion_fields = re.findall(r"`(?:[A-Za-z]+\.)?([A-Za-z_]+)`", completion)
            container = "usageMetadata" if "google-api" in vendors else "usage"
            usage = {name: 3 for name in prompt_fields} | {name: 5 for name in completion_fields}
            for vendor in re.findall(r"`([a-z-]+)`", vendors):
                with self.subTest(vendor=vendor):
                    self.assertEqual(
                        (3 * len(prompt_fields), 5 * len(completion_fields)),
                        api_delegate.parse_usage(vendor, {container: usage}),
                    )

    def test_every_added_json_field_is_documented_with_its_value(self):
        section = self.cost_section()
        data = cost.calculate_cost_report([{}]).to_dict()
        for key in ("token_basis", "measured_runs", "estimated_runs", "assumed_tokens_per_run"):
            with self.subTest(key=key):
                self.assertIn(f"| `{key}` |", section)
        self.assertIn(json.dumps(data.get("assumed_tokens_per_run")), section)

    def test_the_site_does_not_advertise_cost_tracking_keel_does_not_do(self):
        """Three integration cards claimed per-run token cost tracking, an exact token
        expenditure ledger and token cost analytics. keel records only the counts a
        hosted-API delegate reports (#1373), and prices them from its own table."""
        cards = (REPO_ROOT / "website/integrations.js").read_text(encoding="utf-8").lower()
        for claim in ("cost tracking", "expenditure ledger", "cost analytics", "exact token"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, cards)


def _html_text(fragment: str) -> str:
    """A fragment of the site's HTML as the words a reader sees: tags out, entities in."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


class TheLongRunsArticleClaimsOnlyWhatKeelDoes(unittest.TestCase):
    """#1338: `website/long-runs.html` carries the README's long-run table and one real
    run that stopped. The table is held to the README's, whose names are checked against
    the code above; the run is held to the code that would print it today."""

    ARTICLE = SITE / "long-runs.html"

    @classmethod
    def setUpClass(cls):
        page = cls.ARTICLE.read_text(encoding="utf-8")
        cls.body = page[page.index("<body>") :]

    def test_the_table_is_the_readmes_table(self):
        article = [
            (_html_text(practice), _html_text(what))
            for practice, what in re.findall(
                r"<tr><td>(.*?)</td><td>(.*?)</td></tr>", self.body, re.S
            )
        ]
        section = _readme_sections()["Built for long unattended runs"]
        readme = [
            tuple(" ".join(cell.replace("`", "").split()) for cell in line.split("|")[1:3])
            for line in section.splitlines()
            if line.startswith("| ")
        ][1:]
        self.assertGreaterEqual(len(readme), 6, readme)
        self.assertEqual(article, readme)

    def test_it_links_the_guide_rather_than_reproducing_it(self):
        """The issue's vendor-neutral terms: maps to, a link, independence, no logos."""
        text = _html_text(self.body)
        self.assertIn('href="https://claude.dev/blog/getting-the-most-out-of-opus-5-5/"', self.body)
        self.assertIn("These map to the long-run advice", text)
        self.assertIn("keel is independent and not affiliated with Anthropic", text)
        self.assertNotIn("<img", self.body)
        self.assertNotIn("<svg", self.body)

    def test_the_run_links_its_record(self):
        for record in ("issues/965", "pull/968", "pull/919", "pull/920", "pull/958", "pull/967"):
            with self.subTest(record=record):
                self.assertIn(f'href="https://github.com/berkayturanci/keel/{record}"', self.body)

    def _transcript(self) -> list[str]:
        block = re.search(r"<pre><code>\$ keel evidence-verify (.*?)</code></pre>", self.body, re.S)
        self.assertIsNotNone(block, "the article no longer shows the evidence check")
        return html.unescape(block.group(1)).splitlines()

    def test_the_command_it_shows_is_one_keel_accepts(self):
        argv = ["evidence-verify", *shlex.split(self._transcript()[0])]
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                args = cli.build_parser().parse_args(argv)
        except SystemExit:
            self.fail(f"keel's parser refuses the article's command: keel {' '.join(argv)}")
        else:
            self.assertEqual(args.phase, evidence.PHASE_PRE_MERGE)

    def test_the_missing_evidence_it_shows_is_what_tier_3_required(self):
        """Three reviewers and a gating jury, as tier 3 was on 2026-08-25."""
        fields = dict(line.split(":", 1) for line in self._transcript()[1:])
        required = evidence.required_items(
            {"reviewers": {"count": 3}, "jury": {"enabled": True, "mode": "gating"}},
            phase=evidence.PHASE_PRE_MERGE,
        )
        self.assertEqual(fields["  required "].strip(), str(len(required)))
        self.assertEqual(
            [item.strip() for item in fields["  missing  "].split(",")],
            [item.id for item in required],
        )

    def test_the_decision_it_reports_is_still_the_workflows(self):
        """The article says `--no-jury` is still passed at the pre-merge check's ARGS line."""
        self.assertIn("still passes <code>--no-jury</code> at that call site", self.body)
        workflow = (REPO_ROOT / ".github/workflows/keel-ship.yml").read_text(encoding="utf-8")
        calls = [
            line
            for line in workflow.splitlines()
            if line.strip().startswith("ARGS=(") and "--phase pre-merge" in line
        ]
        self.assertTrue(calls, "keel-ship.yml no longer runs the pre-merge evidence check")
        for line in calls:
            self.assertIn("--no-jury", line)


class TheSiteAdvertisesNoTokenOrCostMetering(unittest.TestCase):
    """keel records no spend, and no token count beyond what a hosted-API delegate reports
    (#1373; `keel cost-report` prices every other run at a placeholder), yet the home
    page's swarm simulator ran "Tokens Processed",
    "Estimated Spend" and "Routing Savings" counters off random increments, and three
    integration cards promised token cost tracking, an expenditure ledger and cost
    analytics. Every page and script the site serves is read, not a list of them."""

    #: The shapes those claims took, and the obvious neighbours of each.
    _METERING = re.compile(
        r"tokens?\s+processed|estimated\s+spend|routing\s+savings|cost\s+tracking"
        r"|cost\s+analytics|expenditure\s+ledger|exact\s+token|token\s+(?:usage|spend)",
        re.IGNORECASE,
    )

    def test_the_old_claims_are_what_the_pattern_matches(self):
        for claim in (
            "Tokens Processed",
            "Estimated Spend",
            "Routing Savings",
            "per-run token cost tracking",
            "exact token expenditure ledger",
            "token cost analytics",
        ):
            with self.subTest(claim=claim):
                self.assertRegex(claim, self._METERING)

    def test_no_page_or_script_advertises_metering(self):
        served = sorted([*SITE.glob("*.js"), *SITE.glob("*.html")])
        self.assertGreater(len(served), 10, "the site's files were not found")
        found = [
            f"{path.name}: {match.group(0)!r}"
            for path in served
            for match in self._METERING.finditer(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], found)


class TestTheJuryDefaultIsTheOneResolveJuryImplements(unittest.TestCase):
    """#1345: the README called the jury "off by default"; tier 3 turns it on."""

    def test_tier_three_turns_the_jury_on_and_the_gates_list_does_not(self):
        self.assertEqual(
            (True, "tier-3 auto"),
            tuple(ship.resolve_jury(tier=3).get(key) for key in ("enabled", "reason")),
        )
        self.assertFalse(ship.resolve_jury(tier=2, gates=("jury",))["enabled"])

    #: The two shapes the wrong sentence took, within one clause of the word "jury":
    #: "add `jury` to your `gates:` (off by default)" and "the opt-in jury gate".
    _WRONG_DEFAULT = (
        re.compile(r"\bjury[^.;]{0,160}\boff by default|\boff by default[^.;]{0,160}\bjury", re.I),
        re.compile(r"\bopt-in[^.;]{0,40}\bjury|\bjury[^.;]{0,40}\bopt-in", re.I),
    )

    def test_no_public_page_calls_the_jury_off_by_default_or_opt_in(self):
        """README, AGENTS, SECURITY, `docs/keel/` and every site page (#1345).

        Tags are stripped first: on the site the word "jury" sits inside a link whose
        URL has dots in it, which would end the clause before the claim.
        """
        pages = _public_pages()
        for page in ("README.md", "website/index.html", "website/content.js"):
            self.assertIn(page, pages)
        for where, text in pages.items():
            prose = _prose(where, text)
            for pattern in self._WRONG_DEFAULT:
                with self.subTest(page=where, pattern=pattern.pattern[:30]):
                    found = pattern.search(prose)
                    self.assertIsNone(found, found and found.group(0))

    def test_the_gates_reference_says_the_list_does_not_switch_the_jury(self):
        """#1345 asked configuration.md for the same correction: `gates: [jury]` adds an s8
        run, and tier 3 turns the jury on whether or not it is listed."""
        text = (REPO_ROOT / "docs/keel/configuration.md").read_text(encoding="utf-8")
        gates = text.split("#### `gates`", 1)[-1].split("\n#### ", 1)[0]
        gates = " ".join(gates.split())
        for claim in ("**tier-3** change", "leaving it out does not keep the jury off"):
            with self.subTest(claim=claim):
                self.assertTrue(claim in gates, f"the `gates` reference never says {claim!r}")

    #: The one sentence about a missing `jury` binary, as each surface words it.
    _VERDICT_OWED = (
        "a tier-3 merge still requires a `jury-verdict` unless the run passes `--no-jury`"
    )
    #: The whole sentence, which the adapter must carry: its agents act on the relaxation
    #: clause, and a shorter restatement elsewhere in the same file would satisfy the
    #: first half alone (#1361).
    _VERDICT_OWED_IN_FULL = (
        f"{_VERDICT_OWED}; it relaxes to advisory only when a posted verdict "
        "(or `--jury-vendors`) reports fewer than 2 vendors"
    )
    #: The `ship` adapter's source and every copy `make adapters plugin` generates from it
    #: (#1361). The drift tests hold the copies to the source byte for byte; they are
    #: listed so that a stale copy fails here too, naming the file an agent reads.
    _SHIP_ADAPTERS = (
        "src/keel/adapters/commands/ship.md",
        "commands/ship.md",
        ".claude/commands/keel/ship.md",
        ".agents/skills/keel-ship/SKILL.md",
    )
    _NO_BINARY = {
        "README.md": _VERDICT_OWED,
        "SECURITY.md": _VERDICT_OWED,
        "docs/keel/configuration.md": _VERDICT_OWED,
        "docs/keel/overview.md": _VERDICT_OWED,
        "docs/keel/cli.md": _VERDICT_OWED,
        "docs/keel/parameter-reference.md": _VERDICT_OWED,
        "website/index.html": "the merge still needs a jury verdict",
        "website/content.js": (
            "a tier-3 merge still requires a jury verdict "
            "unless the run passes <code>--no-jury</code>"
        ),
        **dict.fromkeys(_SHIP_ADAPTERS, _VERDICT_OWED_IN_FULL),
        # Its docstrings only: read through `_surface_text`, RST's ``x`` as Markdown's `x`.
        "src/keel/jury.py": _VERDICT_OWED_IN_FULL,
    }
    #: Every shape the wrong sentence took: "a fail-soft no-op without the `jury` binary",
    #: "Without the `jury` binary … it degrades to advisory", "a tier-3 change's jury is a
    #: fail-soft no-op", and at the evidence layer "the flow runs with or without jury"
    #: (cli.md, jury.py) and "an absent … jury can never manufacture a block"
    #: (parameter-reference.md, the ship adapter). #1361 added three: the adapter's
    #: evidence check that "declines to *require* a verdict from a panel that never ran"
    #: (it needs the count reported), `run_gate`'s "Fail-soft no-op when … the ``jury``
    #: CLI is not installed", and parameter-reference.md's "A missing `jury` CLI is a
    #: fail-soft no-op". Tags are stripped first. `the tool binary` (a preset) is not it.
    _WAIVED = re.compile(
        r"fail-soft[^.;]{0,60}\b(the|jury)`? binary"
        r"|jury`? binary[^.;]{0,80}(fail-soft|degrades to advisory)"
        r"|jury is a fail-soft"
        r"|flow runs with or without jury"
        r"|can never manufacture a block"
        r"|verdict from a panel that never ran"
        r"|fail-soft no-op when[^.;]{0,80}not installed"
        r"|jury`? CLI is a fail-soft",
        re.I,
    )

    @staticmethod
    def _surface_text(name: str) -> str:
        """A surface's words, whitespace collapsed. A Python module contributes its
        docstrings and nothing else, with RST's double backticks read as single ones."""
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        if name.endswith(".py"):
            tree = ast.parse(text)
            nodes = [tree, *(n for n in ast.walk(tree) if isinstance(n, _DOCUMENTED))]
            text = " ".join(filter(None, map(ast.get_docstring, nodes))).replace("``", "`")
        return " ".join(text.split())

    def test_every_surface_says_a_missing_binary_does_not_waive_the_verdict(self):
        """Round 1 of #1360: "fail-soft" / "degrades to advisory" without the binary.

        Measured: with no `jury` on PATH a tier-3 `keel ship --json` still reports the
        jury `gating (tier-3 auto)` and lists `jury-verdict` in the required evidence.
        Only the s8 run is a no-op; a posted verdict's vendor count is what relaxes it.
        """
        for name, sentence in self._NO_BINARY.items():
            text = self._surface_text(name)
            with self.subTest(page=name, check="says the verdict is still owed"):
                self.assertTrue(sentence in text, f"{name} does not say {sentence!r}")
            with self.subTest(page=name, check="does not call it fail-soft"):
                found = self._WAIVED.search(_prose(name, text))
                self.assertIsNone(found, found and found.group(0))
        tier3 = ship.resolve_jury(tier=3)
        self.assertEqual(("gating", True), (tier3["mode"], tier3["enabled"]))
        short = ship.resolve_jury(tier=3, participating_vendors=1)
        self.assertEqual("advisory", short["mode"])

    #: The relaxation clause of `_VERDICT_OWED_IN_FULL`. It is the default policy only,
    #: and read alone it is the whole rule: `team.jury.min_vendors` raises its 2, and on a
    #: tier whose review is the jury panel neither a flag nor a short panel relaxes it.
    _RELAXATION = "relaxes to advisory only when a posted verdict"
    #: What every statement of that clause must say within the next few sentences.
    _DEFAULT_ONLY = (
        "That is the default policy",
        "`team.jury.min_vendors`",
        "tier whose review is the jury panel",
        "`team.jury.on_unavailable: fallback`",
    )
    _RELAXATION_SURFACES = (
        "README.md",
        "docs/keel/cli.md",
        "docs/keel/parameter-reference.md",
        "src/keel/jury.py",
        "website/content.js",
        *_SHIP_ADAPTERS,
    )

    def test_every_relaxation_clause_says_it_is_the_default_policy(self):
        """Docs audit 2026-09-29: "it relaxes to advisory only when … fewer than 2 vendors"
        read as the whole rule on every surface but the adapter and configuration.md.

        Measured in `resolve_jury`: `team.jury.min_vendors` raises the floor,
        `team.jury.mode: advisory` relaxes it off a panel tier, a panel tier ignores both
        flags and a short panel, and only the measured fallback turns that tier off.
        """
        raised = ship.resolve_jury(tier=3, participating_vendors=2, minimum_vendors=3)
        self.assertEqual(("advisory", True), (raised["mode"], raised["downgraded"]))
        self.assertEqual("advisory", ship.resolve_jury(tier=3, policy_mode="advisory")["mode"])
        panel = ship.resolve_jury(
            tier=3, panel_is_jury=True, no_jury=True, jury_advisory=True, participating_vendors=1
        )
        self.assertEqual("gating", panel["mode"])
        fallback = ship.resolve_jury(tier=3, panel_is_jury=True, panel_unavailable=True)
        self.assertEqual("off", fallback["mode"])
        stated = {
            where
            for where, text in _public_pages().items()
            if self._RELAXATION in _prose(where, text)
        }
        with self.subTest(check="every public page stating the clause is checked below"):
            self.assertLessEqual(stated, set(self._RELAXATION_SURFACES))
        for name in self._RELAXATION_SURFACES:
            # Tags stripped and backticks dropped, so the site's `<code>` reads the same.
            text = _prose(name, self._surface_text(name)).replace("`", "")
            starts = [found.start() for found in re.finditer(self._RELAXATION, text)]
            with self.subTest(page=name, check="states the clause"):
                self.assertTrue(starts, f"{name} no longer states {self._RELAXATION!r}")
            for start in starts:
                window = " ".join(text[start : start + 800].split())
                for phrase in self._DEFAULT_ONLY:
                    with self.subTest(page=name, at=start, phrase=phrase):
                        self.assertIn(phrase.replace("`", ""), window)

    #: "the optional jury verdict" (evidence.py) and "optional jury-verdict comments"
    #: (github-actions.md): at tier 3 it is required by default, binary or no binary.
    _OPTIONAL_VERDICT = re.compile(r"\boptional `?jury`?[- ]verdict", re.I)

    #: The flags a jury-panel tier records and does not apply, as `resolve_jury` spells them.
    _PANEL_IGNORES = (("--no-jury", "no_jury"), ("--jury-advisory", "jury_advisory"))

    def _sentence(self, name: str, pattern: str, *, whole: bool = False) -> str:
        """The adapter's first sentence matching ``pattern`` (case-insensitive): all of it
        when ``whole``, otherwise from the match to the sentence's end.

        A sentence ends at a full stop followed by whitespace, so `team.jury.mode` does not
        end one; `;` does not either, so a sentence keeps its second clause.
        """
        for sentence in re.split(r"(?<=\.)\s+", self._surface_text(name)):
            found = re.search(pattern, sentence, re.I)
            if found:
                return sentence if whole else sentence[found.start() :]
        raise self.failureException(f"{name} has no sentence matching {pattern!r}")

    def _panel_clause(self, name: str) -> str:
        """What the adapter says about a jury-panel tier, to the end of that sentence."""
        return self._sentence(name, r"on a tier whose review is the jury panel\b")

    def test_the_adapter_names_every_flag_a_panel_tier_ignores(self):
        """#1367 review: "`--jury-advisory` never requires the verdict" was unscoped.

        `resolve_jury` applies `--jury-advisory` below a panel tier and ignores it on one,
        so the adapter must scope the relaxation and list the flag with the ignored ones.
        """
        for flag, kwarg in self._PANEL_IGNORES:
            with self.subTest(flag=flag, check="ignored on a panel tier"):
                panel = ship.resolve_jury(tier=3, panel_is_jury=True, **{kwarg: True})
                self.assertEqual("gating", panel["mode"])
        self.assertEqual("advisory", ship.resolve_jury(tier=3, jury_advisory=True)["mode"])
        for name in self._SHIP_ADAPTERS:
            clause = self._panel_clause(name)
            for flag, _ in self._PANEL_IGNORES:
                with self.subTest(page=name, flag=flag):
                    self.assertIn(f"`{flag}`", clause)
            relax = self._sentence(
                name, r"`--jury-advisory` never requires the verdict", whole=True
            )
            with self.subTest(page=name, check="the relaxation is scoped"):
                self.assertIn("off a jury-panel tier", relax.lower())

    def test_the_adapter_names_the_fallback_that_turns_a_panel_tier_off(self):
        """#1367 review: the case list read as complete and left out the one path that
        turns a panel tier's jury off without `--no-jury` (ship.py's `panel_unavailable`)."""
        fallback = ship.resolve_jury(tier=3, panel_is_jury=True, panel_unavailable=True)
        self.assertEqual((False, "off"), (fallback["enabled"], fallback["mode"]))
        for name in self._SHIP_ADAPTERS:
            with self.subTest(page=name):
                self.assertIn("`team.jury.on_unavailable: fallback`", self._panel_clause(name))

    def test_the_adapter_says_an_unreported_count_downgrades_nothing(self):
        """#1367 review: "a run where no agent returned output is simply zero vendors, so
        … a jury that did not complete cleanly does not gate" at the evidence check.

        `resolve_jury` downgrades only a *reported* count, and
        `evidence.jury_participating_vendors` reports nothing when no verdict is posted:
        with no count the mode stays gating and the verdict stays required.
        """
        unreported = ship.resolve_jury(tier=3, participating_vendors=None)
        self.assertEqual(("gating", False), (unreported["mode"], unreported["downgraded"]))
        self.assertEqual("advisory", ship.resolve_jury(tier=3, participating_vendors=0)["mode"])
        self.assertIsNone(evidence.jury_participating_vendors([], [], head_sha="a" * 40))
        self.assertIn(
            "jury-verdict",
            {
                item.id
                for item in evidence.required_items(
                    {"jury": unreported}, phase=evidence.PHASE_PRE_MERGE
                )
            },
        )
        for name in self._SHIP_ADAPTERS:
            zero = self._sentence(name, r"no agent returned output", whole=True)
            with self.subTest(page=name, check="zero counts only once reported"):
                self.assertIn("reported", zero)
            none = self._sentence(name, r"with no count at all", whole=True)
            with self.subTest(page=name, check="no count keeps the verdict required"):
                self.assertIn("no downgrade", none)
                self.assertIn("`jury-verdict`", none)

    def test_no_surface_calls_the_jury_verdict_optional(self):
        """#1361: the evidence module and the Actions guide called the verdict optional."""
        pages = dict(_public_pages())
        for name in (*self._SHIP_ADAPTERS, "src/keel/evidence.py", "src/keel/jury.py"):
            pages[name] = self._surface_text(name)
        for page in ("docs/keel/github-actions.md", "src/keel/evidence.py"):
            self.assertIn(page, pages)
        for where, text in pages.items():
            with self.subTest(page=where):
                found = self._OPTIONAL_VERDICT.search(_prose(where, text))
                self.assertIsNone(found, found and found.group(0))

    def test_the_pre_merge_phase_names_the_jury_verdict(self):
        """#1361: evidence.md's phase contract listed the review verdicts and the gate
        results before s10 and left out the one artifact a gating jury adds to them."""
        self.assertIn(
            "jury-verdict",
            {
                item.id
                for item in evidence.required_items(
                    {"jury": ship.resolve_jury(tier=3)}, phase=evidence.PHASE_PRE_MERGE
                )
            },
        )
        text = (REPO_ROOT / "docs/keel/evidence.md").read_text(encoding="utf-8")
        bullet = re.search(r"\* \*\*Pre-Merge Phase.*?(?=\n\* |\n\n)", text, re.S)
        self.assertIsNotNone(bullet, "evidence.md has no Pre-Merge Phase bullet")
        self.assertIn("`jury-verdict`", bullet.group(0))

    def test_the_readme_says_tier_three_turns_it_on(self):
        bullet = re.search(
            r"\n- \*\*A cross-vendor jury(.*?)\n- \*\*",
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            re.S,
        )
        self.assertIsNotNone(bullet, "the README no longer has its jury bullet")
        text = " ".join(bullet.group(1).split())
        self.assertIn("automatically at tier 3", text)
        self.assertIn("`--no-jury`", text)


class TheReadmeCoverageClaimIsTheGate(unittest.TestCase):
    """The README's coverage sentence describes what `fail_under = 100` measures (#1363 review).

    It said only "the pure core (`config`, `model`, …, `cli`)" was held at 100 %, while
    `[tool.coverage.run]` measures the whole `keel` package and omits one file, the
    `python -m keel` shim. A claim smaller than the gate undersells it, and a reader who
    trusts it treats every other module as uncovered. So the sentence is read against
    `pyproject.toml`, not against a list retyped here.
    """

    def setUp(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
            self.coverage = tomllib.load(fh)["tool"]["coverage"]
        self.readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_gate_measures_the_whole_package_at_100(self):
        self.assertEqual(self.coverage["run"]["source"], ["keel"])
        self.assertEqual(self.coverage["report"]["fail_under"], 100)

    def test_the_readme_claims_the_whole_package(self):
        self.assertIn("Every module under `src/keel/` is held at **100% line + branch", self.readme)
        self.assertNotRegex(self.readme, r"The pure core \([^)]*\) is held at")

    def test_the_readme_names_every_omitted_file(self):
        omitted = self.coverage["run"]["omit"]
        self.assertTrue(omitted)
        for pattern in omitted:
            self.assertTrue(pattern.startswith("*/keel/"), pattern)
            with self.subTest(omit=pattern):
                self.assertIn(f"`src/keel/{pattern.removeprefix('*/keel/')}`", self.readme)


class TheQuickstartDescribesTheFirstRunKeelHas(unittest.TestCase):
    """The Quickstart's first-run advice matches what setup and doctor now do (#1328, #1334).

    #1360 wrote the Quickstart while a stackless project still got `make test`, so it told
    the reader to expect `make: *** No rule to make target`. Setup now writes no command
    and says `build gate   : not configured`, and the gate fails with a finding naming the
    knob. Each quoted string is checked against the code that prints it.
    """

    def setUp(self):
        self.readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    def test_no_make_fallback_is_promised(self):
        self.assertNotIn("No rule to make target", self.readme)
        self.assertNotIn("fell\nback to `make test`", self.readme)
        self.assertNotIn("fell back to `make test`", self.readme)

    def test_the_quoted_setup_line_is_what_init_prints(self):
        quoted = "build gate   : not configured"
        self.assertIn(f"`{quoted}`", self.readme)
        with tempfile.TemporaryDirectory() as d:
            rc, out, err = _run_cli(["init", "--root", d])
        self.assertEqual(rc, 0, err)
        self.assertIn(quoted, out)

    def test_the_quoted_finding_is_the_gates(self):
        head = gates.UNCONFIGURED_BUILD_GATE.split(" in ", 1)[0]
        self.assertEqual(head, "no build gate configured: set knobs.build_gate_cmd")
        self.assertIn(f"`{head} …`", self.readme)

    def test_the_doctor_comment_names_checks_doctor_has(self):
        self.assertIn("# versions, adapters, gh auth and agent hosts", self.readme)
        report = doctor.run_doctor(
            installed_version="1.0.0",
            latest_version=None,
            adapter_markers=[],
            orphans=[],
            core_version=None,
            state_paths=[],
        )
        names = {check["name"] for check in report["checks"]}
        self.assertLessEqual({"cli_version", "adapter_version", "github_cli", "agent_hosts"}, names)


class TheInitReferenceQuotesInitsOwnLine(unittest.TestCase):
    """`cli.md` quotes the line `keel init` prints for an unset build gate, whole (#1365 review).

    It was a code span broken across two source lines, which Markdown renders with one
    space where the output has three (`build gate   :`), so a reader searching for what
    they saw found nothing. The quote is now the exact line, checked against the constant
    `init` prints.
    """

    def test_the_whole_line_is_quoted_verbatim(self):
        text = CLI_DOC.read_text(encoding="utf-8")
        self.assertIn(f"  build gate   : {cli._UNSET_BUILD_NOTE}\n", text)


class TheWorkflowPageMatchesThisRepositorysJuryPolicy(unittest.TestCase):
    """`github-actions.md` describes this repository's own `keel-ship` workflow (#1367).

    The jury-verdict requirement it names depends on `team.jury.mode` in the
    `.keel/project.yaml` that workflow passes to `evidence-verify`, so the page must say
    what that file sets rather than only what the default policy does.
    """

    _ADVISORY_HERE = ("sets `team.jury.mode: advisory`", "so here the jury never requires one")

    def _mode(self) -> str:
        from keel.config import load_config

        return load_config(REPO_ROOT / ".keel" / "project.yaml").knobs.team.jury_mode

    def _page(self) -> str:
        return " ".join(
            (REPO_ROOT / "docs" / "keel" / "github-actions.md").read_text(encoding="utf-8").split()
        )

    def test_the_page_names_the_mode_this_repository_sets(self):
        page = self._page()
        self.assertIn("evidence-verify .keel/project.yaml", page)
        advisory_here = [phrase in page for phrase in self._ADVISORY_HERE]
        if self._mode() == "advisory":
            self.assertEqual(advisory_here, [True, True])
        else:
            self.assertEqual(advisory_here, [False, False], "the page claims advisory; it is not")

    def test_the_check_holds_in_both_directions(self):
        page = self._page()
        claims_advisory = all(phrase in page for phrase in self._ADVISORY_HERE)
        for mode in ("advisory", "gating"):
            with (
                self.subTest(mode=mode),
                unittest.mock.patch.object(type(self), "_mode", lambda self, m=mode: m),
            ):
                if (mode == "advisory") == claims_advisory:
                    self.test_the_page_names_the_mode_this_repository_sets()
                else:
                    with self.assertRaises(AssertionError):
                        self.test_the_page_names_the_mode_this_repository_sets()


if __name__ == "__main__":
    unittest.main()
