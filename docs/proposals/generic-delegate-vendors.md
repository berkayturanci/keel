# Proposal: generic delegate vendors (`cli`, `openai-compatible`, `google-api`)

- **Issue:** [#659](https://github.com/berkayturanci/keel/issues/659)
- **Status:** proposed — design settled here; `cli` implemented alongside, hosted vendors follow
- **Precedent:** [#548](https://github.com/berkayturanci/keel/issues/548) /
  [`api-token-delegate.md`](api-token-delegate.md) — same "swap the engine, keep the
  contract" shape, same constraints (stdlib-only, no-tools contract, `secrets` scope, tier-3
  refusal).

## Summary

`--delegate` accepts a closed set of vendors. Every new provider is a code change. This adds
two **generic** vendors that turn provider support into configuration, plus one named vendor
that was deferred from #548:

| vendor | unlocks | status here |
| --- | --- | --- |
| `cli` | any local coding-agent CLI — `cursor-agent`, `gemini`, Aider, Goose, Copilot CLI | **implemented** |
| `openai-compatible` | any OpenAI-shaped hosted API — OpenRouter, Groq, DeepSeek, Together, LiteLLM, vLLM | designed, deferred |
| `google-api` | Gemini via the hosted API (`GEMINI_API_KEY`) | designed, deferred |

`cli` goes first because it is the only one with a **demonstrated blocked case**: `cursor-agent`
and `gemini` are installed and authenticated on the operator's machine and cannot be delegated
to (issue #659 comment). It is also the only one that adds no new network surface.

## Decision 1 — extra config lives in named delegate profiles

`--delegate` is a single CLI token parsed by `agents.split_delegate()` (first colon →
`(vendor, model)`). That fits `ollama:qwen2.5` and `anthropic-api:claude-sonnet-5`, but the
generic vendors need more fields than a token can carry: a `cli` vendor needs `command` and a
prompt-delivery mode; `openai-compatible` needs `endpoint` and an API-key env-var name.

**Decided: named profiles under `knobs.delegate_profiles`, referenced by name.**

```yaml
knobs:
  delegate_profiles:
    cursor:
      vendor: cli
      command: cursor-agent
      args: ["-p", "--force"]   # implementer: print mode + non-interactive approval
      review_args: ["-p"]       # reviewer: same, minus permission to approve edits
      prompt_mode: arg          # "stdin" (default) | "arg"
      model: null               # default model; --delegate cursor:<model> beats it
      model_arg: --model        # how the model reaches the command
    gemini-cli:
      vendor: cli
      command: gemini
      prompt_mode: arg
```

Used as `--delegate cursor`. Rejected alternatives:

- **Extra CLI flags** (`--delegate-command`, `--delegate-prompt-mode`, …) — multiplies the flag
  surface and puts project configuration on the command line, against keel's own rule that
  every project specific is read from `.keel/project.yaml`.
- **Structured delegate strings** (`cli:cursor-agent:arg`) — breaks the two-part split every
  other vendor uses, and gets unreadable once `openai-compatible` needs an endpoint.

Profiles also give the hosted vendors a home when they land, so the config shape is decided
once rather than twice.

### Name resolution is fail-closed

A profile name is resolved **after** the built-in vendors, and a profile that shadows a
built-in name is a **config validation error**, not a silent override. So `claude`, `codex`,
`agy`, `ollama`, `anthropic-api`, `openai-api` can never be redefined by config, and an
operator who tries gets told at `keel validate` time instead of discovering it mid-run.

## Decision 2 — `prompt_mode`, because stdin is not universal

ship.md s4 currently hardcodes prompt delivery:

> write the prompt to a temp file and pipe via **stdin** (positional-arg passing hangs some CLIs)

That is correct for the three CLIs keel knows, and wrong in general. `cursor-agent`'s usage is
`agent [options] [command] [prompt...]` — the prompt is a **positional argument**. A `cli`
vendor without a delivery knob still could not drive it, which is exactly why ai-jury's
`GenericCLIAdapter` exposes `prompt_mode`.

`stdin` stays the default, so the existing guidance remains the norm and `arg` is the opt-in
for CLIs that need it.

## Decision 3 — the `cli` vendor inherits the local-model contract exactly

A generic CLI is treated as a **delegated CLI implementer**, the same category as
`codex exec` / `agy --print` today:

- The orchestrator owns every git/PR step; the delegate is asked only for code generation.
- Retry ×2 on an unusable result, then fall soft back to `HOST_AGENT`.
- **Refused on tier-3**, same as local models — an unvetted CLI is not a high-risk-path
  implementer.
- Attribution records the effective vendor and model: `agent:cli` plus the profile's model
  when known, and the profile name in the run record so the closure comment says *which* CLI
  ran, not just "cli".

No new consent scope: invoking a local CLI is the `shell`/subprocess surface keel already
uses for `codex`/`agy`. The profile's `command` is operator-authored config, exactly like
`build_gate_cmd` — the same trust level, and the same reason it is never taken from PR content
or agent output.

## Field report — validated against a real run before this landed

Another session hit this exact gap the same day, driving a different repo, and worked around
it manually. That baseline is worth recording because it confirms two design choices and adds
one the proposal had missed:

- **`prompt_mode: arg` is right.** They drove `cursor-agent -p --model <model> --force` with
  the prompt as a positional argument — independent confirmation that the stdin-only rule
  would have blocked them.
- **Per-run model selection matters.** `cursor-agent --list-models` offers
  `cursor-grok-4.5-high`, `cursor-grok-4.5-high-fast`, `gpt-5.3-codex*`, `composer-2.5` and
  several Claude tiers. Editing config to switch model is friction, so the s4 prose now
  states the precedence explicitly: `--delegate <profile>:<model>` beats the profile's
  `model`, which beats the CLI default. One `cursor` profile serves the whole family.
- **The reviewer role is where a generic delegate earned its place, not the implementer
  role.** Prompted to *refute* rather than approve, `cursor-grok-4.5-high` reviewed three
  PRs in that repo and refuted all three; each refutation was verified correct against the
  file before being acted on, and **none of the three was visible to CI** — every one was
  green (6/6, 16 tests, 16/16), plausible code, honest-sounding description. The common
  shape was *a claim the artefact does not deliver*: a fix that closed one of two
  directions with a test blind to the second; a gate whose verifier is invoked with an
  unbound variable under `set -u`, so it aborts instead of running, in a `.sh` no test
  exercises; a proving test fed an input the code under test makes unreachable. Gates check
  whether code runs, not whether it does what the PR says — that gap is what an adversarial
  reviewer covers.

  As implementer the same CLI produced sound code — but also a specific-looking external
  citation (registry reference numbers, an archive snapshot id) presented as verified when
  nothing had verified it. Generalised in s4: a delegate emitting the *artefact* of a check
  instead of the check is one failure mode with several costumes (invented citations, a
  fabricated `keel.review-verdict.v1` marker written into a shipped file, "tests pass" with
  no run behind it), so **any** verification a delegate reports is unperformed until the
  orchestrator reproduces it.

  **The tier-3 refusal is right for implementers and questionable for reviewers.** All
  three refuted PRs matched that repo's `tier3_globs`, so this proposal's rule would have
  declined exactly the cases where the delegate produced its entire value. The risk
  profiles are not symmetric: an implementer's output becomes a commit, while a reviewer's
  output is a claim the orchestrator must check before anything reaches the base branch — a
  wrong refutation costs a verification round, a right one catches what CI cannot. Splitting
  the tier gate **by role** rather than by vendor is tracked in #665; deliberately not done
  here, since loosening a safety rule belongs in its own change.

## Deferred, with the hard parts named

**`google-api`** is mechanical — one row in `api_delegate._VENDORS`
(`https://generativelanguage.googleapis.com/…`, `GEMINI_API_KEY`) plus its payload/parse pair,
mirroring the existing two. Deferred only to keep this change reviewable.

**`openai-compatible`** carries the one genuinely new risk in this issue: a **config-supplied
endpoint**. Today `api_delegate` talks to two hardcoded URLs, which is why its SSRF story is
trivially safe. Letting config name the host means deciding, before any code:

- whether non-loopback endpoints require an explicit opt-in env var (ai-jury's
  `_ALLOW_REMOTE_ENDPOINT_ENV` is the precedent),
- whether plaintext `http://` to a non-loopback host is refused or merely warned,
- and that a custom `api_key_env` supplies only the **name** — the value still comes from the
  environment and never from config.

That deserves its own security pass rather than riding along here.

## Scope of the implementation landing with this doc

| file | change |
| --- | --- |
| `src/keel/config.py` | `knobs.delegate_profiles`; parse + validate (known vendor, required fields per vendor, `prompt_mode` ∈ {stdin, arg}, no built-in shadowing) |
| `src/keel/agents.py` | `CLI_VENDORS`; `resolve_delegate_profile()`; `profile_attribution()`; `is_safe_model_token()`; profile-aware `split_delegate` consumers — all pure |
| `src/keel/adapters/commands/ship.md` | s4/s7: generic-CLI dispatch, `prompt_mode`, tier-3 refusal, attribution; regenerate adapter surfaces |
| `docs/keel/configuration.md`, `parameter-reference.md`, `runtime-capabilities.md` | document the profile block and the new delegate form |
| `tests/test_config.py`, `tests/test_agents.py` | parsing, validation errors, resolution precedence, shadowing rejection — to the 100 % bar |

**Two guarantees here are enforced by adapter prose, not by code**, and are recorded so
they are not mistaken for something keel checks:

- **Argv safety of the per-run model.** `agents.is_safe_model_token()` exists and is
  tested, but nothing in `src/keel` calls it — keel core never spawns the delegate, the
  orchestrator does. Same shape as `split_delegate`/`attribution`, but this one is
  security-flavoured, so the obligation is written down rather than assumed.
- **Read-only review for a `cli` profile.** Unlike every other reviewer vendor, a profile
  is an arbitrary binary with one `command` for both roles. `review_args` is the
  operator's lever; keel validates neither list.

Out of scope: `openai-compatible`, `google-api`, any change to hosted-API delegates, and any
relaxation of tier-3.
