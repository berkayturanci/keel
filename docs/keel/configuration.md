# `project.yaml` reference

A keel consumer holds exactly one `project.yaml` (plus its `.keel/extensions/`).
It is validated against the bundled JSON Schema (`src/keel/schema/project.schema.json`)
by `keel validate`. Unknown keys are rejected, so typos fail loudly.

## How to read this reference

Each field below is validated by the bundled schema. Unknown keys are rejected. Paths and
commands are project-owned values; Keel core reads them to plan, validate, classify, or
preflight work, but project-specific behavior stays in config, extension files, or
project-provided commands.

Required fields are required by schema validation. Optional fields may still be required by
a specific command or extension policy at runtime; those runtime requirements should be
declared through `required_capabilities`, `policy_pack`, or extension docs.

## Top-level fields

| field | type | required | description |
|---|---|---|---|
| `extends` | `"keel"` (const) | ✅ | marks the file as a keel consumer config |
| `core_version` | string | ✅ | pinned keel core range, e.g. `^1.0` |
| `base_branch` | string | ✅ | branch PRs target (`develop`, `main`, …) |
| `knobs` | object | ✅ | per-project values (see below) |
| `owner` | string | | GitHub owner |
| `repo` | string | | GitHub repo |
| `platform` | string | | free-form tag for the consumer's runtime family |
| `timezone` | string | | IANA tz for the merge window (`Europe/Istanbul`, `Etc/GMT-3`) |
| `merge_window` | string `HH:MM-HH:MM` | | open merge window; the complement is the night no-merge window |
| `merge_window_mode` | `freeze` \| `pause` | `freeze` | outside the window: `freeze` blocks the merge but keeps gates/CI running; `pause` halts the pipeline |
| `consent_mode` | `explicit` \| `standing` \| `agent` | `explicit` | default live-run consent mode for every command |
| `gates` | string[] | | built-in gates to run: any of `build`, `lint`, `jury` |
| `extensions` | object | | add-only Lego pieces keyed by named slot |
| `extensions_dir` | string | | dir holding extension files (default `.keel/extensions`) |
| `policy_pack` | object | | durable project-owned policy data (see below) |

### Top-level field details

#### `extends`

Must be `keel`. This is the schema marker that tells tools the file consumes the Keel
backbone.

#### `core_version`

The selected Keel core version range for this consumer, for example `^1.0`. Humans and
adapters use it to keep installed command surfaces aligned with the expected core contract.

#### `owner` and `repo`

Optional GitHub repository coordinates. Commands that read or write GitHub state use these
when present; otherwise they may infer the repository from the local git remote or the
selected GitHub transport.

#### `base_branch`

The branch that implementation work is forked from and PRs target. Ship, wrap, regression,
review-all-day, and CI assessment all use it when computing diffs or deciding whether a
branch is in scope.

#### `platform`

A free-form consumer runtime tag. It is informational and useful in generated plans,
reports, and docs; it must stay generic enough that core behavior does not branch on a
specific product.

#### `timezone` and `merge_window`

`timezone` is an IANA timezone used to evaluate `merge_window`. `merge_window` is the open
merge interval in `HH:MM-HH:MM` format and may wrap midnight. `keel window` and `keel ship`
use both values to decide whether a merge may proceed.

#### `merge_window_mode`

Controls behavior outside the merge window:

- `freeze` keeps non-merge work moving but blocks the merge decision.
- `pause` halts the pipeline outside the window.

If omitted, Keel defaults to `freeze`.

#### `consent_mode`

Default operator-consent mode for live command preflight. The built-in default is
`explicit`, and per-run inputs override it in this order:

1. `--consent-mode explicit|standing|agent`
2. `KEEL_CONSENT_MODE`
3. `consent_mode` in `.keel/project.yaml`
4. built-in `explicit`

Modes:

- `explicit` requires the current run to pass `--approve-scope` for any live mutation
  scopes.
