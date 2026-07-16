"""Operator consent contracts for live mutating keel runs."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "keel.operator-consent.v1"
ESCALATION_SCHEMA_VERSION = "keel.risk-trust-escalation.v1"

CONSENT_SCOPES = (
    "filesystem",
    "git",
    "github",
    "secrets",
    "release",
    "production-adjacent",
)

APPROVAL_SOURCES = ("none", "flag", "env", "config")
CONSENT_MODES = ("explicit", "standing", "agent")
RISK_TIERS = ("tier-1", "tier-2", "tier-3")
TRUST_SIGNALS = ("high", "medium", "low")

_SIDE_EFFECT_SCOPES: dict[str, tuple[str, ...]] = {
    "capture": ("filesystem",),
    "deferral_queue": ("filesystem",),
    "file_edit": ("filesystem",),
    "report_write": ("filesystem",),
    "session_recap": ("filesystem",),
    "session_report": ("filesystem",),
    "git_branch": ("git",),
    "git_commit": ("git",),
    "git_checkout": ("git",),
    "git_push": ("git",),
    "git_worktree": ("filesystem", "git"),
    "comments": ("github",),
    "issue_close": ("github",),
    "issue_write": ("github",),
    "labels": ("github",),
    "merge": ("github",),
    "pull_request": ("github",),
    "reviews": ("github",),
    "package_publish": ("release",),
    "release": ("release",),
    "credential_access": ("secrets",),
    "secret_access": ("secrets",),
    "production_access": ("production-adjacent",),
    "production_write": ("production-adjacent",),
}

_READ_ONLY_SIDE_EFFECTS: tuple[str, ...] = (
    "check_runs",
    "issue_read",
    "pr_read",
)

_CAPABILITY_SIDE_EFFECTS: dict[str, tuple[str, ...]] = {
    "filesystem-write": ("file_edit",),
    "worktree": ("git_worktree",),
    "release-publish": ("release",),
    "secret-access": ("secret_access",),
    "production-adjacent": ("production_access",),
    "private-setup": ("credential_access",),
}

_SCOPE_ORDER = {scope: i for i, scope in enumerate(CONSENT_SCOPES)}


def side_effect_scopes(side_effects: Iterable[str]) -> tuple[str, ...]:
    """Map declared side effects to operator consent scopes."""
    scopes: set[str] = set()
    unknown: list[str] = []
    for effect in side_effects:
        if effect in _READ_ONLY_SIDE_EFFECTS:
            continue
        mapped = _SIDE_EFFECT_SCOPES.get(effect)
        if mapped is None:
            unknown.append(effect)
        else:
            scopes.update(mapped)
    if unknown:
        raise ValueError(
            f"unknown side effect {unknown[0]!r}; declare it as read-only or map it to consent"
        )
    return _sort_scopes(scopes)


def capability_side_effects(capabilities: Iterable[str]) -> tuple[str, ...]:
    """Return consent side effects implied by generic runtime capabilities."""
    effects: list[str] = []
    for capability in capabilities:
        effects.extend(_CAPABILITY_SIDE_EFFECTS.get(capability, ()))
    return tuple(dict.fromkeys(effects))


def escalation_contract_as_dict() -> dict[str, Any]:
    """Return the deterministic risk x trust escalation contract."""
    return {
        "schema_version": ESCALATION_SCHEMA_VERSION,
        "consumer_neutral": True,
        "deterministic": True,
        "stdlib_only": True,
        "signals": {
            "risk": {
                "source": "s5 classify",
                "tiers": list(RISK_TIERS),
            },
            "trust": {
                "source": "adapter/runtime trust signal",
                "values": list(TRUST_SIGNALS),
                "not_confidence_alone": True,
            },
        },
        "triggers": {
            "irreversible_or_side_effecting": "always gate",
            "repeated_retry": "retry_count >= 2",
            "conflicting_sources": "true",
            "large_diff": "changed_lines >= large_diff_threshold",
        },
        "low_risk_sampling": {
            "deterministic": True,
            "sample_bucket_range": "0..99",
        },
        "enforcement_boundary": "execution-layer",
        "agent_self_approval": False,
    }


def evaluate_escalation(
    *,
    risk_tier: str = "tier-1",
    trust_signal: str = "medium",
    side_effects: Iterable[str] = (),
    retry_count: int = 0,
    conflicting_sources: bool = False,
    changed_lines: int = 0,
    large_diff_threshold: int = 500,
    low_risk_sample_rate: int = 0,
    sample_bucket: int = 0,
) -> dict[str, Any]:
    """Evaluate whether operator escalation is required from risk x trust signals."""
    risk = _risk_tier(risk_tier)
    trust = _trust_signal(trust_signal)
    consent_scope = side_effect_scopes(side_effects)
    triggers = {
        "irreversible_or_side_effecting": bool(consent_scope),
        "repeated_retry": retry_count >= 2,
        "conflicting_sources": bool(conflicting_sources),
        "large_diff": large_diff_threshold > 0 and changed_lines >= large_diff_threshold,
    }
    sample = _sample(low_risk_sample_rate, sample_bucket)
    operator_required, reason = _escalation_reason(risk, trust, triggers, sample)
    return {
        "schema_version": ESCALATION_SCHEMA_VERSION,
        "risk_tier": risk,
        "trust_signal": trust,
        "operator_required": operator_required,
        "decision": "operator-gate" if operator_required else "no-escalation",
        "reason": reason,
        "triggers": triggers,
        "consent_scope": list(consent_scope),
        "sample": sample,
        "enforcement_boundary": "execution-layer",
        "agent_can_self_approve": False,
    }


def normalize_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    """Normalize user-supplied consent scopes and reject unknown scope names."""
    normalized = []
    unknown = []
    for raw in scopes:
        for part in str(raw).split(","):
            scope = part.strip()
            if not scope:
                continue
            if scope not in CONSENT_SCOPES:
                unknown.append(scope)
            else:
                normalized.append(scope)
    if unknown:
        raise ValueError(
            f"unknown consent scope {unknown[0]!r}; valid: {', '.join(CONSENT_SCOPES)}"
        )
    return _sort_scopes(normalized)


def build_consent_contract(
    *,
    command: str,
    side_effects: Iterable[str],
    dry_run: bool,
    approved_scopes: Iterable[str] = (),
    approval_source: str = "flag",
    mode: str = "explicit",
    operator: str | None = None,
    target: str | None = None,
    now: datetime | None = None,
    risk_tier: str = "tier-1",
    trust_signal: str = "medium",
    retry_count: int = 0,
    conflicting_sources: bool = False,
    changed_lines: int = 0,
    large_diff_threshold: int = 500,
    low_risk_sample_rate: int = 0,
    sample_bucket: int = 0,
) -> dict[str, Any]:
    """Build a JSON-compatible consent block for a command contract."""
    side_effects = tuple(side_effects)
    consent_scope = side_effect_scopes(side_effects)
    approved_scope = normalize_scopes(approved_scopes)
    if approval_source not in APPROVAL_SOURCES:
        raise ValueError(
            f"unknown approval source {approval_source!r}; valid: {', '.join(APPROVAL_SOURCES)}"
        )
    if mode not in CONSENT_MODES:
        raise ValueError(f"unknown consent mode {mode!r}; valid: {', '.join(CONSENT_MODES)}")
    effective_approved_scope = tuple(scope for scope in consent_scope if scope in approved_scope)
    missing_scope = tuple(scope for scope in consent_scope if scope not in effective_approved_scope)
    would_require = bool(consent_scope)
    agent_delegated = (not dry_run) and mode == "agent" and would_require
    requires = (not dry_run) and bool(missing_scope) and not agent_delegated
    status = _status(
        dry_run=dry_run,
        would_require=would_require,
        requires=requires,
        agent_delegated=agent_delegated,
    )
    approved_live = (not dry_run) and would_require and not missing_scope
    return {
        "schema_version": SCHEMA_VERSION,
        "requires_operator_consent": requires,
        "would_require_operator_consent": would_require,
        "status": status,
        "mode": mode,
        "consent_scope": list(consent_scope),
        "approved_scope": list(approved_scope),
        "approval_source": approval_source if approved_scope else "none",
        "effective_approved_scope": list(effective_approved_scope),
        "missing_scope": list(missing_scope if not dry_run else ()),
        "consent_prompt": _prompt(command, target, dry_run, consent_scope, missing_scope),
        "risk_trust_escalation": evaluate_escalation(
            risk_tier=risk_tier,
            trust_signal=trust_signal,
            side_effects=side_effects,
            retry_count=retry_count,
            conflicting_sources=conflicting_sources,
            changed_lines=changed_lines,
            large_diff_threshold=large_diff_threshold,
            low_risk_sample_rate=low_risk_sample_rate,
            sample_bucket=sample_bucket,
        ),
        "delegated_agent_scope": {
            "approved_mutation_scopes": list(effective_approved_scope if approved_live else ()),
            "scope_expansion_policy": "block-or-escalate",
            "secret_values_permitted_in_prompt": False,
            "secret_access_requires_explicit_scope": "secrets",
        },
        "consent_record": (
            _record(command, target, effective_approved_scope, operator, dry_run, now)
            | {"source": approval_source}
            if approved_live
            else None
        ),
    }


def assert_operator_consent(contract: dict[str, Any]) -> tuple[bool, str]:
    """Return whether a live run may proceed and the human-readable reason."""
    if contract.get("requires_operator_consent"):
        missing = ", ".join(contract.get("missing_scope", []))
        prompt = contract.get("consent_prompt") or "operator consent is required"
        return False, f"operator consent required: {prompt} Missing approved scope: {missing}."
    return True, "operator consent satisfied"


def _status(*, dry_run: bool, would_require: bool, requires: bool, agent_delegated: bool) -> str:
    if dry_run:
        return "not-required-dry-run" if would_require else "not-required-read-only"
    if agent_delegated:
        return "agent-delegated"
    if requires:
        return "missing"
    return "approved" if would_require else "not-required-read-only"


def _prompt(
    command: str,
    target: str | None,
    dry_run: bool,
    consent_scope: tuple[str, ...],
    missing_scope: tuple[str, ...],
) -> str:
    mode = "dry-run" if dry_run else "live"
    target_text = target or "unspecified target"
    scope_text = ", ".join(consent_scope) if consent_scope else "none"
    if dry_run:
        return (
            f"Command {command!r} is planned for {mode} mode against {target_text}. "
            f"A live run would require operator approval for: {scope_text}."
        )
    missing_text = ", ".join(missing_scope) if missing_scope else "none"
    return (
        f"Approve command {command!r} for live mode against {target_text}. "
        f"Required scopes: {scope_text}. Missing approvals: {missing_text}."
    )


def _record(
    command: str,
    target: str | None,
    approved_scope: tuple[str, ...],
    operator: str | None,
    dry_run: bool,
    now: datetime | None,
) -> dict[str, Any]:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "operator": operator,
        "workflow": command,
        "target": target,
        "scopes_approved": list(approved_scope),
        "dry_run": dry_run,
        "secret_values_recorded": False,
    }


def _sort_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(scopes), key=lambda s: (_SCOPE_ORDER.get(s, 999), s)))


def _risk_tier(value: str) -> str:
    return value if value in RISK_TIERS else "tier-3"


def _trust_signal(value: str) -> str:
    return value if value in TRUST_SIGNALS else "low"


def _sample(rate: int, bucket: int) -> dict[str, Any]:
    sample_rate = min(100, max(0, int(rate)))
    sample_bucket = min(99, max(0, int(bucket)))
    return {
        "rate": sample_rate,
        "bucket": sample_bucket,
        "selected": sample_bucket < sample_rate,
    }


def _escalation_reason(
    risk: str,
    trust: str,
    triggers: dict[str, bool],
    sample: dict[str, Any],
) -> tuple[bool, str]:
    if triggers["irreversible_or_side_effecting"]:
        return True, "irreversible-or-side-effecting"
    if triggers["repeated_retry"]:
        return True, "repeated-retry"
    if triggers["conflicting_sources"]:
        return True, "conflicting-sources"
    if triggers["large_diff"]:
        return True, "large-diff"
    if risk == "tier-3" and trust != "high":
        return True, "risk-trust"
    if risk == "tier-2" and trust == "low":
        return True, "risk-trust"
    if sample["selected"]:
        return True, "low-risk-sample"
    return False, "below-escalation-threshold"
