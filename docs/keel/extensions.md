# Authoring extensions (Lego pieces)

An **extension** adds a project-specific step or gate into a **named backbone slot**
without changing the backbone. Extensions are **add-only**: they can never remove,
reorder, or replace a backbone step. Turning a built-in gate on/off is a *config knob*,
not an extension.

## Slots

| slot | runs at step | typical use |
|---|---|---|
| `after-implement` | s4 implement | post-implementation fix-ups |
| `reviewers` | s7 review | an extra reviewer dimension (e.g. a11y) |
| `tester` | s8 test | an extra test suite / quality gate |
| `pre-merge` | s10 merge | a hard merge gate (may block) |
| `post-merge` | s11 capture | post-merge capture/automation |

Register a file into a slot in `project.yaml`:

```yaml
extensions:
  tester: [design-parity.md]
  pre-merge: [design-parity-gate.md]
extensions_dir: .keel/extensions     # files live here
```

## File format

An extension is a markdown file: a YAML frontmatter mini-spec, then a body
(the prompt, for agentic extensions).

```markdown
---
id: design-parity          # required, unique
slot: tester               # required, must match the slot it is registered in
kind: agentic              # agentic | command   (default: agentic)
agent: inherit             # inherit | claude | codex | agy | ollama:<model>
on_fail: suggest           # warn | suggest | block   (block only in pre-merge)
anchorable: true           # may post inline diff comments
---
Render the changed screens and compare against the Figma baseline.
Report any pixel/layout delta above threshold as a finding.
```

A `command` extension runs a shell command instead of a prompt:

```markdown
---
id: design-parity-gate
slot: pre-merge
kind: command
on_fail: block             # hard gate: merge is blocked unless it passes
run: ./scripts/check-design-parity.sh
---
```

## Rules (validated by `keel validate --root .`)

- `id` and `slot` are required; `slot` must be one of the named slots and must match the
  slot the file is registered in.
- `kind` is `agentic` or `command`. A `command` extension requires `run`; an `agentic`
  extension requires a `prompt` (or a non-empty body).
- `on_fail` is `warn` / `suggest` / `block`. **`block` is only allowed in `pre-merge`**
  (a hard gate).
- A failed gate with no explicit findings is reported at: `block`→`major`, `suggest`→`minor`,
  `warn`→`nit`.

## Fail-soft

A broken or erroring extension **degrades to a no-op** (logged), never aborting the run —
**unless** it declared `on_fail: block`, in which case its error surfaces as a blocking
finding (a hard gate can't silently pass). This mirrors keel's `fail_soft` invariant.

## Agent-neutral

Each extension declares the agent that runs it (`agent:`, default `inherit`). So a
`tester` Lego can run on `ollama:qwen` while review runs on `claude` — all on the same
backbone.

## Worked example: ingreview's "design-equality" test

ingreview wants every UI PR to pass a design-parity check before merge. That is **one
`tester` piece** (runs the comparison, reports deltas) plus **one `pre-merge` gate**
(`on_fail: block`, fails the merge if parity is not met). The backbone is untouched;
SmartInventory — with no such files — simply has empty slots there.
