"""The read side of capture: retrieved learnings reach both briefs (#1155).

`capture.retrieve_relevant_learnings` has existed and been documented as reading
`.keel/learning/` since the capture contract was written, and **nothing in `src/keel`
called it**. Every learning keel wrote was write-only: an agent implementing issue N
never saw that issue N-40 hit the same trap, so the capture half of the backbone
produced artifacts no step consumed.

Two things measured here that the issue did not ask for:

* **The diff is empty on the run that most needs a lesson.** Retrieval happens at s3,
  before s4 has written a line, so scoring against `git diff` alone matches on the
  title and nothing else. `--declared-file` is the only file list that exists that
  early, and it is what makes a path match possible at all.
* **Text scoring alone ranks the wrong file.** A lesson that says "ledger" eight
  times outscored the one that declared `changed_files: [src/keel/ledger.py]` for a
  task touching exactly that path. Front matter is matched exactly, and weighted
  above prose, because declaring a path is a claim and repeating a word is not.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from keel import capture, findings, ship
from keel import config as cfg


def run(argv):
    """Invoke the CLI in-process and capture its streams.

    Written here rather than imported from another test module: `unittest discover
    -s tests` loads these as top-level modules, so a relative import works under
    one runner and not the other.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


def cli_main(argv):
    from keel import cli

    return cli.main(argv)


def _config(learning: dict | None) -> cfg.ProjectConfig:
    capture_policy: dict = {"enabled": True, "mode": "extension"}
    if learning is not None:
        capture_policy["learning"] = learning
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.1",
        knobs={},
        owner="berkayturanci",
        repo="keel",
        base_branch="main",
        policy_pack={"capture": capture_policy},
    )


#: A learning as the sink writes one. Built from parts so a test can vary exactly
#: one field without restating the document.
def learning_document(
    *,
    title: str,
    description: str = "",
    fingerprint: str = "",
    labels: tuple[str, ...] = (),
    changed_files: tuple[str, ...] = (),
    body: str = "",
) -> str:
    return capture.render_learning_document(
        title=title,
        description=description,
        pr_number=None,
        issue_number=None,
        repo="keel",
        date="2026-09-09",
        labels=list(labels),
        changed_files=list(changed_files),
        fingerprint=fingerprint,
        what_changed=body,
    )


class TheSourceIsWhereTheSinkWrites(unittest.TestCase):
    """Unset, the read path and the write path are the same directory."""

    def test_no_configuration_at_all_uses_the_convention(self):
        self.assertEqual(capture.learning_source_dirs(None), [".keel/learning"])
        self.assertEqual(capture.learning_source_dirs(_config(None)), [".keel/learning"])

    def test_a_sink_path_becomes_the_default_source(self):
        """One place a project's learnings live, not two settings to keep in step."""
        config = _config({"sink": {"kind": "markdown-dir", "path": "~/k/learnings"}})
        self.assertEqual(capture.learning_source_dirs(config), ["~/k/learnings"])

    def test_an_explicit_source_wins_over_the_sink(self):
        config = _config(
            {"source": "elsewhere", "sink": {"kind": "markdown-dir", "path": "~/k/learnings"}}
        )
        self.assertEqual(capture.learning_source_dirs(config), ["elsewhere"])

    def test_a_list_reads_several_directories_in_order(self):
        config = _config({"source": [".keel/learning", "~/shared"]})
        self.assertEqual(capture.learning_source_dirs(config), [".keel/learning", "~/shared"])

    def test_a_repeated_directory_is_read_once(self):
        config = _config({"source": ["a", "a", "b"]})
        self.assertEqual(capture.learning_source_dirs(config), ["a", "b"])

    def test_placeholders_expand(self):
        config = _config({"source": "~/k/{owner}/{repo}/{base_branch}"})
        self.assertEqual(
            capture.learning_source_dirs(
                config,
                values={"owner": "berkayturanci", "repo": "keel", "base_branch": "main"},
            ),
            ["~/k/berkayturanci/keel/main"],
        )

    def test_a_placeholder_nothing_expanded_drops_the_directory(self):
        """A per-document sink path is not a folder called `{pr}`.

        `{pr}`, `{date}` and `{slug}` name one file. Inherited as a *source* they
        cannot resolve, and taking them literally would create a directory whose
        name is a template and report that retrieval found nothing.
        """
        config = _config({"sink": {"kind": "markdown-dir", "path": "~/k/{repo}/{pr}"}})
        self.assertEqual(capture.learning_source_dirs(config, values={"repo": "keel"}), [])

    def test_junk_entries_are_ignored(self):
        self.assertEqual(capture.learning_source_dirs(_config({"source": [1, "b"]})), ["b"])
        self.assertEqual(capture.learning_source_dirs(_config({"source": 7})), [".keel/learning"])
        self.assertEqual(capture.learning_source_dirs(_config({"source": ["  "]})), [])


