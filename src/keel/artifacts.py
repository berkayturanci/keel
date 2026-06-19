"""Canonical Markdown renderers for ship artifacts.

These helpers keep public GitHub artifacts deterministic and consumer-neutral.
Adapters should post the rendered bodies verbatim instead of hand-writing PR
descriptions, review verdicts, jury verdicts, or extension result summaries.
"""

from __future__ import annotations

from typing import Any

from . import evidence

SCHEMA_VERSION = "keel.artifacts.v1"
EXTENSION_RESULT_MARKER = "<!-- keel.extension-result.v1 -->"
ISSUE_UPDATE_MARKER = "<!-- keel.issue-update.v1 -->"
STEP_HANDOFF_MARKER = "<!-- keel.step-handoff.v1 -->"
RUN_CONTROL_HALT_MARKER = "<!-- keel.run-control-halt.v1 -->"
REVIEW_CYCLE_SUMMARY_MARKER = "keel.review-cycle-summary.v1"

#: Severity buckets that drive the consolidated histogram + merge recommendation,
#: in must-fix → advisory order. ``critical`` folds into ``blocker`` (must-fix).
SEVERITY_ORDER = ("blocker", "major", "minor", "nit")
_SEVERITY_ALIASES = {"critical": "blocker"}


def contract_as_dict() -> dict[str, Any]:
    """Return the canonical artifact renderer contract for ship-like flows."""
    return {
        "schema_version": SCHEMA_VERSION,
        "consumer_neutral": True,
        "deterministic": True,
        "renderers": {
            "pr_body": "keel.artifacts.render_pr_body",
            "issue_update": "keel.artifacts.render_issue_update",
            "review_verdict": "keel.artifacts.render_review_verdict",
            "jury_verdict": "keel.artifacts.render_jury_verdict",
            "review_cycle_summary": "keel.artifacts.render_review_cycle_summary",
            "extension_result": "keel.artifacts.render_extension_result",
            "step_handoff": "keel.artifacts.render_step_handoff",
            "run_control_halt": "keel.artifacts.render_run_control_halt",
        },
        "markers": {
            "review_verdict": evidence.REVIEW_VERDICT_MARKER,
            "jury_verdict": evidence.JURY_VERDICT_MARKER,
            "review_cycle_summary": REVIEW_CYCLE_SUMMARY_MARKER,
            "issue_update": ISSUE_UPDATE_MARKER,
            "extension_result": EXTENSION_RESULT_MARKER,
            "step_handoff": STEP_HANDOFF_MARKER,
            "run_control_halt": RUN_CONTROL_HALT_MARKER,
        },
        "adapter_rule": "post rendered markdown verbatim when available",
    }


def render_pr_body(
    *,
    issue_number: int | None = None,
    issue_intake: dict[str, Any] | None = None,
    changed_files: list[str] | tuple[str, ...] = (),
    testing: list[str] | tuple[str, ...] = (),
    docs_impact: str | None = None,
) -> str:
    """Render the canonical PR body used by ship implementers."""
    intake = issue_intake if isinstance(issue_intake, dict) else {}
    lines = [
        "## Summary",
        f"- { _value(intake.get('deliverable'), 'Implement the requested change.') }",
        "",
        "## Context / Root Cause",
        _value(intake.get("objective"), "See the linked issue for context."),
        "",
        "## Changes Made",
    ]
    files = [file for file in changed_files if isinstance(file, str)]
    if files:
        lines.extend(f"- Updated `{file}`." for file in files)
    else:
        lines.append("- No changed files recorded yet.")
    lines.extend(["", "## Testing"])
    tests = [item for item in testing if isinstance(item, str) and item.strip()]
    lines.extend(f"- {item.strip()}" for item in tests) if tests else lines.append(
        "- Not run yet; update this section before marking the PR ready."
    )
    lines.extend([
        "",
        "## Docs Impact",
        _value(docs_impact, "Docs Impact: none — no operator-facing behavior changed."),
        "",
        _closing_reference(issue_number),
    ])
    return "\n".join(lines).rstrip() + "\n"


