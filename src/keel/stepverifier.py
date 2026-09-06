"""Deterministic backbone step completion verification.

Agentic ship steps may be performed by different runtimes, but advancing the
backbone must not depend on private prose. This module defines the shared
"done" contract for each step, the structured handoff shape between steps, and
the fail-closed transition check adapters can run before moving forward.

:data:`HANDOFF_FIELDS` is the single declaration of the handoff schema. The producer
(:func:`build_handoff`), the published contract (:func:`contract_as_dict`) and the
verifier (:func:`_check_handoff_schema`) all read it, so the verifier cannot fall
behind the shape the renderer emits — which is exactly how it came to check two of
ten fields while the renderer emitted all ten (#1101).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import artifacts, evidence, model, provenance

SCHEMA_VERSION = "keel.step-verification.v1"
HANDOFF_SCHEMA_VERSION = "keel.step-handoff.v1"
HANDOFF_MARKER = artifacts.STEP_HANDOFF_MARKER
COMPLETE_STATUS = "complete"

#: JSON type names the handoff schema uses, and the Python type each one is once the
#: document has been read back out of JSON.
_JSON_TYPES: dict[str, type] = {"string": str, "array": list, "object": dict}


@dataclass(frozen=True)
class HandoffField:
    """One field of the published ``keel.step-handoff.v1`` handoff object."""

    name: str
    json_type: str
    #: ``None`` is a value the canonical renderer legitimately emits for this field.
    nullable: bool = False
    #: An empty string or an empty array is legitimate for this field.
    may_be_blank: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.json_type, "nullable": self.nullable}

    def problem(self, handoff: dict[str, Any]) -> str | None:
        """Name what is wrong with this field in ``handoff``, or return ``None``.

        Every reason names the field, because "something is missing" is not a
        message an agent can act on and not one an operator can audit.
        """
        if self.name not in handoff:
            return f"handoff field missing: {self.name}"
        value = handoff[self.name]
        if value is None:
            return None if self.nullable else f"handoff field null: {self.name}"
        if not isinstance(value, _JSON_TYPES[self.json_type]):
            return f"handoff field wrong type: {self.name}"
        if not self.may_be_blank and not _has_content(value):
            return f"handoff field empty: {self.name}"
        return None


#: The published handoff schema — the *one* declaration shared with the producer.
#: A field added here is emitted by :func:`build_handoff`, published by
#: :func:`contract_as_dict` and required by the verifier in the same commit. A second,
#: hand-kept list is how the verifier fell eight fields behind the renderer (#1101).
HANDOFF_FIELDS: tuple[HandoffField, ...] = (
    HandoffField("schema_version", "string"),
    HandoffField("step_id", "string"),
    HandoffField("step_name", "string"),
    HandoffField("status", "string"),
    HandoffField("summary", "string"),
    HandoffField("evidence_ids", "array", may_be_blank=True),
    HandoffField("next_step", "string", nullable=True, may_be_blank=True),
    HandoffField("producer", "string", nullable=True, may_be_blank=True),
    HandoffField("provenance", "object"),
    HandoffField("rendered", "string"),
)


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
            "required_fields": [field.name for field in HANDOFF_FIELDS],
            "fields": [field.as_dict() for field in HANDOFF_FIELDS],
            "renderer": "keel.artifacts.render_step_handoff",
            "marker": HANDOFF_MARKER,
            "rendered_body_matches_fields": True,
            "completed_step_claims_required_evidence": True,
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
    vendor: str | None = None,
    model_name: str | None = None,
    allowed_capabilities: tuple[str, ...] | list[str] = (),
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
    values = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "step_id": step.id,
        "step_name": step.name,
        "status": status,
        "summary": summary or "No summary recorded.",
        "evidence_ids": list(clean_evidence),
        "next_step": next_step,
        "producer": producer,
        "provenance": provenance.source_tag(
            source_agent=producer,
            step_id=step.id,
            vendor=vendor,
            model=model_name,
            allowed_capabilities=allowed_capabilities,
        ),
        "rendered": rendered,
    }
    # Emit the schema, not a dict that happens to resemble it: a field declared in
    # HANDOFF_FIELDS and not built above raises here rather than shipping a handoff
    # the verifier will refuse, and one built above and not declared cannot leak into
    # the document at all.
    return {field.name: values[field.name] for field in HANDOFF_FIELDS}


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
        _check_handoff_provenance(step_id, handoff),
        _check_handoff_evidence(requirement, handoff),
        _check_required_evidence(requirement, evidence_report),
    ]
    missing = [reason for check in checks if not check["ok"] for reason in check["missing"]]
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
    """Check the whole published schema, naming every field that is absent or wrong."""
    if not isinstance(handoff, dict):
        return _check("handoff_schema", False, "handoff missing")
    problems = [field.problem(handoff) for field in HANDOFF_FIELDS]
    named = [problem for problem in problems if problem]
    if named:
        return _check("handoff_schema", False, *named)
    if handoff["schema_version"] != HANDOFF_SCHEMA_VERSION:
        return _check("handoff_schema", False, "handoff schema mismatch")
    if handoff["step_id"] != step_id:
        return _check("handoff_schema", False, "handoff step mismatch")
    # The backbone names its own steps, so a handoff that names one wrong was not
    # written from the step it claims to be reporting.
    if handoff["step_name"] != model.get_step(step_id).name:
        return _check("handoff_schema", False, "handoff step name mismatch")
    return _check("handoff_schema", True)


def _check_handoff_status(handoff: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(handoff, dict):
        return _check("handoff_status", False, "handoff missing")
    if handoff.get("status") != COMPLETE_STATUS:
        return _check("handoff_status", False, "handoff not complete")
    return _check("handoff_status", True)


def _check_handoff_marker(handoff: dict[str, Any] | None) -> dict[str, Any]:
    """Check the rendered body against the renderer, not against a substring.

    A marker can be typed; the canonical rendering of *these* fields cannot be typed
    into disagreement with them. Re-rendering the handoff's own structured fields and
    comparing catches a body pasted from another step, or one whose prose says
    something the fields do not.
    """
    if not isinstance(handoff, dict):
        return _check("handoff_renderer", False, "handoff missing")
    rendered = handoff.get("rendered")
    if not isinstance(rendered, str) or HANDOFF_MARKER not in rendered:
        return _check("handoff_renderer", False, "canonical handoff renderer missing")
    if rendered != _render_from(handoff):
        return _check("handoff_renderer", False, "handoff body does not match its own fields")
    return _check("handoff_renderer", True)


def _render_from(handoff: dict[str, Any]) -> str:
    evidence_ids = handoff.get("evidence_ids")
    return artifacts.render_step_handoff(
        step_id=handoff.get("step_id"),
        step_name=handoff.get("step_name"),
        status=handoff.get("status"),
        summary=handoff.get("summary"),
        next_step=handoff.get("next_step"),
        evidence_ids=evidence_ids if isinstance(evidence_ids, list) else [],
    )


def _check_handoff_provenance(step_id: str, handoff: dict[str, Any] | None) -> dict[str, Any]:
    """Check that the provenance tag is a canonical one, bound to this step.

    The evidence chain downstream reads this tag. A handoff carrying an arbitrary
    object under ``provenance`` has answered "who says so?" with nothing.
    """
    if not isinstance(handoff, dict):
        return _check("handoff_provenance", False, "handoff missing")
    tag = handoff.get("provenance")
    if not isinstance(tag, dict):
        return _check("handoff_provenance", False, "handoff field wrong type: provenance")
    if tag.get("schema_version") != provenance.SCHEMA_VERSION:
        return _check("handoff_provenance", False, "handoff provenance schema mismatch")
    if (
        tag.get("role") != provenance.UNTRUSTED_ROLE
        or tag.get("trusted_as_instructions") is not False
    ):
        return _check("handoff_provenance", False, "handoff provenance is not untrusted output")
    scope = tag.get("capability_scope")
    if not isinstance(scope, dict) or scope.get("can_expand_capabilities") is not False:
        return _check("handoff_provenance", False, "handoff provenance expands capabilities")
    source = tag.get("source")
    if not isinstance(source, dict):
        return _check("handoff_provenance", False, "handoff provenance names no source")
    if source.get("step_id") != step_id:
        return _check("handoff_provenance", False, "handoff provenance step mismatch")
    producer = handoff.get("producer")
    named = producer.strip() if isinstance(producer, str) else ""
    if named and source.get("agent_id") != named:
        return _check("handoff_provenance", False, "handoff provenance producer mismatch")
    return _check("handoff_provenance", True)


def _check_handoff_evidence(
    requirement: StepRequirement,
    handoff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Check that a completed handoff claims the evidence its step owes.

    The required-evidence check below reads a *separately supplied* report. This one
    binds the handoff to it: a step that reports completed work without naming the
    evidence for it is the fabrication this verifier exists to refuse.
    """
    if not isinstance(handoff, dict):
        return _check("handoff_evidence", False, "handoff missing")
    claimed = handoff.get("evidence_ids")
    if not isinstance(claimed, list):
        return _check("handoff_evidence", False, "handoff field wrong type: evidence_ids")
    if any(not (isinstance(item, str) and item.strip()) for item in claimed):
        return _check("handoff_evidence", False, "handoff evidence ids are not all named")
    if handoff.get("status") != COMPLETE_STATUS:
        return _check("handoff_evidence", True)
    ids = {item.strip() for item in claimed}
    unclaimed = [item for item in requirement.required_evidence if item not in ids]
    return _check(
        "handoff_evidence",
        not unclaimed,
        *(f"handoff claims no evidence id: {item}" for item in unclaimed),
    )


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
        evidence_id for evidence_id in requirement.required_evidence if evidence_id not in ok_ids
    ]
    return _check("required_evidence", not missing, *missing)


def _evidence_results(evidence_report: dict[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not isinstance(evidence_report, dict):
        return ()
    results = evidence_report.get("results")
    if not isinstance(results, list):
        return ()
    return tuple(item for item in results if isinstance(item, dict))


def _has_content(value: Any) -> bool:
    return bool(value.strip()) if isinstance(value, str) else bool(value)


def _check(name: str, ok: bool, *missing: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "missing": list(missing),
    }
