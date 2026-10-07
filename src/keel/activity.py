"""Lightweight, additive **command-activity** records for live observability.

The resumable ``checkpoint`` is the ship backbone's own artifact (s0–s12) and is
deliberately ship-shaped. Most other keel commands (``triage``, ``morning``,
``pr-loop`` …) run in the main checkout, never write a checkpoint, and so are
invisible to ``keel-visual``'s live board.

This module adds a *separate, additive* channel: a per-run JSON record under
``.keel/activity/`` that any command's adapter can stamp as it moves through its
own flow phases (from :mod:`keel.flows`). It never touches the checkpoint
contract. Records are keyed by ``run_id`` (one file each), so two commands in the
same repo never clobber one another.

Pure-core + thin I/O, mirroring :mod:`keel.checkpoint`: the builders/validators
are deterministic (stable ordering, no wall-clock, no randomness); only
read/write/remove touch the filesystem.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from . import config as cfg
from . import flows, lock, workspace

ACTIVITY_SCHEMA_VERSION = "keel.activity.v1"
RECORD_TYPE_ACTIVITY = "command_activity"
DEFAULT_ACTIVITY_DIR = ".keel/activity"
STATUSES = ("running", "done", "merged")

#: Whether the phase the run reached was actually *passed*. Separate from
#: :data:`STATUSES` because "advanced to s8" and "cleared s8" are different facts and
#: were previously written identically (#636) — a run whose gates came back red
#: recorded as ``phase: s8, status: running``, carrying no failure signal at all, and
#: the board painted it as in-progress. ``None`` means the step reached this stamp
#: without a verdict to report (planning, a phase with nothing to pass), which is not
#: the same as passing.
VERDICTS = ("pass", "blocked")

#: The field a record keeps its delegates' measured token counts in (#1373): an object
#: keyed by the delegate call's id, each value ``{"model", "prompt_tokens",
#: "completion_tokens"}``. Keyed rather than summed, for two reasons: one ship run makes
#: several delegate calls, often to different vendors, and a sum could only be priced at
#: one model; and a key makes recording the same call twice a no-op instead of a double
#: count. Absent until a delegate records into the run.
USAGE_FIELD = "delegate_usage"

#: Optional "who is driving this run" fields (#1482): the host (Claude Code, Codex…),
#: the agent or delegate (claude, codex…), its model id and reasoning effort. Additive
#: to ``keel.activity.v1`` — absent from a record means unknown, never ``None``.
IDENTITY_FIELDS = ("host", "agent", "model", "effort")
IDENTITY_MAX_LEN = 64

#: How long a writer waits for another writer of the same record: attempts x poll.
#: Holding the lock takes one read and one atomic write, so two seconds is ample; it is
#: bounded because a holder killed mid-write leaves its claim behind.
LOCK_ATTEMPTS = 40
LOCK_POLL_S = 0.05

# A run_id reduces to this slug for its filename; anything else is rejected so a
# crafted run_id can never escape the activity directory.
_RUN_ID_SLUG = re.compile(r"[^a-z0-9._-]+")


class ActivityError(ValueError):
    """Raised when an activity record or path is malformed."""


def activity_contract_as_dict() -> dict[str, Any]:
    """Return the stable activity-record contract consumed by adapters."""
    return {
        "schema_version": ACTIVITY_SCHEMA_VERSION,
        "record_type": RECORD_TYPE_ACTIVITY,
        "dir": DEFAULT_ACTIVITY_DIR,
        "keyed_by": "run_id",
        "statuses": list(STATUSES),
        "additive": True,
        "touches_checkpoint": False,
        "phase_source": "keel.flows.flow_for(command)",
        "usage_field": USAGE_FIELD,
        "identity_fields": list(IDENTITY_FIELDS),
    }


def configured_activity_dir(config: cfg.ProjectConfig) -> tuple[str, str]:
    """Return the configured activity directory and its source."""
    pack = config.policy_pack or {}
    reports = pack.get("reports") if isinstance(pack.get("reports"), dict) else {}
    value = reports.get("activity")
    if isinstance(value, str) and value.strip():
        return value, "policy_pack.reports.activity"
    return DEFAULT_ACTIVITY_DIR, "default"


def resolve_dir(root: str | Path, config: cfg.ProjectConfig) -> Path:
    """Resolve the activity directory under ``root`` and reject escapes."""
    raw, _ = configured_activity_dir(config)
    path = Path(raw)
    if workspace.is_root_anchored(raw):
        raise ActivityError("activity dir must be relative to the project root")
    root_path = Path(root).resolve()
    resolved = (root_path / path).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ActivityError("activity dir escapes the project root") from exc
    return resolved


def run_id_slug(run_id: str) -> str:
    """Reduce a run_id to a safe filename stem (lowercase ``[a-z0-9._-]``)."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ActivityError("run_id must be a non-empty string")
    slug = _RUN_ID_SLUG.sub("-", run_id.strip().lower()).strip("-.")
    if not slug:
        raise ActivityError("run_id has no usable characters")
    return slug


