---
description: Implement a single issue on a branch (the s4 step of ship, standalone).
argument-hint: "[issue number] [--delegate <claude|codex|agy|ollama:MODEL>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Edit, Write, Agent
---

# /keel:implement

The standalone implement step (`s4`) of the backbone. Reads `.keel/project.yaml`.

```bash
keel validate .keel/project.yaml --root .
keel plan     .keel/project.yaml --root .   # read base_branch, implementer_agents
```

1. Cut a branch off `base_branch` for the issue.
2. Resolve the implementer from `implementer_agents` by the issue's role label, overridden by
   `--delegate`, defaulting to the host agent.
3. Implement the issue; keep changes scoped to it.
4. Return the contract: `branch`, `files_changed`, a summary. Hand off to `/keel:ship` (or
   `/keel:pr-loop`) to open the PR and drive review/CI/merge.

Fail over to the host agent on delegate quota errors; attribute the **effective** agent.
Do **not** merge here — that is `/keel:ship`'s job (window + lock + review).
