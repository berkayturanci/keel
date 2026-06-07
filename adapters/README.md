# keel adapters

An **adapter** is the thin layer that lets a host agent drive keel's fixed backbone. The round
structure, the deterministic plumbing, and the contracts all live in **keel-core** (`src/keel`,
exposed via the `keel` CLI). An adapter only:

1. **reads the project config** via the `keel` CLI (`keel validate|plan|run-gates`), so it
   carries **no** project specifics;
2. **dispatches the agentic steps** (implement / classify / review) to *its* agent; and
3. lets keel-core do the deterministic work (config resolution, gate planning + execution,
   findings normalisation, attribution).

Because all the logic is in keel-core, the body is the same everywhere — only the *surface* it
is installed into differs (this mirrors ai-jury's "orchestrator owns the rounds; adapters are
thin").

## Where the adapters live

The shipped, project-neutral command bodies are packaged with keel under
`src/keel/adapters/commands/*.md` and installed via
[`keel install-adapter`](../docs/keel/cli.md). `claude/keel-ship.md` here is kept as a
**reference** of the fully-spelled flow.

keel installs into the **two surfaces** that match how agents actually discover commands —
never one copy per agent (that would re-introduce file-copy drift):

| surface | path | who reads it |
|---|---|---|
| `claude` | `.claude/commands/keel/<cmd>.md` | Claude Code — native `/keel:<cmd>` |
| `skills` | `.agents/skills/keel-<cmd>/SKILL.md` | **every non-Claude agent** (Codex, Antigravity, Gemini, …) via its skill discovery / chat-command wrapper — **one shared copy** |

```bash
keel install-adapter all        # both surfaces
keel install-adapter claude     # just the native Claude commands
keel install-adapter skills     # just the shared skill set
```

## Invocation

`keel` is the namespace; the same string works in every project (only that project's
`.keel/project.yaml` + extensions change behaviour):

- Claude Code: `/keel:ship`, `/keel:wrap`, …
- Every other agent: the `keel-ship` / `keel-wrap` … skill (one shared set under
  `.agents/skills/`).

## Changing a command

Edit the packaged body under `src/keel/adapters/commands/<cmd>.md` (the single source), then
`keel install-adapter all --force` to re-install both surfaces. Never inline a project
specific: if you would type a branch name, build tool, framework, service, path glob, or agent
name, read it from config instead.
