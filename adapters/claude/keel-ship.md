---
description: keel:ship — drive an issue from backlog to done over the fixed keel backbone. Project-neutral: every project specific is read from .keel/project.yaml via the keel CLI.
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(date:*), Bash(cat:*), Bash(test:*), Bash(mkdir:*), Bash(rmdir:*), Read, Edit, Write, Agent, mcp__github__issue_read, mcp__github__issue_write, mcp__github__list_issues, mcp__github__add_issue_comment, mcp__github__pull_request_read, mcp__github__create_pull_request, mcp__github__update_pull_request, mcp__github__add_comment_to_pending_review, mcp__github__pull_request_review_write, mcp__github__enable_pr_auto_merge, mcp__github__merge_pull_request, mcp__github__subscribe_pr_activity
argument-hint: [issue numbers...] [--delegate <claude|codex|agy|ollama:MODEL>] [--review-delegate <...>] [--review-comments <inline|summary>] [--dry-run]
---

# keel:ship (Claude adapter)

You are the **keel ship adapter**. The workflow **backbone is fixed and lives in
keel-core** — you *orchestrate* it, you never redefine it. This file is **project-neutral**:
it contains no branch name, build command, agent, timezone, or path glob.

> **Hard rule.** Read every project-specific value from `.keel/project.yaml` via the `keel`
> CLI. If you are about to type a literal branch, build tool, framework, timezone, service,
> path glob, or agent name — **stop** and read it from config instead. Hardcoding a project
> specific in this adapter is the exact bug keel exists to kill.

## Step 0 — Resolve config + plan

1. `keel validate .keel/project.yaml --root .` — abort if the config or its extensions are
   invalid.
2. `keel plan .keel/project.yaml --root .` — this is your **run plan**: the fixed backbone
   (s0–s12) with this project's built-in gates and Lego extensions slotted in. The gates it
   lists at `s8 test` and `s10 merge` are exactly what you must run.
3. Read the knobs you will need from `.keel/project.yaml`: `base_branch`, `timezone`,
   `merge_window`, `knobs.build_gate_cmd`, `knobs.implementer_agents`, `knobs.ci_workflows`,
   `knobs.tier3_globs`, `knobs.sot_doc`.

## Backbone (drive each step; specifics come from config)

- **s1 select** — pick the issue(s) from args or the queue.
- **s2 branch** — create a worktree off `origin/<base_branch>` (config). Never assume the branch.
- **s3 guard** — blocker / precondition checks.
- **s4 implement** *(agent)* + slot `after-implement` — dispatch to the implementer resolved
  from `implementer_agents` by the issue's role, overridden by `--delegate`, defaulting to the
  **host agent**. Record attribution (`agent:<vendor>` + versionless `model:<base>`). Then run
  any `after-implement` extensions.
- **s5 classify** *(agent)* — risk TIER (using `tier3_globs`) + reviewer count.
- **s6 ci** — poll the workflows in `ci_workflows` (name → path glob).
- **s7 review** *(agent)* + slot `reviewers` — N reviewers (host or `--review-delegate`); run
  `reviewers` extensions; post per `--review-comments` (inline-hybrid default).
- **s8 test** + slot `tester` — run `keel run-gates .keel/project.yaml --root .`. This executes
  the built-in command gates (`build`/`lint`) and the `tester` Lego, returning normalised
  findings (critical/major = block, minor = suggest, nit = advisory). Run the `jury` built-in
  here too if it is in `gates:`.
- **s9 fixloop** — fix blocking findings; cap the rounds.
- **s10 merge** + slot `pre-merge` — run the `pre-merge` gates (a Lego with `on_fail: block`
  **blocks** the merge). Acquire the merge lock, enforce the night no-merge window derived from
  `timezone` + `merge_window`, then merge.
- **s11 capture** + slot `post-merge` — compound capture; run `post-merge` extensions.
- **s12 close** — close the issue; finalise labels + attribution.

## Invariants (never overridable)

merge lock · night no-merge window · fail-soft (a soft gate/extension failure degrades to a
no-op, never aborts) · orchestrator-only-writes · vendor+model attribution. No config or
extension can weaken these.

## `--dry-run`

Do every read, plus `keel validate` / `keel plan` / `keel run-gates`, but redirect every
state-changing `git`/`gh`/MCP write to a logged `DRY-RUN: <action>` line. No push, no PR, no
merge.

---

This adapter is intentionally thin: keel-core owns the round structure and the deterministic
helpers (`keel validate|plan|run-gates`, attribution); the host agent runs the agentic steps.
Porting to another agent (Codex / Gemini / Antigravity) is a ~20-line re-skin of this same
flow — see [`../README.md`](../README.md).
