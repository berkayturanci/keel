"""Tests for keel's runtime workspace (the ``.keel`` dir, gitignore, scratch).

Covers the artifact-hygiene contract: keel writes runtime state only under
``.keel`` and scaffolds a ``.keel/.gitignore`` so none of it surfaces in the
consumer's checkout. The regression suite at the bottom exercises the real
runtime writers (checkpoint / activity / ledger / lock) and asserts no
root-level files appear.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from keel import activity, checkpoint, cli, lock, workspace


class TestKeelDir(unittest.TestCase):
    def test_keel_dir_under_root(self):
        self.assertEqual(workspace.keel_dir("/repo"), Path("/repo/.keel"))

    def test_keel_dir_defaults_to_cwd(self):
        self.assertEqual(workspace.keel_dir(), Path(".keel"))


class TestGitignoreBody(unittest.TestCase):
    def test_body_is_deterministic_and_lists_runtime_entries(self):
        body = workspace.runtime_gitignore_body()
        self.assertEqual(body, workspace.runtime_gitignore_body())
        for entry in workspace.RUNTIME_IGNORE_ENTRIES:
            self.assertIn(f"\n{entry}\n", f"\n{body}")
        self.assertTrue(body.endswith("\n"))
        # Committed config must never be ignored.
        self.assertNotIn("project.yaml", body)
        self.assertNotIn("extensions", body)


class TestEnsureGitignore(unittest.TestCase):
    def test_creates_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            keel = Path(tmp) / ".keel"
            keel.mkdir()
            self.assertTrue(workspace.ensure_runtime_gitignore(keel))
            gitignore = keel / ".gitignore"
            self.assertEqual(gitignore.read_text(encoding="utf-8"),
                             workspace.runtime_gitignore_body())

    def test_idempotent_second_call_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            keel = Path(tmp) / ".keel"
            keel.mkdir()
            self.assertTrue(workspace.ensure_runtime_gitignore(keel))
            self.assertFalse(workspace.ensure_runtime_gitignore(keel))

    def test_missing_directory_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(workspace.ensure_runtime_gitignore(Path(tmp) / ".keel"))

    def test_tops_up_missing_entries_preserving_operator_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            keel = Path(tmp) / ".keel"
            keel.mkdir()
            gitignore = keel / ".gitignore"
            gitignore.write_text("state/\n# my own rule\nbuild-cache/\n")
            self.assertTrue(workspace.ensure_runtime_gitignore(keel))
            text = gitignore.read_text(encoding="utf-8")
            # Operator content preserved.
            self.assertIn("# my own rule", text)
            self.assertIn("build-cache/", text)
            # Every runtime entry now present.
            for entry in workspace.RUNTIME_IGNORE_ENTRIES:
                self.assertIn(entry, text.splitlines())
            # And it is now complete.
            self.assertFalse(workspace.ensure_runtime_gitignore(keel))

    def test_appends_newline_when_existing_lacks_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            keel = Path(tmp) / ".keel"
            keel.mkdir()
            gitignore = keel / ".gitignore"
            gitignore.write_text("state/")  # no trailing newline, missing others
            self.assertTrue(workspace.ensure_runtime_gitignore(keel))
            lines = gitignore.read_text(encoding="utf-8").splitlines()
            self.assertIn("state/", lines)
            self.assertIn("activity/", lines)

    def test_empty_file_is_topped_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            keel = Path(tmp) / ".keel"
            keel.mkdir()
            gitignore = keel / ".gitignore"
            gitignore.write_text("")
            self.assertTrue(workspace.ensure_runtime_gitignore(keel))
            self.assertIn("scratch/", gitignore.read_text(encoding="utf-8").splitlines())


class TestEnsureGitignoreForArtifact(unittest.TestCase):
    def test_finds_keel_ancestor_and_scaffolds(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / ".keel" / "state" / "checkpoint.json"
            artifact.parent.mkdir(parents=True)
            self.assertTrue(workspace.ensure_runtime_gitignore_for(artifact))
            self.assertTrue((Path(tmp) / ".keel" / ".gitignore").exists())

    def test_no_keel_ancestor_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "elsewhere" / "out.json"
            artifact.parent.mkdir(parents=True)
            self.assertFalse(workspace.ensure_runtime_gitignore_for(artifact))


class TestScratchDir(unittest.TestCase):
    def test_creates_dir_and_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = workspace.scratch_dir(tmp)
            self.assertTrue(scratch.is_dir())
            self.assertEqual(scratch, Path(tmp) / ".keel" / "scratch")
            self.assertTrue((Path(tmp) / ".keel" / ".gitignore").exists())

    def test_no_create_does_not_touch_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = workspace.scratch_dir(tmp, create=False)
            self.assertFalse(scratch.exists())
            self.assertFalse((Path(tmp) / ".keel").exists())


class TestScratchDirCommand(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_prints_and_creates_scratch_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = self._run(["scratch-dir", "--root", tmp])
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), str(Path(tmp) / ".keel" / "scratch"))
            self.assertTrue((Path(tmp) / ".keel" / "scratch").is_dir())

    def test_no_create_flag_only_prints(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = self._run(["scratch-dir", "--root", tmp, "--no-create"])
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), str(Path(tmp) / ".keel" / "scratch"))
            self.assertFalse((Path(tmp) / ".keel").exists())


class TestRuntimeWritersStayUnderKeel(unittest.TestCase):
    """Regression: keel runtime writers create nothing at the consumer repo root.

    Reproduces issue #473 — a keel run must not leave ``plan.json`` /
    ``ship.json`` / ``pr_<n>.diff`` / checkpoint / activity files in the checkout
    root; everything lands under ``.keel`` and is gitignored.
    """

    def _root_entries(self, root: Path) -> set[str]:
        return {p.name for p in root.iterdir()}

    def test_checkpoint_activity_and_lock_only_touch_keel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            checkpoint.write_checkpoint(
                root / ".keel" / "state" / "checkpoint.json",
                checkpoint.build_checkpoint_record(
                    run_id="r1", command="ship", current_step="s4",
                    base_branch="main", target="473"),
            )
            activity.write_activity(
                root / ".keel" / "activity" / "r1.json",
                activity.build_activity_record(
                    command="ship", run_id="r1", phase="s4",
                    issue=473, pr=None, note=None),
            )
            lock.claim_resource(root / ".keel" / "state" / "locks", "merge",
                                owner="tester")

            # The only top-level entry the run created is `.keel`.
            self.assertEqual(self._root_entries(root), {".keel"})

            # The gitignore exists and ignores every runtime subtree we wrote.
            gitignore = (root / ".keel" / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertIn("state/", gitignore)
            self.assertIn("activity/", gitignore)
            self.assertIn("scratch/", gitignore)

            # And the runtime files really are under those ignored subtrees.
            self.assertTrue((root / ".keel" / "state" / "checkpoint.json").exists())
            self.assertTrue((root / ".keel" / "activity" / "r1.json").exists())

    def test_writer_outside_keel_does_not_scaffold_gitignore(self):
        # An operator-chosen explicit output path outside .keel is honoured as-is.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint.write_checkpoint(
                root / "explicit" / "checkpoint.json",
                checkpoint.build_checkpoint_record(
                    run_id="r1", command="ship", current_step="s0",
                    base_branch="main", target="473"),
            )
            self.assertTrue((root / "explicit" / "checkpoint.json").exists())
            self.assertFalse((root / ".keel").exists())


def _activity_dir_with(tmp: str, names_oldest_first: list[str]) -> Path:
    """Create activity .json records with strictly increasing mtime (oldest first)."""
    directory = Path(tmp) / ".keel" / "activity"
    directory.mkdir(parents=True)
    for i, name in enumerate(names_oldest_first):
        path = directory / name
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
    return directory


class TestCleanScratch(unittest.TestCase):
    def test_entries_and_clean_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = workspace.scratch_dir(tmp)
            (scratch / "pr-1.diff").write_text("x")
            (scratch / "sub").mkdir()
            self.assertEqual(workspace.scratch_entries(tmp), ["pr-1.diff", "sub"])
            removed = workspace.clean_scratch(tmp)
            self.assertEqual(removed, ["pr-1.diff", "sub"])
            self.assertTrue((Path(tmp) / ".keel" / "scratch").is_dir())
            self.assertEqual(workspace.scratch_entries(tmp), [])

    def test_clean_missing_scratch_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(workspace.scratch_entries(tmp), [])
            self.assertEqual(workspace.clean_scratch(tmp), [])


class TestPruneActivity(unittest.TestCase):
    def test_negative_keep_last_rejected(self):
        with self.assertRaises(ValueError):
            workspace.activity_prune_plan("anywhere", keep_last=-1)

    def test_missing_dir_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                workspace.activity_prune_plan(Path(tmp) / "nope", keep_last=2), [])

    def test_within_budget_keeps_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = _activity_dir_with(tmp, ["a.json", "b.json"])
            self.assertEqual(workspace.activity_prune_plan(directory, keep_last=2), [])
            self.assertEqual(workspace.prune_activity(directory, keep_last=5), [])

    def test_prunes_oldest_beyond_budget_and_ignores_non_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = _activity_dir_with(
                tmp, ["old1.json", "old2.json", "new1.json", "new2.json"])
            (directory / "notes.txt").write_text("keep me")  # non-json never counts
            plan = workspace.activity_prune_plan(directory, keep_last=2)
            self.assertEqual(plan, ["old1.json", "old2.json"])
            removed = workspace.prune_activity(directory, keep_last=2)
            self.assertEqual(removed, ["old1.json", "old2.json"])
            # Newest two records and the non-json file survive.
            self.assertEqual(
                sorted(p.name for p in directory.iterdir()),
                ["new1.json", "new2.json", "notes.txt"])

    def test_keep_zero_removes_all_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = _activity_dir_with(tmp, ["a.json", "b.json"])
            self.assertEqual(workspace.prune_activity(directory, keep_last=0),
                             ["a.json", "b.json"])


def _write_config(tmp: str, *, activity_path: str | None = None) -> str:
    target = Path(tmp) / ".keel" / "project.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["extends: keel", "core_version: '^1.0'", "base_branch: main",
             "knobs:", "  build_gate_cmd: 'true'"]
    if activity_path is not None:
        lines += ["policy_pack:", "  name: t", "  reports:",
                  f"    activity: '{activity_path}'"]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)


class TestGcCommand(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_missing_config_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = self._run(["gc", str(Path(tmp) / "nope.yaml"), "--root", tmp])
            self.assertEqual(rc, 1)
            self.assertIn("no such config", err)

    def test_invalid_config_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.yaml"
            bad.write_text("extends: keel\ncore_version: 5\n")  # core_version wrong type
            rc, _, err = self._run(["gc", str(bad), "--root", tmp])
            self.assertEqual(rc, 1)

    def test_dry_run_reports_without_removing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _write_config(tmp)
            scratch = workspace.scratch_dir(tmp)
            (scratch / "pr.diff").write_text("x")
            _activity_dir_with(tmp, ["a.json", "b.json", "c.json"])
            rc, out, _ = self._run(
                ["gc", cfg_path, "--root", tmp, "--keep-activity", "1",
                 "--dry-run", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["scratch_removed"], ["pr.diff"])
            self.assertEqual(payload["activity_removed"], ["a.json", "b.json"])
            # Nothing was actually removed.
            self.assertTrue((scratch / "pr.diff").exists())
            self.assertEqual(len(list((Path(tmp) / ".keel" / "activity").iterdir())), 3)

    def test_live_reclaims_scratch_and_activity_human_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _write_config(tmp)
            scratch = workspace.scratch_dir(tmp)
            (scratch / "pr.diff").write_text("x")
            _activity_dir_with(tmp, ["a.json", "b.json", "c.json"])
            rc, out, _ = self._run(["gc", cfg_path, "--root", tmp, "--keep-activity", "1"])
            self.assertEqual(rc, 0)
            self.assertIn("removed 1 entry", out)          # singular scratch entry
            self.assertIn("removed 2 record(s)", out)
            self.assertEqual(workspace.scratch_entries(tmp), [])
            self.assertTrue((Path(tmp) / ".keel" / "scratch").is_dir())
            self.assertEqual(
                [p.name for p in (Path(tmp) / ".keel" / "activity").iterdir()],
                ["c.json"])

    def test_plural_entries_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _write_config(tmp)
            scratch = workspace.scratch_dir(tmp)
            (scratch / "a").write_text("x")
            (scratch / "b").write_text("y")
            rc, out, _ = self._run(["gc", cfg_path, "--root", tmp, "--no-activity"])
            self.assertEqual(rc, 0)
            self.assertIn("removed 2 entries", out)
            self.assertNotIn("activity  :", out)

    def test_no_scratch_skips_scratch(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _write_config(tmp)
            scratch = workspace.scratch_dir(tmp)
            (scratch / "pr.diff").write_text("x")
            rc, out, _ = self._run(["gc", cfg_path, "--root", tmp, "--no-scratch"])
            self.assertEqual(rc, 0)
            self.assertNotIn("scratch   :", out)
            self.assertTrue((scratch / "pr.diff").exists())  # untouched

    def test_durable_artifacts_never_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _write_config(tmp)
            state = Path(tmp) / ".keel" / "state"
            state.mkdir(parents=True)
            (state / "checkpoint.json").write_text("{}")
            (state / "run-ledger.jsonl").write_text("{}\n")
            _activity_dir_with(tmp, ["a.json"])
            rc, _, _ = self._run(["gc", cfg_path, "--root", tmp, "--keep-activity", "0"])
            self.assertEqual(rc, 0)
            self.assertTrue((state / "checkpoint.json").exists())
            self.assertTrue((state / "run-ledger.jsonl").exists())

    def test_scratch_failure_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _write_config(tmp)
            with mock.patch.object(workspace, "clean_scratch",
                                   side_effect=OSError("disk gone")):
                rc, out, err = self._run(["gc", cfg_path, "--root", tmp, "--no-activity"])
            self.assertEqual(rc, 0)  # degrades, never aborts
            self.assertIn("degraded", err)
            self.assertIn("disk gone", err)

    def test_activity_failure_is_fail_soft_via_bad_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            # An absolute activity path makes resolve_dir raise ActivityError.
            cfg_path = _write_config(tmp, activity_path="/abs/activity")
            rc, _, err = self._run(["gc", cfg_path, "--root", tmp, "--no-scratch"])
            self.assertEqual(rc, 0)
            self.assertIn("degraded", err)
            self.assertIn("activity", err)

    def test_path_traversal_payloads_in_activity_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = _write_config(tmp, activity_path="../../etc/shadow")
            rc, _, err = self._run(["gc", cfg_path, "--root", tmp, "--no-scratch"])
            self.assertEqual(rc, 0)
            self.assertIn("degraded", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

