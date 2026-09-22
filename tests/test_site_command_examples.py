"""The site's slash-command examples must use flags those commands actually take.

`website/content.js` carries a `cmdExample` map — one illustrative `/keel:<command>`
invocation per command — rendered on the commands page. Nothing checked it against the
commands themselves, and it had drifted badly: 12 of 17 entries used a flag the command
does not define. Six passed a value as `--issue` / `--pr` / `--issues` where the command
takes a **positional** argument; `--comments` should have been `--review-comments`; and
`--route-to-ship`, `--apply`, `--until` and `--propose-fix` are not flags of any keel
command. (`--until` appears in the tree only as git's own option, in prose about
`git log --since/--until`.)

`/keel:deps-audit --security-only` was a different case: the flag is real — documented
in the adapter body and implemented — but its own `argument-hint` omits it, so the
example was moved to one the hint does declare.

The earlier hand check missed it by asking the wrong question — whether a flag token
existed *somewhere* in keel's CLI, rather than on the command being illustrated. A flag
is only real for a command if that command's own adapter declares it, so this test reads
the per-command `argument-hint` frontmatter in `src/keel/adapters/commands/<cmd>.md`
(the same string the hosts show as the command's usage line) and holds every example to
it. Stdlib-only, like the rest of the suite.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT_JS = REPO_ROOT / "website" / "content.js"
COMMANDS_DIR = REPO_ROOT / "src" / "keel" / "adapters" / "commands"

_BLOCK_RE = re.compile(r"cmdExample:\s*\{(.*?)\n  \},", re.DOTALL)
_ENTRY_RE = re.compile(r'"([a-z][a-z0-9-]*)":\s*"([^"]+)"')
_HINT_RE = re.compile(r'^argument-hint:\s*"(.*)"\s*$', re.MULTILINE)
_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")
# The adapters document their own flags as a bullet opening with the flag in
# backticks — "- `--dry-run` — …". Anchoring on that shape keeps the CLI's own
# flags, which the bodies also quote inside `keel …` command lines, out of it.
#
# One bullet can head several alternatives: "- `--jury` / `--no-jury` /
# `--jury-advisory` — …". Reading only the first left `--no-jury` and
# `--jury-advisory` unchecked, so the whole head of the bullet is read — up to the
# separator that opens the prose, which is where a quoted flag would be someone
# else's.
#
# **What this deliberately does not read**, so a reader does not assume more coverage
# than there is:
#
# - A bullet opening `- **`--flag`**`. Two adapters declare that way (`stale-prs`,
#   `triage`), and both flags are declared — but `- **`--head-sha` is the head that
#   merged**` (`ship.md:1389`) is the *same shape* around a `keel capture-land` flag,
#   not a slash-command one. Widening to it was measured and claimed two CLI flags
#   immediately, so the shape stays out.
# - Adapters that document their flags in running prose rather than a bullet list.
#   11 of the 17 yield no flags at all, `overnight`, `swarm` and `work-block` among
#   them, and their hints are therefore held to nothing here. Closing that gap means
#   a documentation convention, not a wider regex.
_BULLET_HEAD_RE = re.compile(r"^- (`--[a-z][a-z0-9-]*[^\n]*)$", re.MULTILINE)
_BACKTICKED_FLAG_RE = re.compile(r"`(--[a-z][a-z0-9-]*)")


def site_examples() -> dict[str, str]:
    """The `cmdExample` map as {command: example invocation}."""
    block = _BLOCK_RE.search(CONTENT_JS.read_text(encoding="utf-8"))
    if block is None:  # pragma: no cover - guarded by the vacuity test below
        return {}
    return dict(_ENTRY_RE.findall(block.group(1)))


# The separator between a bullet's declaration and its prose. Most adapters use an
# em dash; three (`coverage`, `deps-audit`, `flake-audit`) use an arrow on their
# `--dry-run` bullet. Splitting on only one of them read a whole line as declaration.
_BULLET_SEPARATORS = re.compile(r"[\u2014\u2192]")


def _flags_in_bullet_head(head: str) -> set[str]:
    """The flags a documentation bullet declares as the command's own.

    Everything up to the separator is the declaration; past it is prose, where a
    backticked flag belongs to whatever `keel …` line the prose is describing.
    """
    return set(_BACKTICKED_FLAG_RE.findall(_BULLET_SEPARATORS.split(head, 1)[0]))


def documented_flags(command: str) -> set[str]:
    """The flags the adapter body describes as this command's own."""
    source = COMMANDS_DIR / f"{command}.md"
    if not source.exists():  # pragma: no cover - callers iterate real adapters
        return set()
    flags: set[str] = set()
    for head in _BULLET_HEAD_RE.findall(source.read_text(encoding="utf-8")):
        flags.update(_flags_in_bullet_head(head))
    return flags


def argument_hint(command: str) -> str | None:
    """The command adapter's declared usage line, or None when it declares none."""
    source = COMMANDS_DIR / f"{command}.md"
    if not source.exists():
        return None
    found = _HINT_RE.search(source.read_text(encoding="utf-8"))
    return found.group(1) if found else None


