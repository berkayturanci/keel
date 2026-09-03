"""Adapter prose describes *policy*; `keel delegate run` owns the *argv* (#1012).

The `claude`/`codex`/`agy` argv shapes, the stdin framing, the Ollama endpoint and the
JSON return contract lived only as prose in `ship.md` s4/s7. Prose cannot be executed, so
every host agent re-implemented it and the copies drifted: a live run on 2026-09-03 wrote
its own argv, its own response parsing and its own return contract, recorded a vendor keel
does not use, and lost three reviewers' verdicts.

Moving the mechanics into core fixes that only for as long as the prose stays out of the
business. This sweep is what keeps it out: a vendor flag re-added to an adapter file is a
second source of truth the day it lands, and a second source of truth is the bug.

Both surfaces are swept — the adapter sources under ``src/keel/adapters/commands/`` and
every generated copy (``commands/``, ``.claude/commands/keel/``, ``.agents/skills/``) —
because the generated files are what an agent actually reads.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every file an agent could read a dispatch instruction out of.
_SURFACES = (
    "src/keel/adapters/commands/*.md",
    "commands/*.md",
    ".claude/commands/keel/*.md",
    ".agents/skills/keel-*/SKILL.md",
)

#: Vendor invocation fragments that must never reappear. Each was in the prose before
#: #1012 and is now a property of :mod:`keel.delegate`, asserted in ``tests/test_delegate``.
_FORBIDDEN = {
    "codex exec": "the codex argv belongs to keel.delegate, not to adapter prose",
    "agy --print": "the agy invocation belongs to keel.delegate, not to adapter prose",
    "agy --": "the agy argv belongs to keel.delegate, not to adapter prose",
    "claude -p": "the claude argv belongs to keel.delegate, not to adapter prose",
    "--dangerously-skip-permissions": "a permission flag is core's to choose, not prose's",
    "--disallowed-tools": "the read-only tool list is core's, not prose's",
    "--allowed-tools": "the read-only tool list is core's, not prose's",
    "model_reasoning_effort": "codex's effort spelling is core's, not prose's",
    "-s read-only": "the codex sandbox flag is core's, not prose's",
    "-s workspace-write": "the codex sandbox flag is core's, not prose's",
    "--input-format stream-json": "agy's stdin framing is core's, not prose's",
    "/api/generate": "the Ollama endpoint is a hardcoded constant in core, not prose",
    "/api/tags": "the Ollama endpoint is a hardcoded constant in core, not prose",
    "prompt_mode": "how a prompt is delivered is core's decision, not prose's",
    "role_args(": "role flag selection is core's, not prose's",
}

#: The command every adapter must route dispatch through, and the files that must say so.
_DISPATCH_COMMAND = "keel delegate run"
_MUST_DISPATCH = ("ship.md", "implement.md", "review-cycle.md")

#: What an orchestrator must be told to use instead of polling.
_WAIT_COMMAND = "keel delegate wait"

#: Prose that contradicts the code. The resolution order was inverted in four places at
#: once — the adapter source, its three generated copies, `providers.plan_probes` and
#: `configuration.md` — because each restated it in its own words rather than pointing at
#: one place. An operator who reads any of them and lets a registry entry shadow `claude`
#: gets a `keel doctor --providers` error they were told to expect to work.
_STALE_PRECEDENCE = re.compile(r"profile\s*>\s*(?:machine\s+)?registry\s*>\s*built-in")

#: Fields of the return contract an adapter must actually name, because reading the wrong
#: one is a silent failure rather than an error. `read_only` alone reports the role that
#: was *asked for*; only `read_only_backed` says whether anything enforces it.
_MUST_NAME = ("read_only_backed",)

#: Sleep-and-poll advice: a loop in the host's own turn cannot outlive the turn.
_SLEEP_ADVICE = re.compile(r"\bsleep\s+\d|\bsleep loop\b|poll(ing)? (in a )?loop", re.IGNORECASE)


def _adapter_files():
    for pattern in _SURFACES:
        yield from sorted(REPO_ROOT.glob(pattern))


class AdapterProseCarriesNoVendorArgv(unittest.TestCase):
    def test_the_sweep_has_files_to_sweep(self):
        """Keeps every rule below from passing on a tree it cannot read."""
        files = list(_adapter_files())
        self.assertGreaterEqual(len(files), 60, f"expected the adapter surfaces, found {files}")

    def test_no_adapter_file_spells_a_vendor_invocation(self):
        offenders = []
        for path in _adapter_files():
            text = path.read_text(encoding="utf-8")
            for fragment, why in _FORBIDDEN.items():
                if fragment in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {fragment!r} — {why}")
        self.assertEqual([], offenders, "\n".join(offenders))

    def test_the_dispatching_adapters_route_through_the_command(self):
        for name in _MUST_DISPATCH:
            for path in _adapter_files():
                if path.name == name or path.parent.name.endswith(name[:-3]):
                    with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                        self.assertIn(_DISPATCH_COMMAND, path.read_text(encoding="utf-8"))

    def test_no_adapter_states_the_inverted_resolution_order(self):
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _adapter_files()
            if _STALE_PRECEDENCE.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders, "resolution order is built-in > profile > registry")

    def test_a_dispatching_adapter_names_the_field_that_gates_a_reviewer(self):
        for name in _MUST_DISPATCH:
            for path in _adapter_files():
                if path.name == name or path.parent.name.endswith(name[:-3]):
                    text = path.read_text(encoding="utf-8")
                    if _DISPATCH_COMMAND not in text:
                        continue
                    for field in _MUST_NAME:
                        with self.subTest(path=str(path.relative_to(REPO_ROOT)), field=field):
                            self.assertIn(field, text)

    def test_every_detach_example_bounds_the_run(self):
        """A detached run with no `--timeout` has no deadline, so a killed child is
        indistinguishable from a slow one and `wait` can block forever."""
        for path in _adapter_files():
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if "--detach" not in line:
                    continue
                with self.subTest(path=str(path.relative_to(REPO_ROOT)), line=line.strip()):
                    # the flag may sit on a continuation line of the same example
                    block = text[max(0, text.index(line) - 400) : text.index(line) + 400]
                    self.assertIn("--timeout", block)

    def test_the_detach_primitive_replaces_sleep_polling_advice(self):
        for path in _adapter_files():
            text = path.read_text(encoding="utf-8")
            match = _SLEEP_ADVICE.search(text)
            if match is None:
                continue
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                # A file may only mention polling in order to forbid it, and only where
                # it also names the primitive that replaces it.
                self.assertIn(_WAIT_COMMAND, text, f"{match.group(0)!r} with no wait primitive")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
