"""What ``keel swarm-review`` decides about a cluster's pull request (#1423).

The owner's decision on #1423: keel dispatches each cluster pull request's reviewer seats
itself and posts their verdicts with ``keel review``, pinned to the pull request's head, so
``swarm-run --live`` -> ``swarm-review --live`` -> ``swarm-land --live`` works without a host
agent. It is its own opt-in step; neither ``swarm-run`` nor ``swarm-land`` calls it.

This module holds every *decision* in that step, and nothing else:

- **Consent.** :func:`consent_refusal` reads the operator's contract over
  :data:`REVIEW_SIDE_EFFECTS` — a checkout per seat (``filesystem``, ``git``) and the posted
  verdicts (``github``) — and accepts only an approved one: keel dispatches the seats and
  posts their verdicts itself, so no host agent is there to approve them.
- **Who reviews.** The cluster's planned reviewer seats — the ones
  :func:`keel.team.resolve_assignment` resolved for it, which ``swarm-plan`` prints — each
  planned exactly as ``keel delegate run --provider <seat> --role review`` would plan it
  (:func:`plan_review_seat`). A seat keel cannot run read-only is refused, and so is one from
  the implementer's own vendor; :func:`cluster_refusal` then refuses the cluster when what is
  left cannot meet the tier's verdict count or ``require_distinct_vendors``.
- **What a seat is told.** :func:`render_review_brief`: ``/keel:ship`` s7's briefing — the
  focus slice, the refute-not-approve stance, no cross-reading, the head pinned — with the
  issue text and the diff keel read, and the one JSON verdict it must answer with.
- **How a seat's answer is read.** :func:`read_seat_verdict` parses it through the loader
  ``keel review --reviews`` uses (:func:`keel.review.parse_reviews`) and the evidence gate's
  substance rule; anything that does not parse is a failed review, never an approval.
- **Whether anything is posted.** :func:`posting_decision`. keel's evidence gate counts a
  posted verdict whatever its ``Verdict:`` line says, so a posted ``REQUEST_CHANGES`` would
  let ``keel merge`` land the pull request: a cluster whose seats did not all approve posts
  nothing, and its findings are in the report instead.

Pure and deterministic: no subprocess, no filesystem, no clock. The runtime
(:mod:`keel.swarm_review_runtime`) checks the head out, runs the seats and posts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, NamedTuple

from . import artifacts, consent, delegate, evidence, review, swarm_worker, team
from . import findings as fnd
from . import providers as providers_mod

#: Every mutation a live ``swarm-review`` performs: a checkout of the head per seat, and the
#: verdicts posted on the pull request. The operator's consent is asked over exactly these.
REVIEW_SIDE_EFFECTS = ("git_worktree", "comments")

#: The role every seat runs in: :data:`keel.delegate.READ_ONLY_ROLES`.
REVIEW_ROLE = "review"

#: The two verdicts a seat may answer with.
APPROVE = "APPROVE"
REQUEST_CHANGES = "REQUEST_CHANGES"
VERDICTS = (APPROVE, REQUEST_CHANGES)
#: A seat that answered nothing keel can read: never an approval.
FAILED = "failed"

#: What became of one cluster.
POSTED = "posted"
PLANNED = "planned"
HELD = "held"
REFUSED = "refused"
SKIPPED = "skipped"
MERGED = "already-merged"
ERROR = "failed"
#: The outcomes a clean run ends with: verdicts posted (live), a plan printed (dry run), or
#: nothing left to review because the pull request has merged.
CLEAN_STATUSES = (POSTED, PLANNED, MERGED)

#: How much of the diff a brief carries. Past it the brief says the diff was cut, and the
#: seat reads the rest in its checkout of the head.
MAX_DIFF_CHARS = 200_000

#: ``/keel:ship`` s7's reviewer stance (``src/keel/adapters/commands/ship.md``), all four
#: together: the first without the rest is worse than neither.
REVIEW_STANCE = (
    (
        "**Refute it.** Default to the position that the change is wrong and concede only "
        "when the code forces you to. The author already made the case for it; nobody has "
        "made the case against it."
    ),
    (
        "**A finding you cannot demonstrate is not a finding.** Prefer a reproduction — a "
        "failing input, a trace through the code to the line — over an assertion."
    ),
    (
        "**Finish the trace.** Follow a defect from where you noticed it to where it "
        "actually lands: a wrong value on a screen and a wrong value written to a record "
        "are the same bug with very different severities."
    ),
    (
        '**"I checked X, Y and Z and found nothing" is a complete review.** Say what you '
        "checked. A manufactured finding is a failure of the review, not a strict one."
    ),
)


def required_scopes() -> tuple[str, ...]:
    """The consent scopes a live ``swarm-review`` needs: those of :data:`REVIEW_SIDE_EFFECTS`."""
    return consent.side_effect_scopes(REVIEW_SIDE_EFFECTS)


def consent_refusal(contract: Mapping[str, Any]) -> str:
    """Why a live ``swarm-review`` may not start under ``contract``; ``""`` when it may.

    Asked before anything is read or run. A missing scope refuses, and so does consent left
    to a host agent (``consent_mode: agent``): keel runs the seats and posts their verdicts
    itself, so the operator approves the scopes explicitly.
    """
    ok, message = consent.assert_operator_consent(dict(contract))
    if not ok:
        return message
    status = contract.get("status")
    if status != "approved":
        return (
            f"operator consent is {status!r}, which approves nothing keel can act on: "
            "swarm-review --live dispatches its reviewer seats and posts their verdicts "
            f"itself, so the operator approves the scopes (--approve-scope "
            f"{','.join(required_scopes())} --operator NAME, or KEEL_APPROVE_SCOPE with "
            "KEEL_OPERATOR under consent_mode: standing)"
        )
    return ""


class PullRequestFacts(NamedTuple):
    """What keel read from a cluster's pull request before any seat runs."""

    head_sha: str
    title: str
    #: The tier ``keel review`` resolves from the pull request's own diff.
    tier: int | None
    #: The review contract ``keel review`` resolves for it — the count it will refuse to
    #: under-post, ``require_distinct_vendors``, the focus slices and the project's additions.
    contract: Mapping[str, Any]
    #: The unified diff at the head; empty in a dry run, which briefs no seat.
    diff: str = ""


