"""The ``jury`` built-in gate — run the ai-jury CLI on the diff (optional, fail-soft).

keel does **not** depend on ai-jury. If the ``jury`` CLI is on PATH, this gate runs it on
the change's diff and maps its findings into keel :class:`~keel.findings.Finding`s; if it is
absent, the gate is a fail-soft no-op (the flow runs with or without jury). Parsing is pure
and unit-tested; the subprocess is behind the injectable ``_run`` seam.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any

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
    (critical/major) or when the run produced no verdict at all in gating mode.
    Fail-soft no-op when there is no diff or the ``jury`` CLI is not installed — keel
    does not depend on ai-jury, so an absent CLI is a legitimate no-op, distinct from
    a run that started and did not finish.

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
        return True, [], False
    size = len(diff_text.encode("utf-8"))
    if size > MAX_DIFF_BYTES:
        if mode == "gating":
            return False, [_oversize_finding(size, severity="major")], False
        return True, [_oversize_finding(size)], False
    if not available(cwd=cwd, _run=_run):
        return True, [], False
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
