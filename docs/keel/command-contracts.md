# Command contracts

Keel command contracts are stable JSON records for agent adapters and parity tests. They
describe what a command would do before an adapter starts mutating work.

## Where contracts appear

- `keel plan <project.yaml> --json`
- `keel plan <project.yaml> --command <adapter> --json`
- `keel plan <project.yaml> --command <adapter> --live --json`
- `keel ship <project.yaml> --dry-run --json`
- `keel ship <project.yaml> --live --json`

Human-readable output remains the default. JSON output is the adapter-facing contract.

## Contract envelope

Every contract includes:

| field | meaning |
|---|---|
| `schema_version` | Contract schema identifier. Current value: `keel.command-contract.v1`. |
| `command` | Adapter command being planned. |
| `mode` / `dry_run` / `no_mutations` | Whether this record represents a non-mutating rehearsal. |
| `project` | Resolved project config summary plus stable `config_hash`. |
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

`keel ship --dry-run --json` adds a `result` object with deterministic data:

- changed files and changed-file count
- gate outcomes and normalized findings
- aggregate verdict
- risk tier, reviewer count, window state, CI state, and merge decision

This result contract is intentionally deterministic so parity tests can compare adapter
behavior without creating branches, posting comments, or merging PRs.

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
