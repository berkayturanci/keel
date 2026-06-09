# Command contracts

Keel command contracts are stable JSON records for agent adapters and parity tests. They
describe what a command would do before an adapter starts mutating work.

## Where contracts appear

- `keel plan <project.yaml> --json`
- `keel plan <project.yaml> --command <adapter> --json`
- `keel plan <project.yaml> --command <adapter> --live --json`
- `keel ship <project.yaml> --dry-run --json`
- `keel ship <project.yaml> --live --json`
- `keel ship-v2 <project.yaml> --dry-run --json`
- `keel ship-v2 <project.yaml> --live --json`
- `keel ledger <project.yaml> --json`
- `keel checkpoint <project.yaml> --json`
- `keel resume <project.yaml> --json`
- `keel implement <project.yaml> <issue> --dry-run --json`
- `keel implement <project.yaml> <issue> --live --json`
- `keel ci-check <project.yaml> --pr <number> --json`
- `keel morning <project.yaml> --json`
- `keel morning <project.yaml> --live --json`
- `keel wrap <project.yaml> --json`
- `keel overnight <project.yaml> --json`
- `keel regression <project.yaml> --json`
- `keel regression <project.yaml> --live --json`
- `keel review-all-day <project.yaml> [days] --json`
- `keel review-all-day <project.yaml> [days] --live --json`

Human-readable output remains the default. JSON output is the adapter-facing contract.

## Contract envelope

Every contract includes:

| field | meaning |
|---|---|
| `schema_version` | Contract schema identifier. Current value: `keel.command-contract.v1`. |
| `command` | Adapter command being planned. |
| `mode` / `dry_run` / `no_mutations` | Whether this record represents a non-mutating rehearsal. |
| `project` | Resolved project config summary plus stable `config_hash`. |
| `workflow_profile` | Command profile metadata. `ship` is `standard`; `ship-v2` is a first-class `compound` variant that inherits shared ship primitives and declares step overrides. |
| `graph` | Command step graph. `ship` and `ship-v2` use the fixed backbone steps; other adapters expose their command-local steps; project commands expose a single `project_command` graph entry. |
| `backbone_plan` | Fixed keel backbone with gates slotted onto steps. |
| `gates` | Planned gate specs, including kind, phase, failure behavior, source, and capability declarations. |
| `project_commands` | Project-provided commands declared by policy, separate from packaged keel adapters. |
| `extension_hooks` | Loaded extension hooks grouped by slot. |
| `extension_problems` | Fail-soft extension load problems. |
| `required_capabilities` / `optional_capabilities` | Capability names the adapter should evaluate before mutating work. |
| `capabilities` | Runtime evaluation for the current environment. |
| `github_transport` | Selected GitHub transport and degraded GitHub operation capabilities. |
| `run_ledger` | Structured JSONL run-ledger storage and schema contract. |
| `checkpoint` | Resumable checkpoint storage and resume/reconcile contract for ship and work-block runs. |
| `capture` | Post-merge capture marker, skip vocabulary, fail-soft, recursion-guard, redaction, and verifier contract. |
| `side_effects` | Declared possible live-run side effects and whether dry-run mutates. |
| `operator_consent` | Operator consent requirement, approved mutation scopes, delegated-agent scope, and consent record metadata. |
| `issue_intake` | Present for work-owning flows (`ship`, `ship-v2`, `implement`, `overnight`); extracted objective, deliverable, acceptance criteria, readiness, questions, and ledger metadata. |
| `morning_contract` | Present for `morning`; project-neutral daily-brief sections, health providers, report destinations, priority sources, and deferral queue metadata. |
| `session_contract` | Present for `wrap` and `overnight`; project-neutral linked-worktree, gate, PR, merge-window, report, deferral, and ship-handoff metadata. |
| `scan_contract` | Present for `regression` and `review-all-day`; project-neutral scan target, scope, dedupe, issue-write, reviewer-isolation, and final-report metadata. |
| `reporting_contract` | Present for `coverage`, `deps-audit`, and `flake-audit`; project-neutral codename anchors, idempotency/dedupe rules, dry-run write behavior, degradation modes, and ship handoff metadata. |

Project command entries include name, local command path, description, agent role, path
selectors, required/optional capabilities, side effects, dry-run safety, and source
(`policy_pack.project_commands` or `policy_pack.command_routing`). The contract never embeds
the project command body.

