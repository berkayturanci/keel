"""The ``jury`` built-in gate — run the ai-jury CLI on the diff at s8.

keel does **not** depend on ai-jury. If the ``jury`` CLI is on PATH, this gate runs it on
the change's diff and maps its findings into keel :class:`~keel.findings.Finding`s. Without
the ``jury`` binary the s8 run is a no-op (reported ``SKIPPED``, and blocking when ``jury`` is
the only gate planned), but a tier-3 merge still requires a
``jury-verdict`` unless the run passes ``--no-jury``; it relaxes to advisory only when a
posted verdict (or ``--jury-vendors``) reports fewer than 2 vendors. That is the default
policy: off a jury-panel tier, ``team.jury.mode: advisory`` or ``--jury-advisory`` never
requires the verdict and ``team.jury.min_vendors`` may raise the 2; on a tier whose review is
the jury panel, no flag or short panel relaxes it, and only a probe that finds the panel
unstaffable turns that tier's jury off, under ``team.jury.on_unavailable: fallback`` (the
default). That requirement is not decided here: :func:`keel.ship.resolve_jury` resolves the
mode, and :func:`keel.evidence.required_items` demands the verdict from it. Parsing is pure and
unit-tested; the subprocess is behind the injectable ``_run`` seam.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any

from . import artifacts, evidence
from .findings import Finding
from .model import DEFAULT_JURY_TIMEOUT_S
from .runner import CommandResult, run_argv

#: ai-jury severities → keel severities (unknown ⇒ ``minor``).
_SEVERITY = {
    "critical": "critical",
    "blocker": "critical",
    "major": "major",
    "minor": "minor",
    "nit": "nit",
    "info": "nit",
    "note": "nit",
}

MAX_DIFF_BYTES = 1_000_000


def map_severity(severity: str) -> str:
    """Map an ai-jury severity onto a keel severity (default ``minor``)."""
    return _SEVERITY.get((severity or "").strip().lower(), "minor")


def parse_report(data: dict | str) -> list[Finding] | None:
    """Map an ai-jury JSON report into Findings, or ``None`` if it is not a report.

    The ``None`` return is the point: it separates *"the panel reviewed the diff and
    found nothing"* from *"this output is not a verdict at all"*, which
    :func:`parse_findings` collapses into the same empty list. Only the caller that
    decides whether a gate passed needs that distinction — see :func:`run_gate`.

    Tolerates trailing non-JSON. :func:`keel.runner.run_argv` hands back
    ``stdout + stderr`` concatenated, and ai-jury logs its progress to stderr, so a
    real report is followed by ``[jury] …`` lines. A strict ``json.loads`` rejects the
    whole thing and silently loses every finding.
    """
    if isinstance(data, str):
        try:
            data, _end = json.JSONDecoder().raw_decode(data.lstrip())
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or "findings" not in data:
        return None
    return _findings_from(data)


def parse_findings(data: dict | str) -> list[Finding]:
    """Map an ai-jury JSON report (dict or raw string) into keel Findings.

    Unparseable input yields ``[]``. Use :func:`parse_report` when the difference
    between "no findings" and "no report" matters.
    """
    return parse_report(data) or []


def _findings_from(data: dict) -> list[Finding]:
    out: list[Finding] = []
    for f in data.get("findings") or []:
        path = f.get("file")
        line = f.get("line")
        line = line if isinstance(line, int) else None
        out.append(
            Finding(
                severity=map_severity(f.get("severity", "")),
                message=f.get("claim") or "(jury finding)",
                source=f"jury:{f.get('reviewer') or 'consensus'}",
                path=path,
                line=line,
                anchorable=bool(path) and line is not None,
            )
        )
    return out


def _kw(_run):
    return {"_run": _run} if _run is not None else {}


def available(*, cwd: str | None = None, _run=None) -> bool:
    """True if the ``jury`` CLI is callable."""
    return run_argv(["jury", "--version"], cwd=cwd, timeout=30, **_kw(_run)).ok


def _incomplete_finding(result: CommandResult, *, timeout: int, severity: str = "nit") -> Finding:
    """Record that the jury CLI ran but produced no verdict.

    A timeout, or a nonzero exit whose output carries no parseable findings, means the
    panel never reached a conclusion. That is emphatically **not** a clean pass — it is
    the *absence* of a review — so in gating mode it fails closed exactly as an oversize
    diff does. The timeout case is named apart from a crash so the operator can tell a
    slow panel from a broken one.
    """
    if result.timed_out:
        detail = (
            f"timed out after {timeout}s; no verdict was produced. Raise "
            "knobs.jury_timeout_s if the panel legitimately needs longer"
        )
    else:
        detail = f"exited {result.code} without a parseable verdict; the panel did not complete"
    return Finding(
        severity=severity,
        message=f"jury run incomplete: the jury CLI {detail}.",
        source="jury:incomplete-run",
        path=None,
        line=None,
        anchorable=False,
    )


def _unreadable_diff_finding(*, severity: str = "minor") -> Finding:
    """Record that the diff itself could not be read, so no review was possible."""
    return Finding(
        severity=severity,
        message=(
            "jury could not run: the diff could not be read from git (is the base "
            "branch fetched locally? a shallow or single-branch clone cannot "
            "resolve base...HEAD). No review was performed."
        ),
        source="jury:unreadable-diff",
        path=None,
        line=None,
        anchorable=False,
    )


def _oversize_finding(size: int, *, severity: str = "nit") -> Finding:
    """Record that the jury gate skipped an oversize diff.

    Advisory jury mode keeps the finding non-blocking (``nit``). Gating jury mode
    escalates it to ``major`` so an oversize diff cannot bypass the blocking
    cross-vendor review gate.
    """
    return Finding(
        severity=severity,
        message=(
            f"jury skipped: diff is {size} bytes, over the {MAX_DIFF_BYTES}-byte "
            "limit (ai-jury large-diff chunking not applied)"
        ),
        source="jury:skipped-oversize",
        path=None,
        line=None,
        anchorable=False,
    )


#: The source of the finding a jury gate that **could not run** reports (#1369): no ``jury``
#: CLI on the host, or an empty diff. It judged nothing, so it must never read as ``ok``.
#: :func:`could_not_run` reads it back; :func:`keel.gates.lone_jury_cannot_judge` turns it
#: into a blocking outcome when the jury is the only gate planned.
NOT_RUN_SOURCE = "jury:not-run"

#: Why the jury could not run, as the finding says it.
NOT_RUN_NO_CLI = "the jury CLI is not available (`jury --version` failed; install ai-jury)"
NOT_RUN_EMPTY_DIFF = "the diff against the base branch is empty"


def _not_run_finding(reason: str) -> Finding:
    """Record that the jury gate judged nothing, and why (#1369).

    ``nit``: beside another gate that judges, a jury that could not run stays the
    documented s8 no-op and does not hold the merge. It is reported rather than silent,
    and it is what marks the outcome ``SKIPPED`` rather than ``ok``.
    """
    return Finding(
        severity="nit",
        message=f"jury did not run: {reason}; nothing was judged.",
        source=NOT_RUN_SOURCE,
        path=None,
        line=None,
        anchorable=False,
    )


#: The source of the finding a jury gate reports for the panel's **consensus** (#1436).
CONSENSUS_SOURCE = "jury:consensus"


def panel_consensus(data: dict | str) -> str | None:
    """The panel's consensus in an ai-jury report, in keel's vocabulary, or ``None``.

    The consensus is the **chair record's** ``verdict`` in the report's ``reviewers``
    array (``role: chair``). ai-jury writes it from the chair's synthesis headline, or
    from the panel vote when the run is configured with ``decision: vote``
    (``ai_jury.ballots.chair_verdict``), so one field carries both. It is read through
    :func:`map_verdict`, so ``APPROVE`` / ``READY`` arrive as ``LGTM``.

    A ballot report with **no chair record** — the synthesis failed — has no consensus, and
    reads ``ABSTAIN``, which is what :func:`jury_verdict` posts for it too. ``None`` means
    the report carries **no ballots at all**: an ai-jury from before report schema 1.1,
    which has no ``reviewers`` array, or one whose ballots are malformed.
    """
    try:
        panel = parse_panel(data)
    except JuryReportError:
        return None
    if panel is None:
        return None
    return panel.chair.verdict if panel.chair is not None else "ABSTAIN"


def _consensus_finding(data: dict | str, *, gating: bool) -> Finding | None:
    """The finding a jury gate reports when the panel's consensus does not approve (#1436).

    Read against :data:`keel.evidence.APPROVING_VERDICTS` by the same reader the
    evidence gate applies to the posted ``AI Jury verdict:`` line
    (:func:`keel.evidence.jury_verdict_approves`), so the gates-pass and the evidence
    gate cannot disagree about what the panel said. ``major`` in gating mode — the gate
    fails — and ``minor`` in advisory mode, which reports a rejection and never gates on
    one, as the evidence gate does.

    **A report with no consensus fails closed in gating mode, and is silent in advisory
    mode.** A gating jury's verdict comment has to approve before ``keel merge`` will land
    the change, and a report that states no consensus cannot truthfully produce one; a
    gates-pass for it would certify a review that never concluded. Advisory mode keeps
    today's behaviour: the severity rule alone, with nothing added.
    """
    consensus = panel_consensus(data)
    if consensus is None:
        if not gating:
            return None
        return Finding(
            severity="major",
            message=(
                "jury report states no panel consensus: it carries no readable `reviewers` "
                "ballots with a chair record (ai-jury before report schema 1.1, or a "
                "malformed report). A gating jury must conclude; upgrade ai-jury."
            ),
            source=CONSENSUS_SOURCE,
            path=None,
            line=None,
            anchorable=False,
        )
    line = f"AI Jury verdict: {consensus}."
    if evidence.jury_verdict_approves(line):
        return None
    token = evidence.jury_verdict_token(line) or consensus
    return Finding(
        severity="major" if gating else "minor",
        message=f"jury consensus is {token}, not an approval.",
        source=CONSENSUS_SOURCE,
        path=None,
        line=None,
        anchorable=False,
    )


#: The source of the findings a jury gate carries over from a reused panel (#1437).
REUSED_SOURCE = "jury:reused"


def reuse_posted_verdict(body: str, *, gating: bool) -> tuple[bool, list[Finding]] | None:
    """The jury gate's outcome read off the panel already posted for the head (#1437).

    ``body`` is the standing ``keel.jury-verdict.v1`` comment for the head
    (:func:`keel.evidence.standing_jury_verdict`). The gate is judged by the two rules a
    panel keel ran is judged by, so reusing one can never be kinder than running it:

    * **the severity rule** — each ``<severity>: <message>`` item of the comment's findings
      summary (:func:`keel.artifacts.jury_verdict_summary`, the verified findings
      :func:`jury_verdict` posts) becomes a finding, and a critical or major one fails;
    * **the consensus rule** (#1436) — an ``AI Jury verdict:`` line that does not approve,
      or no readable line at all, is a ``major`` finding in gating mode and a ``minor`` one
      in advisory mode, the same as :func:`_consensus_finding` for a report.

    ``(ok, findings)``, or ``None`` when the comment's summary cannot be read — a body keel
    did not render. ``None`` means *do not reuse*: the caller convenes the panel as it
    always did, so an unreadable comment can never stand in for a run.
    """
    summary = artifacts.jury_verdict_summary(body)
    if summary is None:
        return None
    findings: list[Finding] = []
    for item in summary:
        label, sep, _message = item.partition(":")
        findings.append(
            Finding(
                # An item with no `<severity>:` label is read as unknown, which
                # map_severity already maps to `minor`.
                severity=map_severity(label if sep else ""),
                message=f"posted jury finding — {item}",
                source=REUSED_SOURCE,
                path=None,
                line=None,
                anchorable=False,
            )
        )
    if not evidence.jury_verdict_approves(body):
        token = evidence.jury_verdict_token(body)
        findings.append(
            Finding(
                severity="major" if gating else "minor",
                message=(
                    f"posted jury consensus is {token}, not an approval."
                    if token
                    else "posted jury verdict has no readable AI Jury verdict line."
                ),
                source=CONSENSUS_SOURCE,
                path=None,
                line=None,
                anchorable=False,
            )
        )
    blocked = any(f.severity in ("critical", "major") for f in findings)
    return (not blocked), findings


def could_not_run(findings) -> bool:
    """Did this jury gate result come back without running (no CLI, or an empty diff)?"""
    return any(f.source == NOT_RUN_SOURCE for f in findings)


def run_gate(
    diff_text: str,
    *,
    cwd: str | None = None,
    mode: str = "advisory",
    timeout: int = DEFAULT_JURY_TIMEOUT_S,
    _run=None,
) -> tuple[bool, list[Finding], bool]:
    """Run ``jury`` on ``diff_text`` and map its findings.

    Returns ``(ok, findings, timed_out)``. ``ok`` is False when a finding blocks
    (critical/major) or when the run produced no verdict at all in gating mode — and, in
    gating mode, when the panel's **consensus** does not approve or the report states
    none (#1436, :func:`_consensus_finding`). Both rules hold at once: a verified major
    still blocks a panel that approved, and a panel that requested changes over minors
    alone, or abstained, no longer passes because no finding was severe.
    No-op when there is no diff or the ``jury`` CLI is not installed — keel does not
    depend on ai-jury, so an absent CLI is a legitimate no-op *for this run*, distinct
    from a run that started and did not finish. A no-op is not a pass: it returns one
    ``nit`` finding from :data:`NOT_RUN_SOURCE` saying why nothing was judged, so the
    outcome reads ``SKIPPED`` and a plan with no other gate blocks (#1369). It waives
    nothing downstream: a gating jury's ``jury-verdict`` is still required at merge (see
    the module docstring).

    Three ways a run can end without a review, all handled alike — gating fails closed
    with a blocking ``major``, advisory surfaces a ``minor``:

    * the diff is oversize and was never submitted,
    * the CLI was killed by ``timeout``,
    * the CLI returned no parseable verdict, whatever its exit code.

    The last used to report ``(True, [])``: :func:`parse_findings` yields ``[]`` for
    unparseable output, so ``blocked`` came out False and a hung, crashed, or
    unreadable panel read as a clean pass. The test is deliberately *"did we parse a
    verdict"* rather than *"was the exit code zero"* — ai-jury exits nonzero to signal
    "request changes", which is a completed review whose findings must be honoured,
    while an exit of zero carrying unreadable output is not a review at all.
    """
    if diff_text is None:
        # The diff could not be read (git failed). That is not "nothing to review":
        # passing here would silently remove the review gate from the merge decision,
        # which is the same fail-open the verdict check below exists to prevent.
        gating = mode == "gating"
        return (
            (not gating),
            [_unreadable_diff_finding(severity="major" if gating else "minor")],
            False,
        )
    if not diff_text:
        return True, [_not_run_finding(NOT_RUN_EMPTY_DIFF)], False
    size = len(diff_text.encode("utf-8"))
    if size > MAX_DIFF_BYTES:
        if mode == "gating":
            return False, [_oversize_finding(size, severity="major")], False
        return True, [_oversize_finding(size)], False
    if not available(cwd=cwd, _run=_run):
        return True, [_not_run_finding(NOT_RUN_NO_CLI)], False
    fd, path = tempfile.mkstemp(suffix=".diff")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(diff_text)
        result = run_argv(
            ["jury", "--format", "json", "--diff-file", path], cwd=cwd, timeout=timeout, **_kw(_run)
        )
    finally:
        os.unlink(path)
    # stdout alone: ai-jury logs its progress (`[jury] …`) to stderr, and reading the
    # concatenation is what made every report unparseable (#624). `parse_report` still
    # tolerates trailing non-JSON, for a vendor that also chats on stdout.
    report = parse_report(result.stdout)
    if report is None:
        gating = mode == "gating"
        incomplete = _incomplete_finding(
            result, timeout=timeout, severity="major" if gating else "minor"
        )
        # timed_out rides along so the outcome renders as TIMEOUT rather than FAIL,
        # the distinction #622 established for command gates.
        return (not gating), [incomplete], result.timed_out
    consensus = _consensus_finding(result.stdout, gating=mode == "gating")
    if consensus is not None:
        report = [*report, consensus]
    blocked = any(f.severity in ("critical", "major") for f in report)
    return (not blocked), report, False


# --------------------------------------------------------------------------- #
# Per-reviewer ballots (#1015) — the panel *as* the review, not beside it.
# --------------------------------------------------------------------------- #

#: The ``role`` ai-jury stamps on the chair's entry in the report's ``reviewers``
#: array. The chair is the consensus record, not a panelist ballot, so it renders
#: as the jury verdict rather than as one more review verdict.
CHAIR_ROLE = "chair"

#: ai-jury ballot tokens → keel verdict vocabulary. ai-jury emits one machine
#: token per ballot (``REQUEST_CHANGES``, never ``REQUEST CHANGES``) in either the
#: code or the ``--issue`` vocabulary; keel's verdicts are ``LGTM`` /
#: ``REQUEST_CHANGES`` / ``COMMENT`` / ``ABSTAIN``. An unknown token is carried
#: through verbatim rather than folded into ``LGTM``: inventing an approval for a
#: stance keel does not recognise is the one mapping error that cannot be undone.
_VERDICT = {
    "APPROVE": "LGTM",
    "READY": "LGTM",
    "REQUEST_CHANGES": "REQUEST_CHANGES",
    "NEEDS_INFO": "REQUEST_CHANGES",
    "COMMENT": "COMMENT",
    "UNCLEAR": "COMMENT",
    "ABSTAIN": "ABSTAIN",
    "NO_QUORUM": "ABSTAIN",
}

#: Files a ballot's scope line names before it starts counting instead.
_SCOPE_FILES = 8

#: The verification status ai-jury stamps on a consensus group the verification
#: round upheld. Only these findings gate: an unsupported or unverified claim is
#: reported, never merged against.
VERIFIED_STATUS = "verified"


class JuryReportError(ValueError):
    """Raised when an ai-jury report cannot be read as a panel of ballots."""


def map_verdict(verdict: str) -> str:
    """Map an ai-jury ballot token onto keel's verdict vocabulary."""
    token = (verdict or "").strip().upper().replace(" ", "_").replace("-", "_")
    if not token:
        return "ABSTAIN"
    return _VERDICT.get(token, token)


@dataclass(frozen=True)
class Ballot:
    """One panelist's own stance, with the provenance that makes it evidence."""

    reviewer: str
    verdict: str
    vendor: str | None = None
    model: str | None = None
    verified_count: int = 0
    round1_ok: bool = True
    findings: tuple[dict[str, Any], ...] = ()
    scope: str | None = None
    testing: str | None = None
    counts_as_review: bool | None = None
    scope_substantive: bool | None = None
    abstention_cause: str | None = None

    def as_review(self) -> dict[str, Any]:
        """This ballot in the ``keel review --reviews`` bundle shape."""
        return {
            "reviewer": self.reviewer,
            "verdict": self.verdict,
            "scope": ballot_scope(self),
            "findings": [dict(finding) for finding in self.findings],
            "testing": ballot_testing(self),
            "vendor": self.vendor,
            "model": self.model,
        }


def _review_ballots(ballots: tuple[Ballot, ...]) -> tuple[Ballot, ...]:
    """Ballots that count as reviews — the one definition :class:`Panel` consumes."""
    return tuple(ballot for ballot in ballots if ballot_is_review(ballot))


@dataclass(frozen=True)
class Panel:
    """A parsed ai-jury panel: the panelist ballots and the chair's consensus."""

    ballots: tuple[Ballot, ...] = ()
    chair: Ballot | None = None
    verified: tuple[dict[str, Any], ...] = ()

    @property
    def size(self) -> int:
        """Reviews this panel produced — the reviewer count the evidence gate sizes.

        Aligns with ai-jury's ``is_review``: an abstention, an empty ballot, or a
        ``counts_as_review: false`` record is not a review and does not inflate
        ``panelists`` / ``jury_panel_size``. :func:`parse_panel` already drops
        those from :attr:`ballots`; this property re-applies the same predicate
        so a hand-built panel cannot disagree with the posting path.
        """
        return len(_review_ballots(self.ballots))

    @property
    def vendors(self) -> tuple[str, ...]:
        """Distinct declared vendors across the reviews, in panel order.

        Lower-cased and de-duplicated exactly as :func:`keel.evidence.distinct_vendor_check`
        reads the posted ``vendor:`` lines, so the count declared on the jury verdict and
        the count the evidence gate recomputes from the verdicts cannot disagree.
        Abstaining seats are not reviews and do not contribute a vendor.
        """
        seen: list[str] = []
        for ballot in _review_ballots(self.ballots):
            vendor = (ballot.vendor or "").strip().lower()
            if vendor and vendor not in seen:
                seen.append(vendor)
        return tuple(seen)

    def reviews(self) -> tuple[dict[str, Any], ...]:
        """Review ballots in the ``--reviews`` bundle shape.

        Non-reviews are omitted: posting them as head-pinned ``review-verdict-*``
        evidence is the defect this mapping exists to close.
        """
        return tuple(ballot.as_review() for ballot in _review_ballots(self.ballots))


def _finding_record(raw: Any) -> dict[str, Any] | None:
    """One ai-jury finding in keel's finding shape (``file``→``path``, ``claim``→``message``)."""
    if not isinstance(raw, dict):
        return None
    line = raw.get("line")
    return {
        "severity": map_severity(raw.get("severity", "")),
        "path": raw.get("file") or None,
        "line": line if isinstance(line, int) else None,
        "message": raw.get("claim") or "(jury finding)",
    }


def _ballot_findings(raw: Any, findings: list[Any]) -> tuple[dict[str, Any], ...]:
    """Resolve a ballot's ``findings`` index list against the report's findings array.

    Out-of-range and non-integer indexes are dropped rather than raising: the
    ballot's stance is the evidence, and a report whose indexes do not line up
    must still produce a verdict that says so with the findings it *can* resolve.
    """
    if not isinstance(raw, list):
        return ()
    records: list[dict[str, Any]] = []
    for index in raw:
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if not 0 <= index < len(findings):
            continue
        record = _finding_record(findings[index])
        if record is not None:
            records.append(record)
    return tuple(records)


def _text_field(raw: dict[str, Any], key: str) -> str | None:
    """A non-empty string field, or ``None`` when absent / blank / the wrong type."""
    value = raw.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _bool_field(raw: dict[str, Any], key: str) -> bool | None:
    """A JSON boolean field, or ``None`` when absent or not a bool.

    Integers are refused: ``1``/``0`` are not the schema ≥1.2 flags, and treating
    them as booleans would let a malformed report opt a ballot into the review
    count.
    """
    value = raw.get(key)
    if isinstance(value, bool):
        return value
    return None


def _ballot(raw: Any, findings: list[Any], *, position: int) -> Ballot:
    if not isinstance(raw, dict):
        raise JuryReportError(f"jury report reviewer #{position} must be a JSON object")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise JuryReportError(f"jury report reviewer #{position} requires a non-empty 'name'")
    vendor = raw.get("vendor")
    model = raw.get("model")
    verified = raw.get("verified_count")
    return Ballot(
        reviewer=name.strip(),
        verdict=map_verdict(raw.get("verdict", "")),
        vendor=vendor.strip() if isinstance(vendor, str) and vendor.strip() else None,
        model=model.strip() if isinstance(model, str) and model.strip() else None,
        verified_count=verified
        if isinstance(verified, int) and not isinstance(verified, bool)
        else 0,
        round1_ok=bool(raw.get("round1_ok", True)),
        findings=_ballot_findings(raw.get("findings"), findings),
        scope=_text_field(raw, "scope"),
        testing=_text_field(raw, "testing"),
        counts_as_review=_bool_field(raw, "counts_as_review"),
        scope_substantive=_bool_field(raw, "scope_substantive"),
        abstention_cause=_text_field(raw, "abstention_cause"),
    )


def _verified_records(data: dict) -> tuple[dict[str, Any], ...]:
    """Consensus-group representatives the verification round upheld.

    These are the findings that gate. ai-jury verifies a consensus group and
    stamps ``verification_status``; keel's own rule — critical/major block —
    applies to the *upheld* ones only, so a claim the panel could not support
    never holds a merge.
    """
    records: list[dict[str, Any]] = []
    for group in data.get("consensus") or []:
        if not isinstance(group, dict):
            continue
        if (group.get("verification_status") or "") != VERIFIED_STATUS:
            continue
        record = _finding_record(group.get("representative"))
        if record is not None:
            reviewers = group.get("reviewers")
            record["reviewers"] = (
                [name for name in reviewers if isinstance(name, str)]
                if isinstance(reviewers, list)
                else []
            )
            records.append(record)
    return tuple(records)


def parse_panel(data: dict | str) -> Panel | None:
    """Parse an ai-jury JSON report into a :class:`Panel`, or ``None``.

    ``None`` means *this is not a report carrying per-reviewer ballots* — an
    unparseable document, or a pre-schema-1.1 report with no ``reviewers`` array.
    The caller turns that into an actionable error (upgrade ai-jury, or supply a
    ``--reviews`` bundle); it is deliberately not an exception, because "not a
    ballot report" is the same question :func:`parse_report` answers for findings.

    A report that *does* carry ballots but carries them malformed raises
    :class:`JuryReportError`: dropping a panelist would silently post fewer
    verdicts than the panel produced, which is the one failure this whole path
    exists to prevent.

    Only ballots that :func:`ballot_is_review` accepts enter :attr:`Panel.ballots`.
    An ``ABSTAIN``, a ``counts_as_review: false`` record, or an older empty
    ballot is parsed and then dropped, so it cannot inflate ``panel.size`` or
    become a posted ``review-verdict-*``.
    """
    if isinstance(data, str):
        try:
            data, _end = json.JSONDecoder().raw_decode(data.lstrip())
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    raw_reviewers = data.get("reviewers")
    if not isinstance(raw_reviewers, list):
        return None
    findings = list(data.get("findings") or [])
    ballots: list[Ballot] = []
    chair: Ballot | None = None
    for position, raw in enumerate(raw_reviewers, start=1):
        ballot = _ballot(raw, findings, position=position)
        if isinstance(raw, dict) and (raw.get("role") or "") == CHAIR_ROLE:
            chair = ballot
            continue
        if ballot_is_review(ballot):
            ballots.append(ballot)
    return Panel(ballots=tuple(ballots), chair=chair, verified=_verified_records(data))


def _finding_paths(ballot: Ballot) -> list[str]:
    """Distinct file paths this ballot's own findings named, in first-seen order."""
    files: list[str] = []
    for finding in ballot.findings:
        path = finding.get("path")
        if isinstance(path, str) and path.strip() and path not in files:
            files.append(path.strip())
    return files


def ballot_is_review(ballot: Ballot) -> bool:
    """Whether this panelist ballot counts as a review (ai-jury ``is_review``).

    A review is a panelist whose scope is substantive and whose verdict is not
    ``ABSTAIN``. The chair is split off before this predicate runs.

    The flags a schema ≥1.2 report declares — ``counts_as_review`` and
    ``scope_substantive`` — can only ever **remove** a ballot here, never admit
    one that carries nothing. ai-jury derives ``counts_as_review`` from
    ``scope_substantive``, which is itself derived from the ``scope`` prose, so a
    record claiming ``counts_as_review: true`` with no ``scope`` and no finding
    is not a clean review it produced; it is internally inconsistent, and
    admitting it means keel writing the substance the report failed to supply.
    That is the escape hatch #1150 is about, so the ambiguous record fails closed
    like every other one.

    What is left is the fact the scope line reads: a ballot counts when the
    report gave prose to post or a path to name. Older reports carry no flags and
    are decided by that same fact, so a schema-1.1 empty ``APPROVE`` is dropped
    rather than dressed up.
    """
    if ballot.verdict == "ABSTAIN":
        return False
    if ballot.counts_as_review is False or ballot.scope_substantive is False:
        return False
    return bool(ballot.scope) or bool(_finding_paths(ballot))


def _abstention_scope(ballot: Ballot) -> str:
    """An explicitly anchorless scope: no ``Checked …``, no path, no backtick."""
    cause = (ballot.abstention_cause or "").replace("_", " ")
    if cause:
        return f"ai-jury panelist {ballot.reviewer} did not review ({cause})."
    return f"ai-jury panelist {ballot.reviewer} did not review."


def ballot_scope(ballot: Ballot) -> str:
    """The scope line keel renders for a panelist ballot.

    A ballot that is not a review never gets a substance-passing opener: keel
    used to always start with ``Checked the changed-file diff…``, which is its
    own :func:`keel.evidence.verdict_substance` escape hatch, so an ``ABSTAIN``
    still passed the gate by construction.

    Every remaining branch renders something the report actually supplied. A
    schema ≥1.2 ballot carries its own ``scope`` and that prose is posted as
    written — including the clean review that read the diff and found nothing,
    which ai-jury describes itself rather than leaving keel to. Otherwise the
    ballot named paths, and the ``checked …`` line is built from them. There is
    no third case: :func:`ballot_is_review` admits a ballot only when one of
    those two is true, so this function never has to invent a scope for a ballot
    it was told to post.
    """
    if not ballot_is_review(ballot):
        return _abstention_scope(ballot)
    if ballot.scope:
        return ballot.scope
    # Non-empty: `ballot_is_review` accepted this ballot, and with no `scope`
    # prose the only way it could have is by naming a path.
    files = _finding_paths(ballot)
    opening = f"Checked the changed-file diff as ai-jury panelist {ballot.reviewer}"
    listed = ", ".join(files[:_SCOPE_FILES])
    more = len(files) - _SCOPE_FILES
    suffix = f" (+{more} more)" if more > 0 else ""
    return f"{opening}; named {len(files)} file(s): {listed}{suffix}."


def ballot_testing(ballot: Ballot) -> str:
    """The testing line keel renders for a panelist ballot.

    Schema ≥1.2 reports carry their own ``testing`` prose; that is preferred
    when present. Otherwise the panel's verification round *is* the ballot's
    testing note: it is the only check ai-jury performs on a reviewer's claims,
    and a ballot whose claims were never upheld must say so rather than borrow
    the PR's own testing section.
    """
    if ballot.testing:
        return ballot.testing
    if ballot.verified_count > 0:
        note = (
            f"ai-jury verification upheld {ballot.verified_count} consensus "
            "group(s) this panelist joined."
        )
    else:
        note = "ai-jury verification upheld no consensus group from this panelist."
    if not ballot.round1_ok:
        return f"The panelist's adapter reported a failed run; its output was still read. {note}"
    return note


def verified_findings(panel: Panel) -> list[Finding]:
    """Verified consensus findings as keel :class:`~keel.findings.Finding`s.

    This is the s9 input: ``critical``/``major`` block, ``minor`` is a gated
    suggestion, ``nit`` is advisory — the same mapping a host reviewer's findings
    get, which is the whole point of the panel being the review rather than a
    second opinion beside it.
    """
    out: list[Finding] = []
    for record in panel.verified:
        reviewers = record.get("reviewers") or []
        source = f"jury:{reviewers[0]}" if reviewers else "jury:consensus"
        path = record.get("path")
        line = record.get("line")
        out.append(
            Finding(
                severity=record["severity"],
                message=record["message"],
                source=source,
                path=path,
                line=line,
                anchorable=bool(path) and isinstance(line, int),
            )
        )
    return out


def jury_verdict(panel: Panel) -> dict[str, Any]:
    """The ``render_jury_verdict`` arguments for a parsed panel.

    The chair's ballot is the consensus record — that is what the jury verdict
    comment has always been — and the panel's own size and vendor count ride
    along on it, because the posted verdict is the only channel by which either
    reaches a hosted evidence check (see :func:`keel.artifacts.render_jury_verdict`).
    """
    chair = panel.chair
    summary = [f"{record['severity']}: {record['message']}" for record in panel.verified]
    return {
        "verdict": chair.verdict if chair is not None else "ABSTAIN",
        "participants": [
            f"{ballot.reviewer} ({ballot.vendor})" if ballot.vendor else ballot.reviewer
            for ballot in _review_ballots(panel.ballots)
        ],
        "participating_vendors": len(panel.vendors),
        "panelists": panel.size,
        "findings_summary": summary,
        "remaining_risks": None if summary else "none identified",
    }
