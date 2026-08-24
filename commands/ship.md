---
description: Drive a GitHub issue end-to-end through the keel backbone (select → branch → implement → CI → review → test → merge → close → capture), reading every project value from .keel/project.yaml via the keel CLI.
argument-hint: "[issue numbers...] [--compound|--profile <standard|compound>] [--delegate <claude|codex|agy|ollama:MODEL|anthropic-api:MODEL|openai-api:MODEL|google-api:MODEL|PROFILE>] [--review-delegate <claude|codex|agy|ollama:MODEL|anthropic-api:MODEL|openai-api:MODEL|google-api:MODEL|PROFILE>] [--review-comments <inline|summary>] [--reviewers <1|2|3>] [--jury|--no-jury|--jury-advisory] [--hotfix] [--dry-run] [--wizard]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(jury:*), Read, Edit, Write, Agent
---

# /keel:ship

## Command step evidence

Every numbered step in this command is contractual. Complete the step, record the
evidence it asks for, or explicitly mark it `N/A — <reason>` before moving on. If a step
has an external side effect such as a GitHub comment, issue, review, report, branch, or
PR, the side effect must be posted or written through the selected transport and cited in
the final summary. Never silently skip a step because the runtime, agent, or prompt feels
obvious.

Project-neutral flagship workflow. **Every project value comes from `.keel/project.yaml`
via the `keel` CLI** — never hardcode a branch, command, glob, agent, timezone, window,
allowlist, or workflow name here. Reference knobs by name: `base_branch`, `build_gate_cmd`,
`lint_cmd`, `implementer_agents`, `delegate_profiles`, `tier3_globs`, `ci_workflows`,
`docs_gate_paths`, `merge_window`, `merge_window_mode`, `timezone`. Anything truly app-specific stays in the
project (config knobs, or a `.keel/extensions/` Lego), never inlined here.

All committed/published artifacts (commits, branch names, PR/issue titles + bodies,
comments, queue files) follow the project's language policy. Free-form chat with the user
may stay in any language.

## Live progress — stamp this run (required)

So this run shows live on `keel-visual`'s board — exactly like every other keel command —
record it with `keel activity` **as you go**. Ship's phases are the backbone:
`s0` → `s1` → `s2` → `s3` → `s4` → `s5` → `s6` → `s7` → `s8` → `s9` → `s10` → `s11` → `s12`
(config → select → branch → guard → implement → classify → ci → review → test → fixloop →
merge → capture → close). Use **the same `--run-id`** you pass to `keel ship` / `keel
checkpoint` (e.g. `ship-<issue-or-pr>`) so the board treats them as one run:

- **Right now, before Step 0 below**, stamp the first phase:
  `keel activity .keel/project.yaml --root . --write --command ship --run-id "$RUN_ID" --phase s0`
- Re-run with the next `--phase` (`s1`, `s2`, …) **as you advance** through the backbone,
  adding `--issue <N>` once the issue is selected (s1) and `--pull-request <PR>` once the PR
  exists (s2+) so the board can pair this with the checkpoint/ledger records and never
  double-list the same run.
- At the end (after close): `keel activity .keel/project.yaml --root . --run-id "$RUN_ID" --done`

This is in addition to the rich `keel checkpoint` / ledger records the steps below write;
the board de-duplicates the two and prefers the checkpoint's detail. Treat it like any other
contractual step — do not skip it. The one allowed exception is a core too old to ship
`keel activity` (keel < 1.6.0): then skip it silently and never block the command.

## Artifact hygiene — never scribble in the consumer's checkout (required)

The consumer's repo root is theirs, not your scratchpad. **Do not write transient files to it.**
Any staging file you need — a PR diff (`gh pr diff > …`), an issue/body dump, draft review or
closure prose, a `plan.json`/`ship.json` capture, a one-off patch or script — goes in the
**keel-owned scratch directory**, which is gitignored so it never pollutes `git status` or
Finder:

```
SCRATCH="$(keel scratch-dir --root .)"   # = .keel/scratch (created + gitignored on first call)
gh pr diff "$PR" > "$SCRATCH/pr-$PR.diff"
```

Rules: (1) prefer piping/here-strings over temp files at all; (2) when a file is unavoidable,
write it under `$SCRATCH` (or a true OS temp dir), **never** the repo root; (3) honour an
explicit operator `--output`/`--debug` path verbatim when one is given. keel's own runtime
state (`.keel/state/`, `.keel/activity/`, locks) is auto-written under `.keel/` and the
`.keel/.gitignore` is scaffolded for you — leave it tracked. If you find yourself about to
create `pr_<n>_review.md`, `pr<n>.diff`, `issue.md`, `plan.json`, or similar at the root —
stop and redirect it into `$SCRATCH`.

**Reclaim at end of run (required).** Taking out the trash is also our job: after s12 (or on
any early exit), run `keel gc .keel/project.yaml --root .` to empty `.keel/scratch` and prune
old `.keel/activity` records so they do not accumulate. It is **fail-soft** (a cleanup error
degrades to a no-op, never blocks the run) and **never** touches the durable run ledger,
checkpoint, or locks. Tune retention with `--keep-activity <N>`; preview with `--dry-run`.

## Step 0 (s0) — orient (deterministic, via the CLI)

```bash
keel validate .keel/project.yaml --root .     # config + extensions must be valid
keel plan     .keel/project.yaml --root .     # the backbone + this project's gates/Lego
keel plan     .keel/project.yaml --root . --command ship --live --json \
              --run-id "$RUN_ID" --issue <N>  # ALSO stamps the activity board (run shows live)
keel window   .keel/project.yaml              # is the merge window open right now?
```

Passing `--run-id`/`--issue` to this Step 0 plan makes core write the activity record
itself, so the run appears on `keel-visual`'s board the moment it plans — you do not
depend on the per-phase `keel activity` calls below for the run to *show up* (they still
advance it). Use the same `$RUN_ID` as the rest of the run (`ship-<issue-or-pr>`).

The live plan is the operator-consent preflight. Before s1 and before any branch,
worktree, GitHub write, delegation, secret, release, or production-adjacent access, parse
`contract.operator_consent`; if `requires_operator_consent` is true, STOP and ask the
operator to rerun with the required `--approve-scope` values. Do not infer secret or
credential approval from project knowledge. Store `operator_consent.delegated_agent_scope`
for every later delegated-agent brief.

`keel validate`/`plan` resolve `base_branch`, the knob commands (`build_gate_cmd`,
`lint_cmd`), `implementer_agents`, `delegate_profiles`, `tier3_globs`, `ci_workflows`,
`docs_gate_paths`, and the `tester` / `pre-merge` / `reviewers` / `capture` extensions. `keel window`
evaluates `merge_window` in the project `timezone` and reports `merge_window_mode`
(`pause` = halt outside the window; `freeze` = defer to the morning queue). The merge
resource claim is acquired and released by `keel merge` at the merge step (s10) only.

After s1 selects an issue, rerun the live preflight with the selected issue title/body/labels:

```bash
keel plan .keel/project.yaml --root . --command ship --live --json \
  --target "issue #<N>" \
  --issue-title "$ISSUE_TITLE" \
  --issue-body "$ISSUE_BODY" \
  --issue-label "$ISSUE_LABELS"
```

