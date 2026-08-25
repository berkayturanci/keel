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
COVERAGE_DELTA_MARKER = "keel.coverage-delta.v1"
DEPS_AUDIT_MARKER = "keel.deps-audit.v1"
FLAKE_AUDIT_MARKER = "keel.flake-audit.v1"
SCAN_FINDING_MARKER = "keel.scan-finding.v1"
TRIAGE_AUDIT_MARKER = "keel.triage-audit.v1"

#: Severity buckets that drive the consolidated histogram + merge recommendation,
#: in must-fix → advisory order. ``critical`` folds into ``blocker`` (must-fix).
SEVERITY_ORDER = ("blocker", "major", "minor", "nit")
_SEVERITY_ALIASES = {"critical": "blocker"}

#: Dependency-advisory severity buckets, most → least severe.
DEPS_SEVERITY_ORDER = ("critical", "high", "moderate", "low")


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
    changed_files: list[str] | tuple[str, ...] | None = (),
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
    # `None` is "git could not be read", which must not render as "nothing changed".
    if changed_files is None:
        lines.append("- The changed-file list could not be read from git.")
    else:
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

    **Pass a real ``scope`` or real ``findings``.** The defaults — "Full
    changed-file diff and relevant contracts" and "none" — name nothing, and
    :func:`keel.evidence.verdict_substance` refuses a verdict that names nothing
    (#926). That is deliberate: 75 of 75 verdicts across 25 pull requests were
    this template with the defaults left in, and the gate could not tell them
    apart from a review that caught a blocker. Give the scope a path, a symbol,
    or a "checked X, Y and Z" clause — a genuinely clean review stays
    expressible, it just has to say what it looked at.
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
    participating_vendors: int | None = None,
) -> str:
    """Render a head-bound jury verdict comment accepted by evidence verification.

    The verdict declares ``vendors: <N>`` — the distinct vendors that actually
    took part. That line is the only channel by which the panel size reaches a
    CI evidence check: the run ledger and the jury artifact both live under the
    gitignored ``.keel/state/``, so a hosted runner cannot read them, while PR
    comments are always visible. When ``participating_vendors`` is omitted it is
    inferred from ``participants``, so a caller that already lists them does not
    have to count twice.
    """
    people = [person.strip() for person in participants if isinstance(person, str)
              and person.strip()]
    vendors = participating_vendors if participating_vendors is not None else len(people)
    lines = [
        evidence.JURY_VERDICT_MARKER,
        f"head: {_value(head_sha, '<head-sha>')}",
        f"vendors: {vendors}",
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


def render_coverage_delta(
    *,
    codename: str,
    base_sha: str | None = None,
    head_sha: str | None = None,
    areas: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> str:
    """Render the deterministic per-PR coverage delta comment.

    The literal first line is the caller-supplied ``codename`` (e.g.
    ``COVERAGE-<PR>-<UTC>``) — the load-bearing anchor the adapter finds by prefix
    to update the comment in place, so nothing precedes it. The adapter supplies
    the timestamped codename, so the renderer stays pure and byte-stable for a
    given input.
    """
    lines = [
        codename,
        "",
        f"Coverage delta: base@{_value(base_sha, '<base>')} → head@{_value(head_sha, '<head>')}",
    ]
    for area in areas:
        if isinstance(area, dict):
            lines.extend(["", *_coverage_area_lines(area)])
    lines.extend(["", f"<!-- {COVERAGE_DELTA_MARKER} -->"])
    return "\n".join(lines) + "\n"


def render_deps_audit(
    *,
    codename: str,
    ecosystems: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    licences: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    skipped: list[str] | tuple[str, ...] = (),
    security_only: bool = False,
) -> str:
    """Render the deterministic dependency-audit comment for the tracking issue.

    The literal first line is the caller-supplied ``codename`` (e.g.
    ``DEPS-AUDIT-<DATE>-<UTC>``); a fresh comment is appended per run, found later
    by that prefix. Under ``security_only`` the licence-drift section is omitted.
    """
    counts = _deps_counts(ecosystems)
    lines = [
        codename,
        "",
        " | ".join(f"{severity}: {counts[severity]}" for severity in DEPS_SEVERITY_ORDER),
    ]
    for ecosystem in ecosystems:
        if isinstance(ecosystem, dict):
            lines.extend(["", *_deps_ecosystem_lines(ecosystem)])
    if not security_only:
        lines.extend(["", *_deps_licence_lines(licences)])
    skipped_items = _string_list(skipped)
    if skipped_items:
        lines.extend(["", "## Skipped", ""])
        lines.extend(f"- {item}" for item in skipped_items)
    lines.extend(["", f"<!-- {DEPS_AUDIT_MARKER} -->"])
    return "\n".join(lines) + "\n"


def render_flake_audit(
    *,
    codename: str,
    summary: dict[str, Any] | None = None,
    new_flakes: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    tracked: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    limitations: list[str] | tuple[str, ...] = (),
) -> str:
    """Render the deterministic flake-audit report comment.

    The literal first line is the caller-supplied ``codename`` (e.g.
    ``FLAKE-AUDIT-<DATE>-<UTC>``). Newly-classified flakes render as a table (or a
    single italic line when none cleared the threshold); already-tracked flakes
    and honest limitations render only when present.
    """
    stats = summary if isinstance(summary, dict) else {}
    lines = [
        codename,
        "",
        (f"runs examined: {_count(stats.get('runs'))} · "
         f"distinct failing tests: {_count(stats.get('distinct'))} · "
         f"classified flakes: {_count(stats.get('classified'))} · "
         f"newly-opened issues: {_count(stats.get('opened'))}"),
        "",
        "## Newly classified flakes",
        "",
    ]
    flakes = _dict_list(new_flakes)
    if flakes:
        lines.append("| Test | Fail rate | Failures | Sample runs | Signature |")
        lines.append("| --- | --- | --- | --- | --- |")
        lines.extend(
            _table_row([
                _cell(_value(flake.get("test"), "—")),
                _cell(_value(flake.get("fail_rate"), "—")),
                _cell(str(_count(flake.get("failures")))),
                _cell(", ".join(_string_list(flake.get("samples"))) or "—"),
                _cell(_value(flake.get("signature"), "—")),
            ])
            for flake in flakes
        )
    else:
        lines.append("_no new flakes above threshold_")
    tracked_rows = _dict_list(tracked)
    if tracked_rows:
        lines.extend(["", "## Already tracked (deduped)", ""])
        lines.extend(
            f"- {_value(row.get('test'), '—')} — see {_issue_ref(row.get('issue'))}"
            for row in tracked_rows
        )
    limitation_items = _string_list(limitations)
    if limitation_items:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in limitation_items)
    lines.extend(["", f"<!-- {FLAKE_AUDIT_MARKER} -->"])
    return "\n".join(lines) + "\n"


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _dict_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _issue_ref(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"#{value}"
    return _value(value, "?")


def _scalar(value: Any, fallback: str) -> str:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return str(value)
    return _value(value, fallback)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt_pct(value: Any) -> str:
    return f"{value:.1f}%" if _is_number(value) else "—"


def _fmt_delta(base: Any, head: Any) -> tuple[str, bool]:
    if _is_number(base) and _is_number(head):
        delta = head - base
        return f"{delta:+.1f}%", abs(delta) >= 0.5
    return "—", False


def _coverage_row(unit: str, base: Any, head: Any, files: Any, *, is_overall: bool = False) -> str:
    delta_text, bold = _fmt_delta(base, head)
    files_text = _cell(str(files)) if str(files).strip() else ""
    cells = [_cell(unit), _fmt_pct(base), _fmt_pct(head), delta_text, files_text]
    if bold:
        cells = [f"**{cell}**" if cell else "" for cell in cells]
    elif is_overall:
        cells[0] = f"**{cells[0]}**"
    return _table_row(cells)


def _coverage_area_lines(area: dict[str, Any]) -> list[str]:
    name = _value(area.get("name"), "area")
    if area.get("skipped"):
        return [f"_{name} coverage skipped: {_value(area.get('skip_reason'), 'not run')}_"]
    lines = [
        f"## {name}",
        "",
        "| Unit | Base % | Head % | Δ | Files |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        _coverage_row(_value(row.get("unit"), "—"), row.get("base"), row.get("head"),
                      row.get("files", ""))
        for row in _dict_list(area.get("rows"))
    )
    overall = area.get("overall")
    if isinstance(overall, dict):
        lines.append(
            _coverage_row("overall", overall.get("base"), overall.get("head"), "",
                          is_overall=True)
        )
    return lines


def _deps_sev_rank(severity: str) -> int:
    lowered = severity.lower()
    return DEPS_SEVERITY_ORDER.index(lowered) if lowered in DEPS_SEVERITY_ORDER \
        else len(DEPS_SEVERITY_ORDER)


def _deps_counts(ecosystems: Any) -> dict[str, int]:
    counts = dict.fromkeys(DEPS_SEVERITY_ORDER, 0)
    for ecosystem in _dict_list(ecosystems):
        for finding in _dict_list(ecosystem.get("findings")):
            severity = _value(finding.get("severity"), "low").lower()
            if severity in counts:
                counts[severity] += 1
    return counts


def _deps_ecosystem_lines(ecosystem: dict[str, Any]) -> list[str]:
    name = _value(ecosystem.get("name"), "ecosystem")
    findings = _dict_list(ecosystem.get("findings"))
    if not findings:
        threshold = _value(ecosystem.get("threshold"), "low")
        return [f"_No {name} findings at or above {threshold} severity._"]
    findings = sorted(findings, key=lambda f: _deps_sev_rank(_value(f.get("severity"), "low")))
    lines = [
        f"## {name}",
        "",
        "| Package | Version | Severity | Advisory | Fix available |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        _table_row([
            _cell(_value(finding.get("package"), "—")),
            _cell(_value(finding.get("version"), "—")),
            _cell(_value(finding.get("severity"), "low")),
            _cell(_value(finding.get("advisory"), "—")),
            _cell(_value(finding.get("fix_available"), "—")),
        ])
        for finding in findings
    )
    return lines


