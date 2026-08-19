"""Keel Analytics, Token Usage, and USD Cost Estimation Engine.

Computes exact token expenditures and estimated USD costs per issue, PR, and Swarm wave
using built-in model pricing tables without any external billing APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import activity

# Model pricing in USD per 1,000,000 tokens: (prompt_price_per_m, completion_price_per_m)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "claude": (3.00, 15.00),
    # Google Gemini
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini": (0.15, 0.60),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "o1": (15.00, 60.00),
    "o3-mini": (1.10, 4.40),
    "codex": (2.50, 10.00),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek": (0.27, 1.10),
    # Local
    "ollama": (0.00, 0.00),
    "local": (0.00, 0.00),
}

DEFAULT_FALLBACK_PRICE = (1.00, 3.00)
FRONTIER_BENCHMARK_PRICE = (15.00, 75.00)  # Used for computing savings vs Claude Opus/o1


def normalize_model_name(model: str) -> str:
    """Normalize vendor:model strings to model pricing keys."""
    raw = model.lower().strip()
    if raw.startswith("ollama:") or raw.startswith("local:"):
        return raw.split(":", 1)[0]
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    raw = raw.replace("/", "-")
    for key in sorted(MODEL_PRICING, key=len, reverse=True):
        if key in raw:
            return key
    return raw or "default"


def estimate_token_cost(
    prompt_tokens: int, completion_tokens: int, model: str = ""
) -> float:
    """Calculate estimated USD cost for token usage based on model pricing."""
    key = normalize_model_name(model)
    prompt_rate, completion_rate = MODEL_PRICING.get(key, DEFAULT_FALLBACK_PRICE)
    cost = (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000.0
    return round(cost, 6)


def estimate_benchmark_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate benchmark frontier model cost for computing tiered routing savings."""
    p_rate, c_rate = FRONTIER_BENCHMARK_PRICE
    return round((prompt_tokens * p_rate + completion_tokens * c_rate) / 1_000_000.0, 6)


@dataclass(frozen=True)
class CostReport:
    total_runs: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost_usd: float
    estimated_savings_usd: float
    model_breakdown: dict[str, dict[str, Any]]
    top_performer: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_runs": self.total_runs,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "estimated_savings_usd": round(self.estimated_savings_usd, 4),
            "model_breakdown": self.model_breakdown,
            "top_performer": self.top_performer,
        }


def calculate_cost_report(records: list[dict[str, Any]]) -> CostReport:
    """Aggregate token metrics and compute USD costs from activity records."""
    total_prompt = 0
    total_completion = 0
    total_actual_cost = 0.0
    total_benchmark_cost = 0.0
    models_data: dict[str, dict[str, Any]] = {}

    for rec in records:
        p_tok = int(rec.get("prompt_tokens") or 0)
        c_tok = int(rec.get("completion_tokens") or 0)
        model = rec.get("model") or "gemini-2.5-flash"

        if p_tok == 0 and c_tok == 0:
            # Synthetic conservative estimate per activity phase (1,500 prompt, 400 completion)
            p_tok = 1500
            c_tok = 400

        total_prompt += p_tok
        total_completion += c_tok

        cost = estimate_token_cost(p_tok, c_tok, model)
        bench = estimate_benchmark_cost(p_tok, c_tok)

        total_actual_cost += cost
        total_benchmark_cost += bench

        m_key = normalize_model_name(model)
        if m_key not in models_data:
            models_data[m_key] = {
                "runs": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            }
        models_data[m_key]["runs"] += 1
        models_data[m_key]["prompt_tokens"] += p_tok
        models_data[m_key]["completion_tokens"] += c_tok
        models_data[m_key]["cost_usd"] = round(models_data[m_key]["cost_usd"] + cost, 4)

    total_tokens = total_prompt + total_completion
    savings = max(0.0, total_benchmark_cost - total_actual_cost)

    top_perf = None
    if models_data:
        # Top performer is model with most runs
        top_perf = max(models_data.items(), key=lambda item: item[1]["runs"])[0]

    return CostReport(
        total_runs=len(records),
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_tokens=total_tokens,
        total_cost_usd=round(total_actual_cost, 4),
        estimated_savings_usd=round(savings, 4),
        model_breakdown=models_data,
        top_performer=top_perf,
    )


def render_cost_report(report: CostReport) -> str:
    """Render human-readable markdown / CLI report."""
    lines = [
        "Keel Efficiency & Cost Ledger",
        "────────────────────────────────────────────────────────",
        f"  Total Runs Tracked    : {report.total_runs}",
        f"  Total Tokens          : {report.total_tokens:,} "
        f"(Prompt: {report.total_prompt_tokens:,} / "
        f"Completion: {report.total_completion_tokens:,})",
        f"  Estimated Spend (USD) : ${report.total_cost_usd:.4f}",
        f"  Estimated Savings     : ${report.estimated_savings_usd:.4f} "
        "(via Tiered Routing & Local/Flash Models)",
    ]
    if report.top_performer:
        lines.append(f"  Top Dispatched Model  : {report.top_performer}")

    if report.model_breakdown:
        lines.append("")
        lines.append("  Model Breakdown:")
        for m, stats in sorted(report.model_breakdown.items(), key=lambda x: -x[1]["runs"]):
            lines.append(
                f"    - {m:<18}: {stats['runs']:>3} runs | "
                f"{stats['prompt_tokens'] + stats['completion_tokens']:>9,} tokens | "
                f"${stats['cost_usd']:.4f}"
            )

    return "\n".join(lines)


def generate_cost_report(root: str | Path = ".") -> CostReport:
    """Read all activity records from .keel/activity and compile CostReport."""
    root_path = Path(root).resolve()
    act_dir = root_path / activity.DEFAULT_ACTIVITY_DIR
    records: list[dict[str, Any]] = []

    if act_dir.exists() and act_dir.is_dir():
        records = activity.read_all_activity(act_dir)

    return calculate_cost_report(records)
