"""Deterministic run budgets, step caps, and oscillation detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import artifacts, model

SCHEMA_VERSION = "keel.run-controls.v1"
DEFAULT_RUN_BUDGET = 250
DEFAULT_STEP_CAP = 1
DEFAULT_FIXLOOP_CAP = 3
DEFAULT_REVIEWER_CAP = 3
DEFAULT_TEST_CAP = 2
DEFAULT_IDENTICAL_THRESHOLD = 3
DEFAULT_ALTERNATION_WINDOW = 4


@dataclass(frozen=True)
class HaltReason:
    """One deterministic hard-halt reason."""

    control: str
    reason: str
    scope: str
    observed: int | str
    limit: int | str
    action: str = "halt"

    def as_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "reason": self.reason,
            "scope": self.scope,
            "observed": self.observed,
            "limit": self.limit,
            "action": self.action,
            "rendered": artifacts.render_run_control_halt(
                control=self.control,
                reason=self.reason,
                scope=self.scope,
                observed=self.observed,
                limit=self.limit,
                action=self.action,
            ),
        }


def contract_as_dict() -> dict[str, Any]:
    """Return the pure-core run-control contract for agentic commands."""
    return {
        "schema_version": SCHEMA_VERSION,
        "consumer_neutral": True,
        "deterministic": True,
        "stdlib_only": True,
        "fail_closed": True,
        "wall_clock_timeouts": False,
        "hard_halts": [
            "run-budget-exceeded",
            "step-cap-exceeded",
            "oscillation-detected",
        ],
        "fail_soft_preserved": (
            "soft failures are recorded by their owning step; only budget, cap, or "
            "oscillation breaches produce a hard halt"
        ),
        "budget": {
            "unit": "work_unit",
            "default_max_work_units": DEFAULT_RUN_BUDGET,
            "breach_policy": "halt-fail-closed",
        },
        "step_caps": {
            "applies_to_slots": list(model.SLOTS),
            "default_max_iterations": DEFAULT_STEP_CAP,
            "overrides": _default_step_caps(),
            "breach_policy": "halt-fail-closed",
        },
        "oscillation": {
            "identical_action_threshold": DEFAULT_IDENTICAL_THRESHOLD,
            "alternating_diff_window": DEFAULT_ALTERNATION_WINDOW,
            "breach_policy": "halt-fail-closed",
        },
        "renderer": {
            "halt_reason": "keel.artifacts.render_run_control_halt",
            "marker": artifacts.RUN_CONTROL_HALT_MARKER,
        },
    }


def evaluate_run_controls(
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    max_work_units: int = DEFAULT_RUN_BUDGET,
    default_step_cap: int = DEFAULT_STEP_CAP,
    step_caps: dict[str, int] | None = None,
    identical_action_threshold: int = DEFAULT_IDENTICAL_THRESHOLD,
    alternating_diff_window: int = DEFAULT_ALTERNATION_WINDOW,
) -> dict[str, Any]:
    """Evaluate run controls and return pass or a structured hard halt."""
    normalized = [_normalize_event(event) for event in events if isinstance(event, dict)]
    reason = (
        _budget_halt(normalized, max_work_units)
        or _step_cap_halt(normalized, default_step_cap, step_caps or _default_step_caps())
        or _oscillation_halt(
            normalized,
            identical_action_threshold=identical_action_threshold,
            alternating_diff_window=alternating_diff_window,
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "halt" if reason else "pass",
        "hard_halt": reason is not None,
        "fail_closed": reason is not None,
        "reason": reason.as_dict() if reason else None,
        "summary": {
            "event_count": len(normalized),
            "work_units": sum(event["work_units"] for event in normalized),
        },
    }


def _default_step_caps() -> dict[str, int]:
    return {
        "fixloop": DEFAULT_FIXLOOP_CAP,
        "reviewers": DEFAULT_REVIEWER_CAP,
        "tester": DEFAULT_TEST_CAP,
        "test": DEFAULT_TEST_CAP,
    }


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": _string(event.get("step_id")),
        "slot": _string(event.get("slot")),
        "action": _string(event.get("action")),
        "output_fingerprint": _string(event.get("output_fingerprint")),
        "diff_fingerprint": _string(event.get("diff_fingerprint")),
        "work_units": _positive_int(event.get("work_units"), default=1),
        "soft_failure": bool(event.get("soft_failure")),
    }


def _budget_halt(events: list[dict[str, Any]], max_work_units: int) -> HaltReason | None:
    limit = _positive_int(max_work_units, default=DEFAULT_RUN_BUDGET)
    observed = sum(event["work_units"] for event in events)
    if observed > limit:
        return HaltReason(
            control="run-budget",
            reason="run-budget-exceeded",
            scope="run",
            observed=observed,
            limit=limit,
        )
    return None


def _step_cap_halt(
    events: list[dict[str, Any]],
    default_step_cap: int,
    step_caps: dict[str, int],
) -> HaltReason | None:
    default_limit = _positive_int(default_step_cap, default=DEFAULT_STEP_CAP)
    counts: dict[str, int] = {}
    for event in events:
        scope = event["slot"] or event["step_id"]
        if not scope:
            continue
        counts[scope] = counts.get(scope, 0) + 1
        limit = _step_limit(scope, step_caps, default_limit)
        if counts[scope] > limit:
            return HaltReason(
                control="step-cap",
                reason="step-cap-exceeded",
                scope=scope,
                observed=counts[scope],
                limit=limit,
            )
    return None


def _oscillation_halt(
    events: list[dict[str, Any]],
    *,
    identical_action_threshold: int,
    alternating_diff_window: int,
) -> HaltReason | None:
    repeated = _repeated_identical_action(
        events,
        threshold=_positive_int(
            identical_action_threshold,
            default=DEFAULT_IDENTICAL_THRESHOLD,
        ),
    )
    if repeated:
        return repeated
    return _alternating_diff(
        events,
        window=_positive_int(
            alternating_diff_window,
            default=DEFAULT_ALTERNATION_WINDOW,
        ),
    )


def _repeated_identical_action(
    events: list[dict[str, Any]],
    *,
    threshold: int,
) -> HaltReason | None:
    if threshold <= 1:
        threshold = DEFAULT_IDENTICAL_THRESHOLD
    streak = 0
    previous: tuple[str, str, str, str] | None = None
    for event in events:
        if not event["action"] and not event["output_fingerprint"]:
            previous = None
            streak = 0
            continue
        current = (
            event["step_id"],
            event["slot"],
            event["action"],
            event["output_fingerprint"],
        )
        streak = streak + 1 if current == previous else 1
        previous = current
        if streak >= threshold:
            return HaltReason(
                control="oscillation",
                reason="repeated-identical-action",
                scope=event["slot"] or event["step_id"] or "run",
                observed=streak,
                limit=threshold,
            )
    return None


def _alternating_diff(events: list[dict[str, Any]], *, window: int) -> HaltReason | None:
    if window < 4 or window % 2:
        window = DEFAULT_ALTERNATION_WINDOW
    diffs = [event["diff_fingerprint"] for event in events if event["diff_fingerprint"]]
    if len(diffs) < window:
        return None
    tail = diffs[-window:]
    left = tail[: window // 2]
    right = tail[window // 2 :]
    if left == right and len(set(left)) > 1:
        return HaltReason(
            control="oscillation",
            reason="alternating-diff-fingerprint",
            scope="diff",
            observed=",".join(tail),
            limit=f"no {window}-round alternation",
        )
    return None


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _positive_int(value: Any, *, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def _step_limit(scope: str, step_caps: dict[str, int], default_limit: int) -> int:
    default_caps = _default_step_caps()
    scope_default = default_caps.get(scope, default_limit)
    configured = step_caps.get(scope)
    return configured if isinstance(configured, int) and configured > 0 else scope_default