Parse `contract.issue_intake` before s2. If `status` is `needs-input`, post or ask the
generated `questions` and STOP that issue before branch/worktree/code mutation. If `status`
is `blocked` or `out-of-scope`, append or preserve the structured run-ledger record,
skip mutation, and move to the next selected issue when watch/work-block policy allows.
Only `ready` may proceed to s2. This is the same readiness discipline expected from a
human teammate: clarify the ticket before starting work and keep the clarification trail
in the run ledger.

**Run ledger.** Read `contract.run_ledger` from `keel plan --json` or the
`result.run_ledger` block from `keel ship --json`. Do not infer ship outcomes by parsing
free-form PR or issue comments. For live runs, append exactly one structured record with
`keel ship .keel/project.yaml --root . --live --append-ledger --run-id <id> --issue <N>
--pull-request <PR> --capture-status <applied|deferred|skipped[:reason]> --capture-reason <reason>
--implementer <agent> --reviewer-agent <agent> --tester <agent>
--host-agent <HOST_AGENT> --transport <gh|mcp> --profile <standard|compound>
--approve-scope <scopes>
--operator <operator> --json` after the ship assessment and capture status are known.
Pass the s0 preflight **run context** through: `--host-agent` (the resolved `HOST_AGENT`)
and `--transport` (the detected `gh`|`mcp` transport from s0); `--profile`, the jury mode,
and the consent summary are already available from the run and are stamped onto the
`ship_run` record so the s11 closure comment renders a durable **Run context** block.
`--transport` defaults to the transport keel resolved for the run when omitted. A missing
`--host-agent` emits a warning on live append; pass `--strict-run-context` when the run
should fail instead of producing a degraded closure audit trail.
If the configured ledger path is missing, treat it as empty history; if a ledger record is
malformed, stop capture/reporting and ask for operator help instead of silently falling
back to comment scraping.

**Checkpoint / resume.** Read `contract.checkpoint` from `keel plan --json` before live
work. At the start of a run, call `keel resume .keel/project.yaml --root . --json` and
inspect `resume_plan`. If it returns `no-checkpoint`, start normally. If it returns
`ambiguous`, stop and reconcile the PR/worktree state it names before doing any mutation.
If it reports a merged PR, resume at capture or closeout; never repeat the merge.

During live ship runs, write a checkpoint after each safe step boundary and before moving
to the next step:

```bash
keel checkpoint .keel/project.yaml --root . --write \
  --run-id "$RUN_ID" --checkpoint-command ship --step s6 \
  --target "issue #<N>" --issue-queue <N> --active-issue <N> \
  --branch "$BRANCH" --worktree "$WORKTREE" --pull-request "$PR" \
  --head-sha "$HEAD_SHA" --last-check ci
```

Update the arguments to match the actual boundary: completed steps, last gate/review/check,
merge state, capture state, close state, and stop reason. The checkpoint is the active
resume point, not run history. Do not delete or overwrite project extensions while writing
or resuming from it.

When checkpointing is configured (`policy_pack.reports.checkpoint`), `keel merge` enforces a
**checkpoint gate** at s10 (audit GAP-13): write a checkpoint for the run at `--step s10`
before calling `keel merge`, or the merge is refused with
`no current checkpoint for run <id> at step s10`. Projects without checkpoint config are
unaffected (the gate is advisory). The audited escape is `keel merge --no-checkpoint-gate`
with a named `--operator`.

**GitHub transport.** Prefer the `gh` CLI when present (richer JSON, `--watch`); detect
once at session start (`command -v gh`) and, when absent, fall back to an equivalent
GitHub MCP/API transport for the same operations (issue read/list/comment/close/label,
PR read/create/ready/merge/branch-update, check-runs, review writes). Translate field
semantics consistently (e.g. mergeable-state `behind`/`dirty`, draft flag, base ref) and
poll-with-delay where no native `--watch` exists. The orchestrator passes the resolved
transport mode to the implementer so it uses the same one (push via `git push` first,
fall back to an API push on an HTTP 403). Raw failed-CI-log access may be unavailable on
the fallback transport — there the fixer gets the check name + details URL and reproduces
locally; if it cannot, mark blocked and quote the details URL. State the detected transport
mode in your first user-facing line, **and record it** (alongside the resolved host agent)
for the s11 closure comment: carry the transport (`gh`|`mcp`) and `HOST_AGENT` forward so
the `--append-ledger` call at s11 stamps them onto the `ship_run` record as durable PR
evidence (see s11). Evidence verification flags a closure Run context where every field
degraded as `run-context-empty`; do not treat an all-unknown Run context as acceptable
capture.

### Argument parsing

- **Bare positive integers** ⇒ explicit issue number(s). Reject zero/negative.
- `--compound` / `--profile <standard|compound>` — select the workflow profile. Default
  `standard`; `--compound` is an alias for `--profile compound`. The compound profile swaps
  the `s4`/`s7`/`s9`/`s11` steps to compound behavior (see the **Compound profile** section)
  without forking the backbone. Composes with every other flag (e.g. `--compound --jury`).
- `--delegate <claude|codex|agy|ollama:MODEL|anthropic-api:MODEL|openai-api:MODEL|google-api:MODEL|PROFILE>` — the
  **implementer**. Per-run override of any issue role/delegate label. `ollama:` and the
  `*-api:` values require a non-empty model. The `*-api:` values are the **hosted-API
  delegates** (no agent CLI needed — just `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` in the
  environment; see s4). `PROFILE` is the name of a `knobs.delegate_profiles` entry — a
  **generic CLI vendor** configured in `.keel/project.yaml` (e.g. `--delegate cursor`;
  see s4). Built-in vendor names always win over a profile name. Default: the **host
  agent** (the CLI driving this run).
- `--review-delegate <…>` — the **reviewer** vendor (same value set, profile names
  included). Default: host agent.
- `--review-comments <inline|summary>` — how reviewer findings post (s7). Default `inline`.
- `--reviewers <1|2|3>` — override the tier-derived reviewer count. Default: auto (from tier).
- `--jury` / `--no-jury` / `--jury-advisory` — control the cross-vendor jury gate (s8).
  Precedence `--no-jury` > `--jury` > tier-3 auto > off. `--jury-advisory` = report-only.
- `--hotfix` — audited merge-window bypass (s10). Use sparingly.
- `--dry-run` — read-only rehearsal (see `--dry-run` section).
- `--wizard` — interactive opt-in only; runs the guided pre-s1 config collector (see
  `--wizard` section). In any non-interactive context it degrades to a logged no-op.

Reject unknown `--flags`, out-of-range `--reviewers`, an empty `ollama:`/`*-api:` model, a
`--delegate`/`--review-delegate` value that is neither a built-in vendor nor a configured
`knobs.delegate_profiles` name, a flag
missing its value, or a negative/zero positional. A flag and its value must appear
together; positionals are everything not consumed by a flag. Repeated single-value flags
(e.g. `--reviewers 2 --reviewers 3`) are user error. With **no issue numbers**, run in
watch mode: take the top of the backlog (s1). Resolve **`HOST_AGENT`** from the runtime
(the CLI executing this command: `claude` / `codex` / `agy`) — it is the default
implementer and reviewer; a delegate label or an explicit `--delegate`/`--review-delegate`
overrides it. State the detected window state and host agent in your first user-facing line.

## Backbone (do not reorder; the step IDs are fixed)

