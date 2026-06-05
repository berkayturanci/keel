# keel

> **keel** is a project-neutral, multi-agent **workflow core**. A *fixed backbone*
> of steps drives a unit of work — a GitHub issue — from backlog to done
> (branch → implement → CI → review → test → merge → close). Projects never fork the
> backbone: they set per-project **values** in `project.yaml` and snap their own
> **Lego pieces** into named extension slots.

The keel is a ship's backbone — the fixed spine every project builds on. The flagship
command is `keel:ship`; keel is where ships are built.

> Formerly **`ai-infra`** (a one-way file-copy sync of "portable" commands). keel replaces
> that with a thin-consumer model: the core is installed + pinned, never copied, so the
> drift/overwrite class of bug is structurally gone. Background:
> [`docs/proposals/divergence-audit-2035.md`](docs/proposals/divergence-audit-2035.md),
> [`docs/proposals/keel-architecture.md`](docs/proposals/keel-architecture.md).

## Three layers

```
Layer 3  EXTENSIONS   project-owned Lego pieces, ADD-ONLY into named slots
Layer 2  CONFIG       project.yaml — per-project values (branch, build cmd, globs, agents…)
Layer 1  BACKBONE     keel-core — fixed ordered step machine + invariants (this package)
```

Changing the backbone is a keel-core change. Projects only ever touch layers 2–3.

### The backbone

| step | name | slot | |
|---|---|---|---|
| s0 | config | | |
| s1 | select | | |
| s2 | branch | | |
| s3 | guard | | |
| s4 | implement | `after-implement` | agent |
| s5 | classify | | agent |
| s6 | ci | | |
| s7 | review | `reviewers` | agent |
| s8 | test | `tester` | |
| s9 | fixloop | | |
| s10 | merge | `pre-merge` | |
| s11 | capture | `post-merge` | |
| s12 | close | | |

Invariants the backbone always preserves: merge lock, night no-merge window, fail-soft,
orchestrator-only-writes, vendor+model attribution.

## Install

keel is a Python (≥3.11) package with one runtime dependency (PyYAML). Private install:

```bash
pip install "git+https://github.com/berkayturanci/keel@v0.3.0"
```

In a cloud agent session, install it from a `SessionStart` hook (or add keel to the
session's repo scope) so the pinned core is available before a run.

## Quickstart

```bash
keel validate projects/example-flutter.yaml          # validate a config against the schema
keel plan      projects/example-flutter.yaml          # show the backbone plan for a project
keel version
```

`keel plan` renders the fixed backbone with each project's gates/extensions slotted in —
exactly what a dry-run executes:

```
keel plan — example-flutter
  base_branch: main   core_version: ^0.3
  backbone:
     s4  implement  [agent]
     ...
     s8  test
           - gate: build
           - gate: lint
           - gate: design-parity
    s10  merge
           - gate: design-parity-gate
```

## Invocation (`/keel:<command>`)

The agentic workflows ship **with the package** as project-neutral adapters and install into
a project's agent command directory, so they appear as `/keel:<command>` — the same string
works in every project; only that project's `.keel/project.yaml` + extensions change the
behaviour:

```bash
keel install-adapter claude   # → /keel:ship, /keel:regression, /keel:morning, /keel:wrap, …
#  (also: codex | gemini | agy — or `all` to set up every agent dir at once)
```

Shipped commands: `ship` (flagship), `regression`, `implement`, `review-cycle`, `pr-loop`,
`morning`, `overnight`, `wrap`, `triage`, `stale-prs`, `ci-check`, `deps-audit`,
`flake-audit`, `coverage`. The `keel` CLI does the deterministic work; the adapters are the
agentic flows (per-round review, inline comments, delegation).

## Dogfooding

keel drives **itself**: its config is `projects/keel.yaml` (Python, `make test` + `make lint`
gates) and CI runs keel on keel-core on every push —

```bash
keel plan      projects/keel.yaml          # render keel's own backbone
keel run-gates projects/keel.yaml --root . # keel runs its own test + lint gates
keel ship      projects/keel.yaml --root . # full dry assessment: tier, window, gates, decision
#   risk tier     : TIER-3  → 3 reviewer(s)
#   decision      : MERGE — clear to merge
```

If a step's gate fails, keel blocks its own merge — the same backbone every consumer gets.

## Docs

- 🌐 **Website + live coverage report** — `make site` builds the coverage HTML into
  `website/coverage/` and serves the site at <http://localhost:8000>. (Publishing to GitHub
  Pages is available via the manual `pages.yml` workflow once Pages is enabled.)
- [`docs/keel/configuration.md`](docs/keel/configuration.md) — `project.yaml` reference
- [`docs/keel/extensions.md`](docs/keel/extensions.md) — authoring Lego extensions
- [`docs/keel/cli.md`](docs/keel/cli.md) — CLI reference
- [`docs/keel/cutover.md`](docs/keel/cutover.md) — staged guide to retire a project's copied command bodies (install → verify → retire), losing nothing
- [`docs/keel/comparison.md`](docs/keel/comparison.md) — competitive landscape (Mergify, GitHub merge queue, Qodo/PR-Agent, CodeRabbit, Sweep, OpenHands, Danger, …) + ranked borrow-ideas
- [`docs/keel/github-actions.md`](docs/keel/github-actions.md) — run keel live on GitHub's free runner (the `keel-ship` workflow)
- [`docs/proposals/keel-architecture.md`](docs/proposals/keel-architecture.md) — full design

## Development

Stdlib-first, pure-core + thin-I/O, deterministic, fully covered (ai-jury ethos).

```bash
make test       # offline unit suite (no network, no credentials)
make lint       # ruff
make coverage   # coverage gate (fail_under in pyproject)
make validate   # validate every projects/*.yaml
make site       # build the coverage report + serve the website at localhost:8000
```

The pure core (`config`, `model`, `extensions`, `findings`, `gates`, `orchestrator`,
`cli`) is held at **100% line + branch coverage**; the coverage gate (`fail_under = 95`)
runs in CI.

## Repo layout

```
src/keel/            the core package (config, model, extensions, findings, gates, orchestrator, cli)
src/keel/schema/     project.schema.json (bundled)
projects/*.yaml      example configs (example-android, example-flutter, keel)
adapters/            thin per-agent adapters (claude, codex, gemini, agy)
website/             static site + coverage report (make site)
tests/               unit suite
docs/                docs + proposals
```
