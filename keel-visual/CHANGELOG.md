# Changelog

All notable changes to keel-visual are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); keel-visual adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] — 2026-10-01

- `keel-visual swarm` draws the plan the run executed — its real waves, dependencies, predicted
  files and a dependent wave marked refused — instead of a flat graph it made up.
- A swarm worker card shows its wave and the stage a live worker is in.

### Added
- **A swarm worker card shows its wave and stage** (keel #1280 item 2). When the worker record
  carries them — keel now writes `wave` and, for a live worker, the `stage` it is in — the card
  reads `Cluster: … · Step: … · Wave 2 · Stage gates`; both go through `esc()`. A record from
  before the fields renders as it did.

### Fixed
- **The swarm view draws the plan the run executed, not a flat one it made up** (keel #1275,
  #1280 item 1). `keel-visual swarm` rebuilt a plan from the run's workers, with no predicted
  files, so no two issues conflicted and every run drew as one wave with no dependencies. It now
  reads the plan `keel swarm-run` persists as `.keel/state/swarm/<id>.plan.json`
  (`schema: keel.swarm-plan`, `version: 1`) and draws its real waves — `Dependent — Refused`
  where the plan is `sequential_dependent` — with each cluster's issue titles, scope sources,
  predicted files, dependencies (and the wave each waits on), difficulty band and implementer,
  every value through `esc()`. A run with no plan file, or one that is not JSON, not a
  `keel.swarm-plan`, of another schema version or another run's, shows a sentence saying so
  instead of a DAG; the worker matrix still shows the run. With a core that has
  `keel.swarm.swarm_plan_from_payload` the plan goes through the same strict reader
  `swarm-land` uses; with an older core (the declared floor is `keel-workflow>=1.15.0`) a local
  check of the documented format stands in. `--json` now carries `plan` (or `null`),
  `plan_status` (`persisted`/`missing`/`unreadable`) and `plan_detail`.
- **The newest swarm run is the newest state file, not the newest `*.json`** (keel #1275).
  A `<id>.plan.json` written after its state became "the newest run", named `<id>.plan`, and
  drew nothing; run discovery now skips plan files, as core's `latest_swarm_id` does.
- **The swarm view says a dependent wave is refused, not funneled** (keel #1276). A wave that
  is not eligible for direct landing was labelled `Sequential Funnel`, and the landing-mode tile
  read `Adaptive Funnel`, but `keel swarm-land` refuses such a wave until the earlier wave lands
  and the rest is re-planned. The wave now reads `Dependent — Refused` and the tile
  `Direct Batch · Dependent Refused`; tests fail if the template says "funnel" again.
- **The PyPI page's links and screenshots work** (#1371). The README is the PyPI long
  description, and its eight relative targets (`../README.md`, `../docs/keel/swarm.md`,
  `RELEASING.md`, `screenshots/…`) resolved against pypi.org, so the links were dead and the
  board screenshots did not load. They are absolute GitHub URLs now, and a test fails on a
  relative one or on an absolute link to a file that no longer exists.
- **A release cannot publish a version its tag does not name** (#1371). `publish-visual.yml`
  now runs `scripts/release_check.py --package keel-visual --tag "$TAG"` before building; with
  `skip-existing: true`, a `keel-visual-v*` tag that disagreed with `pyproject.toml` used to
  skip the upload, or publish a different version, without an error.
- **The wheel names every template it ships** (#1371). `force-include` listed three of the
  four templates (not `swarm.html`, which shipped only because the package directory is
  included), and the workflow checked `runviz.html` alone. All four are listed, the workflow
  checks every template in the source tree, and `RELEASING.md`'s manual check does the same.

## [0.9.0] — 2026-09-28

- `keel-visual swarm` is in the published package: render a swarm run's waves, clusters and
  workers as an HTML page, or serve it on localhost.
- The live board no longer shows abandoned runs as running, and run data that contains
  markup is shown as text instead of running in the page.

### Added
- **`keel-visual swarm`** (#721, #727). Renders a swarm run as a 2D DAG of its waves and
  clusters, a pseudo-3D multi-wave topology (drag to rotate, scroll to zoom) and a worker
  matrix with each cluster's state (`queued`/`running`/`passed`/`failed`/`merged`/`held`),
  role and recorded details. It reads the latest run under `.keel/state/swarm/` (or
  `--swarm-id ID`), writes `--out FILE` (default `keel-swarm.html`), serves the page with
  `--serve [--host] [--port]` (default `127.0.0.1:8766`), prints the resolved data with
  `--json`, and renders an offline fixture with `--swarm-json FILE`. The page is a snapshot of
  the run state; re-run it to refresh. The documented usage is in keel's
  `docs/keel/swarm.md` §5 and `docs/keel/cli.md`.

### Changed
- **keel-visual now needs keel core ≥ 1.15.0** (was ≥ 1.6.0). `keel-visual swarm` imports
  `keel.swarm`, which first shipped in `keel-workflow` 1.15.0; against 1.14.2 the command
  died with `ImportError: cannot import name 'swarm' from 'keel'`. Measured: the 0.9.0 wheel's
  suite passes against `keel-workflow` 1.15.0 and 1.24.2 from PyPI and fails on the swarm
  tests against 1.14.2. The other commands still work on older cores, but the dependency now
  states what the whole package needs.

### Fixed
- **Abandoned runs render as stale, never as running** (#823, #824). A session that died
  without stamping `keel activity --done` left a record that the board drew exactly like a
  live run. The record file's modification time is now the last-seen signal: a run that is
  neither done nor merged and was last seen more than six hours ago renders as grey
  "stale · last seen 63d ago", leaves the active view and sorts with the finished runs. The
  web dashboard also shows "loading runs…" at once instead of a blank page until the first
  `/board.json` arrives. A long-lived `serve` keeps running the code it started with, so
  restart it after upgrading.
- **`keel_visual.__version__` names the installed version** (#796, #797). It said `0.6.0`
  through the 0.7.0 and 0.8.0 releases. Both version markers are now written by
  `scripts/release_bump.py --package keel-visual`, and a test fails if they disagree.
- **`keel-visual dash` no longer crashes on a Windows console** (#953, #954). The board's
  box-drawing characters and status glyphs raised `UnicodeEncodeError` under the cp1252
  console default before anything printed; the output stream is now switched to UTF-8, and a
  glyph a terminal cannot draw shows as a placeholder.
- **A swarm state file of the wrong shape no longer kills `keel-visual swarm`** (#1273,
  #1307). A document that is not an object, a worker that is not an object, or an issue that
  is not a whole number (including `1e999`) is skipped, and the rest of the run is drawn.

### Security
- **The swarm page escapes every value it interpolates.** `swarm.html` wrote worker
  details, roles, statuses, cluster ids, steps, issue numbers and scope paths into
  `innerHTML` unescaped, so markup in a failed worker's details ran when the page was
  opened. They now pass through `esc()`, and a status used as a CSS class must be one
  plain token or falls back to `queued`. `runviz.html` escapes its meta chips and step
  labels the same way (defence in depth: `runstate` already normalises them).

## [0.8.0] — 2026-08-14

### Added
- **Blocked step rendering on failed gates.** When a gate failure occurs (e.g. at `s8`),
  the terminal board and HTML run view now render the step as `blocked` (red `.fail`
  node in runviz and dedicated glyph in terminal) rather than remaining indefinitely
  in an in-progress/running state (#636).
- **Distinct TIMEOUT outcome glyph.** Surfaced timed-out gate executions with a distinct
  hourglass glyph (`⧗`) to distinguish machine/network timeouts from actual test
  assertion failures (#622).

## [0.7.0] — 2026-07-17

### Added
- **Unread ledger fields surfaced end-to-end.** `build_run_state()` now projects
  `tier`, `window_open`/`bypassed_window`, the named `gates[]` outcomes, `reviewers`,
  `tester`, `host_agent`, `merge_reason`, and `file_count` from the ledger `ship_run`
  record keel already writes (fail-soft, no new core field). Terminal `play` shows a
  tier badge (`[T3]`) and merge-window tag (`[win:open]`/`[win:closed]`/`[win:bypassed]`)
  in the header plus a compact named-gate strip (`gates: build✓ evidence✗ jury–`) under
  the flow; the web dashboard drawer gains tier/window chips, a per-gate status list,
  and an agents row (implementer / reviewers / tester / host). (#575)
- **Jury verdict, not just jury mode.** When ai-jury's gate runs, keel's s8 writes the
  machine-readable report to `.keel/state/jury/<run-id>.json`; keel-visual reads it (never
  imports ai-jury) and shows the actual verdict — `APPROVE` / `COMMENT` / `REQUEST CHANGES`
  — with a per-severity count, in the terminal header chip and the web drawer. The mapping
  mirrors ai-jury's default CI gate (only *verified* critical/major blocks). Fail-soft: an
  absent or malformed file reproduces the previous mode-only display. (#576)

### Tests
- **JS-level tests for the web templates.** A zero-dependency `node --test` suite
  (`tests/js/`) drives each template's inline script in a `vm` context against a stub DOM,
  pinning payload parsing, board filtering, theme cycling, the runviz panels, the live-poll
  re-render, the drawer field guards, and the 3D style picker — run in CI via a Python
  bridge that skips cleanly when node is absent. (#577)

## [0.6.0] — 2026-07-03

### Added
- **Richer, larger board cards (web).** Each card now shows a status-icon chip, the live
  **issue/PR title**, the **branch → base**, and the **author**. Branch / base / author are
  read from the ledger `ship_run` record keel already writes (fixed per run, never stale —
  no new core field); titles are fetched **live** from `gh` and cached per process (a renamed
  issue/PR reflects on next start). Best-effort throughout: an activity-only run, or a machine
  without `gh`, simply shows the number(s) — the card never blanks. Both the live `serve` and
  static `render` boards. (#505)

### Changed
- **Board label shows both issue and PR.** A run carrying both now reads `#<issue>→#<PR>`
  (e.g. `#500→#501`) instead of only the PR, so rows are unambiguous; `issue` and `pr` are
  also exposed as separate board-entry fields for the web drawer. (#503)

## [0.5.9] — 2026-06-16

### Added
- **Confirmed merges read as green `merged`, not muted `done`.** An activity record
  stamped `merged` (by core ≥ 1.6.5's `keel merge`) sets `run_state["merged"]`, so the
  board shows a real merge in green instead of folding it into the closed-out `done` tone.

### Changed
- **Auto-stamped runs are labelled by their issue/PR number.** A run whose id is its
  command name followed by the number (`ship-585`, `pr-loop-2253`) now shows `#585` /
  `#2253` even when no explicit `--issue` reached the record — the per-phase backbone
  stamps don't all carry one. Opaque counters (a `morning` run's `m-1`) stay raw.

## [0.5.8] — 2026-06-16

### Changed
- **Particle 3D styles are now light/dark aware.** plexus/aurora/comet used additive
  blending on a forced-dark scene (invisible on light). They now switch to normal
  blending on a light scene in light mode (and keep the additive glow on dark), so the
  whole 3D view follows the theme like the geometric styles already did. The open scene
  rebuilds when you toggle the theme.

### Added
- **Particle 3D styles in the drawer.** The drawer's 3D style picker now also offers
  runviz's interweaving particle scenes — **plexus** (a flowing node web), **aurora**
  (strand ribbons), and **comet** (orbiting trails) — alongside the geometric
  `curve · helix · ring · line`. The 3D engine was unified onto runviz's
  build-once / recolor-per-poll / update-per-frame model, so all seven styles share one
  set of live step markers (the active step glows in every style). Particle styles
  render on a dark scene (additive blending) and the geometric styles stay on the
  theme-aware transparent scene; switching flips the background automatically.

## [0.5.6] — 2026-06-16

### Added
- **A 3D-style switch inside the drawer's 3D view.** The per-run 3D scene now has a
  `curve · helix · ring · line` picker — four distinct 3D arrangements of the same step
  markers (the active step glows in each): the rising **curve**, a **helix** spiral, a
  **ring**, and a flat **line**. The choice is remembered in `localStorage` and the
  scene re-frames itself for whichever layout you pick. (runviz's particle styles —
  plexus/aurora/comet — remain a heavier optional port for a future release.)
- **Zoom in the drawer 3D** — scroll to zoom on desktop, pinch on touch (dolly, clamped).
- **command / phase / status under the 3D too.** The run meta table moved out of the 2D
  body into a shared footer, so it shows under both the 2D step list and the 3D scene.

## [0.5.5] — 2026-06-16

### Added
- **Per-run 3D scene inside the live dashboard drawer.** Clicking a run on the `serve`
  dashboard now opens a detail drawer with a **2D / 3D** switch. 2D is the step list;
  3D is a live WebGL scene of that one run's backbone as a gently rising curve — done
  steps green, the active step a pulsing glow, gates amber, idle steps muted — that you
  can drag to orbit. THREE.js is **lazy-loaded** only on the first 3D switch (the base
  dashboard stays dependency-free), the scene tracks the 0.5s poll, re-themes with the
  light/dark toggle, and the renderer + animation loop are disposed when the drawer
  closes. Falls back to "3D unavailable" if THREE can't load (offline).

## [0.5.4] — 2026-06-16

### Added
- **Filter box on the live dashboard.** A search field in the `serve` dashboard header
  filters runs as you type — by project, label (`#PR`/issue/run-id), or command — and
  composes with the all/active toggle. Useful when many projects' runs share the board.
- **Favicon on the board pages.** The `serve` dashboard and the `render` board now
  carry the keel mark as an inline SVG favicon, so the browser tab shows the brand
  glyph instead of a blank icon (self-contained data URI — no extra file to serve).

### Fixed
- **A finished run is no longer mislabelled "merged".** The board mapped a `done`
  activity record straight to `merged`, so any closed-out run — a `morning`/`triage`
  that never merges, or a `ship`/`pr-loop` that **deferred** its merge to the next
  window — showed a green "merged" badge it hadn't earned. A finished-but-not-merged
  run is now a distinct **"done"** state: it still fades/filters like a completed run,
  but carries a muted "done" badge, and only a real merge (checkpoint/ledger) shows
  the green "merged". Applies to the live `serve` dashboard and the `render` board
  (2D + 3D).

### Changed
- **Board de-duplicates a ship run's activity record against its checkpoint.** As of
  keel 1.6.3 the `ship` adapter stamps the activity channel too (so agent-driven ship
  runs reliably show live). A worktree that has both a ship checkpoint and a ship
  activity record for the same run now lists that run once — keyed by the shared
  run-id — preferring the checkpoint's richer detail (merge gate, jury, test gate).

## [0.5.3] — 2026-06-16

### Fixed
- **Dashboard step labels no longer overlap on custom flows.** In the `serve`
  detail drawer the monospace step id sat in a fixed 34px box sized for the ship
  ids (`s0`–`s12`); a custom command whose step ids are full words (`config`,
  `enrichment`, …) overflowed that box and painted on top of the step name. The id
  column now sizes to its content, and where a flow's id *is* its name the
  redundant id is dropped so each step shows a single clean label. The same
  `id · name` de-duplication is applied to the card footer, the drawer phase row,
  and the step tooltip.

## [0.5.2] — 2026-06-16

### Added
- **Rich step tooltips.** Hovering a step dot now shows a detailed card — the step
  id + name, a `gate`/`merge` kind chip, the live status (in progress / gate / blocked /
  done / not reached), and a one-line description (the s0–s12 backbone has full
  descriptions; other commands show the phase position). Replaces the bare title.
- **Manual light/dark toggle.** A header button cycles **system → light → dark**
  (remembered in `localStorage`), overriding `prefers-color-scheme`. On the board the
  3D scene re-themes on switch. Applies to the board (`render --all`) and the live
  `serve` dashboard.

### Fixed
- The dashboard drawer's drop-shadow no longer bleeds onto the page when it's closed.

## [0.5.1] — 2026-06-16

### Added
- **Hover a step dot to see which step it is.** The compact step strips on the
  board/dashboard cards now show a styled tooltip (`sN · name`, e.g. `s8 · test`)
  on hover — the dots were unlabeled before. Applies to both `render --all` and the
  live `serve` dashboard.

## [0.5.0] — 2026-06-15

### Added
- **`keel-visual serve` — a live web dashboard.** Unlike `render --all` (a static
  snapshot), `serve` runs a tiny localhost HTTP server: it serves the dashboard once
  and a `/board.json` endpoint that **re-reads the records on every request**, so the
  page polls (every 0.5s) and updates itself. Open it on the side and watch ship +
  non-ship runs appear and advance live. Click a run to open a closable right-side
  **detail drawer** (the run's step flow + metadata; full-screen on mobile). `--all`
  spans every project under a parent; localhost-only by default (`--host`/`--port`).

## [0.4.2] — 2026-06-15

### Changed
- **The web board adapts to the screen.** Wider cap (`min(1680px, 100%)`) so wide
  monitors fill with more columns instead of empty gutters; padding scales down on
  small screens (`clamp(14px, 2.4vw, 30px)`); the grid drops to a single column on
  narrow/mobile widths without cards overflowing (`minmax(min(330px, 100%), 1fr)`);
  the 3D scene grows taller on large screens (`clamp(360px, 68vh, 780px)`).

## [0.4.1] — 2026-06-15

### Fixed
- **Activity records in worktrees were missed.** Agents run non-ship commands in
  their own git worktree and stamp `.keel/activity/` there — but the board read
  the activity channel only from the project root, so those runs never appeared.
  Read activity from **every worktree** (exactly like checkpoints), not just the
  root.

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