### s1 select
Take the issue(s) from args, or the top of the backlog (highest priority first, then
ascending issue number; cap the watch-mode batch and let the next run pick up the rest).
Validate each issue (skip/warn on closed ones); on a `gh` rate-limit/auth/network error,
log partial state and stop. Snapshot the queue once — do not re-poll mid-session. If the
queue is empty, log a one-line summary and exit.

For every selected issue, read title, body, and labels through the selected GitHub transport
and feed them into the issue intake preflight described in s0. In work-block/watch mode,
non-ready issues are skipped with their readiness reason and concrete questions recorded,
then the run continues with the next ready issue if one exists.

**More than one issue selected? Make the whole batch visible up front (deterministic).** Ship
processes issues one branch/worktree at a time, so an issue not yet reached would be invisible
on the `keel-visual` board until its turn. The moment the queue is snapshotted — before s2 for
the first item — stamp every selected issue's `s0` so the whole batch appears at once:

```bash
keel activity .keel/project.yaml --root . --write \
  --command ship --run-id "ship-$N" --phase s0 --issue "$N"
```

Use the canonical `ship-<N>` run id (the same one you carry through this run's per-phase
stamps) so each issue's later advances update the **same** row. This is the deterministic
dispatch stamp; do not leave board presence to whether a later per-phase call happens to run.
Same fail-soft exception as the run-stamp above (skip silently on keel < 1.6.0).

### s2 branch
Cut a work branch off `base_branch`. A **git worktree per issue** is the isolation
contract: never mutate the user's primary checkout. Create it under a gitignored,
repo-nested path (e.g. `worktrees/issue-<N>`); the worktree path is returned in the JSON
contract and hard-validated at s10 (must be nested under the repo root, never the repo
root or filesystem root). Every edit/build/push happens inside the worktree. Once the PR
exists, `keel verify-branch <project.yaml> --pr <N>` enforces this contract: the head must be
cut from an up-to-date `origin/base_branch` (else `stale`) and the work must live in a nested
linked worktree, not the primary checkout (else `contaminated`). `--allow-stale-base` is the
recorded operator escape for an intentionally stale base.

### s3 guard
Refuse if the working tree is dirty or the branch already has an open PR. **Blocker
auto-detection** is deterministic and core-owned — run `keel guard .keel/project.yaml
--issue <N>` (or `--issue-title`/`--issue-labels` offline). It evaluates the issue against
the configured blocker ruleset (`policy_pack.blocker_rules`, or built-in defaults) and
returns the matched rule id(s): word-boundary, case-insensitive title regexes
(`\bhotfix\b`, `\bsecurity\b`, `\bblocker\b`) and `blocker`/`hotfix`/`security` labels.
A non-empty match promotes the issue to a window-bypassing blocker — and the matched rule
id is what s10 requires as justification for `--hotfix` (see s10). Do **not** hand-wave a
blocker: if `keel guard` returns no match, the issue is not a blocker. Run `make plugin`
after editing this contract so the rendered adapters stay in sync.

Additional live signals the agent may still weigh (not part of the deterministic ruleset):
an alert/escalation label, or `base_branch` currently red on a **gating** `ci_workflow`
whose paths this PR touches. When the branch-scoped red-`base_branch` signal is unavailable
on the fallback transport, treat that rule as no-fire and log it.

### s4 implement *(agent)*
Resolve the implementer: `implementer_agents` by the issue's role label, **overridden by
`--delegate`**, defaulting to `HOST_AGENT`. Precedence: `--delegate` flag > issue
`delegate:*` label > `HOST_AGENT`. Dispatch:

- **Host / Claude-class subagent** — pick the role agent from `implementer_agents` by the
  issue's labels/paths; run the standard implement brief.
- **Delegated CLI implementer** (`codex exec`, `agy --print`, an Ollama model) — write the
  prompt to a temp file and pipe via **stdin** (positional-arg passing hangs some CLIs);
  run in the project root with the vendor's **network-enabled** mode so it can reach the
  GitHub API (sandbox-blocking flags break PR creation). Pass any per-issue model override
  from a `delegate-model:<name>` label. A bare local model (Ollama) **cannot run tools** —
  there the orchestrator does every git/PR step itself and delegates only code generation
  (generate a unified diff against a size-limited slice of the in-scope files, apply it,
  run gates, then commit/push/open the PR); retry up to 2 times on a bad/unapplicable
  diff, then fall back. **Local-model implementers are refused on tier-3** (high-risk,
  per `tier3_globs`; pre-classified from the issue's target paths/labels before the diff
  exists, ambiguous ⇒ treat as tier-2 and let s7 gate) — fall back to `HOST_AGENT` there.
