---
description: Drive a GitHub issue end-to-end through the keel backbone (branch → implement → CI → review → test → merge → close).
argument-hint: "[issue numbers...] [--delegate <claude|codex|agy|ollama:MODEL>] [--review-delegate <...>] [--review-comments <inline|summary>] [--dry-run]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(jury:*), Read, Edit, Write, Agent
---

# /keel:ship

Project-neutral flagship workflow. Every project value comes from `.keel/project.yaml`
via the `keel` CLI — never hardcode a branch, command, glob, agent, or timezone here.

## Step 0 — orient (deterministic, via the CLI)

```bash
keel validate .keel/project.yaml --root .     # config + extensions must be valid
keel plan     .keel/project.yaml --root .     # the backbone + this project's gates/Lego
keel window   .keel/project.yaml              # is the merge window open right now?
```

Read `base_branch`, `knobs` (build/lint commands, `implementer_agents`, `tier3_globs`,
`ci_workflows`), `merge_window(_mode)`, and the `tester` / `pre-merge` extensions from the
plan. Hold the **merge lock** for the merge step only.

## Backbone (do not reorder; the step IDs are fixed)

- **s1 select** — take the issue(s) from args, or the top of the backlog.
- **s2 branch** — cut a work branch off `base_branch` (a worktree per issue is fine).
- **s3 guard** — refuse if the working tree is dirty or the branch already has an open PR.
- **s4 implement** *(agent)* — implement the issue. The implementer is resolved from
  `implementer_agents` by the issue's role label, **overridden by `--delegate`**, defaulting
  to the host agent. A delegated implementer (`codex exec`, `agy --print`, `ollama`) gets the
  same brief + the return contract (`pr_number`, `branch`, `files_changed`). Fail over to the
  host agent on quota errors (429/RESOURCE_EXHAUSTED) and record the **effective** agent.
- **s5 classify** — `keel ship .keel/project.yaml --root .` prints the **risk tier** (from
  `tier3_globs`) → reviewer count, the window state, the gate results, and the merge decision.
- **s6 ci** — push the branch, open the PR, wait for the project's `ci_workflows` to go green
  (`gh pr checks`). Re-kick on transient failures; surface real ones.
- **s7 review** *(agent)* + slot `reviewers` — run **N reviewers** (N from the tier), the host
  or `--review-delegate`. Each reviewer reads the diff and produces structured findings
  (severity + `file:line`). Run any `reviewers` Lego extensions. **Post findings per
  `--review-comments` (inline-hybrid default):**
  - `inline` → anchor each `critical`/`major` finding as an **inline review comment** on its
    `file:line`; post a short summary comment with the rest.
  - `summary` → one consolidated review comment.
  Severity → action: **critical/major = block**, minor = suggestion, nit = advisory.
- **s8 test** — `keel run-gates .keel/project.yaml --root .` runs the project gates
  (`build` / `lint` / `jury` + `tester` Lego). The **`jury` gate** runs the ai-jury CLI on
  the diff when present (fail-soft no-op otherwise) and folds its findings in.
- **s9 fixloop** — while there are blocking findings and the budget (≤3 rounds) is not spent:
  fix → push → re-run s7/s8. Each round posts its own review (per `--review-comments`).
- **s10 merge** — only inside the **merge window** (unless `--hotfix`), holding the merge
  lock: re-confirm CI green + zero blocking findings, then squash-merge. `pause` mode halts
  here outside the window; `freeze` defers to the morning queue.
- **s11 capture** — record the run (effective agents, tier, rounds, decision) for `/keel:wrap`.
- **s12 close + cleanup** — close the issue, link the PR, drop the lock, then **clean up the
  workspace** for this issue:
  - remove the worktree: `git worktree remove <repo-root>/worktrees/<slug> --force`;
  - **delete the now-merged branch** locally and on the remote:
    `git branch -d <branch>` (the safe `-d` refuses an unmerged branch) and
    `git push origin --delete <branch>`. Skip the remote delete if the repo already
    auto-deletes head branches on merge. Only run this **after** the merge is confirmed.

## `--dry-run`

Run s0–s8 read-only and print the plan + `keel ship` assessment (tier, window, gates,
decision). Do **not** push, open a PR, post comments, or merge.

## Invariants (always)

Merge lock around the merge only · never merge in the night no-merge window (except an
audited `--hotfix`) · fail-soft (a missing CLI/gate degrades, never crashes the run) ·
the orchestrator owns writes · attribute the **effective** vendor+model.
