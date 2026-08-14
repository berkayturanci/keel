# keel — Competitive Landscape and Comparison

> Research report compiled June 2026 (citation-backed). Facts are cited inline; my interpretation/assessment is explicitly labeled **Assessment**.

## What keel is (baseline for comparison)

keel is an **agent-agnostic, project-agnostic work-ownership backbone** that drives a
single GitHub issue end-to-end through a fixed lifecycle: issue intake/readiness →
branch/worktree → implement (coding agent) → push → CI wait → multi-agent code review
(review→debate→verify→synthesize) → project test/build/lint gates → risk classification
(TIER 1/2/3 → reviewer count) → safe merge → close → capture hooks. v1 includes the
capture marker/verifier contract, redaction-before-durability guardrails, capture-health
surfacing, and optional learning-quality decisions in the run ledger. Distinctive elements:

- **Agent adapters** (Claude Code, Codex, Gemini, Antigravity) behind one backbone.
- **`.keel/project.yaml`** per project (base branch, build/lint/test commands, CI names, file globs) + pluggable "Lego" extension gates.
- **Merge invariants**: timezone-aware merge *window* ("night no-merge"), `mkdir`-based merge *lock* (mutual exclusion), risk-tiered reviewer counts, fix-loop with capped budget.
- **Pure-core + thin-IO, deterministic, stdlib-only ethos** (sibling `ai-jury` is the multi-agent review engine).

The crux of keel's novelty hypothesis: nobody combines *(fixed issue→done ownership
backbone)* + *(agent-agnostic adapters)* + *(merge-window/lock invariants)* +
*(multi-agent debate review)* + *(closeout capture with learning-quality decisions)*
in one open, deterministic tool. The research below tests that.

## Executive comparison — work ownership, not only automation

Keel does not compete with a single category. The closest tools each own one slice:

- **Issue-to-PR coding agents** such as GitHub Copilot coding agent, OpenHands, and
  SWE-agent can take an issue or task, edit code, and create or update a pull request.
  GitHub's Copilot docs explicitly describe assigning an issue or prompt so the agent
  works on the task, raises a PR, and requests review.
  [GitHub Copilot coding agent docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/assign-copilot-to-an-issue)
