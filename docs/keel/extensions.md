# Authoring extensions (Lego pieces)

An **extension** adds a project-specific step or gate into a **named backbone slot**
without changing the backbone. Extensions are **add-only**: they can never remove,
reorder, or replace a backbone step. Turning a built-in gate on/off is a *config knob*,
not an extension.

## Hook Slots

Every backbone step exposes at least one hook slot. `s0 config` is the loader for the
extension system, so only the read-only `after:config` reporting hook is available there.

| slot | runs at step | mode shown in plan | may block? | typical use |
|---|---|---|---|---|
| `after:config` | s0 config | deterministic | no | read-only config report |
| `before:select`, `select`, `after:select` | s1 select | deterministic / adapter-required | no | queue filters or selection notes |
| `before:branch`, `after:branch` | s2 branch | deterministic | no | branch/worktree reporting |
| `guard` | s3 guard | deterministic | yes | pre-implementation safety checks |
| `before:implement`, `after-implement` | s4 implement | adapter-required | no | implementer prompts or post-implementation checks |
| `classify`, `after:classify` | s5 classify | adapter-required / deterministic | no | risk/routing additions |
| `before:ci`, `after:ci` | s6 ci | deterministic | no | CI diagnostics or summaries |
| `reviewers`, `after:review` | s7 review | agentic / adapter-required | no | reviewer dimensions and summaries |
| `tester`, `test`, `after:test` | s8 test | hybrid / deterministic | `tester` and `test` only | project test gates and summaries |
| `before:fixloop`, `fixloop`, `after:fixloop` | s9 fixloop | adapter-required / hybrid | no | fix-loop prompts and reports |
| `pre-merge`, `after:merge` | s10 merge | deterministic | `pre-merge` only | merge gates and merge reports |
| `capture`, `post-merge` | s11 capture | adapter-required | no | knowledge capture and post-merge automation |
| `before:close`, `on-close`, `after:close` | s12 close | deterministic / adapter-required | no | lifecycle labels, closeout reports, notifications |

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
mode: agentic              # deterministic | agentic | hybrid (default follows kind)
agent: inherit             # inherit | claude | codex | agy | ollama:<model>
on_fail: suggest           # warn | suggest | block (block only in blocking slots)
anchorable: true           # may post inline diff comments
required_capabilities: []  # optional: runtime capabilities that must be available
optional_capabilities: []  # optional: runtime capabilities that may degrade
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
- `mode` is `deterministic`, `agentic`, or `hybrid`. If omitted, `command` defaults to
  `deterministic` and `agentic` defaults to `agentic`.
- `on_fail` is `warn` / `suggest` / `block`. **`block` is only allowed in `guard`,
  `tester`, `test`, and `pre-merge`**.
- A failed gate with no explicit findings is reported at: `block`→`major`, `suggest`→`minor`,
  `warn`→`nit`.
- `required_capabilities` and `optional_capabilities` must use known runtime capability names.
  See [`runtime-capabilities.md`](runtime-capabilities.md).

## Fail-soft

A broken or erroring extension **degrades to a no-op** (logged), never aborting the run —
**unless** it declared `on_fail: block`, in which case its error surfaces as a blocking
finding (a hard gate can't silently pass). This mirrors keel's `fail_soft` invariant.

## Agent-neutral

Each extension declares the agent that runs it (`agent:`, default `inherit`). So a
`tester` Lego can run on `ollama:qwen` while review runs on `claude` — all on the same
backbone.

## Plan Visibility

`keel plan` renders every loaded hook under its backbone step with slot, kind, `on_fail`,
execution mode, adapter requirement, and required/optional capabilities. Agentic or hybrid
hooks may require a host adapter even when the deterministic CLI can validate and display
them.

## Worked example: design parity

A project can require every UI PR to pass a design-parity check before merge. That is **one
`tester` piece** (runs the comparison, reports deltas) plus **one `pre-merge` gate**
(`on_fail: block`, fails the merge if parity is not met). The backbone is untouched; a
project without those files simply has empty hooks there.
