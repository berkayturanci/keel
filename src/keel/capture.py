"""Consumer-neutral post-merge capture contract and verification helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import config as cfg

CAPTURE_SCHEMA_VERSION = "keel.capture.v1"
MARKER_PREFIX = "compound-learning"
STATUSES = ("applied", "deferred", "skipped")
SKIP_REASONS = (
    "dry-run",
    "deferred",
    "merge-failed",
    "recursion-guard",
    "capability-unavailable",
    "no-policy",
)

_MARKER_RE = re.compile(
    r"^compound-learning:\s+pr=(?P<pr>[1-9][0-9]*)\s+status="
    r"(?P<status>applied|deferred|skipped(?::[a-z0-9-]+)?)$"
)


class CaptureError(ValueError):
    """Raised when a capture marker or capture record is invalid."""


@dataclass(frozen=True)
class CaptureMarker:
    """One stable capture marker emitted after a merged PR."""

    pr_number: int
    status: str
    reason: str | None = None

    def as_text(self) -> str:
        return marker_text(
            pr_number=self.pr_number,
            status=self.status,
            reason=self.reason,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "prefix": MARKER_PREFIX,
            "pr": self.pr_number,
            "status": self.status,
            "reason": self.reason,
            "text": self.as_text(),
        }


def contract_as_dict(config: cfg.ProjectConfig | None = None) -> dict[str, Any]:
    """Return the stable capture contract consumed by adapters and verifiers."""
    capture_policy = _capture_policy(config)
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "marker": {
            "prefix": MARKER_PREFIX,
            "format": "compound-learning: pr=<N> status=<applied|deferred|skipped:reason>",
            "statuses": list(STATUSES),
            "skip_reasons": list(SKIP_REASONS),
            "required_after_merged_pr": True,
        },
        "extension_slots": ["capture", "post-merge"],
        "policy_source": "policy_pack.capture + capture/post-merge extensions",
        "policy_enabled": bool(capture_policy.get("enabled", False)),
        "policy_mode": capture_policy.get("mode", "extension"),
        "recursion_guard": {
            "enabled": True,
            "reason": "recursion-guard",
            "never_capture_capture_work": True,
        },
        "fail_soft": {
            "enabled": True,
            "merge_revert_on_capture_failure": False,
            "failure_marker": "skipped:capability-unavailable",
        },
        "durable_artifacts": {
            "requires_redaction": True,
            "redaction_contract": "run_ledger.capture_redaction",
            "core_destination": "run-ledger",
            "project_destination": "extension-owned",
        },
        "session_end_verifier": {
            "primitive": "capture.verify_session",
            "cli": "keel capture-verify",
            "missing_marker_status": "missing",
            "invalid_marker_status": "invalid",
        },
    }


def marker_text(*, pr_number: int, status: str, reason: str | None = None) -> str:
    """Render one stable capture marker."""
    marker = build_marker(pr_number=pr_number, status=status, reason=reason)
    suffix = marker.status if marker.reason is None else f"{marker.status}:{marker.reason}"
    return f"{MARKER_PREFIX}: pr={marker.pr_number} status={suffix}"


def build_marker(*, pr_number: int, status: str, reason: str | None = None) -> CaptureMarker:
    """Validate and build a capture marker."""
    if pr_number <= 0:
        raise CaptureError("capture marker requires a positive PR number")
    status, reason = normalize_status(status, reason)
    return CaptureMarker(pr_number=pr_number, status=status, reason=reason)


def normalize_status(status: str | None, reason: str | None = None) -> tuple[str, str | None]:
    """Normalize ``skipped:<reason>`` into a structured status and reason."""
    if not status:
        raise CaptureError("capture status is required")
    raw = status.strip()
    if raw.startswith("skipped:"):
        raw, embedded_reason = raw.split(":", 1)
        reason = embedded_reason
    if raw not in STATUSES:
        raise CaptureError(f"unsupported capture status: {status}")
    clean_reason = reason.strip() if isinstance(reason, str) and reason.strip() else None
    if raw == "skipped":
        if clean_reason not in SKIP_REASONS:
            raise CaptureError("skipped capture requires an allowed skip reason")
    else:
        clean_reason = None
    return raw, clean_reason


def parse_marker(text: str) -> CaptureMarker:
    """Parse a stable marker string into structured data."""
    match = _MARKER_RE.match(text.strip())
    if not match:
        raise CaptureError("invalid capture marker")
    status_text = match.group("status")
    status, reason = normalize_status(status_text)
    return CaptureMarker(
        pr_number=int(match.group("pr")),
        status=status,
        reason=reason,
    )


def record_marker(
    *,
    pr_number: int | None,
    status: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the capture block stored in a ship run ledger record."""
    if status is None:
        return {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "status": None,
            "reason": reason,
            "marker_reason": None,
            "marker": None,
            "fail_soft": True,
        }
    marker_reason = _marker_reason(status, reason)
    if pr_number is None:
        clean_status, clean_marker_reason = normalize_status(status, marker_reason)
        return {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "status": clean_status,
            "reason": reason,
            "marker_reason": clean_marker_reason,
            "marker": None,
            "fail_soft": True,
        }
    marker = build_marker(pr_number=pr_number, status=status, reason=marker_reason)
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "status": marker.status,
        "reason": reason,
        "marker_reason": marker.reason,
        "marker": marker.as_text(),
        "fail_soft": True,
    }


