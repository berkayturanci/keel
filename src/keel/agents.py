"""Agent dispatch + attribution — the pure resolution logic.

The backbone dispatches agentic steps (implement / review / extensions) to a
configured agent: the **host agent** by default, a per-run **delegate** override,
or a per-role agent from ``knobs.implementer_agents``. Attribution records the
*effective* implementer as labels (``agent:<vendor>`` + a versionless
``model:<base>``), reusing the ship #2036 stripping algorithm.

All functions here are pure and deterministic — no subprocess, no network.
"""

from __future__ import annotations

from .config import ProjectConfig

#: Default host agent when nothing else is resolved.
HOST_DEFAULT = "claude"

#: Hosted-API delegate vendors (issue #548): the vendor's real API keyed by an
#: env token, no agent CLI installed. Same no-tools contract as ``ollama:`` — the
#: orchestrator owns every git/PR step and delegates only code generation. The
#: vendor names match ai-jury's hosted-adapter vocabulary so the value fits the
#: existing first-colon ``vendor:model`` split unchanged.
API_VENDORS = ("anthropic-api", "openai-api")


def split_delegate(value: str) -> tuple[str, str | None]:
    """Split ``ollama:qwen2.5`` -> ``("ollama", "qwen2.5")``; ``codex`` -> ``("codex", None)``."""
    vendor, sep, model = value.partition(":")
    return vendor, (model if (sep and model) else None)


def is_api_delegate(vendor: str) -> bool:
    """True when ``vendor`` is a hosted-API delegate (``anthropic-api``/``openai-api``)."""
    return vendor in API_VENDORS


def resolve_agent(
    config: ProjectConfig,
    *,
    role: str | None = None,
    delegate: str | None = None,
    host_agent: str = HOST_DEFAULT,
) -> str:
    """Resolve which agent runs a step.

    Precedence: explicit ``delegate`` > per-role ``implementer_agents`` mapping >
    ``host_agent`` default.
    """
    if delegate:
        return delegate
    if role and role in config.knobs.implementer_agents:
        return config.knobs.implementer_agents[role]
    return host_agent


def model_base(model: str) -> str:
    """Strip a model id to a coarse, versionless base label (ship #2036 algorithm).

    Examples: ``qwen2.5:7b`` -> ``qwen``, ``gemma2`` -> ``gemma``,
    ``llama3.1`` -> ``llama``, ``gpt-5.5`` -> ``gpt-5``, ``gpt-4o`` -> ``gpt-4o``.
    """
    m = model.strip().lower()
    if not m:
        return ""
    m = m.split(":", 1)[0]  # (1) drop any ollama :tag
    if "-" in m:
        # (3) hyphenated family: keep <word>-<major>, drop the .minor
        head, _, tail = m.partition("-")
        major = tail.split(".", 1)[0]
        return f"{head}-{major}"
    # (2) non-hyphenated family: drop the trailing numeric run (digits + dots)
    i = len(m)
    while i > 0 and (m[i - 1].isdigit() or m[i - 1] == "."):
        i -= 1
    return m[:i]


def agent_label(vendor: str) -> str:
    """The persistent ``agent:<vendor>`` label."""
    return f"agent:{vendor}"


def model_label(model: str) -> str | None:
    """The versionless ``model:<base>`` label, or ``None`` when no base is known."""
    base = model_base(model)
    return f"model:{base}" if base else None


def attribution(vendor: str, model: str | None = None) -> dict[str, str | None]:
    """Resolve the effective attribution for an implementer/reviewer.

    Returns ``{"agent_label", "model_label", "system"}`` where ``system`` is the
    full ``vendor`` or ``vendor:model`` string for the closure comment.
    """
    system = f"{vendor}:{model}" if model else vendor
    return {
        "agent_label": agent_label(vendor),
        "model_label": model_label(model) if model else None,
        "system": system,
    }
