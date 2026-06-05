# Changelog

All notable changes to keel are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); keel adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Post-merge cleanup in `/keel:ship`** — the close step now removes the issue's worktree
  **and deletes the now-merged branch** (local `git branch -d` — safe, refuses unmerged —
  plus the remote). `git.delete_branch` wrapper added; `pr-loop` cleans up the same way.

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
