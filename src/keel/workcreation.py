"""Deterministic policy for signal-driven work creation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "keel.work-creation.v1"
DEFAULT_MIN_OCCURRENCES = 2
DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_MAX_CREATIONS = 5
DEFAULT_NEAR_TEXT_SIMILARITY = 0.6

DECISIONS = (
    "create",
    "suppress-transient",
    "suppress-duplicate",
    "limit-reached",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class WorkDecision:
    """One deterministic work-creation decision."""

    candidate_id: str
    decision: str
    reason: str
    title: str
    duplicate_of: int | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "decision": self.decision,
            "reason": self.reason,
            "title": self.title,
            "creates_issue": self.decision == "create",
        }
        if self.duplicate_of is not None:
            result["duplicate_of"] = self.duplicate_of
        return result


def contract_as_dict(*, near_text_similarity: float | None = None) -> dict[str, Any]:
    """Return the shared work-creation policy contract.

    ``near_text_similarity`` is the project's resolved
    ``policy_pack.scan.near_text_similarity``. It has to be passed in rather than
    defaulted here, because the caller embeds this dict *beside* a `dedupe` block that
    already honours the knob — hardcoding the default shipped two different thresholds
    under near-identical keys in one contract, agreeing only as long as the project set
    the knob to exactly the built-in default (#633).
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "consumer_neutral": True,
        "deterministic": True,
        "stdlib_only": True,
        "source": "signal-driven commands",
        "decisions": list(DECISIONS),
        "transient_filter": {
            "default_min_occurrences": DEFAULT_MIN_OCCURRENCES,
            "default_min_confidence": DEFAULT_MIN_CONFIDENCE,
            "transient_outcome": "suppress-transient",
        },
        "dedupe": {
            "against": "open work",
            "keys": ["dedupe_key", "normalized_title", "near_text"],
            "near_text_similarity": _confidence(
                near_text_similarity, DEFAULT_NEAR_TEXT_SIMILARITY
            ),
            "duplicate_outcome": "suppress-duplicate",
        },
        "cycle_limit": {
            "default_max_creations": DEFAULT_MAX_CREATIONS,
            "limit_outcome": "limit-reached",
        },
        "consumers": [
            "regression",
            "review-all-day",
            "coverage",
            "deps-audit",
            "flake-audit",
        ],
    }


def evaluate_candidates(
    candidates: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    existing_work: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    *,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_creations: int = DEFAULT_MAX_CREATIONS,
    near_text_similarity: float = DEFAULT_NEAR_TEXT_SIMILARITY,
) -> dict[str, Any]:
    """Evaluate candidate signals against transient, dedupe, and cycle-limit policy."""
    policy = {
        "min_occurrences": _positive_int(min_occurrences, DEFAULT_MIN_OCCURRENCES),
        "min_confidence": _confidence(min_confidence, DEFAULT_MIN_CONFIDENCE),
        "max_creations": _positive_int(max_creations, DEFAULT_MAX_CREATIONS),
        "near_text_similarity": _confidence(
            near_text_similarity,
            DEFAULT_NEAR_TEXT_SIMILARITY,
        ),
    }
    normalized_existing = [
        _normalize_existing(item)
        for item in existing_work
        if isinstance(item, dict) and _is_open(item)
    ]
    created = 0
    decisions: list[WorkDecision] = []
    for index, raw in enumerate(candidates, start=1):
        if not isinstance(raw, dict):
            continue
        candidate = _normalize_candidate(raw, index)
        duplicate = _find_duplicate(candidate, normalized_existing, policy["near_text_similarity"])
        if _is_transient(candidate, policy):
            decisions.append(WorkDecision(
                candidate["id"],
                "suppress-transient",
                "transient-signal",
                candidate["title"],
            ))
        elif duplicate is not None:
            decisions.append(WorkDecision(
                candidate["id"],
                "suppress-duplicate",
                "open-work-duplicate",
                candidate["title"],
                duplicate_of=duplicate["number"],
            ))
        elif created >= policy["max_creations"]:
            decisions.append(WorkDecision(
                candidate["id"],
                "limit-reached",
                "per-cycle-limit-reached",
                candidate["title"],
            ))
        else:
            created += 1
            decisions.append(WorkDecision(
                candidate["id"],
                "create",
                "eligible",
                candidate["title"],
            ))
            normalized_existing.append(_created_as_existing(candidate, created))
    decision_dicts = [decision.as_dict() for decision in decisions]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "policy": policy,
        "summary": {
            "candidates": len([item for item in candidates if isinstance(item, dict)]),
            "create": _count(decision_dicts, "create"),
            "suppress_transient": _count(decision_dicts, "suppress-transient"),
            "suppress_duplicate": _count(decision_dicts, "suppress-duplicate"),
            "limit_reached": _count(decision_dicts, "limit-reached"),
        },
        "decisions": decision_dicts,
    }


