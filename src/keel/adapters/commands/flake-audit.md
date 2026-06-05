---
description: Detect and quarantine flaky tests; open issues to fix the root cause.
argument-hint: "[--runs <N>] [--open-issues]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Edit
---

# /keel:flake-audit

Project-neutral flaky-test audit. Reads `.keel/project.yaml` (`build_gate_cmd`).

1. Re-run the project's test gate (`keel run-gates .keel/project.yaml --root .`) `--runs`
   times (default 5); record per-test pass/fail across runs.
2. A test that both passes and fails across identical runs is **flaky** — list them with the
   failure signature and `file:line`.
3. `--open-issues` → open a deduped issue per flaky test (labelled `flake`, tiered) and hand
   the fix to `/keel:ship`. Optionally suggest a quarantine annotation (never silently skip).

Determinism note: only an across-runs *disagreement* marks a flake — a consistent failure is
a real bug, not a flake. Fail-soft.
