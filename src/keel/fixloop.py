"""s9 fixloop — who fixes a review finding, and the brief they are handed (#1016).

``ship.md`` s9 said *"aggregate findings → hand to the implementer → fix → push"*. When
the implementer was a delegate there was nothing behind the arrow: no command, no prompt
shape, no ownership rule. In the live run two blocking majors sat with nobody assigned
and the host — the orchestrator whose quota the delegation existed to protect — wrote the
fix itself.

This module is the pure half of the answer:

* :func:`render_brief` — the deterministic fix brief. Findings grouped by severity with
  ``file:line`` anchors and the reviewer's reproduction, the round and its budget, and the
  narrowed-re-review sentence the next reviewer will be held to. Byte-stable for identical
  input, which is what lets ``keel fixloop brief`` be snapshot-tested and lets two hosts
  hand the same delegate the same words.
* :func:`resolve_fixer` — the escalation ladder ``implementer → gate → host``, a pure
  function of *(round, provider availability, budget)* and nothing else. Round 1 goes to
  the seat ``knobs.team.fix`` resolved (the implementer, by default); a failed round
  escalates one rung; an unavailable provider is skipped rather than dispatched to; the
  budget (**≤3 rounds**, unchanged) still ends the loop.
* :func:`brief_document` — the JSON ``keel fixloop brief`` prints and the adapter reads,
  including the ``keel delegate run --role fix`` invocation for the resolved seat.

The ladder is deliberately short and deliberately terminal. Each rung is a *different*
opinion — the seat that wrote the change, the seat the project already trusts to gate it,
and the host that is always there — so a duplicate rung is dropped rather than dispatched
twice, and running off the end stays with the last usable fixer instead of inventing one.

Pure and deterministic: no wall-clock, no randomness, no I/O. Severity vocabulary and
ordering come from :mod:`keel.findings`, so the loop-exit rule (``critical``/``major``
block, ``minor`` is a gated suggestion, ``nit`` is advisory) has exactly one definition.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import findings as findings_mod

SCHEMA_VERSION = "keel.fixloop.v1"

#: Marker on the rendered brief, so a brief that reaches a PR is recognisable as one.
BRIEF_MARKER = "<!-- keel.fixloop-brief.v1 -->"

#: Review-fix rounds a run may spend. **Unchanged by #1016** — the ladder decides *who*
#: fixes, never *how often*. Mirrors :data:`keel.runcontrols.DEFAULT_FIXLOOP_CAP`, which
#: caps the same loop from the run-controls side.
DEFAULT_ROUND_BUDGET = 3

#: The ladder, in order. ``implementer`` is the resolved ``assignment.fix`` seat (the
#: provider that implemented, unless ``knobs.team.fix`` names another); ``gate`` is the
#: mandatory second opinion; ``host`` is the agent driving the run.
STAGES = ("implementer", "gate", "host")

#: Why a hop happened.
HOP_REASONS = ("start", "round-failed", "provider-unavailable", "ladder-exhausted")

#: Outcome of :func:`resolve_fixer`, plus the ``no-config`` refusal
#: :func:`no_config_document` renders — a fix round resolved against a team policy that
#: could not be read is a fix round handed to the host by accident.
STATUSES = ("assigned", "budget-exhausted", "no-fixer", "no-config")

#: Default host agent, mirroring :data:`keel.team.HOST_DEFAULT`.
HOST_DEFAULT = "claude"

#: The sentence a narrowed re-reviewer is held to. Quoted verbatim into the brief so the
#: fixer knows exactly how small the next review is, and so the adapter does not improvise
#: a looser one.
NARROWED_INSTRUCTION = (
    "verify only the applied fix in commit {sha}; do not re-review what you already approved"
)

#: Placeholder when the fix commit does not exist yet — it cannot, the fix is unwritten.
UNKNOWN_SHA = "<fix-commit-sha>"

#: Most reviewer-supplied text the brief embeds per field, and the most lines of it. The
#: brief *becomes* a delegate's prompt, so an unbounded finding is an unbounded prompt.
MAX_QUOTED_CHARS = 2000
MAX_QUOTED_LINES = 40

#: Most of a reviewer-supplied value the brief renders inline — a headline, a reviewer id.
MAX_INLINE_CHARS = 200

#: The brief's own trailer keys. A reviewer line that reads as one is rendered as inline
#: code inside the quote, so it cannot be mistaken for the brief's trailer.
_TRAILER_KEYS = ("blocking:", "head:")

_TRUNCATED = "… (truncated)"

#: Indent for a quoted block, lining it up under its `   - label:` bullet.
_QUOTE_INDENT = "     "

_BLOCKING = ("critical", "major")


class FixloopError(ValueError):
    """A fix-loop input that cannot be read: a bad round, or a malformed finding."""


@dataclass(frozen=True)
class Rung:
    """One rung of the escalation ladder: a stage, a seat, and where the seat came from."""

    stage: str
    provider: str
    name: str
    kind: str
    source: str
    model: str | None = None
    effort: str | None = None
    #: ``"implementer"`` when this seat is the fix alias resolved — *you are fixing this
    #: because you implemented it*, which is the whole point of the default.
    alias: str | None = None
    available: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "provider": self.provider,
            "name": self.name,
            "kind": self.kind,
            "model": self.model,
            "effort": self.effort,
            "source": self.source,
            "alias": self.alias,
            "available": self.available,
        }


def contract_as_dict() -> dict[str, Any]:
    """The pure-core fix-loop contract an agentic command publishes."""
    return {
        "schema_version": SCHEMA_VERSION,
        "deterministic": True,
        "stdlib_only": True,
        "round_budget": DEFAULT_ROUND_BUDGET,
        "ladder": list(STAGES),
        "hop_reasons": list(HOP_REASONS),
        "statuses": list(STATUSES),
        "severities": list(findings_mod.SEVERITIES),
        "blocking_severities": list(_BLOCKING),
        "renderer": {"brief": "keel.fixloop.render_brief", "marker": BRIEF_MARKER},
        "dispatch": "keel delegate run --role fix",
        "narrowed_instruction": NARROWED_INSTRUCTION.format(sha=UNKNOWN_SHA),
    }


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _seat_of(raw: Any, *, stage: str, default_source: str) -> Rung | None:
    """A seat record from ``assignment`` -> a :class:`Rung`, or ``None`` when unusable."""
    if not isinstance(raw, Mapping):
        return None
    provider = _text(raw.get("provider"))
    if provider is None:
        return None
    name = _text(raw.get("name")) or provider
    return Rung(
        stage=stage,
        provider=provider,
        name=name,
        kind=_text(raw.get("kind")) or "provider",
        source=_text(raw.get("source")) or default_source,
        model=_text(raw.get("model")),
        effort=_text(raw.get("effort")),
        alias=_text(raw.get("alias")),
    )


def ladder(
    assignment: Mapping[str, Any] | None,
    *,
    host_agent: str = HOST_DEFAULT,
    unavailable: Iterable[str] = (),
) -> tuple[tuple[Rung, ...], list[str]]:
    """The escalation ladder for one assignment, plus the warnings building it produced.

    ``assignment`` is what ``keel plan``/``keel ship --json`` render (see
    :func:`keel.team.resolve_assignment`). Rung 1 is ``assignment.fix``, already resolved
    — the ``implementer`` alias has been substituted for the seat that actually ran, so a
    delegated implementation is handed its own findings back rather than the host's.

    A rung that repeats an earlier rung's ``provider`` is **dropped**, not dispatched: the
    ladder exists to reach a different pair of eyes, and ``gate.provider == fix.provider``
    escalates to the same seat that just failed the round. The comparison is on the
    provider and not the bare name, because ``subagent:opus-reviewer`` and
    ``opus-reviewer`` share a name and are two genuinely different seats.
    """
    blocked = {name for name in unavailable if isinstance(name, str) and name.strip()}
    record = assignment if isinstance(assignment, Mapping) else {}
    first = _seat_of(record.get("fix"), stage="implementer", default_source="team.fix")
    if first is None:
        first = _seat_of(record.get("implementer"), stage="implementer", default_source="host")
    rungs: list[Rung] = []
    warnings: list[str] = []
    if first is not None:
        rungs.append(first)
    gate = _seat_of(record.get("gate"), stage="gate", default_source="team.gate")
    if gate is not None:
        # Compared on `provider`, which is the seat's identity — `subagent:opus-reviewer`
        # and `opus-reviewer` share a `name` and are two different seats (a host subagent
        # and a vendor), so a name comparison would merge two rungs that are not one.
        if any(rung.provider == gate.provider for rung in rungs):
            warnings.append(
                f"team.gate provider {gate.provider!r} is already the fixer; the ladder "
                "skips it — escalating to the seat that just failed the round is not an "
                "escalation"
            )
        else:
            rungs.append(gate)
    host = Rung(stage="host", provider=host_agent, name=host_agent, kind="provider", source="host")
    # No warning for a host that is already seated — on a project with no `knobs.team` the
    # fixer *is* the host, and a warning on every round of the default path is noise. The
    # short ladder is reported where it costs something: the round that wanted to escalate
    # and had nowhere to go says so, in `resolve_fixer`.
    if not any(rung.provider == host.provider for rung in rungs):
        rungs.append(host)
    resolved = tuple(
        Rung(
            stage=rung.stage,
            provider=rung.provider,
            name=rung.name,
            kind=rung.kind,
            source=rung.source,
            model=rung.model,
            effort=rung.effort,
            alias=rung.alias,
            # Either spelling: an operator writes down what the assignment showed them,
            # and a subagent seat shows `provider: subagent:x` beside `name: x`.
            available=rung.name not in blocked and rung.provider not in blocked,
        )
        for rung in rungs
    )
    return resolved, warnings


def _hop(
    *,
    round_number: int,
    previous: Rung | None,
    rung: Rung,
    reason: str,
    used: bool,
) -> dict[str, Any]:
    return {
        "round": round_number,
        "from": previous.stage if previous is not None else None,
        "to": rung.stage,
        "provider": rung.provider,
        "reason": reason,
        "used": used,
    }


def _walk(
    rungs: Sequence[Rung],
    *,
    round_number: int,
) -> tuple[Rung | None, list[dict[str, Any]]]:
    """Walk the ladder to ``round_number``; the trail is what the ledger records."""
    hops: list[dict[str, Any]] = []
    index = 0
    current: Rung | None = None
    for number in range(1, round_number + 1):
        if current is not None:
            if index + 1 >= len(rungs):
                hops.append(
                    _hop(
                        round_number=number,
                        previous=current,
                        rung=current,
                        reason="ladder-exhausted",
                        used=True,
                    )
                )
                continue
            index += 1
        while index < len(rungs) and not rungs[index].available:
            hops.append(
                _hop(
                    round_number=number,
                    previous=current,
                    rung=rungs[index],
                    reason="provider-unavailable",
                    used=False,
                )
            )
            index += 1
        if index >= len(rungs):
            if current is None:
                return None, hops
            hops.append(
                _hop(
                    round_number=number,
                    previous=current,
                    rung=current,
                    reason="ladder-exhausted",
                    used=True,
                )
            )
            index = len(rungs) - 1
            continue
        previous, current = current, rungs[index]
        hops.append(
            _hop(
                round_number=number,
                previous=previous,
                rung=current,
                reason="start" if previous is None else "round-failed",
                used=True,
            )
        )
    return current, hops


def resolve_fixer(
    assignment: Mapping[str, Any] | None,
    *,
    round_number: int = 1,
    unavailable: Iterable[str] = (),
    budget: int = DEFAULT_ROUND_BUDGET,
    host_agent: str = HOST_DEFAULT,
) -> dict[str, Any]:
    """Who fixes round ``round_number`` — a pure function of round, availability, budget.

    Round 1 is the resolved ``fix`` seat. Every failed round escalates exactly one rung,
    an unavailable provider is skipped on the way past, and a round past the last rung
    stays with the last usable fixer rather than fabricating one. Past ``budget`` there is
    no fixer at all: the loop is over and the issue is marked blocked with the outstanding
    findings quoted, which is the s9 rule #1016 did not change.
    """
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 1:
        raise FixloopError(f"round must be a positive integer, got {round_number!r}")
    limit = budget if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0 else 0
    if limit <= 0:
        raise FixloopError(f"budget must be a positive integer, got {budget!r}")
    rungs, warnings = ladder(assignment, host_agent=host_agent, unavailable=unavailable)
    ladder_records = [rung.as_dict() for rung in rungs]
    if round_number > limit:
        return {
            "schema_version": SCHEMA_VERSION,
            "round": round_number,
            "budget": limit,
            "within_budget": False,
            "status": "budget-exhausted",
            "blocked": True,
            "fixer": None,
            "ladder": ladder_records,
            "hops": [],
            "next_action": (
                f"the {limit}-round review-fix budget is spent: mark the issue blocked, "
                "quote the outstanding findings on the PR, and stop — a further round "
                "needs an explicit operator `keel fixloop brief --budget` override"
            ),
            "warnings": warnings,
        }
    # Bounded: one round advances one rung, so no round past `len(rungs) + 1` can reach a
    # rung — or a hop — the round before it did not. Walking the literal round number made
    # `--round 1000000` a million identical `ladder-exhausted` entries in the document an
    # adapter has to read, for no more information than the first one carries.
    walk_to = min(round_number, len(rungs) + 1)
    fixer, hops = _walk(rungs, round_number=walk_to)
    if hops and walk_to < round_number:
        # The trail is clamped; the round it ends on is not. Re-stamp the terminal hop so
        # the record still says which round this resolution is for.
        hops[-1] = {**hops[-1], "round": round_number}
    if fixer is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "round": round_number,
            "budget": limit,
            "within_budget": True,
            "status": "no-fixer",
            "blocked": True,
            "fixer": None,
            "ladder": ladder_records,
            "hops": hops,
            "next_action": (
                "every rung of the ladder is unavailable: mark the issue blocked and say "
                "which providers were refused — a fix nobody can run is not a fix"
            ),
            "warnings": warnings,
        }
    final = hops[-1]
    if final["reason"] == "ladder-exhausted":
        warnings.append(
            f"round {round_number} stays with {fixer.provider!r}: the ladder has no rung "
            "left to escalate to"
        )
    record = fixer.as_dict()
    record["reason"] = final["reason"]
    return {
        "schema_version": SCHEMA_VERSION,
        "round": round_number,
        "budget": limit,
        "within_budget": True,
        "status": "assigned",
        "blocked": False,
        "fixer": record,
        "ladder": ladder_records,
        "hops": hops,
        "next_action": (
            f"dispatch round {round_number} to {fixer.provider!r} with "
            "`keel delegate run --role fix`"
        ),
        "warnings": warnings,
    }


def no_config_document(*, path: str, reason: str, round_number: int = 1) -> dict[str, Any]:
    """The fail-closed answer when the project's team policy cannot be read.

    ``knobs.team.fix`` is *the* input to this command: it says whether a delegate's
    findings go back to that delegate or to the host. Resolving it against an
    unconfigured policy answers "the host", silently — which is the exact failure #1016
    exists to prevent, arrived at by a missing file rather than by a decision. So an
    unreadable config is a refusal, and an operator who really means "no policy, the host
    fixes" says so with ``--no-project``.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "round": round_number,
        "status": "no-config",
        "blocked": True,
        "fixer": None,
        "ladder": [],
        "hops": [],
        "config_path": path,
        "reason": reason,
        "next_action": (
            f"cannot read the project config at {path}: {reason}. `knobs.team.fix` decides "
            "whether this round goes back to the delegate that implemented or to the host, "
            "so it is not guessed — pass --project/--root, or --no-project to say "
            "deliberately that there is no policy and the host fixes"
        ),
        "warnings": [],
    }


