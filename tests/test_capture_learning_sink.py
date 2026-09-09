"""The built-in capture extension: one Markdown file per applied capture (#1154).

`policy_pack.capture` with `mode: extension` has always let a project produce
durable learning content after a merge, and keel shipped no implementation of it —
so every project that wanted learnings wrote the whole s11 hook itself, and keel
captured none from its own runs.

The sink is deliberately small. A directory of Markdown with stable frontmatter is
the entire contract: keel never learns what reads that directory, and a project
points it at a repo-local folder, a shared knowledge folder, or nothing at all.

Two things this file holds that the issue did not ask for, because measurement
found them:

* **The writer and the existing reader have to agree.** `retrieve_relevant_learnings`
  took a file's title from its first line. A document with YAML frontmatter is
  titled `---` and summarised `schema: keel.learning.v1` under that rule — measured
  before the reader was changed. Shipping the writer alone would have filled a
  directory with learnings the one existing reader mis-reads.
* **A template typo has no symptom.** `path: "~/k/{repoo}/learnings"` would create a
  directory called `{repoo}`, successfully, on a machine nobody is watching. The
  placeholder set is closed and validated where the config is read.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from keel import capture, cli
from keel import config as cfg


def run(argv):
    """Invoke the CLI in-process and capture its streams.

    Written here rather than imported from `tests/test_cli.py`: no other module in
    this suite imports another, and `unittest discover -s tests` loads these as
    top-level modules, so a relative import works under one runner and not the
    other.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def write_config(directory: Path, extra_policy_pack_lines: list[str] | None = None) -> str:
    """A minimal valid project config with a run ledger, plus any policy lines."""
    extra = "\n".join(extra_policy_pack_lines or [])
    if extra:
        extra = f"\n{extra}"
    path = directory / "project.yaml"
    path.write_text(
        "extends: keel\ncore_version: '^0.1'\nbase_branch: main\n"
        "repo: tmp\ngates: [build]\nknobs:\n  build_gate_cmd: 'true'\n"
        "policy_pack:\n  name: tmp\n  reports:\n"
        "    run_ledger: 'state/runs.jsonl'"
        f"{extra}\n",
        encoding="utf-8",
    )
    return str(path)


def _front_matter_fields(document: str) -> dict:
    """The front-matter block, parsed by a real YAML parser.

    Not keel's own reader: the point of the block is that *other* tools read it,
    and a reader that splits on the first colon cannot tell a string from a list.
    """
    import yaml

    return yaml.safe_load(document.split("---")[1])


def _config(sink: dict | None) -> cfg.ProjectConfig:
    learning: dict = {"mode": "create-learning"}
    if sink is not None:
        learning["sink"] = sink
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.1",
        knobs={},
        owner="berkayturanci",
        repo="keel",
        base_branch="main",
        policy_pack={"capture": {"enabled": True, "mode": "extension", "learning": learning}},
    )


class ThePlanIsPure(unittest.TestCase):
    """It resolves a path and renders a document; it touches nothing."""

    def plan(self, sink, **kwargs):
        params = {
            "config": _config(sink),
            "decision": {"decision": "create-learning", "fingerprint": "abc123"},
            "capture_status": "applied",
            "owner": "berkayturanci",
            "repo": "keel",
            "base_branch": "main",
            "date": "2026-09-09",
            "pr_number": 1154,
            "title": "capture: built-in Markdown learning sink",
        }
        params.update(kwargs)
        return capture.learning_sink_plan(**params)

    def test_a_configured_sink_resolves_its_placeholders(self):
        plan = self.plan(
            {
                "kind": "markdown-dir",
                "path": "~/k/{owner}/{repo}",
                "filename": "{date}-pr{pr}-{slug}-{fingerprint}.md",
            }
        )
        self.assertEqual(plan["directory"], "~/k/berkayturanci/keel")
        # The slug is capped so a long issue title cannot produce an unusable
        # filename; the cap cuts at a character count, not at a word boundary.
        self.assertTrue(plan["filename"].startswith("2026-09-09-pr1154-capture-built-in"))
        self.assertTrue(plan["filename"].endswith("-abc123.md"))
        self.assertLessEqual(len(plan["filename"]), 96)

    def test_an_omitted_path_keeps_the_existing_convention(self):
        """Turning the sink on must not move where a project already looks."""
        plan = self.plan({"kind": "markdown-dir"})
        self.assertEqual(plan["directory"], capture.DEFAULT_LEARNING_SINK_PATH)
        self.assertEqual(plan["directory"], ".keel/learning")

    def test_no_sink_is_no_plan(self):
        self.assertIsNone(self.plan(None))

    def test_a_capture_that_did_not_apply_writes_nothing(self):
        for status in ("deferred", "skipped", None):
            with self.subTest(status=status):
                self.assertIsNone(self.plan({"kind": "markdown-dir"}, capture_status=status))

    def test_a_duplicate_decision_writes_nothing(self):
        """The dedupe doing its job, not a failure — so no file and no error."""
        plan = self.plan(
            {"kind": "markdown-dir"},
            decision={"decision": "duplicate", "fingerprint": "abc123"},
        )
        self.assertIsNone(plan)

    def test_a_title_that_reduces_to_nothing_still_names_a_file(self):
        plan = self.plan({"kind": "markdown-dir"}, title="::: ---")
        self.assertIn("learning", plan["filename"])

    def test_an_invalid_sink_produces_no_plan_rather_than_a_bad_path(self):
        """Validation refuses it at config load; the plan refuses it again here."""
        self.assertIsNone(self.plan({"kind": "obsidian"}))