- **Generic CLI implementer** (a `knobs.delegate_profiles` name, e.g. `--delegate cursor`)
  — resolve the delegate value against `knobs.delegate_profiles` **after** the built-in
  vendors above: built-ins always win, and a profile that shadows a built-in name is a
  `keel validate` error, never a silent override. Run the profile's `command` from the
  project root under the **same no-tools contract as the local-model path** — the
  orchestrator does every git/PR step itself and asks the CLI only for code generation
  (unified diff against a size-limited slice of the in-scope files, apply it, run gates,
  then commit/push/open the PR). Deliver the prompt per the profile's `prompt_mode`:
  **`stdin`** (the default) writes the prompt to a temp file and pipes it in
  (positional-arg passing hangs some CLIs), **`arg`** passes it as a positional argument
  for CLIs whose usage requires it (`cursor-agent`'s is `agent [options] [command]
  [prompt...]`). Model precedence: a per-run `--delegate <profile>:<model>` wins, else the
  profile's `model`, else the CLI's own default — so one profile serves a whole family
  (`--delegate cursor:cursor-grok-4.5-high` vs `--delegate cursor:composer-2.5`) without
  editing config per run. Pass the effective model as **`<model_arg> <model>`**, where
  `model_arg` is the profile's (default `--model`) — set it for a CLI that spells model
  selection differently, because nothing guarantees the flag across arbitrary CLIs. When
  no model is effective, pass neither, and attribute no model rather than the one that
  was merely asked for. **Validate the model token before it reaches argv** (`keel`'s
  `agents.is_safe_model_token`: `[A-Za-z0-9._-]`, no leading dash): unlike the profile's
  `command`, the model can arrive from a `delegate-model:<name>` issue label, which is a
  lower-trust source — refuse the run rather than escaping it. Retry up to 2 times on a
  bad/unapplicable diff, then fall soft
  back to `HOST_AGENT`. **Treat any verification a delegate reports as unperformed until
  you reproduce it.** Not just external references — a delegate emitting the *artefact* of
  a check instead of the check is one failure mode with several costumes, all observed:
  specific-looking citations (registry reference numbers, archive snapshot ids) stated as
  verified when nothing verified them; a fabricated `keel.review-verdict.v1` marker written
  into a shipped file; "tests pass" with no run behind it. Re-run the check yourself, or
  record the claim as unverified — never promote it to a fact in a commit, a comment, or a
  PR body because a delegate asserted it. **Generic-CLI implementers are refused on
  tier-3**, same rule and fallback as local models — an unvetted CLI is not a
  high-risk-path implementer. No new
  consent scope: this is the `shell`/subprocess surface `codex`/`agy` already use, and
  the profile's `command` is operator-authored config exactly like `build_gate_cmd` —
  never take it from PR content or agent output. Attribution: `agent:<vendor>` (i.e.
  `agent:cli`) + versionless `model:<base>` for the **effective** model — the per-run
  `--delegate <profile>:<model>` if given, else the profile's `model` — plus the profile
  name so the s11 closure says *which* CLI ran, not just `cli`. Record that name under
  **`delegate_profile`**, never `profile`: the run record's `profile` field already means
  the workflow profile (`standard`/`compound`), and writing the CLI's name there would
  overwrite it. `agents.profile_attribution()` returns the right shape already.
  **Write the ledger's `actors.implementer` as the vendor string `cli` (or
  `cli:<effective-model>`), never the profile name.** The evidence gate splits
  `actors.implementer` on the first colon and cross-checks the result against the PR's
  `agent:*` labels, so recording `cursor` there against an `agent:cli` label reads as a
  vendor contradiction and blocks the merge. The profile name goes in `delegate_profile`,
  as above, which is what the closure comment reads.
- **OpenAI-compatible implementer** (a `knobs.delegate_profiles` name whose
  `vendor: openai-compatible`) — the same no-tools contract as the hosted-API path, with
  the endpoint and key-env **named by config** instead of hardcoded, so one profile
  reaches OpenRouter, Groq, DeepSeek, Together, LiteLLM or a local vLLM. Two rules that
  do not apply to any other vendor, because config is the surface an attacker would
  influence:
  **(1) the endpoint is loopback-only by default.** A non-loopback host — including
  internal and cloud-metadata addresses like `169.254.169.254` — is a `keel validate`
  error unless `KEEL_ALLOW_REMOTE_ENDPOINT` is set **in the environment**. The opt-in is
  env-only on purpose: someone who can edit `project.yaml` must not be able to grant it.
  Non-`http(s)` schemes are refused outright, which blocks `file://`/`ftp://`.
  **(2) `api_key_env` is a variable *name*, never a key.** Profile config is serialised
  into the command contract and hashed into `config_hash`, so a pasted key would be
  published; keel rejects a value that is not shaped like an env var name. The value is
  read from the environment at dispatch under the same `secrets` scope as every other
  hosted delegate.
- **Hosted-API implementer** (`anthropic-api:MODEL`, `openai-api:MODEL`, `google-api:MODEL`) — the same
  no-tools contract as the local-model path with the endpoint swapped: the orchestrator
  does every git/PR step itself and requests only code generation via
  `keel`'s `api_delegate` wrapper (one stdlib HTTP call per attempt against the vendor's
  API, keyed by `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` from the
  environment). Same retry-×2-then-fall-back rule; **never retry HTTP 429 /
  rate-limit** — fail soft and fall back immediately. `google-api` is the one vendor
  whose URL carries the **model in its path**, so an unsafe model id there is refused
  before any request (`bad-model`) rather than escaped — treat a `delegate-model:`
  label as untrusted for that vendor. It also answers an invalid key with HTTP **400**,
  not 401, which keel maps to `auth` so a mistyped key reads as a key problem. Reading the token is `secret_access`, so the run's approved
  scope must include `secrets` — without it, resolve to `HOST_AGENT` before any key is
  read. **Hosted-API implementers are refused on tier-3**, same rule and fallback as
  local models. Attribution: `agent:<vendor>` (e.g. `agent:anthropic-api`) + versionless
  `model:<base>`, system `anthropic-api:MODEL`.

Every implementer (delegated or not) receives the same brief plus:
- The approved `operator_consent.delegated_agent_scope`. If the implementer attempts work
  outside `approved_mutation_scopes`, the orchestrator blocks or escalates instead of
  silently continuing. Secret access requires the explicit `secrets` scope for this run.
- Worktree isolation + branch-off-`base_branch` + a detailed PR body + open as **draft**.
  When `keel ship --json` exposes `result.artifact_bodies.pr_body`, use that rendered
  body as the PR-body shape and fill in the concrete implementation/testing details before
  opening or updating the PR. The PR body MUST NOT be only a closing reference. It must
  include at least: `Context / Root Cause`, `Changes Made`, `Testing`, `Docs Impact`, and
  a final `Closes #<N>` reference. If any section is not applicable, write
  `N/A — <reason>` inside that section instead of omitting it.
- A pre-push scope self-check: `git diff base_branch...HEAD --name-only`, revert anything
  outside the issue's scope.
- The vendor's `Co-Authored-By:` trailer on every commit.
- **The JSON return contract** as the final fenced block of the response:
  ```json
  { "pr_number": <int>, "branch": "<string>", "files_changed": ["<string>"],
    "test_results": "<string>", "codename": "<string>", "worktree_path": "<abs path>" }
  ```
  The orchestrator parses this for s5/s10. `worktree_path` must be the absolute path passed
  to `git worktree add`. Free-text above the block is fine; the JSON envelope is the contract.

**Quota / unavailability fail-over.** On a missing CLI, nonzero exit with no parseable
JSON, or a quota error (HTTP 429 / RESOURCE_EXHAUSTED — do **not** retry; quota resets
slowly), fall back to `HOST_AGENT` and log the reason. A local-model harness that already
created a worktree must remove it before the host path recreates one at the same path
(same obligation under `--dry-run` if it created one).

**Attribution (mandatory on every path, even a plain run).** Record and persist a vendor
label and the full `IMPLEMENTER_SYSTEM` string (vendor + model when known, e.g.
`codex:<model>`, `ollama:<model>`). When a specific model is known, also add a versionless
`model:<base>` label (strip an Ollama `:tag`; drop a trailing numeric run on non-hyphenated
families, e.g. `<word>2.5`→`<word>`; keep `<word>-<major>` on hyphenated ones, dropping a
`.minor`). Attribution always reflects the **effective** implementer that actually ran —
never the requested-but-fell-back one — and is written at label-flip time (skipped only
under `--dry-run`, logged instead).

After the implementer returns, the **orchestrator** runs a **branch-scope validation gate**.
Persist the implementer's declared `files_changed` into the run-ledger record at append time
(`keel ship --append-ledger --declared-file <path>` per declared file), then enforce the
comparison with **`keel scope-verify .keel/project.yaml --root . --pr <PR>`**: it reads the
declared files from the ship-run ledger record, diffs them against the live PR changed files,
and flags anything outside the declared scope (and not a `docs_gate_paths` exempt path) as
scope creep. On a failing verdict, hand it back for **one** correction pass; if it persists,
mark blocked and quote the offending files `scope-verify` named. (One pass is intentionally
stricter than the CI budget — the implementer's own pre-push self-check should have caught
drift; a second failure is systemic.) Docs-only extras under `docs_gate_paths` are exempt,
and when no declared scope was recorded `scope-verify` is an advisory pass. An operator may
accept creep for a single run with `--deferral scope-waived`. This gate is the primary
defence against branch contamination — it catches scope creep before review spends budget.

### s5 classify
`keel ship .keel/project.yaml --root .` prints, deterministically: the **risk tier** (from
`tier3_globs` against the diff) → reviewer count, the window state, the gate results, and
the merge decision. Tiers: **tier-3** (any `tier3_globs` match → most reviewers + jury
auto-on), **tier-1** (all paths in `docs_gate_paths` → fewest reviewers), **tier-2**
(everything else). `--reviewers N` overrides the count but does **not** suppress the
tier-3 jury auto-trigger logic below; log the detected tier and reason
(`tier-<N> → reviewers=<N> (reason: <matched glob | docs-only>)`). When `--reviewers` is
passed the tier is not computed, so the tier-3 jury auto-trigger does not apply.