def parse_findings(raw: Any) -> list[findings_mod.Finding]:
    """Read the ``--findings`` document into :class:`keel.findings.Finding` objects.

    Accepts the bare list reviewers return and the ``{"findings": [...]}`` envelope
    ``keel review`` bundles use, so an operator does not have to reshape the file between
    the review that produced it and the fix loop that consumes it.
    """
    items = raw.get("findings") if isinstance(raw, Mapping) else raw
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        raise FixloopError(
            "findings must be a JSON array of findings, or an object with a 'findings' array"
        )
    parsed: list[findings_mod.Finding] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise FixloopError(f"findings[{index}] is not an object")
        severity = _text(item.get("severity"))
        if severity is None:
            raise FixloopError(f"findings[{index}] has no severity")
        line = item.get("line")
        try:
            parsed.append(
                findings_mod.Finding(
                    severity=severity,
                    message=_text(item.get("message")) or "(no message)",
                    source=_text(item.get("source")) or "reviewer",
                    path=_text(item.get("path")),
                    line=line if isinstance(line, int) and not isinstance(line, bool) else None,
                    anchorable=bool(item.get("anchorable")),
                    reproduction=_text(item.get("reproduction")),
                )
            )
        except findings_mod.FindingError as exc:
            raise FixloopError(f"findings[{index}]: {exc}") from None
    return parsed


