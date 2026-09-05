"""Single-host resource claims backed by atomic ``mkdir``.

Every merge goes through the merge lock (a keel invariant) so concurrent ``ship``
runs on the same checkout cannot race the branch tip. The merge lock is now one
consumer of the generalized resource-claim primitive below: ``mkdir`` is atomic,
so a resource directory is either created for one owner or already held.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import workspace

SCHEMA_VERSION = "keel.resource-claim.v1"

#: Holder of a claim whose owner cannot be read — ``owner.json`` missing, corrupt,
#: unreadable, or the wrong shape. Deliberately *not* ``None``: the claim directory
#: exists, so the resource **is** held; we simply cannot name by whom. The window is
#: narrow but real, because :func:`_claim_path` creates the directory before it writes
#: the owner file. An *error* in that window is now unwound on a **best-effort** basis
#: (#1077), which leaves two ways to end up here: the unmaskable one — ``SIGKILL``,
#: container teardown, power loss — where no handler of ours ever runs, and a cleanup
#: the handler cannot finish, such as an unwritable directory or a stray file the failed
#: step left behind. ``keel release <resource>`` with no ``--owner`` recovers both.
UNKNOWN_HOLDER = "<unknown>"


class LockError(RuntimeError):
    """Raised when the merge lock is already held."""


@dataclass(frozen=True)
class ClaimResult:
    """Structured result for a single-host resource claim operation."""

    schema_version: str
    resource: str
    owner: str
    path: str
    granted: bool
    status: str
    reason: str
    holder: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible deterministic representation."""
        return asdict(self)


def contract_as_dict() -> dict[str, Any]:
    """Return the stable resource-claim contract."""
    return {
        "schema_version": SCHEMA_VERSION,
        "consumer_neutral": True,
        "deterministic": True,
        "stdlib_only": True,
        "scope": "single-host",
        "primitive": "mkdir",
        "deny_mode": "structured-feedback",
        "statuses": ["granted", "denied", "released", "missing", "not-owner"],
        "merge_lock_consumer": True,
        "stale_recovery": "caller-owned",
    }


def claim_resource(root: str | Path, resource: str, *, owner: str) -> ClaimResult:
    """Claim a named resource under ``root`` for exactly one owner.

    Contention is structured feedback: a resource already held comes back as a ``denied``
    result, never an exception. An **I/O failure** is not contention and still raises, the
    same way :func:`release_resource` propagates one — ``denied`` has to keep meaning
    "somebody else holds this" for callers that back off and retry on it. A failure after
    the directory is created unwinds the half-built claim, so a raise normally leaves the
    resource free rather than held by nobody; that unwind is best-effort and owner-scoped
    (see :func:`_discard_partial_claim`).
    """
    path = resource_path(root, resource)
    return _claim_path(path, resource=_clean(resource, "unknown-resource"), owner=owner)


def release_resource(
    root: str | Path,
    resource: str,
    *,
    owner: str | None = None,
    best_effort: bool = False,
) -> ClaimResult:
    """Release a named resource claim, optionally requiring the owner to match."""
    path = resource_path(root, resource)
    return _release_path(
        path,
        resource=_clean(resource, "unknown-resource"),
        owner=owner,
        best_effort=best_effort,
    )


@contextmanager
def resource_claim(root: str | Path, resource: str, *, owner: str) -> Iterator[ClaimResult]:
    """Context manager that yields a structured claim result and releases on success."""
    result = claim_resource(root, resource, owner=owner)
    try:
        yield result
    finally:
        if result.granted:
            release_resource(root, resource, owner=owner)


def resource_path(root: str | Path, resource: str) -> Path:
    """Return the deterministic lock directory path for ``resource``."""
    name = _clean(resource, "unknown-resource")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-").lower()
    slug = slug or "resource"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return Path(root) / f"{slug}-{digest}.lock"


@contextmanager
def merge_lock(lock_dir: str | Path) -> Iterator[Path]:
    """Acquire the merge lock for the duration of the ``with`` block."""
    path = Path(lock_dir)
    result = _claim_path(path, resource="merge", owner="merge-lock")
    if not result.granted:
        raise LockError(f"merge lock already held: {path}")
    try:
        yield path
    finally:
        _release_path(path, resource="merge", owner="merge-lock", best_effort=True)


