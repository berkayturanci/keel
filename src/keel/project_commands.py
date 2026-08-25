"""Project-provided command declarations.

Project commands are durable policy data: keel can discover, list, plan, and capability-check
them, but their implementation remains in the consumer repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ProjectConfig


@dataclass(frozen=True)
class ProjectCommand:
    """One project-owned command exposed through ``policy_pack``."""

    name: str
    command: str | None = None
    description: str | None = None
    agent_role: str | None = None
    paths: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    dry_run_safe: bool = False
    source: str = "policy_pack.project_commands"

    def as_dict(self) -> dict[str, Any]:
        """Render as JSON-compatible contract data."""
        return {
            "name": self.name,
            "command": self.command,
            "description": self.description,
            "agent_role": self.agent_role,
            "paths": list(self.paths),
            "required_capabilities": list(self.required_capabilities),
            "optional_capabilities": list(self.optional_capabilities),
            "side_effects": list(self.side_effects),
            "dry_run_safe": self.dry_run_safe,
            "source": self.source,
        }


def list_project_commands(config: ProjectConfig) -> tuple[ProjectCommand, ...]:
    """Return project-owned commands declared by the policy pack."""
    pack = config.policy_pack
    commands: list[ProjectCommand] = []
    commands.extend(
        _commands_from_map(pack.get("project_commands", {}), source="policy_pack.project_commands")
    )
    legacy_names = {cmd.name for cmd in commands}
    for command in _commands_from_map(
        pack.get("command_routing", {}), source="policy_pack.command_routing"
    ):
        if command.name not in legacy_names:
            commands.append(command)
    return tuple(sorted(commands, key=lambda cmd: cmd.name))


def get_project_command(config: ProjectConfig, name: str) -> ProjectCommand | None:
    """Return one project command by name, or ``None`` when absent."""
    for command in list_project_commands(config):
        if command.name == name:
            return command
    return None


def _commands_from_map(value: Any, *, source: str) -> list[ProjectCommand]:
    if not isinstance(value, dict):
        return []
    commands: list[ProjectCommand] = []
    for name, raw in value.items():
        if not isinstance(raw, dict):
            continue
        commands.append(
            ProjectCommand(
                name=name,
                command=raw.get("command"),
                description=raw.get("description"),
                agent_role=raw.get("agent_role"),
                paths=tuple(raw.get("paths", [])),
                required_capabilities=tuple(raw.get("required_capabilities", [])),
                optional_capabilities=tuple(raw.get("optional_capabilities", [])),
                side_effects=tuple(raw.get("side_effects", [])),
                dry_run_safe=bool(raw.get("dry_run_safe", False)),
                source=source,
            )
        )
    return commands
