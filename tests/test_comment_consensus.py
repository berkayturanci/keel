"""#1570: the opt-in COMMENT-consensus merge rule, its follow-up issue, and base facts.

A jury verdict whose consensus is ``COMMENT`` — no blocking finding, review verdicts at
the head all approving — was refused by ``keel merge`` as ``jury-verdict-not-approved``,
so the only ways forward were another paid panel round that said ``COMMENT`` again or an
owner override that left the minors untracked. ``team.jury.comment_consensus`` lets such a
verdict satisfy ``jury-verdict``, files the minors as one follow-up issue, and never
relaxes ``REQUEST_CHANGES`` or a blocking finding. ``team.jury.base_facts`` hands the
jury the PR's claims about code outside the diff, to verify.
"""

import argparse
import json
import subprocess
import tempfile
import unittest
import unittest.mock as mock
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from keel import artifacts, cli, contracts, evidence, jury, ledger, ship, team
from keel import config as cfg
from keel.runner import CommandResult

HEAD = "abc123"


def _cast(reviewer, verdict, *, head=HEAD):
    body = artifacts.render_review_verdict(
        reviewer=reviewer,
        head_sha=head,
        verdict=verdict,
        scope="Checked `src/keel/evidence.py` and tests/test_evidence.py.",
    )
    return {"body": body, "author_association": "MEMBER"}


def _jury(verdict="COMMENT", *, head=HEAD, findings=("minor: rename `x`",)):
    return {
        "body": artifacts.render_jury_verdict(
            head_sha=head,
            participants=("a (anthropic)", "b (google)"),
            verdict=verdict,
            findings_summary=findings,
        ),
        "author_association": "MEMBER",
    }


def _contract(min_approvals=2, *, reviewers=2):
    contract = ship.resolve_review_contract(tier=None, reviewer_override=reviewers, jury=True)
    contract["jury"]["comment_consensus"] = min_approvals
    return contract


def _verify(items, *, contract=None, head=HEAD, enforced=True):
    return evidence.verify(
        contract if contract is not None else _contract(),
        pr_comments=items,
        head_sha=head,
        enforced=enforced,
        phase=evidence.PHASE_PRE_MERGE,
    )


APPROVALS = (_cast("alice", "APPROVE"), _cast("bob", "LGTM"))


