"""Unit tests for mkdir-based resource claims and merge lock compatibility."""

import tempfile
import unittest
from pathlib import Path

from keel import lock as lk
from keel.lock import LockError, merge_lock


class TestResourceClaims(unittest.TestCase):
    def test_contract_declares_structured_single_host_claims(self):
        contract = lk.contract_as_dict()

        self.assertEqual(contract["schema_version"], "keel.resource-claim.v1")
        self.assertEqual(contract["scope"], "single-host")
        self.assertEqual(contract["primitive"], "mkdir")
        self.assertEqual(contract["deny_mode"], "structured-feedback")
        self.assertTrue(contract["merge_lock_consumer"])

    def test_claim_denies_concurrent_owner_with_structured_feedback(self):
        with tempfile.TemporaryDirectory() as d:
            first = lk.claim_resource(d, "review:pr-1", owner="agent-a")
            second = lk.claim_resource(d, "review:pr-1", owner="agent-b")

            self.assertTrue(first.granted)
            self.assertEqual(first.status, "granted")
            self.assertEqual(first.holder, "agent-a")
            self.assertFalse(second.granted)
            self.assertEqual(second.status, "denied")
            self.assertEqual(second.reason, "resource-already-claimed")
            self.assertEqual(second.holder, "agent-a")
            self.assertEqual(second.as_dict()["schema_version"], "keel.resource-claim.v1")

    def test_release_and_reclaim(self):
        with tempfile.TemporaryDirectory() as d:
            first = lk.claim_resource(d, "issue-227", owner="agent-a")
            released = lk.release_resource(d, "issue-227", owner="agent-a")
            second = lk.claim_resource(d, "issue-227", owner="agent-b")

            self.assertTrue(first.granted)
            self.assertEqual(released.status, "released")
            self.assertTrue(second.granted)
            self.assertEqual(second.holder, "agent-b")

    def test_release_missing_claim_is_structured_feedback(self):
        with tempfile.TemporaryDirectory() as d:
            released = lk.release_resource(d, "missing", owner="agent-a")

            self.assertFalse(released.granted)
            self.assertEqual(released.status, "missing")
            self.assertEqual(released.reason, "resource-not-claimed")

    def test_release_by_non_owner_is_denied_without_deleting_claim(self):
        with tempfile.TemporaryDirectory() as d:
            lk.claim_resource(d, "shared", owner="agent-a")
            denied = lk.release_resource(d, "shared", owner="agent-b")
            still_denied = lk.claim_resource(d, "shared", owner="agent-c")

            self.assertEqual(denied.status, "not-owner")
            self.assertEqual(denied.holder, "agent-a")
            self.assertEqual(still_denied.status, "denied")
            self.assertEqual(still_denied.holder, "agent-a")

    def test_release_without_owner_releases_any_claim(self):
        with tempfile.TemporaryDirectory() as d:
            lk.claim_resource(d, "shared", owner="agent-a")
            released = lk.release_resource(d, "shared")

            self.assertEqual(released.status, "released")
            self.assertEqual(released.owner, "any-owner")

    def test_release_without_owner_metadata_still_releases_unowned(self):
        # An `owner=None` release is the deliberate any-owner escape for clearing a
        # stuck claim, so it works even when nobody's name is recorded.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "manual")
            path.mkdir()
            released = lk.release_resource(d, "manual")

            self.assertEqual(released.status, "released")
            self.assertEqual(released.holder, lk.UNKNOWN_HOLDER)

    def test_an_unreadable_owner_refuses_a_named_release(self):
        # The claim directory exists, so the resource IS held — we just cannot name the
        # holder. Treating that as "unheld" let a second run release a live merge claim
        # and take the lock (#631). It must refuse, exactly as a mismatched name does.
        with tempfile.TemporaryDirectory() as d:
            lk.claim_resource(d, "shared", owner="agent-a")
            path = lk.resource_path(d, "shared")
            (path / "owner.json").write_text("{", encoding="utf-8")
            released = lk.release_resource(d, "shared", owner="agent-b")

            self.assertEqual(released.status, "not-owner")
            self.assertEqual(released.holder, lk.UNKNOWN_HOLDER)
            self.assertTrue(path.exists())          # still held
            denied = lk.claim_resource(d, "shared", owner="agent-b")
            self.assertFalse(denied.granted)        # and agent-b cannot take it

    def test_a_missing_owner_file_refuses_a_named_release(self):
        # The reachable version: `_claim_path` mkdirs before writing owner.json, so a
        # crash in that window leaves the lock held but ownerless. No disk corruption
        # required.
        with tempfile.TemporaryDirectory() as d:
            lk.claim_resource(d, "shared", owner="agent-a")
            path = lk.resource_path(d, "shared")
            (path / "owner.json").unlink()
            released = lk.release_resource(d, "shared", owner="agent-b")

            self.assertEqual(released.status, "not-owner")
            self.assertTrue(path.exists())
            # The any-owner escape still clears it.
            self.assertEqual(lk.release_resource(d, "shared").status, "released")

    def test_release_raises_when_claim_directory_is_not_empty(self):
        with tempfile.TemporaryDirectory() as d:
            lk.claim_resource(d, "shared", owner="agent-a")
            path = lk.resource_path(d, "shared")
            (path / "extra").write_text("leftover", encoding="utf-8")

            with self.assertRaises(OSError):
                lk.release_resource(d, "shared", owner="agent-a")

            (path / "extra").unlink()
            lk.release_resource(d, "shared", owner="agent-a")

    def test_resource_claim_context_releases_granted_claim_only(self):
        with tempfile.TemporaryDirectory() as d:
            with lk.resource_claim(d, "shared", owner="agent-a") as first:
                self.assertTrue(first.granted)
                with lk.resource_claim(d, "shared", owner="agent-b") as second:
                    self.assertFalse(second.granted)
            reclaimed = lk.claim_resource(d, "shared", owner="agent-c")

            self.assertTrue(reclaimed.granted)

    def test_resource_path_is_deterministic_and_sanitized(self):
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "Review PR #1")

            self.assertEqual(path, lk.resource_path(d, "Review PR #1"))
            self.assertEqual(path.parent, Path(d))
            self.assertTrue(path.name.startswith("review-pr-1-"))
            self.assertTrue(path.name.endswith(".lock"))


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
            caught = False
            try:
                with merge_lock(lock):
                    raise ValueError("boom")
            except ValueError:
                caught = True
            self.assertTrue(caught)
            self.assertFalse(lock.exists())

    def test_merge_lock_release_is_best_effort(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "merge.lock"
            with merge_lock(lock) as held:
                (held / "leftover").write_text("x", encoding="utf-8")

            self.assertTrue(lock.exists())
            (lock / "leftover").unlink()
            lock.rmdir()


if __name__ == "__main__":
    unittest.main()
