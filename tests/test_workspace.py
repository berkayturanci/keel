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
import re
import subprocess
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from keel import activity, checkpoint, cli, lock, swarm_runtime, workspace


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


class TestGitActuallyIgnoresWhatKeelWrites(unittest.TestCase):
    """Ask git, and ask it about the paths the code really writes.

    The assertions elsewhere in this file iterate ``RUNTIME_IGNORE_ENTRIES`` and
    check each member is in the file — true for whatever the tuple happens to
    contain, and therefore blind to a runtime directory missing from it. That is
    how ``worktrees/`` went unignored while a swarm run left one full working
    copy per worker in ``git status`` (#877).

    These derive the path from the writer (``swarm_runtime.build_worktree_path``) and
    put the question to git itself.
    """

    def _repo(self, tmp: str) -> Path:
        root = Path(tmp)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".keel").mkdir()
        workspace.ensure_runtime_gitignore(root / ".keel")
        return root

    def _untracked(self, root: Path) -> list[str]:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, check=True,
        )
        return [
            line[3:] for line in proc.stdout.splitlines()
            if line.startswith("??") and not line[3:].startswith(".keel/.gitignore")
        ]

    def test_git_ignores_a_swarm_worktree_at_the_path_swarm_writes_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            # Not a literal: the path comes from the function swarm calls.
            path = swarm_runtime.build_worktree_path("swarm-1", "cluster-1-42", root=root)
            path.mkdir(parents=True)
            (path / "src.py").write_text("x = 1\n", encoding="utf-8")

            self.assertEqual([], self._untracked(root))

    #: Subtrees of ``.keel`` that are committed on purpose, so the sweep below
    #: does not demand they be ignored. Anything found in the source that is in
    #: neither this set nor ``RUNTIME_IGNORE_ENTRIES`` is unclassified, and the
    #: test says so rather than guessing.
    COMMITTED_SUBTREES = frozenset({"extensions"})

    #: How the source spells a ``.keel`` subtree. Kept as patterns rather than a
    #: list of names, because a hand-maintained list of names is the same thing
    #: the tuple already is — and the reason #877 went unnoticed.
    _SUBTREE_PATTERNS = (
        r'"\.keel/([a-z_]+)',
        r'"\.keel"\s*/\s*"([a-z_]+)"',
        r'KEEL_DIRNAME\s*/\s*"([a-z_]+)"',
        r'keel_dir\([^)]*\)\s*/\s*"([a-z_]+)"',
    )

    def _subtrees_in_source(self) -> dict[str, set[str]]:
        src = Path(workspace.__file__).resolve().parent
        found: dict[str, set[str]] = {}
        for path in sorted(src.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for pattern in self._SUBTREE_PATTERNS:
                for match in re.finditer(pattern, text):
                    found.setdefault(match.group(1), set()).add(path.name)
        return found

    def test_every_keel_subtree_in_the_source_is_classified(self):
        """Derived from the source, so the next one cannot be forgotten.

        An earlier version of this test listed four paths by hand — which is the
        same hand-maintained list as the tuple it was meant to police, and would
        have missed #877 exactly as the tuple did. This scans the package for
        every ``.keel/<name>`` construction and requires each to be either
        ignored at runtime or declared committed. A new subtree in neither is a
        failure, not a silent omission.
        """
        ignored = {entry.strip("/") for entry in workspace.RUNTIME_IGNORE_ENTRIES}
        found = self._subtrees_in_source()
        self.assertTrue(found, "the scan found nothing; the patterns have gone stale")
        unclassified = {
            name: sorted(files)
            for name, files in found.items()
            if name not in ignored and name not in self.COMMITTED_SUBTREES
        }
        self.assertEqual(
            {}, unclassified,
            "a .keel subtree is written by the source but is neither ignored at "
            f"runtime nor declared committed: {unclassified}",
        )

    def test_every_runtime_directory_keel_creates_is_ignored(self):
        """One live case per ignored entry, asked of git rather than the tuple."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            written = {
                "state": root / ".keel" / "state" / "checkpoint.json",
                "activity": root / ".keel" / "activity" / "r1.json",
                "scratch": root / ".keel" / "scratch" / "pr-1.diff",
                "worktrees": swarm_runtime.build_worktree_path("s1", "c1", root=root) / "f.py",
                # The one entry with no writer to derive from. Without a case it
                # is the next `worktrees/` — an unverified member of a tuple
                # whose other members are all covered.
                "*.tmp": root / ".keel" / "half-written.tmp",
            }
            for path in written.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")

            self.assertEqual(
                [], self._untracked(root),
                "a runtime path keel writes to is not covered by .keel/.gitignore",
            )

    def test_a_stray_file_outside_those_subtrees_is_still_visible(self):
        """The counterweight: over-broad ignores would hide a real stray file.

        `.keel/` must not be blanket-ignored — `project.yaml` and `extensions/`
        are committed config, and an operator noticing an unexpected file there
        is the point.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / ".keel" / "project.yaml").write_text("extends: keel\n", encoding="utf-8")
            (root / ".keel" / "extensions").mkdir()
            (root / ".keel" / "extensions" / "e.yaml").write_text("x: 1\n", encoding="utf-8")
            (root / "plan.json").write_text("{}", encoding="utf-8")

            self.assertEqual(
                sorted(self._untracked(root)),
                [".keel/extensions/e.yaml", ".keel/project.yaml", "plan.json"],
            )

    def test_an_extensions_directory_named_worktrees_stays_visible(self):
        """The entry is anchored, and this is what the anchor is for.

        Unanchored, ``worktrees/`` matches at any depth — including
        ``.keel/extensions/<ext>/worktrees/``, and ``extensions/`` is committed
        config that the comment above the tuple calls "intentionally absent".
        A third-party extension is far likelier to contain a directory called
        ``worktrees`` than one called ``scratch``, so the looseness that stays
        theoretical for the other entries is not theoretical for this one.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            ext = root / ".keel" / "extensions" / "myext" / "worktrees"
            ext.mkdir(parents=True)
            (ext / "template.yaml").write_text("x: 1\n", encoding="utf-8")
            swarm = swarm_runtime.build_worktree_path("s1", "c1", root=root)
            swarm.mkdir(parents=True)
            (swarm / "f.py").write_text("x = 1\n", encoding="utf-8")

            visible = set(self._untracked(root))
            self.assertIn(
                ".keel/extensions/myext/worktrees/template.yaml", visible,
                "the entry reaches into extensions/, which is committed config",
            )
            self.assertNotIn(
                str(swarm.relative_to(root)) + "/f.py", visible,
                "the swarm worktree it exists for is no longer ignored",
            )

    def test_the_entries_cannot_hide_committed_config_wherever_they_land(self):
        """Guards the over-fix the previous test only appears to guard.

        Adding ``.keel/`` to the tuple looks like the obvious wholesale mistake,
        but the entries are written *into* ``.keel/.gitignore``, so there it
        matches only a nested ``.keel/.keel/`` and the counterweight above sails
        through. The mistake that actually hides config is a blanket rule, or an
        unanchored entry reaching into ``extensions/``. Both are checked here by
        writing each entry into the **root** gitignore as well.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / ".gitignore").write_text(
                "\n".join(workspace.RUNTIME_IGNORE_ENTRIES) + "\n", encoding="utf-8"
            )
            for rel in (".keel/project.yaml", ".keel/extensions/e.yaml"):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")

            visible = set(self._untracked(root))
            for rel in (".keel/project.yaml", ".keel/extensions/e.yaml"):
                with self.subTest(path=rel):
                    self.assertIn(
                        rel, visible,
                        "a runtime ignore entry hides committed config when it "
                        "lands in a gitignore above .keel/",
                    )


