"""Unit tests for the pure ``keel review`` orchestration module and CLI wiring."""

import atexit
import contextlib
import io
import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from keel import cli, closure, evidence, juryavail, review, runtime
from keel.runner import CommandResult

#: A canned ``keel doctor --providers`` report in which the panel *is* staffable (#1066).
#: s7 probes provider availability before it dispatches the panel, and that probe is the
#: one machine-dependent input to the reviewer bench — so an offline suite has to supply
#: it, or these tests resolve one bench on a laptop with three agent CLIs installed and a
#: different one on a CI runner with none.
_STAFFED_PANEL_REPORT = {
    "schema_version": "keel.providers.v1",
    "providers": [
        {"name": "claude", "vendor": "claude", "available": True, "reason": "/usr/bin/claude"},
        {"name": "codex", "vendor": "codex", "available": True, "reason": "/usr/bin/codex"},
    ],
    "available": 2,
    "total": 2,
}

#: …and the `jury` binary s7 would dispatch, present and runnable. Both halves have to be
#: answered from memory: the probe asks the runner as well as the inventory (#1066 round 2),
#: and a suite that let the real `jury --doctor` run would resolve a different bench on a
#: machine that happens to have ai-jury installed.
_STAFFED_RUNNER = juryavail.Runner(True, "/usr/bin/jury (no readable --doctor report)")

_NO_REAL_PROVIDER_PROBE: list = []


def setUpModule():
    """Answer the s7 panel-availability probe from memory, never from this machine."""
    _NO_REAL_PROVIDER_PROBE.extend(
        [
            patch("keel.providerprobe.collect", lambda *_a, **_kw: dict(_STAFFED_PANEL_REPORT)),
            patch("keel.providerprobe.probe_jury_runner", lambda **_kw: _STAFFED_RUNNER),
        ]
    )
    for entry in _NO_REAL_PROVIDER_PROBE:
        entry.start()


def tearDownModule():
    for entry in _NO_REAL_PROVIDER_PROBE:
        entry.stop()
    _NO_REAL_PROVIDER_PROBE.clear()


PROJECTS = Path(__file__).resolve().parent.parent / "projects"
ANDROID = str(PROJECTS / "example-android.yaml")


def _proc(output="", *, ok=True):
    """A fake ``run_argv`` return whose **stdout** carries ``output``.

    Parsers read ``.stdout`` alone, never the concatenated ``.output`` (#629), so a
    fake that populates only ``output`` would read back as an empty stream.
    """
    return CommandResult(ok, 0 if ok else 1, output, stdout=output)


# Module-level scratch directory backing the path-returning ``_write`` helper.
# Cleaned at process exit so the suite leaves no stray temp files behind.
_TMP = tempfile.TemporaryDirectory()
atexit.register(_TMP.cleanup)
_TMP_COUNTER = itertools.count()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _capable_report():
    return runtime.CapabilityReport(
        (
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("gh", True, "ok", "test"),
            runtime.Capability("gh-auth", True, "ok", "test"),
        )
    )


def _write(value) -> str:
    path = Path(_TMP.name) / f"reviews-{next(_TMP_COUNTER)}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def _two_reviews():
    return [
        {
            "reviewer": "Reviewer A",
            "verdict": "LGTM",
            "scope": "core",
            "findings": [{"severity": "nit", "message": "tidy"}],
            "testing": "make test",
        },
        {"reviewer": "Reviewer B", "verdict": "LGTM"},
    ]


