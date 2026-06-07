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

## `keel plan <project.yaml> [--root DIR] [--command COMMAND] [--live] [--approve-scope SCOPE] [--operator ID] [--target TARGET] [--json]`

Render the backbone plan for a project: the fixed steps with the project's built-in gates
and extensions slotted in. This is the dry-run view — what an actual run would execute.

`--root DIR` (default `.`) is where extension files are resolved. Extensions that can't be
loaded are reported as warnings on stderr (fail-soft) and the plan still renders with the
built-in gates.

```bash
keel plan .claude/project.yaml
keel plan .claude/project.yaml --json
keel plan .claude/project.yaml --command morning --json
keel plan .claude/project.yaml --command ship --live --json
keel plan .claude/project.yaml --command ship --live --approve-scope filesystem,git,github --operator "$USER" --target "issue #123" --json
```

With `--json`, the output includes a structured command contract under `contract`: resolved
project config, command step graph, backbone plan, gates, extension hooks, capability
requirements/evaluation, selected GitHub transport, declared side effects, and operator
consent requirements. See [`command-contracts.md`](command-contracts.md).

By default, `plan` renders a dry-run contract. `--live` renders the live preflight contract
for an adapter or orchestrator. If the command has live mutation scopes and the current run
does not approve them with `--approve-scope`, `plan --live` exits non-zero after printing the
resolved contract. Dry-runs never require live-write consent, but still show the live scopes
that would require approval.

Example output:

