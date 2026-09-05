"""Unit tests for mkdir-based resource claims and merge lock compatibility."""

import tempfile
import unittest
import unittest.mock as mock
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
            self.assertTrue(path.exists())  # still held
            denied = lk.claim_resource(d, "shared", owner="agent-b")
            self.assertFalse(denied.granted)  # and agent-b cannot take it

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

    def test_a_failed_gitignore_scaffold_leaves_no_claim_behind(self):
        # #1077: the directory is created before the claim is initialised. A failure in
        # between used to leave it there — held by <unknown>, released by nobody.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(
                lk.workspace,
                "ensure_runtime_gitignore_for",
                side_effect=PermissionError("read-only .keel"),
            ):
                with self.assertRaises(PermissionError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertFalse(path.exists())
            reclaimed = lk.claim_resource(d, "merge", owner="agent-b")
            self.assertTrue(reclaimed.granted)
            self.assertEqual(reclaimed.holder, "agent-b")

    def test_a_failed_owner_write_leaves_no_claim_behind(self):
        # The half-written owner.json goes too, so the retry does not inherit it.
        def _tear_the_owner_file(path, owner, **_kw):
            (path / "owner.json").write_text('{"owner":', encoding="utf-8")
            raise OSError("no space left on device")

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk, "_write_owner", side_effect=_tear_the_owner_file):
                with self.assertRaises(OSError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertFalse(path.exists())
            reclaimed = lk.claim_resource(d, "merge", owner="agent-b")
            self.assertTrue(reclaimed.granted)
            self.assertEqual(reclaimed.holder, "agent-b")

    def test_unremovable_partial_claim_still_reports_the_original_failure(self):
        # Cleanup is best-effort: when it cannot finish, the caller must still see the
        # failure that started it rather than the rmdir's own OSError.
        def _litter_then_fail(artifact_path):
            (Path(artifact_path) / "stray").write_text("x", encoding="utf-8")
            raise PermissionError("read-only .keel")

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(
                lk.workspace, "ensure_runtime_gitignore_for", side_effect=_litter_then_fail
            ):
                with self.assertRaises(PermissionError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertTrue(path.exists())

    def test_unwind_leaves_a_claim_another_owner_took_meanwhile(self):
        # The dangerous interleaving: agent-a stalls mid-initialisation, an operator runs
        # the any-owner recovery on the ownerless directory, agent-b claims the resource,
        # and only then does agent-a reach its handler. The unwind must not delete b's
        # live claim just because a created *a* directory at that path.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")

            def _released_and_reclaimed_by_b(artifact_path):
                lk.release_resource(d, "merge")
                Path(artifact_path).mkdir(parents=True)
                lk._write_owner(Path(artifact_path), "agent-b")
                raise PermissionError("read-only .keel")

            with mock.patch.object(
                lk.workspace,
                "ensure_runtime_gitignore_for",
                side_effect=_released_and_reclaimed_by_b,
            ):
                with self.assertRaises(PermissionError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertTrue(path.exists())
            self.assertTrue((path / "owner.json").exists())
            self.assertEqual(lk._holder(path), "agent-b")
            contended = lk.claim_resource(d, "merge", owner="agent-c")
            self.assertFalse(contended.granted)
            self.assertEqual(contended.holder, "agent-b")

    def test_unwind_leaves_a_same_named_claim_taken_meanwhile(self):
        # An owner is a *name*, not a claim id: merge_lock always claims as "merge-lock"
        # and `keel merge` derives one name per PR, so the claim the unwind must not touch
        # commonly carries the same string. Same interleaving as above — released by the
        # any-owner recovery, re-claimed behind us — but under agent-a's own name. Only
        # the directory's identity separates the two, and it must survive.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")

            def _released_and_reclaimed_under_the_same_name(artifact_path):
                lk.release_resource(d, "merge")
                Path(artifact_path).mkdir(parents=True)
                lk._write_owner(Path(artifact_path), "agent-a")
                raise PermissionError("read-only .keel")

            with mock.patch.object(
                lk.workspace,
                "ensure_runtime_gitignore_for",
                side_effect=_released_and_reclaimed_under_the_same_name,
            ):
                with self.assertRaises(PermissionError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertTrue(path.exists())
            self.assertEqual(lk._holder(path), "agent-a")
            contended = lk.claim_resource(d, "merge", owner="agent-c")
            self.assertFalse(contended.granted)
            self.assertEqual(contended.holder, "agent-a")

    def test_unwind_leaves_a_claim_that_has_only_been_created(self):
        # The ownerless window is not ours alone: between another caller's mkdir and its
        # finished owner.json its live claim reads as <unknown> too. Deleting "whatever is
        # ownerless" would hand the resource to a third run while that caller believes it
        # holds it.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")

            def _released_then_only_mkdir_by_b(artifact_path):
                lk.release_resource(d, "merge")
                Path(artifact_path).mkdir(parents=True)
                raise PermissionError("read-only .keel")

            with mock.patch.object(
                lk.workspace,
                "ensure_runtime_gitignore_for",
                side_effect=_released_then_only_mkdir_by_b,
            ):
                with self.assertRaises(PermissionError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertTrue(path.exists())
            self.assertEqual(lk._holder(path), lk.UNKNOWN_HOLDER)
            contended = lk.claim_resource(d, "merge", owner="agent-c")
            self.assertFalse(contended.granted)

    def test_unwind_leaves_a_claim_whose_owner_file_is_half_written(self):
        # The same window's other half: `_write_owner` is a plain write_text, so a torn
        # owner.json also reads as <unknown> while the writer is live.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")

            def _released_then_torn_owner_by_b(artifact_path):
                lk.release_resource(d, "merge")
                Path(artifact_path).mkdir(parents=True)
                (Path(artifact_path) / "owner.json").write_text('{"owner": "agen', encoding="utf-8")
                raise PermissionError("read-only .keel")

            with mock.patch.object(
                lk.workspace,
                "ensure_runtime_gitignore_for",
                side_effect=_released_then_torn_owner_by_b,
            ):
                with self.assertRaises(PermissionError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertTrue(path.exists())
            self.assertEqual(lk._holder(path), lk.UNKNOWN_HOLDER)
            self.assertTrue((path / "owner.json").exists())

    def test_unwind_is_a_no_op_when_the_claim_was_already_recovered(self):
        # The operator cleared the ownerless directory and nothing took the resource. The
        # unwind finds no directory to identify and simply does nothing — no crash, and the
        # resource stays free.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")

            def _released_by_the_operator(artifact_path):
                lk.release_resource(d, "merge")
                raise PermissionError("read-only .keel")

            with mock.patch.object(
                lk.workspace,
                "ensure_runtime_gitignore_for",
                side_effect=_released_by_the_operator,
            ):
                with self.assertRaises(PermissionError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertFalse(path.exists())
            self.assertTrue(lk.claim_resource(d, "merge", owner="agent-b").granted)

    def test_unwind_declines_when_the_directory_cannot_be_identified(self):
        # Fail closed: with no identity recorded (the stat right after mkdir failed) the
        # unwind removes nothing. A leaked claim an operator can release beats deleting a
        # directory we cannot prove is ours.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk, "_identity", return_value=None):
                with mock.patch.object(
                    lk.workspace,
                    "ensure_runtime_gitignore_for",
                    side_effect=PermissionError("read-only .keel"),
                ):
                    with self.assertRaises(PermissionError):
                        lk.claim_resource(d, "merge", owner="agent-a")

            self.assertTrue(path.exists())
            self.assertEqual(lk._holder(path), lk.UNKNOWN_HOLDER)
            # Recoverable the documented way.
            self.assertEqual(lk.release_resource(d, "merge").status, "released")

    def test_the_unwind_still_runs_where_the_directory_cannot_be_pinned(self):
        # Windows cannot open a directory, so there is no descriptor to hold the inode
        # with. The identity check falls back to what stat alone reports and the unwind
        # still has to happen — the pin narrows a window, it is not the guard.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk.os, "open", side_effect=PermissionError("no dir fd")):
                with mock.patch.object(
                    lk.workspace,
                    "ensure_runtime_gitignore_for",
                    side_effect=PermissionError("read-only .keel"),
                ):
                    with self.assertRaises(PermissionError):
                        lk.claim_resource(d, "merge", owner="agent-a")

            self.assertFalse(path.exists())
            self.assertTrue(lk.claim_resource(d, "merge", owner="agent-b").granted)

    def test_the_unpinned_unwind_removes_no_file_and_leaves_a_written_owner_file(self):
        # Without a descriptor no unlink is bound to an inode, so the unpinned unwind
        # removes no file: a directory holding our own owner file stays as the
        # documented leak rather than risk a by-name unlink of somebody else's.
        def _write_then_fail(path, owner, **_kw):
            (path / "owner.json").write_text('{"owner": "agent-a"}', encoding="utf-8")
            raise OSError("no space left on device")

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk.os, "open", side_effect=PermissionError("no dir fd")):
                with mock.patch.object(lk, "_write_owner", side_effect=_write_then_fail):
                    with self.assertRaises(OSError):
                        lk.claim_resource(d, "merge", owner="agent-a")

            self.assertTrue(path.exists())
            self.assertEqual(lk._holder(path), "agent-a")

    def test_the_unpinned_unwind_never_unlinks_a_strangers_owner_file(self):
        # Round-4 finding: a by-name unlink after a single matching stat is the same
        # check-then-act defect. Swap in a stranger's live claim right after the first
        # identity read of the unwind; its owner file must survive.
        real = lk._identity
        reads: list[int] = []

        def _swap_after_the_first_unwind_read(path):
            reads.append(1)
            answer = real(path)
            if len(reads) == 2:
                path.rmdir()  # ours is empty here; rmtree would need the patched os.open
                path.mkdir()
                lk._write_owner(path, "agent-b")
            return answer

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk.os, "open", side_effect=PermissionError("no dir fd")):
                with mock.patch.object(
                    lk, "_identity", side_effect=_swap_after_the_first_unwind_read
                ):
                    with mock.patch.object(
                        lk.workspace,
                        "ensure_runtime_gitignore_for",
                        side_effect=PermissionError("read-only .keel"),
                    ):
                        with self.assertRaises(PermissionError):
                            lk.claim_resource(d, "merge", owner="agent-a")

            self.assertEqual(lk._holder(path), "agent-b")

    def test_a_claim_taken_between_the_mkdir_and_the_pin_is_not_adopted(self):
        # Round-4 finding: the pin latches whatever sits at the path, which can already
        # be a stranger's claim under the same owner name. Anything in the directory is
        # proof it is not ours: contention is reported and nothing is unwound.
        real_pin = lk._pin

        def _retarget_then_pin(path):
            path.rmdir()
            path.mkdir()
            lk._write_owner(path, "agent-a")
            return real_pin(path)

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk, "_pin", side_effect=_retarget_then_pin):
                with mock.patch.object(lk.workspace, "ensure_runtime_gitignore_for") as scaffold:
                    result = lk.claim_resource(d, "merge", owner="agent-a")

            self.assertFalse(result.granted)
            self.assertEqual(result.reason, "resource-already-claimed")
            self.assertEqual(result.holder, "agent-a")
            scaffold.assert_not_called()
            self.assertEqual(lk._holder(path), "agent-a")

    def test_an_unlistable_directory_is_an_io_failure_not_a_claim(self):
        # Round-5 finding: reading "could not list" as "empty" adopted a stranger's
        # claim. It is an I/O failure now: it raises, and nothing is unwound.
        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk.os, "listdir", side_effect=PermissionError("no read")):
                with self.assertRaises(PermissionError):
                    lk.claim_resource(d, "merge", owner="agent-a")
            self.assertTrue(path.exists())

    def test_a_claim_taken_during_the_scaffold_is_never_overwritten(self):
        # Round-5 finding: the write that finishes a claim was by name, so a claim
        # taken underneath while the scaffold ran was overwritten and ours reported
        # granted. Through the pin the exclusive create lands in the directory this
        # call made — which the operator has removed — so it fails instead.
        def _released_and_reclaimed_by_b(artifact_path):
            path = Path(artifact_path)
            path.rmdir()
            path.mkdir()
            lk._write_owner(path, "agent-b")

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(
                lk.workspace,
                "ensure_runtime_gitignore_for",
                side_effect=_released_and_reclaimed_by_b,
            ):
                with self.assertRaises(FileNotFoundError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertEqual(lk._holder(path), "agent-b")

    def test_the_unpinned_claim_reports_contention_instead_of_overwriting(self):
        # Without a descriptor the create is by name but exclusive: the stranger's
        # owner file makes it fail, and that is reported as contention, not granted.
        def _released_and_reclaimed_by_b(artifact_path):
            path = Path(artifact_path)
            path.rmdir()
            path.mkdir()
            lk._write_owner(path, "agent-b")

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk.os, "open", side_effect=PermissionError("no dir fd")):
                with mock.patch.object(
                    lk.workspace,
                    "ensure_runtime_gitignore_for",
                    side_effect=_released_and_reclaimed_by_b,
                ):
                    result = lk.claim_resource(d, "merge", owner="agent-a")

            self.assertFalse(result.granted)
            self.assertEqual(result.reason, "resource-already-claimed")
            self.assertEqual(result.holder, "agent-b")
            self.assertEqual(lk._holder(path), "agent-b")

    def test_a_swap_between_the_owner_unlink_and_the_rmdir_is_caught(self):
        # The by-name ``rmdir`` re-reads the identity right before it runs. Answer it
        # with a foreign identity on that read alone and the directory must survive.
        real = lk._identity
        reads: list[int] = []

        def _foreign_on_the_last_read(path):
            reads.append(1)
            answer = real(path)
            if len(reads) == 3 and answer is not None:
                return (answer[0], answer[1] + 1, answer[2])
            return answer

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk, "_identity", side_effect=_foreign_on_the_last_read):
                with mock.patch.object(
                    lk.workspace,
                    "ensure_runtime_gitignore_for",
                    side_effect=PermissionError("read-only .keel"),
                ):
                    with self.assertRaises(PermissionError):
                        lk.claim_resource(d, "merge", owner="agent-a")

            self.assertEqual(len(reads), 3)
            self.assertTrue(path.exists())

    def test_unwind_leaves_our_own_directory_once_it_names_someone_else(self):
        # The owner check is the second condition, not a leftover: a directory that still
        # is the one we created but by now records another owner is left alone too.
        def _stamp_another_owner_then_fail(path, owner, **_kw):
            (path / "owner.json").write_text('{"owner": "agent-b"}\n', encoding="utf-8")
            raise OSError("no space left on device")

        with tempfile.TemporaryDirectory() as d:
            path = lk.resource_path(d, "merge")
            with mock.patch.object(lk, "_write_owner", side_effect=_stamp_another_owner_then_fail):
                with self.assertRaises(OSError):
                    lk.claim_resource(d, "merge", owner="agent-a")

            self.assertTrue(path.exists())
            self.assertEqual(lk._holder(path), "agent-b")

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