def _reviewers(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    block = contract.get("reviewers")
    return block if isinstance(block, Mapping) else {}


def required_count(contract: Mapping[str, Any]) -> int:
    """The verdicts ``keel review`` requires for the pull request (``tier requires at least``)."""
    count = _reviewers(contract).get("count")
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def distinct_vendors_required(contract: Mapping[str, Any]) -> bool:
    return _reviewers(contract).get("require_distinct_vendors") is True


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v.strip() for v in value if isinstance(v, str) and v.strip())


def focus_for(contract: Mapping[str, Any], slot: str, index: int) -> tuple[str, ...]:
    """The focus slice ``/keel:ship`` s7 gives the reviewer in ``slot``.

    The contract's ``reviewers.focuses`` names one slice per slot, merging dimensions when
    the count drops (they merge, never drop). A slot it does not name — a bench larger
    than the count — takes the slice at its position, and past the last one, every
    dimension.
    """
    focuses = [f for f in _reviewers(contract).get("focuses") or () if isinstance(f, Mapping)]
    for entry in focuses:
        if entry.get("slot") == slot:
            return _strings(entry.get("focus"))
    if index < len(focuses):
        return _strings(focuses[index].get("focus"))
    return tuple(dim for entry in focuses for dim in _strings(entry.get("focus")))


def restaffed(persisted: Mapping[str, Any] | None, resolved: Mapping[str, Any]) -> dict[str, Any]:
    """``resolved``'s review bench, with the implementer the run actually dispatched.

    ``--reviewers`` / ``--review-delegate`` re-resolve a cluster's assignment through the
    resolver ``swarm-plan`` used (:func:`keel.swarm.resolve_cluster_assignment`). Only the
    bench may change: the implementer is the seat that wrote the pull request, which is the
    one a reviewer's vendor is compared with.
    """
    staffed = dict(resolved)
    if persisted and isinstance(persisted.get("implementer"), Mapping):
        staffed["implementer"] = dict(persisted["implementer"])
    return staffed