class TestParseReviews(unittest.TestCase):
    def test_parses_full_and_minimal_reviews(self):
        items = review.parse_reviews(_two_reviews())
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].reviewer, "Reviewer A")
        self.assertEqual(items[0].verdict, "LGTM")
        self.assertEqual(items[0].scope, "core")
        self.assertEqual(items[0].testing, "make test")
        self.assertEqual(items[0].findings, ({"severity": "nit", "message": "tidy"},))
        self.assertEqual(items[1].scope, None)
        self.assertEqual(items[1].findings, ())

    def test_rejects_non_array(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews({"reviewer": "x"})

    def test_rejects_non_object_entry(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews(["not-an-object"])

    def test_rejects_missing_reviewer(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"verdict": "LGTM"}])

    def test_rejects_blank_reviewer(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "  ", "verdict": "LGTM"}])

    def test_rejects_missing_verdict(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "a"}])

    def test_rejects_blank_verdict(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "a", "verdict": ""}])

    def test_rejects_non_string_scope(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "a", "verdict": "LGTM", "scope": 5}])

    def test_rejects_non_string_testing(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "a", "verdict": "LGTM", "testing": 5}])

    def test_rejects_non_list_findings(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "a", "verdict": "LGTM", "findings": {}}])

    def test_rejects_non_object_finding(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "a", "verdict": "LGTM", "findings": ["x"]}])

    def test_findings_none_is_empty(self):
        items = review.parse_reviews([{"reviewer": "a", "verdict": "LGTM", "findings": None}])
        self.assertEqual(items[0].findings, ())

    def test_parses_optional_vendor_and_model_provenance(self):
        items = review.parse_reviews(
            [
                {"reviewer": "a", "verdict": "LGTM", "vendor": " Claude ", "model": "opus"},
                {"reviewer": "b", "verdict": "LGTM"},
            ]
        )
        self.assertEqual(items[0].vendor, "Claude")
        self.assertEqual(items[0].model, "opus")
        self.assertIsNone(items[1].vendor)
        self.assertIsNone(items[1].model)

    def test_blank_vendor_becomes_none(self):
        items = review.parse_reviews([{"reviewer": "a", "verdict": "LGTM", "vendor": "  "}])
        self.assertIsNone(items[0].vendor)

    def test_rejects_non_string_vendor(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "a", "verdict": "LGTM", "vendor": 5}])

    def test_rejects_non_string_model(self):
        with self.assertRaises(review.ReviewError):
            review.parse_reviews([{"reviewer": "a", "verdict": "LGTM", "model": 5}])


class TestRunIdSubKeys(unittest.TestCase):
    def test_review_run_id_is_stable_slug(self):
        self.assertEqual(review.review_run_id("run", "Reviewer A"), "run:rv-reviewer-a")

    def test_closure_run_id(self):
        self.assertEqual(review.closure_run_id("run"), "run:closure")

    def test_jury_run_id(self):
        self.assertEqual(review.jury_run_id("run"), "run:jury")


