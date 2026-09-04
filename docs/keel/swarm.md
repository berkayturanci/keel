# Keel Swarm — High-Concurrency Multi-Agent Orchestration

**keel-swarm** is an additive, high-concurrency orchestration layer designed to coordinate
multiple AI developer agents working in parallel across complex backlogs. It transforms a list of
GitHub issues into a topologically ordered execution graph, partitions issues into conflict-free
clusters, executes them in isolated git worktrees, and lands them cleanly through orthogonal batch
merges or self-healing funnel rebases.

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
   worktree isolation, and applies dual-mode landing (Direct Batch vs Adaptive Funnel).
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
              │  (Direct Batch)   │               │ (Adaptive Funnel) │
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
              │    origin/main    │ ◄─────────────┤ Rebase & Funnel   │
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
- **Explicit Labels & Roles**: Issues with `role:docs` or `role:frontend` map to deterministic globs
  (e.g. `docs/**`, `website/**`).
- **Title / Body Keyword Parsing**: Mentions of files (`src/keel/*.py`, `tests/test_*.py`) or modules
  automatically expand the predicted scope list.
- **Disjointness Matrix**: If two issues touch non-overlapping directory trees or orthogonal subsystems,
  they are marked disjoint ($D_{ij} = 1$). If scopes intersect, a conflict edge is created ($C_{ij} = 1$).
- **Topological Wave Partitioning**: Disjoint clusters are scheduled in Wave 1. Dependent or conflicting
  clusters are placed in subsequent waves (Wave 2, Wave 3...).

### ASCII DAG Tree Visualizer
The `--tree` flag renders a full terminal diagram:

```
┌─────────────────────────────────────────────────────────────┐
│                   KEEL SWARM EXECUTION PLAN                 │
│                 Swarm ID: swarm-2026-08-15                  │
└─────────────────────────────────────────────────────────────┘

  WAVE 1 [Direct Orthogonal Batch Landing]
  ├── CLUSTER: cluster-1 [Role: docs]
  │   ├── Issue #714: Author comprehensive architecture proposal
  │   └── Scopes: docs/proposals/keel-swarm.md, docs/keel/comparison.md
  └── CLUSTER: cluster-2 [Role: core]
      ├── Issue #715: Implement static dependency analysis
      └── Scopes: src/keel/swarm.py, tests/test_swarm.py

  WAVE 2 [Adaptive Sequential Funnel]
  └── CLUSTER: cluster-3 [Role: visual]
      ├── Issue #721: Enhance keel-visual with 2D DAG & 3D topology
      ├── Dependencies: #715
      └── Scopes: keel-visual/src/**, keel-visual/tests/**
```

---

## 3. Isolated Multi-Worktree Runtime (`keel swarm-run`)

Parallel execution runs across isolated git worktrees created under `.keel/worktrees/swarm/<cluster_id>/`:

```bash
keel swarm-run .keel/project.yaml --root . --issues 714,715,716,717 --live
```

### Worktree Lifecycle & Isolation
1. **Creation**: Dedicated worktrees are branched from `origin/main` (e.g. `swarm/cluster-1`).
2. **Execution**: One **team lead** per cluster dispatches the implementer its `assignment`
   named, to execute steps `s0` through `s9`. The lead appends the cluster's team to every
   child ship — `--delegate <implementer>`, one `--review-delegate` per staffed reviewer
   slot, `--role`, and `--effort`/`--team` for the bench the cluster was staffed from — so
   the child reproduces the parent's resolution instead of quietly deriving a different
   team from config alone. `keel ship` accepts all five.
3. **Dynamic Rebalancing**: If a worker modifies files outside its predicted scope that overlap with another
   active cluster, the rebalancer detects the drift, halts conflicting execution in the current wave,
   and reschedules the cluster to the next wave tier.
4. **Cleanup**: On completion or error, worktrees are pruned cleanly without leaving orphaned locks.

### Live Status Dashboard (`keel swarm-status`)
Inspect active workers, their lead and difficulty band, current execution steps, and cluster
health in real time:

```bash
keel swarm-status .keel/project.yaml --root .
```

---

## 4. Dual-Mode Landing & Drift Self-Healing (`keel swarm-land`)

Landing is coordinated by `src/keel/swarm_landing.py` protected by the atomic `merge_lock`
(`.keel/state/merge.lock`):

```bash
keel swarm-land .keel/project.yaml --root . --wave 1 --live
```

### Landing Modes:
1. **Direct Orthogonal Batch Landing (`batch`)**:
   - Activated when all clusters in the wave have verified disjoint diff trees.
   - All cluster branches are fast-forwarded or merged into `main` in parallel.
   - Zero rebase overhead and maximum throughput.