def restaff_plan(plan: Any, resolve: Callable[[Any], Mapping[str, Any] | None]) -> Any:
    """``plan`` with each cluster's bench re-resolved by ``resolve`` (:func:`restaffed`).

    ``resolve`` answers ``None`` for a cluster it cannot re-resolve (a plan with no
    difficulty recorded for it), which keeps the persisted bench.
    """
    waves = []
    for wave in plan.waves:
        clusters = []
        for cluster in wave.clusters:
            resolved = resolve(cluster)
            if resolved is not None:
                cluster = replace(cluster, assignment=restaffed(cluster.assignment, resolved))
            clusters.append(cluster)
        waves.append(replace(wave, clusters=tuple(clusters)))
    return replace(plan, waves=tuple(waves))


def seat_token(seat: Mapping[str, Any]) -> str:
    """``provider`` or ``provider:model`` — the ``--provider`` token ``keel delegate run`` takes."""
    model = seat.get("model")
    return str(seat.get("provider")) + (f":{model}" if model else "")


def seat_vendor(
    seat: Mapping[str, Any] | None,
    *,
    config: Any,
    registry: providers_mod.Registry | None,
    host_agent: str,
) -> str:
    """The vendor attribution names for a seat — the implementer's, here.

    A provider seat resolves as ``keel delegate run`` resolves it (a profile or registry
    entry's ``vendor_label`` included). A host subagent runs in the host agent. ``""`` when
    the cluster records no seat at all.
    """
    if not seat:
        return ""
    if seat.get("kind") != "provider":
        return host_agent
    try:
        resolution = delegate.resolve_provider(config, registry, seat_token(seat))
    except delegate.DelegateError:
        return str(seat.get("name") or seat.get("provider"))
    return resolution.provider.label_vendor()


@dataclass(frozen=True)
class ReviewSeat:
    """One reviewer seat of a cluster, as keel would dispatch it, or why it would not."""

    slot: str
    #: The ``reviewer`` its verdict carries — stable per slot and vendor, so a second run
    #: on the same head edits the same comment rather than adding a verdict.
    reviewer: str
    #: The seat's ``--provider`` token.
    provider: str
    #: Where the seat came from (``team.review``, ``flag:--review-delegate`` …).
    source: str
    plan: delegate.RunPlan | None = None
    refusal: str = ""

    @property
    def eligible(self) -> bool:
        return self.plan is not None and not self.refusal

    @property
    def vendor(self) -> str | None:
        return None if self.plan is None else self.plan.vendor

    def to_dict(self) -> dict[str, Any]:
        plan = self.plan
        return {
            "slot": self.slot,
            "reviewer": self.reviewer,
            "provider": self.provider,
            "source": self.source,
            "vendor": None if plan is None else plan.vendor,
            "model": None if plan is None else plan.model,
            "transport": None if plan is None else plan.transport,
            "read_only_backed": None if plan is None else plan.read_only_backed,
            "eligible": self.eligible,
            "refusal": self.refusal,
        }


def reviewer_id(slot: str, vendor: str) -> str:
    return artifacts.slug(f"swarm-review-{slot}-{vendor}")


