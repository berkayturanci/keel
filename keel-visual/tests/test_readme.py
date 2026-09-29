"""keel-visual's README, held to the parser and the flows it describes (docs audit 2026-09-29)."""

import re
import unittest
from pathlib import Path

from keel import flows

from keel_visual import cli

README = Path(__file__).resolve().parents[1] / "README.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


class TheScopeLimitNamesEveryAllFlag(unittest.TestCase):
    """The README's first scope limit said keel-visual "does **not** aggregate across
    separate repos", thirty lines above the `dash --all` / `render --all` / `serve --all`
    sections that do."""

    def test_the_bullet_names_each_subcommand_that_takes_all(self):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if a.dest == "cmd")
        with_all = {name for name, p in sub.choices.items() if "--all" in p._option_string_actions}
        self.assertEqual({"dash", "render", "serve"}, with_all, "fixture: the --all surface moved")

        text = README.read_text(encoding="utf-8")
        bullet = _flat(
            text.split("Two scope limits follow", 1)[1].split("\n- **Same machine", 1)[0]
        )
        self.assertNotIn("does **not** aggregate", bullet)
        named = set(re.findall(r"`(\w+) --all`", bullet))
        self.assertEqual(with_all, named)


class TheFlowExamplesAreTheFlows(unittest.TestCase):
    """The README showed `triage` without its first phase, `config`."""

    def test_every_quoted_flow_is_that_commands_phases(self):
        text = _flat(README.read_text(encoding="utf-8"))
        quoted = re.findall(r"`([a-z-]+)` shows `([^`]+)`", text)
        self.assertIn("triage", dict(quoted), "fixture: the triage example moved")
        for command, shown in quoted:
            with self.subTest(command=command):
                phases = [p.name for p in flows.flow_for(command)]
                self.assertEqual(phases, [s.strip() for s in shown.split("→")])


if __name__ == "__main__":
    unittest.main()
