"""Unit tests for the pure ``keel review`` orchestration module and CLI wiring."""

import atexit
import contextlib
import io
import itertools
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from keel import cli, closure, evidence, review, runtime

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
ANDROID = str(PROJECTS / "example-android.yaml")

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
    return runtime.CapabilityReport((
        runtime.Capability("shell", True, "ok", "test"),
        runtime.Capability("gh", True, "ok", "test"),
        runtime.Capability("gh-auth", True, "ok", "test"),
    ))


def _write(value) -> str:
    path = Path(_TMP.name) / f"reviews-{next(_TMP_COUNTER)}.json"
    path.write_text(json.dumps(value))
    return str(path)


def _two_reviews():
    return [
        {"reviewer": "Reviewer A", "verdict": "LGTM", "scope": "core",
         "findings": [{"severity": "nit", "message": "tidy"}], "testing": "make test"},
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
        items = review.parse_reviews([
            {"reviewer": "a", "verdict": "LGTM", "vendor": " Claude ", "model": "opus"},
            {"reviewer": "b", "verdict": "LGTM"},
        ])
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
        items = review.parse_reviews([
            {"reviewer": "A", "verdict": "LGTM", "vendor": "claude", "model": "opus"},
            {"reviewer": "B", "verdict": "LGTM", "vendor": "codex"},
        ])
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
        items = review.parse_reviews([*_two_reviews(),
                                      {"reviewer": "C", "verdict": "LGTM"}])
        plan = review.build_review_plan(
            items, required_count=2, head_sha="h", pull_request=1,
            issue=None, run_id="run", tier=2,
        )
        self.assertEqual(len(plan.posts), 3)

    def test_under_count_fails(self):
        items = review.parse_reviews([{"reviewer": "a", "verdict": "LGTM"}])
        with self.assertRaises(review.ReviewError):
            review.build_review_plan(
                items, required_count=2, head_sha="h", pull_request=1,
                issue=None, run_id="run", tier=2,
            )

    def test_head_sha_none_renders_placeholder(self):
        plan = review.build_review_plan(
            self._items(), required_count=2, head_sha=None, pull_request=1,
            issue=None, run_id="run", tier=2,
        )
        self.assertIn("head: <head-sha>", plan.posts[0].body)

    def test_closure_posts_to_pr_and_issue(self):
        plan = review.build_review_plan(
            self._items(), required_count=2, head_sha="h", pull_request=9,
            issue=33, run_id="run", tier=2,
            closure_record={"run_id": "RUN-1", "pull_request": {"number": 9}},
        )
        closure_posts = [p for p in plan.posts if p.artifact == "closure-comment"]
        self.assertEqual(len(closure_posts), 2)
        self.assertEqual({p.target_kind for p in closure_posts}, {"pr", "issue"})
        self.assertEqual(closure_posts[0].marker, closure.COMMENT_MARKER)
        self.assertEqual(closure_posts[0].run_id, "run:closure")

    def test_closure_omits_issue_when_absent(self):
        plan = review.build_review_plan(
            self._items(), required_count=2, head_sha="h", pull_request=9,
            issue=None, run_id="run", tier=2,
            closure_record={"run_id": "RUN-1"},
        )
        closure_posts = [p for p in plan.posts if p.artifact == "closure-comment"]
        self.assertEqual(len(closure_posts), 1)
        self.assertEqual(closure_posts[0].target_kind, "pr")

    def test_closure_must_be_object(self):
        with self.assertRaises(review.ReviewError):
            review.build_review_plan(
                self._items(), required_count=2, head_sha="h", pull_request=9,
                issue=None, run_id="run", tier=2, closure_record=["nope"],
            )

    def test_as_dict_round_trips(self):
        plan = review.build_review_plan(
            self._items(), required_count=2, head_sha="h", pull_request=9,
            issue=2, run_id="run", tier=3,
        )
        data = plan.as_dict()
        self.assertEqual(data["schema_version"], review.SCHEMA_VERSION)
        self.assertEqual(data["tier"], 3)
        self.assertEqual(data["posts"][0]["target"], {"kind": "pr", "number": 9})


class TestReviewCli(unittest.TestCase):
    def test_dry_run_json_renders_and_does_not_post(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, out, _ = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--changed-file", "android/app/src/main/Foo.kt",
                "--head-sha", "abc123", "--json",
            ])
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
            rc, out, _ = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--head-sha", "abc",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("DRY-RUN: would post review-verdict", out)
        self.assertIn("keel review — dry-run", out)
        self.assertIn("required      : 2", out)
        self.assertIn("tier          : unresolved", out)

    def test_tier3_changed_file_requires_three_reviewers(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--changed-file", ".github/workflows/ci.yml",
                "--head-sha", "abc",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("tier requires at least 3", err)

    def test_reviewer_override_lowers_required_count(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, out, _ = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--reviewers", "1", "--head-sha", "abc", "--json",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["plan"]["required_count"], 1)

    def test_dry_run_and_live_conflict(self):
        reviews = _write(_two_reviews())
        rc, _, err = run([
            "review", ANDROID, "--pr", "42", "--reviews", reviews,
            "--dry-run", "--live",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("cannot be used together", err)

    def test_missing_config(self):
        reviews = _write(_two_reviews())
        rc, _, err = run([
            "review", str(PROJECTS / "nope.yaml"), "--pr", "1", "--reviews", reviews,
        ])
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
        missing = runtime.CapabilityReport((
            runtime.Capability("gh", False, "missing", "test"),
            runtime.Capability("gh-auth", False, "missing", "test"),
        ))
        with patch("keel.cli.runtime.detect", return_value=missing):
            rc, _, err = run([
                "review", ANDROID, "--pr", "1", "--reviews", reviews,
            ])
        self.assertEqual(rc, 1)
        self.assertIn("missing required", err)

    def test_unreadable_reviews_file(self):
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "1", "--reviews",
                str(PROJECTS / "does-not-exist.json"),
            ])
        self.assertEqual(rc, 1)
        self.assertIn("cannot read --reviews", err)

    def test_malformed_json_reviews_file(self):
        path = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
        path.write("{not json")
        path.close()
        self.addCleanup(os.unlink, path.name)
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "1", "--reviews", path.name,
            ])
        self.assertEqual(rc, 1)
        self.assertIn("not valid JSON", err)

    def test_malformed_review_shape(self):
        reviews = _write([{"verdict": "LGTM"}])
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "1", "--reviews", reviews,
            ])
        self.assertEqual(rc, 1)
        self.assertIn("requires a non-empty 'reviewer'", err)

    def test_unreadable_closure_file(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "1", "--reviews", reviews,
                "--closure", str(PROJECTS / "missing-closure.json"),
            ])
        self.assertEqual(rc, 1)
        self.assertIn("cannot read --closure", err)

    def test_malformed_closure_file(self):
        reviews = _write(_two_reviews())
        closure_path = _write(["not", "an", "object"])
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "1", "--reviews", reviews,
                "--closure", closure_path, "--head-sha", "abc",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("must be a JSON object", err)

    def test_closure_included_in_dry_plan(self):
        reviews = _write(_two_reviews())
        closure_path = _write({"run_id": "RUN-1", "pull_request": {"number": 42}})
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, out, _ = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--closure", closure_path, "--issue", "7",
                "--head-sha", "abc", "--json",
            ])
        self.assertEqual(rc, 0)
        posts = json.loads(out)["plan"]["posts"]
        closure_posts = [p for p in posts if p["artifact"] == "closure-comment"]
        self.assertEqual(len(closure_posts), 2)

    def test_under_count_fails_via_cli(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--head-sha", "abc",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("refusing to under-post", err)

    def test_live_without_consent_is_blocked(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--head-sha", "abc", "--live",
            ])
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
            rc, out, _ = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--live", "--json",
                "--approve-scope", "github", "--operator", "tester",
            ])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertFalse(data["dry_run"])
        self.assertEqual([p["action"] for p in data["posted"]], ["posted", "posted"])
        self.assertEqual(data["plan"]["head_sha"], "abc123")
        self.assertTrue(any("POST" in argv for argv in calls))

    def test_invalid_approve_scope_is_reported(self):
        reviews = _write(_two_reviews())
        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--live", "--approve-scope", "bogus-scope",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("unknown consent scope", err)

    def test_live_post_comment_fetch_failure_is_reported(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])
        state = {"comment_fetches": 0}

        def fake_run(argv, **_kw):
            if "POST" in argv or "PATCH" in argv:
                return Namespace(ok=True, output=json.dumps({"id": 1}))
            endpoint = argv[-1]
            if "comments" in endpoint:
                state["comment_fetches"] += 1
                # First fetch (evidence load) succeeds; the posting fetch is malformed.
                if state["comment_fetches"] == 1:
                    return Namespace(ok=True, output="[]")
                return Namespace(ok=True, output=json.dumps({"not": "a list"}))
            return _live_fetch(argv)

        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, _, err = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--reviewers", "1", "--live",
                "--approve-scope", "github", "--operator", "tester",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("did not return a JSON array", err)

    def test_live_post_fetch_failure_is_reported(self):
        reviews = _write(_two_reviews())

        def fake_run(argv, **_kw):
            return Namespace(ok=False, output="boom")

        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
        ):
            rc, _, err = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--live",
                "--approve-scope", "github", "--operator", "tester",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("boom", err)

    def test_live_post_mutation_failure_is_reported(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])

        def fake_run(argv, **_kw):
            if "POST" in argv:
                return Namespace(ok=False, output="post failed")
            return _live_fetch(argv)

        with (
            patch("keel.cli.runtime.detect", return_value=_capable_report()),
            patch("keel.cli.run_argv", side_effect=fake_run),
            patch("keel.github.run_argv", side_effect=fake_run),
        ):
            rc, _, err = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--reviewers", "1", "--live",
                "--approve-scope", "github", "--operator", "tester",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("post failed", err)

    def test_live_owner_repo_missing(self):
        reviews = _write([{"reviewer": "Solo", "verdict": "LGTM"}])
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=False) as f:
            f.write(_config_without_owner())
            cfg_path = f.name
        self.addCleanup(os.unlink, cfg_path)

        with patch("keel.cli.runtime.detect", return_value=_capable_report()):
            rc, _, err = run([
                "review", cfg_path, "--pr", "42", "--reviews", reviews,
                "--reviewers", "1", "--live",
                "--approve-scope", "github", "--operator", "tester",
            ])
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
            rc, out, _ = run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--reviewers", "1", "--live", "--verify",
                "--approve-scope", "github", "--operator", "tester",
            ])
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
            return run([
                "review", ANDROID, "--pr", "42", "--reviews", reviews,
                "--reviewers", "1", "--live", "--verify", "--json",
                "--approve-scope", "github", "--operator", "tester",
            ])


