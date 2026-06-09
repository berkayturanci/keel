---
name: keel-ship-v2
description: Compound-engineering flavour of the ship workflow — folded into /keel:ship. This file points to keel:ship and notes the deltas, which are expressed as keel extensions, not a separate command.
---

# keel-ship-v2

Use this skill when the user asks to run the keel command `ship-v2` (e.g. `keel ship-v2 ...`, `ship-v2 <args>`, or `/keel:ship-v2`). It reads every project value from `.keel/project.yaml` via the `keel` CLI.

# /keel:ship-v2

There is no distinct project-neutral `ship-v2` command. The "v2" workflow was a strict
superset of `ship` with five well-scoped substitutions, and **every portable part of it is
already a knob or a `.keel/extensions/` Lego in `/keel:ship`** — not a parallel backbone.
Run **`/keel:ship`**; reach v2 behaviour by configuring the project, not by switching
commands.

## Why it folds in

The v2 deltas were inserted at fixed `ship` step boundaries and never bypassed the merge
lock, scope-validation gate, or window gate. In keel terms each maps cleanly:

- **Commit + PR quality** (conventional-commit subject, value-first PR body) — part of the
  s4 implement brief; a project may strengthen it via an `implementer_agents` role brief.
  Skipped automatically when the resolved implementer is a self-driving CLI vendor that
  already opens its own PR (key off the **effective** implementer, not just a label), so v2
  never double-commits; a local-model implementer is orchestrator-driven, so it is **not**
  skipped (the orchestrator opens its PR).
- **Self-review / simplify pass** before review fan-out — a `pre-merge` or pre-review Lego
  the project supplies; its changes push as a follow-up commit and re-trigger the s4
  branch-scope gate on the new HEAD.
- **Persona / diff-aware reviewer fan-out** — the s7 `reviewers` Lego. The tier (s5) is the
  canonical risk signal that decides depth; size thresholds for a docs-only lightweight path
  stay project-specific config. Log the path decision (tier, files, lines, reason) so a
  downgrade is never silent.
- **Structured PR-feedback resolution** — the s9 fix-loop; a project may route the fix
  through a richer resolver Lego. The blocker-vs-suggestion loop-exit and the ≤3-round
  budget are unchanged.
- **Post-merge durable-learning capture** — the s11 `capture` Lego, with the same canonical
  marker discipline and session-end verifier already described in `/keel:ship`.

## Notable differences to keep in mind (project-specific; stay in the project)

- A reviewer Lego may post findings **directly** to the PR (its own native contract),
  diverging from `ship`'s orchestrator-only-writes default. That divergence is a property of
  that extension and must be declared by it — the default elsewhere remains orchestrator-only.
- If a project's compound/persona extensions are unavailable at runtime, **fall back to the
  base `ship` behaviour for that step and log it**; treat "most substitutions degraded" as
  operator error and just run plain `/keel:ship`.
- `--reviewers` may be ignored by a size-gated reviewer Lego — warn and continue.

For the full flag surface, backbone (s0–s12), JSON contract, jury gate, merge lock,
night no-merge window, fail-soft rules, and effective-vendor attribution, see
**`/keel:ship`** (`adapters/commands/ship.md`).
