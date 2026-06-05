"""`keel install-adapter` — install the packaged command adapters into a project.

keel ships its agentic workflows once (as markdown under ``keel/adapters/commands/``) and
installs them into the **two surfaces** that match how agents actually discover commands —
never one copy per agent (that would re-introduce the very file-copy drift keel removes):

- ``claude`` — native slash commands at ``.claude/commands/keel/<cmd>.md`` → ``/keel:<cmd>``.
- ``skills`` — a **single, shared** skill set at ``.agents/skills/keel-<cmd>/SKILL.md`` that
  every non-Claude agent (Codex, Antigravity, Gemini, …) discovers via the repo's skill
  mechanism / "chat command wrapper". One universal copy, not one dir per agent.

``all`` installs both. The skill body is the same project-neutral adapter (it leans on the
``keel`` CLI), wrapped with skill frontmatter so the agents' skill discovery picks it up.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ADAPTERS = Path(__file__).parent / "adapters" / "commands"

#: native Claude slash-command dir (namespaced under ``keel/``).
CLAUDE_DIR = ".claude/commands/keel"
#: the universal skill dir every non-Claude agent reads.
SKILLS_DIR = ".agents/skills"
#: skill name prefix, so keel skills sit beside the project's own (e.g. ``source-command-*``).
SKILL_PREFIX = "keel-"

#: the logical install surfaces (``all`` fans over these).
TARGETS: tuple[str, ...] = ("claude", "skills")


def adapter_names(*, _src: Path | None = None) -> list[str]:
    """The command adapters that ship with keel (e.g. ``ship.md``, ``regression.md``)."""
    return sorted(p.name for p in (_src or ADAPTERS).glob("*.md"))


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` YAML frontmatter from a markdown body. Returns ``(meta, body)``."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1])
            return (meta if isinstance(meta, dict) else {}), parts[2].lstrip("\n")
    return {}, text


def render_skill(adapter_text: str, command: str) -> str:
    """Render an adapter command markdown as a ``.agents/skills`` SKILL.md (pure).

    Lifts the adapter's ``description`` into skill frontmatter (``name: keel-<command>``) and
    keeps the full project-neutral body, so non-Claude agents discover and run it as a skill.
    """
    meta, body = _split_frontmatter(adapter_text)
    desc = " ".join(str(meta.get("description", f"keel {command} workflow")).split())
    name = f"{SKILL_PREFIX}{command}"
    front = yaml.safe_dump({"name": name, "description": desc},
                           sort_keys=False, allow_unicode=True, width=10**9).strip()
    intro = (
        f"Use this skill when the user asks to run the keel command `{command}` "
        f"(e.g. `keel {command} ...`, `{command} <args>`, or `/keel:{command}`). It reads every "
        f"project value from `.keel/project.yaml` via the `keel` CLI."
    )
    return f"---\n{front}\n---\n\n# {name}\n\n{intro}\n\n{body}"


def _install_commands(
    root: str | Path, *, force: bool, _src: Path | None
) -> tuple[list[str], list[str]]:
    target = Path(root) / CLAUDE_DIR
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


def _install_skills(
    root: str | Path, *, force: bool, _src: Path | None
) -> tuple[list[str], list[str]]:
    src = _src or ADAPTERS
    base = Path(root) / SKILLS_DIR
    installed: list[str] = []
    skipped: list[str] = []
    for f in sorted(src.glob("*.md")):
        name = f"{SKILL_PREFIX}{f.stem}"
        dest = base / name / "SKILL.md"
        if dest.exists() and not force:
            skipped.append(name)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_skill(f.read_text(encoding="utf-8"), f.stem), encoding="utf-8")
        installed.append(name)
    return installed, skipped


def install(
    agent: str, root: str | Path, *, force: bool = False, _src: Path | None = None
) -> tuple[list[str], list[str]]:
    """Install one surface into ``root``. ``agent`` is ``claude`` or ``skills``.

    Returns ``(installed, skipped)`` names. Existing files are skipped unless ``force``.
    Raises :class:`KeyError` for an unknown surface.
    """
    if agent == "claude":
        return _install_commands(root, force=force, _src=_src)
    if agent == "skills":
        return _install_skills(root, force=force, _src=_src)
    raise KeyError(agent)


def install_all(
    root: str | Path, *, force: bool = False, _src: Path | None = None
) -> dict[str, tuple[list[str], list[str]]]:
    """Install **both** surfaces (Claude commands + the universal skill set).

    Returns ``surface -> (installed, skipped)`` for each entry in :data:`TARGETS`.
    """
    return {t: install(t, root, force=force, _src=_src) for t in TARGETS}