def _live_fetch(argv, **_kw):
    """Offline stand-in for the gh reads/writes the live review path performs."""
    if "POST" in argv or "PATCH" in argv:
        return Namespace(ok=True, output=json.dumps({"id": 1, "html_url": "u"}))
    endpoint = argv[-1]
    if endpoint.endswith("/pulls/42"):
        return Namespace(ok=True, output=json.dumps({
            "body": "", "head": {"sha": "abc123"}, "labels": [],
        }))
    if endpoint.endswith("/pulls/42/files"):
        return Namespace(ok=True, output=json.dumps([]))
    if endpoint.endswith("/pulls/42/reviews"):
        return Namespace(ok=True, output="[]")
    if "comments" in endpoint:
        return Namespace(ok=True, output="[]")
    return Namespace(ok=False, output="unexpected endpoint")


def _config_without_owner() -> str:
    lines = (PROJECTS / "example-android.yaml").read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines
            if not line.startswith("owner:") and not line.startswith("repo:")]
    return "\n".join(kept) + "\n"


class TestParseCycleReviewers(unittest.TestCase):
    def test_parses_a_list_of_reviewer_objects(self):
        reviewers = review.parse_cycle_reviewers([
            {"codename": "Alpha", "verdict": "LGTM", "findings": []},
            {"codename": "Beta", "verdict": "needs fixes"},
        ])
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
        return [{
            "codename": "Alpha-2269",
            "focus": "Security",
            "verdict": "LGTM-with-suggestions",
            "findings": [{"severity": "minor", "location": "a.js:1",
                          "description": "d", "suggested_fix": "f"}],
        }]

    def test_renders_body_to_stdout(self):
        path = _write(self._findings())
        rc, out, _ = run([
            "review-cycle-summary", "--findings", path,
            "--head-sha", "abc123", "--run-id", "run-1:cycle-summary",
        ])
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
        rc, _, err = run([
            "review-cycle-summary", "--findings",
            str(Path(_TMP.name) / "does-not-exist.json"),
        ])
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
