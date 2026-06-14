# keel-visual

An **optional** animated run visualizer for [keel](../README.md). It *renders* a
keel run — it never drives one. keel-visual depends on keel core; core never
depends on keel-visual, so installing it is purely additive.

> Think of it as `keel`'s window: the same ship_run records keel already writes,
> shown as a "where are we" animation — in your terminal or as a web page.

## Two surfaces, one source of truth

Both outputs are fed by a single **pure** adapter,
`runstate.build_run_state(record, …)`, which projects a keel ship_run ledger
record onto the fixed backbone (`keel.model.BACKBONE`). No parallel data model,
no second source of truth — the visualizer shows exactly what the ledger says.

### 1. Terminal — `keel-visual play` (runs in the CLI)

The flow animates right in the terminal while a command runs:

```
keel-visual play .keel/project.yaml --pr 361              # animate the run
keel-visual play .keel/project.yaml --pr 361 --style wave # sine "ribbon" with a light trail
keel-visual play .keel/project.yaml --pr 361 --step 8     # a single frame (e.g. the test gate)
```

- `flow` — a pipeline of `s0…s12` with a playhead, gate colours (amber gate,
  red when blocked), a regression bar, and a "where are we" pointer.
- `wave` — the run drawn on a sine ribbon with a light trail up to the active
  step (the terminal's take on the 3D ribbon).

Colour is `--color auto` (only on a tty), `always`, or `never`.

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

```
pip install keel-visual        # pulls in keel-workflow (core)
```

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