def neutralise(text: str) -> str:
    """Defang the one token reviewer text must never be able to forge.

    The brief opens with an HTML-comment marker and is handed to a delegate as its
    prompt. A reviewer who can emit ``<!--`` can emit a second
    ``keel.fixloop-brief.v1`` marker — or any other keel artifact marker — so the opener
    and closer are broken here, once, for every reviewer-supplied string.
    """
    return text.replace("<!--", "< !--").replace("-->", "-- >")


def _inline(value: Any, *, fallback: str = "") -> str:
    """One reviewer-supplied value, safe to interpolate into the middle of a line.

    First line only, defanged and capped: a value that reaches the middle of a rendered
    line cannot be allowed to carry a newline, because the text after that newline would
    start a line of the brief that keel did not write.
    """
    if not isinstance(value, str) or not value.strip():
        return fallback
    first = neutralise(value.strip().splitlines()[0]).strip()
    if len(first) > MAX_INLINE_CHARS:
        first = first[:MAX_INLINE_CHARS].rstrip() + _TRUNCATED
    return first or fallback


def _quoted_line(line: str) -> str:
    """One line of reviewer text, with anything that reads as structure defanged."""
    stripped = line.strip()
    if stripped.startswith("#"):
        # Escaped rather than dropped: the reviewer wrote it and the fixer should see it.
        # It just must not render as a heading of its own inside the quote.
        return line.rstrip().replace("#", "\\#", 1)
    # ⚡ Bolt Optimization: Passing a tuple directly to startswith() executes in C
    # and is ~83.4% faster than checking each prefix iteratively using a generator with any()
    if stripped.lower().startswith(_TRAILER_KEYS):
        return "`" + stripped.replace("`", "'") + "`"
    return line.rstrip()


