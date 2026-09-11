---
schema: keel.learning.v1
title: "implement: `knobs.loop` / `--loop` — a bounded, gate-verified s4 iteration loop that composes with `implement_mode: tdd`"
description: "An s4 implementer gets exactly one pass. When the gates come back red after it, nothing in the backbone sends the work back to the seat that wrote it:"
repo: keel
pr: 1171
issue: 1165
date: "2026-09-11"
fingerprint: "107c017e98e676b43ded5a7651d61aaaaf6a97f2824789d6d15e38a039d4f072"
labels:
  - "type:enhancement"
  - "status:backlog"
  - "priority:medium"
  - "role:core"
changed_files:
  - ".agents/skills/keel-ship/SKILL.md"
  - ".claude/commands/keel/ship.md"
  - CHANGELOG.md
  - README.md
  - commands/ship.md
  - docs/keel/cli.md
  - docs/keel/command-contracts.md
  - docs/keel/commands.md
  - docs/keel/comparison.md
  - docs/keel/configuration.md
  - docs/keel/parameter-reference.md
  - src/keel/adapters/commands/ship.md
  - src/keel/capture.py
  - src/keel/cli.py
  - src/keel/closure.py
  - src/keel/config.py
  - src/keel/contracts.py
  - src/keel/ledger.py
  - src/keel/loop.py
  - src/keel/schema/project.schema.json
  - tests/test_capture_learning_sink.py
  - tests/test_cli.py
  - tests/test_closure.py
  - tests/test_config.py
  - tests/test_contracts.py
  - tests/test_ledger.py
  - tests/test_loop.py
  - tests/test_tdd.py
  - website/params.js
---

# implement: `knobs.loop` / `--loop` — a bounded, gate-verified s4 iteration loop that composes with `implement_mode: tdd`

An s4 implementer gets exactly one pass. When the gates come back red after it, nothing in the backbone sends the work back to the seat that wrote it:

## What changed

## Problem

An s4 implementer gets exactly one pass. When the gates come back red after it, nothing in the backbone sends the work back to the seat that wrote it:

- s9's fix loop is for **review findings** — `keel fixloop brief` reads a findings file, not gate output, and its ladder (`implementer → gate → host`) escalates on a *reviewer's* verdict.
- s6's CI retry budget is for a **pushed** branch: three fix-and-push rounds after a PR exists, each one spending CI minutes and a reviewer's attention on what is often a compile error.

So a red `make test` after a single implement pass falls to the host — "quietly fix it yourself" is exactly the failure s9 names — or the issue is marked blocked after a change that was two iterations from green. Observed on keel's own runs with flash-class implementers: the diff is right in shape and wrong in one detail the suite names precisely.

