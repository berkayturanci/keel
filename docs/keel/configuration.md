# `project.yaml` reference

A keel consumer holds exactly one `project.yaml` (plus its `.keel/extensions/`).
It is validated against the bundled JSON Schema (`src/keel/schema/project.schema.json`)
by `keel validate`. Unknown keys are rejected, so typos fail loudly.

## Top-level fields

| field | type | required | description |
|---|---|---|---|
| `extends` | `"keel"` (const) | ✅ | marks the file as a keel consumer config |
| `core_version` | string | ✅ | pinned keel core range, e.g. `^0.5` |
| `base_branch` | string | ✅ | branch PRs target (`develop`, `main`, …) |
| `knobs` | object | ✅ | per-project values (see below) |
| `owner` | string | | GitHub owner |
| `repo` | string | | GitHub repo |
| `platform` | string | | free-form tag for the consumer's runtime family |
| `timezone` | string | | IANA tz for the merge window (`Europe/Istanbul`, `Etc/GMT-3`) |
| `merge_window` | string `HH:MM-HH:MM` | | open merge window; the complement is the night no-merge window |
| `merge_window_mode` | `freeze` \| `pause` | `freeze` | outside the window: `freeze` blocks the merge but keeps gates/CI running; `pause` halts the pipeline |
| `gates` | string[] | | built-in gates to run: any of `build`, `lint`, `jury` |
| `extensions` | object | | add-only Lego pieces keyed by named slot |
| `extensions_dir` | string | | dir holding extension files (default `.keel/extensions`) |
| `policy_pack` | object | | durable project-owned policy data (see below) |

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
| `command_routing` | map command→object | | project command routing, path selectors, capability needs, and side effects |
| `reports` | map name→string | | report destinations, paths, or issue prefixes |
| `review` | object | | project-owned rubric additions and required PR/review sections |

`risk_rules[]` entries require `id` and `paths`. `test_groups.*` entries require
`command`. `health_providers.*` entries require `kind`. These required fields are
validated by the bundled schema.

## `gates` vs `extensions`

- **`gates`** lists which **built-in** gates run (`build` / `lint` / `jury`). An unknown
  name here is an error.
- **`extensions`** registers **project-provided** gates/steps (Lego pieces) into named
  backbone slots. They are add-only and run at their slot's step. See
  [extensions.md](extensions.md).

Keel core stays consumer-neutral: project-specific labels, path globs, commands, health
signals, and manual playbooks belong in config, extensions, or project-provided commands.
The boundary is documented in [consumer-neutrality.md](consumer-neutrality.md).

## Example

```yaml
extends: keel
core_version: "^0.5"
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