def quote(text: Any, *, indent: str = _QUOTE_INDENT) -> list[str]:
    """Reviewer text as a blockquote: quoted **data**, never instructions.

    Findings are the one part of the brief keel did not write, and the brief becomes the
    fixer's ``--prompt-file``. A finding whose message carried its own
    ``## Rules for this round`` section, a second brief marker and a forged
    ``blocking: no`` trailer would otherwise render as brief structure — reviewer text
    giving the fixer orders keel never issued.

    So every line is prefixed with ``> ``: no line of reviewer text can sit at the start
    of a line of the brief, which is where every structural token of this format lives.
    On top of that the comment opener is defanged (:func:`neutralise`), a leading ``#``
    is escaped, a line that reads as one of the brief's trailer keys becomes inline code,
    and the whole field is capped — a prompt has a budget.
    """
    if not isinstance(text, str):
        text = ""
    body = neutralise(text)
    truncated = False
    if len(body) > MAX_QUOTED_CHARS:
        body, truncated = body[:MAX_QUOTED_CHARS], True
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) > MAX_QUOTED_LINES:
        lines, truncated = lines[:MAX_QUOTED_LINES], True
    rendered = []
    for line in lines:
        content = _quoted_line(line)
        rendered.append(f"{indent}> {content}" if content else f"{indent}>")
    if truncated:
        rendered.append(f"{indent}> {_TRUNCATED}")
    return rendered


