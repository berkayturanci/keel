---
description: Triage the open issue backlog — label, tier, and rank by readiness.
argument-hint: "[--label <name>] [--assign]"
allowed-tools: Bash(keel:*), Bash(gh:*), Read
---

# /keel:triage

Project-neutral backlog triage. Reads `.keel/project.yaml`.

1. List open issues (`gh`).
2. For each: infer a **risk tier** from the files it implies vs. `tier3_globs`; suggest
   labels (bug/feature/severity/role); flag missing acceptance criteria or repro steps.
3. Rank by readiness: clear + unblocked + high-severity first.
4. `--assign` → set the role label that routes the implementer (`implementer_agents`).

Read-only by default (only labels/assignments are written with `--assign`). Deterministic
ordering for identical backlog state. Hand ready items to `/keel:ship`.