The industry answer is the Ralph loop ([Ralph Loop plugin](https://claude.com/plugins/ralph-loop), [`anthropics/claude-code` `plugins/ralph-wiggum`](https://github.com/anthropics/claude-code/blob/main/plugins/ralph-wiggum/README.md)): re-feed the same prompt until the agent emits a completion promise. It works because the prompt stays fixed while the codebase and the test output change under it. It has two defects keel cannot accept:

1. **The completion criterion is the model's own claim** (`--completion-promise "DONE"`). That is the promise-versus-verification gap the `tdd-order` gate (#1020) was built to close, reopened one step earlier.
2. **It is a host stop hook.** It exists only for Claude Code, only inside one session, and leaves no record — nothing says how many iterations ran, what each changed, or what the gates said between them.

keel needs the iteration, with the gates as the judge, bounded, recorded, and host-agnostic.

## Proposal

`knobs.loop` (and `--loop` for one run): an s4 **iteration policy** that wraps the implement pass. It is not a third `implement_mode` value, because it composes with both existing profiles — the best use of a loop is against tests the implementer has to satisfy, which is `implement_mode: tdd` phase B. Backbone step ids do not change; every step other than s4 behaves identically.

```yaml
knobs:
  build_gate_cmd: "make test"
  implement_mode: tdd          # optional; the loop composes with default and tdd alike
  loop:
    enabled: true
    max_iterations: 3          # 1..10, default 3; 1 is today's single pass
    gate_output_max_bytes: 16384
```

### The contract

- **Iteration 1 is the ordinary implement pass.** After each iteration the orchestrator runs the project's **command** gates (`build`, `lint`, and any `tester`/`test` Lego of kind `command`) inside the worktree with the existing `keel run-gates … --phase s8 --json`. Green ends the loop. Red starts iteration k+1, up to `max_iterations`.
- **The completion criterion is the gate run, never the implementer's text.** A delegate result that says it is done with red gates is iteration k *failing*, not the loop *ending*. A loop whose budget is spent with the gates still red is `status: budget-exhausted`, exits non-zero, and blocks the issue — the same exit shape `keel fixloop brief` uses, so a spent loop cannot be mistaken for a round to run.
- **The brief is fixed; the evidence changes.** Iteration k+1 receives the same brief as iteration 1 plus one appended section, **Gate output from iteration k**, rendered by core from the `GateOutcome` list (gate id, `ok`, `not_run`, the finding text, truncated to `gate_output_max_bytes` with a visible marker). The implementer never composes its own summary of what failed, and gate text is quoted data exactly as reviewer text is in the s9 brief — it cannot contribute a heading, a marker or a trailer to the prompt.
- **Same seat for every iteration.** The loop re-dispatches `assignment.implementer`; it never escalates. Escalation is s9's ladder and stays there. The s4 retry/fall-back rules are unchanged and apply per iteration (retry at most twice on an unapplicable diff, never retry `rate-limit`).
- **One commit per iteration.** Each iteration ends with a commit whose subject core renders (`loop(k/N): <issue title>`), so the ledger and the closure can point at what each iteration changed. Squashing iterations locally destroys the evidence the record reads; s10 squash-merges the PR as always, so the base branch history is unaffected.
- **Composes with `tdd`.** Under `implement_mode: tdd` the loop wraps **phase B only**. Phase A (the failing tests) is one commit and never iterates: its red gate run is its proof, not a failure. The `tdd-order` gate already accepts several implementation commits after the tests commit — it reads the *first* one — so a looped tdd branch passes it unchanged. Under `implement_mode: default` the loop wraps the single implement pass.
- **Bounded and priced by what already exists.** `max_iterations` bounds the count. Every `keel delegate run` result already carries `duration_s` and the run id, and `keel cost-report` reads them, so a loop costs what its iterations' delegate records say. No new budget mechanism.
- **A `dry-run` loop runs iteration 1 only** and logs the policy it would have applied.

### What core owns (pure)

A new `src/keel/loop.py`, pure and deterministic like `keel.tdd` — no I/O, no wall clock, no keel imports at module scope beyond the gates vocabulary:

- `resolve(knobs_loop, *, flag)` → `LoopPolicy(enabled, max_iterations, gate_output_max_bytes, source)`; precedence `--loop` > `knobs.loop.enabled` > off. There is **no `--no-loop`**, for the reason there is no `--no-tdd` (#1020): a project that configured the contract has said the contract is the policy.
- `decide(iteration, outcomes, policy)` → `LoopDecision(status: continue|done|budget-exhausted, iteration, budget, blocking: [gate ids])`. `done` requires every gate `ok` and no *blocking* gate `not_run`; a soft gate that failed does not keep the loop running (it never held a merge either).
- `render_iteration_brief(base_brief, iteration, outcomes, policy)` → the next prompt, byte-stable for identical inputs, quoted-data section, truncation marker.
- `iteration_records(pairs)` → the ledger shape (below).

### CLI surface

- `keel loop brief --project .keel/project.yaml --root . --iteration <k> --brief <base-brief> --gates <run-gates.json> --out <next-brief> --json` — renders iteration k+1's brief and prints the decision. Exit 0 on `continue`/`done`, non-zero on `budget-exhausted`, non-zero with `no-config` when the project cannot be read (the `fixloop brief` rule: never guess a policy).
- `keel ship … --loop` and `keel plan … --loop` select the policy for one run; `keel run-gates` needs no new flag.
- `keel ship --live --append-ledger … --loop-iteration <k>=<sha>:<pass|fail>` (repeatable) records what ran.

### Contract and records

- `keel plan` / `keel ship --json` publish `contract.implement_mode.loop` — `{enabled, max_iterations, gate_output_max_bytes, source, wraps: "implement" | "implementation"}` (`wraps` says which phase the loop is around). A project with neither the knob nor the flag publishes `{enabled: false, …}` and its `config_hash` does not rotate; `knobs.loop` is omitted from the canonical config when disabled, exactly as `implement_mode: default` is.
- The ledger's `run_context` gains `implement_loop: [{iteration, commit, gates_ok, implementer}]`. Emit-only, like `implement_phases`: keel records what the orchestrator reports.
- The closure comment renders **`Implement: loop (3/3 iterations: a1b2c3d red → e4f5a6b red → 9c8d7e6 green)`** for a loop run, and one line for a tdd+loop run (`Implement: TDD (tests <sha> …) · loop (k/N …)`). A run without the loop renders exactly what it renders today.

### Adapter (`/keel:ship`)

`src/keel/adapters/commands/ship.md` gains a **Looped s4 (`knobs.loop` / `--loop`)** section beside **Test-first s4**: the per-iteration `run-gates` call, the `keel loop brief` call, the one-commit-per-iteration rule, the "the gate run decides, not the delegate's text" rule, the phase-B-only rule under tdd, and the budget-exhausted exit. `make plugin` regenerates the six surfaces.

### Why not …

- **… a host stop hook?** keel runs inside Claude Code, Codex, Gemini CLI and Antigravity through one backbone; a hook exists in one of them. And a hook loop leaves no record — the ledger and closure lines above are the point.
- **… s9?** s9 is post-PR, post-review, with a seat ladder. The s4 loop is pre-push: no PR, no reviewer, no CI minutes spent on a compile error. Folding them would spend review budget on what `make test` already said.
- **… `implement_mode: loop`?** It would make `tdd` and `loop` mutually exclusive, and the tdd+loop combination is the one Ralph's own guidance recommends ("give it tests to satisfy").

## Not in this change

- A wall-clock or token budget of its own. `max_iterations` plus the delegate `--timeout` bound it; cost is read off the existing delegate records.
- Iterating on reviewer findings (s9) or CI failures (s6). Both keep their own budgets.
- Running the loop *for* a host subagent implementer (`kind: subagent`): the adapter runs the iteration under the subagent the same way it runs a single pass; core's part is identical.
- Any change to `keel.tdd` or the `tdd-order` gate.

## Acceptance

- `keel validate` accepts the block above and refuses `max_iterations: 0`, `11`, a non-integer, and an unknown key under `knobs.loop` (`unknown property`).
- `keel plan`/`keel ship --json` publish `contract.implement_mode.loop` with the shape above; a project without the knob or flag publishes `enabled: false` and its `config_hash` is unchanged from 1.22.0.
- `keel loop brief` is a pure function of its inputs: the rendered brief is byte-identical for identical inputs (snapshot test), gate output is truncated at `gate_output_max_bytes` with a marker, a `done` decision is returned only when every gate is `ok` and no blocking gate is `not_run`, and `budget-exhausted` exits non-zero.
- Gate text in the rendered brief is quoted data: a finding containing `## Heading`, a `keel.review-verdict.v1` marker or a `Co-Authored-By:` line appears only inside the quoted block (test).
- `keel ship --live --append-ledger --loop-iteration …` records `run_context.implement_loop`; the closure comment renders the loop line for a loop run and is byte-identical to today's for every other run (existing closure snapshot tests unchanged).
- Under `implement_mode: tdd` + loop, `tdd-order` passes on a branch with one tests commit followed by three implementation commits (test through `keel.tdd.check_order` with a looped commit list).
- `src/keel/loop.py` is at 100 % line + branch; `tests/test_loop.py` mirrors it; `tests/test_cli.py` covers the new flags and the `loop brief` exits.
- `ship.md` carries the section; `make plugin` output is committed; `tests/test_documented_commands.py` and `tests/test_parity_matrix.py` pass.

## Docs impact

`docs/keel/configuration.md` (`knobs.loop`, beside `implement_mode`), `docs/keel/cli.md` (`keel loop brief`, `--loop`, `--loop-iteration`), `docs/keel/commands.md` (the `/keel:ship` row), `docs/keel/command-contracts.md` (`contract.implement_mode.loop`), `docs/keel/parameter-reference.md`, `README.md` (one bullet beside the test-first one), `CHANGELOG.md`.

## Tests

`tests/test_loop.py` (new), `tests/test_cli.py`, `tests/test_gates.py`, `tests/test_ship.py`, `tests/test_closure.py`, `tests/test_config.py`, `tests/test_documented_commands.py`.

## Relates to

- #1020 — `implement_mode: tdd`, the s4 profile this composes with.
- #1016 — `keel fixloop brief`, whose brief-and-decision shape this mirrors.
- The comparison entry for the Ralph loop lands in the docs issue opened alongside this one.

## What we learned

Gates on the merged head — build: ok, lint: ok, bandit: ok

## What to do differently next time

Recorded automatically from the run. Edit this file to say what the next run should do differently; the read path scores on its text.

## Files

- [.agents/skills/keel-ship/SKILL.md](../../.agents/skills/keel-ship/SKILL.md)
- [.claude/commands/keel/ship.md](../../.claude/commands/keel/ship.md)
- [CHANGELOG.md](../../CHANGELOG.md)
- [README.md](../../README.md)
- [commands/ship.md](../../commands/ship.md)
- [docs/keel/cli.md](../../docs/keel/cli.md)
- [docs/keel/command-contracts.md](../../docs/keel/command-contracts.md)
- [docs/keel/commands.md](../../docs/keel/commands.md)
- [docs/keel/comparison.md](../../docs/keel/comparison.md)
- [docs/keel/configuration.md](../../docs/keel/configuration.md)
- [docs/keel/parameter-reference.md](../../docs/keel/parameter-reference.md)
- [src/keel/adapters/commands/ship.md](../../src/keel/adapters/commands/ship.md)
- [src/keel/capture.py](../../src/keel/capture.py)
- [src/keel/cli.py](../../src/keel/cli.py)
- [src/keel/closure.py](../../src/keel/closure.py)
- [src/keel/config.py](../../src/keel/config.py)
- [src/keel/contracts.py](../../src/keel/contracts.py)
- [src/keel/ledger.py](../../src/keel/ledger.py)
- [src/keel/loop.py](../../src/keel/loop.py)
- [src/keel/schema/project.schema.json](../../src/keel/schema/project.schema.json)
- [tests/test\_capture\_learning\_sink.py](../../tests/test_capture_learning_sink.py)
- [tests/test\_cli.py](../../tests/test_cli.py)
- [tests/test\_closure.py](../../tests/test_closure.py)
- [tests/test\_config.py](../../tests/test_config.py)
- [tests/test\_contracts.py](../../tests/test_contracts.py)
- [tests/test\_ledger.py](../../tests/test_ledger.py)
- [tests/test\_loop.py](../../tests/test_loop.py)
- [tests/test\_tdd.py](../../tests/test_tdd.py)
- [website/params.js](../../website/params.js)
