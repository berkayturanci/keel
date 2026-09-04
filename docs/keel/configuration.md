# `project.yaml` reference

A keel consumer holds exactly one `project.yaml` (plus its `.keel/extensions/`).
It is validated against the bundled JSON Schema (`src/keel/schema/project.schema.json`)
by `keel validate`. Unknown keys are rejected, so typos fail loudly.

## How to read this reference

Each field below is validated by the bundled schema. Unknown keys are rejected. Paths and
commands are project-owned values; Keel core reads them to plan, validate, classify, or
preflight work, but project-specific behavior stays in config, extension files, or
project-provided commands.

Required fields are required by schema validation. Optional fields may still be required by
a specific command or extension policy at runtime; those runtime requirements should be
declared through `required_capabilities`, `policy_pack`, or extension docs.

## Top-level fields

| field | type | required | description |
|---|---|---|---|
| `extends` | `"keel"` (const) | ✅ | marks the file as a keel consumer config |
| `core_version` | string | ✅ | pinned keel core range, e.g. `^1.0` |
| `base_branch` | string | ✅ | branch PRs target (`develop`, `main`, …) |
| `knobs` | object | ✅ | per-project values (see below) |
| `owner` | string | | GitHub owner |
| `repo` | string | | GitHub repo |
| `platform` | string | | free-form tag for the consumer's runtime family |
| `timezone` | string | | IANA tz for the merge window (`Europe/Istanbul`, `Etc/GMT-3`) |
| `merge_window` | string `HH:MM-HH:MM` | | open merge window; the complement is the night no-merge window |
| `merge_window_mode` | `freeze` \| `pause` | `freeze` | outside the window: `freeze` blocks the merge but keeps gates/CI running; `pause` halts the pipeline |
| `consent_mode` | `explicit` \| `standing` \| `agent` | `explicit` | default live-run consent mode for every command |
| `gates` | string[] | | built-in gates to run: any of `build`, `lint`, `jury` |
| `extensions` | object | | add-only Lego pieces keyed by named slot |
| `extensions_dir` | string | | dir holding extension files (default `.keel/extensions`) |
| `policy_pack` | object | | durable project-owned policy data (see below) |

### Top-level field details

#### `extends`

Must be `keel`. This is the schema marker that tells tools the file consumes the Keel
backbone.

#### `core_version`

The selected Keel core version range for this consumer, for example `^1.0`. Humans and
adapters use it to keep installed command surfaces aligned with the expected core contract.

#### `owner` and `repo`

Optional GitHub repository coordinates. Commands that read or write GitHub state use these
when present; otherwise they may infer the repository from the local git remote or the
selected GitHub transport.

#### `base_branch`

The branch that implementation work is forked from and PRs target. Ship, wrap, regression,
review-all-day, and CI assessment all use it when computing diffs or deciding whether a
branch is in scope.

#### `platform`

A free-form consumer runtime tag. It is informational and useful in generated plans,
reports, and docs; it must stay generic enough that core behavior does not branch on a
specific product.

#### `timezone` and `merge_window`

`timezone` is an IANA timezone used to evaluate `merge_window`. `merge_window` is the open
merge interval in `HH:MM-HH:MM` format and may wrap midnight. `keel window` and `keel ship`
use both values to decide whether a merge may proceed.

#### `merge_window_mode`

Controls behavior outside the merge window:

- `freeze` keeps non-merge work moving but blocks the merge decision.
- `pause` halts the pipeline outside the window.

If omitted, Keel defaults to `freeze`.

#### `consent_mode`

Default operator-consent mode for live command preflight. The built-in default is
`explicit`, and per-run inputs override it in this order:

1. `--consent-mode explicit|standing|agent`
2. `KEEL_CONSENT_MODE`
3. `consent_mode` in `.keel/project.yaml`
4. built-in `explicit`

Modes:

- `explicit` requires the current run to pass `--approve-scope` for any live mutation
  scopes.
- `standing` allows trusted unattended approval from `KEEL_APPROVE_SCOPE` or
  `automation.approved_scopes`, with an operator identity.
- `agent` delegates prompting/enforcement to the host agent permission model. Keel still
  emits the structured consent contract and delegated scope, but it does not double-prompt
  or fail the preflight for missing `--approve-scope`.

No mode bypasses findings, CI, project gates, merge windows, merge locks, or release
policy. Read-only live contracts do not consume standing approvals, so stale or invalid
standing approval environment values do not break read-only checks.

#### `gates`

Lists built-in gates that should run in the test stage. Current built-in gates are
`build`, `lint`, and `jury`. Unknown gate names are rejected by command execution rather
than treated as project-specific code. Project-specific gates should be declared as
extensions or `policy_pack.test_groups`.

#### `extensions`

Maps a named backbone hook to a list of extension file names under `extensions_dir`.
Extensions are add-only: they can add project gates, prompts, reports, or checks at the
hook, but they must not reorder the backbone.

#### `extensions_dir`

Directory used to resolve extension file names. The default convention is
`.keel/extensions`.

#### `policy_pack`

Durable project-owned policy data. Keel can validate, plan, and expose it in command
contracts, but executable project behavior remains in extension files or project commands.

## `knobs`

| knob | type | required | description |
|---|---|---|---|
| `build_gate_cmd` | string | ✅ | command the `build` gate runs |
| `lint_cmd` | string | | command the `lint` gate runs (gate skipped if absent) |
| `implementer_agents` | map role→agent | | **deprecated** by `team.implement.by_role`: role to local agent mapping (still accepted and mapped onto it) |
| `team.lead` | seat | | seat that coordinates a batch of ships; workers report through it |
| `team.by_difficulty` | map band→bench | | `easy`/`standard`/`hard` → the bench that staffs work of that weight |
| `team.profiles` | map name→bench | | operator-selectable benches, chosen with `--team <name>` |
| `team` | object | | who implements / gates / reviews per role and risk tier, and how the jury gates |
| `delegate_profiles` | map name→profile | | named generic delegate vendors, referenced as `--delegate <name>` |
| `tier3_globs` | string[] | | high-risk paths that force full scrutiny |
| `ci_workflows` | map name→glob | | CI workflow display name → gating path glob |
| `docs_gate_paths` | string[] | | the docs surface: paths that trigger the docs gate |
| `docs_only_allowlist` | string[] | | paths that may ride along in a docs change without forcing code-risk classification |
| `sot_doc` | string | | source-of-truth doc, e.g. `AGENTS.md` |
| `required_capabilities` | string[] | | runtime capabilities that must be present before mutating work starts |
| `optional_capabilities` | string[] | | runtime capabilities that may degrade explicitly when unavailable |
| `evidence_gate_label` | string | | Legacy PR label that also arms the required pre-merge evidence gate (default `keel:ship`); ship provenance now arms the gate by default |
| `evidence_require_distinct_vendors` | boolean | `false` | requires each required review verdict to carry vendor provenance, and no two to share a vendor. **Opt-in: unset is `false` on every risk tier**; set it to `true` on a project whose reviewer bench really spans vendors |
| `swarm_review_evidence` | boolean | | Swarm landings enforce the same per-PR review-evidence contract as ship s10 (default `true`); `false` is the explicit, logged opt-out |
| `implement_mode` | `default` \| `tdd` | | the s4 implement profile: one pass (default), or test-first in two phases with the blocking `tdd-order` gate at s8 |
| `gate_timeout_s` | integer ≥ 1 | | wall-clock seconds a command gate may run before it is killed (default `600`) |
| `jury_timeout_s` | integer ≥ 1 | | wall-clock seconds the `jury` built-in may run before it is killed (default `600`) |

### `knobs` field details

#### `build_gate_cmd`

Command run by the built-in `build` gate. This is required because the build/test gate is
the minimum deterministic project health check.

#### `lint_cmd`

Command run by the built-in `lint` gate. If absent, the lint gate is skipped.

#### `implementer_agents`

