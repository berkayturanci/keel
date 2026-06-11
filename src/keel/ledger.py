"""Structured run ledger helpers for keel workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import capture, redaction
from . import config as cfg

LEDGER_SCHEMA_VERSION = "keel.run-ledger.v1"
CAPTURE_HEALTH_SCHEMA_VERSION = "keel.capture-health.v1"
DEFAULT_LEDGER_PATH = ".keel/state/run-ledger.jsonl"
RECORD_TYPE_SHIP_RUN = "ship_run"


class LedgerError(ValueError):
    """Raised when a ledger file cannot be decoded as the stable schema."""


def ledger_contract_as_dict(config: cfg.ProjectConfig) -> dict[str, Any]:
    """Return the project-neutral ledger storage and schema contract."""
    path, source = configured_ledger_path(config)
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "format": "jsonl",
        "path": path,
        "path_source": source,
        "missing_handling": "treat-as-empty",
        "append_owner": ["ship"],
        "readers": ["morning", "wrap", "overnight", "capture-verification", "ledger"],
        "consumer_neutral": True,
        "capture_redaction": redaction.contract_as_dict(config),
        "capture_contract": capture.contract_as_dict(config),
        "capture_health": capture_health_contract_as_dict(),
        "record_types": [RECORD_TYPE_SHIP_RUN],
    }


def capture_health_contract_as_dict() -> dict[str, Any]:
    """Return the ledger-derived capture-health summary contract."""
    return {
        "schema_version": CAPTURE_HEALTH_SCHEMA_VERSION,
        "source": "run-ledger ship_run records",
        "readers": ["morning", "wrap", "status", "ledger"],
        "consumer_neutral": True,
        "missing_ledger_handling": "clean-empty-history",
        "dry_run": {
            "no_mutations": True,
            "safe_reconcile_actions_only": True,
        },
        "states": ["clean", "needs-reconcile"],
        "item_statuses": ["applied", "deferred", "skipped", "missing-marker"],
    }


def configured_ledger_path(config: cfg.ProjectConfig) -> tuple[str, str]:
    """Return the configured ledger path and the config source that supplied it."""
    pack = config.policy_pack or {}
    reports = pack.get("reports") if isinstance(pack.get("reports"), dict) else {}
    value = reports.get("run_ledger")
    if isinstance(value, str) and value.strip():
        return value, "policy_pack.reports.run_ledger"
    return DEFAULT_LEDGER_PATH, "default"


def resolve_path(root: str | Path, config: cfg.ProjectConfig) -> Path:
    """Resolve the configured ledger path under ``root`` and reject escapes."""
    raw, _ = configured_ledger_path(config)
    path = Path(raw)
    if path.is_absolute():
        raise LedgerError("run ledger path must be relative to the project root")
    root_path = Path(root).resolve()
    resolved = (root_path / path).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise LedgerError("run ledger path escapes the project root") from exc
    return resolved


def build_ship_run_record(
    *,
    command: str,
    base_branch: str,
    changed_files: list[str],
    outcomes: list[Any],
    verdict: Any,
    assessment: Any,
    issue_intake: dict[str, Any] | None = None,
    target: str | None = None,
    run_id: str | None = None,
    issue_number: int | None = None,
    pr_number: int | None = None,
    branch: str | None = None,
    head_sha: str | None = None,
    capture_status: str | None = None,
    capture_reason: str | None = None,
    issue_title: str | None = None,
    issue_labels: list[str] | tuple[str, ...] = (),
    existing_records: list[dict[str, Any]] | None = None,
    config: cfg.ProjectConfig | None = None,
    implementer: str | None = None,
    reviewer_agents: list[str] | None = None,
    tester: str | None = None,
    host_agent: str | None = None,
    transport: str | None = None,
    profile: str | None = None,
    jury_mode: str | None = None,
    consent_status: str | None = None,
    consent_scopes: list[str] | tuple[str, ...] | None = None,
    run_controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one deterministic consumer-neutral ship ledger record."""
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "record_type": RECORD_TYPE_SHIP_RUN,
        "command": command,
        "run_id": run_id,
        "target": target,
        "issue": {"number": issue_number},
        "pull_request": {"number": pr_number},
        "git": {
            "base_branch": base_branch,
            "branch": branch,
            "head_sha": head_sha,
        },
        "changes": {
            "file_count": len(changed_files),
            "files": list(changed_files),
        },
        "gates": [
            {
                "gate": outcome.gate,
                "ok": outcome.ok,
                "skipped": outcome.skipped,
                "error": outcome.error,
                "finding_count": len(outcome.findings),
            }
            for outcome in outcomes
        ],
        "verdict": {
            "blocked": verdict.blocked,
            "counts": dict(verdict.counts),
        },
        "assessment": {
            "tier": assessment.tier,
            "reviewers": assessment.reviewers,
            "window_open": assessment.window_open,
            "ci_ok": assessment.ci_ok,
            "merge": {
                "action": assessment.merge.action,
                "reason": assessment.merge.reason,
            },
            "halted": assessment.halted,
            "bypassed_window": assessment.bypassed_window,
        },
        "actors": {
            "implementer": implementer,
            "reviewers": list(reviewer_agents or ()),
            "tester": tester,
        },
        "run_context": _run_context(
            host_agent=host_agent,
            transport=transport,
            profile=profile,
            jury_mode=jury_mode,
            consent_status=consent_status,
            consent_scopes=consent_scopes,
        ),
        "run_controls": run_controls,
        "issue_intake": issue_intake,
        "capture": capture.record_marker(
            pr_number=pr_number,
            status=capture_status,
            reason=capture_reason,
            title=issue_title,
            labels=issue_labels,
            changed_files=changed_files,
            existing_records=existing_records or [],
            config=config,
        ),
    }


