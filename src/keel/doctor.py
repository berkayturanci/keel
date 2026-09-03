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
``python_toolchain`` the interpreter the build gate will actually run on, its
                     version, and whether PyYAML imports there (advisory).
``providers``        which delegates are usable on this machine — only when
                     ``--providers`` asked for the probe (#1011).
``policy_labels``    the labels the project's policy pack (and keel's own
                     attribution vocabulary) declare, vs the labels that exist on
                     the repository (#1021).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePath

SCHEMA_VERSION = "keel.doctor.v1"

#: per-check status levels, ordered worst-last for summary roll-up. ``skipped`` is
#: a *reported* outcome, not a passing one: a check that could not look (no config,
#: no ``gh``, ``--offline``) says so instead of claiming ``ok``, and ranks with
#: ``ok`` so it never moves the roll-up.
_OK = "ok"
_SKIPPED = "skipped"
_WARN = "warn"
_FAIL = "fail"
_RANK = {_OK: 0, _SKIPPED: 0, _WARN: 1, _FAIL: 2}

#: a release version: ``MAJOR.MINOR.PATCH`` with optional further dotted parts.
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
#: the lowest Python keel supports — ``requires-python`` in ``pyproject.toml``.
MIN_PYTHON = (3, 11)
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
            "cli_version",
            _WARN,
            f"installed {installed}; latest unknown (offline or PyPI unreachable)",
            {"installed": installed, "latest": "unknown"},
        )
    inst, lat = _parse_version(installed), _parse_version(latest)
    detail = {"installed": installed, "latest": latest}
    if inst is None or lat is None:
        return CheckResult(
            "cli_version",
            _WARN,
            f"installed {installed}; latest {latest}; cannot compare versions",
            detail,
        )
    pi, pl = _pad(inst, lat)
    if pi < pl:
        return CheckResult(
            "cli_version",
            _FAIL,
            f"installed {installed} is behind latest {latest} — upgrade keel-workflow",
            detail,
        )
    if pi > pl:
        return CheckResult(
            "cli_version",
            _WARN,
            f"installed {installed} is ahead of latest {latest} (pre-release or unpublished)",
            detail,
        )
    return CheckResult(
        "cli_version",
        _OK,
        f"installed {installed} is up to date",
        detail,
    )


def _check_adapter_version(installed: str, markers: list[dict[str, object]]) -> CheckResult:
    """Installed adapter ``keel_version`` markers vs the running CLI version."""
    if not markers:
        return CheckResult(
            "adapter_version",
            _WARN,
            "no keel-generated adapter surfaces found under --root",
            {"installed": installed, "surfaces": 0, "drift": []},
        )
    drift = []
    for marker in markers:
        marker_version = marker.get("keel_version")
        if marker_version != installed:
            drift.append(
                {
                    "surface": marker.get("surface", ""),
                    "name": marker.get("name", ""),
                    "keel_version": marker_version,
                }
            )
    if drift:
        return CheckResult(
            "adapter_version",
            _WARN,
            f"{len(drift)} of {len(markers)} adapter surface(s) drift from CLI {installed} "
            "— run keel update-adapter",
            {"installed": installed, "surfaces": len(markers), "drift": drift},
        )
    return CheckResult(
        "adapter_version",
        _OK,
        f"all {len(markers)} adapter surface(s) match CLI {installed}",
        {"installed": installed, "surfaces": len(markers), "drift": []},
    )


def _check_orphan_adapters(orphans: list[dict[str, object]]) -> CheckResult:
    """Surfaces whose command is no longer in the installed keel (stale-marker orphans)."""
    if not orphans:
        return CheckResult(
            "orphan_adapters",
            _OK,
            "no orphan adapter surfaces",
            {"orphans": []},
        )
    return CheckResult(
        "orphan_adapters",
        _WARN,
        f"{len(orphans)} orphan adapter surface(s) — command(s) no longer in installed keel",
        {"orphans": list(orphans)},
    )


def _check_core_version(installed: str, core_version: str | None) -> CheckResult:
    """``core_version`` constraint from project.yaml vs the installed CLI version."""
    if core_version is None:
        return CheckResult(
            "core_version",
            _OK,
            "no project config given — core_version check skipped",
            {"installed": installed, "core_version": None},
        )
    detail = {"installed": installed, "core_version": core_version}
    satisfied = constraint_satisfied(installed, core_version)
    if satisfied is None:
        return CheckResult(
            "core_version",
            _WARN,
            f"cannot evaluate core_version {core_version!r} against installed {installed}",
            detail,
        )
    if not satisfied:
        return CheckResult(
            "core_version",
            _FAIL,
            f"installed {installed} does not satisfy core_version {core_version!r}",
            detail,
        )
    return CheckResult(
        "core_version",
        _OK,
        f"installed {installed} satisfies core_version {core_version!r}",
        detail,
    )


