"""A re-exporting module declares its public surface, and the declaration stays true (#1070).

Four modules carry ``X as X`` re-exports — a name imported so that *other* modules read it
from there: ``agents`` republishes the vendor vocabulary, ``delegate`` the effort
vocabulary, ``api_delegate`` ``OPENAI_COMPATIBLE``, and ``juryavail`` the three jury names
``keel.team`` has to own because the import runs the other way. Every one has readers.
``ruff``'s F401 honours the ``X as X`` spelling as a statement of intent; CodeQL's
``py/unused-import`` counts same-module uses only and reported five of them as unused.

``__all__`` is the language's own answer to that question, and a name listed in it is used
by definition — but only if the list is honest. A ``__all__`` holding the re-exports alone
would be a *smaller* lie than no ``__all__`` at all: ``from keel.delegate import *`` and
every documentation tool would read it as "these three names are the module", silently
hiding the twenty-six others. So the declaration is held to the whole surface, in both
directions:

* every ``X as X`` re-export is listed — a future re-export cannot be added without being
  declared, which is the drift that opened #1070;
* every top-level public definition is listed — so the list cannot rot into a partial
  statement as the module grows;
* every listed name really resolves on the imported module — a rename that misses
  ``__all__`` fails here rather than at some importer's ``ImportError``.

A module may also list a plainly-imported name it means to publish: ``delegate.EFFORTS``
is read by ``keel.cli`` and by ``tests/test_delegate.py``, so it belongs in the surface
even though it is used in-module too and never needed the ``X as X`` spelling.

The scan is discovery-driven — it finds the re-exporting modules by parsing the package —
so a fifth one joins the guard by existing, not by being remembered here.
"""

from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

import keel

SRC = Path(keel.__file__).resolve().parent

#: The modules that carry the idiom today. The guard discovers its subjects, but a scan
#: that silently found *nothing* would pass every assertion below, so the discovery has to
#: keep finding at least these.
KNOWN_REEXPORTERS = frozenset({"agents", "api_delegate", "delegate", "juryavail"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _reexports(tree: ast.Module) -> set[str]:
    """The ``X as X`` names — an import whose alias repeats the imported name."""
    return {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if alias.asname is not None and alias.asname == alias.name
    }


def _public_definitions(tree: ast.Module) -> set[str]:
    """Public names the module *defines* at top level: classes, functions, constants."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")} - {"__all__"}


def _declared_all(tree: ast.Module) -> list[str] | None:
    """The literal ``__all__`` list, or ``None`` when the module declares none."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple)):
                return [e.value for e in value.elts if isinstance(e, ast.Constant)]
    return None


def _reexporting_modules() -> dict[str, ast.Module]:
    trees = {
        path.stem: _parse(path) for path in sorted(SRC.glob("*.py")) if path.stem != "__init__"
    }
    return {stem: tree for stem, tree in trees.items() if _reexports(tree)}


class ReexportSurfaceTest(unittest.TestCase):
    """``__all__`` is present, complete, and resolvable wherever the idiom is used."""

    def setUp(self) -> None:
        self.modules = _reexporting_modules()

    def test_the_scan_still_finds_the_modules_it_guards(self):
        self.assertLessEqual(KNOWN_REEXPORTERS, set(self.modules))

    def test_every_reexporting_module_declares_all(self):
        for stem, tree in self.modules.items():
            with self.subTest(module=stem):
                self.assertIsNotNone(
                    _declared_all(tree),
                    f"keel.{stem} re-exports {sorted(_reexports(tree))} but declares no "
                    "__all__; CodeQL's py/unused-import reports an undeclared re-export "
                    "as unused (#1070)",
                )

    def test_every_reexport_is_declared(self):
        for stem, tree in self.modules.items():
            declared = set(_declared_all(tree) or ())
            with self.subTest(module=stem):
                self.assertLessEqual(_reexports(tree), declared)

    def test_all_covers_every_public_definition(self):
        for stem, tree in self.modules.items():
            declared = set(_declared_all(tree) or ())
            with self.subTest(module=stem):
                self.assertLessEqual(
                    _public_definitions(tree),
                    declared,
                    f"keel.{stem}.__all__ omits a public name it defines; a partial "
                    "__all__ hides the rest of the surface from `import *` and from "
                    "documentation tools",
                )

    def test_every_declared_name_resolves_on_the_module(self):
        for stem, tree in self.modules.items():
            module = importlib.import_module(f"keel.{stem}")
            for name in _declared_all(tree) or ():
                with self.subTest(module=stem, name=name):
                    self.assertTrue(hasattr(module, name))

    def test_no_declaration_repeats_a_name(self):
        for stem, tree in self.modules.items():
            declared = _declared_all(tree) or []
            with self.subTest(module=stem):
                self.assertEqual(len(declared), len(set(declared)))

    def test_no_declaration_publishes_a_private_name(self):
        for stem, tree in self.modules.items():
            declared = _declared_all(tree) or []
            with self.subTest(module=stem):
                self.assertEqual([], [n for n in declared if n.startswith("_")])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