def _claim_path(path: Path, *, resource: str, owner: str) -> ClaimResult:
    clean_owner = _clean(owner, "unknown-owner")
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        return ClaimResult(
            schema_version=SCHEMA_VERSION,
            resource=resource,
            owner=clean_owner,
            path=str(path),
            granted=False,
            status="denied",
            reason="resource-already-claimed",
            holder=_holder(path),
        )
    try:
        workspace.ensure_runtime_gitignore_for(path)
        _write_owner(path, clean_owner)
    except BaseException:
        # Left in place, the half-built claim is worse than the error that caused it —
        # it is held forever by ``UNKNOWN_HOLDER``, denies every later claim, and nothing
        # releases it (#1077). The unwind is scoped by *owner* rather than by "this call
        # created a directory here": between the ``mkdir`` and this handler an operator
        # can have run the any-owner recovery on the ownerless claim and somebody else
        # can have claimed the same resource, so the cleanup refuses to touch a claim
        # that now names another owner.
        _discard_partial_claim(path, owner=clean_owner)
        raise
    return ClaimResult(
        schema_version=SCHEMA_VERSION,
        resource=resource,
        owner=clean_owner,
        path=str(path),
        granted=True,
        status="granted",
        reason="claim-acquired",
        holder=clean_owner,
    )


def _release_path(
    path: Path,
    *,
    resource: str,
    owner: str | None,
    best_effort: bool = False,
) -> ClaimResult:
    clean_owner = _clean(owner, "unknown-owner") if owner is not None else "any-owner"
    if not path.exists():
        return ClaimResult(
            schema_version=SCHEMA_VERSION,
            resource=resource,
            owner=clean_owner,
            path=str(path),
            granted=False,
            status="missing",
            reason="resource-not-claimed",
        )
    holder = _holder(path)
    # An unidentifiable holder refuses a *named* release, exactly as a differently
    # named one does. Releasing with `owner=None` stays the deliberate any-owner
    # escape for clearing a stuck claim.
    if owner is not None and holder != clean_owner:
        return ClaimResult(
            schema_version=SCHEMA_VERSION,
            resource=resource,
            owner=clean_owner,
            path=str(path),
            granted=False,
            status="not-owner",
            reason="resource-held-by-different-owner",
            holder=holder,
        )
    try:
        owner_file = path / "owner.json"
        if owner_file.exists():
            owner_file.unlink()
        path.rmdir()
    except OSError:
        if not best_effort:
            raise
    return ClaimResult(
        schema_version=SCHEMA_VERSION,
        resource=resource,
        owner=clean_owner,
        path=str(path),
        granted=False,
        status="released",
        reason="claim-released",
        holder=holder,
    )


def _discard_partial_claim(path: Path, *, owner: str) -> None:
    """Unwind a claim directory whose initialisation failed — if it is still ours.

    That this call created *a* directory at ``path`` does not prove the path still
    denotes that directory. The documented any-owner recovery (``keel release
    <resource>`` with no ``--owner``) can have removed the ownerless half-built claim
    while this call was stalled, and another owner can have claimed the resource behind
    it. So the owner is read back the way :func:`_holder` reads it, and a claim naming a
    *different* owner is left completely alone: the original failure still propagates,
    it just takes nothing live with it. Only an ``owner.json`` that is missing,
    unreadable, or names this call's own owner is removed, and only then the directory.

    The removal is best-effort: the caller re-raises the failure that got us here, and
    that error is the one worth reporting. If the cleanup itself cannot finish — the
    directory is unwritable, or the failed step left a file behind that ``rmdir``
    refuses — masking the original cause with the cleanup's own ``OSError`` would hide
    *why* the claim failed and leave the operator with the leaked directory either way.
    Clearing what is left over is then the same caller-owned stale recovery as for a
    claim orphaned by an unmaskable kill.
    """
    if _holder(path) not in (UNKNOWN_HOLDER, owner):
        return
    try:
        owner_file = path / "owner.json"
        if owner_file.exists():
            owner_file.unlink()
        path.rmdir()
    except OSError:
        pass


def _write_owner(path: Path, owner: str) -> None:
    (path / "owner.json").write_text(
        json.dumps({"owner": owner}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _holder(path: Path) -> str:
    """The named owner of an **existing** claim, or :data:`UNKNOWN_HOLDER`.

    Every caller reaches here only with the claim directory present, so there is no
    "unheld" answer to give. Each way of failing to read the name — file missing,
    corrupt JSON, unreadable, wrong shape — means the resource is held by someone we
    cannot identify, not that it is free. Collapsing those to ``None`` made the
    ownership guard *vanish* rather than fail closed, letting a second run release a
    live merge claim and take the lock (#631).
    """
    owner_file = path / "owner.json"
    if not owner_file.exists():
        return UNKNOWN_HOLDER
    try:
        data = json.loads(owner_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UNKNOWN_HOLDER
    owner = data.get("owner") if isinstance(data, dict) else None
    return owner if isinstance(owner, str) and owner.strip() else UNKNOWN_HOLDER


def _clean(value: str | None, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback
