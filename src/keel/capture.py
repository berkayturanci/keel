"""Consumer-neutral post-merge capture contract and verification helpers."""

from __future__ import annotations

import json
import os.path
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

# `workspace` imports nothing from this package's config layer, so naming it here
# keeps the import graph acyclic — see `_HasPolicyPack` for why that matters.
from . import workspace
from . import yaml_helper as yaml


class _HasPolicyPack(Protocol):
    """Duck type for a loaded ``ProjectConfig``.

    This module only reads ``policy_pack``. Naming ``config.ProjectConfig`` here
    would import ``config``, and ``config`` already imports this module to
    validate ``policy_pack.capture.learning``. CodeQL counts even a
    ``TYPE_CHECKING`` import as that reverse edge — the cycle it reports as
    ``py/cyclic-import``, because ``ProjectConfig`` is defined *after*
    ``config``'s import of ``capture``.
    """

    policy_pack: Any


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


def contract_as_dict(config: _HasPolicyPack | None = None) -> dict[str, Any]:
    """Return the stable capture contract consumed by adapters and verifiers."""
    capture_policy = _capture_policy(config)
    # **The sink core will actually use**, not merely one written down. A dormant
    # `sink:` block under `capture.enabled: false` or `mode: marker-only` published
    # `project_destination: "sink"` — telling an adapter core would write — while
    # `learning_sink_writes` refused, so neither wrote and the contract had promised
    # one of them would. Third reader of the same question; they all ask it here.
    sink_policy = learning_sink_policy(config) if capture_hook_enabled(config) else None
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
            # `extension-owned` was the whole truth until keel shipped a writer.
            # A project that configures `learning.sink` has **core** writing the
            # file and filling `capture.artifact`, and an adapter that read this
            # block and wrote its own would have had it overwritten (#1154).
            "project_destination": "sink" if sink_policy is not None else "extension-owned",
            # Defaulted the way `learning_sink_plan` defaults it. Read without the
            # fallback, a sink that did not spell out its `kind` — which the schema,
            # the docs and the validator all allow — published
            # `{project_destination: sink, sink: None}`: a contract disagreeing with
            # itself about the writer it had just named.
            "sink": (
                sink_policy.get("kind", LEARNING_SINK_KINDS[0]) if sink_policy is not None else None
            ),
            # **Whether the file lands in the working tree, and so has to be
            # committed.** keel writes it and stops there. A relative sink path is
            # inside the repository, where an uncommitted file is one the next
            # worktree — cut from `origin/<base>` — and every CI runner never see:
            # keel would be writing a learning and then throwing it away, which is
            # the failure classifying `.keel/learning` as committed was for. An
            # absolute or `~` path is outside the checkout and git never sees it.
            "commit_required": learning_sink_in_worktree(config),
        },
        "learning_quality": learning_quality_contract_as_dict(config),
        "learning_retrieval": learning_retrieval_contract_as_dict(config),
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


def learning_retrieval_contract_as_dict(
    config: _HasPolicyPack | None = None,
) -> dict[str, Any]:
    """The read side of capture, declared so an adapter knows the section exists.

    Policy only — where this project reads learnings from and how many a brief
    carries. What was actually *found* is measured per run and travels on the ship
    contract, because reading a directory is I/O and this contract is pure.
    """
    return {
        "schema_version": LEARNING_RETRIEVAL_SCHEMA_VERSION,
        "policy_source": "policy_pack.capture.learning.source",
        # As **configured**, not as resolved: this contract is pure and has no
        # `{repo}` to expand with, so a resolved list would report an empty
        # `sources` for a templated setting that reads perfectly well at run time.
        "sources": learning_source_entries(config),
        "limit": DEFAULT_LEARNING_RETRIEVAL_LIMIT,
        "heading": LEARNING_BRIEF_HEADING,
        "briefs": ["implement", "review"],
        "reader": "capture.retrieve_relevant_learnings",
        "ledger_field": "capture.retrieved",
        "silent_when_empty": True,
    }