2. **Adaptive Atomic Funnel Landing (`funnel`)**:
   - Activated when clusters share base dependencies or have overlapping file touches.
   - Clusters are merged sequentially. Before each merge, the cluster branch is rebased onto the newly
     updated `main`.
   - **Self-Healing Rebase**: If `git rebase` succeeds cleanly, the merge proceeds. If an unresolvable
     conflict occurs, `git rebase --abort` is immediately executed, leaving `main` and the worktree clean,
     while recording a structured failure report.

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
# Generate static HTML report
keel-visual swarm .keel/project.yaml --root . --out keel-swarm.html

# Start live localhost dashboard
keel-visual swarm .keel/project.yaml --root . --serve --port 8766
```

### Visual Features:
- **2D DAG Cluster Partition View**: Interactive HTML/SVG graph displaying wave tiers, cluster cards,
  issue pills, role badges, and conflict connection lines.
- **3D Multi-Wave Spatial Topology**: WebGL/HTML5 Canvas renderer projecting stacked wave layers in 3D
  space with interactive orbit rotation, zooming, and depth slicing.
- **Live Worker Matrix**: Real-time worker execution cards with active step indicators, role icons,
  and log summaries.

---

## 6. AI Jury & Compound Learning Synthesis

Swarm coordinates AI Jury deliberation and Compound Learning across all parallel workers:

- **AI Jury Deliberation**: Each cluster run produces a multi-agent review panel outcome. Swarm aggregates
  all individual verdicts into an overall unanimous consensus verdict before authorizing wave landing.
- **Compound Learning Synthesis**: Post-merge learning artifacts (`.keel/knowledge/`) from all parallel workers
  are synthesized into today's collective memory without duplication or knowledge overwrite.

---

## 7. Competitive Comparison Matrix

| Feature / Capability | Keel Swarm | CrewAI | AutoGen | MetaGPT | Devin / OpenHands |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Deterministic Static Analysis** | **Yes (`swarm-plan`)** | No (LLM prompt loop) | No | No (SOP templates) | No |
| **Deterministic Difficulty Scoring** | **Yes (pure, signal-attributed)** | No | No | No | No |
| **Declared Org Chart (CTO/lead/worker)** | **Yes (`knobs.team`, one resolver)** | Role prompts | Conversational | SOP roles | Single agent |
| **Fixed Backbone Machine** | **Yes (`s0`–`s12` immutable)** | No | No | No | No |
| **Isolated Git Worktrees** | **Yes (`.keel/worktrees/`)** | No (shared workspace) | No | No (file overwrite) | Docker container |
| **Dual-Mode Batch Landing** | **Yes (Direct + Funnel)** | No | No | No | PR per run |
| **Atomic Single-Host Lock** | **Yes (`merge_lock`)** | No | No | No | No |
| **Drift Self-Healing Rebase** | **Yes (fail-soft abort)** | No | No | No | Manual |
| **Multi-Agent AI Jury Gate** | **Yes (Cross-Vendor)** | No | Conversational | No | Single Agent |
| **2D DAG & 3D Spatial Viz** | **Yes (`keel-visual`)** | Basic Tree | Plotly / None | Static Diagrams | Web Terminal |

---

## 8. Risk & Failure Mitigations

| Risk / Failure Scenario | Detection Mechanism | Fail-Soft Mitigation |
| :--- | :--- | :--- |
| **Scope Divergence during Implementation** | `keel swarm-run` post-step file audit | Dynamic rebalancing: cluster is halted in current wave and re-queued to next wave. |
| **Rebase Conflict during Funnel Landing** | `git rebase` non-zero exit code | Automatic `git rebase --abort`; `main` remains untouched; worker marked `failed_rebase`. |
| **Concurrent Merge Race Condition** | `merge_lock` file mutex | Atomic `mkdir`-based lock with timeout retry; guarantees single-writer landing. |
| **Worker Subprocess Crash / OOM** | Subprocess exit status monitoring | Fail-soft error capture in `SwarmRunState`; remaining parallel workers continue unimpeded. |
| **Stale State Discovery** | SHA-256 fingerprint validation | Fallback to reconstructed safe plan; corrupt JSON files fail soft to empty state. |
| **A cluster scored lighter than it turns out to be** | The lead's own progress against the plan | The lead reports through its worker record and the CTO re-plans; a lead never re-staffs itself, so the run's team stays the one the plan published. |
| **`--team` names a bench that is not configured** | `assignment.warnings` at plan time | The run falls back to the configured policy and says so; the name is never silently ignored. |
