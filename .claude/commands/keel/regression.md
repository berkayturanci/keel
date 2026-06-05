---
description: Run the project's regression suite, triage failures, and open fix issues/PRs through keel.
argument-hint: "[--scope <changed|full>] [--since <ref>] [--open-issues] [--delegate <...>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Edit, Write, Agent
---

# /keel:regression

Project-neutral regression runner. All project specifics (the test command, paths, CI
workflow names, risk globs) come from `.keel/project.yaml` — nothing is hardcoded here.

## Step 0 — orient
```bash
keel validate .keel/project.yaml --root .
keel plan     .keel/project.yaml --root .    # read build_gate_cmd, tier3_globs, ci_workflows
```

## Flow
1. **Scope** — `--scope changed` (default) runs against `git diff base...HEAD`; `--scope full`
   runs the whole suite. `--since <ref>` overrides the base.
2. **Run gates** — `keel run-gates .keel/project.yaml --root .` executes the project's
   `build` (the regression/test command from `build_gate_cmd`), `lint`, and (if listed) the
   `jury` gate. Capture each gate's findings (`file:line`, severity).
3. **Triage failures** — group failures; for each, classify the **risk tier** via
   `keel ship` (uses `tier3_globs`). A failure touching a tier-3 area is escalated.
4. **Report** — post a structured summary; anchor reproducible failures as inline findings
   (`file:line`) where the gate output carries a location.
5. **`--open-issues`** — open a GitHub issue per distinct failure (deduped by signature),
   labelled by tier, linking the failing gate output. Hand each off to `/keel:ship` (or the
   `--delegate` agent) to fix.

## Invariants
Fail-soft (a missing tool degrades to a skipped check, never a crash) · deterministic
grouping (same failures ⇒ same issues) · never auto-merge a fix — that goes through
`/keel:ship`'s backbone (window + lock + review).
