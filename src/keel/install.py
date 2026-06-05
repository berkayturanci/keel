"""`keel install-adapter` — copy the packaged command adapters into a project's agent dir.

The agentic slash/prompt commands (`/keel:ship`, `/keel:regression`, …) ship *with* the keel
package as markdown under ``keel/adapters/commands/``; this installs them into a consumer's
agent command directory so they appear as ``/keel:<command>`` — no hand-copying, no drift.
"""

from __future__ import annotations

from pathlib import Path

ADAPTERS = Path(__file__).parent / "adapters" / "commands"

#: agent → the project-relative directory its commands live in (namespaced under ``keel/``).
AGENT_DIRS: dict[str, str] = {
    "claude": ".claude/commands/keel",
    "codex": ".codex/prompts/keel",
    "gemini": ".gemini/commands/keel",
    "agy": ".agents/keel",
}


def adapter_names(*, _src: Path | None = None) -> list[str]:
    """The command adapters that ship with keel (e.g. ``ship.md``, ``regression.md``)."""
    return sorted(p.name for p in (_src or ADAPTERS).glob("*.md"))


def install(
    agent: str, root: str | Path, *, force: bool = False, _src: Path | None = None
) -> tuple[list[str], list[str]]:
    """Copy the command adapters into ``root/<agent dir>``.

    Returns ``(installed, skipped)`` file names. Existing files are skipped unless ``force``.
    Raises :class:`KeyError` for an unknown agent.
    """
    target = Path(root) / AGENT_DIRS[agent]
    src = _src or ADAPTERS
    target.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    skipped: list[str] = []
    for f in sorted(src.glob("*.md")):
        dest = target / f.name
        if dest.exists() and not force:
            skipped.append(f.name)
            continue
        dest.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        installed.append(f.name)
    return installed, skipped


def install_all(
    root: str | Path, *, force: bool = False, _src: Path | None = None
) -> dict[str, tuple[list[str], list[str]]]:
    """Install the adapters into **every** known agent dir.

    Returns ``agent -> (installed, skipped)`` for each entry in :data:`AGENT_DIRS`, so one
    invocation sets up Claude, Codex, Gemini and the agents dir together.
    """
    return {agent: install(agent, root, force=force, _src=_src) for agent in AGENT_DIRS}
