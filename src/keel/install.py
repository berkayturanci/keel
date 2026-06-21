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

from . import __version__
from . import yaml_helper as yaml

ADAPTERS = Path(__file__).parent / "adapters" / "commands"

#: native Claude slash-command dir (namespaced under ``keel/``).
CLAUDE_DIR = ".claude/commands/keel"
#: the universal skill dir every non-Claude agent reads.
SKILLS_DIR = ".agents/skills"
#: skill name prefix, so keel skills sit beside the project's own (e.g. ``source-command-*``).
SKILL_PREFIX = "keel-"

#: Claude Code plugin command dir (flat ``.md`` files at the plugin root). The plugin is
#: named ``keel``, so a flat ``commands/<cmd>.md`` is discovered as ``/keel:<cmd>`` — the same
#: surface as the native ``claude`` install, packaged for ``/plugin install keel``.
PLUGIN_COMMANDS_DIR = "commands"
#: the committed plugin manifest + marketplace catalog live here.
PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
PLUGIN_MARKETPLACE = ".claude-plugin/marketplace.json"

#: the logical install surfaces (``all`` fans over these).
TARGETS: tuple[str, ...] = ("claude", "skills")
STATUS_TARGETS: tuple[str, ...] = ("claude", "skills", "legacy-claude")
LEGACY_TARGETS: tuple[str, ...] = ("claude", "skills")
LEGACY_CLAUDE_DIR = ".claude/commands"
LEGACY_SKILL_PREFIX = "source-command-"
PARITY_READY_STATUSES = frozenset({"parity-proven", "deferred"})

MARKER_RE = re.compile(r"\n?<!-- keel-generated: (?P<meta>[^>]*) -->\n?$")


@dataclass(frozen=True)
class OrphanFileStatus:
    """A file under a managed surface directory that keel does not currently manage.

    ``category`` is ``"orphan"`` (deterministic, class (a)) or ``"unmanaged"`` (heuristic,
    class (b)). ``reason`` is a stable reason code; ``command`` is the marker's ``command=``
    for a stale-marker orphan, or the file stem for a marker-less surface.
    """

    surface: str
    name: str
    path: str
    category: str
    reason: str
    command: str = ""

    def as_dict(self) -> dict[str, str]:
        """Render as JSON-compatible contract data (sorted-stable)."""
        return {
            "surface": self.surface,
            "name": self.name,
            "path": self.path,
            "category": self.category,
            "reason": self.reason,
            "command": self.command,
        }


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
    expected: dict[str, dict[str, tuple[Path, str, str, str]]] = {
        "claude": {},
        "skills": {},
        "legacy-claude": {},
    }
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
    expected["legacy-claude"] = _legacy_expected_files(
        default_legacy_mappings(_src=_src), _src=_src
    )["claude"]
    return expected


def adapter_names(*, _src: Path | None = None) -> list[str]:
    """The command adapters that ship with keel (e.g. ``ship.md``, ``regression.md``)."""
    return sorted(p.name for p in (_src or ADAPTERS).glob("*.md"))


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` YAML frontmatter from a markdown body. Returns ``(meta, body)``."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.load(parts[1])
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
    front = yaml.dump({"name": name, "description": desc},
                           sort_keys=False, allow_unicode=True, width=10**9).strip()
    intro = (
        f"Use this skill when the user asks to run the keel command `{command}` "
        f"(e.g. `keel {command} ...`, `{command} <args>`, or `/keel:{command}`). It reads every "
        f"project value from `.keel/project.yaml` via the `keel` CLI."
    )
    return f"---\n{front}\n---\n\n# {name}\n\n{intro}\n\n{body}"


def render_legacy_claude_wrapper(legacy_command: str, keel_command: str) -> str:
    """Render a native legacy slash-command shim that delegates to ``/keel:<command>``."""
    return (
        f"# /{legacy_command}\n\n"
        f"This legacy command is now a thin compatibility wrapper for `/keel:{keel_command}`.\n\n"
        "Before doing any mutating work, run:\n\n"
        "```bash\n"
        f"keel plan .keel/project.yaml --root . --command {keel_command} --live --json \"$@\"\n"
        "```\n\n"
        f"Then execute `/keel:{keel_command}` with the user's original arguments and flags "
        "unchanged. Preserve dry-run, jury/no-jury, review-comment mode, merge behavior, "
        "issue targeting, PR targeting, and any project policy exposed by `.keel/project.yaml` "
        "or `.keel/extensions/`. Do not duplicate the keel workflow body here; the installed "
        f"`/keel:{keel_command}` adapter is the source of truth.\n\n"
        "If the plan reports missing consent, unavailable required capabilities, or an "
        "unverified migration row, stop and report that blocker instead of guessing.\n"
    )