class TestBuildReviewPlan(unittest.TestCase):
    def _items(self):
        return review.parse_reviews(_two_reviews())

    def test_exact_count_builds_pinned_verdicts(self):
        plan = review.build_review_plan(
            self._items(),
            required_count=2,
            head_sha="abc123",
            pull_request=42,
            issue=7,
            run_id="run",
            tier=2,
        )
        self.assertEqual(plan.supplied_count, 2)
        self.assertEqual(plan.required_count, 2)
        self.assertEqual(len(plan.posts), 2)
        first = plan.posts[0]
        self.assertEqual(first.artifact, "review-verdict")
        self.assertEqual(first.target_kind, "pr")
        self.assertEqual(first.target_number, 42)
        self.assertEqual(first.run_id, "run:rv-reviewer-a")
        self.assertEqual(first.marker, evidence.REVIEW_VERDICT_MARKER)
        self.assertIn("head: abc123", first.body)
        self.assertIn("Verdict: LGTM", first.body)

    def test_vendor_provenance_flows_into_rendered_verdict(self):
        items = review.parse_reviews(
            [
                {"reviewer": "A", "verdict": "LGTM", "vendor": "claude", "model": "opus"},
                {"reviewer": "B", "verdict": "LGTM", "vendor": "codex"},
            ]
        )
        plan = review.build_review_plan(
            items,
            required_count=2,
            head_sha="abc123",
            pull_request=42,
            issue=7,
            run_id="run",
            tier=2,
        )
        self.assertIn("vendor: claude", plan.posts[0].body)
        self.assertIn("model: opus", plan.posts[0].body)
        self.assertIn("vendor: codex", plan.posts[1].body)
        self.assertNotIn("model:", plan.posts[1].body)

    def test_over_count_is_allowed(self):
        items = review.parse_reviews([*_two_reviews(), {"reviewer": "C", "verdict": "LGTM"}])
        plan = review.build_review_plan(
            items,
            required_count=2,
            head_sha="h",
            pull_request=1,
            issue=None,
            run_id="run",
            tier=2,
        )
        self.assertEqual(len(plan.posts), 3)

    def test_under_count_fails(self):
        items = review.parse_reviews([{"reviewer": "a", "verdict": "LGTM"}])
        with self.assertRaises(review.ReviewError):
            review.build_review_plan(
                items,
                required_count=2,
                head_sha="h",
                pull_request=1,
                issue=None,
                run_id="run",
                tier=2,
            )

    def test_head_sha_none_renders_placeholder(self):
        plan = review.build_review_plan(
            self._items(),
            required_count=2,
            head_sha=None,
            pull_request=1,
            issue=None,
            run_id="run",
            tier=2,
        )
        self.assertIn("head: <head-sha>", plan.posts[0].body)

    def test_closure_posts_to_pr_and_issue(self):
        plan = review.build_review_plan(
            self._items(),
            required_count=2,
            head_sha="h",
            pull_request=9,
            issue=33,
            run_id="run",
            tier=2,
            closure_record={"run_id": "RUN-1", "pull_request": {"number": 9}},
        )
        closure_posts = [p for p in plan.posts if p.artifact == "closure-comment"]
        self.assertEqual(len(closure_posts), 2)
        self.assertEqual({p.target_kind for p in closure_posts}, {"pr", "issue"})
        self.assertEqual(closure_posts[0].marker, closure.COMMENT_MARKER)
        self.assertEqual(closure_posts[0].run_id, "run:closure")

    def test_closure_omits_issue_when_absent(self):
        plan = review.build_review_plan(
            self._items(),
            required_count=2,
            head_sha="h",
            pull_request=9,
            issue=None,
            run_id="run",
            tier=2,
            closure_record={"run_id": "RUN-1"},
        )
        closure_posts = [p for p in plan.posts if p.artifact == "closure-comment"]
        self.assertEqual(len(closure_posts), 1)
        self.assertEqual(closure_posts[0].target_kind, "pr")

    def test_closure_must_be_object(self):
        with self.assertRaises(review.ReviewError):
            review.build_review_plan(
                self._items(),
                required_count=2,
                head_sha="h",
                pull_request=9,
                issue=None,
                run_id="run",
                tier=2,
                closure_record=["nope"],
            )

    def test_a_jury_record_posts_the_panels_verdict_beside_its_ballots(self):
        """One call posts the whole panel: N ballots plus the consensus record (#1015)."""
        plan = review.build_review_plan(
            self._items(),
            required_count=2,
            head_sha="abc123",
            pull_request=9,
            issue=None,
            run_id="run",
            tier=3,
            jury_record={
                "verdict": "REQUEST_CHANGES",
                "participants": ["alpha (anthropic)", "beta (google)"],
                "participating_vendors": 2,
                "panelists": 2,
                "findings_summary": ["major: a real one"],
                "remaining_risks": None,
            },
        )

        jury_posts = [post for post in plan.posts if post.artifact == "jury-verdict"]
        self.assertEqual(len(jury_posts), 1)
        post = jury_posts[0]
        self.assertEqual(post.marker, evidence.JURY_VERDICT_MARKER)
        self.assertEqual(post.run_id, "run:jury")
        self.assertEqual(post.target_kind, "pr")
        # Ballots and verdict are pinned to the same head by construction.
        self.assertIn("head: abc123", post.body)
        self.assertIn("vendors: 2", post.body)
        self.assertIn("panelists: 2", post.body)
        self.assertIn("AI Jury verdict: REQUEST_CHANGES.", post.body)

    def test_a_jury_record_rides_before_the_closure(self):
        plan = review.build_review_plan(
            self._items(),
            required_count=2,
            head_sha="h",
            pull_request=9,
            issue=33,
            run_id="run",
            tier=3,
            closure_record={"run_id": "RUN-1"},
            jury_record={"verdict": "LGTM", "panelists": 2},
        )

        self.assertEqual(
            [post.artifact for post in plan.posts],
            [
                "review-verdict",
                "review-verdict",
                "jury-verdict",
                "closure-comment",
                "closure-comment",
            ],
        )

    def test_an_unknown_jury_field_is_dropped_rather_than_forwarded(self):
        """The record comes from a parsed ai-jury report; the renderer takes keel's fields."""
        plan = review.build_review_plan(
            self._items(),
            required_count=2,
            head_sha="h",
            pull_request=9,
            issue=None,
            run_id="run",
            tier=3,
            jury_record={"verdict": "LGTM", "panelists": 2, "surprise": "boom"},
        )

        self.assertIn("AI Jury verdict: LGTM.", plan.posts[-1].body)

    def test_jury_record_must_be_object(self):
        with self.assertRaises(review.ReviewError):
            review.build_review_plan(
                self._items(),
                required_count=2,
                head_sha="h",
                pull_request=9,
                issue=None,
                run_id="run",
                tier=3,
                jury_record=["nope"],
            )

    def test_as_dict_round_trips(self):
        plan = review.build_review_plan(
            self._items(),
            required_count=2,
            head_sha="h",
            pull_request=9,
            issue=2,
            run_id="run",
            tier=3,
        )
        data = plan.as_dict()
        self.assertEqual(data["schema_version"], review.SCHEMA_VERSION)
        self.assertEqual(data["tier"], 3)
        self.assertEqual(data["posts"][0]["target"], {"kind": "pr", "number": 9})