def record_path(root: str | Path, config: cfg.ProjectConfig, run_id: str) -> Path:
    """Path of the activity record for ``run_id`` under ``root``."""
    return resolve_dir(root, config) / f"{run_id_slug(run_id)}.json"


def _phase_ids(command: str) -> tuple[str, ...]:
    return tuple(phase.id for phase in flows.flow_for(command))


def build_activity_record(
    *,
    command: str,
    run_id: str,
    phase: str,
    status: str = "running",
    verdict: str | None = None,
    issue: int | None = None,
    pr: int | None = None,
    note: str | None = None,
    host: str | None = None,
    agent: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    """Build one deterministic activity record, validating command + phase.

    ``command`` must be a known :mod:`keel.flows` command and ``phase`` one of
    that command's flow phase ids. ``status`` is ``running``, ``done`` or
    ``merged`` (a real merge landed, distinct from a soft ``done``).

    ``verdict`` (:data:`VERDICTS`) says whether the phase was **passed**, which
    ``status`` deliberately does not: a blocked gate run is still ``running`` in the
    board's sense — it advanced, it did not finish — and recording only that made a
    red gate indistinguishable from an in-progress one (#636). ``None`` = no verdict
    to report, which must not read as a pass.

    ``host`` / ``agent`` / ``model`` / ``effort`` (:data:`IDENTITY_FIELDS`) say who is
    driving the run; each is a short single-line string and is left out of the record
    when not given.
    """
    if not flows.is_known(command):
        raise ActivityError(f"unknown command: {command!r}")
    if phase not in _phase_ids(command):
        raise ActivityError(f"phase {phase!r} is not a {command} flow phase")
    if status not in STATUSES:
        raise ActivityError(f"unsupported status: {status!r}")
    if verdict is not None and verdict not in VERDICTS:
        raise ActivityError(f"unsupported verdict: {verdict!r}")
    record: dict[str, Any] = {
        "schema_version": ACTIVITY_SCHEMA_VERSION,
        "record_type": RECORD_TYPE_ACTIVITY,
        "command": command,
        "run_id": run_id,
        "phase": phase,
        "status": status,
        "verdict": verdict,
        "issue": issue,
        "pr": pr,
        "note": note,
    }
    for name, value in zip(IDENTITY_FIELDS, (host, agent, model, effort), strict=True):
        if value is not None:
            issue_text = identity_issue(value)
            if issue_text is not None:
                raise ActivityError(f"{name}: {issue_text}")
            record[name] = value
    return record


def identity_issue(value: Any) -> str | None:
    """Why ``value`` is not a valid identity string, or ``None`` when it is."""
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    if len(value) > IDENTITY_MAX_LEN:
        return f"must be at most {IDENTITY_MAX_LEN} characters"
    if value != value.strip():
        return "must not start or end with whitespace"
    # Cc: C0, DEL and C1 (incl. NEL); Zl/Zp: U+2028/U+2029 — anything that breaks a line.
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in value):
        return "must not contain control characters or line breaks"
    return None


