# Supported AI Models & Providers Guide

> **Keel is multi-model and vendor-neutral by design.**
> You can drive any step of the Keel workflow using your choice of AI model:
> hosted API endpoints, official coding CLIs, OpenAI-compatible providers,
> or locally hosted models (Ollama/vLLM).

---

## Table of Contents

1. [Architecture & Roles](#architecture--roles)
2. [How to Select Models](#how-to-select-models)
3. [Hosted API Delegates (Zero-CLI)](#1-hosted-api-delegates-zero-cli)
   - [Anthropic (Claude)](#anthropic-claude)
   - [OpenAI (GPT / o-series)](#openai-gpt--o-series)
   - [Google (Gemini)](#google-gemini)
4. [OpenAI-Compatible Profiles (OpenRouter, DeepSeek, Groq, local LLMs)](#2-openai-compatible-profiles)
   - [OpenRouter (Universal Model Gateway)](#openrouter)
   - [DeepSeek Official API](#deepseek-official-api)
   - [Groq](#groq)
   - [Together AI](#together-ai)
   - [Local vLLM / LM Studio / LiteLLM Proxy](#local-vllm--lm-studio--litellm)
5. [Local Offline Models (Ollama)](#3-local-offline-models-ollama)
6. [Agent CLIs (Subprocess)](#4-agent-clis-subprocess)
   - [Claude Code (`claude`)](#claude-code)
   - [Codex (`codex`)](#codex)
   - [Antigravity (`agy`)](#antigravity)
7. [Generic CLI Profiles (Aider, Cursor Agent, Custom Scripts)](#5-generic-cli-profiles)
8. [Multi-Model & Ensemble Review Posture](#6-multi-model--ensemble-review-posture)
9. [Summary Comparison Table](#summary-comparison-table)

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

### 3. Project Configuration Defaults (`project.yaml`)
Map specific issue roles/platforms to default agents in `.keel/project.yaml`:
```yaml
knobs:
  implementer_agents:
    core: backend-developer     # maps to host agent or profile
    frontend: anthropic-api:claude-3-7-sonnet-20250219
    docs: google-api:gemini-2.5-flash
```

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

| Category | Identifier / Vendor | Transport | Setup Requirement | Example Usage |
|---|---|---|---|---|
| **Hosted Anthropic** | `anthropic-api:MODEL` | HTTP (stdlib) | `ANTHROPIC_API_KEY` | `--delegate anthropic-api:claude-3-7-sonnet-20250219` |
| **Hosted OpenAI** | `openai-api:MODEL` | HTTP (stdlib) | `OPENAI_API_KEY` | `--delegate openai-api:gpt-4o` |
| **Hosted Google** | `google-api:MODEL` | HTTP (stdlib) | `GEMINI_API_KEY` | `--delegate google-api:gemini-2.5-pro` |
| **OpenAI-Compatible** | `knobs.delegate_profiles` | HTTP (stdlib) | Custom endpoint + env key | `--delegate openrouter:deepseek/deepseek-r1` |
| **Local Ollama** | `ollama:MODEL` | HTTP (local) | Local `ollama` daemon | `--delegate ollama:qwen2.5-coder:32b` |
| **Agent CLI** | `claude`, `codex`, `agy` | Subprocess | Installed CLI in PATH | `--delegate claude` |
| **Generic CLI** | `knobs.delegate_profiles` (`cli`) | Subprocess | Tool binary in PATH | `--delegate aider` |
