"""Pure branch-contract verification — base ancestry + worktree isolation.

keel's s2 branch contract has three rules: cut the work branch off an
*up-to-date* ``origin/<base_branch>``; keep the work in one repo-nested,
gitignored linked worktree per issue; and never mutate the operator's primary
checkout. A branch cut from a *stale* local base produces phantom diffs and
wrong tier classification, and edits landing in the primary checkout contaminate
the operator's tree. Until now that contract was unenforced (audit GAP-5).

This module makes the contract a pure function of gathered git facts. The CLI
(:func:`keel.cli._cmd_verify_branch`) collects the live facts via the thin
``git``/``gh`` wrappers; this module compares them and returns a structured
verdict. There is no I/O here, so the verdict is a deterministic function of its
arguments alone and is fully unit-testable offline.

Two independent checks compose into one verdict:

* **Base ancestry** — the PR head's merge-base with ``origin/<base_branch>``
  must equal the current base tip (strict) *or* be a recent ancestor within a
  bounded tolerance (``base_distance`` commits behind the tip). Beyond the
  tolerance the branch was cut from a stale base → ``stale``. The
  ``--allow-stale-base`` operator escape (consent scope ``git``) downgrades a
  stale verdict to an advisory pass, recorded on the report.
* **Worktree isolation** — when run locally with a linked worktree nested under
  the repo root, that is the clean topology. A working branch living in the
  *primary* checkout (or a worktree outside the repo root) is ``contaminated``.
  In CI / PR-only mode there is no local worktree to inspect; the check is N/A
  and skipped gracefully.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "keel.verify-branch.v1"

#: Default ancestry tolerance: how many commits the merge-base may sit behind the
#: current base tip before the branch counts as stale. ``0`` is strict (merge-base
#: must equal the base tip). A small positive default tolerates a base that moved
#: forward by a few commits after the branch was cut, which is normal and benign.
DEFAULT_BASE_DISTANCE = 5


def verify(
    *,
    base_branch: str,
    head_sha: str | None,
    merge_base_sha: str | None,
    base_tip_sha: str | None,
    base_distance: int | None,
    worktree_path: str | None = None,
    repo_root: str | None = None,
    is_linked_worktree: bool | None = None,
    tolerance: int = DEFAULT_BASE_DISTANCE,
    allow_stale_base: bool = False,
) -> dict[str, Any]:
    """Compare gathered git facts against the s2 branch contract.

    Parameters describe *facts*, not I/O:

    * ``head_sha`` / ``merge_base_sha`` / ``base_tip_sha`` — the PR head, its
      merge-base with ``origin/<base_branch>``, and the current base tip.
    * ``base_distance`` — commits the merge-base sits behind the base tip
      (``git rev-list --count merge_base..base_tip``), or ``None`` when it could
      not be computed.
    * ``worktree_path`` / ``repo_root`` / ``is_linked_worktree`` — the local
      working-tree facts; all ``None`` in CI / PR-only mode, where the isolation
      check is skipped.
    * ``tolerance`` — max allowed ``base_distance`` before stale (pure knob).
    * ``allow_stale_base`` — operator escape that downgrades stale to advisory.

    Returns a JSON-compatible report with a ``status`` of ``pass``/``fail`` and a
    ``verdict`` of ``ok``/``stale``/``contaminated``, plus per-check detail and an
    advisory note recording any escape that was applied.
    """
    ancestry = _check_ancestry(
        head_sha=head_sha,
        merge_base_sha=merge_base_sha,
        base_tip_sha=base_tip_sha,
        base_distance=base_distance,
        tolerance=tolerance,
        allow_stale_base=allow_stale_base,
    )
    isolation = _check_isolation(
        worktree_path=worktree_path,
        repo_root=repo_root,
        is_linked_worktree=is_linked_worktree,
    )

    if isolation["verdict"] == "contaminated":
        verdict = "contaminated"
    elif ancestry["verdict"] == "stale":
        verdict = "stale"
    else:
        verdict = "ok"

    blocking = (isolation["status"] == "fail") or (ancestry["status"] == "fail")
    # Surface the most actionable note: a contamination failure first, then a
    # stale/advisory ancestry note, then any informational skip.
    if isolation["verdict"] == "contaminated":
        note = isolation["note"]
    elif ancestry["verdict"] == "stale":
        note = ancestry["note"]
    else:
        note = ancestry["note"] or isolation["note"]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "fail" if blocking else "pass",
        "verdict": verdict,
        "base_branch": base_branch,
        "allow_stale_base": allow_stale_base,
        "tolerance": tolerance,
        "note": note,
        "ancestry": ancestry,
        "isolation": isolation,
    }


def _check_ancestry(
    *,
    head_sha: str | None,
    merge_base_sha: str | None,
    base_tip_sha: str | None,
    base_distance: int | None,
    tolerance: int,
    allow_stale_base: bool,
) -> dict[str, Any]:
    """Verify the PR head was cut from an up-to-date base.

    The merge-base of the head with the base tip must be the base tip itself
    (strict) or sit within ``tolerance`` commits behind it. Missing facts (a base
    or merge-base that could not be resolved) are an advisory skip — fail-soft, so
    a transient ``gh``/``git`` gap never hard-blocks. ``allow_stale_base``
    downgrades an otherwise-blocking stale verdict to an advisory pass.
    """
    if merge_base_sha is None or base_tip_sha is None or base_distance is None:
        return {
            "verdict": "unknown",
            "status": "pass",
            "advisory": True,
            "note": "base ancestry not resolved; skipping",
            "head_sha": head_sha,
            "merge_base_sha": merge_base_sha,
            "base_tip_sha": base_tip_sha,
            "base_distance": base_distance,
            "tolerance": tolerance,
        }
    up_to_date = merge_base_sha == base_tip_sha
    within_tolerance = base_distance <= tolerance
    fresh = up_to_date or within_tolerance
    if fresh:
        return {
            "verdict": "ok",
            "status": "pass",
            "advisory": False,
            "note": None,
            "head_sha": head_sha,
            "merge_base_sha": merge_base_sha,
            "base_tip_sha": base_tip_sha,
            "base_distance": base_distance,
            "tolerance": tolerance,
        }
    reason = (
        f"base is stale: head was cut {base_distance} commit(s) behind the current "
        f"base tip (tolerance {tolerance})"
    )
    return {
        "verdict": "stale",
        "status": "pass" if allow_stale_base else "fail",
        "advisory": allow_stale_base,
        "note": (
            f"{reason}; downgraded to advisory by --allow-stale-base"
            if allow_stale_base
            else reason
        ),
        "head_sha": head_sha,
        "merge_base_sha": merge_base_sha,
        "base_tip_sha": base_tip_sha,
        "base_distance": base_distance,
        "tolerance": tolerance,
    }


def _check_isolation(
    *,
    worktree_path: str | None,
    repo_root: str | None,
    is_linked_worktree: bool | None,
) -> dict[str, Any]:
    """Verify the working branch lives in a repo-nested linked worktree.

    With no local worktree facts (CI / PR-only mode) the check is N/A and skipped
    gracefully. Locally, a primary-checkout edit (``is_linked_worktree`` false) or
    a worktree outside the repo root is contamination → fail.
    """
    if worktree_path is None or repo_root is None or is_linked_worktree is None:
        return {
            "verdict": "n/a",
            "status": "pass",
            "advisory": True,
            "note": "no local worktree (CI/PR-only); isolation check skipped",
            "worktree_path": worktree_path,
            "repo_root": repo_root,
            "is_linked_worktree": is_linked_worktree,
            "nested": None,
        }
    nested = _is_nested(worktree_path, repo_root)
    if not is_linked_worktree:
        return {
            "verdict": "contaminated",
            "status": "fail",
            "advisory": False,
            "note": "working branch is the primary checkout, not a linked worktree",
            "worktree_path": worktree_path,
            "repo_root": repo_root,
            "is_linked_worktree": is_linked_worktree,
            "nested": nested,
        }
    if not nested:
        return {
            "verdict": "contaminated",
            "status": "fail",
            "advisory": False,
            "note": "linked worktree is not nested under the repo root",
            "worktree_path": worktree_path,
            "repo_root": repo_root,
            "is_linked_worktree": is_linked_worktree,
            "nested": nested,
        }
    return {
        "verdict": "ok",
        "status": "pass",
        "advisory": False,
        "note": None,
        "worktree_path": worktree_path,
        "repo_root": repo_root,
        "is_linked_worktree": is_linked_worktree,
        "nested": nested,
    }


def _is_nested(worktree_path: str, repo_root: str) -> bool:
    """True when ``worktree_path`` is strictly under ``repo_root`` (pure path math).

    Uses normalized POSIX-style segment comparison so it is deterministic and
    platform-independent — no filesystem access. A path equal to the root is not
    "nested" (the primary checkout sits at the root).
    """
    root_parts = _segments(repo_root)
    wt_parts = _segments(worktree_path)
    if len(wt_parts) <= len(root_parts):
        return False
    return wt_parts[: len(root_parts)] == root_parts


def _segments(path: str) -> list[str]:
    """Split a path into non-empty, ``.``-free segments (normalized, no I/O)."""
    normalized = path.replace("\\", "/")
    return [part for part in normalized.split("/") if part not in ("", ".")]
