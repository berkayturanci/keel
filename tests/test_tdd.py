"""Unit tests for the test-first s4 profile and its pure commit-order gate (#1020).

Every test here is a pure-function test: commit lists in, a verdict out. That is the
point of the split — the gate is decided by :mod:`keel.tdd` against records, and the one
git read that produces those records lives behind :func:`keel.git.commit_log`, so nothing
in this module needs a repository, a clock, or a network.
"""

import unittest

from keel import tdd


def _commit(sha, *files, subject="work", merge=False, status=tdd.MODIFIED):
    """A commit touching ``files``; every path gets ``status`` unless it is a pair.

    ``_commit("a", "tests/x.py", ("D", "tests/y.py"))`` — a bare string is the common
    case (the path was written), a ``(status, path)`` pair spells the status out.
    """
    changes = tuple(
        tdd.Change(*entry) if isinstance(entry, tuple) else tdd.Change(status, entry)
        for entry in files
    )  # a pair is (status, path); a triple adds a rename source
    return tdd.Commit(sha=sha, subject=subject, changes=changes, merge=merge)


class TestResolveMode(unittest.TestCase):
    def test_unset_is_the_single_pass_default(self):
        mode = tdd.resolve_mode(None)
        self.assertEqual(mode.name, tdd.DEFAULT_MODE)
        self.assertFalse(mode.is_tdd)
        self.assertEqual(mode.source, "default")

    def test_configured_tdd_names_the_knob_it_came_from(self):
        mode = tdd.resolve_mode("tdd")
        self.assertTrue(mode.is_tdd)
        self.assertEqual(mode.source, "knobs.implement_mode")

    def test_flag_selects_tdd_for_one_run(self):
        mode = tdd.resolve_mode(None, flag=True)
        self.assertTrue(mode.is_tdd)
        self.assertEqual(mode.source, "flag:--tdd")

    def test_flag_wins_over_a_project_already_configured_for_tdd(self):
        # There is no --no-tdd: a flag can only select the stricter profile, so a project
        # that configured the contract cannot have it switched off from a command line.
        self.assertEqual(tdd.resolve_mode("tdd", flag=True).source, "flag:--tdd")

    def test_blank_and_non_string_values_read_as_default(self):
        for configured in ("", "   ", 7, [], None):
            with self.subTest(configured=configured):
                self.assertEqual(tdd.resolve_mode(configured).name, tdd.DEFAULT_MODE)

    def test_default_mode_publishes_no_phases_and_no_gate(self):
        record = tdd.resolve_mode(None).as_dict()
        self.assertEqual(
            record,
            {
                "mode": "default",
                "tdd": False,
                "source": "default",
                "phases": [],
                "gate": None,
            },
        )

    def test_tdd_mode_publishes_both_phases_and_the_gate_it_adds(self):
        record = tdd.resolve_mode("tdd").as_dict()
        self.assertEqual(record["phases"], ["tests", "implementation"])
        self.assertEqual(record["gate"], tdd.GATE_ID)


class TestTestGlobs(unittest.TestCase):
    def test_no_policy_pack_declares_nothing(self):
        self.assertEqual(tdd.test_globs(None), ())
        self.assertEqual(tdd.test_globs({}), ())
        self.assertEqual(tdd.test_globs({"test_groups": "nope"}), ())

    def test_group_selectors_are_the_fallback(self):
        pack = {"test_groups": {"unit": {"command": "make test", "paths": ["tests/**"]}}}
        self.assertEqual(tdd.test_globs(pack), ("tests/**",))

    def test_declared_test_paths_exclude_the_implementation_surface(self):
        # keel's own `unit` group selects src/** as well as tests/**. Read as test paths
        # those would make the gate vacuous, so a declared `test_paths` replaces them.
        pack = {
            "test_groups": {
                "unit": {
                    "command": "make test",
                    "paths": ["src/**", "tests/**"],
                    "test_paths": ["tests/**"],
                },
                "validate": {"command": "make validate", "paths": ["projects/**"]},
            }
        }
        self.assertEqual(tdd.test_globs(pack), ("tests/**",))

    def test_the_fallback_is_whole_config_not_per_group(self):
        """The documented rule, pinned: one `test_paths` silences every group's `paths`.

        `configuration.md` says so explicitly because the alternative reading — per-group
        fallback — is the natural one and is wrong: it would re-import exactly the
        implementation surface `test_paths` exists to exclude. A group whose tests should
        count declares its own `test_paths`.
        """
        pack = {
            "test_groups": {
                "unit": {"command": "x", "paths": ["src/**"], "test_paths": ["tests/**"]},
                "e2e": {"command": "y", "paths": ["e2e/**"]},
            }
        }
        self.assertEqual(tdd.test_globs(pack), ("tests/**",))

        # …and with no `test_paths` anywhere, every group's selectors count.
        del pack["test_groups"]["unit"]["test_paths"]
        self.assertEqual(tdd.test_globs(pack), ("e2e/**", "src/**"))

    def test_duplicate_globs_collapse_in_declaration_order(self):
        pack = {
            "test_groups": {
                "a": {"command": "x", "test_paths": ["tests/**", "spec/**"]},
                "b": {"command": "y", "test_paths": ["spec/**"]},
            }
        }
        self.assertEqual(tdd.test_globs(pack), ("tests/**", "spec/**"))

    def test_malformed_groups_and_entries_contribute_nothing(self):
        pack = {
            "test_groups": {
                "broken": ["not", "a", "mapping"],
                "stringy": {"command": "x", "paths": "tests/**"},
                "blanks": {"command": "x", "paths": ["  ", 4, "tests/**"]},
            }
        }
        self.assertEqual(tdd.test_globs(pack), ("tests/**",))