**Jury enablement** (always evaluated, even when `--reviewers` was passed; precedence
`--no-jury` > `--jury` > tier-3 auto > off): tier-3 ⇒ auto-on. Mode is **gating** by
default (`--jury-advisory` ⇒ advisory-only). The jury never changes the reviewer count.
Log the decision (`jury: enabled (reason; mode) / disabled`).

### Step boundary verification
At every successful backbone transition, persist the canonical JSON handoff produced from
`keel.stepverifier.build_handoff`, write/update the checkpoint for the next safe boundary,
and run `keel step-verify --step sN --handoff-file <file> --evidence-report <file>` before
advancing. A failed step verification is a BLOCKER: do not continue, merge, or mark the
step complete from chat prose alone.

### s6 ci
Push the branch, open the **draft** PR, and wait for the project's `ci_workflows` to go
green. The required `keel evidence (required)` check is provenance-armed for ship-driven PRs
(ship branch, posted review marker, ship-run ledger record, or legacy gate label); only an
operator-applied `keel:evidence-waived` label may disarm it. Evaluate the rollup with
**failure-before-pending** precedence — a
mixed state with any failure is a failure, never poll past it. Three branches:
- **all green** (`success`/`skipped`/`neutral`/`stale`) ⇒ proceed.
- **empty check set** ⇒ allow only if every changed path is in `docs_gate_paths`, else
  mark blocked ("CI did not run on a non-docs PR"). Both `keel merge` **and** the `keel
  ship` assessment enforce this themselves — merge reports the rollup as `no-checks`
  (never `pass`), and ship reports `ci_ran: false` and blocks with *"no CI ran — nothing
  verified this commit"* — applying the same docs-only carve-out, so the two never
  disagree. This branch is a description of core's behaviour, not a rule you implement.
  A **declared** `ci_workflows` entry that produced no check for this head also blocks,
  named in the reason: green tells you what ran, not that the things you require ran.
- **any failure/pending** ⇒ watch with a hard timeout (portable `timeout`/`gtimeout`
  wrapper; require `coreutils` on hosts lacking GNU `timeout`), then on a real failure run
  the fix-and-reply loop (read the failed log, fix, self-review, push) and re-enter s6.

**Per-issue CI retry budget: 3 fix-and-push rounds**, then mark blocked.
**Session-wide cooldown: 3 consecutive issues hitting a budget without a successful merge ⇒
abort the session** (counter resets after any merge).

### s7 review *(agent)* + slot `reviewers`
Run **N reviewers** (N from the s5 tier, or `--reviewers`), the host or `--review-delegate`
vendor. A non-host reviewer vendor runs **read-only / findings-only** (the vendor's
read-only mode, local endpoint, or — for `anthropic-api:`/`openai-api:`/`google-api:` — one hosted-API
call via the `api_delegate` wrapper: diff + rubric in, structured verdict out; same
`secrets`-scope and no-retry-on-429 rules as s4, no tier restriction since review output
is advisory, not a mutation), the orchestrator still posts — the **orchestrator owns
all writes**; reviewers never call a GitHub write API.
A **`knobs.delegate_profiles` reviewer is the one case keel cannot make read-only for
you.** Every other non-host vendor has a mechanism behind that promise — a vendor
read-only flag, a local endpoint, a single hosted-API call — but a profile is an
arbitrary binary, and the same `command` serves both roles. Its `args` typically carry
the *implementer's* write-enabling flags (`cursor-agent`'s `--force` approves edits
non-interactively), so reusing them for review hands a reviewer permission to edit the
checkout. Invoke a profile reviewer with **`review_args`** when set, else `args`
(`DelegateProfile.role_args(review=True)`), and set `review_args` to a read-only
invocation for any profile used as a reviewer. keel validates neither — this is
operator-configured, not enforced. Treat a profile reviewer's diff as advisory and
**re-check the worktree is clean afterwards** rather than assuming it was untouched.
Spawn all reviewers in a **single
Agent message** so they run concurrently; each gets a fresh codename, the PR head SHA, its
focus slice, and a no-cross-reading instruction. Coverage invariant: when the count drops,
focus dimensions **merge, never drop** (a 1-reviewer slot covers all dimensions; suitable
only for narrow tier-1 PRs). Run any `reviewers` Lego extensions. Capture per-reviewer
**effective** vendor+model for attribution (lock-step parallel arrays so the s11 closure
can zip them by index). On a missing/erroring delegate vendor, fall back to the host
reviewer and log it (record the effective vendor that ran).

**Reviewer stance — brief every reviewer to *refute*, not to approve.** The focus slices
above say *where* to look; without a stance a reviewer reads the change sympathetically and
confirms it looks right, which is how a defect ships past a green CI. Each reviewer's brief
must carry all four of these together — the first without the rest is worse than neither:

- **Refute it.** Default to the position that the change is wrong and concede only when the
  code forces you to. The author already made the case for it; nobody has made the case
  against it.
- **A finding you cannot demonstrate is not a finding.** Prefer a reproduction — a failing
  input, a trace through the code to the line — over an assertion. This is the counterweight
  to the stance, not a footnote to it.
- **Finish the trace.** Follow a defect from where you noticed it to where it actually lands.
  A wrong value on a screen and a wrong value written to a record are the same bug with very
  different severities, and only the second one is the reason to hold the merge.
- **"I checked X, Y and Z and found nothing" is a complete review.** Say what you checked.
  A reviewer with no way to report a clean result either goes quiet or manufactures
  something, and a manufactured finding is a failure of the review, not a strict one.

Gates check whether code *runs*, not whether it does what the PR *says* — a claim the
artefact does not deliver is the class of defect this stance exists to catch, and the class
CI structurally cannot.

**Give every reviewer the project's own failure family.** The stance above is
project-neutral by construction — this file may not name a path glob, let alone the shapes
a particular codebase keeps producing. The project can: `policy_pack.review.additions`
reaches the contract as `review_merge_contract.reviewers.project_additions`. **Pass those
entries into each reviewer's brief verbatim**, under a heading that marks them as recurring
shapes to look for, not a checklist to tick. Measured on a controlled pair (same PR, same
commit, same model, one reviewer with worked examples and one without): both found the
defect, and only the briefed one **followed it from the screen into the persistence layer**,
where a stale value turned out to be permanent data loss. The examples do not make a
reviewer see more — they make it *finish the trace*, which is the difference between a
follow-up ticket and a rollback. Likewise
`review_merge_contract.reviewers.required_sections` (from `policy_pack.review.
required_sections`) are sections a review body must contain; a review missing one is
incomplete, not merely terse. Both are absent for most projects — pass nothing then, and
never invent entries to fill the slot.

