"""Risk classification — which tier a change is, from the files it touches.

Pure and deterministic: the tier is a function of the changed paths and the
project's globs, with no I/O. The tier drives the reviewer count (see
:func:`keel.ship.reviewer_count`).
"""

from __future__ import annotations

import fnmatch

#: Default tier when nothing else matches.
DEFAULT_TIER = 2


def _matches_any(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def tier_for_files(
    changed: list[str], *, tier3_globs: tuple[str, ...] = (), docs_globs: tuple[str, ...] = ()
) -> int:
    """Classify a change into TIER 1/2/3 from its changed files.

    * any file matching ``tier3_globs`` (migrations, CI, core code…) ⇒ **TIER-3**;
    * otherwise, if *every* changed file matches ``docs_globs`` (docs-only) ⇒ **TIER-1**;
    * otherwise ⇒ **TIER-2** (the default). An empty changeset is TIER-2 (unknown).
    """
    if not changed:
        return DEFAULT_TIER
    if any(_matches_any(p, tier3_globs) for p in changed):
        return 3
    if docs_globs and all(_matches_any(p, docs_globs) for p in changed):
        return 1
    return DEFAULT_TIER
