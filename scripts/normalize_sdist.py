#!/usr/bin/env python3
"""Make a built source distribution byte-reproducible.

``SOURCE_DATE_EPOCH`` is necessary and not sufficient. With setuptools 84.0.0 —
the version ``.github/requirements/publish-tools.txt`` pins — two builds of one
tree produce an identical wheel and a **different sdist**. Measured, not assumed:

    build 1  keel_workflow-1.19.3.tar.gz  ccbe9a67…
    build 2  keel_workflow-1.19.3.tar.gz  7cf93d4e…
    (the wheel was 74409e71… both times)

Three things carry wall-clock or builder identity into the archive even when
every file member correctly takes ``SOURCE_DATE_EPOCH``:

1. the **root directory member** (``keel_workflow-<v>/``) gets the moment the
   sdist was assembled, written as a PAX ``mtime`` record with sub-second
   precision — ``mtime: 1788437700.5630772`` in one build, ``1788437703.3743458``
   in the next;
2. the **gzip header** stores its own mtime, again wall clock;
3. every member carries the builder's ``uid``/``gid``/``uname``/``gname``
   (``501``/``0``/``berkayturanci``/``wheel`` on the machine this was found on),
   which are a property of the runner rather than of the release.

That is not cosmetic. PyPI keeps the first upload of a file forever
(``skip-existing: true``), so a publish that succeeds and then fails while
creating the GitHub Release leaves a re-run rebuilding the sdist and uploading a
``SHA256SUMS`` that describes a *different* archive — its first upload, so
``createdAt == updatedAt`` and the "was this asset replaced?" tolerance correctly
says no. The verify job would then compare PyPI's original sdist against the
rebuilt digest and hard-fail a healthy release with the wrong diagnosis.

So the archive is rewritten here, after ``python -m build`` and before anything
reads it: members sorted by name, ``USTAR`` format (which has no PAX records at
all, so 1 cannot come back), every mtime pinned to ``SOURCE_DATE_EPOCH``,
ownership zeroed, modes reduced to the executable bit, and gzip written with
``mtime=0``. Stdlib only, so it adds nothing to the hash-locked toolchain.

The sdist's *contents* are untouched: this rewrites the envelope's metadata, not
a single byte any consumer unpacks.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

#: USTAR cannot express a PAX record, which is the point — see 1 above. It caps
#: member names at 100 characters (plus a 155-character prefix); keel's longest
#: is 68, and a future path that overflows should fail loudly here rather than
#: silently promoting the archive back to PAX.
ARCHIVE_FORMAT = tarfile.USTAR_FORMAT

#: Reduced to the executable bit: an sdist carries source, and the group/other
#: write bits a builder's umask happens to set are not part of the release.
FILE_MODE = 0o644
EXEC_MODE = 0o755
DIR_MODE = 0o755


def normalized_mode(member: tarfile.TarInfo) -> int:
    if member.isdir():
        return DIR_MODE
    return EXEC_MODE if member.mode & 0o111 else FILE_MODE


def normalize(path: Path, epoch: int) -> int:
    """Rewrite ``path`` (a ``.tar.gz``) in place, deterministically.

    Returns the number of members rewritten. The write goes to a temporary file
    in the same directory and is moved into place, so an interrupted run cannot
    leave a half-written archive where a valid one used to be.
    """
    with tarfile.open(path, "r:gz") as archive:
        members = sorted(archive.getmembers(), key=lambda member: member.name)
        payloads = [
            archive.extractfile(member).read() if member.isreg() else None for member in members
        ]

    handle, staging = tempfile.mkstemp(dir=path.parent, suffix=".tar.gz")
    os.close(handle)
    staged = Path(staging)
    try:
        # `filename=""` keeps the output file's own name out of the gzip header,
        # and `mtime=0` keeps the clock out of it. Both are stored in the header
        # by default, and both differ between two builds of the same tree.
        with open(staged, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=ARCHIVE_FORMAT
                ) as normalized:
                    for member, payload in zip(members, payloads, strict=True):
                        member.mtime = epoch
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        member.mode = normalized_mode(member)
                        member.pax_headers.clear()
                        normalized.addfile(
                            member, io.BytesIO(payload) if payload is not None else None
                        )
        shutil.move(str(staged), str(path))
    finally:
        if staged.exists():  # pragma: no cover - only on an interrupted write
            staged.unlink()
    return len(members)


def resolve_epoch(explicit: str | None, environ: dict[str, str]) -> int:
    """``--epoch``, else ``SOURCE_DATE_EPOCH``. Refuses to guess."""
    raw = explicit if explicit is not None else environ.get("SOURCE_DATE_EPOCH")
    if not raw:
        raise ValueError(
            "no timestamp given: pass --epoch or set SOURCE_DATE_EPOCH. Defaulting to "
            "the clock would reintroduce exactly the non-determinism this removes."
        )
    try:
        epoch = int(raw)
    except ValueError:
        raise ValueError(f"timestamp must be an integer number of seconds, got {raw!r}") from None
    if epoch < 0:
        raise ValueError(f"timestamp must not be negative, got {epoch}")
    return epoch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sdist", nargs="+", help="source distribution(s) to rewrite in place")
    parser.add_argument(
        "--epoch",
        default=None,
        help="timestamp to pin every member to (defaults to $SOURCE_DATE_EPOCH)",
    )
    args = parser.parse_args(argv)

    try:
        epoch = resolve_epoch(args.epoch, dict(os.environ))
        for name in args.sdist:
            path = Path(name)
            count = normalize(path, epoch)
            print(f"normalized {path.name}: {count} members pinned to {epoch}")
    except (ValueError, OSError, tarfile.TarError) as exc:
        print(f"normalize-sdist failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
