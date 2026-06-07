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
| `side_effects` | Declared possible live-run side effects and whether dry-run mutates. |
| `operator_consent` | Operator consent requirement, approved mutation scopes, delegated-agent scope, and consent record metadata. |
| `morning_contract` | Present for `morning`; project-neutral daily-brief sections, health providers, report destinations, priority sources, and deferral queue metadata. |
| `session_contract` | Present for `wrap` and `overnight`; project-neutral linked-worktree, gate, PR, merge-window, report, deferral, and ship-handoff metadata. |
| `scan_contract` | Present for `regression` and `review-all-day`; project-neutral scan target, scope, dedupe, issue-write, reviewer-isolation, and final-report metadata. |

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
| `status` | `not-required-dry-run`, `not-required-read-only`, `missing`, or `approved`. |
| `consent_scope` | Mutation classes required for a live run: `filesystem`, `git`, `github`, `secrets`, `release`, `production-adjacent`. |
| `approved_scope` / `effective_approved_scope` / `missing_scope` | Scope approved by the operator, the subset that matches the resolved plan, and any live-run gap. |
| `consent_prompt` | Consumer-neutral prompt generated from the resolved command, target, mode, and scopes. |
| `delegated_agent_scope` | Scope adapters must pass to delegated agents; scope expansion must block or escalate. |
| `consent_record` | Local metadata for approved live runs: timestamp, operator, workflow, target, scopes, mode, and `secret_values_recorded: false`. |

Dry-run contracts do not require approval, but still expose the live scopes that would need
approval. Live contracts with missing consent are preflight blockers and must stop before
files, git state, GitHub state, releases, secrets, or production-adjacent systems are touched.

## Dry-run result records

`keel ship --dry-run --json` and `keel ship-v2 --dry-run --json` add a `result` object
with deterministic data:

- changed files and changed-file count
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