def _anchor(finding: findings_mod.Finding) -> str:
    if not isinstance(finding.path, str) or not finding.path.strip():
        return "whole PR"
    # A backtick would close the code span the anchor is rendered in.
    path = _inline(finding.path.replace("`", "'"), fallback="whole PR")
    if finding.line is None:
        return path
    return f"{path}:{finding.line}"


def _finding_block(index: int, finding: findings_mod.Finding) -> list[str]:
    """One finding: a scannable headline, then everything reviewer-written as a quote."""
    message = finding.message if isinstance(finding.message, str) else ""
    # The headline stays one scannable line and the rest of the message is quoted beneath
    # it rather than dropped — the same split `cli._cmd_run_gates` makes for a multi-line
    # gate message, for the same reason: line two onwards is often where the detail is.
    headline, *detail = message.splitlines() or [""]
    lines = [
        (
            f"{index}. **{finding.severity}** · `{_anchor(finding)}` · "
            f"{_inline(headline, fallback='(no message)')}"
        ),
        f"   - reported by: {_inline(finding.source, fallback='an unnamed reviewer')}",
        f"   - decision: {findings_mod.decision_for(finding.severity)}",
    ]
    if detail:
        lines.append("   - the rest of the reviewer's message:")
        lines.extend(quote("\n".join(detail)))
    if finding.reproduction is not None:
        lines.append("   - reproduction:")
        lines.extend(quote(finding.reproduction))
    else:
        lines.append("   - reproduction: not supplied by the reviewer — reproduce it yourself")
    return lines