class TheContractSaysWhoWritesTheFile(unittest.TestCase):
    """`extension-owned` was the whole truth until keel shipped a writer.

    An adapter that read the contract and wrote its own durable artifact had it
    overwritten: `_cmd_ship` puts the sink's path into `capture.artifact` when the
    write succeeds. A machine-readable contract that is wrong about who writes is
    worse than one that says nothing.
    """

    def destination(self, sink):
        return capture.contract_as_dict(_config(sink))["durable_artifacts"]

    def test_a_project_with_no_sink_is_unchanged(self):
        self.assertEqual(self.destination(None)["project_destination"], "extension-owned")
        self.assertIsNone(self.destination(None)["sink"])
        self.assertEqual(
            capture.contract_as_dict()["durable_artifacts"]["project_destination"],
            "extension-owned",
        )

    def test_a_configured_sink_says_core_writes_it(self):
        block = self.destination({"kind": "markdown-dir"})
        self.assertEqual(block["project_destination"], "sink")
        self.assertEqual(block["sink"], "markdown-dir")


class TheDecisionDecidesWhetherAnythingIsWritten(unittest.TestCase):
    """A sink names a destination; the learning decision says whether to use it."""

    def plan(self, decision):
        return capture.learning_sink_plan(
            config=_config({"kind": "markdown-dir"}),
            decision=decision,
            capture_status="applied",
            owner="berkayturanci",
            repo="keel",
            base_branch="main",
            date="2026-09-09",
            pr_number=1154,
            title="capture: built-in Markdown learning sink",
        )

    def test_only_create_learning_plans_a_write(self):
        self.assertIsNotNone(self.plan({"decision": "create-learning", "fingerprint": "a"}))
        for decision in ("marker-only", "defer", "duplicate"):
            with self.subTest(decision=decision):
                self.assertIsNone(self.plan({"decision": decision, "fingerprint": "a"}))

    def test_no_decision_at_all_plans_no_write(self):
        """Silence is not consent. Nothing decided, nothing durable."""
        self.assertIsNone(self.plan(None))
        self.assertIsNone(self.plan("create-learning"))

    def test_the_policy_that_produces_each_decision_reaches_the_same_answer(self):
        """Through `learning_decision`, not a decision handed over by name.

        The four policies below are the ones a project actually writes, and each
        planned a write before the gate moved onto the decision.
        """
        for label, learning in (
            ("enabled omitted", {"mode": "create-learning"}),
            ("enabled false", {"enabled": False, "mode": "create-learning"}),
            ("defer", {"enabled": True, "mode": "defer"}),
            ("marker-only", {"enabled": True, "mode": "marker-only"}),
        ):
            with self.subTest(policy=label):
                config = cfg.ProjectConfig(
                    extends="keel",
                    core_version="^0.1",
                    knobs={},
                    owner="berkayturanci",
                    repo="keel",
                    base_branch="main",
                    policy_pack={
                        "capture": {
                            "enabled": True,
                            "mode": "extension",
                            "learning": {**learning, "sink": {"kind": "markdown-dir"}},
                        }
                    },
                )
                decision = capture.learning_decision(
                    title="capture: built-in Markdown learning sink",
                    capture_status="applied",
                    config=config,
                )
                self.assertFalse(decision["durable_artifact"], label)
                self.assertIsNone(
                    capture.learning_sink_plan(
                        config=config,
                        decision=decision,
                        capture_status="applied",
                        owner="berkayturanci",
                        repo="keel",
                        base_branch="main",
                        date="2026-09-09",
                        pr_number=1154,
                        title="capture: built-in Markdown learning sink",
                    )
                )


class ADuplicatePointsAtTheFileItDuplicates(unittest.TestCase):
    """`applied` stays provable when the dedupe suppresses the write."""

    DUPLICATE = {"decision": "duplicate", "fingerprint": "abc123"}

    def records(self, *artifacts, fingerprint="abc123"):
        return [
            {
                "run_id": f"r{index}",
                "capture": {
                    "artifact": artifact,
                    "learning": {"decision": "create-learning", "fingerprint": fingerprint},
                },
            }
            for index, artifact in enumerate(artifacts)
        ]

    def artifact(self, decision, records, sink={"kind": "markdown-dir"}, status="applied"):  # noqa: B006
        return capture.duplicate_learning_artifact(
            config=_config(sink),
            decision=decision,
            capture_status=status,
            existing_records=records,
        )

    def test_only_an_applied_capture_borrows_one(self):
        """`learning_decision` answers `duplicate` before it looks at the status.

        Without this gate a `not-run` or `skipped` record was handed the earlier
        run's path — the exact contradiction the CLI refuses at its flag boundary,
        *a run that never reached capture produced no artifact*, and the one
        `record_marker` states for `deferred` and `skipped`.
        """
        for status in (None, "deferred", "skipped", "skipped:capability-unavailable"):
            with self.subTest(status=status):
                self.assertIsNone(
                    self.artifact(self.DUPLICATE, self.records("/k/one.md"), status=status)
                )

    def test_it_names_the_recorded_file(self):
        self.assertEqual(self.artifact(self.DUPLICATE, self.records("/k/one.md")), "/k/one.md")

    def test_the_newest_record_wins(self):
        """The ledger is append-only, so the last matching path is the current one."""
        records = self.records("/k/one.md", "/k/two.md")
        self.assertEqual(self.artifact(self.DUPLICATE, records), "/k/two.md")

    def test_a_record_with_no_artifact_is_not_a_match(self):
        records = self.records("/k/one.md") + self.records(None)
        self.assertEqual(self.artifact(self.DUPLICATE, records), "/k/one.md")
        self.assertIsNone(self.artifact(self.DUPLICATE, self.records("   ")))

    def test_a_different_fingerprint_is_a_different_learning(self):
        records = self.records("/k/one.md", fingerprint="zzz")
        self.assertIsNone(self.artifact(self.DUPLICATE, records))

    def test_only_a_duplicate_borrows_an_artifact(self):
        for decision in ("create-learning", "marker-only", "defer"):
            with self.subTest(decision=decision):
                self.assertIsNone(
                    self.artifact(
                        {"decision": decision, "fingerprint": "abc123"},
                        self.records("/k/one.md"),
                    )
                )
        self.assertIsNone(self.artifact(None, self.records("/k/one.md")))
        self.assertIsNone(self.artifact({"decision": "duplicate"}, self.records("/k/one.md")))

    def test_a_project_with_no_sink_borrows_nothing(self):
        """No sink is no feature. A run that never wrote learnings does not start
        inheriting an operator's hand-passed `--capture-artifact`."""
        self.assertIsNone(self.artifact(self.DUPLICATE, self.records("/k/one.md"), sink=None))

    def test_junk_records_are_skipped(self):
        records = ["not a record", {"capture": "not a mapping"}, {"capture": {"learning": []}}]
        self.assertIsNone(self.artifact(self.DUPLICATE, records))