def render_legacy_skill_wrapper(legacy_command: str, keel_command: str) -> str:
    """Render a shared skill shim for non-Claude agents that delegates to ``keel-<command>``."""
    name = f"{LEGACY_SKILL_PREFIX}{legacy_command}"
    desc = (
        f"Compatibility wrapper for the legacy `{legacy_command}` command; delegates to "
        f"`/keel:{keel_command}` and the `keel-{keel_command}` skill without changing flags."
    )
    front = yaml.dump({"name": name, "description": desc},
                           sort_keys=False, allow_unicode=True, width=10**9).strip()
    return (
        f"---\n{front}\n---\n\n"
        f"# {name}\n\n"
        f"Use this skill when the user asks for the legacy `{legacy_command}` command. "
        f"This is a thin compatibility wrapper for the project-neutral `keel-{keel_command}` "
        f"skill and `/keel:{keel_command}` command.\n\n"
        "1. Preserve the user's original issue or PR target and every flag, including "
        "`--dry-run`, jury/no-jury choices, review-comment mode, and merge-mode flags.\n"
        "2. Run a live structured preflight before mutating state:\n\n"
        "```bash\n"
        f"keel plan .keel/project.yaml --root . --command {keel_command} --live --json\n"
        "```\n\n"
        f"3. Delegate to the `keel-{keel_command}` skill. Do not copy or reinterpret the "
        "workflow body in this wrapper.\n\n"
        "Stop if consent, capabilities, or parity verification is missing.\n"
    )


def parity_ready_commands(matrix_text: str) -> set[str]:
    """Return keel command names whose parity-matrix rows are ready for legacy wrappers."""
    ready: set[str] = set()
    for line in matrix_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("| `") or "`/keel:" not in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        match = re.search(r"`/keel:([^`]+)`", cells[1])
        status = cells[2].strip("`")
        if match and status in PARITY_READY_STATUSES:
            ready.add(match.group(1))
    return ready


def _legacy_expected_files(
    mappings: dict[str, str], *, _src: Path | None = None
) -> dict[str, dict[str, tuple[Path, str, str, str]]]:
    src = _src or ADAPTERS
    expected: dict[str, dict[str, tuple[Path, str, str, str]]] = {"claude": {}, "skills": {}}
    for legacy, command in sorted(mappings.items()):
        source = src / f"{command}.md"
        source_text = source.read_text(encoding="utf-8")
        claude_body = render_legacy_claude_wrapper(legacy, command)
        expected["claude"][f"{legacy}.md"] = (
            Path(LEGACY_CLAUDE_DIR) / f"{legacy}.md",
            command,
            source_text,
            claude_body,
        )
        skill_name = f"{LEGACY_SKILL_PREFIX}{legacy}"
        skill_body = render_legacy_skill_wrapper(legacy, command)
        expected["skills"][skill_name] = (
            Path(SKILLS_DIR) / skill_name / "SKILL.md",
            command,
            source_text,
            skill_body,
        )
    return expected


def default_legacy_mappings(*, _src: Path | None = None) -> dict[str, str]:
    """Default one-to-one legacy wrapper mapping for every packaged adapter command."""
    return {Path(name).stem: Path(name).stem for name in adapter_names(_src=_src)}


def _validate_legacy_mappings(
    mappings: dict[str, str],
    *,
    ready_commands: set[str] | None,
    _src: Path | None,
) -> None:
    packaged = {Path(name).stem for name in adapter_names(_src=_src)}
    for legacy, command in mappings.items():
        if not legacy or not command:
            raise ValueError("legacy wrapper mappings must use non-empty command names")
        if command not in packaged:
            raise ValueError(f"unknown keel command for legacy wrapper: {command}")
        if ready_commands is not None and command not in ready_commands:
            raise ValueError(f"keel command is not parity-ready for legacy wrapper: {command}")


