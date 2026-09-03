---
name: keel-swarm
description: Multi-agent swarm coordinator — cluster backlog issues, execute parallel waves in isolated worktrees, and land orthogonal batches with self-healing rebase.
---

# keel-swarm

Use this skill when the user asks to run the keel command `swarm` (e.g. `keel swarm ...`, `swarm <args>`, or `/keel:swarm`). It reads every project value from `.keel/project.yaml` via the `keel` CLI.

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
git worktrees, and land batches cleanly via orthogonal fast-forward merges or adaptive
self-healing funnel rebases.

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

Launch parallel workers per cluster in dedicated git worktrees under `.keel/worktrees/swarm/`:

```bash
keel swarm-run .keel/project.yaml --root . --issues <n,n,n> --live
```

- Spawn **one team lead subagent per cluster**, briefed with that cluster's `assignment`
  and `difficulty` verbatim. The lead runs the cluster's issues through the standard
  `keel ship` backbone steps (`s0`–`s12`) in the cluster's isolated worktree.
- The lead passes its cluster's team to every child ship it starts:
  `--delegate <assignment.implementer>`, one `--review-delegate` per staffed reviewer slot,
  and `--role <assignment.role>`. `keel swarm-run` already appends these for the runs it
  starts itself; a lead driving ships by hand must append the same flags, or the child
  re-resolves from config alone and quietly runs a different team.
- A lead never re-scores its cluster and never re-staffs it. If the work turns out heavier
  than the band said, it reports that through the worker record and the CTO re-plans.
- If runtime file modification divergence is detected, dynamic rebalancing partitions overlapping branches to the next wave tier.
- Track live worker states with `keel swarm-status` — the board's `Lead` and `Band` columns
  are how the operator sees which lead owns which cluster and why it drew its provider.

## Step 3 — Orthogonal batch landing & drift self-healing

When an execution wave completes, land all passing clusters onto `main`:

```bash
keel swarm-land .keel/project.yaml --root . --wave <n> --live
```

- The landing mode is **derived from the wave's diff map**, not passed on the command line.
- **Orthogonal Batch Landing**: Disjoint diff trees are fast-forwarded or batch-merged concurrently under atomic `merge_lock`.
- **Adaptive Funnel Landing**: If overlapping file trees exist, sequential cherry-pick/rebase is executed with fail-soft self-healing.

## Step 4 — Real-time visual tracking & live terminal dash

Render the spatial DAG cluster graphs and 3D wave topology for the active swarm:

```bash
keel swarm-status .keel/project.yaml --root .
keel-visual swarm .keel/project.yaml --root . --out keel-swarm.html
```

When `--visual` was requested, launch the localhost visualizer dashboard:
```bash
keel-visual swarm .keel/project.yaml --root . --serve --port 8766
```

## Step 5 — Synthesis & swarm recap report

Compile the overall multi-agent swarm outcome:
- Total issues planned, clustered, and executed.
- Per cluster: its difficulty band and score, its lead, and the implementer/reviewer seats
  that ran it — plus any `assignment.warnings` that were raised and what was done about them.
- Worker success/failure breakdown.
- Landing mode used (Direct Batch vs Adaptive Funnel) and rebase self-healing stats.
- Final multi-agent jury deliberation consensus and compound learning synthesis.
- Record final completion:
  `keel activity .keel/project.yaml --root . --run-id "$RUN" --done`

<!-- keel-generated: surface=skills command=swarm keel_version=1.19.3 source_sha256=59fe137ba554f65db429ca4b45485e4a4ff134184b61a1ccfdb3265b2413faba generated_sha256=3253e2eb2a41e772c5d5feca51dd1a48b55a066128b39a4726050996d4e35460 -->
