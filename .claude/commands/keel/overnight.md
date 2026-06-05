---
description: Unattended overnight loop — ship ready work until the merge window closes.
argument-hint: "[--max <N>] [--review-comments <inline|summary>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(jury:*), Read, Edit, Write, Agent
---

# /keel:overnight

Run `/keel:ship` unattended over the backlog until the **merge window** closes or `--max`
issues are shipped. Reads `.keel/project.yaml`.

1. `keel window .keel/project.yaml` — only proceed while the window is open; stop at close.
2. Pick the next ready issue (backlog order; skip blocked/needs-input).
3. Run `/keel:ship` for it (full backbone, inline-hybrid review, jury gate).
4. On a blocking failure that can't be auto-fixed within the round budget: **defer** it to the
   morning queue (for `/keel:morning`) and move on — never force a risky merge.
5. Loop. At window close (or `--max`), write an overnight summary for `/keel:morning`.

Invariants: never merge outside the window, fail-soft per issue (one failure never aborts the
loop), attribute the effective agents.
