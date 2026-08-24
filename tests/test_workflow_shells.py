"""A multi-command workflow step must say which shell it runs in (#953).

GitHub's default shell differs by runner: bash with ``-e`` on Linux and macOS,
PowerShell on Windows. A PowerShell script's exit status is that of its **last**
statement, so in

    run: |
      coverage run -m unittest discover -s tests
      coverage report

a failing test run was masked by a succeeding coverage report, and
`test (py* / windows-latest)` reported **success** with six failures and one
error. It had been doing that long enough for seven real failures to accumulate.

This is the general form of that bug rather than a pin on the one step: any
multi-command block whose shell is left to the runner has the same asymmetry
waiting in it.
"""

from __future__ import annotations

import re
import unittest
from itertools import pairwise
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: `run: |` (or `>`), capturing the block's indentation.
_RUN_BLOCK = re.compile(r"^(?P<indent>\s*)(?:- )?run:\s*[|>][-+]?\s*$", re.MULTILINE)


def _step_sets_shell(text: str, block_start: int, indent: int) -> bool:
    """Whether the step containing the block at ``block_start`` declares a shell.

    Scans the step's own keys — the lines at the same indentation as ``run:``,
    in both directions, stopping at the next list item.
    """
    lines = text.splitlines(keepends=True)
    offsets, running = [], 0
    for line in lines:
        offsets.append(running)
        running += len(line)
    index = max(i for i, off in enumerate(offsets) if off <= block_start)

    for direction in (-1, 1):
        cursor = index + direction
        while 0 <= cursor < len(lines):
            line = lines[cursor]
            if not line.strip():
                cursor += direction
                continue
            current = len(line) - len(line.lstrip())
            stripped = line.lstrip()
            if current < indent or (direction == -1 and stripped.startswith("- ")
                                    and cursor != index):
                if stripped.startswith("- ") and current == indent - 2:
                    break
                if current < indent:
                    break
            if current == indent and stripped.startswith("shell:"):
                return True
            if direction == 1 and stripped.startswith("- name:"):
                break
            cursor += direction
    return False


def _commands(block_text: str) -> list[str]:
    """Top-level commands in a block: non-blank, non-comment, not a continuation."""
    out, continued = [], False
    for raw in block_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not continued:
            out.append(line)
        continued = line.endswith("\\")
    return out


class EveryMultiCommandStepDeclaresItsShell(unittest.TestCase):
    @staticmethod
    def _windows_capable_regions(text: str) -> list[tuple[int, int]]:
        """Spans of the jobs that can run on a Windows runner.

        Scoped to the actual hazard. A job pinned to `ubuntu-latest` gets bash
        with `-e` whatever it declares, so requiring `shell:` there would be
        ceremony — and 13 of the 18 blocks in this repo are exactly that.
        """
        lines = text.splitlines(keepends=True)
        starts, offset = [], 0
        for line in lines:
            if line.startswith("  ") and line.rstrip().endswith(":") and not line[2:3].isspace():
                starts.append(offset)
            offset += len(line)
        starts.append(len(text))

        regions = []
        for begin, end in pairwise(starts):
            if "windows-latest" in text[begin:end]:
                regions.append((begin, end))
        return regions

    def _offenders(self) -> list[str]:
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            regions = self._windows_capable_regions(text)
            for match in _RUN_BLOCK.finditer(text):
                if not any(b <= match.start() < e for b, e in regions):
                    continue
                indent = len(match.group("indent"))
                body, cursor = [], match.end()
                for line in text[cursor:].splitlines(keepends=True):
                    if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                        break
                    body.append(line)
                if len(_commands("".join(body))) < 2:
                    continue
                if _step_sets_shell(text, match.start(), indent):
                    continue
                line_no = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line_no}")
        return offenders

    def test_no_multi_command_block_leaves_the_shell_to_the_runner(self):
        self.assertEqual(
            [], self._offenders(),
            "a multi-command `run:` block does not set `shell:`. On Windows the "
            "default is PowerShell, where only the last command's exit code "
            "reaches the step — a failing command in any earlier line is "
            "silently ignored (#953)",
        )

    def test_the_sweep_finds_blocks_to_check(self):
        """Keeps the assertion above from passing on a tree it cannot parse."""
        blocks = sum(
            len(_RUN_BLOCK.findall(path.read_text(encoding="utf-8")))
            for path in WORKFLOWS.glob("*.yml")
        )

        self.assertGreater(blocks, 5, "expected the workflows' run: blocks")

    def test_the_ci_test_step_is_the_one_that_was_masked(self):
        """The specific regression, named — this is where the seven hid."""
        text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        step = text[text.index("- name: Test + coverage gate"):]
        step = step[: step.index("      - name:", 1)]

        self.assertIn("shell: bash", step)
        self.assertIn("coverage run -m unittest discover", step)


if __name__ == "__main__":
    unittest.main()
