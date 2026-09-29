"""Keel token counts and USD cost estimates.

Totals the token counts the activity records under ``.keel/activity`` carry — or a
placeholder where they carry none — and prices them from built-in model pricing tables,
without any external billing API.

**The report says which of its figures are measured.** A record that carries a
non-zero ``prompt_tokens`` / ``completion_tokens``, or a
:data:`keel.activity.USAGE_FIELD` entry with non-zero counts, is *measured*; one that
carries neither is priced at :data:`ASSUMED_PROMPT_TOKENS` /
:data:`ASSUMED_COMPLETION_TOKENS` and counted as *estimated* (#1359) — a report whose
dollar figure is a run count times a constant must not read as a bill.
:class:`CostReport` carries the split, and both renderings state it.

The one keel writer of counts is ``keel delegate run --activity-run-id`` (#1373): a
hosted-API delegate's reported usage, one entry per call, priced at that call's model.
A measured run is therefore measured *for its delegate calls*: an agent host's own
tokens, and a CLI delegate's, are never in a record, and the report says so. The dollar
figures stay estimates either way — they are keel's pricing table applied to counts,
not the provider's bill.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import activity, agents
from .agents import LOCAL_TRANSPORTS

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

#: The token counts a record without its own is priced at: a placeholder per run, not a
#: measurement. Named, because the report has to name it — #1359 found the numbers
#: inlined in the aggregation loop and nowhere in the output, so a report built entirely
#: from them printed a dollar figure with nothing to say it was one.
ASSUMED_PROMPT_TOKENS = 1500
ASSUMED_COMPLETION_TOKENS = 400
FRONTIER_BENCHMARK_PRICE = (15.00, 75.00)  # Used for computing savings vs Claude Opus/o1

# #930's `_PRICING_KEYS_BY_LENGTH` is gone with the substring scan it ordered.
# Longest-first existed so `gpt-4o-mini` would not be captured by `gpt-4o`;
# exact resolution makes that impossible by construction, and a constant nothing
# reads is a comment pretending to be a guard. The property it protected is
# asserted on the outcome in `tests/test_cost.py`.


#: Cloud re-hosters put the vendor in front of an otherwise ordinary model id.
#: Bedrock uses ``anthropic.claude-…-v1:0``, Vertex
#: ``publishers/anthropic/models/…``, OpenRouter ``anthropic/claude-…``. The old
#: code split on the first ``:`` and kept the **right** side, so a Bedrock id
#: normalised to ``0`` — not an approximation, a total loss of the model name
#: (#941).
_REHOST_VENDORS = frozenset(
    {
        "anthropic",
        "openai",
        "google",
        "meta",
        "mistral",
        "cohere",
        "amazon",
        "ai21",
        "deepseek",
        "qwen",
        "x-ai",
        "perplexity",
    }
)

#: A release stamp or revision that carries no pricing signal: ``-20240229``,
#: ``-v1``, ``-latest``, ``-preview``.
_MODEL_STAMP = re.compile(r"-(?:\d{6,8}|v\d+|latest|preview|exp)(?=-|$)")

#: The bridge #942 asked for: the vocabulary :func:`keel.agents.model_base`
#: emits, mapped onto the pricing keys that already exist.
#:
#: keel writes a versionless ``model:<base>`` label on every PR — ``opus-4-8``,
#: ``sonnet-4-5`` — and **none** of those bases appears in ``MODEL_PRICING``,
#: whose keys are 2024-era product names. So `keel cost-report` priced keel's own
#: runs at the ``DEFAULT_FALLBACK_PRICE``: roughly 5 % of true Opus spend, with
#: the difference then claimed as savings (#944).
#:
#: **These are aliases onto prices already in the table, not new prices.** An
#: alias says "this label names that tier", which is a fact about naming and is
#: checkable. Whether the tier's *numbers* are still current is a separate,
#: operator-owned question — #942's "refresh the keys" — and inventing figures
#: here would bury that question under a plausible-looking table.
MODEL_ALIASES: dict[str, str] = {
    # Anthropic — keel's own attribution bases and the vendor's current ids.
    "opus": "claude-3-opus",
    "opus-4": "claude-3-opus",
    "opus-4-5": "claude-3-opus",
    "opus-4-8": "claude-3-opus",
    "claude-opus": "claude-3-opus",
    "claude-opus-4": "claude-3-opus",
    "sonnet": "claude-3-7-sonnet",
    "sonnet-4": "claude-3-7-sonnet",
    "sonnet-4-5": "claude-3-7-sonnet",
    "claude-sonnet": "claude-3-7-sonnet",
    "claude-sonnet-4": "claude-3-7-sonnet",
    "haiku": "claude-3-5-haiku",
    "haiku-4-5": "claude-3-5-haiku",
    "claude-haiku": "claude-3-5-haiku",
    # OpenAI. Only the CLI's own label, which names the same product as `codex`.
    "codex-cli": "codex",
}

# Deliberately absent: `gpt-5 -> gpt-4o`, `gemini-2 -> gemini-2.5-pro` and
# friends. Those are not naming facts, they are price guesses across tiers — and
# the first draft of this map proved the point by resolving
# `gemini-2.5-flash-lite` to the *pro* price, 8x its own. An unknown model is
# reported as unpriced, which `calculate_cost_report` counts; a wrong tier is
# reported as a number, which nobody counts.


def _bare_model_id(model: str) -> str:
    """Strip re-hosting decoration down to the vendor's own model id.

    Order matters: the path form is unwrapped before the colon is read, because
    a Bedrock id carries *both* (``anthropic.claude-3-opus-20240229-v1:0``).
    """
    raw = model.lower().strip()
    # Local inference is free, and `MODEL_PRICING` prices the *tier* at 0.00 —
    # so `ollama:`/`local:` collapse to the transport on purpose. This is the one
    # place where pricing and attribution legitimately want different answers:
    # #955's label must name the model, this must name the free tier.
    if raw.startswith(tuple(f"{p}:" for p in LOCAL_TRANSPORTS)):
        return raw.split(":", 1)[0]
    # `<vendor>-api:model` is a transport prefix, and which names are transports
    # is defined once, in `agents` (#955). Sharing the definition is what stops
    # the pricing key and the attribution label drifting apart again.
    raw = agents.strip_transport(raw)
    if "/" in raw:  # openrouter `vendor/model`, vertex `publishers/v/models/model`
        raw = raw.rsplit("/", 1)[1]
    if ":" in raw:
        head, tail = raw.split(":", 1)
        # `…-v1:0` is a Bedrock revision; `google:gemini-2.5-pro` is an
        # unrecognised re-hoster, whose model is still on the right.
        raw = head if tail.isdigit() else tail
    if "." in raw and raw.split(".", 1)[0] in _REHOST_VENDORS:
        raw = raw.split(".", 1)[1]
    return _MODEL_STAMP.sub("", raw)


def normalize_model_name(model: str) -> str:
    """Resolve a model id to a pricing key, or ``"default"`` when unknown.

    Three changes from the substring match this replaces, one per finding:

    * **Re-hosted ids are unwrapped first** (#941). See :func:`_bare_model_id`.
    * **The key is the attribution base**, produced by
      :func:`keel.agents.model_base` — the same function that writes the
      ``model:<base>`` label onto a PR. #942's finding was that the pricing table
      and the attribution convention were two vocabularies with nothing
      connecting them, so keel priced its own runs at the fallback. They are one
      vocabulary now, and :mod:`tests.test_cost_vocabulary` asserts it.
    * **Matching is on token boundaries** (#943). A key matches only as a whole
      run of ``-``-separated segments, so ``o1-mini`` no longer resolves to
      ``o1`` — a 13.6x overcharge on a widely used model. Longest-first (#930)
      still decides between two keys that both match.

    An unrecognised model returns ``"default"`` rather than the raw string, so a
    caller can tell "priced" from "guessed"; :func:`calculate_cost_report` counts
    the guesses instead of quietly folding them into the total.
    """
    raw = _bare_model_id(model)
    if not raw:
        return "default"
    for candidate in (raw, agents.model_base(raw), _family_root(raw)):
        if candidate in MODEL_PRICING:
            return candidate
        aliased = MODEL_ALIASES.get(candidate)
        if aliased in MODEL_PRICING:
            return aliased
    return "default"


def _family_root(raw: str) -> str:
    """``claude-opus-4-5`` -> ``claude-opus``: the id with its version run removed.

    A naming rule, not a price guess. Every member of a vendor's named family
    shares a tier, so enumerating `-4`, `-4-5`, `-4-8` in
    :data:`MODEL_ALIASES` would be a list that goes stale on the next release —
    which is the failure #942 is about, re-created one level down.

    Stops at the first purely numeric segment, so ``gpt-4o`` (whose ``4o`` is not
    numeric) and ``gemini-2.5-flash`` (whose price differs per member) are left
    whole and fall through to ``default`` rather than borrowing a sibling's
    price.
    """
    segments = raw.split("-")
    head = list(itertools.takewhile(lambda part: not part.isdigit(), segments))
    return "-".join(head) if head and len(head) < len(segments) else raw


def estimate_token_cost(prompt_tokens: int, completion_tokens: int, model: str = "") -> float:
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
    #: Runs whose model could not be priced. Their tokens and fallback cost are
    #: in the totals; they are excluded from the savings figure, and this is the
    #: number that says how much of the report is a guess (#944).
    unpriced_runs: int = 0
    #: Runs whose record carried its own token counts (#1359).
    measured_runs: int = 0
    #: Runs priced at :data:`ASSUMED_PROMPT_TOKENS` / :data:`ASSUMED_COMPLETION_TOKENS`
    #: because their record carried none. ``measured_runs + estimated_runs ==
    #: total_runs``.
    estimated_runs: int = 0

    @property
    def token_basis(self) -> str:
        """``none`` | ``measured`` | ``estimated`` | ``mixed`` — what the token totals rest on.

        Keyed on ``measured_runs``, so a report that does not say how many runs were
        measured reads as ``estimated``: the unflattering direction, as with #944's
        unpriced runs.
        """
        if not self.total_runs:
            return "none"
        if not self.measured_runs:
            return "estimated"
        if self.measured_runs >= self.total_runs:
            return "measured"
        return "mixed"

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
            "unpriced_runs": self.unpriced_runs,
            # Additive (#1359): every key above keeps its name and meaning.
            "token_basis": self.token_basis,
            "measured_runs": self.measured_runs,
            "estimated_runs": self.estimated_runs,
            "assumed_tokens_per_run": {
                "prompt_tokens": ASSUMED_PROMPT_TOKENS,
                "completion_tokens": ASSUMED_COMPLETION_TOKENS,
            },
        }


def calculate_cost_report(records: list[dict[str, Any]]) -> CostReport:
    """Aggregate token metrics and compute USD costs from activity records.

    A record whose model cannot be priced is counted in the totals — the tokens
    were spent either way — but excluded from ``estimated_savings_usd`` and
    reported in ``unpriced_runs``.

    Two reasons, both from #944. The savings figure is
    ``frontier_benchmark - actual``, so *understating* a cost *inflates* the
    saving: every pricing error propagated into the headline at double weight.
    And a missing ``model`` used to default to ``gemini-2.5-flash``, one of the
    cheapest entries — so **missing attribution read as maximum savings**, which
    is the flattering direction, on a repo whose own ledger has ``model: None``
    for every record.
    """
    total_prompt = 0
    total_completion = 0
    total_actual_cost = 0.0
    priced_actual_cost = 0.0
    total_benchmark_cost = 0.0
    unpriced_runs = 0
    estimated_runs = 0
    models_data: dict[str, dict[str, Any]] = {}

    for rec in records:
        parts = _measured_parts(rec)
        if not parts:
            # No counts on the record: price it at the placeholder, and count it, so the
            # report can say how much of itself is the placeholder (#1359).
            parts = [(rec.get("model") or "", ASSUMED_PROMPT_TOKENS, ASSUMED_COMPLETION_TOKENS)]
            estimated_runs += 1

        run_unpriced = False
        run_models: set[str] = set()
        for model, p_tok, c_tok in parts:
            total_prompt += p_tok
            total_completion += c_tok

            m_key = normalize_model_name(model)
            cost = estimate_token_cost(p_tok, c_tok, model)
            total_actual_cost += cost
            if m_key == "default":
                run_unpriced = True
            else:
                # Only a run whose real price is known can evidence a saving against
                # the frontier benchmark.
                priced_actual_cost += cost
                total_benchmark_cost += estimate_benchmark_cost(p_tok, c_tok)

            stats = models_data.setdefault(
                m_key, {"runs": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
            )
            # A model's `runs` counts the runs that used it, once each: a ship run whose
            # implementer and reviewer both called the same model is one run of it.
            if m_key not in run_models:
                stats["runs"] += 1
                run_models.add(m_key)
            stats["prompt_tokens"] += p_tok
            stats["completion_tokens"] += c_tok
            stats["cost_usd"] = round(stats["cost_usd"] + cost, 4)
        unpriced_runs += run_unpriced

    total_tokens = total_prompt + total_completion
    # Like with like: both sides of the subtraction cover exactly the priced
    # runs. Benchmarking a subset against a total would understate the saving
    # rather than inflate it, which is safer but still wrong.
    savings = max(0.0, total_benchmark_cost - priced_actual_cost)

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
        unpriced_runs=unpriced_runs,
        measured_runs=len(records) - estimated_runs,
        estimated_runs=estimated_runs,
    )


def _measured_parts(rec: dict[str, Any]) -> list[tuple[str, int, int]]:
    """Every ``(model, prompt_tokens, completion_tokens)`` a record carries; ``[]`` if none.

    Two sources, both measured: the record's own top-level counts (any writer's, priced at
    the record's ``model``), and each :data:`keel.activity.USAGE_FIELD` entry keel's
    delegates recorded (#1373), priced at that call's model. A part whose counts are both
    zero is not a measurement, as before; neither is a malformed entry — a record read
    from disk has been validated, but this also takes records built in memory.
    """
    parts: list[tuple[str, int, int]] = []
    p_tok = int(rec.get("prompt_tokens") or 0)
    c_tok = int(rec.get("completion_tokens") or 0)
    if p_tok or c_tok:
        parts.append((rec.get("model") or "", p_tok, c_tok))
    usage = rec.get(activity.USAGE_FIELD)
    for entry in usage.values() if isinstance(usage, dict) else ():
        if activity.usage_entry_issue(entry) is None and (
            entry["prompt_tokens"] or entry["completion_tokens"]
        ):
            parts.append((entry["model"], entry["prompt_tokens"], entry["completion_tokens"]))
    return parts


def _token_basis_lines(report: CostReport) -> list[str]:
    """The report's statement of what its token figures rest on (#1359).

    Placed directly under the run count, above the first figure it qualifies, because a
    caveat printed after the dollar amount is read after the dollar amount.
    """
    assumption = (
        f"{ASSUMED_PROMPT_TOKENS:,} prompt / {ASSUMED_COMPLETION_TOKENS:,} completion "
        "tokens per run"
    )
    indent = " " * 26
    basis = report.token_basis
    if basis == "none":
        return []
    # What a measured count covers, said wherever one is counted: keel records what a
    # hosted-API delegate reports (#1373), and nothing of the agent host running the run.
    scope = [
        f"{indent}Measured counts are the ones the records carry: keel records what a",
        f"{indent}hosted-API delegate reports, never an agent host's own tokens.",
    ]
    if basis == "measured":
        whole = (
            "the 1 run carries token counts"
            if report.total_runs == 1
            else f"all {report.total_runs} runs carry token counts"
        )
        return [f"  Token Basis           : measured ({whole})", *scope]
    if basis == "estimated":
        return [
            f"  Token Basis           : ESTIMATED at {assumption}",
            f"{indent}No record carries measured token counts, so every figure below rests",
            f"{indent}on that placeholder. Read it as a run count, not a bill.",
        ]
    return [
        f"  Token Basis           : {report.measured_runs} measured, "
        f"{report.estimated_runs} ESTIMATED at {assumption}",
        f"{indent}The estimated runs carry no token counts; their share of every figure",
        f"{indent}below is that assumption, not a measurement.",
        *scope,
    ]


def render_cost_report(report: CostReport) -> str:
    """Render human-readable markdown / CLI report."""
    lines = [
        "Keel Token & Cost Report",
        "────────────────────────────────────────────────────────",
        f"  Total Runs Tracked    : {report.total_runs}",
        *_token_basis_lines(report),
        f"  Total Tokens          : {report.total_tokens:,} "
        f"(Prompt: {report.total_prompt_tokens:,} / "
        f"Completion: {report.total_completion_tokens:,})",
        f"  Estimated Spend (USD) : ${report.total_cost_usd:.4f}",
        f"  Estimated Savings     : ${report.estimated_savings_usd:.4f} "
        "(priced runs only, vs the frontier benchmark)",
    ]
    if report.unpriced_runs:
        # Printed whenever it is non-zero, because the spend figure above is
        # partly a fallback guess and the reader cannot tell from the number.
        lines.append(
            f"  Unpriced Runs         : {report.unpriced_runs} "
            "(model not in the pricing table; excluded from savings)"
        )
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
