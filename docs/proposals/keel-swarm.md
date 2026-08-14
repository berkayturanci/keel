# Proposal: Keel Swarm — Multi-Agent Swarm Orchestration, Conflict-Free Clustering & Parallel Delivery

- **Issue:** [#714](https://github.com/berkayturanci/keel/issues/714)
- **Milestone:** [v1.15.0 — Swarm Orchestration, Conflict-Free Clustering & Parallel Delivery](https://github.com/berkayturanci/keel/milestone/12)
- **Status:** Proposed
- **Decision Drivers:** `AGENTS.md` core invariants (pure-core/thin-I/O split, fixed backbone `s0`–`s12`, atomic merge lock, commit-bound evidence chain, stdlib-first determinism), existing worktree workspace management (`src/keel/workspace.py`, `branchscope.py`), atomic locking (`src/keel/lock.py`), multi-run telemetry (`src/keel/activity.py`), and multi-vendor AI Jury (`src/keel/jury.py`).

---

## 1. Executive Summary & Vision

Modern coding agents excel at solving isolated issues, but scaling them across large backlogs currently hits a severe operational bottleneck: **serial execution**. While `keel:work-block` and `keel:overnight` provide safe multi-issue iteration, they process issues sequentially ($O(N)$ elapsed time). A backlog of 20 independent tasks (e.g. 5 documentation updates, 5 website UI fixes, 5 CLI subcommands, and 5 independent unit test additions) takes 20 times the individual issue latency, leaving computing resources and multi-agent capacity underutilized.

Conversely, naive "swarm" systems (CrewAI, AutoGen, OpenAI Swarm) attempt unconstrained parallel execution and invariably produce **merge collision chaos, non-deterministic drift, unverified code landing, and broken repository main branches**.

**Keel Swarm closes this gap.** It introduces an invariant-guided multi-agent swarm architecture:
1. **Deterministic Dependency Graph (DAG) Clustering**: Statically estimates the blast radius of candidate issues and partitions them into orthogonal (disjoint) parallel clusters (**Wave 1, Wave 2, ...**).
2. **Physical Git Worktree Isolation**: Dispatches worker agents into dedicated `.keel/workspaces/swarm-<id>/` environments.
3. **Dynamic Cluster Rebalancing**: If a worker expands its scope during implementation, the orchestrator dynamically re-evaluates the DAG, re-assigns workers, or defers conflicting tasks without aborting independent siblings.
4. **Dual-Mode Landing Engine**:
   - **Direct Orthogonal Batch Landing**: Disjoint file trees merge directly in parallel without serial queue delays.
   - **Adaptive Atomic Funnel**: Overlapping trees funnel through Keel's atomic `merge_lock` with automated rebase and `s9 fixloop` conflict self-healing.
5. **Multi-Layer Evidence & Jury Integration**: Every parallel PR preserves its commit-SHA-locked evidence record, multi-vendor AI Jury deliberation, and compound learning markers.
6. **Unified Terminal & 3D Visual Telemetry**: Live Unicode/ASCII DAG trees directly in the CLI (`keel swarm-plan --tree`, `keel swarm-status`) paired with `keel-visual` 2D cluster partitions and 3D WebGL galaxy scenes.

---

## 2. Competitive Landscape & Failure Mode Analysis

| Dimension | CrewAI / AutoGen / Magentic-One | OpenAI Swarm | MetaGPT / ChatDev | **Keel Swarm** ⚓ |
|---|---|---|---|---|
| **Repository & Git Tree Awareness** | ❌ None (operates in text memory) | ❌ None (stateless handoff routines) | ❌ None (virtual conversational roles) | ✅ **Physical Git Worktree Isolation** (`.keel/workspaces/swarm-<id>`) |
| **Dependency & Collision Analysis** | ❌ None (agents overwrite each other) | ❌ None | ❌ None | ✅ **Deterministic Static DAG & AST Blast Radius Matrix** |
| **Code Verification & Quality Gates** | ❌ LLM self-reflection only | ❌ None | ❌ None | ✅ **100% Compiler, Linter, Security Presets & Test Gates** |
| **Landing & Merge Safety** | ❌ Merge collision chaos | ❌ Does not manage git merges | ❌ Does not manage git merges | ✅ **Dual-Mode: Direct Batch Landing + Atomic Lock Funnel** |
| **Conflict Recovery** | ❌ Fails completely on collision | ❌ None | ❌ None | ✅ **Automated Rebase + `s9 fixloop` Self-Healing** |
| **Auditable Evidence Chain** | ❌ Unstructured verbose chat logs | ❌ None | ❌ None | ✅ **Commit-SHA-Bound Multi-Vendor Evidence & Jury Proof** |
| **Real-Time Observability** | ⚠️ Flat terminal logs | ❌ None | ⚠️ Web chat logs | ✅ **Terminal ASCII DAG + `keel-visual` 2D/3D Swarm Board** |

### Why Naive Swarms Fail in Production
1. **Hallucinated Completion**: An agent declares *"Task complete!"* without running tests, linters, or verifying compiler passes. Keel Swarm forces every worker through the full `s0`–`s12` backbone with mandatory gate enforcement.
2. **Merge Collision Disaster**: Five agents edit the repository concurrently on shared branches, leading to corrupt git trees or silent overwrites. Keel Swarm enforces disjoint clustering and physical worktree isolation.
3. **Approval Drift**: Code approved by a reviewer changes before merge without re-validation. Keel Swarm locks evidence and jury verdicts to exact commit SHAs.

---

## 3. Architecture & Core Lifecycle

```
                 [ Backlog / Queue: N Issues ]
                               │
                               ▼
 ┌───────────────────────────────────────────────────────────┐
 │ Phase 1: Scope & DAG Dependency Clustering Engine         │
 │ (keel swarm-plan <project.yaml> --issues 101,102,103,104) │
 │ - Predict file touchsets (AST hints, globs, history)      │
 │ - Compute pairwise disjointness matrix                    │
 │ - Partition into Waves (Wave 1: Orthogonal, Wave 2: Seq)  │
 └───────────────────────────────────────────────────────────┘
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼ (Wave 1 - Worker 1)   ▼ (Wave 1 - Worker 2)   ▼ (Wave 1 - Worker 3)
 ┌───────────┐           ┌───────────┐           ┌───────────┐
 │Worktree #1│           │Worktree #2│           │Worktree #3│
 │Agent 1    │           │Agent 2    │           │Agent 3    │
 │(s0 → s9)  │           │(s0 → s9)  │           │(s0 → s9)  │
 └───────────┘           └───────────┘           └───────────┘
       │                       │                       │
       │ (Dynamic Collision?)  │                       │
       │ ──► Dynamic Re-cluster│                       │
       │                       │                       │
       └───────────────────────┼───────────────────────┘
                               │
                               ▼
 ┌───────────────────────────────────────────────────────────┐
 │ Phase 3: Dual-Mode Landing Engine (keel swarm-land)       │
 │                                                           │
 │ ├── [Path A: Direct Orthogonal Batch Landing]             │
 │ │   - Verified 100% disjoint file trees                   │
 │ │   - Land PRs in parallel / single atomic integration    │
 │ │                                                         │
 │ └── [Path B: Adaptive Atomic Funnel & Self-Healing]       │
 │     - Overlapping file paths funnel through merge_lock    │
 │     - Auto-rebase against updated main HEAD               │
 │     - If conflict: route to s9 fixloop for auto-resolve   │
 └───────────────────────────────────────────────────────────┘
                               │
                               ▼
 ┌───────────────────────────────────────────────────────────┐
 │ Phase 4: Swarm Evidence, Jury & Compound Synthesis        │
 │ - Commit-bound evidence chain for every issue             │
 │ - Optional Swarm Batch Integration Jury verification      │
 │ - Durable compound learning synthesis into project memory │
 └───────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Component Specifications

### 4.1. Static Scope & DAG Clustering (`src/keel/swarm.py`)
- **Pure Core**: Implements graph partitioning using Python's stdlib `graphlib.TopologicalSorter`.
- **Scope Inference**: Extracts predicted file paths from issue metadata (body file paths, issue title, policy pack labels, previous commit history).
- **Disjointness Matrix**: If $\text{Scope}(I_A) \cap \text{Scope}(I_B) = \emptyset$, issues $I_A$ and $I_B$ are orthogonal and placed into the same concurrent wave. If $\text{Scope}(I_A) \cap \text{Scope}(I_C) \neq \emptyset$, a dependency edge $I_A \to I_C$ is created.
- **Output**: Deterministic JSON plan schema:
  ```json
  {
    "swarm_id": "swarm-20260815-001",
    "waves": [
      {
        "wave_index": 1,
        "mode": "orthogonal_parallel",
        "eligible_direct_landing": true,
        "clusters": [
          {"issue": 715, "role": "core", "predicted_scope": ["src/keel/swarm.py", "tests/test_swarm.py"]},
          {"issue": 720, "role": "cli", "predicted_scope": ["src/keel/cli.py", "tests/test_cli.py"]},
          {"issue": 721, "role": "visual", "predicted_scope": ["keel-visual/*"]}
        ]
      },
      {
        "wave_index": 2,
        "mode": "sequential_dependent",
        "eligible_direct_landing": false,
        "clusters": [
          {"issue": 716, "depends_on": [715], "predicted_scope": ["src/keel/swarm.py"]}
        ]
      }
    ]
  }
  ```

### 4.2. Isolated Worktree Execution Runtime
- **Physical Workspace**: Each worker runs in `.keel/workspaces/swarm-<issue_id>/` created via `git worktree add`.
- **Concurrency Control**: `--max-workers N` (defaults to `knobs.swarm_concurrency` or CPU core budget).
- **Fail-Soft Isolation**: If Worker 1 fails a quality gate, Worker 2 and Worker 3 continue unimpeded.

### 4.3. Dynamic Scope Expansion & Rebalancing
- During `s4 implement`, if Worker 1 discovers it must modify a file outside its predicted scope (e.g. adding an export in `src/keel/__init__.py`), it reports scope expansion to the swarm coordinator.
- The coordinator dynamically recalculates remaining wave dependencies:
  - If a downstream worker was slated to touch `__init__.py`, it is deferred to Wave 2.
  - Independent workers continue in parallel without restart.

### 4.4. Dual-Mode Landing Engine
- **Direct Batch Landing**: When all workers in an orthogonal wave complete `s8 test` and `s7 review/jury`, their PRs are landed simultaneously onto `main` with preserved individual evidence records.
- **Adaptive Funnel & Self-Healing**: For non-orthogonal clusters, merges are serialized via `merge_lock`. After each merge, remaining PR branches execute `git rebase origin/main`. If merge conflicts arise, the orchestrator invokes `s9 fixloop` with the conflict diff, allowing the agent to resolve the conflict autonomously.

### 4.5. AI Jury & Compound Learning Integration
- **AI Jury**:
  - *Per-Worker Deliberation (`s7 review`)*: Standard cross-vendor jury deliberation per PR.
  - *Batch Integration Jury (`s10 merge`)*: An optional unified panel assesses the combined diff of the batch before landing.
- **Compound Learning**:
  - *Per-Worker Capture (`s11 capture`)*: Individual `compound-learning` markers emitted.
  - *Swarm Wave Synthesis*: Global wave efficiency and scope accuracy telemetry recorded with stable fingerprint deduplication.

### 4.6. Multi-Layer Real-Time Observability
- **CLI Terminal DAG Tree (`keel swarm-plan --tree`)**:
  ```text
  Keel Swarm Plan — 8 Issues Partitioned across 3 Waves
  ├── Wave 1 [Orthogonal Parallel — Direct Batch Landing Eligible]
  │   ├── #714 (docs/proposals/keel-swarm.md)  [Ready]
  │   ├── #715 (src/keel/swarm.py)             [Ready]
  │   └── #721 (keel-visual/*)                 [Ready]
  ├── Wave 2 [Dependent Parallel]
  │   ├── #716 (src/keel/swarm.py) ── depends on #715
  │   └── #720 (src/keel/cli.py)
  └── Wave 3 [Integration & Documentation]
      ├── #717 (src/keel/swarm.py) ── depends on #716
      ├── #718 (src/keel/adapters/*)
      └── #719 (docs/keel/*)
  ```
- **Live Terminal Dashboard (`keel swarm-status`)**: Live ANSI progress table displaying worker IDs, active steps (`s0`..`s12`), elapsed time, and gate indicators.
- **`keel-visual` 2D/3D Web Visualizer**: Interactive 2D cluster bounding boxes and 3D WebGL orbit topographies with live wave pulse animations.

---

## 5. Risk & Mitigation Matrix

| Risk | Consequence | Keel Swarm Mitigation |
|---|---|---|
| **Git Merge Collisions** | Broken base branch, conflicting patches | Static DAG clustering + Direct Batch Landing for disjoint trees + Atomic Funnel with `s9` self-healing for overlaps. |
| **Token / Rate-Limit Exhaustion** | API 429 throttling across parallel workers | Strict `--max-workers N` cap with exponential backoff in `api_delegate.py`. |
| **Scope Hallucination** | Worker touches unexpected global files | Real-time scope monitoring and dynamic cluster rebalancing. |
| **Worker Hanging / Crash** | Pipeline stalls indefinitely | Per-worker timeouts (`knobs.timeout_s`) and fail-soft isolation. |
| **Evidence / Compliance Loss** | Ambiguous multi-agent approvals | Immutable commit-SHA binding for every individual PR and integration commit. |

---

## 6. Delivery Roadmap (Milestone 12)

| Issue | Title | Scope & Component |
|---|---|---|
| **[#714](https://github.com/berkayturanci/keel/issues/714)** | `docs(proposals): author comprehensive keel-swarm architecture proposal` | Proposal document & competitive analysis matrix. |
| **[#715](https://github.com/berkayturanci/keel/issues/715)** | `feat(swarm): implement deterministic static dependency analysis and conflict clustering engine` | Pure core `src/keel/swarm.py` & `keel swarm-plan`. |
| **[#720](https://github.com/berkayturanci/keel/issues/720)** | `feat(cli): add interactive terminal ASCII DAG tree renderer and live swarm cluster status dashboard` | Terminal DAG tree & `keel swarm-status`. |
| **[#716](https://github.com/berkayturanci/keel/issues/716)** | `feat(swarm): implement isolated multi-worktree execution runtime with dynamic cluster rebalancing` | `.keel/workspaces/swarm-*` runtime & dynamic rebalancing. |
| **[#717](https://github.com/berkayturanci/keel/issues/717)** | `feat(swarm): implement orthogonal batch landing and drift self-healing merge engine` | Direct batch landing & `s9 fixloop` conflict resolver. |
| **[#721](https://github.com/berkayturanci/keel/issues/721)** | `feat(visual): enhance keel-visual with 2D DAG cluster partition graphs and 3D multi-wave swarm topology` | 2D/3D `keel-visual` swarm dashboard scenes. |
| **[#718](https://github.com/berkayturanci/keel/issues/718)** | `feat(adapters): add /keel:swarm agent skill and command adapters` | `/keel:swarm` command & skills. |
| **[#719](https://github.com/berkayturanci/keel/issues/719)** | `docs(swarm): document keel swarm guide, competitive analysis, risk mitigations` | Public docs, CLI guide & website topic. |
