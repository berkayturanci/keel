# keel in depth

The [README](../../README.md) covers what keel is, how to install it and how to run a first
issue. This page holds the longer material that used to sit on its first screen: why keel
exists, the full feature list, how it compares with other tools, the backbone table, how keel
runs on itself, and the keel-visual companion.

## Contents

- [From "I opened a PR" to merged](#from-i-opened-a-pr-to-merged)
- [What you get](#what-you-get)
- [How keel compares](#how-keel-compares)
- [The backbone](#the-backbone)
- [Dogfooding](#dogfooding)
- [keel-visual](#keel-visual)

## From "I opened a PR" to merged

The keel is a ship's backbone — the fixed spine every project builds on. Work is driven
by `keel:ship`; `keel:swarm` aims the same backbone at a whole backlog as parallel waves,
but it is **experimental** and does not land work yet ([#1281](https://github.com/berkayturanci/keel/issues/1281)). keel is where
ships and fleets are built.

Keel is based on the work pattern of a strong teammate in a real engineering team:
take an issue from the queue, decide whether it is ready, own the implementation,
get it reviewed, keep the quality gates green, merge inside policy, and leave useful
memory behind for the next session. keel focuses on one agent owning work end to end.

The bottleneck in agentic coding is rarely code generation—it is **work ownership and delivery governance**.

Most coding agents stop at *"I opened a PR."* Without an invariant delivery backbone, agent-written code stalls in review loops, introduces silent regressions, causes merge collisions, or bypasses compliance.

**Keel closes that gap.** It provides the fixed backbone (`s0`–`s12`) that drives every unit of work through scope validation, multi-agent adversarial reviews, commit-bound evidence verification, timezone-aware merge locks, and post-merge proof, so work either merges into the base branch or halts at a named step with its reason recorded; a capture path that cannot be written is logged as degraded rather than stopping the run, while a missing jury CLI still leaves a tier-3 merge owing its `jury-verdict` unless the run passes `--no-jury`.

> keel uses a thin-consumer model: the core is installed + pinned, never copied, so the
> drift/overwrite class of bug is structurally gone. Background: the original design
> proposal, [`docs/proposals/keel-architecture.md`](../proposals/keel-architecture.md)
> (historical; current behaviour is documented in [`docs/keel/`](./)).

## What you get

- **One backbone, four hosts** — keel installs into Claude Code, Codex, Cursor (partial) and Antigravity
  ([per-host steps](install.md)); `/keel:<command>` runs as native Claude commands
  *and* as a single shared skill set under `.agents/skills/` for agents that read skills there.
  Cursor is partial: its commands arrive only through the marketplace route, and a local
  install registers one skill ([#1332](https://github.com/berkayturanci/keel/issues/1332)).
- **High-concurrency Swarm orchestration** (**experimental**) — cluster entire backlogs into topological dependency waves, execute disjoint clusters in isolated git worktrees, and land them under a single-writer merge lock with sequential `git merge --no-ff` ([guide](swarm.md)). The planning commands run; **a live run cannot produce a commit or a pull request**. The child `keel ship` is a dry assessment that never commits or opens a PR in any mode — keel's design has the *agent* implement by following `/keel:ship` — and `swarm-run --live` is refused, since its workers could not pass `keel ship --live`'s operator-consent gate ([#1269](https://github.com/berkayturanci/keel/issues/1269)); scope cannot be given per issue, so a multi-issue plan returns one flat wave, or a fully serial one when a shared `--declared-file` puts every issue in the same scope ([#1274](https://github.com/berkayturanci/keel/issues/1274)). Audit epic: [#1281](https://github.com/berkayturanci/keel/issues/1281). Use `/keel:ship` for work you need merged.
- **Project Lego + policy packs** — snap gates/steps into named hooks (`guard`, `tester`,
  `pre-merge`, …) and keep labels, path policy, health sources, local commands, and
  workflow preferences in `policy_pack` data instead of packaged command prose.
- **Security presets** — declarative `policy_pack.presets: ["bandit", "gitleaks", "semgrep", "trivy"]`
  automatically slot SAST, secret scanning, and vulnerability auditing into the pipeline.
- **A cross-vendor `jury`, on by default for tier 3** — the
  [ai-jury](https://github.com/berkayturanci/ai-jury) multi-agent reviewer reads the diff.
  `ship.resolve_jury` turns it on **automatically for a tier-3 change** (one that matches
  `knobs.tier3_globs`), and the evidence gate then requires a `jury-verdict`. Below tier 3 it is
  off unless a run passes `--jury` or `knobs.team` makes the panel the review; `--no-jury` turns
  it off below a panel tier. Listing `jury` in `gates:` also runs it as a
  `keel run-gates` gate at s8, at every tier. Without the `jury` binary the s8 run is a
  no-op (reported `SKIPPED`; with no other gate planned it blocks), but a tier-3 merge still requires a `jury-verdict` unless the run passes `--no-jury`;
  it relaxes to advisory only when a posted verdict (or `--jury-vendors`) reports fewer than
  2 vendors. Core resolves the mode from the panel that actually ran: a cross-vendor gate
  needs ≥2 distinct vendors, so a short panel downgrades to advisory instead of blocking.
- **…or the panel *is* the review** — set `knobs.team.review.by_tier."3": jury` and s7 dispatches
  ai-jury **once** instead of running host reviewers beside it. `keel review --from-jury
  <report.json>` turns each panelist's ballot into a head-pinned `keel.review-verdict.v1` with the
  vendor and model that produced it, posts the jury verdict as the consensus record, and hands the
  fix loop the panel's *verified* findings. The evidence gate then requires one verdict per ballot
  (the panel declares its own size) plus that verdict. Too few participating vendors relaxes
  neither: on a panel tier the verdict stays required and the thin span is refused as
  `review-vendor-distinctness`, because a short panel may not excuse itself from the record
  that says it was short. The bench is a function of config alone, so every surface of a run
  agrees on it. Needs ai-jury's ballot report (`jury --format json`, schema 1.1+); keel still
  never imports it.
- **Headless with just an API key** — the hosted-API delegates
  (`--delegate anthropic-api:MODEL` / `openai-api:MODEL` / `google-api:MODEL`) drive the implement/review steps
  with only `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` in the environment — no agent CLI
  installed. Connect any OpenAI-compatible provider (OpenRouter, DeepSeek, Groq, local vLLM/Ollama) or custom CLI
  via `knobs.delegate_profiles` ([design](../proposals/api-token-delegate.md)), or keep operator-owned
  entries out of the repository entirely in the machine-level
  [provider registry](configuration.md#provider-registry) (`~/.keel/providers.yaml`).
- **A team, not a delegate** — `knobs.team` states who implements (per issue role, with model
  and reasoning effort), who gives the gate review from a *different* vendor, who reviews at
  each risk tier — or `jury`, when the cross-vendor panel **is** the review — and who applies
  the findings ([reference](configuration.md#team)). `keel plan`/`keel ship --json`
  render it as one resolved `assignment` so every host runs the same team, and `keel validate`
  refuses a policy keel cannot execute: an unknown provider, an effort a vendor cannot honour, or
  a gate reviewer that is the implementer. The reviewer count and the jury mode are enforced by
  the evidence gate; the **gate review is emit-only** — core publishes the seat and the adapter
  dispatches it, with no evidence item behind it, the same boundary operator consent sits behind.
- **Test-first when you want it** — `knobs.implement_mode: tdd` (or `--tdd` for a single run)
  splits s4 into two phases: a **test-only commit** carrying the issue's acceptance criteria,
  then the implementation. s8 gains the pure, blocking **`tdd-order`** gate, which checks that
  commit order against the `policy_pack.test_groups` paths, so "tests first" is verified rather
  than asserted. The resolved profile is published as `contract.implement_mode`
  (`{"mode": "tdd", "gate": "tdd-order", "phases": ["tests", "implementation"], …}`) and
  recorded in the ledger, and `--phase-implementer tests=…` / `implementation=…` records a run
  where the two phases were written by different seats
  ([reference](configuration.md#implement_mode)).
- **Iterate s4 with the gates as the judge** — `knobs.loop` (or `--loop` for a single run)
  wraps the implement pass in a **bounded, gate-verified loop**: after each iteration the
  command gates run; green ends the loop, red starts the next iteration with the same brief
  plus the gate output, up to `max_iterations`. The completion criterion is the gate run,
  never the implementer's own "done" — the Ralph loop's iteration without its judge or its
  amnesia: every iteration is one commit the ledger names, published as
  `contract.implement_mode.loop` and rendered in the closure comment. It composes with
  `--tdd` (the loop wraps phase B only) and runs on every host keel runs in
  ([reference](configuration.md#loop)).
- **Every merge can leave a lesson the next run reads** — with `policy_pack.capture.learning.sink`
  set, an applied `create-learning` capture writes one Markdown learning: the issue, the gate
  results on the head it merges, and a link to every file it changed, so a knowledge-graph
  builder gets the edges ([reference](configuration.md#policy_packcapturelearningsink)).
  With an in-repo sink, s10 runs `keel capture-land --write --onto "$BRANCH"`, which commits that
  lesson onto the pull request itself, so the same squash carries it into the base branch: a
  protected base never sees a direct push, and there is no second pull request to forget
  ([reference](cli.md#--write-the-lesson-is-written-here-and-recorded-at-s11)). The
  review still holds for the head that landing produces — the evidence gate accepts a pin across
  a commit with one parent, the `keel.capture-land.v1` marker and exactly one added or modified
  file inside the sink, and across nothing else. `keel plan` and `keel ship` read matching
  lessons back into the implement and review briefs, at most five
  ([reference](configuration.md#policy_packcapturelearningsource)).
- **Know which providers this machine can actually dispatch to** — `keel doctor --providers [--json]`
  probes every provider keel supports (agent CLIs, hosted APIs, local Ollama models, delegate profiles
  and registry entries) and reports `available` / `reason` / transport / capabilities / model list for
  each. Probes are time-boxed and fail-soft, and print key *names* only, never values
  ([reference](runtime-capabilities.md#probing-providers-keel-doctor---providers)).
- **Auditable evidence chain & compliance** — every PR merged through Keel carries a
  commit-SHA-bound, auditable record of reviewer verdicts, test results, and model
  attributions ([guide](evidence.md)). Approvals are locked to the exact HEAD commit,
  preventing approval drift across subsequent pushes — a lesson `keel capture-land` lands is the one
  commit they survive, under the rule above — with first-class, audited exception tracking.
- **Safe merges by construction** — the core-owned `keel merge` path (resource claim,
  window re-check, live CI rollup, and evidence verification before the merge), timezone-aware
  night no-merge window, risk-tier → reviewer count, hotfix bypass with an audit line,
  vendor+model attribution. The PR evidence gate arms from ship provenance by default —
  only the operator-applied `keel:evidence-waived` label disarms it, and a gate that never
  armed now **blocks** rather than reporting a pass having checked nothing. Requirements are
  split by phase, so the merge gate asks for the review/jury evidence that exists at s10 and
  not the closure comments s11 writes after it. Where a host's egress proxy blocks GitHub's
  GraphQL endpoint, `keel merge` asks the same questions over REST
  (`--transport auto|graphql|rest`) — the claim, window, rollup, evidence and SHA-pinned
  gates-pass are unchanged — and its read-only drift check, `keel verify-merge`, takes the same
  flag ([reference](cli.md#transport-graphql-or-rest-when-the-endpoint-is-blocked)).

## How keel compares

Keel sits between three established tool categories:

| category | examples | where they usually stop | what Keel adds |
|---|---|---|---|
| Coding agents | OpenHands, SWE-agent, Copilot coding agent, Devin | create or update a PR | intake, review gates, merge policy, closeout, capture hooks |
| PR reviewers | CodeRabbit, Qodo / PR-Agent, Greptile, Cursor Bugbot | review an existing PR | implementation loop, tests, merge lock/window, closeout capture |
| Merge queues | GitHub Merge Queue, Mergify, Graphite, Trunk | serialize tested PRs | issue ownership before the PR exists |

Keel is not trying to replace those tools. It is the work-ownership backbone that can use
coding agents, reviewers, gates, and merge policy in one lifecycle. See
[`comparison.md`](comparison.md) for the source-backed comparison and
the ideas Keel should borrow.

## The backbone

| step | name | primary hooks | |
|---|---|---|---|
| s0 | config | `after:config` | |
| s1 | select | `before:select`, `select`, `after:select` | |
| s2 | branch | `before:branch`, `after:branch` | |
| s3 | guard | `guard` | |
| s4 | implement | `before:implement`, `after-implement` | agent |
| s5 | classify | `classify`, `after:classify` | agent |
| s6 | ci | `before:ci`, `after:ci` | |
| s7 | review | `reviewers`, `after:review` | agent |
| s8 | test | `tester`, `test`, `after:test` | |
| s9 | fixloop | `before:fixloop`, `fixloop`, `after:fixloop` | |
| s10 | merge | `pre-merge`, `after:merge` | |
| s11 | capture | `capture`, `post-merge` | |
| s12 | close | `before:close`, `on-close`, `after:close` | |

Invariants the backbone always preserves: merge lock, night no-merge window, fail-soft,
orchestrator-only-writes, vendor+model attribution. The full hook table is in
[`extensions.md`](extensions.md).

The capture step has a core marker/verifier contract:
`compound-learning: pr=<N> status=<applied|deferred|skipped:reason>`. Projects provide
capture content and destinations through `capture` / `post-merge` extensions; keel owns the
marker, fail-soft semantics, redaction-before-durability, offline `capture-verify`, and the
learning-quality decision recorded in `capture.learning`. Durable learning is optional:
policy can choose `create-learning`, `marker-only`, or `defer`, while duplicate candidates
are suppressed by stable fingerprints so routine merges do not flood the learning surface.

## Dogfooding

keel drives **itself** from `.keel/project.yaml` using the latest `^1.0` core contract
(Python, `make test` + `make lint` gates), and CI runs keel on keel-core on every push.
`projects/keel.yaml` remains a seed copy and is tested to stay in sync with the dogfood
config.

```bash
keel plan      .keel/project.yaml --root . # render keel's own backbone
keel run-gates .keel/project.yaml --root . # keel runs its own test + lint gates
keel ship      .keel/project.yaml --root . # full dry assessment: tier, window, gates, decision
#   (example output; the tier follows the files a change touches)
#   risk tier     : TIER-3  → 3 reviewer(s)
#   decision      : MERGE — clear to merge
```

If a step's gate fails, keel blocks its own merge — the same backbone every consumer gets.

## keel-visual

[`keel-visual`](https://pypi.org/project/keel-visual/) is an **optional, separately
installable** animated run visualizer (`pipx install keel-visual`). It *renders* a
keel run from the ledger/checkpoint keel already writes — it never drives one — for
every command flow (`ship` is the s0–s12 backbone; every other command renders its own
phases). The full guide is [`keel-visual.md`](keel-visual.md).

Four surfaces:

- **`play`** — the run animates in the terminal (flow + wave ribbon, `--loop` for a
  demo, live `--follow`; `--theater` hands off to [ai-jury](https://github.com/berkayturanci/ai-jury)'s
  deliberation theater at the review step, then resumes).
- **`dash`** — a terminal board of every active run; **`dash --all`** aggregates every
  keel project under a parent folder into one board. It shows `ship` runs *and*
  non-ship commands (triage, morning, pr-loop …) live via the `keel activity`
  channel — each with its own phases.
- **`render`** — a self-contained web page: a **2D flow** and a **3D scene** with five
  selectable styles (`plexus`/`comet`/`aurora`/`combined`/`line`); **`render --all`**
  writes a multi-project board with a **2D grid / 3D scene** toggle, automatic
  **light/dark** theme, and an **all / active** filter that fades finished runs.
- **`serve` / `serve --all`** — a **live** localhost web dashboard (polls every ~0.5s):
  a filterable board, and per-run a detail drawer with a **2D / 3D switch** (a live
  per-run scene with `curve · helix · ring · line · plexus · aurora · comet` styles,
  drag-orbit + scroll/pinch zoom, theme-aware) and, from the ledger record, the run's
  command / phase / status, **risk tier**, **merge-window** state, **per-named-gate**
  status (build / lint / evidence / jury …), **agent attribution** (implementer,
  reviewers, host — including the hosted-API delegates), and the **jury verdict**
  itself when ai-jury ran, not just the jury mode.

As of **keel 1.6.4**, the deterministic backbone commands (`keel plan` at Step 0, `keel
run-gates` at s8, `keel merge` at s10) auto-stamp the `keel activity` board when given a
`--run-id`, so a run shows up **and advances** even if the agent skips the per-phase
`keel activity` calls. Since **1.6.5**, `keel merge` stamps the run `merged` (a real merge
landed) rather than a soft `done`, so the board distinguishes a green confirmed-merge from
a closed-out run; runs whose `--run-id` ends in their issue/PR (`ship-585`) are labelled
`#585` even when no explicit issue is passed.

Depends on this core (`keel-workflow >= 1.6.0`); the core never depends on it (it only
reads records, and probes `shutil.which("jury")` — never imports ai-jury). See
[`keel-visual/README.md`](../../keel-visual/README.md).
