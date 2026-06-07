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

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import __version__

ADAPTERS = Path(__file__).parent / "adapters" / "commands"

#: native Claude slash-command dir (namespaced under ``keel/``).
CLAUDE_DIR = ".claude/commands/keel"
#: the universal skill dir every non-Claude agent reads.
SKILLS_DIR = ".agents/skills"
#: skill name prefix, so keel skills sit beside the project's own (e.g. ``source-command-*``).
SKILL_PREFIX = "keel-"

#: the logical install surfaces (``all`` fans over these).
TARGETS: tuple[str, ...] = ("claude", "skills")

MARKER_RE = re.compile(r"\n?<!-- keel-generated: (?P<meta>[^>]*) -->\n?$")


@dataclass(frozen=True)
class AdapterFileStatus:
    surface: str
    name: str
    path: str
    status: str
    detail: str = ""
    source_sha256: str = ""
    installed_sha256: str = ""
    expected_sha256: str = ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _marker(surface: str, command: str, source_text: str, generated_text: str) -> str:
    return (
        "<!-- keel-generated: "
        f"surface={surface} command={command} keel_version={__version__} "
        f"source_sha256={_sha256(source_text)} generated_sha256={_sha256(generated_text)} "
        "-->"
    )


def _with_marker(surface: str, command: str, source_text: str, generated_text: str) -> str:
    marker = _marker(surface, command, source_text, generated_text)
    return f"{generated_text.rstrip()}\n\n{marker}\n"


def _split_marker(text: str) -> tuple[str, dict[str, str]]:
    match = MARKER_RE.search(text)
    if not match:
        return text, {}
    body = text[:match.start()].rstrip() + "\n"
    meta: dict[str, str] = {}
    for part in match.group("meta").split():
        if "=" in part:
            key, value = part.split("=", 1)
            meta[key] = value
    return body, meta


def _expected_files(_src: Path | None = None) -> dict[str, dict[str, tuple[Path, str, str, str]]]:
    src = _src or ADAPTERS
    expected: dict[str, dict[str, tuple[Path, str, str, str]]] = {"claude": {}, "skills": {}}
    for f in sorted(src.glob("*.md")):
        source_text = f.read_text(encoding="utf-8")
        command = f.stem
        expected["claude"][f.name] = (
            Path(CLAUDE_DIR) / f.name,
            command,
            source_text,
            source_text,
        )
        expected["skills"][f"{SKILL_PREFIX}{command}"] = (
            Path(SKILLS_DIR) / f"{SKILL_PREFIX}{command}" / "SKILL.md",
            command,
            source_text,
            render_skill(source_text, command),
        )
    return expected


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
        source_text = f.read_text(encoding="utf-8")
        dest.write_text(_with_marker("claude", f.stem, source_text, source_text),
                        encoding="utf-8")
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
        source_text = f.read_text(encoding="utf-8")
        rendered = render_skill(source_text, f.stem)
        dest.write_text(_with_marker("skills", f.stem, source_text, rendered),
                        encoding="utf-8")
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


def adapter_status(
    agent: str, root: str | Path, *, _src: Path | None = None
) -> dict[str, list[AdapterFileStatus]]:
    """Report installed adapter freshness for one surface or ``all`` surfaces."""
    targets = TARGETS if agent == "all" else (agent,)
    if any(t not in TARGETS for t in targets):
        raise KeyError(agent)
    root_path = Path(root)
    expected = _expected_files(_src)
    out: dict[str, list[AdapterFileStatus]] = {}
    for surface in targets:
        rows: list[AdapterFileStatus] = []
        for name, (rel, _command, source_text, generated_text) in expected[surface].items():
            path = root_path / rel
            expected_hash = _sha256(generated_text)
            source_hash = _sha256(source_text)
            if not path.exists():
                rows.append(AdapterFileStatus(surface, name, str(rel), "missing",
                                              expected_sha256=expected_hash,
                                              source_sha256=source_hash))
                continue
            body, marker = _split_marker(path.read_text(encoding="utf-8"))
            installed_hash = _sha256(body)
            if not marker:
                rows.append(AdapterFileStatus(surface, name, str(rel), "unknown",
                                              "missing keel-generated marker",
                                              installed_sha256=installed_hash,
                                              expected_sha256=expected_hash,
                                              source_sha256=source_hash))
            elif installed_hash != marker.get("generated_sha256"):
                rows.append(AdapterFileStatus(surface, name, str(rel), "locally-modified",
                                              "generated file changed after install",
                                              installed_sha256=installed_hash,
                                              expected_sha256=expected_hash,
                                              source_sha256=source_hash))
            elif marker.get("source_sha256") != source_hash or installed_hash != expected_hash:
                rows.append(AdapterFileStatus(surface, name, str(rel), "outdated",
                                              "packaged adapter source changed",
                                              installed_sha256=installed_hash,
                                              expected_sha256=expected_hash,
                                              source_sha256=source_hash))
            else:
                rows.append(AdapterFileStatus(surface, name, str(rel), "current",
                                              installed_sha256=installed_hash,
                                              expected_sha256=expected_hash,
                                              source_sha256=source_hash))
        out[surface] = rows
    return out


def update_adapters(
    agent: str,
    root: str | Path,
    *,
    dry_run: bool = False,
    _src: Path | None = None,
) -> dict[str, list[AdapterFileStatus]]:
    """Update generated adapter files that are missing or outdated.

    Locally-modified or unknown files are reported and left untouched.
    """
    targets = TARGETS if agent == "all" else (agent,)
    if any(t not in TARGETS for t in targets):
        raise KeyError(agent)
    root_path = Path(root)
    expected = _expected_files(_src)
    before = adapter_status(agent, root, _src=_src)
    updated: dict[str, list[AdapterFileStatus]] = {t: [] for t in targets}
    for surface in targets:
        rows_by_name = {row.name: row for row in before[surface]}
        for name, (rel, command, source_text, generated_text) in expected[surface].items():
            row = rows_by_name[name]
            if row.status not in {"missing", "outdated"}:
                updated[surface].append(row)
                continue
            if not dry_run:
                path = root_path / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_with_marker(surface, command, source_text, generated_text),
                                encoding="utf-8")
            updated[surface].append(AdapterFileStatus(surface, name, str(rel), "would-update"
                                                      if dry_run else "updated",
                                                      row.detail,
                                                      source_sha256=row.source_sha256,
                                                      installed_sha256=row.installed_sha256,
                                                      expected_sha256=row.expected_sha256))
    return updated
