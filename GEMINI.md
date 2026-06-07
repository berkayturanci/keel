# keel — Gemini / Antigravity Entry Point

`AGENTS.md` is the canonical cross-AI instruction file for this repository.

Gemini CLI and **Antigravity** should read [`AGENTS.md`](AGENTS.md) first — it holds the
durable rules (the fixed backbone and its invariants, the pure-core/thin-I/O split, the
100 % coverage bar, the single-runtime-dependency rule, conventions, and keel's agent
dispatch). Antigravity reads `AGENTS.md` natively; this file is the Gemini-family pointer
to it.

Then, for the task at hand:

- **Driving an issue end-to-end** (`keel:ship`): the adapters are
  installed into the shared [`keel-ship` skill](.agents/skills/keel-ship/SKILL.md).
  Gemini, Antigravity, Codex, and other non-Claude agents read the shared
  `.agents/skills/keel-<cmd>/SKILL.md` files instead of separate per-agent adapter copies.
- **Architecture / design questions**:
  [`docs/proposals/keel-architecture.md`](docs/proposals/keel-architecture.md).
- **Config / extensions / CLI reference**: [`docs/keel/`](docs/keel/).

Keep durable workflow rules in `AGENTS.md` instead of duplicating them here.
