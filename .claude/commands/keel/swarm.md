---
description: Multi-agent swarm coordinator — cluster backlog issues, execute parallel waves in isolated worktrees, and land them under a single-writer merge lock.
argument-hint: "[issue numbers...] [--plan-only] [--tree] [--visual] [--delegate <provider>] [--review-delegate <provider>] [--effort <low|medium|high>] [--team <profile>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(jury:*), Read, Edit, Write, Agent
---

# /keel:swarm

## Live progress — stamp this run (required)

So this run shows live on `keel-visual`'s board, record it with `keel activity` **as you
go**. This command's phases are: `config` → `plan` → `isolate` → `execute` → `land` → `report`.
Pick one stable `--run-id` for the whole swarm execution (e.g. `swarm-<date-or-id>`):

- **Right now, before the work below**, stamp the first phase:
  `keel activity .keel/project.yaml --root . --write --command swarm --run-id "$RUN" --phase config`
- Re-run with the next `--phase` (`plan`, `isolate`, `execute`, `land`, `report`) **as you advance** through the flow.
- At the end: `keel activity .keel/project.yaml --root . --run-id "$RUN" --done`

Treat this like any other contractual step — do not skip it. The one allowed exception is a
core too old to ship `keel activity` (keel < 1.6.0): then skip it silently and never block
the command.

## Command step evidence

Every numbered step in this command is contractual. Complete the step, record the
evidence it asks for, or explicitly mark it `N/A — <reason>` before moving on. Any GitHub
comment, review, issue label, branch, PR, merge, report, or queue write must be posted or
written through the selected transport and cited in the final summary.
Never silently skip a step because the runtime, agent, or prompt feels obvious.

Run a high-concurrency multi-agent swarm: partition dependent and independent backlog issues
into topologically ordered execution waves, execute disjoint clusters in parallel isolated
git worktrees, and land them under a single-writer merge lock with sequential
`git merge --no-ff`.

## Who does what — CTO, team lead, worker

Three levels, and each one only does its own job:

- **You are the CTO.** You cluster the backlog, launch one lead per cluster, and land the
  waves. You do not implement, review, or drive a child ship yourself.
- **One team lead per cluster.** A lead is a subagent you spawn for exactly one cluster. It
  runs that cluster's `/keel:ship` runs with the providers the cluster's `assignment` names,
  and it reports through the cluster's worker status record — the same records
  `keel swarm-status` renders, so a lead needs no reporting channel of its own.
- **Workers are the child ship runs** the lead drives, one per issue in its cluster.

The hierarchy is not a suggestion about tone: a lead that reports up through anything other
than the worker record is invisible to the board, and a CTO that implements has no one left
to land the wave.

## Step 0 — Resolve config + swarm contract

```bash
keel validate .keel/project.yaml --root .
keel plan     .keel/project.yaml --root . --command swarm --live --json
keel window   .keel/project.yaml
keel swarm-plan .keel/project.yaml --issues <n,n,n> --tree
```

Parse `contract.operator_consent` before selecting work, creating branches/worktrees,
spawning subagents, opening PRs, merging, writing reports, or touching GitHub labels or
comments. If `requires_operator_consent` is true, STOP and ask the operator to rerun with
the required `--approve-scope` values. Pass
`operator_consent.delegated_agent_scope` into every child `/keel:ship` handoff. Children
may use only `approved_mutation_scopes`; scope expansion blocks or escalates.

## Step 1 — Deterministic static dependency analysis, scoring & staffing

Run static dependency analysis, scope prediction, difficulty scoring and per-cluster
staffing across the target issue set:

```bash
keel swarm-plan .keel/project.yaml --issues <n,n,n> --tree --json
```

Pass the operator's staffing flags straight through — `--delegate`, `--review-delegate`,
`--effort`, `--team <profile>` and `--reviewers` — so the plan shows the team the run will
actually dispatch rather than the default one.

- Inspect the generated waves, disjoint clusters, conflict edges, and direct landing eligibility.
- Each cluster carries a `difficulty` (`band`, `score`, `tier` and the `signals` that
  produced them) and an `assignment` (`lead`, `implementer`, `effort`, `reviewers`,
  `review_panel`, `gate`, `fix`). Both are resolved by core from `knobs.team` plus
  `knobs.team.by_difficulty`; do not re-derive either, and do not substitute a provider of
  your own choosing for one the assignment names.
