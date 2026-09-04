---
description: Drive a GitHub issue end-to-end through the keel backbone (select → branch → implement → CI → review → test → merge → close → capture), reading every project value from .keel/project.yaml via the keel CLI.
argument-hint: "[issue numbers...] [--compound|--profile <standard|compound>] [--delegate <claude|codex|agy|ollama:MODEL|anthropic-api:MODEL|openai-api:MODEL|google-api:MODEL|PROFILE>] [--review-delegate <...> (repeatable, one per reviewer slot)] [--review-comments <inline|summary>] [--reviewers <1|2|3>] [--effort <low|medium|high>] [--team <profile>] [--jury|--no-jury|--jury-advisory] [--tdd] [--hotfix] [--dry-run] [--wizard]"
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
`lint_cmd`, `team`, `implementer_agents` (deprecated by `team.implement.by_role`),
`delegate_profiles`, `tier3_globs`, `ci_workflows`,
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
`lint_cmd`), `team`, `implementer_agents`, `delegate_profiles`, `tier3_globs`, `ci_workflows`,
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
- `--tdd` — select the **test-first s4 profile** for this run (the per-project spelling is
  `knobs.implement_mode: tdd`). s4 runs in two phases and s8 gains the `tdd-order` gate;
  see **Test-first s4** below. There is no `--no-tdd`: a project that configured the
  contract has said the contract is the policy, and a flag that switched it off would
  make it advisory. Read the resolved profile from `contract.implement_mode` of
  `keel plan`/`keel ship --json` (`mode`, `source`, `phases`, `gate`) — never re-derive it.
- `--delegate <claude|codex|agy|ollama:MODEL|anthropic-api:MODEL|openai-api:MODEL|google-api:MODEL|PROFILE>` — the
  **implementer**. Per-run override of any issue role/delegate label. `ollama:` and the
  `*-api:` values require a non-empty model. The `*-api:` values are the **hosted-API
  delegates** (no agent CLI needed — just `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` in the
  environment; see s4). `PROFILE` is the name of a `knobs.delegate_profiles` entry — a
  **generic CLI vendor** configured in `.keel/project.yaml` (e.g. `--delegate cursor`;
  see s4). Built-in vendor names always win over a profile name. Default: the **host
  agent** (the CLI driving this run).
- `--review-delegate <…>` — a **reviewer** slot (same value set, profile names included).
  **Repeatable and positional**: the first occurrence is reviewer slot A, the second slot
  B, and so on, so a two-vendor panel needs no config change. A value past the last
  staffed slot is reported in `assignment.warnings` and not dispatched. Default: the seat
  `knobs.team.review` names for the tier, else the host agent.
- `--role <label>` — the issue role label that selects
  `knobs.team.implement.by_role`. Default: the role label read off the issue in s1.
- `--effort <low|medium|high>` — reasoning effort for the **implementer** seat, in keel's
  vendor-neutral vocabulary. Wins over an `effort` the seat itself names and over one a
  `knobs.team.by_difficulty` bench supplies. A provider with no spelling for reasoning
  effort reports `effort_applied: false` rather than silently running at its default.
  Default: the seat's own effort, else the bench's, else none.
- `--team <profile>` — staff this run from the named `knobs.team.profiles` bench, which
  can supply the implementer, the reviewer seats, the lead and an effort in one name.
  Outranks a `knobs.team.by_difficulty` band; `--delegate` / `--review-delegate` still
  outrank it. A name matching no configured profile is reported in `assignment.warnings`
  and the run falls back to the configured policy. This is how a batch runner
  (`/keel:work-block`, `/keel:overnight`, a `/keel:swarm` lead) hands its bench down: the
  child re-resolves the **same** seats instead of deriving a different team from config
  alone. Default: no profile.
- `--review-comments <inline|summary>` — how reviewer findings post (s7). Default `inline`.

**Carry `--effort` and `--team` to every command that resolves this contract.** Six do —
`ship`, `plan`, `review`, `step-verify`, `evidence-verify` and `merge` — and they all read
the *same* resolver. A bench that reaches only the dispatching half is the #1014 defect in
a new spelling: a `--team` naming a one-seat bench dispatches one reviewer while
`evidence-verify` and `merge` still demand the tier's three verdicts, and no run of that
project can ever satisfy the gate. So whenever this run was given `--effort` or `--team`,
pass the **same values** to `keel review` (s7), `keel step-verify`, `keel evidence-verify`
(s10) and `keel merge` (s10/s12). `contract.assignment` is the source of truth: it names
`team_profile`, `effort`, `lead`, `implementer` and `reviewers[]`, and every one of those
six commands must resolve to the block this run published.
- `--reviewers <1|2|3>` — override the resolved reviewer count. Default: the seats
  `knobs.team.review` names for the tier, else the tier-derived count. On a tier whose
  review policy is `jury` it is reported in `assignment.warnings` and not applied: the
  panel is the review, so there are no host slots to size.
- `--jury` / `--no-jury` / `--jury-advisory` — control the cross-vendor jury gate (s8).
  Precedence: a `knobs.team` **jury panel tier** > `--no-jury` > `--jury` > tier-3 auto >
  off. `--jury-advisory` = report-only. **None of them changes the reviewer bench**, which
  is a pure function of config + tier + role + `--reviewers` / `--review-delegate` — the
  commands that read this contract do not all receive the jury flags, so a bench that
  moved with one would make `keel evidence-verify` demand verdicts `keel ship` told you
  never to produce. On a tier whose review policy **is** the panel, `--no-jury` and
  `--jury-advisory` are recorded in `assignment.warnings` and not applied: the panel is
  the only review that tier has, so its verdict stays required.
