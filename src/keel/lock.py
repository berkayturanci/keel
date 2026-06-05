"""The merge lock — a ``mkdir``-based mutual exclusion, as a context manager.

Every merge goes through this lock (a keel invariant) so concurrent ``ship`` runs
on the same checkout cannot race the branch tip. ``mkdir`` is atomic: the directory
either is created (lock acquired) or already exists (lock held).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockError(RuntimeError):
    """Raised when the merge lock is already held."""


@contextmanager
def merge_lock(lock_dir: str | Path) -> Iterator[Path]:
    """Acquire the merge lock for the duration of the ``with`` block."""
    path = Path(lock_dir)
    try:
        path.mkdir(parents=True)
    except FileExistsError as exc:
        raise LockError(f"merge lock already held: {path}") from exc
    try:
        yield path
    finally:
        try:
            path.rmdir()
        except OSError:  # pragma: no cover - best-effort release
            pass