- Read `assignment.warnings` before launching anything. A `--team` profile that names no
  configured bench, or a gate that is its own implementer, is reported there and nowhere
  else.
- If `--plan-only` was requested, render the ASCII DAG tree and exit.

## Step 2 — Launch one lead per cluster

Launch parallel workers per cluster in dedicated git worktrees under `.keel/worktrees/<swarm_id>/<cluster_id>/`:

```bash
keel swarm-run .keel/project.yaml --root . --issues <n,n,n> --live
```

- Spawn **one team lead subagent per cluster**, briefed with that cluster's `assignment`
  and `difficulty` verbatim. The lead runs the cluster's issues through the standard
  `keel ship` backbone steps (`s0`–`s12`) in the cluster's isolated worktree.
- The lead passes its cluster's team to every child ship it starts, using the **same five
  flags a work block hands down** — `keel ship` accepts all of them:

  ```
  keel ship <project.yaml> --issue <N> --delegate <assignment.implementer> [--review-delegate <provider>]... [--role <assignment.role>] [--effort <assignment.effort>] [--team <assignment.team_profile>]
  ```

  One `--review-delegate` per staffed reviewer slot, in slot order. `--effort` and
  `--team` carry the bench the cluster was staffed from, so the child's own resolution
  reproduces the parent's instead of re-deriving a different one from config alone.
  `keel swarm-run` appends exactly this set for the runs it starts itself; a lead driving
  ships by hand appends the same. A seat that is a host `subagent:` rather than a provider
  is not a `--delegate` value — spawn it as a subagent instead.
- A role that is not `[A-Za-z0-9][A-Za-z0-9._-]*` is **not** passed: it would be parsed as
  a flag by the child and break the run. Core drops it and says so in
  `assignment.warnings`; the child resolves its role from the issue's own labels.
- A lead never re-scores its cluster and never re-staffs it. If the work turns out heavier
  than the band said, it reports that through the worker record and the CTO re-plans.
- When a cluster's issue fails, `rebalance_swarm_plan` drops the clusters carrying that issue from the remaining waves; there is no runtime file-divergence detection — clusters are kept apart by plan-time overlap partitioning and per-worktree isolation.
- Track live worker states with `keel swarm-status` — the board's `Lead` and `Band` columns
  are how the operator sees which lead owns which cluster and why it drew its provider.

## Step 3 — Batch landing under the merge lock

When an execution wave completes, land all passing clusters onto `main`:

```bash
keel swarm-land .keel/project.yaml --root . --wave <n> --live
```

- The landing mode is **derived from the plan's predicted scopes for the wave**, not passed on the command line.
- **Orthogonal Batch Landing**: Disjoint diff trees are merged into main with `git merge --no-ff`, sequentially under the atomic `merge_lock`.
- Every planned wave is internally disjoint, so landing always runs in direct-batch mode; the library's adaptive rebase funnel is not selected by this command.

## Step 4 — Visual tracking & terminal dashboard

Render the spatial DAG cluster graphs and pseudo-3D wave topology for the swarm (a rendered snapshot):

```bash
keel swarm-status .keel/project.yaml --root .
keel-visual swarm .keel/project.yaml --root . --out keel-swarm.html
```

When `--visual` was requested, launch the localhost visualizer dashboard:
```bash
keel-visual swarm .keel/project.yaml --root . --serve --port 8766
```

## Step 5 — Swarm recap report

Compile the overall multi-agent swarm outcome:
- Total issues planned, clustered, and executed.
- Per cluster: its difficulty band and score, its lead, and the implementer/reviewer seats
  that ran it — plus any `assignment.warnings` that were raised and what was done about them.
- Worker success/failure breakdown.
- Landing outcome per cluster: merged, or `merge failed` / held with its reason.
- Per-cluster review outcome (the configured review, or the ai-jury panel on tier-3) and each cluster's `compound-learning:` ledger marker.
- Record final completion:
  `keel activity .keel/project.yaml --root . --run-id "$RUN" --done`

<!-- keel-generated: surface=claude command=swarm keel_version=1.23.1 source_sha256=03cf63c242f2afd376675e3c5db6f277e2ff7769411c0ee3cf2866a79c3b9e17 generated_sha256=03cf63c242f2afd376675e3c5db6f277e2ff7769411c0ee3cf2866a79c3b9e17 -->