def _check_state_paths(state_paths: list[dict[str, object]]) -> CheckResult:
    """Advisory check on configured ledger/checkpoint paths — missing == empty history."""
    if not state_paths:
        return CheckResult(
            "state_paths",
            _OK,
            "no state paths configured",
            {"paths": []},
        )
    for entry in state_paths:
        if entry.get("status") == "invalid":
            present = sum(1 for e in state_paths if e.get("status") == "present")
            return CheckResult(
                "state_paths",
                _WARN,
                "one or more configured state paths are invalid",
                {"paths": list(state_paths), "present": present},
            )
    present = sum(1 for e in state_paths if e.get("status") == "present")
    return CheckResult(
        "state_paths",
        _OK,
        f"{present} of {len(state_paths)} state path(s) present "
        "(missing paths report as empty history)",
        {"paths": list(state_paths), "present": present},
    )


def _check_python_toolchain(toolchain: dict[str, object] | None) -> CheckResult:
    """Will the build gate's interpreter satisfy ``requires-python`` + PyYAML?

    The facts (which interpreter the gate resolves to, its version, whether
    ``yaml`` imports there) are gathered by the caller — this only classifies
    them. Never a ``fail``: keel cannot know that a red gate is *this* problem,
    only that the interpreter behind it would produce one. A ``warn`` names the
    interpreter, so a `make test` that dies with a hundred syntax errors reads
    as a 3.9 on PATH rather than as a regression in the tree (#1022).
    """
    if toolchain is None:
        return CheckResult(
            "python_toolchain",
            _OK,
            "build-gate interpreter not probed",
            {},
        )
    detail = dict(toolchain)
    interpreter = toolchain.get("interpreter")
    reason = str(toolchain.get("reason") or "no interpreter resolved")
    if not interpreter:
        return CheckResult(
            "python_toolchain",
            _WARN,
            f"the build gate has no usable interpreter — {reason}",
            detail,
        )
    version = toolchain.get("version")
    parsed = _parse_version(version) if isinstance(version, str) else None
    if parsed is None:
        return CheckResult(
            "python_toolchain",
            _WARN,
            f"the build gate runs on {interpreter}, whose version is unknown — {reason}",
            detail,
        )
    minimum = ".".join(str(part) for part in MIN_PYTHON)
    problems = []
    if parsed < MIN_PYTHON:
        problems.append(f"Python {version} is below the required {minimum}")
    if not toolchain.get("yaml"):
        problems.append("PyYAML is not importable there")
    if problems:
        return CheckResult(
            "python_toolchain",
            _WARN,
            f"the build gate would run on {interpreter}: {'; '.join(problems)}",
            detail,
        )
    return CheckResult(
        "python_toolchain",
        _OK,
        f"the build gate runs on {interpreter} (Python {version}, PyYAML present)",
        detail,
    )


def _within(child: str, parent: str) -> bool:
    """Is ``child`` ``parent`` itself, or nested inside it? Pure path-part comparison."""
    parent_parts = PurePath(parent).parts
    return PurePath(child).parts[: len(parent_parts)] == parent_parts


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
            "checkout_binding",
            _OK,
            "not run against a keel checkout; binding not checked",
            {"module_path": module_path, "checkout_root": None},
        )
    detail: dict[str, object] = {"module_path": module_path, "checkout_root": checkout_root}
    if not module_path:
        return CheckResult(
            "checkout_binding",
            _WARN,
            "the importable keel could not be located",
            detail,
        )
    if _within(module_path, checkout_root):
        return CheckResult(
            "checkout_binding",
            _OK,
            "importable keel resolves inside this checkout",
            detail,
        )
    return CheckResult(
        "checkout_binding",
        _WARN,
        f"importable keel resolves outside this checkout ({module_path}) — local runs "
        "exercise that tree; reinstall with `pip install -e .` from here",
        detail,
    )


