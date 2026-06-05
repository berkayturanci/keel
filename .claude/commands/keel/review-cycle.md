---
description: Review an open PR to ready-to-merge (the s7/s9 review+fix loop, standalone).
argument-hint: "[pr number] [--review-delegate <...>] [--review-comments <inline|summary>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(jury:*), Read, Edit, Agent
---

# /keel:review-cycle

The standalone review→fix loop (`s7`+`s9`) over an existing PR. Reads `.keel/project.yaml`.

1. `keel ship .keel/project.yaml --root . --pr <N>` → risk tier (reviewer count), CI status,
   gate results, decision.
2. Run **N reviewers** (host or `--review-delegate`) + any `reviewers` Lego extensions; each
   produces structured findings (severity + `file:line`).
3. `keel run-gates .keel/project.yaml --root .` (build/lint/**jury**/tester).
4. Post findings per `--review-comments` (inline-hybrid default): critical/major as **inline
   comments** on `file:line`, the rest as a summary. critical/major = block.
5. While blocking findings remain and the budget (≤3 rounds) is not spent: fix → push →
   re-review. Stop when clean or the budget is exhausted; report the verdict.

Does not merge — hand a clean PR back to `/keel:ship` for the windowed, locked merge.