class OneLessonTakesOneSlot(unittest.TestCase):
    """A shared folder synced into a checkout is the normal way a lesson is in two
    directories at once, and five slots is not many to spend twice on one of them."""

    def hit(self, **kwargs):
        return {"file": "a.md", "path": "/a/a.md", "fingerprint": "fp-1", "score": 9, **kwargs}

    def test_the_same_fingerprint_under_two_paths_is_one_lesson(self):
        hits = [self.hit(), self.hit(path="/b/a.md")]
        self.assertEqual(len(capture.dedupe_learning_hits(hits)), 1)

    def test_the_better_ranked_copy_is_the_one_kept(self):
        """The caller sorts before deduping, so the first is the best."""
        hits = [self.hit(score=9, path="/a/a.md"), self.hit(score=2, path="/b/a.md")]
        self.assertEqual(capture.dedupe_learning_hits(hits)[0]["path"], "/a/a.md")

    def test_different_lessons_are_both_kept(self):
        hits = [self.hit(), self.hit(fingerprint="fp-2", path="/b/b.md")]
        self.assertEqual(len(capture.dedupe_learning_hits(hits)), 2)

    def test_a_file_with_no_fingerprint_falls_back_to_its_path(self):
        hits = [self.hit(fingerprint=""), self.hit(fingerprint="", path="/b/a.md")]
        self.assertEqual(len(capture.dedupe_learning_hits(hits)), 2)
        self.assertEqual(len(capture.dedupe_learning_hits([self.hit(fingerprint="")] * 2)), 1)


