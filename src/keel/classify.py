"""Risk classification — which tier a change is, from the files it touches.

Pure and deterministic: the tier is a function of the changed paths and the
project's globs, with no I/O. The tier drives the reviewer count (see
:func:`keel.ship.reviewer_count`).
"""

from __future__ import annotations

import fnmatch

#: Default tier when nothing else matches.
DEFAULT_TIER = 2


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

    # ⚡ Bolt Optimization: Use explicit unrolled loops instead of nested any() generators
    # to avoid the heavy overhead of generator instantiation during iteration hot paths.
    if tier3_globs:
        for p in changed:
            for g in tier3_globs:
                if fnmatch.fnmatch(p, g):
                    return 3

    if docs_globs:
        for p in changed:
            matches_doc = False
            for g in docs_globs:
                if fnmatch.fnmatch(p, g):
                    matches_doc = True
                    break
            if not matches_doc:
                return DEFAULT_TIER
        return 1

    return DEFAULT_TIER
