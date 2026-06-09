"""Capture-artifact redaction helpers.

The defaults intentionally target common credential shapes only. Project-specific
terms stay in ``policy_pack.capture_redaction.deny_patterns``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import config as cfg

REDACTION_SCHEMA_VERSION = "keel.capture-redaction.v1"


class RedactionError(ValueError):
    """Raised when a redaction policy cannot be compiled safely."""


@dataclass(frozen=True)
class RedactionRule:
    id: str
    pattern: re.Pattern[str]
    replacement: str
    source: str
    literal_replacement: bool = False


@dataclass(frozen=True)
class RedactionPolicy:
    rules: tuple[RedactionRule, ...]


@dataclass(frozen=True)
class RedactionResult:
    value: Any
    audit: dict[str, Any]


_DEFAULT_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "private-key-block",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        "[REDACTED:private-key]",
    ),
    (
        "bearer-token",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}",
        "Bearer [REDACTED:bearer-token]",
    ),
    (
        "github-token",
        r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b",
        "[REDACTED:github-token]",
    ),
    (
        "credential-url",
        r"([A-Za-z][A-Za-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@",
        r"\1[REDACTED:credentials]@",
    ),
    (
        "credential-assignment",
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
        r"token|secret|password|passwd|pwd)\b\s*[:=]\s*([\"']?)[^\s\"']{8,}\2",
        r"\1=[REDACTED:credential]",
    ),
)


def policy_from_config(
    config: cfg.ProjectConfig | None = None,
    *,
    strict: bool = True,
) -> RedactionPolicy:
    """Compile the default policy plus project-provided deny patterns."""
    rules = [
        RedactionRule(rule_id, _compile(pattern, f"default.{rule_id}"), replacement, "default")
        for rule_id, pattern, replacement in _DEFAULT_RULES
    ]
    if config is not None:
        rules.extend(_configured_rules(config, strict=strict))
    return RedactionPolicy(tuple(rules))


def sanitize(value: Any, policy: RedactionPolicy) -> RedactionResult:
    """Recursively sanitize strings inside ``value`` and return an audit summary."""
    counts: dict[str, int] = {}
    sanitized = _sanitize_value(value, policy, counts)
    rules = [
        {"id": rule.id, "source": rule.source, "count": counts[rule.id]}
        for rule in policy.rules
        if counts.get(rule.id, 0)
    ]
    return RedactionResult(
        sanitized,
        {
            "schema_version": REDACTION_SCHEMA_VERSION,
            "status": "applied",
            "rules": rules,
            "redaction_count": sum(item["count"] for item in rules),
        },
    )


def contract_as_dict(config: cfg.ProjectConfig) -> dict[str, Any]:
    """Return the redaction policy contract without exposing sensitive values."""
    configured_ids = _configured_rule_ids(config)
    return {
        "schema_version": REDACTION_SCHEMA_VERSION,
        "default_redaction": True,
        "configured_rule_count": len(configured_ids),
        "configured_rule_ids": configured_ids,
        "policy_source": "defaults + policy_pack.capture_redaction.deny_patterns",
        "audit_includes_original_values": False,
        "invalid_policy_handling": "skip-artifact-with-reason",
    }


def _configured_rule_ids(config: cfg.ProjectConfig) -> list[str]:
    pack = config.policy_pack or {}
    section = pack.get("capture_redaction")
    if not isinstance(section, dict):
        return []
    raw_rules = section.get("deny_patterns")
    if not isinstance(raw_rules, list):
        return []
    ids: list[str] = []
    for index, raw in enumerate(raw_rules):
        if isinstance(raw, dict):
            ids.append(str(raw.get("id") or f"deny-pattern-{index + 1}"))
    return ids


def _configured_rules(config: cfg.ProjectConfig, *, strict: bool = True) -> list[RedactionRule]:
    pack = config.policy_pack or {}
    section = pack.get("capture_redaction")
    if not isinstance(section, dict):
        return []
    raw_rules = section.get("deny_patterns")
    if not isinstance(raw_rules, list):
        return []
    rules: list[RedactionRule] = []
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            continue
        rule_id = str(raw.get("id") or f"deny-pattern-{index + 1}")
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            continue
        replacement = raw.get("replacement")
        if not isinstance(replacement, str) or not replacement:
            replacement = f"[REDACTED:{rule_id}]"
        source = f"policy_pack.capture_redaction.deny_patterns.{rule_id}"
        try:
            compiled = _compile(pattern, source)
        except RedactionError:
            if strict:
                raise
            continue
        rules.append(RedactionRule(rule_id, compiled, replacement, source,
                                   literal_replacement=True))
    return rules


def _compile(pattern: str, source: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise RedactionError(f"invalid capture redaction pattern at {source}: {exc}") from exc


def _sanitize_value(value: Any, policy: RedactionPolicy, counts: dict[str, int]) -> Any:
    if isinstance(value, str):
        return _sanitize_string(value, policy, counts)
    if isinstance(value, list):
        return [_sanitize_value(item, policy, counts) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_value(child, policy, counts) for key, child in value.items()}
    return value


def _sanitize_string(value: str, policy: RedactionPolicy, counts: dict[str, int]) -> str:
    text = value
    for rule in policy.rules:
        replacement = (
            (lambda _match, value=rule.replacement: value)
            if rule.literal_replacement else rule.replacement
        )
        text, count = rule.pattern.subn(replacement, text)
        if count:
            counts[rule.id] = counts.get(rule.id, 0) + count
    return text