class TheRule(unittest.TestCase):
    def test_comment_with_minors_and_two_approvals_at_the_head_passes(self):
        report = _verify([*APPROVALS, _jury(findings=("minor: rename `x`", "nit: typo"))])

        self.assertEqual(report["status"], evidence.STATUS_PASS)
        self.assertEqual(report["counts"]["jury_verdict"], 1)
        consensus = report["comment_consensus"]
        self.assertTrue(consensus["applied"])
        self.assertEqual(consensus["approvals"], 2)
        # Nits are advisory and never gate: they do not become follow-up work.
        self.assertEqual(consensus["minors"], ["minor: rename `x`"])
        self.assertNotIn(evidence.JURY_NOT_APPROVED_FINDING, [f["id"] for f in report["findings"]])

    def test_the_rule_off_keeps_the_refusal_unchanged(self):
        report = _verify([*APPROVALS, _jury()], contract=_contract(None))

        self.assertEqual(report["status"], evidence.STATUS_FAIL)
        self.assertIsNone(report["comment_consensus"])
        self.assertIn(
            "jury-verdict-not-approved: the jury's consensus at abc123 is COMMENT, "
            "not an approval.",
            evidence.refusal_reason(report),
        )

    def test_an_advisory_panel_is_left_alone(self):
        contract = _contract()
        contract["jury"]["mode"] = "advisory"
        report = _verify([*APPROVALS, _jury()], contract=contract)
        self.assertIsNone(report["comment_consensus"])
        refusal = [f for f in report["findings"] if f["id"] == evidence.JURY_NOT_APPROVED_FINDING]
        self.assertEqual(refusal[0]["severity"], "minor")

    def test_a_bool_is_not_a_count(self):
        self.assertIsNone(
            _verify([*APPROVALS, _jury()], contract=_contract(True))["comment_consensus"]
        )

    def test_hard_stops_are_never_relaxed(self):
        for verdict in ("REQUEST_CHANGES", "ABSTAIN", "NO_QUORUM"):
            with self.subTest(verdict=verdict):
                report = _verify([*APPROVALS, _jury(verdict)])
                self.assertEqual(report["status"], evidence.STATUS_FAIL)
                self.assertIsNone(report["comment_consensus"])
                self.assertIn(f"is {verdict}, not an approval.", evidence.refusal_reason(report))

    def test_an_approving_panel_needs_no_rule(self):
        report = _verify([*APPROVALS, _jury("APPROVE")])
        self.assertEqual(report["status"], evidence.STATUS_PASS)
        self.assertIsNone(report["comment_consensus"])

    def test_no_jury_verdict_leaves_the_rule_silent(self):
        report = _verify(list(APPROVALS))
        self.assertIsNone(report["comment_consensus"])
        self.assertIn("jury-verdict", report["missing"])

    def _refused(self, items, reason, **kw):
        report = _verify(items, **kw)
        self.assertEqual(report["status"], evidence.STATUS_FAIL)
        self.assertFalse(report["comment_consensus"]["applied"])
        self.assertEqual(report["comment_consensus"]["reason"], reason)
        self.assertIn(f"(comment_consensus not met: {reason}).", evidence.refusal_reason(report))
        return report

    def test_a_blocking_finding_holds(self):
        for label in ("major", "critical", "MAJOR"):
            with self.subTest(label=label):
                self._refused(
                    [*APPROVALS, _jury(findings=(f"{label}: wrong", "minor: x"))],
                    f"its findings summary carries a blocking finding ({label}: wrong)",
                )

    def test_an_unrecognised_severity_holds(self):
        for item in ("no colon here", "blocker: stop", "warning: odd", ": blank label"):
            with self.subTest(item=item):
                self._refused(
                    [*APPROVALS, _jury(findings=(item, "minor: x"))],
                    f"its findings summary carries an item with an unrecognised severity ({item})",
                )

    def test_one_approval_is_not_enough(self):
        report = self._refused(
            [APPROVALS[0], _jury()],
            "1 of 2 required approving review verdicts at the head",
            contract=_contract(reviewers=1),
        )
        self.assertEqual(report["comment_consensus"]["approvals"], 1)

    def test_min_approvals_raises_the_bar(self):
        self._refused(
            [*APPROVALS, _jury()],
            "2 of 3 required approving review verdicts at the head",
            contract=_contract(3),
        )

    def test_a_non_approving_reviewer_holds(self):
        self._refused(
            [*APPROVALS, _cast("carol", "COMMENT"), _jury()],
            "1 review verdict(s) at the head do not approve",
        )

    def test_approvals_at_an_older_head_do_not_count(self):
        self._refused(
            [_cast("alice", "APPROVE", head="old"), _cast("bob", "APPROVE", head="old"), _jury()],
            "0 of 2 required approving review verdicts at the head",
        )

    def test_an_unreadable_summary_holds(self):
        body = (
            f"{evidence.JURY_VERDICT_MARKER}\nhead: {HEAD}\nvendors: 2\npanelists: 2\n\n"
            "AI Jury verdict: COMMENT.\n"
        )
        self._refused(
            [*APPROVALS, {"body": body, "author_association": "MEMBER"}],
            "its findings summary is not one keel rendered and cannot be read",
        )

    def test_a_blank_head_never_relaxes(self):
        report = _verify([*APPROVALS, _jury()], head=None, enforced=True)
        consensus = report["comment_consensus"]
        self.assertFalse(consensus["applied"])
        self.assertEqual(consensus["reason"], "no head was resolved to hold the review verdicts to")
        self.assertEqual(report["counts"]["jury_verdict"], 0)

    def test_comment_with_no_findings_passes_with_nothing_to_follow_up(self):
        report = _verify([*APPROVALS, _jury(findings=())])
        self.assertTrue(report["comment_consensus"]["applied"])
        self.assertEqual(report["comment_consensus"]["minors"], [])


