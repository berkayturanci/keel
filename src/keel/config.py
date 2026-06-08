"""Load + validate a keel ``project.yaml`` into a typed, immutable config.

Pure and deterministic: parsing the same YAML always yields the same
``ProjectConfig`` and the same :func:`config_hash`. The only I/O is reading the
file in :func:`load_config`; everything else operates on plain data so it is
trivially unit-testable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import jsonschema_min
from .capabilities import validate_names
from .model import SLOTS  # single source of truth for the named slots (re-exported)

SCHEMA_PATH = Path(__file__).parent / "schema" / "project.schema.json"

DEFAULT_EXTENSIONS_DIR = ".keel/extensions"

__all__ = ["SLOTS", "DEFAULT_EXTENSIONS_DIR", "Knobs", "ProjectConfig", "ConfigError",
           "load_config", "parse_config", "validate_data", "load_schema", "config_hash"]


class ConfigError(ValueError):
    """Raised when a project config fails schema validation."""

    def __init__(self, source: str, errors: list[str]):
        self.source = source
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"invalid keel config {source}:\n  - {joined}")


def load_schema() -> dict:
    """Load the bundled JSON Schema for ``project.yaml``."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_data(data: Any, schema: dict | None = None) -> list[str]:
    """Return schema-validation errors for raw config data (empty == valid)."""
    return jsonschema_min.validate(data, schema if schema is not None else load_schema())


@dataclass(frozen=True)
class Knobs:
    """Per-project values consumed by the (otherwise neutral) backbone steps."""

    build_gate_cmd: str
    lint_cmd: str | None = None
    implementer_agents: dict[str, str] = field(default_factory=dict)
    tier3_globs: tuple[str, ...] = ()
    ci_workflows: dict[str, str] = field(default_factory=dict)
    docs_gate_paths: tuple[str, ...] = ()
    docs_only_allowlist: tuple[str, ...] = ()
    sot_doc: str | None = None
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectConfig:
    """A resolved, immutable keel project config."""

    extends: str
    core_version: str
    base_branch: str
    knobs: Knobs
    owner: str | None = None
    repo: str | None = None
    platform: str | None = None
    timezone: str | None = None
    merge_window: str | None = None
    merge_window_mode: str = "freeze"
    gates: tuple[str, ...] = ()
    extensions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    extensions_dir: str = DEFAULT_EXTENSIONS_DIR
    policy_pack: dict[str, Any] = field(default_factory=dict)

    def slot(self, name: str) -> tuple[str, ...]:
        """Extension files registered for a named slot (``()`` if none)."""
        if name not in SLOTS:
            raise KeyError(f"unknown slot {name!r}; valid slots: {', '.join(SLOTS)}")
        return self.extensions.get(name, ())


def _build(data: dict) -> ProjectConfig:
    k = data["knobs"]
    knobs = Knobs(
        build_gate_cmd=k["build_gate_cmd"],
        lint_cmd=k.get("lint_cmd"),
        implementer_agents=dict(k.get("implementer_agents", {})),
        tier3_globs=tuple(k.get("tier3_globs", [])),
        ci_workflows=dict(k.get("ci_workflows", {})),
        docs_gate_paths=tuple(k.get("docs_gate_paths", [])),
        docs_only_allowlist=tuple(k.get("docs_only_allowlist", [])),
        sot_doc=k.get("sot_doc"),
        required_capabilities=tuple(k.get("required_capabilities", [])),
        optional_capabilities=tuple(k.get("optional_capabilities", [])),
    )
    extensions = {slot: tuple(files) for slot, files in data.get("extensions", {}).items()}
    return ProjectConfig(
        extends=data["extends"],
        core_version=data["core_version"],
        base_branch=data["base_branch"],
        knobs=knobs,
        owner=data.get("owner"),
        repo=data.get("repo"),
        platform=data.get("platform"),
        timezone=data.get("timezone"),
        merge_window=data.get("merge_window"),
        merge_window_mode=data.get("merge_window_mode", "freeze"),
        gates=tuple(data.get("gates", [])),
        extensions=extensions,
        extensions_dir=data.get("extensions_dir", DEFAULT_EXTENSIONS_DIR),
        policy_pack=json.loads(json.dumps(data.get("policy_pack", {}), sort_keys=True)),
    )


def parse_config(data: Any, *, source: str = "<dict>", schema: dict | None = None) -> ProjectConfig:
    """Validate raw data and build a :class:`ProjectConfig` (raises on error)."""
    if not isinstance(data, dict):
        raise ConfigError(source, [f"$: expected an object (got {type(data).__name__})"])
    errors = validate_data(data, schema)
    if isinstance(data, dict) and isinstance(data.get("knobs"), dict):
        knobs = data["knobs"]
        errors.extend(validate_names(
            tuple(knobs.get("required_capabilities", [])),
            source=f"{source}: knobs.required_capabilities",
        ))
        errors.extend(validate_names(
            tuple(knobs.get("optional_capabilities", [])),
            source=f"{source}: knobs.optional_capabilities",
        ))
    if isinstance(data, dict) and isinstance(data.get("policy_pack"), dict):
        for path, names in _policy_capability_fields(data["policy_pack"]):
            errors.extend(validate_names(tuple(names), source=f"{source}: {path}"))
    if errors:
        raise ConfigError(source, errors)
    return _build(data)


def load_config(path: str | Path) -> ProjectConfig:
    """Read + validate a ``project.yaml`` from disk."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_config(data, source=str(path))


def config_hash(config: ProjectConfig) -> str:
    """Stable SHA-256 over the canonicalised config (cache key / determinism)."""
    payload = json.dumps(_canonical(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _policy_capability_fields(value: Any, path: str = "policy_pack") -> list[tuple[str, list]]:
    fields: list[tuple[str, list]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if (
                key in {"required_capabilities", "optional_capabilities"}
                and isinstance(child, list)
            ):
                fields.append((child_path, child))
            else:
                fields.extend(_policy_capability_fields(child, child_path))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            fields.extend(_policy_capability_fields(child, f"{path}[{i}]"))
    return fields


def _canonical(config: ProjectConfig) -> dict:
    return {
        "extends": config.extends,
        "core_version": config.core_version,
        "base_branch": config.base_branch,
        "owner": config.owner,
        "repo": config.repo,
        "platform": config.platform,
        "timezone": config.timezone,
        "merge_window": config.merge_window,
        "merge_window_mode": config.merge_window_mode,
        "gates": list(config.gates),
        "extensions_dir": config.extensions_dir,
        "extensions": {k: list(v) for k, v in sorted(config.extensions.items())},
        "policy_pack": config.policy_pack,
        "knobs": {
            "build_gate_cmd": config.knobs.build_gate_cmd,
            "lint_cmd": config.knobs.lint_cmd,
            "implementer_agents": dict(sorted(config.knobs.implementer_agents.items())),
            "tier3_globs": list(config.knobs.tier3_globs),
            "ci_workflows": dict(sorted(config.knobs.ci_workflows.items())),
            "docs_gate_paths": list(config.knobs.docs_gate_paths),
            "docs_only_allowlist": list(config.knobs.docs_only_allowlist),
            "sot_doc": config.knobs.sot_doc,
            "required_capabilities": list(config.knobs.required_capabilities),
            "optional_capabilities": list(config.knobs.optional_capabilities),
        },
    }
