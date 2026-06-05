# keel commands (`/keel:<command>`)

keel ships **16** agentic workflow commands with the package. Install them with
[`keel install-adapter`](cli.md#keel-install-adapter-target---root-dir---force):

```bash
keel install-adapter claude   # native Claude slash commands → /keel:<command>
keel install-adapter skills   # one shared skill set under .agents/skills/keel-<command>/
keel install-adapter all      # both surfaces
```

The `keel` CLI does the deterministic work (config resolution, gate planning/execution,
risk-tier classification, merge window + lock, attribution); these commands are the **agentic**
flows the host agent runs (per-round review, inline comments, delegation). Every command is
**project-neutral** — it reads each project value (`base_branch`, `build_gate_cmd`, `lint_cmd`,
`implementer_agents`, `tier3_globs`, `ci_workflows`, `timezone`, `merge_window`, …) from that
project's `.keel/project.yaml`.

## Flagship

| command | what it does |
|---|---|
| **`/keel:ship`** | Drive a GitHub issue end-to-end through the keel backbone (select → branch → implement → CI → review → test → merge → close → capture). The full flow: per-round review, inline `file:line` comments, `--delegate` / `--review-delegate`, `--review-comments inline\|summary`, `--reviewers N`, the `jury` gate, the timezone-aware merge window + `mkdir` merge lock, and vendor+model attribution. |
| `/keel:ship-v2` | Compound-engineering flavour of the ship workflow — **folded into `/keel:ship`**; its deltas are expressed as keel extensions, not a separate backbone. This command points at `/keel:ship`. |

## Per-step (standalone slices of the backbone)

| command | what it does |
|---|---|
| `/keel:implement` | Delegate a single issue to the right implementer and drive the **s4 implement** step standalone. |
| `/keel:review-cycle` | Multi-reviewer **review → fix** cycle over one or more open PRs — parallel reviewers, structured findings, inline-vs-summary posting, capped fix rounds (s7 + s9). Does **not** merge. |
| `/keel:pr-loop` | Iterate on an open PR's review comments + CI until checks are green and reviewers are satisfied, then hand off to the windowed merge (s6–s12). |

## Review & triage

| command | what it does |
|---|---|
| `/keel:review-all-day` | Time-window diff-review sweep — scan every commit in a merge-window-aligned span, classify each diff via parallel reviewers, and open one issue per serious finding. Read-only w.r.t. git/PRs; the only state change is issue creation. |
| `/keel:regression` | Codebase-wide regression scan — fan out per-area review subagents in parallel, dedupe against existing issues, open fix issues for high-confidence findings, and hand each to `/keel:ship`. |
| `/keel:triage` | Auto-classify open issues missing a status label via a classifier subagent — applies role/priority/status labels from the existing label set; risk-tier from `tier3_globs`. |

## Daily rhythm

| command | what it does |
|---|---|
| `/keel:morning` | Daily morning briefing — cross-session deferrals, shipped-since-last-brief, production/health signals, GitHub status, and a ranked focus list. |
| `/keel:overnight` | Unattended overnight work block — time-aware merge mode keyed on the merge window; runs `/keel:ship` over the queue until the window closes, then writes a session/morning report. |
| `/keel:wrap` | Finish the current work session — run the configured gates, commit, push, open a PR, and record a session recap. |

## Audits

| command | what it does |
|---|---|
| `/keel:ci-check` | Check the latest CI run's status; on failure, locate the failing job/step, diagnose the root cause, and propose **one** fix — never auto-apply. |
| `/keel:coverage` | Compute and post the per-PR test-coverage delta (base → head), flag low-coverage × high-risk hot spots, and open issues to close gaps — routed to `/keel:ship`. |
| `/keel:deps-audit` | On-demand dependency **security + licence** audit across the project's ecosystems; classify security vs. routine, append findings to today's tracking issue, route fixes to `/keel:ship`. |
| `/keel:flake-audit` | Detect intermittently-failing tests from recent CI history (or repeated local runs); dedupe against tracked flakes and open one tracking issue per newly-detected flake. |
| `/keel:stale-prs` | Find open PRs that have gone quiet or drifted off the base branch; triage, comment, and optionally rebase — respecting the merge window. |

## How a command stays project-neutral

A command never hardcodes a branch, build/lint command, agent, glob, timezone, window, or
workflow name. It references the knob by name and asks the `keel` CLI for the value, so the same
`/keel:ship` produces Flutter behaviour in one repo and Android behaviour in another purely from
each repo's `.keel/project.yaml`. Project-specific *gates* live in `.keel/extensions/` (Lego);
project-only commands (platform builds, device tests, app-specific regressions) stay in the
project. See [`configuration.md`](configuration.md) and [`extensions.md`](extensions.md).
