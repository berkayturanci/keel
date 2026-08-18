"""Pure diagnostics for ``keel doctor`` — a read-only health pass.

This module is **pure**: no network, no wall-clock, no randomness. The caller
(``keel.cli``) performs all I/O — fetching the latest version from PyPI, reading
adapter markers off disk, loading config, probing state-path existence — and
passes the already-gathered facts into :func:`run_doctor`, which classifies each
check as ``ok`` / ``warn`` / ``fail`` and returns a structured, JSON-stable
result. Every branch here is deterministic and unit-tested.

Checks
------
``cli_version``      installed ``keel.__version__`` vs latest on PyPI (the
                     headline check — a silent downgrade is a ``fail``).
``adapter_version``  ``keel_version=`` markers on installed adapter surfaces vs
                     the running CLI version.
``orphan_adapters``  surfaces whose ``command=`` is no longer in the installed
                     keel (stale-marker orphans).
``core_version``     ``core_version`` constraint from project.yaml vs the
                     installed CLI version.
``state_paths``      existence/validity of the configured ledger + checkpoint
                     paths (advisory; missing == empty history, not a defect).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath

SCHEMA_VERSION = "keel.doctor.v1"

#: per-check status levels, ordered worst-last for summary roll-up.
_OK = "ok"
_WARN = "warn"
_FAIL = "fail"
_RANK = {_OK: 0, _WARN: 1, _FAIL: 2}

#: a release version: ``MAJOR.MINOR.PATCH`` with optional further dotted parts.
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
#: a ``core_version`` constraint: an optional operator (``^`` / ``~`` / ``>=`` /
#: ``==``) followed by a dotted version. A bare version means exact match.
_CONSTRAINT_RE = re.compile(r"^(?P<op>\^|~|>=|<=|>|<|==|=)?\s*(?P<version>\d+(?:\.\d+)*)$")


@dataclass(frozen=True)
class CheckResult:
    """One diagnostic check outcome (JSON-stable via :meth:`as_dict`)."""

    name: str
    status: str
    summary: str
    detail: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "detail": dict(self.detail),
        }


def _parse_version(text: str) -> tuple[int, ...] | None:
    """Parse a dotted release version into a comparable tuple (``None`` if unparseable)."""
    if not isinstance(text, str) or not _VERSION_RE.match(text.strip()):
        return None
    return tuple(int(part) for part in text.strip().split("."))


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Right-pad the shorter tuple with zeros so the two compare component-wise."""
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)), b + (0,) * (width - len(b))


def constraint_satisfied(installed: str, constraint: str) -> bool | None:
    """Does ``installed`` satisfy the ``core_version`` ``constraint``?

    Supports ``^`` (caret: same leading non-zero, ``>=``), ``~`` (tilde: same
    major+minor, ``>=``), the comparison operators (``>=``, ``<=``, ``>``,
    ``<``, ``==``/``=``), and a bare version (exact match). Returns ``None`` when
    either side is unparseable so the caller can report ``unknown`` rather than
    guess. Pure and deterministic.
    """
    inst = _parse_version(installed)
    match = _CONSTRAINT_RE.match(constraint.strip()) if isinstance(constraint, str) else None
    if inst is None or match is None:
        return None
    want = _parse_version(match.group("version"))
    if want is None:  # pragma: no cover - regex already guarantees a parseable version
        return None
    op = match.group("op") or "=="
    pi, pw = _pad(inst, want)
    if op == "^":
        # caret: pin the most-significant non-zero component, then ``>=``.
        lead = next((i for i, part in enumerate(want) if part != 0), len(want) - 1)
        return pi[lead] == pw[lead] and pi[:lead] == pw[:lead] and pi >= pw
    if op == "~":
        # tilde: pin major+minor (or major when no minor given), then ``>=``.
        pin = min(2, len(want))
        return pi[:pin] == pw[:pin] and pi >= pw
    if op in (">=",):
        return pi >= pw
    if op in (">",):
        return pi > pw
    if op in ("<=",):
        return pi <= pw
    if op in ("<",):
        return pi < pw
    return pi == pw  # ``==`` / ``=`` / bare