def plan_review_seat(
    seat: Mapping[str, Any],
    *,
    config: Any,
    registry: providers_mod.Registry | None,
    prompt_path: str,
    cwd: str,
    timeout: int = delegate.DEFAULT_TIMEOUT_S,
) -> tuple[delegate.RunPlan | None, str]:
    """A reviewer seat as a read-only delegate plan, or ``(None, reason)``.

    The sibling of :func:`keel.swarm_worker.plan_implementer`: resolved and planned exactly
    as ``keel delegate run --provider <seat> --role review`` would, in the seat's own
    checkout of the head. What is refused, and why:

    - a host subagent (``subagent:…``), which only an agent host can spawn;
    - a seat keel cannot plan (an unknown provider, a bad model token);
    - a seat whose plan is not **backed** read-only
      (:attr:`keel.delegate.RunPlan.read_only_backed`): a ``knobs.delegate_profiles``
      entry with no ``review_args``, which would run with the implementer's write flags.

    Every other transport is accepted. The three built-in CLIs carry their vendor's
    documented read-only invocation and read the code around the diff in the checkout. An
    ``api``/``ollama`` seat has no tools at all — it cannot write, and it cannot read the
    checkout either, but its brief carries the diff and the issue text, which is what a
    review needs; it reviews the diff alone.
    """
    if seat.get("kind") != "provider":
        return None, (
            f"the reviewer seat {seat.get('provider')!r} is a host subagent, which only an "
            "agent host can spawn; name a provider for it with --review-delegate"
        )
    token = seat_token(seat)
    try:
        resolution = delegate.resolve_provider(config, registry, token)
        plan = delegate.plan_run(
            resolution.provider,
            REVIEW_ROLE,
            prompt_path,
            cwd,
            timeout,
            seat.get("effort"),
            resolution.model,
            profile=resolution.profile,
        )
    except delegate.DelegateError as exc:
        return None, f"the reviewer seat {token!r} cannot be planned ({exc.code}): {exc.message}"
    if not plan.read_only_backed:
        return None, (
            f"nothing makes the reviewer seat {token!r} read-only (a profile with no "
            "review_args runs with the implementer's flags); set review_args for it, or "
            "name another provider with --review-delegate"
        )
    return plan, ""


def review_seats(
    assignment: Mapping[str, Any] | None,
    *,
    config: Any,
    registry: providers_mod.Registry | None,
    implementer_vendor: str,
    brief_path: Callable[[str], str],
    checkout_path: Callable[[str], str],
    timeout: int = delegate.DEFAULT_TIMEOUT_S,
) -> tuple[ReviewSeat, ...]:
    """The cluster's reviewer seats, each planned or refused.

    ``brief_path`` and ``checkout_path`` give a slot's brief file and its checkout of the
    head. A seat from the implementer's own vendor is refused: the owner's decision on #1423
    is a review from a different vendor than the one that wrote the change, which the review
    contract states as ``self_review_counts_toward_lgtm: false``.
    """
    seats: list[ReviewSeat] = []
    raw = (assignment or {}).get("reviewers") or ()
    for index, seat in enumerate(s for s in raw if isinstance(s, Mapping)):
        slot = str(seat.get("slot") or chr(ord("A") + index))
        plan, refusal = plan_review_seat(
            seat,
            config=config,
            registry=registry,
            prompt_path=brief_path(slot),
            cwd=checkout_path(slot),
            timeout=timeout,
        )
        vendor = plan.vendor if plan is not None else str(seat.get("name") or "seat")
        if plan is not None and implementer_vendor and plan.vendor == implementer_vendor:
            refusal = (
                f"its vendor {plan.vendor!r} is the implementer's; a review from the vendor "
                "that wrote the change is not an independent opinion"
            )
        seats.append(
            ReviewSeat(
                slot=slot,
                reviewer=reviewer_id(slot, vendor),
                provider=seat_token(seat),
                source=str(seat.get("source") or ""),
                plan=plan,
                refusal=refusal,
            )
        )
    return tuple(seats)