def _normalize_candidate(raw: dict[str, Any], index: int) -> dict[str, Any]:
    title = _string(raw.get("title")) or f"candidate-{index}"
    body = _string(raw.get("body"))
    return {
        "id": _string(raw.get("id")) or f"candidate-{index}",
        "title": title,
        "body": body,
        "dedupe_key": _string(raw.get("dedupe_key")),
        "occurrences": _positive_int(raw.get("occurrences"), 1),
        "confidence": _confidence(raw.get("confidence"), 1.0),
        "normalized_title": _normalize_text(title),
        "tokens": _tokens(f"{title} {body}"),
    }


def _normalize_existing(raw: dict[str, Any]) -> dict[str, Any]:
    title = _string(raw.get("title"))
    body = _string(raw.get("body"))
    return {
        "number": raw.get("number") if isinstance(raw.get("number"), int) else None,
        "title": title,
        "body": body,
        "dedupe_key": _string(raw.get("dedupe_key")),
        "normalized_title": _normalize_text(title),
        "tokens": _tokens(f"{title} {body}"),
    }


def _created_as_existing(candidate: dict[str, Any], created_index: int) -> dict[str, Any]:
    return {
        "number": -created_index,
        "title": candidate["title"],
        "body": candidate["body"],
        "dedupe_key": candidate["dedupe_key"],
        "normalized_title": candidate["normalized_title"],
        "tokens": candidate["tokens"],
    }


def _is_transient(candidate: dict[str, Any], policy: dict[str, Any]) -> bool:
    return (
        candidate["occurrences"] < policy["min_occurrences"]
        or candidate["confidence"] < policy["min_confidence"]
    )


def _find_duplicate(
    candidate: dict[str, Any],
    existing: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any] | None:
    for item in existing:
        if _same_key(candidate, item) or _same_title(candidate, item):
            return item
        if _jaccard(candidate["tokens"], item["tokens"]) >= threshold:
            return item
    return None


def _same_key(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    return bool(candidate["dedupe_key"] and candidate["dedupe_key"] == existing["dedupe_key"])


def _same_title(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    return bool(
        candidate["normalized_title"]
        and candidate["normalized_title"] == existing["normalized_title"]
    )


def _is_open(raw: dict[str, Any]) -> bool:
    state = _string(raw.get("state")).lower()
    # Missing state is treated as open so incomplete GitHub/search fixtures
    # suppress duplicates conservatively instead of creating duplicate work.
    return state in {"", "open"}


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(_normalize_text(text)))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _normalize_text(text: str) -> str:
    return " ".join(_TOKEN_RE.findall(text.lower()))


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _positive_int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def _confidence(value: Any, default: float) -> float:
    return value if isinstance(value, int | float) and 0 <= value <= 1 else default


def _count(decisions: list[dict[str, Any]], decision: str) -> int:
    return sum(item["decision"] == decision for item in decisions)
