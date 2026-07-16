# Proposal: API-token-driven implementer/reviewer delegate

- **Issue:** [#548](https://github.com/berkayturanci/keel/issues/548)
- **Status:** proposed (phase 1 of 2 — design; implementation follows on approval)
- **Decision drivers:** `AGENTS.md` invariants (pure-core/thin-I/O split, single runtime
  dependency, fail-soft, attribution), the existing `ollama:MODEL` delegate contract in
  `ship.md` s4, and the stdlib-only hosted-API adapter precedent already shipped in
  ai-jury (`ai_jury/adapters.py`, issue ai-jury#430).

## Summary

Add hosted-API delegate values — `anthropic-api:MODEL` and `openai-api:MODEL` — to the
existing `--delegate` / `--review-delegate` set, so the s4 implement and s7 review steps
can run with **only an API token in the environment** (`ANTHROPIC_API_KEY` /
`OPENAI_API_KEY`) and no installed agent CLI.

The core insight that keeps this small: **keel already has a delegate contract for a
model that cannot run tools.** `ship.md` s4 says of `ollama:MODEL`:

> A bare local model (Ollama) cannot run tools — there the orchestrator does every
> git/PR step itself and delegates only code generation (generate a unified diff against
> a size-limited slice of the in-scope files, apply it, run gates, then commit/push/open
> the PR); retry up to 2 times on a bad/unapplicable diff, then fall back.

A hosted-API delegate is that same contract with the HTTP endpoint swapped: instead of a
local Ollama server, a single stdlib `urllib` POST to the vendor's messages/responses
endpoint. No agentic tool-use loop, no SDK, no new orchestration path.

## Decisions (the seven questions from #548)

### 1. Where the loop lives — there is no loop

The `ollama:` precedent already answers this. The orchestrator (host agent following the
adapter prose) keeps doing every git/PR step itself; the delegate is invoked exactly
once per attempt to turn *(task brief + size-limited file slice)* into *(unified diff)*,
and once per review to turn *(diff + rubric)* into *(verdict + findings)*. That is a
single-shot HTTP call, not an agent loop.

The HTTP call itself is network I/O, so per the pure-core/thin-I/O split it lives in a
new thin wrapper: **`src/keel/api_delegate.py`**, alongside `runner`/`git`/`github`.
The pure core (`agents.py`, `ship.py`) gains only:

- two new accepted vendor strings in the delegate value set, and
- a pure `is_api_delegate(vendor) -> bool` predicate for dispatch.

Both are trivially held at the 100 % line+branch bar. The wrapper follows the same
fail-soft convention as the other thin wrappers ("keep logic out of them").

### 2. Dependency budget — stdlib only, no SDK, no optional extra

`AGENTS.md`: *"Exactly one runtime dependency on Linux/macOS: PyYAML."* ai-jury already
proved the pattern works: its `_HostedApiAdapter` subclasses call Anthropic/OpenAI/Gemini
with nothing but `urllib.request` — payload built by hand, response parsed with `json`,
an SSRF-safe opener that registers only HTTP/HTTPS handlers and follows no redirects.

`api_delegate.py` ports that pattern (not the code verbatim — keel needs diff-shaped
output, ai-jury needed review text, see §3):

| vendor value | endpoint | auth header | env key |
|---|---|---|---|
| `anthropic-api:MODEL` | `POST https://api.anthropic.com/v1/messages` | `x-api-key` + `anthropic-version` | `ANTHROPIC_API_KEY` |
| `openai-api:MODEL` | `POST https://api.openai.com/v1/chat/completions` | `Authorization: Bearer` | `OPENAI_API_KEY` |

The optional-extra alternative (`pip install "keel[api-delegate]"` pulling vendor SDKs)
is **rejected**: it forks the install story for no capability gain — the two calls above
are a few dozen lines of stdlib each, and ai-jury has run the same shape in production
since its 1.10 release.

### 3. Tool surface — none; the diff contract replaces it

The delegate never touches the filesystem, git, or GitHub. Per the `ollama:` contract
the orchestrator:

1. builds the prompt: issue brief + acceptance criteria + a size-limited slice of the
   in-scope files (same slicing rule as the local-model path);
2. requests **a single unified diff** (implementer) or **a structured verdict**
   (reviewer) as the entire response;
3. applies the diff in the worktree, runs gates, commits/pushes — exactly the work it
   already owns for `ollama:MODEL`.

This is also why ai-jury's adapters can't be imported wholesale: they are read-only
review adapters. What keel reuses is the transport/security plumbing (§2, §7), while the
prompt/response contract comes from keel's own s4 prose.

### 4. Turn and cost limits

Same envelope as the local-model path, made explicit for an unattended, metered API:

- **Attempts:** 1 call + up to 2 retries on a bad/unapplicable diff (existing s4 rule),
  then fail-soft fallback to `HOST_AGENT`. A retry re-sends with the apply error
  appended; it is a fresh single-shot call, not a conversation.
- **Response cap:** `max_tokens` set explicitly on every request (default 16384,
  overridable via a knob) — an unattended loop must not be able to buy an unbounded
  completion.
- **No retry on quota:** HTTP 429 / `RESOURCE_EXHAUSTED` is not retried (matches the
  existing s4 quota fail-over rule and ai-jury's shared status-code mapping:
  401/403 → auth failure, 429 → rate-limit); the step fails soft and falls back.
- **Timeout:** one per-request timeout knob (default 300 s), enforced by `urllib`.

### 5. GitHub access — unchanged

The delegate replaces the *code-generation engine only*. All issue/PR reads and writes
keep flowing through the already-resolved transport (`gh` → `mcp` → `none`, per
`docs/keel/github-transport.md`), driven by the orchestrator. The API token grants
vendor-API access, never GitHub access; `GITHUB_TOKEN`/`gh` auth remains a separate,
existing concern. Nothing in this proposal touches the transport story.

### 6. Capability name — one generic `api-token`

`runtime-capabilities.md`'s boundary section requires capability detection to stay
consumer-neutral, which argues against per-vendor names (`anthropic-api-key`, …) in the
capability table. One new capability:

- **`api-token`** — a vendor API key required by the selected `*-api:` delegate is
  present in the environment.

Detection is contextual: it reports present only when the *selected* delegate's env key
exists (running with `openai-api:` and only `ANTHROPIC_API_KEY` set does not satisfy
it). Vendor selection lives in the delegate value, not the capability name. Unknown
names failing validation (existing rule) is unaffected.

### 7. Secret handling — reuse the `secrets` consent scope and redaction

- The key is read **from the environment only** — never from `project.yaml` or any file
  keel parses (same rule as ai-jury).
- Reading it is a `secret_access` side effect, which `consent.py` already maps to the
  `secrets` scope (`_SIDE_EFFECT_SCOPES["secret_access"] = ("secrets",)`). An
  `*-api:` delegate therefore **requires the `secrets` scope in the approved
  `operator_consent.delegated_agent_scope`** — no new enforcement path, the existing
  gate covers it. Without the scope, resolution fails soft to `HOST_AGENT` before any
  key is read.
- The key never appears in prompts, PR/issue bodies, comments, or the run ledger: error
  text is scrubbed before surfacing (port of ai-jury's `_scrub_secret` +
  `_invalid_key_reason` control-character guard), and keel's existing
  credential-assignment redaction rules already cover the ledger path.

## Two decisions the issue left open

### Delegate value syntax: `anthropic-api:MODEL`, not `api:anthropic`

`agents.split_delegate()` partitions on the **first** colon into `(vendor, model)` —
`"ollama:qwen2.5"` → `("ollama", "qwen2.5")`. The issue's suggested `api:anthropic`
would parse as vendor `api`, model `anthropic`, and leave nowhere for the actual model
name. Instead of changing the splitter, adopt ai-jury's exact vendor vocabulary —
`anthropic-api`, `openai-api` — which fits the existing 2-part shape unchanged:

- `--delegate anthropic-api:claude-sonnet-5` → `("anthropic-api", "claude-sonnet-5")`
- attribution via the existing pure helpers: `agent:anthropic-api` +
  `model:claude-sonnet-5` (versionless base via `model_base()`), ledger `system`
  `anthropic-api:claude-sonnet-5`.

A model name is **required** (like `ollama:`): there is no safe default to bake into
config for a metered vendor API.

### Tier-3: refused, like the local-model path

The conservative default: `*-api:` implementers are **refused on tier-3** exactly as
`ollama:` implementers are today, falling back to `HOST_AGENT`. Rationale: what tier-3
guards against is an implementer without interactive judgment driving high-risk paths,
and a single-shot diff generator has the same shape regardless of whether the weights
run locally or behind a vendor API. If experience shows hosted frontier models warrant
tier-3 trust, loosening later is an additive knob; the reverse migration is a breaking
change. Reviewers (`--review-delegate`) are not tier-restricted today and stay
unrestricted — review output is advisory input to the gate, not a mutation.

## Scope of the implementation PR (phase 2)

| file | change |
|---|---|
| `src/keel/api_delegate.py` | new thin I/O wrapper: request builder, stdlib POST, SSRF-safe opener, response→diff/verdict extraction, secret scrubbing, status-code mapping |
| `src/keel/agents.py` | accept the two new vendor strings; `is_api_delegate()` predicate (pure, 100 % covered) |
| `src/keel/adapters/commands/ship.md` | extend `--delegate`/`--review-delegate` value set + s4/s7 prose: API-delegate follows the local-model contract with the endpoint swapped |
| `docs/keel/runtime-capabilities.md` | add `api-token` |
| `docs/keel/cli.md`, `parity-matrix.md`, `command-contracts.md` | document the new values + consent/attribution contract |
| `docs/keel/github-actions.md` | narrow the "agentic steps still need an agent host" carve-out |
| `tests/test_agents.py`, `tests/test_api_delegate.py` | pure dispatch/precedence to 100 %; wrapper tested with a mocked opener per the `runner`/`git`/`github` fail-soft pattern |

Out of scope (explicitly): a multi-turn tool-use loop, vendor SDK integration, Google
(Gemini) support (additive later, same pattern), any change to GitHub transport, and
any relaxation of tier-3 policy.

## Risks

- **Diff quality from single-shot generation.** Hosted frontier models are markedly
  stronger than typical local models at emitting clean unified diffs, but the retry ×2 +
  fail-soft fallback bounds the blast radius: worst case, the step costs three API calls
  and lands back on the host agent.
- **Prompt-injection surface.** The prompt embeds issue text and file contents. The
  delegate's output is a diff that the orchestrator applies *inside the worktree* and
  then pushes through the full CI/review/gate pipeline — the same containment story as
  every other implementer; no new trust is granted to the model output.
- **Cost surprise.** Bounded by `max_tokens`, the no-retry-on-429 rule, and the 3-call
  attempt cap per step.