def _check_providers(payload: dict[str, object]) -> CheckResult:
    """Classify an already-probed provider report (#1011).

    Pure, like every other check: :mod:`keel.providerprobe` did the PATH lookups,
    the ``--version`` calls and the one loopback HTTP request, and hands the facts in.

    A name clash is a **fail**: a registry entry that shadows a built-in vendor or a
    project profile is a configuration error the operator has to resolve, and the
    entry is not being used meanwhile. A malformed registry, or a machine where no
    provider at all is usable, is a ``warn`` — keel still runs on its host agent.
    """
    available = int(payload.get("available", 0) or 0)
    total = int(payload.get("total", 0) or 0)
    errors = list(payload.get("errors") or [])
    warnings = list(payload.get("warnings") or [])
    detail = {
        "available": available,
        "total": total,
        "registry_path": payload.get("registry_path"),
        "registry_present": payload.get("registry_present", False),
        "errors": errors,
        "warnings": warnings,
    }
    summary = f"{available} of {total} provider(s) available"
    if errors:
        return CheckResult("providers", _FAIL, f"{summary}; {errors[0]}", detail)
    if warnings:
        return CheckResult("providers", _WARN, f"{summary}; {warnings[0]}", detail)
    if not available:
        return CheckResult(
            "providers",
            _WARN,
            f"{summary} — no delegate is usable on this machine",
            detail,
        )
    return CheckResult("providers", _OK, summary, detail)


def _label_values(value: object) -> list[str]:
    """The non-empty strings in a policy-pack label list (anything else is ignored)."""
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _qualify(group: str, name: str) -> str:
    """Qualify a bare vocabulary entry with its group: ``role`` + ``core`` -> ``role:core``.

    A policy pack may spell a label either way — ``status: ["status:backlog"]`` carries
    the group already, ``role: ["core"]`` does not — and both mean the same GitHub label.
    Anything already carrying a ``:`` is taken as the full label name.
    """
    return name if ":" in name else f"{group}:{name}"


def declared_labels(
    policy_pack: object,
    *,
    attribution: Iterable[str] = (),
) -> tuple[str, ...]:
    """Every label name a project's policy pack requires to exist on its repository.

    Three sources, all of them labels keel itself writes: ``policy_pack.labels.*``
    (the status/priority/role vocabularies ship and triage apply),
    ``policy_pack.scan.issue_labels.*`` (what the scan-and-file commands stamp on an
    issue they open), and ``attribution`` — the ``agent:*`` / ``model:*`` names from
    :func:`keel.agents.attribution_labels`, passed in so this module stays free of
    config types. Pure and deterministic: the result is sorted and deduplicated.
    """
    pack = policy_pack if isinstance(policy_pack, dict) else {}
    names: set[str] = set()
    groups = pack.get("labels")
    if isinstance(groups, dict):
        for group, values in groups.items():
            names.update(_qualify(str(group), name) for name in _label_values(values))
    scan = pack.get("scan")
    issue_labels = scan.get("issue_labels") if isinstance(scan, dict) else None
    if isinstance(issue_labels, dict):
        for values in issue_labels.values():
            names.update(_label_values(values))
    names.update(name.strip() for name in attribution if name.strip())
    return tuple(sorted(names))


def missing_labels(declared: Iterable[str], existing: Iterable[str]) -> tuple[str, ...]:
    """The declared labels that do not exist on the repository.

    Compared case-insensitively because GitHub label names are: creating ``Bug`` on a
    repository that already has ``bug`` is rejected as a duplicate, so a case-only
    difference is a label that exists, not one to create.
    """
    have = {name.strip().lower() for name in existing}
    return tuple(sorted({name for name in declared if name.strip().lower() not in have}))


#: Missing labels named in the check summary before the rest are counted.
_LABELS_SHOWN = 5