- `standing` allows trusted unattended approval from `KEEL_APPROVE_SCOPE` or
  `automation.approved_scopes`, with an operator identity.
- `agent` delegates prompting/enforcement to the host agent permission model. Keel still
  emits the structured consent contract and delegated scope, but it does not double-prompt
  or fail the preflight for missing `--approve-scope`.

No mode bypasses findings, CI, project gates, merge windows, merge locks, or release
policy. Read-only live contracts do not consume standing approvals, so stale or invalid
standing approval environment values do not break read-only checks.

#### `gates`

Lists built-in gates that should run in the test stage. Current built-in gates are
`build`, `lint`, and `jury`. Unknown gate names are rejected by command execution rather
than treated as project-specific code. Project-specific gates should be declared as
extensions or `policy_pack.test_groups`.

#### `extensions`

Maps a named backbone hook to a list of extension file names under `extensions_dir`.
Extensions are add-only: they can add project gates, prompts, reports, or checks at the
hook, but they must not reorder the backbone.

#### `extensions_dir`

Directory used to resolve extension file names. The default convention is
`.keel/extensions`.

#### `policy_pack`

Durable project-owned policy data. Keel can validate, plan, and expose it in command
contracts, but executable project behavior remains in extension files or project commands.

## `knobs`

| knob | type | required | description |
|---|---|---|---|
| `build_gate_cmd` | string | ✅ | command the `build` gate runs |
| `lint_cmd` | string | | command the `lint` gate runs (gate skipped if absent) |
| `implementer_agents` | map role→agent | | role to local agent mapping |
| `tier3_globs` | string[] | | high-risk paths that force full scrutiny |
| `ci_workflows` | map name→glob | | CI workflow display name → gating path glob |
| `docs_gate_paths` | string[] | | paths that trigger the docs gate |
| `docs_only_allowlist` | string[] | | paths allowed in a docs-only PR |
| `sot_doc` | string | | source-of-truth doc, e.g. `AGENTS.md` |
| `required_capabilities` | string[] | | runtime capabilities that must be present before mutating work starts |
| `optional_capabilities` | string[] | | runtime capabilities that may degrade explicitly when unavailable |
| `evidence_gate_label` | string | | PR label that opts a PR into the required pre-merge evidence gate (default `keel:ship`); `keel:ship` applies it at PR open |

### `knobs` field details

#### `build_gate_cmd`

Command run by the built-in `build` gate. This is required because the build/test gate is
the minimum deterministic project health check.

#### `lint_cmd`

Command run by the built-in `lint` gate. If absent, the lint gate is skipped.

#### `implementer_agents`

Map from a role label or project role to the local implementer agent name. `keel ship` and
`keel implement` use it when choosing the implementation delegate.

#### `tier3_globs`

Path globs that mark a diff as high risk. `keel ship` uses them to choose the strongest
review posture, including the maximum reviewer count and auto-jury behavior when enabled
by the command policy.

#### `ci_workflows`

Map of GitHub check or workflow display name to a path glob. `keel ship --pr` uses this
mapping to decide which CI checks are relevant to a PR's changed files.

#### `docs_gate_paths`

Paths that count as docs-gate surfaces. Ship uses them to classify docs-only changes and
to decide when an empty CI check set may be acceptable.

#### `docs_only_allowlist`

Paths that are allowed in a docs-only PR. Use this to include related generated docs
artifacts, site files, or metadata that should not force code-risk classification.

#### `sot_doc`

Source-of-truth project instructions file, for example `AGENTS.md`. Adapters and reviewers
use it as the first project policy reference.

#### `required_capabilities`

Runtime capabilities that must be present before live mutation begins. Examples include
`shell`, `git`, `worktree`, `gh`, or `github-mcp`. `keel capabilities` and live command
preflight evaluate these declarations.

#### `optional_capabilities`

Runtime capabilities that improve behavior but can degrade explicitly. Missing optional
capabilities are reported as degraded rather than silently treated as success.

#### `evidence_gate_label`

