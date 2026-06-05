# keel CLI reference

```
keel <command> [options]
keel --version
```

## `keel version`

Print the keel version.

## `keel validate <project.yaml…> [--root DIR]`

Validate one or more project configs against the bundled schema. Reports `OK` /
`INVALID` / `MISSING` per file; exits non-zero if any file is invalid.

With `--root DIR`, also **strict-validates the extensions** the config references
(resolved under `DIR/<extensions_dir>`): a missing or malformed extension fails the file.
Without `--root`, only the config schema is checked.

```bash
keel validate projects/*.yaml                 # schema only
keel validate .claude/project.yaml --root .   # schema + extensions (use in CI)
```

## `keel plan <project.yaml> [--root DIR]`

Render the backbone plan for a project: the fixed steps with the project's built-in gates
and extensions slotted in. This is the dry-run view — what an actual run would execute.

`--root DIR` (default `.`) is where extension files are resolved. Extensions that can't be
loaded are reported as warnings on stderr (fail-soft) and the plan still renders with the
built-in gates.

```bash
keel plan .claude/project.yaml
```

Example output:

```
keel plan — example-flutter
  base_branch: main   core_version: ^0.1
  backbone:
     s0  config
     ...
     s8  test
           - gate: build
           - gate: lint
           - gate: design-parity
    s10  merge
           - gate: design-parity-gate
    ...
```

## `keel run-gates <project.yaml> [--root DIR]`

Run the project's **command gates** (the `command`/`build`/`lint` Lego) under `--root DIR`
(default `.`) and report each as a structured finding. Agentic gates (review, design
parity) are not run here — this is the deterministic, runnable slice of the test step (s8).

Each gate runs its configured shell command; a non-zero exit becomes a blocking finding
(`gate:<name>`), a zero exit a pass. The command's output tail is captured for context.

If `gates:` includes **`jury`** and the [ai-jury](https://github.com/berkayturanci/ai-jury)
`jury` CLI is installed, the jury gate runs it on the diff (`git diff base...HEAD`) and maps
its findings (file/line/severity) into keel findings (critical/major block). If `jury` is
not installed the gate is a **fail-soft no-op** — the flow runs with or without jury.

```bash
keel run-gates .keel/project.yaml --root .
```

Exits non-zero if any gate blocks (so it can be wired straight into CI).

## `keel window <project.yaml>`

Report whether the project's **merge window** is open right now, in the project's
timezone. The window (e.g. `07:00-01:30`) is the *open* window; its complement is the
night no-merge window. A window may wrap midnight. Prints `OPEN` / `CLOSED` and the
`timezone merge_window` it evaluated; prints a notice (and exits 0) if the project sets no
window.

```bash
keel window .keel/project.yaml
# merge window OPEN  [Europe/Istanbul 07:00-01:30]
```

## `keel ship <project.yaml> [--root DIR] [--pr N]`

Run the **deterministic slice of a ship** against the current checkout and print the
assessment: how many files changed vs. the base branch, the **risk tier** (→ reviewer
count), whether the **merge window** is open, optional **CI** status (`--pr N` reads the
check-rollup via `gh`), each gate's result, and the final **merge decision**
(`MERGE` / `DEFER` / `BLOCK`).

This is the runnable, agent-free part of the backbone (s5 classify + s6 CI + s8 gates +
s10 merge decision). It does **not** call coding agents and does **not** perform the merge —
the live merge (s10) needs a configured runner with `git` + an authenticated `gh`.

```bash
keel ship projects/keel.yaml --root .
# keel ship — keel  (base main)
#   changed files : 53
#   risk tier     : TIER-3  → 3 reviewer(s)
#   merge window  : OPEN
#   ci            : unknown
#   gate build          ok
#   gate lint           ok
#   decision      : MERGE — clear to merge
```

Exits non-zero when the decision is `BLOCK` (failing gates, blocking findings, or failing
CI), so it can gate a runner before it attempts a real merge.

`--hotfix` marks an emergency change so it may merge **outside** the merge window (an audit
line is printed). It never bypasses failing gates, blocking findings, or failing CI.

## `keel init [--root DIR] [--force]`

Scaffold a default `.keel/project.yaml` for the repo. keel detects the stack from marker
files (`pubspec.yaml`→Flutter, `pyproject.toml`/`setup.py`→Python, `package.json`→Node,
`build.gradle*`→Android, else generic) and writes a config that already passes
`keel validate`. Refuses to overwrite an existing config unless `--force`.

```bash
keel init                 # scaffold .keel/project.yaml for the detected stack
keel init --root ../app   # scaffold elsewhere
keel init --wizard        # prompt for base branch, merge-window hours, timezone, commands
```

With `--wizard`, keel prompts for each value (base branch, timezone, **merge window
`HH:MM-HH:MM`**, build/lint commands); press Enter to accept the stack default, or leave a
field blank to skip it. The result still passes `keel validate`.

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | a config was invalid/missing, or a plan target could not be loaded |
| 2 | no command given (help printed) |
