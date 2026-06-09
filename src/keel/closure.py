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
HEADING = "Ship outcome"
JURY_LABEL = "AI Jury"


def contract_as_dict() -> dict[str, Any]:
    """Return the stable closure-comment contract consumed by ship adapters."""
    return {
        "schema_version": CLOSURE_SCHEMA_VERSION,
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
            "pull_request",
            "changed_files",
            "capture",
            "run_id",
        ],
        "jury_label": JURY_LABEL,
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
    lines: list[str] = [f"## {HEADING}", ""]
    lines.extend(_target_line(record.get("target")))
    lines.append(f"- **Implementer:** {_value(actors.get('implementer'))}")
    lines.append(f"- **Reviewers:** {_reviewers(actors.get('reviewers'))}")
    lines.append(f"- **Tester:** {_value(actors.get('tester'))}")
    lines.append(f"- **PR:** {_pull_request(record.get('pull_request'))}")
    lines.extend(_changed_files(record.get("changes")))
    lines.append(f"- **Capture:** {_capture(record.get('capture'))}")
    lines.append(f"- **Run id:** {_value(record.get('run_id'))}")
    return "\n".join(lines) + "\n"


def _target_line(target: Any) -> list[str]:
    if not isinstance(target, str) or not target.strip():
        return []
    return [f"**Target:** {target.strip()}", ""]


def _reviewers(reviewers: Any) -> str:
    if not isinstance(reviewers, list):
        return "none"
    entries = [reviewer.strip() for reviewer in reviewers if _is_reviewer(reviewer)]
    listed = [reviewer for reviewer in entries if not _is_jury(reviewer)]
    has_jury = any(_is_jury(reviewer) for reviewer in entries)
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
    files = block.get("files")
    files = list(files) if isinstance(files, list) else []
    count = block.get("file_count")
    count = count if isinstance(count, int) else len(files)
    lines = [f"- **Changed files:** {count}"]
    lines.extend(f"  - `{file}`" for file in files)
    return lines


def _capture(capture: Any) -> str:
    block = capture if isinstance(capture, dict) else {}
    status = block.get("status")
    if not isinstance(status, str) or not status:
        return "not recorded"
    reason = block.get("reason")
    if isinstance(reason, str) and reason.strip():
        return f"{status} ({reason.strip()})"
    return status


def _value(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "none"
