"""The leaf-vocabulary invariants that keep the import cycle closed (#1050).

``keel.agents`` and ``keel.config`` import ``keel.team`` at module scope; before this
suite existed, ``keel.team`` and ``keel.config`` reached back for
``BUILTIN_DELEGATE_VENDORS`` / ``EFFORTS`` / ``supports_effort`` through *function-local*
imports, which kept the cycle open at import time while leaving it in the module graph —
benign at run time, four cyclic-import alerts to CodeQL, and a paragraph of explanation
to every reader.

:mod:`keel.vocab` owns those names now. Three things have to stay true for that to keep
paying off, and each is a test here:

1. :mod:`keel.vocab` imports nothing from keel — a single import added there re-opens
   the cycle from the other end.
2. No module reaches the vocabulary function-locally again. The whole point is that the
   names are importable at module scope; a future local import would silently restore
   the alert *and* the confusion.
3. The package's real module graph — every import, module-scope and function-local, the
   way a scanner reads it — has no cycle through ``keel.team`` at all.

Plus the boring but load-bearing one: the re-exports are the same objects, so
``agents.BUILTIN_DELEGATE_VENDORS`` and ``delegate.EFFORTS`` did not become copies that
can drift.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from keel import agents, api_delegate, delegate, team, vocab

SRC = Path(vocab.__file__).resolve().parent
PKG_ROOT = SRC.parent

#: The names that moved. A function-local import of any of them is the smell #1050 removed.
MOVED_NAMES = frozenset(
    {
        "API_VENDORS",
        "BUILTIN_DELEGATE_VENDORS",
        "CLI_VENDORS",
        "EFFORT_VENDORS",
        "EFFORTS",
        "LOCAL_VENDORS",
        "OPENAI_COMPATIBLE",
        "supports_effort",
    }
)

#: Modules of the keel package, by name.
MODULES = {path.stem: path for path in sorted(SRC.glob("*.py")) if path.stem != "__init__"}


def _imports(path: Path, *, module_scope_only: bool) -> set[str]:
    """Intra-package modules ``path`` imports, optionally counting module scope only."""
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if module_scope_only and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            if isinstance(child, ast.ImportFrom) and child.level == 1:
                if child.module is None:  # `from . import agents`
                    found.update(alias.name for alias in child.names if alias.name in MODULES)
                elif child.module.split(".")[0] in MODULES:
                    found.add(child.module.split(".")[0])
            elif isinstance(child, ast.Import):
                found.update(
                    alias.name.split(".")[1]
                    for alias in child.names
                    if alias.name.startswith("keel.")
                )
            visit(child)

    visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def _local_imports_of_moved_names(path: Path) -> list[str]:
    """``"<function>: <name>"`` for every function-local import of a moved name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Import, ast.ImportFrom)):
                for alias in inner.names:
                    name = alias.name.split(".")[-1]
                    if name in MOVED_NAMES:
                        offenders.append(f"{node.name}: {name}")
    return offenders