def render_issue_update(
    *,
    issue_number: int | None = None,
    pull_request: int | None = None,
    status: str = "in-progress",
    summary: str | None = None,
    next_step: str | None = None,
) -> str:
    """Render a stable issue progress/update comment."""
    lines = [
        ISSUE_UPDATE_MARKER,
        "",
        "## Ship update",
        "",
        f"- **Issue:** {_issue(issue_number)}",
        f"- **Pull request:** {_pr(pull_request)}",
        f"- **Status:** {_value(status, 'in-progress')}",
        f"- **Summary:** {_value(summary, 'No summary recorded.')}",
        f"- **Next step:** {_value(next_step, 'Continue the ship workflow.')}",
    ]
    return "\n".join(lines) + "\n"


def render_review_verdict(
    *,
    reviewer: str,
    head_sha: str | None,
    verdict: str = "LGTM",
    scope: str | None = None,
    findings: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    testing: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
) -> str:
    """Render a head-bound reviewer verdict comment accepted by evidence verification.

    When ``vendor`` (and optionally ``model``) is supplied, structured
    ``vendor:`` / ``model:`` provenance lines are emitted so evidence
    verification can enforce vendor distinctness across required verdicts. The
    fields use the same vendor/model conventions as ``keel.provenance`` and are
    omitted entirely when not supplied, so the default rendering is unchanged.
    """
    lines = [
        evidence.REVIEW_VERDICT_MARKER,
        f"reviewer: {_slug(reviewer)}",
        f"head: {_value(head_sha, '<head-sha>')}",
    ]
    if isinstance(vendor, str) and vendor.strip():
        lines.append(f"vendor: {_slug(vendor)}")
        if isinstance(model, str) and model.strip():
            lines.append(f"model: {_slug(model)}")
    lines.extend([
        "",
        f"Verdict: {_value(verdict, 'LGTM')}",
        "",
        f"Scope reviewed: {_value(scope, 'Full changed-file diff and relevant contracts.')}",
        "",
        "Findings:",
    ])
    lines.extend(_finding_lines(findings))
    lines.extend(["", f"Testing noted: {_value(testing, 'See PR Testing section.')}"])
    return "\n".join(lines) + "\n"


def render_jury_verdict(
    *,
    head_sha: str | None,
    participants: list[str] | tuple[str, ...] = (),
    verdict: str = "LGTM",
    findings_summary: list[str] | tuple[str, ...] = (),
    remaining_risks: str | None = None,
) -> str:
    """Render a head-bound jury verdict comment accepted by evidence verification."""
    people = [person.strip() for person in participants if isinstance(person, str)
              and person.strip()]
    lines = [
        evidence.JURY_VERDICT_MARKER,
        f"head: {_value(head_sha, '<head-sha>')}",
        "",
        f"AI Jury verdict: {_value(verdict, 'LGTM')}.",
        "",
        f"Participants: {', '.join(people) if people else 'not recorded'}.",
        "",
        "Findings summary:",
    ]
    summaries = [item.strip() for item in findings_summary if isinstance(item, str)
                 and item.strip()]
    lines.extend(f"- {item}" for item in summaries) if summaries else lines.append("- none")
    lines.extend(["", f"Remaining risks: {_value(remaining_risks, 'none identified')}."])
    return "\n".join(lines) + "\n"


def render_review_cycle_summary(
    *,
    reviewers: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    head_sha: str | None = None,
    run_id: str | None = None,
) -> str:
    """Render the deterministic multi-reviewer review-cycle summary comment.

    Emits one section per reviewer (codename · focus · verdict · a
    ``Severity | File:Line | Description | Suggested Fix`` finding table) followed
    by a consolidated summary whose severity histogram — not the verdict strings —
    is the source of truth for the merge recommendation. The output is byte-stable
    for a given input so the orchestrator posts it verbatim instead of improvising
    a layout. When ``run_id`` is supplied an invisible ``keel.run-id`` marker is
    appended so an idempotent re-post edits the existing comment in place.
    """
    clean = [reviewer for reviewer in reviewers if isinstance(reviewer, dict)]
    lines = [
        REVIEW_CYCLE_SUMMARY_MARKER,
        f"head: {_value(head_sha, '<head-sha>')}",
        "",
    ]
    for index, reviewer in enumerate(clean):
        if index:
            lines.extend(["", "---", ""])
        lines.extend(_cycle_reviewer_lines(reviewer))
    if clean:
        lines.extend(["", "---", ""])
    lines.extend(_cycle_summary_lines(clean))
    if isinstance(run_id, str) and run_id.strip():
        lines.extend(["", f"<!-- keel.run-id: {run_id.strip()} -->"])
    return "\n".join(lines) + "\n"