def _check_cli_version(installed: str, latest: str | None) -> CheckResult:
    """Installed CLI vs latest on PyPI. Offline => ``warn`` (unknown); stale => ``fail``."""
    if latest is None:
        return CheckResult(
            "cli_version", _WARN,
            f"installed {installed}; latest unknown (offline or PyPI unreachable)",
            {"installed": installed, "latest": "unknown"},
        )
    inst, lat = _parse_version(installed), _parse_version(latest)
    detail = {"installed": installed, "latest": latest}
    if inst is None or lat is None:
        return CheckResult(
            "cli_version", _WARN,
            f"installed {installed}; latest {latest}; cannot compare versions",
            detail,
        )
    pi, pl = _pad(inst, lat)
    if pi < pl:
        return CheckResult(
            "cli_version", _FAIL,
            f"installed {installed} is behind latest {latest} — upgrade keel-workflow",
            detail,
        )
    if pi > pl:
        return CheckResult(
            "cli_version", _WARN,
            f"installed {installed} is ahead of latest {latest} (pre-release or unpublished)",
            detail,
        )
    return CheckResult(
        "cli_version", _OK, f"installed {installed} is up to date", detail,
    )


def _check_adapter_version(installed: str, markers: list[dict[str, object]]) -> CheckResult:
    """Installed adapter ``keel_version`` markers vs the running CLI version."""
    if not markers:
        return CheckResult(
            "adapter_version", _WARN,
            "no keel-generated adapter surfaces found under --root",
            {"installed": installed, "surfaces": 0, "drift": []},
        )
    drift = []
    for marker in markers:
        marker_version = marker.get("keel_version")
        if marker_version != installed:
            drift.append({
                "surface": marker.get("surface", ""),
                "name": marker.get("name", ""),
                "keel_version": marker_version,
            })
    if drift:
        return CheckResult(
            "adapter_version", _WARN,
            f"{len(drift)} of {len(markers)} adapter surface(s) drift from CLI {installed} "
            "— run keel update-adapter",
            {"installed": installed, "surfaces": len(markers), "drift": drift},
        )
    return CheckResult(
        "adapter_version", _OK,
        f"all {len(markers)} adapter surface(s) match CLI {installed}",
        {"installed": installed, "surfaces": len(markers), "drift": []},
    )


def _check_orphan_adapters(orphans: list[dict[str, object]]) -> CheckResult:
    """Surfaces whose command is no longer in the installed keel (stale-marker orphans)."""
    if not orphans:
        return CheckResult(
            "orphan_adapters", _OK, "no orphan adapter surfaces", {"orphans": []},
        )
    return CheckResult(
        "orphan_adapters", _WARN,
        f"{len(orphans)} orphan adapter surface(s) — command(s) no longer in installed keel",
        {"orphans": list(orphans)},
    )


def _check_core_version(installed: str, core_version: str | None) -> CheckResult:
    """``core_version`` constraint from project.yaml vs the installed CLI version."""
    if core_version is None:
        return CheckResult(
            "core_version", _OK, "no project config given — core_version check skipped",
            {"installed": installed, "core_version": None},
        )
    detail = {"installed": installed, "core_version": core_version}
    satisfied = constraint_satisfied(installed, core_version)
    if satisfied is None:
        return CheckResult(
            "core_version", _WARN,
            f"cannot evaluate core_version {core_version!r} against installed {installed}",
            detail,
        )
    if not satisfied:
        return CheckResult(
            "core_version", _FAIL,
            f"installed {installed} does not satisfy core_version {core_version!r}",
            detail,
        )
    return CheckResult(
        "core_version", _OK,
        f"installed {installed} satisfies core_version {core_version!r}",
        detail,
    )


