"""Risk classification — which tier a change is, from the files it touches.

Pure and deterministic: the tier is a function of the changed paths and the
project's globs, with no I/O. The tier drives the reviewer count (see
:func:`keel.ship.reviewer_count`).
"""

from __future__ import annotations

import fnmatch
import re

#: Default tier when nothing else matches.
DEFAULT_TIER = 2

#: Paths where a *diff* may lower the tier a path alone would set (#794).
#:
#: Only workflow YAML. For the other tier-3 paths the content **is** the risk — a
#: checksum in ``Formula/keel.rb``, a pin in ``.github/requirements`` — so there is
#: no such thing as a cosmetic change there and every edit stays TIER-3.
DIFF_CLASSIFIED_GLOBS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
)

#: What makes a workflow diff privileged, regardless of how small it is.
#:
#: ``.github/workflows/**`` used to be TIER-3 wholesale, which tiered up a comment,
#: two added CI jobs and a change that *tightened* four action pins — all waived —
#: while leaving the formula ``brew install`` runs at TIER-2 (#786). Splitting the
#: glob by write permissions fixed three of those four; the fourth stays because a
#: path cannot say what was done to the file. These patterns can.
_PRIVILEGED_LINE = re.compile(
    r"""
      \buses\s*:                     # a third-party action: a pin swap is the attack
    | \bsecrets\s*\.                 # reading a stored credential
    | ^\s*permissions\s*:            # granting or widening a token scope
    | ^\s*[\w-]+\s*:\s*write\b       # a scope inside a permissions block
    | ^\s*on\s*:                     # the trigger surface (pull_request_target…)
    | \b(?:curl|wget|nc|ssh|scp)\b   # a run: step reaching the network
    | \bpip\s+install\b
    | \bnpm\s+(?:i|install|ci)\b
    | \bgh\s+(?:api|auth)\b
    """,
    re.VERBOSE | re.MULTILINE,
)

#: A diff line that changes content: an addition or a removal, not context and not
#: the ``+++``/``---`` file headers.
_CHANGED_LINE = re.compile(r"(?m)^[+-](?![+-])(.*)$")

#: A whole-line comment — YAML/shell ``#`` or an HTML comment. Skipped before the
#: privilege match, because a comment cannot execute: #775's only workflow change
#: was a generated banner and a prose line reading ``pip install "git+https://…"``,
#: and matching inside it is what kept a comment-only edit at TIER-3.
#:
#: This is not a bypass. A line that starts with ``#`` is inert in both YAML and
#: the shell, so hiding a ``uses:`` or a ``secrets.`` reference behind one buys an
#: attacker a lower tier on a change that also does nothing.
_COMMENT_LINE = re.compile(r"^\s*(?:#|<!--)")


def privileged_change(patch: str) -> tuple[bool, str]:
    """Whether a workflow diff changes what the workflow *can do*.

    Returns ``(privileged, reason)``. ``reason`` names the first line that decided
    it, so a TIER-3 call is explainable rather than an assertion.

    **Fails closed.** An empty or unreadable patch is privileged: a classifier that
    silently downgrades what it cannot parse is worse than the glob it replaces,
    because the glob at least never guessed. Only a diff that was read *and*
    contained nothing privileged earns the lower tier.
    """
    if not patch or not patch.strip():
        return True, "empty or unreadable patch"
    changed = _CHANGED_LINE.findall(patch)
    if not changed:
        return True, "no add/remove lines found — patch not understood"
    for line in changed:
        if _COMMENT_LINE.match(line):
            continue
        if _PRIVILEGED_LINE.search(line):
            return True, line.strip()[:120]
    return False, ""


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
    if not changed:
        return False
    for p in changed:
        if not _matches_any(p, docs_globs):
            return False
    return True


#: ``diff --git a/<old> b/<new>`` — the header that starts each file in a unified
#: diff. The *new* name is the key, matching what the changed-file list reports.
_DIFF_HEADER = re.compile(r"(?m)^diff --git a/(?:\S+) b/(\S+)$")


def split_unified_diff(diff: str | None) -> dict[str, str]:
    """Split a whole-repo unified diff into per-file patches, keyed by new path.

    ``None`` or an unparseable diff yields ``{}`` — no evidence, so every path keeps
    the tier it would have had. Never a partial mapping: a diff that produced no
    headers is not silently read as "no files changed".
    """
    if not diff:
        return {}
    marks = list(_DIFF_HEADER.finditer(diff))
    if not marks:
        return {}
    out: dict[str, str] = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(diff)
        out[mark.group(1)] = diff[mark.start() : end]
    return out


def _tier3_downgradable(path: str, patches: dict[str, str] | None) -> bool:
    """Whether ``path``'s TIER-3 match may be lowered on the strength of its diff.

    Three things must all hold, and any one missing keeps TIER-3:

    * the path is one we know how to read a diff for (workflow YAML);
    * a patch for it was actually supplied — no patch means no evidence, and no
      evidence means the path decides, exactly as before this existed;
    * that patch changes nothing privileged.
    """
    if patches is None or not _matches_any(path, DIFF_CLASSIFIED_GLOBS):
        return False
    patch = patches.get(path)
    if patch is None:
        return False
    privileged, _reason = privileged_change(patch)
    return not privileged


def tier_for_files(
    changed: list[str],
    *,
    tier3_globs: tuple[str, ...] = (),
    docs_globs: tuple[str, ...] = (),
    allowlist_globs: tuple[str, ...] = (),
    patches: dict[str, str] | None = None,
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
            if not _matches_any(p, tier3_globs):
                continue
            if not _tier3_downgradable(p, patches):
                return 3

    if docs_globs:
        for p in changed:
            if not (_matches_any(p, docs_globs) or _matches_any(p, allowlist_globs)):
                return DEFAULT_TIER
        return 1

    return DEFAULT_TIER
