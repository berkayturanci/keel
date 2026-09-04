"""`keel init` — scaffold a default `.keel/project.yaml`, or build one with a wizard.

Pure + deterministic: :func:`detect_stack` is a function of which marker files exist,
:func:`render_config` renders YAML from explicit values, and :func:`wizard` builds those
values through an injectable `ask` callback (so the interactive flow is unit-tested
offline). The CLI supplies the real `input`-based `ask` and does the file I/O.

Since #1018 the wizard also asks who is on the **team**. The questions come from
:mod:`keel.wizard`, closed over a catalogue of the providers the probe found on this
machine, and their answers are rendered here as the ``knobs.team`` block (#1014) — so
a repository is scaffolded naming seats that actually exist rather than seats copied
out of somebody else's example.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import consent
from . import wizard as wizard_core
from . import yaml_helper as yaml

#: marker file (checked in order) -> stack name.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("pubspec.yaml", "flutter"),
    ("build.gradle", "android"),
    ("build.gradle.kts", "android"),
    ("pom.xml", "java"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements.txt", "python"),
    ("Pipfile", "python"),
    ("package.json", "node"),
)

#: per-stack defaults: platform, build cmd, lint cmd (or None), tier-3 globs.
_TEMPLATES: dict[str, dict] = {
    "flutter": {
        "platform": "flutter",
        "build": "flutter test",
        "lint": "flutter analyze",
        "globs": ("lib/**/*.dart",),
    },
    "python": {
        "platform": "python",
        "build": "make test",
        "lint": "ruff check .",
        "globs": ("src/**/*.py",),
    },
    "node": {
        "platform": "node",
        "build": "npm test",
        "lint": "npm run lint",
        "globs": ("src/**/*.ts", "src/**/*.js"),
    },
    "android": {
        "platform": "android",
        "build": "./gradlew test",
        "lint": "./gradlew lint",
        "globs": ("app/src/**",),
    },
    "rust": {
        "platform": "rust",
        "build": "cargo test",
        "lint": "cargo clippy",
        "globs": ("src/**/*.rs",),
    },
    "go": {
        "platform": "go",
        "build": "go test ./...",
        "lint": "golangci-lint run",
        "globs": ("**/*.go",),
    },
    "java": {
        "platform": "java",
        "build": "mvn test",
        "lint": "mvn checkstyle:check",
        "globs": ("src/main/**",),
    },
    "generic": {"platform": "generic", "build": "make test", "lint": None, "globs": ()},
}


def detect_stack(root: str | Path) -> str:
    """Detect the project stack from marker files (``generic`` if none match)."""
    root = Path(root)
    for marker, stack in _MARKERS:
        if (root / marker).exists():
            return stack
    return "generic"


def detect_base_branch(root: str | Path) -> str:
    """Detect the repository's default base branch (defaults to ``main``)."""
    root = Path(root)
    head_file = root / ".git" / "HEAD"
    if head_file.exists():
        try:
            content = head_file.read_text(encoding="utf-8").strip()
            if content.startswith("ref: refs/heads/"):
                ref_name = content[len("ref: refs/heads/") :].strip()
                if ref_name in ("main", "master", "develop", "trunk"):
                    return ref_name
        except OSError:
            return "main"
    return "main"


def auto_detect_config(
    root: str | Path,
    *,
    repo: str = "my-repo",
) -> tuple[str, dict]:
    """Inspect the repository stack and base branch, returning (yaml_text, metadata)."""
    root = Path(root)
    stack = detect_stack(root)
    base_branch = detect_base_branch(root)
    t = _TEMPLATES.get(stack, _TEMPLATES["generic"])
    meta = {
        "stack": stack,
        "platform": t["platform"],
        "base_branch": base_branch,
        "build_cmd": t["build"],
        "lint_cmd": t["lint"],
        "tier3_globs": t["globs"],
    }
    text = render_config(
        repo=repo,
        base_branch=base_branch,
        platform=t["platform"],
        build_cmd=t["build"],
        lint_cmd=t["lint"],
        tier3_globs=t["globs"],
        generator="keel init --auto",
    )
    return text, meta


def render_config(
    *,
    repo: str = "my-repo",
    base_branch: str = "main",
    platform: str = "generic",
    build_cmd: str = "make test",
    lint_cmd: str | None = None,
    tier3_globs: tuple[str, ...] = (),
    timezone: str | None = None,
    merge_window: str | None = None,
    consent_mode: str = "explicit",
    team: dict[str, Any] | None = None,
    generator: str = "keel init",
) -> str:
    """Render a valid ``project.yaml`` from explicit values (passes ``keel validate``).

    ``team`` is a ``knobs.team`` block (:meth:`keel.wizard.Resolution.team_block`).
    ``None`` — the default — writes no block at all, which is *not* the same as writing
    an empty one: an absent ``team`` leaves ``config_hash`` exactly where it was for
    every project that never opted in (:func:`keel.team.canonical`).
    """
    if consent_mode not in consent.CONSENT_MODES:
        raise ValueError(
            f"unknown consent mode {consent_mode!r}; valid: {', '.join(consent.CONSENT_MODES)}"
        )
    generator_comment = " ".join(str(generator).splitlines())
    lines = [
        f"# keel consumer config (generated by `{generator_comment}`)",
        "extends: keel",
        'core_version: "^1.0"',
        f"repo: {_yaml_scalar(repo)}",
        f"base_branch: {_yaml_scalar(base_branch)}",
        f"platform: {_yaml_scalar(platform)}",
        f"consent_mode: {_yaml_scalar(consent_mode)}",
    ]
    if timezone:
        lines.append(f"timezone: {_yaml_scalar(timezone)}")
    if merge_window:
        lines.append(f"merge_window: {_yaml_scalar(merge_window)}")
    lines += ["", "knobs:", f"  build_gate_cmd: {_yaml_scalar(build_cmd)}"]
    if team:
        lines.append("  team:")
        lines.extend(_render_mapping(team, 2))
    if lint_cmd:
        lines.append(f"  lint_cmd: {_yaml_scalar(lint_cmd)}")
    if tier3_globs:
        lines.append("  tier3_globs:")
        lines.extend(_render_sequence(list(tier3_globs), 2))
    gates = "[build, lint]" if lint_cmd else "[build]"
    lines += ["", f"gates: {gates}", "extensions: {}", "extensions_dir: .keel/extensions", ""]
    return "\n".join(lines)


