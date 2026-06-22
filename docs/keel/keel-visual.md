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
| `serve` / `serve --all` | a **live** web dashboard (localhost server, polls every ~0.5s) | yes |

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

### The live web dashboard — `keel-visual serve`

```bash
keel-visual serve --all --root ~/code        # http://127.0.0.1:8765 — Ctrl-C to stop
```

`serve` is the **live** web board: a tiny localhost HTTP server serves the dashboard once
and a `/board.json` endpoint that re-reads the records on **every request**. The page polls
every ~0.5s and updates itself — open it on the side and watch runs appear and advance.
Localhost-only by default (`--host` / `--port`). It's the web counterpart of the live
terminal `dash --all`. The header has a **filter box** (type a project / `#PR` / command to
narrow the grid), the **all / active** toggle, and a **light / dark** toggle (system →
light → dark, remembered).

Click a run for a closable right-side **detail drawer** (full-screen on mobile, `×` or
`Esc` to close):

- a **2D / 3D** switch. 2D is the step-by-step list; **3D** is a live WebGL scene of *that
  one run's* backbone — done steps green, the active step a pulsing glow, gates amber, the
  rest muted. **Drag to orbit, scroll / pinch to zoom.**
- a **3D-style picker** — `curve · helix · ring · line` (clean geometric arrangements of the
  step markers) plus `plexus · aurora · comet` (runviz's interweaving particle scenes). The
  choice is remembered; the scene follows the light/dark theme. THREE.js is lazy-loaded only
  on the first 3D switch, so the base dashboard stays dependency-free (it shows
  "3D unavailable" offline).
- **command / phase / status** shown under both views.

## What shows on the board

A run appears on the board when keel-visual can find a live record for it on the **same
filesystem**. Two sources feed it:

1. **The `keel activity` channel** (the reliable one). Each run stamps its `keel.flows`
   phase into `.keel/activity/<run-id>.json`. **As of keel 1.6.4 this is automatic** — the
   deterministic backbone commands stamp it themselves: `keel plan` at Step 0 (the run
   appears), `keel run-gates` at the test gate, and `keel merge` at the merge step (so a
   ship advances **start → test → merged** on the board even if the agent never runs the
   per-phase `keel activity` calls). **As of keel 1.6.5** that merge stamp records the
   status **`merged`** (a real merge landed) — so the green `merged` badge comes straight
   from the activity channel, no checkpoint required, and is kept distinct from the muted
   `done` of a run that merely closed out. Non-ship commands (`triage`, `morning`,
   `pr-loop`, …) stamp every phase as they go. The board reads these **per worktree** (an
   agent runs a command in its own worktree and stamps activity there), keyed by run-id,
   with the command in the footer. A **multi-issue** `/keel:ship` or `/keel:work-block`
   stamps every selected issue's `ship-<N>` at `s0` **at dispatch** (in the parent, before
   any child handoff), so the whole batch appears at once — not just the issue currently being
   worked, and even if a child agent never reaches its own per-phase `keel activity` calls.

   Each run is **labelled** by its issue **and** PR when it carries both —
   `#<issue>→#<PR>` (e.g. `#500→#501`) so a row is unambiguous — else by its PR (`#PR`),
   else its issue (`#issue`), else the number its run-id ends in — `ship-585` → `#585`,
   `pr-loop-2253` → `#2253` (keel 1.6.5; the per-phase stamps don't all carry an explicit
   issue). An opaque counter such as a `morning` run's `m-1` is left as-is.

   The **web** card carries more than the label: a status icon, the live **issue/PR
   title**, the **branch → base**, and the **author**. Branch / base / author come from the
   ledger `ship_run` record keel already writes (no new field, never stale); the title is
   fetched **live** from `gh` and cached per process, so a renamed issue shows correctly on
   the next start. All of it is best-effort — a run with only an activity record (no ledger),
   or a machine without `gh`, simply shows its number(s); the card never blanks. The terminal
   `dash` board stays a compact one-line-per-run view.
2. **Ship checkpoints** (richer, optional). With checkpoint config, `keel ship` also writes
   a resumable checkpoint (`s0`–`s12`) carrying the merge gate / jury / test-gate detail.
   The board reads the checkpoint of **every worktree** and, when a run has both, prefers
   the checkpoint's detail (de-duplicated by run-id).

A finished run is faded, last-sorted, and hidden by the `active` filter (nothing is removed
from disk — purely a view filter). A run that actually **merged** shows a green `merged`
badge; one that merely **closed out** — a `morning`/`triage` that never merges, or a ship
that *deferred* its merge to the next window — shows a muted `done` badge (it is not claimed
as merged).

### Why a run might *not* show

- The run executed on a **different machine** (a remote/cloud session) — it writes its
  records there, not on this filesystem.
- The project isn't an immediate child of `--root`, or its checked-out branch has no
  `.keel/project.yaml` (so `--all` doesn't discover it).
- The run started under **keel < 1.6.4**, where the backbone didn't auto-stamp — a ship
  whose agent skipped the per-phase `keel activity` calls left no record. Runs started on
  1.6.4+ stamp automatically; refresh the project's adapters (`keel install-adapter all
  --force`) so its Step 0 / s8 / s10 calls pass `--run-id`.
- A genuinely one-shot command you ran without a run-id (you can still render any command
  on its own with `play --command <name>`).

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
