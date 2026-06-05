---
description: Check a PR's CI status and surface/triage failures.
argument-hint: "[pr number]"
allowed-tools: Bash(keel:*), Bash(gh:*), Read
---

# /keel:ci-check

Project-neutral CI status check. Reads `.keel/project.yaml` (`ci_workflows`).

1. `keel ship .keel/project.yaml --root . --pr <N>` → the CI rollup + decision, or
   `gh pr checks <N>` for per-workflow detail across the project's `ci_workflows`.
2. For each failing workflow: pull the failing job's log tail, classify (real failure vs.
   flake vs. infra), and report with the `file:line` where the tool output carries one.
3. Recommend the next action: re-kick (transient), `/keel:review-cycle` (real failure), or
   escalate (infra/quota). Do not merge.

Read-only; deterministic for identical CI state.
