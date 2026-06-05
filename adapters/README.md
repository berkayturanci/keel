# keel adapters

An **adapter** is the thin, per-agent layer that lets a host agent drive keel's fixed
backbone. The round structure, the deterministic plumbing, and the contracts all live in
**keel-core** (`src/keel`, exposed via the `keel` CLI). An adapter only:

1. **reads the project config** via the `keel` CLI (`keel validate|plan|run-gates`), so it
   carries **no** project specifics;
2. **dispatches the agentic steps** (implement / classify / review) to *its* agent; and
3. lets keel-core do the deterministic work (config resolution, gate planning + execution,
   findings normalisation, attribution).

Because all the logic is in keel-core, adding a vendor is a ~20-line re-skin — the same
backbone, config, and extensions, just invoked from a different agent's command/prompt
surface (this mirrors ai-jury's "orchestrator owns the rounds; adapters are thin").

```
adapters/
  claude/keel-ship.md     Claude Code slash command  (/keel:ship)
  codex/   …              Codex prompt                (planned)
  gemini/  …              Gemini SKILL.md             (planned)
  agy/     …              Antigravity entry           (planned)
```

## Invocation

`keel` is the namespace; the same string works in every project (only that project's
`.keel/project.yaml` + extensions change behaviour):

- Claude Code: `/keel:ship`, `/keel:wrap`, …
- Gemini: the `keel:ship` skill · Codex / Antigravity: the corresponding prompt

## Writing a new adapter

1. Copy `claude/keel-ship.md`.
2. Keep **Step 0** identical (it calls the `keel` CLI — vendor-neutral).
3. Re-map only the agentic dispatch (s4/s5/s7) and the PR/CI/merge tool calls onto the new
   agent's tools.
4. Never inline a project specific — if you would type `develop`/`gradle`/`flutter`/an agent
   name, read it from config instead.
