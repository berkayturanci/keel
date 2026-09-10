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
import shutil
import tempfile
import unittest
from pathlib import Path

from keel import capture, cli, gates
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


@contextlib.contextmanager
def _github_pr_files(files):
    """Stand in for `gh pr view --json files` — a list, or `None` for no host."""
    from keel import github

    original = github.pr_files
    github.pr_files = lambda pr, *, cwd=None, _run=None, _sleep=None: files
    try:
        yield
    finally:
        github.pr_files = original


@contextlib.contextmanager
def _github_issue(payload: str | None):
    """Stand in for `gh issue view` — `payload` JSON, or `None` for no host."""
    from keel import github

    original = github.issue_facts

    def fake(issue, *, cwd=None, fields="title,labels", _run=None):
        if payload is None:
            return github.CommandResult(False, 1, "gh: not found", stdout="")
        return github.CommandResult(True, 0, payload, stdout=payload)

    github.issue_facts = fake
    try:
        yield
    finally:
        github.issue_facts = original


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

    def test_an_expanded_placeholder_cannot_nest_the_file(self):
        """Refusing a separator in the *template* is not enough.

        `{base_branch}` is a legal filename placeholder and `feat/sink` is a normal
        branch, so the expansion carried a slash, `mkdir(parents=True)` made the
        directory, and the lesson landed one level below where the only reader
        looks. Only `{slug}` was slugified; every other value went in raw.
        """
        plan = self.plan(
            {"kind": "markdown-dir", "filename": "{date}-{base_branch}-{fingerprint}.md"},
            base_branch="feat/sink",
        )
        self.assertEqual(plan["filename"], "2026-09-09-feat-sink-abc123.md")

    def test_a_control_character_in_a_placeholder_is_flattened_too(self):
        """Same class one field over: a name that cannot be written is not a name."""
        plan = self.plan(
            {"kind": "markdown-dir", "filename": "{repo}-{fingerprint}.md"},
            repo="ke\x00el",
        )
        self.assertEqual(plan["filename"], "ke-el-abc123.md")

    def test_a_relative_directory_template_stays_relative(self):
        """`{repo}/learnings` with `repo` unset expanded to `/learnings`.

        Absolute, at the filesystem root — while every reader of the template's
        shape (`learning_sink_in_worktree`, and so `commit_required` and the
        adapter's `git add`) still called it in-repo. The filename's expansion was
        flattened for exactly this; the directory's was not.
        """
        for path, repo, expected in (
            ("{repo}/learnings", None, "learnings"),
            ("{repo}/learnings", "keel", "keel/learnings"),
            ("{owner}/{repo}/learnings", "keel", "keel/learnings"),
        ):
            with self.subTest(path=path, repo=repo):
                plan = self.plan({"kind": "markdown-dir", "path": path}, owner=None, repo=repo)
                self.assertEqual(plan["directory"], expected)
                self.assertFalse(Path(plan["directory"]).is_absolute())

    def test_an_absolute_template_is_left_as_the_project_wrote_it(self):
        """Pointing the sink outside the checkout is the feature, not an accident."""
        for path in ("/srv/{repo}/learnings", "~/knowledge/{repo}"):
            with self.subTest(path=path):
                plan = self.plan({"kind": "markdown-dir", "path": path}, repo="keel")
                self.assertTrue(plan["directory"].startswith(path[0]), plan["directory"])

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

    def test_the_contract_says_who_has_to_commit_the_file(self):
        """keel writes the file and stops; someone has to keep it.

        An uncommitted file in the working tree is invisible to the next worktree
        — s2 cuts it from `origin/<base_branch>` — and discarded by every CI
        runner, so keel would be writing a learning and then throwing it away.
        Answered from the path's shape, which is the only thing a pure contract
        can know.
        """
        for sink, expected in (
            (None, False),
            ({"kind": "markdown-dir"}, True),
            ({"path": "learnings"}, True),
            ({"path": "~/knowledge/learnings"}, False),
            ({"path": "/srv/knowledge"}, False),
            # Relative and still outside: the documented "folder next to the
            # checkout" shape without the leading `~`. Reported as in-repo it
            # would send the adapter to `git add` a path git refuses, leaving the
            # file off `base_branch` — the failure the flag exists to prevent,
            # arriving through the flag.
            ({"path": "../learnings"}, False),
            ({"path": "foo/../../outside"}, False),
            # ...and a `..` that does not actually escape still is inside.
            ({"path": "a/../learnings"}, True),
            # **Anchored on any platform, not this one.** `C:/knowledge` is not
            # absolute to POSIX and `/srv/knowledge` is not absolute to Windows, so
            # each host called the other's absolute path in-repo and would have
            # committed a `C:` directory into the repository.
            ({"path": "C:/knowledge/learnings"}, False),
            ({"path": "C:\\knowledge"}, False),
            ({"path": "//server/share/knowledge"}, False),
        ):
            with self.subTest(sink=sink):
                self.assertIs(self.destination(sink)["commit_required"], expected)

    def test_no_ship_surface_hands_out_a_commit_recipe_that_cannot_run(self):
        """The one thing worse than not committing the file is pretending to.

        s2, `overnight` and `swarm` all run inside a worktree while the primary
        checkout holds `base_branch`, so `git switch "$BASE_BRANCH"` there exits
        128 — *already used by worktree*, measured. A recipe built on it looks
        like durability and delivers none, on exactly the topology keel uses for
        itself. The surfaces say what is true instead: keel writes the file, does
        not commit it, and an in-repo sink is not durable yet.
        """
        root = Path(__file__).resolve().parents[1]
        for surface in (
            "src/keel/adapters/commands/ship.md",
            "commands/ship.md",
            ".claude/commands/keel/ship.md",
        ):
            with self.subTest(surface=surface):
                body = (root / surface).read_text(encoding="utf-8")
                s11 = body[body.index("### s11 capture") :]
                self.assertIn("commit_required", body)
                self.assertIn("already used by worktree", s11)
                # The property is that s11 hands out no **runnable** recipe. The
                # prose names the command it warns about, so the needle is the
                # shell fence, not the string inside the warning.
                capture_section = s11[: s11.index("### s12")]
                self.assertNotIn("```" + "bash", capture_section)

    def test_a_dormant_sink_under_a_disabled_capture_promises_nothing(self):
        """The contract must name the writer that will actually write.

        A `sink:` block left in `project.yaml` under `capture.enabled: false` or
        `mode: marker-only` published `project_destination: "sink"` — telling an
        adapter core would handle it — while `learning_sink_writes` refused. So
        neither wrote, and the contract had promised one of them would.
        """
        for label, capture_policy in (
            ("disabled", {"enabled": False, "mode": "extension"}),
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
                            **capture_policy,
                            "learning": {
                                "enabled": True,
                                "mode": "create-learning",
                                "sink": {"kind": "markdown-dir"},
                            },
                        }
                    },
                )
                block = capture.contract_as_dict(config)["durable_artifacts"]
                self.assertEqual(block["project_destination"], "extension-owned")
                self.assertIsNone(block["sink"])
                self.assertFalse(block["commit_required"])

    def test_an_empty_sink_block_is_still_a_sink(self):
        block = self.destination({})
        self.assertEqual(block["project_destination"], "sink")
        self.assertEqual(block["sink"], capture.LEARNING_SINK_KINDS[0])

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

    def test_a_sink_that_did_not_spell_out_its_kind_still_names_one(self):
        """`kind` is optional everywhere else, so the contract must default it too.

        Read without the fallback the block was `{project_destination: sink, sink:
        None}` — a contract disagreeing with itself about the writer it had just
        named, for a config the schema, the docs and the validator all accept.
        """
        block = self.destination({"path": ".keel/learning"})
        self.assertEqual(block["project_destination"], "sink")
        self.assertEqual(block["sink"], capture.LEARNING_SINK_KINDS[0])


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

    def test_the_parent_policy_is_consulted_too(self):
        for label, capture_policy in (
            ("disabled", {"enabled": False, "mode": "extension"}),
            ("marker-only", {"enabled": True, "mode": "marker-only"}),
            ("enabled omitted", {"mode": "extension"}),
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
                            **capture_policy,
                            "learning": {
                                "enabled": True,
                                "mode": "create-learning",
                                "sink": {"kind": "markdown-dir"},
                            },
                        }
                    },
                )
                self.assertFalse(
                    capture.learning_sink_writes(
                        config=config,
                        decision={"decision": "create-learning", "fingerprint": "a"},
                        capture_status="applied",
                    )
                )

    def test_a_status_that_is_not_applied_wants_no_durable_artifact(self):
        """`deferred` is not `applied`, and only `skipped:*` was being caught.

        It fell through and answered `create-learning` with `durable_artifact:
        true` while `learning_sink_writes` refuses every status but `applied` —
        the same write/record disagreement, one status over.
        """
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
                    "learning": {"enabled": True, "mode": "create-learning"},
                }
            },
        )
        for status in ("deferred", "skipped", "skipped:capability-unavailable", None):
            with self.subTest(status=status):
                decision = capture.learning_decision(
                    title="a lesson", capture_status=status, config=config
                )
                self.assertEqual(decision["decision"], "marker-only")
                self.assertFalse(decision["durable_artifact"])
        applied = capture.learning_decision(
            title="a lesson", capture_status="applied", config=config
        )
        self.assertEqual(applied["decision"], "create-learning")

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
    #: `\x85`, `\u2028` and `\u2029` are the ones that matter most: `str.splitlines()`
    #: breaks on all three, and none of them is in C0 — so a pattern written as
    #: "control characters" let a title open a Markdown section of its own through
    #: the very guard added to stop it.
    CONTROLS = (
        "foo\rbar",
        "foo\x00bar",
        "tab\tseparated",
        "vertical\x0bspace",
        "next\x85line",
        "line\u2028separator",
        "paragraph\u2029separator",
    )

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

    def test_no_line_break_python_knows_can_open_a_section(self):
        """Every separator `str.splitlines()` breaks on, not the visible ones.

        The first pass matched C0 and DEL, which is what "control character" reads
        as — and `\x85`, `\u2028` and `\u2029` are none of those while being line
        breaks to Python. A title carrying one walked straight through the guard.
        """
        for separator in ("\n", "\r", "\x85", "\u2028", "\u2029", "\x0c", "\x1e"):
            with self.subTest(separator=repr(separator)):
                document = capture.render_learning_document(
                    title=f"foo{separator}## injected",
                    description=f"d{separator}## also",
                    pr_number=1,
                    issue_number=None,
                    repo="r",
                    date="2026-09-09",
                    labels=(),
                    changed_files=(),
                    fingerprint="f",
                )
                headings = [line for line in document.splitlines() if line.startswith("#")]
                self.assertEqual(headings[0], "# foo ## injected")
                self.assertEqual(len(headings), 4, headings)

    def test_the_body_heading_cannot_open_a_section_of_its_own(self):
        """The front matter and the body have to say the same thing.

        The document's `# {title}` heading took the raw string, so a title carrying
        a newline wrote a heading and then whatever followed it as Markdown of its
        own: `foo` + newline + `## injected` became `# foo` and an `## injected`
        section, sitting beside a front matter that had quoted the same value.
        """
        document = capture.render_learning_document(
            title="foo\n## injected",
            description="d\r## also",
            pr_number=1,
            issue_number=None,
            repo="r",
            date="2026-09-09",
            labels=(),
            changed_files=(),
            fingerprint="f",
        )
        headings = [line for line in document.splitlines() if line.startswith("#")]
        self.assertEqual(
            headings,
            [
                "# foo ## injected",
                "## What changed",
                "## What we learned",
                "## What to do differently next time",
            ],
        )

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

    def test_a_filename_that_is_a_path_is_refused(self):
        """The reader globs one level and skips directories.

        `{pr}/{fingerprint}.md` passes every other check and `mkdir(parents=True)`
        creates the directory happily, so the lesson lands where nothing will ever
        read it — the writer/reader disagreement this whole change opened with,
        arriving through a template the validator accepted.
        """
        for template in ("{pr}/{fingerprint}.md", "a\\{fingerprint}.md"):
            with self.subTest(template=template):
                errors = capture.learning_sink_errors({"filename": template})
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("path separator", errors[0])
        self.assertEqual(capture.learning_sink_errors({"path": "a/b/{repo}"}), [])

    def test_a_filename_the_reader_would_skip_is_refused(self):
        """The reader opens `.md`, `.json` and `.txt` and nothing else.

        A filename ending `.markdown`, or in no suffix at all, was written into the
        sink successfully and then skipped by the only thing that reads it, with
        `capture.artifact` still naming it — so `applied` looked provable while the
        lesson was invisible. Same class as the path separator, one field over.
        """
        for template in ("{fingerprint}", "{fingerprint}.markdown", "{fingerprint}.mdx"):
            with self.subTest(template=template):
                errors = capture.learning_sink_errors({"filename": template})
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("must end in one of", errors[0])
        for suffix in capture.LEARNING_READ_SUFFIXES:
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    capture.learning_sink_errors({"filename": f"{{fingerprint}}{suffix}"}), []
                )

    def test_the_reader_opens_exactly_the_suffixes_the_writer_is_held_to(self):
        """One tuple, so a suffix added to one side reaches the other."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in (*capture.LEARNING_READ_SUFFIXES, ".markdown", ""):
                (root / f"lesson{suffix}").write_text("ledger ledger ledger\n", encoding="utf-8")
            found = {
                Path(hit["file"]).suffix
                for hit in capture.retrieve_relevant_learnings("ledger", root, max_results=9)
            }
            self.assertEqual(found, set(capture.LEARNING_READ_SUFFIXES))

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


class TheHostFillsWhatTheFlagsDidNot(unittest.TestCase):
    """Fail-soft at every step: a host that answers badly is a host that is silent.

    The sink runs after a merge. Anything it reads from GitHub is a convenience,
    and every way that read can go wrong has to end with the document saying what
    the flags already said rather than with the command failing.
    """

    def facts(self, payload, **flags):
        args = cli.build_parser().parse_args(
            [
                "ship",
                "project.yaml",
                "--issue",
                "1155",
                *[part for key, value in flags.items() for part in (f"--{key}", value)],
            ]
        )
        with _github_issue(payload):
            return cli._capture_issue_facts(args)

    def test_the_host_fills_an_empty_title_body_and_labels(self):
        payload = json.dumps(
            {"title": "t", "body": "b", "labels": [{"name": "core"}, {"nope": 1}, "x"]}
        )
        self.assertEqual(self.facts(payload), ("t", "b", ("core",)))

    def test_a_flag_wins_over_the_host(self):
        """The operator said it on this run; the issue may have been edited since."""
        payload = json.dumps({"title": "t", "body": "b", "labels": [{"name": "core"}]})
        self.assertEqual(
            self.facts(payload, **{"issue-title": "mine", "issue-body": "body"}),
            ("mine", "body", ("core",)),
        )

    def test_no_issue_number_asks_nothing(self):
        args = cli.build_parser().parse_args(["ship", "project.yaml", "--issue-title", "t"])
        with _github_issue(None):
            self.assertEqual(cli._capture_issue_facts(args), ("t", "", ()))

    def test_every_bad_answer_leaves_the_flags_standing(self):
        for label, payload in (
            ("no host", None),
            ("not json", "not json at all"),
            ("not an object", "[1, 2]"),
            ("wrong types", json.dumps({"title": 7, "body": None, "labels": "core"})),
        ):
            with self.subTest(answer=label):
                self.assertEqual(self.facts(payload, **{"issue-title": "mine"}), ("mine", "", ()))


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
            # Relative to `--root`: the file is committed, so the same lesson lives
            # at a different absolute path in the next worktree.
            self.assertEqual(block["artifact"], str(written[0].relative_to(root)))
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
            self.assertEqual(
                self.ledger_capture(root)["artifact"], str(written[0].relative_to(root))
            )

    def test_a_run_that_records_nothing_writes_nothing(self):
        """The artifact exists to be named by a ledger record.

        The write was gated on `--live` and the duplicate clash on `--live
        --append-ledger`, so a run with the first and not the second wrote a
        document no row would ever name — the orphan, reachable by dropping one
        flag the adapter happens to pass.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            code, _, err = run(
                [
                    "ship",
                    config,
                    "--root",
                    str(root),
                    "--live",
                    "--run-id",
                    "no-ledger",
                    "--pull-request",
                    "1154",
                    "--issue-title",
                    "capture: built-in Markdown learning sink",
                    "--issue-body",
                    self.BODY,
                    "--capture-status",
                    "applied",
                    "--approve-scope",
                    "filesystem,git,github",
                    "--operator",
                    "tester",
                ]
            )
            self.assertEqual(code, 0, err)
            self.assertFalse(list((Path(root) / "learnings").glob("*.md")))

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
            # The *sentence*, not the heading above it: a keel issue opens with
            # `## Deliverable`, and the front matter carried that as the lesson's
            # one-line summary — the field every `retrieve_relevant_learnings` hit
            # shows a reader.
            self.assertEqual(front["description"], "A Markdown learning file per applied capture.")
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
                    "--append-ledger",
                    "--issue-title",
                    "a change",
                    "--capture-status",
                    "applied",
                ]
            )
            result = cli._write_learning_sink(
                args, cfg.load_config(path), [secret], [], [], ("a change", "", ())
            )
            self.assertTrue(result["ok"], result)
            body = (Path(root) / result["path"]).read_text(encoding="utf-8")
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
                self.assertTrue((Path(root) / path).is_file(), path)
            self.assertIn("core", (Path(root) / first).read_text(encoding="utf-8"))
            self.assertIn("docs", (Path(root) / second).read_text(encoding="utf-8"))

    def test_an_unlinked_merge_says_null_rather_than_nothing(self):
        """Both read back as `None`; only one of them says so on purpose."""
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config)[0], 0)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            self.assertIn("issue: null", body)
            self.assertIsNone(_front_matter_fields(body)["issue"])

    def test_a_gate_nobody_ran_is_not_recorded_as_passing(self):
        """`not_run` is the fourth state, and it read as `ok`.

        An agentic gate reaches the command runner as `ok=True, not_run=True` so a
        soft gate does not spuriously fail the run. Rendered as `ok`, the durable
        document taught the next run that a gate nobody executed had passed —
        inside the artifact that exists to make `applied` provable.
        """
        outcomes = [
            gates.GateOutcome(gate="build", ok=True),
            gates.GateOutcome(gate="review", ok=True, not_run=True),
            gates.GateOutcome(gate="docs", ok=True, skipped=True),
            gates.GateOutcome(gate="tests", ok=False),
        ]
        args = cli.build_parser().parse_args(["ship", "p.yaml", "--issue-title", "t"])
        _, _, learned, _ = cli._learning_sections(args, outcomes, "")
        self.assertEqual(
            learned,
            "Gates on the merged head — build: ok, review: not run, docs: skipped, tests: failed",
        )

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

    def test_an_invalid_redaction_pattern_is_reported_not_raised(self):
        """A pattern that will not compile must not end the command in a traceback.

        It **does** still end the run: `keel ship` refuses to append a record it
        cannot redact, prints why, and exits 1 — which is the designed behaviour and
        not the sink's to change, since writing an unsanitized record would be the
        worse answer. What the sink owes is not raising from a place with no handler,
        which is what it did. This is deliberately *not* the fail-soft an unwritable
        directory gets: that one downgrades the claim and exits 0.
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

    def test_the_files_come_from_the_pull_request_when_the_diff_is_empty(self):
        """s11 runs after the squash, so the local diff reports nothing.

        `--root .` is then the primary checkout sitting on `base_branch`, and
        `git diff --name-only main...HEAD` is empty — so the document recorded
        `changed_files: []` and, worse, the fingerprint hashed an empty list, which
        is the field the sink filename exists to tell two lessons apart by.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            with _github_pr_files(["src/keel/capture.py", "src/keel/cli.py"]):
                code, _, err = self.ship_with(root, config, pr=1160)
            self.assertEqual(code, 0, err)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            self.assertEqual(
                _front_matter_fields(body)["changed_files"],
                ["src/keel/capture.py", "src/keel/cli.py"],
            )

    def test_the_ledger_fingerprints_the_same_lesson_the_document_does(self):
        """One lesson, one fingerprint — and they were two.

        The sink was handed the host's PR files while `build_ship_run_record` kept
        hashing the empty post-merge diff, so the document's front matter and the
        ledger's `capture.learning.fingerprint` named different lessons. A second
        ship then wrote a *second* file (the sink found no matching fingerprint)
        while the record called it a `duplicate` of the first and pointed
        `capture.artifact` at the new one: dedupe and the applied-artifact claim
        crossed, on the dogfood command.

        `changes.files` stays what git reported — an empty diff is a fact about
        this run — so the two lists are deliberately not the same field.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            files = ["src/keel/capture.py", "src/keel/cli.py"]
            with _github_pr_files(files):
                self.assertEqual(self.ship_with(root, config, pr=1160)[0], 0)
            written = sorted((Path(root) / "learnings").glob("*.md"))
            self.assertEqual(len(written), 1, written)
            document = _front_matter_fields(written[0].read_text(encoding="utf-8"))
            block = self.ledger_capture(root)
            self.assertEqual(document["fingerprint"], block["learning"]["fingerprint"])
            self.assertTrue(written[0].name.endswith(f"{document['fingerprint'][:12]}.md"))

    def test_a_second_post_merge_run_is_deduped_not_rewritten(self):
        """The consequence of the crossed fingerprints, through the CLI."""
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            with _github_pr_files(["src/keel/capture.py"]):
                self.assertEqual(
                    self.ship_with(root, config, "--head-sha", "a" * 40, pr=1, run_id="one")[0], 0
                )
                first = self.ledger_capture(root)["artifact"]
                self.assertEqual(
                    self.ship_with(root, config, "--head-sha", "b" * 40, pr=1, run_id="two")[0], 0
                )
            self.assertEqual(len(list((Path(root) / "learnings").glob("*.md"))), 1)
            block = self.ledger_capture(root)
            self.assertEqual(block["learning"]["decision"], "duplicate")
            self.assertEqual(block["artifact"], first)

    def test_no_host_leaves_the_files_as_the_diff_reported_them(self):
        """Fail-soft: offline the lesson is scored on its title alone, not lost."""
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            with _github_pr_files(None):
                code, _, err = self.ship_with(root, config, pr=1160)
            self.assertEqual(code, 0, err)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            self.assertEqual(_front_matter_fields(body)["changed_files"], [])

    def test_an_all_digit_fingerprint_reads_back_as_a_string(self):
        """A sha256 that happens to be all digits is an `int` to a real parser.

        This is the field that *identifies* the lesson, and it was the one
        contracted scalar still written bare after the date was quoted.
        """
        document = capture.render_learning_document(
            title="t",
            description="",
            pr_number=1,
            issue_number=None,
            repo="r",
            date="2026-09-09",
            labels=(),
            changed_files=(),
            fingerprint="0" * 64,
        )
        self.assertEqual(_front_matter_fields(document)["fingerprint"], "0" * 64)

    def test_the_date_reads_back_as_a_string(self):
        """`2026-09-09` bare is a YAML *timestamp*: a real parser returns a `date`
        object where keel's reader returns the string, which is the one
        disagreement all this quoting exists to prevent."""
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            self.assertEqual(self.ship(root, config)[0], 0)
            body = sorted((Path(root) / "learnings").glob("*.md"))[0].read_text(encoding="utf-8")
            self.assertIsInstance(_front_matter_fields(body)["date"], str)

    def test_a_sink_that_names_nothing_takes_the_documented_defaults(self):
        """Every field of a sink is optional, so `sink: {}` is a real declaration.

        Collapsed to a falsy `{}`, it read as *no sink at all* and the project got
        the pre-#1154 behaviour of writing nothing — while the same block naming
        its `kind` wrote.
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
                    "      sink: {}",
                ],
            )
            code, _, err = self.ship(root, config)
            self.assertEqual(code, 0, err)
            written = sorted((Path(root) / ".keel" / "learning").glob("*.md"))
            self.assertEqual(len(written), 1, written)
            self.assertEqual(
                self.ledger_capture(root)["artifact"], str(written[0].relative_to(root))
            )

    def test_the_adapters_own_s11_command_still_writes_a_document(self):
        """The command keel dogfoods passes no title and no body.

        `--issue-title` / `--issue-body` are `keel plan` flags at the start of a
        run; s11 is `keel ship … --live --append-ledger --issue <N> --pull-request
        <PR> --capture-status applied`, and a later invocation inherits nothing.
        Measured on exactly that: a file titled `Learning`, an empty description, a
        `learning` slug and `_Not recorded._` under every heading — an artifact that
        makes `applied` "provable" by pointing at a document recording no lesson.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            facts = json.dumps(
                {
                    "title": "capture: feed retrieved learnings into briefs",
                    "body": "## Deliverable\nWire the reader in.\n",
                    "labels": [{"name": "core"}],
                }
            )
            with _github_issue(facts):
                code, _, err = run(
                    [
                        "ship",
                        config,
                        "--root",
                        str(root),
                        "--live",
                        "--append-ledger",
                        "--run-id",
                        "s11",
                        "--issue",
                        "1155",
                        "--pull-request",
                        "1160",
                        "--capture-status",
                        "applied",
                        "--approve-scope",
                        "filesystem,git,github",
                        "--operator",
                        "tester",
                    ]
                )
            self.assertEqual(code, 0, err)
            written = sorted((Path(root) / "learnings").glob("*.md"))
            self.assertEqual(len(written), 1, written)
            self.assertIn("feed-retrieved-learnings", written[0].name)
            body = written[0].read_text(encoding="utf-8")
            front = _front_matter_fields(body)
            self.assertEqual(front["title"], "capture: feed retrieved learnings into briefs")
            self.assertEqual(front["labels"], ["core"])
            self.assertNotIn("_Not recorded._\n\n## What we learned", body)

    def test_offline_it_writes_what_it_has(self):
        """Fail-soft, like the blocker gate's own reader: no host, no crash."""
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            with _github_issue(None):
                code, _, err = run(
                    [
                        "ship",
                        config,
                        "--root",
                        str(root),
                        "--live",
                        "--append-ledger",
                        "--run-id",
                        "offline",
                        "--issue",
                        "1155",
                        "--pull-request",
                        "1160",
                        "--issue-title",
                        "a title the flag carried",
                        "--issue-body",
                        self.BODY,
                        "--capture-status",
                        "applied",
                        "--approve-scope",
                        "filesystem,git,github",
                        "--operator",
                        "tester",
                    ]
                )
            self.assertEqual(code, 0, err)
            written = sorted((Path(root) / "learnings").glob("*.md"))
            self.assertEqual(len(written), 1, written)
            self.assertEqual(
                _front_matter_fields(written[0].read_text(encoding="utf-8"))["title"],
                "a title the flag carried",
            )

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

    def test_a_retry_on_the_same_head_writes_no_orphan(self):
        """The append no-ops on a repeated (PR, head); the write must too.

        Asked afterwards, a retry whose fingerprint had *moved* — a `gh` outage on
        the first attempt, a label fetched on the second — wrote a second document
        into the sink, possibly a shared knowledge folder, that no ledger record
        would ever name. Reproduced with two labels on one head.
        """
        with tempfile.TemporaryDirectory() as root:
            config = write_config(Path(root), self.SINK_LINES)
            head = ("a" * 40, "--head-sha")
            self.assertEqual(
                self.ship_with(
                    root, config, head[1], head[0], "--issue-label", "core", pr=7, run_id="one"
                )[0],
                0,
            )
            first = self.ledger_capture(root)["artifact"]
            self.assertEqual(
                self.ship_with(
                    root, config, head[1], head[0], "--issue-label", "docs", pr=7, run_id="two"
                )[0],
                0,
            )
            written = sorted((Path(root) / "learnings").glob("*.md"))
            self.assertEqual([p.name for p in written], [Path(first).name])
            self.assertEqual(self.ledger_capture(root)["artifact"], first)

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
        """`--root` is whatever the operator typed, so the record must not be.

        Written as the join of a relative `--root`, the artifact meant "relative to
        the directory *that* run was launched from" — so a later run from a CI
        runner, a worktree or a cron shell found nothing. Written absolute, it named
        a filesystem location, and the file is *committed*, so the next worktree
        holds the same lesson somewhere else. It is recorded **relative to the
        root**; both shapes below reach the same file.
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

    def test_a_shared_folder_outside_the_checkout_is_recorded_absolute(self):
        """Nothing else can name it, so the record does.

        Pointing the sink at a knowledge folder outside the repository is the
        feature — git never sees it, `commit_required` is false, and a path
        relative to a root it does not live under would mean nothing.
        """
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as shared:
            shared_path = Path(shared).resolve()
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
                    f"        path: {str(shared_path)!r}",
                ],
            )
            code, _, err = self.ship(root, config)
            self.assertEqual(code, 0, err)
            written = sorted(shared_path.glob("*.md"))
            self.assertEqual(len(written), 1, written)
            recorded = self.ledger_capture(root)["artifact"]
            self.assertEqual(recorded, str(written[0]))
            self.assertTrue(Path(recorded).is_absolute())

    #: A sink path anchored on the *other* platform, so this reads the same
    #: property wherever the suite runs: `C:/…` is not absolute to POSIX and
    #: `/srv/…` is not absolute to Windows. Naming one literal would have tested
    #: two different things — on Windows `C:/knowledge` is a perfectly good
    #: absolute path, and `Path(root) / "C:"` resolves to the drive, not a
    #: subdirectory, so the POSIX form of the assertion is meaningless there.
    FOREIGN_SINK = "/srv/knowledge/learnings" if os.name == "nt" else "C:/knowledge/learnings"

    def test_a_sink_this_platform_cannot_write_fails_soft(self):
        """The writer has to ask the same question the predicate does.

        `commit_required` reads a path anchored on another platform as
        out-of-repo on every runner — but the writer joined it under `--root`,
        producing a directory *inside* the working tree that the adapter is told
        not to commit, and a ledger path that is relative here and absolute
        there. Two halves of one question, disagreeing. Anchored elsewhere means
        unwritable here, so the capture downgrades instead of landing somewhere
        invented.
        """
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root).resolve()
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
                    f"        path: {self.FOREIGN_SINK!r}",
                ],
            )
            code, _, err = self.ship(root, config)
            self.assertEqual(code, 0, err)
            # Nothing named after the foreign anchor may appear under the
            # checkout. Listed rather than joined: `Path(root) / "C:"` is the
            # drive on Windows, not a child.
            anchor = self.FOREIGN_SINK.split("/")[0] or self.FOREIGN_SINK.split("/")[1]
            self.assertNotIn(anchor, os.listdir(root_path))
            self.assertIn(self.ledger_capture(root)["status"], (None, "skipped"))

    def test_the_next_worktree_finds_the_lesson_the_last_one_wrote(self):
        """The file is committed, so the same lesson lives at a different path.

        s2 cuts the next worktree from `origin/<base_branch>` and CI clones fresh,
        so an artifact recorded as a filesystem location named a file that is on
        disk *here* and nowhere the next run will look — and the duplicate reuse
        then recorded `applied` with no artifact, the finding it exists to close.
        Recorded relative to the root, the same ledger and the same tree resolve
        wherever they are checked out.
        """
        with tempfile.TemporaryDirectory() as first_root, tempfile.TemporaryDirectory() as second:
            first_path, second_path = Path(first_root).resolve(), Path(second).resolve()
            config = write_config(first_path, self.SINK_LINES)
            self.assertEqual(self.ship(first_path, config, pr=1)[0], 0)
            recorded = self.ledger_capture(first_path)["artifact"]
            # What a clone of the merged branch looks like: the same tracked files
            # in a different directory.
            for name in ("learnings", "state"):
                shutil.copytree(first_path / name, second_path / name)
            second_config = write_config(second_path, self.SINK_LINES)
            code, _, err = self.ship_with(second_path, second_config, pr=2, run_id="second")
            self.assertEqual(code, 0, err)
            block = self.ledger_capture(second_path)
            self.assertEqual(block["learning"]["decision"], "duplicate")
            self.assertEqual(block["artifact"], recorded)
            self.assertTrue((second_path / recorded).is_file())

    def test_a_relative_root_finds_its_own_file_back(self):
        """`--root repo` joined the root twice and found nothing.

        The write stored the already-joined `repo/learnings/<file>.md`; the reuse
        joined `--root` onto it again (`repo/repo/learnings/…`), `is_file()` was
        false, and the duplicate recorded `applied` with no artifact — the finding
        the reuse exists to close, on a file still sitting on disk. Every other
        test passes `.` or an absolute root, so both of the working shapes hide it.
        """
        with tempfile.TemporaryDirectory() as parent:
            parent_path = Path(parent).resolve()
            (parent_path / "repo").mkdir()
            write_config(parent_path / "repo", self.SINK_LINES)
            here = Path.cwd()
            os.chdir(parent_path)
            try:
                self.assertEqual(self.ship("repo", "repo/project.yaml", pr=1)[0], 0)
                first = self.ledger_capture(parent_path / "repo")["artifact"]
                self.assertTrue((parent_path / "repo" / first).is_file(), first)
                self.assertEqual(
                    self.ship_with("repo", "repo/project.yaml", pr=2, run_id="two")[0], 0
                )
                block = self.ledger_capture(parent_path / "repo")
            finally:
                os.chdir(here)
            self.assertEqual(block["learning"]["decision"], "duplicate")
            self.assertEqual(block["artifact"], first)

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
            (Path(root) / self.ledger_capture(root)["artifact"]).unlink()
            self.assertEqual(self.ship(root, config, pr=2)[0], 0)
            self.assertIsNone(self.ledger_capture(root)["artifact"])

    def test_a_capture_policy_that_runs_no_hook_writes_no_file(self):
        """The parent switches, one level above the `learning.*` refusals.

        `policy_pack.capture` says whether this project runs a content hook at
        all: `enabled: false` is a project that does not, and `mode: marker-only`
        records the marker *without* one — the schema's own words. The sink **is**
        that hook, so a configured sink was again permission to write, and the
        four inner refusals did not reach it.
        """
        for label, capture_lines in (
            ("capture disabled", ["    enabled: false", "    mode: extension"]),
            ("marker-only", ["    enabled: true", "    mode: marker-only"]),
            ("enabled omitted", ["    mode: extension"]),
        ):
            with self.subTest(policy=label), tempfile.TemporaryDirectory() as root:
                config = write_config(
                    Path(root),
                    [
                        "  capture:",
                        *capture_lines,
                        "    learning:",
                        "      enabled: true",
                        "      mode: create-learning",
                        "      sink:",
                        "        kind: markdown-dir",
                        "        path: 'learnings'",
                    ],
                )
                code, _, err = self.ship(root, config)
                self.assertEqual(code, 0, err)
                self.assertFalse(list((Path(root) / "learnings").glob("*.md")))
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