def re_review(blocked: bool, *, fix_sha: str | None = None) -> dict[str, str]:
    """How the next review is scoped: a blocker re-reviews everything, a suggestion does not."""
    sha = _text(fix_sha) or UNKNOWN_SHA
    if blocked:
        return {
            "mode": "full",
            "instruction": (
                "a blocking finding triggers a full re-review of the change; the reviewer "
                "keeps their original codename"
            ),
        }
    return {"mode": "narrowed", "instruction": NARROWED_INSTRUCTION.format(sha=sha)}


def render_brief(
    *,
    pr_number: int | None,
    round_number: int,
    findings: Sequence[findings_mod.Finding] = (),
    fixer: Mapping[str, Any] | None = None,
    budget: int = DEFAULT_ROUND_BUDGET,
    head_sha: str | None = None,
    issue_number: int | None = None,
    fix_sha: str | None = None,
) -> str:
    """Render the fix brief handed to the round's fixer. Byte-stable for identical input."""
    ordered = findings_mod.sort_findings(list(findings))
    verdict = findings_mod.summarize(list(findings))
    seat = fixer if isinstance(fixer, Mapping) else {}
    provider = _text(seat.get("provider")) or "unassigned"
    stage = _text(seat.get("stage")) or "implementer"
    source = _text(seat.get("source")) or "unresolved"
    alias_note = (
        " — you are fixing this because you implemented it"
        if _text(seat.get("alias")) == "implementer"
        else ""
    )
    pr_label = f"#{pr_number}" if isinstance(pr_number, int) else "the open PR"
    lines = [
        BRIEF_MARKER,
        f"head: {_text(head_sha) or '<head-sha>'}",
        "",
        f"# Fix round {round_number} of {budget} — PR {pr_label}",
        "",
        (
            f"You are the fixer for this round: `{provider}` (ladder stage `{stage}`, "
            f"from `{source}`){alias_note}."
        ),
    ]
    if isinstance(issue_number, int):
        lines.append(f"The change closes issue #{issue_number}.")
    lines.extend(
        [
            "",
            (
                "Fix the findings below in the run's worktree, then commit and push to "
                "the PR branch. Do not open a new PR, do not re-scope the change, and do "
                "not fix anything the reviewers did not raise."
            ),
            "",
            "## Findings",
            "",
        ]
    )
    counter = 0
    for severity in findings_mod.SEVERITIES:
        group = [finding for finding in ordered if finding.severity == severity]
        if not group:
            continue
        decision = findings_mod.decision_for(severity)
        lines.append(f"### {severity} — {len(group)} ({decision})")
        lines.append("")
        for finding in group:
            counter += 1
            lines.extend(_finding_block(counter, finding))
        lines.append("")
    if counter == 0:
        lines.extend(["No findings were supplied — there is nothing to fix this round.", ""])
    lines.extend(
        [
            "## Rules for this round",
            "",
            (
                "- `critical`/`major` block the merge; `minor` is a gated suggestion — "
                "apply it or obtain a recorded `keel.deferral.v1` deferral; `nit` is "
                "advisory."
            ),
            (
                f"- This is round {round_number} of a {budget}-round budget. Exceeding it "
                "marks the issue blocked with the outstanding findings quoted."
            ),
            (
                "- Report what you changed and what you ran. A verification you did not "
                "run is not evidence."
            ),
            "",
            "## Re-review after your push",
            "",
        ]
    )
    scope = re_review(verdict.blocked, fix_sha=fix_sha)
    if scope["mode"] == "full":
        lines.append(f"- Scope: **full** — {scope['instruction']}.")
    else:
        lines.append(f'- Scope: **narrowed** — the reviewer is told: "{scope["instruction"]}".')
        lines.append(
            "- Keep the diff that small. A narrowed reviewer that finds a NEW blocker "
            "escalates the loop back to a full re-review."
        )
    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| severity | count |",
            "| --- | --- |",
        ]
    )
    lines.extend(
        f"| {severity} | {verdict.counts[severity]} |" for severity in findings_mod.SEVERITIES
    )
    lines.append("")
    lines.append(f"blocking: {'yes' if verdict.blocked else 'no'}")
    return "\n".join(lines) + "\n"