def _run_context(
    *,
    host_agent: str | None,
    transport: str | None,
    profile: str | None,
    jury_mode: str | None,
    consent_status: str | None,
    consent_scopes: list[str] | tuple[str, ...] | None,
) -> dict[str, Any]:
    """Build the deterministic consumer-neutral preflight run-context block.

    Every field is optional; a missing scalar degrades to ``None`` so the
    closure renderer can present ``unknown``/``none`` without a schema change.
    Consent is a small summary: a status and the approved mutation scopes,
    reusing the operator/approve-scope inputs already resolved by the caller.
    """
    scopes = [str(scope) for scope in (consent_scopes or ()) if str(scope).strip()]
    return {
        "host_agent": host_agent if _nonblank(host_agent) else None,
        "transport": transport if _nonblank(transport) else None,
        "profile": profile if _nonblank(profile) else None,
        "jury_mode": jury_mode if _nonblank(jury_mode) else None,
        "consent": {
            "status": consent_status if _nonblank(consent_status) else None,
            "scopes": scopes,
        },
    }


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def encode_record(record: dict[str, Any]) -> str:
    """Encode one ledger record as stable JSONL."""
    _validate_record(record)
    return json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"


def parse_records(text: str) -> list[dict[str, Any]]:
    """Parse ledger JSONL text into validated records."""
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"line {line_number}: invalid JSON") from exc
        _validate_record(record, line_number=line_number)
        records.append(record)
    return records


def read_records(path: str | Path) -> list[dict[str, Any]]:
    """Read a ledger file; a missing ledger is a valid empty history."""
    ledger_path = Path(path)
    if not ledger_path.exists():
        return []
    return parse_records(ledger_path.read_text(encoding="utf-8"))