- `--hotfix` — audited merge-window bypass (s10). Use sparingly.
- `--dry-run` — read-only rehearsal (see `--dry-run` section).
- `--wizard` — interactive opt-in only; runs `keel ship --wizard`, the guided pre-s1
  option picker (see `--wizard` section). In any non-interactive context it degrades to a
  logged no-op. `--wizard-answer <key>=<value>` (repeatable) replays a recorded run.

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

## The team — one resolved assignment, not four independent guesses

`knobs.team` is the project's answer to *who runs this ship*: which provider implements
(per issue role), which one gives the mandatory gate review, which ones review (per risk
tier, or `jury` when the cross-vendor panel **is** the review), and how the jury gates.
**Do not re-derive any of it.** `keel plan … --command ship --json` and `keel ship --json`
both render it as `assignment`, resolved against the same tier the review contract used:

```json
{ "assignment": {
    "configured": true, "role": "core", "tier": 2,
    "implementer": { "provider": "agy", "model": "gemini-3.8-flash-high", "effort": "high",
                     "kind": "provider", "name": "agy",
                     "source": "team.implement.by_role.core" },
    "gate": { "provider": "codex", "distinct_from": "implementer", "distinct_ok": true,
              "source": "team.gate" },
    "review_panel": "reviewers", "reviewer_count": 2,
    "reviewers": [ { "slot": "A", "provider": "claude", "source": "team.review.by_tier.2" },
                   { "slot": "C", "provider": "codex", "source": "team.review.by_tier.2" } ],
    "jury": { "mode": "gating", "min_vendors": 2, "panel_is_review": false },
    "fix": { "provider": "agy", "alias": "implementer", "source": "team.fix" },
    "warnings": [] } }
```