## Operator consent block

Every command contract includes `operator_consent`:

| field | meaning |
|---|---|
| `schema_version` | Consent contract schema identifier. Current value: `keel.operator-consent.v1`. |
| `requires_operator_consent` | `true` only when this run is live and approved scope is missing. |
| `would_require_operator_consent` | Whether the command has live-run mutation classes, including under dry-run. |
| `status` | `not-required-dry-run`, `not-required-read-only`, `missing`, `approved`, or `agent-delegated`. |
| `mode` | Resolved consent mode: `explicit`, `standing`, or `agent`. Mode precedence is CLI flag, `KEEL_CONSENT_MODE`, project config, built-in `explicit`. |
| `consent_scope` | Mutation classes required for a live run: `filesystem`, `git`, `github`, `secrets`, `release`, `production-adjacent`. |
| `approved_scope` / `effective_approved_scope` / `missing_scope` | Scope approved by the operator or standing approval, the subset that matches the resolved plan, and any live-run gap. |
| `approval_source` | `none`, `flag`, `env`, or `config`, showing where approval came from. In `standing` mode, precedence is flag, then `KEEL_APPROVE_SCOPE`, then `automation.approved_scopes`. |
| `consent_prompt` | Consumer-neutral prompt generated from the resolved command, target, mode, and scopes. |
| `delegated_agent_scope` | Scope adapters must pass to delegated agents; scope expansion must block or escalate. |
| `consent_record` | Local metadata for approved live runs: timestamp, operator, workflow, target, scopes, approval `source`, and `secret_values_recorded: false`. |

Dry-run contracts do not require approval, but still expose the live scopes that would need
approval. Live contracts with missing consent are preflight blockers and must stop before
files, git state, GitHub state, releases, secrets, or production-adjacent systems are touched.
In `agent` mode, keel emits `status: agent-delegated`; adapters must rely on the host
agent permission model for the actual approval prompt while still respecting the emitted
scope and every non-consent gate.

## Run ledger block

Every command contract includes `run_ledger`. The ledger is the durable,
machine-readable run history that adapters use instead of parsing free-form PR or issue
comments. The default location is `.keel/state/run-ledger.jsonl`; projects can choose a
different local path with `policy_pack.reports.run_ledger`.

The block records:

- `schema_version: keel.run-ledger.v1`
- `format: jsonl`
- `path` and `path_source`
- `missing_handling: treat-as-empty`
- append owners (`ship`, `ship-v2`) and offline readers (`morning`, `wrap`,
  `overnight`, `capture-verification`, `ledger`)

`keel ship --live --append-ledger` appends exactly one structured `ship_run` record after
the ship assessment succeeds. `keel ship --json` always includes the would-be record under
`result.run_ledger.record`; dry-run output never writes it. The record is consumer-neutral:
it stores command, target, optional issue/PR numbers, branch/head SHA, changed-file count,
gate summaries, verdict, risk/review/merge assessment, effective actor labels, issue
intake, and capture outcome. It does not store project labels, domain names, product
paths, or stack-specific fields.

`keel ledger <project.yaml> --root <repo> --json` reads the ledger offline and returns an
empty `records` array when the file is missing. `morning`, `wrap`, overnight session
recaps, and capture verification should use this reader or the same contract path instead
of scraping closure comments.

## Capture block

Every command contract includes `capture`. It is the core post-merge capture contract that
adapters should use instead of re-deriving marker or verifier behavior from prose.

The block records:

- `schema_version: keel.capture.v1`
- stable marker format:
  `compound-learning: pr=<N> status=<applied|deferred|skipped:reason>`
- allowed statuses: `applied`, `deferred`, and `skipped`
- allowed skip reasons: `dry-run`, `deferred`, `merge-failed`, `recursion-guard`,
  `capability-unavailable`, and `no-policy`
- extension slots that may provide project-owned capture content: `capture` and
  `post-merge`
- fail-soft semantics: capture failure after a successful merge must not revert the merge
- recursion guard semantics: capture-on-capture work skips with `skipped:recursion-guard`
- durable-artifact safety: capture artifacts must pass through the run-ledger redaction
  contract before they are persisted
- session-end verifier command: `keel capture-verify`

