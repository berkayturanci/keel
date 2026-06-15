# keel-visual — the live run board

[`keel-visual`](https://pypi.org/project/keel-visual/) is an **optional, separately
installable** companion that *renders* a keel run — it never drives one. It reads only
the records keel already writes (the run ledger, the resumable checkpoint, and the
additive `keel activity` channel) and animates them, in the terminal or the browser.

This page is the dashboard guide. For the full reference (every flag, the 3D style
internals, the colour language) see
[`keel-visual/README.md`](../../keel-visual/README.md).

## Install

```bash
pipx install keel-visual          # or: pip install keel-visual
keel-visual --help
```

keel-visual needs **keel core ≥ 1.6.0** (it reads `keel.flows` and the `keel activity`
channel); installing it pulls in `keel-workflow` automatically. The core never depends on
keel-visual.

## Surfaces

| surface | what it is | live? |
| --- | --- | --- |
| `play` | the run animates in the terminal (flow + wave ribbon) | yes, with `--follow` |
| `dash` | a terminal board of every active run in one repo | yes (refreshes) |
| `dash --all` | one terminal board across **every keel project** under a parent | yes |
| `render` | a self-contained web page for one run (2D flow + 3D scene) | snapshot |
| `render --all` | a self-contained web **board** across every project | snapshot |

### The board — `dash --all` (terminal, live)

```bash
keel-visual dash --all --root ~/code        # every keel project under ~/code, live
```

`--all` discovers every immediate subdirectory of `--root` that is a keel project (has
`.git` **and** `.keel/project.yaml`) and aggregates their runs into one board, grouped by
project. It refreshes on an interval (`--interval N`); `--once` prints a single frame and
exits. Local-only, fail-soft (a malformed project never blanks the board), one level deep.

### The board — `render --all` (web)

```bash
keel-visual render --all --root ~/code --out board.html && open board.html
```

The web board carries a **2D grid / 3D scene** toggle (or open with `?mode=3d`), follows
the system **light/dark** theme, and has an **`all` / `active`** filter (or
`?filter=active`) that fades finished runs and sorts them last — or hides them outright —
so live work stays in focus. The 3D scene packs every run into one perspective, one lane
per run. It's a **snapshot**: re-run `render --all` to refresh.

## What shows on the board

A run appears on the board when keel-visual can find a live record for it on the **same
filesystem**. Two sources feed it:

1. **Ship checkpoints.** `keel ship` runs in its own git worktree and writes a resumable
   checkpoint (`s0`–`s12`). The board reads the checkpoint of **every worktree** of each
   project (via `git worktree list`).
2. **The `keel activity` channel.** Commands that don't write a ship checkpoint
   (`triage`, `morning`, `pr-loop`, …) stamp their own `keel.flows` phase into
   `.keel/activity/<run-id>.json` as they run. Every stepped command's adapter does this
   as a required first step, so non-ship runs show up live too — each with its own phases
   and its command in the footer. The board reads these **per worktree** as well, since an
   agent runs a command in its own worktree and stamps activity there.

A finished run (a merged ship, or an activity record marked `--done`) is faded and
last-sorted, and the `active` filter hides it. Nothing is ever removed from disk — it's
purely a view filter; a run leaves the board only when its worktree/checkpoint/activity
record is gone.

### Why a run might *not* show

- The run executed on a **different machine** (a remote/cloud session) — it writes its
  records there, not on this filesystem.
- A short, one-shot command that never stamps activity (you can still render any command
  on its own with `play --command <name>`).
- The project isn't an immediate child of `--root`, or its checked-out branch has no
  `.keel/project.yaml` (so `--all` doesn't discover it).
- keel core is older than 1.6.0 (no `keel activity` — the emission is skipped silently).

## With the cross-vendor jury and ai-jury

`play --follow` surfaces the jury's live mode (advisory/gating) from the checkpoint;
`play --follow --theater` hands the screen to [ai-jury](https://github.com/berkayturanci/ai-jury)'s
deliberation theater at the review step, then resumes. Fail-soft and dependency-free —
keel-visual never imports ai-jury; if the `jury` CLI is absent nothing animates and
nothing errors.

## See also

- [`keel-visual/README.md`](../../keel-visual/README.md) — full reference + screenshots
- [`cli.md`](cli.md) — the `keel activity` CLI (and every other `keel` command)
- [`commands.md`](commands.md) — the 16 `/keel:<command>` workflows it can render
