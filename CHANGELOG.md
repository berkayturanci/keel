# Changelog

All notable changes to keel are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); keel adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.6.1] — 2026-06-08

### Added
- **Release verification** (#78) — `scripts/release_smoke.py` installs a local, PyPI, or
  TestPyPI package into a clean virtual environment and verifies the `keel` CLI plus generated
  adapter surfaces; `docs/keel/release.md` documents the repeatable PyPI release runbook.

### Changed
- **Public docs refresh** (#119, #120) — README, website, and the `project.yaml` reference now
  reflect the current `keel-workflow` package identity, `0.6.x` core line, every-step extension
  hooks, runtime capabilities, project commands, workflow policies, and policy-pack fields.
- **GitHub Actions maintenance** (#55) — bumped the grouped workflow actions and pinned the
  updated actions to exact commit SHAs, including checkout, setup-python, CodeQL, Pages actions,
  and the workflows that dogfood Keel's PR assessment.

## [0.6.0] — 2026-06-07

### Added
- **Consumer-neutral core** (#77) — the core carries no downstream project names or
  workflows, with a `tests/test_consumer_neutrality.py` guard enforcing it.
- **Runtime capability detection** (#68) — `keel capabilities` reports the runtime's
  detected capabilities and GitHub transport; configs declare `required_capabilities` /
  `optional_capabilities` (per-project knobs and per-gate extension fields). Missing
  required capabilities block; missing optional ones degrade.
- **GitHub transport resolver** (#62) — a normalized resolver picks `gh` (authenticated
  CLI) over host-provided MCP/API access and surfaces degraded operations explicitly.
- **Structured command contracts** (#66) — `keel plan --json` and `keel ship --json`
  emit deterministic, schema-stable contract + result records for adapters to consume.
- **Operator consent gate** (#82) — `keel ship` / `keel plan` accept
  `--live`, `--approve-scope`, `--operator`, and `--target`; live preflight emits an
  operator-consent contract and fails closed when required scopes are unapproved.
- **Safe Codex adapter** (#58) — the packaged Codex adapter runs read-only/sandboxed
  by default.
- **Generated-surface verification** (#79) — `keel adapter-status` reports generated
  adapter freshness against the packaged source bodies.
- **Adapter update/compat flow** (#80) — `keel update-adapter` safely refreshes
  generated adapters (with `--dry-run`) while respecting local markers.
- **Project policy packs** (#65) — projects can declare risk rules, test groups, docs
  requirements, and health providers via a validated policy pack.
- **Extension hooks on every backbone step** (#56) — extension slots span the full
  backbone so add-only Lego hooks can attach at each step.

### Changed
- **Parity matrix** (#63) — a command/capability parity matrix is captured and locked by
  `tests/test_parity_matrix.py`.
- **Public-repo readiness** — `LICENSE` (Apache-2.0), `SECURITY.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, issue/PR templates, `CODEOWNERS`, Dependabot, and a `.pre-commit-config`.
- **PyPI packaging** — `pyproject` carries full metadata (Apache-2.0 SPDX license, classifiers,
  project URLs) and a `publish.yml` workflow: a `v*` tag builds the sdist+wheel and publishes to
  PyPI via **trusted publishing (OIDC)**, with a CycloneDX SBOM, `SHA256SUMS`, build-provenance
  attestation, and a generated GitHub Release. `pip install keel-workflow`.
- **Security workflows** — `codeql.yml` (per-push/PR + weekly) and `scorecard.yml` (OpenSSF
  Scorecard from `main`), Actions pinned to commit SHAs.
- **Brand + site polish** — an SVG **hero** (dark/light, the backbone visualization) and a
  `favicon.svg`; README badges (CI, coverage, CodeQL, PyPI, Python, license); the website embeds
  the hero + favicon, and `pages.yml` now also publishes a self-hosted `coverage-badge.json`.
- **Command reference** (`docs/keel/commands.md`) + a **Workflow commands** section on the
  website — all 16 `/keel:<command>` workflows, each with its description and which surface
  installs it.
- `make adapters` now installs **both** surfaces (`install-adapter all`), and keel dogfoods its
  own `.claude/commands/keel/` + shared `.agents/skills/keel-*` skill set.
- Retired the stale per-agent adapter stubs (`adapters/{codex,gemini,agy}/keel-ship.md`); the
  packaged bodies under `src/keel/adapters/commands/` are the single source, and
  `adapters/README.md` documents the two-surface model.
- **Consolidation gate** (#94) — restored 100% line+branch coverage on the pure core and
  raised the coverage gate to `fail_under = 100`; removed dead code (`MUTATING_CAPABILITIES`).

## [0.5.0] — 2026-06-05

### Changed
- **`install-adapter` now targets two real surfaces, not one dir per agent** (**breaking**).
  Agents don't each read their own command dir — Claude reads `.claude/commands/`, while every
  other agent (Codex, Antigravity, Gemini, …) discovers a **shared** skill set under
  `.agents/skills/`. So keel installs into exactly those two surfaces:
  - `keel install-adapter claude` → `.claude/commands/keel/<cmd>.md` (native `/keel:<cmd>`).
  - `keel install-adapter skills` → **one** shared `.agents/skills/keel-<cmd>/SKILL.md` set
    (rendered from the same adapter via `install.render_skill`), read by all non-Claude agents.
  - `keel install-adapter all` → both.
  The previous per-agent targets (`codex`/`gemini`/`agy` → their own `keel/` dirs, and the
  0.4.0 `all` fan-out over them) are **removed**: they were inert (no agent read them) and
  re-introduced the file-copy duplication keel exists to eliminate. One skill copy now serves
  Codex + Antigravity + Gemini together.

## [0.4.0] — 2026-06-05

### Added
- **`keel install-adapter all`** — install the `/keel:<command>` adapters into **every** known
  agent dir (Claude + Codex + Gemini + agents) in a single run, instead of one agent at a time.
  Per-agent install is unchanged; `all` just fans out over `AGENT_DIRS` (`install.install_all`).
- **Cutover guide** (`docs/keel/cutover.md`) — the staged, verified process for a consumer to
  retire its copied command bodies: install + `keel install-adapter` → A/B verify `/keel:ship`
  on a low-risk test issue → retire the portable bodies (keep project-only) → move project
  specifics to knobs/Lego. Rollback = revert the PR. Lose nothing.

## [0.3.0] — 2026-06-05

### Changed
- **All `/keel:<command>` adapters brought to full project-neutral parity** (#34) — ported from
  the reference workflow bodies, capturing their real operational detail while reading every
  project value from `.keel/project.yaml`: `ship` (GitHub transport abstraction, blocker
  auto-detect, attribution + model-base stripping, mkdir-mutex merge, narrowed fix-loop),
  `regression` (parallel read-only area fan-out + multi-pass dedupe), `triage` (per-issue
  classifier subagent, closed label vocabulary), `flake-audit` (across-runs-disagreement rule),
  `coverage` (base→head delta, hot-spot tiering), `deps-audit`, `ci-check`, `stale-prs`,
  `pr-loop`, `review-cycle`, `review-all-day`, `morning`, `overnight`, `wrap`, `implement`;
  `ship-v2` is a pointer to `keel:ship` (no distinct portable backbone). **Zero downstream/
  app-specific references** — verified.

## [0.2.1] — 2026-06-05

### Changed
- Reverted the experimental post-merge **branch deletion** added after 0.2.0 (#38 → #39):
  deleting a merged head branch is GitHub's *"Automatically delete head branches"* repo
  setting, not keel's job. No other functional change since 0.2.0.

## [0.2.0] — 2026-06-05

### Added
- **Agentic `/keel:<command>` adapters + `keel install-adapter`** (#34) — keel now ships a set
  of project-neutral agentic workflow commands (`ship` — the full flow: per-round review,
  inline comments, `--delegate`/`--review-delegate`/`--review-comments`/`--dry-run`, the jury
  gate — plus `regression`, `implement`, `review-cycle`, `pr-loop`, `morning`, `overnight`,
  `wrap`, `triage`, `stale-prs`, `ci-check`, `deps-audit`, `flake-audit`, `coverage`). They are
  packaged with keel; `keel install-adapter <claude|codex|gemini|agy>` drops them into the
  project's agent command dir so they appear as `/keel:<command>` (installed, never hand-copied
  → no file-copy drift). Existing files are skipped unless `--force`.
- **`jury` gate runner** (#34) — the built-in `jury` gate now invokes the ai-jury CLI on the
  change's diff when it is installed, mapping its findings (file/line/severity) into keel
  Findings (critical/major block); when `jury` is absent the gate is a fail-soft no-op, so
  the flow runs with or without jury. keel takes **no** runtime dependency on ai-jury.
  Wired into `keel run-gates` / `keel ship` via `git diff base...HEAD`.
- **AI entry points** — a canonical, cross-AI `AGENTS.md` (the durable source of truth:
  backbone + invariants, pure-core/thin-I/O split, the 100% coverage bar, the
  single-runtime-dependency rule, conventions, and keel's config-driven agent dispatch)
  with a thin `CLAUDE.md` pointer. `projects/keel.yaml` `sot_doc` now points at
  `AGENTS.md`, matching the other configs.

## [0.1.0] — 2026-06-05

### Added
- **Website + live coverage** — a static site in `website/`; `make site` builds the coverage
  HTML into `website/coverage/` and serves it locally. A manual (`workflow_dispatch`)
  `pages.yml` can publish to GitHub Pages when enabled. `keel init --wizard` interactively
  sets the base branch, **merge-window hours**, timezone, and build/lint commands (#23).
- **Enhancements from the competitive analysis** (see `docs/keel/comparison.md`):
  - `keel init` — golden-path scaffolder: detects the stack (Flutter/Python/Node/
    Android/generic) and writes a valid default `.keel/project.yaml` (#19).
  - Gate findings now carry **`path`/`line`** parsed reviewdog-style from tool
    output, so the fix-loop and inline comments get real locations (#17).
  - `merge_window_mode: freeze|pause` — `freeze` (default) blocks the merge but
    keeps gates running; `pause` halts the pipeline outside the window (#18).
  - **Hotfix bypass**: `keel ship --hotfix` (or a `hotfix` label) merges outside the
    window — never bypassing findings or CI — with an audit line (#20).
- **keel-core** Python package (`src/keel`), stdlib-first with a single runtime
  dependency (PyYAML):
  - `jsonschema_min` — dependency-free JSON-Schema (draft-07 subset) validator.
  - `config` — load + validate `project.yaml` into a typed, immutable
    `ProjectConfig` (knobs + add-only extension slots) with a deterministic
    `config_hash`.
  - `model` — the fixed backbone (steps s0–s12), named slots, and invariants
    (single source of truth; the schema's slots are asserted against it).
  - `findings` — structured `Finding` + severity→decision mapping
    (critical/major=block, minor=suggest, nit=advisory) + `summarize`.
  - `extensions` — parse + validate project Lego extensions (add-only into named
    slots; `on_fail: block` only in `pre-merge`; agentic/command contract) with a
    fail-soft loader.
  - `gates` — plan built-in (build/lint/jury) + extension gates and run them
    through an injected runner with fail-soft semantics.
  - `orchestrator` — pure `build_plan`/`render_plan` mapping a project's
    gates/extensions onto the fixed backbone (deterministic, dry-run view).
  - `agents` — dispatch resolution + #2036 attribution (model-base stripping).
  - `runner` — thin, fail-soft subprocess wrapper + `command_gate_runner` that
    executes `command` gates (build/lint/command Lego).
  - `window` — merge-window logic (the night no-merge invariant, timezone-aware).
  - `lock` — the `mkdir`-based merge lock (context manager).
  - `classify` — pure risk-tier classification from changed files vs. globs.
  - `ship` — deterministic ship decisions (reviewer count, merge/defer/block,
    fix-loop budget) + `assess` (whole decision: tier → reviewers, window, CI, merge).
  - `cli` — `keel version | validate | plan | run-gates | window | ship`
    (`keel ship` = dry assessment of the agent-free backbone slice).
- Adapter: `adapters/claude/keel-ship.md` (thin, project-neutral `keel:ship`) +
  `adapters/README.md` (the adapter model).
- CI: `keel-ship` GitHub Actions workflow — runs `keel ship` live on the free hosted
  runner for every PR (uses the runner's `git` + `gh`/`GITHUB_TOKEN`), comments the
  assessment, and fails the check on a `BLOCK` decision. Docs in
  `docs/keel/github-actions.md`.
- Bundled schema `src/keel/schema/project.schema.json`.
- Seed configs `projects/{example-android,example-flutter,keel}.yaml`.
- Docs: README, `docs/keel/{configuration,extensions,cli,comparison}.md`,
  `docs/proposals/{keel-architecture,divergence-audit-2035}.md`.
- CI: cross-OS × Python matrix running tests, ruff, and the coverage gate.
- Test suite: 105 unit tests at 100% line + branch coverage on the core.

### Changed
- Repository repositioned from **ai-infra** (one-way file-copy sync) to **keel**
  (thin-consumer: pinned install + per-project config/extensions). Direction
  reversed: changes originate centrally and propagate down to projects.

### Removed
- `scripts/sync.sh` and the `/sync-to-ai-infra` mechanism (retired; superseded by
  the thin-consumer model).