**Post findings per `--review-comments` (inline default):** review findings are public PR
evidence. The orchestrator MUST post each reviewer's final verdict to the GitHub PR through
the selected transport as a distinct PR review or PR comment. This applies on every path:
operator-driven, delegated, every tier, and the TIER-1 single-reviewer path. A single
reviewer still emits a posted verdict comment/review for the current PR head.
Local/chat-only review output does not satisfy the step, a rich PR body is not a substitute
for this s7 evidence, and the automated `keel ship` CI assessment block is not a substitute
for the operator-posted review verdict.
**Never carry a review forward across runs or sessions.** If you believe the change was
"already reviewed," that is not evidence: you must still confirm the verdict marker is posted
on **this** PR for the **current** head, and re-run s7 if it is not. The s11 closure
attribution must name only verdicts actually posted on this PR — never an unverifiable
"reviewed in a prior session" claim. If you cannot point to the posted verdict, the review
did not happen for s10's purposes.
When available, use `result.artifact_bodies.review_verdict_template` as the canonical
comment shape: keep `keel.review-verdict.v1`, `reviewer: <stable-id>`, and `head: <sha>`
intact, then fill in the reviewer-specific verdict, scope, findings, and testing notes.
Carry the **effective** reviewer `vendor` (and `model` when known) on each verdict — the
same attribution computed at s7 — so a project that enables `evidence_require_distinct_vendors`
can verify the verdicts came from distinct vendors. This is jury-agnostic: a plain
host-agent reviewer carrying distinct vendor provenance satisfies the check just as a
cross-vendor panel would; keel takes no dependency on any review vendor.
Post each review verdict through `keel post-comment` with a reviewer-scoped run id
(`--run-id "$RUN_ID:<reviewer-id>"`) so same-run idempotency updates that reviewer only and
does not collapse multiple reviewer verdicts into one comment.

**Sanctioned bundle path:** once the reviewers have returned their content, the
orchestrator SHOULD hand the whole set to `keel review` rather than rendering and posting
each verdict by hand. `keel review .keel/project.yaml --root . --pr <PR> --reviews
<reviews.json> --run-id "$RUN_ID" --live` resolves the tier-required reviewer count (failing
closed if the bundle is under-count), renders each verdict head-pinned to the current PR
head SHA, and posts them through the same `post-comment` path with stable
`<run-id>:rv-<reviewer>` sub-keys. Include `vendor` (and optional `model`) on each review
item in `<reviews.json>` so the rendered verdicts carry vendor provenance. Add
`--closure <ship-run.json> --issue <N>` to fold the
s11 closure into the same call, and `--verify` to re-run `evidence-verify` immediately after
posting. This is the canonical way to collapse `render_review_verdict` + N× `post-comment`
+ `evidence-verify` into one deterministic, idempotent step; it never spawns reviewers — the
host still produces the review content above.

- `inline` → fetch the diff once; anchor each `critical`/`major` finding as an **inline
  review comment** on its `file:line` (resolve `RIGHT`/`LEFT` side; `line` is the new-file
  number on `RIGHT`, old-file on `LEFT`; non-anchorable or whole-PR findings go to the
  summary), posting **one submitted review per reviewer** (create the review carrying its
  inline comments in one call — do not post standalone unattached comments). On any
  inline-API error for a reviewer, **fall soft to a summary comment for that one reviewer**
  and continue (scoped to that reviewer, never a whole-round fallback).
- `summary` → one consolidated review comment per reviewer.

Severity → action: **critical/major = block**, minor = suggestion (gated — apply before
merge unless explicitly user-deferred), nit = advisory. The s9 loop-exit parser reads the
reviewer's **returned findings**, not the comment shape, so it is mode-independent.

### s8 test (gates + jury)
`keel run-gates .keel/project.yaml --root . --run-id "$RUN_ID" --command ship --phase s8 --issue <N>`
runs the project gates (`build_gate_cmd`,
`lint_cmd`, plus the `tester` Lego — the manual-test list, which may loop back to the
implementer defensively without spending review budget unless it surfaces a blocking fix).
An **`agentic` gate reports `NOT-RUN` here** — this command does not dispatch those, you
do. `NOT-RUN` is not a pass: a gate declared `on_fail: block` that shows `NOT-RUN` blocks
the merge decision and refuses to certify the run, so `keel merge` will reject the head.
Dispatch the gate yourself (at s9 for `pre-merge` Lego), then **re-run the command with
`--gate-result <id>=pass|fail`** to record what your dispatch found. A recorded result may
only be given for a gate keel did not execute — the command refuses to override its own
measurement of a gate it ran.

When a gating or advisory jury is enabled and `result.artifact_bodies.jury_verdict_template`
is available, use that canonical shape for the posted jury verdict and preserve
`keel.jury-verdict.v1` plus `head: <sha>`.
The **`jury` gate** runs the ai-jury CLI read-only on the PR diff when present (and a no-op
fail-soft otherwise) using the committed panel; it never passes `--strict`. In **gating**
mode the depth is the full verified run; only **verified consensus**
`critical`/`major`/`minor` findings fold into s9 (`critical`/`major` ⇒ block, `minor` ⇒
gated suggestion, `nit` ⇒ advisory; a jury-driven fix consumes one round). **Core resolves
the effective mode from the panel that actually ran** — a run with fewer than
`ship.MINIMUM_JURY_VENDORS` (2) distinct *participating* vendors is downgraded to advisory,
and a run where no agent returned output is simply zero vendors, so at **this layer** — the
pre-merge evidence check — a jury that did not complete cleanly does not gate. Do not
re-derive or override that downgrade: report the count and let core decide. Note this is
distinct from the **s8 gate layer**: there a jury run that produced no verdict (killed by
`knobs.jury_timeout_s`, or output with no parseable report) is a blocking `major` in gating
mode. The gate refuses to call a non-review a pass; the evidence check separately declines
to *require* a verdict from a panel that never ran. Pass the distinct participating vendors — those that actually
returned output, not those merely configured — via `keel evidence-verify --jury-vendors <N>`,
and post a verdict whose declared mode matches the resolved `jury.mode`, which the evidence
gate reads to decide whether a `jury-verdict` is required at all.
Honour `--review-comments` (pass the jury's native inline
flag through in inline mode, never under `--dry-run`). The orchestrator MUST POST the
single jury summary/verdict comment to the GitHub PR through `keel post-comment`:
`keel post-comment .keel/project.yaml --root . --target pr:<PR> --artifact jury-verdict
--body-file <file> --run-id "$RUN_ID"`. Raw `gh pr comment`, `gh issue comment`, or
hand-rolled comment API calls are spec violations for jury/review/closure/issue-update
artifacts because they bypass marker validation, transport selection, and same-run
idempotency. Never interpolate report text into a shell argument. Re-runs use the jury's
incremental/cache flags to stay cheap.

