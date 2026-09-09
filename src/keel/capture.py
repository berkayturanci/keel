"""Consumer-neutral post-merge capture contract and verification helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
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
    artifact: str | None = None,
    title: str | None = None,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
    existing_records: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    config: cfg.ProjectConfig | None = None,
    not_run: bool = False,
) -> dict[str, Any]:
    """Build the capture block stored in a ship run ledger record.

    ``artifact`` is an optional reference (path or content hash) to the durable
    capture artifact. It is the proof that an ``applied`` capture actually
    produced something; capture reconcile treats ``applied`` with no artifact as
    a finding. ``deferred``/``skipped`` need no artifact.

    ``not_run`` marks a record whose run never reached capture, so a reader can
    tell it apart from one that reached capture and lost its marker. Both carry
    ``marker: None``, and without the flag a capture-health reader can only
    assume the second and report a gap that is not there (#945). It is a
    *declaration* by the operator, deliberately: inferring it from the null would
    reclassify every genuinely missing marker as "never attempted", which is the
    fail-open this field exists to avoid.
    """
    clean_artifact = artifact.strip() if isinstance(artifact, str) and artifact.strip() else None
    if status is None:
        return {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "status": None,
            "reason": reason,
            "marker_reason": None,
            "marker": None,
            "not_run": not_run,
            "artifact": clean_artifact,
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
            "artifact": clean_artifact,
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
        "artifact": clean_artifact,
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
            reason=_policy_reason(policy, "policy-deferred"),
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
    # ⚡ Bolt: Return early if title matches to avoid expensive loops on labels and files
    if title and "capture" in title.lower():
        return True

    # ⚡ Bolt: Return early if label matches to avoid expensive loops on files
    for label in labels:
        if label.lower() == "capture":
            return True

    # ⚡ Bolt: Avoid generator overhead with explicit loop for paths (~90x speedup when early match)
    for path in changed_files:
        p = path.lower()
        if "/capture" in p or p.endswith("capture.py"):
            return True

    return False


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
                    _action(
                        "close-linked-issue", pr_number=pr_number, issue_number=issue_numbers[0]
                    ),
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
        actions.append(
            _action("close-linked-issue", pr_number=pr_number, issue_number=issue_numbers[0])
        )
    return _reconcile_result(
        pr_number,
        status="actionable",
        reason=reason,
        verification=verification,
        issue_numbers=issue_numbers,
        marker=marker,
        actions=actions,
    )


def _marker_of(record: dict[str, Any]) -> str | None:
    """This record's capture marker, or ``None`` when it carries none."""
    capture_block = record.get("capture")
    marker = capture_block.get("marker") if isinstance(capture_block, dict) else None
    return marker if marker else None


def _record_head(record: dict[str, Any]) -> str | None:
    """The head a ship_run record was written for, or ``None`` when it names none."""
    git = record.get("git")
    head = git.get("head_sha") if isinstance(git, dict) else None
    return head.strip() if isinstance(head, str) and head.strip() else None


def _verify_pr(records: list[dict[str, Any]], pr_number: int) -> dict[str, Any]:
    candidates = [
        record
        for record in records
        if record.get("record_type") == "ship_run"
        and (record.get("pull_request") or {}).get("number") == pr_number
    ]
    # Markers are counted on one head, not across every head this pull request
    # ever had (#1157). `existing_capture_marker` refuses a second marker per
    # (pull request, head); counting per pull request here would move the same
    # deadlock one step later — a pull request whose superseded head left a
    # marker would fail verification for carrying two, and the only exit would
    # again be editing an append-only ledger by hand.
    #
    # The head is taken from the **last record that carries a marker**, not from
    # the last record. `ledger.latest_ship_run_for_pr` documents why the latter
    # is the wrong proxy, and the concrete case is #945's: a run that never
    # reached capture re-records gates on a new head and writes `not_run` with no
    # marker. Reading the head off that row would look past a real applied marker
    # on the merged head and report the capture missing, and `reconcile_session`
    # would then plan to emit a marker that already exists. A row with no marker
    # cannot move the head the markers are counted on, because it is not evidence
    # about capture at all.
    marked = [record for record in candidates if _marker_of(record)]
    merged_head = _record_head(marked[-1]) if marked else None
    markers = [
        marker
        for record in marked
        if _record_head(record) == merged_head and (marker := _marker_of(record))
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


#: The one sink kind this issue ships. A directory of Markdown with stable
#: frontmatter is the whole contract — no vault format, no wikilinks, no plugin
#: API — so a project can point it at whatever reads Markdown and keel never
#: learns what that is.
LEARNING_SINK_KINDS = ("markdown-dir",)

#: Where a sink writes when a project configures capture but names no path. The
#: existing `.keel/learning/` convention, so turning the sink on changes where
#: files appear only for a project that asked it to.
DEFAULT_LEARNING_SINK_PATH = ".keel/learning"

#: **The fingerprint is in the name because the fingerprint is the identity.** Date,
#: PR and slug do not distinguish two lessons: a second `create-learning` run on the
#: same PR the same day — different labels, different files, a different lesson —
#: resolved to the same path and `os.replace` destroyed the first, leaving the
#: earlier ledger record pointing at a document that says something else. The dedupe
#: cannot help; it suppresses *identical* fingerprints, and these differ.
DEFAULT_LEARNING_SINK_FILENAME = "{date}-pr{pr}-{slug}-{fingerprint}.md"

#: How much of the fingerprint a filename carries. A sha256 prefix this long
#: distinguishes every learning a project will ever write without making the name
#: unreadable.
LEARNING_FINGERPRINT_SLICE = 12

#: The frontmatter contract the reader depends on. Fixed and small on purpose:
#: `retrieve_relevant_learnings` reads `title` and `description` out of it, so a
#: field added here is a field that side has to be taught.
LEARNING_SCHEMA_VERSION = "keel.learning.v1"

#: Every placeholder a `path` or `filename` template may use. Named rather than
#: open-ended: an unknown placeholder is a typo that would otherwise write a
#: directory called `{repoo}` and look like it worked.
LEARNING_SINK_PLACEHOLDERS = (
    "owner",
    "repo",
    "base_branch",
    "date",
    "pr",
    "slug",
    "fingerprint",
)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slugify(text: str | None, *, limit: int = 48) -> str:
    """A filename-safe slug, or `learning` when the title reduces to nothing."""
    slug = _SLUG_STRIP.sub("-", (text or "").lower()).strip("-")
    if not slug:
        return "learning"
    return slug[:limit].rstrip("-")


def learning_sink_policy(config: cfg.ProjectConfig | None) -> dict[str, Any]:
    """The `policy_pack.capture.learning.sink` block, or `{}` when unset."""
    sink = _learning_policy(config).get("sink")
    return sink if isinstance(sink, dict) else {}


def learning_sink_errors(sink: Any) -> list[str]:
    """Why this `sink` block cannot be used, or `[]`.

    Validated where the config is read rather than where the file is written: a
    template naming `{repoo}` is a typo whose only symptom would otherwise be a
    directory by that name, created successfully, on a machine nobody is watching.
    """
    if sink in (None, {}):
        return []
    if not isinstance(sink, dict):
        return ["policy_pack.capture.learning.sink must be a mapping"]
    errors: list[str] = []
    kind = sink.get("kind", LEARNING_SINK_KINDS[0])
    if kind not in LEARNING_SINK_KINDS:
        errors.append(
            f"policy_pack.capture.learning.sink.kind must be one of "
            f"{', '.join(LEARNING_SINK_KINDS)} (got {kind!r})"
        )
    for field_name in ("path", "filename"):
        raw = sink.get(field_name)
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip():
            errors.append(
                f"policy_pack.capture.learning.sink.{field_name} must be a non-empty string"
            )
            continue
        # `[^}]*`, not `[a-z_]*`: a mixed-case or hyphenated typo — `{Repo}`,
        # `{base-branch}` — is exactly as wrong as `{repoo}` and was invisible to a
        # pattern that only matched the shape of a correct name.
        used = re.findall(r"\{([^}]*)\}", raw)
        for name in used:
            if name not in LEARNING_SINK_PLACEHOLDERS:
                errors.append(
                    f"policy_pack.capture.learning.sink.{field_name} uses unknown placeholder "
                    f"{{{name}}}; known: {', '.join(LEARNING_SINK_PLACEHOLDERS)}"
                )
        # **A filename must be able to name two lessons.** Date, PR and slug do not
        # distinguish them: a second `create-learning` run on the same PR the same
        # day is a *different* lesson with a different fingerprint, and without it in
        # the name the write destroys the first one — leaving its ledger record
        # pointing at a document that says something else. Refused here, where the
        # placeholder typos are refused, because the only other symptom is a file
        # that quietly stops existing.
        if field_name == "filename" and "fingerprint" not in used:
            errors.append(
                "policy_pack.capture.learning.sink.filename must contain {fingerprint}; "
                "without it two lessons on one pull request overwrite each other"
            )
    return errors


def _expand(template: str, values: dict[str, str]) -> str:
    out = template
    for name, value in values.items():
        out = out.replace("{" + name + "}", value)
    return out


#: A value plain YAML reads back unchanged: starts with a letter, contains only
#: letters, digits, space and a few punctuation marks that carry no meaning there.
_YAML_PLAIN = re.compile(r"[A-Za-z][A-Za-z0-9 ._/()+-]*")

#: Words plain YAML turns into something that is not a string.
_YAML_KEYWORDS = frozenset(
    {"y", "n", "yes", "no", "true", "false", "on", "off", "null", "none", "~"}
)


def _yaml_scalar(value: str) -> str:
    """A front-matter value that survives a real YAML parser.

    **Quote unless the value is plainly safe**, rather than quoting a list of
    dangerous characters. The first cut listed `:`, `#`, `"` and a newline, and a
    dozen other shapes went through it: a leading `-`, `*`, `&`, `!`, `@`, `%`,
    `|`, `>` or backtick either fails to parse or comes back as something else,
    `{a}` becomes a mapping, `[a]` a list, and `yes` becomes `True`. An issue title
    can be any of those. A deny-list has to be right about every character; an
    allow-list only has to be right about the ones it lets through.
    """
    # `value == value.strip()` is not tidiness: `yes ` matches the allow-list, is not
    # in the keyword set, and goes in bare — and a plain YAML scalar drops its
    # trailing space, so a parser reads `yes` and returns `True`. The same
    # non-string a keyword produces, through the one gap the keyword check had.
    if (
        value
        and value == value.strip()
        and _YAML_PLAIN.fullmatch(value)
        and value.lower() not in _YAML_KEYWORDS
    ):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'


def _yaml_sequence(key: str, values: list[str] | tuple[str, ...]) -> list[str]:
    """A front-matter list, written so an empty one reads back as an empty list.

    `key:` with nothing under it is a **null** to a YAML parser, not `[]`, and the
    contract calls these fields sequences — a consumer that iterates them raises
    `TypeError` on a merge that touched nothing it recorded. `key: []` is the same
    field with the type it promised.
    """
    items = _strings(values)
    if not items:
        return [f"{key}: []"]
    return [f"{key}:", *(f"  - {_yaml_scalar(item)}" for item in items)]


def render_learning_document(
    *,
    title: str | None,
    description: str | None,
    pr_number: int | None,
    issue_number: int | None,
    repo: str | None,
    date: str,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
    fingerprint: str = "",
    what_changed: str = "",
    what_we_learned: str = "",
    do_differently: str = "",
) -> str:
    """One learning file: frontmatter the reader can rely on, then three sections.

    The heading is repeated below the frontmatter deliberately. `retrieve_relevant_learnings`
    scores a file by its own text, and a title that lives only in frontmatter is a
    title the search cannot weigh.
    """
    front = [
        "---",
        f"schema: {LEARNING_SCHEMA_VERSION}",
        f"title: {_yaml_scalar(title or 'Learning')}",
        f"description: {_yaml_scalar(description or '')}",
        f"repo: {_yaml_scalar(repo or '')}",
        f"pr: {pr_number if pr_number is not None else ''}",
        f"issue: {issue_number if issue_number is not None else ''}",
        f"date: {date}",
        f"fingerprint: {fingerprint}",
    ]
    front += _yaml_sequence("labels", labels)
    front += _yaml_sequence("changed_files", changed_files)
    front.append("---")
    body = [
        "",
        f"# {title or 'Learning'}",
        "",
        description or "",
        "",
        "## What changed",
        "",
        what_changed or "_Not recorded._",
        "",
        "## What we learned",
        "",
        what_we_learned or "_Not recorded._",
        "",
        "## What to do differently next time",
        "",
        do_differently or "_Not recorded._",
        "",
    ]
    return "\n".join(front + body)


def learning_sink_writes(
    *,
    config: cfg.ProjectConfig | None,
    decision: dict[str, Any] | None,
    capture_status: str | None,
) -> bool:
    """Whether this run writes a learning file at all.

    Split out so a caller can answer it **before** doing anything expensive — the
    writer redacts its values before rendering, and reaching for the redaction
    policy on a run with no sink turned an invalid `capture_redaction` pattern into
    an exception raised from the wrong place, past the handler `keel ship` has for
    exactly that. :func:`learning_sink_plan` asks the same question through this
    function, so the two cannot drift.

    Three conditions, and the third is the one that took two rounds to get right.
    **The decision is the gate**, not merely the dedupe: `learning_decision` already
    answers whether this run earns a durable artifact, and `create-learning` is the
    one answer whose `durable_artifact` is true. Refusing only `duplicate` let four
    other answers through — `learning.enabled` false, `enabled` omitted, `mode:
    defer`, `mode: marker-only` — each planning a write while the record beside it
    said the policy had decided not to keep one. A configured sink is where a
    project's learnings go, not permission to write one whatever the policy says.
    """
    if capture_status != "applied":
        return False
    sink = learning_sink_policy(config)
    if not sink or learning_sink_errors(sink):
        return False
    return isinstance(decision, dict) and decision.get("decision") == "create-learning"


def learning_sink_plan(
    *,
    config: cfg.ProjectConfig | None,
    decision: dict[str, Any] | None,
    capture_status: str | None,
    owner: str | None,
    repo: str | None,
    base_branch: str | None,
    date: str,
    pr_number: int | None,
    title: str | None = None,
    description: str | None = None,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
    issue_number: int | None = None,
    what_changed: str = "",
    what_we_learned: str = "",
    do_differently: str = "",
) -> dict[str, Any] | None:
    """What to write for this run, or `None` with the reason folded into the caller.

    Pure: it resolves a path and renders a document and touches no filesystem and no
    clock — `date` is passed in for the same reason every other plan in this package
    takes its facts as arguments.

    Returns `None` when :func:`learning_sink_writes` says there is nothing to write:
    no sink configured, a capture that is not `applied`, or a learning decision that
    does not call for a durable artifact — a `duplicate`, which is the dedupe doing
    its job, but equally a project whose policy said `marker-only`.
    """
    if not learning_sink_writes(config=config, decision=decision, capture_status=capture_status):
        return None
    sink = learning_sink_policy(config)
    # No `isinstance` re-check: the gate above returned for anything that is not a
    # `create-learning` mapping, so by here the decision is one.
    fingerprint = str(decision.get("fingerprint") or "")
    values = {
        "owner": owner or "",
        "repo": repo or "",
        "base_branch": base_branch or "",
        "date": date,
        "pr": str(pr_number) if pr_number is not None else "",
        "slug": _slugify(title),
        "fingerprint": fingerprint[:LEARNING_FINGERPRINT_SLICE],
    }
    directory = _expand(str(sink.get("path") or DEFAULT_LEARNING_SINK_PATH), values)
    filename = _expand(str(sink.get("filename") or DEFAULT_LEARNING_SINK_FILENAME), values)
    return {
        "kind": sink.get("kind", LEARNING_SINK_KINDS[0]),
        "directory": directory,
        "filename": filename,
        "content": render_learning_document(
            title=title,
            description=description,
            pr_number=pr_number,
            issue_number=issue_number,
            repo=repo,
            date=date,
            labels=labels,
            changed_files=changed_files,
            fingerprint=fingerprint,
            what_changed=what_changed,
            what_we_learned=what_we_learned,
            do_differently=do_differently,
        ),
    }


def duplicate_learning_artifact(
    *,
    config: cfg.ProjectConfig | None,
    decision: dict[str, Any] | None,
    capture_status: str | None,
    existing_records: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> str | None:
    """The artifact an earlier run already wrote for this same learning, or `None`.

    A `duplicate` decision writes no file — that is the dedupe working — but the
    run still records `applied`, and `applied` with no artifact is exactly what
    `capture-verify` reports as a finding. The lesson is not missing: it is on
    disk, under the run this one duplicates. Naming that file keeps the claim
    provable instead of letting the dedupe manufacture the gap the artifact
    exists to close.

    **Only for an `applied` capture**, and that gate is the whole point rather than
    a precaution: `learning_decision` answers `duplicate` on a fingerprint match
    before it looks at the status, so without it a `not-run` or `skipped` record
    was handed the earlier run's path — the exact contradiction the CLI refuses at
    its flag boundary, *a run that never reached capture produced no artifact*, and
    the one `record_marker` states for `deferred` and `skipped`.

    Pure: it reads recorded paths and never asks whether one still exists. The
    caller that can answer that is the caller that touches the filesystem.
    """
    if capture_status != "applied":
        return None
    if not learning_sink_policy(config):
        return None
    if not isinstance(decision, dict) or decision.get("decision") != "duplicate":
        return None
    fingerprint = decision.get("fingerprint")
    if not fingerprint:
        return None
    artifact: str | None = None
    for record in existing_records:
        if not isinstance(record, dict):
            continue
        capture_block = record.get("capture")
        if not isinstance(capture_block, dict):
            continue
        learning = capture_block.get("learning")
        if not isinstance(learning, dict) or learning.get("fingerprint") != fingerprint:
            continue
        candidate = capture_block.get("artifact")
        if isinstance(candidate, str) and candidate.strip():
            # Keep scanning: the ledger is append-only and the newest record
            # holding this fingerprint is the one whose path is current.
            artifact = candidate.strip()
    return artifact


def _unquote(value: str) -> str:
    """Undo :func:`_yaml_scalar` for the fields this reader uses.

    The writer quotes any value containing a colon — an issue title usually does —
    so a reader that took the raw text would hand back a title wrapped in quotes.
    """
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _front_matter(content: str) -> tuple[dict[str, str], str]:
    """Split a leading `---` block off, as `(fields, body)`.

    Only the scalar fields this contract defines are read; a list value (`labels:`)
    is skipped rather than parsed, because the caller wants a title and a sentence
    and nothing here should grow into a YAML parser.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content
    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return fields, "\n".join(lines[index + 1 :])
        key, sep, value = line.partition(":")
        if sep and not key.startswith(" "):
            fields[key.strip()] = _unquote(value.strip())
    # No closing delimiter: not front matter, whatever it looked like.
    return {}, content


def _learning_title_and_summary(content: str, fallback: str) -> tuple[str, str]:
    """A learning file's title and one-line summary.

    Front matter first, because that is what the writer fills. Read line-by-line
    instead, a file written by `render_learning_document` would be titled `---` and
    summarised `schema: keel.learning.v1` — measured, and the reason the reader is
    part of the change that added the writer.
    """
    fields, body = _front_matter(content)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    title = fields.get("title") or (lines[0].lstrip("#").strip() if lines else fallback)
    summary = fields.get("description") or ""
    if not summary:
        for line in lines[1:]:
            if not line.startswith("#"):
                summary = line
                break
    return title or fallback, summary


def retrieve_relevant_learnings(
    query_text: str,
    learning_dir: str | Path,
    *,
    max_results: int = 3,
    min_score: int = 1,
) -> list[dict[str, Any]]:
    """Retrieve relevant historical learning records for an issue or task.

    Pure, stdlib-first token matching against Markdown or JSON learning files
    in ``learning_dir`` (e.g. ``.keel/learning/``). Returns the top matching lessons
    to be injected into implementation / review contexts.
    """
    path = Path(learning_dir)
    if not path.is_dir():
        return []

    tokens = {
        w.lower()
        for w in re.findall(r"[A-Za-z0-9_-]{3,}", query_text)
        if w.lower() not in {"the", "and", "for", "with", "this", "that", "issue", "feat", "fix"}
    }
    if not tokens:
        return []

    results: list[dict[str, Any]] = []
    for file_path in sorted(path.glob("*")):
        if not file_path.is_file() or file_path.suffix not in {".md", ".json", ".txt"}:
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        score = 0
        content_lower = content.lower()
        filename_lower = file_path.name.lower()

        for token in tokens:
            if token in filename_lower:
                score += 3
            count = content_lower.count(token)
            if count > 0:
                score += min(count, 5)

        if score >= min_score:
            title, summary = _learning_title_and_summary(content, file_path.name)
            results.append(
                {
                    "file": file_path.name,
                    "path": str(file_path),
                    "title": title,
                    "summary": summary,
                    "score": score,
                }
            )

    results.sort(key=lambda r: (-r["score"], r["file"]))
    return results[:max_results]
