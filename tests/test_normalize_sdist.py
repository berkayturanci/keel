"""`scripts/normalize_sdist.py` — the sdist envelope must be deterministic.

`SOURCE_DATE_EPOCH` alone does not make a setuptools sdist reproducible, which is
the defect this script exists for: with the pinned setuptools 84.0.0, two builds
of one tree produced an identical wheel and two different tarballs. Three
carriers of wall clock and builder identity survive the epoch — the root
directory member's PAX `mtime` record, the gzip header's own mtime, and every
member's uid/gid/uname/gname.

These tests assert the *property* on fixture archives rather than by building the
project twice: a unit test that shells out to `python -m build` needs the build
toolchain installed, takes tens of seconds, and would be the one test in this
suite that is neither hermetic nor offline. The end-to-end claim is enforced
where it actually matters and on the real toolchain — `publish.yml` builds twice
on every release and fails if the digests differ.

`scripts/` is maintenance tooling outside the coverage gate, so these are what
hold it.
"""

from __future__ import annotations

import contextlib
import gzip
import importlib.util
import io
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "normalize_sdist.py"
_spec = importlib.util.spec_from_file_location("normalize_sdist", _SCRIPT)
normalize_sdist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(normalize_sdist)

EPOCH = 1788435996

#: The archive's payload, identical in every fixture below. Only the envelope's
#: metadata varies, which is exactly what a rebuild varies.
CONTENT = {
    "pkg-1.0/PKG-INFO": b"Metadata-Version: 2.1\nName: pkg\n",
    "pkg-1.0/README.md": b"# pkg\n",
    "pkg-1.0/src/pkg/__init__.py": b'__version__ = "1.0"\n',
}


def _write_archive(
    path: Path,
    *,
    mtime: float,
    uid: int = 501,
    uname: str = "someone",
    gzip_mtime: int | None = None,
    order: list[str] | None = None,
    executable: str | None = None,
    fmt: int = tarfile.PAX_FORMAT,
) -> None:
    """A tarball shaped like a setuptools sdist, with the varying parts injected."""
    names = order or list(CONTENT)
    with open(path, "wb") as raw:
        with gzip.GzipFile(
            filename=path.name, mode="wb", fileobj=raw, mtime=gzip_mtime
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=fmt) as archive:
                root = tarfile.TarInfo("pkg-1.0")
                root.type = tarfile.DIRTYPE
                # The root member is the one setuptools stamps with wall clock.
                root.mtime = mtime
                root.mode = 0o775
                root.uid, root.gid, root.uname, root.gname = uid, uid, uname, uname
                archive.addfile(root)
                for name in names:
                    payload = CONTENT[name]
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    info.mtime = EPOCH
                    info.mode = 0o755 if name == executable else 0o664
                    info.uid, info.gid, info.uname, info.gname = uid, uid, uname, uname
                    archive.addfile(info, io.BytesIO(payload))


def _members(path: Path) -> list[tarfile.TarInfo]:
    with tarfile.open(path, "r:gz") as archive:
        return archive.getmembers()


def _payloads(path: Path) -> dict[str, bytes]:
    out = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.isreg():
                out[member.name] = archive.extractfile(member).read()
    return out