#: A three-panelist, two-vendor ai-jury report (schema 1.1) with one verified
#: consensus finding — the shape `keel review --from-jury` maps onto s7 evidence.
JURY_REPORT = {
    "schema_version": "1.1",
    "findings": [
        {
            "severity": "major",
            "file": "src/keel/review.py",
            "line": 42,
            "claim": "the closure path drops the jury post",
            "reviewer": "alpha",
        }
    ],
    "consensus": [
        {
            "representative": {
                "severity": "major",
                "file": "src/keel/review.py",
                "line": 42,
                "claim": "the closure path drops the jury post",
                "reviewer": "alpha",
            },
            "reviewers": ["alpha", "gamma"],
            "verification_status": "verified",
        }
    ],
    "reviewers": [
        {
            "name": "alpha",
            "vendor": "anthropic",
            "model": "claude-opus-4",
            "verdict": "REQUEST_CHANGES",
            "findings": [0],
            "round1_ok": True,
            "verified_count": 1,
        },
        {
            "name": "beta",
            "vendor": "google",
            "model": "gemini-3-pro",
            "verdict": "COMMENT",
            "findings": [],
            "round1_ok": True,
            "verified_count": 0,
        },
        {
            "name": "gamma",
            "vendor": "anthropic",
            "model": "",
            "verdict": "APPROVE",
            "findings": [],
            "round1_ok": True,
            "verified_count": 1,
        },
        {
            "name": "chair",
            "role": "chair",
            "vendor": "openai",
            "model": "gpt-5",
            "verdict": "REQUEST_CHANGES",
        },
    ],
}

JURY_PANEL_PROJECT = """
extends: keel
core_version: "^1.0"
base_branch: main
owner: acme
repo: widget
knobs:
  build_gate_cmd: "true"
  tier3_globs: ["src/**"]
  team:
    review:
      by_tier:
        "3": jury
    jury: { mode: gating, min_vendors: 2 }
"""


def _jury_project() -> str:
    path = Path(_TMP.name) / f"jury-project-{next(_TMP_COUNTER)}.yaml"
    path.write_text(JURY_PANEL_PROJECT, encoding="utf-8")
    return str(path)