class ThePolicy(unittest.TestCase):
    def test_parse_and_canonical(self):
        cases = [
            ({"enabled": True}, 2),
            ({"enabled": True, "min_approvals": 3}, 3),
            ({"enabled": True, "min_approvals": 1}, 2),
            ({"enabled": True, "min_approvals": True}, 2),
            ({"enabled": True, "min_approvals": "3"}, 2),
            ({"enabled": False, "min_approvals": 3}, None),
            ({"enabled": "yes"}, None),
            ("on", None),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                policy = team.parse_team({"jury": {"comment_consensus": raw}})
                self.assertEqual(policy.jury_comment_consensus, expected)
                jury_block = team.canonical(policy)["team"].get("jury", {})
                if expected is None:
                    self.assertNotIn("comment_consensus", jury_block)
                else:
                    self.assertEqual(
                        jury_block["comment_consensus"],
                        {"enabled": True, "min_approvals": expected},
                    )

    def test_base_facts_is_tri_state(self):
        for raw, expected in ((True, True), (False, False), ("yes", None)):
            with self.subTest(raw=raw):
                policy = team.parse_team({"jury": {"base_facts": raw}})
                self.assertEqual(policy.jury_base_facts, expected)
                block = team.canonical(policy)["team"].get("jury", {})
                self.assertEqual(block.get("base_facts"), expected)

    def test_unset_leaves_the_canonical_form_alone(self):
        self.assertEqual(team.canonical(team.parse_team({"jury": {}})), {"team": {}})

    def test_the_count_travels_to_the_contract(self):
        policy = team.parse_team({"jury": {"comment_consensus": {"enabled": True}}})
        assignment = team.resolve_assignment(policy, tier=3)
        self.assertEqual(assignment["jury"]["comment_consensus"], 2)
        contract = ship.resolve_review_contract(tier=3, assignment=assignment)
        self.assertEqual(contract["jury"]["comment_consensus"], 2)
        self.assertIsNone(ship.resolve_review_contract(tier=3)["jury"]["comment_consensus"])

    def test_the_schema_accepts_the_options(self):
        from keel import jsonschema_min

        schema = json.loads(
            Path(cfg.__file__)
            .with_name("schema")
            .joinpath("project.schema.json")
            .read_text(encoding="utf-8")
        )
        jury_schema = schema["properties"]["knobs"]["properties"]["team"]["properties"]["jury"]
        good = {"comment_consensus": {"enabled": True, "min_approvals": 2}, "base_facts": True}
        self.assertEqual(jsonschema_min.validate(good, jury_schema), [])
        for bad in (
            {"comment_consensus": {"min_approvals": 2}},
            {"comment_consensus": {"enabled": True, "min_approvals": 1}},
            {"base_facts": "yes"},
        ):
            with self.subTest(bad=bad):
                self.assertNotEqual(jsonschema_min.validate(bad, jury_schema), [])


class TheLedgerRecord(unittest.TestCase):
    def _record(self, **overrides):
        kwargs = {
            "pr_number": 7,
            "head_sha": HEAD,
            "issue_number": 9,
            "issue_url": "https://example.test/9",
            "findings": ["minor: x"],
            "recorded_at": "2026-10-20T00:00:00+00:00",
        }
        kwargs.update(overrides)
        return ledger.build_jury_followup_record(**kwargs)

    def test_round_trip_and_lookup(self):
        first = self._record()
        later = self._record(issue_number=10)
        other = self._record(head_sha="def456", issue_number=11)
        text = "".join(ledger.encode_record(r) for r in (first, later, other))
        text += ledger.encode_record(
            ledger.build_consent_delegation_record(
                {
                    "swarm_id": "s",
                    "clusters": ["c"],
                    "scopes": ["git"],
                    "operator": "o",
                    "source": "flag",
                    "mode": "explicit",
                    "delegated_at": "t",
                },
                recorded_at="t",
            )
        )
        records = ledger.parse_records(text, kinds=(ledger.RECORD_TYPE_JURY_FOLLOWUP,))
        self.assertEqual(ledger.jury_followup_for_head(records, 7, HEAD)["issue"]["number"], 10)
        self.assertEqual(
            ledger.jury_followup_for_head(records, 7, "capture999", covered_heads=(HEAD,))["issue"][
                "number"
            ],
            10,
        )
        self.assertIsNone(ledger.jury_followup_for_head(records, 8, HEAD))
        mixed = [{"record_type": ledger.RECORD_TYPE_SHIP_RUN}, *records]
        self.assertEqual(ledger.jury_followup_for_head(mixed, 7, HEAD)["issue"]["number"], 10)
        self.assertIn(ledger.RECORD_TYPE_JURY_FOLLOWUP, ledger.KNOWN_RECORD_TYPES)
        # A plain reader still sees ship runs only.
        self.assertEqual(ledger.parse_records(text), [])

    def test_validation(self):
        cases = {
            "pull_request.number must be a positive integer": {"pr_number": 0},
            "head_sha must be a non-blank string": {"head_sha": " "},
            "issue.number must be a positive integer": {"issue_number": True},
            "findings must be a non-empty list of non-blank strings": {"findings": []},
        }
        for message, overrides in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ledger.LedgerError, message):
                    self._record(**overrides)


