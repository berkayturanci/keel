# Keel Swarm — High-Concurrency Multi-Agent Orchestration

> ## ⚠️ Experimental — the swarm reviews its own work only when you run `swarm-review`, which has not yet run on a real repository
>
> The planning commands run, and since
> [#1400](https://github.com/berkayturanci/keel/issues/1400) a live run implements: **`swarm-run
> --live` dispatches each cluster's implementer seat in the cluster's own worktree, commits the
> result, runs the project's gates, pushes the cluster branch and opens one pull request per
> cluster** — under operator consent the parent obtains and delegates explicitly (see
> [How a live worker implements a cluster](#how-a-live-worker-implements-a-cluster) and
> [Consent delegation](#consent-delegation)). What it does not do yet is the rest of the ship:
>
> - The pull request carries **no review evidence** when `swarm-run` opens it. `swarm-land`
>   merges it through `keel merge`
>   ([#1287](https://github.com/berkayturanci/keel/issues/1287)), so it holds the cluster until
>   the pull request's review verdicts are posted. The swarm reviews only when you run
>   [`keel swarm-review`](#4-review-keel-swarm-review) — the cluster's own reviewer seats,
>   read-only, their verdicts posted pinned to the head — and it has not yet run on a real
>   repository ([#1423](https://github.com/berkayturanci/keel/issues/1423)); otherwise the
>   verdicts are posted by hand (`keel review --live`), as for any pull request. The rest of what `keel merge`
>   asks of a pull request the worker leaves itself: the seat's attribution labels on it and a
>   gates-pass for its head in the run ledger
>   ([#1420](https://github.com/berkayturanci/keel/issues/1420)).
> - A landed cluster is closed the way `/keel:ship` closes an issue: after each merge,
>   `swarm-land --live` posts the closure comment on the pull request and on each of the
>   cluster's issues, then closes the issues
>   ([#1422](https://github.com/berkayturanci/keel/issues/1422), see
>   [What landing actually does](#what-landing-actually-does)).
> - A **dry** run is unchanged: its worker is `keel ship` — the *CLI subcommand*, registered as
>   `dry ship assessment (tier, window, gates, decision)` — which never commits, pushes or opens a
>   pull request. It is not inert: it runs `git diff` and executes the project's planned gates, so
>   a dry `swarm-run` over N issues still runs the whole gate suite N times. A dry run creates no
>   worktrees, so those runs share your checkout; they run one at a time
>   ([#1288](https://github.com/berkayturanci/keel/issues/1288)), whatever `--max-workers` says.
> - A live worker needs an implementer keel can run itself — an agent CLI (`claude`, `codex`,
>   `agy`) named by `knobs.team.implement` or `--delegate`. A cluster whose seat is a host
>   `subagent:` or an API/Ollama/profile transport refuses the whole live run before any worker
>   starts, with the reason.
>
> Planning reads each issue's own scope, and is only as parallel as the issues say it can be:
> every named issue is read from GitHub, and an issue that declares no scope is planned as `*`
> and serialised against every other issue
> ([#1274](https://github.com/berkayturanci/keel/issues/1274)). A backlog whose issues carry no
> `Scope:` line plans one wave per issue, and every wave after the first is then
> `sequential_dependent`, which `swarm-land` refuses until the earlier wave lands and the
> rest is re-planned — see
> [Declaring an issue's scope](#declaring-an-issues-scope). `swarm-run` persists the plan it
> executes and `swarm-land` lands exactly that plan
> ([#1275](https://github.com/berkayturanci/keel/issues/1275), see
> [Which plan lands](#which-plan-lands)); `keel-visual swarm` draws that same plan — its
> waves, predicted files and dependencies — and says so when a run has none, instead of
> rebuilding one (keel-visual 0.9.0 still rebuilds its scopes without predicted files, so its
> DAG there is always one flat wave;
> [#1275](https://github.com/berkayturanci/keel/issues/1275),
> [#1280](https://github.com/berkayturanci/keel/issues/1280)).
>
> Landing is guarded, and it goes through **`keel merge`**: `swarm-land` hands each cluster's
> pull request (the one its live worker opened, recorded in the run state) to the code `keel merge`
> runs, one cluster at a time ([#1287](https://github.com/berkayturanci/keel/issues/1287)). The
> merge window, the merge lock, the CI rollup, the review-evidence gate, the gates-pass for the
> head, the head-pinned squash and the drift check apply to it as to any other pull request, so a
> cluster lands only once its pull request has what `/keel:ship` needs: verdicts pinned to its head
> and a gates-pass recorded for it. A cluster whose pull request is missing, closed, not mergeable,
> outside the window or without evidence is held with that reason; the rest of the wave is not
> affected. Nothing is checked out or merged in your checkout. `knobs.swarm_review_evidence: false`
> no longer skips anything, because `keel merge`'s evidence gate has no opt-out; `swarm-land` says
> so on stderr.
>
> A live landing has run end to end once, on a throwaway sandbox repository with CI on pull
> requests: `swarm-run --live` implemented two issues and opened their pull requests,
> each pull request's review verdicts were posted from outside the swarm, and `swarm-land --live`
> merged both through `keel merge`
> ([#1281's closing comment](https://github.com/berkayturanci/keel/issues/1281#issuecomment-5935366055)). That is one run on a toy
> repository, not evidence of maturity.
>
> The rest is tracked in [#1423](https://github.com/berkayturanci/keel/issues/1423) (`swarm-review`,
> built and not yet run on a real repository); the audit epic [#1281](https://github.com/berkayturanci/keel/issues/1281) is closed. Everything below describes
> the design and the code that exists; read it as architecture, not as a supported workflow.
>
> **Use [`/keel:ship`](../../src/keel/adapters/commands/ship.md) for work you need landed.**

**keel-swarm** is an additive, high-concurrency orchestration layer designed to coordinate
multiple AI developer agents working in parallel across complex backlogs. It transforms a list of
GitHub issues into a topologically ordered execution graph, partitions issues into conflict-free
clusters, implements each cluster in its own git worktree through the cluster's implementer seat
and opens one pull request per cluster (a live run), and lands each cluster's pull request
through `keel merge`, one at a time, under the same merge lock every other merge takes.

---

## 1. Core Principles & Architecture

Keel Swarm is built on three core pillars:

1. **Backbone Immutability**: The keel core step machine (`s0`–`s12` in `src/keel/model.py`) is
   strictly immutable. Swarm does not alter or bypass backbone steps. A dry run's worker is a
   standard `keel ship` assessment; a live run's worker performs s4 (implement) for its cluster
   in its own worktree and stops at an open pull request, which the later steps take from there.
2. **Pure Core / Thin I/O Separation**: Dependency analysis, clustering, wave partitioning, and landing
   decision logic are 100% pure and deterministic (`src/keel/swarm.py`). Every **subprocess and
   git**
   mutation is confined to thin fail-soft runtime wrappers (`src/keel/swarm_runtime.py`,
   `src/keel/swarm_landing.py`) — but the filesystem is not: `resolve_swarm_state_dir` and
   `save_swarm_state` in `swarm.py` `mkdir` and write `.keel/state/swarm/<swarm_id>.json`, and
   `save_swarm_plan` writes the run's plan beside it as `<swarm_id>.plan.json`
   ([#1275](https://github.com/berkayturanci/keel/issues/1275)), both
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
| **Worker** | dry: one child `keel ship --dry-run` per cluster; live: the cluster's implementer seat, run by keel | dry: the assessment; live: implement, commit, gates, push, one pull request, in the cluster's worktree | reach outside its predicted scope, or hold a consent scope the parent did not hand it |

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
              │  • keel merge/PR  │                         │
              │  • merge_lock     │                         │
              └─────────┬─────────┘                         │
                        │                                   │
                        ▼                                   ▼
              ┌───────────────────┐               ┌───────────────────┐
              │ base branch (host)│ ◄─────────────┤  keel swarm-land  │
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

**The declared scope is also the implementer's fence.** A live worker's brief tells the seat to
change only files inside the cluster's scope, and in the first end-to-end run the seat kept to
it: an issue whose text also asked for an export from `calc/__init__.py` left that file alone,
because its `Scope:` line named only `calc/subtract.py` and its test. So list **every** file the
change has to touch — the package's `__init__.py`, a registry, the docs page — not just the new
module; a file left out is a file the implementer will not edit.

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
`swarm-land` refuses it until the earlier wave lands; see [Landing](#5-landing-keel-swarm-land).
A `*` issue in a later wave always does: it overlaps everything before it.)

---

## 3. Isolated Multi-Worktree Runtime (`keel swarm-run`)

Parallel execution runs across isolated git worktrees created under
`.keel/worktrees/<swarm_id>/<cluster_id>/` (the swarm id is `swarm-YYYYMMDD-HHMMSS`):

```bash
keel swarm-run .keel/project.yaml --root . --issues 714,715,716,717
```

Each worker's gate run — a dry run's child `keel ship`, a live worker's `keel run-gates` — may
run for `--worker-timeout SECONDS`, by default `knobs.gate_timeout_s + knobs.jury_timeout_s`; one
that runs longer is killed and its cluster fails with `timed_out: true`
([#1279](https://github.com/berkayturanci/keel/issues/1279)).

**How many run at once — size it yourself.** A live run runs at most `--max-workers` workers at a
time, **4 by default**, and never more than the wave has clusters; a dry run runs one at a time
whatever the flag says. Each live worker is an implementer seat — an agent CLI such as `claude`,
`codex` or `agy` — **that may call its provider's API** for as long as it runs, and then runs the
project's gate suite in its worktree. So `--max-workers 8` can mean eight concurrent provider
sessions and eight gate suites on one machine. keel has no budget of its own to hold that against
— no config key, and no CPU, memory or API-rate limit
([#1280](https://github.com/berkayturanci/keel/issues/1280)) — so choose the flag from your
provider quota and the machine, not from the number of issues.

### How a live worker implements a cluster

Decided in [#1400](https://github.com/berkayturanci/keel/issues/1400): keel dispatches each
cluster's resolved implementer seat itself, with the machinery `keel delegate run` uses for one
issue, so a live swarm depends on no agent host. `swarm-run --live` runs, per cluster:

1. **Plan the seat, before anything starts.** The seat is `assignment.implementer`, resolved by
   `keel.team.resolve_assignment` — `--delegate` over a `--team` or difficulty bench over
   `knobs.team.implement` over the host default — and planned exactly as `keel delegate run
   --provider <seat> --role implement` plans it (`keel.swarm_worker.plan_implementer`). Only an
   agent CLI can edit a worktree, so a `subagent:` seat (only an agent host can spawn one) or an
   `api`/`ollama`/generic-profile transport is refused. One refused cluster refuses the run: no
   worker starts, and stderr lists every cluster keel could not dispatch.
2. **Cut the worktree** on `swarm/<swarm_id>/<cluster_id>` from `base_branch`.
3. **Implement.** The brief — the cluster's issues, its scope, and "do not commit, push or open
   a pull request" — is written to `.keel/state/swarm/<swarm_id>/<cluster_id>.brief.md`, outside
   the worktree so it is never committed, and the seat runs through `keel.delegaterun.execute`
   with the worktree as its working directory, under the delegate machinery's own limits: the
   prompt on stdin, never in argv; the plan's timeout (`keel delegate run`'s default, 1800 s);
   the vendor's own tool-enabled invocation for the `implement` role.
4. **Check for tampering, then commit** whatever the seat changed (`git add -A`, one commit
   naming the issues with `Refs` and the seat that wrote it). Before the seat ran, the worker read
   the push URL of `origin` and the repository's git setup; if that setup changed while the seat
   ran, the worker stops at `tamper` (see the trust notes). A seat that committed its own work is
   kept as it is, as long as it descends from the commit the worktree was cut at; a seat that
   changed nothing stops the worker.
5. **Gate.** `keel run-gates <project.yaml> --root <worktree> --phases guard,test --defer-jury
   --json` — the gates the s4 loop judges an implementation by, bounded by `--worker-timeout`. The
   jury is a review and is deferred with the rest of review. The JSON report is kept: it is what
   the gates-pass record in step 10 is written from, gate by gate.
6. **Check for tampering again, then push** the commit to
   `refs/heads/swarm/<swarm_id>/<cluster_id>` at the URL read before the seat ran — not to the
   remote's name — with no hooks (never forced).
7. **Open one pull request** for the cluster against `base_branch` (`keel.github.open_pr`). Its
   body says `Refs #N` for each issue — never `Closes`, since nothing has reviewed it; `swarm-land`
   closes the issues once it merges the pull request ([#1422](https://github.com/berkayturanci/keel/issues/1422))
   — and records the implementer seat, the commit the gates passed at, and the consent delegation.
8. **Stamp its provenance.** Right after the pull request opens, keel posts on it the
   `keel.ship-provenance.v1` comment a live `keel ship` run posts on its own
   (`keel.artifacts.render_ship_provenance`: run id `<swarm_id>/<cluster_id>`, the issue, the
   pushed head, the seat's attribution), which arms `keel merge`'s evidence gate — the cluster
   branch matches no ship-branch pattern. The worker record says `provenance_posted`. A post
   that fails stops nothing: the pull request stays open and the run warns that it will be held
   as *evidence gate is not enforced* until the comment or its review verdicts are posted.
9. **Label it** ([#1420](https://github.com/berkayturanci/keel/issues/1420)). Right after the pull
   request opens, keel applies the seat's attribution labels — `agent_label` and, when the seat
   names a model, `model_label`, verbatim from the attribution `keel attribution` prints for that
   seat, never composed — creating any label the repository lacks first, with the operator's
   credentials. `/keel:ship` labels every pull request this way, and `keel merge` holds one
   without its `agent:<vendor>` label (`attribution-label`). The worker record says
   `labels_applied`; a label post that fails stops nothing, and the run warns that the cluster
   will be held on *attribution-label* until the labels are applied.
10. **Record its gates-pass** ([#1420](https://github.com/berkayturanci/keel/issues/1420)). `keel
    merge` lands a pull request only with a `ship_run` record in the run ledger whose gates passed
    for its current head. The worker builds one, with the builder `keel ship --live
    --append-ledger` uses, from the gates its own run reported: `pull_request`, `head_sha` (the
    pushed head), the cluster's first issue, the branch, `actors.implementer` (the seat's
    `system`, the string its labels come from), run id `<swarm_id>/<cluster_id>`,
    `capture.not_run: true` and `assessment.merge.action: defer` — it never reached capture and
    assessed no merge, so nothing counts it as a merged or shipped pull request. It carries no
    consent of its own: the cluster's consent is the `consent_delegation` event, pinned to the
    pushed head. The run appends it to the ledger `keel merge --root <root>` reads, and the worker
    record says `gates_recorded`. A blocking gate the worker did not run — the deferred jury, a
    `pre-merge` gate — is recorded as `not_run`, so that record is **not** a pass, and the run
    says which gate holds it; a record that cannot be written warns, and stops nothing.

**What a worker leaves behind.** On success: a committed, pushed cluster branch and one open pull
request, reported as the cluster's `pr_url` and recorded as the worker's `pull_request` number in
the run state, with the worker at step `s6` in `swarm-status`. The pull request carries the
provenance comment and the seat's attribution labels, and the run ledger carries a gates-pass for
its head (`provenance_posted`, `labels_applied`, `gates_recorded`). It does **not** carry review
evidence yet, so `swarm-land` holds it until its review verdicts are posted, as `keel merge`
requires of any pull request. The worktree itself is
removed when the worker ends; the branch stays, because it heads the pull request. What a
failed worker leaves is in [Worktree Lifecycle & Isolation](#worktree-lifecycle--isolation).

**Failure.** A worker stops at the first stage that fails and reports it: `stage` in its
`cluster_results` entry (`consent`, `worktree`, `implement`, `tamper`, `commit`, `gates`, `push`,
`pull_request`) and the reason in `output` and the state file's `details`. Nothing after the
failed stage runs: a failed implementer commits nothing; a `tamper` after the implementer commits
nothing, and one after the gates leaves the commit local and the branch unpushed; a red gate
leaves the commit local and the branch unpushed; a refused push opens no pull request; a pull request `gh` could not open
says the branch is already pushed. A failed cluster is dropped from the later waves as before.
A worker that failed after its seat ran keeps its worktree and branch for inspection, and its
`output` ends by naming the path ([#1278](https://github.com/berkayturanci/keel/issues/1278)).

**Trust notes.** The implementer is an agent with tools, running in the cluster's worktree with
the delegate machinery's existing sandbox and limits — keel adds none of its own, and the brief
is guidance, not enforcement: nothing stops a seat from writing outside its scope, which is why
the gates run on the commit before anything is pushed and why the pull request still has to be
reviewed. The issue text is untrusted input to the seat, as it is in `/keel:ship` s4.

**The implementer cannot reach the remote.** Only keel's own push, `gh pr create` and the
provenance comment — steps 6 to 8, made in keel's process with the operator's environment, after the implementer has exited
— are meant to reach the forge. The implementer seat runs under
`keel.swarm_worker.implementer_env`:

| What | Why |
| --- | --- |
| `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN` removed (`FORGE_TOKEN_ENV_VARS`) | the operator's forge tokens are not the implementer's to hold |
| `GH_CONFIG_DIR` set to `.keel/state/swarm/<swarm_id>/<cluster_id>.no-gh-login`, a directory with no login | `gh` finds no stored account — not in its config, and, with no host listed there, not in the system keyring either — so `gh pr create` or `gh api` says it is not logged in |
| `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS` empty | git has no one to ask for a password; the empty value also shadows `core.askPass`, `SSH_ASKPASS` and an editor's askpass bridge inherited from the operator's terminal |
| `credential.helper` set empty, through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` | clears every stored-password helper — `osxkeychain`, `manager`, `store`, and the URL-scoped helper `gh auth setup-git` writes |
| `protocol.allow=never`, and `never` for `http`, `https`, `ssh` and `git` by name | git opens no network transport at all — push and fetch, HTTPS and SSH — so a credential that still exists somewhere (an SSH key, an agent socket) is never used; naming each transport outranks a user's own `protocol.https.allow=always` |
| `protocol.file.allow=user` | git's own default for a repository on disk, so a project whose tests clone or push to a local repository keeps working |
| the parent's own `GIT_CONFIG_PARAMETERS` and `GIT_CONFIG_COUNT`/`KEY`/`VALUE` removed | the first is read after `GIT_CONFIG_COUNT` and would outrank the lockdown |

Model providers' keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`CLAUDE_CODE_OAUTH_TOKEN` …) are not forge credentials and pass through: the seat needs them to
reach its model. The environment-config channel needs git 2.31 or later; an older git ignores it
and keeps only the token, `gh` and prompt measures.

This takes the *ambient* credentials away, so a brief that talks the seat into `git push` or
`gh pr create` fails. It is not a sandbox. The seat runs as the operator's OS user, and an agent
set on it can read what that user can — a keychain, an SSH key, a token file — or unset the
variables for its own children.

**The implementer cannot tamper with keel's own git steps.** The worktree's `.git` points into
the operator's repository, so the seat could write that repository's config and hooks — and
keel's own `git status`, `add`, `commit` and push run after it, with the operator's credentials.
A hook, a `core.fsmonitor` or `gpg.program` would then run the seat's code in keel's process; a
`remote.origin.pushurl`, `url.<base>.pushInsteadOf` or a file an `include.path` pulls in would
send keel's push elsewhere. The worker defends each step:

| When | What |
| --- | --- |
| before the seat runs | reads the push URL (`git remote get-url --push origin`), the commit the worktree was cut at, and a snapshot of the git setup: the resolved `git config --list --show-origin --show-scope` of every scope (included files too), the git, common and hooks directories, and a digest of every file under the hooks directory |
| after the seat, and again after the gates (which run the seat's code) | takes the snapshot again; any difference stops the worker at `tamper`, before its next git step, naming the scope and key or the hook that changed — never a value, and a URL in a key (`url.<url>.*`, `credential.<url>.*`, `http.<url>.*`, `includeIf.<condition>.*`) is left out too. `branch.<name>.*`, which git writes itself when a sibling worker cuts its worktree, is not compared: keel's push names its URL and refspec |
| every git step of keel's own after the seat | `git -c core.hooksPath=<an empty directory made only after the seat exited> -c core.fsmonitor=false -c commit.gpgsign=false`, with `--no-verify` on the commit and the push. keel's commit is therefore **unsigned** |
| the commit | must descend from the commit the worktree was cut at, or the worker stops at `tamper` |
| the push | goes to the URL read before the seat ran, never to the remote's name |

The gates and keel's own local git steps also run without the forge tokens
(`keel.swarm_worker.worker_env`): `keel run-gates --phases guard,test --defer-jury` reads nothing
from GitHub, and neither do `git status`/`add`/`commit`. Only keel's push, `gh pr create` and
the provenance comment hold them.

Two limits remain, by design:

- **The gates run the implementer's code.** That is what gates are for. They run it with the
  operator's environment — less consent and the forge tokens — and the operator's own git, which
  a project's tests may need. Code that runs there can still read what the operator's OS user
  can: a keychain, an SSH key, a token file.
- **Nothing here is a sandbox.** For an untrusted backlog, run `swarm-run --live` as a separate OS
  user, or in a container that holds only a model provider's key.

### Consent delegation

`swarm-run --live` obtains the operator's consent at the parent exactly the way every live keel
command does — `--approve-scope` / `--operator`, or `KEEL_APPROVE_SCOPE` + `KEEL_OPERATOR` (or
`automation.approved_scopes` + `automation.operator`) under `consent_mode: standing` — over the
mutations a worker makes: `git_worktree`, `file_edit`, `git_commit`, `git_push`, `pull_request`,
`labels` (the attribution pair, #1420), which need the scopes `filesystem`, `git` and `github`. It is checked before any issue is read:
without it the run is refused with the missing scopes and nothing starts.

```bash
keel swarm-run .keel/project.yaml --root . --issues 714,715 --live \
  --approve-scope filesystem,git,github --operator "$USER" --delegate codex
```

The approved contract becomes a **delegation** (`keel.swarm_worker.ConsentDelegation`) that
records who consented, which scopes, from which source and mode, when (the consent record's
timestamp), and which run and clusters it was delegated to. It is stored as `consent` in the
run's state file (`.keel/state/swarm/<swarm_id>.json`) and in `swarm-run --json`, each worker's
record carries the `scopes` it was handed, and each pull request body names the delegation.

- **Explicit, never ambient.** The parent hands each worker the delegation as an argument. The
  worker's children — the implementer, git, the gates — run with `KEEL_APPROVE_SCOPE`,
  `KEEL_OPERATOR` and `KEEL_CONSENT_MODE` removed from their environment, so an implementer that
  runs `keel` itself cannot approve its own mutations with the parent's consent. The implementer
  also runs without the forge credentials the `git`/`github` scopes are exercised with (see the
  trust notes above): the scopes are delegated to the worker, whose push and pull request keel
  makes itself, and not to the agent the worker dispatches.
- **Never wider.** A worker is handed exactly the parent's effective scopes — an extra approved
  scope such as `secrets` is not passed down — and a cluster the delegation does not name gets
  none. Before its first mutation a worker checks that what it holds covers every mutation it
  will make, and does nothing otherwise.
- **Agent mode delegates nothing.** `consent_mode: agent` leaves approval to a host agent's own
  permission system; `swarm-run` dispatches its workers itself, so a live swarm under `agent`
  mode is refused and asks for explicit scopes. A delegation also names its operator: approving
  scopes without `--operator` is refused.
- **On the record in the run ledger too** ([#1400](https://github.com/berkayturanci/keel/issues/1400)).
  Before any worker starts, `swarm-run --live` appends a `consent_delegation` record — event
  `delegated` — to the run ledger: run, clusters, scopes, operator, source, mode and when. A run
  whose delegation cannot be written (the ledger path escapes the root, or the file cannot be
  appended to) is refused and starts nothing. Each worker that opens its pull request adds one
  more line — event `pull_request` — naming the cluster, the pull request's number and URL, its
  branch and the head it pushed. The ledger is append-only, so the delegation is never rewritten:
  the pull request is a second event, written by the run's collecting thread, never by two
  workers at once. A pull request line that cannot be written is a warning in the run result,
  not a failed worker — the pull request is open either way. The record's fields are in
  [command-contracts.md — Run ledger block](command-contracts.md#run-ledger-block).
- **`keel consent-verify` reads it.** For a cluster's pull request, which has no ship run,
  consent-verify takes the scopes from the `pull_request` event naming its number, and only
  while the pull request's head is still the commit the worker pushed (`head.sha` from the host,
  or `--head-sha` offline). A head that moved gets no delegated consent, and the output says why.
  Nothing is matched by branch name: a fork can name its branch `swarm/<swarm_id>/<cluster>`, so
  a cluster pull request whose own record did not reach the ledger gets no delegated consent.
  The state file and each pull request body still carry the delegation as before.
- **keel older than 1.26.0 refuses a ledger holding these lines**: those readers refused any
  record kind but a ship run. Since 1.26.0 a reader skips a kind it does not know, so a 1.26.x
  keel on the same checkout still ships and merges; an older one has to be upgraded.

### Worktree Lifecycle & Isolation

1. **Creation**: Dedicated worktrees are branched from the configured `base_branch` onto
   `swarm/<swarm_id>/<cluster_id>` ([#1262](https://github.com/berkayturanci/keel/issues/1262)).
2. **Execution**: a live worker runs the cluster's implementer seat (see
   [How a live worker implements a cluster](#how-a-live-worker-implements-a-cluster)). A dry
   run's worker is a `keel ship --dry-run` assessment, and the parent appends the cluster's team
   to every child ship — `--delegate <implementer>`, one `--review-delegate` per staffed reviewer
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
4. **Cleanup** — only on a live run; a dry run creates no worktree
   ([#1278](https://github.com/berkayturanci/keel/issues/1278)). Before cutting its worktree, a
   worker runs `git worktree prune`, which drops the registration of any worktree whose directory
   is gone (a crashed run's, after its directory was deleted), so reusing a `--swarm-id` no longer
   fails with "already used by worktree". Prune touches no directory, no branch and no locked
   worktree, and skips a sibling worker's worktree while git is still adding it. When the worker
   ends — returning or raising — `keel.swarm_worker.worktree_disposal` decides what stays:

   | The worker | Its worktree | Its branch `swarm/<swarm_id>/<cluster_id>` |
   |---|---|---|
   | opened its pull request | removed | kept: it heads the pull request |
   | failed after its implementer seat ran (`implement`, `tamper`, `commit`, `gates`, `push`, `pull_request`, or raised) | **kept for inspection** | kept |
   | failed after cutting the worktree but before its seat ran (the push URL or git setup could not be read) | removed | deleted: nothing was pushed, nothing to inspect |
   | never created the worktree (`consent`, or `git worktree add` failed) | untouched — what is at its path is not its own, e.g. a previous run's kept worktree | untouched |

   This settles the "destroyed before a lead can use it" part of #1278. Since #1400 a live
   worker's seat implements *inside* the worktree, and a lead driving a dry run cuts its own, so
   no lead is left without one; but a failed worker's worktree held the only copy of what its
   seat wrote and never committed (a failed or tampering seat's edits), and the tree its gates
   failed on — the gate output a debugger needs next is produced there — and removing it with
   the rest left only a 4096-character output tail to debug from. A kept worktree is named at the end of the worker's
   `output`, in `details` and as `worktree` on its record in the state file; `keel swarm-status
   --clean` removes it once you are done.

   `remove_swarm_worktree` answers from the directory's own state: `git worktree remove --force`,
   `rmtree` of whatever is left, and `git worktree prune` when git refused. A removal or branch
   deletion that fails is a `warnings` entry of the run result (`swarm-run --json` and the text
   summary) and leaves the worktree's path on the worker record — it used to return `True`
   unconditionally, to a caller that discarded it. Once a wave's workers are done,
   `.keel/worktrees/<swarm_id>/`, and then `.keel/worktrees/`, are removed when empty.

   **Recovery.** A `SIGKILL`ed run settles nothing. `keel swarm-status --orphans` lists what swarm
   runs left under keel's own paths and branch namespace, and `--clean` removes it — never an
   unfinished run's leftovers unless `--swarm-id` names the run, never a branch its worker pushed
   or opened a pull request for; the rules are in
   [the CLI reference](cli.md#leftovers---orphans-and---clean).

   Nothing here manages locks, so "without leaving orphaned locks" — which this line claimed until
   #1285 was audited — described a mechanism that does not exist.

### Status board (`keel swarm-status`)
Print the swarm's clusters — each one's lead, difficulty band, role, step, stage, elapsed time
and status (`queued` / `running` / `passed` / `failed` / `merged` / `held` — the full vocabulary
`SwarmWorkerStatus.status` carries) — from the persisted run state, grouped by wave, each wave
headed by how many of its workers are in each status. It is a one-shot render of that state, not
a live feed; re-run it to refresh:

```bash
keel swarm-status .keel/project.yaml --root .
```

The run state is written while the run is in flight, so the board shows how far each worker has
got ([#1280](https://github.com/berkayturanci/keel/issues/1280)). Each worker record carries:

- `wave` — the wave its cluster runs in (`0` in a record written before the field existed, drawn
  as "Wave ? (not recorded)");
- `stage` — for a live worker, the stage it is in, written as it enters it: `consent`,
  `worktree`, `implement`, `tamper`, `commit`, `gates`, `push`, `pull_request`, then `done`
  (`keel.swarm_worker.STAGES`). A worker that stops keeps the stage that stopped it; one that
  raised keeps the last stage it entered. A dry run's worker is a child `keel ship`, which reports
  no stages, so its `stage` stays empty;
- `started_at` / `finished_at` — when the worker started and ended (ISO 8601). A worker is
  `running` from the moment it starts, not when its wave does — a dry run's workers take turns,
  and the ones still waiting read `queued`. Elapsed time is derived: to `finished_at`, or to now
  for a worker still running.

A state file written before these fields still loads; the missing fields read as unset.

It exits `0` when it read the run, or when no `--swarm-id` was given and there is no run at all;
it exits `1` when the run's state file cannot be read or `--swarm-id` names a run that does not
exist, and `--json` then prints an object with an `error_code` instead of the `{}` that means "no
run". The table is in [the CLI reference](cli.md#keel-swarm-status-projectyaml---root-dir---swarm-id-id---orphans---clean---json).

`--orphans` lists the worktrees, directories and branches swarm runs left behind, and `--clean`
removes the ones no running run owns (see Cleanup above):

```bash
keel swarm-status .keel/project.yaml --root . --orphans
keel swarm-status .keel/project.yaml --root . --clean
```

---

## 4. Review (`keel swarm-review`)

> **Experimental, and not yet run on a real repository** ([#1423](https://github.com/berkayturanci/keel/issues/1423)).
> It is its own opt-in step: neither `swarm-run` nor `swarm-land` calls it.

The owner's decision on #1423: keel dispatches each cluster pull request's reviewer seats itself
and posts their verdicts with `keel review`, pinned to the head, so run → review → land works
without a host agent:

```bash
keel swarm-run    .keel/project.yaml --root . --issues 714,715 --live --delegate agy \
  --approve-scope filesystem,git,github --operator you
keel swarm-review .keel/project.yaml --root . --wave 1             # dry run: who would review what
keel swarm-review .keel/project.yaml --root . --wave 1 --live \
  --approve-scope filesystem,git,github --operator you
keel swarm-land   .keel/project.yaml --root . --wave 1 --live \
  --approve-scope filesystem,git,github --operator you
```

It reviews the plan `swarm-run` persisted, wave by wave, cluster by cluster
(`src/keel/swarm_review.py` decides, `src/keel/swarm_review_runtime.py` runs):

- **The pull request and what it must meet.** Found as `swarm-land` finds it. keel reads its head
  and resolves the tier and review contract `keel review` will resolve from its diff — the verdict
  count `keel review` refuses to under-post, and `require_distinct_vendors`.
- **Who reviews.** The cluster's reviewer seats, the ones `swarm-plan` prints as `review X, Y`
  (`knobs.team.review`, a bench, or `--review-delegate` / `--reviewers`, re-resolved through the
  same resolver with the implementer that ran kept). Each is planned as
  `keel delegate run --provider <seat> --role review` plans it. Refused: a host `subagent:` seat,
  a seat nothing makes read-only (a `delegate_profiles` entry with no `review_args`), and a seat
  from the implementer's own vendor — a review from the vendor that wrote the change is not an
  independent opinion. When what is left cannot meet the count, or `require_distinct_vendors` is
  on and two seats share a vendor, or the tier's review is the jury panel, the cluster is
  **refused** before anything runs.
- **Read-only, and the transports.** A built-in CLI seat (`claude`, `codex`, `agy`) runs with its
  vendor's documented read-only invocation in its own detached worktree at the head,
  `.keel/worktrees/<swarm_id>/<cluster_id>.review-<slot>`, so it can read the code around the
  diff; the worktree is removed when the seat ends, whichever way. An `api`/`ollama` seat has no
  tools — it cannot write, and it cannot read the checkout either — so it reviews the diff and the
  issue text its brief carries; it is accepted for that reason. Every seat runs under the
  implementer's lockdown: no forge token, no `gh` login, no git credential helper, no network
  transport for git; a change to the repository's git config or hooks while it ran holds the
  cluster.
- **The brief** is `/keel:ship` s7's: the seat's focus slice, the refute-not-approve stance, no
  cross-reading, the head pinned, the project's `policy_pack.review` additions, the issue text and
  the diff keel read with `gh pr diff` — and the one JSON verdict to end with (`verdict`
  `APPROVE` or `REQUEST_CHANGES`, `scope`, `findings`, `testing`).
- **Reading the answer.** Through the loader `keel review --reviews` uses and the evidence gate's
  substance rule. An answer that does not parse is a **failed** review — never an approval. An
  approval that carries a critical or major finding is read as `REQUEST_CHANGES`.
- **Posting.** keel re-reads the head and posts the approvals with `keel review --live` (which
  refuses again if the head moved in between), each verdict naming its seat
  (`swarm-review-<slot>-<vendor>`, vendor and model from the seat's attribution). **Nothing is
  posted** — the cluster is **held**, with the reason and every seat's findings in the report —
  when any seat requested changes, when fewer seats approved than the count, or when the head
  moved. keel's evidence gate counts a posted verdict whatever its `Verdict:` line says, so a
  posted `REQUEST_CHANGES` would let `keel merge` land the change it rejected; fix the findings,
  push, and run `swarm-review` again on the new head.

A dry run reads each pull request and prints which seats would review it at which head; it checks
out, runs and posts nothing. A live run needs `filesystem`, `git` and `github` approved, explicitly
or as standing consent, before the plan is read. See the
[CLI reference](cli.md) for every flag and the `--json` shape.

## 5. Landing (`keel swarm-land`)

Landing is coordinated by `src/keel/swarm_landing.py`; each merge is `keel merge`'s own, under
its atomic merge lock (`.keel/state/locks/merge-<sha12>.lock`):

```bash
keel swarm-land .keel/project.yaml --root . --issues 714,715,716,717 --wave 1 --live \
  --approve-scope filesystem,git,github --operator you
```

**Before a wave can land.** Because each merge is `keel merge`'s, the repository needs what
`keel merge` needs, or every cluster is **held** with its reason — which is the design, not a
fault. Measured on the first end-to-end run: with no CI on pull requests each cluster was held
as *CI did not run on a non-docs PR (empty check set)*, and once CI ran, as *evidence gate is not
enforced* — a cluster pull request then carried no keel signal, which is now fixed. So, before the first landing: CI must run on the cluster pull requests, and each
cluster pull request needs its review verdicts like any other. keel merge's evidence gate arms
on a keel signal on the pull request (the `keel:ship` gate label, a ship provenance comment, a
review verdict, …, see [evidence.md](evidence.md)). A live worker arms it at creation: right
after `gh pr create` it posts the same `keel.ship-provenance.v1` comment a live `keel ship` run
posts on its own pull request — by keel, with the operator's credentials, after the implementer
has exited. So a cluster is held as *missing evidence: …* until its review verdicts are posted —
fail closed, never merged unreviewed. Should that post fail, the pull request stays open, the
worker record says `provenance_posted: false`, and the run warns that the cluster will be held
as *evidence gate is not enforced* until the comment (`keel post-comment --artifact
ship-provenance`) or its verdicts are posted.

Measured on the second end-to-end run, with verdicts posted and CI green, each cluster was
still held twice over: its pull request carried no `agent:<vendor>` label, and no gates-pass
was recorded for its head
([#1420](https://github.com/berkayturanci/keel/issues/1420)). The worker now leaves both (steps
9 and 10 above), so the review verdicts are the one thing a cluster still needs from you. When
either is missing anyway — the worker record says `labels_applied: false` or `gates_recorded:
false`, and the run warns — the cluster is held as *blocking finding(s): attribution-label: …*
or *no gates-pass recorded for the current head …* until you apply the labels `keel
attribution` prints or record the gates (`keel ship --live --append-ledger --capture-status
not-run --pull-request <n> --head-sha <sha>`). `keel merge` names every missing item and every
blocking finding when it refuses; it used to name the missing items alone, which left a
refusal on a finding reading `missing evidence: ` with nothing after it. A project whose
gates include a blocking gate the worker defers (the jury, a `pre-merge` gate) gets a
gates-pass record that is not a pass, by design: those gates have to run on the head before
it lands.

### Which plan lands

Before any worker starts, `swarm-run` writes the plan it executes to
`.keel/state/swarm/<swarm_id>.plan.json`, beside the run's state file, with the same atomic
writer: `SwarmPlan.to_dict()` — waves, clusters, dependencies, difficulty, staffing, every
issue's scope and where it came from — under `{"schema": "keel.swarm-plan", "version": 1}`.
It is the plan as planned; a cluster dropped by a mid-run rebalance is still in it, has no pull
request, and is held at landing.

`swarm-land` lands **that plan's wave**
([#1275](https://github.com/berkayturanci/keel/issues/1275)). It used to rebuild the plan from
`--issues`, and a rebuild is not the plan that ran: re-scope an issue, give it an `area:` label
or edit its `Scope:` line between the run and the landing, and the partition moves — a cluster
id such as `cluster-2-102` becomes `cluster-1-102`, whose branch does not exist. Now:

- **A persisted plan is landed as written**, and `--issues` is optional. Issues that are named
  are read and re-planned only to compare; any difference — the issue set, a wave's clusters, an
  issue's scope — is printed to stderr as a warning, one line each, and the persisted plan is
  landed regardless. The run's branches were cut from it.
- **No persisted plan** (a run from before #1275, or no run): the wave is re-planned from the
  issues as before, and stderr says so.
- **A persisted plan keel cannot use** — unreadable, not JSON, a field missing or of the wrong
  type, another run's plan, or a schema version this keel does not read — is refused with exit
  1 before any issue is read. Re-planning around it would be the silent switch this file exists
  to prevent; remove it to re-plan deliberately.

`--json` reports which happened as `plan_source` (`"persisted"` / `"re-planned"`) and the
warning's lines as `plan_drift`.

### What landing actually does

`evaluate_wave_landing_mode` decides from the plan's wave mode whether the wave lands at all:

- **Refused** — the wave is `sequential_dependent`, whatever its size (reason
  `depends_on_earlier_wave`, [#1276](https://github.com/berkayturanci/keel/issues/1276)). Its
  branches were cut before the earlier wave it depends on landed, so `swarm-land` lands none of
  it, dry run or live: it looks up no pull request and calls no `keel merge`, reports
  `mode : refused` with `refused : wave N depends on issues landed by an earlier wave (#a, #b)…`,
  and exits 1. Land the earlier wave, then re-plan the remaining issues (`keel swarm-plan` /
  `swarm-run` without the landed ones, so they plan as a fresh wave 1 on the moved base) and land
  again. The refusal is chosen from the plan's dependency edges, not from how far the base branch
  has actually moved since each branch was cut; comparing against that drift is the open half of
  #1276.
- **Otherwise** (`direct_batch`; `sequential_funnel` only when a caller supplies overlapping
  diffs, which the CLI does not) every cluster of the wave is landed in plan order, each on its
  own ([#1287](https://github.com/berkayturanci/keel/issues/1287)):

1. **Find the cluster's pull request.** The number the live worker recorded in the run state,
   confirmed with `gh pr view` to be open, headed by `swarm/<swarm_id>/<cluster_id>` and aimed at
   the configured base branch. With no record (a run from before #1287, or a pull request opened
   by hand), the one open pull request `gh pr list --head swarm/<swarm_id>/<cluster_id>` names. No
   open pull request, several, a merged or closed one, or one for another branch or base holds
   the cluster with that reason.
2. **Hand it to `keel merge`.** Not a copy of it: `swarm-land` parses a `keel merge` argv with
   `keel merge`'s own parser and runs the same function, so the cluster's pull request gets the
   merge window, the merge lock (claimed and released for this one merge, so a concurrent
   `keel merge` waits between clusters rather than racing one), the operator consent, the merge
   state (`DIRTY`, `BLOCKED` and the rest refuse), the CI rollup, the review-evidence gate, the
   gates-pass for the head, the checkpoint gate, the squash pinned to the verified head, and the
   post-merge drift check. `--transport`, `--approve-scope`, `--operator` and `--consent-mode` are
   passed through; the method is `keel merge`'s default, squash.
3. **Report it.** Merged: `landed`, the worker `merged` at `s10` in the run state. Refused by any of
   the above: `held` with `keel merge`'s reason, e.g. `PR #12: keel merge: merge window is closed`,
   `PR #12: keel merge: missing evidence: review-verdict-1` or `PR #12: keel merge: blocking
   finding(s): attribution-label: …`. The merge call itself failed:
   `failed`. Drift after a merge: `landed`, with a `warning` to run `keel verify-merge`.
4. **Close what landed** ([#1422](https://github.com/berkayturanci/keel/issues/1422)). A cluster
   that merged is closed the way `/keel:ship` closes an issue at s11–s12; a held or failed one
   posts and closes nothing. The cluster's pull request says `Refs #N`, never `Closes` (see
   [How a live worker implements a cluster](#how-a-live-worker-implements-a-cluster)), so
   nothing else closes its issues:
   - **The record.** The `ship_run` record whose gates-pass `keel merge` accepted for the merged
     head — the one the live worker appended — is appended again as the landing's:
     `command: swarm-land`, `assessment.merge.action: merge`, the merged head, the reviewers
     whose verdicts counted for that head, and this landing's run context (host agent,
     transport, operator consent). The gates, changed files, implementer, issue and capture
     block are carried as recorded, so the head's gates-pass and the attribution labels still
     agree with it, and `keel close-reconcile` finds each close attested by a merge.
   - **The closure comment.** Rendered from that record by keel's closure renderer — the
     `keel.closure-comment.v1` artifact `/keel:ship` posts, no other format — and posted through
     `keel post-comment`'s code, with the run id `<swarm_id>/<cluster_id>:closure`, to the pull
     request and to each of the cluster's issues.
   - **The issues**, each closed as completed.

   It runs under the operator's consent: a live landing asks for `keel merge`'s side effects
   and the closure's (`comments`, `issue_close`), all inside `filesystem,git,github`. `--json`
   reports each cluster under `closures` — `closure_posted` (`pr`/`issue`, number, `posted` or
   `edited`), `closed_issues`, `already_closed`, `warnings` — and the text report under
   `closure :`. Whatever cannot be done is a `warning` and stops nothing: no consent, no
   gates-pass record for the merged head, a run ledger that cannot be written, a comment or a
   close that fails. The merge is never undone, and the next cluster still lands. Re-running it
   posts nothing twice: a landing record already appended for the head is rendered from rather
   than appended again, this run's closure comment is edited in place where it already is, and
   an issue already closed is left as it is. A dry run posts and closes nothing; `closures`
   says what the live landing would close.

   Afterwards `keel evidence-verify --phase all --pr <n> --issue <issue>` passes both closure
   items for the pull request (name the issue: `Refs` does not link it). A cluster whose merge
   landed in an earlier run is held at the lookup as already merged, so its closure is not
   retried; finish one that warned by hand, as `/keel:ship`'s s11–s12 would.

The next cluster is tried whatever happened to the one before, and the wave exits non-zero when
any cluster did not land. A **dry run** (no `--live`) runs `keel merge --dry-run` for each
cluster — every check above, no merge — and reports what the live landing would do; like
`keel merge --dry-run`, it needs the operator's consent. A dry run writes nothing to the run state.

**Nothing happens in your checkout.** Before #1287 a landing checked out the base branch and
merged each cluster branch into it inside the checkout `--root` points at; that never reached the
repository and left every pull request open. That path is gone rather than kept behind a flag, because every
cluster a live run produces has a pull request to merge. `swarm-land` checks out, rebases and
merges nothing locally, so it no longer refuses a dirty working tree. It still reads where HEAD is
before the wave and puts it back if anything moved it, with
`warning : could not return the checkout to <branch>…` when it cannot
([#1279](https://github.com/berkayturanci/keel/issues/1279)).

### Review Evidence Gate (`knobs.swarm_review_evidence`)

The review-evidence gate is `keel merge`'s: an armed gate, the tier-derived verdict count, and
verdicts pinned to the pull request's head (#828). A cluster whose pull request does not verify is
**held** with the missing items named, and the wave exits non-zero, so automation cannot read
"refused to land unreviewed code" as success. The gate runs in dry runs too.

`knobs.swarm_review_evidence: false` used to skip this gate at landing. Since landing is
`keel merge` ([#1287](https://github.com/berkayturanci/keel/issues/1287)), whose gate has no
opt-out, it skips nothing: `swarm-land` prints
`swarm-land: knobs.swarm_review_evidence: false has no effect …` to stderr and holds any cluster
without evidence like any other. See [configuration.md](configuration.md#swarm_review_evidence)
and the `keel swarm-land` section of [cli.md](cli.md).

---

## 6. Visual Dashboard Integration (`keel-visual swarm`)

Swarm integrates directly with the companion package `keel-visual` to provide rich spatial observability:

```bash
# Generate a static HTML report
keel-visual swarm .keel/project.yaml --root . --out keel-swarm.html

# Serve that rendered report on localhost (a snapshot — re-run to refresh)
keel-visual swarm .keel/project.yaml --root . --serve --port 8766
```

It finds the newest run by its state file, `.keel/state/swarm/<swarm_id>.json` (or the one
`--swarm-id` names), never by the `<swarm_id>.plan.json` beside it, and draws the plan
`swarm-run` persisted in that file. A run with no plan file (one from before
[#1275](https://github.com/berkayturanci/keel/issues/1275)), or one keel-visual cannot read —
not JSON, not a `keel.swarm-plan`, a schema version other than `1`, another run's plan — shows
a sentence saying so in place of the DAG; the worker matrix still shows the run's state. It
never re-plans: a plan rebuilt from the workers has no predicted files, so it was always one
flat wave with no dependencies, a picture of a plan that never ran
([#1280](https://github.com/berkayturanci/keel/issues/1280)). With a core that has
`keel.swarm.swarm_plan_from_payload`, the plan is parsed by the same strict reader
`swarm-land` uses, so the page never draws a plan `swarm-land` would refuse.

### Visual Features:
- **2D DAG Cluster Partition View**: the persisted plan's waves — each labelled
  `Orthogonal Parallel` or `Dependent — Refused` — and their cluster cards: issue pills, each
  issue's title and where its scope came from, the predicted files (six, then `+N more`), the
  issues a cluster depends on and the wave each lands in, the difficulty band, the implementer,
  and the role badge.
- **Pseudo-3D Multi-Wave Topology**: An HTML5 Canvas renderer projecting the stacked wave layers as
  a pseudo-3D scene, with drag-to-rotate and scroll-to-zoom.
- **Worker Matrix**: Worker cards showing each cluster's state —
  `queued`/`running`/`passed`/`failed`/`merged`/`held` —
  its role badge, and the recorded `details` string.

The rendered page is a snapshot of the run state at render time; re-run `keel-visual swarm` to
refresh it. (The continuously polling board is `keel-visual serve`, which renders *ship* runs —
a different view from this one.)

---

## 7. Review Evidence & Compound Learning

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

## 8. Competitive Comparison Matrix

| Feature / Capability | Keel Swarm | CrewAI | AutoGen | MetaGPT | Devin / OpenHands |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Deterministic Static Analysis** | **Yes (`swarm-plan`)** | No (LLM prompt loop) | No | No (SOP templates) | No |
| **Deterministic Difficulty Scoring** | **Yes (pure, signal-attributed)** | No | No | No | No |
| **Declared Org Chart (CTO/lead/worker)** | **Yes (`knobs.team`, one resolver)** | Role prompts | Conversational | SOP roles | Single agent |
| **Fixed Backbone Machine** | **Yes (`s0`–`s12` immutable)** | No | No | No | No |
| **Isolated Git Worktrees** | **Yes (`.keel/worktrees/`)** | No (shared workspace) | No | No (file overwrite) | Docker container |
| **Batch landing under one writer lock** | **Yes (each cluster's PR through `keel merge`, one at a time)** | No | No | No | PR per run |
| **Atomic Single-Host Lock** | **Yes (`merge_lock`)** | No | No | No | No |
| **Fail-soft conflict handling** | **Yes (a `DIRTY` PR is held; the wave goes on)** | No | No | No | Manual |
| **Per-Branch Review-Evidence Gate** | **Yes (cross-vendor panel per cluster, when configured)** | No | Conversational | No | Single Agent |
| **2D DAG & pseudo-3D snapshot** | **Yes (`keel-visual`, rendered)** | Basic Tree | Plotly / None | Static Diagrams | Web Terminal |

---

## 9. Risk & Failure Mitigations

| Risk / Failure Scenario | Detection Mechanism | Fail-Soft Mitigation |
| :--- | :--- | :--- |
| **A cluster changes files another cluster also touches** | Plan-time static file-overlap partitioning (disjoint trees only share a wave) + isolated per-cluster worktrees | Overlapping clusters are sequenced into later waves. A failed cluster's issue is dropped from the plan and every later wave still runs ([#1268](https://github.com/berkayturanci/keel/issues/1268), fixed in [#1312](https://github.com/berkayturanci/keel/pull/1312)); nothing is re-scheduled — see item 3 above. |
| **Merge conflict during landing** | `keel merge` reads the pull request's merge state | A `DIRTY` (or `BLOCKED`, …) pull request is held with that state named; nothing is merged for it, and the next cluster is tried ([#1287](https://github.com/berkayturanci/keel/issues/1287)). |
| **Concurrent Merge Race Condition** | `merge_lock` file mutex | Atomic `mkdir`-based lock, claimed by `keel merge` for each cluster's merge. When another writer holds it, that cluster is reported `held` with `resource lock is already held`, the hold is written to the run state, and it is not retried ([#1272](https://github.com/berkayturanci/keel/issues/1272), [#1287](https://github.com/berkayturanci/keel/issues/1287)) — landing is single-writer by refusal. |
| **Worker Subprocess Crash / OOM** | Subprocess exit status monitoring | Fail-soft error capture in `SwarmRunState`; remaining parallel workers continue unimpeded. |
| **Missing or unreadable run state** | `load_swarm_state` JSON/Value/Key/Type/Overflow errors, and an `OSError` opening the file | Fails soft to no state rather than raising; the plan is a separate file, so a lost state file costs the board, not the landing. |
| **The issues changed between the run and the landing** | `swarm_plan_drift` against the persisted plan | `swarm-land` lands the persisted plan — the one whose branches exist — and prints each difference as a warning ([#1275](https://github.com/berkayturanci/keel/issues/1275)). |
| **Missing or unreadable persisted plan** | `load_swarm_plan`: absent → `None`; unreadable, not JSON, malformed, another run's, or an unknown schema version → `SwarmPlanError` | Absent: `swarm-land` re-plans from `--issues` and says so. Unusable: refused with exit 1, never re-planned around. |
| **A cluster scored lighter than it turns out to be** | The lead's own progress against the plan | The lead reports through its worker record and the CTO re-plans; a lead never re-staffs itself, so the run's team stays the one the plan published. |
| **`--team` names a bench that is not configured** | `assignment.warnings` at plan time | The run falls back to the configured policy and says so; the name is never silently ignored. |
