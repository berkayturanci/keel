"""Single-host resource claims backed by atomic ``mkdir``.

Every merge goes through the merge lock (a keel invariant) so concurrent ``ship``
runs on the same checkout cannot race the branch tip. The merge lock is now one
consumer of the generalized resource-claim primitive below: ``mkdir`` is atomic,
so a resource directory is either created for one owner or already held.
"""

from __future__ import annotations

import hashlib
import json
import os
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
#: (#1077), which leaves three ways to end up here: the unmaskable one — ``SIGKILL``,
#: container teardown, power loss — where no handler of ours ever runs; a cleanup the
#: handler cannot finish, such as an unwritable directory or a stray file the failed step
#: left behind; and one the handler deliberately declines, because it can no longer prove
#: the directory is the one it created and refuses to delete a stranger's claim. ``keel
#: release <resource>`` with no ``--owner`` recovers all three.
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
    resource free rather than held by nobody; that unwind is best-effort and removes only
    the directory this very call created (see :func:`_discard_partial_claim`).
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
    # Which directory this call made, recorded — and, where the platform allows it, held
    # open — while it is still unambiguously ours. The descriptor keeps the inode
    # allocated for as long as this call runs, so the identity recorded here cannot be
    # handed to a directory somebody else creates at this path; the unwind below touches
    # nothing that does not answer with that same identity.
    pin = _pin(path)
    identity = _identity(path)
    try:
        taken = _has_entries(path, pin)
    except OSError:
        # Unable to tell whether the directory the pin latched is ours, and unable to
        # prove it either way, this is an I/O failure and not contention: it raises, as
        # every other I/O failure here does, and unwinds nothing — a leaked *empty*
        # directory an operator can release is recoverable, a stranger's deleted lock
        # is not (round 5 of #1077).
        _unpin(pin)
        raise
    if taken:
        # Between the ``mkdir`` and the pin the path can already have been retargeted:
        # the any-owner recovery removed our empty directory and somebody claimed the
        # resource behind it, and what the pin latched is *their* directory — under the
        # same owner name, as often as not. A directory of ours is empty at this point,
        # so anything in it is proof the claim is not ours: report contention and touch
        # nothing. An empty stranger's directory taken in that same gap cannot be told
        # from ours — that is the by-name floor the docs state (#1077).
        _unpin(pin)
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
        try:
            _write_owner(path, clean_owner, pin=pin)
        except FileExistsError:
            # The owner file is created *exclusively*, through the pin where there is
            # one. A directory of ours has no owner file at this point, so one already
            # there means the claim was taken underneath us while the scaffold ran —
            # released by the any-owner recovery and re-claimed, under the same name as
            # often as not (round 5 of #1077). Report contention and unwind nothing: the
            # live claim is theirs. A pinned directory that was *removed* underneath
            # surfaces as ``FileNotFoundError`` from the same create, an I/O failure
            # that propagates below; the unwind then finds no directory and leaves it.
            _unpin(pin)
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
    except BaseException:
        # Left in place, the half-built claim is worse than the error that caused it —
        # it is held forever by ``UNKNOWN_HOLDER``, denies every later claim, and nothing
        # releases it (#1077). Between the ``mkdir`` and this handler an operator can have
        # run the any-owner recovery on the ownerless claim and somebody else can have
        # claimed the resource behind it — under the *same* owner name, since an owner is
        # a name and not a claim id — so the unwind identifies the directory rather than
        # trusting either the path or the recorded name.
        _discard_partial_claim(path, owner=clean_owner, identity=identity, pin=pin)
        raise
    finally:
        # After the handler, so the inode stays pinned for the whole comparison above.
        _unpin(pin)
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


