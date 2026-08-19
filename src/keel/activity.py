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
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from . import config as cfg
from . import flows, workspace

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
    if path.is_absolute():
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
    """
    if not flows.is_known(command):
        raise ActivityError(f"unknown command: {command!r}")
    if phase not in _phase_ids(command):
        raise ActivityError(f"phase {phase!r} is not a {command} flow phase")
    if status not in STATUSES:
        raise ActivityError(f"unsupported status: {status!r}")
    if verdict is not None and verdict not in VERDICTS:
        raise ActivityError(f"unsupported verdict: {verdict!r}")
    return {
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
    activity_path = Path(path)
    activity_path.parent.mkdir(parents=True, exist_ok=True)
    workspace.ensure_runtime_gitignore_for(activity_path)
    content = encode_activity(record)
    fd, temp_file = tempfile.mkstemp(
        prefix=f".{activity_path.name}.",
        dir=activity_path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_file, activity_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temp_file)
        raise


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