The PR label that opts a PR into the required pre-merge evidence gate enforced by
`keel evidence-verify` (default `keel:ship`). `keel:ship` applies this label when it opens
the PR, so ship-driven PRs are gated while hand-authored PRs that lack the label pass with
`enforced: false` and `required: 0`. Override per run with `keel evidence-verify
--gate-label`.

## `policy_pack`

`policy_pack` is the durable project policy contract. It is data, not executable logic:
commands can read it during planning and dry-run reporting, while command execution,
custom prompts, and project-owned gates still live in extension slots or project commands.

If `policy_pack` is present, `name` is required and unknown fields are rejected. This makes
missing or misspelled project policy fail during `keel validate` instead of silently falling
back to packaged command prose.

| field | type | required | description |
|---|---|---|---|
| `name` | string | ✅ | stable id for this project's policy pack |
| `labels` | map group→string[] | | label vocabularies such as status, priority, role, type, or command-specific groups |
| `status_transitions` | map transition→label | | lifecycle transition targets |
| `risk_rules` | object[] | | high-risk path rules with extra gate, review, or docs expectations |
| `test_groups` | map name→object | | named test/audit commands, path selectors, reports, and capability needs |
| `docs` | object | | docs gate policy and allowed no-docs reasons |
| `health_providers` | map name→object | | project-owned operational signal providers for reporting commands |
| `scan` | object | | project-owned area/module, branch, dedupe threshold, and label policy for scan-and-file commands |
| `project_commands` | map command→object | | project-provided commands, path selectors, capability needs, and side effects |
| `command_routing` | map command→object | | compatibility routing map for older project command declarations |
| `workflow_policies` | map command→object | | command-specific workflow policy such as posting mode, reviewer isolation, CI/fix-loop behavior, and completion markers |
| `reports` | map name→string | | report destinations, paths, or issue prefixes |
| `capture_redaction` | object | | additional project-owned deny regexes applied before capture artifacts are persisted |
| `capture` | object | | post-merge capture enablement/mode; content and destinations remain extension-owned |
| `review` | object | | project-owned rubric additions and required PR/review sections |

## `automation`

Trusted unattended-run consent defaults. Env approval is preferred for CI/cron because it
keeps authorization outside the repository, but config approval is useful when a project
wants an explicit auditable policy.

| field | type | required | description |
|---|---|---|---|
| `approved_scopes` | string[] | | standing consent scopes such as `filesystem`, `git`, and `github` |
| `operator` | string | runtime-required with `approved_scopes` | automation identity recorded in `consent_record` when config approval is used |

`automation.approved_scopes` only satisfies the consent preflight. It never bypasses
findings, CI, project gates, merge windows, or merge locks. Approval is least-privilege:
any required scope not listed here still blocks the live run.
If `approved_scopes` is selected by a live mutating `standing` run without `operator`,
preflight fails before mutation.

Example:

```yaml
automation:
  approved_scopes: [filesystem, git, github]
  operator: automation:nightly
```

### `policy_pack.name`

Stable identifier for this policy pack. It is required whenever `policy_pack` is present
and helps generated plans distinguish the consumer policy from Keel core.

### `policy_pack.labels`

Map from label group to allowed label names. Common groups include `status`, `priority`,
`role`, `type`, or command-specific groups. Triage, ship, regression, and closeout flows
can use these vocabularies instead of hardcoding labels in command bodies.

### `policy_pack.status_transitions`

Map from lifecycle transition name to the label or state target. Examples include
`start`, `review`, and `done`. Ship-compatible adapters use this to move work through the
project's issue lifecycle without embedding project label names in core.

### `policy_pack.capture_redaction`

Capture artifacts are sanitized by default before they are persisted or handed to durable
learning tooling. Core redacts common credential shapes such as bearer tokens, GitHub tokens,
private-key blocks, credential-bearing URLs, and token/password-style assignments. Projects can
add organization-specific deny regexes without putting those patterns in keel core:

