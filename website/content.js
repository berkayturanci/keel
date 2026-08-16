/* ============================================================
   keel — single source of truth
   Both index.html and docs.html read from window.KEEL.
   Add an entry to KEEL.docs[] and it appears in the docs
   page automatically (sidebar + search + section), and any
   command added to KEEL.commands[] shows up across the site.
   ============================================================ */
window.KEEL = {
  meta: {
    name: "keel",
    glyph: "⚓",
    tagline: "Turn coding agents into work owners.",
    blurb:
      "A project-neutral, multi-agent workflow backbone that drives a GitHub issue from intake to done — projects set values and snap in their own Lego.",
    version: "v1.14.2",
    install: "pip install keel-workflow",
    installAlt: "pip install \"git+https://github.com/berkayturanci/keel@v1.14.2\"",
    pluginAdd: "/plugin marketplace add berkayturanci/keel",
    pluginInstall: "/plugin install keel",
    repo: "https://github.com/berkayturanci/keel",
    pypi: "https://pypi.org/project/keel-workflow/",
    coverageBadge: "https://berkayturanci.github.io/keel/coverage-badge.json",
    python: "Python ≥ 3.11",
    deps: "stdlib-first · 1 runtime dep (PyYAML)",
    license: "Apache-2.0",
    author: "Berkay Turancı",
  },

  /* ---- The fixed backbone: 13 ordered steps (s0–s12) -------------- */
  backbone: [
    { id: "s0",  name: "config",    blurb: "Validate the project config against the bundled schema.", slots: ["after:config"] },
    { id: "s1",  name: "select",    blurb: "Pick the unit of work — a GitHub issue — from the backlog.", slots: ["before:select", "select", "after:select"] },
    { id: "s2",  name: "branch",    blurb: "Cut a work branch off the project's base branch.", slots: ["before:branch", "after:branch"] },
    { id: "s3",  name: "guard",     blurb: "Enforce preflight rules before any code is written.", slots: ["guard"], slot: true, block: true },
    { id: "s4",  name: "implement", blurb: "The coding agent writes the change.", slots: ["before:implement", "after-implement"], agent: true, slot: true },
    { id: "s5",  name: "classify",  blurb: "Classify risk tier from the touched paths.", slots: ["classify", "after:classify"], agent: true },
    { id: "s6",  name: "ci",        blurb: "Wait on the project's CI workflows.", slots: ["before:ci", "after:ci"] },
    { id: "s7",  name: "review",    blurb: "Parallel reviewers — plus the opt-in <a href='https://github.com/berkayturanci/ai-jury' target='_blank' rel='noopener'>jury</a> gate.", slots: ["reviewers", "after:review"], agent: true, slot: true },
    { id: "s8",  name: "test",      blurb: "Run the build / lint / test gates.", slots: ["tester", "test", "after:test"], slot: true, block: true },
    { id: "s9",  name: "fixloop",   blurb: "Bounded rounds of fixes until gates pass.", slots: ["before:fixloop", "fixloop", "after:fixloop"] },
    { id: "s10", name: "merge",     blurb: "Merge — inside the window, behind the lock, after pre-merge gates.", slots: ["pre-merge", "after:merge"], slot: true, block: true },
    { id: "s11", name: "capture",   blurb: "Write reports and capture markers post-merge — the compound-learning contract: sanitized, fail-soft, deduped by fingerprint.", slots: ["capture", "post-merge"], slot: true },
    { id: "s12", name: "close",     blurb: "Post the closeout and retire the issue.", slots: ["before:close", "on-close", "after:close"] },
  ],

  invariants: [
    ["merge lock", "A mkdir lock means only one merge runs at a time."],
    ["night no-merge window", "Timezone-aware window keeps merges out of risky hours."],
    ["fail-soft", "Optional gates that error become no-ops, never hard blocks."],
    ["orchestrator-only writes", "Only the orchestrator mutates git / PRs; agents propose."],
    ["vendor + model attribution", "Every action records which agent and model produced it."],
  ],

  /* ---- 17 shipped commands. featured: true → animated showcase --- */
  commands: [
    {
      slug: "ship", name: "/keel:ship", group: "Flagship", flagship: true, featured: true, scene: "ship",
      cmd: "keel:ship",
      one: "Drive a GitHub issue end-to-end through the whole backbone.",
      detail:
        "Select → branch → implement → CI → review → test → merge → close → capture. The full flow: per-round review, inline file:line comments, --delegate / --review-delegate (incl. hosted-API anthropic-api:MODEL / openai-api:MODEL / google-api:MODEL — no agent CLI, just an API key; plus generic OpenAI-compatible and CLI profiles), --reviewers N, the <a href='https://github.com/berkayturanci/ai-jury' target='_blank' rel='noopener'>ai-jury</a> gate, the timezone-aware merge window + mkdir merge lock, and vendor+model attribution. <b>--compound</b> selects the compound-engineering profile — same backbone and safety primitives, with implement / review / fixloop / capture (s4·s7·s9·s11) as compound step overrides.",
    },
    {
      slug: "swarm", name: "/keel:swarm", group: "Flagship", flagship: true, featured: true, scene: "swarm",
      cmd: "keel:swarm",
      one: "Multi-agent swarm coordinator — cluster backlog issues, run parallel waves, and batch land.",
      detail:
        "Clusters backlog issues into disjoint execution waves based on static file-overlap and explicit DAG dependencies. Spawns parallel workers across isolated git worktrees (.keel/worktrees/swarm/), supports cross-model agent routing (Claude, Gemini, Codex, DeepSeek, Local Ollama), unifies reviews under the AI Jury consensus panel, and executes dual-mode batch landing under the merge lock with self-healing conflict rollback.",
    },
    {
      slug: "implement", name: "/keel:implement", group: "Per-step", featured: true, scene: "implement",
      one: "Delegate one issue to the right implementer — the s4 step, standalone.",
      detail:
        "Drives the s4 implement step on its own: picks the correct implementer agent for the issue and produces the change, without running the rest of the backbone.",
    },
    {
      slug: "review-cycle", name: "/keel:review-cycle", group: "Per-step", featured: true, scene: "review",
      one: "Multi-reviewer review → fix cycle over open PRs. Does not merge.",
      detail:
        "Parallel reviewers, structured findings, inline-vs-summary posting, capped fix rounds (s7 + s9) over one or more open PRs. It reviews and fixes but never merges.",
    },
    {
      slug: "pr-loop", name: "/keel:pr-loop", group: "Per-step", featured: true, scene: "prloop",
      one: "Iterate a PR's comments + CI to green, then hand off to the windowed merge.",
      detail:
        "Works an open PR's review comments and CI until checks are green and reviewers are satisfied, then hands off to the windowed merge (s6–s12).",
    },
    {
      slug: "review-all-day", name: "/keel:review-all-day", group: "Review & triage", featured: true, scene: "sweep",
      one: "Time-window diff-review sweep — one issue per serious finding.",
      detail:
        "Scans every commit in a merge-window-aligned span, classifies each diff via parallel reviewers, and opens one issue per serious finding. Read-only w.r.t. git/PRs; the only state change is issue creation.",
    },
    {
      slug: "regression", name: "/keel:regression", group: "Review & triage", featured: true, scene: "regression",
      one: "Codebase-wide regression scan via parallel subagents → fix issues → ship.",
      detail:
        "Fans out per-area review subagents in parallel, dedupes against existing issues, opens fix issues for high-confidence findings, and hands each to /keel:ship.",
    },
    {
      slug: "triage", name: "/keel:triage", group: "Review & triage", featured: true, scene: "triage",
      one: "Auto-classify unlabelled issues via a classifier subagent.",
      detail:
        "Applies role / priority / status labels from the existing label set to open issues missing a status label; risk-tier comes from tier3_globs.",
    },
    {
      slug: "morning", name: "/keel:morning", group: "Daily rhythm", featured: true, scene: "report",
      one: "Daily morning briefing and a ranked focus list.",
      detail:
        "Cross-session deferrals, shipped-since-last-brief, production / health signals, GitHub status, and a ranked focus list for the day.",
    },
    {
      slug: "work-block", name: "/keel:work-block", group: "Daily rhythm", featured: true, scene: "queue",
      one: "Daytime multi-issue work block — a queue of issues through ship, with stop points.",
      detail:
        "Processes explicit issue numbers or a queue selector through /keel:ship, with per-issue worktrees, readiness refresh between items, operator-visible stop points, progress snapshots (keel status), and a final bucketed report.",
    },
    {
      slug: "overnight", name: "/keel:overnight", group: "Daily rhythm", featured: true, scene: "queue",
      one: "Unattended overnight work block, keyed on the merge window.",
      detail:
        "Time-aware merge mode keyed on the merge window; runs /keel:ship over the queue until the window closes, then writes a session / morning report.",
    },
    {
      slug: "wrap", name: "/keel:wrap", group: "Daily rhythm", featured: true, scene: "report",
      one: "Finish a session — gates, commit, push, open a PR, record a recap.",
      detail:
        "Runs the configured gates, commits, pushes, opens a PR, and records a session recap to close out the current work session.",
    },
    {
      slug: "ci-check", name: "/keel:ci-check", group: "Audits", featured: true, scene: "cicheck",
      one: "Check the latest CI run; on failure diagnose root cause + propose one fix.",
      detail:
        "Checks the latest CI run's status; on failure, locates the failing job/step, diagnoses the root cause, and proposes exactly one fix — never auto-applies it.",
    },
    {
      slug: "coverage", name: "/keel:coverage", group: "Audits", featured: true, scene: "coverage",
      one: "Per-PR coverage delta (base → head); flag low-coverage × high-risk hot spots.",
      detail:
        "Computes and posts the per-PR test-coverage delta, flags low-coverage × high-risk hot spots, and opens issues to close gaps — routed to /keel:ship.",
    },
    {
      slug: "deps-audit", name: "/keel:deps-audit", group: "Audits", featured: true, scene: "audit",
      one: "Dependency security + licence audit; route fixes to ship.",
      detail:
        "On-demand security + licence audit across the project's ecosystems; classifies security vs. routine, appends findings to today's tracking issue, and routes fixes to /keel:ship.",
    },
    {
      slug: "flake-audit", name: "/keel:flake-audit", group: "Audits", featured: true, scene: "audit",
      one: "Detect intermittently-failing tests; one tracking issue per new flake.",
      detail:
        "Detects intermittently-failing tests from recent CI history (or repeated local runs); dedupes against tracked flakes and opens one tracking issue per newly-detected flake.",
    },
    {
      slug: "stale-prs", name: "/keel:stale-prs", group: "Audits", featured: true, scene: "audit",
      one: "Find quiet / drifted PRs; triage, comment, optionally rebase.",
      detail:
        "Finds open PRs that have gone quiet or drifted off the base branch; triages, comments, and optionally rebases — respecting the merge window.",
    },
  ],

  /* ---- example invocation per command (illustrative) ------------- */
  cmdExample: {
    "ship": "/keel:ship --issue 128 --reviewers 3",
    "swarm": "/keel:swarm 714 715 716 717 --rebalance",
    "implement": "/keel:implement --issue 128",
    "review-cycle": "/keel:review-cycle --pr 214 --comments inline",
    "pr-loop": "/keel:pr-loop --pr 214",
    "review-all-day": "/keel:review-all-day --since 07:00",
    "regression": "/keel:regression --route-to-ship",
    "triage": "/keel:triage --apply",
    "morning": "/keel:morning",
    "work-block": "/keel:work-block --issues 128,131,140",
    "overnight": "/keel:overnight --until 01:30",
    "wrap": "/keel:wrap",
    "ci-check": "/keel:ci-check --propose-fix",
    "coverage": "/keel:coverage --pr 214",
    "deps-audit": "/keel:deps-audit --security-only",
    "flake-audit": "/keel:flake-audit",
    "stale-prs": "/keel:stale-prs --days 14",
  },
  cmdNote: "Examples are illustrative — each command is project-neutral and reads its values from .keel/project.yaml. See the CLI reference for the full flag set.",

  /* ---- CLI (the deterministic core) ------------------------------ */
  cli: [
    ["keel setup --root .", "one-command onboarding: init + install-adapter + validate + plan"],
    ["keel init [--wizard]", "scaffold .keel/project.yaml (detects the stack)"],
    ["keel validate <cfg>", "validate a config (and its extensions) against the schema"],
    ["keel plan <cfg> [--live --json]", "render the backbone + the full structured command contract; --live runs the s0 consent preflight"],
    ["keel swarm-plan <cfg> [issues...]", "cluster backlog issues into disjoint execution waves and compute batch vs funnel landing plan"],
    ["keel swarm-status <cfg>", "real-time multi-cluster execution snapshot, worker health, and DAG progress"],
    ["keel swarm-run <cfg>", "orchestrate parallel workers in isolated worktrees with dynamic rebalancing"],
    ["keel swarm-land <cfg>", "dual-mode batch landing under merge lock with self-healing conflict rollback"],
    ["keel-visual swarm", "live 2D DAG graph and 3D spatial worktree topology dashboard"],
    ["keel run-gates <cfg>", "run the project's build / lint / command gates"],
    ["keel window <cfg>", "is the merge window open right now?"],
    ["keel ship <cfg>", "full dry assessment: tier, window, gates, decision"],
    ["keel merge <cfg> --pr N", "fail-closed core-owned merge: lock → window re-check → CI rollup → evidence → gh merge"],
    ["keel evidence-verify <cfg> --pr N", "verify the PR carries the required public evidence (closure markers, reviewer verdicts, jury)"],
    ["keel status [--json]", "progress snapshot for long work blocks (checkpoint + run ledger)"],
    ["keel checkpoint / keel resume", "write the safe resume point at step boundaries; render a dry-run resume plan after interruption"],
    ["keel ledger <cfg> [--limit N]", "read the structured run ledger offline (with capture health)"],
    ["keel capture-verify --merged-pr N", "assert exactly one valid capture marker per merged PR"],
    ["keel claim / keel release", "single-host resource claims — the mkdir lock primitive keel merge uses"],
    ["keel post-comment --artifact …", "the sanctioned write path for evidence artifacts — marker-validated, idempotent per run-id"],
    ["keel runcontrols <events>", "deterministic work caps: run budget, per-slot caps, oscillation detection — hard halts fail closed"],
    ["keel capabilities <cfg>", "check runtime requirements + selected GitHub transport"],
    ["keel project-commands <cfg>", "list project-owned commands declared in policy_pack"],
    ["keel install-adapter <claude|skills|all>", "install the /keel:<command> workflows"],
    ["keel sync", "safely refresh generated adapters (dry-run friendly)"],
    ["keel version", "print the installed core version"],
  ],

  /* ---- The three layers (1 → 3) ---------------------------------- */
  layers: [
    { n: "Layer 1", k: "BACKBONE", t: "keel-core — the fixed step machine", d: "The fixed ordered step machine + invariants. Changing it is a keel-core change; projects never touch it.", tone: "core" },
    { n: "Layer 2", k: "CONFIG", t: "project.yaml — per-project values", d: "Branch, build command, globs, agents, timezone, merge window, capabilities, health sources.", tone: "blue" },
    { n: "Layer 3", k: "EXTENSIONS", t: "project-owned Lego pieces", d: "Add-only into named slots. Snap gates / steps into hooks like guard, tester, pre-merge.", tone: "brass" },
  ],

  /* ---- Config field reference ------------------------------------ */
  config: [
    ["core", "Pinned keel-core version range (e.g. ^0.6) — installed, never copied."],
    ["base_branch", "The branch work is cut from and merged back into."],
    ["timezone · merge_window", "Timezone-aware HH:MM–HH:MM window; merges only happen inside it."],
    ["gates", "Built-in build / lint / test gates run at the s8 test step."],
    ["knobs", "Runnable commands, risk globs, docs paths, CI workflow mapping, local agent roles, runtime capabilities."],
    ["extensions", "Add-only hooks — your Lego — snapped into named backbone slots."],
    ["policy_pack", "Durable project data: labels, lifecycle, risk rules, test groups, docs policy, health providers, review rubric."],
  ],

  /* ---- Comparison (categories, not products — point-in-time) ----- */
  compareCols: ["keel", "Merge queue", "PR-review bot", "Coding agent"],
  compareRows: [
    ["One fixed, ordered backbone — never forked", "y", "p", "n", "n"],
    ["Configured by values, not copied command bodies", "y", "y", "p", "p"],
    ["Add-only extension slots (your Lego)", "y", "p", "n", "n"],
    ["Same flow across every coding agent", "y", "n", "n", "n"],
    ["Risk-tier → reviewer count", "y", "n", "p", "n"],
    ["Timezone merge window + merge lock", "y", "y", "n", "n"],
    ["Opt-in multi-agent review (jury) gate", "y", "n", "p", "p"],
    ["Vendor + model attribution on every action", "y", "n", "n", "n"],
    ["Drives an issue end-to-end, not one stage", "y", "n", "n", "p"],
    ["Built-in run visualization (2D / 3D)", "y", "n", "n", "n"],
    ["Dependency footprint", "stdlib-first", "hosted", "hosted", "hosted"],
  ],
  compareNote: "A point-in-time map of categories — Coding agents (OpenHands, SWE-agent, Devin) stop at a PR · PR reviewers (CodeRabbit, Qodo, Greptile) review an existing PR · Merge queues (GitHub Merge Queue, Mergify, Graphite) serialize tested PRs. keel is the work-ownership backbone that drives the whole lifecycle and can use all three. Verify against current docs before deciding.",

  /* ---- FAQ -------------------------------------------------------- */
  faq: [
    ["Do projects ever fork the backbone?", "No — that's the whole point. The ordered step machine and its invariants live in keel-core, which is installed and pinned, never copied. Projects only ever touch Layer 2 (values in project.yaml) and Layer 3 (add-only extensions). Changing the backbone is a keel-core change."],
    ["What does a command actually read?", "Every command is project-neutral. It never hardcodes a branch, build/lint command, agent, glob, timezone or window — it references the knob by name and asks the keel CLI for the value, so the same /keel:ship behaves differently in each repo purely from that repo's .keel/project.yaml."],
    ["What's the jury gate?", "An opt-in review gate that runs the <a href='https://github.com/berkayturanci/ai-jury' target='_blank' rel='noopener'>ai-jury</a> multi-agent reviewer on the diff when it's installed, and is a fail-soft no-op otherwise. It snaps into the s7 review step."],
    ["Does keel use itself?", "Yes. keel drives itself: its config is .keel/project.yaml (Python, make test + make lint gates) and CI runs keel on keel-core on every push. If a gate fails, keel blocks its own merge — the same backbone every consumer gets."],
    ["What do I need installed?", "Python 3.11+ and PyYAML — that's the one runtime dependency (on Windows, also tzdata for the timezone database). It runs on Linux, macOS, and Windows. The pure core is stdlib-first. Adapters install the /keel:&lt;command&gt; workflows into the surfaces your agents already read."],
  ],

  /* ---- Coverage (pure core held at 100% line + branch) ----------- */
  coverage: {
    overall: { line: 100, branch: 100, statements: 1840, missing: 0, branches: 612, partial: 0 },
    gate: "fail_under = 100",
    modules: [
      ["src/keel/config.py", 100, 100, 214],
      ["src/keel/model.py", 100, 100, 268],
      ["src/keel/extensions.py", 100, 100, 196],
      ["src/keel/findings.py", 100, 100, 142],
      ["src/keel/gates.py", 100, 100, 233],
      ["src/keel/orchestrator.py", 100, 100, 411],
      ["src/keel/cli.py", 100, 100, 376],
    ],
  },

  /* ============================================================
     DOCS — data-driven. Each entry becomes:
       • a sidebar link (grouped by `group`)
       • a searchable record
       • a rendered <article> section
     Add one object here → it appears in docs.html automatically.
     `body` is HTML. `source` links the canonical repo doc.
     ============================================================ */
  docs: [
    {
      group: "Start here", title: "What keel is", slug: "what-keel-is",
      summary: "A project-neutral, multi-agent workflow core: one fixed backbone, projects set values and snap in Lego.",
      body:
        "<p><b>keel</b> closes the <b>vision-to-production gap</b> in agentic AI. While most coding agents stop at opening a PR, keel is a project-neutral, multi-agent <b>workflow backbone</b> that drives a unit of work from backlog to production: branch → implement → CI → review → test → merge → close → capture. Projects never fork the backbone; they set per-project <b>values</b> in <code>project.yaml</code> and snap their own <b>Lego pieces</b> into named extension slots.</p>" +
        "<p>The keel is a ship's backbone — the fixed spine every project builds on. The flagship command is <code>/keel:ship</code>; keel is where ships are built.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/README.md",
    },
    {
      group: "Start here", title: "Three layers", slug: "three-layers",
      summary: "Layer 1 backbone (fixed), Layer 2 config (values), Layer 3 extensions (add-only Lego).",
      body:
        "<p>keel separates what's fixed from what's yours across three layers. Changing the backbone is a keel-core change; projects only ever touch layers 2 and 3.</p>" +
        "<table class='doc-tbl'><thead><tr><th>Layer</th><th>Name</th><th>What it is</th></tr></thead><tbody>" +
        "<tr><td>Layer 3</td><td>EXTENSIONS</td><td>project-owned Lego pieces, <b>add-only</b> into named slots</td></tr>" +
        "<tr><td>Layer 2</td><td>CONFIG</td><td><code>project.yaml</code> — per-project values (branch, build cmd, globs, agents…)</td></tr>" +
        "<tr><td>Layer 1</td><td>BACKBONE</td><td>keel-core — fixed ordered step machine + invariants</td></tr>" +
        "</tbody></table>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/proposals/keel-architecture.md",
    },
    {
      group: "Start here", title: "Install", slug: "install",
      summary: "pip install keel-workflow (Python ≥3.11, Linux/macOS/Windows, one runtime dep: PyYAML), or pin a git tag.",
      body:
        "<p>keel is a Python (≥3.11) package for Linux, macOS, and Windows, with one runtime dependency (PyYAML; on Windows it also installs <code>tzdata</code> for the timezone database).</p>" +
        "<pre class='doc-pre' tabindex='0' role='region' aria-label='Install commands'><code>pip install keel-workflow                                       <span class='cm'># from PyPI (provides the `keel` command)</span>\npip install \"git+https://github.com/berkayturanci/keel@v1.14.2\"  <span class='cm'># or pin an existing git tag</span></code></pre>" +
        "<p>In a cloud agent session, install it from a <code>SessionStart</code> hook (or add keel to the session's repo scope) so the selected core ref is available before a run.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/README.md",
    },
    {
      group: "Start here", title: "Quickstart", slug: "quickstart",
      summary: "validate, plan, then dry-run ship to see tier, window, gates and the decision.",
      body:
        "<pre class='doc-pre' tabindex='0' role='region' aria-label='Quickstart CLI commands'><code>keel validate projects/example-flutter.yaml   <span class='cm'># validate a config against the schema</span>\nkeel plan     projects/example-flutter.yaml   <span class='cm'># show the backbone plan for a project</span>\nkeel version</code></pre>" +
        "<p><code>keel plan</code> renders the fixed backbone with each project's gates / extensions slotted in — exactly what a dry-run executes.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/README.md",
    },
    {
      group: "Reference", title: "The backbone", slug: "the-backbone",
      summary: "13 ordered steps s0–s12, 28 add-only extension slots, the blocking gates, and the preserved invariants.",
      body:
        "<p>The backbone is a fixed, ordered step machine. <b>Every</b> step exposes add-only extension slots — <b>28 named slots</b> across the 13 steps — where projects snap in their own gates and steps. Slots can extend the machine but never reorder or remove it. <b>Primary slots</b> (<code>guard</code>, <code>after-implement</code>, <code>reviewers</code>, <code>tester</code> / <code>test</code>, <code>pre-merge</code>, <code>capture</code>, <code>post-merge</code>) can own or shape a step’s outcome; the <code>before:</code> / <code>after:</code> hooks are passive observation points. Three of the primary slots can <b>block</b> the run: <code>guard</code> (s3), <code>tester</code> / <code>test</code> (s8) and <code>pre-merge</code> (s10). These invariants are always preserved: merge lock, night no-merge window, fail-soft, orchestrator-only-writes, evidence-bearing steps, vendor+model attribution.</p>",
      render: "backbone",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/commands.md",
    },
    {
      group: "Reference", title: "Commands", slug: "commands",
      summary: "All 16 /keel:&lt;command&gt; agentic workflows, each project-neutral, grouped by purpose.",
      body:
        "<p>keel ships <b>16</b> agentic workflow commands with the package. Install them with <code>keel install-adapter claude</code> (native Claude slash commands), <code>skills</code> (one shared skill set every other agent reads), or <code>all</code>. The <code>keel</code> CLI does the deterministic work; these commands are the agentic flows.</p>" +
        "<p>Every command step is an <b>evidence-bearing contract</b>: a generated adapter must complete the step, record the requested evidence, or explicitly mark it <code>N/A — &lt;reason&gt;</code>. Public side effects (PR bodies, review summaries, verdicts, issues, reports) must go through the selected transport — chat-only notes don't count.</p>",
      render: "commands",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/commands.md",
    },
    {
      group: "Reference", title: "CLI", slug: "cli",
      summary: "The deterministic core — from setup/validate/plan to the fail-closed merge, evidence verification, checkpoint/resume and run controls.",
      body:
        "<p>The <code>keel</code> CLI does the deterministic work — config resolution, gate planning / execution, risk-tier classification, merge window + lock, the core-owned <code>keel merge</code> pipeline, evidence verification, checkpoint / resume, run controls and attribution. The adapters layer the agentic flows on top.</p>" +
        "<p>Live runs are consent-gated: <code>--live</code> renders an enforceable preflight, <code>--approve-scope</code> grants scopes (<code>filesystem</code>, <code>git</code>, <code>github</code>, …) and <code>--consent-mode explicit|standing|agent</code> controls how approvals are sourced. The default contract is always the dry-run contract.</p>",
      render: "cli",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/cli.md",
    },
    {
      group: "Reference", title: "Parameter reference", slug: "parameter-reference",
      summary: "The exhaustive per-flag reference: every flag's type, default, the contract fields it changes, precedence rules, and exit codes.",
      body:
        "<p>One level deeper than the CLI quick reference: every flag's type and allowed values, its default, the exact contract fields and gates it changes, precedence and interaction rules (e.g. <code>--no-jury</code> &gt; <code>--jury</code> &gt; tier-3 auto-on), and what makes a command exit non-zero. Covers the shared flag families — <code>--root</code>, <code>--json</code>, <code>--live</code> / <code>--dry-run</code>, the consent flags, issue-intake flags, and the review / jury family — plus every subcommand from <code>keel setup</code> to <code>keel resume</code>. Every claim is grounded in <code>src/keel/cli.py</code> and the pure modules it calls.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/parameter-reference.md",
    },
    {
      group: "Reference", title: "Configuration", slug: "configuration",
      summary: "project.yaml fields: core, base_branch, timezone/merge_window, gates, knobs, extensions, policy_pack.",
      body:
        "<p>A keel consumer is configured with <b>values, not copied command bodies</b>. Top-level fields choose the core version, repository, base branch, timezone, merge window, built-in gates, extension directory and add-only hooks. <code>knobs</code> declares runnable commands, risk globs, docs paths, CI workflow mapping, local agent roles and runtime capabilities. <code>policy_pack</code> is durable project-owned data. Unknown keys are rejected by the bundled schema, so the reference is intentionally strict.</p>",
      render: "config",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/configuration.md",
    },
    {
      group: "Architecture", title: "Evidence chain & auditability", slug: "evidence",
      summary: "How keel guarantees commit-SHA-bound review provenance, model attribution, and auditable exceptions.",
      body:
        "<p>Every PR merged through Keel carries an unbroken, tamper-evident record of reviewer verdicts, test results, and agent attribution. Approvals are cryptographically locked to the exact <code>HEAD_SHA</code> commit to prevent approval drift across subsequent pushes, with fully audited exception tracking via <code>--deferral</code>.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/evidence.md",
    },
    {
      group: "Architecture", title: "Supported AI models & providers", slug: "models",
      summary: "How to use any AI model: hosted APIs (Claude, OpenAI, Gemini), OpenAI-compatible gateways (OpenRouter, Groq, DeepSeek), local Ollama/vLLM, and agent CLIs.",
      body:
        "<p>Keel is model-neutral. Drive implementations (<code>s4</code>) or reviews (<code>s7</code>) using direct hosted APIs (<code>anthropic-api:</code>, <code>openai-api:</code>, <code>google-api:</code>), OpenAI-compatible profiles for OpenRouter, DeepSeek, Groq, Together AI and local vLLM, local offline Ollama models, or official agent CLIs (Claude, Codex, Antigravity).</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/models.md",
    },
    {
      group: "Architecture", title: "Keel Swarm (Multi-Agent Concurrency & Cross-Model Topology)", slug: "swarm",
      summary: "High-concurrency multi-agent orchestration — static DAG clustering, isolated git worktrees, cross-model routing, and dual-mode batch landing.",
      body:
        "<p><b>Keel Swarm</b> is Keel's high-concurrency multi-agent orchestration subsystem. While <code>/keel:ship</code> drives a single issue linearly, <code>/keel:swarm</code> clusters a list or backlog of issues into disjoint execution waves and executes them across isolated git worktrees in parallel.</p>" +
        "<h3>1. Static Dependency DAG & Wave Partitioning</h3>" +
        "<p>Swarm computes file-overlap conflict graphs and explicit issue dependencies (<code>blocks #N</code> / <code>depends on #N</code>) without executing code. Orthogonal clusters are scheduled in parallel in <b>Wave 1</b>, while dependent or overlapping clusters are sequenced into subsequent waves (<code>Wave 2</code>, <code>Wave 3</code>).</p>" +
        "<h3>2. Cross-Model Routing & Unified AI Jury Panel</h3>" +
        "<p>Different clusters can be assigned to different models and agent vendors concurrently via <code>knobs.implementer_agents</code> and <code>knobs.delegate_profiles</code>:</p>" +
        "<ul>" +
        "<li><b>Core / Architecture</b>: Claude 3.7 Sonnet / Claude Code (<code>claude</code>)</li>" +
        "<li><b>Frontend / Visual</b>: Google Gemini 2.5 Flash / Antigravity (<code>agy</code> / <code>google-api:</code>)</li>" +
        "<li><b>Documentation / Scripts</b>: OpenAI GPT-4o / Codex (<code>codex</code> / <code>openai-api:</code>)</li>" +
        "<li><b>Local / Offline Worktrees</b>: Local Ollama / vLLM (<code>ollama:qwen2.5-coder</code>)</li>" +
        "</ul>" +
        "<p>Regardless of which model authored the PR, every cluster goes through the cross-vendor <b>AI Jury</b> panel (Anthropic + OpenAI + Google) for unanimous review consensus before merging.</p>" +
        "<h3>3. Dual-Mode Landing</h3>" +
        "<p>Swarm supports two landing strategies under the single-writer <code>merge_lock</code>:</p>" +
        "<ul>" +
        "<li><b>Direct Orthogonal Batch Landing</b>: Disjoint branches with zero file collisions are squashed directly into the base branch without intermediate rebases.</li>" +
        "<li><b>Adaptive Atomic Funnel</b>: Overlapping clusters trigger a self-healing git rebase onto the newly merged base branch, re-running gates before completing the merge. Conflicts trigger an immediate safe rollback (<code>git rebase --abort</code>).</li>" +
        "</ul>" +
        "<h3>4. Live 2D & 3D Spatial Dashboard</h3>" +
        "<p><code>keel-visual swarm</code> renders real-time interactive DAG topological graphs and 3D spatial node networks in the terminal or browser, tracking worker progress, cluster state, and landing queues live.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/swarm.md",
    },
    {
      group: "Architecture", title: "Security & policy presets", slug: "security",
      summary: "Declarative security presets (Bandit, Gitleaks, Semgrep, Trivy), ReDoS-safe redaction, and concurrent gates.",
      body:
        "<p>Keel provides built-in declarative security presets (<code>policy_pack.presets: ['bandit', 'gitleaks', 'semgrep', 'trivy']</code>) that automatically slot static analysis and secret scanning into the backbone. Combined with ReDoS-resilient capture redaction, least-privilege CI actions, and concurrent gate execution (<code>--concurrency</code>), projects achieve robust security auditing with zero custom scripts.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/configuration.md#policy_packpresets",
    },
    {
      group: "Authoring", title: "Extensions (your Lego)", slug: "extensions",
      summary: "Add-only gates and steps snapped into named hooks — guard, tester, pre-merge, reviewers…",
      body:
        "<p>Extensions are how a project customises behaviour without forking the backbone. They are <b>add-only</b>: you snap gates and steps into named hooks (<code>guard</code>, <code>tester</code>, <code>pre-merge</code>, <code>reviewers</code>, …) and keep labels, path policy, health sources, local commands and workflow preferences in <code>policy_pack</code> data instead of packaged command prose.</p>" +
        "<p>Because hooks are add-only, an extension can add behaviour to a step but never remove or reorder the step itself — the invariants hold for every consumer.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/extensions.md",
    },
    {
      group: "Authoring", title: "Consumer neutrality", slug: "consumer-neutrality",
      summary: "The boundary between keel core, project policy, project commands, capabilities and adapters.",
      body:
        "<p>A command never hardcodes a branch, build/lint command, agent, glob, timezone, window or workflow name. It references the knob by name and asks the <code>keel</code> CLI for the value, so the same <code>/keel:ship</code> produces different behaviour in different repos purely from each repo's <code>.keel/project.yaml</code>.</p>" +
        "<p>Project-specific gates live in <code>.keel/extensions/</code> (Lego); project-only commands (local build checks, smoke tests, project-specific regressions) stay in the project and are exposed as data through <code>keel project-commands</code>.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/consumer-neutrality.md",
    },
    {
      group: "Reference", title: "Command contracts", slug: "command-contracts",
      summary: "Structured JSON plan/result contracts adapters consume — keel plan --json exposes each step's contract.",
      body:
        "<p>Adapters don't parse prose: <code>keel plan --json</code> exposes a structured contract per step (gates, hooks, evidence requirements, the capture marker), and command runs report structured results back. This is what keeps generated adapters honest \u2014 a step is completed, evidenced, or explicitly marked <code>N/A — &lt;reason&gt;</code>.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/command-contracts.md",
    },
    {
      group: "Reference", title: "Runtime capabilities", slug: "runtime-capabilities",
      summary: "Commands declare what they need (git, gh, network, agents); keel detects what the runtime actually has.",
      body:
        "<p>Each command declares its runtime requirements; <code>keel capabilities</code> detects what's actually available in this session (git, <code>gh</code>, network, delegated agents, hosted-API keys via <code>api-token</code>) and the run degrades fail-soft or stops with a clear reason instead of half-running. <code>capability-unavailable</code> is a closed, auditable skip reason.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/runtime-capabilities.md",
    },
    {
      group: "Reference", title: "GitHub transport", slug: "github-transport",
      summary: "How keel selects a GitHub transport (gh CLI, API, …) and normalizes operations across them.",
      body:
        "<p>Issue, PR, review and comment operations go through a selected <b>transport</b> with normalized capabilities \u2014 so the same command works whether the session has the <code>gh</code> CLI, direct API access, or a restricted runner. Public side effects must go through the transport; chat-only notes never satisfy a step.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/github-transport.md",
    },
    {
      group: "Operating", title: "Operator consent", slug: "operator-consent",
      summary: "Live-run consent scopes and modes: what an operator must approve, and how scopes apply to delegated agents.",
      body:
        "<p>Live runs are governed by explicit operator consent scopes \u2014 which mutations (branch, PR, merge, issue writes) the operator has approved, and how those scopes carry over to delegated agents. Three sourcing modes: <code>explicit</code> (only <code>--approve-scope</code> flags count), <code>standing</code> (env / config standing approvals, each requiring a recorded operator), and <code>agent</code> (delegated to the host agent's own permission system). The evidence gate arms from ship provenance by default; only the operator-applied <code>keel:evidence-waived</code> label disarms it \u2014 and a gate that never armed blocks rather than reporting a pass having checked nothing. Its requirements are split by phase, so the merge gate asks for the review/jury evidence that exists at s10, not the closure comments s11 writes after it.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/operator-consent.md",
    },
    {
      group: "Operating", title: "Parity matrix", slug: "parity-matrix",
      summary: "Legacy-to-keel command parity status, with the owning issue for each remaining gap.",
      body:
        "<p>The staged migration from copied legacy command bodies to <code>/keel:&lt;command&gt;</code> workflows is tracked in a parity matrix \u2014 each legacy behaviour maps to its keel equivalent and any remaining gap has an owning issue. Pair it with the <a href='https://github.com/berkayturanci/keel/blob/main/docs/keel/cutover.md'>cutover guide</a> to retire copies without losing anything.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/parity-matrix.md",
    },
    {
      group: "Operating", title: "Release runbook", slug: "release",
      summary: "PyPI/TestPyPI release steps and the package smoke test maintainers run before tagging.",
      body:
        "<p>Releases follow a runbook: version bump, changelog, TestPyPI \u2192 PyPI, and <code>python scripts/release_smoke.py</code> \u2014 a packaged smoke test that must pass before tagging or announcing. <code>pip install keel-workflow</code> provides the <code>keel</code> command.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/release.md",
    },
    {
      group: "Operating", title: "Dogfooding", slug: "dogfooding",
      summary: "keel drives itself; CI runs keel on keel-core on every push and blocks its own merge on a failing gate.",
      body:
        "<p>keel drives <b>itself</b>. Its config is <code>.keel/project.yaml</code> (Python, <code>make test</code> + <code>make lint</code> gates) and CI runs keel on keel-core on every push.</p>" +
        "<pre class='doc-pre' tabindex='0' role='region' aria-label='Dogfooding CLI commands'><code>keel plan      .keel/project.yaml          <span class='cm'># render keel's own backbone</span>\nkeel run-gates .keel/project.yaml --root . <span class='cm'># keel runs its own test + lint gates</span>\nkeel ship      .keel/project.yaml --root . <span class='cm'># full dry assessment</span>\n<span class='cm'>#   risk tier     : TIER-3  → 3 reviewer(s)</span>\n<span class='cm'>#   decision      : MERGE — clear to merge</span></code></pre>" +
        "<p>If a step's gate fails, keel blocks its own merge — the same backbone every consumer gets.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/README.md",
    },
    {
      group: "Operating", title: "Run on the free runner", slug: "github-actions",
      summary: "The keel-ship GitHub Action runs keel against every PR on GitHub's free hosted runner.",
      body:
        "<p>The <code>keel-ship</code> GitHub Action runs keel against every PR on GitHub's free hosted runner — using the runner's own <code>git</code> + <code>gh</code> — and posts the assessment as a comment. This very site and its coverage report are built and deployed the same way.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/github-actions.md",
    },
    {
      group: "Operating", title: "Security audits", slug: "security-audits",
      summary: "Published security audit reports — scope, verdicts, and follow-up status for each release line.",
      body:
        "<p>keel ships with recurring security audits, published in the repository under <code>docs/security/</code>. Each report covers source-level vulnerability classes (command injection, path traversal, unsafe deserialization, secret leakage), data-flow from untrusted GitHub inputs to dangerous sinks, dependency exposure (<code>pip-audit</code>), static analysis (<code>bandit</code>), and GitHub Actions / repository-settings review.</p>" +
        "<h3>2026-08-15 — Swarm Milestone 12 Line</h3>" +
        "<p><b>Zero critical, high, or medium findings (40/40 tests passed).</b> Comprehensive security and fuzzing audit of multi-agent swarm orchestration, worktree filesystem isolation, single-writer atomic mutex under heavy load, command injection defenses across subprocess runners, and ReDoS-resilient redaction. <a href='https://github.com/berkayturanci/keel/blob/main/docs/security/2026-08-15-security-audit.md'>full report →</a></p>" +
        "<h3>2026-06-11 — v1.2.1 line</h3>" +
        "<p><b>No critical, high, or medium-severity finding.</b> The core remains deterministic, uses <code>yaml.safe_load</code> exclusively, keeps all <code>git</code>/<code>gh</code> calls on argv wrappers (no shell), enforces path containment for checkpoint/ledger/worktree paths, and redacts capture artifacts before durability. Both follow-ups from the previous audit are confirmed resolved: secret scanning + push protection enabled, and required PR approvals enforced by branch protection. <a href='https://github.com/berkayturanci/keel/blob/main/docs/security/2026-06-11-security-audit.md'>full report →</a></p>" +
        "<h3>2026-06-09 — v1.0.1 line</h3>" +
        "<p>No critical or high-severity finding; follow-ups raised on GitHub secret scanning and consumer-neutrality (both since resolved). <a href='https://github.com/berkayturanci/keel/blob/main/docs/security/2026-06-09-security-audit.md'>full report →</a></p>" +
        "<h3>2026-06-08 — initial audit</h3>" +
        "<p>Trust-boundary review, <code>bandit</code> + <code>pip-audit</code> passes, workflow-injection and token-permission review. No critical or high-severity finding; the command-gate trust boundary (gates execute your configured shell commands) is documented as the single accepted risk — review a config before running it on a sensitive repository, exactly as you would a Makefile. <a href='https://github.com/berkayturanci/keel/blob/main/docs/security/2026-06-08-security-audit.md'>full report →</a></p>" +
        "<p>Private vulnerability reporting: do not open a public issue — use GitHub's private \u201cReport a vulnerability\u201d advisory, or email the maintainer. See <a href='https://github.com/berkayturanci/keel/blob/main/SECURITY.md'>SECURITY.md</a>.</p>",
      source: "https://github.com/berkayturanci/keel/tree/main/docs/security",
    },
    {
      group: "Operating", title: "Coverage", slug: "coverage-doc",
      summary: "The pure core is held at 100% line + branch coverage; the gate is fail_under = 100 in CI.",
      body:
        "<p>The pure core (<code>config</code>, <code>model</code>, <code>extensions</code>, <code>findings</code>, <code>gates</code>, <code>orchestrator</code>, <code>cli</code>) is held at <b>100% line + branch coverage</b>; the coverage gate (<code>fail_under = 100</code>) runs in CI. See the <a href='coverage.html'>live coverage report →</a></p>",
      source: "https://github.com/berkayturanci/keel/blob/main/README.md",
    },
    {
      group: "Start here", title: "One-command setup", slug: "setup",
      summary: "keel setup creates/reuses .keel/project.yaml, installs the adapter surfaces, validates, and renders the plan — in one command.",
      body:
        "<p><code>keel setup --root .</code> wraps first-run onboarding for a consumer project: it scaffolds (or reuses) <code>.keel/project.yaml</code>, installs the generated Claude + shared-skill adapter surfaces, strictly validates the config, and renders the backbone plan — all in one command. Existing configs are preserved unless <code>--force</code>.</p>" +
        "<p><code>keel sync</code> is the safe refresh: it updates only marker-protected generated adapter files, never your config, extensions, policy docs, or project-owned commands — and is dry-run friendly.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/onboarding.md",
    },
    {
      group: "Start here", title: "Claude Code plugin", slug: "plugin",
      summary: "Add keel's /keel:&lt;command&gt; workflows in Claude Code with no pip install — this repo is its own plugin marketplace.",
      body:
        "<p>The same <code>/keel:&lt;command&gt;</code> flows are packaged as a <b>Claude Code plugin</b>, so you can add them to a session without <code>pip install</code> — straight from this repo's built-in marketplace:</p>" +
        "<pre class='doc-pre' tabindex='0' role='region' aria-label='Claude Code plugin commands'><code>/plugin marketplace add berkayturanci/keel   <span class='cm'># register the keel marketplace</span>\n/plugin install keel                          <span class='cm'># install → /keel:ship, /keel:regression, …</span></code></pre>" +
        "<p>The plugin ships the <b>same</b> project-neutral command bodies as <code>keel install-adapter</code> — they read every value from <code>.keel/project.yaml</code>, so a project still needs <code>keel setup</code> for the flows to act. The two distribution paths are additive.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/plugin.md",
    },
    {
      group: "Start here", title: "Vision — work ownership", slug: "vision",
      summary: "keel makes an agent accountable for the whole path a strong teammate owns — readiness, branch, implement, gates, review, safe merge, closeout.",
      body:
        "<p>keel is an <b>agentic work-ownership backbone</b>. Its job is not to be another isolated coding command, review bot, or merge queue — it is to make an agent <b>accountable for the whole path</b> a strong software teammate would normally own.</p>" +
        "<p>That path starts before code is written: read the issue, decide whether the scope is ready, ask for clarification when it is not, cut an isolated branch, implement, keep CI and tests green, get reviewed, fix feedback, merge inside policy, close the loop, and record what should be remembered next time.</p>" +
        "<p><b>v1 — one-agent work ownership.</b> Hand keel one issue (or a bounded work block) and get the same quality loop every time: readiness before mutation, isolated worktree, deterministic gates + capability checks, independent review and optional jury, merge-window + merge-lock safety, structured ledger, closeout + capture hooks, and morning/wrap visibility. The point isn't autonomy for its own sake — it's work that is observable, recoverable, reviewable, and governed by policy while the agent owns the execution details.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/vision.md",
    },
    {
      group: "Operating", title: "Capture & learning", slug: "capture-learning",
      summary: "Post-merge capture has a stable marker contract: sanitized by default, fail-soft, deduped by fingerprint, verifiable offline.",
      body:
        "<p>The <code>s11 capture</code> step owns a stable marker contract — <code>compound-learning: pr=&lt;N&gt; status=&lt;applied|deferred|skipped:reason&gt;</code> — exposed in <code>keel plan --json</code>. The allowed skip reasons are closed, capture is <b>fail-soft</b> after a successful merge, and <code>keel capture-verify</code> checks the run ledger offline at session end.</p>" +
        "<p>Capture artifacts are <b>sanitized by default</b> before they become durable: generic secret redaction plus project-owned <code>policy_pack.capture_redaction.deny_patterns</code>, storing only an audit of rule ids and counts. Durable learning is optional — policy can choose <code>create-learning</code>, <code>marker-only</code>, or <code>defer</code> — and duplicate candidates are suppressed by stable fingerprints so routine merges don't flood the learning surface.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/docs/keel/commands.md",
    },
    {
      group: "Visualize", title: "keel-visual", slug: "keel-visual",
      summary: "An optional companion that renders any run from the ledger keel already writes \u2014 a 2D flow and an animated 3D scene, in the terminal or the browser.",
      body:
        "<p><a href='https://github.com/berkayturanci/keel/tree/main/keel-visual' target='_blank' rel='noopener'>keel-visual</a> is an optional companion package, published on <a href='https://pypi.org/project/keel-visual/' target='_blank' rel='noopener'>PyPI</a>. It reads the run-ledger and checkpoint keel <b>already writes</b> \u2014 no extra wiring \u2014 and draws the run as a <b>2D flow</b> or an animated <b>3D scene</b>. It ships its own colour language: green = done / merged, cyan = the active step, amber = a gate or the jury, dim = not yet reached.</p>" +
        "<pre class='doc-pre' tabindex='0' role='region' aria-label='Install keel-visual'><code>pipx install keel-visual                 <span class='cm'># pulls in keel-workflow (core) automatically</span>\nkeel-visual play   .keel/project.yaml --follow   <span class='cm'># live in the terminal</span>\nkeel-visual render .keel/project.yaml --pr 42 --out run.html  <span class='cm'># the web 2D/3D page</span></code></pre>" +
        "<p>Terminal <code>play</code> animates the run as it happens (<code>--loop</code> for a demo, <code>--follow</code> for live); <code>dash</code> is a board of every active worktree; <code>render</code> is the self-contained web page below; and <code>serve</code> is a <b>live localhost web dashboard</b> (polls every ~0.5s) — open it on the side and click a run for a per-run detail drawer with a <b>2D / 3D switch</b> (a live scene with <code>curve · helix · ring · line · plexus · aurora · comet</code> styles, drag-orbit + scroll/pinch zoom, theme-aware).</p>" +
        "<p>It composes with the <b>cross-vendor jury</b>: <code>--follow</code> shows the jury status live, and <code>--theater</code> hands the screen to <a href='https://github.com/berkayturanci/ai-jury' target='_blank' rel='noopener'>ai-jury</a>'s deliberation theater at the review step (a flat ANSI scene by default, or a pixel-art room via <code>theater_style = \"pixel\"</code> in <code>jury.toml</code>), then resumes from the live checkpoint. Fail-soft and dependency-free — keel-visual never imports ai-jury, and if the <code>jury</code> CLI is absent the jury simply doesn't animate and nothing errors.</p>" +
        "<p><b>How to watch.</b> keel-visual is a <b>separate observer</b> — it only reads the ledger + checkpoint keel writes, never the run's path — so it works the same whether you launch <code>keel ship</code> by hand, from an agent (Claude Code), or in CI. The live animations need a real terminal (tty), so they don't render <i>inside</i> captured agent/CI output (they degrade gracefully — no colour, no theater). When an agent drives the run you watch it <b>alongside</b>: run <code>keel-visual play --follow</code> (or <code>dash</code>) in your own terminal pointed at the same repo, or open a <code>render</code> HTML page in a browser (no tty needed).</p>" +
        "<p>A single <code>dash</code>/<code>render</code> watches <b>one repo</b> (its worktrees via <code>git worktree list</code>); to span projects, <code>dash --all</code> (terminal) and <code>render --all</code> (web) point at a parent folder and aggregate every keel project one level under it into a single board, grouped by project — local-only and fail-soft. The web board carries a <b>2D grid / 3D scene</b> toggle (one lane per run in the 3D view), follows the system <b>light/dark</b> theme, and offers an <b>all / active</b> filter that fades finished runs and last-sorts them — or hides them outright — so live work stays in focus. It surfaces <code>ship</code> runs <b>and non-ship commands</b> (triage, morning, pr-loop …) live via the additive <code>keel activity</code> channel, each rendered with its own phases. As of <b>keel 1.6.4</b> the deterministic backbone commands (<code>keel plan</code> at Step 0, <code>keel run-gates</code> at s8, <code>keel merge</code> at s10) auto-stamp that channel, so a run shows up <b>and advances</b> even when the agent skips the per-phase <code>keel activity</code> calls. Since <b>1.6.5</b> a real merge stamps the run <b>merged</b> (green) rather than a soft <code>done</code>, and a run whose id ends in its issue/PR (<code>ship-585</code>) is labelled <code>#585</code> even with no explicit issue. The hard limit is the <b>filesystem</b>: keel-visual sees a run only where its records are written — a run on this machine (your <code>keel ship</code> or a local agent) is visible, but one executing on a different machine (a remote/cloud session) writes its records there, not here.</p>" +
        "<figure class='doc-fig'><img src='assets/visual/board.png' alt='live keel-visual serve dashboard: four ship runs across two projects, with merged, done and running badges and issue-numbered labels' loading='lazy'><figcaption>The live <code>serve --all</code> board — a real merge reads green <b>merged</b> (<code>#585</code>), a closed-out run muted <b>done</b> (<code>#601</code>), live runs cyan; every card is labelled by the issue/PR its run-id encodes, even when no explicit issue was passed.</figcaption></figure>",
      source: "https://github.com/berkayturanci/keel/blob/main/keel-visual/README.md",
    },
    {
      group: "Visualize", title: "Ship in 2D & 3D", slug: "visual-ship",
      summary: "The flagship ship run drawn two ways \u2014 the 2D flow with the cross-vendor jury on review, and a 3D scene with five selectable styles.",
      body:
        "<p>The <b>2D flow</b> walks s0\u2013s12 with a playhead. Gates glow amber, the jury surfaces on the review step, and reaching merge turns the whole run green.</p>" +
        "<figure class='doc-fig'><img src='assets/visual/ship-2d-review.png' alt='ship 2D flow on the review step with the gating jury' loading='lazy'><figcaption>s7 review \u2014 three jurors deliberating toward a gating verdict.</figcaption></figure>" +
        "<figure class='doc-fig'><img src='assets/visual/ship-2d-merged.png' alt='ship 2D flow, merged and all green' loading='lazy'><figcaption>Merged \u2014 every step green.</figcaption></figure>" +
        "<p>The <b>3D scene</b> offers a style selector (top-left). All styles share the same run semantics and differ only in how the run is drawn \u2014 pick one in the page or with <code>?mode=3d&amp;s3d=&lt;style&gt;</code>:</p>" +
        "<div class='vz-styles'><figure><img src='assets/visual/ship-3d-plexus.png' alt='ship 3D plexus style' loading='lazy'><figcaption><b>plexus</b> — interweaving web of drifting points + fading links — the default</figcaption></figure><figure><img src='assets/visual/ship-3d-comet.png' alt='ship 3D comet style' loading='lazy'><figcaption><b>comet</b> — three interweaving particle streams with fading tails</figcaption></figure><figure><img src='assets/visual/ship-3d-aurora.png' alt='ship 3D aurora style' loading='lazy'><figcaption><b>aurora</b> — interweaving translucent ribbons with a soft fade</figcaption></figure><figure><img src='assets/visual/ship-3d-combined.png' alt='ship 3D combined style' loading='lazy'><figcaption><b>combined</b> — the plexus web and the comet streams together</figcaption></figure><figure><img src='assets/visual/ship-3d-line.png' alt='ship 3D line style' loading='lazy'><figcaption><b>line</b> — the original flowing-light ribbon — a single tube</figcaption></figure></div>" +
        "<p>Each style honours the same colours; switching never changes what a colour means, only the geometry it is painted on. Unknown <code>s3d</code> values fall back to <code>plexus</code>.</p>",
      source: "https://github.com/berkayturanci/keel/blob/main/keel-visual/README.md",
    },
    {
      group: "Visualize", title: "Every command", slug: "visual-commands",
      summary: "All 16 /keel:<command> workflows render their own flow \u2014 keel-visual reads each command's phases from keel.flows, not just ship.",
      body:
        "<p>keel-visual is not ship-only. Every one of the <b>16</b> commands has its own ordered phases in <code>keel.flows</code>, and the visualizer draws each one \u2014 in the terminal (<code>keel-visual play --command &lt;name&gt;</code>) or the web page. Below, each command at a mid-run frame:</p>" +
        "<div class='vz-grid'><figure class='vz-card'><img src='assets/visual/cmd-ship.png' alt='/keel:ship rendered in keel-visual' loading='lazy'><figcaption>/keel:ship</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-implement.png' alt='/keel:implement rendered in keel-visual' loading='lazy'><figcaption>/keel:implement</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-review-cycle.png' alt='/keel:review-cycle rendered in keel-visual' loading='lazy'><figcaption>/keel:review-cycle</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-pr-loop.png' alt='/keel:pr-loop rendered in keel-visual' loading='lazy'><figcaption>/keel:pr-loop</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-review-all-day.png' alt='/keel:review-all-day rendered in keel-visual' loading='lazy'><figcaption>/keel:review-all-day</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-regression.png' alt='/keel:regression rendered in keel-visual' loading='lazy'><figcaption>/keel:regression</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-triage.png' alt='/keel:triage rendered in keel-visual' loading='lazy'><figcaption>/keel:triage</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-morning.png' alt='/keel:morning rendered in keel-visual' loading='lazy'><figcaption>/keel:morning</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-work-block.png' alt='/keel:work-block rendered in keel-visual' loading='lazy'><figcaption>/keel:work-block</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-overnight.png' alt='/keel:overnight rendered in keel-visual' loading='lazy'><figcaption>/keel:overnight</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-wrap.png' alt='/keel:wrap rendered in keel-visual' loading='lazy'><figcaption>/keel:wrap</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-ci-check.png' alt='/keel:ci-check rendered in keel-visual' loading='lazy'><figcaption>/keel:ci-check</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-coverage.png' alt='/keel:coverage rendered in keel-visual' loading='lazy'><figcaption>/keel:coverage</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-deps-audit.png' alt='/keel:deps-audit rendered in keel-visual' loading='lazy'><figcaption>/keel:deps-audit</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-flake-audit.png' alt='/keel:flake-audit rendered in keel-visual' loading='lazy'><figcaption>/keel:flake-audit</figcaption></figure><figure class='vz-card'><img src='assets/visual/cmd-stale-prs.png' alt='/keel:stale-prs rendered in keel-visual' loading='lazy'><figcaption>/keel:stale-prs</figcaption></figure></div>",
      source: "https://github.com/berkayturanci/keel/blob/main/keel-visual/README.md",
    },
  ],
};