class TestTheDocsPrintTheFileKeelActuallyWrites(unittest.TestCase):
    """`artifacts.md` reproduces the generated gitignore verbatim.

    A reader comparing their own `.keel/.gitignore` against that block is doing
    the right thing, so the block has to be the real one. Nothing pinned it, and
    it silently fell a release behind — the page told a consumer an entry keel
    had just added should not be there.
    """

    DOC = Path(__file__).resolve().parent.parent / "docs/keel/artifacts.md"

    def _block(self) -> str:
        text = self.DOC.read_text(encoding="utf-8")
        start = text.index("```gitignore\n") + len("```gitignore\n")
        return text[start : text.index("```", start)]

    def test_the_printed_block_is_the_generated_body(self):
        generated = "".join(
            line
            for line in workspace.runtime_gitignore_body().splitlines(True)
            if not line.startswith("#")
        )
        self.assertEqual(
            self._block(), generated,
            "docs/keel/artifacts.md prints a gitignore that is not the one keel writes",
        )

    def test_the_directory_table_lists_every_ignored_subtree(self):
        text = self.DOC.read_text(encoding="utf-8")
        for entry in workspace.RUNTIME_IGNORE_ENTRIES:
            name = entry.strip("/")
            if name.startswith("*"):
                continue  # a glob, not a directory the table can name
            with self.subTest(entry=entry):
                self.assertIn(
                    f"`.keel/{name}/", text,
                    f".keel/{name}/ is ignored but the artifacts table never mentions it",
                )


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

