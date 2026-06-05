# keel — Claude Code Entry Point

`AGENTS.md` is the canonical cross-AI instruction file for this repository.

Claude Code should read [`AGENTS.md`](AGENTS.md) first — it holds the durable rules
(the fixed backbone and its invariants, the pure-core/thin-I/O split, the 100 %
coverage bar, the single-runtime-dependency rule, conventions, and keel's agent
dispatch).

Then, for the task at hand:

- **Driving an issue end-to-end** (`/keel:ship`): the adapter is
  [`adapters/claude/keel-ship.md`](adapters/claude/keel-ship.md). It is
  project-neutral and reads every keel-specific value from `projects/keel.yaml` via
  the `keel` CLI.
- **Architecture / design questions**:
  [`docs/proposals/keel-architecture.md`](docs/proposals/keel-architecture.md).
- **Config / extensions / CLI reference**: [`docs/keel/`](docs/keel/).

Keep durable workflow rules in `AGENTS.md` instead of duplicating them here.