def install_legacy_wrappers(
    agent: str,
    root: str | Path,
    *,
    mappings: dict[str, str] | None = None,
    ready_commands: set[str] | None = None,
    force: bool = False,
    _src: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Install thin legacy compatibility wrappers for one legacy surface."""
    if agent not in LEGACY_TARGETS:
        raise KeyError(agent)
    wrapper_mappings = mappings or default_legacy_mappings(_src=_src)
    _validate_legacy_mappings(wrapper_mappings, ready_commands=ready_commands, _src=_src)
    expected = _legacy_expected_files(wrapper_mappings, _src=_src)[agent]
    root_path = Path(root)
    installed: list[str] = []
    skipped: list[str] = []
    surface = f"legacy-{agent}"
    for name, (rel, command, source_text, generated_text) in expected.items():
        dest = root_path / rel
        if dest.exists() and not force:
            skipped.append(name)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_with_marker(surface, command, source_text, generated_text),
                        encoding="utf-8")
        installed.append(name)
    return installed, skipped


def install_all_legacy_wrappers(
    root: str | Path,
    *,
    mappings: dict[str, str] | None = None,
    ready_commands: set[str] | None = None,
    force: bool = False,
    _src: Path | None = None,
) -> dict[str, tuple[list[str], list[str]]]:
    """Install legacy compatibility wrappers into both supported discovery surfaces."""
    return {
        target: install_legacy_wrappers(
            target,
            root,
            mappings=mappings,
            ready_commands=ready_commands,
            force=force,
            _src=_src,
        )
        for target in LEGACY_TARGETS
    }


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


def plugin_files(*, _src: Path | None = None) -> dict[str, str]:
    """Render the committed Claude Code plugin command files (pure).

    Returns a mapping of ``commands/<cmd>.md`` → file content, generated **from the same**
    ``adapters/commands/*.md`` bodies that drive the ``claude`` install surface. The plugin is
    named ``keel``, so each flat command file is discovered as ``/keel:<cmd>`` once the plugin
    is installed via ``/plugin install keel``. This is the single source of truth for the
    repo-level ``commands/`` directory — the drift test asserts the committed files match.
    """
    src = _src or ADAPTERS
    out: dict[str, str] = {}
    for f in sorted(src.glob("*.md")):
        source_text = f.read_text(encoding="utf-8")
        rel = f"{PLUGIN_COMMANDS_DIR}/{f.name}"
        out[rel] = _with_marker("plugin", f.stem, source_text, source_text)
    return out


def install_plugin(
    root: str | Path, *, force: bool = False, _src: Path | None = None
) -> tuple[list[str], list[str]]:
    """Write the generated plugin command files into ``root/commands/`` (idempotent).

    Used by ``keel install-adapter plugin`` and ``make plugin`` to regenerate the committed
    plugin command bodies. Unlike the per-project surfaces, this writes the repo-level plugin
    files; ``force`` is unnecessary because the generator is deterministic, but existing files
    are overwritten so the committed copy always tracks ``adapters/commands/``.
    """
    root_path = Path(root)
    installed: list[str] = []
    skipped: list[str] = []
    for rel, content in plugin_files(_src=_src).items():
        dest = root_path / rel
        existing = dest.read_text(encoding="utf-8") if dest.exists() else None
        if existing == content and not force:
            skipped.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        installed.append(rel)
    return installed, skipped


def adapter_status(
    agent: str, root: str | Path, *, _src: Path | None = None
) -> dict[str, list[AdapterFileStatus]]:
    """Report installed adapter freshness for one surface or ``all`` surfaces."""
    targets = STATUS_TARGETS if agent == "all" else (agent,)
    if any(t not in STATUS_TARGETS for t in targets):
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
                # Legacy claude wrappers are opt-in (``install-legacy-wrappers``).
                # An absent wrapper means "not installed", not a defect, so it is
                # not reported as ``missing`` — that would flag every project that
                # never opted in. Installed legacy wrappers are still freshness-checked.
                if surface == "legacy-claude":
                    continue
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


#: managed surface directories scanned for orphan / unmanaged files.
#: each entry is ``(surface, relative-dir, file-glob, recurse)`` where ``recurse`` selects
#: ``rglob`` (skill ``SKILL.md`` bodies live one directory deeper) over ``glob``.
_ORPHAN_SCAN: tuple[tuple[str, str, str, bool], ...] = (
    ("plugin", PLUGIN_COMMANDS_DIR, "*.md", False),
    ("claude", CLAUDE_DIR, "*.md", False),
    ("legacy-claude", LEGACY_CLAUDE_DIR, "*.md", False),
    ("skills", SKILLS_DIR, "SKILL.md", True),
)

ORPHAN_STALE_MARKER = "orphan"
UNMANAGED_NO_MARKER = "unmanaged"


def default_known_commands(*, _src: Path | None = None) -> set[str]:
    """The command stems keel currently manages: packaged adapters + default legacy targets.

    A surface whose marker ``command=`` is in this set is recognised; anything else carrying a
    keel marker is a stale-marker orphan. Pure and deterministic.
    """
    packaged = {Path(name).stem for name in adapter_names(_src=_src)}
    legacy = set(default_legacy_mappings(_src=_src).values())
    return packaged | legacy


def _surface_command_from_name(surface: str, name: str) -> str:
    """Best-effort command stem for a marker-less file under a managed surface."""
    stem = Path(name).stem
    if surface == "skills":
        # skill dirs are ``keel-<cmd>`` / ``source-command-<cmd>``; the file is ``SKILL.md``.
        parent = Path(name).parent.name
        for prefix in (SKILL_PREFIX, LEGACY_SKILL_PREFIX):
            if parent.startswith(prefix):
                return parent[len(prefix):]
        return parent
    return stem


def scan_surface_orphans(
    root: str | Path,
    *,
    known_commands: set[str],
    project_only: set[str] | None = None,
    include_unmanaged: bool = False,
    _src: Path | None = None,
) -> list[OrphanFileStatus]:
    """Scan managed surface directories for files keel no longer manages (pure, deterministic).

    Class (a) — **deterministic**: a file carrying a ``keel-generated`` marker whose
    ``command=`` is not in ``known_commands`` is reported as ``orphan (stale-marker)``.

    Class (b) — **heuristic, opt-in**: a file with **zero** keel markers is reported as
    ``unmanaged (no-marker)`` only when ``include_unmanaged`` is set, and never when its
    command stem is declared ``project_only``.

    ``known_commands`` is the installed/packaged command set (``adapter_names`` stems plus any
    legacy-mapping target stems). The scan only reads on-disk files; it never deletes.
    """
    project_only = project_only or set()
    root_path = Path(root)
    out: list[OrphanFileStatus] = []
    for surface, rel_dir, pattern, recurse in _ORPHAN_SCAN:
        base = root_path / rel_dir
        if not base.is_dir():
            continue
        matches = base.rglob(pattern) if recurse else base.glob(pattern)
        for path in sorted(matches):
            if not path.is_file():
                continue
            name = path.relative_to(base).as_posix()
            _body, marker = _split_marker(path.read_text(encoding="utf-8"))
            if marker:
                command = marker.get("command", "")
                if command in known_commands:
                    continue  # a recognised, managed surface — not an orphan.
                out.append(OrphanFileStatus(
                    surface, name, str(path.relative_to(root_path).as_posix()),
                    ORPHAN_STALE_MARKER,
                    f"stale-marker: command {command!r} not in installed keel",
                    command,
                ))
                continue
            # no marker: heuristic, opt-in only.
            if not include_unmanaged:
                continue
            command = _surface_command_from_name(surface, name)
            if command in project_only:
                continue  # declared project-only command — never flagged.
            out.append(OrphanFileStatus(
                surface, name, str(path.relative_to(root_path).as_posix()),
                UNMANAGED_NO_MARKER,
                "no-marker: command-like surface not keel-managed",
                command,
            ))
    return out


def scan_adapter_markers(root: str | Path) -> list[dict[str, str]]:
    """Read the ``keel_version`` markers off every installed adapter surface (pure).

    Reuses :data:`_ORPHAN_SCAN` and :func:`_split_marker` so the marker source of
    truth is shared with the orphan scan. Returns one entry per marker-bearing
    surface: ``surface``, ``name``, ``command``, and ``keel_version`` (the value of
    the ``keel_version=`` marker field, or ``""`` when absent). Marker-less files
    are skipped. Deterministic and read-only.
    """
    root_path = Path(root)
    out: list[dict[str, str]] = []
    for surface, rel_dir, pattern, recurse in _ORPHAN_SCAN:
        base = root_path / rel_dir
        if not base.is_dir():
            continue
        matches = base.rglob(pattern) if recurse else base.glob(pattern)
        for path in sorted(matches):
            if not path.is_file():
                continue
            _body, marker = _split_marker(path.read_text(encoding="utf-8"))
            if not marker:
                continue
            out.append({
                "surface": surface,
                "name": path.relative_to(base).as_posix(),
                "command": marker.get("command", ""),
                "keel_version": marker.get("keel_version", ""),
            })
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