def _check_state_paths(state_paths: list[dict[str, object]]) -> CheckResult:
    """Advisory check on configured ledger/checkpoint paths — missing == empty history."""
    if not state_paths:
        return CheckResult(
            "state_paths", _OK, "no state paths configured", {"paths": []},
        )
    for entry in state_paths:
        if entry.get("status") == "invalid":
            present = sum(1 for e in state_paths if e.get("status") == "present")
            return CheckResult(
                "state_paths", _WARN,
                "one or more configured state paths are invalid",
                {"paths": list(state_paths), "present": present},
            )
    present = sum(1 for e in state_paths if e.get("status") == "present")
    return CheckResult(
        "state_paths", _OK,
        f"{present} of {len(state_paths)} state path(s) present "
        "(missing paths report as empty history)",
        {"paths": list(state_paths), "present": present},
    )


def _within(child: str, parent: str) -> bool:
    """Is ``child`` ``parent`` itself, or nested inside it? Pure path-part comparison."""
    parent_parts = PurePath(parent).parts
    return PurePath(child).parts[:len(parent_parts)] == parent_parts


def _check_checkout_binding(module_path: str | None, checkout_root: str | None) -> CheckResult:
    """Is the importable ``keel`` the checkout this command is pointed at?

    ``pip install -e .`` writes a single source tree into site-packages for the
    whole interpreter, so installing from a second checkout silently repoints
    every other one: imports, the test suite, and coverage all follow the other
    tree while the working directory suggests otherwise.

    A mismatch is a ``warn``, never a ``fail`` — running against a deliberately
    installed keel (a release, a pinned build) is legitimate, so this informs
    without changing anyone's exit code.
    """
    if not checkout_root:
        return CheckResult(
            "checkout_binding", _OK,
            "not run against a keel checkout; binding not checked",
            {"module_path": module_path, "checkout_root": None},
        )
    detail: dict[str, object] = {"module_path": module_path, "checkout_root": checkout_root}
    if not module_path:
        return CheckResult(
            "checkout_binding", _WARN,
            "the importable keel could not be located", detail,
        )
    if _within(module_path, checkout_root):
        return CheckResult(
            "checkout_binding", _OK,
            "importable keel resolves inside this checkout", detail,
        )
    return CheckResult(
        "checkout_binding", _WARN,
        f"importable keel resolves outside this checkout ({module_path}) — local runs "
        "exercise that tree; reinstall with `pip install -e .` from here", detail,
    )


def run_doctor(
    *,
    installed_version: str,
    latest_version: str | None,
    adapter_markers: list[dict[str, object]],
    orphans: list[dict[str, object]],
    core_version: str | None,
    state_paths: list[dict[str, object]],
    module_path: str | None = None,
    checkout_root: str | None = None,
) -> dict[str, object]:
    """Run all diagnostic checks over already-gathered facts (pure, deterministic).

    Returns a JSON-stable dict: ``schema_version``, ``installed_version``, the
    ordered ``checks`` list, and a roll-up ``status`` (worst of all checks) plus
    counts. The caller maps ``status`` to an exit code (and ``--strict`` turns a
    ``fail`` roll-up into a non-zero exit).
    """
    checks = [
        _check_checkout_binding(module_path, checkout_root),
        _check_cli_version(installed_version, latest_version),
        _check_adapter_version(installed_version, adapter_markers),
        _check_orphan_adapters(orphans),
        _check_core_version(installed_version, core_version),
        _check_state_paths(state_paths),
    ]
    worst = max((c.status for c in checks), key=lambda s: _RANK[s])
    counts = {_OK: 0, _WARN: 0, _FAIL: 0}
    for check in checks:
        counts[check.status] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "installed_version": installed_version,
        "status": worst,
        "counts": counts,
        "checks": [c.as_dict() for c in checks],
    }


def render_report(report: dict[str, object]) -> str:
    """Render a doctor report as aligned human-readable status lines."""
    lines = [f"keel doctor — {report['status']}  (keel {report['installed_version']})"]
    for check in report["checks"]:
        state = str(check["status"]).upper()
        lines.append(f"  {state:>4}  {check['name']:<16}  {check['summary']}")
    counts = report["counts"]
    lines.append(
        f"  summary       : {counts[_OK]} ok, {counts[_WARN]} warn, {counts[_FAIL]} fail"
    )
    return "\n".join(lines)