class TheDocumentIsReadableByTheReaderThatExists(unittest.TestCase):
    """The coupling the issue did not name, and the reason the reader moved.

    `retrieve_relevant_learnings` is the only thing in this repository that reads a
    learning directory. A writer whose output it mis-reads is a writer that fills a
    folder with files nobody can search.
    """

    def document(self, **kwargs):
        params = {
            "title": "The ledger deadlock was two keys over one append-only file",
            "description": "A ship run recorded against a superseded head made the PR unmergeable.",
            "pr_number": 1158,
            "issue_number": 1157,
            "repo": "berkayturanci/keel",
            "date": "2026-09-09",
            "labels": ["type:bug"],
            "changed_files": ["src/keel/ledger.py"],
            "fingerprint": "abc123",
            "what_changed": "existing_capture_marker keys on (pr, head).",
            "what_we_learned": "A writer and a reader must agree on the key.",
            "do_differently": "Check the reader's key when adding a writer-side guard.",
        }
        params.update(kwargs)
        return capture.render_learning_document(**params)

    def read_back(self, text, query="ledger deadlock append-only"):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / "2026-09-09-pr1158-ledger.md").write_text(text, encoding="utf-8")
        return capture.retrieve_relevant_learnings(query, directory)

    def test_the_reader_gets_the_title_and_not_the_delimiter(self):
        hits = self.read_back(self.document())
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0]["title"].startswith("The ledger deadlock"))
        self.assertTrue(hits[0]["summary"].startswith("A ship run recorded"))

    def test_the_unfixed_reader_would_have_titled_it_the_delimiter(self):
        """The measurement, kept: this is what the old rule did to this document."""
        lines = [line.strip() for line in self.document().splitlines() if line.strip()]
        self.assertEqual(lines[0], "---")
        self.assertTrue(lines[1].startswith("schema:"))

    def test_a_summary_is_taken_from_the_body_when_front_matter_has_none(self):
        """`description:` empty is a file that still deserves a one-line summary.

        The first non-heading line of the body is it — the heading itself is the
        title, and repeating it as the summary tells a reader nothing.
        """
        text = self.document(description="")
        hits = self.read_back(text)
        self.assertTrue(hits[0]["title"].startswith("The ledger deadlock"))
        self.assertFalse(hits[0]["summary"].startswith("#"))
        self.assertIn("existing_capture_marker", hits[0]["summary"])

    def test_a_body_of_headings_alone_summarises_to_nothing(self):
        """No non-heading line to take, so the summary is empty rather than a heading."""
        hits = self.read_back("---\ntitle: Ledger deadlock\ndescription:\n---\n\n# One\n\n## Two\n")
        self.assertEqual(hits[0]["title"], "Ledger deadlock")
        self.assertEqual(hits[0]["summary"], "")

    def test_a_file_with_no_front_matter_still_reads(self):
        hits = self.read_back("# Plain learning\n\nSomething about the ledger deadlock.\n")
        self.assertEqual(hits[0]["title"], "Plain learning")
        self.assertEqual(hits[0]["summary"], "Something about the ledger deadlock.")

    def test_an_unterminated_delimiter_is_not_front_matter(self):
        """`---` with no closing line is a horizontal rule, not a header."""
        hits = self.read_back("---\nledger deadlock notes\nmore\n")
        self.assertEqual(hits[0]["title"], "---")

    def test_the_title_is_in_the_body_too_so_search_can_weigh_it(self):
        text = self.document()
        self.assertIn("# The ledger deadlock", text)

    def test_the_front_matter_parses_as_yaml(self):
        """The whole point of front matter is that other tools read it.

        An issue title routinely contains a colon — `capture: built-in Markdown
        learning sink` — and written bare it makes the block invalid YAML. keel's
        own reader splits on the first colon and would never notice.
        """
        import yaml

        block = self.document(
            title="capture: built-in Markdown learning sink",
            description='He said "no" and used a # too',
            labels=["type:enhancement"],
        ).split("---")[1]
        parsed = yaml.safe_load(block)
        self.assertEqual(parsed["title"], "capture: built-in Markdown learning sink")
        self.assertEqual(parsed["description"], 'He said "no" and used a # too')
        self.assertEqual(parsed["labels"], ["type:enhancement"])

    def test_the_reader_unwraps_a_quoted_title(self):
        """Quoting for other tools must not leave keel's own reader holding quotes."""
        hits = self.read_back(
            self.document(title="capture: the sink", description='a "quoted" summary'),
            query="capture sink ledger",
        )
        self.assertEqual(hits[0]["title"], "capture: the sink")
        self.assertEqual(hits[0]["summary"], 'a "quoted" summary')

    def test_every_contracted_field_is_present(self):
        text = self.document()
        for field in (
            "schema:",
            "title:",
            "description:",
            "repo:",
            "pr:",
            "issue:",
            "date:",
            "fingerprint:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, text)
        for heading in (
            "## What changed",
            "## What we learned",
            "## What to do differently next time",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)

    def test_an_empty_section_says_so_rather_than_being_blank(self):
        self.assertIn("_Not recorded._", self.document(what_changed=""))


class ATemplateTypoIsRefusedWhereTheConfigIsRead(unittest.TestCase):
    """Its only other symptom is a directory named `{repoo}` that looks like success."""

    def test_an_unknown_placeholder_is_named(self):
        errors = capture.learning_sink_errors({"kind": "markdown-dir", "path": "~/k/{repoo}/x"})
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("{repoo}", errors[0])
        self.assertIn("owner", errors[0])

    def test_every_documented_placeholder_is_accepted(self):
        template = "".join("{" + name + "}" for name in capture.LEARNING_SINK_PLACEHOLDERS)
        self.assertEqual(capture.learning_sink_errors({"path": template}), [])

    def test_an_unknown_kind_is_named(self):
        errors = capture.learning_sink_errors({"kind": "obsidian"})
        self.assertIn("markdown-dir", errors[0])

    def test_a_blank_template_is_refused(self):
        self.assertTrue(capture.learning_sink_errors({"filename": "  "}))
        self.assertTrue(capture.learning_sink_errors({"path": 7}))

    def test_a_missing_sink_is_not_an_error(self):
        for value in (None, {}):
            with self.subTest(sink=value):
                self.assertEqual(capture.learning_sink_errors(value), [])

    def test_a_non_mapping_is_an_error(self):
        self.assertTrue(capture.learning_sink_errors(["markdown-dir"]))

    def test_a_capture_block_without_a_learning_mapping_is_not_examined(self):
        """The validator must not trip over a policy that names no learning at all."""
        from keel.config import _learning_sink_issues

        self.assertEqual(_learning_sink_issues({}), [])
        self.assertEqual(_learning_sink_issues({"capture": "yes"}), [])
        self.assertEqual(_learning_sink_issues({"capture": {"learning": "yes"}}), [])
        self.assertEqual(_learning_sink_issues({"capture": {"learning": {}}}), [])

    def test_the_config_loader_refuses_it(self):
        data = {
            "extends": "keel",
            "core_version": "^0.1",
            "base_branch": "main",
            "repo": "tmp",
            "gates": ["build"],
            "knobs": {"build_gate_cmd": "true"},
            "policy_pack": {
                "name": "tmp",
                "capture": {
                    "enabled": True,
                    "mode": "extension",
                    "learning": {"sink": {"kind": "markdown-dir", "path": "~/{repoo}"}},
                },
            },
        }
        with self.assertRaises(cfg.ConfigError) as caught:
            cfg.parse_config(data, source="probe")
        self.assertIn("{repoo}", str(caught.exception))


class TheFrontMatterSurvivesARealParser(unittest.TestCase):
    """Quote unless the value is plainly safe — a deny-list was wrong twelve ways.

    The first cut listed `:`, `#`, `"` and a newline. An issue title beginning with
    `-`, `*`, `&`, `!`, `@`, `%`, `|`, `>` or a backtick then failed to parse or came
    back as something else; `{a}` became a mapping, `[a]` a list, and `yes` became
    `True`. A deny-list has to be right about every character; an allow-list only
    has to be right about the ones it lets through.
    """

    #: Every shape that broke the deny-list, plus two that must stay unquoted.
    TITLES = (
        "- leading dash",
        "*anchor",
        "&anchor",
        "{brace}",
        "[bracket]",
        "yes",
        # A keyword the allow-list let through because of one trailing space: the
        # scalar is plain, the padded form is not in the keyword set, and YAML drops
        # the space and reads `True`.
        "yes ",
        "true ",
        "off ",
        "null ",
        "@at",
        "`tick",
        "%directive",
        "|pipe",
        ">fold",
        "!tag",
        "capture: built-in Markdown learning sink",
        'he said "no"',
        "  padded  ",
        "plain title",
    )

    def block(self, title):
        import yaml

        text = capture.render_learning_document(
            title=title,
            description="",
            pr_number=1,
            issue_number=None,
            repo="r",
            date="2026-09-09",
            labels=(),
            changed_files=(),
            fingerprint="f",
        )
        return yaml.safe_load(text.split("---")[1])

    def test_every_title_round_trips_through_yaml(self):
        for title in self.TITLES:
            with self.subTest(title=title):
                self.assertEqual(self.block(title)["title"], title)

    def test_a_plain_title_is_left_unquoted(self):
        text = capture.render_learning_document(
            title="plain title",
            description="",
            pr_number=1,
            issue_number=None,
            repo="r",
            date="2026-09-09",
            labels=(),
            changed_files=(),
            fingerprint="f",
        )
        self.assertIn("title: plain title\n", text)

    #: Characters that do not survive being written into a line. A carriage return
    #: survives inside quotes, and `_front_matter` splits lines on it — so keel's
    #: own reader gets a title of `"foo` and drops the rest while a real parser
    #: reads the whole thing, the two disagreeing about one file. A NUL makes
    #: PyYAML refuse the document outright. `--issue-title` is a raw CLI string and
    #: can carry either.
    CONTROLS = ("foo\rbar", "foo\x00bar", "tab\tseparated", "vertical\x0bspace")

    def test_a_control_character_becomes_a_space(self):
        for title in self.CONTROLS:
            with self.subTest(title=title):
                parsed = self.block(title)["title"]
                self.assertIsInstance(parsed, str)
                self.assertNotIn("\r", parsed)
                self.assertNotIn("\x00", parsed)

    def test_both_readers_agree_about_a_control_character(self):
        """The property the quoting exists for: *other* tools can read it.

        A value that keel's reader and a real parser disagree about is worse than
        one neither can read — the file looks fine from inside the repository.
        """
        for title in self.CONTROLS:
            with self.subTest(title=title):
                document = capture.render_learning_document(
                    title=title,
                    description="",
                    pr_number=1,
                    issue_number=None,
                    repo="r",
                    date="2026-09-09",
                    labels=(),
                    changed_files=(),
                    fingerprint="f",
                )
                keel_title, _ = capture._learning_title_and_summary(document, "fallback")
                self.assertEqual(keel_title, self.block(title)["title"])

    def test_a_yaml_keyword_is_quoted_so_it_stays_a_string(self):
        for word in ("yes", "no", "true", "off", "null"):
            with self.subTest(word=word):
                self.assertIsInstance(self.block(word)["title"], str)


class EveryTypoShapeIsRefused(unittest.TestCase):
    """`{Repo}` and `{base-branch}` are as wrong as `{repoo}`.

    The first pattern matched `[a-z_]*`, which is the shape of a *correct* name — so
    a mixed-case or hyphenated typo was invisible to the check written to catch typos.
    """

    def test_a_mixed_case_or_hyphenated_placeholder_is_named(self):
        for template in ("~/{Repo}/x", "~/{base-branch}/x", "~/{ repo }/x", "~/{}/x"):
            with self.subTest(template=template):
                self.assertTrue(capture.learning_sink_errors({"path": template}), template)

    def test_the_documented_set_still_passes(self):
        self.assertEqual(capture.learning_sink_errors({"path": "~/{repo}/{date}/{pr}"}), [])

    def test_a_filename_that_cannot_name_two_lessons_is_refused(self):
        """Date, PR and slug do not distinguish two lessons, and the second write
        destroys the first — so the fingerprint is required in the name, refused
        here where the placeholder typos are refused."""
        errors = capture.learning_sink_errors({"filename": "{date}-pr{pr}-{slug}.md"})
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("{fingerprint}", errors[0])
        self.assertEqual(capture.learning_sink_errors({"filename": "{slug}-{fingerprint}.md"}), [])

    def test_the_default_filename_carries_it(self):
        """The rule is only real if the value a project gets without asking obeys it."""
        self.assertIn("{fingerprint}", capture.DEFAULT_LEARNING_SINK_FILENAME)
        self.assertEqual(capture.learning_sink_errors({"kind": "markdown-dir"}), [])

    def test_the_config_loader_refuses_it(self):
        with tempfile.TemporaryDirectory() as root:
            path = write_config(
                Path(root),
                [
                    "  capture:",
                    "    learning:",
                    "      sink:",
                    "        kind: markdown-dir",
                    "        filename: '{slug}.md'",
                ],
            )
            with self.assertRaises(cfg.ConfigError) as caught:
                cfg.load_config(path)
            self.assertIn("{fingerprint}", str(caught.exception))


class ShipWritesTheFileAndRecordsItAsTheArtifact(unittest.TestCase):
    """End to end, through the CLI, because that is where the I/O lives.

    `capture.artifact` is already the field that makes an `applied` capture provable
    rather than asserted — `capture-reconcile` treats `applied` with no artifact as a
    finding. The sink fills it, so a project stops having to pass
    `--capture-artifact` by hand for a file it did not write.
    """

    #: An intake the ship path accepts, so these tests exercise the sink rather than
    #: the readiness gate.
    BODY = (
        "## Deliverable\nA Markdown learning file per applied capture.\n\n"
        "## Acceptance criteria\n- one file per applied capture\n"
    )

    #: The sink block these tests configure, kept in one place so a test that adds
    #: a case cannot quietly configure a different sink from the rest.
    SINK_LINES = [
        "  capture:",
        "    enabled: true",
        "    mode: extension",
        "    learning:",
        "      enabled: true",
        "      mode: create-learning",
        "      sink:",
        "        kind: markdown-dir",
        "        path: 'learnings'",
        "        filename: '{date}-pr{pr}-{slug}-{fingerprint}.md'",
    ]

    def ship(self, root, config, pr=1154, status="applied"):
        return run(
            [
                "ship",
                config,
                "--root",
                str(root),
                "--live",
                "--append-ledger",
                "--run-id",
                f"ship-{pr}",
                "--pull-request",
                str(pr),
                "--issue-title",
                "capture: built-in Markdown learning sink",
                "--issue-body",
                self.BODY,
                "--capture-status",
                status,
                "--approve-scope",
                "filesystem,git,github",
                "--operator",
                "tester",
            ]
        )

    def ship_with(self, root, config, *extra, pr=1154, run_id=None, status="applied"):
        """`ship`, plus flags a single test needs."""
        return run(
            [
                "ship",
                config,
                "--root",
                str(root),
                "--live",
                "--append-ledger",
                "--run-id",
                run_id or f"ship-{pr}",
                "--pull-request",
                str(pr),
                "--issue-title",
                "capture: built-in Markdown learning sink",
                "--issue-body",
                self.BODY,
                "--capture-status",
                status,
                "--approve-scope",
                "filesystem,git,github",
                "--operator",
                "tester",
                *extra,
            ]
        )

    def ledger_capture(self, root):
        path = Path(root) / "state" / "runs.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return records[-1]["capture"]

    def artifact_findings(self, root, config, *, prs):
        """`applied-without-artifact` findings `keel capture-verify` reports.

        Driven through the CLI with the offline merged-PR fixture, because the
        finding this suppresses is one a real session raises against the ledger
        these runs wrote, not one a unit test constructs.
        """
        fixture = Path(root) / "merged.json"
        fixture.write_text(
            json.dumps([{"number": number} for number in prs]),
            encoding="utf-8",
        )
        code, out, err = run(
            [
                "capture-verify",
                config,
                "--root",
                str(root),
                "--from-transport",
                "--merged-prs-json",
                str(fixture),
                "--json",
            ]
        )
        self.assertIn(code, (0, 1), err)
        report = json.loads(out)["reconcile"]
        return [
            finding
            for finding in report["findings"]
            if finding["type"] == "applied-without-artifact"
        ]

    def test_an_applied_capture_writes_one_file_and_names_it(self):
        with tempfile.TemporaryDirectory() as root:
            config = write_config(
                Path(root),
                [
                    "  capture:",
                    "    enabled: true",
                    "    mode: extension",
                    "    learning:",
                    "      enabled: true",
                    "      mode: create-learning",
                    "      sink:",
                    "        kind: markdown-dir",
                    f"        path: {str(Path(root) / 'learnings')!r}",
                    "        filename: '{date}-pr{pr}-{slug}-{fingerprint}.md'",
                ],
            )
            code, _, _ = self.ship(root, config)
            self.assertEqual(code, 0)
            written = sorted((Path(root) / "learnings").glob("*.md"))
            self.assertEqual(len(written), 1, written)
            block = self.ledger_capture(root)
            self.assertEqual(block["status"], "applied")
            self.assertEqual(block["artifact"], str(written[0]))
            body = written[0].read_text(encoding="utf-8")
            self.assertIn("schema: keel.learning.v1", body)
            self.assertIn("pr: 1154", body)

    def test_the_default_path_lands_under_root_not_the_working_directory(self):
        """`.keel/learning` is relative, and relative to *what* is the whole question.

        Resolved against the process working directory, the default wrote wherever
        keel happened to be launched from — on a CI runner, not the repository at
        all. An absolute or `~` path is still used as written, because pointing the
        sink at a shared folder outside the checkout is the feature.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(
                Path(root),
                [
                    "  capture:",
                    "    enabled: true",
                    "    mode: extension",
                    "    learning:",
                    "      enabled: true",
                    "      mode: create-learning",
                    "      sink:",
                    "        kind: markdown-dir",
                ],
            )
            code, _, _ = self.ship(root, config)
            self.assertEqual(code, 0)
            written = sorted((Path(root) / ".keel" / "learning").glob("*.md"))
            self.assertEqual(len(written), 1, written)
            self.assertEqual(self.ledger_capture(root)["artifact"], str(written[0]))

    def test_a_dry_run_writes_nothing(self):
        """`--live` gates it, as it gates the ledger append.

        A `keel ship` without `--live` that scattered files into somebody's
        knowledge folder would be the least expected thing this command does.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            code, _, _ = run(
                [
                    "ship",
                    config,
                    "--root",
                    str(root),
                    "--run-id",
                    "dry",
                    "--pull-request",
                    "1154",
                    "--issue-title",
                    "capture: built-in Markdown learning sink",
                    "--issue-body",
                    self.BODY,
                    # The capture claim the sink acts on. Without it the run declines
                    # for a reason unrelated to the gate under test, and removing the
                    # gate leaves this test green.
                    "--capture-status",
                    "applied",
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(list(Path(root).rglob("*.md")))

    def test_the_document_carries_the_run_and_not_three_placeholders(self):
        """The extension fills the sections from the run; empty ones are a file with
        a filename and nothing in it."""
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config)[0], 0)
            import yaml

            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            front = yaml.safe_load(body.split("---")[1])
            self.assertTrue(front["description"], "the front-matter description is empty")
            self.assertIn("Deliverable", front["description"])
            self.assertIn("A Markdown learning file per applied capture", body)
            self.assertIn("Gates on the merged head", body)
            self.assertEqual(body.count("_Not recorded._"), 0)

    def test_a_secret_survives_redaction_as_a_string_not_a_yaml_list(self):
        """Redact the values, then render — the other order breaks the document.

        `ghp_` + 36 letters is *plain* YAML: letters, digits and an underscore, so
        the quoter correctly let it through bare. Replacing it in the finished
        document put `[REDACTED:github-token]` inside that bare scalar, and a real
        parser reads a **list** where a title belongs. The description is the issue
        body's first non-empty line, so a body that opens with a pasted token hit it.

        The filename is the same ordering bug seen from the other side: it is built
        from the title, and a title redacted afterwards leaves most of the token in
        a committed path.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            secret = "ghp_" + "A" * 36
            code, _, _ = run(
                [
                    "ship",
                    config,
                    "--root",
                    str(root),
                    "--live",
                    "--append-ledger",
                    "--run-id",
                    "plain-secret",
                    "--pull-request",
                    "1154",
                    "--issue-title",
                    f"rotate {secret}",
                    "--issue-body",
                    f"{secret}\n\n## Deliverable\nRotate it.\n\n"
                    "## Acceptance criteria\n- rotated\n",
                    "--capture-status",
                    "applied",
                    "--approve-scope",
                    "filesystem,git,github",
                    "--operator",
                    "tester",
                ]
            )
            self.assertEqual(code, 0)
            written = sorted((Path(root) / "learnings").glob("*.md"))[0]
            body = written.read_text(encoding="utf-8")
            self.assertNotIn(secret, body)
            self.assertNotIn("aaaaaaaa", written.name.lower())
            front = _front_matter_fields(body)
            self.assertIsInstance(front["title"], str)
            self.assertIsInstance(front["description"], str)
            self.assertIn("REDACTED", front["title"])
            self.assertIn("REDACTED", front["description"])

    def test_a_label_that_is_a_secret_stays_a_string(self):
        """The third field in the same class, and the reason the fix moved.

        `_issue_labels` returns a **tuple**, and `redaction.sanitize` walked only
        `str`, `list` and `dict` — so a tuple came back with its secrets intact,
        silently, looking exactly like a value that had been checked. Converting
        each caller's tuple to a list fixed the field in front of it and left the
        next one; `sanitize` walks tuples now.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            secret = "ghp_" + "C" * 36
            code, _, err = self.ship_with(root, config, "--issue-label", secret)
            self.assertEqual(code, 0, err)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            self.assertNotIn(secret, body)
            labels = _front_matter_fields(body)["labels"]
            self.assertEqual(len(labels), 1)
            self.assertIsInstance(labels[0], str)

    def test_a_changed_path_that_is_a_secret_stays_a_string(self):
        """Every value the document is rendered from, not the likely ones.

        A path that *is* a token and nothing else is plain YAML, so it went in bare
        and the document pass put `[REDACTED:…]` inside it — the same break as the
        title, one field over. The fix is the whole set of rendered values, which is
        why this test names a field nobody would have thought to redact.

        Driven at `_write_learning_sink` rather than through `ship`: this list is
        what **git** reported changed, and no flag supplies it.
        """
        with tempfile.TemporaryDirectory() as root:
            path = write_config(Path(root), self.SINK_LINES)
            secret = "ghp_" + "B" * 36
            args = cli.build_parser().parse_args(
                [
                    "ship",
                    path,
                    "--root",
                    str(root),
                    "--live",
                    "--issue-title",
                    "a change",
                    "--capture-status",
                    "applied",
                ]
            )
            result = cli._write_learning_sink(args, cfg.load_config(path), [secret], [], [])
            self.assertTrue(result["ok"], result)
            body = Path(result["path"]).read_text(encoding="utf-8")
            self.assertNotIn(secret, body)
            changed = _front_matter_fields(body)["changed_files"]
            self.assertEqual(len(changed), 1)
            self.assertIsInstance(changed[0], str)

    def test_two_lessons_on_one_pr_the_same_day_do_not_overwrite_each_other(self):
        """The fingerprint is the identity, so it has to be in the name.

        Date, PR and slug do not distinguish two lessons: a second
        `create-learning` run on the same PR the same day — different labels,
        different files, a different lesson — resolved to the same path and
        `os.replace` destroyed the first, leaving its ledger record pointing at a
        document that says something else. The dedupe cannot help: it suppresses
        *identical* fingerprints, and these differ.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            # Distinct heads, because that is what two ship runs on one PR are —
            # and because the ledger keys its clash on (PR, head), so a second
            # record for the same head would be skipped and this test would be
            # reading the first one twice.
            first_code, _, err = self.ship_with(
                root, config, "--issue-label", "core", "--head-sha", "a" * 40, pr=7, run_id="one"
            )
            self.assertEqual(first_code, 0, err)
            first = self.ledger_capture(root)["artifact"]
            second_code, _, err = self.ship_with(
                root, config, "--issue-label", "docs", "--head-sha", "b" * 40, pr=7, run_id="two"
            )
            self.assertEqual(second_code, 0, err)
            second = self.ledger_capture(root)["artifact"]
            self.assertEqual(len(list((Path(root) / "learnings").glob("*.md"))), 2)
            self.assertNotEqual(first, second)
            for path in (first, second):
                self.assertTrue(Path(path).is_file(), path)
            self.assertIn("core", Path(first).read_text(encoding="utf-8"))
            self.assertIn("docs", Path(second).read_text(encoding="utf-8"))

    def test_an_unlinked_merge_says_null_rather_than_nothing(self):
        """Both read back as `None`; only one of them says so on purpose."""
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config)[0], 0)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            self.assertIn("issue: null", body)
            self.assertIsNone(_front_matter_fields(body)["issue"])

    def test_an_empty_label_list_reads_back_as_a_list(self):
        """`labels:` with nothing under it is a **null**, not `[]`.

        The contract calls these fields sequences, so a consumer that iterates them
        raises `TypeError` on any merge that recorded none — which is most of them.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config)[0], 0)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            front = _front_matter_fields(body)
            self.assertEqual(front["labels"], [])
            self.assertEqual(front["changed_files"], [])

    def test_an_invalid_redaction_pattern_fails_soft_instead_of_crashing(self):
        """A pattern that will not compile must not end the command in a traceback.

        `keel ship` has a handler for it where the *ledger* is sanitized; the sink
        reached for the policy earlier, outside every handler, so a project with
        both a sink and a bad pattern died after its merge instead of downgrading
        the capture claim the way an unwritable directory does.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(
                Path(root),
                [
                    *self.SINK_LINES,
                    "  capture_redaction:",
                    "    deny_patterns:",
                    "      - id: bad-regex",
                    "        pattern: '[unclosed'",
                    "        replacement: '[REDACTED]'",
                ],
            )
            code, _, err = self.ship(root, config)
            self.assertEqual(code, 1, err)
            self.assertIn("redaction", err.lower())
            self.assertFalse(list((Path(root) / "learnings").glob("*.md")))

    def test_a_secret_in_the_issue_body_is_redacted_before_it_is_written(self):
        """Redaction before durability is the capture contract's rule, not a new one.

        A learning file is a durable artifact made of an issue body and gate output —
        the two places a secret is most likely to have been pasted — and the first
        cut of this feature claimed the rule in its docs and did not apply it.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            secret = "ghp_" + "A" * 36
            code, _, _ = run(
                [
                    "ship",
                    config,
                    "--root",
                    str(root),
                    "--live",
                    "--append-ledger",
                    "--run-id",
                    "secret",
                    "--pull-request",
                    "1154",
                    "--issue-title",
                    "capture: built-in Markdown learning sink",
                    "--issue-body",
                    f"## Deliverable\nToken {secret}\n\n## Acceptance criteria\n- none\n",
                    "--capture-status",
                    "applied",
                    "--approve-scope",
                    "filesystem,git,github",
                    "--operator",
                    "tester",
                ]
            )
            self.assertEqual(code, 0)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            self.assertNotIn(secret, body)

    def test_a_second_run_on_the_same_shape_is_deduped(self):
        """The dedupe was unreachable on the only path that writes.

        `learning_decision` needs the existing records to answer `duplicate`, and the
        first cut called it without them — so the skip this feature documents could
        never happen in a real run, only in a unit test that passed them by hand.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config, pr=1)[0], 0)
            self.assertEqual(self.ship(root, config, pr=2)[0], 0)
            written = sorted((Path(root) / "learnings").glob("*.md"))
            self.assertEqual(len(written), 1, [p.name for p in written])

    def test_a_deduped_run_points_at_the_file_the_first_run_wrote(self):
        """A dedupe must not manufacture the gap the artifact exists to close.

        The second run writes nothing and still records `applied`, and `applied`
        with no artifact is precisely what `capture-reconcile` reports as a
        finding — so the feature whose stated purpose is to make `applied`
        provable was producing an unprovable one every time the dedupe fired.
        The lesson is not missing: it is the file the first run wrote.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config, pr=1)[0], 0)
            first = self.ledger_capture(root)["artifact"]
            self.assertEqual(self.ship(root, config, pr=2)[0], 0)
            second = self.ledger_capture(root)
            self.assertEqual(second["learning"]["decision"], "duplicate")
            self.assertEqual(second["artifact"], first)
            self.assertEqual(self.artifact_findings(root, config, prs=(1, 2)), [])

    def test_a_deduped_run_finds_the_file_from_another_working_directory(self):
        """`--root` defaults to `.`, so a live run records a *relative* artifact.

        Resolved against the process directory instead, the reuse could only find
        that file when keel was launched from the repository — and a later run from
        a CI runner, a worktree or a cron shell would record `applied` with no
        artifact, which is the finding the reuse exists to prevent. Every test that
        passes an absolute `--root` is blind to it, which is why this one does not.
        """
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root).resolve()
            config = write_config(root_path, self.SINK_LINES)
            here = Path.cwd()
            os.chdir(root_path)
            try:
                self.assertEqual(self.ship(".", "project.yaml", pr=1)[0], 0)
                first = self.ledger_capture(root_path)["artifact"]
                self.assertFalse(Path(first).is_absolute(), first)
            finally:
                os.chdir(here)
            self.assertEqual(self.ship(root_path, config, pr=2)[0], 0)
            self.assertEqual(self.ledger_capture(root_path)["artifact"], first)

    def test_a_run_that_never_reached_capture_borrows_nothing(self):
        """Through the CLI, because that is where the contradiction would land.

        `keel ship` already refuses `--capture-status not-run` with an artifact
        passed by hand — *a run that never reached capture produced no artifact*.
        The reuse was writing that same pair into the record without anyone asking.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config, pr=1)[0], 0)
            self.assertIsNotNone(self.ledger_capture(root)["artifact"])
            code, _, err = self.ship_with(
                root, config, "--head-sha", "c" * 40, pr=2, run_id="not-run", status="not-run"
            )
            self.assertEqual(code, 0, err)
            block = self.ledger_capture(root)
            self.assertTrue(block["not_run"])
            self.assertIsNone(block["artifact"])

    def test_a_deduped_run_claims_nothing_when_that_file_is_gone(self):
        """An artifact that resolves to nothing is worse than no artifact.

        A recorded path can name a file since deleted, or one written on another
        machine into a shared folder this checkout cannot see. Copying it forward
        unchecked would let `capture-reconcile` report a clean session whose proof
        does not exist.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config, pr=1)[0], 0)
            Path(self.ledger_capture(root)["artifact"]).unlink()
            self.assertEqual(self.ship(root, config, pr=2)[0], 0)
            self.assertIsNone(self.ledger_capture(root)["artifact"])

    def test_a_policy_that_wants_no_learning_writes_no_file(self):
        """A configured sink is where learnings go, not permission to write one.

        `learning.enabled` false — and, identically, an omitted `enabled`, a
        `defer` mode and a `marker-only` mode — makes `learning_decision` answer
        `marker-only` with `durable_artifact: false`. The first cut checked only
        for `duplicate`, so all four wrote a durable body of issue text while the
        record beside it said the policy had decided not to keep one.
        """
        for label, learning_lines in (
            ("enabled omitted", ["      mode: create-learning"]),
            ("enabled false", ["      enabled: false", "      mode: create-learning"]),
            ("defer", ["      enabled: true", "      mode: defer"]),
            ("marker-only", ["      enabled: true", "      mode: marker-only"]),
        ):
            with self.subTest(policy=label), tempfile.TemporaryDirectory() as root:
                config = write_config(
                    Path(root),
                    [
                        "  capture:",
                        "    enabled: true",
                        "    mode: extension",
                        "    learning:",
                        *learning_lines,
                        "      sink:",
                        "        kind: markdown-dir",
                        "        path: 'learnings'",
                    ],
                )
                code, _, _ = self.ship(root, config)
                self.assertEqual(code, 0)
                self.assertFalse(list((Path(root) / "learnings").glob("*.md")))
                block = self.ledger_capture(root)
                self.assertIsNone(block["artifact"])
                self.assertFalse(block["learning"]["durable_artifact"])

    def test_declared_files_are_named_in_what_changed(self):
        """The section says what changed, so it should say which files.

        `--declared-file` is what a run states it touched; the document repeats it
        rather than leaving the reader to infer it from a title.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            code, _, _ = run(
                [
                    "ship",
                    config,
                    "--root",
                    str(root),
                    "--live",
                    "--append-ledger",
                    "--run-id",
                    "declared",
                    "--pull-request",
                    "1154",
                    "--issue-title",
                    "capture: built-in Markdown learning sink",
                    "--issue-body",
                    self.BODY,
                    "--declared-file",
                    "src/keel/capture.py",
                    "--declared-file",
                    "src/keel/cli.py",
                    "--capture-status",
                    "applied",
                    "--approve-scope",
                    "filesystem,git,github",
                    "--operator",
                    "tester",
                ]
            )
            self.assertEqual(code, 0)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            self.assertIn("Declared files: src/keel/capture.py, src/keel/cli.py", body)

    def test_a_project_with_no_sink_writes_nothing_and_is_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root))
            code, _, _ = self.ship(root, config)
            self.assertEqual(code, 0)
            self.assertEqual(self.ledger_capture(root)["status"], "applied")
            self.assertFalse(list(Path(root).glob("learnings/*.md")))

    def test_an_unwritable_sink_downgrades_the_claim_and_lands_the_run(self):
        """Fail-soft by contract: a capture that could not be written must not fail a
        merge that already happened."""
        with tempfile.TemporaryDirectory() as root:
            blocker = Path(root) / "blocked"
            blocker.write_text("not a directory\n", encoding="utf-8")
            config = write_config(
                Path(root),
                [
                    "  capture:",
                    "    enabled: true",
                    "    mode: extension",
                    "    learning:",
                    "      enabled: true",
                    "      mode: create-learning",
                    "      sink:",
                    "        kind: markdown-dir",
                    f"        path: {str(blocker / 'inside')!r}",
                ],
            )
            code, _, _ = self.ship(root, config)
            self.assertEqual(code, 0)
            block = self.ledger_capture(root)
            self.assertEqual(block["status"], "skipped")
            self.assertEqual(block["reason"], "capability-unavailable")
            self.assertIsNone(block["artifact"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