def dispatch_argv(
    fixer: Mapping[str, Any] | None,
    *,
    prompt_file: str,
    cwd: str | None = None,
    timeout: int | None = None,
    project: str | None = None,
) -> list[str] | None:
    """The ``keel delegate run --role fix`` argv for a resolved seat, or ``None``.

    ``None`` for a host subagent seat (``kind: subagent``) and for no seat at all: a
    Claude-class subagent is dispatched by the host agent and never reaches
    ``keel delegate run``, exactly as s4 dispatches an implementer.
    """
    if not isinstance(fixer, Mapping):
        return None
    provider = _text(fixer.get("provider"))
    if provider is None or _text(fixer.get("kind")) == "subagent":
        return None
    argv = ["keel", "delegate", "run", "--provider", provider, "--role", "fix"]
    argv.extend(["--prompt-file", prompt_file])
    if _text(cwd) is not None:
        argv.extend(["--cwd", cwd.strip()])
    if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
        argv.extend(["--timeout", str(timeout)])
    model = _text(fixer.get("model"))
    if model is not None:
        argv.extend(["--model", model])
    effort = _text(fixer.get("effort"))
    if effort is not None:
        argv.extend(["--effort", effort])
    if _text(project) is not None:
        argv.extend(["--project", project.strip()])
    return argv


def brief_document(
    *,
    assignment: Mapping[str, Any] | None,
    findings: Sequence[findings_mod.Finding] = (),
    pr_number: int | None = None,
    round_number: int = 1,
    budget: int = DEFAULT_ROUND_BUDGET,
    unavailable: Iterable[str] = (),
    host_agent: str = HOST_DEFAULT,
    head_sha: str | None = None,
    issue_number: int | None = None,
    fix_sha: str | None = None,
    prompt_file: str = "-",
    cwd: str | None = None,
    timeout: int | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """The whole s9 answer for one round: who fixes, the brief, and how to dispatch it."""
    resolution = resolve_fixer(
        assignment,
        round_number=round_number,
        unavailable=unavailable,
        budget=budget,
        host_agent=host_agent,
    )
    verdict = findings_mod.summarize(list(findings))
    brief = render_brief(
        pr_number=pr_number,
        round_number=round_number,
        findings=findings,
        fixer=resolution["fixer"],
        budget=resolution["budget"],
        head_sha=head_sha,
        issue_number=issue_number,
        fix_sha=fix_sha,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "pr": pr_number,
        "issue": issue_number,
        "head": _text(head_sha),
        "round": resolution["round"],
        "budget": resolution["budget"],
        "status": resolution["status"],
        # The *loop* is blocked — no seat can take this round. Whether the *findings*
        # block the merge is `findings.blocking`; conflating the two would have the
        # command exit non-zero on the ordinary case of a blocker with a fixer waiting.
        "blocked": resolution["blocked"],
        "fixer": resolution["fixer"],
        "ladder": resolution["ladder"],
        "hops": resolution["hops"],
        "next_action": resolution["next_action"],
        "warnings": resolution["warnings"],
        "findings": {
            "count": len(verdict.findings),
            "counts": verdict.counts,
            "blocking": verdict.blocked,
        },
        "re_review": re_review(verdict.blocked, fix_sha=fix_sha),
        "dispatch": dispatch_argv(
            resolution["fixer"],
            prompt_file=prompt_file,
            cwd=cwd,
            timeout=timeout,
            project=project,
        ),
        "brief": brief,
    }
