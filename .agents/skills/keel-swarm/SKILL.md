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

## Step 0 — Resolve config + swarm contract

```bash
keel validate .keel/project.yaml --root .
keel plan     .keel/project.yaml --root . --command swarm --live --json
keel window   .keel/project.yaml --root .
keel swarm-plan .keel/project.yaml --root . <issue-numbers...> --tree
```

Parse `contract.operator_consent` before selecting work, creating branches/worktrees,
spawning subagents, opening PRs, merging, writing reports, or touching GitHub labels or
comments. If `requires_operator_consent` is true, STOP and ask the operator to rerun with
the required `--approve-scope` values. Pass
`operator_consent.delegated_agent_scope` into every child `/keel:ship` handoff. Children
may use only `approved_mutation_scopes`; scope expansion blocks or escalates.

## Step 1 — Deterministic static dependency analysis & clustering

Run static dependency analysis and scope prediction across the target issue set:

```bash
keel swarm-plan .keel/project.yaml --root . <issue-numbers...> --tree --json
```

- Inspect the generated waves, disjoint clusters, conflict edges, and direct landing eligibility.
- If `--plan-only` was requested, render the ASCII DAG tree and exit.

## Step 2 — Isolated multi-worktree execution runtime

Launch parallel workers per cluster in dedicated git worktrees under `.keel/worktrees/swarm/`:

```bash
keel swarm-run .keel/project.yaml --root . <issue-numbers...> --rebalance
```

- Each cluster worker drives standard `keel ship` backbone steps (`s0`–`s12`) in its isolated worktree.
- If runtime file modification divergence is detected, dynamic rebalancing partitions overlapping branches to the next wave tier.
- Track live worker states with `keel swarm-status`.

## Step 3 — Orthogonal batch landing & drift self-healing

When an execution wave completes, land all passing clusters onto `main`:

```bash
keel swarm-land .keel/project.yaml --root . --mode auto
```

- **Orthogonal Batch Landing**: Disjoint diff trees are fast-forwarded or batch-merged concurrently under atomic `merge_lock`.
- **Adaptive Funnel Landing**: If overlapping file trees exist, sequential cherry-pick/rebase is executed with fail-soft self-healing.

## Step 4 — Real-time visual tracking & live terminal dash

Render the spatial DAG cluster graphs and 3D wave topology for the active swarm:

```bash
keel swarm-status .keel/project.yaml --root .
keel-visual swarm .keel/project.yaml --root . --out keel-swarm.html
```

Optionally launch the localhost visualizer dashboard:
```bash
keel-visual swarm .keel/project.yaml --root . --serve --port 8766
```

## Step 5 — Synthesis & swarm recap report

Compile the overall multi-agent swarm outcome:
- Total issues planned, clustered, and executed.
- Worker success/failure breakdown.
- Landing mode used (Direct Batch vs Adaptive Funnel) and rebase self-healing stats.
- Final multi-agent jury deliberation consensus and compound learning synthesis.
- Record final completion:
  `keel activity .keel/project.yaml --root . --run-id "$RUN" --done`

<!-- keel-generated: surface=skills command=swarm keel_version=1.19.2 source_sha256=e2c2ce49a7841788c5f69e401f238846c7b59fcee47fb532ef4a4d1e281be856 generated_sha256=bab54f776eb3baed061daf5a6fac2717f8be4bafdd45c572fac2d6aad5a08421 -->