def render_extension_result(
    *,
    slot: str,
    extension_id: str,
    status: str,
    mode: str,
    summary: str | None = None,
    artifacts: list[str] | tuple[str, ...] = (),
    follow_ups: list[str] | tuple[str, ...] = (),
) -> str:
    """Render a canonical extension result block/comment."""
    lines = [
        EXTENSION_RESULT_MARKER,
        "",
        "## Extension result",
        "",
        f"- **Slot:** `{_value(slot, 'unknown')}`",
        f"- **Extension:** `{_value(extension_id, 'unknown')}`",
        f"- **Status:** {_value(status, 'not-recorded')}",
        f"- **Mode:** {_value(mode, 'advisory')}",
        f"- **Summary:** {_value(summary, 'No summary recorded.')}",
        "- **Artifacts:**",
    ]
    artifact_lines = _string_bullets(artifacts)
    lines.extend(artifact_lines if artifact_lines else ["  - none"])
    lines.append("- **Follow-ups:**")
    follow_up_lines = _string_bullets(follow_ups)
    lines.extend(follow_up_lines if follow_up_lines else ["  - none"])
    return "\n".join(lines) + "\n"


def render_step_handoff(
    *,
    step_id: str,
    step_name: str | None = None,
    status: str = "complete",
    summary: str | None = None,
    next_step: str | None = None,
    evidence_ids: list[str] | tuple[str, ...] = (),
) -> str:
    """Render the canonical structured handoff between backbone steps."""
    lines = [
        STEP_HANDOFF_MARKER,
        "",
        "## Step handoff",
        "",
        f"- **Step:** `{_value(step_id, 'unknown')}`",
        f"- **Name:** {_value(step_name, 'not recorded')}",
        f"- **Status:** {_value(status, 'complete')}",
        f"- **Summary:** {_value(summary, 'No summary recorded.')}",
        f"- **Next step:** {_value(next_step, 'Continue the backbone plan.')}",
        "- **Evidence:**",
    ]
    evidence_lines = _string_bullets(evidence_ids)
    lines.extend(evidence_lines if evidence_lines else ["  - none"])
    return "\n".join(lines) + "\n"


def render_run_control_halt(
    *,
    control: str,
    reason: str,
    scope: str | None = None,
    observed: int | str | None = None,
    limit: int | str | None = None,
    action: str | None = None,
) -> str:
    """Render a stable hard-halt reason emitted by run controls."""
    lines = [
        RUN_CONTROL_HALT_MARKER,
        "",
        "## Run control halt",
        "",
        f"- **Control:** `{_value(control, 'unknown')}`",
        f"- **Reason:** {_value(reason, 'No reason recorded.')}",
        f"- **Scope:** {_value(scope, 'run')}",
        f"- **Observed:** {_value(observed, 'not recorded')}",
        f"- **Limit:** {_value(limit, 'not recorded')}",
        f"- **Action:** {_value(action, 'halt')}",
    ]
    return "\n".join(lines) + "\n"