class TestTwoBuildsBecomeOneArchive(unittest.TestCase):
    def test_archives_differing_only_in_envelope_normalize_to_the_same_bytes(self):
        """The measured defect, reduced to its three carriers.

        Different root mtime (the PAX record), different gzip header mtime,
        different builder identity, different member order. Same content.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "a.tar.gz", root / "b.tar.gz"
            _write_archive(
                first,
                mtime=1788437700.5630772,
                uid=501,
                uname="berkayturanci",
                gzip_mtime=1788437700,
            )
            _write_archive(
                second,
                mtime=1788437703.3743458,
                uid=1001,
                uname="runner",
                gzip_mtime=1788437703,
                order=list(reversed(list(CONTENT))),
            )
            self.assertNotEqual(first.read_bytes(), second.read_bytes())

            normalize_sdist.normalize(first, EPOCH)
            normalize_sdist.normalize(second, EPOCH)

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_the_content_is_untouched(self):
        """This rewrites the envelope. Not one byte a consumer unpacks may move."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tar.gz"
            _write_archive(path, mtime=1788437700.5, gzip_mtime=1788437700)
            before = _payloads(path)

            normalize_sdist.normalize(path, EPOCH)

            self.assertEqual(_payloads(path), before)
            self.assertEqual(before, CONTENT)

    def test_every_carrier_of_nondeterminism_is_gone(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tar.gz"
            _write_archive(path, mtime=1788437700.5630772, gzip_mtime=1788437700)

            count = normalize_sdist.normalize(path, EPOCH)

            self.assertEqual(count, len(CONTENT) + 1)  # the files plus the root dir
            members = _members(path)
            # 1. no PAX records survive — USTAR cannot express one.
            self.assertFalse(any(member.pax_headers for member in members))
            # 2. every mtime is the epoch, the root directory included.
            self.assertEqual({member.mtime for member in members}, {EPOCH})
            # 3. no builder identity.
            self.assertEqual({(member.uid, member.gid) for member in members}, {(0, 0)})
            self.assertEqual({(member.uname, member.gname) for member in members}, {("", "")})
            # ...and the two that make the *file* rather than a member differ.
            self.assertEqual([m.name for m in members], sorted(m.name for m in members))
            with open(path, "rb") as handle:
                header = handle.read(10)
            self.assertEqual(
                int.from_bytes(header[4:8], "little"), 0, "gzip header carries a clock"
            )
            # The gzip header must not carry the output filename either.
            self.assertEqual(header[3] & 0x08, 0, "gzip header carries the file name")

    def test_the_executable_bit_survives_but_the_umask_does_not(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tar.gz"
            _write_archive(path, mtime=1788437700.5, executable="pkg-1.0/README.md")

            normalize_sdist.normalize(path, EPOCH)

            modes = {member.name: member.mode for member in _members(path)}
            self.assertEqual(modes["pkg-1.0/README.md"], 0o755)
            self.assertEqual(modes["pkg-1.0/PKG-INFO"], 0o644)
            self.assertEqual(modes["pkg-1.0"], 0o755)

    def test_normalizing_twice_changes_nothing(self):
        """Idempotent, so a re-run of the publish step cannot alter the artifact."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tar.gz"
            _write_archive(path, mtime=1788437700.5, gzip_mtime=1788437700)

            normalize_sdist.normalize(path, EPOCH)
            once = path.read_bytes()
            normalize_sdist.normalize(path, EPOCH)

            self.assertEqual(path.read_bytes(), once)

    def test_a_different_epoch_produces_a_different_archive(self):
        """Vacuity: an implementation ignoring the epoch would pass the tests above."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "a.tar.gz", root / "b.tar.gz"
            for path in (first, second):
                _write_archive(path, mtime=1788437700.5, gzip_mtime=1788437700)

            normalize_sdist.normalize(first, EPOCH)
            normalize_sdist.normalize(second, EPOCH + 1)

            self.assertNotEqual(first.read_bytes(), second.read_bytes())


class TestTheTimestampIsNeverGuessed(unittest.TestCase):
    def test_the_flag_wins_over_the_environment(self):
        self.assertEqual(normalize_sdist.resolve_epoch("123", {"SOURCE_DATE_EPOCH": "456"}), 123)

    def test_the_environment_is_the_fallback(self):
        self.assertEqual(normalize_sdist.resolve_epoch(None, {"SOURCE_DATE_EPOCH": "456"}), 456)

    def test_an_absent_timestamp_is_refused_rather_than_defaulted(self):
        """Falling back to the clock would reintroduce the whole defect, silently."""
        for environ in ({}, {"SOURCE_DATE_EPOCH": ""}):
            with self.subTest(environ=environ):
                with self.assertRaises(ValueError) as caught:
                    normalize_sdist.resolve_epoch(None, environ)
                self.assertIn("SOURCE_DATE_EPOCH", str(caught.exception))

    def test_a_nonsense_timestamp_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_sdist.resolve_epoch("yesterday", {})
        with self.assertRaises(ValueError):
            normalize_sdist.resolve_epoch("-1", {})


class TestTheCommandLine(unittest.TestCase):
    def _exit_code(self, *argv: str) -> int:
        """Run the CLI, keeping its report out of the suite's output."""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return normalize_sdist.main(list(argv))

    def test_it_normalizes_every_archive_it_is_given(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = root / "a.tar.gz", root / "b.tar.gz"
            _write_archive(first, mtime=1788437700.5, gzip_mtime=1788437700)
            _write_archive(second, mtime=1788437703.3, gzip_mtime=1788437703, uname="runner")

            code = self._exit_code("--epoch", str(EPOCH), str(first), str(second))

            self.assertEqual(code, 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_a_missing_file_exits_one_rather_than_raising(self):
        with TemporaryDirectory() as tmp:
            code = self._exit_code("--epoch", str(EPOCH), str(Path(tmp) / "nope.tar.gz"))

            self.assertEqual(code, 1)

    def test_a_file_that_is_not_an_archive_exits_one(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tar.gz"
            path.write_bytes(b"not a tarball")

            self.assertEqual(self._exit_code("--epoch", str(EPOCH), str(path)), 1)

    def test_a_failed_run_leaves_the_original_in_place(self):
        """A half-written archive where a valid one used to be is the worse failure."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.tar.gz"
            path.write_bytes(b"not a tarball")

            self._exit_code("--epoch", str(EPOCH), str(path))

            self.assertEqual(path.read_bytes(), b"not a tarball")
            self.assertEqual(list(Path(tmp).iterdir()), [path], "a staging file was left behind")


if __name__ == "__main__":
    unittest.main()