class ATypoInTheSourceIsRefusedWhereTheConfigIsRead(unittest.TestCase):
    """Its only other symptom is a retrieval that finds nothing, on every run."""

    def errors(self, source):
        return capture.learning_source_errors(source)

    def test_a_correct_source_has_no_errors(self):
        for source in (None, [], "a", ["a", "b"], "~/k/{owner}/{repo}/{base_branch}"):
            with self.subTest(source=source):
                self.assertEqual(self.errors(source), [])

    def test_an_unknown_placeholder_is_named(self):
        for bad in ("{repoo}", "{Repo}", "{base-branch}", "{}"):
            with self.subTest(placeholder=bad):
                errors = self.errors(f"~/k/{bad}")
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("unknown placeholder", errors[0])

    def test_a_document_placeholder_is_refused_here_though_the_sink_allows_it(self):
        """The two sets differ, and the difference is the point.

        `{pr}` is legal in a sink path and meaningless in a source directory. A
        shared placeholder list would have accepted it and read nothing forever.
        """
        for name in ("pr", "date", "slug"):
            with self.subTest(placeholder=name):
                self.assertEqual(capture.learning_sink_errors({"path": f"a/{{{name}}}"}), [])
                self.assertEqual(len(self.errors(f"a/{{{name}}}")), 1)

    def test_a_non_string_entry_is_refused(self):
        self.assertEqual(len(self.errors([1])), 1)
        self.assertEqual(len(self.errors(["  "])), 1)
        self.assertEqual(len(self.errors(7)), 1)

    def test_the_config_loader_refuses_it(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "project.yaml"
            path.write_text(
                "extends: keel\ncore_version: '^0.1'\nbase_branch: main\nrepo: tmp\n"
                "gates: [build]\npolicy_pack:\n  name: tmp\n  capture:\n"
                "    learning:\n      source: 'k/{repoo}'\n",
                encoding="utf-8",
            )
            with self.assertRaises(cfg.ConfigError) as caught:
                cfg.load_config(path)
            self.assertIn("unknown placeholder", str(caught.exception))


class ADeclaredPathOutranksARepeatedWord(unittest.TestCase):
    """Front matter is a claim; prose is a coincidence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, content):
        (self.dir / name).write_text(content, encoding="utf-8")

    def retrieve(self, query="", **kwargs):
        return capture.retrieve_relevant_learnings(query, self.dir, max_results=5, **kwargs)

    def test_a_declared_path_matches_when_no_word_does(self):
        self.write(
            "declared.md",
            learning_document(
                title="Something else entirely", changed_files=("src/keel/ledger.py",)
            ),
        )
        hits = self.retrieve(changed_files=["src/keel/ledger.py"])
        self.assertEqual([hit["file"] for hit in hits], ["declared.md"])
        self.assertEqual(hits[0]["matched_files"], ["src/keel/ledger.py"])

    def test_a_declared_label_matches_when_no_word_does(self):
        self.write("declared.md", learning_document(title="Something else", labels=("core",)))
        hits = self.retrieve(labels=["core"])
        self.assertEqual([hit["file"] for hit in hits], ["declared.md"])
        self.assertEqual(hits[0]["matched_labels"], ["core"])

    def test_the_declaring_file_outranks_the_repeating_one(self):
        self.write(
            "declares.md",
            learning_document(title="A lesson", changed_files=("src/keel/ledger.py",)),
        )
        self.write(
            "repeats.md",
            "# ledger duplicate marker\n\n" + "ledger duplicate marker blocks merge\n" * 5,
        )
        # Several words, which is what `keel ship` builds from a title plus its
        # declared files. On one word the addition happened to put the declaring
        # file first; on five, `min(count, 5)` per token compounded and the
        # coincidence led the brief.
        hits = self.retrieve(
            "ledger duplicate marker blocks merge", changed_files=["src/keel/ledger.py"]
        )
        self.assertEqual([hit["file"] for hit in hits], ["declares.md", "repeats.md"])
        self.assertGreater(hits[1]["score"], hits[0]["score"])
        self.assertEqual(hits[0]["matched_files"], ["src/keel/ledger.py"])

    def test_a_path_is_matched_however_it_is_spelled(self):
        self.write(
            "declared.md",
            learning_document(title="A lesson", changed_files=("SRC/Keel/Ledger.py",)),
        )
        self.assertEqual(len(self.retrieve(changed_files=["src/keel/ledger.py"])), 1)

    def test_plain_markdown_still_ranks(self):
        """The tolerance the reader promises: no front matter is not no lesson."""
        self.write(
            "plain.md", "# Homebrew taps lag a release\n\nThe checksum is a second commit.\n"
        )
        hits = self.retrieve("homebrew tap checksum")
        self.assertEqual([hit["file"] for hit in hits], ["plain.md"])
        self.assertEqual(hits[0]["matched_labels"], [])

    def test_a_labelless_task_with_no_query_retrieves_nothing(self):
        self.write("plain.md", "# A lesson\n")
        self.assertEqual(self.retrieve(""), [])

    def test_an_absent_directory_is_not_an_error(self):
        self.assertEqual(
            capture.retrieve_relevant_learnings("anything", self.dir / "nope", labels=["core"]),
            [],
        )

    def test_the_writers_fingerprint_identifies_the_hit(self):
        self.write(
            "declared.md", learning_document(title="L", fingerprint="fp-1", labels=("core",))
        )
        self.assertEqual(self.retrieve(labels=["core"])[0]["fingerprint"], "fp-1")

    def test_a_handwritten_file_is_identified_by_its_content(self):
        """No front matter is no fingerprint, and a hit still has to be nameable."""
        self.write("plain.md", "# Homebrew taps lag\n")
        first = self.retrieve("homebrew taps lag")[0]["fingerprint"]
        self.assertEqual(len(first), 64)
        self.write("plain.md", "# Homebrew taps lag\n\nplus a line\n")
        self.assertNotEqual(self.retrieve("homebrew taps lag")[0]["fingerprint"], first)

    def test_a_directory_of_other_files_is_skipped(self):
        (self.dir / "sub").mkdir()
        self.write("notes.rst", "ledger duplicate marker\n")
        self.write("notes.md", "ledger duplicate marker\n")
        self.assertEqual(
            [hit["file"] for hit in self.retrieve("ledger duplicate marker")], ["notes.md"]
        )


class TheSectionIsRenderedOnceForBothBriefs(unittest.TestCase):
    """Two renderers agree only until one of them is edited."""

    HIT = {
        "file": "ledger.md",
        "path": "/k/ledger.md",
        "title": "The writer and the readers disagreed",
        "summary": "A marker keyed by PR blocked every later head.",
        "fingerprint": "fp-1",
    }

    def test_nothing_retrieved_renders_nothing_at_all(self):
        """Not an empty heading, not a note that none were found."""
        self.assertEqual(capture.render_learning_brief_section([]), "")

    def test_a_hit_renders_a_title_a_line_and_a_path(self):
        section = capture.render_learning_brief_section([self.HIT])
        self.assertIn(f"### {capture.LEARNING_BRIEF_HEADING}", section)
        self.assertIn("**The writer and the readers disagreed**", section)
        self.assertIn("A marker keyed by PR blocked every later head.", section)
        self.assertIn("`/k/ledger.md`", section)

    def test_a_hit_with_no_summary_still_renders(self):
        section = capture.render_learning_brief_section([{**self.HIT, "summary": ""}])
        self.assertIn("**The writer and the readers disagreed** (`/k/ledger.md`)", section)

    def test_a_hit_with_no_title_falls_back_to_the_file_name(self):
        section = capture.render_learning_brief_section(
            [{"file": "ledger.md", "path": "", "summary": ""}]
        )
        self.assertIn("**ledger.md** (`ledger.md`)", section)

    def test_a_long_summary_is_clamped(self):
        section = capture.render_learning_brief_section([{**self.HIT, "summary": "x" * 900}])
        self.assertIn("…", section)
        self.assertLess(len(section), 500)

    def test_a_summary_is_flattened_to_one_line(self):
        section = capture.render_learning_brief_section([{**self.HIT, "summary": "a\n\nb"}])
        self.assertIn("— a b (", section)

    def test_the_budget_stops_adding_entries(self):
        """A brief becomes an agent's prompt, so an unbounded retrieval is an
        unbounded prompt."""
        hits = [{**self.HIT, "title": f"lesson {index}"} for index in range(50)]
        section = capture.render_learning_brief_section(hits, char_budget=400)
        self.assertLessEqual(len(section), 400)
        self.assertIn("lesson 0", section)
        self.assertNotIn("lesson 49", section)

    def test_a_budget_too_small_for_one_entry_renders_nothing(self):
        """A heading with no lessons under it is worse than no heading."""
        self.assertEqual(capture.render_learning_brief_section([self.HIT], char_budget=40), "")

    def test_the_block_carries_the_fingerprints_the_ledger_records(self):
        block = capture.learning_retrieval_as_dict(sources=["a"], hits=[self.HIT])
        self.assertEqual(block["fingerprints"], ["fp-1"])
        self.assertEqual(block["sources"], ["a"])
        self.assertEqual(block["heading"], capture.LEARNING_BRIEF_HEADING)
        self.assertEqual(block["hits"][0]["title"], self.HIT["title"])

    def test_a_hit_with_no_fingerprint_is_not_recorded_as_one(self):
        block = capture.learning_retrieval_as_dict(hits=[{**self.HIT, "fingerprint": ""}])
        self.assertEqual(block["fingerprints"], [])

    def test_an_empty_retrieval_still_produces_a_block(self):
        block = capture.learning_retrieval_as_dict(sources=["a"])
        self.assertEqual(block["hits"], [])
        self.assertEqual(block["section"], "")
        self.assertEqual(block["fingerprints"], [])


class TheReviewContractCarriesTheSameLessons(unittest.TestCase):
    """The reviewer checks the implementation against what the implementer was shown."""

    def contract(self, learnings):
        return ship.assess(
            changed_files=["src/keel/ledger.py"],
            gate_verdict=findings.summarize([]),
            learnings=learnings,
        )

    def test_the_hits_reach_the_reviewers_beside_the_project_additions(self):
        hits = [{"file": "ledger.md", "title": "L", "summary": "s", "path": "/k/ledger.md"}]
        assessment = self.contract({"hits": hits, "fingerprints": ["fp-1"]})
        reviewers = assessment.review_contract["reviewers"]
        self.assertEqual([hit["file"] for hit in reviewers["past_learnings"]], ["ledger.md"])
        self.assertIn("project_additions", reviewers)
        self.assertEqual(assessment.learnings["fingerprints"], ["fp-1"])

    def test_no_retrieval_leaves_the_contract_as_it_was(self):
        assessment = self.contract(None)
        self.assertEqual(assessment.review_contract["reviewers"]["past_learnings"], [])
        self.assertIsNone(assessment.learnings)

    def test_an_empty_retrieval_is_not_a_missing_one(self):
        """A project that read a directory and found nothing said something."""
        assessment = self.contract(capture.learning_retrieval_as_dict(sources=["a"]))
        self.assertEqual(assessment.review_contract["reviewers"]["past_learnings"], [])
        self.assertEqual(assessment.learnings["sources"], ["a"])


class ShipRetrievesThroughTheCliPath(unittest.TestCase):
    """End to end, because ranking that only a unit test exercises is not wired.

    `retrieve_relevant_learnings` had a full test suite and no caller in `src/keel`
    for the whole of its life. A test that calls the function proves the function;
    only a test that runs `keel ship` proves the feature.
    """

    BODY = "## Deliverable\nFix the ledger.\n\n## Acceptance criteria\n- fixed\n"

    def project(self, root: Path, learning_lines: list[str]) -> str:
        path = root / "project.yaml"
        path.write_text(
            "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
            "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
            "policy_pack:\n  name: tmp\n  reports:\n    run_ledger: 'state/runs.jsonl'\n"
            "  capture:\n    enabled: true\n    mode: extension\n    learning:\n"
            "      enabled: true\n      mode: create-learning\n" + "\n".join(learning_lines) + "\n",
            encoding="utf-8",
        )
        return str(path)

    def learnings(self, root: Path, directory: str = "learnings"):
        """Three files: one about this task, two about other work.

        Written by `render_learning_document`, so the reader is tested against what
        the writer actually emits rather than against a fixture that agrees with it.
        """
        target = root / directory
        target.mkdir(parents=True, exist_ok=True)
        (target / "ledger.md").write_text(
            learning_document(
                title="The ledger writer and its readers disagreed",
                description="A marker keyed by PR blocked every later head.",
                fingerprint="fp-ledger",
                labels=("core",),
                changed_files=("src/keel/ledger.py",),
            ),
            encoding="utf-8",
        )
        (target / "website.md").write_text(
            learning_document(
                title="A copy button copied half of what it showed",
                description="textContent flattens a line break.",
                fingerprint="fp-website",
                labels=("docs",),
                changed_files=("website/index.html",),
            ),
            encoding="utf-8",
        )
        (target / "homebrew.md").write_text(
            "# Homebrew taps lag a release\n\nThe formula checksum is a second commit.\n",
            encoding="utf-8",
        )
        return target

    def ship(self, root: Path, config: str, *extra):
        return run(
            [
                "ship",
                config,
                "--root",
                str(root),
                "--json",
                "--issue-title",
                "ledger: a duplicate marker blocks the merge",
                "--issue-body",
                self.BODY,
                *extra,
            ]
        )

    def contract(self, out):
        return json.loads(out)["contract"]

    def test_the_matching_learning_reaches_both_briefs_and_the_others_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.learnings(root)
            config = self.project(root, ["      source: 'learnings'"])
            code, out, err = self.ship(
                root, config, "--issue-label", "core", "--declared-file", "src/keel/ledger.py"
            )
            self.assertEqual(code, 0, err)
            contract = self.contract(out)
            block = contract["learnings"]
            self.assertEqual([hit["file"] for hit in block["hits"]], ["ledger.md"])
            self.assertIn(f"### {capture.LEARNING_BRIEF_HEADING}", block["section"])
            self.assertIn("The ledger writer and its readers disagreed", block["section"])
            self.assertNotIn("copy button", block["section"])
            self.assertNotIn("Homebrew", block["section"])
            self.assertEqual(
                [
                    hit["file"]
                    for hit in contract["review_merge_contract"]["reviewers"]["past_learnings"]
                ],
                ["ledger.md"],
            )

    def test_a_title_sharing_a_word_with_the_scaffolding_matches_nothing(self):
        """Every document this writer produces carries the same three headings.

        Counted as prose they are a match every file shares: an issue titled
        *"What changed in the merge window"* scored `what` and `changed` against
        all three headings of all three learnings and cleared the floor on every
        one, so the implement brief opened with three unrelated lessons and the
        ledger recorded all three as surfaced.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.learnings(root)
            config = self.project(root, ["      source: 'learnings'"])
            code, out, err = run(
                [
                    "ship",
                    config,
                    "--root",
                    str(root),
                    "--json",
                    "--issue-title",
                    "What changed in the merge window",
                    "--issue-body",
                    self.BODY,
                ]
            )
            self.assertEqual(code, 0, err)
            contract = self.contract(out)
            self.assertEqual(contract["learnings"]["hits"], [])
            self.assertEqual(contract["learnings"]["section"], "")
            record = json.loads(out)["result"]["run_ledger"]["record"]
            self.assertEqual(record["capture"]["retrieved"], [])

    def test_a_handwritten_lesson_reaches_the_brief(self):
        """The rule the reader promises has to be the rule the CLI applies.

        The ship path passed its own floor while the unit test used the function
        default, so `test_plain_markdown_still_ranks` passed against a threshold
        no real run used — and a person-written note that is not named after its
        own words never reached a brief. One rule now, and this asserts it where
        the difference showed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "learnings"
            target.mkdir()
            (target / "plain.md").write_text(
                "# Homebrew taps lag a release\n\nThe checksum is a second commit.\n",
                encoding="utf-8",
            )
            (target / "unrelated.md").write_text(
                learning_document(title="A copy button copied half of what it showed"),
                encoding="utf-8",
            )
            config = self.project(root, ["      source: 'learnings'"])
            code, out, err = run(
                [
                    "ship",
                    config,
                    "--root",
                    str(root),
                    "--json",
                    "--issue-title",
                    "homebrew tap checksum is a second commit",
                    "--issue-body",
                    self.BODY,
                ]
            )
            self.assertEqual(code, 0, err)
            block = self.contract(out)["learnings"]
            self.assertEqual([hit["file"] for hit in block["hits"]], ["plain.md"])

    def test_a_declared_path_leads_the_brief_however_wordy_the_rival(self):
        """Added together, prose compounded past the declaration bonus.

        `min(count, 5)` per query token accumulates and the exact-match bonus does
        not, so on the multi-word query `keel ship` actually builds, a file
        repeating those words outranked the one that *declared* the path the task
        touches — the ranking this whole scheme exists to get right. Declared
        matches are the first sort key now; text only orders ties.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "learnings"
            target.mkdir()
            (target / "declares.md").write_text(
                learning_document(
                    title="An unrelated lesson",
                    fingerprint="fp-declares",
                    changed_files=("src/keel/ledger.py",),
                ),
                encoding="utf-8",
            )
            (target / "repeats.md").write_text(
                "# ledger duplicate marker\n\n" + "ledger duplicate marker blocks the merge\n" * 6,
                encoding="utf-8",
            )
            config = self.project(root, ["      source: 'learnings'"])
            code, out, err = run(
                [
                    "ship",
                    config,
                    "--root",
                    str(root),
                    "--json",
                    "--issue-title",
                    "ledger: a duplicate marker blocks the merge",
                    "--declared-file",
                    "src/keel/ledger.py",
                    "--issue-body",
                    self.BODY,
                ]
            )
            self.assertEqual(code, 0, err)
            hits = self.contract(out)["learnings"]["hits"]
            self.assertEqual([hit["file"] for hit in hits], ["declares.md", "repeats.md"])
            self.assertGreater(hits[1]["score"], hits[0]["score"])

    def test_the_ledger_records_what_was_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.learnings(root)
            config = self.project(root, ["      source: 'learnings'"])
            code, out, err = self.ship(
                root, config, "--issue-label", "core", "--declared-file", "src/keel/ledger.py"
            )
            self.assertEqual(code, 0, err)
            record = json.loads(out)["result"]["run_ledger"]["record"]
            self.assertEqual(record["capture"]["retrieved"], ["fp-ledger"])

    def test_an_empty_directory_changes_no_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "learnings").mkdir()
            config = self.project(root, ["      source: 'learnings'"])
            code, out, err = self.ship(root, config)
            self.assertEqual(code, 0, err)
            block = self.contract(out)["learnings"]
            self.assertEqual(block["hits"], [])
            self.assertEqual(block["section"], "")

    def test_an_absent_directory_changes_no_brief(self):
        """Zero cost when unused: no section, no warning, no failure."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.project(root, ["      source: 'nowhere'"])
            code, out, err = self.ship(root, config)
            self.assertEqual(code, 0, err)
            self.assertEqual(self.contract(out)["learnings"]["section"], "")
            self.assertEqual(err, "")

    def test_a_relative_source_resolves_against_root_not_the_process(self):
        """The same rule the sink follows, for the same reason: on a CI runner the
        working directory is not the repository."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.learnings(root, ".keel/learning")
            config = self.project(root, ["      sink:", "        kind: markdown-dir"])
            code, out, err = self.ship(root, config, "--issue-label", "core")
            self.assertEqual(code, 0, err)
            block = self.contract(out)["learnings"]
            self.assertEqual(block["sources"], [".keel/learning"])
            self.assertEqual([hit["file"] for hit in block["hits"]], ["ledger.md"])

    def test_the_same_lesson_in_two_directories_takes_one_slot(self):
        """A shared folder synced into the checkout is how this happens."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.learnings(root, "local")
            self.learnings(root, "shared")
            config = self.project(
                root, ["      source:", "        - 'local'", "        - 'shared'"]
            )
            code, out, err = self.ship(
                root, config, "--issue-label", "core", "--declared-file", "src/keel/ledger.py"
            )
            self.assertEqual(code, 0, err)
            block = self.contract(out)["learnings"]
            self.assertEqual([hit["file"] for hit in block["hits"]], ["ledger.md"])
            self.assertEqual(block["fingerprints"], ["fp-ledger"])

    def test_the_contract_names_the_directories_as_configured(self):
        """A templated source reads fine at run time; the pure contract has no
        `{repo}` to expand with, and printing an empty list there would say the
        project had configured nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.project(root, ["      source: 'k/{repo}/learnings'"])
            code, out, err = self.ship(root, config)
            self.assertEqual(code, 0, err)
            self.assertEqual(
                self.contract(out)["capture"]["learning_retrieval"]["sources"],
                ["k/{repo}/learnings"],
            )
            self.assertEqual(self.contract(out)["learnings"]["sources"], ["k/tmp/learnings"])

    def test_two_directories_are_ranked_together(self):
        """A shared folder's best lesson must be able to outrank a weak local one."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "local").mkdir()
            (root / "local" / "weak.md").write_text("# ledger\n", encoding="utf-8")
            self.learnings(root, "shared")
            config = self.project(
                root, ["      source:", "        - 'local'", "        - 'shared'"]
            )
            code, out, err = self.ship(
                root, config, "--issue-label", "core", "--declared-file", "src/keel/ledger.py"
            )
            self.assertEqual(code, 0, err)
            block = self.contract(out)["learnings"]
            self.assertEqual(block["sources"], ["local", "shared"])
            self.assertEqual([hit["file"] for hit in block["hits"]][0], "ledger.md")

    def test_the_declared_file_is_what_makes_a_path_match_possible(self):
        """Retrieval runs before s4, so the diff is empty on the run that most
        needs a lesson. Without the declared files, only the title matches."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "learnings"
            target.mkdir()
            (target / "paths.md").write_text(
                learning_document(title="Unrelated words", changed_files=("src/keel/ledger.py",)),
                encoding="utf-8",
            )
            config = self.project(root, ["      source: 'learnings'"])
            _, without, _ = self.ship(root, config)
            self.assertEqual(self.contract(without)["learnings"]["hits"], [])
            _, with_declared, _ = self.ship(root, config, "--declared-file", "src/keel/ledger.py")
            self.assertEqual(
                [hit["file"] for hit in self.contract(with_declared)["learnings"]["hits"]],
                ["paths.md"],
            )

    def test_the_contract_declares_where_it_reads_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.project(root, ["      source: 'learnings'"])
            code, out, err = self.ship(root, config)
            self.assertEqual(code, 0, err)
            retrieval = self.contract(out)["capture"]["learning_retrieval"]
            self.assertEqual(retrieval["sources"], ["learnings"])
            self.assertEqual(retrieval["ledger_field"], "capture.retrieved")
            self.assertEqual(retrieval["briefs"], ["implement", "review"])