def _finding_lines(findings: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[str]:
    if not findings:
        return ["- none"]
    lines: list[str] = []
    for finding in findings:
        severity = _value(finding.get("severity") if isinstance(finding, dict) else None, "nit")
        message = _value(finding.get("message") if isinstance(finding, dict) else None, "")
        if message:
            lines.append(f"- {severity}: {message}")
    return lines or ["- none"]


def _string_bullets(values: list[str] | tuple[str, ...]) -> list[str]:
    return [f"  - {value.strip()}" for value in values if isinstance(value, str)
            and value.strip()]


def _string_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def _canonical_severity(severity: str) -> str:
    lowered = severity.strip().lower()
    return _SEVERITY_ALIASES.get(lowered, lowered)


def _severity_rank(severity: str) -> int:
    canonical = _canonical_severity(severity)
    return SEVERITY_ORDER.index(canonical) if canonical in SEVERITY_ORDER else len(SEVERITY_ORDER)


def _cell(value: str) -> str:
    """Escape a non-empty finding string for a Markdown table cell.

    Callers pass values already normalised through ``_value`` (never blank), so
    escaping the table delimiter and folding newlines keeps the row intact.
    """
    return value.replace("\n", " ").replace("|", "\\|")


def _cycle_findings(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, (list, tuple)):
        return []
    findings = [
        {
            "severity": _value(item.get("severity"), "nit"),
            "location": _value(item.get("location"), "—"),
            "description": _value(item.get("description"), "—"),
            "suggested_fix": _value(item.get("suggested_fix"), "—"),
        }
        for item in raw
        if isinstance(item, dict)
    ]
    findings.sort(key=lambda finding: _severity_rank(finding["severity"]))
    return findings


def _cycle_reviewer_lines(reviewer: dict[str, Any]) -> list[str]:
    lines = [
        f"## Reviewer: {_value(reviewer.get('codename'), 'Reviewer')} "
        f"(Focus: {_value(reviewer.get('focus'), 'general review')})",
        "",
        f"Verdict: {_value(reviewer.get('verdict'), 'LGTM')}",
        "",
    ]
    findings = _cycle_findings(reviewer.get("findings"))
    if findings:
        lines.append("| Severity | File:Line | Description | Suggested Fix |")
        lines.append("| --- | --- | --- | --- |")
        lines.extend(
            f"| {_cell(finding['severity'])} | {_cell(finding['location'])} | "
            f"{_cell(finding['description'])} | {_cell(finding['suggested_fix'])} |"
            for finding in findings
        )
    else:
        lines.append("No findings.")
    clean_areas = _string_list(reviewer.get("clean_areas"))
    if clean_areas:
        lines.extend(["", f"Clean areas: {', '.join(clean_areas)}"])
    return lines


def _cycle_histogram(reviewers: list[dict[str, Any]]) -> dict[str, int]:
    histogram = dict.fromkeys(SEVERITY_ORDER, 0)
    for reviewer in reviewers:
        for finding in _cycle_findings(reviewer.get("findings")):
            canonical = _canonical_severity(finding["severity"])
            if canonical in histogram:
                histogram[canonical] += 1
    return histogram


def _aggregate_clean_areas(reviewers: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for reviewer in reviewers:
        for area in _string_list(reviewer.get("clean_areas")):
            if area not in seen:
                seen.append(area)
    return seen


def _merge_recommendation(reviewers: list[dict[str, Any]], histogram: dict[str, int]) -> str:
    needs_fixes = any(
        not _value(reviewer.get("verdict"), "LGTM").lower().startswith("lgtm")
        for reviewer in reviewers
    )
    if needs_fixes or histogram["blocker"] > 0:
        return "❌ block"
    if histogram["major"] + histogram["minor"] > 0:
        return "⚠️ request changes"
    if histogram["nit"] > 0:
        return "✅ approve (cosmetic nits)"
    return "✅ approve"


def _cycle_summary_lines(reviewers: list[dict[str, Any]]) -> list[str]:
    histogram = _cycle_histogram(reviewers)
    lines = [
        "## Consolidated Summary",
        "",
        "Severity Histogram: "
        + " · ".join(f"{severity} {histogram[severity]}" for severity in SEVERITY_ORDER),
        "",
        "Reviewer verdicts:",
    ]
    if reviewers:
        lines.extend(
            f"- {_value(reviewer.get('codename'), 'Reviewer')}: "
            f"{_value(reviewer.get('verdict'), 'LGTM')}"
            for reviewer in reviewers
        )
    else:
        lines.append("- none")
    areas = _aggregate_clean_areas(reviewers)
    lines.extend(["", f"Clean areas: {', '.join(areas) if areas else 'none reported'}"])
    lines.extend(["", f"Merge recommendation: {_merge_recommendation(reviewers, histogram)}"])
    return lines


def _closing_reference(issue_number: int | None) -> str:
    return f"Closes #{issue_number}" if isinstance(issue_number, int) else "Refs #<issue-number>"


def _issue(issue_number: int | None) -> str:
    return f"#{issue_number}" if isinstance(issue_number, int) else "not recorded"


def _pr(pull_request: int | None) -> str:
    return f"#{pull_request}" if isinstance(pull_request, int) else "not opened"


def slug(value: str) -> str:
    """Stable, deterministic slug for reviewer/run-id sub-keys (public alias)."""
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    return "-".join(part for part in clean.split("-") if part) or "reviewer"


def _slug(value: str) -> str:
    return slug(value)


def _value(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback
