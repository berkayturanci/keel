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


def site_examples() -> dict[str, str]:
    """The `cmdExample` map as {command: example invocation}."""
    block = _BLOCK_RE.search(CONTENT_JS.read_text(encoding="utf-8"))
    if block is None:  # pragma: no cover - guarded by the vacuity test below
        return {}
    return dict(_ENTRY_RE.findall(block.group(1)))


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


if __name__ == "__main__":
    unittest.main()