def learning_quality_contract_as_dict(config: _HasPolicyPack | None = None) -> dict[str, Any]:
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
    config: _HasPolicyPack | None = None,
    not_run: bool = False,
    retrieved: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build the capture block stored in a ship run ledger record.

    ``artifact`` is an optional reference (path or content hash) to the durable
    capture artifact. It is the proof that an ``applied`` capture actually
    produced something; capture reconcile treats ``applied`` with no artifact as
    a finding. ``deferred``/``skipped`` need no artifact.

    ``retrieved`` is the fingerprints of the learnings this run put in front of the
    implementer and the reviewers (#1155). Recording them is what lets a later run
    tell a lesson nobody had from one that was surfaced and still not applied —
    the difference between a retrieval gap and a discipline gap.

    ``not_run`` marks a record whose run never reached capture, so a reader can
    tell it apart from one that reached capture and lost its marker. Both carry
    ``marker: None``, and without the flag a capture-health reader can only
    assume the second and report a gap that is not there (#945). It is a
    *declaration* by the operator, deliberately: inferring it from the null would
    reclassify every genuinely missing marker as "never attempted", which is the
    fail-open this field exists to avoid.
    """
    clean_artifact = artifact.strip() if isinstance(artifact, str) and artifact.strip() else None
    clean_retrieved = _strings(retrieved)
    if status is None:
        return {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "status": None,
            "reason": reason,
            "marker_reason": None,
            "marker": None,
            "not_run": not_run,
            "artifact": clean_artifact,
            "retrieved": clean_retrieved,
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
            "retrieved": clean_retrieved,
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
        "retrieved": clean_retrieved,
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
    config: _HasPolicyPack | None = None,
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
    # **The same parent pair the writer consults.** Gating only the write left the
    # record claiming `create-learning` with `durable_artifact: true` for a run that
    # produced nothing — the write/record disagreement this feature already treats
    # as load-bearing one level down, inverted. One predicate answers both.
    if not policy.get("enabled") or not capture_hook_enabled(config):
        return _learning_result(
            "marker-only",
            reason="policy-unavailable",
            fingerprint=fingerprint,
            policy=policy,
        )
    mode = policy.get("mode", "marker-only")
    if mode == "create-learning":
        # **Anything but `applied`**, not just `skipped:*`. `deferred` fell through
        # and answered `create-learning` with `durable_artifact: true`, while
        # `learning_sink_writes` refuses every status but `applied` — the same
        # write/record disagreement, one status over.
        if capture_status != "applied":
            return _learning_result(
                "marker-only",
                reason=(
                    "capture-skipped"
                    if (capture_status or "").startswith("skipped")
                    else "capture-not-applied"
                ),
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


def capture_hook_enabled(config: _HasPolicyPack | None) -> bool:
    """Whether this project runs a post-merge **content hook** at all.

    `policy_pack.capture.enabled` is the project saying it intends to; `mode:
    marker-only` records the core marker *without* one, which is the schema's own
    wording. The learning sink is that hook, and so is the decision that says a
    durable artifact is wanted — both ask here, because a writer and a record that
    answer this differently is the disagreement this feature keeps producing.
    `_reconcile_marker_decision` reads the same pair for `skipped:no-policy`.
    """
    policy = _capture_policy(config)
    return bool(policy.get("enabled")) and policy.get("mode", "extension") == "extension"


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
    config: _HasPolicyPack | None = None,
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
    config: _HasPolicyPack | None,
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
    config: _HasPolicyPack | None,
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


def _capture_policy(config: _HasPolicyPack | None) -> dict[str, Any]:
    if config is None or not isinstance(config.policy_pack, dict):
        return {}
    policy = config.policy_pack.get("capture")
    return policy if isinstance(policy, dict) else {}


def _learning_policy(config: _HasPolicyPack | None) -> dict[str, Any]:
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

#: The suffixes the reader opens. Named because the *writer* is validated against
#: them: a sink filename ending `.markdown`, or in nothing at all, was written
#: successfully into the sink and then skipped by the only thing that reads it —
#: the writer/reader disagreement this feature exists inside, arriving through a
#: template the validator accepted.
LEARNING_READ_SUFFIXES = (".md", ".json", ".txt")

#: How much of the fingerprint a filename carries. A sha256 prefix this long
#: distinguishes every learning a project will ever write without making the name
#: unreadable.
LEARNING_FINGERPRINT_SLICE = 12

#: The document's fourth section (#1166): one entry per ``changed_files`` path, written
#: as a Markdown link so a **link-following** reader — a knowledge-graph builder, a
#: wiki — gets the file ↔ lesson edge. The front-matter list is a string to such a
#: reader; keel's own reader keeps matching on the list, which is why it stays.
LEARNING_FILES_HEADING = "## Files"
LEARNING_NO_FILES = "_No files recorded._"

#: Characters that end, escape, or open something inside a CommonMark link *text*:
#: the bracket pair and the backslash; the angle brackets that would start an autolink
#: or raw HTML from a file name (a merged PR chooses those bytes); the emphasis and
#: strikethrough delimiters that turned ``__init__.py`` — the most common Python file
#: name — into bold ``init``; the ampersand that would decode an entity reference; and
#: the backtick, because a code span binds more tightly than the link's brackets and
#: swallows the characters between a pair. Every one is ASCII punctuation, which
#: CommonMark lets a backslash escape. The destination is percent-encoded instead, so
#: the two halves of a link never disagree about where a path ends.
_LINK_TEXT_UNSAFE = re.compile(r"([\\\[\]<>_*~&`])")

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


def learning_sink_policy(config: _HasPolicyPack | None) -> dict[str, Any] | None:
    """The `policy_pack.capture.learning.sink` block, or `None` when unset.

    `None` and `{}` are different answers and the difference is load-bearing: every
    field of a sink is optional, so `sink: {}` is a project taking the documented
    defaults — `markdown-dir` into `.keel/learning`. Collapsed to `{}`, that
    declaration read as *no sink at all* and the project got the pre-#1154
    behaviour of writing nothing, while the same block naming its `kind` wrote.
    """
    sink = _learning_policy(config).get("sink")
    return sink if isinstance(sink, dict) else None


def learning_sink_in_worktree(config: _HasPolicyPack | None) -> bool:
    """Does this project's sink write **inside the repository**?

    Pure, and answered from the path's shape rather than the filesystem: a relative
    path resolves against `--root`, which is the checkout, while an absolute or `~`
    path is a folder somewhere else. It decides who has to commit the file — keel
    writes it and does not, so one inside the working tree is lost to the next
    worktree and to every CI runner unless the run commits it.
    """
    # Gated on the hook too: a dormant `sink:` under a disabled capture writes
    # nothing, so nothing needs committing. Fourth reader of the same question.
    sink = learning_sink_policy(config) if capture_hook_enabled(config) else None
    if sink is None:
        return False
    path = str(sink.get("path") or DEFAULT_LEARNING_SINK_PATH)
    if path.startswith("~"):
        return False
    # **Anchored on *any* platform, not this one.** `Path("C:/knowledge").is_absolute()`
    # is False on POSIX and `Path("/srv/knowledge").is_absolute()` is False on
    # Windows, so each host called the other's absolute path in-repo and would have
    # told the adapter to `git add` it — committing a `C:` directory into the
    # repository, or reaching outside it. A keel config is the same text wherever it
    # is read; `workspace.is_root_anchored` is the question already asked that way.
    if workspace.is_root_anchored(path):
        return False
    # **Normalised, because `../learnings` is relative and still outside.** It is
    # the documented "folder next to the checkout" shape without the leading `~`,
    # and reported as in-repo it would send the adapter to `git add` a path git
    # refuses — leaving the file off `base_branch` and the next worktree empty,
    # which is the failure this flag exists to prevent, arriving through the flag.
    return Path(os.path.normpath(path)).parts[:1] != ("..",)


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
        # **A filename is a name, not a path.** `{pr}/{fingerprint}.md` passes every
        # other check, `mkdir(parents=True)` creates the directory happily, and
        # `retrieve_relevant_learnings` — the only reader — globs one level and
        # skips directories, so the lesson is written where nothing will ever read
        # it. Nesting belongs in `path`, which is the field that names a directory.
        if field_name == "filename" and not raw.endswith(LEARNING_READ_SUFFIXES):
            errors.append(
                f"policy_pack.capture.learning.sink.filename must end in one of "
                f"{', '.join(LEARNING_READ_SUFFIXES)}; the read path opens no other "
                f"suffix, so anything else is written and never found"
            )
        if field_name == "filename" and ("/" in raw or "\\" in raw):
            errors.append(
                "policy_pack.capture.learning.sink.filename must not contain a path "
                "separator; it names a file inside `path`, and the read path does not "
                "descend into subdirectories"
            )
    return errors


#: Anything that would make a filename more than one path component, or unwritable.
#: `/` and `\\` split it; the control characters are the same class one field over.
_FILENAME_UNSAFE = re.compile(r"[/\\\x00-\x1f\x7f]+")


def _relative_stays_relative(template: str, values: dict[str, str]) -> str:
    """Expand a directory template without letting it change what it *is*.

    An absolute or `~` template stays what the project wrote. A relative one has
    to come back relative: a leading placeholder that expands to nothing otherwise
    turns `{repo}/learnings` into `/learnings`, which the shape-reading predicates
    still report as inside the repository — so the adapter is told to `git add` a
    path at the filesystem root.
    """
    expanded = _expand(template, values)
    if template.startswith("~") or workspace.is_root_anchored(template):
        return expanded
    return expanded.lstrip("/\\") or DEFAULT_LEARNING_SINK_PATH


def _one_component(name: str) -> str:
    """A filename that names exactly one file, whatever the placeholders held.

    Refusing a separator in the *template* is not enough: `{base_branch}` is a legal
    filename placeholder and `feat/sink` is a normal branch, so
    `{date}-pr{pr}-{base_branch}-{fingerprint}.md` expands to a name with a slash in
    it, `mkdir(parents=True)` makes the directory, and the lesson lands one level
    below where `retrieve_relevant_learnings` looks. Only `{slug}` was slugified;
    every other value went in raw.
    """
    return _FILENAME_UNSAFE.sub("-", name)


def _expand(template: str, values: dict[str, str]) -> str:
    out = template
    for name, value in values.items():
        out = out.replace("{" + name + "}", value)
    return out


#: A value plain YAML reads back unchanged: starts with a letter, contains only
#: letters, digits, space and a few punctuation marks that carry no meaning there.
_YAML_PLAIN = re.compile(r"[A-Za-z][A-Za-z0-9 ._/()+-]*")

#: **Everything `str.splitlines()` treats as a line break**, plus the rest of C0 and
#: DEL. Not a taste question and not only the obvious ones: a raw CR splits a line
#: inside quotes, a NUL makes a real parser refuse the document, and `\x85` (NEL),
#: `\u2028` (LINE SEPARATOR) and `\u2029` (PARAGRAPH SEPARATOR) are line breaks to
#: Python while looking like nothing at all — a C0-only pattern let a title open a
#: Markdown section of its own through the very guard written to stop it.
_YAML_CONTROL = re.compile("[\x00-\x1f\x7f\x85\u2028\u2029]")

#: Words plain YAML turns into something that is not a string.
_YAML_KEYWORDS = frozenset(
    {"y", "n", "yes", "no", "true", "false", "on", "off", "null", "none", "~"}
)


def _one_line(value: str) -> str:
    """A value that cannot start a second line, wherever it is written.

    :func:`_yaml_scalar` applies this before quoting, and the **body** needs it too:
    the document's `# {title}` heading took the raw string, so a title carrying a
    newline wrote a heading and then whatever followed it as Markdown of its own —
    `foo\n## injected` became `# foo` and an `## injected` section. The front matter
    and the body have to say the same thing about the same field.
    """
    return _YAML_CONTROL.sub(" ", value)


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
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    # **Every control character, not the newline.** A raw carriage return survives
    # inside quotes, and `_front_matter` splits lines on it — the reader gets a
    # title of `"foo` and drops the rest, while a real parser reads `foo bar`, so
    # the two disagree about the same file. A NUL is worse: PyYAML refuses the
    # document outright. They become spaces, because a title is a line.
    return f'"{_one_line(escaped)}"'


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


def learning_file_link_base(
    *,
    directory: str,
    in_repo: bool,
    owner: str | None,
    repo: str | None,
    head_sha: str | None,
) -> str | None:
    """Where the **Files** section's links point, or ``None`` for bare paths (#1166).

    Two forms, chosen by where the sink is. A sink **inside the checkout** links
    relative to the document's own directory — ``../..`` from the default
    ``.keel/learning`` — so the link resolves on disk from where the file sits, and a
    graph builder walking the repository makes the edge to a node it already has.
    The prefix is **lexical** — one ``..`` per component of the normalised directory —
    because a plan touches no filesystem: :func:`posixpath.relpath` would consult
    ``os.getcwd()`` for two relative arguments, which made the prefix depend on where
    the process stood and raise from a deleted directory. It is POSIX on every platform
    because the document is read on machines other than the one that wrote it.

    A sink **outside** the checkout has nothing to link to relatively, and a relative
    link that resolves to nothing is worse than none. It links the file on GitHub at
    the merged head when the owner, the repository and the head are all known, and
    otherwise says nothing — the caller renders the bare path. An *expanded* template
    that climbs out of the checkout (``{owner}/../learnings`` with ``owner`` unset) is
    outside it whatever the unexpanded template looked like, and takes the same forms.
    """
    if in_repo:
        normalised = posixpath.normpath(directory.replace("\\", "/"))
        parts = [part for part in normalised.split("/") if part not in ("", ".")]
        if not parts or parts[0] != "..":
            return "/".join([".."] * len(parts)) or "."
    if owner and repo and head_sha:
        return f"https://github.com/{owner}/{repo}/blob/{head_sha}"
    return None


def _file_bullet(path: str, base: str | None) -> str:
    """One **Files** bullet: a CommonMark link when there is a base, else the bare path.

    The link text is the repository's own name for the file, which also puts every
    path into the body — and :func:`retrieve_relevant_learnings` scores a document by
    its text, so a query naming a file now scores the lesson about it higher than the
    front-matter list alone did (the list matched once; the link matches again — in
    its text, or in its destination when the text carries an escape).
    The destination is percent-encoded: a space or a parenthesis in a path would
    otherwise end the link where the path continues.
    """
    name = _one_line(path)
    if base is None:
        # A code span ends at a backtick run as long as its opener, so a path carrying
        # one is fenced with a run one longer, padded as CommonMark allows — never
        # written plain, where its own Markdown would render.
        longest = max((len(run) for run in re.findall(r"`+", name)), default=0)
        if longest == 0:
            return f"- `{name}`"
        fence = "`" * (longest + 1)
        return f"- {fence} {name} {fence}"
    text = _LINK_TEXT_UNSAFE.sub(r"\\\1", name)
    return f"- [{text}]({base}/{quote(name, safe='/')})"


def _files_section(changed_files: list[str] | tuple[str, ...], base: str | None) -> list[str]:
    """The **Files** section's lines, from the same list the front matter is given.

    An empty list still renders the heading: the document shape is stable — four
    sections, always — and a lesson about no file says so rather than pointing at
    nothing.
    """
    items = _strings(changed_files)
    if not items:
        return [LEARNING_NO_FILES]
    return [_file_bullet(item, base) for item in items]


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
    file_link_base: str | None = None,
) -> str:
    """One learning file: frontmatter the reader can rely on, then four sections.

    The heading is repeated below the frontmatter deliberately. `retrieve_relevant_learnings`
    scores a file by its own text, and a title that lives only in frontmatter is a
    title the search cannot weigh.

    ``file_link_base`` is what :func:`learning_file_link_base` decided for the sink;
    the **Files** section links each ``changed_files`` entry under it, and renders
    the bare path when it is ``None``. The front matter does not read it: the list
    up there is byte-for-byte what it was before the section existed (#1166).
    """
    front = [
        "---",
        f"schema: {LEARNING_SCHEMA_VERSION}",
        f"title: {_yaml_scalar(title or 'Learning')}",
        f"description: {_yaml_scalar(description or '')}",
        f"repo: {_yaml_scalar(repo or '')}",
        # `null`, not an empty value. Both read back as `None`; only one of them
        # says so on purpose, and a bare `issue:` looks like a field somebody forgot
        # to fill rather than a merge that was linked to no issue.
        f"pr: {pr_number if pr_number is not None else 'null'}",
        f"issue: {issue_number if issue_number is not None else 'null'}",
        # Quoted like every other scalar in the block. Left bare, `2026-09-09` is a
        # YAML *timestamp*: a real parser returns `datetime.date` where keel's reader
        # returns the string, which is the one disagreement all this quoting exists
        # to prevent.
        f"date: {_yaml_scalar(date)}",
        # Quoted like every other scalar: a sha256 that happens to be all digits is
        # an `int` to a real parser and a 64-character string to keel's reader, and
        # this is the field that *identifies* the lesson.
        f"fingerprint: {_yaml_scalar(fingerprint)}",
    ]
    front += _yaml_sequence("labels", labels)
    front += _yaml_sequence("changed_files", changed_files)
    front.append("---")
    body = [
        "",
        # Through `_one_line`, like the front matter above: a heading built from a
        # raw title let a newline open a section of its own inside the document.
        f"# {_one_line(title or 'Learning')}",
        "",
        _one_line(description or ""),
        "",
        LEARNING_SECTION_HEADINGS[0],
        "",
        what_changed or LEARNING_EMPTY_SECTION,
        "",
        LEARNING_SECTION_HEADINGS[1],
        "",
        what_we_learned or LEARNING_EMPTY_SECTION,
        "",
        LEARNING_SECTION_HEADINGS[2],
        "",
        do_differently or LEARNING_EMPTY_SECTION,
        "",
        LEARNING_FILES_HEADING,
        "",
        *_files_section(changed_files, file_link_base),
        "",
    ]
    return "\n".join(front + body)


def learning_sink_writes(
    *,
    config: _HasPolicyPack | None,
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
    if not capture_hook_enabled(config):
        return False
    sink = learning_sink_policy(config)
    if sink is None or learning_sink_errors(sink):
        return False
    return isinstance(decision, dict) and decision.get("decision") == "create-learning"


def learning_sink_plan(
    *,
    config: _HasPolicyPack | None,
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
    head_sha: str | None = None,
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
    sink = learning_sink_policy(config) or {}
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
    # **A relative template stays relative.** `{repo}/learnings` with `repo` unset
    # expands to `/learnings` — absolute, at the filesystem root — while every
    # reader of the template's shape (`learning_sink_in_worktree`, and so
    # `commit_required` and the adapter's `git add`) still calls it in-repo. The
    # filename's expansion was already flattened; the directory's was not.
    directory = _relative_stays_relative(
        str(sink.get("path") or DEFAULT_LEARNING_SINK_PATH), values
    )
    filename = _one_component(
        _expand(str(sink.get("filename") or DEFAULT_LEARNING_SINK_FILENAME), values)
    )
    # Decided from the sink's *shape*, the way `learning_sink_in_worktree` decides
    # who commits the file: the same question, and the two answers agree about where
    # the document sits — except when a placeholder's expansion climbs out of the
    # checkout, which the link base sees and the template reader does not, and then
    # the outside form is the safe one.
    file_link_base = learning_file_link_base(
        directory=directory,
        in_repo=learning_sink_in_worktree(config),
        owner=owner,
        repo=repo,
        head_sha=head_sha,
    )
    return {
        "kind": sink.get("kind", LEARNING_SINK_KINDS[0]),
        "directory": directory,
        "filename": filename,
        "file_link_base": file_link_base,
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
            file_link_base=file_link_base,
        ),
    }


def duplicate_learning_artifact(
    *,
    config: _HasPolicyPack | None,
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
    # **The hook, not just the sink block.** `learning_decision` answers
    # `duplicate` on a fingerprint match *before* it reads the enabled flags, so
    # `duplicate` reaches here under a `capture.enabled: false` or `marker-only`
    # project — and the caller treats a returned path as permission to replace the
    # operator's own `--capture-artifact` with a stale sink file, for a project
    # whose contract says `extension-owned`. Fifth reader of the same question.
    if not capture_hook_enabled(config) or learning_sink_policy(config) is None:
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


#: Placeholders a `source` directory may use. A subset of the sink's: `{pr}`,
#: `{date}` and `{slug}` name one *document*, and a directory that named a single
#: PR would retrieve only that PR's lesson.
LEARNING_SOURCE_PLACEHOLDERS = ("owner", "repo", "base_branch")

#: How many lessons a brief carries, and how much of it they may take. A brief
#: *becomes* an agent's prompt, so an unbounded retrieval is an unbounded prompt —
#: the same reason `fixloop` clamps a reviewer's findings.
DEFAULT_LEARNING_RETRIEVAL_LIMIT = 5
LEARNING_BRIEF_CHAR_BUDGET = 4000
LEARNING_BRIEF_SUMMARY_CHARS = 240

#: The heading both briefs use, fixed so an agent reading two of them recognises
#: the same section rather than inferring one from prose.
LEARNING_BRIEF_HEADING = "Relevant past learnings"
LEARNING_RETRIEVAL_SCHEMA_VERSION = "keel.learning-retrieval.v1"

#: What an exact front-matter match is worth beside a text hit. A file whose
#: `labels` or `changed_files` name this task's own label or path is *about* this
#: task; one that merely says "auth" eight times is worded like it. Text scoring
#: alone ranked the second above the first.
LEARNING_LABEL_MATCH_SCORE = 6
LEARNING_FILE_MATCH_SCORE = 8

#: keel's own scaffolding, emitted into **every** document this writer produces. The
#: scorer subtracts it before counting words: an issue titled *"What changed in the
#: merge window"* otherwise scored `what` and `changed` against all three headings of
#: every learning in the directory and cleared the floor on all of them, so three
#: unrelated lessons opened the brief. Named here rather than spelled twice, so the
#: writer and the scorer cannot drift.
LEARNING_SECTION_HEADINGS = (
    "## What changed",
    "## What we learned",
    "## What to do differently next time",
)
LEARNING_EMPTY_SECTION = "_Not recorded._"

#: How many **distinct** query words a file must contain to be a text match at all.
#: One is a coincidence — "keel" appears in every learning this repository writes —
#: and repetition does not make it less of one, which a point floor could not say: at
#: four points a single word said four times passed, and a genuine three-word match
#: in a short handwritten note did not. An exact front-matter match is admitted on
#: its own, whatever the text says.
LEARNING_MIN_DISTINCT_TOKENS = 2

#: Words too common to distinguish one learning from another.
_LEARNING_STOPWORDS = frozenset(
    {"the", "and", "for", "with", "this", "that", "issue", "feat", "fix"}
)


def learning_source_errors(source: Any) -> list[str]:
    """Why this `source` cannot be used, or `[]`.

    Checked where the config is read, like the sink's templates and for the same
    reason: a directory naming `{repoo}` is a typo whose only symptom is a
    retrieval that silently finds nothing, on every run, forever.
    """
    if source in (None, [], ()):
        return []
    entries = [source] if isinstance(source, str) else source
    if not isinstance(entries, (list, tuple)):
        return ["policy_pack.capture.learning.source must be a string or a list of strings"]
    errors: list[str] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            errors.append("policy_pack.capture.learning.source entries must be non-empty strings")
            continue
        for name in re.findall(r"\{([^}]*)\}", entry):
            if name not in LEARNING_SOURCE_PLACEHOLDERS:
                errors.append(
                    f"policy_pack.capture.learning.source uses unknown placeholder "
                    f"{{{name}}}; known: {', '.join(LEARNING_SOURCE_PLACEHOLDERS)}"
                )
    return errors


def learning_source_entries(config: _HasPolicyPack | None) -> list[str]:
    """The directories a project *configured*, before any placeholder is expanded.

    Unset, it is **the directory the sink writes to** — a project that turned
    capture on has exactly one place its learnings live, and a second setting to
    keep in step with the first is a second setting to get wrong. With no sink
    either, the `.keel/learning/` convention, so retrieval works the moment a
    project has files however they got there.

    Separate from :func:`learning_source_dirs` because the pure contract has no
    `{repo}` to expand with: reporting the resolved list there would print an
    empty `sources` for a templated setting that reads fine at run time.
    """
    policy = _learning_policy(config)
    raw = policy.get("source")
    if isinstance(raw, str):
        entries = [raw]
    elif isinstance(raw, (list, tuple)):
        entries = [entry for entry in raw if isinstance(entry, str)]
    else:
        entries = []
    if not entries:
        sink = learning_sink_policy(config) or {}
        entries = [str(sink.get("path") or DEFAULT_LEARNING_SINK_PATH)]
    return entries


def learning_source_dirs(
    config: _HasPolicyPack | None,
    *,
    values: dict[str, str] | None = None,
) -> list[str]:
    """The directories this run reads, with the placeholders expanded.

    Pure. An entry still holding a placeholder after expansion is dropped rather
    than taken literally, which is what keeps a per-document sink path (`…/{pr}/`)
    from being read as a folder called `{pr}`.
    """
    resolved: list[str] = []
    for entry in learning_source_entries(config):
        expanded = _expand(entry, values or {}).strip()
        if not expanded or "{" in expanded:
            continue
        if expanded not in resolved:
            resolved.append(expanded)
    return resolved


def learning_query_text(
    *,
    title: str | None = None,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
) -> str:
    """The text a task is matched by: its title, its labels, and its paths.

    The paths belong in it. A lesson about `src/keel/ledger.py` and an issue that
    touches `src/keel/ledger.py` share no words at all when only titles are
    compared, which is the pairing retrieval most needs to make.
    """
    parts = [title or "", *_strings(labels), *_strings(changed_files)]
    return " ".join(part for part in parts if part)


def _front_matter_lists(content: str) -> dict[str, list[str]]:
    """Read string sequences from a bounded, complete YAML front matter block.

    Use the same safe parser as project configuration for block and flow lists.
    Only direct string items are consumed; aliases cannot cause recursive walks.
    Invalid or oversized metadata contributes no exact matches.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            front = "\n".join(lines[1:index])
            if len(front) > 65536:
                return {}
            try:
                fields = yaml.load(front)
            except (yaml.YAMLError, RecursionError):
                return {}
            if not isinstance(fields, dict):
                return {}
            return {
                key: [item for item in value if isinstance(item, str)]
                for key in ("labels", "changed_files")
                if isinstance(value := fields.get(key), list)
            }
    return {}


def _learning_tokens(query_text: str) -> set[str]:
    return {
        word.lower()
        for word in re.findall(r"[A-Za-z0-9_-]{3,}", query_text)
        if word.lower() not in _LEARNING_STOPWORDS
    }


_LEARNING_METADATA_KEYS = (
    "schema",
    "title",
    "description",
    "repo",
    "pr",
    "issue",
    "date",
    "fingerprint",
    "labels",
    "changed_files",
)
_LEARNING_METADATA_LINE = re.compile(
    r"^(?:" + "|".join(_LEARNING_METADATA_KEYS) + r")\s*[:=].*$",
    re.IGNORECASE,
)


def _lesson_text(body: str, suffix: str = ".md") -> str:
    """Score prose without treating metadata-shaped lesson sentences as fields.

    Markdown metadata was already removed with its front matter. JSON fields
    are removed structurally; plain text recognizes a leading schema header,
    ending at the first non-metadata line. Body sentences stay intact.
    """
    if suffix == ".json":
        try:
            record = json.loads(body)
        except (ValueError, RecursionError):
            record = None
        if isinstance(record, dict):
            body = "\n".join(
                value
                for key, value in record.items()
                if key.lower() not in _LEARNING_METADATA_KEYS and isinstance(value, str)
            )
    elif suffix == ".txt":
        lines = body.splitlines()
        if lines and re.fullmatch(r"schema\s*[:=]\s*keel\.learning\.v1", lines[0], re.I):
            index = 0
            while index < len(lines) and _LEARNING_METADATA_LINE.fullmatch(lines[index]):
                index += 1
            body = "\n".join(lines[index:])
    text = body.lower()
    for scaffold in (
        *LEARNING_SECTION_HEADINGS,
        LEARNING_EMPTY_SECTION,
        LEARNING_FILES_HEADING,
        LEARNING_NO_FILES,
        "## Changed files",
    ):
        text = text.replace(scaffold.lower(), " ")
    return text


def _text_score(content_lower: str, filename_lower: str, tokens: set[str]) -> tuple[int, int]:
    """`(points, distinct tokens matched)` for one file.

    The two answer different questions and only one of them decides admission.
    Points order the results; **distinct tokens** say whether this is a match at
    all, because repetition is not evidence — a note saying `ledger` twelve times
    matches a query about ledgers exactly as much as one saying it twice.
    """
    score = 0
    distinct = 0
    for token in tokens:
        hit = False
        if token in filename_lower:
            score += 3
            hit = True
        count = content_lower.count(token)
        if count > 0:
            score += min(count, 5)
            hit = True
        distinct += hit
    return score, distinct


def _exact_matches(content: str, labels: set[str], changed_files: set[str]) -> tuple[list, list]:
    """The declared `labels` / `changed_files` this file and this task share.

    Front matter only. A file without it is plain Markdown and falls back to text
    scoring, which is the whole tolerance the read path promises: learnings keel
    wrote and learnings a person wrote both rank.
    """
    lists = _front_matter_lists(content)
    matched_labels = sorted({_normalize_text(value) for value in lists.get("labels", [])} & labels)
    matched_files = sorted(
        {_normalize_path(value) for value in lists.get("changed_files", [])} & changed_files
    )
    return matched_labels, matched_files


def dedupe_learning_hits(
    hits: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """One entry per lesson, keeping the first — which is the best-ranked.

    Reading several directories is the point of a list, and the same lesson living
    in two of them is the normal way that happens: a shared knowledge folder synced
    into a checkout, a copy taken before a move. Left in, one lesson would take two
    of the five slots a brief has and push a different one out.

    Identity is the fingerprint, not the path: the same lesson under two names is
    still one lesson. A hit without one (a hand-written file) falls back to its
    path, which is the only thing that distinguishes it.
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for hit in hits:
        key = str(hit.get("fingerprint") or hit.get("path") or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


def _render_learning_brief(
    hits: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    char_budget: int = LEARNING_BRIEF_CHAR_BUDGET,
) -> tuple[str, list[dict[str, Any]]]:
    """The fixed **Relevant past learnings** block and the hits that fit in budget.

    Empty in, empty out, and the caller renders nothing at all then — which is
    what keeps a project with no learnings byte-identical to the brief it got
    before this existed. Zero cost when unused is a requirement, not a nicety.
    """
    if not hits:
        return "", []
    lines = [f"### {LEARNING_BRIEF_HEADING}", ""]
    used = sum(len(line) + 1 for line in lines)
    rendered_hits: list[dict[str, Any]] = []
    for hit in hits:
        title = str(hit.get("title") or hit.get("file") or "Learning").strip()
        summary = " ".join(str(hit.get("summary") or "").split())
        if len(summary) > LEARNING_BRIEF_SUMMARY_CHARS:
            summary = summary[: LEARNING_BRIEF_SUMMARY_CHARS - 1].rstrip() + "\u2026"
        path = str(hit.get("path") or hit.get("file") or "")
        entry = f"- **{title}**" + (f" — {summary}" if summary else "") + f" (`{path}`)"
        if used + len(entry) + 1 > char_budget:
            # Stop at the budget rather than trimming the last entry into a
            # sentence fragment: a brief that ends mid-lesson reads as a bug.
            break
        lines.append(entry)
        used += len(entry) + 1
        rendered_hits.append(hit)
    if len(lines) == 2:
        return "", []
    return "\n".join(lines) + "\n", rendered_hits


def render_learning_brief_section(
    hits: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    char_budget: int = LEARNING_BRIEF_CHAR_BUDGET,
) -> str:
    """The fixed **Relevant past learnings** block, or `""` when nothing matched.

    Empty in, empty out, and the caller renders nothing at all then — which is
    what keeps a project with no learnings byte-identical to the brief it got
    before this existed. Zero cost when unused is a requirement, not a nicety.
    """
    section, _ = _render_learning_brief(hits, char_budget=char_budget)
    return section


def learning_retrieval_as_dict(
    *,
    sources: list[str] | tuple[str, ...] = (),
    hits: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    limit: int = DEFAULT_LEARNING_RETRIEVAL_LIMIT,
    char_budget: int = LEARNING_BRIEF_CHAR_BUDGET,
) -> dict[str, Any]:
    """The retrieval block adapters read and the ledger records.

    `section` is rendered here rather than left to each brief's author: the
    implement brief and the reviewer brief must carry the *same* section, and two
    renderers agree only until one of them is edited.
    """
    section, rendered_hits = _render_learning_brief(hits, char_budget=char_budget)
    entries = [
        {
            "file": hit.get("file"),
            "path": hit.get("path"),
            "title": hit.get("title"),
            "summary": hit.get("summary"),
            "score": hit.get("score"),
            "fingerprint": hit.get("fingerprint"),
            "matched_labels": list(hit.get("matched_labels") or []),
            "matched_files": list(hit.get("matched_files") or []),
        }
        for hit in rendered_hits
    ]
    return {
        "schema_version": LEARNING_RETRIEVAL_SCHEMA_VERSION,
        "heading": LEARNING_BRIEF_HEADING,
        "policy_source": "policy_pack.capture.learning.source",
        "sources": list(sources),
        "limit": limit,
        "hits": entries,
        "fingerprints": [entry["fingerprint"] for entry in entries if entry["fingerprint"]],
        "section": section,
    }


def retrieve_relevant_learnings(
    query_text: str,
    learning_dir: str | Path,
    *,
    max_results: int = 3,
    min_score: int = 1,
    min_distinct_tokens: int = LEARNING_MIN_DISTINCT_TOKENS,
    labels: list[str] | tuple[str, ...] = (),
    changed_files: list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Retrieve relevant historical learning records for an issue or task.

    Stdlib-first token matching against Markdown or JSON learning files in
    ``learning_dir`` (e.g. ``.keel/learning/``). Returns the top matching lessons
    to be injected into implementation / review contexts.

    ``labels`` and ``changed_files`` are this task's own, and a file that
    *declares* one of them in its front matter is about this task rather than
    merely worded like it — see :data:`LEARNING_LABEL_MATCH_SCORE`. A file with no
    front matter is plain Markdown and scores on its text alone, which is the
    tolerance this reader promises: a lesson keel wrote and a lesson a person
    wrote both rank.

    The one function in this module that reads the filesystem, and it was written
    that way before the pure/thin-I/O split had a name for it. Everything it
    decides is in the pure helpers around it.
    """
    path = Path(learning_dir)
    if not path.is_dir():
        return []

    tokens = _learning_tokens(query_text)
    want_labels = {_normalize_text(label) for label in _strings(labels)}
    want_files = {_normalize_path(name) for name in _strings(changed_files)}
    if not tokens and not want_labels and not want_files:
        return []

    results: list[dict[str, Any]] = []
    for file_path in sorted(path.glob("*")):
        if not file_path.is_file() or file_path.suffix not in LEARNING_READ_SUFFIXES:
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fields, body = _front_matter(content)
        lists = _front_matter_lists(content)
        matched_labels, matched_files = _exact_matches(content, want_labels, want_files)
        # **The body, minus keel's own scaffolding.** Front matter is metadata with
        # its own exact matching, and scoring it as prose made every learning this
        # repository ever wrote match every task in it (`repo: keel` and `schema:`
        # name the project in all of them). The section headings are the same problem
        # one layer down: they are in every document, so a title sharing a word with
        # one matched the whole directory.
        lesson_text = _lesson_text(body, file_path.suffix)
        changed_paths = " ".join(lists.get("changed_files", []))
        if changed_paths:
            lesson_text = f"{lesson_text} {changed_paths.lower()}"
        text_score, distinct = _text_score(lesson_text, file_path.name.lower(), tokens)
        declared = len(matched_labels) + len(matched_files)
        score = (
            text_score
            + LEARNING_LABEL_MATCH_SCORE * len(matched_labels)
            + LEARNING_FILE_MATCH_SCORE * len(matched_files)
        )

        # **A declaration beats prose, whatever the prose says.** Added together, a
        # file repeating the query's words a dozen times outscored one that *declared*
        # the path the task touches — the ranking this whole scheme exists to get
        # right — because `min(count, 5)` per token compounds and the bonus does not.
        # It is a lexicographic key now: declared matches first, text only to break
        # the tie among files that declare the same number.
        if declared or (distinct >= min_distinct_tokens and text_score >= min_score):
            title, summary = _learning_title_and_summary(content, file_path.name)
            results.append(
                {
                    "file": file_path.name,
                    "path": str(file_path),
                    "title": title,
                    "summary": summary,
                    "score": score,
                    "declared": declared,
                    # The writer's own fingerprint when there is one, so a later
                    # run can tell that this exact lesson was surfaced. A file
                    # written by hand has none; its content identifies it.
                    "fingerprint": fields.get("fingerprint") or _content_fingerprint(content),
                    "matched_labels": matched_labels,
                    "matched_files": matched_files,
                }
            )

    results.sort(key=lambda r: (-r["declared"], -r["score"], r["file"]))
    return results[:max_results]


def _content_fingerprint(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
