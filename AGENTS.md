# AGENTS.md — keel

> Cross-AI agent configuration for the **keel** repository — the canonical, durable
> instruction file. Compatible with Claude Code, Codex/OpenAI, Gemini, Antigravity, and
> any other AI coding assistant. Tool-specific entry points (e.g. `CLAUDE.md`) are thin
> pointers to this file; keep durable rules here, not duplicated in them.

**keel** is a project-neutral, multi-agent **workflow core**. A *fixed backbone* of
ordered steps (`s0`–`s12`) drives a unit of work — a GitHub issue — from backlog to done.
Projects never fork the backbone: they set per-project **values** in `project.yaml` and
snap their own **Lego pieces** into named extension slots. Full design:
[`docs/proposals/keel-architecture.md`](docs/proposals/keel-architecture.md).

## Quick Reference

The rules you will hit most often. Details follow below.

- **Never change the backbone for a single project.** The step machine (`s0`–`s12`) and
  its slots live in `src/keel/model.py` — the *single source of truth*. Changing it is a
  **keel-core** change with strong justification; projects only touch layers 2–3 (config
  + extensions). If you are about to edit `model.py` to make one project happy, stop.
- **Pure core, thin I/O.** Network/subprocess/filesystem code stays in the thin
  fail-soft wrappers (`runner`, `git`, `github`, `lock`). The pure modules (`config`,
  `model`, `extensions`, `findings`, `gates`, `orchestrator`, `classify`, `ship`,
  `window`, `cli`) must stay deterministic and side-effect-free.
- **Coverage bar is non-negotiable.** The pure core is held at **100 % line + branch**;
  the CI gate is `fail_under = 100` (`pyproject.toml`). New core code ships with tests
  that keep it at 100 %.
- **Stdlib-first.** Exactly one runtime dependency on Linux/macOS: **PyYAML**. Do not add
  another runtime dep without an explicit, discussed reason — `jsonschema_min` is a
  hand-rolled validator precisely to avoid pulling `jsonschema`. The sole platform
  exception is **`tzdata` on Windows only** (`sys_platform == 'win32'`): Windows has no
  system IANA zoneinfo database, so the stdlib `zoneinfo` used by the merge-window logic
  needs that pure-data package there. It is never installed on Linux/macOS.
- **Determinism.** No wall-clock or randomness in the pure core. Plans, `config_hash`,
  and ship decisions must be reproducible for identical inputs.
- **Operator consent is emit-only in core.** keel core only **emits** the operator-consent
  contract and fails closed on its own preflight; actual **enforcement depends on the
  adapter** honoring it, because the deterministic core never performs the live mutation
  itself (see `docs/keel/operator-consent.md`).
- **Before every push:** `make test` and `make lint` must pass; `make validate` must
  pass if any `projects/*.yaml` or the schema changed.
- **Language:** all repo artifacts (code, comments, commits, PR/issue bodies, Markdown)
  in **English**. Free-form chat may be any language.
- **Commit format:** `type(scope): short description` — `feat` / `fix` / `refactor` /
  `test` / `chore` / `docs` / `ci`. Scope is usually `keel`.
- **The work is issue-driven.** Ship changes through keel's own backbone: an issue →
  branch → implement → gates → review → merge. keel dogfoods itself (see below).

## Commands

```bash
make test       # offline unit suite (no network, no credentials)
make lint       # ruff check .
make coverage   # run tests under coverage + enforce the gate
make validate   # validate every projects/*.yaml against the bundled schema
make site       # build the coverage report into website/ and serve at :8000
```

Run a single module's tests: `PYTHONPATH=src python3 -m unittest tests.test_<module> -v`.
The CLI entry point is `python3 -m keel …` (or the installed `keel` script) — **not**
`python3 -m keel.cli`, which has no `__main__` guard and silently no-ops. `ruff` lives in
the `dev` extra (`pip install -e ".[dev]"`).

CLI surface: `keel version | validate | plan | run-gates | window | ship`, plus
`keel init [--wizard]` (golden-path scaffolder). `keel plan <project.yaml>` renders the
fixed backbone with that project's gates/extensions slotted in — the dry-run view.

## Architecture — three layers

```
Layer 3  EXTENSIONS   project-owned Lego pieces, ADD-ONLY into named slots
Layer 2  CONFIG       project.yaml — per-project values (branch, build cmd, globs, agents…)
Layer 1  BACKBONE     keel-core — fixed ordered step machine + invariants (this package)
```

Changing the backbone is a keel-core change; projects only ever touch layers 2–3.

### The backbone (`src/keel/model.py`)

Steps `s0`–`s12`: config, select, branch, guard, **implement**, classify, ci,
**review**, **test**, fixloop, **merge**, capture, close. Every step exposes one or more
add-only extension hooks; `s0 config` is loader-only except for read-only `after:config`.
The compatibility slots `after-implement`, `reviewers`, `tester`, `pre-merge`, and
`post-merge` remain valid. The full hook table lives in
[`docs/keel/extensions.md`](docs/keel/extensions.md).

`SLOTS` and the step IDs are asserted against the bundled schema
(`src/keel/schema/project.schema.json`) — if you add/rename a slot, **update both** or
the consistency tests fail.

### Invariants (`INVARIANTS` in `model.py`) — no config or extension may override