class TheReaderHasACaller(unittest.TestCase):
    """The one property whose absence was the whole issue.

    A reader with a full test suite and no call site passes every test it has and
    does nothing. Asserted structurally — a call, in `src/keel`, outside the module
    that defines it — because a docstring saying it is wired is exactly what was
    there before.
    """

    SRC = Path(__file__).resolve().parents[1] / "src" / "keel"

    #: Built from parts: a self-checking test must not contain its own needle, or
    #: this file satisfies the search it is running.
    READER = "retrieve_relevant" + "_learnings"

    def callers(self):
        import ast

        found = []
        for path in sorted(self.SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == self.READER:
                    found.append(path.relative_to(self.SRC).as_posix())
        return found

    def test_something_in_src_keel_calls_the_reader(self):
        callers = self.callers()
        self.assertTrue(callers, f"{self.READER} has no caller in src/keel")
        self.assertNotIn("capture.py", callers, "a call from its own module is not a caller")


class TheAdapterIsToldToRenderIt(unittest.TestCase):
    """A section core computes and no brief prints is the bug this issue is about.

    `retrieve_relevant_learnings` was reachable, documented and uncalled for its
    whole life. The generated command file is what an agent actually reads, so the
    instruction has to be *there*, not only in the source it is generated from.
    """

    ROOT = Path(__file__).resolve().parents[1]
    SURFACES = (
        "src/keel/adapters/commands/ship.md",
        "commands/ship.md",
    )

    def test_every_ship_surface_names_the_section_for_both_steps(self):
        for surface in self.SURFACES:
            with self.subTest(surface=surface):
                text = (self.ROOT / surface).read_text(encoding="utf-8")
                self.assertIn("learnings.section", text)
                self.assertIn(capture.LEARNING_BRIEF_HEADING, text)
                s4 = text.index("### s4 implement")
                s7 = text.index("### s7 review")
                self.assertLess(text.index("learnings.section"), s7)
                self.assertGreater(text.index("learnings.section"), s4)
                self.assertGreater(text.rindex("learnings.section"), s7)

    def test_the_configuration_reference_documents_the_setting(self):
        text = (self.ROOT / "docs/keel/configuration.md").read_text(encoding="utf-8")
        self.assertIn("policy_pack.capture.learning.source", text)
        self.assertIn(capture.LEARNING_BRIEF_HEADING, text)


if __name__ == "__main__":
    unittest.main()