def _discard_partial_claim(
    path: Path,
    *,
    owner: str,
    identity: tuple[int, int, float | None] | None,
    pin: int | None = None,
) -> None:
    """Unwind a claim directory whose initialisation failed — if it is still the one we made.

    That this call created *a* directory at ``path`` does not prove the path still denotes
    that directory. The documented any-owner recovery (``keel release <resource>`` with no
    ``--owner``) can have removed the ownerless half-built claim while this call was
    stalled, and another owner can have claimed the resource behind it.

    The recorded **owner name cannot tell those apart**, because an owner is a name, not a
    claim id: :func:`merge_lock` always claims as ``merge-lock`` and ``keel merge`` derives
    one name per pull request, so the live claim this unwind must not touch routinely
    carries the very string this call does. The ownerless reading is no safer — between
    another caller's ``mkdir`` and its finished ``owner.json`` its live claim reads as
    :data:`UNKNOWN_HOLDER` too, and so does a torn write, because :func:`_write_owner` is a
    plain ``write_text``.

    So the directory is **identified**, not named. ``identity`` is what :func:`_identity`
    read straight after this call's own ``mkdir`` — ``st_dev``, ``st_ino``, and the birth
    time where the platform reports one; the path is re-stat'ed here and nothing is removed
    unless the answer matches. A directory removed and re-created in between is a different
    inode, so neither a same-named re-claim nor another caller's ownerless window can be
    deleted. The owner check is kept as a second condition: a directory that still is ours
    but by now names a *different* owner is left alone as well.

    What the check proves: the directory being removed is the one this call created, at the
    same path, not unlinked and re-created since. An inode number on its own would not
    prove that — a filesystem may reuse one as soon as the directory is unlinked, and on
    ext4 the next ``mkdir`` in the same parent is a likely taker — which is why
    :func:`_claim_path` holds the directory open (:func:`_pin`) for the whole of this call:
    a pinned inode is not recycled, so within that window the pair names one directory and
    only one.

    What it does **not** prove: that nothing can change between the last identity read
    and the ``rmdir`` itself. ``owner.json`` is unlinked through the pinned descriptor, so
    that removal is bound to our inode; a directory has no by-descriptor removal in POSIX,
    so the ``rmdir`` is by name and the identity is re-read right before it. A directory
    swapped in between those two calls survives if it holds anything — ``rmdir`` refuses a
    non-empty directory — so the residual exposure is another caller's *empty* claim
    directory created in that same instant, which its own ``_write_owner`` would then
    fail on and which it would unwind itself. Nor the gap between the ``mkdir`` and the
    pin: a stranger's directory taken *there* is what the pin latches, so anything already
    in it is treated as contention and nothing is unwound, while an empty one cannot be
    told from ours. Nor anything on a platform that cannot pin.
    Windows answers no
    descriptor, and there this rests on ``stat`` alone — an NTFS file id whose sequence
    number is bumped on reuse, plus ``st_birthtime`` on 3.12+. It also does not survive
    something that removes and re-creates the directory *and* reproduces its recorded
    identity, which no ordinary filesystem does. Combined with the owner check and the
    window this runs in — the microseconds between a failed initialisation and its own
    handler — that is the practical guarantee. This is a single-host primitive, not a
    distributed lock, and it has never claimed to be one.

    The removal is best-effort: the caller re-raises the failure that got us here, and that
    error is the one worth reporting. If the cleanup itself cannot finish — the directory is
    unwritable, or the failed step left a file behind that ``rmdir`` refuses — masking the
    original cause with the cleanup's own ``OSError`` would hide *why* the claim failed and
    leave the operator with the leaked directory either way. Declining to remove is the same
    outcome: clearing what is left over is the caller-owned stale recovery, as for a claim
    orphaned by an unmaskable kill.
    """
    if identity is None:
        # The identity was never established (the ``stat`` right after ``mkdir`` failed).
        # Unable to prove the directory is ours, we leave it: a leaked claim an operator
        # can release is recoverable, another run's deleted lock is not.
        return
    if _holder(path) not in (UNKNOWN_HOLDER, owner):
        return
    # Everything that removes is kept as close to the identity check as the platform
    # allows, and bound to the pinned inode where it can be (round 3 of #1077): the
    # owner file is unlinked *through the pin* (``dir_fd``), so it is our directory's
    # ``owner.json`` or nothing, whatever the path denotes by now. ``rmdir`` has no such
    # form — POSIX removes directories by name, never by descriptor — so the identity is
    # re-read immediately before it and the remaining window is the two syscalls in
    # between. A directory swapped in *there* survives if it has anything in it
    # (``rmdir`` refuses a non-empty directory), so what that window can still take is
    # another caller's empty, just-``mkdir``-ed claim in the same instant. That is the
    # honest floor of a by-name primitive, and the docs state it.
    if _identity(path) != identity:
        return
    try:
        if pin is not None:
            # Bound to our inode: this is our directory's owner file or nothing.
            try:
                os.unlink("owner.json", dir_fd=pin)
            except FileNotFoundError:
                pass
        # Without a descriptor there is no unlink that is bound to an inode, and a
        # by-name unlink after a by-name check is the very defect this exists to close —
        # so the unpinned path removes no file at all. The ``rmdir`` below then refuses a
        # directory holding our own torn ``owner.json``, which stays as the documented
        # leak, and equally refuses a stranger's live claim.
        if _identity(path) != identity:
            return
        path.rmdir()
    except OSError:
        pass