class TestIsTestPath(unittest.TestCase):
    def test_matches_nested_paths_under_a_glob(self):
        self.assertTrue(tdd.is_test_path("tests/unit/test_x.py", ["tests/**"]))
        self.assertFalse(tdd.is_test_path("src/keel/tdd.py", ["tests/**"]))

    def test_no_globs_matches_nothing(self):
        self.assertFalse(tdd.is_test_path("tests/test_x.py", []))


class TestParseCommits(unittest.TestCase):
    def _log(self, *records):
        return "".join(records)

    def _record(self, sha, parents, subject, files):
        """One `--format` header plus its `--name-status` body.

        ``files`` entries are paths (status ``M``) or ``"<status>\t<path>"`` lines.
        """
        head = f"{tdd.RECORD_SEP}{sha}{tdd.FIELD_SEP}{parents}{tdd.FIELD_SEP}{subject}\n"
        body = "".join(f"{path}\n" if "\t" in path else f"M\t{path}\n" for path in files)
        return head + "\n" + body

    def test_unreadable_history_stays_none(self):
        # `None` is git failing. Collapsing it to () here would let an unreadable branch
        # read as an empty one, which is the conflation the whole gate exists to refuse.
        self.assertIsNone(tdd.parse_commits(None))

    def test_empty_output_is_an_empty_branch(self):
        self.assertEqual(tdd.parse_commits(""), ())
        self.assertEqual(tdd.parse_commits("\n\n"), ())

    def test_parses_sha_subject_and_files(self):
        text = self._log(
            self._record("a" * 40, "p" * 40, "test(x): failing tests", ["tests/test_x.py"]),
            self._record("b" * 40, "a" * 40, "feat(x): implement", ["src/x.py", "docs/x.md"]),
        )
        commits = tdd.parse_commits(text)
        self.assertEqual([c.sha for c in commits], ["a" * 40, "b" * 40])
        self.assertEqual(commits[0].subject, "test(x): failing tests")
        self.assertEqual(commits[0].files, ("tests/test_x.py",))
        self.assertEqual(commits[1].files, ("src/x.py", "docs/x.md"))
        self.assertFalse(commits[0].merge)

    def test_two_parents_mark_a_merge(self):
        text = self._record("c" * 40, f"{'a' * 40} {'b' * 40}", "Merge main", [])
        self.assertTrue(tdd.parse_commits(text)[0].merge)

    def test_a_commit_touching_nothing_keeps_an_empty_file_list(self):
        text = self._record("d" * 40, "a" * 40, "chore: empty", [])
        self.assertEqual(tdd.parse_commits(text)[0].files, ())
        self.assertEqual(tdd.parse_commits(text)[0].changes, ())

    def test_statuses_are_parsed_and_a_rename_records_its_destination(self):
        text = self._record(
            "f" * 40,
            "a" * 40,
            "refactor: move",
            ["A\ttests/new.py", "D\ttests/old.py", "R100\ttests/a.py\ttests/b.py"],
        )
        commit = tdd.parse_commits(text)[0]
        self.assertEqual(
            [(c.status, c.path, c.source) for c in commit.changes],
            [
                ("A", "tests/new.py", None),
                ("D", "tests/old.py", None),
                # The origin is kept: after a move out of the test tree it is the only
                # mention of the test that was removed.
                ("R", "tests/b.py", "tests/a.py"),
            ],
        )
        # `present` is "the path exists after this commit" — everything but a delete, so a
        # rename counts as written rather than as removing its source.
        self.assertEqual([c.present for c in commit.changes], [True, False, True])
        self.assertTrue(commit.changes[1].deleted)

    def test_a_body_line_that_is_not_a_change_is_skipped(self):
        # A bare line with no status column cannot be a `--name-status` change; git does
        # not emit one, and reading it as a path would invent a touched file.
        head = f"{tdd.RECORD_SEP}{'g' * 40}{tdd.FIELD_SEP}{'a' * 40}{tdd.FIELD_SEP}feat: x\n"
        text = head + "\nno-tab-here\nA\tsrc/x.py\n"
        self.assertEqual(tdd.parse_commits(text)[0].files, ("src/x.py",))

    def test_a_change_line_with_a_blank_status_or_path_is_skipped(self):
        self.assertIsNone(tdd._change("\ttests/x.py"))
        self.assertIsNone(tdd._change("A\t   "))

    def test_a_record_without_a_sha_is_skipped(self):
        # Both spellings of "nothing to read here": a chunk that is only separators
        # (git's own leading empty split) and one that carries a subject but no sha.
        text = (
            f"{tdd.RECORD_SEP}{tdd.FIELD_SEP}{tdd.FIELD_SEP}truncated\n\nsrc/x.py\n"
            + self._record("e" * 40, "a" * 40, "feat: real", ["src/x.py"])
        )
        self.assertEqual([c.sha for c in tdd.parse_commits(text)], ["e" * 40])

    def test_short_renders_seven_characters(self):
        self.assertEqual(_commit("0123456789abcdef").short, "0123456")

    def test_as_dict_is_json_stable(self):
        self.assertEqual(
            _commit("abc", ("A", "tests/a.py"), subject="test: a").as_dict(),
            {
                "sha": "abc",
                "subject": "test: a",
                "changes": [{"status": "A", "path": "tests/a.py", "source": None}],
                "files": ["tests/a.py"],
                "merge": False,
            },
        )


