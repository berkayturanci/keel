# Keel Swarm — High-Concurrency Multi-Agent Orchestration

> ## ⚠️ Experimental — this subsystem does not land work
>
> The planning commands run. **A live run cannot produce a commit or a pull request**, and the
> reason is deeper than a missing flag:
>
> - `keel ship` — the *CLI subcommand*, registered as `dry ship assessment (tier, window, gates,
>   decision)` — never commits, pushes or opens a pull request, in any mode. It is not inert: it
>   runs `git diff` and executes the project's planned gates, and that gate run is **not** behind
>   `--live`, so a dry `swarm-run` over N issues still runs the whole gate suite N times. A dry
>   run creates no worktrees, so those runs share your checkout; they run one at a time
>   ([#1288](https://github.com/berkayturanci/keel/issues/1288)), whatever `--max-workers`
>   says. What it never does is produce the commit. In keel's design the
>   implementation is done by the **agent** following `/keel:ship`, and the CLI assesses it;
>   swarm's workers spawn the CLI, so a worker cannot produce a commit in any mode.
> - `swarm-run --live` is refused before anything starts. Its workers are handed `--live`
>   ([#1269](https://github.com/berkayturanci/keel/issues/1269)), and `keel ship --live` stops
>   at the operator-consent gate, which swarm has no way to satisfy for a child — so a live run
>   would fail every worker and leave `swarm/<id>/…` branches behind
>   ([#1281](https://github.com/berkayturanci/keel/issues/1281)).
>
> Planning reads each issue's own scope, and is only as parallel as the issues say it can be:
> every named issue is read from GitHub, and an issue that declares no scope is planned as `*`
> and serialised against every other issue
> ([#1274](https://github.com/berkayturanci/keel/issues/1274)). A backlog whose issues carry no
> `Scope:` line plans one wave per issue, and every wave after the first is then
> `sequential_dependent`, which `swarm-land` refuses until the earlier wave lands and the
> rest is re-planned — see
> [Declaring an issue's scope](#declaring-an-issues-scope). `keel-visual swarm` rebuilds its scopes
> without predicted files, so its DAG is always one flat wave
> ([#1275](https://github.com/berkayturanci/keel/issues/1275),
> [#1280](https://github.com/berkayturanci/keel/issues/1280)).
>
> Landing is guarded, and it is **local**: `swarm-land` merges each cleared cluster branch into the
> **local base branch** with `git merge --no-ff`. It does not push, and it does not open or merge a
> pull request — pushing the base branch, and closing each cluster's pull request, is the
> operator's step ([#1287](https://github.com/berkayturanci/keel/issues/1287)). On a protected base
> branch that push is refused, so the merge commits stay local. The guard is what works as written:
> with `knobs.swarm_review_evidence`
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
lock with sequential `git merge --no-ff` into the local base branch — a local merge that pushes
nothing.

---

## 1. Core Principles & Architecture

Keel Swarm is built on three core pillars:

1. **Backbone Immutability**: The keel core step machine (`s0`–`s12` in `src/keel/model.py`) is
   strictly immutable. Swarm does not alter or bypass backbone steps; instead, each parallel cluster
   worker executes a complete, standard `keel ship` run within its own isolated worktree.
2. **Pure Core / Thin I/O Separation**: Dependency analysis, clustering, wave partitioning, and landing
   decision logic are 100% pure and deterministic (`src/keel/swarm.py`). Every **subprocess and
   git**
   mutation is confined to thin fail-soft runtime wrappers (`src/keel/swarm_runtime.py`,
   `src/keel/swarm_landing.py`) — but the filesystem is not: `resolve_swarm_state_dir` and
   `save_swarm_state` in `swarm.py` `mkdir` and write `.keel/state/swarm/<swarm_id>.json`
   (atomically
   since [#932](https://github.com/berkayturanci/keel/issues/932); the `#872` in the code comment
   beside it names an unrelated `gh api` fix). The separation the heading claims holds for the
   process boundary, not for disk.
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
              │  (Direct Batch)   │               │ (Dependent Wave)  │
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
              │ local base branch │ ◄─────────────┤  keel swarm-land  │
              │   (Wave 1 Done)   │               │(after re-planning)│
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

### Declaring an issue's scope

`swarm-plan`, `swarm-run` and `swarm-land` read every issue named by `--issues`/`--issue`
once, with `gh issue view N --json title,body,labels` run in `--root`
([#1274](https://github.com/berkayturanci/keel/issues/1274)). An issue's **declared** scope is
the first of these that names anything:

1. **`--issue-scope N=glob[,glob…]`** — repeatable, one per issue (repeating an `N` adds its
   globs). It wins over whatever the issue says. `N` must be a positive integer that
   `--issues`/`--issue` also names, and at least one glob must follow the `=`; anything else is
   refused.
2. **A `Scope:` declaration in the issue body**, in either spelling:

   ```markdown
   Scope: src/keel/swarm*.py, docs/keel/swarm.md
   ```

   ```markdown
   ## Scope
   - `src/keel/swarm.py` — the planner
   - tests/test_swarm.py
   ```

   The line form is any line starting with `Scope:`; the heading form is a `Scope` heading of
   any level followed by a bullet list, which ends at the first line that is neither a bullet
   nor blank. Globs are separated by commas or spaces and may be backticked; a word with no
   `/`, `.` or wildcard is read as prose and skipped (write `./Makefile` for a bare file name).
   Declarations inside a fenced code block are ignored, and several declarations add up.
3. **`area:<name>` labels**, each mapped through the project's own
   [`policy_pack.scan.areas`](configuration.md) — the area-to-globs map the scan commands
   already use. A project without that map, or a label naming an area it does not list,
   contributes nothing here; swarm adds no mapping of its own.

An issue with **no declared scope gets `*` — everything** — so it conflicts with every other
issue and gets a wave to itself; stderr names each such issue. Past wave 1 that wave depends
on every issue before it, so it is `sequential_dependent` and `swarm-land` refuses it (#1276)
until the earlier waves land and the rest is re-planned — slow, but never a collision. The
paths its title and body
happen to name, and the label hints below, are still kept in its scope (the risk tier and the
difficulty score read them), but beside `*`, never instead of it: a mention of `a.py` is not a
promise that the change stays out of `cli.py`.

An issue that cannot be read — no `gh`, no auth, no network, an unparseable reply — is named on
stderr and planned from the flags alone, which for a multi-issue plan means `*`. It is never
assumed disjoint. Each issue's resolved `predicted_files` and its `scope_source` (`override`,
`issue-body`, `area-label`, `declared-file` or `default`) are in `swarm-plan --json`'s
`issue_scopes`.

`--issue-title`, `--issue-body`, `--issue-label` and `--declared-file` describe **one** issue.
With a single issue they fill in for (title, body) or add to (labels, files) what GitHub
returned, and `--declared-file` counts as a declaration; beside several issues they are refused,
because handing the same text to every issue is what made every plan either one flat wave or
fully serial before #1274.

### Scope Prediction Heuristics
These apply only to an issue with no declared scope, and what they find sits **beside** `*`,
so they inform the tier and the difficulty score and never make an issue look disjoint.

- **Title / Body Path Parsing**: a path written in the title or body is added to the scope —
  `touch src/keel/*.py` yields `src/keel/*`. It is path matching, not language awareness, and it
  cuts both ways: `tests/test_*.py` yields nothing, because the extractor does not accept the
  `test_*` segment — while a **backticked** module name is taken as a file, so
  `` `keel.swarm` `` becomes the phantom path `keel.swarm`. Before #1274 such a phantom was the
  whole scope, and it matched no real file, so the issue looked disjoint from everything; now it
  rides next to `*`. Check what a scope actually resolved to with `swarm-plan --tree` (or
  `issue_scopes` in `--json`) rather than assuming a mention was understood.
- **Label / Role Fallback**: only when the text and `--declared-file` named *nothing*, a
  substring match over the role **and every label** maps `docs`/`website`/`visual` to a directory
  glob and `cli` to the single file `src/keel/cli.py`. For a scope that holds, use an
  `area:<name>` label backed by `policy_pack.scan.areas` instead.
- **Disjointness Matrix**: If two issues touch non-overlapping directory trees or orthogonal subsystems,
  they are marked disjoint ($D_{ij} = 1$). If scopes intersect, a conflict edge is created ($C_{ij} = 1$).
- **Topological Wave Partitioning**: Disjoint clusters are scheduled in Wave 1. Dependent or conflicting
  clusters are placed in subsequent waves (Wave 2, Wave 3...).
- **Wave mode**: Wave 1 is `orthogonal_parallel` (`eligible_direct_landing: true`). A later wave is
  `orthogonal_parallel` only when none of its clusters lists a `depends_on_issues` entry; otherwise it
  is `sequential_dependent` (`eligible_direct_landing: false`), because the wave it depends on moves
  the base its branches were cut from. A plan built from scratch always gives a later wave a
  dependency — an issue only waits for a later wave because it overlaps something already placed —
  so every wave after the first is `sequential_dependent` until a failure's rebalance drops its last
  dependency ([#1276](https://github.com/berkayturanci/keel/issues/1276)). Before that fix every
  wave claimed `orthogonal_parallel`.

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

(The three issues above declare disjoint scopes, so they share one wave; had any of them declared
none, it would be planned as `*` and sit in a wave of its own. Issues whose predicted
scopes overlap are pushed into later waves instead — each wave stays internally disjoint. Such a
wave prints as `⏳ Wave 2 [sequential_dependent] — Dependent — refused until re-planned`, because
`swarm-land` refuses it until the earlier wave lands; see [Landing](#4-landing-keel-swarm-land).
A `*` issue in a later wave always does: it overlaps everything before it.)

---

## 3. Isolated Multi-Worktree Runtime (`keel swarm-run`)

Parallel execution runs across isolated git worktrees created under
`.keel/worktrees/<swarm_id>/<cluster_id>/` (the swarm id is `swarm-YYYYMMDD-HHMMSS`):

```bash
keel swarm-run .keel/project.yaml --root . --issues 714,715,716,717
```

Each worker's child `keel ship` may run for `--worker-timeout SECONDS`, by default
`knobs.gate_timeout_s + knobs.jury_timeout_s`; one that runs longer is killed and its cluster
fails with `timed_out: true` ([#1279](https://github.com/berkayturanci/keel/issues/1279)).

### Worktree Lifecycle & Isolation

> While `swarm-run --live` is refused ([#1269](https://github.com/berkayturanci/keel/issues/1269)),
> no CLI path creates worktrees: this lifecycle is the library's
> (`run_swarm_orchestration(dry_run=False)`), described so the next change starts from what it does.

1. **Creation**: Dedicated worktrees are branched from the configured `base_branch` onto
   `swarm/<swarm_id>/<cluster_id>` ([#1262](https://github.com/berkayturanci/keel/issues/1262)).
2. **Execution**: One **team lead** per cluster dispatches the implementer its `assignment`
   named, to execute the full `s0`–`s12` backbone. The lead appends the cluster's team to every
   child ship — `--delegate <implementer>`, one `--review-delegate` per staffed reviewer
   slot **whose seat is a provider** (a host-subagent seat gets no flag and the child re-resolves
   it), `--role`, and `--effort`/`--team` for the bench the cluster was staffed from — so
   the child reproduces the parent's resolution instead of quietly deriving a different
   team from config alone. `keel ship` accepts all five.
3. **Rebalancing on failure**: when a cluster's issue fails, `rebalance_swarm_plan` drops the
   clusters carrying that issue, discards any wave left empty, and removes the failed issue from
   every survivor's `depends_on_issues` and from the plan's `conflict_map` and `issue_scopes`
   ([#1277](https://github.com/berkayturanci/keel/issues/1277), fixed in
   [#1310](https://github.com/berkayturanci/keel/pull/1310)). It re-schedules nothing: the
   partition emits one issue per cluster, so no *remaining* wave carries the failed issue, and
   the waves keep their order — a cluster that overlapped the failed one still runs in its own
   later wave. `run_swarm_orchestration` follows the waves by `wave_index`, not by position, so a
   discarded wave no longer makes it step past the next, unrelated one
   ([#1268](https://github.com/berkayturanci/keel/issues/1268), fixed in
   [#1312](https://github.com/berkayturanci/keel/pull/1312)); before that fix, three conflicting
   issues with issue 1 failing ran waves 1 and 3 and left `cluster-2-2` `queued`.
   `tests/test_swarm_runtime.py::AFailedWaveDoesNotSkipTheNext` holds it with a distinct issue per
   wave: reusing one issue in two waves, as the fixture behind
   [#873](https://github.com/berkayturanci/keel/issues/873)'s fix did, hides the skip.

   (There is also no runtime scope audit — clusters are kept off each other's files by plan-time
   overlap partitioning and per-worktree isolation, not by watching what a worker writes.)
4. **Cleanup**: partial, and only on a live run. `remove_swarm_worktree` runs
   `git worktree remove --force` on the cluster's leaf directory (falling back to `rmtree`), and the
   `finally` that calls it is guarded by `create_worktrees and not dry_run` — a dry run creates no
   worktree to remove. Four things it does **not** do, each verified against
   `src/keel/swarm_runtime.py`:

   - the `.keel/worktrees/<swarm_id>/` parent directory is created by `mkdir(parents=True)` and
     never
     removed, so one directory per run accumulates;
   - the `swarm/<swarm_id>/<cluster_id>` branch is never deleted — nothing in `src/keel` runs
     `git branch -d/-D`. A re-run with the same `--swarm-id` therefore force-resets a surviving
     branch, because the worktree is created with `git worktree add -B`;
   - `git worktree prune` is never run, so a registration left behind by a failed remove stays in
     `.git/worktrees`;
   - `remove_swarm_worktree` returns `True` unconditionally and its caller discards the value, so a
     failed cleanup is silent.

   Nothing here manages locks, so "without leaving orphaned locks" — which this line claimed until
   #1285 was audited — described a mechanism that does not exist. Tracked under
   [#1278](https://github.com/berkayturanci/keel/issues/1278).

### Status board (`keel swarm-status`)
Print the swarm's clusters — each one's lead, difficulty band, role, step and status
(`queued` / `running` / `passed` / `failed` / `merged` / `held` — the full vocabulary
`SwarmWorkerStatus.status` carries) — from the persisted run state. It is a one-shot render of
that state, not a live feed; re-run it to refresh:

```bash
keel swarm-status .keel/project.yaml --root .
```

It exits `0` when it read the run, or when no `--swarm-id` was given and there is no run at all;
it exits `1` when the run's state file cannot be read or `--swarm-id` names a run that does not
exist, and `--json` then prints an object with an `error_code` instead of the `{}` that means "no
run". The table is in [the CLI reference](cli.md#keel-swarm-status-projectyaml---root-dir---swarm-id-id---json).

---

## 4. Landing (`keel swarm-land`)

Landing is coordinated by `src/keel/swarm_landing.py` under the atomic `merge_lock`
(`.keel/state/locks/merge-<sha12>.lock`):

```bash
keel swarm-land .keel/project.yaml --root . --issues 714,715,716,717 --wave 1 --live
```

### What landing actually does

`evaluate_wave_landing_mode` decides from the plan's wave mode whether the wave lands at all:

- **Direct batch** — the wave is `orthogonal_parallel` (wave 1, or a later wave with no
  dependency left). Each cluster branch is merged into the **local** copy of the configured base
  branch with `git merge --no-ff`, **one after another** inside the lock. A merge that conflicts
  is `git merge --abort`ed, the base is left untouched, and the cluster is reported `merge failed`.
  The decision's reason is `single_cluster` for a wave of one and `orthogonal_diff_trees`
  otherwise: `build_swarm_plan` only admits an issue to a wave it conflicts with nothing in, and
  the CLI passes no PR diff map, so a wave's own clusters never overlap each other.
- **Refused** — the wave is `sequential_dependent`, whatever its size (reason
  `depends_on_earlier_wave`, [#1276](https://github.com/berkayturanci/keel/issues/1276)). Its
  branches were cut before the earlier wave it depends on landed, so `swarm-land` lands none of
  it, dry run or live: it runs no git command and no review-evidence check, reports
  `mode : refused` with `refused : wave N depends on issues landed by an earlier wave (#a, #b)…`,
  and exits 1. Land the earlier wave, then re-plan the remaining issues (`keel swarm-plan` /
  `swarm-run` without the landed ones, so they plan as a fresh wave 1 on the moved base) and land
  again. Before #1276 every wave claimed direct landing, so `keel swarm-land --wave 2` merged a
  branch cut before wave 1 landed and reported the conflict as `merge failed`.

`swarm_landing.py` also implements an adaptive rebase funnel (rebase onto the moved base,
deterministic marker-resolver healing, hold-and-rewind of anything the resolver touched). No
`keel swarm-land` invocation reaches it: it is selected only for an `orthogonal_parallel` wave
whose supplied `pr_diff_map` overlaps (`overlapping_diff_trees`), and the CLI passes none. It
stays off for dependent waves until
[#1266](https://github.com/berkayturanci/keel/issues/1266) feeds its overlap check real diffs.
The refusal is chosen from the plan's dependency edges, not from how far the base branch has
actually moved since each branch was cut; comparing against that drift is the open half of #1276.

**The landing is a local merge only.** `merge_cluster_branch` runs `git checkout <base_branch>` and
`git merge --no-ff <cluster branch>` in your checkout, and nothing after it pushes: `swarm-land`
does not push the base branch, and it does not open or merge a pull request — each cluster's pull
request stays open. A cluster reported `merged` is merged locally; `origin` is unchanged. Pushing
is the operator's step, and on a protected base branch (the configuration keel recommends) a
direct push is refused, so the merge commits cannot reach the remote that way. Use `keel merge`
per pull request — through `/keel:ship` — for work that has to reach the repository
([#1287](https://github.com/berkayturanci/keel/issues/1287)).

All of this happens in the checkout `--root` points at, which is usually your own. So a live
landing starts only from a clean tree: when `git status --porcelain` shows any change, tracked or
untracked, it names the files, checks out and merges nothing, and exits 1 with
`refused : the working tree has uncommitted changes…`. Untracked files keel writes itself, under
`.keel/state/`, `.keel/activity/`, `.keel/scratch/`, `.keel/worktrees/` and the scaffolded
`.keel/.gitignore`, do not count. It records the branch (or detached commit) you were on and checks
it out again when the wave ends, however it ends: landed, conflicted, aborted, or raised. If that
checkout fails, the result carries `warning : could not return the checkout to <branch>…` with the
command to run ([#1279](https://github.com/berkayturanci/keel/issues/1279)). A dry run touches no
branch and is unchanged.

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
- **Worker Matrix**: Worker cards showing each cluster's state —
  `queued`/`running`/`passed`/`failed`/`merged`/`held` —
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
| **Batch landing under one writer lock** | **Yes (sequential local `merge --no-ff`; pushes nothing)** | No | No | No | PR per run |
| **Atomic Single-Host Lock** | **Yes (`merge_lock`)** | No | No | No | No |
| **Fail-soft conflict handling** | **Yes (`merge --abort`, cluster reported failed)** | No | No | No | Manual |
| **Per-Branch Review-Evidence Gate** | **Yes (cross-vendor panel per cluster, when configured)** | No | Conversational | No | Single Agent |
| **2D DAG & pseudo-3D snapshot** | **Yes (`keel-visual`, rendered)** | Basic Tree | Plotly / None | Static Diagrams | Web Terminal |

---

## 8. Risk & Failure Mitigations

| Risk / Failure Scenario | Detection Mechanism | Fail-Soft Mitigation |
| :--- | :--- | :--- |
| **A cluster changes files another cluster also touches** | Plan-time static file-overlap partitioning (disjoint trees only share a wave) + isolated per-cluster worktrees | Overlapping clusters are sequenced into later waves. A failed cluster's issue is dropped from the plan and every later wave still runs ([#1268](https://github.com/berkayturanci/keel/issues/1268), fixed in [#1312](https://github.com/berkayturanci/keel/pull/1312)); nothing is re-scheduled — see item 3 above. |
| **Merge conflict during landing** | `git merge` non-zero exit code | Automatic `git merge --abort`; the base branch remains untouched; the cluster is reported `merge failed`. |
| **Concurrent Merge Race Condition** | `merge_lock` file mutex | Atomic `mkdir`-based lock. When another writer holds it, `swarm-land` merges nothing: every cleared cluster is reported `held` with the lock named in the reason, the hold is written to the run state, and the wave's result is returned rather than retried ([#1272](https://github.com/berkayturanci/keel/issues/1272)) — landing is single-writer by refusal. |
| **Worker Subprocess Crash / OOM** | Subprocess exit status monitoring | Fail-soft error capture in `SwarmRunState`; remaining parallel workers continue unimpeded. |
| **Missing or unreadable run state** | `load_swarm_state` JSON/Value/Key/Type/Overflow errors, and an `OSError` opening the file | Fails soft to no state rather than raising; `swarm-land` rebuilds the plan from `--issues`, so a lost state file costs the board, not the landing. |
| **A cluster scored lighter than it turns out to be** | The lead's own progress against the plan | The lead reports through its worker record and the CTO re-plans; a lead never re-staffs itself, so the run's team stays the one the plan published. |
| **`--team` names a bench that is not configured** | `assignment.warnings` at plan time | The run falls back to the configured policy and says so; the name is never silently ignored. |