def capture_health_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize capture visibility for morning, wrap, status, and ledger readers."""
    items = [_capture_health_item(record) for record in records]
    counts = {
        "applied": 0,
        "marker_only": 0,
        "create_learning": 0,
        "duplicate_learning": 0,
        "deferred": 0,
        "skipped": 0,
        "missing_marker": 0,
        "needs_reconcile": 0,
    }
    skipped_by_reason: dict[str, int] = {}
    for item in items:
        status = item["status"]
        learning = item["learning_decision"]
        if status == "applied":
            counts["applied"] += 1
        elif status == "deferred":
            counts["deferred"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
            skipped_by_reason[item["reason"] or "unspecified"] = (
                skipped_by_reason.get(item["reason"] or "unspecified", 0) + 1
            )
        else:
            counts["missing_marker"] += 1
        if learning == "marker-only":
            counts["marker_only"] += 1
        elif learning == "create-learning":
            counts["create_learning"] += 1
        elif learning == "duplicate":
            counts["duplicate_learning"] += 1
        if item["needs_reconcile"]:
            counts["needs_reconcile"] += 1
    return {
        "schema_version": CAPTURE_HEALTH_SCHEMA_VERSION,
        "status": "needs-reconcile" if counts["needs_reconcile"] else "clean",
        "record_count": len(records),
        "counts": counts,
        "skipped_by_reason": dict(sorted(skipped_by_reason.items())),
        "items": items,
        "reconcile_actions": [
            action for item in items for action in item["reconcile_actions"]
        ],
        "dry_run": {
            "no_mutations": True,
            "description": "Morning and wrap surface these actions; they do not mutate "
            "ledger, GitHub, or capture destinations.",
        },
    }


def append_record(path: str | Path, record: dict[str, Any]) -> None:
    """Append one validated JSONL record, creating parent directories as needed."""
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(encode_record(record))


def sanitize_record(
    record: dict[str, Any],
    config: cfg.ProjectConfig | None = None,
) -> dict[str, Any]:
    """Apply capture redaction before a ledger record becomes durable."""
    result = redaction.sanitize(record, redaction.policy_from_config(config))
    sanitized = dict(result.value)
    sanitized["redaction"] = result.audit
    return sanitized


def _validate_record(record: Any, *, line_number: int | None = None) -> None:
    prefix = f"line {line_number}: " if line_number is not None else ""
    if not isinstance(record, dict):
        raise LedgerError(f"{prefix}record must be an object")
    if record.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise LedgerError(f"{prefix}unsupported schema_version")
    if record.get("record_type") != RECORD_TYPE_SHIP_RUN:
        raise LedgerError(f"{prefix}unsupported record_type")


def _capture_health_item(record: dict[str, Any]) -> dict[str, Any]:
    block = record.get("capture") if isinstance(record.get("capture"), dict) else {}
    status = block.get("status")
    marker = block.get("marker")
    reason = block.get("marker_reason") or block.get("reason")
    learning = block.get("learning") if isinstance(block.get("learning"), dict) else {}
    item_status = _capture_health_status(status, marker)
    needs_reconcile = item_status in {"missing-marker", "deferred"}
    pr_number = (record.get("pull_request") or {}).get("number")
    item = {
        "run_id": record.get("run_id"),
        "issue": (record.get("issue") or {}).get("number"),
        "pull_request": pr_number,
        "status": item_status,
        "capture_status": status,
        "marker": marker if isinstance(marker, str) and marker.strip() else None,
        "reason": reason if isinstance(reason, str) and reason.strip() else None,
        "learning_decision": learning.get("decision"),
        "learning_reason": learning.get("reason"),
        "needs_reconcile": needs_reconcile,
        "reconcile_actions": [],
    }
    if needs_reconcile:
        item["reconcile_actions"].append(_capture_reconcile_action(pr_number, item_status))
    return item


def _capture_health_status(status: Any, marker: Any) -> str:
    if not isinstance(marker, str) or not marker.strip():
        return "missing-marker"
    if status == "deferred":
        return "deferred"
    if status == "skipped":
        return "skipped"
    return "applied"


def _capture_reconcile_action(pr_number: Any, status: str) -> dict[str, Any]:
    return {
        "type": "capture-reconcile",
        "reason": status,
        "pr": pr_number if isinstance(pr_number, int) else None,
        "command": (
            f"keel capture-reconcile .keel/project.yaml --root . --merged-pr {pr_number}"
            if isinstance(pr_number, int)
            else "keel capture-reconcile .keel/project.yaml --root ."
        ),
        "dry_run": True,
    }