class TheArtifacts(unittest.TestCase):
    def test_the_pr_body_carries_the_section_only_when_asked(self):
        self.assertNotIn(artifacts.BASE_FACTS_HEADING, artifacts.render_pr_body())
        body = artifacts.render_pr_body(base_facts=True)
        self.assertIn(f"{artifacts.BASE_FACTS_HEADING}\n{artifacts.BASE_FACTS_PROMPT}\n", body)
        self.assertLess(body.index("## Context"), body.index(artifacts.BASE_FACTS_HEADING))
        self.assertLess(body.index(artifacts.BASE_FACTS_HEADING), body.index("## Changes Made"))
        # The unfilled prompt states no facts.
        self.assertIsNone(artifacts.base_facts_section(body))

    def test_ship_json_renders_it_from_the_option(self):
        assessment = SimpleNamespace(
            tier=2,
            reviewers=2,
            window_open=True,
            ci_ok=None,
            merge=SimpleNamespace(action="merge", reason="ok"),
            halted=False,
            bypassed_window=False,
            review_contract={},
            assignment=None,
        )
        verdict = SimpleNamespace(blocked=False, counts={"blocker": 0}, findings=[])
        for flag in (True, False):
            with self.subTest(base_facts=flag):
                result = contracts.ship_result_as_dict(
                    changed_files=[],
                    outcomes=[],
                    verdict=verdict,
                    assessment=assessment,
                    base_facts=flag,
                )
                body = result["artifact_bodies"]["pr_body"]
                self.assertEqual(artifacts.BASE_FACTS_HEADING in body, flag)

    def test_the_section_is_read_back(self):
        body = (
            "## Summary\n- x\n\n## Base facts \n- `a.py:3` validates the id upstream.\n"
            "- main already retries.\n\n## Changes Made\n- y\n"
        )
        self.assertEqual(
            artifacts.base_facts_section(body),
            "- `a.py:3` validates the id upstream.\n- main already retries.",
        )
        last = "## Base facts\n- only fact\n"
        self.assertEqual(artifacts.base_facts_section(last), "- only fact")

    def test_no_facts(self):
        bodies = (
            None,
            "## Summary\n",
            "## Base facts\n\n## Changes\n",
            "## Base facts\n- none\n",
            "## Base facts\nNone.\n",
            "## Base facts\n- None.\n",
            "## Base facts\nN/A\n",
            "## Base facts\n- n/a\n",
            "## Base facts\nN/A \u2014 pure refactor\n",
            "## Base facts\n- N/A \u2014 docs only\n",
            "## Base facts\nn/a - no assumptions\n",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(artifacts.base_facts_section(body))

    def test_the_followup_issue(self):
        title, body = artifacts.render_jury_followup_issue(
            pr_number=7, head_sha=HEAD, findings=["minor: a\n  b", "minor: c"]
        )
        self.assertEqual(title, "Follow up the jury's minor findings from #7")
        self.assertIn("- [ ] minor: a b\n- [ ] minor: c\n", body)
        self.assertIn(f"#7 at `{HEAD}`", body)
        self.assertTrue(body.endswith("Refs #7\n"))


class TheJuryInput(unittest.TestCase):
    def test_facts_go_ahead_of_the_diff(self):
        diff = "diff --git a/x b/x\n+1\n"
        out = jury.with_base_facts(diff, "  - `a.py:3` checks it.  ")
        self.assertTrue(out.startswith(jury.BASE_FACTS_LEAD + "\n\n- `a.py:3` checks it.\n\n"))
        self.assertIn(jury.BASE_FACTS_CHECK + "\n\n" + diff, out)
        self.assertTrue(out.endswith(diff))

    def test_nothing_to_add(self):
        for facts in (None, "", "  "):
            self.assertEqual(jury.with_base_facts("d", facts), "d")
        self.assertEqual(jury.with_base_facts("", "fact"), "")

    def test_run_gate_hands_the_facts_to_the_panel(self):
        seen = {}

        def fake(argv, **kwargs):
            if argv[:2] == ["jury", "--version"]:
                return CommandResult(True, 0, "1")
            seen["diff"] = Path(argv[argv.index("--diff-file") + 1]).read_text(encoding="utf-8")
            return CommandResult(True, 0, "{}", stdout='{"findings": []}')

        with mock.patch.object(jury, "run_argv", side_effect=fake):
            jury.run_gate("diff --git a/x b/x\n", base_facts="- fact", _run=None)
        self.assertTrue(seen["diff"].startswith(jury.BASE_FACTS_LEAD))
        self.assertTrue(seen["diff"].endswith("diff --git a/x b/x\n"))


def _config(tmp, *, base_facts=True):
    path = Path(tmp) / "project.yaml"
    jury_block = "    jury:\n      base_facts: true\n" if base_facts else ""
    path.write_text(
        "extends: keel\ncore_version: '^1.0'\nowner: o\nrepo: r\nbase_branch: main\n"
        "knobs:\n  team:\n" + (jury_block or "    {}\n"),
        encoding="utf-8",
    )
    return path


class TheCli(unittest.TestCase):
    def test_pr_base_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            on = cfg.load_config(_config(tmp))
            pull = {"body": "## Base facts\n- fact\n"}
            with mock.patch.object(cli, "_gh_json", return_value=pull) as gh:
                self.assertEqual(cli._pr_base_facts(on, 7, root=tmp), "- fact")
            gh.assert_called_once_with(["repos", "o/r", "pulls", "7"], cwd=tmp)
            with mock.patch.object(cli, "_gh_json", return_value={"body": None}):
                self.assertIsNone(cli._pr_base_facts(on, 7, root=tmp))
            with mock.patch.object(cli, "_gh_json", side_effect=ValueError("down")):
                self.assertIsNone(cli._pr_base_facts(on, 7, root=tmp))
            self.assertIsNone(cli._pr_base_facts(on, None, root=tmp))
            rest = argparse.Namespace(name="rest")
            self.assertIsNone(cli._pr_base_facts(on, 7, root=tmp, transport=rest))
            off = cfg.load_config(_config(tmp, base_facts=False))
            self.assertIsNone(cli._pr_base_facts(off, 7, root=tmp))

    def test_the_gate_runner_passes_the_facts_on(self):
        spec = argparse.Namespace(kind="builtin", id="jury", phase="test", timeout=5)
        with mock.patch.object(jury, "run_gate", return_value=(True, [], False)) as gate:
            cli._gate_runner(".", "d", base_facts="- fact")(spec)
        self.assertEqual(gate.call_args.kwargs.get("base_facts"), "- fact")

    def _jury_diff(self, tmp, *extra, base_facts=True, diff="diff --git a/x b/x\n"):
        config = _config(tmp, base_facts=base_facts)
        diff_path = Path(tmp) / "in.diff"
        diff_path.write_text(diff, encoding="utf-8")
        out = Path(tmp) / "out.diff"
        err = StringIO()
        with redirect_stderr(err):
            code = cli.main(
                ["jury-diff", str(config), "--diff", str(diff_path), "--out", str(out), *extra]
            )
        return code, (out.read_text(encoding="utf-8") if out.exists() else None), err.getvalue()

    def test_jury_diff_from_a_body_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.md"
            body.write_text("## Base facts\n- fact\n", encoding="utf-8")
            code, out, _ = self._jury_diff(tmp, "--pr-body-file", str(body))
            self.assertEqual(code, 0)
            self.assertTrue(out.startswith(jury.BASE_FACTS_LEAD))

    def test_jury_diff_from_the_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli, "_pr_base_facts", return_value="- f") as read:
                code, out, _ = self._jury_diff(tmp, "--pr", "7")
            self.assertEqual(code, 0)
            self.assertEqual(read.call_args.args[1], 7)
            self.assertIn("- f", out)

    def test_jury_diff_without_facts_copies_the_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self._jury_diff(tmp, "--pr", "7", base_facts=True)
            self.assertEqual((code, out), (0, "diff --git a/x b/x\n"))
            self.assertIn("no base facts stated", err)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self._jury_diff(tmp, base_facts=False)
            self.assertEqual((code, out, err), (0, "diff --git a/x b/x\n", ""))

    def test_jury_diff_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self._jury_diff(tmp, "--pr-body-file", str(Path(tmp) / "nope"))
            self.assertEqual(code, 1)
            self.assertIn("cannot read --pr-body-file", err)
            err = StringIO()
            with redirect_stderr(err):
                code = cli.main(
                    ["jury-diff", str(_config(tmp)), "--diff", "/nope", "--out", f"{tmp}/o"]
                )
            self.assertEqual(code, 1)
            self.assertIn("cannot read --diff", err.getvalue())
            with redirect_stderr(StringIO()):
                self.assertEqual(
                    cli.main(["jury-diff", f"{tmp}/missing.yaml", "--diff", "d", "--out", "o"]), 1
                )
            bad = Path(tmp) / "bad.yaml"
            bad.write_text("knobs: [\n", encoding="utf-8")
            with redirect_stderr(StringIO()):
                self.assertEqual(cli.main(["jury-diff", str(bad), "--diff", "d", "--out", "o"]), 1)
            Path(tmp, "in.diff").write_text("d", encoding="utf-8")
            with redirect_stderr(StringIO()) as err:
                code = cli.main(
                    ["jury-diff", str(_config(tmp)), "--diff", f"{tmp}/in.diff", "--out", tmp]
                )
            self.assertEqual(code, 1)
            self.assertIn("cannot write --out", err.getvalue())


