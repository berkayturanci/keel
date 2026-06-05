# keel:ship (Gemini adapter)

Thin re-skin of [`../claude/keel-ship.md`](../claude/keel-ship.md) for Gemini. Ships as a
`SKILL.md` under the project's `.gemini/skills/`; the backbone, config, gates, and
invariants are keel-core.

## Same as the canonical flow
- **Step 0** identical: `keel validate` / `keel plan` / read `.keel/project.yaml`.
- Backbone s1–s12 and invariants unchanged; test step calls `keel run-gates`.

## Gemini-specific
- **Invocation:** the `keel:ship` skill (Gemini Code Assist / `.gemini/skills/`).
- **Agentic dispatch (s4/s5/s7):** Gemini is the implementer/reviewer unless a delegate is
  selected. Attribution vendor = the effective agent (`keel` attribution rules).
- **PR/CI/merge:** `gh` via the keel `github` wrappers.

> Read every project specific from `.keel/project.yaml`; hardcode nothing.