class VocabIsALeaf(unittest.TestCase):
    """`keel.vocab` is the leaf the cycle-free graph rests on."""

    def test_vocab_imports_nothing_from_keel(self) -> None:
        """An intra-package import here would re-open the cycle from the other end."""
        self.assertEqual(
            _imports(Path(vocab.__file__), module_scope_only=False),
            set(),
            "keel.vocab must stay dependency-free — it exists so keel.team, keel.config "
            "and the dispatch modules can share a vocabulary without importing each other",
        )

    def test_only_the_standard_future_import(self) -> None:
        """Not even a stdlib import is needed; the module is constants plus one predicate."""
        tree = ast.parse(Path(vocab.__file__).read_text(encoding="utf-8"))
        imported = [
            node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertEqual(
            [getattr(node, "module", None) for node in imported],
            ["__future__"],
        )


class NoFunctionLocalVocabularyImports(unittest.TestCase):
    """The names moved so the imports could come out of the functions — keep them out."""

    def test_team_has_no_function_local_imports_at_all(self) -> None:
        """#1050's acceptance criterion, worded exactly as the issue words it."""
        tree = ast.parse(Path(team.__file__).read_text(encoding="utf-8"))
        local: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local.extend(
                    node.name
                    for inner in ast.walk(node)
                    if isinstance(inner, (ast.Import, ast.ImportFrom))
                )
        self.assertEqual(local, [], "keel.team must import everything at module scope")

    def test_no_module_reaches_the_moved_names_locally(self) -> None:
        """A local import of a moved name is the alert coming back, module by module."""
        offenders = {
            name: found
            for name, path in MODULES.items()
            if (found := _local_imports_of_moved_names(path))
        }
        self.assertEqual(
            offenders,
            {},
            "import these from keel.vocab at module scope — that is what it is for",
        )


class TheGraphHasNoCycleThroughTeam(unittest.TestCase):
    """The property the CodeQL alerts were about, asserted on the real module graph."""

    def _graph(self, *, module_scope_only: bool) -> dict[str, set[str]]:
        return {
            name: _imports(path, module_scope_only=module_scope_only)
            for name, path in MODULES.items()
        }

    def _cycles_through(self, graph: dict[str, set[str]], target: str) -> list[tuple[str, ...]]:
        found: set[tuple[str, ...]] = set()

        def walk(node: str, path: tuple[str, ...], seen: frozenset[str]) -> None:
            for nxt in sorted(graph.get(node, ())):
                if nxt == target:
                    found.add(path + (nxt,))
                elif nxt not in seen and len(path) < 6:
                    walk(nxt, path + (nxt,), seen | {nxt})

        walk(target, (target,), frozenset({target}))
        return sorted(found)

    def test_no_cycle_through_team_counting_every_import(self) -> None:
        """Function-local imports included: this is the graph a scanner builds."""
        self.assertEqual(
            self._cycles_through(self._graph(module_scope_only=False), "team"),
            [],
            "keel.team is a consumer of keel.vocab and nothing else in the package; "
            "an edge out of keel.team is what re-opens CodeQL alerts 58-61",
        )

    def test_team_imports_only_vocab(self) -> None:
        """Named positively, so the failure message says what the rule is."""
        self.assertEqual(_imports(Path(team.__file__), module_scope_only=False), {"vocab"})

    def test_no_cycle_through_config_or_agents_either(self) -> None:
        """The same lift removed ``keel.config`` -> ``keel.agents`` (the #1050 sibling)."""
        graph = self._graph(module_scope_only=False)
        self.assertNotIn("agents", graph["config"])
        self.assertNotIn("delegate", graph["wizard"])


class ImportingTeamFirstPullsNothingElse(unittest.TestCase):
    """A fresh interpreter that imports ``keel.team`` first must not drag keel in."""

    def test_fresh_interpreter_imports_team_alone(self) -> None:
        script = (
            "import sys, json, keel.team;"
            "print(json.dumps(sorted(m for m in sys.modules if m.startswith('keel.'))))"
        )
        env = dict(os.environ, PYTHONPATH=str(PKG_ROOT))
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        self.assertEqual(
            json.loads(result.stdout.strip().splitlines()[-1]),
            ["keel.team", "keel.vocab"],
            f"importing keel.team pulled more than the leaf: {result.stdout!r}",
        )


class TheReExportsAreTheSameObjects(unittest.TestCase):
    """A move, not a copy: the old spellings still name the one definition."""

    def test_agents_re_exports_the_vendor_tuples(self) -> None:
        self.assertIs(agents.BUILTIN_DELEGATE_VENDORS, vocab.BUILTIN_DELEGATE_VENDORS)
        self.assertIs(agents.CLI_VENDORS, vocab.CLI_VENDORS)
        self.assertIs(agents.LOCAL_VENDORS, vocab.LOCAL_VENDORS)
        self.assertIs(agents.API_VENDORS, vocab.API_VENDORS)

    def test_delegate_re_exports_the_effort_vocabulary(self) -> None:
        self.assertIs(delegate.EFFORTS, vocab.EFFORTS)
        self.assertIs(delegate.EFFORT_VENDORS, vocab.EFFORT_VENDORS)
        self.assertIs(delegate.supports_effort, vocab.supports_effort)

    def test_api_delegate_re_exports_the_openai_compatible_vendor(self) -> None:
        self.assertIs(api_delegate.OPENAI_COMPATIBLE, vocab.OPENAI_COMPATIBLE)

    def test_the_vendor_tuples_still_compose_the_builtin_set(self) -> None:
        self.assertEqual(
            vocab.BUILTIN_DELEGATE_VENDORS,
            vocab.CLI_VENDORS + vocab.LOCAL_VENDORS + vocab.API_VENDORS,
        )

    def test_supports_effort_answers_both_ways(self) -> None:
        """Covers the predicate here too, so the leaf does not lean on another suite."""
        for vendor in vocab.EFFORT_VENDORS:
            self.assertTrue(vocab.supports_effort(vendor), vendor)
        self.assertFalse(vocab.supports_effort("claude"))
        self.assertFalse(vocab.supports_effort("ollama"))


if __name__ == "__main__":
    unittest.main()
