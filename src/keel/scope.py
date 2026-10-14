"""Branch-scope verification — declared files vs. the observed PR diff.

keel's adapter prose calls the implementer's declared-files-vs-actual-diff
comparison "the primary defence against branch contamination". This module makes
that defence enforceable: given the implementer's *declared* file set, the live
PR diff's changed files, and the project's docs-gate globs, it computes which
diff files fall outside the declared scope ("scope creep") and returns a verdict.

Pure and deterministic: every input is data and there is no I/O, so the verdict
is a function of its arguments alone (the cli loads the ledger record and the
live diff). Docs-path matching reuses the same glob matcher as risk
classification so docs-only extras are exempt rather than flagged.
"""

from __future__ import annotations

import fnmatch
import posixpath
from typing import Any


def _matches_any(path: str, globs: tuple[str, ...]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
    return False


SCHEMA_VERSION = "keel.scope-verify.v1"


def verify(
    declared_files: list[str] | None,
    actual_files: list[str],
    *,
    docs_globs: tuple[str, ...] = (),
    deferrals: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Compare ``declared_files`` against ``actual_files`` and return a verdict.

    * ``declared_files`` is the implementer's recorded scope contract, or
      ``None`` when no scope was recorded. With ``None`` the result is an
      advisory pass carrying a ``no-declared-scope`` note — back-compat so
      existing flows that never recorded a scope are never broken.
    * Files in ``actual_files`` not present in ``declared_files`` are scope
      creep, *unless* they match ``docs_globs`` (docs extras are allowed) or the
      operator has waived scope via a ``scope-waived`` deferral.

    The returned report lists the in-scope files and the creep files and sets a
    ``pass``/``fail`` status. ``waived`` and ``advisory`` flags explain a pass
    that carried creep or had no declared scope.
    """
    waived = "scope-waived" in deferrals or "all" in deferrals
    if declared_files is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "pass",
            "advisory": True,
            "waived": waived,
            "note": "no declared scope recorded",
            "declared": None,
            "in_scope": [],
            "scope_creep": [],
            "docs_exempt": [],
        }
    declared = set(declared_files)
    in_scope: list[str] = []
    creep: list[str] = []
    docs_exempt: list[str] = []
    for path in actual_files:
        if path in declared:
            in_scope.append(path)
        elif docs_globs and _matches_any(path, docs_globs):
            docs_exempt.append(path)
        else:
            creep.append(path)
    blocking = bool(creep) and not waived
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "fail" if blocking else "pass",
        "advisory": False,
        "waived": waived,
        "note": ("scope creep waived by operator deferral" if creep and waived else None),
        "declared": sorted(declared),
        "in_scope": in_scope,
        "scope_creep": creep,
        "docs_exempt": docs_exempt,
    }


def _normalize_repo_path(path: str, root: str | None = None) -> str:
    norm = posixpath.normpath(path.replace("\\", "/"))
    if root:
        root_norm = posixpath.normpath(root.replace("\\", "/")).rstrip("/")
        if norm == root_norm:
            return "."
        elif norm.startswith(root_norm + "/"):
            norm = norm[len(root_norm) + 1 :]
    return norm


def _is_within_expected(path: str, expected: set[str]) -> bool:
    if path in expected or "." in expected:
        return True
    return any(path.startswith(exp + "/") for exp in expected)


def check_staged_contamination(
    staged_modified_or_deleted: list[str] | None,
    *,
    implementer_files: list[str] | None = None,
    files_since_start: list[str] | None = None,
    since_unreadable: bool = False,
    root: str | None = None,
) -> dict[str, Any]:
    """Check whether staged modified or deleted files include paths outside the implementer run.

    Compares against ``implementer_files`` when available, else against a non-empty
    committed-history ``files_since_start`` baseline. When ``since_unreadable`` is true or
    ``files_since_start`` is an empty list (no commits since ``since``), surfaces an
    explicit warning rather than silently reporting clean — pass ``implementer_files`` to
    check uncommitted work. When a baseline is present and ``staged_modified_or_deleted``
    is ``None`` (unreadable git index), surfaces a warning. Warns, never blocks.
    """
    if implementer_files is not None:
        expected = {_normalize_repo_path(p, root) for p in implementer_files}
        source = "implementer-files"
    elif since_unreadable:
        return {
            "has_contamination": False,
            "suspect_paths": [],
            "warning": (
                "could not resolve --since baseline from git history "
                "to check for staged contamination"
            ),
            "comparison_source": "files-since-start",
        }
    elif files_since_start is not None:
        if not files_since_start:
            return {
                "has_contamination": False,
                "suspect_paths": [],
                "warning": (
                    "no committed files found since --since baseline; "
                    "pass --implementer-file to check uncommitted work"
                ),
                "comparison_source": "files-since-start",
            }
        expected = {_normalize_repo_path(p, root) for p in files_since_start}
        source = "files-since-start"
    else:
        return {
            "has_contamination": False,
            "suspect_paths": [],
            "warning": None,
            "comparison_source": "none",
        }

    if staged_modified_or_deleted is None:
        return {
            "has_contamination": False,
            "suspect_paths": [],
            "warning": "could not read staged files from git index to check for contamination",
            "comparison_source": source,
        }

    normalized_staged = [_normalize_repo_path(p, root) for p in staged_modified_or_deleted]
    suspect = [path for path in normalized_staged if not _is_within_expected(path, expected)]
    if suspect:
        sorted_suspect = sorted(set(suspect))
        warning = (
            "staged change set includes deleted or modified paths not touched by the "
            f"implementer run: {', '.join(sorted_suspect)}"
        )
        return {
            "has_contamination": True,
            "suspect_paths": sorted_suspect,
            "warning": warning,
            "comparison_source": source,
        }
    return {
        "has_contamination": False,
        "suspect_paths": [],
        "warning": None,
        "comparison_source": source,
    }
