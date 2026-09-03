"""Deterministic consumer-neutral closure-comment renderer.

The ``ship`` backbone posts a human-readable "ship outcome" comment to both the
issue and the PR at s11. This module renders that markdown **from** a structured
``ship_run`` ledger record (see :func:`keel.ledger.build_ship_run_record`); it is a
mirror of the ledger, never a parser source.

Pure-core / thin-I/O: :func:`render_closure_comment` takes a plain dict and returns
markdown. It is deterministic (stable ordering, no wall-clock, no randomness) and
consumer-neutral — the project codename comes from the record's ``target``, never a
literal baked into core.
"""

from __future__ import annotations

from typing import Any

CLOSURE_SCHEMA_VERSION = "keel.closure-comment.v1"
COMMENT_MARKER = f"<!-- {CLOSURE_SCHEMA_VERSION} -->"
HEADING = "Ship outcome"
JURY_LABEL = "AI Jury"
WATERMARK_MARKER = "<!-- keel.watermark.v1 -->"
WATERMARK_BODY = (
    "⚓ **Shipped by [keel](https://github.com/berkayturanci/keel)** — "
    "*Driven on fixed backbone `s0`→`s12` "
    "(with [ai-jury](https://github.com/berkayturanci/ai-jury) consensus)*  \n"
    "[⭐ Star on GitHub](https://github.com/berkayturanci/keel) · "
    "[Add Keel to your repo](https://github.com/berkayturanci/keel#readme)"
)

# Project-neutral documentation detection. A changed file counts as docs when any
# path component equals ``docs`` (case-insensitive) or its suffix is a documentation
# format. Custom docs paths are a project-config/policy concern, not core: keep this
# set generic so the consumer-neutrality guard holds.
#
# ``.txt`` is intentionally excluded: it is false-positive prone (e.g.
# ``requirements.txt``, lockfile-style manifests) and matches plenty of non-docs.
# A doc-ish text file (e.g. ``docs/notes.txt``) still counts via the ``docs/``
# path-component rule, so rely on that directory rule for text docs.
_DOC_SUFFIXES = frozenset({".md", ".mdx", ".markdown", ".rst", ".adoc"})
_DOC_SUFFIXES_TUPLE = tuple(_DOC_SUFFIXES)

#: The ``knobs.implement_mode`` value this renderer names. Spelled here rather than
#: imported so the renderer keeps its "plain dict in, markdown out" contract.
_TDD_MODE = "tdd"


def contract_as_dict() -> dict[str, Any]:
    """Return the stable closure-comment contract consumed by ship adapters."""
    return {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "comment_marker": COMMENT_MARKER,
        "heading": HEADING,
        "source": "run-ledger ship_run record",
        "deterministic": True,
        "consumer_neutral": True,
        "mirror_not_parser": True,
        "renderer": "keel.closure.render_closure_comment",
        "sections": [
            "implementer",
            "reviewers",
            "tester",
            # Rendered only when the run spent an s9 fix round (#1016); a clean run's
            # comment is byte-identical to what it was before the section existed.
            "fix_rounds",
            "pull_request",
            "changed_files",
            "docs_touched",
            "capture",
            "run_id",
            "run_context",
            "watermark",
        ],
        "run_context_fields": [
            "host_agent",
            "transport",
            "profile",
            "jury_mode",
            "implement_mode",
            "consent",
        ],
        "jury_label": JURY_LABEL,
        "watermark_marker": WATERMARK_MARKER,
    }