class TheMergeFollowup(unittest.TestCase):
    def _run(self, tmp, consensus, *, dry_run=False, created=None, ok=True):
        config = cfg.load_config(_config(tmp))
        args = argparse.Namespace(pr=7, root=tmp, dry_run=dry_run)
        stdout = json.dumps(created) if created is not None else ""
        result = CommandResult(ok, 0 if ok else 1, stdout or "boom", stdout=stdout)
        with mock.patch.object(cli.github, "create_issue", return_value=result) as create:
            outcome = cli._comment_consensus_followup(
                args, config, {"comment_consensus": consensus}, HEAD
            )
        return outcome, create

    APPLIED = {"applied": True, "minors": ["minor: x"]}

    def test_not_applied_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            for consensus in (None, {"applied": False}):
                (record, error), create = self._run(tmp, consensus)
                self.assertEqual((record, error), (None, None))
                create.assert_not_called()

    def test_no_minors_files_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (record, error), create = self._run(tmp, {"applied": True, "minors": []})
            self.assertIsNone(error)
            self.assertFalse(record["filed"])
            create.assert_not_called()

    def test_files_once_records_it_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = {"number": 42, "html_url": "https://example.test/42"}
            (record, error), create = self._run(tmp, self.APPLIED, created=created)
            self.assertIsNone(error)
            self.assertEqual(record["issue"], {"number": 42, "url": "https://example.test/42"})
            self.assertTrue(record["filed"])
            owner_repo, title, body = create.call_args.args
            self.assertEqual(owner_repo, "o/r")
            self.assertIn("#7", title)
            self.assertIn("- [ ] minor: x", body)
            path = ledger.resolve_path(tmp, cfg.load_config(_config(tmp)))
            stored = ledger.read_records(path, kinds=(ledger.RECORD_TYPE_JURY_FOLLOWUP,))
            self.assertEqual(stored[0]["issue"]["number"], 42)

            (record, error), create = self._run(tmp, self.APPLIED, created=created)
            self.assertIsNone(error)
            self.assertTrue(record["reused"])
            self.assertEqual(record["issue"]["number"], 42)
            create.assert_not_called()

    def test_a_url_less_answer_records_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            (record, error), _ = self._run(tmp, self.APPLIED, created={"number": 5})
            self.assertIsNone(error)
            self.assertEqual(record["issue"], {"number": 5, "url": None})

    def test_dry_run_files_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (record, error), create = self._run(tmp, self.APPLIED, dry_run=True)
            self.assertIsNone(error)
            self.assertFalse(record["filed"])
            create.assert_not_called()

    def test_a_filing_failure_refuses_the_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            for created, ok in ((None, False), ({"number": True}, True), ([], True)):
                with self.subTest(created=created):
                    (record, error), _ = self._run(tmp, self.APPLIED, created=created, ok=ok)
                    self.assertIn(
                        "could not file the follow-up issue for the jury's minor findings", error
                    )
                    self.assertFalse(record["filed"])

    def test_no_owner_repo_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.yaml"
            path.write_text(
                "extends: keel\ncore_version: '^1.0'\nbase_branch: main\nknobs: {}\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(pr=7, root=tmp, dry_run=False)
            record, error = cli._comment_consensus_followup(
                args, cfg.load_config(path), {"comment_consensus": self.APPLIED}, HEAD
            )
            self.assertIn("could not file the follow-up issue", error)

    def test_an_invalid_ledger_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = ledger.resolve_path(tmp, cfg.load_config(_config(tmp)))
            path.parent.mkdir(parents=True)
            path.write_text("not json\n", encoding="utf-8")
            (record, error), create = self._run(tmp, self.APPLIED)
            self.assertTrue(error.startswith("invalid run ledger:"))
            create.assert_not_called()

    def test_an_unwritable_ledger_names_the_filed_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli.ledger, "append_record", side_effect=OSError("ro")):
                (record, error), _ = self._run(tmp, self.APPLIED, created={"number": 42})
            self.assertTrue(record["filed"])
            self.assertIn("filed follow-up issue #42, but could not record it", error)

    def test_a_capture_commit_reuses_the_followup_for_the_consensus_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = {"number": 42, "html_url": "https://example.test/42"}
            consensus = {"applied": True, "head": HEAD, "minors": ["minor: x"]}
            (record, error), create = self._run(tmp, consensus, created=created)
            self.assertIsNone(error)
            self.assertTrue(record["filed"])
            self.assertEqual(record["head_sha"], HEAD)

            config = cfg.load_config(_config(tmp))
            args = argparse.Namespace(pr=7, root=tmp, dry_run=False)
            with mock.patch.object(cli.github, "create_issue") as create_after_capture:
                reused, reuse_error = cli._comment_consensus_followup(
                    args,
                    config,
                    {"comment_consensus": consensus},
                    "capture999",
                    covered_heads=(HEAD,),
                )
            self.assertIsNone(reuse_error)
            self.assertTrue(reused["reused"])
            self.assertFalse(reused["filed"])
            self.assertEqual(reused["issue"]["number"], 42)
            self.assertEqual(reused["head_sha"], HEAD)
            create_after_capture.assert_not_called()