class SiteCommandExamples(unittest.TestCase):
    def setUp(self):
        self.examples = site_examples()

    def test_the_example_map_is_found_and_not_empty(self):
        """Vacuity: if the block is renamed or reshaped, every check below would
        silently pass on an empty map."""
        self.assertGreaterEqual(
            len(self.examples),
            10,
            f"no usable cmdExample map parsed from {CONTENT_JS}",
        )

    def test_every_example_names_a_real_command(self):
        for command in self.examples:
            with self.subTest(command=command):
                self.assertTrue(
                    (COMMANDS_DIR / f"{command}.md").exists(),
                    f"cmdExample has {command!r}, which is not a keel command adapter",
                )

    def test_every_example_flag_is_declared_by_that_command(self):
        """The defect this file exists for: a flag is only real for the command
        whose adapter declares it, not for keel's CLI at large."""
        for command, example in self.examples.items():
            hint = argument_hint(command)
            if hint is None:
                continue
            declared = set(_FLAG_RE.findall(hint))
            used = set(_FLAG_RE.findall(example))
            unknown = sorted(used - declared)
            with self.subTest(command=command):
                self.assertEqual(
                    unknown,
                    [],
                    f"/keel:{command} example uses {unknown}, which its "
                    f"argument-hint does not declare:\n  example: {example}\n"
                    f"  hint:    {hint}",
                )

    def test_examples_invoke_the_command_they_are_keyed_under(self):
        for command, example in self.examples.items():
            with self.subTest(command=command):
                self.assertTrue(
                    example.startswith(f"/keel:{command}"),
                    f"cmdExample[{command!r}] does not invoke /keel:{command}: {example}",
                )


class HintsDeclareEveryDocumentedFlag(unittest.TestCase):
    """The other half of the drift, and the one that cost a working feature.

    `SiteCommandExamples` holds the *examples* to the hint. It cannot see a flag the
    hint leaves out, because an example that used one would simply be reported as
    unknown — which is how `/keel:deps-audit --security-only` was handled when it was
    first found: the flag was real, documented in the body and implemented, and the
    site example was moved off it rather than the hint corrected (#1282). The hint is
    the usage line every host shows, so a flag absent from it is a feature no reader
    is told exists.

    This holds the hint to the body instead. Writing it surfaced a second live
    instance, `/keel:ship --role`, which was in the body's flag list beside
    `--delegate`, `--effort` and `--team` — all three declared — and wired at
    `cli.py`, but missing from the usage line.
    """

    def setUp(self):
        self.commands = sorted(p.stem for p in COMMANDS_DIR.glob("*.md"))

    def test_the_adapter_set_is_found_and_not_empty(self):
        """Vacuity: an empty or renamed directory would pass every check below."""
        self.assertGreaterEqual(
            len(self.commands), 10, f"no command adapters parsed from {COMMANDS_DIR}"
        )

    def test_the_bullet_shape_actually_matches_something(self):
        """Vacuity: if the adapters' documentation convention changes, the regex
        would find nothing and this file would go quietly blind."""
        with_flags = [c for c in self.commands if documented_flags(c)]
        self.assertGreaterEqual(
            len(with_flags),
            5,
            "no adapter documents a flag as '- `--flag`'; the convention moved and "
            "this check no longer reads anything",
        )

    def test_a_flag_documented_with_its_parameter_is_read(self):
        """Round 2 finding, and a fix that made things worse before it made them
        better. Requiring a closing backtick straight after the flag name excluded
        every bullet of the form `- ``--role <label>`` — …`, which is most of them:
        15 flags across 6 adapters, `--role` among them — the exact flag this branch
        claimed to have pinned."""
        flags = documented_flags("ship")
        for flag in ("--role", "--delegate", "--review-delegate", "--effort", "--team"):
            with self.subTest(flag=flag):
                self.assertIn(flag, flags)

    def test_a_flag_quoted_in_the_prose_is_not_claimed(self):
        """The counterweight to reading the whole head: the bullets describe keel's
        CLI in their prose, and those flags belong to `keel …`, not to the slash
        command. The separator is the boundary.

        The first version of this test could not fail: its fixture wrote the prose
        flag as `` `keel gc --scratch` ``, where no backtick sits immediately before
        `--scratch`, so the flag regex missed it whether or not the split ran. A
        counterweight that passes with the thing it guards removed is the #1289 class
        this very check exists to catch — found by the lead review, inside the check.
        The prose flag is now its own code span, which is the only shape that reaches
        the boundary."""
        for separator in ("\u2014", "\u2192"):
            with self.subTest(separator=separator):
                self.assertEqual(
                    _flags_in_bullet_head(
                        f"- `--dry-run` {separator} does nothing; pass `--scratch` to `keel gc`"
                    ),
                    {"--dry-run"},
                )

    def test_a_bullet_heading_several_alternatives_is_read_whole(self):
        """Round 1 finding. `- ``--jury`` / ``--no-jury`` / ``--jury-advisory`` — …`
        is one bullet documenting three flags; reading only the first left two of
        them unchecked, so deleting either from the hint stayed green."""
        flags = documented_flags("ship")
        for flag in ("--jury", "--no-jury", "--jury-advisory", "--compound", "--profile"):
            with self.subTest(flag=flag):
                self.assertIn(flag, flags)

    def test_every_documented_flag_is_declared_in_the_hint(self):
        for command in self.commands:
            hint = argument_hint(command)
            if hint is None:
                continue
            declared = set(_FLAG_RE.findall(hint))
            undeclared = sorted(documented_flags(command) - declared)
            with self.subTest(command=command):
                self.assertEqual(
                    undeclared,
                    [],
                    f"/keel:{command} documents {undeclared} in its body but its "
                    f"argument-hint omits them, so no host shows them:\n"
                    f"  hint: {hint}",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