def cluster_refusal(
    seats: Sequence[ReviewSeat],
    *,
    panel: str,
    required: int,
    require_distinct: bool,
) -> str:
    """Why keel will not review this cluster at all; ``""`` when it will.

    Decided before any seat runs, so a cluster that could never post enough verdicts spends
    nothing. ``required`` is the count ``keel review`` refuses to under-post; at least one
    seat is always required.
    """
    if panel == team.JURY_PANEL:
        return (
            "the tier's review is the jury panel, which swarm-review does not convene; run "
            "the panel and post its ballots with `keel review --from-jury`"
        )
    eligible = [s for s in seats if s.eligible]
    needed = max(required, 1)
    if len(eligible) < needed:
        why = "; ".join(f"seat {s.slot} ({s.provider}): {s.refusal}" for s in seats if s.refusal)
        return (
            f"the tier requires at least {needed} review verdict(s), and only {len(eligible)} "
            f"of the cluster's {len(seats)} reviewer seat(s) can review it"
            + (f" — {why}" if why else "")
            + "; name providers per slot with --review-delegate"
        )
    if require_distinct:
        check = evidence.distinct_vendor_check(
            [s.vendor for s in eligible], required_count=len(eligible)
        )
        if not check["ok"]:
            return (
                f"require_distinct_vendors is on and the seats would fail it ({check['reason']}); "
                "every posted verdict must come from a different vendor — name another "
                "provider for the duplicated slot with --review-delegate"
            )
    return ""