def carry_identity(record: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    """``record`` with the identity ``existing`` held, where the stamp does not restate it.

    A later stamp that names neither ``agent`` nor ``model`` keeps all four fields (it may
    still change one, e.g. ``effort``). A stamp that names an ``agent`` or a ``model`` is a
    new driver: it replaces the whole set it names and drops what it does not restate (the
    old effort belonged to the old model), so a delegate's identity never leaks into the next
    phase. Only ``host`` — where the run lives, whoever drives it — is always kept.
    """
    new_driver = "agent" in record or "model" in record
    names = ("host",) if new_driver else IDENTITY_FIELDS
    carried = {
        name: existing[name]
        for name in names
        if name not in record and existing and name in existing
    }
    return {**record, **carried} if carried else record


def validate_activity(record: Any) -> None:
    """Validate the stable activity-record shape."""
    if not isinstance(record, dict):
        raise ActivityError("activity must be an object")
    if record.get("schema_version") != ACTIVITY_SCHEMA_VERSION:
        raise ActivityError("unsupported schema_version")
    if record.get("record_type") != RECORD_TYPE_ACTIVITY:
        raise ActivityError("unsupported record_type")
    command = record.get("command")
    if not isinstance(command, str) or not flows.is_known(command):
        raise ActivityError("unsupported command")
    if not isinstance(record.get("run_id"), str) or not record["run_id"].strip():
        raise ActivityError("run_id must be a non-empty string")
    if record.get("phase") not in _phase_ids(command):
        raise ActivityError("unsupported phase")
    if record.get("status") not in STATUSES:
        raise ActivityError("unsupported status")
    # Absent is fine (older records, phases with nothing to pass); a *wrong* value is
    # not — a board that trusts this field must never read a typo as a pass.
    if record.get("verdict") is not None and record.get("verdict") not in VERDICTS:
        raise ActivityError("unsupported verdict")
    for name in IDENTITY_FIELDS:
        if name in record:
            problem = identity_issue(record[name])
            if problem is not None:
                raise ActivityError(f"{name}: {problem}")
    usage = record.get(USAGE_FIELD)
    if usage is not None:
        # The cost report prices whatever is here as measured, so a malformed entry is
        # refused at the door rather than read as a count.
        if not isinstance(usage, dict):
            raise ActivityError(f"{USAGE_FIELD} must be an object")
        for call_id, entry in usage.items():
            issue = usage_entry_issue(entry)
            if issue is not None:
                raise ActivityError(f"{USAGE_FIELD}[{call_id!r}]: {issue}")


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def usage_entry_issue(entry: Any) -> str | None:
    """Why ``entry`` is not a well-formed delegate-usage entry, or ``None`` when it is."""
    if not isinstance(entry, dict):
        return "entry must be an object"
    model = entry.get("model")
    if not isinstance(model, str) or not model.strip():
        return "model must be a non-empty string"
    for name in ("prompt_tokens", "completion_tokens"):
        if not _is_count(entry.get(name)):
            return f"{name} must be a non-negative integer"
    return None


def with_delegate_usage(
    record: dict[str, Any],
    call_id: str,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, Any]:
    """A copy of ``record`` that also carries one delegate call's token counts.

    Keyed by ``call_id``, so recording the same call again replaces its entry rather than
    adding it twice. The entry is validated here, not only on write, so a bad count fails
    before anything is built on it.
    """
    entry = {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    issue = usage_entry_issue(entry)
    if issue is not None:
        raise ActivityError(f"{USAGE_FIELD}: {issue}")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ActivityError(f"{USAGE_FIELD}: call id must be a non-empty string")
    updated = dict(record)
    updated[USAGE_FIELD] = {**(record.get(USAGE_FIELD) or {}), call_id: entry}
    return updated


def carry_usage(record: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    """``record`` with the delegate counts ``existing`` already held.

    Every phase stamp rebuilds the record from :func:`build_activity_record`, which knows
    nothing of counts; without this, the next stamp after a delegate recorded would erase
    what it recorded (#1373).
    """
    usage = (existing or {}).get(USAGE_FIELD)
    if not usage:
        return record
    return {**record, USAGE_FIELD: dict(usage)}


def encode_activity(record: dict[str, Any]) -> str:
    """Encode one activity record as stable JSON."""
    validate_activity(record)
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def parse_activity(text: str) -> dict[str, Any]:
    """Parse and validate one activity record."""
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ActivityError("invalid JSON") from exc
    validate_activity(record)
    return record


def read_activity(path: str | Path) -> dict[str, Any] | None:
    """Read one activity record; a missing file means no such run."""
    activity_path = Path(path)
    if not activity_path.exists():
        return None
    return parse_activity(activity_path.read_text(encoding="utf-8"))


def write_activity(path: str | Path, record: dict[str, Any]) -> None:
    """Write one validated activity record atomically."""
    workspace.write_text_atomic(path, encode_activity(record))


@contextlib.contextmanager
def record_lock(
    lock_root: str | Path,
    path: str | Path,
    *,
    owner: str,
    attempts: int | None = None,
    _sleep: Callable[[float], None] | None = None,
) -> Iterator[bool]:
    """Serialise the read-modify-write of one activity record; yields whether it is held.

    Detached reviewers finish independently, and two recording at once would each read
    the record without the other's counts and the second write would drop the first's.
    The claim is :mod:`keel.lock`'s atomic ``mkdir``. Bounded: after ``attempts`` tries —
    or on an I/O error claiming it — this yields ``False`` and the caller decides whether
    to go ahead unlocked (a phase stamp must land) or skip (a count may not be guessed).
    """
    # Resolved at call time, not bound as defaults, so the bound and the sleep can be
    # changed where they are defined rather than at every caller.
    attempts = LOCK_ATTEMPTS if attempts is None else attempts
    sleep = time.sleep if _sleep is None else _sleep
    resource = f"activity-{Path(path).stem}"
    held = False
    for attempt in range(attempts):
        try:
            held = lock.claim_resource(lock_root, resource, owner=owner).granted
        except OSError:
            break
        if held or attempt + 1 == attempts:
            break
        sleep(LOCK_POLL_S)
    try:
        yield held
    finally:
        if held:
            with contextlib.suppress(OSError):
                lock.release_resource(lock_root, resource, owner=owner, best_effort=True)


def record_delegate_usage(
    path: str | Path,
    lock_root: str | Path,
    *,
    call_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    attempts: int | None = None,
    _sleep: Callable[[float], None] | None = None,
) -> str:
    """Add one delegate call's counts to the run's existing record, under the record lock.

    Returns ``recorded``; ``no-record`` when the run has no record to add to (a count is
    never the thing that creates one — it has no phase); or ``busy`` when the lock could
    not be taken, in which case nothing is written rather than risking a lost update.
    Raises :class:`ActivityError` / ``OSError`` for a malformed record or a failed write.
    """
    with record_lock(lock_root, path, owner=call_id, attempts=attempts, _sleep=_sleep) as held:
        if not held:
            return "busy"
        existing = read_activity(path)
        if existing is None:
            return "no-record"
        write_activity(
            path,
            with_delegate_usage(
                existing,
                call_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
        )
    return "recorded"


def remove_activity(path: str | Path) -> bool:
    """Delete an activity record. Returns ``True`` if a file was removed."""
    activity_path = Path(path)
    if not activity_path.exists():
        return False
    activity_path.unlink()
    return True


def read_all_activity(dir_path: str | Path) -> list[dict[str, Any]]:
    """Every readable activity record in ``dir_path``, sorted by run_id.

    Fail-soft: an unreadable or malformed file is skipped, never raised — one bad
    record must not blank the board. The directory missing yields ``[]``.
    """
    directory = Path(dir_path)
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for entry in sorted(directory.glob("*.json")):
        try:
            record = parse_activity(entry.read_text(encoding="utf-8"))
        except (ActivityError, OSError):
            continue
        records.append(record)
    return records
