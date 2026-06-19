# Artifact & output locations

keel produces two kinds of files while it drives a workflow: **runtime state** it
writes deterministically, and **transient scratch** an agentic step stages while it
works. Neither belongs in the consumer's primary checkout. This page documents where
each lands and how keel keeps them out of your `git status`.

## The `.keel/` directory

Everything keel owns lives under a single project directory, `.keel/`:

| Path | What | Tracked? |
|------|------|----------|
| `.keel/project.yaml` | project configuration | **committed** |
| `.keel/extensions/` | project-owned Lego extensions | **committed** |
| `.keel/.gitignore` | ignores the runtime subtrees below | **committed** |
| `.keel/state/checkpoint.json` | resumable ship checkpoint | ignored |
| `.keel/state/run-ledger.jsonl` | structured run ledger | ignored |
| `.keel/state/locks/` | merge / resource locks | ignored |
| `.keel/activity/` | live-board activity records | ignored |
| `.keel/scratch/` | agent scratch (diffs, dumps, drafts) | ignored |

`project.yaml` and `extensions/` are config you commit. The rest is disposable per-run
state — safe to delete at any time.

## Auto-scaffolded `.keel/.gitignore`

So runtime state never shows up as repo noise, keel scaffolds `.keel/.gitignore` and
keeps it topped up. It is created on `keel init` / `keel setup`, and self-heals on the
first runtime write of any existing install — so a repo onboarded before this behaviour
existed gets the gitignore the next time keel writes a checkpoint, activity record,
ledger entry, or lock. The file is committed; only the runtime subtrees it lists are
ignored:

```gitignore
state/
activity/
scratch/
*.tmp
```

If you add your own entries they are preserved — keel only ever appends the runtime
patterns it needs and never rewrites an already-complete file. The scaffolder is a no-op
when an output path is deliberately pointed outside `.keel/` (see below).

## The scratch directory

Agentic steps occasionally need to stage a transient file — a PR diff, an issue/body
dump, draft review or closure prose, a one-off patch. These go in the keel-owned scratch
directory, never the repo root. Resolve it from the CLI:

```bash
SCRATCH="$(keel scratch-dir --root .)"   # = .keel/scratch (created + gitignored on first call)
gh pr diff "$PR" > "$SCRATCH/pr-$PR.diff"
```

`keel scratch-dir` prints the path and creates it (with the gitignore) on first use; pass
`--no-create` to print without touching the filesystem. The `/keel:ship`,
`/keel:pr-loop`, and `/keel:review-cycle` adapters carry an **artifact-hygiene** rule
instructing agents to route all transient files here, which is why a consumer repo no
longer accumulates `plan.json`, `ship.json`, `pr_<n>_review.md`, `pr<n>.diff`, `issue.md`
and friends at its root.

## Explicit output paths still work

This hygiene is the *default*, not a cage. When an operator passes an explicit output or
debug path, keel honours it verbatim — including a path outside `.keel/`. In that case
keel does not scaffold a gitignore for it: the operator chose that location deliberately,
and keel must not silently ignore a path they asked to see.
