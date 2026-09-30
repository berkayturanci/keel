---
description: EXPERIMENTAL — a multi-agent swarm coordinator that clusters backlog issues, executes parallel waves in isolated worktrees, and lands them under a single-writer merge lock. Planning runs; a live run lands nothing yet (#1281). Use /keel:ship for work that must merge.
argument-hint: "[issue numbers...] [--plan-only] [--tree] [--visual] [--delegate <provider>] [--review-delegate <provider>] [--effort <low|medium|high>] [--team <profile>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(jury:*), Read, Edit, Write, Agent
---

# /keel:swarm

## ⚠️ Experimental — do not use this to land work

`keel swarm-plan`, `--plan-only` and `--tree` run and render a plan. A dry `swarm-run`'s worker is
`keel ship` the CLI subcommand — a *dry ship assessment* that reports tier, window, gates and a
decision and never commits, pushes or opens a PR. `swarm-run --live` (#1400) is different: keel
dispatches each cluster's implementer seat (`--delegate`, else `knobs.team.implement`; it must be an
agent CLI such as `claude`, `codex` or `agy` — a `subagent:` seat refuses the run) in the cluster's
own worktree, commits the result, runs the gates, pushes `swarm/<swarm_id>/<cluster_id>` and opens
one pull request per cluster. It needs the **operator's** consent — `--approve-scope
filesystem,git,github --operator <name>` — which you ask the user for and never supply on your own,
and its pull requests carry **no review evidence**: nothing reviews or lands them yet, so
`swarm-land` holds them (#1287).

It is not free, though: that CLI runs `git diff` and executes the project's planned gates, and the
gate run is **not** behind `--live`. A dry `swarm-run` over N issues runs the whole gate suite N
times — one at a time, since a dry run has no worktrees and the runs share your checkout
(#1288). Budget for that before you start one.

Planning reads each issue's scope from the issue (#1274): a `Scope: a/b.py, docs/*` line or a
`## Scope` bullet list in its body, else its `area:<name>` labels mapped through
`policy_pack.scan.areas`. An issue that declares none — or that `gh` cannot read — is planned as
`*` and serialised against every other issue, and stderr names it; paths its text merely mentions
do not make it disjoint. So a backlog without `Scope:` lines plans one wave per issue, and
every wave after the first is `sequential_dependent`, which `swarm-land` refuses: land wave 1,
re-plan the rest, land again. To give an
issue a scope for this run, pass `--issue-scope <n>=<glob>[,<glob>…]` (repeatable, on
`swarm-plan`, `swarm-run` and `swarm-land` alike — pass the same ones to all three), and never
pass `--issue-title`/`--issue-body`/`--issue-label`/`--declared-file` beside several issues:
they describe one issue and are refused. `keel-visual swarm` always renders a flat DAG (#1275, #1280).

So: **`--plan-only` is the one that stops**, and it already renders the ASCII tree — `--tree` is
passed on the `swarm-plan` calls either way, so adding it changes nothing. `--visual` is a
different matter twice over: it is read at Step 4, *after* Step 2, so on its own
`/keel:swarm <issues> --visual` walks straight into `swarm-run` and `swarm-land`; and
with `--plan-only` it never runs at all — and could not show anything if it did, because
`keel-visual swarm` reads the state file only `swarm-run` writes and falls back to an empty
board without it. That runs the N child gate suites above, one at a time in your checkout —
for a run that lands nothing. Read the plan it renders as "what I passed", not
"per-issue scope". For anything
the user expects to be **merged**, say plainly that swarm cannot do it and run `/keel:ship` per
issue instead.

Do not hand-drive the children to work around this. With `knobs.swarm_review_evidence` on — the
default — `swarm-land` would hold the clusters anyway: no open PR, an unarmed gate, missing
evidence, or a head that does not match the reviewed one. (With it off, a documented and logged
opt-out, they would merge unverified.) Either way you would be skipping the per-issue ledger and
the backbone that `/keel:ship` gives you. The rest
is tracked under the audit epic #1281.

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
`git merge --no-ff` into the local base branch — a local merge that pushes nothing.

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
- Read stderr and each issue's `scope_source` in `issue_scopes`: `default` (scope `*`) means
  the issue declared no scope — or could not be read — and so runs alone, in a wave
  `swarm-land` refuses until the waves before it land and the rest is re-planned. Report those issues
  to the operator instead of inventing a scope for them.
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

```bash
keel swarm-run .keel/project.yaml --root . --issues <n,n,n>
```

This is the dry run: it assesses each cluster and commits nothing. A worker gets its own git
worktree (`.keel/worktrees/<swarm_id>/<cluster_id>/`, branch `swarm/<swarm_id>/<cluster_id>`)
only when worktrees are enabled **and** the run is not dry — so in the one mode this command
allows, no worktree is created: each cluster's child assessment runs in your own checkout,
one at a time (#1288). Add `--live` only when the user has consented to the scopes it needs
(see the top of this command) and wants one unreviewed pull request per cluster; otherwise the
implementation is the leads' work, below.

- Spawn **one team lead subagent per cluster**, briefed with that cluster's `assignment`
  and `difficulty` verbatim. The lead runs the cluster's issues through the standard
  `keel ship` backbone steps (`s0`–`s12`) in an isolated worktree of its own, cut from
  `base_branch` (config) — `swarm-run`'s dry run did not create one.
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
- When a cluster's issue fails, `rebalance_swarm_plan` drops the clusters carrying that issue from the remaining waves; there is no runtime file-divergence detection — clusters are kept apart by plan-time overlap partitioning (and, in a non-dry run, per-worktree isolation).
- Track live worker states with `keel swarm-status` — the board's `Lead` and `Band` columns
  are how the operator sees which lead owns which cluster and why it drew its provider.

## Step 3 — Batch landing under the merge lock

When an execution wave completes, land all passing clusters onto the project's
`base_branch` (config — `keel swarm-land` reads it; never assume a branch name):

```bash
keel swarm-land .keel/project.yaml --root . --issues <n,n,n> --wave <n> --live
```

- The landing mode is **derived from the plan's wave mode**, not passed on the command line.
- **Orthogonal Batch Landing** (wave 1, and any later wave none of whose clusters depends on an earlier wave's issue): disjoint diff trees are merged into the local `base_branch` with `git merge --no-ff`, sequentially under the atomic `merge_lock`.
- **A dependent wave is refused.** A `sequential_dependent` wave — in a fresh plan, every wave after the first — had its branches cut before the earlier wave it depends on landed, so `swarm-land --wave N` refuses it, dry run or live: no checkout, no merge, exit 1, `"mode": "refused"` with the reason in `refused`. Land the earlier wave, then re-plan the remaining issues (`keel swarm-plan` / `swarm-run` without the landed ones) and land again. The library's adaptive rebase funnel is not reached from this command until #1266 feeds its overlap check real diffs.
- **The landing is a local merge only.** `swarm-land` does not push, and it does not open or merge a pull request: a cluster reported `merged` is merged in the local base branch, its pull request stays open, and `origin` is unchanged (#1287). Pushing is the operator's step — leave it to them, and do not report the work as landed on the repository. A protected base branch refuses the push anyway; work that has to reach the repository goes through `/keel:ship` and `keel merge`, one pull request at a time.

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
- Landing outcome per cluster: merged into the local base branch (not pushed; its pull request still open), or `merge failed` / held with its reason.
- Per-cluster review outcome (the configured review, or the ai-jury panel on tier-3) and each cluster's `compound-learning:` ledger marker.
- Record final completion:
  `keel activity .keel/project.yaml --root . --run-id "$RUN" --done`

<!-- keel-generated: surface=claude command=swarm keel_version=1.25.0 source_sha256=9110ba5e915b47592120b674b12a63a437551dc6a2806b3da814e22031768b87 generated_sha256=9110ba5e915b47592120b674b12a63a437551dc6a2806b3da814e22031768b87 -->