```yaml
policy_pack:
  name: example
  capture_redaction:
    deny_patterns:
      - id: private-host
        pattern: 'internal\.example\.test'
      - id: org-ticket-url
        pattern: 'https://tickets\.example\.test/[A-Z]+-[0-9]+'
        replacement: '[REDACTED:org-ticket-url]'
```

The redaction audit records rule ids and replacement counts only; it never records the original
matched value. Invalid configured regexes make the capture write skip/fail with an explicit
reason before the artifact is written. Redaction is a safety layer, not a complete DLP system:
projects should still avoid sending raw secrets, full CI logs, or private production data into
capture extensions.

### `policy_pack.capture`

Core owns the post-merge capture mechanics: the stable marker, fail-soft semantics,
recursion guard, redaction-before-durability requirement, and the offline session-end
verifier. Projects own the content and destination by declaring a `capture` or `post-merge`
extension.

```yaml
policy_pack:
  name: example
  capture:
    enabled: true
    mode: extension
```

`mode: extension` means a project hook can produce the learning content after the core
checks and records the marker. `mode: marker-only` records the core marker without running
a project content hook. The marker format is:

```text
compound-learning: pr=<N> status=<applied|deferred|skipped:reason>
```

Allowed skip reasons are `dry-run`, `deferred`, `merge-failed`, `recursion-guard`,
`capability-unavailable`, and `no-policy`. Capture failures after a successful merge are
fail-soft: the merge is not reverted, but the marker and ledger must record the applied,
deferred, or allowed skipped state so `keel capture-verify` can surface gaps.

### `policy_pack.risk_rules`

Array of high-risk policy rules. Each entry requires:

| field | type | required | used for |
|---|---|---|---|
| `id` | string | ✅ | stable name shown in plans and review context |
| `paths` | string[] | ✅ | path globs that activate the rule |
| `required_gates` | string[] | | extra gate names expected for matching changes |
| `review_additions` | string[] | | project-specific review checklist text |
| `docs_required` | boolean | | whether matching changes must update docs |

Use `risk_rules` for project-owned elevated scrutiny beyond generic `tier3_globs`.

### `policy_pack.test_groups`

Map from test group name to a test command contract. Each group requires `command`.

| field | type | required | used for |
|---|---|---|---|
| `command` | string | ✅ | runnable project test/audit command |
| `paths` | string[] | | path selectors that make the group relevant |
| `reports` | string[] | | report paths or destinations produced by the command |
| `required_capabilities` | string[] | | capabilities needed before the command can run |
| `optional_capabilities` | string[] | | capabilities that may degrade when unavailable |

Commands such as ship, coverage, deps-audit, and flake-audit can surface these groups in
plans and test guidance.

### `policy_pack.docs`

Documentation policy used by docs gates and reviewers.

| field | type | used for |
|---|---|---|
| `required_paths` | string[] | docs surfaces expected when behavior or contracts change |
| `allow_none_reasons` | string[] | approved reasons for `Docs Impact: none` |
| `impact_required` | boolean | whether PR bodies must state docs impact |

### `policy_pack.health_providers`

Map from health provider name to metadata used by reporting commands such as `morning`.
Each provider requires `kind`.

| field | type | required | used for |
|---|---|---|---|
| `kind` | string | ✅ | provider type, for example `github-checks` or `project-command` |
| `command` | string | | project command to execute when the provider is command-backed |
| `reports` | string[] | | report sources or destinations |
| `required_capabilities` | string[] | | hard runtime requirements |
| `optional_capabilities` | string[] | | degraded-but-allowed runtime requirements |

### `policy_pack.scan`

Project-owned scan scope for `regression` and `review-all-day`.

| field | type | used for |
|---|---|---|
| `areas` | map name→string[] | module/path fan-out groups for scan reviewers |
| `active_branch_patterns` | string[] | branch globs considered active work during time-window scans |
| `issue_labels` | map command→string[] | labels for issues opened by scan commands |
| `near_text_similarity` | number 0..1 | deterministic duplicate-finding threshold |
| `batch_threshold` | integer | commit count threshold before batch/fan-out behavior |
| `large_diff_max_bytes` | integer | max diff bytes before file-boundary truncation |