**Save the jury artifact for visualizers (#576):** in the same jury invocation, also write
the machine-readable report to keel's state dir — pass
`--format json -o .keel/state/jury/$RUN_ID.json` (create `.keel/state/jury/` first; the
filename is the **raw** run id, exact match — run ids are `[A-Za-z0-9._-]+` by
construction). keel-visual reads this file to show the jury verdict alongside the jury
mode; it is state, never committed (the `.keel/.gitignore` scaffold already ignores
`state/`). Fail-soft: if the write fails, log and continue — the artifact is
display-only and never gates.

### s9 fixloop
While there are blocking findings and the budget (**≤3 review-fix rounds**) is not spent:
aggregate findings → hand to the implementer → fix → push → re-run s6/s7/s8. A **blocker**
triggers a full re-review; **suggestion-only** fixes trigger a **narrowed re-review** of
just the originating focus(es) (carry the original reviewer codename forward, fresh codename
per narrowed reviewer, "verify only the applied fix in commit `<sha>`; do not re-review what
you already approved" prompt; spawn multiple narrowed focuses in one Agent message). A
suggestion is gated like a blocker — apply it, or obtain an explicit, tracked user deferral;
never silently relabel one "advisory/flake". Recorded suggestion deferrals must be public
and checkable: post a `keel.deferral.v1` PR/issue comment marker that names the finding,
authorising operator, and reason before treating the suggestion as deferred. A narrowed
reviewer that surfaces a NEW blocker
escalates back to the full loop. Each round posts its own review (per `--review-comments`)
and increments the counter; exceeding the budget marks the issue blocked with the
outstanding findings quoted. Defensive loop-backs (tester, merge-conflict prep) don't spend
budget unless they require a fix. Before exiting, surface any deferred suggestion/nit with
its authorising decision/issue — a silent skip is a process violation.

Append each fix/review/test round to the run-events file with `keel runcontrols`. A hard
halt from `keel runcontrols` is fail-closed and must stop the ship run until an operator
chooses an explicit `--max-rounds` override.

### s10 merge
The literal merge is **core-owned**: route it through `keel merge`. Raw `gh pr merge`
calls and hand-rolled lock shells are **spec violations** for ship-style flows — the
lock, window re-check, CI rollup read, evidence verification, and the SHA-stamped
gates-pass check must run deterministically inside core, not as adapter prose.

- **Evidence gate — do this first, on every path (audit GAP-REV):** before *any*
  merge — including a raw `gh`/REST merge you might be tempted to use — run
  `keel evidence-verify .keel/project.yaml --root . --pr <PR>` and confirm it
  **exits 0**. It fails when the s7 review verdict (a posted PR comment/review
  carrying `keel.review-verdict.v1` for the **current head**) is not on the PR. A
  prior session's summary, a chat-only review, the rich PR body, and the `keel
  ship` assessment block do **not** satisfy it. If it fails, **STOP — do not
  merge**: go back to s7 and post the review verdict for the current head, then
  re-verify. The only disarm is an operator-applied `keel:evidence-waived` label;
  **never self-apply it.** Cite the `evidence-verify` pass (PR + head SHA) in the
  s11 summary as the merge's authorization.
- **Pre-merge prep:** re-assert mergeability; if behind/dirty, integrate `base_branch`
  (merge, not rebase), re-green CI, run a single focused merge-conflict review (max 2
  integration iterations, then blocked + morning queue). Run any `pre-merge` Lego — and for
each one that is `kind: agentic`, record what it found with
`keel ship … --append-ledger --gate-result <id>=pass|fail`, or its `NOT-RUN` outcome will
refuse to certify the run at s10. Then
  pre-clean the worktree so `--delete-branch` won't be held by a local ref — remove it
  with `keel worktree-remove <worktree_path> --root .`, which validates the path is nested
  under the repo root and registered in `git worktree list` before removing (never call
  `git worktree remove --force` directly on an implementer-supplied path).
- **Core-owned merge:** run
  `keel merge .keel/project.yaml --root . --pr <PR> --run-id "$RUN_ID" --approve-scope <scopes> --operator <operator>`.
  (The `--run-id` lets the merge advance the activity board to the merge step.)
  The command acquires the merge resource claim (atomic `mkdir`, single-host), re-checks
  the **merge window inside the claim**, reads the live PR check rollup with
  failure-before-pending precedence, runs `evidence-verify` against the current PR
  artifacts, requires a SHA-stamped gates-pass (a `ship_run` ledger record whose gates
  passed against the PR's **current** head SHA, so a stale green run from an older head
  cannot authorize the merge), and only then performs the squash-merge. Any failed stage
  exits non-zero **without merging** — on a closed window, append to the morning queue, post
  the deferral comment via `keel post-comment`, leave the PR ready, and continue with the
  next issue; on a missing gates-pass for the current head, re-run `keel run-gates` (or
  ship with `--append-ledger`) against the head and retry — if the refusal names a
  `NOT-RUN` blocking gate, the re-run needs `--gate-result <id>=pass|fail` for it, since
  re-running alone reproduces the same not-run record; on a denied claim, treat it as
  lock contention (mark the issue blocked, comment, continue). For a blocker issue, pass
  `--hotfix` — the audited bypass of both the window and the gates-SHA requirement; it still
  requires the approved consent scopes and is recorded in the ledger. **The `--hotfix`
  bypass is refused without a justification** (audit GAP-11): pass
  `--blocker-rule <id>` where `<id>` is the rule `keel guard` returned for this issue
  (s3) — `keel merge` re-validates it against the issue's title/labels and refuses an
  unknown or non-matching rule — or, for a genuine emergency no rule covers,
  `--operator-override` with a named `--operator`. The chosen justification
  (`hotfix_justification: {kind: matched-rule|operator-override, …}`) is recorded in the
  ledger.
- **Outcome:** treat the **PR state (`MERGED`)** as authoritative. A non-zero exit after a
  successful server-side squash is a local-cleanup failure — proceed to capture/close; a
  real non-MERGED state aborts the closure block and blocks the issue.

`merge_window_mode`: `pause` halts here outside the window; `freeze` defers to the morning
queue. The merge claim and "the only merge path is `keel merge` at s10" are non-negotiable
invariants.

**`keel merge` now runs this check itself, immediately after the merge lands** — it exits
**3** and prints the overtaking pull request when it finds drift, distinct from **1** for a
merge that failed. Run `keel verify-merge <config> --root . --pr <PR>` by hand only when
the merge happened outside `keel merge`, or to re-ask later.

Until #934 this paragraph said to run it and nothing did: the instruction lived in this
prompt and no code path invoked it, which is how a stale-base squash reverted #811 on main
and stayed there for six days.

A merge succeeding is not the same as a merge applying what was reviewed. A
`gh api …/update-branch` merge commit followed by a squash-merge silently reverted
unrelated already-merged work **twice in one day** while shipping 1.8.1/1.8.2, and CI never
saw it: the reverted state was internally consistent — old code, no test for the removed
behaviour — so every gate stayed green. Both were found only by reading the day's whole diff
against a pre-session baseline.

The check asks whether this merge wrote to files that another pull request changed **after
this one branched**, which is the only way that revert can happen. Three exit codes:

| exit | status | what to do |
| --- | --- | --- |
| `0` | `clean` or `out-of-scope`, every input read | proceed |
| `1` | `drift` | **stop and read the diff** |
| `2` | `unknown`, or a finding kept with `incomplete: true` | the check could not answer every question — retry, and do not read it as a pass |

A report can print `out-of-scope` **and** exit 2: the finding is real and is kept
rather than erased, but an input the *drift* question needed could not be read.
Treat the exit code as the verdict, not the status word.

`drift` names each file with the pull request it collided with. Treat it as *stop and read
the diff*, not as an automatic failure — two PRs editing one file in sequence is ordinary —
and **never proceed to s11/s12 on a drift finding without a human confirming the merge is
sound**. `2` means GitHub could not be read (or the merge commit is not visible yet, which
is a real race immediately after merging): retry it, and if it stays `2`, say so in the
closure comment rather than recording a clean verdict the check never reached. Silence is
the failure mode being fixed here, so surface the report in the closure comment rather than
only in the run log.

### s11 capture
Record the run for `/keel:wrap`: the **effective** implementer + reviewer vendors/models,
tier, rounds, window decision, and outcome. Post the **closure comment** to **both** the
issue and the PR as distinct comments through `keel post-comment` with
`--artifact closure-comment` and the same `--run-id`. The PR closure comment MUST be a PR
conversation comment, not appended to or folded into the PR body, and not represented by
the automated `keel ship` CI assessment block. Render it deterministically from the
`ship_run` ledger record via the `result.closure_comment` field of `keel ship --json` (the
`contract.closure_comment` contract describes its stable marker plus sections: heading,
Implementer `vendor (model)`, Reviewers — noting AI Jury when present, Tester, PR number,
changed files, capture outcome, run id). Do **not** hand-write closure prose: post the
rendered markdown verbatim so the issue and PR comments mirror the ledger byte-for-byte.
`evidence-verify` enforces this **closure fidelity**: when a `ship_run` ledger record exists
for the PR, the posted closure body must match that record's canonical render (after
whitespace normalization) on both the PR and the issue, so a stale or edited marker-bearing
body fails the closure check.
Use `keel post-comment` for issue-update, review-verdict, jury-verdict, and
closure-comment artifacts; a malformed body missing its marker must stop the step before
any public comment is posted.
Run any post-merge
`capture` Lego (e.g. durable-learning capture: classify the merged PR's signal, optionally
file a follow-up issue or hand off to a project-owned destination) fail-soft, emit its
core marker, and do a post-merge worktree safety-net cleanup. **Marker discipline:** every
merged PR that reaches capture must write exactly one structured ledger record with this
stable marker: `compound-learning: pr=<N> status=<applied|deferred|skipped:reason>`.
Allowed skip reasons are closed: `dry-run`, `deferred`, `merge-failed`,
`recursion-guard`, `capability-unavailable`, and `no-policy`. Capture-on-capture recursion
must skip with `skipped:recursion-guard`. A session-end verifier runs
`keel capture-verify .keel/project.yaml --root . --merged-pr <PR> ...` and blocks the
session if any merged PR is missing a valid marker. The closure comment's capture field is
mandatory and never empty, but it is a human audit mirror, not the parser source.

Also append the structured `ship_run` record to `contract.run_ledger.path` via
`keel ship --live --append-ledger` or the equivalent core ledger writer. The ledger append
is the machine-readable source for `/keel:morning`, `/keel:wrap`, overnight summaries, and
capture verification; the closure comments are human/audit mirrors, not the parser source.
Capture artifacts MUST pass through the core redaction policy first: default secret rules plus
any project-owned `policy_pack.capture_redaction.deny_patterns`. If the configured redaction
policy is invalid, stop the durable write and ask for operator help rather than persisting
unsanitized output. The audit may include rule ids and counts, never original secret values.

### s12 close
Close the issue (idempotent if the squash auto-closed it via `Closes #<N>`), link the PR,
flip the status label to done **only here** (post-merge), and drop the lock.

## Compound profile (`--compound`)

`--compound` (or `--profile compound`) selects the **compound-engineering** workflow
profile. It is a first-class profile of `ship`, **not** a second backbone and **not** a
project extension: the same selection, worktree safety, guard, classification, CI, gates,
review/jury/merge-gate contract, merge window, merge lock, closeout, and capture-marker
discipline apply. It differs only where `workflow_profile.step_overrides` says it differs.

Render the compound contract through the same deterministic CLI before mutating work:

```bash
keel plan .keel/project.yaml --root . --command ship --profile compound --live --json
keel ship .keel/project.yaml --root . --compound --live --json
```

The JSON contract's `workflow_profile` then reports:

- `profile: "compound"`
- `inherits: "ship"`
- `first_class_variant: true`
- `step_overrides` for `s4 implement`, `s7 review`, `s9 fixloop`, and `s11 capture`

The compound profile differs only at these four steps:

| step | profile mode | compound behavior |
|---|---|---|
| `s4 implement` | `compound` | Use a compound implement pass that emphasizes PR quality, scope simplification, and value-first change shaping before handoff. |
| `s7 review` | `compound` | Use compound/persona reviewer fan-out when available, while **preserving the reviewer count, posting mode, and gating semantics (including jury) from `review_merge_contract`**. |
| `s9 fixloop` | `compound` | Resolve PR feedback through a structured compound loop, but keep the shared blocker/suggestion policy and review-fix budget. |
| `s11 capture` | `compound` | Run durable-learning capture through the capture slot, with the shared canonical marker requirement. |

Compound helpers may be supplied by the host runtime or by project extensions. If a
compound helper is unavailable for a step, fall back to the standard behavior for that step,
log the degraded step, and continue unless the configured extension marks the degradation as
blocking.

Under `--dry-run`, the compound profile must show the same non-mutating contract as the
standard profile, plus the compound `workflow_profile`; it must not create branches, edit
files, push commits, post comments, request reviews, merge, close issues, or write capture
artifacts.

## `--dry-run`

Run s0–s8 read-only and print the plan + `keel ship` assessment (tier, window, gates,
decision). Do **not** push, open a PR, post comments/labels, or merge — log every would-be
write as `DRY-RUN: …` (every label edit, comment, ready-flip, merge, close, and any review-
API or jury-inline write). The implementer is told not to push or open a PR; reviewers still
run for real (read-only) so findings stay meaningful. `keel merge --dry-run` may still be
run to exercise the claim/window/rollup path without merging. The capture step is a logged
no-op (`dry-run` marker).

## `--wizard` (interactive opt-in only)

A pre-s1 front layer that collects the same options the grammar above produces — it adds no
new pipeline behaviour and cannot produce a config the grammar could not. **Hard
interactivity guard:** never enter the wizard in any non-interactive context (watch mode,
overnight/background/headless runs); there it degrades to a logged no-op and proceeds with
the literal flags as parsed (never a hang, never a rejection). Best-effort tool/model probe
(installed CLIs + local models) builds the offered choices; detection failures just yield
shorter lists. First question is a **Quick-start vs Customize** fast path (Quick-start
resolves every option to its default and only still asks for Issues). Every question shows
its `(default)` option first with a one-line description of what the default does. After
collecting, echo the resolved config in the worked-example shape, then proceed to s1.

## Invariants (always)

The only merge path is `keel merge` at s10 (claim, window, CI rollup, and evidence checks
run in core) · never merge
in the night no-merge window except a blocker / audited `--hotfix` · fail-soft (a missing
CLI/gate/jury/capture-path degrades, never crashes the run; an absent/erroring jury can
never manufacture a block) · the **orchestrator owns all writes** (reviewers are
findings-only, every vendor) · never push directly to `base_branch` · the status-done label
is set in exactly one place (s12, post-merge) · attribute the **effective** vendor+model
everywhere · a local-model implementer is orchestrator-driven, refused on tier-3, and never
bypasses review/tester/merge gates or the lock.

<!-- keel-generated: surface=plugin command=ship keel_version=1.19.0 source_sha256=853a898bb789b827d1a9b9e988008402f21787a4a78a3d96942cf66e7ccc8e7f generated_sha256=853a898bb789b827d1a9b9e988008402f21787a4a78a3d96942cf66e7ccc8e7f -->
