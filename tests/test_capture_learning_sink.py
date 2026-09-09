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
                "filename": "{date}-pr{pr}-{slug}.md",
            }
        )
        self.assertEqual(plan["directory"], "~/k/berkayturanci/keel")
        # The slug is capped so a long issue title cannot produce an unusable
        # filename; the cap cuts at a character count, not at a word boundary.
        self.assertTrue(plan["filename"].startswith("2026-09-09-pr1154-capture-built-in"))
        self.assertTrue(plan["filename"].endswith(".md"))
        self.assertLessEqual(len(plan["filename"]), 80)

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

    def test_a_run_with_no_learning_decision_still_plans(self):
        """The decision is optional input: without one there is simply no fingerprint.

        `learning_decision` is what supplies it, and a caller that has not run it —
        or a project with dedupe off — should still get a document rather than a
        crash or a silent skip.
        """
        plan = self.plan({"kind": "markdown-dir"}, decision=None)
        self.assertIsNotNone(plan)
        self.assertIn("fingerprint: \n", plan["content"])

    def test_an_invalid_sink_produces_no_plan_rather_than_a_bad_path(self):
        """Validation refuses it at config load; the plan refuses it again here."""
        self.assertIsNone(self.plan({"kind": "obsidian"}))


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
        "        filename: '{date}-pr{pr}-{slug}.md'",
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

    def ledger_capture(self, root):
        path = Path(root) / "state" / "runs.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return records[-1]["capture"]

    def test_an_applied_capture_writes_one_file_and_names_it(self):
        with tempfile.TemporaryDirectory() as root:
            config = write_config(
                Path(root),
                [
                    "  capture:",
                    "    enabled: true",
                    "    mode: extension",
                    "    learning:",
                    "      mode: create-learning",
                    "      sink:",
                    "        kind: markdown-dir",
                    f"        path: {str(Path(root) / 'learnings')!r}",
                    "        filename: '{date}-pr{pr}-{slug}.md'",
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
            self.assertIsNone(self.ledger_capture(root)["artifact"])

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
