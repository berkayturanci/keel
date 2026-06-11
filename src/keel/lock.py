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

SCHEMA_VERSION = "keel.resource-claim.v1"


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
    """Claim a named resource under ``root`` for exactly one owner."""
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
    _write_owner(path, clean_owner)
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
    if owner is not None and holder is not None and holder != clean_owner:
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


def _write_owner(path: Path, owner: str) -> None:
    (path / "owner.json").write_text(
        json.dumps({"owner": owner}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _holder(path: Path) -> str | None:
    owner_file = path / "owner.json"
    if not owner_file.exists():
        return None
    try:
        data = json.loads(owner_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    owner = data.get("owner") if isinstance(data, dict) else None
    return owner if isinstance(owner, str) and owner.strip() else None


def _clean(value: str | None, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback
