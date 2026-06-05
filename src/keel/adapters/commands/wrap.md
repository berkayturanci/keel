---
description: End-of-session wrap — summarize what shipped, what's open, and what's deferred.
argument-hint: "[--since <ref|timestamp>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Write
---

# /keel:wrap

Project-neutral session wrap-up. Reads `.keel/project.yaml`.

1. **Shipped this session** — issues closed / PRs merged since `--since` (default: session
   start), with the effective agent + tier captured by `/keel:ship` (s11).
2. **Open** — PRs awaiting CI/review/merge and their blockers.
3. **Deferred** — items pushed to the morning queue (outside-window merges, unresolved
   blockers) for `/keel:morning` to pick up.
4. **Window** — `keel window .keel/project.yaml` so the wrap notes whether the no-merge window
   is in effect.

Write the wrap to the project's reports path; deterministic for identical state.
