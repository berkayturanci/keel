"""Deterministic backbone step completion verification.

Agentic ship steps may be performed by different runtimes, but advancing the
backbone must not depend on private prose. This module defines the shared
"done" contract for each step, the structured handoff shape between steps, and
the fail-closed transition check adapters can run before moving forward.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import artifacts, evidence, model

SCHEMA_VERSION = "keel.step-verification.v1"
HANDOFF_SCHEMA_VERSION = "keel.step-handoff.v1"
HANDOFF_MARKER = artifacts.STEP_HANDOFF_MARKER
COMPLETE_STATUS = "complete"


@dataclass(frozen=True)
class StepRequirement:
    """Required evidence for one backbone step."""

    step_id: str
    step_name: str
    required_evidence: tuple[str, ...] = ()
    verifier: str = "keel.stepverifier.verify_step_completion"

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_name": self.step_name,
            "required_evidence": list(self.required_evidence),
            "verifier": self.verifier,
        }


def contract_as_dict(
    review_contract: dict[str, Any],
    *,
    dry_run: bool = False,
    enforced: bool = True,
) -> dict[str, Any]:
    """Return the deterministic step-completion contract for ship-like flows."""
    del dry_run  # The contract describes the required done-state even for dry-run output.
    requirements = step_requirements(review_contract, dry_run=False, enforced=enforced)
    return {
        "schema_version": SCHEMA_VERSION,
        "consumer_neutral": True,
        "deterministic": True,
        "fail_closed": True,
        "dry_run_disables_runtime_gating": True,
        "source": "backbone_plan + evidence",
        "no_premature_termination": True,
        "handoff_schema": {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "required_fields": [
                "schema_version",
                "step_id",
                "step_name",
                "status",
                "summary",
                "evidence_ids",
                "rendered",
            ],
            "renderer": "keel.artifacts.render_step_handoff",
            "marker": HANDOFF_MARKER,
        },
        "completion_rule": (
            "A step may transition as success only when its structured handoff has "
            "status=complete and every required evidence id for that step is ok."
        ),
        "steps": [requirement.as_dict() for requirement in requirements],
    }


def step_requirements(
    review_contract: dict[str, Any],
    *,
    dry_run: bool = False,
    enforced: bool = True,
) -> tuple[StepRequirement, ...]:
    """Map the public evidence contract onto the fixed backbone steps."""
    evidence_ids = [
        item.id
        for item in evidence.required_items(
            review_contract,
            dry_run=dry_run,
            enforced=enforced,
        )
    ]
    by_step = {
        "s7": tuple(item for item in evidence_ids if item.startswith("review-verdict-")),
        "s8": tuple(item for item in evidence_ids if item == "jury-verdict"),
        "s12": tuple(item for item in evidence_ids if item.startswith("closure-comment-")),
    }
    return tuple(
        StepRequirement(
            step_id=step.id,
            step_name=step.name,
            required_evidence=by_step.get(step.id, ()),
        )
        for step in model.BACKBONE
    )


def build_handoff(
    *,
    step_id: str,
    status: str = COMPLETE_STATUS,
    summary: str | None = None,
    evidence_ids: tuple[str, ...] | list[str] = (),
    next_step: str | None = None,
    producer: str | None = None,
) -> dict[str, Any]:
    """Build a structured handoff object rendered through canonical artifacts."""
    step = model.get_step(step_id)
    clean_evidence = tuple(
        item.strip() for item in evidence_ids if isinstance(item, str) and item.strip()
    )
    rendered = artifacts.render_step_handoff(
        step_id=step.id,
        step_name=step.name,
        status=status,
        summary=summary,
        next_step=next_step,
        evidence_ids=clean_evidence,
    )
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "step_id": step.id,
        "step_name": step.name,
        "status": status,
        "summary": summary or "No summary recorded.",
        "evidence_ids": list(clean_evidence),
        "next_step": next_step,
        "producer": producer,
        "rendered": rendered,
    }


def verify_step_completion(
    *,
    step_id: str,
    handoff: dict[str, Any] | None,
    evidence_report: dict[str, Any] | None,
    review_contract: dict[str, Any],
    dry_run: bool = False,
    enforced: bool = True,
) -> dict[str, Any]:
    """Verify that one step can be marked complete without trusting prose."""
    requirement = _requirement_for(
        step_id,
        review_contract,
        dry_run=dry_run,
        enforced=enforced,
    )
    checks = [
        _check_handoff_schema(step_id, handoff),
        _check_handoff_status(handoff),
        _check_handoff_marker(handoff),
        _check_required_evidence(requirement, evidence_report),
    ]
    missing = [
        reason
        for check in checks
        if not check["ok"]
        for reason in check["missing"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "step_id": step_id,
        "status": "pass" if not missing else "fail",
        "no_premature_termination": True,
        "required_evidence": list(requirement.required_evidence),
        "missing": missing,
        "checks": checks,
    }


def _requirement_for(
    step_id: str,
    review_contract: dict[str, Any],
    *,
    dry_run: bool,
    enforced: bool,
) -> StepRequirement:
    requirements = {
        requirement.step_id: requirement
        for requirement in step_requirements(
            review_contract,
            dry_run=dry_run,
            enforced=enforced,
        )
    }
    if step_id not in requirements:
        raise KeyError(f"unknown backbone step: {step_id}")
    return requirements[step_id]


def _check_handoff_schema(step_id: str, handoff: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(handoff, dict):
        return _check("handoff_schema", False, "handoff missing")
    if handoff.get("schema_version") != HANDOFF_SCHEMA_VERSION:
        return _check("handoff_schema", False, "handoff schema mismatch")
    if handoff.get("step_id") != step_id:
        return _check("handoff_schema", False, "handoff step mismatch")
    return _check("handoff_schema", True)


def _check_handoff_status(handoff: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(handoff, dict):
        return _check("handoff_status", False, "handoff missing")
    if handoff.get("status") != COMPLETE_STATUS:
        return _check("handoff_status", False, "handoff not complete")
    return _check("handoff_status", True)


def _check_handoff_marker(handoff: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(handoff, dict):
        return _check("handoff_renderer", False, "handoff missing")
    rendered = handoff.get("rendered")
    if not isinstance(rendered, str) or HANDOFF_MARKER not in rendered:
        return _check("handoff_renderer", False, "canonical handoff renderer missing")
    return _check("handoff_renderer", True)


def _check_required_evidence(
    requirement: StepRequirement,
    evidence_report: dict[str, Any] | None,
) -> dict[str, Any]:
    if not requirement.required_evidence:
        return _check("required_evidence", True)
    ok_ids = {
        result["id"]
        for result in _evidence_results(evidence_report)
        if result.get("ok") is True and isinstance(result.get("id"), str)
    }
    missing = [
        evidence_id
        for evidence_id in requirement.required_evidence
        if evidence_id not in ok_ids
    ]
    return _check("required_evidence", not missing, *missing)


def _evidence_results(evidence_report: dict[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not isinstance(evidence_report, dict):
        return ()
    results = evidence_report.get("results")
    if not isinstance(results, list):
        return ()
    return tuple(item for item in results if isinstance(item, dict))


def _check(name: str, ok: bool, *missing: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "missing": list(missing),
    }
