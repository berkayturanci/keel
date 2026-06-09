"""Structured run ledger helpers for keel workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import capture, redaction
from . import config as cfg

LEDGER_SCHEMA_VERSION = "keel.run-ledger.v1"
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
        "append_owner": ["ship", "ship-v2"],
        "readers": ["morning", "wrap", "overnight", "capture-verification", "ledger"],
        "consumer_neutral": True,
        "capture_redaction": redaction.contract_as_dict(config),
        "capture_contract": capture.contract_as_dict(config),
        "record_types": [RECORD_TYPE_SHIP_RUN],
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
    """Resolve the configured ledger path under ``root`` unless it is absolute."""
    raw, _ = configured_ledger_path(config)
    path = Path(raw)
    return path if path.is_absolute() else Path(root) / path


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
    implementer: str | None = None,
    reviewer_agents: list[str] | None = None,
    tester: str | None = None,
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
        "issue_intake": issue_intake,
        "capture": capture.record_marker(
            pr_number=pr_number,
            status=capture_status,
            reason=capture_reason,
        ),
    }


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