- `kind` says how to dispatch the seat: `provider` goes to `keel delegate run --provider
  <provider>`; `subagent` is a **host (Claude-class) subagent** named by `name` and never
  reaches `keel delegate run`; `alias` only ever appears resolved (`fix` carries the
  implementer's seat plus `alias: "implementer"`).
- `source` is the config path the seat came from, including
  `knobs.implementer_agents.<role> (deprecated)` and `flag:--delegate` /
  `flag:--review-delegate`. Cite it when you say who you dispatched.
- `warnings` is not decoration: an entry there says a flag or a seat you supplied was not
  dispatched, or that `gate.distinct_from: implementer` could not be honoured.
- `review_panel: "jury"` means the cross-vendor panel **is** the review for that tier:
  `review_merge_contract.reviewers.slots` is empty and `reviewers.source` is `jury`. Do
  **not** invent host reviewers to fill the gap, and do not run both — s7 dispatches the
  panel once and maps its ballots onto the review verdicts (see s7). `reviewers.count` is
  the number of ballots that must be posted: the size a posted jury verdict declared, or
  the jury's `min_vendors` floor before the panel has run.
- **The bench never moves, and neither does the verdict requirement.**
  `reviewers.source` on such a tier is always `jury`: no jury flag and no
  participating-vendor count changes *who* reviews, and on a panel tier none of them makes
  the verdict advisory either — the panel is the whole review, so a short panel may not
  excuse itself from the consensus record that says it was short. You receive the same
  contract whichever surface you ask, which is the point: every surface accepts the jury
  flags (`--jury` / `--no-jury` / `--jury-advisory`, `keel review` included since #1043),
  but nothing makes a run pass them uniformly, and only `evidence-verify`/`keel merge` can
  see a vendor count. The thin vendor span is reported as `review-vendor-distinctness`
  rather than swapping in a bench nobody dispatched.
- **Pass the same jury flags to `keel review` that you passed to `keel ship`.** They do not
  move the bench, so the verdicts you post are unchanged either way; they do decide whether
  the contract `keel review --verify` re-checks requires a `jury-verdict`. A run that ships
  with `--no-jury` and then posts with a bare `keel review --verify` is asking two halves of
  one run for two different gates.

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
Read the implementer from `assignment.implementer` — core resolved it from
`knobs.team.implement` (or the deprecated `implementer_agents`) by the issue's role label,
**overridden by `--delegate`**, defaulting to `HOST_AGENT`. Precedence: `--delegate` flag >
`team.implement.by_role` > `team.implement.default` > `implementer_agents` > issue
`delegate:*` label > `HOST_AGENT`. Dispatch on `assignment.implementer.kind`:

- **Host / Claude-class subagent** (`kind: "subagent"`) — run the standard implement brief
  under the subagent named by `assignment.implementer.name`.
- **Any non-host implementer — one command.** `keel delegate run` is the executor for
  every transport; **never** hand-build a delegate invocation. It resolves the provider
  (built-in vendor, `knobs.delegate_profiles` entry, or a machine-level
  `~/.keel/providers.yaml` entry), picks the vendor's flags for the role, delivers the
  prompt off the process list, applies `--effort` in the vendor's own spelling, and
  returns one JSON document:

  ```bash
  keel delegate run --provider "$DELEGATE" --role implement \
    --prompt-file "$BRIEF" --cwd "$WORKTREE" --timeout 3600 --project .keel/project.yaml
  ```

  ```json
  { "ok": true, "provider": "…", "vendor": "…", "model": "…", "role": "implement",
    "transport": "cli|profile|api|ollama", "text": "…", "exit_code": 0,
    "duration_s": 42.5, "timed_out": false, "error_code": null, "error": null,
    "attribution": { "agent_label": "…", "model_label": "…", "system": "…" },
    "read_only": false, "read_only_backed": false,
    "effort_applied": true, "warnings": [] }
  ```

  Parse that document; do not re-derive any of it. `attribution` is computed by core, so
  the labels you write and the ledger's `actors.implementer` can no longer drift from what
  actually ran. `warnings` is not decoration — an entry there says a flag you asked for
  did not take effect. On `ok: false`, branch on `error_code`
  (`missing-binary` · `nonzero-exit` · `timeout` · `rate-limit` · `no-key` · `auth` ·
  `http` · `network` · `bad-response` · `unknown-provider` · `bad-model` · `no-model` ·
  `no-prompt` · `empty-output` · `lost`) — never on the message text.
- **Long runs detach.** A delegated implementation outlives your turn. Start it with
  `--detach`, which prints a `run_id` and returns immediately, then block on it:

  ```bash
  keel delegate run --provider "$DELEGATE" --role implement --prompt-file "$BRIEF" \
    --cwd "$WORKTREE" --timeout 3600 --detach --run-id "$RUN_ID" --root .
  keel delegate wait "$RUN_ID" --root . --timeout 3600
  ```

  `keel delegate wait` prints the same JSON contract; `keel delegate status` lists live
  runs. **Do not write a sleep-and-poll loop.** The state file under
  `.keel/state/delegate/` is authoritative, so the result survives your turn ending, the
  session ending, and the machine rebooting — which a loop in your own context does not.
  **Always pass `--timeout` to both `run` and `wait`.** The one on `run` is stamped into
  the run record as its deadline, which is what lets a child that was `SIGKILL`ed or
  OOM-killed be reported as `lost` instead of sitting at `running` forever; the one on
  `wait` bounds your own call. `wait` on an unknown run id fails closed with
  `unknown-run`, and a run whose process vanished returns `error_code: lost` with the
  child's captured output named in the message.
- **What still belongs to you, not to the command.** `keel delegate run` is a transport:
  it never retries, never falls back, and never consults the risk tier. Those are the
  orchestrator's rules and they are unchanged:
  - **Retry at most twice** on a bad or unapplicable diff, then fall back to `HOST_AGENT`
    and log the reason.
  - **Never retry `rate-limit`** — quota resets slowly. Fail over immediately.
  - **Refuse a non-tool implementer on tier-3** (high-risk, per `tier3_globs`;
    pre-classified from the issue's target paths/labels before the diff exists, ambiguous
    ⇒ treat as tier-2 and let s7 gate). A provider whose `transport` is `api`, `ollama` or
    a generic `profile` **cannot run tools**: there the orchestrator does every git/PR
    step itself and delegates only code generation (generate a unified diff against a
    size-limited slice of the in-scope files, apply it, run gates, then commit/push/open
    the PR). Fall back to `HOST_AGENT` on tier-3.
  - **Reading an API key is `secret_access`**, so the run's approved scope must include
    `secrets` — without it, resolve to `HOST_AGENT` before invoking a hosted provider.
  - **Refuse a read-only role that nothing backs.** When the result says `read_only: true`
    and `read_only_backed: false`, the provider is running with whatever flags its entry
    carries — for a profile with `args` and no `review_args`, that is the *implementer's*
    write-enabling set. Fall back to `HOST_AGENT`, or accept the run only as advisory and
    re-check the worktree is clean afterwards. Never read `read_only` alone: it reports
    the role you asked for, not whether anything enforces it.
  - A local-model or generic-CLI harness that already created a worktree must remove it
    before the host path recreates one at the same path (same obligation under
    `--dry-run` if it created one).
  - **Treat any verification a delegate reports as unperformed until you reproduce it.**
    Not just external references — a delegate emitting the *artefact* of a check instead
    of the check is one failure mode with several costumes, all observed: specific-looking
    citations (registry reference numbers, archive snapshot ids) stated as verified when
    nothing verified them; a fabricated `keel.review-verdict.v1` marker written into a
    shipped file; "tests pass" with no run behind it. Re-run the check yourself, or record
    the claim as unverified — never promote it to a fact in a commit, a comment, or a PR
    body because a delegate asserted it.
- **Model and effort selection.** `--provider <name>:<model>` or `--model <token>` picks
  the model; a per-run choice wins over the profile's or the registry entry's, and core
  validates the token (`agents.is_safe_model_token`) before it can reach an argv or a URL
  path — a `delegate-model:<name>` issue label is a lower-trust source than config, so an
  unsafe value is refused rather than escaped. `--effort low|medium|high` is translated
  per vendor; a provider that cannot express it returns `effort_applied: false` with a
  warning instead of silently running at its default.
- **Configured providers.** A `knobs.delegate_profiles` entry (`vendor: cli` or
  `vendor: openai-compatible`) and a `~/.keel/providers.yaml` entry are resolved by name,
  precedence **built-in > project profile > registry**: a built-in vendor always wins and
  may not be redefined — a profile that shadows one is a `keel validate` error and a
  registry entry that does is a `keel doctor --providers` error, never a silent override. Two rules
  hold for an `openai-compatible` endpoint, because config is the surface an attacker
  would influence: **(1) the endpoint is loopback-only by default** — a non-loopback host,
  including cloud-metadata addresses like `169.254.169.254`, is a `keel validate` error
  unless `KEEL_ALLOW_REMOTE_ENDPOINT` is set **in the environment** (env-only on purpose:
  someone who can edit `project.yaml` must not be able to grant it), and non-`http(s)`
  schemes are refused outright; **(2) `api_key_env` is a variable *name*, never a key** —
  profile config is hashed into `config_hash`, so a pasted key would be published.
- **Attribution comes back in the result.** Record `attribution.agent_label` and
  `attribution.model_label` as PR labels and `attribution.system` as `IMPLEMENTER_SYSTEM`.
  For a configured provider the document also carries `delegate_profile` — the entry's
  name, so the s11 closure can say *which* CLI ran rather than just `cli`. Record it under
  **`delegate_profile`**, never `profile`: the run record's `profile` field already means
  the workflow profile (`standard`/`compound`). **Write the ledger's `actors.implementer`
  as `attribution.system`** (the vendor string, e.g. `cli:<model>`), never the profile
  name: the evidence gate splits it on the first colon and cross-checks the result against
  the PR's `agent:*` labels, so recording `cursor` there against an `agent:cli` label
  reads as a vendor contradiction and blocks the merge.

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

**Attribution (mandatory on every path, even a plain run).** **Never compose an
`agent:` or `model:` label in prose.** Ask core for them:

```bash
keel attribution --vendor <effective-vendor> --model <effective-model> \
  --config .keel/project.yaml --json
```

or read the `attribution` block a delegate result already carries. Apply
`agent_label` and `model_label` to the PR **verbatim**, and record `system` as the
`IMPLEMENTER_SYSTEM` string. Whatever transformation turns a model id into a base label
lives in `keel.agents` and only there — a host that re-derived it wrote `agent:gemini` /
`model:gemini` for a run keel calls `agent:agy` / `model:gemini-3`, and because the same
host also wrote the ledger, the cross-check compared its own guess to its own guess and
passed (#1013). `keel evidence-verify` now refuses a label the CLI could not have
produced (`attribution-vocabulary`), so a hand-composed label blocks the merge.

Write the ledger's `actors.implementer` as the same `<vendor>` (or `<vendor>:<model>`)
you passed to `keel attribution` — `keel ship --live --append-ledger` warns when that
vendor is not one keel knows. Attribution always reflects the **effective** implementer
that actually ran — never the requested-but-fell-back one — and is written at label-flip
time (skipped only under `--dry-run`, logged instead).

**Stamp the run's provenance on the PR.** Immediately after the PR exists (before CI, before
review), post the ship-provenance artifact:

```bash
keel post-comment .keel/project.yaml --root . --target pr:<PR> \
  --artifact ship-provenance --body-file <rendered.md> --run-id "$RUN_ID"
```

Render `<rendered.md>` from `result.artifact_bodies.ship_provenance` of
`keel ship --json` — do not hand-write it. That comment is what tells
`keel evidence-verify` this is a keel run: the gate arms on the `keel.ship-provenance.v1`
marker **ahead of** the branch-name regex, so a run whose branch is not named
`fix/issue-<N>-…`, or whose ledger lives in a per-run worktree CI cannot read, is still
gated. Skipping this step is how a PR that was never reviewed reports
`enforced: false (no-ship-provenance)`.

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

#### Test-first s4 (`implement_mode: tdd` / `--tdd`)

`tdd` is an **s4 profile**, exactly as `compound` is a workflow profile: the backbone step
ids do not change, and every other step behaves identically. What changes is that s4 runs
**twice against the same provider** — two `keel delegate run` calls, one commit each:

| phase | brief | diff | gates |
|---|---|---|---|
| `tests` | *Derive the failing tests from the issue's acceptance criteria. Do not implement anything.* | **test paths only** (`policy_pack.test_groups.*.test_paths`, else `.paths`) | expected **red** — that is the phase's proof |
| `implementation` | *Make those tests pass without weakening them.* | the change | must end **green** |

Rules for the two phases:

- **Same provider for both.** The point is that the implementer wrote the tests it then
  had to satisfy; handing phase B to a different seat loses that.
- **One commit per phase, in order.** Phase A's commit must touch nothing outside the
  project's test paths, and phase B must actually touch an implementation path. Squashing
  the two locally, or amending A after B, destroys the evidence the gate reads.
- **Phase A's red gates are not a failure.** Do not run the fix loop against them, and do
  not let a red phase-A gate run trigger a fall-back to `HOST_AGENT`. Run the gates after
  phase B.
- **Never weaken a test to make it pass, and never delete one.** If a criterion turns out
  to be wrong, say so in the PR body under `Testing` and change the *test* in a commit of
  its own with the reason — do not quietly delete an assertion in the implementation
  commit. Removing a test path after the tests commit is a **blocking** gate failure, not
  a style note — deleting it and renaming it out of the test paths count the same, so
  moving a test into the implementation tree does not get around the gate.
- **Everything else about s4 is unchanged**: the same dispatch table, the same retry and
  fall-back policy, the same attribution, the same declared-scope self-check.

At **s8** the pure `tdd-order` gate then verifies what actually happened, from the commit
list and the path policy alone: the first non-merge commit on the branch touches only test
paths **and adds or modifies at least one of them**, no later commit removes a test
(deleted, or renamed out of the test paths), a later commit touches an implementation
path, and the gate run is green. It is
`on_fail: block` like `build`; its message names the offending paths, the removed tests,
the test globs it matched against, or the missing half. A project whose `test_groups`
declare no paths fails the gate closed rather than passing vacuously — declare
`policy_pack.test_groups.<group>.test_paths` before asking for the profile.

**What the gate does not check.** It reads commit *order and paths*. It does not run phase
A's tests and cannot report that they were red, and it cannot tell whether the committed
tests assert anything. Phase A's red run is *your* evidence: say in the PR body's `Testing`
section what phase A ran and that it failed. The gate is the shape check, not the substance
one.

Both phases are recorded: the ledger's `run_context.implement_mode` is `tdd` and
`run_context.implement_phases` carries one record per phase with its commit **and the
implementer that ran it** — pass `keel ship --phase-implementer tests=<label>` when a phase
really did run on a different provider, rather than letting one `--implementer` stand for
both. The rendered closure comment says
**`Implement: TDD (tests <sha> by <implementer> → implementation <sha> by <implementer>)`**.

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
`knobs.team` jury panel > `--no-jury` > `--jury` > tier-3 auto > off): tier-3 ⇒ auto-on.
Mode is **gating** by default (`--jury-advisory` ⇒ advisory-only). A tier whose
`knobs.team` review policy is `jury` is **always gating and always enabled** — the panel is
that tier's review, so a per-run flag cannot leave it with no required evidence; the
ignored flag is reported in `assignment.warnings`. The reviewer count never changes with a
jury flag, and the jury never changes the reviewer count — except on a panel tier, where
the reviewers *are* the panel, so `reviewers.count` is the number of ballots owed (the size
a posted verdict declared, else the `min_vendors` floor). Read both from
`review_merge_contract` rather than re-deriving them. Log the decision
(`jury: enabled (reason; mode) / disabled`).

### Step boundary verification
At every successful backbone transition, persist the canonical JSON handoff produced from
`keel.stepverifier.build_handoff`, write/update the checkpoint for the next safe boundary,
and run `keel step-verify --step sN --handoff-file <file> --evidence-report <file>`
(plus this run's `--project` / `--tier` / `--effort` / `--team`, so it reads the same bench)
before
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
Run one reviewer per entry in `review_merge_contract.reviewers.slots` — the same seats as
`assignment.reviewers`, already reconciled with the s5 tier, `--reviewers`, and each
positional `--review-delegate`. Each slot carries its own `provider`/`model`/`effort`, so
a two-vendor panel is two different providers, not one vendor run twice.

**When `reviewers.panel` is `jury`, the panel is the review — dispatch it here, once.**
There are no host reviewer slots and there is no second reading at s8: running both would
pay twice for the same diff, which is the arrangement this path replaced. Read the panel
off the contract, never off this prose:

```bash
# 1. one panel run, read-only, machine-readable, saved for the visualizer
mkdir -p .keel/state/jury
jury --format json --diff-file "$DIFF" -o ".keel/state/jury/$RUN_ID.json"

# 2. its ballots become the s7 evidence: one head-pinned verdict per panelist,
#    carrying the vendor and model that produced it, plus the jury verdict
keel review .keel/project.yaml --root . --pr <PR> \
  --from-jury ".keel/state/jury/$RUN_ID.json" --run-id "$RUN_ID" --live
```

`keel review --from-jury` reads the report's per-reviewer ballots (`jury --format json`,
report schema 1.1+), refuses a report that carries none rather than posting a thinner
review, and fails closed when the bundle is under the required count. Its `--json` result
carries a `panel` block — the ballots, the distinct vendors, and the **verified** consensus
findings already in keel's severity vocabulary — and that block is what feeds s9: verified
`critical`/`major` block exactly as a host reviewer's findings do. Take the fix loop's input
from there, not from the panel's prose.

**The panel's ballot count governs — post one verdict per ballot, not
`reviewers.count` verdicts.** That field is a *floor*, not a target: `keel plan` and
`keel ship` resolve the contract with no pull request in reach, so on a panel tier they can
only publish `jury.min_vendors`. The number of ballots is known to the run that dispatched
the panel, which is you. `keel review --from-jury` posts one verdict per ballot in the
report and declares the real size as `panelists: <N>` on the jury verdict, which is how
`keel evidence-verify` and `keel merge` then require exactly that many. Never trim the
bundle to `reviewers.count`: a declared count may only ever raise the requirement, so a
larger panel is honoured and a short one is still held to the floor.

A panel that spans fewer than `jury.minimum_vendors` distinct vendors is reported by core,
exactly as it always was — and on a panel tier it changes **nothing** about what is
required. The bench does not move, the ballots stay required, and the jury verdict stays
required: a short panel does not get to excuse itself from the consensus record that says
it was short (the shortfall surfaces as `review-vendor-distinctness` from
`evidence-verify` instead). Report the count (`keel evidence-verify --jury-vendors <N>`),
post every ballot the panel returned, and let core decide. Do not fall back to host
reviewers on your own; a tier's reviewers are what its config says they are.

Before the panel, run the **gate review** when `assignment.gate` is present: the project's
second opinion on the implementation, dispatched read-only exactly like a reviewer but
with `--role gate`. **Nothing in core enforces it** — there is no evidence item for a gate
review, so `keel evidence-verify` and `keel merge` cannot tell whether it ran. It is
mandatory the way operator consent is mandatory: emitted by core, honoured by you. `distinct_ok: false` means the policy asked for a vendor
other than the implementer and did not get one — say so rather than passing it off as an
independent opinion.

A non-host reviewer runs through the **same one command as s4**, with the role that makes
it read-only — `--role review` (or `gate` / `chair`). The `--provider` value is the slot's
own `provider` (plus `:model` / `--effort` when the seat carries them), read straight off
`review_merge_contract.reviewers.slots[i]` — never one vendor reused for every slot:

```bash
# one dispatch per slot; SLOT is an entry of review_merge_contract.reviewers.slots
keel delegate run --provider "$SLOT_PROVIDER" --role review \
  --prompt-file "$RUBRIC_AND_DIFF" --cwd . --timeout 900 --project .keel/project.yaml

# the gate review, when assignment.gate is present
keel delegate run --provider "$GATE_PROVIDER" --role gate \
  --prompt-file "$RUBRIC_AND_DIFF" --cwd . --timeout 900 --project .keel/project.yaml
```

A slot whose `kind` is `subagent` never reaches `keel delegate run`: it is a host
(Claude-class) subagent named by `slot.name`, dispatched the way s4 dispatches one.

Read the same JSON contract s4 documents: `text` is the verdict, `attribution` is what
you record per reviewer, `error_code` is what you branch on. Long panels detach exactly
as s4 does — `--detach` per reviewer with `--timeout`, then `keel delegate wait <run-id>
--timeout <s>` on each; never a sleep loop. The `secrets`-scope and
no-retry-on-`rate-limit` rules are unchanged; there is **no tier restriction**, because
review output is advisory, not a mutation. The orchestrator still posts — the
**orchestrator owns all writes**; reviewers never call a GitHub write API.

The read-only role is a policy core enforces where it can and reports where it cannot, and
the result tells you which: **`read_only` is the role you asked for, `read_only_backed` is
whether anything enforces it.** For the three built-in CLI vendors it is backed — the
invocation carries the vendor's documented read-only mechanism and no write-enabling flag,
asserted per vendor in keel's own tests. Two of the three carry no permission bypass
either; `agy` is the exception, because its sandbox is the only read-only mechanism it
documents and it still needs the non-interactive flag to run unattended, so its promise
rests on the sandbox alone. A **`knobs.delegate_profiles` (or registry)
reviewer is the one case keel cannot make read-only for you**: a profile is an arbitrary
binary, the same `command` serves both roles, and its `args` typically carry the
*implementer's* write-enabling flags (`aider`'s `--yes-always`, `cursor-agent`'s
`--force`). `keel delegate run` uses the entry's **`review_args`** for a read-only role,
and when none is configured it returns `read_only_backed: false` plus a warning naming the
provider — because the profile's fallback is `args`, so "no `review_args`" means the
reviewer just received the implementer's flags. **Check that field before you trust the
run**: set `review_args` to a read-only invocation for any profile used as a reviewer, and
otherwise treat the output as advisory and **re-check the worktree is clean afterwards**
rather than assuming it was untouched.
Spawn all reviewers in a **single
Agent message** so they run concurrently; each gets a fresh codename, the PR head SHA, its
focus slice, and a no-cross-reading instruction. Coverage invariant: when the count drops,
focus dimensions **merge, never drop** (a 1-reviewer slot covers all dimensions; suitable
only for narrow tier-1 PRs). Run any `reviewers` Lego extensions. Capture per-reviewer
**effective** vendor+model for attribution (lock-step parallel arrays so the s11 closure
can zip them by index) — from `keel attribution --vendor <v> --model <m> --json`, or from
the delegate result's own `attribution` block, never by composing the labels yourself.
On a missing/erroring delegate vendor, fall back to the host
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
same attribution computed at s7 — so `evidence_require_distinct_vendors` can verify the
verdicts came from distinct vendors. That knob is **on by default from TIER-2 up** (a
project that has decided otherwise sets it to `false` explicitly), so a verdict without
`vendor:` provenance blocks the pre-merge evidence gate on most changes. This is jury-agnostic: a plain
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
Pass this run's `--effort` / `--team` on the `keel review` call too, so the reviewer count
it fails closed against is the one `contract.assignment` published rather than the tier's.
posting. This is the canonical way to collapse `render_review_verdict` + N× `post-comment`
+ `evidence-verify` into one deterministic, idempotent step; it never spawns reviewers — the
host still produces the review content above.