def _fence(text: str) -> str:
    """A backtick fence longer than any run of backticks in ``text``."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_review_brief(
    *,
    swarm_id: str,
    cluster_id: str,
    pull_request: int,
    facts: PullRequestFacts,
    seat: ReviewSeat,
    focus: Sequence[str],
    issues: Sequence[tuple[int, str, str]],
) -> str:
    """One seat's brief: ``/keel:ship`` s7's reviewer briefing, for one cluster pull request.

    The head is pinned, the diff is the one keel read at it, and the answer is one JSON
    object :func:`read_seat_verdict` parses. ``issues`` are ``(number, title, body)``.
    """
    reviewers = _reviewers(facts.contract)
    lines = [
        (
            f"You are reviewer {seat.slot} of pull request #{pull_request} (cluster "
            f"{cluster_id} of keel swarm {swarm_id}), at head {facts.head_sha}."
        ),
        (
            "Your working directory is a checkout of exactly that head. Read the code around "
            "the diff there; review only that head."
        ),
        "",
        "## Rules",
        "",
        (
            "- You are read-only. Do not edit, create or delete files, commit, push, or run "
            "anything that changes the repository or its remote. keel posts your verdict "
            "itself."
        ),
        (
            "- You have no GitHub credentials and git here cannot reach any remote, by "
            "design; do not work around it."
        ),
        ("- Review independently: do not look for, read or wait on any other reviewer's output."),
        "",
        "## Stance",
        "",
        *(f"- {line}" for line in REVIEW_STANCE),
        "",
        "## Your focus",
        "",
        *(f"- {dim}" for dim in focus or ("every dimension of the change",)),
    ]
    additions = _strings(reviewers.get("project_additions"))
    if additions:
        lines += [
            "",
            "## Recurring shapes in this project (look for these; not a checklist)",
            "",
            *(f"- {entry}" for entry in additions),
        ]
    sections = _strings(reviewers.get("required_sections"))
    if sections:
        lines += [
            "",
            "## Sections your scope must cover",
            "",
            *(f"- {entry}" for entry in sections),
        ]
    for number, title, body in issues:
        lines += ["", f"## Issue #{number}: {title or '(title unavailable)'}", ""]
        lines.append(body or "(The issue body could not be read; work from the title.)")
    diff = facts.diff
    note = ""
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS]
        note = f"(The diff is cut at {MAX_DIFF_CHARS} characters; read the rest in your checkout.)"
    fence = _fence(diff)
    lines += [
        "",
        f"## The diff at {facts.head_sha}",
        "",
        f"{fence}diff",
        diff.rstrip("\n"),
        fence,
    ]
    if note:
        lines.append(note)
    lines += [
        "",
        "## Your answer",
        "",
        "End your answer with exactly one JSON object, and nothing after it:",
        "",
        "```json",
        json.dumps(
            {
                "verdict": "APPROVE or REQUEST_CHANGES",
                "scope": "what you checked, naming the files, functions or paths",
                "findings": [
                    {
                        "severity": "critical | major | minor | nit",
                        "message": "the defect, demonstrated",
                        "path": "src/file.py",
                        "line": 42,
                    }
                ],
                "testing": "what the tests do and do not pin",
            },
            indent=2,
        ),
        "```",
        "",
        (
            "Any critical or major finding means REQUEST_CHANGES. `findings` is an empty "
            "list for a clean review, whose `scope` says what you checked. An answer keel "
            "cannot parse is recorded as a failed review, never as an approval."
        ),
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class SeatVerdict:
    """What one seat answered, as keel read it."""

    slot: str
    reviewer: str
    #: :data:`APPROVE`, :data:`REQUEST_CHANGES` or :data:`FAILED`.
    outcome: str
    reason: str = ""
    findings: tuple[Mapping[str, Any], ...] = ()
    #: The ``keel review --reviews`` entry for this verdict; ``None`` for a failed one.
    item: Mapping[str, Any] | None = None
    #: The seat changed the repository's git setup: nothing of the cluster is posted.
    tampered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "reviewer": self.reviewer,
            "outcome": self.outcome,
            "reason": self.reason,
            "findings": [dict(f) for f in self.findings],
            "tampered": self.tampered,
        }


def failed_verdict(seat: ReviewSeat, reason: str, *, tampered: bool = False) -> SeatVerdict:
    return SeatVerdict(seat.slot, seat.reviewer, FAILED, reason, tampered=tampered)


def extract_verdict_object(text: str) -> dict[str, Any] | None:
    """The last JSON object in ``text`` that carries a ``verdict``, or ``None``.

    A seat answers in prose and ends with the object, often inside a fenced block; every
    ``{`` is tried as the start of one, and the last object with a ``verdict`` key wins.
    """
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    for match in re.finditer(r"\{", text):
        try:
            value, _end = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict) and "verdict" in value:
            found = value
    return found


def _verdict_word(raw: Any) -> str:
    return re.sub(r"[\s-]+", "_", raw.strip()).upper() if isinstance(raw, str) else ""


def _finding_issue(finding: Mapping[str, Any], index: int) -> str:
    severity = finding.get("severity")
    if not isinstance(severity, str) or severity.strip().lower() not in fnd.SEVERITIES:
        valid = ", ".join(fnd.SEVERITIES)
        return f"finding #{index + 1} has severity {severity!r}, not one of {valid}"
    message = finding.get("message")
    if not isinstance(message, str) or not message.strip():
        return f"finding #{index + 1} has no message"
    return ""


def verdict_from_object(
    seat: ReviewSeat, obj: Mapping[str, Any], *, head_sha: str, pr_title: str = ""
) -> SeatVerdict:
    """One seat's JSON verdict, read the way ``keel review --reviews`` reads a bundle entry.

    keel, not the seat, names the reviewer, vendor and model — from the seat's own
    attribution. The entry goes through :func:`keel.review.parse_reviews`, then keel's own
    rules: a verdict of :data:`VERDICTS`, a non-empty scope, findings in keel's severity
    vocabulary, and a body :func:`keel.evidence.verdict_substance` would count. An approval
    that carries a critical or major finding is read as ``REQUEST_CHANGES``.
    """
    plan = seat.plan
    entry = {
        "reviewer": seat.reviewer,
        "verdict": obj.get("verdict"),
        "scope": obj.get("scope"),
        "findings": obj.get("findings"),
        "testing": obj.get("testing"),
        "vendor": None if plan is None else plan.vendor,
        "model": None if plan is None else plan.model,
    }
    try:
        (item,) = review.parse_reviews([entry])
    except review.ReviewError as exc:
        return failed_verdict(seat, f"its verdict does not parse: {exc}")
    word = _verdict_word(item.verdict)
    if word not in VERDICTS:
        return failed_verdict(seat, f"its verdict {item.verdict!r} is not {' or '.join(VERDICTS)}")
    if not item.scope or not item.scope.strip():
        return failed_verdict(seat, "its verdict says nothing about what it checked (no scope)")
    for index, finding in enumerate(item.findings):
        if why := _finding_issue(finding, index):
            return failed_verdict(seat, f"its verdict does not parse: {why}")
    findings = tuple(
        {**finding, "severity": str(finding["severity"]).strip().lower()}
        for finding in item.findings
    )
    reason = ""
    if word == APPROVE and any(fnd.decision_for(f["severity"]) == "block" for f in findings):
        word = REQUEST_CHANGES
        reason = "it approved with a critical or major finding, which is read as REQUEST_CHANGES"
    body = artifacts.render_review_verdict(
        reviewer=item.reviewer,
        head_sha=head_sha,
        verdict=word,
        scope=item.scope,
        findings=list(findings),
        testing=item.testing,
        vendor=item.vendor,
        model=item.model,
    )
    ok, why = evidence.verdict_substance(body, pr_title=pr_title)
    if not ok:
        return failed_verdict(
            seat, f"its verdict names nothing concrete, so keel merge would not count it: {why}"
        )
    posted = {
        "reviewer": item.reviewer,
        "verdict": word,
        "scope": item.scope,
        "findings": [dict(f) for f in findings],
        "testing": item.testing,
        "vendor": item.vendor,
        "model": item.model,
    }
    return SeatVerdict(seat.slot, seat.reviewer, word, reason, findings, posted)


def read_seat_verdict(
    seat: ReviewSeat, result: Mapping[str, Any], *, head_sha: str, pr_title: str = ""
) -> SeatVerdict:
    """A seat's ``keel delegate run`` result as its verdict: a failed run or an answer with no
    JSON verdict in it is :data:`FAILED`."""
    if not result.get("ok"):
        return failed_verdict(
            seat,
            f"the seat did not answer ({result.get('error_code')}): {result.get('error')}",
        )
    obj = extract_verdict_object(str(result.get("text") or ""))
    if obj is None:
        return failed_verdict(seat, "its answer carries no JSON object with a verdict")
    return verdict_from_object(seat, obj, head_sha=head_sha, pr_title=pr_title)


def posting_decision(verdicts: Sequence[SeatVerdict], *, required: int) -> str:
    """Why nothing of a cluster is posted; ``""`` when its approvals are.

    - A seat that changed the repository's git setup holds the whole cluster.
    - A ``REQUEST_CHANGES`` holds it: keel's evidence gate counts a posted verdict whatever
      its ``Verdict:`` line says, so posting one would let ``keel merge`` land the change it
      rejected. The findings are in the report.
    - Fewer approvals than the tier requires hold it. A failed seat is never an approval,
      and it does not hold the cluster when enough others approved.
    """
    tampered = [v.slot for v in verdicts if v.tampered]
    if tampered:
        return (
            f"seat(s) {', '.join(tampered)} changed the repository's git setup while they ran; "
            "nothing is posted — inspect the repository's git config and hooks"
        )
    rejecting = [v.slot for v in verdicts if v.outcome == REQUEST_CHANGES]
    if rejecting:
        return (
            f"seat(s) {', '.join(rejecting)} requested changes, so nothing is posted: keel's "
            "evidence gate counts a posted verdict whatever it says, and a posted "
            "REQUEST_CHANGES would let keel merge land the pull request. Address the "
            "findings, push, and run swarm-review again on the new head"
        )
    approving = [v for v in verdicts if v.outcome == APPROVE]
    needed = max(required, 1)
    if len(approving) < needed:
        return (
            f"{len(approving)} seat(s) approved and the tier requires at least {needed} "
            "verdict(s), so nothing is posted (a failed seat is never counted as an approval)"
        )
    return ""


def head_moved(reviewed: str, current: str) -> str:
    """Why nothing is posted when the head the seats reviewed is no longer the head."""
    if current == reviewed:
        return ""
    return (
        f"the pull request's head moved from {reviewed} to {current or 'an unreadable head'} "
        "while the seats ran; nothing is posted — run swarm-review again on the new head"
    )


def review_run_id(swarm_id: str, cluster_id: str) -> str:
    """The ``keel review --run-id`` of a cluster: its provenance run id, so each seat's
    verdict is the comment ``<run-id>:rv-<reviewer>`` and a second run edits it in place."""
    return swarm_worker.provenance_run_id(swarm_id, cluster_id)


@dataclass(frozen=True)
class ClusterReview:
    """What ``swarm-review`` did, or would do, for one cluster."""

    cluster_id: str
    #: One of :data:`POSTED`, :data:`PLANNED`, :data:`HELD`, :data:`REFUSED`,
    #: :data:`SKIPPED`, :data:`MERGED` or :data:`ERROR`.
    status: str
    reason: str = ""
    pull_request: int | None = None
    head_sha: str = ""
    tier: int | None = None
    required: int = 0
    seats: tuple[ReviewSeat, ...] = ()
    verdicts: tuple[SeatVerdict, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def posted(self) -> bool:
        return self.status == POSTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "status": self.status,
            "posted": self.posted,
            "reason": self.reason,
            "pull_request": self.pull_request,
            "head_sha": self.head_sha,
            "tier": self.tier,
            "required": self.required,
            "seats": [s.to_dict() for s in self.seats],
            "verdicts": [v.to_dict() for v in self.verdicts],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SwarmReviewResult:
    swarm_id: str
    wave_index: int
    dry_run: bool
    clusters: tuple[ClusterReview, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def status(self) -> str:
        """``success`` when every cluster ended clean (:data:`CLEAN_STATUSES`) and there was
        at least one; otherwise ``failed``."""
        if self.clusters and all(c.status in CLEAN_STATUSES for c in self.clusters):
            return "success"
        return "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "wave_index": self.wave_index,
            "dry_run": self.dry_run,
            "status": self.status,
            "clusters": [c.to_dict() for c in self.clusters],
            "warnings": list(self.warnings),
        }


def _seat_line(seat: ReviewSeat, verdict: SeatVerdict | None) -> list[str]:
    plan = seat.plan
    where = f"{seat.provider}" + (f" over {plan.transport}" if plan is not None else "")
    if not seat.eligible:
        return [f"    seat {seat.slot} {where}: refused — {seat.refusal}"]
    if verdict is None:
        return [f"    seat {seat.slot} {where}: would review as {seat.reviewer}"]
    line = f"    seat {seat.slot} {where}: {verdict.outcome}"
    if verdict.reason:
        line += f" — {verdict.reason}"
    out = [line]
    out += [f"      - {f['severity']}: {f['message']}" for f in verdict.findings]
    return out


def render_swarm_review_result(result: SwarmReviewResult) -> str:
    mode = "dry-run" if result.dry_run else "live"
    head = f"keel swarm-review — {mode}  swarm {result.swarm_id}, wave {result.wave_index}"
    lines = [f"{head}: {result.status}"]
    if not result.clusters:
        lines.append("  no cluster in this wave")
    for cluster in result.clusters:
        where = f"PR #{cluster.pull_request}" if cluster.pull_request is not None else "no PR"
        if cluster.head_sha:
            where += f" @ {cluster.head_sha[:12]}"
        lines.append(f"  {cluster.cluster_id}: {where} — {cluster.status}")
        if cluster.required:
            lines.append(f"    tier {cluster.tier}: requires {cluster.required} verdict(s)")
        verdicts = {v.slot: v for v in cluster.verdicts}
        for seat in cluster.seats:
            lines += _seat_line(seat, verdicts.get(seat.slot))
        if cluster.reason:
            lines.append(f"    {cluster.reason}")
        lines += [f"    warning: {w}" for w in cluster.warnings]
    lines += [f"  warning: {w}" for w in result.warnings]
    return "\n".join(lines)


def with_status(review_: ClusterReview, status: str, reason: str) -> ClusterReview:
    return replace(review_, status=status, reason=reason)


def posted_items(verdicts: Iterable[SeatVerdict]) -> list[dict[str, Any]]:
    """The ``keel review --reviews`` bundle: every approval's entry, in seat order."""
    return [dict(v.item) for v in verdicts if v.outcome == APPROVE and v.item is not None]