### `policy_pack.command_routing`

Compatibility map for older project command declarations. Prefer `project_commands` for
new configs. Each command entry may include:

| field | type | used for |
|---|---|---|
| `command` | string | project command path or invocation |
| `description` | string | human-facing description shown in command lists |
| `agent_role` | string | role used for implementer selection |
| `paths` | string[] | path selectors for relevance |
| `required_capabilities` | string[] | hard runtime requirements |
| `optional_capabilities` | string[] | degraded runtime requirements |
| `side_effects` | string[] | declared writes, pushes, reports, or external effects |
| `dry_run_safe` | boolean | whether the command can run during dry-run contexts |

### `policy_pack.project_commands`

Preferred map for project-provided commands that Keel should preserve without owning their
bodies. The subfields are the same as `command_routing`. `keel project-commands` lists
these commands, and `keel plan --command <name> --json` emits their structured contract.

### `policy_pack.workflow_policies`

Map from Keel command name to command-specific workflow behavior. This keeps compatibility
semantics explicit without forking packaged adapters.

Supported sub-objects:

| field | type | used for |
|---|---|---|
| `posting_mode` | `inline` \| `summary` | default review/comment posting mode |
| `posting_owner` | `orchestrator` \| `reviewer` | who owns GitHub writes |
| `reviewer_isolation` | object | reviewer no-cross-reading and codename policy |
| `inputs` | map string→boolean | supported command input behavior |
| `ci` | map string→boolean | CI recheck and degradation behavior |
| `review` | map string→boolean | review fan-out and summary behavior |
| `fix_loop` | map string→boolean/integer/string | fix-loop enablement and budget |
| `completion` | map string→boolean/string/null | marker, merge, approval, or summary behavior |

`reviewer_isolation` supports:

| field | type | used for |
|---|---|---|
| `shared_with_ship` | boolean | whether the policy mirrors ship reviewer isolation |
| `codename_prefix` | string | stable prefix for reviewer codenames |
| `no_cross_reading` | boolean | whether reviewers must avoid reading each other's comments |

### `policy_pack.reports`

Map from report name to a path, destination, or issue prefix. Commands such as `morning`,
`overnight`, `wrap`, and `ship` use report destinations to avoid inventing
project-specific files.

### `policy_pack.review`

Project-owned review policy.

| field | type | used for |
|---|---|---|
| `additions` | string[] | extra reviewer rubric items |
| `required_sections` | string[] | PR/review body sections that must be present |

`risk_rules[]` entries require `id` and `paths`. `test_groups.*` entries require
`command`. `health_providers.*` entries require `kind`. These required fields are
validated by the bundled schema.

`health_providers` are used by reporting commands such as `keel morning`. Core reads their
metadata and declared capabilities, but provider execution remains project-owned. If a
provider only has optional capabilities and those capabilities are unavailable, morning
marks that provider `unavailable` instead of treating the missing signal as success.
`reports` can declare destinations such as `morning`, `priorities`, or `deferrals`; the
`deferrals` entry is the shared queue contract surfaced by ship, overnight, wrap, and
morning adapters.
`wrap` reads `session` or `wrap` report destinations for recap output. `overnight` reads
`overnight`, `morning`, and `session` destinations to choose the night report or day
session report path. Missing report destinations degrade as unconfigured in preflight
output; core does not invent project-specific paths.

`run_ledger` is the optional structured run-history path. When absent, keel uses
`.keel/state/run-ledger.jsonl`. When present, `keel plan --json`, `keel ship --json`,
`keel ledger`, `morning`, `wrap`, and overnight contracts all resolve the same path.
The file is JSONL with schema `keel.run-ledger.v1`; missing files are treated as empty
history, while malformed records are errors.