def render_closure_comment(record: dict[str, Any]) -> str:
    """Render one ``ship_run`` ledger record as the ship outcome markdown comment.

    Missing or ``None`` optional fields degrade gracefully. Only the target line is
    omitted when its value is absent or blank; every other field always renders. The
    implementer, reviewers, tester, run id, and capture all render ``none`` (capture
    renders ``not recorded``) when missing. An empty or jury-only reviewer list
    renders ``none`` / ``AI Jury``; a missing PR number renders ``none``; a
    ``capture`` status of ``None`` renders ``not recorded``.
    """
    actors = record.get("actors") or {}
    lines: list[str] = [COMMENT_MARKER, "", f"## {HEADING}", ""]
    lines.extend(_target_line(record.get("target")))
    lines.append(f"- **Implementer:** {_value(actors.get('implementer'))}")
    lines.append(f"- **Reviewers:** {_reviewers(actors.get('reviewers'))}")
    lines.append(f"- **Tester:** {_value(actors.get('tester'))}")
    lines.extend(_fix_rounds(actors))
    lines.append(f"- **PR:** {_pull_request(record.get('pull_request'))}")
    lines.extend(_changed_files(record.get("changes")))
    lines.append(f"- **Docs touched:** {_docs_touched(record.get('changes'))}")
    lines.append(f"- **Capture:** {_capture(record.get('capture'))}")
    lines.append(f"- **Run id:** {_value(record.get('run_id'))}")
    lines.extend(_run_context(record.get("run_context")))
    lines.extend(_watermark(record.get("watermark")))
    return "\n".join(lines) + "\n"


def _fix_rounds(actors: dict[str, Any]) -> list[str]:
    """The s9 fix rounds, when any were recorded (#1016).

    Omitted entirely on a run with no fix round, which is most of them — a blank
    ``- **Fix rounds:** none`` on every clean ship would be noise, and the field is
    additive to a comment shape other tooling already reads.
    """
    fixers = actors.get("fixers")
    if not isinstance(fixers, list) or not fixers:
        return []
    rendered = ", ".join(
        f"round {item.get('round')}: {_value(item.get('actor'))}{_stage_suffix(item.get('stage'))}"
        for item in fixers
        if isinstance(item, dict)
    )
    if not rendered:
        return []
    return [f"- **Fix rounds:** {rendered}"]


def _stage_suffix(stage: Any) -> str:
    return f" ({stage.strip()})" if isinstance(stage, str) and stage.strip() else ""


def _target_line(target: Any) -> list[str]:
    if not isinstance(target, str) or not target.strip():
        return []
    return [f"**Target:** {target.strip()}", ""]


def _reviewers(reviewers: Any) -> str:
    if not isinstance(reviewers, list):
        return "none"
    entries = [reviewer.strip() for reviewer in reviewers if _is_reviewer(reviewer)]
    listed = [reviewer for reviewer in entries if not _is_jury(reviewer)]
    # ⚡ Bolt Optimization: Compare lengths instead of redundant predicate evaluation via any()
    has_jury = len(listed) < len(entries)
    if not listed:
        return JURY_LABEL if has_jury else "none"
    rendered = ", ".join(listed)
    if has_jury:
        return f"{rendered} — {JURY_LABEL}"
    return rendered


def _is_reviewer(reviewer: Any) -> bool:
    return isinstance(reviewer, str) and bool(reviewer.strip())


def _is_jury(reviewer: str) -> bool:
    return "jury" in reviewer.lower()


def _pull_request(pull_request: Any) -> str:
    number = pull_request.get("number") if isinstance(pull_request, dict) else None
    return f"#{number}" if isinstance(number, int) else "none"


def _changed_files(changes: Any) -> list[str]:
    block = changes if isinstance(changes, dict) else {}
    if block.get("unreadable") is True:
        # The record says git could not be read. The defensive coercions below would
        # otherwise absorb the `None`s and post "0" to the PR — an affirmative claim
        # about a diff nobody saw, in the one artifact a human actually reads.
        return ["- **Changed files:** unreadable (git diff failed)"]
    files = block.get("files")
    files = list(files) if isinstance(files, list) else []
    count = block.get("file_count")
    count = count if isinstance(count, int) else len(files)
    lines = [f"- **Changed files:** {count}"]
    lines.extend(f"  - `{file}`" for file in files)
    return lines


def _docs_touched(changes: Any) -> str:
    """Return ``"yes"`` when any changed file is documentation, else ``"no"``.

    Derived deterministically and consumer-neutrally from ``changes.files``; the
    ledger schema is unchanged and no project config is read. An unreadable diff
    answers ``"unknown"`` rather than ``"no"`` — the file list it would be derived
    from does not exist.
    """
    block = changes if isinstance(changes, dict) else {}
    if block.get("unreadable") is True:
        return "unknown"
    files = block.get("files")
    files = files if isinstance(files, list) else []
    return "yes" if any(_is_doc(file) for file in files) else "no"


