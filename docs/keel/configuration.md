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

gates: [build, lint]

extensions:
  tester: [design-parity.md]
  pre-merge: [design-parity-gate.md]
extensions_dir: .keel/extensions
```

The other seed configs live in [`projects/`](../../projects/).

## Determinism

`keel.config.config_hash(config)` is a stable SHA-256 over the canonicalised config —
key order in the YAML does not affect it. Use it as a cache key.
