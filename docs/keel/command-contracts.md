# Command contracts

Keel command contracts are stable JSON records for agent adapters and parity tests. They
describe what a command would do before an adapter starts mutating work.

## Where contracts appear

- `keel plan <project.yaml> --json`
- `keel plan <project.yaml> --command <adapter> --json`
- `keel ship <project.yaml> --dry-run --json`

Human-readable output remains the default. JSON output is the adapter-facing contract.

## Contract envelope

Every contract includes:

| field | meaning |
|---|---|
| `schema_version` | Contract schema identifier. Current value: `keel.command-contract.v1`. |
| `command` | Adapter command being planned. |
| `mode` / `dry_run` / `no_mutations` | Whether this record represents a non-mutating rehearsal. |
| `project` | Resolved project config summary plus stable `config_hash`. |
| `graph` | Command step graph. `ship` and `ship-v2` use the fixed backbone steps; other adapters expose their command-local steps. |
| `backbone_plan` | Fixed keel backbone with gates slotted onto steps. |
| `gates` | Planned gate specs, including kind, phase, failure behavior, source, and capability declarations. |
| `extension_hooks` | Loaded extension hooks grouped by slot. |
| `extension_problems` | Fail-soft extension load problems. |
| `required_capabilities` / `optional_capabilities` | Capability names the adapter should evaluate before mutating work. |
| `capabilities` | Runtime evaluation for the current environment. |
| `github_transport` | Selected GitHub transport and degraded GitHub operation capabilities. |
| `side_effects` | Declared possible live-run side effects and whether dry-run mutates. |

## Dry-run result records

`keel ship --dry-run --json` adds a `result` object with deterministic data:

- changed files and changed-file count
- gate outcomes and normalized findings
- aggregate verdict
- risk tier, reviewer count, window state, CI state, and merge decision

This result contract is intentionally deterministic so parity tests can compare adapter
behavior without creating branches, posting comments, or merging PRs.

## Adapter rules

Adapters should:

- read the contract before mutating files or GitHub state
- stop when required capabilities are missing
- report optional capability degradation explicitly
- use `github_transport` instead of duplicating `gh` vs MCP mapping tables
- preserve `no_mutations: true` under dry-run
- use `extension_hooks` and `gates` from the contract rather than reparsing project config

Projects can still declare project-specific policy in config and extensions, but the
contract shape itself stays consumer-neutral.