def _is_doc(file: Any) -> bool:
    if not isinstance(file, str):
        return False
    lowered = file.lower()
    if "docs" in lowered.replace("\\", "/").split("/"):
        return True
    return lowered.endswith(_DOC_SUFFIXES_TUPLE)


def _capture(capture: Any) -> str:
    block = capture if isinstance(capture, dict) else {}
    status = block.get("status")
    if not isinstance(status, str) or not status:
        return "not recorded"
    reason = block.get("reason")
    learning = _learning(block.get("learning"))
    suffix = f"; learning: {learning}" if learning else ""
    if isinstance(reason, str) and reason.strip():
        return f"{status} ({reason.strip()}){suffix}"
    return f"{status}{suffix}"


def _learning(learning: Any) -> str | None:
    block = learning if isinstance(learning, dict) else {}
    decision = block.get("decision")
    if not isinstance(decision, str) or not decision.strip():
        return None
    reason = block.get("reason")
    if isinstance(reason, str) and reason.strip():
        return f"{decision.strip()} ({reason.strip()})"
    return decision.strip()


def _run_context(run_context: Any) -> list[str]:
    """Render the deterministic preflight Run context block.

    Always emitted (additive section, appended after the existing lines). Each
    field degrades gracefully when missing: host agent / profile / consent
    status render ``unknown``; transport renders ``unknown``; jury renders
    ``off``; an empty consent scope list renders ``none``.
    """
    block = run_context if isinstance(run_context, dict) else {}
    return [
        "",
        "### Run context",
        "",
        f"- **Host agent:** {_unknown(block.get('host_agent'))}",
        f"- **Transport:** {_unknown(block.get('transport'))}",
        f"- **Profile:** {_unknown(block.get('profile'))}",
        f"- **Jury:** {_jury_mode(block.get('jury_mode'))}",
        *_implement_mode(block),
        f"- **Consent:** {_consent(block.get('consent'))}",
    ]


#: How a test-first run names itself in the closure comment (#1020).
TDD_LABEL = "TDD"


def _implement_mode(block: dict[str, Any]) -> list[str]:
    """The s4 profile line — emitted only for a ``tdd`` run.

    Conditional, unlike every other run-context field: ``default`` is what s4 has always
    done, and a line saying so on every closure comment keel has ever posted would be
    noise. A test-first run is the exception worth naming, and it names both phases'
    commits so a reader can check the order the gate checked.
    """
    if block.get("implement_mode") != _TDD_MODE:
        return []
    phases = block.get("implement_phases")
    parts = [
        f"{phase.get('phase')} {_short(phase.get('commit'))}"
        for phase in (phases if isinstance(phases, list) else [])
        if isinstance(phase, dict)
    ]
    detail = f" ({' → '.join(parts)})" if parts else ""
    return [f"- **Implement:** {TDD_LABEL}{detail}"]


def _short(sha: Any) -> str:
    if isinstance(sha, str) and sha.strip():
        return sha.strip()[:7]
    return "unknown"


def _unknown(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _jury_mode(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "off"


def _consent(consent: Any) -> str:
    block = consent if isinstance(consent, dict) else {}
    status = _unknown(block.get("status"))
    scopes = block.get("scopes")
    scopes = scopes if isinstance(scopes, list) else []
    listed = [scope.strip() for scope in scopes if _is_scope(scope)]
    rendered = ", ".join(listed) if listed else "none"
    return f"{status} (scopes: {rendered})"


def _is_scope(scope: Any) -> bool:
    return isinstance(scope, str) and bool(scope.strip())


def _value(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "none"


def _watermark(watermark: Any) -> list[str]:
    """Render the optional attribution and viral watermark signature.

    Emitted by default (when ``watermark is not False``). Can be disabled via
    ``watermark: false`` in record or customized with a string value.
    """
    if watermark is False:
        return []
    if isinstance(watermark, str) and watermark.strip():
        return ["", "---", watermark.strip()]
    return ["", "---", WATERMARK_BODY]
