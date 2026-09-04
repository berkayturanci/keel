"""The jury panel *is* the tier-3 review, end to end (#1015).

Before this, a tier-3 pull request paid twice for the same reading: three host
reviewers under s7 and a four-agent panel under s8, with the panel's per-reviewer
ballots reaching no gate at all — ai-jury posted one consensus comment and kept
its ballots in the JSON report. `evidence.required_items` then demanded both, so
the cheapest way through was to run the panel and ignore it.

These tests drive the whole path the deliverable describes, through the CLI, with
no network: an ai-jury report goes in, one head-pinned `keel.review-verdict.v1`
per panelist comes out with real vendor provenance, the jury verdict stays as the
consensus record, and `keel evidence-verify` reads the result back as the review
it is. The two vendor scenarios are the issue's own acceptance criteria.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from keel import cli, juryavail, runtime

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


PROJECT = """
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


def _report(vendors: list[str]) -> dict:
    """An ai-jury JSON report (schema 1.1) whose panelists carry ``vendors``."""
    return {
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
                "reviewers": ["alpha"],
                "verification_status": "verified",
            }
        ],
        "reviewers": [
            {
                "name": name,
                "vendor": vendor,
                "model": f"{vendor}-model",
                "verdict": verdict,
                "findings": [0] if name == "alpha" else [],
                "round1_ok": True,
                "verified_count": 1 if name == "alpha" else 0,
            }
            for name, vendor, verdict in zip(
                ("alpha", "beta", "gamma"),
                vendors,
                ("REQUEST_CHANGES", "COMMENT", "APPROVE"),
                strict=True,
            )
        ]
        + [
            {
                "name": "chair",
                "role": "chair",
                "vendor": "openai",
                "model": "gpt-5",
                "verdict": "REQUEST_CHANGES",
            }
        ],
    }


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _capable():
    return runtime.CapabilityReport(
        (
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("gh", True, "ok", "test"),
            runtime.Capability("gh-auth", True, "ok", "test"),
        )
    )


class TestTheJuryIsTheReviewPanel(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "project.yaml").write_text(PROJECT, encoding="utf-8")
        (self.root / "empty.json").write_text("[]", encoding="utf-8")
        (self.root / "body.md").write_text("Closes #1", encoding="utf-8")

    def _write(self, name: str, value) -> str:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def _map_panel(self, vendors: list[str]) -> dict:
        """Run `keel review --from-jury` and return its JSON result."""
        report = self._write("report.json", _report(vendors))
        with patch("keel.cli.runtime.detect", return_value=_capable()):
            rc, out, err = run(
                [
                    "review",
                    str(self.root / "project.yaml"),
                    "--root",
                    str(self.root),
                    "--pr",
                    "7",
                    "--from-jury",
                    report,
                    "--changed-file",
                    "src/keel/review.py",
                    "--head-sha",
                    "abc123",
                    "--run-id",
                    "run",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def _verify(self, posts: list[dict]) -> tuple[int, dict]:
        """Feed the planned comments back to `keel evidence-verify`."""
        comments = self._write(
            "comments.json",
            [
                {"body": post["body"], "author_association": "OWNER", "user": {"login": "orch"}}
                for post in posts
            ],
        )
        rc, out, err = run(
            [
                "evidence-verify",
                str(self.root / "project.yaml"),
                "--root",
                str(self.root),
                "--pr",
                "7",
                "--changed-file",
                "src/keel/review.py",
                "--head-sha",
                "abc123",
                "--pr-label",
                "keel:ship",
                "--pr-label",
                "agent:anthropic",
                "--pr-body-file",
                str(self.root / "body.md"),
                "--pr-comments-json",
                comments,
                "--issue-comments-json",
                str(self.root / "empty.json"),
                "--pr-reviews-json",
                str(self.root / "empty.json"),
                "--phase",
                "pre-merge",
                "--require-distinct-vendors",
                "--json",
            ]
        )
        self.assertIn(rc, (0, 1), err)
        return rc, json.loads(out)

    def test_a_two_vendor_panel_of_three_satisfies_the_gate(self):
        """The issue's first acceptance criterion, driven through both commands."""
        result = self._map_panel(["anthropic", "google", "anthropic"])

        verdicts = [p for p in result["plan"]["posts"] if p["artifact"] == "review-verdict"]
        self.assertEqual(len(verdicts), 3)
        self.assertEqual(
            [line for post in verdicts for line in post["body"].splitlines() if "vendor:" in line],
            ["vendor: anthropic", "vendor: google", "vendor: anthropic"],
        )

        rc, report = self._verify(result["plan"]["posts"])

        self.assertEqual(rc, 0)
        self.assertEqual(report["verification"]["status"], "pass")
        self.assertEqual(report["verification"]["missing"], [])
        self.assertEqual(report["verification"]["findings"], [])
        self.assertEqual(
            [item["id"] for item in report["verification"]["results"]],
            ["review-verdict-1", "review-verdict-2", "review-verdict-3", "jury-verdict"],
        )

    def test_a_single_vendor_panel_of_three_fails_distinctness(self):
        """The issue's second criterion: one vendor three times is one opinion."""
        result = self._map_panel(["anthropic", "anthropic", "anthropic"])

        rc, report = self._verify(result["plan"]["posts"])

        self.assertEqual(rc, 1)
        self.assertEqual(report["verification"]["status"], "fail")
        finding = next(
            f for f in report["verification"]["findings"] if f["id"] == "review-vendor-distinctness"
        )
        self.assertEqual(finding["severity"], "major")
        # The panel's own rule, not the per-slot one: it names the span it found.
        self.assertIn("spans 1 distinct vendor(s), below the minimum of 2", finding["message"])

    def test_a_short_panel_never_shrinks_the_review_it_owes(self):
        """A short panel loses nothing from what it owes (#1014 round 3, #1015).

        A panel spanning one vendor is below `min_vendors`. On a tier whose panel
        is the whole review that changes **nothing** about the requirement: the
        bench does not move (only the surfaces that read the posted verdict ever
        learn the vendor count, so a moving bench would split the contract), all
        three ballots stay required, and the jury verdict stays required too —
        a short panel may not excuse itself from the consensus record that says
        it was short. The shortfall is reported by the vendor check above.
        """
        result = self._map_panel(["anthropic", "anthropic", "anthropic"])

        _rc, report = self._verify(result["plan"]["posts"])

        results = report["verification"]["results"]
        self.assertEqual(
            [item["id"] for item in results if item["kind"] == "review"],
            ["review-verdict-1", "review-verdict-2", "review-verdict-3"],
        )
        self.assertTrue(all(item["present"] for item in results if item["kind"] == "review"))
        self.assertIn("jury-verdict", [item["id"] for item in results])

    def test_the_panel_is_dispatched_once_not_beside_a_host_bench(self):
        """`keel ship --json` at tier-3: the reviewers are the panel, and only the panel."""
        rc, out, err = run(
            ["ship", str(self.root / "project.yaml"), "--root", str(self.root), "--json"]
        )

        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        assessment = data["result"]["assessment"]
        self.assertEqual(assessment["tier"], 3)
        reviewers = assessment["review_merge_contract"]["reviewers"]
        self.assertEqual(reviewers["source"], "jury")
        self.assertEqual(reviewers["panel"], "jury")
        self.assertEqual(reviewers["slots"], [])
        self.assertEqual(reviewers["focuses"], [])
        self.assertEqual(assessment["assignment"]["reviewers"], [])
        self.assertEqual(assessment["review_merge_contract"]["jury"]["mode"], "gating")


if __name__ == "__main__":
    unittest.main()
