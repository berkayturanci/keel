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

## Reclaiming runtime artifacts — `keel gc`

Keeping artifacts out of `git` is only half the job: the disposable trees still grow on
disk. `keel gc` is the single, auditable entry point that takes out keel's own trash:

```bash
keel gc .keel/project.yaml --root .                # empty scratch + prune old activity
keel gc .keel/project.yaml --root . --dry-run      # show what would be reclaimed, remove nothing
keel gc .keel/project.yaml --root . --keep-activity 100   # keep more recent runs
```

What it does:

- **Scratch** — empties `.keel/scratch` entirely (it is transient by definition). Skip with
  `--no-scratch`.
- **Activity** — count-based retention: keeps the newest `--keep-activity` records (default
  **50**, by modification time) and removes the rest, so the live board stays useful without
  growing forever. Skip with `--no-activity`.

What it never touches: the **run ledger** (`.keel/state/run-ledger.jsonl`, durable by
design), the **checkpoint** (overwritten per run), and **locks** (released as they are
used). Reclamation is scoped to `scratch/` and `activity/*.json` by construction.

`keel gc` is **fail-soft**: a failure reclaiming one tree degrades to a no-op (reported on
stderr, or in the `degraded` array under `--json`) and the other tree still runs — it never
aborts its caller. The `/keel:ship` adapter runs `keel gc` at the end of every run (and on
early exit), so under normal operation scratch and activity never accumulate; run it by hand
any time to reclaim on demand.

| artifact | bounded by | auto-reclaimed? |
|----------|-----------|-----------------|
| `.keel/scratch/` | `keel gc` (end of run) | yes — emptied each run |
| `.keel/activity/` | `keel gc --keep-activity N` | yes — newest N kept |
| `.keel/state/run-ledger.jsonl` | append-only, durable | **no** — never auto-removed |
| `.keel/state/checkpoint.json` | overwritten per run | n/a — single file |
| `.keel/state/locks/` | released after use | n/a — claim lifecycle |

## Explicit output paths still work

This hygiene is the *default*, not a cage. When an operator passes an explicit output or
debug path, keel honours it verbatim — including a path outside `.keel/`. In that case
keel does not scaffold a gitignore for it: the operator chose that location deliberately,
and keel must not silently ignore a path they asked to see.