def _yaml_scalar(value: str) -> str:
    """Render a scalar as inline YAML so scaffolded values cannot inject new keys."""
    return yaml.dump(
        str(value),
        default_style='"',
        default_flow_style=True,
        width=10**6,
        sort_keys=False,
    ).strip()


def _yaml_key(key: str) -> str:
    """A mapping key, quoted unless it is a plain identifier.

    The tier keys of ``review.by_tier`` are the reason: ``"1"`` has to stay quoted
    because YAML reads a bare ``1:`` as an *integer* key, which the schema cannot
    describe and :func:`keel.team._tier_key_issues` rejects by name.
    """
    return key if key.isidentifier() else _yaml_scalar(key)


def _yaml_value(value: Any) -> str:
    return str(value) if isinstance(value, int) else _yaml_scalar(value)


def _render_mapping(data: dict[str, Any], indent: int) -> list[str]:
    """Block-style YAML for a nested mapping of scalars, mappings and lists."""
    pad = "  " * indent
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{_yaml_key(key)}:")
            lines.extend(_render_mapping(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{pad}{_yaml_key(key)}:")
            lines.extend(_render_sequence(value, indent + 1))
        else:
            lines.append(f"{pad}{_yaml_key(key)}: {_yaml_value(value)}")
    return lines


def _render_sequence(items: list[Any], indent: int) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            rendered = _render_mapping(item, indent + 1)
            lines.append(f"{pad}- {rendered[0].strip()}")
            lines.extend(rendered[1:])
        else:
            lines.append(f"{pad}- {_yaml_value(item)}")
    return lines


def default_config(stack: str, *, repo: str = "my-repo", base_branch: str = "main") -> str:
    """Render the default ``project.yaml`` for ``stack`` (non-interactive)."""
    t = _TEMPLATES.get(stack, _TEMPLATES["generic"])
    return render_config(
        repo=repo,
        base_branch=base_branch,
        platform=t["platform"],
        build_cmd=t["build"],
        lint_cmd=t["lint"],
        tier3_globs=t["globs"],
    )


def _ignore(_message: str) -> None:
    """Default ``notify``: a caller that did not ask for feedback gets none."""


def wizard(
    stack: str,
    ask: Callable[[str, str], str],
    *,
    repo: str = "my-repo",
    catalog: wizard_core.Catalog | None = None,
    notify: Callable[[str], None] | None = None,
) -> str:
    """Build a config by asking for each value, defaulting to the stack template.

    ``ask(prompt, default)`` returns the chosen value (an empty answer ⇒ the default).
    Pure given ``ask`` — the CLI passes a real `input`-based implementation.

    ``catalog`` is the providers the probe found usable on this machine (#1018). Given
    one, the wizard adds the **team step**: who implements, who gives the mandatory
    gate review, who reviews at each risk tier, and how the jury gates — every option
    drawn from the catalogue, so the scaffolded ``knobs.team`` cannot name a provider
    this machine has never had. Without one (or with an empty one — a machine where
    nothing is installed yet) the step is skipped and no ``team`` block is written.
    """
    t = _TEMPLATES.get(stack, _TEMPLATES["generic"])
    base = ask("Base branch", "main")
    tz = ask("Timezone (IANA, blank to skip)", "Europe/Istanbul")
    win = ask("Merge window HH:MM-HH:MM (blank to skip)", "07:00-01:30")
    mode = ask("Consent mode (explicit, standing, agent)", "explicit") or "explicit"
    build = ask("Build/test command", t["build"])
    lint = ask("Lint command (blank to skip)", t["lint"] or "")
    return render_config(
        repo=repo,
        base_branch=base,
        platform=t["platform"],
        build_cmd=build,
        lint_cmd=lint or None,
        tier3_globs=t["globs"],
        timezone=tz or None,
        merge_window=win or None,
        consent_mode=mode,
        team=team_block(ask, catalog, notify=notify),
        generator="keel init --wizard",
    )


def team_block(
    ask: Callable[[str, str], str],
    catalog: wizard_core.Catalog | None,
    *,
    notify: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Ask the team questions and return the ``knobs.team`` block (``None`` to skip)."""
    # Narrowed before the emptiness check, not after: a machine whose only providers are
    # registry entries has nothing a committed policy could name, and a wizard that asked
    # anyway would write a `team` block `keel validate` then refuses.
    catalog = None if catalog is None else wizard_core.committable(catalog)
    if catalog is None or not catalog.candidates:
        return None
    state = wizard_core.start(catalog, scope=wizard_core.SCOPE_CONFIG)
    answered = wizard_core.run(state, ask, notify if notify is not None else _ignore)
    return answered.resolve().team_block()
