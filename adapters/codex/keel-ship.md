# keel:ship (Codex adapter)

Thin re-skin of [`../claude/keel-ship.md`](../claude/keel-ship.md) for the Codex CLI.
The backbone, config resolution, gates, and invariants are all keel-core — this file only
changes the host surface.

## Same as the canonical flow
- **Step 0** is identical: `keel validate` / `keel plan` / read `.keel/project.yaml`.
- The backbone s1–s12 and the invariants are unchanged.
- The test step calls `keel run-gates`.

## Codex-specific
- **Invocation:** the `keel:ship` prompt in Codex (`codex` CLI).
- **Sandbox:** run read-only by default (`-s read-only`); only the merge/PR steps escalate.
- **Agentic dispatch (s4/s5/s7):** the implementer/reviewer is Codex itself unless the
  issue's `delegate:*` label or `--delegate` selects another vendor. Attribution vendor =
  `codex` (append `:<model>` when known); use `keel`'s attribution rules.
- **PR/CI/merge:** use `gh` via the keel `github` wrappers (or Codex's GitHub tools).

> Never inline a project specific — read base branch, build command, agents, CI workflows,
> and globs from `.keel/project.yaml`.
