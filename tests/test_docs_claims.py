"""What the docs and the site *claim* keel is, checked against keel (issue #1019).

`tests/test_documented_commands.py` already asks whether a documented `keel <cmd>`
is a real subcommand. Every defect this module pins slipped past it, because
every one of them is a claim of a different shape:

* Eleven subcommands had no section in `docs/keel/cli.md` at all — `gc`,
  `canary`, `rollback`, `cost-report`, `close-reconcile`, `dryrun-verify`,
  `scratch-dir`, `release`, `adapter-status`, `update-adapter`, `sync`. A
  reference is not a reference if reaching a command means reading `cli.py`.
* `knobs.swarm_review_evidence` — the knob that decides whether swarm landings
  enforce review at all — was in the schema and in no configuration table.
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

import contextlib
import inspect
import io
import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path

from keel import cli, extensions, install, model

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"
CLI_DOC = REPO_ROOT / "docs" / "keel" / "cli.md"
CONFIG_DOC = REPO_ROOT / "docs" / "keel" / "configuration.md"
PARAM_DOC = REPO_ROOT / "docs" / "keel" / "parameter-reference.md"
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

    `knobs.swarm_review_evidence` decides whether `keel swarm-land` enforces the
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
        ("website/docs.html", re.compile(r"extension slots, (\d+) /keel commands")),
        ("website/docs.html", re.compile(r'Workflow commands <span class="badge">(\d+)</span>')),
        ("website/index.html", re.compile(r"extension slots, (\d+) /keel commands")),
        ("website/index.html", re.compile(r'Workflow commands <span class="badge">(\d+)</span>')),
        ("website/index.html", re.compile(r"(\d+) /keel commands, stdlib-first")),
        ("website/index.html", re.compile(r"All (\d+) <code>/keel:")),
        ("website/index.html", re.compile(r"(\d+) workflows · /keel:")),
        ("website/index.html", re.compile(r"<b>(\d+)<span class=\"u\"> cmds</span>")),
        ("website/coverage.html", re.compile(r"extension slots, (\d+) /keel commands")),
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
    """`13 steps` and `28 extension slots` are printed in nine places across the site.

    Both are hero numbers: they appear in `og:image:alt`, in `twitter:image:alt`, in the
    README's hero and in the body copy, and every one of them is hand-typed. The command
    count drifted exactly this way — 16 in five places, 17 in five others — and there is
    no reason the other two are safer. `model.BACKBONE` and `model.SLOTS` are the answer.
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


if __name__ == "__main__":
    unittest.main()
