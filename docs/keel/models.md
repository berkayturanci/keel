# Supported AI Models & Providers Guide

> **Keel is multi-model and vendor-neutral by design.**
> You can drive any step of the Keel workflow using your choice of AI model:
> hosted API endpoints, official coding CLIs, OpenAI-compatible providers,
> or locally hosted models (Ollama/vLLM).

---

## Table of Contents

1. [Architecture & Roles](#architecture--roles)
2. [How to Select Models](#how-to-select-models)
3. [One Executor: `keel delegate run`](#one-executor-keel-delegate-run)
4. [Hosted API Delegates (Zero-CLI)](#1-hosted-api-delegates-zero-cli)
   - [Anthropic (Claude)](#anthropic-claude)
   - [OpenAI (GPT / o-series)](#openai-gpt--o-series)
   - [Google (Gemini)](#google-gemini)
5. [OpenAI-Compatible Profiles (OpenRouter, DeepSeek, Groq, local LLMs)](#2-openai-compatible-profiles)
   - [OpenRouter (Universal Model Gateway)](#openrouter)
   - [DeepSeek Official API](#deepseek-official-api)
   - [Groq](#groq)
   - [Together AI](#together-ai)
   - [Local vLLM / LM Studio / LiteLLM Proxy](#local-vllm--lm-studio--litellm)
6. [Local Offline Models (Ollama)](#3-local-offline-models-ollama)
7. [Agent CLIs (Subprocess)](#4-agent-clis-subprocess)
   - [Claude Code (`claude`)](#claude-code)
   - [Codex (`codex`)](#codex)
   - [Antigravity (`agy`)](#antigravity)
8. [Generic CLI Profiles (Aider, Cursor Agent, Custom Scripts)](#5-generic-cli-profiles)
9. [Multi-Model & Ensemble Review Posture](#6-multi-model--ensemble-review-posture)
10. [Summary Comparison Table](#summary-comparison-table)
11. [Which of these work on your machine](#which-of-these-work-on-your-machine)

---

## Architecture & Roles

In Keel, the **orchestrator** owns the deterministic workflow: branch creation, git staging,
commit signing, PR creation, merge window enforcement, and merge locking.
AI models are delegated specific bounded tasks:

* **Implementer (`s4 implement`)**: Receives the issue context, codebase instructions, and
  produces a unified diff or code modification.
* **Reviewer (`s7 review`)**: Inspects PR diffs, executes test validations, and returns
  structured findings (`critical`, `major`, `minor`, `nit`) with file:line annotations.

---

## How to Select Models

You can choose models at three different levels:

### 1. Per-Command Override (CLI Flags)
Pass `--delegate` (for implementer) or `--review-delegate` (for reviewer):
```bash
# Implement with Gemini 2.5 Pro and review with Claude 3.7 Sonnet
/keel:ship 123 --delegate google-api:gemini-2.5-pro --review-delegate anthropic-api:claude-3-7-sonnet-20250219

# Implement with DeepSeek-R1 via an OpenRouter profile
/keel:ship 123 --delegate openrouter:deepseek/deepseek-r1
```

### 2. Issue Labels
Label an issue on GitHub to route implementation automatically:
* `delegate:google-api` + `delegate-model:gemini-2.5-pro`
* `delegate:anthropic-api` + `delegate-model:claude-3-7-sonnet-20250219`
* `delegate:openrouter` + `delegate-model:meta-llama/llama-3.3-70b-instruct`

### 3. Project Team Policy (`project.yaml`)
`knobs.team` is where a project states its whole team — implementer per issue role, one
mandatory gate reviewer from a different vendor, reviewer seats per risk tier, and the
jury:

```yaml
knobs:
  team:
    implement:
      default: { provider: claude }
      by_role:
        core: { provider: agy, model: gemini-3.8-flash-high, effort: high }
        frontend: { provider: anthropic-api, model: claude-3-7-sonnet-20250219 }
        docs: { provider: "subagent:docs-writer" }   # a host subagent, not a vendor
    gate: { provider: codex, distinct_from: implementer }
    review:
      by_tier:
        "2": [{ provider: claude }, { provider: codex }]
        "3": jury
    jury: { mode: gating, min_vendors: 2 }
```

A `provider` is resolved by the same registry `keel delegate run` uses, and
`subagent:<name>` is the explicit spelling for a host (Claude-class) subagent. Full
reference: [`configuration.md#team`](configuration.md#team).

The older `knobs.implementer_agents` still works and is mapped onto
`team.implement.by_role`, but it is **deprecated**: its values were documented as vendor
strings here and as Claude subagent names in `ship.md` s4, and nothing said which.

Both documented spellings keep working, and keel now says which is which: a value whose
head names a provider it can resolve **is** that provider (with the model after the colon);
anything else is the host subagent, and keel prefixes it for you.

```yaml
knobs:
  implementer_agents:                                 # deprecated — prefer team.implement.by_role
    core: backend-developer                           # -> subagent:backend-developer
    frontend: anthropic-api:claude-3-7-sonnet-20250219  # -> anthropic-api, model claude-3-7-…
    docs: google-api:gemini-2.5-flash                 # -> google-api, model gemini-2.5-flash
```

---

## One Executor: `keel delegate run`

Whatever you select above, **one command performs the dispatch**. `keel delegate run`
resolves the provider, picks the vendor's flags for the role, delivers the prompt off the
process list, translates `--effort` into the vendor's own spelling, and prints one JSON
document. The `/keel:*` adapters call it; nothing hand-builds a vendor invocation any
more, which is what stopped the argv shapes drifting between adapters.

```bash
# tool-enabled implementer, any transport
keel delegate run --provider agy:gemini-3.8-flash --role implement \
  --prompt-file brief.md --cwd ../wt-1012 --timeout 3600

# read-only reviewer — the role picks the vendor's read-only invocation
keel delegate run --provider anthropic-api:claude-opus-4-5 --role review \
  --prompt-file rubric.md --effort high

# a long run that outlives the caller
keel delegate run --provider codex --role implement --prompt-file brief.md \
  --timeout 3600 --detach --run-id impl-1012 --root .
keel delegate wait impl-1012 --root . --timeout 3600
```

* `--provider` takes the same token as `--delegate`: a name or `name:model`, resolved
  **built-in vendor > project profile > machine registry**. A built-in always wins and can
  never be redefined by config or by a file in `$HOME`.
* Model ids are validated against where they land: the strict `[A-Za-z0-9._-]` rule where
  the model reaches a command line or `google-api`'s URL path, and `[A-Za-z0-9._:/-]` where
  it is only a JSON body field — which is why `ollama:qwen2.5-coder:32b` and
  `openrouter:deepseek/deepseek-r1` work.
* `--role review|gate|chair` runs read-only; `--role implement|fix` runs tool-enabled. For
  the three built-in CLIs the read-only invocation carries no write-enabling flag, asserted
  per vendor in keel's tests. keel cannot enforce read-only for an arbitrary binary, so the
  result reports `read_only` **and** `read_only_backed` — branch on the second: a profile
  with `args` and no `review_args` reviews with the *implementer's* flags.
* `--effort low|medium|high` becomes an `agy` model suffix, a `codex`
  `model_reasoning_effort` override, Anthropic `thinking`, OpenAI `reasoning_effort`, or
  Gemini `thinkingConfig` — and `effort_applied: false` plus a warning where the vendor
  cannot express it. A provider entry's own `effort:` is the default when `--effort` is
  absent.
* Failures are fail-soft: `ok: false` with an `error_code`
  (`missing-binary`, `nonzero-exit`, `timeout`, `rate-limit`, `no-key`, `lost`, …), never a
  traceback. The retry, fall-back and tier rules stay with the caller.
* Pass `--timeout` to both `run` and `wait` on a detached run: the first becomes the run's
  own deadline, which is what lets a killed child be reported `lost` instead of sitting at
  `running` forever.

Full flag and contract reference: [`cli.md`](cli.md#keel-delegate).

---

## 1. Hosted API Delegates (Zero-CLI)

Hosted API delegates require **no agent CLI installed**. Keel connects directly to the provider's
endpoint via Python standard library HTTP (`urllib`), needing only an API key in your environment.

### Anthropic (Claude)
* **Vendor Prefix**: `anthropic-api:<model>`
* **Required Env Var**: `ANTHROPIC_API_KEY`
* **Common Models**:
  * `claude-3-7-sonnet-20250219`
  * `claude-3-5-sonnet-20241022`
  * `claude-3-5-haiku-20241022`

```bash
export ANTHROPIC_API_KEY="sk-ant-..."

# Run implementation via Claude 3.7 Sonnet API
/keel:ship 42 --delegate anthropic-api:claude-3-7-sonnet-20250219
```

### OpenAI (GPT / o-series)
* **Vendor Prefix**: `openai-api:<model>`
* **Required Env Var**: `OPENAI_API_KEY`
* **Common Models**:
  * `gpt-4o`
  * `gpt-4o-mini`
  * `o3-mini`
  * `o1`

```bash
export OPENAI_API_KEY="sk-proj-..."

# Run implementation via GPT-4o API
/keel:ship 42 --delegate openai-api:gpt-4o
```

### Google (Gemini)
* **Vendor Prefix**: `google-api:<model>`
* **Required Env Var**: `GEMINI_API_KEY`
* **Common Models**:
  * `gemini-2.5-pro`
  * `gemini-2.5-flash`
  * `gemini-2.0-flash-exp`

```bash
export GEMINI_API_KEY="AIzaSy..."

# Run implementation via Gemini 2.5 Pro API
/keel:ship 42 --delegate google-api:gemini-2.5-pro
```

> **Security Note on Google API**: Keel sends the key via the `x-goog-api-key` header (never in query parameters)
> and validates model names against path traversal characters.

---

## 2. OpenAI-Compatible Profiles

Any provider or local server exposing an OpenAI-compatible `/v1/chat/completions` endpoint can be configured under `knobs.delegate_profiles` in `.keel/project.yaml`.

> **SSRF Protection**:
> - Loopback addresses (`localhost`, `127.0.0.1`, `[::1]`) are allowed by default.
> - Remote hosts require setting `export KEEL_ALLOW_REMOTE_ENDPOINT=1` in your environment.
> - Private ranges (`10.x`, `172.16–31.x`, `192.168.x`) need `export KEEL_ALLOW_INTERNAL_ENDPOINT=1`
>   as well — permitting keel to reach *out* is not the same decision as permitting it to reach *in*.
> - Cloud-metadata and link-local addresses are refused by **both** opt-ins, and every
>   spelling of them (`2852039166`, `0xA9FEA9FE`) normalises to the same address first.
> - `api_key_env` stores only the **name** of the environment variable, preventing secret leakage into `config_hash` — and only a name from the delegate-key allowlist (`*_API_KEY` for the supported providers, or your own `KEEL_DELEGATE_KEY_*`).

### OpenRouter
Access hundreds of models (DeepSeek, Llama 3.3, Qwen, Mistral, Command R+) through a single key:

```yaml
# .keel/project.yaml
knobs:
  delegate_profiles:
    openrouter:
      vendor: openai-compatible
      endpoint: https://openrouter.ai/api/v1/chat/completions
      api_key_env: OPENROUTER_API_KEY
      model: deepseek/deepseek-r1
```

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
export KEEL_ALLOW_REMOTE_ENDPOINT=1

# Use default profile model (deepseek-r1)
/keel:ship 42 --delegate openrouter

# Or switch model on the fly
/keel:ship 42 --delegate openrouter:qwen/qwen-2.5-coder-32b-instruct
/keel:ship 42 --delegate openrouter:meta-llama/llama-3.3-70b-instruct
```

### DeepSeek Official API
```yaml
knobs:
  delegate_profiles:
    deepseek:
      vendor: openai-compatible
      endpoint: https://api.deepseek.com/chat/completions
      api_key_env: DEEPSEEK_API_KEY
      model: deepseek-chat
```
```bash
export DEEPSEEK_API_KEY="sk-..."
export KEEL_ALLOW_REMOTE_ENDPOINT=1

/keel:ship 42 --delegate deepseek:deepseek-reasoner
```

### Groq
Ultra-low-latency inference for open models:
```yaml
knobs:
  delegate_profiles:
    groq:
      vendor: openai-compatible
      endpoint: https://api.groq.com/openai/v1/chat/completions
      api_key_env: GROQ_API_KEY
      model: llama-3.3-70b-versatile
```
```bash
export GROQ_API_KEY="gsk_..."
export KEEL_ALLOW_REMOTE_ENDPOINT=1

/keel:ship 42 --delegate groq
```

### Together AI
```yaml
knobs:
  delegate_profiles:
    together:
      vendor: openai-compatible
      endpoint: https://api.together.xyz/v1/chat/completions
      api_key_env: TOGETHER_API_KEY
      model: meta-llama/Llama-3.3-70B-Instruct-Turbo
```

### Local vLLM / LM Studio / LiteLLM
Run local or self-hosted models completely on-premise without remote network calls:

```yaml
knobs:
  delegate_profiles:
    local_vllm:
      vendor: openai-compatible
      endpoint: http://127.0.0.1:8000/v1/chat/completions
      api_key_env: LOCAL_LLM_KEY   # Can be dummy value in env
      model: Qwen/Qwen2.5-Coder-32B-Instruct

    lmstudio:
      vendor: openai-compatible
      endpoint: http://localhost:1234/v1/chat/completions
      api_key_env: LM_STUDIO_KEY
      model: local-model
```
*(No `KEEL_ALLOW_REMOTE_ENDPOINT` needed for loopback endpoints)*

---

## 3. Local Offline Models (Ollama)

Keel natively supports local [Ollama](https://ollama.com) instances without any API key or configuration profile.

* **Vendor Prefix**: `ollama:<model>`
* **Prerequisites**: Ollama running locally (`ollama serve`)

```bash
# Pull model first
ollama pull qwen2.5-coder:32b

# Delegate implementation to local Ollama
/keel:ship 42 --delegate ollama:qwen2.5-coder:32b

# Use DeepSeek-R1 locally for review
/keel:ship 42 --review-delegate ollama:deepseek-r1:14b

# The same dispatch, directly: one POST to the local /api/generate
keel delegate run --provider ollama:qwen2.5-coder:32b --role implement --prompt-file brief.md
```

---

## 4. Agent CLIs (Subprocess)

If you have official agent CLI tools installed and authenticated on your machine, Keel can drive them as subprocesses.

### Claude Code
* **Vendor**: `claude`
* **Prerequisites**: Claude Code installed (`claude` in PATH) and authenticated.
```bash
/keel:ship 42 --delegate claude
```

### Codex
* **Vendor**: `codex`
* **Prerequisites**: OpenAI Codex CLI in PATH.
```bash
/keel:ship 42 --delegate codex
```

### Antigravity
* **Vendor**: `agy`
* **Prerequisites**: Antigravity CLI (`agy`) in PATH.
```bash
/keel:ship 42 --delegate agy
```

For all three, the invocation is core's: `keel delegate run` selects the vendor's
read-only mode for `--role review|gate|chair` and its write-enabled mode for
`--role implement|fix`, and the prompt travels on the CLI's standard input rather than its
argv — a prompt carries the diff, and an argv is world-readable in `ps`. A read-only
`claude` runs under a tool **allow-list** and no permission bypass; `codex` under its
`read-only` sandbox; `agy` under `--sandbox`, which is the only read-only mechanism it
documents and the reason it still needs the non-interactive permission flag.

```bash
keel delegate run --provider claude --role review --prompt-file rubric.md
```

---

## 5. Generic CLI Profiles

You can wrap other AI coding tools (such as [Aider](https://aider.chat) or Cursor's CLI) as Keel delegates:

```yaml
knobs:
  delegate_profiles:
    aider:
      vendor: cli
      command: aider
      args: ["--yes", "--no-git", "--message-file"]
      prompt_mode: arg
      model_arg: "--model"
      model: sonnet
      review_args: ["--read-only"]

    cursor:
      vendor: cli
      command: cursor-agent
      args: ["--force"]
      prompt_mode: arg
      model_arg: "--model"
      model: cursor-grok-4.5-high
```

---

## 6. Multi-Model & Ensemble Review Posture

Keel encourages **cross-model verification** to prevent self-affirming model blind spots:

### Heterogeneous Review Panels
Run implementation on one vendor and assign independent reviewers on different model architectures:
```bash
# Implement with Claude 3.7, review with Gemini 2.5 Pro and GPT-4o
/keel:ship 101 \
  --delegate anthropic-api:claude-3-7-sonnet-20250219 \
  --review-delegate google-api:gemini-2.5-pro \
  --reviewers 2
```

### Cross-Vendor AI-Jury Gate
When [`ai-jury`](https://github.com/berkayturanci/ai-jury) is installed, Keel gates PR merges using a multi-vendor deliberation panel (e.g. Claude + OpenAI + Gemini voting on safety and quality):
```bash
/keel:ship 101 --jury
```

---

## Summary Comparison Table

| Category | Identifier / Vendor | Transport | Setup Requirement | `keel delegate run --provider …` |
|---|---|---|---|---|
| **Hosted Anthropic** | `anthropic-api:MODEL` | HTTP (stdlib) | `ANTHROPIC_API_KEY` | `anthropic-api:claude-3-7-sonnet-20250219` |
| **Hosted OpenAI** | `openai-api:MODEL` | HTTP (stdlib) | `OPENAI_API_KEY` | `openai-api:gpt-4o` |
| **Hosted Google** | `google-api:MODEL` | HTTP (stdlib) | `GEMINI_API_KEY` | `google-api:gemini-2.5-pro` |
| **OpenAI-Compatible** | `knobs.delegate_profiles` | HTTP (stdlib) | Custom endpoint + env key | `openrouter:deepseek/deepseek-r1` |
| **Local Ollama** | `ollama:MODEL` | HTTP (local) | Local `ollama` daemon | `ollama:qwen2.5-coder:32b` |
| **Agent CLI** | `claude`, `codex`, `agy` | Subprocess | Installed CLI in PATH | `claude` |
| **Generic CLI** | `knobs.delegate_profiles` (`cli`) | Subprocess | Tool binary in PATH | `aider` |

---

## Which of these work on your machine

Everything above is what keel *supports*. What is usable **here** is a property of the
machine and the person, and `keel doctor --providers` is the command that answers it:

```bash
keel doctor --providers          # a table: available / reason / transport / capabilities
keel doctor --providers --json   # providers[], registry_path, warnings
```

It probes every built-in vendor, every `knobs.delegate_profiles` entry, and every entry of
the machine-level [provider registry](configuration.md#provider-registry) — agent CLIs by
`PATH` plus `--version`, hosted APIs by key *presence* (names only; no request is made),
Ollama by its local `/api/tags`, which also lists the served models. Probes are time-boxed
and fail-soft; an unavailable provider always says why.

Keys and endpoints you do not want in a committed `project.yaml` belong in
`~/.keel/providers.yaml` (or `$KEEL_PROVIDERS`), which is operator-owned and never
committed. Project profiles win on a name clash, and the clash is reported rather than
silently applied.