`checkpoint` is the optional resumable-run state path. When absent, keel uses
`.keel/state/checkpoint.json`. When present, `keel plan --json`, `keel checkpoint`,
`keel resume`, and work-owning adapter contracts all resolve the same path. The file is
a single JSON record with schema `keel.checkpoint.v1`; missing files mean there is no
active resumable run, while malformed records are errors.

`scan` is used by `keel regression` and `keel review-all-day`. Core owns the generic
scan-and-file contract, while projects own the module list, active work branch patterns,
issue labels, and thresholds:

```yaml
policy_pack:
  name: example
  scan:
    areas:
      app: ["app/**"]
      service: ["service/**"]
      workflows: [".github/workflows/**"]
    active_branch_patterns: ["feature/**", "fix/**", "chore/**"]
    issue_labels:
      regression: ["type:bug", "source:regression-scan"]
      review-all-day: ["type:bug", "source:review-all-day"]
    near_text_similarity: 0.6
    batch_threshold: 5
    large_diff_max_bytes: 200000
```

`areas` drives regression fan-out and remains project-specific. `active_branch_patterns`
drives review-all-day's active branch scope. `near_text_similarity` is the deterministic
dedupe threshold. Review-all-day's issue title prefix is intentionally core-owned and fixed
as `[review-all-day] ` so issue searches and created titles stay parity-safe.

`project_commands` is the preferred place to preserve local commands that keel should not
own. Keel can list them, include them in structured command contracts, and evaluate their
declared capabilities; the command body itself remains in the project:

```yaml
policy_pack:
  name: example
  project_commands:
    device-smoke:
      command: ".keel/commands/device-smoke"
      description: "Run the project's smoke-test checklist."
      agent_role: app
      paths: ["app/**"]
      required_capabilities: [shell]
      optional_capabilities: [browser]
      side_effects: [report_write]
      dry_run_safe: false
```

`workflow_policies` preserves command-specific workflow semantics that should be explicit
project policy rather than hidden in adapter prose. It is especially useful for feedback
commands that share ship primitives but do not share ship's full lifecycle:

```yaml
policy_pack:
  name: example
  workflow_policies:
    pr-loop:
      posting_mode: summary
      posting_owner: orchestrator
      reviewer_isolation:
        shared_with_ship: true
        codename_prefix: PR-LOOP
        no_cross_reading: true
      ci:
        recheck_after_push: true
        green_required_to_exit: true
        degrade_when_logs_unavailable: true
      fix_loop:
        budget: 3
      completion:
        merge: handoff
        summary_comment: true
    review-cycle:
      posting_mode: inline
      posting_owner: reviewer
      reviewer_isolation:
        shared_with_ship: true
        codename_prefix: REVIEW-CYCLE
        no_cross_reading: true
      review:
        parallel_reviewers_within_pr: true
        severity_histogram_source_of_truth: true
      completion:
        marker: review-cycle-complete
        marker_after_summary: true
        merge: never
        formal_approval: never
```

## `gates` vs `extensions`

- **`gates`** lists which **built-in** gates run (`build` / `lint` / `jury`). An unknown
  name here is an error.
- **`extensions`** registers **project-provided** gates/steps (Lego pieces) into named
  backbone slots. They are add-only and run at their slot's step. See
  [extensions.md](extensions.md).

Keel core stays consumer-neutral: project-specific labels, path globs, commands, health
signals, and manual playbooks belong in config, extensions, or project-provided commands.
The boundary is documented in [consumer-neutrality.md](consumer-neutrality.md).

## Extension hooks

Every extension hook key maps to a list of extension file names under `extensions_dir`.
The schema currently accepts these hooks:

