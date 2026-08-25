"""Agent-output provenance tags for cross-agent containment."""

from __future__ import annotations

from typing import Any

from .capabilities import KNOWN_CAPABILITIES

SCHEMA_VERSION = "keel.agent-output-provenance.v1"
UNTRUSTED_ROLE = "untrusted-agent-output"


def contract_as_dict() -> dict[str, Any]:
    """Return the deterministic provenance-tagging contract."""
    return {
        "schema_version": SCHEMA_VERSION,
        "consumer_neutral": True,
        "deterministic": True,
        "stdlib_only": True,
        "threat_model": "prior-agent-output-is-data-not-instructions",
        "trusted_as_instructions": False,
        "default_role": UNTRUSTED_ROLE,
        "source_fields": ["agent_id", "step_id", "vendor", "model"],
        "capability_scope": {
            "default_allowed_capabilities": [],
            "rule": "tagged content cannot expand downstream capabilities",
        },
        "consumers": [
            "findings",
            "step_handoff",
            "review_verdict",
            "jury_verdict",
            "feedback_workflow",
        ],
    }


def source_tag(
    *,
    source_agent: str | None,
    step_id: str | None,
    vendor: str | None = None,
    model: str | None = None,
    allowed_capabilities: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Build a source-only provenance tag for structured records."""
    capabilities, unknown = _capabilities(allowed_capabilities)
    return {
        "schema_version": SCHEMA_VERSION,
        "role": UNTRUSTED_ROLE,
        "trusted_as_instructions": False,
        "source": {
            "agent_id": _value(source_agent, "unknown-agent"),
            "step_id": _value(step_id, "unknown-step"),
            "vendor": _optional(vendor),
            "model": _optional(model),
        },
        "capability_scope": {
            "allowed_capabilities": capabilities,
            "unknown_capabilities": unknown,
            "can_expand_capabilities": False,
        },
    }


def tag_output(
    content: str,
    *,
    source_agent: str | None,
    step_id: str | None,
    vendor: str | None = None,
    model: str | None = None,
    allowed_capabilities: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Wrap text produced by an agent as untrusted data with provenance."""
    return {
        **source_tag(
            source_agent=source_agent,
            step_id=step_id,
            vendor=vendor,
            model=model,
            allowed_capabilities=allowed_capabilities,
        ),
        "content": content if isinstance(content, str) else "",
    }


def normalize_tag(
    value: dict[str, Any] | None,
    *,
    fallback_agent: str,
    step_id: str,
) -> dict[str, Any]:
    """Return a valid untrusted provenance tag, preserving valid source fields."""
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return source_tag(source_agent=fallback_agent, step_id=step_id)
    source = value.get("source") if isinstance(value.get("source"), dict) else {}
    scope = value.get("capability_scope") if isinstance(value.get("capability_scope"), dict) else {}
    allowed = scope.get("allowed_capabilities")
    return source_tag(
        source_agent=_value(source.get("agent_id"), fallback_agent),
        step_id=_value(source.get("step_id"), step_id),
        vendor=_optional(source.get("vendor")),
        model=_optional(source.get("model")),
        allowed_capabilities=allowed if isinstance(allowed, list) else [],
    )


def _value(value: Any, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _optional(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _capabilities(values: tuple[str, ...] | list[str]) -> tuple[list[str], list[str]]:
    known = set(KNOWN_CAPABILITIES)
    clean = {item.strip() for item in values if isinstance(item, str) and item.strip()}
    return (
        sorted(item for item in clean if item in known),
        sorted(item for item in clean if item not in known),
    )
