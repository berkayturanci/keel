"""Unit tests for the mkdir-based merge lock."""

import tempfile
import unittest
from pathlib import Path

from keel.lock import LockError, merge_lock


class TestMergeLock(unittest.TestCase):
    def test_acquire_and_release(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "merge.lock"
            with merge_lock(lock) as held:
                self.assertTrue(held.exists())
            self.assertFalse(lock.exists())  # released

    def test_double_acquire_raises(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "merge.lock"
            with merge_lock(lock):
                second_lock = merge_lock(lock)
                with self.assertRaises(LockError):
                    second_lock.__enter__()

    def test_reacquire_after_release(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "merge.lock"
            with merge_lock(lock):
                pass
            with merge_lock(lock):  # should not raise
                pass

    def test_released_on_exception(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "merge.lock"
            try:
                with merge_lock(lock):
                    raise ValueError("boom")
            except ValueError:
                pass
            else:
                self.fail("expected ValueError")
            self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
