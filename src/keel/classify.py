"""Risk classification — which tier a change is, from the files it touches.

Pure and deterministic: the tier is a function of the changed paths and the
project's globs, with no I/O. The tier drives the reviewer count (see
:func:`keel.ship.reviewer_count`).
"""

from __future__ import annotations

import fnmatch

#: Default tier when nothing else matches.
DEFAULT_TIER = 2

#: Strictest tier — the fail-closed answer when the changed-file list could not be
#: read at all. An unreadable diff must never classify as the *default* tier: that
#: is the answer for "an empty changeset", and it silently drops a reviewer and the
#: gating jury on a change nobody has seen.
UNKNOWN_TIER = 3


def _matches_any(path: str, globs: tuple[str, ...]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
    return False


def is_docs_only(changed: list[str], docs_globs: tuple[str, ...]) -> bool:
    """Whether *every* changed path is a docs-surface path (and there is at least one).

    Asked directly rather than inferred from ``tier_for_files(...) == 1``, because the
    two questions have deliberately different answers: ``allowlist_globs`` may keep a
    change classified TIER-1 without making it docs-*only*. The CI empty-check-set
    carve-out needs this stricter question — a generated site file riding along with a
    docs edit is precisely the case where a workflow *should* have run.

    An empty list is not docs-only: an unreadable or empty changeset must fail closed.
    """
    return bool(changed) and all(_matches_any(p, docs_globs) for p in changed)


def tier_for_files(
    changed: list[str],
    *,
    tier3_globs: tuple[str, ...] = (),
    docs_globs: tuple[str, ...] = (),
    allowlist_globs: tuple[str, ...] = (),
) -> int:
    """Classify a change into TIER 1/2/3 from its changed files.

    * any file matching ``tier3_globs`` (migrations, CI, core code…) ⇒ **TIER-3**;
    * otherwise, if *every* changed file matches ``docs_globs`` or ``allowlist_globs``
      (docs-only) ⇒ **TIER-1**;
    * otherwise ⇒ **TIER-2** (the default). An empty changeset is TIER-2 (unknown).

    ``allowlist_globs`` (``knobs.docs_only_allowlist``) are paths permitted to ride along
    in a docs change without forcing code-risk classification — generated site output,
    metadata. They widen *this* judgement only: they are not a docs surface, so they do
    not relax scope-creep tolerance and they do not buy the empty-CI-check carve-out
    (see :func:`is_docs_only`).
    """
    if not changed:
        return DEFAULT_TIER

    if tier3_globs:
        for p in changed:
            if _matches_any(p, tier3_globs):
                return 3

    if docs_globs:
        for p in changed:
            if not (_matches_any(p, docs_globs) or _matches_any(p, allowlist_globs)):
                return DEFAULT_TIER
        return 1

    return DEFAULT_TIER