```
keel plan — example-flutter
  base_branch: main   core_version: ^0.6
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

## `keel capabilities [--root DIR] [--project project.yaml] [--for COMMAND] [--json]`

Print the runtime capability report for the current execution environment. With
`--project`, keel also evaluates the selected command's required and optional capabilities
against that report.

```bash
keel capabilities --root .
keel capabilities --project .keel/project.yaml --for ship --root .
keel capabilities --project .keel/project.yaml --for morning --root .
keel capabilities --project .keel/project.yaml --for device-smoke --json
keel capabilities --project .keel/project.yaml --for ship --json
```

Required capabilities fail with a non-zero exit before mutating work starts. Optional
capabilities are reported as degraded in human output and as `missing_optional` in JSON.
The output also includes the selected GitHub transport and any degraded GitHub operation
capabilities. See [`runtime-capabilities.md`](runtime-capabilities.md) and
[`github-transport.md`](github-transport.md).

## `keel project-commands <project.yaml> [--json]`

List project-provided commands declared by `policy_pack.project_commands` or the older
`policy_pack.command_routing` compatibility map. These commands are not packaged keel
adapters; keel only exposes their metadata so wrappers and adapters can preserve local
behavior without copying project-specific command bodies into core.

```bash
keel project-commands .keel/project.yaml
keel project-commands .keel/project.yaml --json
keel plan .keel/project.yaml --command device-smoke --json
```

When `keel plan --json --command <project-command>` targets a project command, the contract
contains a `project_command` graph entry and the command's required/optional capabilities.

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

## `keel ship <project.yaml> [--root DIR] [--pr N] [--dry-run] [--live] [--approve-scope SCOPE] [--operator ID] [--target TARGET] [--json]`

Run the **deterministic slice of a ship** against the current checkout and print the
assessment: how many files changed vs. the base branch, the **risk tier** (→ reviewer
count), whether the **merge window** is open, optional **CI** status (`--pr N` reads the
check-rollup through the selected GitHub transport), each gate's result, and the final
**merge decision**
(`MERGE` / `DEFER` / `BLOCK`).

This is the runnable, agent-free part of the backbone (s5 classify + s6 CI + s8 gates +
s10 merge decision). It does **not** call coding agents and does **not** perform the merge —
the live merge (s10) needs a configured runner with `git` + an authenticated `gh`.

`--live` turns the command into a live preflight gate for adapters. The command builds the
same structured contract and stops before running project gates if operator consent is
missing. `--approve-scope` can be repeated or comma-separated. Approved live runs include a
local `consent_record` in JSON output; secret values are never recorded.

```bash
keel ship projects/keel.yaml --root .
keel ship projects/keel.yaml --root . --live --json
keel ship projects/keel.yaml --root . --live --approve-scope filesystem,git,github --operator "$USER" --target "issue #123" --json
# keel ship — keel  (base main)
#   changed files : 53
#   risk tier     : TIER-3  → 3 reviewer(s)
#   merge window  : OPEN
#   ci            : unknown
#   github        : gh
#   gate build          ok
#   gate lint           ok
#   decision      : MERGE — clear to merge
```

Exits non-zero when the decision is `BLOCK` (failing gates, blocking findings, or failing
CI), so it can gate a runner before it attempts a real merge.

`--hotfix` marks an emergency change so it may merge **outside** the merge window (an audit
line is printed). It never bypasses failing gates, blocking findings, or failing CI.

`--json` emits the structured command contract plus a deterministic `result` record for the
dry assessment. `--dry-run` is accepted for adapter clarity; this CLI command is already
non-mutating.

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

## `keel install-adapter <target> [--root DIR] [--force]`

Install the agentic **`/keel:<command>`** adapters (which ship with the keel package) into a
project, so they appear as slash commands (Claude) or skills (every other agent):

keel installs into the **two surfaces** that match how agents actually discover commands —
never one copy per agent (that would re-introduce file-copy drift):

| target | installs into | who reads it |
|---|---|---|
| `claude` | `.claude/commands/keel/<cmd>.md` | Claude Code, as native `/keel:<cmd>` |
| `skills` | `.agents/skills/keel-<cmd>/SKILL.md` | **every non-Claude agent** (Codex, Antigravity, Gemini, …) via its skill discovery / chat-command wrapper — **one shared copy** |
| `all` | both of the above | |

```bash
keel install-adapter claude          # → /keel:ship, /keel:regression, …
keel install-adapter skills          # → one shared keel-<cmd> skill set under .agents/skills/
keel install-adapter all             # both surfaces
keel install-adapter claude --force  # overwrite existing adapters
```

The `skills` surface is a **single** universal skill set (`keel-<cmd>`), not a dir per agent:
non-Claude agents all read `.agents/skills/`, so one copy serves Codex, Antigravity and Gemini
together. The skill body is the same project-neutral adapter, wrapped with skill frontmatter.
Generated skill frontmatter intentionally contains only `name: keel-<cmd>` and `description`.
Claude-only command metadata such as `argument-hint` and `allowed-tools` remains on the
packaged command body / Claude command surface and is intentionally not copied into
`SKILL.md`, because current shared skills use the skill manifest shape rather than Claude
slash-command metadata.

The CLI (`keel ship`, `keel run-gates`, …) does the deterministic work; these adapters are
the **agentic** flows (per-round review, inline comments, delegation) the agent runs. The
shipped set: `ship`, `regression`, `implement`, `review-cycle`, `pr-loop`, `morning`,
`review-all-day`, `overnight`, `wrap`, `triage`, `stale-prs`, `ci-check`, `deps-audit`,
`flake-audit`, `coverage`, `ship-v2`. Existing files are skipped unless `--force` (so your
edits are never clobbered).

The generated surface is covered as a release contract: tests install into a clean temporary
project, verify that every packaged command has a matching Claude command and shared skill,
validate skill frontmatter, check idempotent skip / `--force` overwrite behavior, and scan the
generated files for consumer-specific strings. PyPI release smoke tests can reuse the same
`keel install-adapter all --root <tmp-project>` flow.

Generated adapter files carry a trailing `keel-generated` marker with the surface, command,
keel version, source hash, and generated-body hash. That marker powers the safe update flow:

```bash
keel adapter-status all --root <repo>
keel update-adapter all --root <repo> --dry-run
keel update-adapter all --root <repo>
```

`adapter-status` reports:

| status | meaning |
|---|---|
| `current` | installed generated file matches the packaged source |
| `outdated` | installed generated file is unchanged locally, but packaged source changed |
| `missing` | expected generated file is absent |
| `locally-modified` | generated file has a marker, but its body changed after install |
| `unknown` | file exists without a keel generated marker |

`update-adapter` updates only `missing` and `outdated` generated adapter files. It refuses to
overwrite `locally-modified` or `unknown` files; those need a human merge. `--dry-run` prints
the same planned changes as `would-update` rows without writing. Adapter updates never touch
project-owned config, `.keel/extensions/*`, project-provided commands, or local compatibility
wrappers unless those files are explicitly marked as generated keel adapter surfaces.

Extension schema migrations are separate from adapter command updates and must be documented
as their own versioned migration.

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | a config was invalid/missing, or a plan target could not be loaded |
| 2 | no command given (help printed) |