class TestReviewFromJury(unittest.TestCase):
    """`keel review --from-jury`: the panel's ballots *are* the s7 evidence (#1015)."""

    def _run(self, report=None, *, project=None, extra=()):
        path = _write(JURY_REPORT if report is None else report)
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            return run(
                [
                    "review",
                    project or _jury_project(),
                    "--pr",
                    "42",
                    "--from-jury",
                    path,
                    "--changed-file",
                    "src/keel/review.py",
                    "--head-sha",
                    "abc123",
                    "--run-id",
                    "run",
                    "--json",
                    *extra,
                ]
            )

    def test_each_ballot_becomes_a_head_pinned_verdict_with_provenance(self):
        rc, out, err = self._run()

        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        posts = data["plan"]["posts"]
        verdicts = [post for post in posts if post["artifact"] == "review-verdict"]
        self.assertEqual(len(verdicts), 3)
        self.assertEqual(
            [post["run_id"] for post in verdicts],
            ["run:rv-alpha", "run:rv-beta", "run:rv-gamma"],
        )
        for post in verdicts:
            self.assertIn("head: abc123", post["body"])
            self.assertIn("vendor: ", post["body"])
        self.assertIn("vendor: anthropic", verdicts[0]["body"])
        self.assertIn("model: claude-opus-4", verdicts[0]["body"])
        self.assertIn("vendor: google", verdicts[1]["body"])

    def test_the_jury_verdict_still_posts_as_the_consensus_record(self):
        rc, out, err = self._run()

        self.assertEqual(rc, 0, err)
        posts = json.loads(out)["plan"]["posts"]
        jury_posts = [post for post in posts if post["artifact"] == "jury-verdict"]
        self.assertEqual(len(jury_posts), 1)
        body = jury_posts[0]["body"]
        self.assertIn("vendors: 2", body)
        self.assertIn("panelists: 3", body)
        self.assertIn("head: abc123", body)

    def test_the_panel_that_ran_sizes_the_requirement_it_satisfies(self):
        rc, out, err = self._run()

        self.assertEqual(rc, 0, err)
        plan = json.loads(out)["plan"]
        self.assertEqual(plan["tier"], 3)
        self.assertEqual(plan["required_count"], 3)
        self.assertEqual(plan["supplied_count"], 3)

    def test_the_panel_block_hands_s9_the_verified_findings(self):
        rc, out, err = self._run()

        self.assertEqual(rc, 0, err)
        panel = json.loads(out)["panel"]
        self.assertEqual(panel["size"], 3)
        self.assertEqual(panel["vendors"], ["anthropic", "google"])
        self.assertEqual(
            panel["findings"],
            [
                {
                    "severity": "major",
                    "message": "the closure path drops the jury post",
                    "source": "jury:alpha",
                    "path": "src/keel/review.py",
                    "line": 42,
                    "decision": "block",
                }
            ],
        )
        self.assertEqual(
            [ballot["verdict"] for ballot in panel["ballots"]],
            ["REQUEST_CHANGES", "COMMENT", "LGTM"],
        )

    def test_a_host_bundle_reports_no_panel(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, out, _ = run(
                ["review", ANDROID, "--pr", "1", "--reviews", reviews, "--head-sha", "h", "--json"]
            )

        self.assertEqual(rc, 0)
        self.assertIsNone(json.loads(out)["panel"])

    def test_both_sources_at_once_is_refused(self):
        reviews = _write(_two_reviews())
        report = _write(JURY_REPORT)
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--reviews",
                    reviews,
                    "--from-jury",
                    report,
                    "--head-sha",
                    "h",
                ]
            )

        self.assertEqual(rc, 1)
        self.assertIn("exactly one of --reviews or --from-jury", err)

    def test_neither_source_is_refused(self):
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(["review", ANDROID, "--pr", "1", "--head-sha", "h"])

        self.assertEqual(rc, 1)
        self.assertIn("exactly one of --reviews or --from-jury", err)

    def test_a_report_without_ballots_says_what_to_do_about_it(self):
        rc, _, err = self._run({"findings": []})

        self.assertEqual(rc, 1)
        self.assertIn("per-reviewer ballots", err)
        self.assertIn("--format json", err)

    def test_a_panel_that_returned_nothing_is_not_a_review(self):
        rc, _, err = self._run({"findings": [], "reviewers": []})

        self.assertEqual(rc, 1)
        self.assertIn("no panelist ballot", err)

    def test_a_malformed_ballot_is_reported_not_dropped(self):
        rc, _, err = self._run({"findings": [], "reviewers": [{"verdict": "APPROVE"}]})

        self.assertEqual(rc, 1)
        self.assertIn("non-empty 'name'", err)

    def test_an_unreadable_report_names_the_flag_that_supplied_it(self):
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--from-jury",
                    str(PROJECTS / "does-not-exist.json"),
                    "--head-sha",
                    "h",
                ]
            )

        self.assertEqual(rc, 1)
        self.assertIn("cannot read --from-jury", err)

    def test_a_malformed_json_report_names_the_flag_that_supplied_it(self):
        path = Path(_TMP.name) / f"broken-{next(_TMP_COUNTER)}.json"
        path.write_text("{not json", encoding="utf-8")
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--from-jury",
                    str(path),
                    "--head-sha",
                    "h",
                ]
            )

        # A `--from-jury` file that is not JSON at all is "not a ballot report",
        # which is the actionable message; the JSON decoder never sees it.
        self.assertEqual(rc, 1)
        self.assertIn("per-reviewer ballots", err)


