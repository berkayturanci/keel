# keel:ship (Antigravity / agy adapter)

Thin re-skin of [`../claude/keel-ship.md`](../claude/keel-ship.md) for Antigravity (`agy`).
The backbone, config, gates, and invariants are keel-core.

## Same as the canonical flow
- **Step 0** identical: `keel validate` / `keel plan` / read `.keel/project.yaml`.
- Backbone s1–s12 and invariants unchanged; test step calls `keel run-gates`.

## agy-specific
- **Invocation:** the `keel:ship` entry for the `agy` CLI.
- **Sandbox:** run with `--sandbox` by default; escalate only for the merge/PR steps.
- **Agentic dispatch (s4/s5/s7):** `agy` is the implementer/reviewer unless a delegate is
  selected. Attribution vendor = `agy` (append `:<model>` when the configured model is
  known, e.g. from `~/.gemini/antigravity-cli/settings.json`).
- **Quota fallback:** on HTTP 429 / RESOURCE_EXHAUSTED, fall back to the host agent
  immediately (do not retry) and record the *effective* implementer in attribution.
- **PR/CI/merge:** `gh` via the keel `github` wrappers.

> Read every project specific from `.keel/project.yaml`; hardcode nothing.