**Repeat this run's jury flags on the `keel review` call.** `keel review` accepts
`--jury` / `--no-jury` / `--jury-advisory` (#1043) and resolves the same review contract
every other surface does. They never change how many verdicts you post — the bench is a
pure function of config + tier + role + `--reviewers` / `--review-delegate` — but they do
decide whether the contract requires a `jury-verdict`, so a run started as
`keel ship --no-jury` must post with `keel review --no-jury …`; otherwise `--verify`
re-checks a stricter gate than the one this run was planned against. On a
`review.by_tier.<n>: jury` tier the panel outranks all three and the verdict stays
required whatever was typed, and `--from-jury` is orthogonal to them: it says *where the
verdicts come from*, the flags say *whether a jury verdict is required*.

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
Under `implement_mode: tdd` (or `--tdd`, which `run-gates` also accepts) this run also
carries the pure **`tdd-order`** gate — see **Test-first s4** — evaluated after the other
gates because its verdict includes theirs.
An **`agentic` gate reports `NOT-RUN` here** — this command does not dispatch those, you
do. `NOT-RUN` is not a pass: a gate declared `on_fail: block` that shows `NOT-RUN` blocks
the merge decision and refuses to certify the run, so `keel merge` will reject the head.
Dispatch the gate yourself (at s9 for `pre-merge` Lego), then **re-run the command with
`--gate-result <id>=pass|fail`** to record what your dispatch found. A recorded result may
only be given for a gate keel did not execute — the command refuses to override its own
measurement of a gate it ran.

**One panel per head, never two.** When `review_merge_contract.reviewers.panel` is `jury`,
s7 already ran the panel and `keel review --from-jury` already posted its ballots and its
`keel.jury-verdict.v1` comment. Do **not** run the jury again here: re-read the saved report
at `.keel/state/jury/$RUN_ID.json` if you need its findings, run the command gates below,
and leave the verdict alone. Running it twice buys a second opinion from the first opinion
and doubles the bill for it. The rest of this section is the jury as a *gate* — the
arrangement for a tier whose reviewers are the host bench and whose panel is a second,
separate reading.

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
gate reads to decide whether a `jury-verdict` is required at all. A verdict rendered by keel
also declares `panelists: <N>`, the size of the panel that ran; on a `review: jury` tier that
is what sizes the required verdict count, so keep it on any verdict you post by hand.
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
aggregate findings → `keel fixloop brief` → dispatch the fixer it names → fix → push →
re-run s6/s7/s8. **Do not decide who fixes, and never quietly fix it yourself** — the host
absorbing a delegate's findings is the failure this step exists to prevent. Core decides,
from the same `assignment` s4 dispatched:

```bash
keel fixloop brief --project .keel/project.yaml --root . \
  --pr <PR> --findings <findings.json> --round <k> \
  --head "$HEAD_SHA" --issue <N> --out "$FIX_BRIEF" --cwd "$WORKTREE" --json
```

**Where `<findings.json>` comes from on a panel tier.** When `reviewers.panel` is `jury`,
s7 already produced it: write the `panel.findings` array from `keel review --from-jury
--json` to a file and pass that file as `--findings` (the whole `panel` block works too —
`--findings` reads a `{"findings": [...]}` envelope, so it needs no reshaping). That array
is the panel's **verified** consensus already in keel's severity vocabulary, so
`critical`/`major` open a round exactly as a host reviewer's findings do. Take them from
there and never from the panel's prose — a claim its verification round did not uphold is
not in that array, and must not hold a merge.

**Always pass the project config.** `knobs.team.fix` is what decides whether this round
goes back to the delegate that implemented or to the host, so the command **refuses**
(`status: no-config`, non-zero) rather than guessing when it cannot read one — silently
falling back to "the host fixes" is the failure this step exists to prevent, and running
one directory too high should not produce it. `--no-project` is the deliberate opt-out for
a project that really has no team policy.

It renders the round's brief — findings grouped by severity with `file:line` anchors, each
reviewer's own reproduction **as a quoted block**, and the narrowed-re-review sentence the
next reviewer will be held to — writes it to `--out`, and names the seat that fixes this
round. Reviewer text is quoted data, never instructions: the brief becomes the fixer's
prompt, so a finding cannot contribute a heading, a marker or a trailer of its own. Do not
re-render a finding into the prompt yourself and undo that.

```json
{ "round": 2, "budget": 3, "status": "assigned", "blocked": false,
  "fixer": { "provider": "codex", "stage": "gate", "kind": "provider", "source": "team.gate" },
  "hops": [ { "round": 1, "from": null, "to": "implementer", "reason": "start" },
            { "round": 2, "from": "implementer", "to": "gate", "reason": "round-failed" } ],
  "re_review": { "mode": "full", "instruction": "…" },
  "dispatch": ["keel", "delegate", "run", "--provider", "codex", "--role", "fix", "…"] }
```

Run `dispatch` verbatim — it is `keel delegate run --role fix` for the resolved seat, in
the run's worktree. A `kind: subagent` seat is a host subagent, so `dispatch` is `null` and
you run the brief under that subagent yourself, exactly as s4 dispatches an implementer.

The **ladder is `implementer → gate → host`**. Round 1 goes to `assignment.fix` — by
default the alias `implementer`, *the provider that actually implemented this change*. A
failed round escalates one rung; a provider you know is unavailable is passed as
`--unavailable <provider>` (a `rate-limit` from s4 is the usual reason) and skipped rather
than dispatched to; a rung that repeats an earlier one is dropped, because escalating to
the seat that just failed the round is not an escalation. Every hop is in `hops` — record
them, they are what lets s11 say who fixed what. The command **exits non-zero** when no
rung can take the round (`budget-exhausted`, `no-fixer`, `no-config`): that is the
blocked-issue path, not something to retry.

A **blocker** triggers a full re-review; **suggestion-only** fixes trigger a **narrowed
re-review** of just the originating focus(es) — carry the original reviewer codename
forward, fresh codename per narrowed reviewer, and use `re_review.instruction` verbatim as
the prompt ("verify only the applied fix in commit `<sha>`; do not re-review what you
already approved"); spawn multiple narrowed focuses in one Agent message. A
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

Append each fix/review/test round to the run-events file with `keel runcontrols`, and put
the round's fixer on the event so the closure can attribute it:

```bash
keel runcontrols "$RUN_EVENTS" --slot fixloop --action fix --round <k> \
  --provider "$FIXER" --stage <implementer|gate|host> --attribution "$AGENT_LABEL"
```

`fix_attribution.sentence` in that command's `--json` is the deterministic phrase s11
embeds — *"implemented by agy, fixed by opus in round 2"*. Pass the `attribution` the
delegate run returned, not a label you composed. A hard
halt from `keel runcontrols` is fail-closed and must stop the ship run until an operator
chooses an explicit `--max-rounds` override.

### s10 merge
The literal merge is **core-owned**: route it through `keel merge`. Raw `gh pr merge`
calls and hand-rolled lock shells are **spec violations** for ship-style flows — the
lock, window re-check, CI rollup read, evidence verification, and the SHA-stamped
gates-pass check must run deterministically inside core, not as adapter prose.

- **Evidence gate — do this first, on every path (audit GAP-REV):** before *any*
  merge — including a raw `gh`/REST merge you might be tempted to use — run
  `keel evidence-verify .keel/project.yaml --root . --pr <PR> --phase pre-merge` — adding this
  run's `--effort` / `--team` when it had them, or the gate re-derives a bench the run never
  dispatched — and confirm it **exits 0**. It fails when the s7 review verdict (a posted PR comment/review
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
  `keel merge .keel/project.yaml --root . --pr <PR> --run-id "$RUN_ID" --approve-scope <scopes> --operator <operator>`
  — with this run's `--effort` / `--team` when it had them, for the same reason: `merge`
  verifies the evidence contract itself and must resolve the bench this run dispatched.
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
Record the run for `/keel:wrap`: the **effective** implementer + reviewer vendors/models
(as `keel attribution` reported them at s4/s7 — the closure repeats those labels, it does
not re-derive them), tier, rounds, window decision, and outcome. When s9 spent a round,
name **who fixed it**: read `fix_attribution.sentence` off `keel runcontrols … --json` and
use it verbatim. An escalated round was not fixed by the implementer, and a closure that
says otherwise is wrong about the one fact a reader will trust it for.
Post the **closure comment** to **both** the
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
Use `keel post-comment` for ship-provenance, issue-update, review-verdict, jury-verdict,
and closure-comment artifacts; a malformed body missing its marker must stop the step before
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
new pipeline behaviour and cannot produce a config the grammar could not.

**Do not improvise the questions.** Run the core picker and use what it prints:

```bash
keel ship <project.yaml> --root . --wizard [--wizard-answer key=value]...
```

Core owns the offered choices, and they come from one source: the provider probe behind
`keel doctor --providers`. **A provider that probe did not mark available is never offered
and cannot be selected** — so the wizard cannot propose a CLI the operator has not
installed, which is exactly what an improvised list did.

**Only an answered question becomes a flag.** Every option also has a default, read from
`knobs.team` and the flags already on the command line, and core deliberately does *not*
write those back: the offered reviewer bench is derived at a nominal tier (the real one is
classified at s5, after the wizard) and the jury default is whatever the tier and
`knobs.team` already say, so materialising them would override the policy they came from.
Quick-start therefore emits **no flags at all** — pass none on, and let s1–s10 resolve the
run exactly as they would have.

The probe lists only providers keel can *dispatch* to, so a `subagent:` seat is never
among them: on a project whose `knobs.team` names one, the implementer question shows the
first dispatchable provider as its default. An operator who accepts it leaves `knobs.team`
in charge of s4 — including `implement.by_role` and the `subagent:` seat. An operator who
answers it gets `--delegate`, a per-run override that wins over `knobs.team.implement` and
takes `implement.by_role` (and `--role`) out of the picture. Say so if they answer it.

**Hard interactivity guard:** never enter the wizard in any non-interactive context (watch
mode, overnight/background/headless runs); there it degrades to a logged no-op and proceeds
with the literal flags as parsed (never a hang, never a rejection). Core enforces this
itself through an `isatty` check, and a machine where no provider is usable is the same
logged no-op — but do not pass `--wizard` from an unattended run just because core would
survive it.

First question is a **Quick-start vs Customize** fast path (Quick-start resolves every
option to its default and only still asks for Issues). Every question shows its
`(default)` option first with a one-line description. A run is asked `mode`,
`implement.provider`, `implement.model`, `jury`, `review` (this run's bench) and
`review_comments` — and nothing else, because every one of those lands on a real
`keel ship` flag. The gate seat, the reasoning effort and the jury-as-panel are
`keel init --wizard` questions: no run flag carries them (`--delegate` splits
`provider:model`; `--reviewers` takes `1|2|3`), so a run neither asks them nor accepts
them via `--wizard-answer`. The gate you dispatch at s7 is `assignment.gate`, exactly as
before.

Core echoes the resolved **flag set**; pass those flags on literally and proceed to s1:

```text
keel ship --wizard — resolved
  flags : --delegate ollama:qwen2.5-coder --reviewers 1 --review-delegate claude --review-comments summary --jury-advisory
  seats : implement=ollama:qwen2.5-coder · gate=claude (distinct from the implementer) · review=claude
```

The `seats` line restates the flags in seat form; it never carries a value the flags do
not, so there is nothing on it to apply separately — read `assignment` for the gate seat
and any per-seat effort, as you already do. A `flags` line reading `(none — every option
kept its default …)` is the normal quick-start outcome, not a failure: pass no flags and
proceed. A malformed or unofferable `--wizard-answer` exits 1 before any gate runs —
surface that to the operator rather than retrying with a guess.

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

<!-- keel-generated: surface=plugin command=ship keel_version=1.19.3 source_sha256=14f781a4e0cb3a464990e015abae9cba0eb424327ff98e7884bb20ceeb9acace generated_sha256=14f781a4e0cb3a464990e015abae9cba0eb424327ff98e7884bb20ceeb9acace -->