- `merge_lock` — every merge goes through the `mkdir`-based lock.
- `window_gate` — the night no-merge window is enforced (timezone-aware).
- `fail_soft` — a soft failure degrades to a no-op, never aborts the pipeline.
- `orchestrator_only_writes` — only the orchestrator writes to the PR.
- `attribution` — implementer/reviewer vendor + model is recorded.

### Findings → decisions (`src/keel/findings.py`)

`critical` / `major` ⇒ **block** · `minor` ⇒ **suggest** · `nit` ⇒ **advisory**
(logged, never gates). Keep this mapping in sync with the schema and docs.

## Agents (keel-specific)

keel's agentic steps are driven by the **keel:ship adapter**, whose project-neutral source
lives in `src/keel/adapters/commands/` and is generated per host (the plugin `commands/`,
the installed `.claude/commands/keel/`, and `.agents/skills/keel-*`). The adapter is
project-neutral — it reads every project specific from `projects/keel.yaml` (this repo's own
config) via the `keel` CLI and never hardcodes a value. For keel itself:

- **Implementer** — resolved from `knobs.implementer_agents` by the issue's role; keel
  maps `core → backend-developer`. Overridable per run with `--delegate`; defaults to the
  host agent. Attribution (`agent:<vendor>` + versionless `model:<base>`) is recorded.
- **Reviewers** — step s7 dispatches N reviewers, where N is the reviewer count for the
  risk tier from s5 classify (using `knobs.tier3_globs`). Overridable with
  `--review-delegate`.
- **Gates** — s8 runs the built-in `build`/`lint` command gates (`make test` / `make lint`
  from `knobs`) plus any `tester` Lego; s10 runs `pre-merge` gates.

Do not invent per-agent model tiers here — keel routes agents through config, not a fixed
tier table. When adding an agent or changing dispatch, update `projects/keel.yaml` (and
the schema if the contract changes), not this prose.

## High-risk files — look closer

- `src/keel/model.py` — the single source of truth. A change here changes every
  consumer's backbone. Touch only for deliberate keel-core evolution; never to
  accommodate one project.
- `src/keel/schema/project.schema.json` — the public config contract. Schema changes are
  breaking for consumers; version carefully and update `docs/keel/`.
- `src/keel/jsonschema_min.py` — the dependency-free validator. Keep it minimal and
  draft-07-subset correct rather than reaching for an external lib.
- The thin I/O wrappers (`runner`, `git`, `github`, `lock`) — the only place side effects
  are allowed. Keep logic out of them so the pure core stays testable.

## Conventions

- **Add-only extensions.** Lego pieces snap into named hooks; they never remove or
  reorder backbone steps. `on_fail: block` is permitted only in documented blocking hooks:
  `guard`, `tester`, `test`, and `pre-merge`. The loader is fail-soft.
- **Tests mirror modules.** Each `src/keel/<m>.py` has `tests/test_<m>.py`. New behaviour
  comes with tests that hold the core at 100 % line + branch.
- **Dogfooding.** keel drives itself via `projects/keel.yaml`; CI runs `keel` on keel-core
  every push. `keel ship projects/keel.yaml --root .` is the full dry assessment (tier →
  reviewers, window, gates, decision).
- **Docs impact.** When behaviour, config, or the CLI changes, update `docs/keel/` (and
  `README.md` / `CHANGELOG.md`) or state `Docs Impact: none` with a reason.

## Repo layout

```
src/keel/            core package (config, model, extensions, findings, gates, orchestrator, cli, …)
src/keel/schema/     project.schema.json (bundled, package-data)
projects/*.yaml      seed configs — one per consumer project (keel itself dogfoods via projects/keel.yaml)
tests/               unit suite (mirrors src/keel modules)
src/keel/adapters/commands/   project-neutral adapter source (e.g. ship.md), generated per host
commands/            generated Claude-plugin (marketplace) command surface — `/plugin install keel`
.claude/commands/keel/        generated Claude slash-command adapters (per-project install)
.agents/skills/keel-*         generated shared skill adapters for non-Claude agents (per-project install)
docs/                docs (docs/keel/*) + proposals (docs/proposals/*)
website/             static site + live coverage report (make site)
```

## Docs

- [`docs/keel/configuration.md`](docs/keel/configuration.md) — `project.yaml` reference
- [`docs/keel/extensions.md`](docs/keel/extensions.md) — authoring Lego extensions
- [`docs/keel/consumer-neutrality.md`](docs/keel/consumer-neutrality.md) — core vs project policy boundary
- [`docs/keel/parity-matrix.md`](docs/keel/parity-matrix.md) — legacy-to-keel command parity status
- [`docs/keel/runtime-capabilities.md`](docs/keel/runtime-capabilities.md) — runtime capability detection and requirements
- [`docs/keel/github-transport.md`](docs/keel/github-transport.md) — GitHub transport selection contract
- [`docs/keel/command-contracts.md`](docs/keel/command-contracts.md) — structured command plan/result contracts
- [`docs/keel/cli.md`](docs/keel/cli.md) — CLI reference
- [`docs/keel/github-actions.md`](docs/keel/github-actions.md) — run keel on GitHub's runner
- [`docs/proposals/keel-architecture.md`](docs/proposals/keel-architecture.md) — full design
