"""Tests for the structured run ledger."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from keel import config as cfg
from keel import ledger, redaction


def _config(
    *,
    run_ledger: str | None = None,
    deny_patterns: list[dict] | None = None,
    learning_policy: dict | None = None,
) -> cfg.ProjectConfig:
    reports = {"run_ledger": run_ledger} if run_ledger is not None else {}
    policy_pack = {"name": "test", "reports": reports}
    if deny_patterns is not None:
        policy_pack["capture_redaction"] = {"deny_patterns": deny_patterns}
    if learning_policy is not None:
        policy_pack["capture"] = {"learning": learning_policy}
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^0.7",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true"),
        policy_pack=policy_pack,
    )


def _record(*, config: cfg.ProjectConfig | None = None) -> dict:
    outcome = SimpleNamespace(gate="build", ok=True, skipped=False, timed_out=False,
                              error=None, findings=[])
    verdict = SimpleNamespace(blocked=False, counts={"blocker": 0})
    merge = SimpleNamespace(action="merge", reason="all gates passed")
    assessment = SimpleNamespace(
        tier=2,
        reviewers=2,
        window_open=True,
        ci_ok=None,
        merge=merge,
        halted=False,
        bypassed_window=False,
    )
    return ledger.build_ship_run_record(
        command="ship",
        base_branch="main",
        changed_files=["src/keel/ledger.py"],
        outcomes=[outcome],
        verdict=verdict,
        assessment=assessment,
        issue_intake={"status": "ready"},
        target="issue #140",
        run_id="RUN-140",
        issue_number=140,
        pr_number=160,
        branch="feat/issue-140-run-ledger",
        head_sha="abc123",
        capture_status="applied",
        capture_reason="capture hook completed",
        config=config,
        implementer="codex:gpt-5",
        reviewer_agents=["reviewer-a:gpt-5", "reviewer-b:claude"],
        tester="tester:gpt-5-mini",
    )


class TestLedgerContract(unittest.TestCase):
    def test_default_contract_is_consumer_neutral(self):
        contract = ledger.ledger_contract_as_dict(_config())

        self.assertEqual(contract["schema_version"], ledger.LEDGER_SCHEMA_VERSION)
        self.assertEqual(contract["format"], "jsonl")
        self.assertEqual(contract["path"], ".keel/state/run-ledger.jsonl")
        self.assertEqual(contract["path_source"], "default")
        self.assertTrue(contract["consumer_neutral"])
        self.assertTrue(contract["capture_redaction"]["default_redaction"])
        self.assertFalse(contract["capture_redaction"]["audit_includes_original_values"])
        self.assertEqual(contract["capture_contract"]["schema_version"], "keel.capture.v1")
        self.assertTrue(contract["capture_contract"]["fail_soft"]["enabled"])
        self.assertEqual(contract["capture_health"]["schema_version"],
                         "keel.capture-health.v1")
        self.assertTrue(contract["capture_health"]["consumer_neutral"])
        self.assertIn("ship", contract["append_owner"])
        self.assertIn("morning", contract["readers"])

    def test_reports_override_changes_path_only(self):
        config = _config(run_ledger="state/runs.jsonl")

        self.assertEqual(
            ledger.ledger_contract_as_dict(config)["path_source"],
            "policy_pack.reports.run_ledger",
        )
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                ledger.resolve_path(directory, config),
                Path(directory).resolve() / "state" / "runs.jsonl",
            )

    def test_resolve_path_rejects_absolute_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ledger.LedgerError, "must be relative"):
                ledger.resolve_path(directory, _config(run_ledger="/tmp/runs.jsonl"))

            with self.assertRaisesRegex(ledger.LedgerError, "escapes"):
                ledger.resolve_path(directory, _config(run_ledger="../runs.jsonl"))

    def test_resolve_path_allows_normalized_path_inside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                ledger.resolve_path(directory, _config(run_ledger="state/../runs.jsonl")),
                Path(directory).resolve() / "runs.jsonl",
            )


class TestLedgerRecords(unittest.TestCase):
    def test_ship_run_record_schema_is_stable(self):
        record = _record()

        self.assertEqual(list(record), [
            "schema_version",
            "record_type",
            "command",
            "run_id",
            "target",
            "issue",
            "pull_request",
            "git",
            "changes",
            "declared",
            "gates",
            "verdict",
            "assessment",
            "actors",
            "run_context",
            "run_controls",
            "issue_intake",
            "capture",
        ])
        self.assertEqual(record["record_type"], "ship_run")
        self.assertEqual(record["issue"]["number"], 140)
        self.assertEqual(record["changes"]["file_count"], 1)
        self.assertEqual(record["gates"][0]["finding_count"], 0)
        self.assertEqual(record["actors"]["implementer"], "codex:gpt-5")
        self.assertEqual(record["actors"]["reviewers"], ["reviewer-a:gpt-5", "reviewer-b:claude"])
        self.assertEqual(record["actors"]["tester"], "tester:gpt-5-mini")
        self.assertIsNone(record["run_controls"])
        self.assertEqual(record["capture"]["schema_version"], "keel.capture.v1")
        self.assertEqual(record["capture"]["marker"],
                         "compound-learning: pr=160 status=applied")
        self.assertEqual(record["capture"]["learning"]["decision"], "marker-only")
        self.assertEqual(record["capture"]["learning"]["reason"], "policy-unavailable")
        self.assertTrue(record["capture"]["fail_soft"])

    def test_run_context_block_is_built_from_inputs(self):
        record = ledger.build_ship_run_record(
            command="ship",
            base_branch="main",
            changed_files=["src/keel/ledger.py"],
            outcomes=[],
            verdict=SimpleNamespace(blocked=False, counts={}),
            assessment=SimpleNamespace(
                tier=2, reviewers=2, window_open=True, ci_ok=None,
                merge=SimpleNamespace(action="merge", reason="ok"),
                halted=False, bypassed_window=False,
            ),
            host_agent="claude",
            transport="mcp",
            profile="compound",
            jury_mode="gating",
            consent_status="approved",
            consent_scopes=["pr-merge", "label"],
        )

        run_context = record["run_context"]
        self.assertEqual(run_context["host_agent"], "claude")
        self.assertEqual(run_context["transport"], "mcp")
        self.assertEqual(run_context["profile"], "compound")
        self.assertEqual(run_context["jury_mode"], "gating")
        self.assertEqual(run_context["consent"]["status"], "approved")
        self.assertEqual(run_context["consent"]["scopes"], ["pr-merge", "label"])

    def test_run_context_block_is_optional_and_degrades(self):
        # The default fixture passes none of the run-context inputs.
        run_context = _record()["run_context"]
        self.assertIsNone(run_context["host_agent"])
        self.assertIsNone(run_context["transport"])
        self.assertIsNone(run_context["profile"])
        self.assertIsNone(run_context["jury_mode"])
        self.assertIsNone(run_context["consent"]["status"])
        self.assertEqual(run_context["consent"]["scopes"], [])

    def test_run_context_blank_scalars_and_scopes_degrade(self):
        record = ledger.build_ship_run_record(
            command="ship",
            base_branch="main",
            changed_files=[],
            outcomes=[],
            verdict=SimpleNamespace(blocked=False, counts={}),
            assessment=SimpleNamespace(
                tier=1, reviewers=1, window_open=True, ci_ok=None,
                merge=SimpleNamespace(action="merge", reason="ok"),
                halted=False, bypassed_window=False,
            ),
            host_agent="  ",
            transport="",
            profile="   ",
            jury_mode="",
            consent_status="  ",
            consent_scopes=["pr-merge", "  ", ""],
        )

        run_context = record["run_context"]
        self.assertIsNone(run_context["host_agent"])
        self.assertIsNone(run_context["transport"])
        self.assertIsNone(run_context["profile"])
        self.assertIsNone(run_context["jury_mode"])
        self.assertIsNone(run_context["consent"]["status"])
        # Blank scope entries are dropped; non-blank ones are kept.
        self.assertEqual(run_context["consent"]["scopes"], ["pr-merge"])

    def test_ship_run_record_can_store_learning_create_decision(self):
        record = _record(
            config=_config(learning_policy={
                "enabled": True,
                "mode": "create-learning",
                "reason": "new invariant",
            })
        )

        learning = record["capture"]["learning"]
        self.assertEqual(learning["decision"], "create-learning")
        self.assertEqual(learning["reason"], "new invariant")
        self.assertTrue(learning["durable_artifact"])

    def test_capture_health_clean_session(self):
        record = _record(
            config=_config(learning_policy={
                "enabled": True,
                "mode": "create-learning",
            })
        )

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["schema_version"], "keel.capture-health.v1")
        self.assertEqual(summary["status"], "clean")
        self.assertEqual(summary["counts"]["applied"], 1)
        self.assertEqual(summary["counts"]["create_learning"], 1)
        self.assertEqual(summary["counts"]["needs_reconcile"], 0)
        self.assertEqual(summary["reconcile_actions"], [])
        self.assertTrue(summary["dry_run"]["no_mutations"])

    def test_capture_health_surfaces_missing_marker_distinctly(self):
        record = _record()
        record["capture"]["marker"] = None

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["status"], "needs-reconcile")
        self.assertEqual(summary["counts"]["missing_marker"], 1)
        self.assertEqual(summary["counts"]["skipped"], 0)
        self.assertEqual(summary["items"][0]["status"], "missing-marker")
        self.assertEqual(summary["items"][0]["reconcile_actions"][0]["type"],
                         "capture-reconcile")

    def test_capture_health_counts_skipped_by_allowed_reason(self):
        record = _record()
        record["capture"] = {
            "schema_version": "keel.capture.v1",
            "status": "skipped",
            "reason": "capture recursion guard matched",
            "marker_reason": "recursion-guard",
            "marker": "compound-learning: pr=160 status=skipped:recursion-guard",
            "learning": {
                "decision": "marker-only",
                "reason": "capture-skipped",
            },
        }

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["status"], "clean")
        self.assertEqual(summary["counts"]["skipped"], 1)
        self.assertEqual(summary["counts"]["marker_only"], 1)
        self.assertEqual(summary["skipped_by_reason"], {"recursion-guard": 1})

    def test_capture_health_deferred_capture_needs_reconcile(self):
        record = _record(
            config=_config(learning_policy={
                "enabled": True,
                "mode": "defer",
            })
        )
        record["capture"]["status"] = "deferred"
        record["capture"]["reason"] = "capture extension can be rerun"
        record["capture"]["marker"] = "compound-learning: pr=160 status=deferred"

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["status"], "needs-reconcile")
        self.assertEqual(summary["counts"]["deferred"], 1)
        self.assertEqual(summary["counts"]["needs_reconcile"], 1)
        self.assertEqual(summary["reconcile_actions"][0]["pr"], 160)
        self.assertIn("--merged-pr 160", summary["reconcile_actions"][0]["command"])

    def test_capture_health_counts_duplicate_learning(self):
        record = _record()
        record["capture"]["learning"] = {
            "decision": "duplicate",
            "reason": "duplicate-learning",
        }

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["counts"]["duplicate_learning"], 1)
        self.assertEqual(summary["status"], "clean")

    def test_capture_health_handles_malformed_capture_block_without_pr(self):
        record = _record()
        record["pull_request"] = {"number": None}
        record["capture"] = "not a capture block"

        summary = ledger.capture_health_summary([record])

        self.assertEqual(summary["counts"]["missing_marker"], 1)
        self.assertEqual(summary["counts"]["marker_only"], 0)
        self.assertEqual(summary["reconcile_actions"][0]["pr"], None)
        self.assertNotIn("--merged-pr", summary["reconcile_actions"][0]["command"])

    def test_capture_health_ignores_unmerged_ship_runs_and_non_ship_records(self):
        unmerged_record = _record()
        unmerged_record["assessment"]["merge"] = {"action": "hold", "reason": "window closed"}
        unmerged_record["capture"] = None

        non_dict_assessment = {
            "record_type": "ship_run",
            "assessment": "not-a-dict",
            "capture": {"status": "applied", "marker": "compound-learning: pr=1 status=applied"},
        }
        non_dict_merge = {
            "record_type": "ship_run",
            "assessment": {"merge": "not-a-dict"},
            "capture": {"status": "applied", "marker": "compound-learning: pr=2 status=applied"},
        }

        summary = ledger.capture_health_summary([
            unmerged_record,
            "not-a-dict",
            {"record_type": "other"},
            non_dict_assessment,
            non_dict_merge,
        ])

        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["status"], "clean")
        self.assertEqual(summary["counts"]["applied"], 2)
        self.assertEqual(summary["counts"]["missing_marker"], 0)

    def test_sanitize_record_redacts_default_secret_patterns(self):
        record = _record()
        record["capture"]["reason"] = (
            "Bearer abcdefghijklmnopqrstuvwxyz and "
            "https://user:supersecret@example.test/repo"
        )

        sanitized = ledger.sanitize_record(record, _config())

        serialized = json.dumps(sanitized, sort_keys=True)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertNotIn("supersecret", serialized)
        self.assertIn("[REDACTED:bearer-token]", serialized)
        self.assertIn("[REDACTED:credentials]", serialized)
        self.assertEqual(sanitized["redaction"]["status"], "applied")
        self.assertEqual(sanitized["redaction"]["redaction_count"], 2)

    def test_sanitize_record_uses_project_deny_patterns(self):
        record = _record()
        record["capture"]["reason"] = "Private host: internal.example.test"
        config = _config(deny_patterns=[
            {"id": "private-host", "pattern": r"internal\.example\.test"}
        ])

        sanitized = ledger.sanitize_record(record, config)

        self.assertNotIn("internal.example.test", json.dumps(sanitized, sort_keys=True))
        self.assertIn("[REDACTED:private-host]", sanitized["capture"]["reason"])
        self.assertEqual(sanitized["redaction"]["rules"][0]["id"], "private-host")

    def test_sanitize_record_uses_project_custom_replacement(self):
        record = _record()
        record["capture"]["reason"] = "Ticket: https://tickets.example.test/ABC-123"
        config = _config(deny_patterns=[
            {
                "id": "ticket-url",
                "pattern": r"https://tickets\.example\.test/[A-Z]+-[0-9]+",
                "replacement": "[REDACTED:ticket-url]",
            }
        ])

        sanitized = ledger.sanitize_record(record, config)

        self.assertEqual(sanitized["capture"]["reason"], "Ticket: [REDACTED:ticket-url]")
        self.assertEqual(sanitized["redaction"]["rules"][0]["id"], "ticket-url")

    def test_project_replacement_is_literal_and_cannot_reinsert_secret(self):
        record = _record()
        record["capture"]["reason"] = "captured top-secret-value"
        config = _config(deny_patterns=[
            {"id": "bad-replacement", "pattern": r"top-secret-value", "replacement": r"\g<0>"}
        ])

        sanitized = ledger.sanitize_record(record, config)

        self.assertNotIn("top-secret-value", json.dumps(sanitized, sort_keys=True))
        self.assertEqual(sanitized["capture"]["reason"], r"captured \g<0>")

    def test_redaction_policy_without_config_uses_defaults(self):
        policy = redaction.policy_from_config()

        result = redaction.sanitize("ghp_" + "a" * 24, policy)

        self.assertEqual(result.value, "[REDACTED:github-token]")
        self.assertEqual(result.audit["redaction_count"], 1)

    def test_malformed_project_redaction_entries_are_ignored_until_valid_regex_compiles(self):
        record = _record()
        record["capture"]["reason"] = "x marks the value"
        config = _config(deny_patterns=[
            "not-a-rule",
            {"id": "missing-pattern"},
            {"pattern": "x"},
        ])

        contract = ledger.ledger_contract_as_dict(config)["capture_redaction"]
        sanitized = ledger.sanitize_record(record, config)

        self.assertEqual(contract["configured_rule_ids"],
                         ["missing-pattern", "deny-pattern-3"])
        self.assertEqual(
            sanitized["capture"]["reason"],
            "[REDACTED:deny-pattern-3] marks the value",
        )

    def test_non_list_project_redaction_policy_is_ignored(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.7",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true"),
            policy_pack={"name": "test", "capture_redaction": {"deny_patterns": "not-a-list"}},
        )

        contract = ledger.ledger_contract_as_dict(config)["capture_redaction"]
        sanitized = ledger.sanitize_record(_record(), config)

        self.assertEqual(contract["configured_rule_count"], 0)
        self.assertEqual(sanitized["redaction"]["redaction_count"], 0)

    def test_sanitize_record_leaves_safe_text_unchanged(self):
        record = _record()
        record["capture"]["reason"] = "Capture skipped because no hook is configured."

        sanitized = ledger.sanitize_record(record, _config())

        self.assertEqual(sanitized["capture"]["reason"], record["capture"]["reason"])
        self.assertEqual(sanitized["redaction"]["redaction_count"], 0)

    def test_encode_parse_append_and_read_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".keel" / "state" / "run-ledger.jsonl"
            self.assertEqual(ledger.read_records(path), [])

            record = _record()
            record = ledger.sanitize_record(record, _config())
            ledger.append_record(path, record)
            ledger.append_record(path, record)

            self.assertEqual(ledger.parse_records("\n" + ledger.encode_record(record)),
                             [record])
            self.assertEqual(ledger.read_records(path), [record, record])

    def test_parse_rejects_invalid_json_and_schema(self):
        with self.assertRaisesRegex(ledger.LedgerError, "invalid JSON"):
            ledger.parse_records("{")

        bad_schema = dict(_record(), schema_version="other")
        with self.assertRaisesRegex(ledger.LedgerError, "unsupported schema_version"):
            ledger.encode_record(bad_schema)

        bad_type = dict(_record(), record_type="other")
        with self.assertRaisesRegex(ledger.LedgerError, "unsupported record_type"):
            ledger.parse_records(ledger.encode_record(_record()) + "\n" + json.dumps(bad_type))

        with self.assertRaisesRegex(ledger.LedgerError, "record must be an object"):
            ledger.parse_records("[]")


class TestLatestShipRunForPr(unittest.TestCase):
    def _record_for(self, pr_number, run_id):
        record = dict(_record())
        record["pull_request"] = {"number": pr_number}
        record["run_id"] = run_id
        return record

    def test_returns_latest_match_for_pr(self):
        records = [
            self._record_for(160, "RUN-1"),
            self._record_for(999, "RUN-other"),
            self._record_for(160, "RUN-2"),
        ]
        match = ledger.latest_ship_run_for_pr(records, 160)
        self.assertEqual(match["run_id"], "RUN-2")

    def test_returns_none_when_no_match(self):
        records = [self._record_for(160, "RUN-1")]
        self.assertIsNone(ledger.latest_ship_run_for_pr(records, 161))

    def test_ignores_non_ship_run_and_malformed_pull_request(self):
        non_ship = dict(self._record_for(160, "RUN-x"), record_type="capture_run")
        malformed = dict(self._record_for(160, "RUN-y"))
        malformed["pull_request"] = "nope"
        records = [non_ship, malformed]
        self.assertIsNone(ledger.latest_ship_run_for_pr(records, 160))


def _gates_record(*, pr, head_sha, run_id="RUN-1", blocked=False, gates=None):
    return {
        "schema_version": ledger.LEDGER_SCHEMA_VERSION,
        "record_type": ledger.RECORD_TYPE_SHIP_RUN,
        "run_id": run_id,
        "pull_request": {"number": pr},
        "git": {"head_sha": head_sha},
        "verdict": {"blocked": blocked},
        "gates": gates if gates is not None
        else [{"gate": "build", "ok": True, "skipped": False, "error": None}],
    }


class TestRecordGatesPassed(unittest.TestCase):
    def test_clean_run_with_ok_and_skipped_gates_passes(self):
        record = _gates_record(pr=1, head_sha="a", gates=[
            {"gate": "build", "ok": True, "skipped": False, "error": None},
            {"gate": "docs", "ok": False, "skipped": True, "error": None},
        ])
        self.assertTrue(ledger.record_gates_passed(record))

    def test_blocked_verdict_is_not_a_pass(self):
        self.assertFalse(ledger.record_gates_passed(_gates_record(pr=1, head_sha="a",
                                                                  blocked=True)))

    def test_missing_or_malformed_verdict_is_not_a_pass(self):
        no_verdict = _gates_record(pr=1, head_sha="a")
        del no_verdict["verdict"]
        self.assertFalse(ledger.record_gates_passed(no_verdict))

    def test_empty_or_missing_gates_is_not_a_pass(self):
        self.assertFalse(ledger.record_gates_passed(_gates_record(pr=1, head_sha="a", gates=[])))
        no_gates = _gates_record(pr=1, head_sha="a")
        no_gates["gates"] = "nope"
        self.assertFalse(ledger.record_gates_passed(no_gates))

    def test_a_blocking_gate_that_never_ran_is_not_a_pass(self):
        # `ok=True, not_run=True` is what a command-only runner reports for an agentic
        # gate it does not execute. "Nobody ran it" must never certify as "it passed",
        # or a required review gate authorizes the merge without a reviewer (#626).
        never_ran = _gates_record(pr=1, head_sha="a", gates=[
            {"gate": "build", "ok": True, "skipped": False, "error": None},
            {"gate": "review", "ok": True, "skipped": False, "error": None,
             "not_run": True, "on_fail": "block"},
        ])
        self.assertFalse(ledger.record_gates_passed(never_ran))

    def test_a_non_blocking_gate_that_never_ran_still_passes(self):
        # A warn/suggest gate is advisory by declaration; not running it withholds
        # advice, it does not withhold a merge authorization.
        advisory = _gates_record(pr=1, head_sha="a", gates=[
            {"gate": "style", "ok": True, "skipped": False, "error": None,
             "not_run": True, "on_fail": "warn"},
        ])
        self.assertTrue(ledger.record_gates_passed(advisory))

    def test_an_unrun_gate_with_no_recognised_severity_is_not_a_pass(self):
        # The strict read-time default exists for exactly these records: a producer that
        # learned `not_run` without its sibling key, or round-tripped the value through
        # JSON. A missing key, a null, and a severity keel does not know all mean "we
        # cannot tell this gate was optional" — and this is the certification path.
        for on_fail in ({}, {"on_fail": None}, {"on_fail": "bogus"}, {"on_fail": ""}):
            with self.subTest(on_fail=on_fail):
                record = _gates_record(pr=1, head_sha="a", gates=[
                    {"gate": "review", "ok": True, "skipped": False, "error": None,
                     "not_run": True, **on_fail},
                ])
                self.assertFalse(ledger.record_gates_passed(record))

    def test_a_record_predating_the_not_run_field_still_passes(self):
        # Older ledger lines carry neither key; absence means "ran", as it always did.
        legacy = _gates_record(pr=1, head_sha="a", gates=[
            {"gate": "build", "ok": True, "skipped": False, "error": None},
        ])
        self.assertTrue(ledger.record_gates_passed(legacy))

    def test_gate_with_error_or_not_ok_is_not_a_pass(self):
        errored = _gates_record(pr=1, head_sha="a", gates=[
            {"gate": "build", "ok": True, "skipped": False, "error": "boom"},
        ])
        self.assertFalse(ledger.record_gates_passed(errored))
        failed = _gates_record(pr=1, head_sha="a", gates=[
            {"gate": "build", "ok": False, "skipped": False, "error": None},
        ])
        self.assertFalse(ledger.record_gates_passed(failed))
        malformed = _gates_record(pr=1, head_sha="a", gates=["nope"])
        self.assertFalse(ledger.record_gates_passed(malformed))


def _marker_record(*, pr, run_id="RUN-1", marker="keel-capture:1"):
    record = _gates_record(pr=pr, head_sha="a", run_id=run_id)
    if marker is not None:
        record["capture"] = {"marker": marker, "status": "applied"}
    return record


class TestExistingCaptureMarker(unittest.TestCase):
    """One capture marker per merged PR, enforced at *write* time.

    It was only ever detected afterwards: `capture.verify_session` refuses the whole
    session on a second marker and `capture-reconcile` returns `blocked` with nothing to
    offer, so the natural retry after a crash mid-s11 was the very action that bricked
    the run.
    """

    def test_a_second_marker_for_the_same_pr_is_a_duplicate(self):
        existing = _marker_record(pr=7, run_id="RUN-1")
        clash = ledger.existing_capture_marker([existing], _marker_record(pr=7, run_id="RUN-2"))
        self.assertIsNotNone(clash)
        self.assertEqual(clash["run_id"], "RUN-1")

    def test_a_different_pr_is_not_a_duplicate(self):
        existing = _marker_record(pr=7)
        self.assertIsNone(ledger.existing_capture_marker([existing], _marker_record(pr=8)))

    def test_a_record_carrying_no_marker_never_clashes(self):
        existing = _marker_record(pr=7)
        self.assertIsNone(
            ledger.existing_capture_marker([existing], _marker_record(pr=7, marker=None)))
        blank = _marker_record(pr=7, marker="   ")
        self.assertIsNone(ledger.existing_capture_marker([existing], blank))

    def test_a_record_without_a_pr_number_never_clashes(self):
        candidate = _marker_record(pr=7)
        candidate["pull_request"] = "nope"
        self.assertIsNone(ledger.existing_capture_marker([_marker_record(pr=7)], candidate))

    def test_markerless_and_non_ship_records_are_skipped(self):
        markerless = _gates_record(pr=7, head_sha="a")          # no capture block at all
        malformed_pr = _marker_record(pr=7)
        malformed_pr["pull_request"] = "nope"
        non_ship = dict(_marker_record(pr=7), record_type="capture_run")
        self.assertIsNone(ledger.existing_capture_marker(
            [markerless, malformed_pr, non_ship], _marker_record(pr=7, run_id="RUN-2")))


class TestGatesPassForHead(unittest.TestCase):
    def test_matching_head_with_passing_gates_matches(self):
        records = [_gates_record(pr=42, head_sha="head-new", run_id="RUN-9")]
        matched, record = ledger.gates_pass_for_head(records, 42, "head-new")
        self.assertTrue(matched)
        self.assertEqual(record["run_id"], "RUN-9")

    def test_returns_latest_matching_record(self):
        records = [
            _gates_record(pr=42, head_sha="head-new", run_id="RUN-1"),
            _gates_record(pr=42, head_sha="head-new", run_id="RUN-2"),
        ]
        matched, record = ledger.gates_pass_for_head(records, 42, "head-new")
        self.assertTrue(matched)
        self.assertEqual(record["run_id"], "RUN-2")

    def test_stale_head_does_not_match(self):
        records = [_gates_record(pr=42, head_sha="head-old")]
        matched, record = ledger.gates_pass_for_head(records, 42, "head-new")
        self.assertFalse(matched)
        self.assertIsNone(record)

    def test_blank_head_never_matches(self):
        records = [_gates_record(pr=42, head_sha="head-new")]
        self.assertEqual(ledger.gates_pass_for_head(records, 42, ""), (False, None))
        self.assertEqual(ledger.gates_pass_for_head(records, 42, None), (False, None))

    def test_other_pr_and_non_ship_run_are_ignored(self):
        other_pr = _gates_record(pr=99, head_sha="head-new")
        non_ship = dict(_gates_record(pr=42, head_sha="head-new"),
                        record_type="capture_run")
        malformed_git = _gates_record(pr=42, head_sha="head-new")
        malformed_git["git"] = "nope"
        bad_pr = _gates_record(pr=42, head_sha="head-new")
        bad_pr["pull_request"] = "nope"
        records = [other_pr, non_ship, malformed_git, bad_pr]
        self.assertEqual(ledger.gates_pass_for_head(records, 42, "head-new"), (False, None))

    def test_matching_head_but_failing_gates_does_not_match(self):
        records = [_gates_record(pr=42, head_sha="head-new", blocked=True)]
        matched, record = ledger.gates_pass_for_head(records, 42, "head-new")
        self.assertFalse(matched)
        self.assertIsNone(record)

    def test_a_later_red_run_supersedes_an_earlier_green_one(self):
        # Re-gating the same head is ordinary (flaky suite settles, fix-loop re-runs).
        # Latest-wins: scanning for *any* green would let the superseded pass authorize
        # the merge and the later red never be consulted.
        records = [
            _gates_record(pr=42, head_sha="head-new", run_id="RUN-1"),
            _gates_record(pr=42, head_sha="head-new", run_id="RUN-2", blocked=True),
        ]
        self.assertEqual(ledger.gates_pass_for_head(records, 42, "head-new"), (False, None))

    def test_a_later_green_run_clears_an_earlier_red_one(self):
        records = [
            _gates_record(pr=42, head_sha="head-new", run_id="RUN-1", blocked=True),
            _gates_record(pr=42, head_sha="head-new", run_id="RUN-2"),
        ]
        matched, record = ledger.gates_pass_for_head(records, 42, "head-new")
        self.assertTrue(matched)
        self.assertEqual(record["run_id"], "RUN-2")

    def test_a_red_run_on_another_head_does_not_supersede(self):
        # Only records for the *current* head are consulted; a red run against a
        # superseded commit is irrelevant, not a veto.
        records = [
            _gates_record(pr=42, head_sha="head-new", run_id="RUN-1"),
            _gates_record(pr=42, head_sha="head-old", run_id="RUN-2", blocked=True),
        ]
        matched, record = ledger.gates_pass_for_head(records, 42, "head-new")
        self.assertTrue(matched)
        self.assertEqual(record["run_id"], "RUN-1")
