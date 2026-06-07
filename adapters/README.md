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

## Generated-surface contract

`keel install-adapter all --root <tmp-project>` is the release-smoke path for adapter
generation. It must produce one Claude command and one shared skill for every packaged
command body under `src/keel/adapters/commands/*.md`.

The generated Claude command preserves the packaged command body byte-for-byte, followed
by a `keel-generated` marker used for adapter status and update compatibility. The
generated skill preserves the same project-neutral body but uses skill frontmatter instead of
Claude slash-command metadata: `name: keel-<cmd>` and `description` are preserved;
`argument-hint` and `allowed-tools` are intentionally dropped from `SKILL.md`. It also
receives the same generated marker.

Generated files are idempotent: a second install without `--force` skips existing files, and
`--force` overwrites only the generated adapter surfaces. Tests validate the command counts,
frontmatter shape, idempotency, and absence of consumer-specific strings.

## Update compatibility

Generated adapter files include a trailing `keel-generated` marker with the command name,
surface, keel version, source hash, and generated-body hash. `keel adapter-status` uses this
marker to classify files as `current`, `outdated`, `missing`, `locally-modified`, or
`unknown`; `keel update-adapter --dry-run` shows the planned changes; `keel update-adapter`
regenerates only `missing` and `outdated` adapter files.

If a generated adapter was edited by hand, keel reports `locally-modified` and leaves it
untouched. Files without a generated marker are `unknown` and are also left untouched. Project
config, `.keel/extensions/`, project-provided commands, and local compatibility wrappers are
outside this update path unless they are explicitly marked as generated keel adapter files.

## Changing a command

Edit the packaged body under `src/keel/adapters/commands/<cmd>.md` (the single source), then
`keel install-adapter all --force` to re-install both surfaces. Never inline a project
specific: if you would type a branch name, build tool, framework, service, path glob, or agent
name, read it from config instead.
