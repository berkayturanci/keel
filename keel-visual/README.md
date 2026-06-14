# keel-visual

An **optional** animated run visualizer for [keel](../README.md). It *renders* a
keel run — it never drives one. keel-visual depends on keel core; core never
depends on keel-visual, so installing it is purely additive.

> Think of it as `keel`'s window: the same ship_run records keel already writes,
> shown as a "where are we" animation — in your terminal or as a web page.

## Two surfaces, one source of truth

Both outputs are fed by a single **pure** adapter,
`runstate.build_run_state(record, …)`, which projects a keel run onto its
**command flow** — the canonical phase list each command has in keel core
(`keel.flows.flow_for`). No parallel data model, no second source of truth.

## Every command, not just ship

`--command` accepts **all 16 keel commands** (ci-check, coverage, deps-audit,
flake-audit, implement, morning, overnight, pr-loop, regression, review-all-day,
review-cycle, ship, stale-prs, triage, work-block, wrap). Each renders its own
flow — e.g. `overnight` shows `config → preflight → queue → work-block loop →
report`; `triage` shows `find → tier → classify → rank → apply → summary`.

`ship` is the full s0–s12 backbone with live merge/test-gate and regression
detail. The other commands render their phase structure (animatable via
`--step`/`play`); live `--follow`/`dash` position stays accurate for the
checkpoint-writing commands (ship / work-block / overnight).

### Parallel runs — `keel-visual dash`

Running 2-3 `keel ship` (or other) commands at once? Each runs in its own git
worktree with its own `.keel/state/`, so `dash` discovers them all via
`git worktree list` and shows one live board:

```
keel-visual dash .keel/project.yaml          # live board of every active run
keel-visual dash .keel/project.yaml --once   # one snapshot
```

```
keel · 3 active runs
  #351  ████████░░░░  s8  test     gate
  #352  ██████████░░  s10 merge    gate
  #360  ████████████  s12 close    merged
```

A worktree with no live checkpoint is skipped; all per-run reads are fail-soft so
one bad run never blanks the board. See
[`screenshots/dash-board.png`](screenshots/dash-board.png).

### 1. Terminal — `keel-visual play` (runs in the CLI)

The flow animates right in the terminal while a command runs:

```
keel-visual play .keel/project.yaml --pr 361              # animate the run once
keel-visual play .keel/project.yaml --pr 361 --loop       # replay continuously (demo / wall display)
keel-visual play .keel/project.yaml --follow              # LIVE: show where the run is right now
keel-visual play .keel/project.yaml --pr 361 --style wave # sine "ribbon" with a light trail
keel-visual play .keel/project.yaml --pr 361 --step 8     # a single frame (e.g. the test gate)
```

- `flow` — a pipeline of `s0…s12` with a playhead, gate colours (amber gate,
  red when blocked), a regression bar, and a "where are we" pointer.
- `wave` — the run drawn on a sine ribbon with a light trail up to the active
  step (the terminal's take on the 3D ribbon).
- **`--follow`** — live mode: every `--interval` seconds it re-reads the run's
  ledger + checkpoint (`position.current_step` *and* the `state` block, so merge
  progress shows live — pending / merged / failed — not just position) and
  redraws where the run *actually* is now. Point it at a running keel command and
  watch the playhead move in real time. Ctrl-C to stop.
- **`--loop`** — replay the animation continuously (demo / always-on display).

`render` and `play` both pick up the live checkpoint automatically when you
don't pass `--checkpoint-step`. Colour is `--color auto` (only on a tty),
`always`, or `never`. See
[`screenshots/keel-visual-play.gif`](screenshots/keel-visual-play.gif) for the
animation in motion.

### 2. Web — `keel-visual render` (the alternative)

The same run as a single self-contained HTML page with a **2D flow** view and a
**3D flowing-light ribbon** (Three.js): the light runs to where the run is, gates
glow, and reaching merge turns the whole ribbon green.

```
keel-visual render .keel/project.yaml --pr 361 --out keel-run.html
open keel-run.html
```

The page reads its run-state from `window.KEEL_RUN`, and honours
`?mode=2d|3d`, `?step=N`, `?play=1` URL params (used by the screenshot harness).

## Colour language

| colour | meaning |
| --- | --- |
| green | step done · gate passed · merged (run is green) |
| cyan | the active step (where the run is) |
| amber | a gate being evaluated · regression `major` |
| yellow | regression `minor` finding |
| red | a blocked gate · regression `critical` finding |
| dim | a step the run has not reached yet |

## Install

keel-visual needs **keel core ≥ 1.3.0** (it reads `keel.flows`, the ledger, and
the checkpoint). Until both packages are published to PyPI, install from this
repo — installing the repo's core first guarantees a matching version:

```
# from the repo root
pip install ./              # keel-workflow (core), ≥ 1.3.0
pip install ./keel-visual   # keel-visual
keel-visual --help
```

Editable (development):

```
pip install -e ./ -e ./keel-visual
```

Once both are on PyPI, this becomes a one-liner:

```
pipx install keel-visual    # pulls in keel-workflow (core) automatically
```

See [`RELEASING.md`](RELEASING.md) for building and publishing keel-visual.

## Develop

```
python -m pytest                       # or: python -m unittest discover -s tests -t .
python -m coverage run --branch --source=keel_visual -m unittest discover -s tests -t .
python -m coverage report --fail-under=100 --omit="*/templates/*"
ruff check src/keel_visual tests
```

The Python core (`runstate`, `render`, `terminal`, the CLI's pure paths) is held
to **100% line + branch coverage**, matching keel core's bar. The HTML/JS
template is excluded from coverage (it is exercised by the screenshot harness).

## Screenshots

See [`screenshots/`](screenshots/): `terminal-cli.png` (the `play` output),
`2d-s8-test.png` (a blocked test gate), `3d-s10-merge.png` (the 3D ribbon), and
`2d-s12-merged.png` (a merged, all-green run).