Core owns marker generation, validation, and offline verification. Projects own what to
learn and where the learning goes through `policy_pack.capture` plus a `capture` or
`post-merge` extension. `keel capture-verify <project.yaml> --root <repo> --merged-pr <N>`
reads the configured run ledger and returns `complete` only when every expected merged PR
has exactly one valid marker. Missing, invalid, or duplicate markers make verification
`incomplete` and exit non-zero.

## Checkpoint block

Every command contract includes `checkpoint`. The checkpoint is the durable current
resume point for an active work-owning run. It is intentionally separate from the run
ledger:

- the run ledger is append-only history and shipped-run evidence;
- the checkpoint is a replace-in-place state file for the latest safe step boundary.

The default location is `.keel/state/checkpoint.json`; projects can choose a different
local path with `policy_pack.reports.checkpoint`. The contract records:

- `schema_version: keel.checkpoint.v1`
- `format: json`
- `path` and `path_source`
- `missing_handling: no-checkpoint`
- write owners (`ship`, `ship-v2`, `overnight`)
- the `checkpoint` reader/writer command and the `resume` dry-run command
- every backbone step id, whether it is a safe resume boundary, and the idempotent
  resume action for that boundary

Adapters should write a checkpoint at step boundaries, after the step's live state is
known and before moving to the next step. The checkpoint stores the run id, command,
target, issue queue, current and completed step ids, branch/worktree/PR/head SHA
identifiers, last gate/review/check markers, merge/capture/close state, and a repeat
policy. That policy is conservative: never repeat a merge when live PR state is already
merged, use idempotent anchors or skip duplicate comments, and refuse to continue when a
recorded worktree path conflicts with live state.

`keel resume <project.yaml> --root <repo> --json` reads the checkpoint and returns a
dry-run resume plan. It never mutates git, files, GitHub, comments, or releases. Adapters
must reconcile live state before resuming and can pass the reconciled state into the
dry-run plan with `--live-pr-state` and `--live-worktree-state`. Missing checkpoints
return `status: no-checkpoint`. Ambiguous state, such as a checkpoint that names a PR or
worktree that live state reports missing, returns `status: ambiguous`, exits non-zero,
and includes the reconcile action to perform before retrying. If a PR is already merged,
resume moves to capture or closeout and does not repeat the merge.

## Issue intake block

Work-owning commands expose `issue_intake` before branch/worktree creation or implementation.
Adapters should pass the selected issue title, body, and labels into `keel plan` or
`keel ship` using `--issue-title`, `--issue-body`, and repeated/comma-separated
`--issue-label` flags. The core then classifies readiness as one of:

- `ready` — objective, deliverable, and acceptance criteria are present; code mutation may
  start after the other gates pass.
- `needs-input` — missing or ambiguous scope; adapters must ask the generated questions
  and must not mutate code for that issue.
- `blocked` — a dependency or waiting condition is present; adapters must not mutate code.
- `out-of-scope` — the issue is marked not planned or outside scope; adapters must not
  mutate code.

The block records `objective`, `deliverable`, `acceptance_criteria`, `risk_tier_inputs`,
`required_docs_tests`, `missing_info`, `blockers`, `questions`, and a compact
`ledger_record`. Work-block commands such as `overnight` should skip non-ready issues and
continue with the next ready issue when their queue policy allows it. This mirrors a human
teammate's readiness discipline: clarify the ticket before starting work, and preserve the
clarification trail in the run ledger.

## Dry-run result records

`keel ship --dry-run --json` and `keel ship-v2 --dry-run --json` add a `result` object
with deterministic data:

- changed files and changed-file count
- issue intake readiness and ledger record
- would-be run-ledger append record and resolved ledger path
- gate outcomes and normalized findings
- aggregate verdict
- risk tier, reviewer count, window state, CI state, and merge decision

This result contract is intentionally deterministic so parity tests can compare adapter
behavior without creating branches, posting comments, or merging PRs.

Standalone commands also emit deterministic result records:

- `implement` shows the issue target, base branch, branch/worktree path patterns, resolved
  delegate override or project-routing source, and the handoff commands. It never marks
  standalone implement as a merge path.
- `ci-check` shows the selected PR target, configured workflow map, latest-run context
  shape, diagnostic classifications, the selected GitHub transport, and the next-command
  routing recommendations. It is read-only and proposes at most one fix.
- `morning` shows the brief target/window, generic report sections, selected GitHub
  transport, project-declared health providers, priority sources, report destinations,
  and the shared deferral queue. It does not run project health commands or write reports
  in dry-run output. Missing optional provider capabilities are reported as
  `unavailable`, not as a successful empty health section.