def verify_session(
    records: list[dict[str, Any]],
    merged_prs: list[int] | tuple[int, ...],
) -> dict[str, Any]:
    """Verify that each merged PR has an applied/deferred/allowed-skip capture marker."""
    results = [_verify_pr(records, pr) for pr in merged_prs]
    missing = [item for item in results if item["status"] == "missing"]
    invalid = [item for item in results if item["status"] == "invalid"]
    status = "complete" if not missing and not invalid else "incomplete"
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "status": status,
        "expected_prs": list(merged_prs),
        "results": results,
        "summary": {
            "ok": sum(1 for item in results if item["ok"]),
            "missing": len(missing),
            "invalid": len(invalid),
        },
    }


def recursion_guard(
    *,
    title: str | None = None,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
) -> bool:
    """Return true when capture should skip to avoid capture-on-capture recursion."""
    title_hit = bool(title and "capture" in title.lower())
    label_hit = any(label.lower() == "capture" for label in labels)
    path_hit = any("/capture" in path.lower() or path.lower().endswith("capture.py")
                   for path in changed_files)
    return title_hit or label_hit or path_hit


def _verify_pr(records: list[dict[str, Any]], pr_number: int) -> dict[str, Any]:
    candidates = [
        record for record in records
        if record.get("record_type") == "ship_run"
        and (record.get("pull_request") or {}).get("number") == pr_number
    ]
    markers = [
        capture_block.get("marker")
        for record in candidates
        if isinstance(capture_block := record.get("capture"), dict)
        and capture_block.get("marker")
    ]
    if len(markers) > 1:
        return {
            "pr": pr_number,
            "ok": False,
            "status": "invalid",
            "reason": "multiple capture markers found for merged PR",
            "marker": markers[-1],
            "marker_count": len(markers),
        }
    for marker in markers:
        try:
            parsed = parse_marker(marker)
        except CaptureError as exc:
            return {
                "pr": pr_number,
                "ok": False,
                "status": "invalid",
                "reason": str(exc),
                "marker": marker,
            }
        if parsed.pr_number != pr_number:
            return {
                "pr": pr_number,
                "ok": False,
                "status": "invalid",
                "reason": "marker PR does not match ledger PR",
                "marker": marker,
            }
        return {
            "pr": pr_number,
            "ok": True,
            "status": parsed.status,
            "reason": parsed.reason,
            "marker": marker,
        }
    return {
        "pr": pr_number,
        "ok": False,
        "status": "missing",
        "reason": "no capture marker found for merged PR",
        "marker": None,
    }


def _capture_policy(config: cfg.ProjectConfig | None) -> dict[str, Any]:
    if config is None or not isinstance(config.policy_pack, dict):
        return {}
    policy = config.policy_pack.get("capture")
    return policy if isinstance(policy, dict) else {}


def _marker_reason(status: str, reason: str | None) -> str | None:
    raw = status.strip()
    if raw.startswith("skipped:"):
        return None
    if raw != "skipped":
        return None
    if reason in SKIP_REASONS:
        return reason
    return "no-policy"