class TestReviewCli(unittest.TestCase):
    def test_dry_run_json_renders_and_does_not_post(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, out, _ = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--changed-file",
                    "android/app/src/main/Foo.kt",
                    "--head-sha",
                    "abc123",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["plan"]["tier"], 2)
        self.assertEqual(data["plan"]["required_count"], 2)
        self.assertEqual(data["plan"]["supplied_count"], 2)
        self.assertEqual(data["plan"]["head_sha"], "abc123")
        self.assertTrue(all(p["action"] == "dry-run" for p in data["posted"]))
        self.assertIsNone(data["verification"])

    def test_dry_run_human_output(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, out, _ = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--head-sha",
                    "abc",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("DRY-RUN: would post review-verdict", out)
        self.assertIn("keel review — dry-run", out)
        self.assertIn("required      : 2", out)
        self.assertIn("tier          : unresolved", out)

    def test_tier3_changed_file_requires_three_reviewers(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--changed-file",
                    ".github/workflows/ci.yml",
                    "--head-sha",
                    "abc",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("tier requires at least 3", err)

    def test_reviewer_override_lowers_required_count(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, out, _ = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--reviewers",
                    "1",
                    "--head-sha",
                    "abc",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["plan"]["required_count"], 1)

    def test_dry_run_and_live_conflict(self):
        reviews = _write(_two_reviews())
        rc, _, err = run(
            [
                "review",
                ANDROID,
                "--pr",
                "42",
                "--reviews",
                reviews,
                "--dry-run",
                "--live",
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("cannot be used together", err)

    def test_missing_config(self):
        reviews = _write(_two_reviews())
        rc, _, err = run(
            [
                "review",
                str(PROJECTS / "nope.yaml"),
                "--pr",
                "1",
                "--reviews",
                reviews,
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config(self):
        reviews = _write(_two_reviews())
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")
            bad = f.name
        self.addCleanup(os.unlink, bad)
        rc, _, err = run(["review", bad, "--pr", "1", "--reviews", reviews])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", err)

    def test_missing_capability_blocks(self):
        reviews = _write(_two_reviews())
        missing = runtime.CapabilityReport(
            (
                runtime.Capability("gh", False, "missing", "test"),
                runtime.Capability("gh-auth", False, "missing", "test"),
            )
        )
        with patch("keel.cli.runtime.detect", return_value=missing):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--reviews",
                    reviews,
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("missing required", err)

    def test_unreadable_reviews_file(self):
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--reviews",
                    str(PROJECTS / "does-not-exist.json"),
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("cannot read --reviews", err)

    def test_malformed_json_reviews_file(self):
        path = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
        path.write("{not json")
        path.close()
        self.addCleanup(os.unlink, path.name)
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--reviews",
                    path.name,
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("not valid JSON", err)

    def test_malformed_review_shape(self):
        reviews = _write([{"verdict": "LGTM"}])
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--reviews",
                    reviews,
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("requires a non-empty 'reviewer'", err)

    def test_unreadable_closure_file(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--reviews",
                    reviews,
                    "--closure",
                    str(PROJECTS / "missing-closure.json"),
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("cannot read --closure", err)

    def test_malformed_closure_file(self):
        reviews = _write(_two_reviews())
        closure_path = _write(["not", "an", "object"])
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "1",
                    "--reviews",
                    reviews,
                    "--closure",
                    closure_path,
                    "--head-sha",
                    "abc",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("must be a JSON object", err)

    def test_closure_included_in_dry_plan(self):
        reviews = _write(_two_reviews())
        closure_path = _write({"run_id": "RUN-1", "pull_request": {"number": 42}})
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, out, _ = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--closure",
                    closure_path,
                    "--issue",
                    "7",
                    "--head-sha",
                    "abc",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        posts = json.loads(out)["plan"]["posts"]
        closure_posts = [p for p in posts if p["artifact"] == "closure-comment"]
        self.assertEqual(len(closure_posts), 2)

    def test_under_count_fails_via_cli(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--head-sha",
                    "abc",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("refusing to under-post", err)

    def test_live_without_consent_is_blocked(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--head-sha",
                    "abc",
                    "--live",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("operator consent required", err)

    def test_live_posts_each_verdict(self):
        reviews = _write(_two_reviews())
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            return _live_fetch(argv)

        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, out, _ = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--live",
                    "--json",
                    "--approve-scope",
                    "github",
                    "--operator",
                    "tester",
                ]
            )
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertFalse(data["dry_run"])
        self.assertEqual([p["action"] for p in data["posted"]], ["posted", "posted"])
        self.assertEqual(data["plan"]["head_sha"], "abc123")
        self.assertTrue(any("POST" in argv for argv in calls))

    def test_invalid_approve_scope_is_reported(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--live",
                    "--approve-scope",
                    "bogus-scope",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("unknown consent scope", err)

    def test_live_post_comment_fetch_failure_is_reported(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])
        state = {"comment_fetches": 0}

        def fake_run(argv, **_kw):
            if "POST" in argv or "PATCH" in argv:
                return _proc(json.dumps({"id": 1}))
            endpoint = argv[-1]
            if "comments" in endpoint:
                state["comment_fetches"] += 1
                # First fetch (evidence load) succeeds; the posting fetch is malformed.
                if state["comment_fetches"] == 1:
                    return _proc("[]")
                return _proc(json.dumps({"not": "a list"}))
            return _live_fetch(argv)

        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--reviewers",
                    "1",
                    "--live",
                    "--approve-scope",
                    "github",
                    "--operator",
                    "tester",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("did not return a JSON array", err)

    def test_live_post_fetch_failure_is_reported(self):
        reviews = _write(_two_reviews())

        def fake_run(argv, **_kw):
            return _proc("boom", ok=False)

        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
        ):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--live",
                    "--approve-scope",
                    "github",
                    "--operator",
                    "tester",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("boom", err)

    def test_live_post_mutation_failure_is_reported(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])

        def fake_run(argv, **_kw):
            if "POST" in argv:
                return _proc("post failed", ok=False)
            return _live_fetch(argv)

        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, _, err = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--reviewers",
                    "1",
                    "--live",
                    "--approve-scope",
                    "github",
                    "--operator",
                    "tester",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("post failed", err)

    def test_live_owner_repo_missing(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=False) as f:
            f.write(_config_without_owner())
            cfg_path = f.name
        self.addCleanup(os.unlink, cfg_path)

        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run(
                [
                    "review",
                    cfg_path,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--reviewers",
                    "1",
                    "--live",
                    "--approve-scope",
                    "github",
                    "--operator",
                    "tester",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("owner and repo", err)

    def test_live_verify_pass(self):
        rc, out, _ = self._run_live_verify(verify_status="pass")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["verification"]["verification"]["status"], "pass")

    def test_live_verify_fail(self):
        rc, out, _ = self._run_live_verify(verify_status="fail")
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["verification"]["verification"]["status"], "fail")

    def test_live_verify_human_output(self):
        reviews = _write([{"reviewer": "a", "verdict": "LGTM"}])
        fake = {"verification": {"status": "pass"}}
        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=_live_fetch),
            patch("keel.github.run_argv", side_effect=_live_fetch),
            patch("keel.cli._verify_merge_evidence", return_value=fake),
        ):
            rc, out, _ = run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--reviewers",
                    "1",
                    "--live",
                    "--verify",
                    "--approve-scope",
                    "github",
                    "--operator",
                    "tester",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("verify        : pass", out)

    def _run_live_verify(self, *, verify_status):
        reviews = _write([{"reviewer": "a", "verdict": "LGTM"}])
        fake_verification = {"verification": {"status": verify_status, "missing": []}}
        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=_live_fetch),
            patch("keel.github.run_argv", side_effect=_live_fetch),
            patch("keel.cli._verify_merge_evidence", return_value=fake_verification),
        ):
            return run(
                [
                    "review",
                    ANDROID,
                    "--pr",
                    "42",
                    "--reviews",
                    reviews,
                    "--reviewers",
                    "1",
                    "--live",
                    "--verify",
                    "--json",
                    "--approve-scope",
                    "github",
                    "--operator",
                    "tester",
                ]
            )


def _live_fetch(argv, **_kw):
    """Offline stand-in for the gh reads/writes the live review path performs."""
    if "POST" in argv or "PATCH" in argv:
        return _proc(json.dumps({"id": 1, "html_url": "u"}))
    endpoint = argv[-1]
    if endpoint.endswith("/pulls/42"):
        return _proc(
            json.dumps(
                {
                    "body": "",
                    "head": {"sha": "abc123"},
                    "labels": [],
                }
            )
        )
    if endpoint.endswith("/pulls/42/files"):
        return _proc(json.dumps([]))
    if endpoint.endswith("/pulls/42/reviews"):
        return _proc("[]")
    if "comments" in endpoint:
        return _proc("[]")
    return _proc("unexpected endpoint", ok=False)


def _config_without_owner() -> str:
    lines = (PROJECTS / "example-android.yaml").read_text(encoding="utf-8").splitlines()
    kept = [
        line for line in lines if not line.startswith("owner:") and not line.startswith("repo:")
    ]
    return "\n".join(kept) + "\n"


class TestParseCycleReviewers(unittest.TestCase):
    def test_parses_a_list_of_reviewer_objects(self):
        reviewers = review.parse_cycle_reviewers(
            [
                {"codename": "Alpha", "verdict": "LGTM", "findings": []},
                {"codename": "Beta", "verdict": "needs fixes"},
            ]
        )
        self.assertEqual(len(reviewers), 2)
        self.assertEqual(reviewers[0]["codename"], "Alpha")
        self.assertEqual(reviewers[1]["verdict"], "needs fixes")

    def test_copies_each_entry(self):
        source = {"codename": "Alpha"}
        reviewers = review.parse_cycle_reviewers([source])
        self.assertIsNot(reviewers[0], source)

    def test_rejects_non_list_payload(self):
        with self.assertRaises(review.ReviewError) as ctx:
            review.parse_cycle_reviewers({"codename": "Alpha"})
        self.assertIn("JSON array", str(ctx.exception))

    def test_rejects_non_object_entry(self):
        with self.assertRaises(review.ReviewError) as ctx:
            review.parse_cycle_reviewers(["nope"])
        self.assertIn("reviewer #1 must be a JSON object", str(ctx.exception))


class TestReviewCycleSummaryCli(unittest.TestCase):
    def _findings(self):
        return [
            {
                "codename": "Alpha-2269",
                "focus": "Security",
                "verdict": "LGTM-with-suggestions",
                "findings": [
                    {
                        "severity": "minor",
                        "location": "a.js:1",
                        "description": "d",
                        "suggested_fix": "f",
                    }
                ],
            }
        ]

    def test_renders_body_to_stdout(self):
        path = _write(self._findings())
        rc, out, _ = run(
            [
                "review-cycle-summary",
                "--findings",
                path,
                "--head-sha",
                "abc123",
                "--run-id",
                "run-1:cycle-summary",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("keel.review-cycle-summary.v1\n"))
        self.assertIn("head: abc123", out)
        self.assertIn("## Reviewer: Alpha-2269 (Focus: Security)", out)
        self.assertIn("Merge recommendation: ⚠️ request changes", out)
        self.assertIn("<!-- keel.run-id: run-1:cycle-summary -->", out)

    def test_json_output_carries_marker_and_body(self):
        path = _write(self._findings())
        rc, out, _ = run(["review-cycle-summary", "--findings", path, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["marker"], "keel.review-cycle-summary.v1")
        self.assertEqual(data["reviewers"], 1)
        self.assertIn("## Consolidated Summary", data["body"])

    def test_missing_findings_file_errors(self):
        rc, _, err = run(
            [
                "review-cycle-summary",
                "--findings",
                str(Path(_TMP.name) / "does-not-exist.json"),
            ]
        )
        self.assertEqual(rc, 1)
        self.assertIn("cannot read --findings", err)

    def test_invalid_json_errors(self):
        path = Path(_TMP.name) / f"bad-{next(_TMP_COUNTER)}.json"
        path.write_text("{not json", encoding="utf-8")
        rc, _, err = run(["review-cycle-summary", "--findings", str(path)])
        self.assertEqual(rc, 1)
        self.assertIn("is not valid JSON", err)

    def test_malformed_payload_errors(self):
        path = _write({"codename": "Alpha"})  # object, not the required array
        rc, _, err = run(["review-cycle-summary", "--findings", path])
        self.assertEqual(rc, 1)
        self.assertIn("JSON array", err)


if __name__ == "__main__":
    unittest.main()