TESTS = ("tests/**",)


class TestCheckOrder(unittest.TestCase):
    def test_tests_first_then_implementation_passes(self):
        result = tdd.check_order(
            [_commit("a" * 40, "tests/test_x.py"), _commit("b" * 40, "src/x.py")],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.code, tdd.OK)
        self.assertEqual(result.tests_commit, "a" * 40)
        self.assertEqual(result.implementation_commit, "b" * 40)
        self.assertIn("tests committed first", result.message)

    def test_unreadable_history_blocks(self):
        result = tdd.check_order(None, test_globs=TESTS)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, tdd.UNREADABLE_HISTORY)

    def test_a_project_declaring_no_test_paths_blocks_with_the_fix(self):
        result = tdd.check_order([_commit("a" * 40, "tests/x.py")], test_globs=())
        self.assertFalse(result.ok)
        self.assertEqual(result.code, tdd.NO_TEST_PATHS)
        self.assertIn("policy_pack.test_groups", result.message)

    def test_a_branch_with_no_commits_blocks(self):
        self.assertEqual(tdd.check_order([], test_globs=TESTS).code, tdd.NO_COMMITS)

    def test_merges_are_not_the_first_commit(self):
        # A merge from the base branch carries every path the base moved; judging it
        # would report the base's implementation as this implementer's first commit.
        result = tdd.check_order(
            [
                _commit("m" * 40, "src/base.py", merge=True),
                _commit("a" * 40, "tests/test_x.py"),
                _commit("b" * 40, "src/x.py"),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.tests_commit, "a" * 40)

    def test_a_branch_of_merges_alone_has_no_commit_to_verify(self):
        result = tdd.check_order([_commit("m" * 40, "src/x.py", merge=True)], test_globs=TESTS)
        self.assertEqual(result.code, tdd.NO_COMMITS)

    def test_an_empty_first_commit_cannot_be_the_tests_commit(self):
        result = tdd.check_order(
            [_commit("a" * 40), _commit("b" * 40, "src/x.py")], test_globs=TESTS
        )
        self.assertEqual(result.code, tdd.EMPTY_FIRST_COMMIT)
        self.assertEqual(result.tests_commit, "a" * 40)

    def test_implementation_in_the_first_commit_names_the_offending_paths(self):
        result = tdd.check_order(
            [_commit("a" * 40, "tests/test_x.py", "src/x.py"), _commit("b" * 40, "src/y.py")],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, tdd.IMPLEMENTATION_FIRST)
        self.assertEqual(result.offending, ("src/x.py",))
        self.assertIn("src/x.py", result.message)
        self.assertIn("tests/**", result.message)

    def test_a_first_commit_that_only_deletes_tests_is_not_writing_them(self):
        # `git rm tests/test_a.py` then edit src: every path in the first commit is a test
        # path, so a name-only view called this test-first. Removing the failing tests is
        # the inverse of the phase.
        result = tdd.check_order(
            [
                _commit("a" * 40, ("D", "tests/test_a.py")),
                _commit("b" * 40, ("M", "src/x.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, tdd.NO_TESTS_ADDED)
        self.assertEqual(result.offending, ("tests/test_a.py",))

    def test_a_first_commit_may_delete_a_test_while_adding_one(self):
        result = tdd.check_order(
            [
                _commit("a" * 40, ("A", "tests/test_new.py"), ("D", "tests/test_old.py")),
                _commit("b" * 40, ("M", "src/x.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertTrue(result.ok)

    def test_a_rename_counts_as_written_not_as_deleted(self):
        result = tdd.check_order(
            [
                _commit("a" * 40, ("R", "tests/test_b.py", "tests/test_a.py")),
                _commit("b" * 40, ("M", "src/x.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertTrue(result.ok)

    def test_renaming_a_test_out_of_the_test_tree_is_a_removal(self):
        # `git mv tests/test_a.py src/legacy_test_a.py` is `git rm` wearing a rename: the
        # suite that was red in phase A stops collecting the test either way, and the
        # destination path alone cannot show it.
        result = tdd.check_order(
            [
                _commit("a" * 40, ("A", "tests/test_a.py")),
                _commit(
                    "b" * 40,
                    ("M", "src/x.py"),
                    ("R", "src/legacy_test_a.py", "tests/test_a.py"),
                ),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, tdd.TESTS_DELETED)
        # The *source* is named: it is the path that stopped being a test.
        self.assertEqual(result.offending, ("tests/test_a.py",))
        self.assertIn("renamed out of the test paths", result.message)

    def test_a_rename_within_the_test_tree_stays_ordinary(self):
        result = tdd.check_order(
            [
                _commit("a" * 40, ("A", "tests/test_a.py")),
                _commit(
                    "b" * 40,
                    ("M", "src/x.py"),
                    ("R", "tests/unit/test_a.py", "tests/test_a.py"),
                ),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertTrue(result.ok)

    def test_a_copy_out_of_the_test_tree_is_not_a_removal(self):
        # `C` leaves the source in place, so nothing was removed — unlike `R`.
        result = tdd.check_order(
            [
                _commit("a" * 40, ("A", "tests/test_a.py")),
                _commit("b" * 40, ("M", "src/x.py"), ("C", "src/copy.py", "tests/test_a.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertTrue(result.ok)

    def test_renaming_a_non_test_path_out_is_ordinary_work(self):
        result = tdd.check_order(
            [
                _commit("a" * 40, ("A", "tests/test_a.py")),
                _commit("b" * 40, ("R", "src/new.py", "src/old.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertTrue(result.ok)

    def test_a_first_commit_renaming_a_test_out_blocks_as_implementation_first(self):
        # Rule 4 needs no change: the destination is not a test path, so the first-commit
        # rule already refuses it — with the message that names the path to move.
        result = tdd.check_order(
            [
                _commit("a" * 40, ("R", "src/legacy_test_a.py", "tests/test_a.py")),
                _commit("b" * 40, ("M", "src/x.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertEqual(result.code, tdd.IMPLEMENTATION_FIRST)
        self.assertEqual(result.offending, ("src/legacy_test_a.py",))

    def test_deleting_the_failing_tests_in_the_implementation_commit_blocks(self):
        result = tdd.check_order(
            [
                _commit("a" * 40, ("A", "tests/test_a.py")),
                _commit("b" * 40, ("M", "src/x.py"), ("D", "tests/test_a.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, tdd.TESTS_DELETED)
        self.assertEqual(result.offending, ("tests/test_a.py",))
        self.assertIn("tests/test_a.py", result.message)

    def test_deleting_the_tests_in_any_later_commit_blocks(self):
        # Not only "the implementation commit": a third commit that quietly removes them
        # is the same evasion one step further along.
        result = tdd.check_order(
            [
                _commit("a" * 40, ("A", "tests/test_a.py")),
                _commit("b" * 40, ("M", "src/x.py")),
                _commit("c" * 40, ("D", "tests/test_a.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertEqual(result.code, tdd.TESTS_DELETED)

    def test_deleting_a_non_test_path_later_is_ordinary_work(self):
        result = tdd.check_order(
            [
                _commit("a" * 40, ("A", "tests/test_a.py")),
                _commit("b" * 40, ("M", "src/x.py"), ("D", "src/old.py")),
            ],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertTrue(result.ok)

    def test_tests_only_branch_never_reached_phase_b(self):
        result = tdd.check_order(
            [_commit("a" * 40, "tests/test_x.py"), _commit("b" * 40, "tests/test_y.py")],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertEqual(result.code, tdd.NO_IMPLEMENTATION_COMMIT)
        self.assertIsNone(result.implementation_commit)

    def test_a_later_empty_commit_is_not_an_implementation(self):
        result = tdd.check_order(
            [_commit("a" * 40, "tests/test_x.py"), _commit("b" * 40)],
            test_globs=TESTS,
        )
        self.assertEqual(result.code, tdd.NO_IMPLEMENTATION_COMMIT)

    def test_red_gates_block_even_with_a_perfect_commit_order(self):
        result = tdd.check_order(
            [_commit("a" * 40, "tests/test_x.py"), _commit("b" * 40, "src/x.py")],
            test_globs=TESTS,
            gates_green=False,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, tdd.GATES_RED)
        self.assertEqual(result.implementation_commit, "b" * 40)

    def test_an_unmeasured_gate_run_judges_the_order_alone(self):
        result = tdd.check_order(
            [_commit("a" * 40, "tests/test_x.py"), _commit("b" * 40, "src/x.py")],
            test_globs=TESTS,
        )
        self.assertTrue(result.ok)

    def test_as_dict_is_json_stable(self):
        result = tdd.check_order(
            [_commit("a" * 40, "tests/t.py", "src/x.py"), _commit("b" * 40, "src/y.py")],
            test_globs=TESTS,
        )
        record = result.as_dict()
        self.assertEqual(record["code"], tdd.IMPLEMENTATION_FIRST)
        self.assertEqual(record["offending"], ["src/x.py"])
        self.assertEqual(record["test_globs"], ["tests/**"])
        self.assertIsNone(record["implementation_commit"])


class TestPhaseImplementers(unittest.TestCase):
    def test_the_run_implementer_covers_both_phases(self):
        self.assertEqual(
            tdd.phase_implementers(default="codex (gpt-5)"),
            {"tests": "codex (gpt-5)", "implementation": "codex (gpt-5)"},
        )

    def test_a_named_phase_overrides_the_default(self):
        self.assertEqual(
            tdd.phase_implementers([("implementation", "claude")], default="codex"),
            {"tests": "codex", "implementation": "claude"},
        )

    def test_an_unknown_phase_name_is_dropped(self):
        self.assertEqual(
            tdd.phase_implementers([("review", "claude")], default=None),
            {"tests": None, "implementation": None},
        )


class TestPhaseRecords(unittest.TestCase):
    def test_no_result_means_the_run_had_no_tdd_phases(self):
        self.assertIsNone(tdd.phase_records(None))

    def test_both_phases_are_recorded_in_order(self):
        result = tdd.check_order(
            [_commit("a" * 40, "tests/test_x.py"), _commit("b" * 40, "src/x.py")],
            test_globs=TESTS,
            gates_green=True,
        )
        self.assertEqual(
            tdd.phase_records(result),
            [
                {"phase": "tests", "commit": "a" * 40, "implementer": None},
                {"phase": "implementation", "commit": "b" * 40, "implementer": None},
            ],
        )

    def test_each_phase_records_the_implementer_that_ran_it(self):
        result = tdd.check_order(
            [_commit("a" * 40, ("A", "tests/test_x.py")), _commit("b" * 40, ("M", "src/x.py"))],
            test_globs=TESTS,
            gates_green=True,
        )
        seats = tdd.phase_implementers([("implementation", "claude")], default="codex")
        records = tdd.phase_records(result, implementers=seats)
        # Two different providers is exactly what the record has to be able to show:
        # "the same provider wrote the tests and the implementation" is the profile's
        # premise, and nothing else in the ledger could contradict it.
        self.assertEqual([r["implementer"] for r in records], ["codex", "claude"])

    def test_a_missing_half_records_null_rather_than_vanishing(self):
        result = tdd.check_order([], test_globs=TESTS)
        self.assertEqual(
            tdd.phase_records(result),
            [
                {"phase": "tests", "commit": None, "implementer": None},
                {"phase": "implementation", "commit": None, "implementer": None},
            ],
        )


if __name__ == "__main__":
    unittest.main()
