"""Load + validate project Lego extensions snapped into named backbone slots.

An extension is a small markdown file with a YAML frontmatter mini-spec plus a
body (prompt or command). The contract (see ``docs/proposals/keel-architecture.md``):

* **Add-only.** An extension may only register into one of the named
  :data:`keel.model.SLOTS`; it can never remove/reorder/replace a backbone step.
* **Fail-soft.** A broken extension degrades to a no-op (``strict=False`` returns
  the problems instead of raising) — unless it declares itself a hard gate
  (``on_fail: block``, valid only in the ``pre-merge`` slot).
* **Agent-neutral.** Each extension declares its ``agent`` (default ``inherit``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import ProjectConfig
from .model import SLOTS

KINDS: tuple[str, ...] = ("agentic", "command")
ON_FAIL: tuple[str, ...] = ("warn", "suggest", "block")


class ExtensionError(ValueError):
    """Raised on a malformed extension (or, in strict mode, any load problem)."""


@dataclass(frozen=True)
class Extension:
    """A parsed, validated Lego piece."""

    id: str
    slot: str
    kind: str
    agent: str
    on_fail: str
    anchorable: bool
    run: str | None
    prompt: str | None
    body: str
    source: str


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---\\n…\\n---\\n<body>`` into (metadata dict, body). No fence ⇒ ({}, text)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta = yaml.safe_load("\n".join(lines[1:i])) or {}
            body = "\n".join(lines[i + 1:])
            return meta, body
    raise ExtensionError("unterminated frontmatter (no closing '---')")


def parse_extension(text: str, *, source: str, expected_slot: str | None = None) -> Extension:
    """Parse + validate one extension file's text into an :class:`Extension`."""
    meta, body = split_frontmatter(text)
    if not isinstance(meta, dict) or not meta:
        raise ExtensionError(f"{source}: missing frontmatter mini-spec")

    errors: list[str] = []
    ext_id = meta.get("id")
    slot = meta.get("slot")
    kind = meta.get("kind", "agentic")
    on_fail = meta.get("on_fail", "warn")
    agent = meta.get("agent", "inherit")
    run = meta.get("run")
    prompt = meta.get("prompt")

    if not ext_id:
        errors.append("missing 'id'")
    if not slot:
        errors.append("missing 'slot'")
    elif slot not in SLOTS:
        errors.append(f"unknown slot {slot!r}; valid: {', '.join(SLOTS)}")
    elif expected_slot is not None and slot != expected_slot:
        errors.append(
            f"slot {slot!r} does not match its registered slot ({expected_slot!r})"
        )

    if kind not in KINDS:
        errors.append(f"invalid kind {kind!r}; valid: {', '.join(KINDS)}")
    if on_fail not in ON_FAIL:
        errors.append(f"invalid on_fail {on_fail!r}; valid: {', '.join(ON_FAIL)}")
    elif on_fail == "block" and slot != "pre-merge":
        errors.append("on_fail: block (hard gate) is only allowed in the 'pre-merge' slot")

    if kind == "command" and not run:
        errors.append("a 'command' extension requires a 'run' value")
    if kind == "agentic" and not (prompt or body.strip()):
        errors.append("an 'agentic' extension requires a 'prompt' value or a body")

    if errors:
        raise ExtensionError(f"{source}: " + "; ".join(errors))

    return Extension(
        id=ext_id,
        slot=slot,
        kind=kind,
        agent=agent,
        on_fail=on_fail,
        anchorable=bool(meta.get("anchorable", False)),
        run=run,
        prompt=prompt,
        body=body,
        source=source,
    )


def load_extensions(
    config: ProjectConfig, repo_root: str | Path, *, strict: bool = True
) -> tuple[dict[str, list[Extension]], list[str]]:
    """Load every extension referenced by ``config`` from ``repo_root``.

    Returns ``(loaded, problems)`` where ``loaded`` maps each slot to its
    extensions in declared order. In ``strict`` mode any problem raises
    :class:`ExtensionError`; otherwise problems are returned (fail-soft) and the
    offending pieces are skipped.
    """
    ext_dir = Path(repo_root) / config.extensions_dir
    loaded: dict[str, list[Extension]] = {slot: [] for slot in SLOTS}
    problems: list[str] = []

    for slot in SLOTS:
        for fname in config.slot(slot):
            path = ext_dir / fname
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(f"{slot}: cannot read {fname}: {exc.strerror or exc}")
                continue
            try:
                loaded[slot].append(parse_extension(text, source=str(path), expected_slot=slot))
            except ExtensionError as exc:
                problems.append(str(exc))

    if strict and problems:
        raise ExtensionError("invalid extensions:\n  - " + "\n  - ".join(problems))
    return loaded, problems