def _deps_licence_lines(licences: Any) -> list[str]:
    rows = _dict_list(licences)
    if not rows:
        return ["_licences: no drift_"]
    lines = [
        "## Licences",
        "",
        "| Status | Package | Baseline | Current |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        _table_row([
            _cell(_value(row.get("status"), "—")),
            _cell(_value(row.get("package"), "—")),
            _cell(_value(row.get("baseline"), "—")),
            _cell(_value(row.get("current"), "—")),
        ])
        for row in rows
    )
    return lines


def render_scan_finding_issue(
    *,
    problem: str | None = None,
    location: str | None = None,
    severity: str | None = None,
    justification: str | None = None,
    evidence: str | None = None,
    suggested_fix: str | None = None,
    source: str | None = None,
    regression_of: int | None = None,
) -> str:
    """Render the deterministic issue body for a scan finding.

    Shared by ``regression`` and ``review-all-day`` — the body carries the
    problem statement, ``path:line`` location, severity + justification, fenced
    evidence, and suggested fix, plus a provenance marker. When ``regression_of``
    is supplied the grep-able ``regression-of: #N`` cross-reference is the body's
    literal last line.
    """
    lines = [
        "## Problem",
        "",
        _value(problem, "A scan finding was reported without a problem statement."),
        "",
        "## Location",
        "",
        f"`{_value(location, 'unknown')}`",
        "",
        "## Severity",
        "",
        f"{_value(severity, 'minor')} — {_value(justification, 'no justification recorded')}",
        "",
        "## Evidence",
        "",
        "```",
        _value(evidence, "none provided"),
        "```",
        "",
        "## Suggested fix",
        "",
        _value(suggested_fix, "none proposed"),
        "",
        f"Found by keel {_value(source, 'scan')}.",
        "",
        f"<!-- {SCAN_FINDING_MARKER} -->",
    ]
    if isinstance(regression_of, int) and not isinstance(regression_of, bool):
        lines.extend(["", f"regression-of: #{regression_of}"])
    return "\n".join(lines) + "\n"


def render_triage_audit(
    *,
    issue: int | None = None,
    role: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    tier: int | str | None = None,
    rationale: str | None = None,
    run_id: str | None = None,
) -> str:
    """Render the deterministic, label-only triage audit comment.

    One comment per triaged issue: the applied role / priority / status labels and
    risk tier on one line, then the classifier's rationale. When ``run_id`` is
    supplied an idempotent re-post edits the existing comment in place.
    """
    labels = " · ".join([
        f"role: {_value(role, 'unassigned')}",
        f"priority: {_value(priority, 'unset')}",
        f"status: {_value(status, 'unset')}",
        f"tier: {_scalar(tier, 'n/a')}",
    ])
    lines = [
        TRIAGE_AUDIT_MARKER,
        f"keel triage — {_issue_ref(issue)}: {labels}",
        "",
        _value(rationale, "Classified from the existing label set."),
    ]
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
    # Optimize deduplication: O(N) using C-level dict.fromkeys instead of O(N^2) list lookups
    return list(dict.fromkeys(
        area
        for reviewer in reviewers
        for area in _string_list(reviewer.get("clean_areas"))
    ))


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