class TheJuryGate(unittest.TestCase):
    """#1570: the s8 `jury` gate honours `team.jury.comment_consensus`."""

    @staticmethod
    def _report(verdict="COMMENT"):
        return json.dumps(
            {
                "findings": [],
                "reviewers": [
                    {"name": "a", "vendor": "anthropic", "verdict": verdict},
                    {"name": "b", "vendor": "google", "verdict": verdict},
                    {"name": "chair", "role": "chair", "verdict": verdict},
                ],
            }
        )

    def test_reuse_posted_verdict_and_run_gate_respect_comment_consensus(self):
        body = _jury("COMMENT", findings=("minor: rename `x`",))["body"]
        applied = {
            "applied": True,
            "reason": None,
            "head": HEAD,
            "required_approvals": 2,
            "approvals": 2,
            "minors": ["minor: rename `x`"],
        }
        ok, findings = jury.reuse_posted_verdict(body, gating=True, comment_rule=applied)
        self.assertTrue(ok)
        consensus = [f for f in findings if f.source == jury.CONSENSUS_SOURCE]
        self.assertEqual(len(consensus), 1)
        self.assertEqual(consensus[0].severity, "minor")
        self.assertIn("comment_consensus applied (2 of 2", consensus[0].message)

        refused = {
            "applied": False,
            "reason": "1 of 2 required approving review verdicts at the head",
            "head": HEAD,
            "required_approvals": 2,
            "approvals": 1,
            "minors": [],
        }
        ok, findings = jury.reuse_posted_verdict(body, gating=True, comment_rule=refused)
        self.assertFalse(ok)
        consensus = [f for f in findings if f.source == jury.CONSENSUS_SOURCE]
        self.assertEqual(consensus[0].severity, "major")
        self.assertIn(
            "comment_consensus not met: 1 of 2 required approving review verdicts at the head",
            consensus[0].message,
        )

        # Rule off: blocked as before.
        ok, findings = jury.reuse_posted_verdict(body, gating=True, comment_rule=None)
        self.assertFalse(ok)
        consensus = [f for f in findings if f.source == jury.CONSENSUS_SOURCE]
        self.assertEqual(
            (consensus[0].severity, consensus[0].message),
            ("major", "posted jury consensus is COMMENT, not an approval."),
        )

        # REQUEST_CHANGES: blocked regardless, even if a comment_rule record is passed.
        reject_body = _jury("REQUEST_CHANGES", findings=())["body"]
        ok, findings = jury.reuse_posted_verdict(reject_body, gating=True, comment_rule=applied)
        self.assertFalse(ok)
        self.assertEqual(
            (findings[0].severity, findings[0].message),
            ("major", "posted jury consensus is REQUEST_CHANGES, not an approval."),
        )

        # Live `run_gate` -> `_consensus_finding` shares the same rule.
        def fake_run(argv, **kwargs):
            if argv[:2] == ["jury", "--version"]:
                return CommandResult(True, 0, "1")
            return CommandResult(True, 0, "", stdout=self._report("COMMENT"))

        with mock.patch.object(jury, "run_argv", side_effect=fake_run):
            ok, findings, _ = jury.run_gate(
                "diff --git a/x b/x\n", mode="gating", comment_rule=applied
            )
            self.assertTrue(ok)
            self.assertEqual(findings[0].severity, "minor")
            self.assertIn("comment_consensus applied (2 of 2", findings[0].message)

            ok, findings, _ = jury.run_gate(
                "diff --git a/x b/x\n", mode="gating", comment_rule=refused
            )
            self.assertFalse(ok)
            self.assertEqual(findings[0].severity, "major")
            self.assertIn("comment_consensus not met: 1 of 2", findings[0].message)

            ok, findings, _ = jury.run_gate(
                "diff --git a/x b/x\n", mode="gating", comment_rule=None
            )
            self.assertFalse(ok)
            self.assertEqual(findings[0].message, "jury consensus is COMMENT, not an approval.")

    def _ship_with_posted_panel(self, comments, *, comment_consensus=True, reviews=()):
        with tempfile.TemporaryDirectory() as root:
            jury_block = (
                "    jury:\n      comment_consensus:\n        enabled: true\n"
                if comment_consensus
                else "    {}\n"
            )
            config_path = Path(root) / "project.yaml"
            config_path.write_text(
                "extends: keel\ncore_version: '^1.0'\nbase_branch: main\nowner: o\nrepo: r\n"
                "gates: [build, jury]\nknobs:\n  build_gate_cmd: 'true'\n  team:\n"
                + jury_block
                + "policy_pack:\n  name: tmp\n  reports:\n    run_ledger: 'state/runs.jsonl'\n",
                encoding="utf-8",
            )

            def gh_list(args, *, cwd):
                if args[-1] == "reviews":
                    return list(reviews)
                return list(comments)

            report = cli.runtime.CapabilityReport(
                (
                    cli.runtime.Capability("shell", True, "ok", "test"),
                    cli.runtime.Capability("git", True, "ok", "test"),
                    cli.runtime.Capability("worktree", True, "ok", "test"),
                    cli.runtime.Capability("gh", True, "ok", "test"),
                    cli.runtime.Capability("gh-auth", True, "ok", "test"),
                )
            )
            out = StringIO()
            with (
                mock.patch("keel.cli.runtime.detect", return_value=report),
                mock.patch("keel.cli.github.ci_conclusion", return_value=None),
                mock.patch("keel.cli.github.ci_check_names", return_value=None),
                mock.patch("keel.cli.github.ci_workflow_names", return_value=None),
                mock.patch(
                    "keel.cli.github.pr_labels",
                    return_value=["agent:claude", "model:opus-4"],
                ),
                mock.patch("keel.cli._gh_json_list", side_effect=gh_list),
                mock.patch(
                    "keel.cli._gh_json",
                    return_value={
                        "title": "Fix thing",
                        "head": {"sha": HEAD, "repo": {"full_name": "o/r"}},
                    },
                ),
                mock.patch("keel.cli._covered_heads", return_value=()),
                mock.patch("keel.cli.git.diff", return_value="diff --git a/src/x.py b/src/x.py\n"),
                mock.patch("keel.jury.run_gate") as run_gate,
                mock.patch("sys.stdout", out),
            ):
                rc = cli.main(
                    [
                        "ship",
                        str(config_path),
                        "--root",
                        root,
                        "--live",
                        "--json",
                        "--append-ledger",
                        "--run-id",
                        "RUN-1570",
                        "--issue",
                        "1570",
                        "--pr",
                        "7",
                        "--head-sha",
                        HEAD,
                        "--capture-status",
                        "not-run",
                        "--approve-scope",
                        "filesystem,git,github",
                        "--operator",
                        "tester",
                    ]
                )
            data = json.loads(out.getvalue())
            record = data["result"]["run_ledger"]["record"]
            entry = next(g for g in record["gates"] if g["gate"] == "jury")
            return rc, entry, record, run_gate, data

    def test_ship_jury_gate_not_blocked_when_rule_on_and_two_approvals_at_head(self):
        rc, entry, record, run_gate, data = self._ship_with_posted_panel(
            [*APPROVALS, _jury("COMMENT", findings=("minor: rename `x`",))],
            comment_consensus=True,
        )
        run_gate.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertTrue(entry["ok"])
        self.assertTrue(ledger.record_gates_passed(record))
        matched, _ = ledger.gates_pass_for_head([record], 7, HEAD)
        self.assertTrue(matched)
        findings = data["result"]["verdict"]["findings"]
        consensus = [f for f in findings if f["source"] == jury.CONSENSUS_SOURCE]
        self.assertEqual(consensus[0]["severity"], "minor")
        self.assertIn("comment_consensus applied (2 of 2", consensus[0]["message"])

    def test_ship_jury_gate_blocked_with_reason_when_only_one_approval(self):
        _rc, entry, record, run_gate, data = self._ship_with_posted_panel(
            [APPROVALS[0], _jury("COMMENT", findings=("minor: rename `x`",))],
            comment_consensus=True,
        )
        run_gate.assert_not_called()
        self.assertFalse(entry["ok"])
        self.assertFalse(ledger.record_gates_passed(record))
        findings = data["result"]["verdict"]["findings"]
        consensus = [f for f in findings if f["source"] == jury.CONSENSUS_SOURCE]
        self.assertEqual(consensus[0]["severity"], "major")
        self.assertIn(
            "comment_consensus not met: 1 of 2 required approving review verdicts at the head",
            consensus[0]["message"],
        )

    def test_ship_jury_gate_blocked_as_before_when_rule_off(self):
        _rc, entry, record, run_gate, data = self._ship_with_posted_panel(
            [*APPROVALS, _jury("COMMENT", findings=("minor: rename `x`",))],
            comment_consensus=False,
        )
        run_gate.assert_not_called()
        self.assertFalse(entry["ok"])
        self.assertFalse(ledger.record_gates_passed(record))
        findings = data["result"]["verdict"]["findings"]
        consensus = [f for f in findings if f["source"] == jury.CONSENSUS_SOURCE]
        self.assertEqual(
            (consensus[0]["severity"], consensus[0]["message"]),
            ("major", "posted jury consensus is COMMENT, not an approval."),
        )

    def test_ship_jury_gate_blocked_regardless_on_request_changes(self):
        _rc, entry, record, run_gate, data = self._ship_with_posted_panel(
            [*APPROVALS, _jury("REQUEST_CHANGES", findings=())],
            comment_consensus=True,
        )
        run_gate.assert_not_called()
        self.assertFalse(entry["ok"])
        self.assertFalse(ledger.record_gates_passed(record))
        findings = data["result"]["verdict"]["findings"]
        consensus = [f for f in findings if f["source"] == jury.CONSENSUS_SOURCE]
        self.assertEqual(
            (consensus[0]["severity"], consensus[0]["message"]),
            ("major", "posted jury consensus is REQUEST_CHANGES, not an approval."),
        )


class TheGithubWrapper(unittest.TestCase):
    def test_create_issue_argv(self):
        from keel import github

        calls = []

        def fake(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        github.create_issue("o/r", "@t", "@b", _run=fake)
        self.assertEqual(
            calls[0],
            ["gh", "api", "repos/o/r/issues", "-X", "POST", "-f", "title=@t", "-f", "body=@b"],
        )


if __name__ == "__main__":
    unittest.main()
