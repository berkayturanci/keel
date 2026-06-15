# Changelog

All notable changes to keel-visual are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); keel-visual adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] — 2026-06-15

### Added
- **Non-ship command runs on the board.** keel-visual now reads the additive
  `.keel/activity/` records (the `keel activity` channel, core 1.6.0) alongside
  ship checkpoints, so commands that never write a ship checkpoint — triage,
  morning, pr-loop … — appear live with their own `keel.flows` phases. A finished
  (`done`) record is faded and last-sorted like any merged run; the `all`/`active`
  filter hides them. The `keel.activity` import is fail-soft, so keel-visual still
  installs against an older core (the feature just no-ops).

### Changed
- **Dependency floor raised to `keel-workflow >= 1.6.0`** (the `keel activity`
  channel landed in core 1.6.0).

## [0.3.0] — 2026-06-15

### Added
- **Automatic light/dark board theme.** The web board (2D grid + 3D scene)
  follows the system `prefers-color-scheme`. The grid re-themes live; the 3D
  scene picks the palette (fog, idle, labels, tracks) at load and reloads to
  re-theme if the system flips while it's open (#432).
- **`all` / `active` filter + faded finished runs.** Finished (merged) runs are
  sorted last and rendered faded — dimmed cards in the 2D grid, dimmed lanes in
  the 3D scene. A header `all` / `active` toggle (or `?filter=active`) hides them
  entirely in both views; the 3D scene rebuilds and reframes. Purely a view
  filter — nothing is removed from disk (#433).

## [0.2.0] — 2026-06-15

### Added
- **Multi-project boards — one view across every keel project.** `dash --all`
  (terminal) and `render --all` (web) point at a parent folder and aggregate
  every keel project one level under it into a single board, grouped by project.
  Local-only, fail-soft, same-filesystem (#416, #419).
- **Web board 2D grid / 3D scene toggle.** The `render --all` board gains a
  header toggle (or `?mode=3d`). The 3D scene packs every run into one Three.js
  perspective — one lane per run, a sphere per step in the shared colour
  language, the active node glowing, each lane labelled `project #PR` (#421).
- **Live jury status from the checkpoint.** `play --follow` surfaces the jury's
  *live* mode (advisory/gating) read from the run checkpoint's `jury_mode`, not
  only the post-run ledger — so the jury appears as it resolves. Requires keel
  core ≥ 1.4.0, which writes `state.jury_mode` (#399).
- **Cinematic theater handoff.** `play --follow --theater` hands the screen to
  ai-jury's `jury --theater` at the review step when the jury is active, then
  resumes from the live checkpoint. Fail-soft and dependency-free — keel-visual
  never imports ai-jury; if the `jury` CLI is absent nothing animates and
  nothing errors (#401).

### Changed
- **Three.js loaded with Subresource Integrity.** Both the per-run `render` and
  the `render --all` board pin Three.js from cdnjs with a sha512 SRI hash +
  `crossorigin`, so the browser refuses altered bytes. The 2D views never touch
  the network (#421).
- **Dependency floor raised to `keel-workflow >= 1.4.0`** for the live-jury
  checkpoint field.

## [0.1.0] — 2026-06-14

### Added
- Initial release: an optional animated 2D/3D run visualizer that *renders* a
  keel run from its ledger and checkpoint (it never drives one). Terminal `play`
  (flow + wave ribbon, `--loop`, live `--follow`), a parallel `dash` board, and a
  web `render` (2D flow + selectable 3D styles: plexus, comet, aurora, combined,
  line). Renders any of the 16 command flows via `keel.flows`. Depends on
  `keel-workflow`; the core never depends on it.