def _check_policy_labels(payload: dict[str, object] | None) -> CheckResult:
    """Do the labels this project declares actually exist on its repository (#1021)?

    ``ship`` and ``triage`` apply ``status:*`` / ``priority:*`` / ``role:*`` and the
    ``agent:*`` / ``model:*`` attribution pair by name. GitHub rejects a label that was
    never created, and the rejection surfaces as a failed ``gh`` call in the middle of a
    run rather than as a diagnosis — keel's own repository ran for months with every one
    of those labels missing and nothing said so.

    Never a ``fail``: the caller cannot always look (no config, no ``gh`` on PATH,
    ``--offline``, an unauthenticated or unreachable GitHub), and a check that could not
    look reports ``skipped`` with the reason. Missing labels are a ``warn`` carrying the
    exact ``gh label create`` commands, which ``keel doctor --fix`` runs for you.
    """
    if payload is None:
        return CheckResult(
            "policy_labels",
            _SKIPPED,
            "no project config given — no policy pack to check",
            {},
        )
    declared = list(payload.get("declared") or [])
    missing = list(payload.get("missing") or [])
    repo = payload.get("repo")
    detail: dict[str, object] = {
        "repo": repo,
        "declared": declared,
        "missing": missing,
        "commands": list(payload.get("commands") or []),
        "existing": len(list(payload.get("existing") or [])),
    }
    if not payload.get("available"):
        reason = str(payload.get("reason") or "repository labels not read")
        return CheckResult("policy_labels", _SKIPPED, reason, detail)
    if missing:
        shown = ", ".join(missing[:_LABELS_SHOWN])
        extra = f", +{len(missing) - _LABELS_SHOWN} more" if len(missing) > _LABELS_SHOWN else ""
        return CheckResult(
            "policy_labels",
            _WARN,
            f"{len(missing)} of {len(declared)} declared label(s) missing on {repo}: "
            f"{shown}{extra} — create them, or run keel doctor --fix",
            detail,
        )
    return CheckResult(
        "policy_labels",
        _OK,
        f"all {len(declared)} declared label(s) exist on {repo}",
        detail,
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
    python_toolchain: dict[str, object] | None = None,
    policy_labels: dict[str, object] | None = None,
    providers: dict[str, object] | None = None,
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
        _check_python_toolchain(python_toolchain),
        _check_policy_labels(policy_labels),
    ]
    # Only when asked for: the provider probe shells out once per CLI vendor and makes
    # one loopback request, which the default run must not pay for on every invocation.
    if providers is not None:
        checks.append(_check_providers(providers))
    # A skipped check reports that it could not look; it never speaks for the roll-up.
    # ``checkout_binding`` is always ``ok`` or ``warn``, so this is never empty.
    worst = max((c.status for c in checks if c.status != _SKIPPED), key=lambda s: _RANK[s])
    counts = {_OK: 0, _SKIPPED: 0, _WARN: 0, _FAIL: 0}
    for check in checks:
        counts[check.status] += 1
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "installed_version": installed_version,
        "status": worst,
        "counts": counts,
        "checks": [c.as_dict() for c in checks],
    }
    if providers is not None:
        # Merged at the top level rather than nested: the provider document is the
        # thing `--providers` was asked for, and `{providers, registry_path,
        # warnings}` is the shape #1011 specifies for it.
        for key in ("providers", "registry_path", "registry_present", "warnings", "errors"):
            report[key] = providers.get(key)
    return report


def render_report(report: dict[str, object]) -> str:
    """Render a doctor report as aligned human-readable status lines."""
    lines = [f"keel doctor — {report['status']}  (keel {report['installed_version']})"]
    for check in report["checks"]:
        # Four characters keeps the column aligned: OK / WARN / FAIL / SKIP(ped).
        state = str(check["status"]).upper()[:4]
        lines.append(f"  {state:>4}  {check['name']:<16}  {check['summary']}")
        # A check that can name its own fix prints it as a runnable line rather than
        # burying it in --json, which is the whole point of the policy-label warning.
        for command in check.get("detail", {}).get("commands") or ():
            lines.append(f"        $ {command}")
    counts = report["counts"]
    lines.append(
        f"  summary       : {counts[_OK]} ok, {counts[_SKIPPED]} skipped, "
        f"{counts[_WARN]} warn, {counts[_FAIL]} fail"
    )
    return "\n".join(lines)


#: Compact capability flags in the provider table, in a fixed order.
_CAPABILITY_FLAGS = (
    ("tools", "tools"),
    ("read_only_mode", "read-only"),
    ("model_selection", "model"),
)

#: Models listed per provider row before the rest are summarised as a count.
_MODELS_SHOWN = 6


def render_providers(payload: dict[str, object]) -> str:
    """Render the provider probe as an aligned human table (pure).

    One row per provider in probe order — built-ins, then project profiles, then the
    machine-level registry — each naming the transport, where the entry came from, and
    a reason an operator can act on. Registry warnings and name-clash errors follow the
    table rather than replacing it: a broken entry must not hide the providers that do
    work.
    """
    rows = list(payload.get("providers") or [])
    registry = payload.get("registry_path") or "(none)"
    state = "present" if payload.get("registry_present") else "not present"
    lines = [
        f"keel providers — {payload.get('available', 0)} of {payload.get('total', 0)} available",
        f"  registry: {registry} ({state})",
    ]
    for row in rows:
        flags = row.get("capabilities") or {}
        marks = ",".join(label for key, label in _CAPABILITY_FLAGS if flags.get(key)) or "-"
        state = "yes" if row.get("available") else "no"
        lines.append(
            f"  {state:>3}  {str(row.get('name')):<18} {str(row.get('transport')):<6} "
            f"{str(row.get('source')):<8} {marks:<22} {row.get('reason')}"
        )
        models = list(row.get("models") or [])
        if models:
            shown = ", ".join(models[:_MODELS_SHOWN])
            extra = f", +{len(models) - _MODELS_SHOWN} more" if len(models) > _MODELS_SHOWN else ""
            lines.append(f"       models: {shown}{extra}")
    for warning in payload.get("warnings") or []:
        lines.append(f"  warn  {warning}")
    for error in payload.get("errors") or []:
        lines.append(f"  FAIL  {error}")
    return "\n".join(lines)
