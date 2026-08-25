"""The defense-in-depth layers #868/#870/#872 specified and did not ship (#932).

None of these was an open hole: each issue's primary attack was closed and
mutation-killed. What was missing is the second layer each issue explicitly
asked for — the one that keeps the first true when the next input arrives.
Filed together because they share a shape: a multi-requirement issue where
requirement 1 shipped and the rest were read as optional.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from keel import evidence, install, swarm, workspace


class HeaderScanningStopsWhereAsked(unittest.TestCase):
    """#868 req 2: stop scanning at the first non-header or blank line.

    Only req 1 (parse the top block) and req 3 (tests) shipped. `_fields` broke
    on a non-header line *only if* a header had already been seen, so it walked
    past prose and kept looking.
    """

    def test_fields_after_prose_are_not_harvested(self):
        body = "Some prose line here.\n\nhead: 0000000\nvendor: spoofed\n"

        self.assertEqual({}, evidence._fields(body))

    def test_a_prose_reviewer_line_no_longer_keys_the_comment(self):
        # The reachable consequence: `_reviewer_key` calls `_fields` with no
        # marker requirement, so a comment whose *prose* said "reviewer: x" was
        # keyed to x. Narrow — it needs a trusted author — but it is the residual
        # of the class #868 named.
        body = "Thanks! I think reviewer: someone-else should look at this.\n"

        self.assertNotIn("reviewer", evidence._fields(body))

    def test_a_real_verdict_header_still_parses_in_full(self):
        # The counterweight: stopping early must not cost the real format.
        body = (
            "keel.review-verdict.v1\n"
            "reviewer: claude-victor\n"
            "head: abc123\n"
            "vendor: anthropic\n"
            "model: opus\n"
            "\n"
            "Verdict: pass\n"
            "Findings:\n- none\n"
        )

        fields = evidence._fields(body)

        self.assertEqual("claude-victor", fields["reviewer"])
        self.assertEqual("abc123", fields["head"])
        self.assertEqual("anthropic", fields["vendor"])
        self.assertEqual("opus", fields["model"])

    def test_leading_blank_lines_are_skipped_not_treated_as_the_end(self):
        # A comment body routinely begins with a newline; breaking there would
        # reject legitimate verdicts.
        body = "\n\n  \nreviewer: claude\nhead: 123\n"

        self.assertEqual({"reviewer": "claude", "head": "123"}, evidence._fields(body))


class LegacyWrappersAreWrittenOnlyUnderTheirOwnDirectory(unittest.TestCase):
    """#870 req 2: destination paths must resolve strictly under the target dir.

    Only req 1 (a regex on legacy names) shipped. Both inputs to the relative
    path are gated today, so no traversal can be constructed — this is the layer
    that keeps that true when a third input appears.
    """

    def test_a_destination_that_escapes_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError) as caught:
                install._contained_destination(
                    root, Path("../outside.md"), agent="claude", name="evil"
                )

        self.assertIn("resolves outside", str(caught.exception))

    def test_a_destination_in_a_sibling_directory_is_refused(self):
        # Under the root but not under the *commands* directory — the check is
        # anchored to the write root, not merely to the project.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                install._contained_destination(
                    Path(tmp), Path(".claude/settings.json"), agent="claude", name="evil"
                )

    def test_the_ordinary_destination_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            dest = install._contained_destination(
                root,
                Path(install.LEGACY_CLAUDE_DIR) / "ship.md",
                agent="claude",
                name="ship.md",
            )

        self.assertEqual("ship.md", dest.name)

    def test_the_skills_surface_is_anchored_to_its_own_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ok = install._contained_destination(
                root,
                Path(install.SKILLS_DIR) / "source-command-ship" / "SKILL.md",
                agent="skills",
                name="source-command-ship",
            )
            with self.assertRaises(ValueError):
                install._contained_destination(
                    root,
                    Path(install.LEGACY_CLAUDE_DIR) / "ship.md",
                    agent="skills",
                    name="ship.md",
                )

        self.assertEqual("SKILL.md", ok.name)


class RuntimeWritesAreAtomicAndDurable(unittest.TestCase):
    """#872: `os.replace` makes the swap atomic; it does not make the bytes durable.

    The issue's own Impact section named power loss, and only the atomicity half
    shipped. The third writer — swarm run state — was still a bare `write_text`,
    because each writer carried its own copy of the dance instead of sharing one.
    """

    def test_the_content_is_fsynced_before_the_rename(self):
        order = []
        real_fsync, real_replace = os.fsync, os.replace

        def spy_fsync(fd):
            order.append("fsync")
            return real_fsync(fd)

        def spy_replace(src, dst):
            order.append("replace")
            return real_replace(src, dst)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            with patch("os.fsync", spy_fsync), patch("os.replace", spy_replace):
                workspace.write_text_atomic(target, '{"a": 1}\n')

            self.assertEqual('{"a": 1}\n', target.read_text(encoding="utf-8"))

        # Data fsynced first, then the swap. A rename before the fsync is the
        # exact ordering that survives a crash with an empty file.
        self.assertEqual("fsync", order[0])
        self.assertEqual("replace", order[1])

    def test_a_failed_write_leaves_the_previous_file_and_no_debris(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            target.write_text("original\n", encoding="utf-8")

            with patch("os.fsync", side_effect=OSError("disk gone")):
                with self.assertRaises(OSError):
                    workspace.write_text_atomic(target, "replacement\n")

            self.assertEqual("original\n", target.read_text(encoding="utf-8"))
            self.assertEqual(
                ["state.json"],
                sorted(p.name for p in Path(tmp).iterdir()),
                "a temp file was left behind",
            )

    def test_the_directory_entry_is_synced_after_the_rename(self):
        """Covers the success path on every platform, including Windows.

        Windows cannot `os.open` a directory, so a test that relies on the real
        call leaves these lines unexecuted there — which dropped the Windows
        coverage run under the 100% bar while #953's masked job hid the failure.
        Faking the handle keeps the assertion about *ordering and cleanup*, which
        is the part that is platform-independent.
        """
        synced, closed = [], []
        with (
            patch("os.open", return_value=4242),
            patch("os.fsync", synced.append),
            patch("os.close", closed.append),
        ):
            flushed = workspace._fsync_directory(Path("."))

        self.assertTrue(flushed)
        self.assertIn(4242, synced)
        self.assertEqual([4242], closed, "the directory handle must be closed")

    def test_a_directory_that_cannot_be_opened_is_not_an_error(self):
        with patch("os.open", side_effect=OSError("Windows says no")):
            self.assertFalse(workspace._fsync_directory(Path(".")))

    def test_a_directory_fsync_failure_does_not_fail_the_write(self):
        # Windows cannot open a directory with os.open. Failing the write over a
        # durability hint we cannot request there would trade a real guarantee on
        # POSIX for a broken one everywhere.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"

            real_open = os.open

            def refuse_directories(path, *args, **kwargs):
                # Only the directory fsync is made to fail; mkstemp needs os.open
                # to keep working, so a blanket patch would test the wrong thing.
                if Path(path).is_dir():
                    raise OSError("cannot open a directory here")
                return real_open(path, *args, **kwargs)

            with patch("os.open", refuse_directories):
                workspace.write_text_atomic(target, "written\n")

            self.assertEqual("written\n", target.read_text(encoding="utf-8"))

    def test_swarm_state_goes_through_the_same_writer(self):
        # The writer this issue is about: it was a bare `write_text`.
        state = swarm.SwarmRunState(swarm_id="sw-1", total_workers=2)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                workspace, "write_text_atomic", wraps=workspace.write_text_atomic
            ) as atomic:
                path = swarm.save_swarm_state(state, root=tmp)

            atomic.assert_called_once()
            self.assertEqual("sw-1", json.loads(path.read_text(encoding="utf-8"))["swarm_id"])


if __name__ == "__main__":
    unittest.main()
