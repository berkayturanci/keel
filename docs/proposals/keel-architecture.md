# keel — architecture & extension contract

> Status: **proposal** (design tour for example-android#2035, Phase 2.5).
> Canonical home for the workflow core's design. The example-android#2035 issue is the
> cross-repo **coordination** point and links here; this doc is the **spec**.
>
> Convergence note: two independent design passes (the ai-infra/keel side and the
> example-android/#2035 side) reached the same conclusion — the roadmap captured
> *fixed backbone + config values* but not *project-added steps*. The missing pillar is a
> first-class **extension-point (Lego) model**. This doc specifies it.

## 0. What keel is

**keel** is a project-neutral, multi-agent **workflow engine** that drives a unit of work
(a GitHub issue) from backlog to done — branch → implement → CI → review → test → merge →
close — through a **fixed backbone** of steps. Projects don't fork the backbone; they
**snap their own Lego pieces into named slots** and set per-project values via config.

- Name metaphor: the **keel** is a ship's backbone — the fixed spine every project builds
  on. The flagship command is `/ship`; keel is where ships are built.
- Siblings: modelled on **ai-jury** (vendor-neutral core + thin per-vendor adapters,
  installable, OSS-ready). ai-jury is itself both a *component* keel calls (the review
  jury is a built-in gate) and a potential *consumer* (its own dev can run on keel).

### The two kinds of project-specificity (why config alone is not enough)

| Kind | Mechanism | Example |
|---|---|---|
| **Value** (a knob) | `project.yaml` config | `base_branch: main`, `build_gate_cmd: flutter test`, `timezone` |
| **Step** (a Lego piece) | **extension into a named slot** | example-flutter adds a *design-parity* test + a *no-merge-without-design-parity* gate |

The original #2035 roadmap modelled only the first. keel's core addition is the second.

## 1. Three layers

```
┌─ Layer 3: EXTENSIONS (project-owned Lego) ── snap pieces into named backbone slots; ADD-ONLY
├─ Layer 2: CONFIG (project.yaml knobs) ────── per-project VALUES (branch, build cmd, globs, agents…)
└─ Layer 1: BACKBONE (keel-core, central) ──── fixed ordered step machine + deterministic plumbing
                                               + agent-neutral dispatch + invariants + tests
```

Changing **Layer 1** = a change in the **central keel repo** (your rule: the Lego flow is
fixed; if it must change, the core changes). Projects only ever touch Layers 2–3.

### Layer 1 — Backbone (vendor-neutral core CLI)

Per the locked decision, the backbone ships as a **vendor-neutral, testable package**
(ai-jury-style): deterministic plumbing as a CLI/lib, agentic steps dispatched to a
configured agent. "Orchestrator owns the round structure; adapters are thin."

**Ordered steps (stable IDs = the contract).** Every step exposes extension hooks; the
canonical hook table is maintained in [`docs/keel/extensions.md`](../keel/extensions.md).

| # | Step | Primary hooks | Notes |
|---|---|---|---|
| s0 | `config` | `after:config` | load `project.yaml`, resolve flags/wizard |
| s1 | `select` | `before:select`, `select`, `after:select` | pick issue from queue |
| s2 | `branch` | `before:branch`, `after:branch` | worktree off `base_branch` (knob) |
| s3 | `guard` | `guard` | blocker / precondition checks |
| s4 | `implement` | `before:implement`, `after-implement` | host-agent or delegate writes code |
| s5 | `classify` | `classify`, `after:classify` | risk tier + reviewer count |
| s6 | `ci` | `before:ci`, `after:ci` | poll `ci_workflows` (knob) |
| s7 | `review` | `reviewers`, `after:review` | parallel reviewer dimensions → findings |
| s8 | `test` | `tester`, `test`, `after:test` | run the project's test/gate set |
| s9 | `fixloop` | `before:fixloop`, `fixloop`, `after:fixloop` | apply fixes, cap rounds |
| s10 | `merge` | `pre-merge`, `after:merge` | lock + window + merge |
| s11 | `capture` | `capture`, `post-merge` | post-merge capture |
| s12 | `close` | `before:close`, `on-close`, `after:close` | close issue lifecycle |

**Invariants the backbone always preserves** (non-overridable by any extension): merge
lock (`MERGE_GATE_ONLY`), the night no-merge window gate, fail-soft (a soft failure never
aborts the run), orchestrator-only-writes, and the #2036 attribution contract.

**keel-core CLI surface** (deterministic ⇒ golden/snapshot-testable — satisfies the test
phase):

```
keel config show              # resolved project.yaml + flags + loaded extensions
keel next-issue               # pick from queue
keel branch <issue>           # worktree off base_branch
keel open-pr ...
keel ci-wait <pr>             # poll ci_workflows
keel run-step <slot> <pr>     # run a slot's extensions (agent-neutral dispatch)
keel run-gates <pr>           # iterate tester + pre-merge gates → normalised findings JSON
keel merge <pr> --window-check
keel attribute <pr> ...       # labels / vendor+model (#2036)
```

The **agentic** steps (implement / review / classify) stay as prompts the adapter
dispatches; everything **deterministic** is a `keel …` subcommand (testable, reproducible).

### Layer 2 — Config (`project.yaml`) — values only

The knobs enumerated in #2035, now with a home:

```yaml
extends: keel
core_version: "^1.0"          # pinned central core; no file-copy

base_branch: main
timezone: Europe/Istanbul
merge_window: "07:00-01:30"   # night no-merge window is the complement

knobs:
  implementer_agents: { mobile: flutter-developer, backend: supabase-developer }
  build_gate_cmd: "cd apps/mobile && flutter test"
  lint_cmd: "flutter analyze"
  tier3_globs: ["supabase/migrations/**", "apps/mobile/lib/**/*.dart"]
  ci_workflows: { "Flutter CI": "apps/mobile/**", "Supabase CI": "supabase/**" }
  docs_gate_paths: ["docs/**", "*.md"]
  sot_doc: AGENTS.md
```

### Layer 3 — Extensions (Lego pieces) — ADD-ONLY into named slots

```yaml
extensions_dir: .keel/extensions
extensions:
  guard: [preflight.md]
  after-implement: []
  reviewers:  [a11y-review.md]            # add a reviewer dimension
  tester:     [design-parity.md]          # example-flutter: design-equality test suite
  pre-merge:  [design-parity-gate.md]     # no merge unless design-parity passes
  post-merge: []
```

**Hard rules (the contract):**

1. **Add-only.** An extension may only *add* a piece into a *named slot*. It can **not**
   delete, reorder, or replace a backbone step. (Turning a built-in gate on/off is a
   **knob**, not an extension.) This is what keeps the backbone fixed across all consumers.
2. **Fail-soft inherited.** A broken/erroring extension can never break the backbone — it
   degrades to a logged no-op — **unless** it explicitly declares itself a hard gate
   (`on_fail: block`, only valid in `guard`, `tester`, `test`, or `pre-merge`).
3. **Agent-neutral.** Each extension declares which agent runs it; default = inherit the
   step's agent (host-agent/delegate). So `tester:design-parity` can run on `qwen` while
   review runs on `claude`, all on the same backbone.

**Extension mini-spec** (one agent-neutral file per piece, in `extensions_dir`):

```yaml
# .keel/extensions/design-parity.md (frontmatter + body)
id: design-parity
slot: tester
kind: agentic            # or: command
mode: agentic            # deterministic | agentic | hybrid
agent: inherit           # or claude | codex | agy | ollama:<model>
on_fail: suggest         # warn | suggest | block(blocking hooks only)
anchorable: true         # may post inline on the diff (ties into ship #2039 inline mode)
# --- body: the prompt or command the piece runs ---
run: "flutter test --tags golden"
```

`keel run-gates` runs blocking-capable command hooks (`guard`, `tester`, `test`,
`pre-merge`) through this uniform contract and **normalises results into the same findings
shape** ship already uses (severity → gate decision). The cross-vendor **jury** remains a
built-in gate under the same contract.

> **example-flutter worked example.** "Design-equality test" = one `tester` piece
> (`design-parity.md`, runs golden/screenshot diff) + one `pre-merge` gate
> (`design-parity-gate.md`, `on_fail: block`). The backbone is untouched; example-flutter gets
> its own step. example-android, with no such file, simply has empty slots there.

## 2. Agent-neutral distribution

- **Backbone = vendor-neutral package** (pip primary, ai-jury ethos). The round structure
  lives here once.
- **Adapters = thin, per-agent** — each just invokes `keel` and supplies the agentic
  sub-prompts: a **Claude** skill/slash-command, a **Codex** prompt, a **Gemini** `SKILL.md`,
  an **agy** entry. (~20 lines each, like ai-jury vendors.) Projects already carry
  `.claude/ .gemini/ .codex/ .agents/` trees, so multi-agent is an existing reality the
  core now owns.
- **Consumer holds only:** `project.yaml` + `.keel/extensions/*`. The core is **installed
  + pinned**, never copied ⇒ the overwrite/drift class of bug is structurally gone.
- **OSS-ready** (like ai-jury); versioned skill/plugin in a marketplace later.

## 3. Revised roadmap (Phase 2.5 inserted)

| Phase | What | Owner |
|---|---|---|
| 1 | Cross-repo audit ✅ | done |
| 2 | **Import** Tier-A/B bodies to SI tip (final seed); drop Tier-C + retire `sync-to-ai-infra` ✅ | keel (PR #5) |
| **2.5** | **Extension-point model** — backbone slot IDs, extension contract, config↔extension binding (**this doc**) | keel |
| 3 | Config schema + JSON-Schema validator — incl. the `extensions:` block; seed SI + example-flutter | keel |
| 4 | **keel-core** skeleton CLI + step-runner + extension loader (+ tests) | keel |
| 5 | `ship` POC: render as a thin adapter over keel-core; SI golden-diff identical | keel |
| 6 | De-contaminate example-flutter → Flutter/Supabase + its first Lego (design-parity) | example-flutter |
| 7 | Tests in keel CI — config-injection snapshots + **extension-injection** (inject a test-extension; assert the backbone still runs + invariants hold) + dry-run smoke; publish gate | keel |
| 8 | Adapters (Codex/Gemini/agy) + cutover: pinned install; retire file-copy sync | keel → projects |

**Direction note:** the flow now originates centrally (keel → projects via pinned
install). There is no upstream project→core sync; `sync-to-ai-infra` is retired (PR #5).
PR #5 is the final seed from example-android.

## 4. Open items

- **Repo rename:** `ai-infra` → **`keel`** (operator action on GitHub). Package/CLI = `keel`.
- Config schema (`project.schema.json`) — Phase 3, must validate `knobs` + `extensions`.
- Exact built-in gate set vs. extensible gates (which gates ship in core vs. live as
  project pieces) — settle in Phase 4.