**Deprecated by [`team.implement.by_role`](#team).** Map from a role label or project role
to the local implementer agent name. Still accepted: keel maps each value onto a
`team.implement.by_role` seat, reading a value that names a provider keel can resolve as
that provider and anything else as a host subagent (`subagent:<name>`). That ambiguity —
the same field documented as a vendor string here and as a Claude subagent name in
`ship.md` s4 — is why `team` exists. `team.implement` wins where both name a role.

#### `team`

Who runs the ship. `knobs.team` is the whole team a project fields, not just an
implementer: which provider implements (per issue role), which one gives the mandatory
**gate review**, which ones review (per risk tier — or `jury`, when the cross-vendor panel
*is* the review), who applies the findings, and how the jury gates.

```yaml
knobs:
  team:
    implement:
      default: { provider: claude }
      by_role:
        core: { provider: agy, model: gemini-3.8-flash-high, effort: high }
        docs: { provider: "subagent:docs-writer" }
    gate:                                  # one second opinion on every implementation
      provider: codex
      distinct_from: implementer
    review:
      by_tier:
        "1": [{ provider: claude }]
        "2": [{ provider: claude }, { provider: grok-via-openai-compatible }]
        "3": jury                          # the panel is the review (see the caveat below)
    jury:
      mode: gating
      min_vendors: 2
      on_unavailable: fallback       # or `block` — what to do when the panel cannot sit
    fix: { provider: implementer }         # who applies review findings
    lead: { provider: claude }             # coordinates a batch; workers report through it
    by_difficulty:                         # how much work it is -> which bench staffs it
      easy: { implement: { provider: ollama, model: qwen2.5-coder } }
      hard:
        lead: { provider: claude, model: opus }
        implement: { provider: codex, effort: high }
        review: jury
    profiles:                              # operator-selectable benches (--team <name>)
      night-shift:
        implement: { provider: codex, effort: medium }
        review: [{ provider: agy, model: gemini-3.8-pro }]
```

**`review.default` and `review.by_tier`.** `by_tier` names the seats for a specific risk
tier; `default` covers every tier `by_tier` does not name, and takes the same two shapes —
a list of reviewer seats, or `jury`. A tier with neither falls back to the tier-derived
count staffed by the host agent, which is keel's pre-`team` behaviour.

**The gate review is adapter-enforced.** `team.gate` is emitted in the assignment and the
adapter dispatches it; core has no evidence item for it, so nothing in `keel merge` or
`keel evidence-verify` blocks a merge whose gate review never ran. This is the same
emit-only boundary operator consent sits behind (see
[`operator-consent.md`](operator-consent.md)) — the deterministic core does not perform the
dispatch, so it cannot certify it happened.

**`fix` is who applies review findings**, and its default is the alias `implementer`: the
seat s4 actually dispatched, whatever it resolved to. Omitting the block means the same
thing as writing `fix: { provider: implementer }`; name a provider instead to send every
fix round to one seat regardless of who implemented.

s9 does not read this key directly — `keel fixloop brief` does, and escalates from it when
a round fails or the seat is unavailable, along the ladder `fix` → `gate` → the host agent.
A rung repeating an earlier one is dropped rather than dispatched twice, and the
three-round review-fix budget is unaffected: the ladder decides who fixes, not how often.
See [`cli.md`](cli.md) under `keel fixloop brief`.

**Providers.** A `provider` names an entry the same registry `keel delegate run` resolves:
a built-in vendor (`claude`, `codex`, `agy`, `ollama`, `anthropic-api`, `openai-api`,
`google-api`), a [`delegate_profiles`](#delegate_profiles) entry, or a machine-level
`~/.keel/providers.yaml` entry. Two spellings are reserved:

- `subagent:<name>` — a **host (Claude-class) subagent**, never a `keel delegate run`
  dispatch. This is the pre-`team` meaning of an `implementer_agents` value, made explicit.
- `implementer` — *whoever implemented this change*. Valid at `fix.provider` and
  `gate.distinct_from` only.

**Validation** (`keel validate`) rejects a provider name that is neither a built-in vendor
nor a `delegate_profiles` entry nor a `subagent:` name; an `effort` on a provider that has
no spelling for reasoning effort (`claude`, `ollama`, a generic `cli` profile), or on `agy`
without the `model` its suffix-based effort needs; a `gate.provider` equal to a configured
implementer when `gate.distinct_from: implementer`; more than three reviewer seats for a
tier; and a `review` value that is neither seats nor `jury`. A machine-level
`~/.keel/providers.yaml` entry is deliberately **not** consulted: validation must give the
same answer on every machine.

**`"3": jury` means the panel is dispatched once and its ballots *are* the review.** On
such a tier `s7` runs ai-jury and `keel review --from-jury <report.json>` posts one
head-pinned `keel.review-verdict.v1` per panelist — carrying the vendor and model that
produced that ballot — plus the `keel.jury-verdict.v1` consensus record. Host reviewers are
**not** staffed as well: paying for three host readings *and* a four-agent panel over the
same diff, while the panel's ballots reached no gate, is what this policy replaced. The
required verdict count is the panel's own size, declared as `panelists: <N>` on the posted
jury verdict; see [`evidence.md`](evidence.md#4-who-the-reviewers-are-the-bench-or-the-panel).

**Adopt it deliberately — and keel itself has not yet.** A panel tier commits every change
at that tier to a jury run: it has no host reviewer slots to fall back on, and nothing
per-run can take the panel away. keel's own `projects/keel.yaml` therefore keeps three
reviewer seats at tier-3 with `jury.mode: advisory` until `keel review --from-jury` has been
exercised on a real pull request.

Three consequences worth stating plainly:

- **No per-run flag can take the panel away.** `--no-jury` and `--jury-advisory` are
  recorded in `assignment.warnings` and not applied on a panel tier, because removing the
  panel there would leave the tier with *no* required review evidence at all — a stricter
  policy producing a weaker gate. Below a panel tier both flags keep their usual meaning.
- **`jury.mode: advisory` may not be combined with a jury panel.** "The panel is the
  review" and "the panel does not gate" together mean the tier requires nothing, so
  `keel validate` refuses the pair. Note that `jury.mode` is a **single global knob** while
  review panels are **per-tier**, so the refusal is whole-config: "a gating panel at tier-3,
  with the advisory jury that `--jury` would raise at tier-1/2" is rejected even though
  `resolve_jury` would scope the two correctly at run time (a panel tier ignores the mode;
  a non-panel tier applies it). Express that shape as `jury.mode: gating` plus seats at the
  tiers that should not gate, or keep the panel off until `jury.mode` is per-tier.
- **A short panel changes nothing about what the tier owes.** Below `jury.min_vendors`
  participating vendors a jury is downgraded `gating → advisory` only where it sits *beside*
  a host bench. On a panel tier there is no bench behind it, so the downgrade is suppressed:
  the verdict stays gating and required, and every ballot stays required. The shortfall is
  refused by `evidence.panel_vendor_check` as `review-vendor-distinctness` instead — a short
  panel does not get to excuse itself from the consensus record that says it was short. The
  bench does not move with the vendor count either: only `evidence-verify` and `keel merge`
  can read a count off a posted verdict, so a bench that followed it would put two surfaces
  of the same run in disagreement about who reviews. Every review-aware surface *accepts*
  the jury flags — `keel review` included, since #1043 — but nothing makes a run pass them
  to all six, which is the other half of why the bench may not follow them.

### `jury.on_unavailable` — when the panel cannot be staffed here

A panel tier commits every change at that tier to a jury run, and no per-run flag can take
the panel away. That is the right shape while the panel can actually run. When it cannot —
an agent CLI is not installed, is unauthenticated, or the account is out of quota — the
tier would otherwise be simply stuck: the only review it has is one this machine cannot
convene. A single-maintainer project hits that routinely.

So **before s7 dispatches the panel, keel probes it** — the panel s7 would actually
dispatch, which is the `jury` binary and the agents *it* is configured with, not keel's own
delegate list. The probe asks the runner first (`jury --doctor --json`, ai-jury's own
readiness document): that establishes the binary is present and runnable, and reports which
of its agents are usable. For a runner too old to answer, keel falls back to the inventory
`keel doctor --providers` already collects — one `PATH` lookup and one `--version` call per
CLI vendor, an env-var *name* check per hosted API, one loopback request for Ollama — so
keel keeps one answer to "is this provider usable here" instead of two that drift.

The panel is *staffable* when **both** halves hold: the `jury` runner is usable here, and
at least `jury.min_vendors` distinct vendors are available to it. Two entries that shell out
to the same CLI are one vendor and one opinion, exactly as they are everywhere else — and
agent CLIs on `PATH` with no `jury` to convene them are an inventory, not a panel, so a host
with `claude` and `codex` and no ai-jury installed is *not* staffable.

`on_unavailable` is what happens when it is not:

| value | behaviour |
| --- | --- |
| `fallback` *(default)* | Staff a **host bench of the same size the tier requires** — three seats at tier-3, exactly as a tier without a panel resolves — and record why. |
| `block` | Refuse the run, with a message naming each unavailable provider and the reason the probe reported. |

A missing runner is a seat like any other: it is listed first under
`availability.unavailable` as `jury`, so the message an operator reads names the thing to
install rather than sending them to chase a panelist that was never the problem.

`fallback` is the sensible default for a solo project: the panel is the better review when
it is available and should not become a wall when it is not. `block` preserves the strict
behaviour for a project whose product claim *is* cross-vendor review.

**The fallback changes who sat, never how many.** It seats the tier's own reviewer count
and publishes the tier's own required evidence: three `review-verdict-*` items at tier-3,
not two. What it does drop is `jury-verdict`, and it must — there is no panel to produce
one, and requiring it would leave the tier stuck one layer down.

**Nothing about it is silent.** The probe's verdict is recorded in full — which seats were
unavailable and why — and travels with the run:

- `assignment.jury.availability` and `review_merge_contract.jury.availability` carry the
  whole record; `assignment.reviewer_source` reads `jury-fallback` rather than `risk-tier`,
  so a fallback bench is distinguishable from a tier that never had a panel;
- `assignment.warnings` names the unavailable seats in one sentence;
- `availability.runner` says whether the `jury` binary itself was usable, and
  `availability.inventory` says which of the two sources the vendor counts were read from;
- the run ledger records it at `run_context.jury_panel`;
- the closure comment renders a **Jury panel:** line saying the panel was unavailable and a
  host bench reviewed instead, listing the seats. A run whose panel convened, and every run
  of a project with no panel, posts the comment it always did, byte for byte.

That is the point: a reader can tell a jury-reviewed change from a fallback-reviewed one
without re-deriving it. A panel that quietly collapses and still reports success is
[ai-jury #682](https://github.com/berkayturanci/ai-jury/issues/682), and this must not
reintroduce it on keel's side.

**Availability is measured, never asserted.** There is no flag that says "the panel is
fine", and #1014's rule survives intact: what may not take the panel off is an operator's
*preference*. Availability is a fact about the world, and it is allowed to change the
outcome precisely because it is recorded. The consequence is that two machines can resolve
the same tier differently — a CI runner with no agent CLI falls back where a workstation
convenes the panel — and each says which it did rather than either quietly claiming the
other's provenance.

**`config_hash`.** `on_unavailable` is absent from the canonical `team` block when it is
unset, like every other optional field there, so a project that never names the setting
keeps the `config_hash` it had before the setting existed. Writing it explicitly — even as
`fallback`, the value it would default to — is a config change and rotates the hash, which
is the guarantee `knobs.team` has made since #1014: the hash changes *iff* `team` does.

**Tier keys are quoted strings** (`"1"`, `"2"`, `"3"`). YAML reads a bare `1:` as an
integer key, which a JSON schema cannot describe; keel says so instead of accepting it and
meaning something else.

**What it resolves to.** `keel plan --command ship --json` and `keel ship --json` render
the resolved team as `assignment` — `implementer`, `gate`, `reviewers[]` (with per-slot
`provider`/`model`/`effort`), `jury`, `fix`, and a `warnings` list — and the same seats
appear on `review_merge_contract.reviewers.slots`, so any host runs the same team. A tier
whose review policy is `jury` yields empty `slots`, `reviewers.source: "jury"` and a gating
jury: the panel is the review, `reviewers.count` is the number of ballots that must be
posted, and the evidence gate requires that many review verdicts **plus** the jury verdict
as the consensus record.

**A committed policy may only name built-ins and `delegate_profiles`.** Validation does not
consult the machine-level `~/.keel/providers.yaml`, so a registry entry named in
`knobs.team` fails `keel validate` — deliberately: the policy is committed and read by
people whose home directories do not have that entry, and a rule that only holds on its
author's laptop is not a rule. Reach a registry provider **per run** instead, with
`--delegate` / `--review-delegate`, which resolve through the full registry; or promote it
to a `delegate_profiles` entry when the whole team should share it.

**Per-run overrides still win.** `--delegate <provider[:model]>` replaces the implementer;
`--review-delegate` is repeatable and **positional per slot** (first flag = slot A, second
= slot B). `--reviewers N` overrides the seat count, except on a `jury` tier, where it is
reported in `assignment.warnings` rather than silently replacing the panel.

##### `team.lead`, `team.by_difficulty` and `team.profiles` — staffing a batch

The seats above answer *who runs a ship*. These three answer *who runs a **batch** of
ships* — a swarm cluster, a work block, an overnight session.

**`lead`** is the seat that coordinates the batch and that its workers report through: a
swarm spawns one lead per cluster, and the cluster's workers show that lead on
`keel swarm-status`. Unset, the lead is the host agent driving the run.

**`by_difficulty`** keys a bench on how much work something is. A **difficulty band** is
not a risk tier:

| | asks | read from | used for |
| :--- | :--- | :--- | :--- |
| risk tier | how dangerous is this change | `knobs.tier3_globs` + the changed files | how much review it needs |
| difficulty band | how much work is this | tier, predicted file count, `priority:*`/`size:*` labels, dependency depth | which bench is worth spending on it |

A one-line fix to a tier-3 glob is dangerous and trivial; a twelve-file docs migration is
safe and long. Keeping them apart is what lets one backlog say *the hard cluster gets the
strong implementer at high effort, the easy ones go to the cheap local model*.
`keel swarm-plan` scores every cluster and prints the band with the signals that produced
it; the bands are `easy`, `standard` and `hard`, and `keel validate` rejects any other key.

**`profiles`** are benches an operator picks by name with `--team <profile>` on
`work-block`, `overnight` and the `swarm` commands. A profile **outranks** the scored band
— it is the operator naming the bench for this batch — but it does not replace it: each of
`lead`, `implement`, `review` and `effort` resolves down the list on its own, so a profile
that names only reviewers leaves the band's implementer standing.

Resolution, most specific first, for each field independently:

```
--delegate / --review-delegate / --effort   (per-run flags)
  > team.profiles.<--team>                  (operator-named bench)
  > team.by_difficulty.<band>               (scored bench)
  > team.implement.by_role.<role>           (which part of the system)
  > team.implement.default
  > knobs.implementer_agents.<role>         (deprecated)
  > the host agent
```

A bench outranks `by_role` deliberately: the role says *which part of the system this is*,
the band says *what this piece of work costs*, and only the second can express "the hard
ones get the strong implementer". `effort` is the one exception to "most specific first" —
a seat that names both a provider and an effort is one statement, so the seat's own effort
beats the bench's; only the `--effort` flag beats the seat.

A `--team` name with no matching profile is reported in `assignment.warnings` and the run
falls back to the configured policy — it is never silently ignored.

**A child ship inherits the bench.** `--effort` and `--team` are accepted by `keel ship` and
`keel plan`, not only by the batch commands, and a batch hands them to every child. That is
what makes a difficulty bench survive the handoff: the child re-resolves the *same* bench
from the *same* config instead of falling back to the role default. The parent may still
override what the bench chose — `--delegate` and `--review-delegate` on the child's command
line win over any bench, exactly as they do on the parent's.

**A bench `effort` is validated against every seat it could land on.** A seat's own `effort`
sits beside its provider, so an operator reading one line sees both halves. A bench's does
not — it lands on whichever implementer resolves for that band, written somewhere else — so
`keel validate` checks it against each candidate implementer under the same rules: an `agy`
seat needs the `model` its effort suffix rides on, a provider with no effort dial is
refused, and a `subagent:` implementer (which has no dial at all) is refused too. A seat
naming its own `effort` never receives the bench's, so it is not flagged.

`team` participates in `config_hash` only when it is present, so adding the knob does not
rotate the hash for a project that has not adopted it — and `config_hash` changes whenever
`team` does.

**Writing one with the wizard.** `keel init --wizard` / `keel setup --wizard` end with a
team step that builds this block from the [`keel doctor --providers`](cli.md#keel-doctor)
probe, so the seats it writes are providers that exist on the machine doing the
scaffolding. It offers only what a *committed* policy may name — the built-in vendors and
this project's `delegate_profiles`, never a machine-level `~/.keel/providers.yaml` entry —
for the same reason validation refuses one, and it will not offer an `effort` whose
provider has no spelling for it. `keel ship --wizard` picks the same seats for a single
run instead of writing them down; it *does* offer registry providers, because
`--delegate` resolves through the full registry. See
[`cli.md`](cli.md#init-team-step).

#### `delegate_profiles`

Named **generic delegate vendors**, referenced by name as `--delegate <name>` /
`--review-delegate <name>`. Without them every provider is a code change; with them any
local coding-agent CLI is a config entry:

```yaml
knobs:
  delegate_profiles:
    cursor:
      vendor: cli
      command: cursor-agent
      args: ["-p", "--force"]   # implementer: print mode + non-interactive approval
      review_args: ["-p"]       # reviewer: same, minus permission to approve edits
      prompt_mode: arg          # "stdin" (default) | "arg"
      model: null               # optional default model for this profile
      model_arg: --model        # flag the model is passed on (default "--model")
    gemini-cli:
      vendor: cli
      command: gemini
      prompt_mode: arg
```

Then: `/keel:ship 123 --delegate cursor`.

| field | type | required | description |
|---|---|---|---|
| `vendor` | string | ✅ | the generic vendor. Only `cli` today |
| `command` | string | ✅ for `cli` | the executable keel runs, e.g. `cursor-agent` |
| `args` | string[] | | standing flags the command always takes, e.g. `["-p", "--force"]` |
| `review_args` | string[] \| null | | flags for the **reviewer** role; falls back to `args` when unset (`null` ≠ `[]`) |
| `prompt_mode` | `stdin` \| `arg` | | how the prompt reaches the command (default `stdin`) |
| `model` | string \| null | | default model for this profile; a per-run `--delegate <name>:<model>` beats it |
| `model_arg` | string | | flag the model is passed on, as `<model_arg> <model>` (default `--model`) |
| `endpoint` | string | ✅ for `openai-compatible` | the OpenAI-shaped chat-completions URL. Loopback by default |
| `api_key_env` | string | ✅ for `openai-compatible` | the **name** of the env var holding the key — never the key, and only an [allowlisted name](#which-env-vars-may-hold-a-delegate-key) |

<a id="which-env-vars-may-hold-a-delegate-key"></a>
**Which env vars may hold a delegate key.** This field names the variable whose *value*
becomes an `Authorization: Bearer` header sent to the endpoint above, so it is restricted
to variables created for that purpose:

`OPENAI_API_KEY` · `GROQ_API_KEY` · `DEEPSEEK_API_KEY` · `TOGETHER_API_KEY` ·
`OPENROUTER_API_KEY` · `LITELLM_API_KEY` · `VLLM_API_KEY`

For a provider not on that list, name the variable `KEEL_DELEGATE_KEY_<SOMETHING>`.
Prefixing is a deliberate act on a variable you created for this — which is exactly the
property being protected, and one an ambient runner secret does not have. A name outside
both forms is a `keel validate` error, so a config cannot point the header at
`VAULT_TOKEN`, `KUBECONFIG` or `DATABASE_URL`.

**`openai-compatible` reaches any OpenAI-shaped hosted API** — OpenRouter, Groq, DeepSeek,
Together, LiteLLM, a local vLLM — from configuration rather than a code change:

```yaml
knobs:
  delegate_profiles:
    router:
      vendor: openai-compatible
      endpoint: https://openrouter.ai/api/v1/chat/completions
      api_key_env: OPENROUTER_API_KEY     # the NAME, never the key
      model: qwen/qwen-2.5-coder-32b-instruct
```

Then `--delegate router`, or `--delegate router:<model>` to switch model per run.

**The endpoint is loopback-only until you say otherwise.** Every other keel delegate talks
to a hardcoded URL, which is what makes their SSRF story trivial. A config-supplied host
turns `project.yaml` into a request-forgery primitive pointed wherever it says, so:

- `localhost` / `127.0.0.1` / `[::1]` are allowed with no ceremony;
- any other host is a `keel validate` error unless `KEEL_ALLOW_REMOTE_ENDPOINT` is set;
- **cloud-metadata and link-local addresses are refused outright** — no opt-in reaches
  them. `169.254.169.254` and every alternate spelling of it (`2852039166`,
  `0251.0376.0251.0376`, `0xA9FEA9FE`) resolve to the same address before the check, so
  the encoding cannot be used to step around it;
- **private ranges need their own opt-in.** `10.0.0.0/8`, `172.16.0.0/12` and
  `192.168.0.0/16` require `KEEL_ALLOW_INTERNAL_ENDPOINT=1`: `KEEL_ALLOW_REMOTE_ENDPOINT`
  permits reaching *out*, not reaching *in*, and a model server on your own subnet is a
  deliberate choice rather than a side effect of allowing remote hosts;
- both opt-ins live in the **environment, not this file**. The threat model is a config an
  attacker influenced, so the switches must sit outside the surface they would control;
- a non-`http(s)` scheme is refused outright, blocking `file://`, `ftp://` and friends;
- a malformed URL is a config error, not a traceback.

The same guard applies whether the endpoint is remote or a `vLLM` on your own machine —
the local case simply passes it without an opt-in.

**`prompt_mode` exists because stdin is not universal.** `stdin` (the default) writes the
prompt to a temp file and pipes it in, because positional-arg passing hangs some CLIs. But
`cursor-agent`'s usage is `agent [options] [command] [prompt...]` — the prompt *is* a
positional argument — so those CLIs need `arg`.

**`model_arg` exists for the same reason.** Model precedence is per-run
`--delegate <name>:<model>` > the profile's `model` > the CLI's own default, so one
`cursor` profile serves `cursor-grok-4.5-high`, `composer-2.5` and the rest without a
config edit. But an *arbitrary* CLI shares no guaranteed model-selection syntax, so the
profile has to say how: the effective model is applied as `<model_arg> <model>`. The
default `--model` covers `cursor-agent`, `gemini` and Aider; set it for anything else.

**keel cannot make a generic CLI reviewer read-only — `review_args` is your lever.**
Every other non-host reviewer vendor has a mechanism behind the "read-only / findings
only" promise: a vendor read-only flag, a local endpoint, a single hosted-API call. A
profile is an arbitrary binary, and the same `command` serves both the implementer and
the reviewer role. `args` typically carries the implementer's write-enabling flags —
`cursor-agent`'s `--force` approves edits non-interactively — so a reviewer invoked with
them can edit the checkout. Set `review_args` to a read-only invocation for any profile
you use as a reviewer. keel validates neither list; this is operator-configured, not
enforced — but it does **report** it: `keel delegate run --role review` returns
`read_only_backed: false` plus a warning naming the profile when `review_args` is unset,
which is the signal an orchestrator refuses on. `review_args: []` is not the same as
omitting the key: an empty list says "no flags needed to review" and counts as configured,
while `null`/absent falls back to `args`.

**Quote a profile name that YAML would not read as a string.** A bare `on:`, `yes:`,
`2:` or `~:` key parses as a boolean, integer or null, not a name — `keel validate`
rejects those with an explicit message. A name may also not be blank or contain `:`,
since `--delegate` splits on the first colon to separate the profile from a per-run
model, which would make such a name unselectable.

**Name resolution is fail-closed.** A profile name is resolved *after* the built-in
delegate vendors (`claude`, `codex`, `agy`, `ollama`, `anthropic-api`, `openai-api`, `google-api`), and a
profile that shadows one of those names is a **`keel validate` error**, not a silent
override. So config can never redefine a built-in, and the operator is told at validation
time instead of discovering it mid-run.

A `cli` delegate inherits the local-model contract exactly: the orchestrator owns every
git/PR step and asks the CLI only for code generation, retries twice on an unusable result
then falls back to the host agent, and is **refused on tier-3** — an unvetted CLI is not a
high-risk-path implementer. No new consent scope is needed: this is the same subprocess
surface `codex`/`agy` already use, and `command` is operator-authored config with the same
trust level as `build_gate_cmd` — it is never taken from PR content or agent output.

For full model options and provider configurations, see the [Supported AI Models & Providers Guide](models.md).
Design and proposals: [`docs/proposals/generic-delegate-vendors.md`](../proposals/generic-delegate-vendors.md).

<a id="provider-registry"></a>
**The machine-level provider registry (`~/.keel/providers.yaml`).** Which providers are
usable is a property of the **machine and the person**, not of the project: one operator has
`claude`/`codex`/`agy` logged in and no API key, another has only `XAI_API_KEY`. Those facts
do not belong in a file everyone on the team commits. The registry is where an operator
keeps them:

```yaml
# ~/.keel/providers.yaml — never committed; override the path with KEEL_PROVIDERS
providers:
  cursor:
    transport: cli                 # cli | api | local
    command: cursor-agent
    review_args: ["-p"]            # a read-only invocation for the reviewer role
    model: composer-2.5
    model_arg: --model             # default "--model"
    effort: high                   # vendor-specific reasoning-effort selector
  vllm:
    transport: api
    endpoint: http://127.0.0.1:8000/v1/chat/completions
    api_key_env: VLLM_API_KEY      # the NAME, never the key
  my-gateway:
    transport: api                 # non-loopback: needs the env opt-in below
    endpoint: https://gateway.example.com/v1/chat/completions
    api_key_env: KEEL_DELEGATE_KEY_GATEWAY
```

**A registry `api` endpoint is held to the same loopback-only default as a project
profile's**, so a remote one of your own — the `XAI_API_KEY`-style case this registry
exists for — is refused until you export the opt-in:

```bash
export KEEL_ALLOW_REMOTE_ENDPOINT=1     # any non-loopback endpoint, registry included
export KEEL_ALLOW_INTERNAL_ENDPOINT=1   # additionally, for 10./172.16./192.168. hosts
```

Until then the entry is **skipped with a warning naming that variable**, and every other
entry still registers. The opt-in stays in the environment rather than in the file for the
same reason it does for `project.yaml`: a file must not be able to widen its own reach —
so no registry entry can grant itself a remote endpoint, however trusted the directory it
sits in.

| field | required | description |
|---|---|---|
| `transport` | ✅ | `cli` (a local binary), `api` (an OpenAI-shaped hosted endpoint), `local` (a model served on this machine) |
| `command` | ✅ for `cli` / `local` | the executable keel runs |
| `endpoint` | ✅ for `api` | the OpenAI-shaped chat-completions URL, under the same loopback-by-default rules as a profile's |
| `api_key_env` | ✅ for `api` | the **name** of the env var holding the key |
| `model` | | default model for this entry |
| `model_arg` | | flag the model is passed on, as `<model_arg> <model>` (default `--model`) |
| `effort` | | vendor-specific reasoning-effort selector, carried through to dispatch |
| `review_args` | | flags for the reviewer role; their presence is what the probe reports as `read_only_mode` |

**A registry entry's `review_args` is present or it is not — there is no third state.**
A *profile* distinguishes them, because `role_args` falls back to `args`: `review_args: []`
there means "this CLI needs no flags to review", a deliberate choice keel counts as a
configured read-only invocation, while omitting the key entirely means the reviewer
silently receives the implementer's `args`. A registry entry has no `args` to fall back
to, so an empty or absent `review_args` is the same thing — nothing configured — and
`keel delegate run --role review|gate|chair` reports `read_only_backed: false` with a
warning for it. See [`cli.md`](cli.md#keel-delegate).

**Precedence: built-in > project profile > registry.** A built-in vendor always wins and can
never be redefined — not by a committed profile, not by a file in your home directory. Below
the built-ins a project's `knobs.delegate_profiles` entry wins, so a repository can pin the
provider its team shares; below that the registry adds entries the project never has to know
about. `keel delegate run` resolves in exactly this order.

**A clash is an error naming both sources, not a silent override.** A registry entry named
after a built-in vendor (`claude`, `codex`, `agy`, `ollama`, `*-api`) or after one of this
project's profiles is refused: it is dropped from the plan and reported by
`keel doctor --providers` as a `fail` that names the registry path *and* the
`knobs.delegate_profiles.<name>` it collided with. This mirrors the built-in shadowing rule
above — the operator is told which file to edit instead of discovering mid-run that their
entry did nothing. The check lives in `doctor` rather than `keel validate` on purpose:
`keel validate` must stay a function of the committed config alone, so its result cannot
depend on whose home directory it runs in.

**A broken registry never breaks a run.** A missing file means no machine-level providers —
the state of every machine that has not opted in. A malformed document, an unknown
transport, a `cli` entry with no `command`, an `api` entry with no key name: each is a
warning on the entry, keel keeps the entries that parse, and nothing raises.

**`api_key_env` here is not held to the project allowlist.** The
[allowlist above](#which-env-vars-may-hold-a-delegate-key) exists because `project.yaml` is
committed and reviewed by people other than its author — the threat model is a config an
attacker influenced through a pull request. This file is not committed and not shared: it
sits in your home directory at the same trust level as your shell profile, and an operator
whose only key is `XAI_API_KEY` should not have to rename it. The **denylist still applies**:
a high-privilege system credential (`GITHUB_TOKEN`, `AWS_*`, `SSH_AUTH_SOCK`, …) may never
become an `Authorization` header, wherever the entry was written.

Nothing here affects `config_hash`: a project that references no machine-level provider
hashes exactly as it did before, and the registry is never part of the hash.

Inspect the whole picture with [`keel doctor --providers`](cli.md#keel-doctor).

#### `tier3_globs`

Path globs that mark a diff as high risk. `keel ship` uses them to choose the strongest
review posture, including the maximum reviewer count and auto-jury behavior when enabled
by the command policy.

#### `ci_workflows`

Map of GitHub check or workflow display name to a path glob. `keel ship --pr` uses this
mapping to decide which CI checks are relevant to a PR's changed files.

#### `docs_gate_paths`

The **docs surface**: paths that *are* documentation. Ship uses them to classify docs-only
changes, to decide when scope creep is tolerable, and to decide when an empty CI check set
may be acceptable.

#### `docs_only_allowlist`

Paths permitted to **ride along** in a docs change without forcing code-risk
classification — generated site output, metadata. They widen the risk-tier judgement only;
they are deliberately narrower than `docs_gate_paths`:

| | `docs_gate_paths` | `docs_only_allowlist` |
|---|---|---|
| keeps a change at TIER-1 | yes | yes |
| exempt from scope-creep | yes | no |
| allows an empty CI check set | yes | no |

The last row is the point of the distinction: a generated site file riding along with a
docs edit is exactly the case where a workflow *should* have run, so the allowlist must not
buy the empty-check-set carve-out. Leave it empty unless you have such riders.

#### `sot_doc`

Source-of-truth project instructions file, for example `AGENTS.md`. Adapters and reviewers
use it as the first project policy reference.

#### `required_capabilities`

Runtime capabilities that must be present before live mutation begins. Examples include
`shell`, `git`, `worktree`, `gh`, or `github-mcp`. `keel capabilities` and live command
preflight evaluate these declarations.

#### `optional_capabilities`

Runtime capabilities that improve behavior but can degrade explicitly. Missing optional
capabilities are reported as degraded rather than silently treated as success.

#### `evidence_gate_label`

The legacy PR label that also arms the required pre-merge evidence gate enforced by
`keel evidence-verify` (default `keel:ship`). The gate no longer relies on an agent-applied
opt-in label: ship provenance such as a ship-style issue branch, posted review marker,
trusted `keel ship` assessment comment, or ship-run ledger record arms it by default. The
assessment comment is only an arming signal, not accepted evidence. Hand-authored PRs
without ship provenance pass with `enforced: false` and `required: 0`. The operator waiver
label `keel:evidence-waived` is the intentional disarm path and is reported in the
verifier output. Override the legacy arming label per run with
`keel evidence-verify --gate-label`.

#### `evidence_require_distinct_vendors`

**Opt-in. Unset is `false`, on every risk tier.** The knob makes two different claims
separable. The *count* claim — three reviewers looked at this — keel can verify from the
posted evidence for any bench a project configured. The *independence* claim — three
independent opinions looked at this — needs distinct vendors, and it is a property a
cross-vendor panel provides rather than one every high-tier review has to carry. Asserting
it by default would have keel say, on the project's behalf, something the project never
said: a reviewer bench drawn from one vendor is the normal case for someone running a
single agent CLI, and that person's TIER-3 change must still be reachable without
configuring anything (#1065). Set it to `true` on a project whose bench really does span
vendors, and the requirement then lives in a file a reviewer can read.

The setting stays tri-state at the config boundary — unset, `true`, `false` — so an
explicit `false` remains distinguishable from silence, but the two resolve identically.
When it resolves to `true`, `keel evidence-verify` additionally enforces **verdict
provenance distinctness**:
each required review verdict must carry a `vendor:` provenance line, and no two required
verdicts may declare the same vendor. This closes the gap where one agent could post N
verdicts under invented reviewer ids — the verdict *count* was checkable but the *vendors*
behind them were not.

The check is **jury-agnostic**: it operates purely on the `vendor:` / `model:` fields
carried by the posted review verdicts (rendered by `render_review_verdict` and supplied per
review through `keel review`). Any reviewer — a plain host-agent reviewer or a cross-vendor
jury — satisfies it simply by carrying distinct vendor provenance; keel takes no dependency
on any review vendor. A missing `vendor:` on a required verdict, or two verdicts sharing a
vendor, fails verification with a blocking `review-vendor-distinctness` finding. Override
per run with `keel evidence-verify --require-distinct-vendors`.

#### `implement_mode`

The **s4 implement profile**. `default` is the single implement pass keel has always run.
`tdd` asks for the change to be written test-first, and then *verifies* that it was:

```yaml
knobs:
  build_gate_cmd: "make test"
  implement_mode: tdd

policy_pack:
  name: my-project
  test_groups:
    unit:
      command: "make test"
      paths: ["src/**", "tests/**"]   # what makes the group relevant
      test_paths: ["tests/**"]        # where the tests actually live
```

In `tdd` mode s4 runs **two phases against the same provider**, one `keel delegate run`
call and one commit each:

| phase | what the implementer is asked for | diff | gates |
|---|---|---|---|
| `tests` | the failing tests derived from the issue's acceptance criteria | test paths only | expected **red** |
| `implementation` | the change that turns them green, without weakening a test | the change | must end **green** |

At **s8** the run then carries one extra gate, `tdd-order`. It is `on_fail: block`, and it
is a **pure function of the commit list and the path policy** — keel reads the branch
through a single `git log` and decides in `keel.tdd`, with no other I/O. It passes when:

1. the branch history is readable at all (an unreadable one blocks — it is not an empty branch);
2. the first non-merge commit touches at least one path, and **only** test paths;
3. that commit *adds or modifies* at least one test — a first commit that only runs
   `git rm` over the suite is the opposite of writing it;
4. no later commit *removes* a test — deleted outright, **or renamed out of the test
   paths**, which stops the suite collecting it just as surely (`git mv tests/test_a.py
   src/legacy_test_a.py` is `git rm` wearing a rename). A rename *within* the test paths
   is an ordinary move and stays fine, and a copy is not a removal at all — its source
   still exists. Making the failing tests go away is the cheapest way to make phase B
   "pass";
5. a later commit touches a non-test path — the implementation the tests were written for;
6. the rest of the gate run is green.

Otherwise it blocks and names what to fix: the offending paths in the first commit, the
removed tests to restore (a rename-out is named by the path it *left*, the one that
stopped being a test), the test globs it matched against, or the missing half of s4.

The branch is read with `git log --topo-order --first-parent --reverse --name-status
base..HEAD`. Ancestry order, not commit-date order: once a branch integrates its base at
s10, a base commit dated *before* the tests commit would otherwise sort ahead of it and be
judged as this implementer's first commit. `--first-parent` follows only this branch's own
line, so the commits a base merge brought in are not on it at all; the merge commits
themselves stay and are skipped rather than judged.

> **What the gate does not check.** It reads commit **order and paths**, and nothing else.
> It never runs phase A's tests, so it cannot report that they were red, and it cannot tell
> whether the committed tests assert anything. Three residuals follow, each a reviewer's
> catch rather than a gate's:
>
> - a first commit adding an **empty file** under `tests/` satisfies rule 3 — and so does
>   one that merely **renames an existing test** within the test paths, since the
>   destination is present and is a test path;
> - a test deleted inside a **merge from a side branch** is not judged. Merges are skipped
>   deliberately: a test legitimately deleted on the base arrives through every branch that
>   integrates it, and judging merges would block this implementer for someone else's
>   change. The cost is that `git rm tests/test_a.py` on a side branch merged with
>   `--no-ff` leaves no non-merge commit recording the deletion. Telling a base merge from
>   a side merge is not something the commit list can do — both are just a second parent —
>   so the gate declares the gap rather than guessing;
> - nothing here says the tests are *good*.
>
> The gate makes the *shape* of a test-first run machine-checkable; it does not certify
> that the tests are good.

**Where the test paths come from.** The fallback is **whole-config, not per-group**, and
that distinction matters when you write the config:

- if **no** group declares `test_paths`, the globs are the union of every group's `paths`;
- if **any** group declares `test_paths`, the globs are the union of the declared
  `test_paths` **only** — every group that declares none contributes nothing, including
  its `paths`.

So `{unit: {paths, test_paths}, e2e: {paths}}` yields `unit.test_paths` alone, and `e2e`'s
selectors are dropped: give `e2e` its own `test_paths` if its tests should count. This is
deliberate rather than incidental. Group `paths` are *selectors* — the paths that make the
group relevant — and on a real project they routinely include the implementation surface
(keel's own `unit` group selects `src/**` as well as `tests/**`). Mixing the remaining
groups' selectors back in would re-import exactly the surface `test_paths` exists to
exclude, and a gate whose "test paths" include `src/**` is vacuous.

A project that declares no path at all fails the gate **closed**, with a message naming
the key to add: a gate that cannot look must not pass.

`--tdd` selects the profile for a single run of `keel ship`, `keel plan` or
`keel run-gates`. There is no `--no-tdd`: a project that configured the contract has said
the contract is the policy, and a flag that switched it off from a command line would make
it advisory. The resolved profile is published as `contract.implement_mode` by
`keel plan`/`keel ship --json` (`mode`, `source`, `phases`, `gate`), the ledger records
`run_context.implement_mode` plus one `run_context.implement_phases` entry per phase —
each carrying that phase's commit **and the implementer that ran it**, so "the same
provider wrote the tests and the implementation" is auditable rather than assumed
(`keel ship --phase-implementer tests=<label>` records a phase whose implementer differed
from `--implementer`). The closure comment says
`Implement: TDD (tests <sha> by <implementer> → implementation <sha> by <implementer>)`.
A `default` run records neither key's value — `implement_mode` is `null` and
`implement_phases` is `[]` — and its closure comment is unchanged.

Backbone step ids are unchanged: `tdd` is an s4 profile the way `compound` is a workflow
profile. Setting it to `default` (or leaving it out) does not change `config_hash`.

#### `gate_timeout_s`

Wall-clock seconds a **command gate** may run before keel kills it. Defaults to `600`
(ten minutes), which is what keel used unconditionally before this knob existed. Raise it
on a slow host where a legitimate build or test suite needs longer:

```yaml
knobs:
  build_gate_cmd: "make test"
  gate_timeout_s: 3600     # this project's suite needs far more than ten minutes here
```

When only **one** gate is the slow one, leave the project knob alone and give that gate its
own limit with `timeout:` frontmatter (see [extensions](extensions.md)). Resolution is
most-specific-first, per gate: the extension's `timeout:` → `knobs.gate_timeout_s` → `600`.

A gate killed by this limit is reported as a **timeout**, not as a failing test:

```
  TIMEOUT  build
    [major] build: build timed out after 600s (exit 124); the command produced no pass/fail result. Raise the limit via knobs.gate_timeout_s (or this gate's timeout:) if it legitimately needs longer — a genuinely hanging command is still a defect.
BLOCKED — merge is gated by the findings above
```

**A timeout still blocks the merge, exactly as a failure does.** Only the label and the
operator-facing explanation change. This is deliberate: a genuinely *hanging* command
(deadlock, infinite loop) is a real defect that also presents as a timeout, so making
timeouts advisory would punch a hole in the very thing the gate protects. The goal is that
an operator can tell *why* the gate is red — a slow host or a broken test — never that it
stops being red.

> **Not the same as the run-control layer.** `runcontrols.contract_as_dict()` advertises
> `"wall_clock_timeouts": False`. That refers to run budgets, step caps, and oscillation
> halts — keel imposes no wall-clock limit on a *run*. `gate_timeout_s` and `jury_timeout_s` are
> subprocess limits on a single gate. The two are independent and do not contradict each other.

#### `jury_timeout_s`

Wall-clock seconds the **`jury` built-in** may run. Defaults to `600`, which is what keel
used unconditionally before this knob existed. It is deliberately **separate** from
`gate_timeout_s`: the jury is a cross-vendor agent CLI, not a project test command, so a
panel that legitimately needs an hour should not force every test gate to wait an hour too.

```yaml
knobs:
  gate_timeout_s: 1800    # this project's test suite is slow
  jury_timeout_s: 3600    # ...and a full cross-vendor panel is slower still
```

A jury run killed by this limit — or one whose output carries no parseable verdict at all,
whatever its exit code — **produced no review**. In `gating` mode that fails closed with a
blocking `major`; in `advisory` mode it surfaces a non-blocking `minor`. That is the point
of the knob: a panel that never reached a conclusion must not be reported as a clean pass.

(`minor` rather than the oversize branch's `nit`: an oversize diff is a deterministic skip
the operator can see from the diff itself, while an incomplete run is an invisible
operational failure that will otherwise recur silently on every run.)

A nonzero exit that *does* carry a parseable report is a completed review — ai-jury signals
"request changes" that way — so its findings are used as-is. The test is deliberately
"did we parse a verdict", not "was the exit code zero".

#### `swarm_review_evidence`

Whether `keel swarm-land` holds a cluster whose open PR does not pass the same pre-merge
review-evidence verification `keel merge` enforces at s10 (#828). Default `true`.

```yaml
knobs:
  swarm_review_evidence: false   # clusters land on CI alone — see below
```

Setting it `false` is the **explicit, logged** opt-out, not a quiet one: a live
`swarm-land` prints `swarm review evidence: OFF by config` to stderr before landing
anything, because skipping review has to be a visible configured exception rather than a
driver's judgement call. `swarm-land` runs no CI of its own, so with the gate off a
cluster lands unverified.

Left on, the gate runs in dry runs too — the checks are read-only — so a preview reports
`would hold: <reason>` per cluster instead of promising a landing a live run would refuse.
A *live* wave with any held cluster exits non-zero. See
the `keel swarm-land` section of [cli.md](cli.md)
for what each hold reason means and [swarm.md](swarm.md) for the landing modes.

## `policy_pack`

`policy_pack` is the durable project policy contract. It is data, not executable logic:
commands can read it during planning and dry-run reporting, while command execution,
custom prompts, and project-owned gates still live in extension slots or project commands.

If `policy_pack` is present, `name` is required and unknown fields are rejected. This makes
missing or misspelled project policy fail during `keel validate` instead of silently falling
back to packaged command prose.

| field | type | required | description |
|---|---|---|---|
| `name` | string | ✅ | stable id for this project's policy pack |
| `labels` | map group→string[] | | label vocabularies such as status, priority, role, type, or command-specific groups |
| `status_transitions` | map transition→label | | lifecycle transition targets |
| `risk_rules` | object[] | | high-risk path rules with extra gate, review, or docs expectations |
| `blocker_rules` | object[] | | deterministic blocker ruleset for `keel guard` / `keel merge --hotfix` (absent → built-in defaults) |
| `test_groups` | map name→object | | named test/audit commands, path selectors, reports, and capability needs |
| `docs` | object | | docs gate policy and allowed no-docs reasons |
| `health_providers` | map name→object | | project-owned operational signal providers for reporting commands |
| `scan` | object | | project-owned area/module, branch, dedupe threshold, and label policy for scan-and-file commands |
| `project_commands` | map command→object | | project-provided commands, path selectors, capability needs, and side effects |
| `command_routing` | map command→object | | compatibility routing map for older project command declarations |
| `workflow_policies` | map command→object | | command-specific workflow policy such as posting mode, reviewer isolation, CI/fix-loop behavior, and completion markers |
| `reports` | map name→string | | report destinations, paths, or issue prefixes |
| `capture_redaction` | object | | additional project-owned deny regexes applied before capture artifacts are persisted |
| `capture` | object | | post-merge capture enablement/mode; content and destinations remain extension-owned |
| `review` | object | | project-owned rubric additions and required PR/review sections |

## `automation`

Trusted unattended-run consent defaults. Env approval is preferred for CI/cron because it
keeps authorization outside the repository, but config approval is useful when a project
wants an explicit auditable policy.

| field | type | required | description |
|---|---|---|---|
| `approved_scopes` | string[] | | standing consent scopes such as `filesystem`, `git`, and `github` |
| `operator` | string | runtime-required with `approved_scopes` | automation identity recorded in `consent_record` when config approval is used |

`automation.approved_scopes` only satisfies the consent preflight. It never bypasses
findings, CI, project gates, merge windows, or merge locks. Approval is least-privilege:
any required scope not listed here still blocks the live run.
If `approved_scopes` is selected by a live mutating `standing` run without `operator`,
preflight fails before mutation.

Example:

```yaml
automation:
  approved_scopes: [filesystem, git, github]
  operator: automation:nightly
```

### `policy_pack.name`

Stable identifier for this policy pack. It is required whenever `policy_pack` is present
and helps generated plans distinguish the consumer policy from Keel core.

### `policy_pack.presets`

Declarative security and SAST scanning presets (`1.13.0+`). Keel provides built-in,
dependency-free preset definitions that automatically slot static analysis tools into
the backbone without writing custom gate scripts:

| preset | target tool | planned backbone step | fail-soft behavior |
|---|---|---|---|
| `bandit` | [Bandit](https://github.com/PyCQA/bandit) (Python SAST) | `s8 test` (as a gate) | degraded / skipped if `bandit` is not installed |
| `gitleaks` | [Gitleaks](https://github.com/gitleaks/gitleaks) (Secret scanner) | `s3 guard` / `s8 test` | degraded / skipped if `gitleaks` is not installed |
| `semgrep` | [Semgrep](https://github.com/semgrep/semgrep) (Static analysis) | `s8 test` | degraded / skipped if `semgrep` is not installed |
| `trivy` | [Trivy](https://github.com/aquasecurity/trivy) (Vulnerability scanner) | `s8 test` | degraded / skipped if `trivy` is not installed |

Example enabling Bandit and Gitleaks on a Python repository:

```yaml
policy_pack:
  name: keel-python
  presets: ["bandit", "gitleaks"]
```

When enabled, `keel plan` automatically renders the preset gates under `s8 test`, and
`keel run-gates` executes them. All presets are strictly fail-soft: if the host environment
lacks the tool binary, the pipeline degrades cleanly with structured feedback rather than
crashing.

### `policy_pack.labels`

Map from label group to allowed label names. Common groups include `status`, `priority`,
`role`, `type`, or command-specific groups. Triage, ship, regression, and closeout flows
can use these vocabularies instead of hardcoding labels in command bodies.

An entry may carry its group (`status: ["status:done"]`) or not (`role: ["core"]`); both
name the label `status:done` / `role:core`. Declaring a label does not create it, and
GitHub rejects a label that does not exist — [`keel doctor`](cli.md#keel-doctor)'s
`policy_labels` check reports the declared labels missing from the repository, and
`keel doctor <config> --fix` creates them.

### `policy_pack.status_transitions`

Map from lifecycle transition name to the label or state target. Examples include
`start`, `review`, and `done`. Ship-compatible adapters use this to move work through the
project's issue lifecycle without embedding project label names in core.

### `policy_pack.capture_redaction`

Capture artifacts are sanitized by default before they are persisted or handed to durable
learning tooling. Core redacts common credential shapes such as bearer tokens, GitHub tokens,
private-key blocks, credential-bearing URLs, and token/password-style assignments. Projects can
add organization-specific deny regexes without putting those patterns in keel core:

```yaml
policy_pack:
  name: example
  capture_redaction:
    deny_patterns:
      - id: private-host
        pattern: 'internal\.example\.test'
      - id: org-ticket-url
        pattern: 'https://tickets\.example\.test/[A-Z]+-[0-9]+'
        replacement: '[REDACTED:org-ticket-url]'
```

The redaction audit records rule ids and replacement counts only; it never records the original
matched value. Invalid configured regexes make the capture write skip/fail with an explicit
reason before the artifact is written. Redaction is a safety layer, not a complete DLP system:
projects should still avoid sending raw secrets, full CI logs, or private production data into
capture extensions.

### `policy_pack.capture`

Core owns the post-merge capture mechanics: the stable marker, fail-soft semantics,
recursion guard, redaction-before-durability requirement, and the offline session-end
verifier. Projects own the content and destination by declaring a `capture` or `post-merge`
extension.

```yaml
policy_pack:
  name: example
  capture:
    enabled: true
    mode: extension
```

`mode: extension` means a project hook can produce the learning content after the core
checks and records the marker. `mode: marker-only` records the core marker without running
a project content hook. The marker format is:

```text
compound-learning: pr=<N> status=<applied|deferred|skipped:reason>
```

Allowed skip reasons are `dry-run`, `deferred`, `merge-failed`, `recursion-guard`,
`capability-unavailable`, and `no-policy`. Capture failures after a successful merge are
fail-soft: the merge is not reverted, but the marker and ledger must record the applied,
deferred, or allowed skipped state so `keel capture-verify` can surface gaps.

### `policy_pack.risk_rules`

Array of high-risk policy rules. Each entry requires:

| field | type | required | used for |
|---|---|---|---|
| `id` | string | ✅ | stable name shown in plans and review context |
| `paths` | string[] | ✅ | path globs that activate the rule |
| `required_gates` | string[] | | extra gate names expected for matching changes |
| `review_additions` | string[] | | project-specific review checklist text |
| `review.additions` | string[] | | recurring failure shapes, passed verbatim into every s7 reviewer's brief |
| `review.required_sections` | string[] | | sections a review body must contain |
| `docs_required` | boolean | | whether matching changes must update docs |

Use `risk_rules` for project-owned elevated scrutiny beyond generic `tier3_globs`.

### `policy_pack.blocker_rules`

The deterministic blocker ruleset consumed by [`keel guard`](cli.md) and validated by
`keel merge --hotfix --blocker-rule <id>`. Blocker promotion is what unlocks the
night-window bypass at s10, so it is a verifiable function of the issue's title and labels
rather than agent judgment (audit GAP-11). Each entry requires:

| field | type | required | used for |
|---|---|---|---|
| `id` | string | ✅ | stable rule id reported by `keel guard` and passed to `keel merge --blocker-rule` |
| `kind` | `label` \| `title-regex` | ✅ | how the rule matches the issue |
| `labels` | string[] | for `label` | label names that fire the rule (case-insensitive exact match) |
| `pattern` | string | for `title-regex` | regex matched against the issue title (case-insensitive) |

When `blocker_rules` is **absent or empty**, the built-in defaults apply (back-compatible):
`blocker-label`, `hotfix-label`, `security-label` (labels `blocker`/`hotfix`/`security`) and
`blocker-title-regex` (`\b(?:hotfix|security|blocker)\b`). A malformed configured rule
(missing `id`/`kind`, duplicate id, empty `labels`, or an invalid `pattern`) is rejected
fail-closed so a typo can't silently widen or narrow the bypass surface.

```yaml
policy_pack:
  name: my-project
  blocker_rules:
    - id: p0-label
      kind: label
      labels: ["P0", "incident"]
    - id: urgent-title
      kind: title-regex
      pattern: '\b(?:hotfix|outage|sev1)\b'
```

### `policy_pack.test_groups`

Map from test group name to a test command contract. Each group requires `command`.

| field | type | required | used for |
|---|---|---|---|
| `command` | string | ✅ | runnable project test/audit command |
| `paths` | string[] | | path selectors that make the group relevant |
| `test_paths` | string[] | | where this group's **tests** live, when that differs from `paths`. Read by the [`implement_mode: tdd`](#implement_mode) `tdd-order` gate |
| `reports` | string[] | | report paths or destinations produced by the command |
| `required_capabilities` | string[] | | capabilities needed before the command can run |
| `optional_capabilities` | string[] | | capabilities that may degrade when unavailable |

Commands such as ship, coverage, deps-audit, and flake-audit can surface these groups in
plans and test guidance.

### `policy_pack.docs`

Documentation policy used by docs gates and reviewers.

| field | type | used for |
|---|---|---|
| `required_paths` | string[] | docs surfaces expected when behavior or contracts change |
| `allow_none_reasons` | string[] | approved reasons for `Docs Impact: none` |
| `impact_required` | boolean | whether PR bodies must state docs impact |

### `policy_pack.health_providers`

Map from health provider name to metadata used by reporting commands such as `morning`.
Each provider requires `kind`.

| field | type | required | used for |
|---|---|---|---|
| `kind` | string | ✅ | provider type, for example `github-checks` or `project-command` |
| `command` | string | | project command to execute when the provider is command-backed |
| `reports` | string[] | | report sources or destinations |
| `required_capabilities` | string[] | | hard runtime requirements |
| `optional_capabilities` | string[] | | degraded-but-allowed runtime requirements |

### `policy_pack.scan`

Project-owned scan scope for `regression` and `review-all-day`.

| field | type | used for |
|---|---|---|
| `areas` | map name→string[] | module/path fan-out groups for scan reviewers |
| `active_branch_patterns` | string[] | branch globs considered active work during time-window scans |
| `issue_labels` | map command→string[] | labels for issues opened by scan commands |
| `near_text_similarity` | number 0..1 | deterministic duplicate-finding threshold |
| `batch_threshold` | integer ≥ 1 | commit count threshold before batch/fan-out behavior |
| `large_diff_max_bytes` | integer ≥ 1 | max diff bytes before file-boundary truncation |

These three bounds were declared in the schema from the start but only became *enforced*
when `jsonschema_min` gained `minimum` / `maximum` support. A config carrying a
meaningless value (`batch_threshold: 0`, `near_text_similarity: 42`) loaded silently
before and now fails `keel validate`.

### `policy_pack.command_routing`

Compatibility map for older project command declarations. Prefer `project_commands` for
new configs. Each command entry may include:

| field | type | used for |
|---|---|---|
| `command` | string | project command path or invocation |
| `description` | string | human-facing description shown in command lists |
| `agent_role` | string | role used for implementer selection |
| `paths` | string[] | path selectors for relevance |
| `required_capabilities` | string[] | hard runtime requirements |
| `optional_capabilities` | string[] | degraded runtime requirements |
| `side_effects` | string[] | declared writes, pushes, reports, or external effects |
| `dry_run_safe` | boolean | whether the command can run during dry-run contexts |

### `policy_pack.project_commands`

Preferred map for project-provided commands that Keel should preserve without owning their
bodies. The subfields are the same as `command_routing`. `keel project-commands` lists
these commands, and `keel plan --command <name> --json` emits their structured contract.

### `policy_pack.workflow_policies`

Map from Keel command name to command-specific workflow behavior. This keeps compatibility
semantics explicit without forking packaged adapters.

Supported sub-objects:

| field | type | used for |
|---|---|---|
| `posting_mode` | `inline` \| `summary` | default review/comment posting mode |
| `posting_owner` | `orchestrator` \| `reviewer` | who owns GitHub writes |
| `reviewer_isolation` | object | reviewer no-cross-reading and codename policy |
| `inputs` | map string→boolean | supported command input behavior |
| `ci` | map string→boolean | CI recheck and degradation behavior |
| `review` | map string→boolean | review fan-out and summary behavior |
| `fix_loop` | map string→boolean/integer/string | fix-loop enablement and budget |
| `completion` | map string→boolean/string/null | marker, merge, approval, or summary behavior |

`reviewer_isolation` supports:

| field | type | used for |
|---|---|---|
| `shared_with_ship` | boolean | whether the policy mirrors ship reviewer isolation |
| `codename_prefix` | string | stable prefix for reviewer codenames |
| `no_cross_reading` | boolean | whether reviewers must avoid reading each other's comments |

### `policy_pack.reports`

Map from report name to a path, destination, or issue prefix. Commands such as `morning`,
`overnight`, `wrap`, and `ship` use report destinations to avoid inventing
project-specific files.

### `policy_pack.review`

Project-owned review policy.

| field | type | used for |
|---|---|---|
| `additions` | string[] | extra reviewer rubric items |
| `required_sections` | string[] | PR/review body sections that must be present |

`risk_rules[]` entries require `id` and `paths`. `test_groups.*` entries require
`command`. `health_providers.*` entries require `kind`. These required fields are
validated by the bundled schema.

`health_providers` are used by reporting commands such as `keel morning`. Core reads their
metadata and declared capabilities, but provider execution remains project-owned. If a
provider only has optional capabilities and those capabilities are unavailable, morning
marks that provider `unavailable` instead of treating the missing signal as success.
`reports` can declare destinations such as `morning`, `priorities`, or `deferrals`; the
`deferrals` entry is the shared queue contract surfaced by ship, overnight, wrap, and
morning adapters.
`wrap` reads `session` or `wrap` report destinations for recap output. `overnight` reads
`overnight`, `morning`, and `session` destinations to choose the night report or day
session report path. Missing report destinations degrade as unconfigured in preflight
output; core does not invent project-specific paths.

`run_ledger` is the optional structured run-history path. When absent, keel uses
`.keel/state/run-ledger.jsonl`. When present, `keel plan --json`, `keel ship --json`,
`keel ledger`, `morning`, `wrap`, and overnight contracts all resolve the same path.
The file is JSONL with schema `keel.run-ledger.v1`; missing files are treated as empty
history, while malformed records are errors.
The configured path must be relative and must resolve inside the project root; absolute
paths and `..` escapes are rejected before any parent directory is created.

`checkpoint` is the optional resumable-run state path. When absent, keel uses
`.keel/state/checkpoint.json`. When present, `keel plan --json`, `keel checkpoint`,
`keel resume`, and work-owning adapter contracts all resolve the same path. The file is
a single JSON record with schema `keel.checkpoint.v1`; missing files mean there is no
active resumable run, while malformed records are errors.
The configured path follows the same safety rule as `run_ledger`: it must be relative and
resolve inside the project root.

`scan` is used by `keel regression` and `keel review-all-day`. Core owns the generic
scan-and-file contract, while projects own the module list, active work branch patterns,
issue labels, and thresholds:

```yaml
policy_pack:
  name: example
  scan:
    areas:
      app: ["app/**"]
      service: ["service/**"]
      workflows: [".github/workflows/**"]
    active_branch_patterns: ["feature/**", "fix/**", "chore/**"]
    issue_labels:
      regression: ["type:bug", "source:regression-scan"]
      review-all-day: ["type:bug", "source:review-all-day"]
    near_text_similarity: 0.6
    batch_threshold: 5
    large_diff_max_bytes: 200000
```

`areas` drives regression fan-out and remains project-specific. `active_branch_patterns`
drives review-all-day's active branch scope. `near_text_similarity` is the deterministic
dedupe threshold. Review-all-day's issue title prefix is intentionally core-owned and fixed
as `[review-all-day] ` so issue searches and created titles stay parity-safe.

`project_commands` is the preferred place to preserve local commands that keel should not
own. Keel can list them, include them in structured command contracts, and evaluate their
declared capabilities; the command body itself remains in the project:

```yaml
policy_pack:
  name: example
  project_commands:
    device-smoke:
      command: ".keel/commands/device-smoke"
      description: "Run the project's smoke-test checklist."
      agent_role: app
      paths: ["app/**"]
      required_capabilities: [shell]
      optional_capabilities: [browser]
      side_effects: [report_write]
      dry_run_safe: false
```

`workflow_policies` preserves command-specific workflow semantics that should be explicit
project policy rather than hidden in adapter prose. It is especially useful for feedback
commands that share ship primitives but do not share ship's full lifecycle:

```yaml
policy_pack:
  name: example
  workflow_policies:
    pr-loop:
      posting_mode: summary
      posting_owner: orchestrator
      reviewer_isolation:
        shared_with_ship: true
        codename_prefix: PR-LOOP
        no_cross_reading: true
      ci:
        recheck_after_push: true
        green_required_to_exit: true
        degrade_when_logs_unavailable: true
      fix_loop:
        budget: 3
      completion:
        merge: handoff
        summary_comment: true
    review-cycle:
      posting_mode: inline
      posting_owner: reviewer
      reviewer_isolation:
        shared_with_ship: true
        codename_prefix: REVIEW-CYCLE
        no_cross_reading: true
      review:
        parallel_reviewers_within_pr: true
        severity_histogram_source_of_truth: true
      completion:
        marker: review-cycle-complete
        marker_after_summary: true
        merge: never
        formal_approval: never
```

## `gates` vs `extensions`

- **`gates`** lists which **built-in** gates run (`build` / `lint` / `jury`). An unknown
  name here is an error.
- **`extensions`** registers **project-provided** gates/steps (Lego pieces) into named
  backbone slots. They are add-only and run at their slot's step. See
  [extensions.md](extensions.md).

Keel core stays consumer-neutral: project-specific labels, path globs, commands, health
signals, and manual playbooks belong in config, extensions, or project-provided commands.
The boundary is documented in [consumer-neutrality.md](consumer-neutrality.md).

## Extension hooks

Every extension hook key maps to a list of extension file names under `extensions_dir`.
The schema currently accepts these hooks:

| hook | backbone location | typical use |
|---|---|---|
| `after:config` | after s0 config | config reports or environment preflight notes |
| `before:select` | before s1 select | queue filters or backlog guards |
| `select` | during s1 select | project-owned selection policy |
| `after:select` | after s1 select | selected-issue reporting |
| `before:branch` | before s2 branch | branch naming or worktree guards |
| `after:branch` | after s2 branch | branch metadata capture |
| `guard` | s3 guard | project-specific blockers and preflight checks |
| `before:implement` | before s4 implement | implementation briefs or setup |
| `after-implement` | after s4 implement | generated-output checks |
| `classify` | during s5 classify | extra risk classification |
| `after:classify` | after s5 classify | risk reporting |
| `before:ci` | before s6 CI | CI preflight |
| `after:ci` | after s6 CI | CI summary capture |
| `reviewers` | s7 review | additional reviewer prompts or reviewer gates |
| `after:review` | after s7 review | review summary or posting checks |
| `tester` | s8 test | manual or agentic tester guidance |
| `test` | s8 test | project-owned deterministic tests |
| `after:test` | after s8 test | test report capture |
| `before:fixloop` | before s9 fixloop | fix-loop guardrails |
| `fixloop` | during s9 fixloop | project-specific fix policy |
| `after:fixloop` | after s9 fixloop | fix-loop summary |
| `pre-merge` | before s10 merge | blocking gates that must pass before merge |
| `after:merge` | after s10 merge | post-merge verification |
| `capture` | s11 capture | knowledge/session capture |
| `post-merge` | s11 capture | compatibility hook for post-merge capture |
| `before:close` | before s12 close | issue-close preflight |
| `on-close` | during s12 close | closeout comments or labels |
| `after:close` | after s12 close | final reporting |

Blocking policy should be explicit. Use `pre-merge` for gates that must block a merge, and
document any earlier hook that can stop a live run.

## Example

```yaml
extends: keel
core_version: "^1.0"
owner: example-owner
repo: example-repo
base_branch: main
platform: example-runtime
timezone: Europe/Istanbul
merge_window: "07:00-01:30"

knobs:
  implementer_agents:
    app: app-developer
    service: service-developer
  build_gate_cmd: "./tools/build-check"
  lint_cmd: "./tools/lint-check"
  tier3_globs: ["migrations/**", "src/**/critical/**"]
  ci_workflows:
    "App CI": "src/app/**"
    "Service CI": "src/service/**"
  sot_doc: AGENTS.md
  required_capabilities: [shell]
  optional_capabilities: [gh, gh-auth]

gates: [build, lint]

extensions:
  tester: [design-parity.md]
  pre-merge: [design-parity-gate.md]
extensions_dir: .keel/extensions

policy_pack:
  name: example-service
  labels:
    status: ["status:backlog", "status:in-progress", "status:done"]
    priority: ["priority:high", "priority:medium", "priority:low"]
    role: ["app", "service"]
  status_transitions:
    start: "status:in-progress"
    done: "status:done"
  risk_rules:
    - id: data-migration
      paths: ["migrations/**"]
      required_gates: ["build", "lint", "migration-check"]
      review_additions: ["Check upgrade and rollback safety."]
      docs_required: true
  test_groups:
    app:
      command: "./tools/test-app"
      paths: ["src/app/**"]
      reports: ["reports/app-tests/"]
      required_capabilities: [shell]
  docs:
    required_paths: ["docs/**"]
    allow_none_reasons: ["No operator-facing behavior changed."]
    impact_required: true
  health_providers:
    service-health:
      kind: project-command
      command: ".keel/health/service-summary"
      optional_capabilities: [shell]
  command_routing:
    smoke:
      agent_role: app
      paths: ["src/app/**"]
      required_capabilities: [shell]
      side_effects: ["report_write"]
      dry_run_safe: true
  reports:
    morning: "reports/morning/"
  review:
    additions: ["Check the project-specific rollout notes."]
    required_sections: ["Testing", "Docs Impact"]
```

The other seed configs live in [`projects/`](../../projects/).

## Determinism

`keel.config.config_hash(config)` is a stable SHA-256 over the canonicalised config —
key order in the YAML does not affect it. Use it as a cache key.