- **AI PR reviewers** such as CodeRabbit, Qodo / PR-Agent, Greptile, and Cursor Bugbot
  operate after a PR exists. Their strongest surface is review comments, summaries,
  suggested fixes, and repository context.
  [Qodo code review overview](https://docs.qodo.ai/code-review),
  [Cursor Bugbot docs](https://docs.cursor.com/bugbot)
- **Merge queues** such as GitHub Merge Queue, Mergify, Graphite, and Trunk operate
  after a PR is ready to merge. They protect the base branch with queues, batching,
  speculative checks, priority, pause/freeze, or anti-flake behavior.
  [GitHub merge queue docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue),
  [Mergify pause docs](https://docs.mergify.com/merge-queue/pause/)

Keel's product claim is narrower and more integrated: it turns a coding agent into a
work owner. It starts before the PR exists and ends after merge, closure, capture markers,
and optional project-owned learning decisions.

| capability | Keel | Coding agents | PR reviewers | Merge queues | Multi-Agent Swarms (CrewAI/AutoGen/Swarm) |
|---|---:|---:|---:|---:|---:|
| Takes an issue as input | yes | yes | no | no | yes |
| Performs intake/readiness gating | yes | partial | no | no | no |
| Implements code | yes, through adapters | yes | no | no | yes |
| Opens or updates a PR | yes | yes | no | no | partial |
| Performs independent review / jury | yes | partial | yes | no | ❌ (LLM chat only) |
| Runs project gates | yes | partial | partial | yes, as required checks | ❌ |
| Owns merge window + lock | yes | no | no | partial queue controls | ❌ |
| Closes the issue / PR loop | yes | partial | no | partial | no |
| Supports multi-issue work blocks | yes | partial | no | queue-only | yes (unconstrained) |
| Conflict-free DAG clustering | yes (Keel Swarm) | no | no | no | ❌ |
| Direct batch landing & self-healing | yes (Keel Swarm) | no | no | partial | ❌ |
| Supports resume/checkpoint/reconcile | yes | partial | no | partial queue state | partial |
| Captures post-merge learning | yes, policy-gated | no | partial repo memory | no | no |
| Project policy extensibility | yes | partial | partial | yes | partial |
| Agent/vendor agnostic | yes | partial | no | not applicable | yes |

**Assessment:** no reviewed tool owns this whole loop as a single product contract. The
individual parts are proven elsewhere, which lowers risk: coding agents prove issue→PR,
PR reviewers prove diff analysis, and merge queues prove safe merge serialization. Keel's
job is to connect those proven pieces into one deterministic, project-neutral lifecycle.

## Borrowed ideas mapped to the roadmap

| idea to borrow | source category | Keel issue |
|---|---|---|
| Plan-before-code / readiness before implementation | coding agents + human team workflow | shipped in v1 |
| Cloud/session progress and operator visibility | Copilot coding agent sessions, coding agent UX | shipped as ledger snapshots / status in v1 |
| Checkpoint and resume after interrupted work | coding agent sessions + merge queue state | shipped in v1 |
| Queue pause/freeze and out-of-order hotfix language | Mergify, Graphite, Trunk | shipped via #18/#20 |
| Repository memory/context with redaction | Greptile, PR review tools | shipped as capture policy, redaction, and learning decisions in v1 |
| Low-noise inline vs summary review UX | CodeRabbit, Qodo / PR-Agent, Cursor Bugbot | shipped basics; refine via review-cycle work |
| Plugin/marketplace install surface | agent platforms and Claude Code plugin model | #135 |
| Capability detection and safe degradation | agent platform packaging and local tool variance | shipped basics; reused by #134 |

---

## Category 1 — Merge automation / merge queues

### Mergify
- **What**: Hosted merge queue + merge automation for GitHub/GitLab, with queue rules, batching, and "merge protections." [mergify.com/product/merge-queue](https://mergify.com/product/merge-queue)
- **License / model**: Proprietary SaaS (open-source `Mergifyio/mergify` repo exists but the product is a hosted service). [github.com/Mergifyio/mergify](https://github.com/Mergifyio/mergify)
- **Overlap with keel**: Gatekeeps merges on CI + approvals; separates *queue conditions* from *merge conditions* (analogous to keel's "enter pipeline" vs "ready to merge"). [docs.mergify.com/merge-queue/rules](https://docs.mergify.com/merge-queue/rules/)
- **Merge windows — the key finding**: Mergify is the closest analogue to keel's merge-window concept. It supports a **`schedule` condition** (e.g. don't merge on weekends/nights) and **timezone-aware time conditions** (`current-time`, `schedule`, `created-at`, etc.), plus **Queue Pause** and (deprecated) **Queue Freeze** for halting merges while CI keeps running. [docs.mergify.com/merge-protections/freeze](https://docs.mergify.com/merge-protections/freeze/), [docs.mergify.com/merge-queue/pause](https://docs.mergify.com/merge-queue/pause/)
- **What keel does that Mergify does not**: drives the *implementation* step (coding agent writes the code), runs *multi-agent debate review*, and is agent-agnostic. Mergify starts from an existing PR; it never authors code.
- **Idea to borrow**: Mergify's explicit *timezone-aware schedule condition* and the *pause vs. freeze* distinction (pause = stop everything incl. running checks; freeze = stop merges, let CI run). keel's merge window could expose a "freeze" mode (block merge, keep running gates) vs a hard "pause."

### bors-ng (and homu, its predecessor)
- **What**: Elixir merge bot enforcing an "evergreen" main: batches `r+`'d PRs, tests them on a staging branch, bisects failures, merges only the green set. Spiritual successor to Rust's `homu`. [github.com/bors-ng/bors-ng](https://github.com/bors-ng/bors-ng), [mergify.com/blog/the-origin-story-of-merge-queues](https://mergify.com/blog/the-origin-story-of-merge-queues/)
- **License / popularity**: **Apache-2.0**, ~**1.5k** stars, primarily Elixir, **deprecated** (bug fixes only; author points users to GitHub's native merge queue). [github.com/bors-ng/bors-ng](https://github.com/bors-ng/bors-ng)
- **Overlap**: Comment-driven merge gating + "never break main."
- **What keel does that bors does not**: implementation, review, agent-agnosticism, merge windows, risk tiers. bors is purely a serialization/batching layer.
- **Idea to borrow**: bors's **bisection on batch failure** is elegant — but keel merges *one issue at a time*, so batching/bisection is largely out of scope (see honest-skip list).

### GitHub native merge queue
- **What**: Built-in queue that tests PRs against the prospective post-merge state; configurable min/max batch size, timeout, CI-wait timeout. [docs.github.com/.../managing-a-merge-queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue), [github.blog/.../github-merge-queue-is-generally-available](https://github.blog/news-insights/product-news/github-merge-queue-is-generally-available/)
- **License / model**: Proprietary (GitHub feature).
- **Merge windows — key finding**: GitHub merge queue **does NOT natively support scheduled merges or no-merge windows.** This is repeatedly requested by the community and solved only via workarounds: a scheduled Action that flips a required-status-check, or marketplace actions like **"No Out of Hours Merge"** and **"Merge Schedule."** [github.com/orgs/community/discussions/61147](https://github.com/orgs/community/discussions/61147), [github.com/marketplace/actions/no-out-of-hours-merge](https://github.com/marketplace/actions/no-out-of-hours-merge), [github.com/marketplace/actions/merge-schedule](https://github.com/marketplace/actions/merge-schedule)
- **Assessment**: This is a meaningful validation of keel's design — *time-windowed merging is a real, unmet need that even GitHub punts on.* keel bakes it into the engine instead of bolting on a self-failing status check.

### Graphite Merge Queue
- **What**: Stack-aware merge queue; parallel/fast-forward merges for stacked PRs, reordering, pause-the-queue, hotfix-out-of-order. [graphite.com/docs/graphite-merge-queue](https://www.graphite.com/docs/graphite-merge-queue)
- **License / model**: Proprietary; Team/Enterprise plans. [graphite.com/guides/merge-queue-tools-options](https://graphite.com/guides/merge-queue-tools-options)
- **Overlap**: Queue + pause controls.
- **What keel does that Graphite does not**: implementation + review + agent-agnosticism + windows. Graphite has no AI authoring/review in its merge queue.
- **Idea to borrow**: **pause the queue + out-of-order hotfix** — keel could allow a "hotfix bypass" that skips the merge window for a flagged emergency issue.

### Trunk.io Merge Queue
- **What**: CI-reliability-focused queue: parallel lanes by touched code, batching with bisection (cuts CI cost up to ~90%), anti-flake retries; supports forked/OSS PRs. [trunk.io/merge-queue](https://trunk.io/merge-queue), [trunk.io/changelog/merge-support-for-forked-and-open-source-repos](https://trunk.io/changelog/merge-support-for-forked-and-open-source-repos)
- **License / model**: Proprietary SaaS.
- **Overlap**: CI-gated merging.
- **Idea to borrow**: **anti-flake retry** semantics — keel's CI-wait step could optionally re-run a known-flaky required check once before failing the fix-loop (must stay deterministic/opt-in; see skip discussion).

### homu
- Covered above as bors-ng's Rust predecessor; legacy, Rust-ecosystem-specific. [mergify.com/blog/the-origin-story-of-merge-queues](https://mergify.com/blog/the-origin-story-of-merge-queues/)

**Category 1 takeaway (Assessment)**: Merge queues solve *serialization/batching*, a problem keel mostly sidesteps (one issue at a time). The genuinely transferable idea is **timezone-aware schedule/freeze**, where Mergify is ahead and GitHub is absent — and keel already has it natively. keel's window/lock is *not* novel as a concept (Mergify proves the demand) but is novel as a **first-class, deterministic, stdlib invariant inside an issue→merge agent pipeline.**

---

## Category 2 — AI PR review / AI coding agents

### Qodo Merge (formerly CodiumAI PR-Agent)
- **What**: AI PR reviewer/assistant; `/review`, `/improve`, `/describe` commands on PRs. [qodo.ai/blog/unveiling-the-future-of-streamlined-software-development](https://www.qodo.ai/blog/unveiling-the-future-of-streamlined-software-development/)
- **License / popularity**: Open-source core **PR-Agent under Apache-2.0**, self-hostable via Docker with your own LLM keys; stewardship moved to community org **The-PR-Agent** and license restored to Apache-2.0. Hosted "Qodo Merge" is the commercial tier. [github.com/The-PR-Agent/pr-agent](https://github.com/The-PR-Agent/pr-agent), [futurumgroup.com/.../qodo-hands-pr-agent-to-the-community](https://futurumgroup.com/insights/qodo-hands-pr-agent-to-the-community-will-open-governance-accelerate-ai-code-review/)
- **Overlap**: The "review" portion of keel's backbone; multi-platform (GitHub/GitLab/Bitbucket/Azure/Gitea).
- **What keel does that it does not**: orchestrates the *whole* lifecycle (branch→implement→merge→close), multi-agent *debate* (PR-Agent is single-model per command), merge windows/locks, risk tiers.
- **Idea to borrow**: PR-Agent's **multi-platform adapter abstraction** and its **command palette** model — keel's review step is conceptually one "command" in a richer taxonomy.

### CodeRabbit
- **What**: AI-first PR reviewer with line-by-line suggestions and chat; >10M PRs across ~1M repos; free Pro for public repos. [coderabbit.ai](https://www.coderabbit.ai/), [docs.coderabbit.ai](https://docs.coderabbit.ai/)
- **License / model**: Proprietary SaaS (~$24/user/mo; free for OSS).
- **Overlap**: PR review only.
- **What keel does that it does not**: full lifecycle, agent-agnostic authoring, multi-agent debate, merge invariants.
- **Idea to borrow**: CodeRabbit's **summarized, threaded review UX** and its public **benchmark posture** (it advertises top F1 on Martian's benchmark). keel/ai-jury could publish a reproducible review-quality benchmark. [coderabbit.ai/blog/coderabbit-tops-martian-code-review-benchmark](https://www.coderabbit.ai/blog/coderabbit-tops-martian-code-review-benchmark)

### Sweep
- **What**: Open-source "AI junior dev": reads an issue, searches the repo, writes code, opens a PR. [github.com/sweepai/sweep](https://github.com/sweepai/sweep)
- **License / popularity**: **Apache-2.0**, ~**6k+** stars (note: repo has since pivoted toward a JetBrains assistant). [aiagentslist.com/agents/sweep-ai](https://aiagentslist.com/agents/sweep-ai)
- **Overlap**: This is the closest *issue→PR* analogue to keel's front half.
- **What keel does that Sweep does not**: agent-agnosticism (Sweep is its own agent), multi-agent review, gates, risk tiers, merge windows/locks, and the *merge+close* tail. Sweep stops at "opens a PR; you review and merge."
- **Idea to borrow**: Sweep's **issue triage → file localization** heuristics for the "select issue/implement" step.

### Aider
- **What**: Terminal pair-programming agent; edits files via diffs, strong git/multi-file workflows; scores well on SWE-bench. ~**45.6k** stars. [github.com/bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- **License**: Apache-2.0 (commonly).
- **Overlap**: The *implement* step — Aider is exactly the kind of agent keel would wrap in an adapter.
- **What keel does that it does not**: everything around implementation (CI wait, review, gates, merge). Aider is a tool keel *consumes*, not a competitor.
- **Idea to borrow**: Aider's **repo-map / diff-application discipline** as adapter expectations.

### OpenHands (formerly OpenDevin)
- **What**: Open platform for cloud coding agents; edits files, runs commands, browses web; **model-agnostic** (Claude/GPT/DeepSeek/Qwen/Llama/Ollama). ~**68.6k** stars, **MIT**. [github.com/OpenHands/OpenHands](https://github.com/OpenHands/OpenHands), [openhands.dev](https://www.openhands.dev/)
- **Overlap**: Autonomous implement step; *model*-agnostic (vs keel's *agent*-agnostic).
- **What keel does that it does not**: prescriptive issue→merge backbone, multi-agent debate review, merge windows/locks, risk tiers. OpenHands is a flexible agent platform, not an opinionated merge pipeline.
- **Idea to borrow**: OpenHands's **sandboxed execution** discipline (keel's `ai-jury` already sandboxes reviewers; reinforces that posture).

### SWE-agent
- **What**: Princeton/Stanford agent that takes a GitHub issue and fixes it via a custom **Agent-Computer Interface (ACI)**; NeurIPS 2024; SOTA on SWE-bench. **MIT**. [github.com/SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent)
- **Overlap**: issue→fix implementer.
- **What keel does that it does not**: the entire orchestration/merge layer; multi-agent review.
- **Idea to borrow**: the **ACI concept** — a clean, constrained tool surface the agent operates against; informs how keel's adapters should expose repo operations.

### Cursor / Devin-likes
- Proprietary IDE/autonomous-dev products. Adjacent to the *implement* step; not merge-pipeline orchestrators. (No specific OSS claim to cite; treated as proprietary context.)

**Category 2 takeaway (Assessment)**: There is a dense field of *issue→PR* agents (Sweep, SWE-agent, OpenHands) and *PR-review* tools (Qodo/PR-Agent, CodeRabbit). **None combines both halves and wraps them in an agent-agnostic merge pipeline with merge invariants.** Crucially, I found *no* mainstream tool doing **multi-agent debate→verify→synthesize review on PRs** in production — that pattern lives in research (arXiv) and a Claude Code skill ("Star Chamber"), not in a packaged reviewer. That's keel/ai-jury's strongest differentiator.

---

## Category 3 — CI gate aggregation / policy-as-code

### Danger / Danger JS
- **What**: Runs in CI to automate code-review chores (PR conventions, changelog presence, file-touch rules); huge CI coverage. **MIT.** [github.com/danger/danger-js](https://github.com/danger/danger-js), [danger.systems/js](https://danger.systems/js/)
- **Overlap**: keel's pluggable "Lego" gates are conceptually Danger rules expressed in keel's project config.
- **What keel does that it does not**: agent authoring, AI review, merge orchestration/windows. Danger only *comments/fails*; it doesn't drive the lifecycle.
- **Idea to borrow**: Danger's **Dangerfile rule model** — a small, declarative per-project rule surface — as inspiration for keel's extension-gate API.

### reviewdog
- **What**: Universal adapter that turns linter/SAST output into PR review comments across GitHub/GitLab/Bitbucket. [claude-plugins.dev/skills/.../reviewdog](https://claude-plugins.dev/skills/@AgentSecOps/SecOpsAgentKit/reviewdog) (project: github.com/reviewdog/reviewdog, MIT)
- **Overlap**: Normalizes heterogeneous gate output — directly analogous to keel needing a uniform result schema from build/lint/test gates.
- **Idea to borrow**: reviewdog's **`-diff`/efm normalization** — a canonical "finding" format keel's gates emit (line, severity, message), enabling consistent fix-loop input.

### pre-commit.ci
- **What**: Hosted service that runs `pre-commit` hooks on PRs and auto-fixes. (pre-commit is OSS, MIT; the `.ci` hosted service is free for OSS.) [pre-commit.ci]
- **Overlap**: Pre-merge gate execution + autofix.
- **Idea to borrow**: **auto-fix-and-push** for trivial lint failures inside keel's fix-loop, bounded by the existing capped budget.

### Open Policy Agent / Conftest
- **What**: Rego-based policy-as-code; Conftest tests structured config; both usable to gate PRs (validate commit/PR metadata, block merge on policy fail). **Apache-2.0**, CNCF. [github.com/open-policy-agent/conftest](https://github.com/open-policy-agent/conftest), [openpolicyagent.org/docs/cicd](https://www.openpolicyagent.org/docs/cicd)
- **Overlap**: Declarative merge-gating policy.
- **What keel does that it does not**: agent authoring/review/orchestration. OPA is a policy engine, not a pipeline.
- **Idea to borrow (Assessment, with caveat)**: A *declarative policy layer* for keel's risk tiers / required gates is appealing, but adopting Rego/OPA would **violate keel's stdlib-only, deterministic, no-runtime-dep ethos.** Borrow the *concept* (policy expressed in `.keel/project.yaml`), not the engine.

### Backstage software templates / scaffolder
- **What**: Golden-path scaffolder; YAML-defined templates generate services with baked-in CI/security/golden paths. Backstage is CNCF (Apache-2.0); community template repos are MIT. [backstage.io/docs/features/software-templates](https://backstage.io/docs/features/software-templates/)
- **Overlap**: The *project-agnostic, YAML-configured* philosophy mirrors `.keel/project.yaml`.
- **Idea to borrow**: Backstage's **golden-path template** idea → a `keel init` that scaffolds a sensible default `.keel/project.yaml` per detected stack (Flutter, Python, Node).

**Category 3 takeaway (Assessment)**: This category validates keel's *gate-aggregation* design and offers two clean, ethos-compatible borrows: a **canonical finding schema** (reviewdog) and a **declarative gate/policy surface** (Danger/OPA-concept, not OPA-the-binary).

---

## Category 4 — Multi-agent swarms & orchestration frameworks

| Framework | Model | License | Orchestration model | Git / Merge Awareness | Production Delivery Invariants |
|---|---|---|---|---|---|
| **CrewAI** | role-based crews | MIT | Sequential/Hierarchical role processes | ❌ None (memory only) | ❌ LLM self-reflection only |
| **LangGraph** | stateful agent graphs | OSS | Directed graph w/ conditional edges | ❌ None | ❌ None |
| **AutoGen / Magentic-One** | conversational group chat | OSS | GroupChat / Lead orchestrator | ❌ None | ❌ None |
| **OpenAI Swarm** | lightweight client-side handoffs | MIT | Stateless agent routines + handoffs | ❌ None | ❌ None |
| **MetaGPT / ChatDev** | simulated software company | MIT | SOP-driven conversational roles | ❌ None | ❌ None |
| **Keel Swarm** | **deterministic backbone swarm** | Apache-2.0 | **DAG conflict clustering + git worktree fan-out** | ✅ **Physical worktree isolation** | ✅ **100% test gates + dual-mode batch landing** |

Sources: [gurusup.com/blog/best-multi-agent-frameworks-2026](https://gurusup.com/blog/best-multi-agent-frameworks-2026), [medium.com/.../magentic-one-autogen-langgraph-crewai-or-openai-swarm](https://medium.com/data-science-in-your-pocket/magentic-one-autogen-langgraph-crewai-or-openai-swarm-which-multi-ai-agent-framework-is-best-6629d8bd9509)

**Positioning & Failure Mode Analysis (Assessment)**:
General swarm frameworks operate on unstructured conversational abstractions without git-tree or physical file boundary awareness. In production, unconstrained multi-agent swarms fail due to three core bottlenecks:
1. **Merge Collision Chaos**: Agents concurrently edit overlapping repository files without dependency awareness, producing corrupt git histories and broken builds.
2. **Hallucinated Verification**: Agents declare tasks complete based on conversational LLM self-affirmation rather than deterministic compiler, linter, and unit test gates.
3. **Approval & Evidence Drift**: Reviews performed mid-stream lose validity when underlying commits shift before landing.

**How Keel Swarm Solves This**:
Keel Swarm anchors multi-agent parallelism inside deterministic engineering invariants:
- **Static DAG Dependency Clustering**: Pre-analyzes issue blast radiuses to schedule orthogonal tasks in parallel waves while serializing dependent tasks.
- **Physical Git Worktree Isolation**: Workers develop inside dedicated `.keel/workspaces/swarm-<id>/` sandboxes.
- **Dual-Mode Landing Engine**: Merges 100% disjoint trees via Direct Orthogonal Batch Landing while routing overlapping trees through the atomic `merge_lock` with automated rebase and `s9 fixloop` conflict self-healing.
- **Commit-Bound Evidence & Multi-Vendor Jury**: Every PR carries an immutable, commit-SHA-locked evidence record and cross-vendor panel verdict.
- **Full-Spectrum Observability**: Live terminal ASCII DAG diagrams (`keel swarm-plan --tree`, `keel swarm-status`) paired with `keel-visual` 2D/3D WebGL swarm galaxy scenes.

---

## Positioning statement (Assessment)

**Is keel's combination unique? Largely yes — by combination, not by any single part.**

- Every *individual* capability exists somewhere: issue→PR (Sweep, SWE-agent), AI review (Qodo, CodeRabbit), merge gating + **timezone-aware scheduled freeze** (Mergify — the one tool that genuinely has merge windows), policy gates (OPA/Danger), model/agent-agnosticism (OpenHands).
- **No tool combines all of them**, and three pieces in particular are rare-to-absent in shipped products:
  1. **An end-to-end fixed backbone** from *issue selection* through *merge + close* — agents stop at "opened a PR"; merge queues start at "PR exists." keel owns the whole arc.
  2. **Agent-agnostic adapters** over that backbone (run Claude Code *or* Codex *or* Gemini *or* Antigravity through the identical pipeline). OpenHands is *model*-agnostic; keel is *agent/CLI*-agnostic, which is a different and underserved axis.
  3. **Multi-agent debate→verify→synthesize review as a production gate** — this is research/skill-level elsewhere, not packaged.
- The **merge window + `mkdir` lock as deterministic, stdlib invariants** are not conceptually novel (Mergify schedules; GitHub punts to self-failing Actions), but keel's framing — *native, deterministic, dependency-free, inside the agent pipeline* — is distinctive. The market evidence (GitHub's most-requested-but-absent scheduled-merge feature) confirms the need is real.

**Honest caveat**: keel is *not* a merge queue and shouldn't pretend to be — it lacks (and arguably shouldn't add) batching/bisection/parallel-lane serialization, which is the entire value of Mergify/Trunk/Graphite/bors. keel's "one issue at a time + lock" is a different problem (orchestrated authorship), not a competing one.

---

## Ranked features keel could adopt

| # | Feature (idea) | Inspired by | Why it fits keel | Effort |
|---|---|---|---|---|
| 1 | **Canonical "finding" schema for all gates** (line, severity, message), so build/lint/test/extension gates emit a uniform structure the fix-loop consumes | reviewdog | Pure-core, deterministic, makes the capped fix-loop's input consistent; no new deps | **S** |
| 2 | **Freeze vs. Pause distinction for the merge window** — "freeze" blocks merge but lets gates/CI keep running; "pause" halts everything | Mergify (pause/freeze) | Direct upgrade to the existing window/lock invariants; pure config + clock logic | **S** |
| 3 | **`keel init` golden-path scaffolder** that detects stack and writes a sensible default `.keel/project.yaml` | Backstage software templates; ai-jury's own `jury init` | Reinforces project-agnosticism; lowers onboarding; stdlib templating | **S/M** |
| 4 | **Hotfix bypass** — a flagged emergency issue may skip the merge window (with audit log) | Graphite (out-of-order hotfix), Mergify | Operationally necessary; deterministic given an explicit flag | **S/M** |
| 5 | **Bounded auto-fix-and-push for trivial lint failures** inside the existing fix-loop budget | pre-commit.ci | Reuses the capped-budget mechanism; opt-in to preserve determinism | **M** |
| 6 | **Adaptive stability-based early stopping for debate rounds** (stop when reviewer consensus stabilizes) in ai-jury | MAD-for-LLM-judges research; Star Chamber | Cuts cost without losing rigor; must stay deterministic (fixed seed / KS-test on recorded scores, not wall-clock) | **M** |
| 7 | **Declarative policy/gate surface** in `.keel/project.yaml` (express required gates / risk-tier rules as data, not code) | OPA/Conftest *concept*; Danger Dangerfile | Keeps logic data-driven and testable | **M** |
| 8 | **Reproducible review-quality benchmark** for ai-jury (publish methodology/results) | CodeRabbit's Martian benchmark posture | Credibility for the multi-agent-review claim; pure tooling | **M/L** |

**Ideas I deliberately skip (Assessment):**
- **Adopting OPA/Rego as the policy engine** — violates the stdlib-only, zero-runtime-dependency ethos. Borrow the *concept*, not the binary.
- **Merge-queue batching/bisection/parallel lanes** (bors, Trunk) — keel processes one issue at a time behind a lock; batching is a different problem and would add nondeterminism and complexity for no in-scope benefit.
- **Anti-flake auto-retry of failing checks** (Trunk) — tempting, but blind retries dent determinism and can mask real failures; only acceptable as an explicit, logged, single-retry opt-in per check.
- **Building keel on LangGraph/CrewAI/AutoGen** — clashes with stdlib-only + deterministic + fixed-backbone design; their value is flexibility keel intentionally rejects.

---

## Comparison table

Legend: ✅ yes · ◑ partial/limited · ❌ no · `OSS`/`Prop.`

| Tool | Agent-agnostic | Merge queue | Merge window/freeze | AI review | Multi-agent debate | Policy/gate aggregation | Project config | Open source |
|---|---|---|---|---|---|---|---|---|
| **keel** | ✅ (CLI adapters) | ❌ (one-at-a-time + lock) | ✅ (native, TZ-aware) | ✅ (via ai-jury) | ✅ (review→debate→verify→synth) | ✅ (Lego gates) | ✅ (`.keel/project.yaml`) | OSS (Apache-2.0) |
| **keel-swarm** | ✅ (CLI adapters) | ✅ (Orthogonal Batch + Funnel) | ✅ (native, TZ-aware) | ✅ (via ai-jury) | ✅ (multi-wave consensus) | ✅ (Lego gates) | ✅ (`.keel/project.yaml`) | OSS (Apache-2.0) |
| **Mergify** | ❌ | ✅ | ✅ (schedule + pause/freeze) | ❌ | ❌ | ◑ (conditions) | ◑ (config.yml) | Prop. (OSS repo exists) |
| **GitHub merge queue** | ❌ | ✅ | ❌ (workarounds only) | ❌ | ❌ | ◑ (required checks) | ◑ | Prop. |
| **bors-ng** | ❌ | ✅ (batch+bisect) | ❌ | ❌ | ❌ | ◑ | ◑ | OSS (Apache-2.0, deprecated) |
| **Graphite MQ** | ❌ | ✅ (stack-aware) | ◑ (pause/hotfix) | ❌ | ❌ | ◑ | ◑ | Prop. |
| **Trunk MQ** | ❌ | ✅ (parallel+bisect) | ◑ (pause) | ❌ | ❌ | ◑ | ◑ | Prop. |
| **Qodo / PR-Agent** | ◑ (model-agnostic) | ❌ | ❌ | ✅ | ❌ | ◑ | ◑ | OSS core (Apache-2.0) + Prop. |
| **CodeRabbit** | ❌ | ❌ | ❌ | ✅ | ❌ | ◑ | ◑ | Prop. (free for OSS) |
| **Sweep** | ❌ (own agent) | ❌ | ❌ | ◑ | ❌ | ❌ | ◑ | OSS (Apache-2.0) |
| **Aider** | ❌ (own agent) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | OSS (Apache-2.0) |
| **OpenHands** | ✅ (model-agnostic) | ❌ | ❌ | ◑ | ❌ | ❌ | ◑ | OSS (MIT) |
| **SWE-agent** | ◑ (LM-agnostic) | ❌ | ❌ | ❌ | ❌ | ❌ | ◑ | OSS (MIT) |
| **Danger JS** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ (Dangerfile) | OSS (MIT) |
| **reviewdog** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (normalizer) | ◑ | OSS (MIT) |
| **OPA / Conftest** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (Rego) | ✅ (policies) | OSS (Apache-2.0) |
| **Backstage templates** | ❌ | ❌ | ❌ | ❌ | ❌ | ◑ | ✅ (YAML golden paths) | OSS (Apache-2.0) |
| **CrewAI / LangGraph / AutoGen** | ✅ (general) | ❌ | ❌ | ◑ (buildable) | ◑ (buildable) | ❌ | ❌ | OSS (MIT / OSS) |
| **OpenAI Swarm** | ❌ (OpenAI-only) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | OSS (MIT) |
| **MetaGPT / ChatDev** | ❌ (simulated roles) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | OSS (MIT) |

> Note: ◑ for the general frameworks under "AI review / debate" means *you could build it*, not that it ships. keel's value is that the backbone is *fixed and shipped*, not assemble-it-yourself.

---

## Sources

- Mergify merge queue: https://mergify.com/product/merge-queue
- Mergify queue rules: https://docs.mergify.com/merge-queue/rules/
- Mergify scheduling freezes: https://docs.mergify.com/merge-protections/freeze/
- Mergify pause: https://docs.mergify.com/merge-queue/pause/
- Mergify repo: https://github.com/Mergifyio/mergify
- Mergify origin story of merge queues: https://mergify.com/blog/the-origin-story-of-merge-queues/
- bors-ng repo: https://github.com/bors-ng/bors-ng
- GitHub merge queue docs: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- GitHub merge queue GA: https://github.blog/news-insights/product-news/github-merge-queue-is-generally-available/
- GitHub scheduled-merge discussion: https://github.com/orgs/community/discussions/61147
- "No Out of Hours Merge" Action: https://github.com/marketplace/actions/no-out-of-hours-merge
- "Merge Schedule" Action: https://github.com/marketplace/actions/merge-schedule
- Graphite merge queue: https://www.graphite.com/docs/graphite-merge-queue
- Graphite merge queue tools guide: https://graphite.com/guides/merge-queue-tools-options
- Trunk merge queue: https://trunk.io/merge-queue
- Trunk merge queue product page: https://www.trunk.io/merge
- Trunk OSS/forked PR support: https://trunk.io/changelog/merge-support-for-forked-and-open-source-repos
- GitHub Copilot coding agent issue-to-PR docs: https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/assign-copilot-to-an-issue
- Qodo / PR-Agent launch: https://www.qodo.ai/blog/unveiling-the-future-of-streamlined-software-development/
- Qodo code review overview: https://docs.qodo.ai/code-review
- Qodo configuration docs: https://docs.qodo.ai/install-and-configure/configuration-overview/configuration-file
- PR-Agent community repo: https://github.com/The-PR-Agent/pr-agent
- Qodo hands PR-Agent to community: https://futurumgroup.com/insights/qodo-hands-pr-agent-to-the-community-will-open-governance-accelerate-ai-code-review/
- CodeRabbit: https://www.coderabbit.ai/
- CodeRabbit docs: https://docs.coderabbit.ai/
- CodeRabbit Martian benchmark: https://www.coderabbit.ai/blog/coderabbit-tops-martian-code-review-benchmark
- Cursor Bugbot docs: https://docs.cursor.com/bugbot
- Greptile API introduction: https://greptile.mintlify.dev/docs/api-reference/introduction
- Greptile product page: https://www.greptile.com/
- Sweep repo: https://github.com/sweepai/sweep
- Sweep details: https://aiagentslist.com/agents/sweep-ai
- Aider / CLI agents list: https://github.com/bradAGI/awesome-cli-coding-agents
- OpenHands repo: https://github.com/OpenHands/OpenHands
- OpenHands site: https://www.openhands.dev/
- SWE-agent repo: https://github.com/SWE-agent/SWE-agent
- Danger JS repo: https://github.com/danger/danger-js
- Danger JS site: https://danger.systems/js/
- reviewdog (skill page): https://claude-plugins.dev/skills/@AgentSecOps/SecOpsAgentKit/reviewdog
- OPA in CI/CD: https://www.openpolicyagent.org/docs/cicd
- Conftest repo: https://github.com/open-policy-agent/conftest
- OPA PR approvals sample: https://github.com/Antvirf/open-policy-agent-pr-approvals
- Backstage software templates: https://backstage.io/docs/features/software-templates/
- Multi-agent frameworks 2026: https://gurusup.com/blog/best-multi-agent-frameworks-2026
- CrewAI vs LangGraph vs AutoGen: https://www.datacamp.com/tutorial/crewai-vs-langgraph-vs-autogen
- Magentic-One / framework comparison: https://medium.com/data-science-in-your-pocket/magentic-one-autogen-langgraph-crewai-or-openai-swarm-which-multi-ai-agent-framework-is-best-6629d8bd9509
- MAD for LLM judges (arXiv): https://arxiv.org/html/2510.12697v1
- Star Chamber (multi-LLM consensus): https://blog.mozilla.ai/the-star-chamber-multi-llm-consensus-for-code-quality/