- `wrap` shows linked-worktree and base-branch guards, configured gate source, commit and
  ready-PR conventions, session recap destination, and the shared deferral queue. It does
  not run gates, commit, push, open a PR, or write reports in dry-run output.
- `overnight` shows merge-window mode sourced from `keel window`, no-night-merge policy,
  ship handoff, priority queue shape, session or morning report destinations, stop
  conditions, and the shared deferral queue. It does not spawn ship runs, create PRs,
  merge, or write reports in dry-run output.
- `pr-loop` shows the feedback workflow policy for PR targeting, comment sources, CI
  re-checks, fix-loop budget, reviewer fan-out, summary comments, and merge handoff. It
  does not commit, push, post comments, or merge in dry-run output.
- `review-cycle` shows the feedback workflow policy for multi-PR sequencing, reviewer
  isolation, posting mode, severity histogram handling, fix-loop budget, completion marker,
  and no-merge/no-formal-approval invariants. It does not commit, push, post comments, or
  merge in dry-run output.
- `regression` shows the canonical base scan target, clean-tree preflight, read-only
  worktree requirement, area policy source, confidence filtering, dedupe behavior, issue
  lock, ship handoff, final report sections, and issue-write safety. It does not edit code,
  push, merge, or open issues in dry-run output.
- `review-all-day` shows the merge-window-aligned span inputs, trunk plus active branch
  scope, remote-ref default, batch/fan-out threshold, file-boundary diff truncation,
  serious-finding filter, exact issue title prefix, dedupe behavior, final report sections,
  and issue-write safety. It does not edit code, push, comment on PRs, merge, or open
  issues in dry-run output.

Reporting commands expose `reporting_contract` records through `keel plan --command ...`
so adapters can preserve legacy report behavior without copying project-specific policy into
keel core:

- `coverage` declares the `COVERAGE-<PR>-` first-line anchor, one-comment-per-PR
  idempotency, update-in-place behavior, the no-duplicate rule when update is unavailable,
  `coverage-regression` label handling, skipped unwired tools, and fatal real coverage
  command failures.
- `deps-audit` declares the exact `deps-audit: <DATE>` tracking issue title, the
  `DEPS-AUDIT-<DATE>-` run anchor, append-per-run comments, skipped per-ecosystem
  degradation, severity/security-only arguments, and no auto-applied fixes.
- `flake-audit` declares the `FLAKE-AUDIT-<DATE>-` run anchor, one-issue-per-flake
  dedupe title, across-run-disagreement-only classification, `fail_count >= 3`, consistent
  failure exclusion, degraded run-level limitations, and no auto-disable behavior.

## Workflow profiles

Workflow profiles let command variants stay directly invokable without copying an entire
adapter body.

`ship` uses:

- `workflow_profile.profile: "standard"`
- no step overrides

`ship-v2` uses:

- `workflow_profile.profile: "compound"`
- `workflow_profile.inherits: "ship"`
- `workflow_profile.first_class_variant: true`
- shared primitives for selection, branching, worktree safety, guard, classification, CI,
  tests, merge-window/merge-lock safety, capture markers, merge, and closeout
- compound overrides for `s4 implement`, `s7 review`, `s9 fixloop`, and `s11 capture`

The graph marks each backbone step with `profile_step`, so adapters can tell which steps are
standard and which are compound without reparsing Markdown.

Standalone direct-use commands use first-class profiles too:

- `implement` uses `workflow_profile.profile: "standalone-step"` and inherits `ship.s4`.
  Its shared primitives include issue targeting, branch/worktree planning, implementer
  routing, operator consent, and handoff to `ship` or `pr-loop`.
- `ci-check` uses `workflow_profile.profile: "standalone-diagnostic"`. Its shared
  primitives include GitHub transport, check runs, latest-run context, log diagnostics,
  read-only behavior, and routing recommendations.
- `morning` uses `workflow_profile.profile: "daily-brief"`. Its shared primitives include
  date/window handling, the deferral queue, shipped-since-last-brief and GitHub summaries,
  project health providers, priority sources, ranked focus, and report output.
- `wrap` uses `workflow_profile.profile: "session-wrap"`. Its shared primitives include
  linked-worktree and base-branch preflights, configured gates, conventional commits, ready
  PR creation, session recap, the deferral queue, and operator consent.
