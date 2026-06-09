"""Consumer-neutral post-merge capture contract and verification helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import config as cfg

CAPTURE_SCHEMA_VERSION = "keel.capture.v1"
RECONCILE_SCHEMA_VERSION = "keel.capture-reconcile.v1"
LEARNING_DECISION_SCHEMA_VERSION = "keel.capture-learning.v1"
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
LEARNING_DECISIONS = ("create-learning", "marker-only", "defer", "duplicate")

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
        "learning_quality": learning_quality_contract_as_dict(config),
        "session_end_verifier": {
            "primitive": "capture.verify_session",
            "cli": "keel capture-verify",
            "missing_marker_status": "missing",
            "invalid_marker_status": "invalid",
        },
        "reconcile": {
            "schema_version": RECONCILE_SCHEMA_VERSION,
            "primitive": "capture.reconcile_session",
            "cli": "keel capture-reconcile",
            "idempotent": True,
            "never_reopens_implementation": True,
            "never_pushes_code": True,
            "never_merges_prs": True,
            "actions": [
                "emit-capture-marker",
                "run-capture-extension",
                "post-closure-summary",
                "close-linked-issue",
                "record-skip",
            ],
        },
    }


def learning_quality_contract_as_dict(config: cfg.ProjectConfig | None = None) -> dict[str, Any]:
    """Return the consumer-neutral durable-learning quality contract."""
    policy = _learning_policy(config)
    dedupe = policy.get("dedupe") if isinstance(policy.get("dedupe"), dict) else {}
    return {
        "schema_version": LEARNING_DECISION_SCHEMA_VERSION,
        "decisions": list(LEARNING_DECISIONS),
        "policy_source": "policy_pack.capture.learning",
        "policy_enabled": bool(policy.get("enabled", False)),
        "policy_mode": policy.get("mode", "policy-unavailable"),
        "default_decision": "marker-only",
        "default_reason": "policy-unavailable",
        "marker_required_for_every_merge": True,
        "durable_learning_optional": True,
        "dedupe": {
            "enabled": bool(dedupe.get("enabled", True)),
            "fingerprint": "sha256(normalized title + labels + changed files)",
            "matching": "stable fingerprint plus configured matching rules",
        },
        "ledger_field": "capture.learning",
        "closure_summary_field": "Capture",
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
    title: str | None = None,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
    existing_records: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    config: cfg.ProjectConfig | None = None,
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
            "learning": learning_decision(
                title=title,
                labels=labels,
                changed_files=changed_files,
                capture_status=None,
                capture_reason=reason,
                existing_records=existing_records,
                config=config,
            ),
        }
    learning = learning_decision(
        title=title,
        labels=labels,
        changed_files=changed_files,
        capture_status=status,
        capture_reason=reason,
        existing_records=existing_records,
        config=config,
    )
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
            "learning": learning,
        }
    marker = build_marker(pr_number=pr_number, status=status, reason=marker_reason)
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "status": marker.status,
        "reason": reason,
        "marker_reason": marker.reason,
        "marker": marker.as_text(),
        "fail_soft": True,
        "learning": learning,
    }


def learning_decision(
    *,
    title: str | None = None,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
    capture_status: str | None = None,
    capture_reason: str | None = None,
    existing_records: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    config: cfg.ProjectConfig | None = None,
) -> dict[str, Any]:
    """Classify whether a merged PR deserves a durable learning artifact.

    The marker is mandatory and independent from this decision. Durable learning is
    optional, policy-driven, and deduped by a stable fingerprint so routine merges can stay
    marker-only without losing auditability.
    """
    policy = _learning_policy(config)
    fingerprint = learning_fingerprint(
        title=title,
        labels=labels,
        changed_files=changed_files,
    )
    if _learning_dedupe_enabled(policy):
        duplicate_of = _duplicate_learning_fingerprint(fingerprint, existing_records)
        if duplicate_of is not None:
            return _learning_result(
                "duplicate",
                reason="duplicate-learning",
                fingerprint=fingerprint,
                duplicate_of=duplicate_of,
                policy=policy,
            )
    if not policy.get("enabled"):
        return _learning_result(
            "marker-only",
            reason="policy-unavailable",
            fingerprint=fingerprint,
            policy=policy,
        )
    mode = policy.get("mode", "marker-only")
    if mode == "create-learning":
        if capture_status and capture_status.startswith("skipped"):
            return _learning_result(
                "marker-only",
                reason="capture-skipped",
                fingerprint=fingerprint,
                policy=policy,
            )
        return _learning_result(
            "create-learning",
            reason=_policy_reason(policy, "policy-requested-learning"),
            fingerprint=fingerprint,
            policy=policy,
        )
    if mode == "defer":
        return _learning_result(
            "defer",
            reason=_policy_reason(policy, capture_reason or "policy-deferred"),
            fingerprint=fingerprint,
            policy=policy,
        )
    return _learning_result(
        "marker-only",
        reason=_policy_reason(policy, "marker-only-policy"),
        fingerprint=fingerprint,
        policy=policy,
    )


def learning_fingerprint(
    *,
    title: str | None = None,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
) -> str:
    """Return a stable, consumer-neutral dedupe fingerprint for learning candidates."""
    import hashlib
    import json

    payload = {
        "title": _normalize_text(title),
        "labels": sorted(_normalize_text(label) for label in _strings(labels)),
        "changed_files": sorted(_normalize_path(path) for path in _strings(changed_files)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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


def reconcile_session(
    records: list[dict[str, Any]],
    merged_prs: list[int | dict[str, Any]] | tuple[int | dict[str, Any], ...],
    *,
    config: cfg.ProjectConfig | None = None,
    capture_capability_available: bool = False,
) -> dict[str, Any]:
    """Plan idempotent post-merge reconciliation actions for capture gaps.

    The returned plan is pure data. It never writes ledger records, comments, issues, git
    state, or PR state; adapters may apply the listed actions after their own transport and
    consent checks. This keeps reconcile recovery deterministic and safe to run repeatedly.
    """
    items = [_merged_pr_info(item) for item in merged_prs]
    results = [
        _reconcile_pr(
            records,
            item,
            config=config,
            capture_capability_available=capture_capability_available,
        )
        for item in items
    ]
    actionable = [item for item in results if item["actions"]]
    blocked = [item for item in results if item["status"] in {"invalid", "ambiguous"}]
    complete = [item for item in results if item["status"] == "complete"]
    status = "blocked" if blocked else "actionable" if actionable else "complete"
    return {
        "schema_version": RECONCILE_SCHEMA_VERSION,
        "status": status,
        "dry_run_safe": True,
        "idempotent": True,
        "no_code_mutations": True,
        "expected_prs": [item["number"] for item in items],
        "results": results,
        "summary": {
            "complete": len(complete),
            "actionable": len(actionable),
            "blocked": len(blocked),
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


def _merged_pr_info(item: int | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, int):
        return {
            "number": item,
            "title": None,
            "labels": [],
            "changed_files": [],
            "issue_numbers": [],
        }
    number = item.get("number")
    if not isinstance(number, int) or number <= 0:
        raise CaptureError("merged PR entry requires a positive number")
    return {
        "number": number,
        "title": item.get("title") if isinstance(item.get("title"), str) else None,
        "labels": _strings(item.get("labels")),
        "changed_files": _strings(item.get("changed_files")),
        "issue_numbers": _positive_ints(item.get("issue_numbers")),
    }


def _reconcile_pr(
    records: list[dict[str, Any]],
    item: dict[str, Any],
    *,
    config: cfg.ProjectConfig | None,
    capture_capability_available: bool,
) -> dict[str, Any]:
    pr_number = item["number"]
    verification = _verify_pr(records, pr_number)
    issue_numbers = _linked_issue_numbers(records, item)
    if len(issue_numbers) > 1:
        return _reconcile_result(
            pr_number,
            status="ambiguous",
            reason="multiple linked issues found for merged PR",
            verification=verification,
            issue_numbers=issue_numbers,
            blocked=True,
        )
    if verification["ok"]:
        if len(issue_numbers) == 1:
            return _reconcile_result(
                pr_number,
                status="actionable",
                reason="capture marker already present; linked issue closeout can be reconciled",
                verification=verification,
                issue_numbers=issue_numbers,
                marker=verification["marker"],
                actions=[
                    _action("close-linked-issue", pr_number=pr_number,
                            issue_number=issue_numbers[0]),
                ],
            )
        return _reconcile_result(
            pr_number,
            status="complete",
            reason="capture marker already present",
            verification=verification,
        )
    if verification["status"] == "invalid":
        return _reconcile_result(
            pr_number,
            status="invalid",
            reason=verification["reason"],
            verification=verification,
            issue_numbers=issue_numbers,
            blocked=True,
        )
    marker_status, marker_reason, reason = _reconcile_marker_decision(
        item,
        config=config,
        capture_capability_available=capture_capability_available,
    )
    marker = marker_text(
        pr_number=pr_number,
        status=marker_status,
        reason=marker_reason,
    )
    actions = [
        _action(
            "emit-capture-marker",
            pr_number=pr_number,
            marker=marker,
            status=marker_status,
            reason=marker_reason,
        ),
        _action("post-closure-summary", pr_number=pr_number),
    ]
    if marker_status == "deferred":
        actions.insert(0, _action("run-capture-extension", pr_number=pr_number))
    if marker_status == "skipped":
        actions.append(_action("record-skip", pr_number=pr_number, reason=marker_reason))
    if len(issue_numbers) == 1:
        actions.append(_action("close-linked-issue", pr_number=pr_number,
                               issue_number=issue_numbers[0]))
    return _reconcile_result(
        pr_number,
        status="actionable",
        reason=reason,
        verification=verification,
        issue_numbers=issue_numbers,
        marker=marker,
        actions=actions,
    )


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


def _reconcile_result(
    pr_number: int,
    *,
    status: str,
    reason: str,
    verification: dict[str, Any],
    issue_numbers: list[int] | None = None,
    marker: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    blocked: bool = False,
) -> dict[str, Any]:
    return {
        "pr": pr_number,
        "status": status,
        "reason": reason,
        "verification_status": verification["status"],
        "blocked": blocked,
        "issue_numbers": list(issue_numbers or ()),
        "marker": marker,
        "actions": list(actions or ()),
    }


def _reconcile_marker_decision(
    item: dict[str, Any],
    *,
    config: cfg.ProjectConfig | None,
    capture_capability_available: bool,
) -> tuple[str, str | None, str]:
    if recursion_guard(
        title=item["title"],
        labels=item["labels"],
        changed_files=item["changed_files"],
    ):
        return "skipped", "recursion-guard", "capture recursion guard matched"
    policy = _capture_policy(config)
    if policy.get("enabled") and policy.get("mode", "extension") == "marker-only":
        return "applied", None, "marker-only capture policy configured"
    if policy.get("enabled") and policy.get("mode", "extension") == "extension":
        if capture_capability_available:
            return "deferred", None, "capture extension can be rerun"
        return "skipped", "capability-unavailable", "capture extension capability unavailable"
    return "skipped", "no-policy", "no capture policy configured"


def _linked_issue_numbers(records: list[dict[str, Any]], item: dict[str, Any]) -> list[int]:
    numbers = set(item["issue_numbers"])
    pr_number = item["number"]
    for record in records:
        if record.get("record_type") != "ship_run":
            continue
        if (record.get("pull_request") or {}).get("number") != pr_number:
            continue
        issue_number = (record.get("issue") or {}).get("number")
        if isinstance(issue_number, int) and issue_number > 0:
            numbers.add(issue_number)
    return sorted(numbers)


def _action(
    action_type: str,
    *,
    pr_number: int,
    marker: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    issue_number: int | None = None,
) -> dict[str, Any]:
    action = {
        "type": action_type,
        "pr": pr_number,
        "idempotency_key": f"{action_type}:pr-{pr_number}",
    }
    if marker is not None:
        action["marker"] = marker
    if status is not None:
        action["status"] = status
    if reason is not None:
        action["reason"] = reason
    if issue_number is not None:
        action["issue"] = issue_number
        action["idempotency_key"] = f"{action_type}:issue-{issue_number}:pr-{pr_number}"
    return action


def _capture_policy(config: cfg.ProjectConfig | None) -> dict[str, Any]:
    if config is None or not isinstance(config.policy_pack, dict):
        return {}
    policy = config.policy_pack.get("capture")
    return policy if isinstance(policy, dict) else {}


def _learning_policy(config: cfg.ProjectConfig | None) -> dict[str, Any]:
    policy = _capture_policy(config)
    learning = policy.get("learning") if isinstance(policy, dict) else None
    return learning if isinstance(learning, dict) else {}


def _learning_dedupe_enabled(policy: dict[str, Any]) -> bool:
    dedupe = policy.get("dedupe")
    if not isinstance(dedupe, dict):
        return True
    return bool(dedupe.get("enabled", True))


def _marker_reason(status: str, reason: str | None) -> str | None:
    raw = status.strip()
    if raw.startswith("skipped:"):
        return None
    if raw != "skipped":
        return None
    if reason in SKIP_REASONS:
        return reason
    return "no-policy"


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [item for item in value if isinstance(item, str)]


def _positive_ints(value: Any) -> list[int]:
    if not isinstance(value, list | tuple):
        return []
    return [item for item in value if isinstance(item, int) and item > 0]


def _duplicate_learning_fingerprint(
    fingerprint: str,
    records: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> str | None:
    for record in records:
        if not isinstance(record, dict):
            continue
        capture_block = record.get("capture")
        learning = capture_block.get("learning") if isinstance(capture_block, dict) else None
        if not isinstance(learning, dict):
            continue
        if learning.get("fingerprint") != fingerprint:
            continue
        decision = learning.get("decision")
        if decision in {"create-learning", "duplicate"}:
            return str(record.get("run_id") or (record.get("pull_request") or {}).get("number"))
    return None


def _learning_result(
    decision: str,
    *,
    reason: str,
    fingerprint: str,
    policy: dict[str, Any],
    duplicate_of: str | None = None,
) -> dict[str, Any]:
    if decision not in LEARNING_DECISIONS:
        raise CaptureError(f"unsupported learning decision: {decision}")
    result = {
        "schema_version": LEARNING_DECISION_SCHEMA_VERSION,
        "decision": decision,
        "reason": reason,
        "fingerprint": fingerprint,
        "policy_source": "policy_pack.capture.learning",
        "policy_mode": policy.get("mode", "policy-unavailable"),
        "durable_artifact": decision == "create-learning",
    }
    if duplicate_of is not None:
        result["duplicate_of"] = duplicate_of
    return result


def _policy_reason(policy: dict[str, Any], default: str) -> str:
    reason = policy.get("reason")
    return reason.strip() if isinstance(reason, str) and reason.strip() else default


def _normalize_text(value: str | None) -> str:
    return " ".join(value.lower().split()) if isinstance(value, str) else ""


def _normalize_path(value: str) -> str:
    return "/".join(value.strip().lower().replace("\\", "/").split("/"))
