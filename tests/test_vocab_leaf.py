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
2. No module reaches the vocabulary function-locally again — whether it imports a moved
   name directly or imports a module and reads the name off it, which is the same edge
   wearing a different spelling. The whole point is that the names are importable at
   module scope; a future local reach would silently restore the alert *and* the
   confusion.
3. The package's real module graph — every import, module-scope and function-local, the
   way a scanner reads it — has no cycle through ``keel.team`` at all, of *any* length.
   The search is exhaustive on purpose: a depth bound would let a cycle one edge longer
   than the bound report clean, which is the failure mode a guard exists to prevent.

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

#: Modules of the keel package, by name. ``keel`` is flat today; a test below asserts the
#: glob really covers every ``.py`` under ``src/keel`` so a future subpackage cannot hide.
MODULES = {path.stem: path for path in sorted(SRC.glob("*.py")) if path.stem != "__init__"}


def _dotted(node: ast.AST) -> str | None:
    """``"keel.vocab.EFFORTS"`` for an attribute chain rooted at a plain name, else ``None``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _imports_in(source: str, *, module_scope_only: bool) -> set[str]:
    """Intra-package modules ``source`` imports, optionally counting module scope only.

    Every spelling that makes an edge counts, not only the ones this package happens to
    use today: ``from . import x``, ``from .x import y``, ``from keel import x``,
    ``import keel.x`` — and a bare ``import keel`` followed by a ``keel.x`` attribute
    read, which is an edge no import *name* ever spells out.
    """
    tree = ast.parse(source)
    found: set[str] = set()
    #: Names bound to the *package* (``import keel`` / ``import keel as k``), whose
    #: attribute chains resolve to sibling modules.
    package_bindings = {
        alias.asname or "keel"
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "keel"
    }

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
            elif isinstance(child, ast.ImportFrom) and child.module == "keel":
                found.update(alias.name for alias in child.names if alias.name in MODULES)
            elif isinstance(child, ast.Import):
                found.update(
                    parts[1]
                    for alias in child.names
                    if (parts := alias.name.split("."))[0] == "keel"
                    and len(parts) > 1
                    and parts[1] in MODULES
                )
            elif (
                isinstance(child, ast.Attribute)
                and child.attr in MODULES
                and isinstance(child.value, ast.Name)
                and child.value.id in package_bindings
            ):
                found.add(child.attr)
            visit(child)

    visit(tree)
    return found


def _imports(path: Path, *, module_scope_only: bool) -> set[str]:
    """:func:`_imports_in` for a file on disk."""
    return _imports_in(path.read_text(encoding="utf-8"), module_scope_only=module_scope_only)


def _local_vocabulary_reaches(source: str) -> list[str]:
    """``"<function>: <what>"`` for every function-local reach for a moved name.

    Two shapes put the vocabulary back inside a function body and both count. The loud
    one imports a moved name directly (``from .vocab import EFFORTS``). The quiet one
    imports a *module* and reads the name off it (``from . import vocab`` …
    ``vocab.EFFORTS``, or ``import keel.vocab`` … ``keel.vocab.EFFORTS``) — matching on
    the imported name alone never sees that, and it restores exactly the same edge.
    """
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound: set[str] = set()
        for inner in ast.walk(node):
            if not isinstance(inner, (ast.Import, ast.ImportFrom)):
                continue
            for alias in inner.names:
                name = alias.name.split(".")[-1]
                if name in MOVED_NAMES:
                    offenders.append(f"{node.name}: {name}")
                bound.add(alias.asname or alias.name.split(".")[0])
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Attribute) or inner.attr not in MOVED_NAMES:
                continue
            dotted = _dotted(inner)
            if dotted is not None and dotted.split(".")[0] in bound:
                offenders.append(f"{node.name}: {dotted}")
    return sorted(offenders)


def _local_imports_of_moved_names(path: Path) -> list[str]:
    """:func:`_local_vocabulary_reaches` for a file on disk."""
    return _local_vocabulary_reaches(path.read_text(encoding="utf-8"))


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


class TheLocalVocabularyCheckSeesModuleAttributeReads(unittest.TestCase):
    """Matching the imported *name* only catches the loud half of the smell.

    ``from .vocab import EFFORTS`` inside a function is the shape everyone pictures.
    ``from . import vocab`` inside a function and then ``vocab.EFFORTS`` restores the
    identical edge without ever spelling a moved name in an import, so the check has to
    follow the binding, not just read the alias list.
    """

    def test_a_direct_local_import_is_caught(self) -> None:
        for source in (
            "def f():\n    from .vocab import EFFORTS\n",
            "def f():\n    from .vocab import EFFORTS as E\n",
            "async def f():\n    from keel.vocab import EFFORTS\n",
        ):
            with self.subTest(source=source.replace("\n", " ")):
                self.assertEqual(_local_vocabulary_reaches(source), ["f: EFFORTS"])

    def test_a_module_import_read_off_the_module_is_caught(self) -> None:
        """The reviewer's hole: the import names a module, the read names the vocabulary."""
        cases = {
            "def f():\n    from . import vocab\n    return vocab.EFFORTS\n": "f: vocab.EFFORTS",
            "def f():\n    from . import vocab as v\n    return v.EFFORTS\n": "f: v.EFFORTS",
            "def f():\n    import keel.vocab\n    return keel.vocab.EFFORTS\n": (
                "f: keel.vocab.EFFORTS"
            ),
            "def f():\n    from . import delegate\n    return delegate.supports_effort('x')\n": (
                "f: delegate.supports_effort"
            ),
        }
        for source, expected in cases.items():
            with self.subTest(source=source.replace("\n", " ")):
                self.assertEqual(_local_vocabulary_reaches(source), [expected])

    def test_module_scope_imports_are_left_alone(self) -> None:
        """The point of the move was to make exactly this shape legal — do not flag it."""
        for source in (
            "from . import vocab\n\n\ndef f():\n    return vocab.EFFORTS\n",
            "from .vocab import EFFORTS\n\n\ndef f():\n    return EFFORTS\n",
            "def f():\n    from . import evidence\n    return evidence.render()\n",
            "def f():\n    return some_other.EFFORTS\n",
        ):
            with self.subTest(source=source.replace("\n", " ")):
                self.assertEqual(_local_vocabulary_reaches(source), [])


