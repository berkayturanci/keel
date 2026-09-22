# Keel Swarm — High-Concurrency Multi-Agent Orchestration

> ## ⚠️ Experimental — this subsystem does not land work
>
> The planning commands run. **A live run cannot produce a commit or a pull request**, and the
> reason is deeper than a missing flag:
>
> - `keel ship` — the *CLI subcommand*, registered as `dry ship assessment (tier, window, gates,
>   decision)` — never commits, pushes or opens a pull request, in any mode. It is not inert: it
>   runs `git diff` and executes the project's planned gates, and that gate run is **not** behind
>   `--live`, so a dry `swarm-run` over N issues still runs the whole gate suite N times, up to
>   `--max-workers` in parallel. What it never does is produce the commit. In keel's design the
>   implementation is done by the **agent** following `/keel:ship`, and the CLI assesses it;
>   swarm's workers spawn the CLI, so a worker cannot produce a commit in any mode.
> - On top of that, `swarm-run --live` never forwards `--live` to the child
>   ([#1269](https://github.com/berkayturanci/keel/issues/1269)): the child argv only gains
>   `--dry-run` when the run is dry, and nothing ever adds `--live`.
>
> Planning runs, but not on real scope. `--issue-title`, `--issue-body`, `--issue-label` and
> `--declared-file` take one value (or, for the repeatable ones, one list) that is shared by
> **every** issue — nothing fetches an issue's own text. So with no scope text and no
> directory-hinting label a multi-issue plan clusters synthetic per-issue globs and returns one
> `orthogonal_parallel` wave
> ([#1274](https://github.com/berkayturanci/keel/issues/1274)), and naming a path — in
> `--declared-file`, in the shared body, or via an `--issue-label` that maps to a directory hint —
> puts it in *every* issue's scope, so they all overlap and serialise. Neither is per-issue scope. `keel-visual swarm` rebuilds its scopes
> without predicted files, so its DAG is always one flat wave
> ([#1275](https://github.com/berkayturanci/keel/issues/1275),
> [#1280](https://github.com/berkayturanci/keel/issues/1280)).
>
> Landing is guarded, which is the one part that works as written: with `knobs.swarm_review_evidence`
> on — the default — `swarm-land` holds any cluster with no open PR, an unarmed gate, missing
> evidence, or a local head that differs from the reviewed PR head. Setting it to `false` is a
> documented opt-out, logged on every *live* landing (a dry preview with it off prints nothing and
> shows the clusters as landing); with it off, cluster branches merge with no PR and no evidence.
>
> The rest is tracked under the audit epic
> [#1281](https://github.com/berkayturanci/keel/issues/1281). Everything below describes the design
> and the code that exists; read it as architecture, not as a supported workflow.
>
> **Use [`/keel:ship`](../../src/keel/adapters/commands/ship.md) for work you need landed.**

**keel-swarm** is an additive, high-concurrency orchestration layer designed to coordinate
multiple AI developer agents working in parallel across complex backlogs. It transforms a list of
GitHub issues into a topologically ordered execution graph, partitions issues into conflict-free
clusters, executes them in isolated git worktrees, and lands them under a single-writer merge
lock with sequential `git merge --no-ff`.

---

## 1. Core Principles & Architecture

Keel Swarm is built on three core pillars:

1. **Backbone Immutability**: The keel core step machine (`s0`–`s12` in `src/keel/model.py`) is
   strictly immutable. Swarm does not alter or bypass backbone steps; instead, each parallel cluster
   worker executes a complete, standard `keel ship` run within its own isolated worktree.
2. **Pure Core / Thin I/O Separation**: Dependency analysis, clustering, wave partitioning, and landing
   decision logic are 100% pure and deterministic (`src/keel/swarm.py`). All filesystem, subprocess,
   and git mutations are confined to thin fail-soft runtime wrappers (`src/keel/swarm_runtime.py`,
   `src/keel/swarm_landing.py`).
3. **Deterministic Conflict Resolution**: Rather than naively merging branches or relying on LLMs
   to resolve arbitrary git merge conflicts, Swarm statically models predicted scopes, enforces
   worktree isolation, and keeps every wave's clusters mutually disjoint so landing never has to
   resolve a conflict in the first place.
4. **One Resolver For Who Runs What**: Swarm does not have its own idea of who implements.
   Every cluster is staffed by `keel.team.resolve_assignment` — the same function `keel ship`
   and `keel plan` call — with that cluster's role, risk tier and difficulty band. A cluster's
   lead can therefore hand its assignment straight to `keel ship` and the child resolves the
   same team from the same config.

### The org chart: CTO → team lead → worker

| Level | Who | Does | Never does |
| :--- | :--- | :--- | :--- |
| **CTO** | the `/keel:swarm` coordinator | clusters the backlog, launches one lead per cluster, lands the waves | implement, review, drive a child ship |
| **Team lead** | one subagent per cluster | runs that cluster's `/keel:ship` runs with the providers its `assignment` names; reports through the cluster's worker status record | re-score or re-staff its own cluster |
| **Worker** | one child `keel ship` run per issue | the standard `s0`–`s12` backbone in the cluster's worktree | reach outside its predicted scope |

The hierarchy is load-bearing, not stylistic: a lead that reports anywhere other than the
worker record is invisible to `keel swarm-status`, and a CTO that implements has no one left
to land the wave. `keel swarm-status` shows each worker's **lead** and **band**, which is how
an operator sees the chain at a glance.

```
                      ┌────────────────────────────────────────┐
                      │ Backlog Issues (#714, #715, #716, ...)  │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │      keel swarm-plan (Pure Core)       │
                      │  • Static Scope Prediction             │
                      │  • Disjointness Matrix & DAG Analysis  │
                      │  • Wave Tiering & Cluster Partitioning │
                      └───────────────────┬────────────────────┘
                                          │
                        ┌─────────────────┴─────────────────┐
                        ▼                                   ▼
              ┌───────────────────┐               ┌───────────────────┐
              │      Wave 1       │               │      Wave 2       │
              │  (Direct Batch)   │               │  (Direct Batch)   │
              └─────────┬─────────┘               └─────────┬─────────┘
                        │                                   │
       ┌────────────────┴────────────────┐                  │
       ▼                                 ▼                  ▼
┌──────────────┐                  ┌──────────────┐   ┌──────────────┐
│  Cluster C1  │                  │  Cluster C2  │   │  Cluster C3  │
│ Worktree W1  │                  │ Worktree W2  │   │ Worktree W3  │
│  (keel ship) │                  │  (keel ship) │   │  (keel ship) │
└──────┬───────┘                  └──────┬───────┘   └──────┬───────┘
       │                                 │                  │
       └────────────────┬────────────────┘                  │
                        ▼                                   │
              ┌───────────────────┐                         │
              │ keel swarm-land   │                         │
              │  • Batch Landing  │                         │
              │  • merge_lock     │                         │
              └─────────┬─────────┘                         │
                        │                                   │
                        ▼                                   ▼
              ┌───────────────────┐               ┌───────────────────┐
              │    base branch    │ ◄─────────────┤  keel swarm-land  │
              │   (Wave 1 Done)   │               │   (Wave 2 Done)   │
              └───────────────────┘               └───────────────────┘
```

---

## 2. Deterministic Static Analysis & DAG Clustering (`keel swarm-plan`)

Before any worker is spawned, `keel swarm-plan` inspects the issue set to predict file touch paths
and build a conflict matrix:

```bash
keel swarm-plan .keel/project.yaml --issues 714,715,716,717,720,721 --tree
```

### Difficulty Scoring & Per-Cluster Staffing

Every cluster comes out of the planner **scored** and **staffed**, and both appear in
`--json`:

```json
"difficulty": { "band": "hard", "score": 9, "tier": 3, "file_count": 4,
                "dependency_depth": 0,
                "signals": [{"name": "tier-3", "points": 4}, {"name": "files:4", "points": 2},
                            {"name": "priority:high", "points": 1}, {"name": "size:l", "points": 2}] },
"assignment": { "lead": {...}, "implementer": {...}, "effort": "high",
                "reviewers": [...], "review_panel": "reviewers", "warnings": [] }
```

A **difficulty band** answers *how much work is this*, which is a different question from the
risk tier's *how dangerous is this change*. A one-line fix to a tier-3 glob is dangerous and
trivial; a twelve-file docs migration is safe and long. The score is a pure function of four
inputs that already exist — no model is asked, and the same backlog always scores the same:

| Input | Points |
| :--- | :--- |
| resolved risk tier (from `knobs.tier3_globs`) | tier-1 `0`, tier-2 `2`, tier-3 `4` |
| predicted file count | `≥2` → `1`, `≥4` → `2`, `≥8` → `3` |
| `priority:` label | `priority:high` `1`, `priority:critical` `2` |
| `size:` label | `size:m` `1`, `size:l` `2`, `size:xl` `3` |
| dependency depth (issues in earlier waves this cluster conflicts with) | `1` each, **points** capped at `3` |

Bands: `0–2` **easy**, `3–5` **standard**, `6+` **hard**. Only signals worth non-zero points
are recorded, so a surprising band can be read back rather than guessed at.

The cap bites the *points*, never the recorded number: `dependency_depth` and the
`depends-on:<n>` signal both carry the depth actually observed. A cluster sitting on nine
earlier issues and one sitting on three score the same — past a few dependencies it is the
same problem, not a worse one — but they do not *read* the same, because the difference is
real and the plan is the only place anyone would see it.

The band selects a bench from
[`knobs.team.by_difficulty`](configuration.md#teamlead-teamby_difficulty-and-teamprofiles--staffing-a-batch);
`--team <profile>` selects one by name and outranks it; `--delegate`, `--review-delegate` and
`--effort` outrank both. **Scoring and staffing run after the partition and never feed back
into it** — changing `team.by_difficulty` changes who runs a cluster and cannot change which
wave it lands in, which is what makes the two independently reviewable.

```yaml
knobs:
  team:
    by_difficulty:
      easy: { implement: { provider: ollama, model: qwen2.5-coder } }
      hard:
        lead:      { provider: claude, model: opus }
        implement: { provider: codex, effort: high }
        review:    jury
```

### Scope Prediction Heuristics
- **Explicit Labels & Roles**: A recognised role maps to a deterministic glob — `docs` to
  `docs/*`, `website` to `website/*`.
- **Title / Body Keyword Parsing**: Mentions of files (`src/keel/*.py`, `tests/test_*.py`) or modules
  automatically expand the predicted scope list.
- **Disjointness Matrix**: If two issues touch non-overlapping directory trees or orthogonal subsystems,
  they are marked disjoint ($D_{ij} = 1$). If scopes intersect, a conflict edge is created ($C_{ij} = 1$).
- **Topological Wave Partitioning**: Disjoint clusters are scheduled in Wave 1. Dependent or conflicting
  clusters are placed in subsequent waves (Wave 2, Wave 3...).

### ASCII plan tree
The `--tree` flag prints the plan as a terminal tree:

```
╭──────────────────────────────────────────────────────────────╮
│ 🐝 Keel Swarm Plan — swarm-20260815-091500                  │
│ Issues: 3   │ Waves: 1   │ Direct Landing Waves: 1  │
╰──────────────────────────────────────────────────────────────╯

⚡ Wave 1 [orthogonal_parallel] — Direct Batch Landing
├── 📦 Cluster cluster-1-714 (#714) [docs]
│   ├── Scope: docs/proposals/keel-swarm.md, docs/keel/comparison.md
│   ├── Difficulty: standard (score 3, tier 2, 2 file(s), depth 0)
│   └── Team: lead claude → implementer claude, review claude, claude
├── 📦 Cluster cluster-1-715 (#715) [core]
│   ├── Scope: src/keel/swarm.py, tests/test_swarm.py
│   ├── Difficulty: standard (score 3, tier 2, 2 file(s), depth 0)
│   └── Team: lead claude → implementer claude, review claude, claude
└── 📦 Cluster cluster-1-721 (#721) [visual]
    ├── Scope: keel-visual/src/app.py, keel-visual/tests/test_app.py
    ├── Difficulty: standard (score 3, tier 2, 2 file(s), depth 0)
    └── Team: lead claude → implementer claude, review claude, claude
```

(The three issues above touch disjoint trees, so they share one wave. Issues whose predicted
scopes overlap are pushed into later waves instead — each wave stays internally disjoint.)

---

## 3. Isolated Multi-Worktree Runtime (`keel swarm-run`)

Parallel execution runs across isolated git worktrees created under
`.keel/worktrees/<swarm_id>/<cluster_id>/` (the swarm id is `swarm-YYYYMMDD-HHMMSS`):

```bash
keel swarm-run .keel/project.yaml --root . --issues 714,715,716,717 --live
```

### Worktree Lifecycle & Isolation
1. **Creation**: Dedicated worktrees are branched from the local `main` onto
   `swarm/<swarm_id>/<cluster_id>`.
2. **Execution**: One **team lead** per cluster dispatches the implementer its `assignment`
   named, to execute the full `s0`–`s12` backbone. The lead appends the cluster's team to every
   child ship — `--delegate <implementer>`, one `--review-delegate` per staffed reviewer
   slot, `--role`, and `--effort`/`--team` for the bench the cluster was staffed from — so
   the child reproduces the parent's resolution instead of quietly deriving a different
   team from config alone. `keel ship` accepts all five.
3. **Rebalancing on failure**: when a cluster's issue fails, `rebalance_swarm_plan` drops the
   clusters carrying that issue from the remaining waves, so a failed prerequisite does not drag
   later waves down; the independent, disjoint clusters proceed uninterrupted. (There is no runtime
   scope audit — clusters are kept off each other's files by plan-time overlap partitioning and
   per-worktree isolation, not by watching what a worker writes.)
4. **Cleanup**: On completion or error, worktrees are pruned cleanly without leaving orphaned locks.

### Status board (`keel swarm-status`)
Print the swarm's clusters — each one's lead, difficulty band, role, step and status
(`running` / `passed` / `failed`) — from the persisted run state. It is a one-shot render of
that state, not a live feed; re-run it to refresh:

```bash
keel swarm-status .keel/project.yaml --root .
```

---

## 4. Landing (`keel swarm-land`)

Landing is coordinated by `src/keel/swarm_landing.py` under the atomic `merge_lock`
(`.keel/state/locks/merge-<sha12>.lock`):

```bash
keel swarm-land .keel/project.yaml --root . --wave 1 --live
```

### What landing actually does

Each cluster branch is merged into the configured base branch with `git merge --no-ff`, **one
after another** inside the lock. A merge that conflicts is `git merge --abort`ed, the base is left
untouched, and the cluster is reported `merge failed`.

**Every wave lands in direct-batch mode today.** `build_swarm_plan` only admits an issue to a wave
it conflicts with nothing in, so a wave's clusters are always mutually disjoint; the CLI also passes
no PR diff map, so `evaluate_wave_landing_mode` returns `direct_batch` (`orthogonal_diff_trees`, or
`single_cluster` for a wave of one) every time.

`swarm_landing.py` *does* implement an adaptive funnel — rebase each overlapping cluster onto the
moved base, heal adjacent conflicts with the deterministic marker resolver, hold anything the
resolver touched for re-review, and rewind a held branch. That path is reachable only by a library
caller that supplies `pr_diff_map`; **no `keel swarm-land` invocation selects it.** It is described
in [cli.md](cli.md) for callers who drive the library directly.

### Review Evidence Gate (`knobs.swarm_review_evidence`)

Before a **live** landing, every cluster branch's open PR must pass the same pre-merge
review-evidence verification `keel merge` enforces at ship s10 (#828): an armed gate,
the tier-derived verdict count, and verdicts pinned to the PR head. A cluster that does
not verify is **held** — reported with its reason, never merged — and a live wave with
any held cluster exits non-zero, so automation cannot read "refused to land unreviewed
code" as success. The gate also runs in dry runs (the checks are read-only), which report
`would hold: <reason>` per cluster.

`knobs.swarm_review_evidence: false` is the explicit opt-out, and it is loud: a live
`swarm-land` prints `swarm review evidence: OFF by config` to stderr, because `swarm-land`
runs no CI of its own — with the gate off, clusters land unverified. See
[configuration.md](configuration.md#swarm_review_evidence) and the
`keel swarm-land` section of [cli.md](cli.md).

---

## 5. Visual Dashboard Integration (`keel-visual swarm`)

Swarm integrates directly with the companion package `keel-visual` to provide rich spatial observability:

```bash
# Generate a static HTML report
keel-visual swarm .keel/project.yaml --root . --out keel-swarm.html

# Serve that rendered report on localhost (a snapshot — re-run to refresh)
keel-visual swarm .keel/project.yaml --root . --serve --port 8766
```

### Visual Features:
- **2D DAG Cluster Partition View**: Interactive graph displaying wave tiers, cluster cards, issue
  pills, and role badges.
- **Pseudo-3D Multi-Wave Topology**: An HTML5 Canvas renderer projecting the stacked wave layers as
  a pseudo-3D scene, with drag-to-rotate and scroll-to-zoom.
- **Worker Matrix**: Worker cards showing each cluster's `running` / `passed` / `failed` state,
  its role badge, and the recorded `details` string.

The rendered page is a snapshot of the run state at render time; re-run `keel-visual swarm` to
refresh it. (The continuously polling board is `keel-visual serve`, which renders *ship* runs —
a different view from this one.)

---

## 6. Review Evidence & Compound Learning

Swarm does not run a jury of its own. Review and learning happen inside each cluster's own
`keel ship` run, and swarm gates landing on the result:

- **Per-cluster review**: each cluster runs the project's configured review. When the project hands
  that cluster's tier to the panel (`knobs.team.review.by_tier."<n>": jury`, typically tier-3), the
  review is ai-jury's cross-vendor panel; otherwise it is the host reviewers (plus the `jury` gate,
  if the project lists one in `gates:`). Before a branch may land, swarm applies a **review-evidence
  check** against its head-pinned `keel.review-verdict.v1` records — the same evidence gate a single
  `keel ship` uses. It is a per-branch gate, not a swarm-wide vote.
- **Compound learning**: each `keel ship` records a `compound-learning:` marker on its run ledger
  and PR (`pr=<N> status=<applied|deferred|skipped:reason>`), so the lesson rides with the merge.
  Swarm surfaces those per-cluster markers in its recap; it does not synthesize a shared knowledge
  store.

---

## 7. Competitive Comparison Matrix

| Feature / Capability | Keel Swarm | CrewAI | AutoGen | MetaGPT | Devin / OpenHands |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Deterministic Static Analysis** | **Yes (`swarm-plan`)** | No (LLM prompt loop) | No | No (SOP templates) | No |
| **Deterministic Difficulty Scoring** | **Yes (pure, signal-attributed)** | No | No | No | No |
| **Declared Org Chart (CTO/lead/worker)** | **Yes (`knobs.team`, one resolver)** | Role prompts | Conversational | SOP roles | Single agent |
| **Fixed Backbone Machine** | **Yes (`s0`–`s12` immutable)** | No | No | No | No |
| **Isolated Git Worktrees** | **Yes (`.keel/worktrees/`)** | No (shared workspace) | No | No (file overwrite) | Docker container |
| **Batch landing under one writer lock** | **Yes (sequential `merge --no-ff`)** | No | No | No | PR per run |
| **Atomic Single-Host Lock** | **Yes (`merge_lock`)** | No | No | No | No |
| **Fail-soft conflict handling** | **Yes (`merge --abort`, cluster reported failed)** | No | No | No | Manual |
| **Per-Branch Review-Evidence Gate** | **Yes (cross-vendor panel per cluster, when configured)** | No | Conversational | No | Single Agent |
| **2D DAG & pseudo-3D snapshot** | **Yes (`keel-visual`, rendered)** | Basic Tree | Plotly / None | Static Diagrams | Web Terminal |

---

## 8. Risk & Failure Mitigations

| Risk / Failure Scenario | Detection Mechanism | Fail-Soft Mitigation |
| :--- | :--- | :--- |
| **A cluster changes files another cluster also touches** | Plan-time static file-overlap partitioning (disjoint trees only share a wave) + isolated per-cluster worktrees | Overlapping clusters are sequenced into later waves; a failed cluster is dropped from the remaining waves. |
| **Merge conflict during landing** | `git merge` non-zero exit code | Automatic `git merge --abort`; the base branch remains untouched; the cluster is reported `merge failed`. |
| **Concurrent Merge Race Condition** | `merge_lock` file mutex | Atomic `mkdir`-based lock; a second writer raises `LockError` rather than retrying, so landing is single-writer by refusal. |
| **Worker Subprocess Crash / OOM** | Subprocess exit status monitoring | Fail-soft error capture in `SwarmRunState`; remaining parallel workers continue unimpeded. |
| **Missing or unreadable run state** | `load_swarm_state` JSON/Value/Key errors | Fails soft to no state rather than raising; `swarm-land` rebuilds the plan from `--issues`, so a lost state file costs the board, not the landing. |
| **A cluster scored lighter than it turns out to be** | The lead's own progress against the plan | The lead reports through its worker record and the CTO re-plans; a lead never re-staffs itself, so the run's team stays the one the plan published. |
| **`--team` names a bench that is not configured** | `assignment.warnings` at plan time | The run falls back to the configured policy and says so; the name is never silently ignored. |