- `overnight` uses `workflow_profile.profile: "session-overnight"` and inherits `ship`.
  Its shared primitives include merge-window handling, ship handoff, priority queue,
  per-issue worktrees, no-night-merge policy, blocker policy, session reports, the
  deferral queue, stop conditions, and operator consent.
- `pr-loop` uses `workflow_profile.profile: "feedback-loop"` and inherits `ship.s6-s9`.
  Its shared primitives include linked-worktree preflight, GitHub transport, reviewer
  isolation, CI re-checks, fix-loop behavior, summary comments, and operator consent. Its
  merge behavior is a handoff, not an in-command merge.
- `review-cycle` uses `workflow_profile.profile: "review-feedback"` and inherits
  `ship.s7-s9`. Its shared primitives include multi-PR targets, reviewer isolation,
  posting mode, the severity histogram, fix-loop behavior, completion markers, and
  operator consent. It never merges or posts formal approval.
- `coverage`, `deps-audit`, and `flake-audit` use `workflow_profile.profile: "reporting"`.
  Their shared primitives include project policy, GitHub transport, codename anchors,
  dedupe, dry-run no-mutation guarantees, ship handoff, and operator consent.

## Feedback workflows

`pr-loop` and `review-cycle` share ship's reviewer-isolation and fix-loop primitives, but
their behavior is represented separately in the `feedback_workflow` contract:

- `posting_mode` and `posting_owner` make comment ownership explicit.
- `reviewer_isolation` carries the shared no-cross-reading rule plus a command-specific
  codename prefix.
- `ci`, `review`, and `fix_loop` record command-specific re-check, reviewer fan-out,
  histogram, degradation, and budget semantics.
- `completion` records whether the command hands off, never merges, posts a summary, or
  applies a completion marker such as `review-cycle-complete`.

Projects can override these values through `policy_pack.workflow_policies` without
changing the packaged command bodies.
- `regression` uses `workflow_profile.profile: "scan-and-file"`. Its shared primitives
  include canonical base scanning, clean-tree preflight, read-only worktrees, area fan-out,
  reviewer isolation, confidence filtering, dedupe, issue locking, issue creation, ship
  handoff, final reports, and operator consent.
- `review-all-day` uses `workflow_profile.profile: "time-window-scan"`. Its shared
  primitives include merge-window spans, remote ref scope, batch/fan-out selection, reviewer
  isolation, diff truncation, finding filtering, dedupe, title-prefix issue creation, final
  reports, and operator consent.

## Adapter rules

Adapters must:

- treat every command step as contractual: complete it, record the requested evidence, or
  mark it `N/A — <reason>` before moving on
- post or write required external side effects through the selected transport, including PR
  bodies, review summaries, jury verdicts, issues, comments, reports, branches, and release
  artifacts
- reject local/chat-only evidence for steps that require public GitHub or file-system output
- read the contract before mutating files or GitHub state
- stop when required capabilities are missing
- report optional capability degradation explicitly
- use `github_transport` instead of duplicating `gh` vs MCP mapping tables
- stop before live mutation when `operator_consent.requires_operator_consent` is true
- pass `operator_consent.delegated_agent_scope` to delegated agents before work starts
- block or escalate if a delegated agent attempts work outside `approved_mutation_scopes`
- never infer secret or credential approval from project knowledge; require the `secrets` scope
- preserve `no_mutations: true` under dry-run
- use `extension_hooks` and `gates` from the contract rather than reparsing project config

Projects can still declare project-specific policy in config and extensions, but the
contract shape itself stays consumer-neutral.

## Command Step Evidence

Generated command surfaces include a command-step evidence rule so autonomous agents cannot
silently skip lifecycle steps. The rule is intentionally generic: a step is done only when the
agent completed the work, recorded the evidence requested by the command, or explicitly marked
the step not applicable with a reason. Mutating commands must make required side effects
observable through GitHub, git, or the configured report path before moving forward.

For `ship` and `ship-v2`, this means a PR body is not valid when it contains only a closing
reference. It must include Context, Changes Made, Testing, Docs Impact, and the closing issue
reference. If review or jury ran, the orchestrator must post the final reviewer verdicts and
the single jury summary/verdict to the GitHub PR. A local transcript or chat-only note is not
merge evidence.