class TheGraphHasNoCycleThroughTeam(unittest.TestCase):
    """The property the CodeQL alerts were about, asserted on the real module graph."""

    def _graph(self, *, module_scope_only: bool) -> dict[str, set[str]]:
        return {
            name: _imports(path, module_scope_only=module_scope_only)
            for name, path in MODULES.items()
        }

    def _cycles_through(self, graph: dict[str, set[str]], target: str) -> list[tuple[str, ...]]:
        """Every simple cycle through ``target``, of any length.

        Deliberately unbounded: ``seen`` already keeps each path simple, so the search
        terminates, and the package is small enough that enumerating the simple paths out
        of one module costs nothing. A depth bound here would be worse than no test —
        a cycle one edge longer than the bound would report clean.
        """
        found: set[tuple[str, ...]] = set()

        def walk(node: str, path: tuple[str, ...], seen: frozenset[str]) -> None:
            for nxt in sorted(graph.get(node, ())):
                if nxt == target:
                    found.add(path + (nxt,))
                elif nxt not in seen:
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

    def test_the_search_finds_a_cycle_of_any_length(self) -> None:
        """A depth-bounded search is a guard-shaped test: one edge past the bound, silence.

        This walks the bound past where any bound would have sat, so a reintroduced
        bound fails here rather than passing quietly on the real graph.
        """
        for hops in range(1, 13):
            chain = ["team", *(f"m{i}" for i in range(1, hops + 1)), "team"]
            graph = {a: {b} for a, b in zip(chain, chain[1:], strict=False)}
            with self.subTest(intermediates=hops):
                self.assertEqual(self._cycles_through(graph, "team"), [tuple(chain)])

    def test_the_search_finds_a_long_cycle_hidden_among_dead_ends(self) -> None:
        """The long way round is found even when shorter branches leave the target alone."""
        graph: dict[str, set[str]] = {
            "team": {"vocab", "a1"},
            "vocab": set(),
            **{f"a{i}": {f"a{i + 1}", "vocab"} for i in range(1, 8)},
        }
        graph["a8"] = {"team"}
        self.assertEqual(
            self._cycles_through(graph, "team"),
            [("team", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "team")],
        )

    def test_the_graph_sees_a_bare_package_import(self) -> None:
        """``import keel`` names no module; the attribute read is still the edge."""
        for source in (
            "import keel\nX = keel.team.ROLES\n",
            "import keel as k\nX = k.team.ROLES\n",
            "import keel.team\n",
            "from keel import team\n",
            "from . import team\n",
            "from .team import ROLES\n",
        ):
            with self.subTest(source=source.replace("\n", " ")):
                self.assertEqual(_imports_in(source, module_scope_only=False), {"team"})

    def test_the_graph_ignores_look_alike_names(self) -> None:
        """No edge from an unrelated ``team`` attribute or a third-party ``keel``-ish import."""
        for source in ("import os\nX = os.team\n", "obj = object()\nX = obj.team\n"):
            with self.subTest(source=source.replace("\n", " ")):
                self.assertEqual(_imports_in(source, module_scope_only=False), set())

    def test_module_map_covers_every_file_in_the_package(self) -> None:
        """A future subpackage must not be invisible to the graph because of a flat glob."""
        self.assertEqual(
            sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")),
            sorted([*(f"{name}.py" for name in MODULES), "__init__.py"]),
            "keel is flat; a nested module would need MODULES to walk the tree",
        )


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