def _has_entries(path: Path, pin: int | None) -> bool:
    """True when the claim directory already holds something.

    Read through the pin where there is one, so the answer is about the inode the
    unwind would later act on. An unreadable directory raises: the caller treats that
    as the I/O failure it is, because "could not look" must not be read as "empty" —
    that reading adopted a stranger's claim as ours (round 5 of #1077).
    """
    entries = os.listdir(pin) if pin is not None else os.listdir(path)
    return bool(entries)


def _identity(path: Path) -> tuple[int, int, float | None] | None:
    """Return ``(st_dev, st_ino, birth time)`` for ``path``, or ``None`` if unknowable.

    ``None`` is the fail-closed answer — the directory is gone, or its metadata cannot be
    read — and callers treat it as "not the directory I am looking for".

    The device and inode pair is the portable half, and :func:`_pin` is what makes it
    conclusive. ``st_birthtime`` is added where the platform reports one (macOS/APFS, the
    BSDs, Windows on 3.12+) and is simply absent elsewhere, which stays consistent within a
    run: both stats of the same path answer the same way, so the comparison never turns on
    the platform. What is deliberately **not** in the tuple is ``st_ctime``: writing
    ``owner.json`` into the claim directory changes the directory's own ctime, so including
    it would report our own initialisation as a foreign directory and refuse to unwind
    exactly the failure this cleanup exists for.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_dev, info.st_ino, getattr(info, "st_birthtime", None))


def _pin(path: Path) -> int | None:
    """Hold the directory at ``path`` open, or ``None`` where that is not possible.

    An inode number is only unique while the inode is allocated: once the directory is
    unlinked the number is free for the next one, and on ext4 the next ``mkdir`` in the
    same parent is a likely taker. An open descriptor keeps the inode alive — ``rmdir``
    still succeeds, the number is simply not recycled until the last reference goes — so
    for as long as this pin is held, ``(st_dev, st_ino)`` names one directory and only one.

    Windows cannot open a directory this way and answers ``None``; there the identity
    falls back to what ``stat`` alone reports, which is stronger than POSIX's to begin
    with, because an NTFS file id folds in a sequence number that is bumped when the record
    is reused. Nothing here fails on a ``None``: the pin narrows a theoretical window, it
    is not the guard itself.
    """
    try:
        return os.open(path, os.O_RDONLY)
    except OSError:
        return None


def _unpin(pin: int | None) -> None:
    """Drop a descriptor from :func:`_pin`, if there was one to take."""
    if pin is not None:
        os.close(pin)


def _write_owner(path: Path, owner: str, *, pin: int | None = None) -> None:
    """Create ``owner.json`` for a claim — exclusively, and through ``pin`` if given.

    ``O_EXCL`` is what makes a claim taken underneath us visible: a directory this call
    made has no owner file, so an existing one is somebody else's live claim and the
    create fails with ``FileExistsError`` instead of overwriting it. Through the pinned
    descriptor the create is bound to the inode this call created, so it cannot land in
    a directory that replaced ours at the same path; if ours was removed underneath, it
    fails with ``FileNotFoundError`` rather than writing anywhere. Without a descriptor
    the create is by name but still exclusive (#1077, round 5).

    The write itself is still not atomic: a caller killed between the create and the
    write leaves a torn file, which :func:`_holder` reads as :data:`UNKNOWN_HOLDER`.
    """
    payload = json.dumps({"owner": owner}, sort_keys=True) + "\n"
    if pin is not None:
        fd = os.open("owner.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=pin)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return
    with open(path / "owner.json", "x", encoding="utf-8") as handle:
        handle.write(payload)


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
