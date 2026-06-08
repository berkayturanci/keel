---
description: Compound-engineering ship workflow variant — first-class, directly invokable, and backed by the shared ship backbone.
argument-hint: "[issue numbers...] [--delegate <claude|codex|agy|ollama:MODEL>] [--review-delegate <claude|codex|agy|ollama:MODEL>] [--review-comments <inline|summary>] [--reviewers <1|2|3>] [--jury|--no-jury|--jury-advisory] [--hotfix] [--dry-run] [--wizard]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(jury:*), Read, Edit, Write, Agent
---

# /keel:ship-v2

Project-neutral compound-engineering variant of `/keel:ship`. It is a **first-class
workflow profile**, not a project extension and not a copied second backbone.

Run the deterministic preflight before mutating work:

```bash
keel plan    .keel/project.yaml --root . --command ship-v2 --live --json
keel ship-v2 .keel/project.yaml --root . --live --json
```

The JSON contract includes `workflow_profile`:

- `profile: "compound"`
- `inherits: "ship"`
- `first_class_variant: true`
- `step_overrides` for `s4 implement`, `s7 review`, `s9 fixloop`, and `s11 capture`

All project values still come from `.keel/project.yaml` via the keel CLI: `base_branch`,
`build_gate_cmd`, `lint_cmd`, `implementer_agents`, `tier3_globs`, `ci_workflows`,
`docs_gate_paths`, `merge_window`, `merge_window_mode`, and `timezone`.

## Shared ship primitives

`ship-v2` reuses the same safety primitives as `/keel:ship`:

- issue selection and queue snapshot
- isolated worktree and branch-from-`base_branch`
- guard and scope validation
- risk classification and reviewer-count policy
- CI evaluation and project gates
- review/jury/merge-gate contract from `review_merge_contract`
- merge window and merge lock
- issue/PR closeout and capture marker discipline

Never fork these mechanics in the adapter. If the shared behavior needs to change, update
the shared ship contract and keep `ship` and `ship-v2` in lockstep for that primitive.

## Compound step overrides

`ship-v2` differs only where `workflow_profile.step_overrides` says it differs:

| step | profile mode | compound behavior |
|---|---|---|
| `s4 implement` | `compound` | Use a compound implement pass that emphasizes PR quality, scope simplification, and value-first change shaping before handoff. |
| `s7 review` | `compound` | Use compound/persona reviewer fan-out when available, while preserving the reviewer count, posting mode, and gating semantics from `review_merge_contract`. |
| `s9 fixloop` | `compound` | Resolve PR feedback through a structured compound loop, but keep the shared blocker/suggestion policy and review-fix budget. |
| `s11 capture` | `compound` | Run durable-learning capture through the capture slot, with the shared canonical marker requirement. |

Compound helpers may be supplied by the host runtime or by project extensions. If a compound
helper is unavailable, fall back to the standard behavior for that step, log the degraded
step, and continue unless the configured extension marks the degradation as blocking.

## When to use it

Use `/keel:ship` for the standard delivery path. Use `/keel:ship-v2` when the operator wants
the compound-engineering flavor: richer implementation shaping, compound review/fix
handling, and durable-learning capture, while retaining the same merge and safety gates.

## Dry run

`--dry-run` must show the same non-mutating contract as `ship`, plus the compound
`workflow_profile`. It must not create branches, edit files, push commits, post comments,
request reviews, merge, close issues, or write capture artifacts.

<!-- keel-generated: surface=plugin command=ship-v2 keel_version=0.7.0 source_sha256=5c0c9191b0ed7d8cfd82d35aafd281627b3d8cf403fdc060c4c8fad7b75b16c8 generated_sha256=5c0c9191b0ed7d8cfd82d35aafd281627b3d8cf403fdc060c4c8fad7b75b16c8 -->