| hook | backbone location | typical use |
|---|---|---|
| `after:config` | after s0 config | config reports or environment preflight notes |
| `before:select` | before s1 select | queue filters or backlog guards |
| `select` | during s1 select | project-owned selection policy |
| `after:select` | after s1 select | selected-issue reporting |
| `before:branch` | before s2 branch | branch naming or worktree guards |
| `after:branch` | after s2 branch | branch metadata capture |
| `guard` | s3 guard | project-specific blockers and preflight checks |
| `before:implement` | before s4 implement | implementation briefs or setup |
| `after-implement` | after s4 implement | generated-output checks |
| `classify` | during s5 classify | extra risk classification |
| `after:classify` | after s5 classify | risk reporting |
| `before:ci` | before s6 CI | CI preflight |
| `after:ci` | after s6 CI | CI summary capture |
| `reviewers` | s7 review | additional reviewer prompts or reviewer gates |
| `after:review` | after s7 review | review summary or posting checks |
| `tester` | s8 test | manual or agentic tester guidance |
| `test` | s8 test | project-owned deterministic tests |
| `after:test` | after s8 test | test report capture |
| `before:fixloop` | before s9 fixloop | fix-loop guardrails |
| `fixloop` | during s9 fixloop | project-specific fix policy |
| `after:fixloop` | after s9 fixloop | fix-loop summary |
| `pre-merge` | before s10 merge | blocking gates that must pass before merge |
| `after:merge` | after s10 merge | post-merge verification |
| `capture` | s11 capture | knowledge/session capture |
| `post-merge` | s11 capture | compatibility hook for post-merge capture |
| `before:close` | before s12 close | issue-close preflight |
| `on-close` | during s12 close | closeout comments or labels |
| `after:close` | after s12 close | final reporting |

Blocking policy should be explicit. Use `pre-merge` for gates that must block a merge, and
document any earlier hook that can stop a live run.

## Example

```yaml
extends: keel
core_version: "^1.0"
owner: example-owner
repo: example-repo
base_branch: main
platform: example-runtime
timezone: Europe/Istanbul
merge_window: "07:00-01:30"

knobs:
  implementer_agents:
    app: app-developer
    service: service-developer
  build_gate_cmd: "./tools/build-check"
  lint_cmd: "./tools/lint-check"
  tier3_globs: ["migrations/**", "src/**/critical/**"]
  ci_workflows:
    "App CI": "src/app/**"
    "Service CI": "src/service/**"
  sot_doc: AGENTS.md
  required_capabilities: [shell]
  optional_capabilities: [gh, gh-auth]

gates: [build, lint]

extensions:
  tester: [design-parity.md]
  pre-merge: [design-parity-gate.md]
extensions_dir: .keel/extensions

policy_pack:
  name: example-service
  labels:
    status: ["status:backlog", "status:in-progress", "status:done"]
    priority: ["priority:high", "priority:medium", "priority:low"]
    role: ["app", "service"]
  status_transitions:
    start: "status:in-progress"
    done: "status:done"
  risk_rules:
    - id: data-migration
      paths: ["migrations/**"]
      required_gates: ["build", "lint", "migration-check"]
      review_additions: ["Check upgrade and rollback safety."]
      docs_required: true
  test_groups:
    app:
      command: "./tools/test-app"
      paths: ["src/app/**"]
      reports: ["reports/app-tests/"]
      required_capabilities: [shell]
  docs:
    required_paths: ["docs/**"]
    allow_none_reasons: ["No operator-facing behavior changed."]
    impact_required: true
  health_providers:
    service-health:
      kind: project-command
      command: ".keel/health/service-summary"
      optional_capabilities: [shell]
  command_routing:
    smoke:
      agent_role: app
      paths: ["src/app/**"]
      required_capabilities: [shell]
      side_effects: ["report_write"]
      dry_run_safe: true
  reports:
    morning: "reports/morning/"
  review:
    additions: ["Check the project-specific rollout notes."]
    required_sections: ["Testing", "Docs Impact"]
```

The other seed configs live in [`projects/`](../../projects/).

## Determinism

`keel.config.config_hash(config)` is a stable SHA-256 over the canonicalised config —
key order in the YAML does not affect it. Use it as a cache key.
