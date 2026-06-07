"""Operator consent contracts for live mutating keel runs."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "keel.operator-consent.v1"

CONSENT_SCOPES = (
    "filesystem",
    "git",
    "github",
    "secrets",
    "release",
    "production-adjacent",
)

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
    operator: str | None = None,
    target: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a JSON-compatible consent block for a command contract."""
    consent_scope = side_effect_scopes(side_effects)
    approved_scope = normalize_scopes(approved_scopes)
    effective_approved_scope = tuple(scope for scope in consent_scope if scope in approved_scope)
    missing_scope = tuple(scope for scope in consent_scope if scope not in effective_approved_scope)
    would_require = bool(consent_scope)
    requires = (not dry_run) and bool(missing_scope)
    status = _status(dry_run=dry_run, would_require=would_require, requires=requires)
    approved_live = (not dry_run) and would_require and not missing_scope
    return {
        "schema_version": SCHEMA_VERSION,
        "requires_operator_consent": requires,
        "would_require_operator_consent": would_require,
        "status": status,
        "consent_scope": list(consent_scope),
        "approved_scope": list(approved_scope),
        "effective_approved_scope": list(effective_approved_scope),
        "missing_scope": list(missing_scope if not dry_run else ()),
        "consent_prompt": _prompt(command, target, dry_run, consent_scope, missing_scope),
        "delegated_agent_scope": {
            "approved_mutation_scopes": list(effective_approved_scope if approved_live else ()),
            "scope_expansion_policy": "block-or-escalate",
            "secret_values_permitted_in_prompt": False,
            "secret_access_requires_explicit_scope": "secrets",
        },
        "consent_record": (
            _record(command, target, effective_approved_scope, operator, dry_run, now)
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


def _status(*, dry_run: bool, would_require: bool, requires: bool) -> str:
    if dry_run:
        return "not-required-dry-run" if would_require else "not-required-read-only"
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
