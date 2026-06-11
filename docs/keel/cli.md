# keel CLI reference

```
keel <command> [options]
keel --version
```

## `keel version`

Print the keel version.

## `keel setup [--root DIR] [--adapter-target all|claude|skills] [--wizard] [--force]`

Set up keel in a consumer project in one pass. This command wraps the normal first-run
sequence:

1. create `.keel/project.yaml` if it does not exist;
2. install the requested adapter surface (`all` by default);
3. strict-validate the config and extensions;
4. render the resolved backbone plan.

```bash
keel setup --root .
keel setup --root . --wizard
keel setup --root . --adapter-target claude
keel setup --root . --force
```

Without `--force`, an existing `.keel/project.yaml` is reused so project-owned policy and
extensions are not overwritten. With `--force`, the scaffolded config and generated adapter
files are replaced from the installed keel package, but `.keel/extensions/*` is still never
deleted or rewritten by `setup`.

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

## `keel plan <project.yaml> [--root DIR] [--command COMMAND] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--target TARGET] [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL] [--json]`

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
keel plan .claude/project.yaml --command ship --live --consent-mode standing --json
KEEL_CONSENT_MODE=agent keel plan .claude/project.yaml --command ship --live --json
keel plan .claude/project.yaml --command ship --review-comments summary --reviewers 2 --jury-advisory --json
keel plan .claude/project.yaml --command ship --issue-title "Add setup docs" --issue-body "$ISSUE_BODY" --issue-label enhancement --json
```

With `--json`, the output includes a structured command contract under `contract`: resolved
project config, command step graph, backbone plan, gates, extension hooks, capability
requirements/evaluation, selected GitHub transport, declared side effects, and operator
consent requirements. It also includes the `run_ledger` and `checkpoint` storage contracts
so adapters know where structured run history and resumable run state live. For `ship`
(including the `--compound` profile), it also includes `contract.evidence`: the exact posted GitHub evidence that must
exist before merge. See
[`command-contracts.md`](command-contracts.md).

By default, `plan` renders a dry-run contract. `--live` renders the live preflight contract
for an adapter or orchestrator. If the command has live mutation scopes and the current run
does not approve them with `--approve-scope`, `plan --live` exits non-zero after printing the
resolved contract. Dry-runs never require live-write consent, but still show the live scopes
that would require approval.

Consent mode is resolved as `--consent-mode` > `KEEL_CONSENT_MODE` >
`consent_mode` in `.keel/project.yaml` > built-in `explicit`. `standing` mode accepts
trusted `KEEL_APPROVE_SCOPE` or `automation.approved_scopes`; `agent` mode delegates the
approval prompt to the host agent permission system while keeping the structured contract.

When `--issue-title`, `--issue-body`, or `--issue-label` is supplied for a work-owning
command, the JSON contract includes `issue_intake`. The intake block classifies the issue
as `ready`, `needs-input`, `blocked`, or `out-of-scope`, extracts acceptance criteria and
docs/test expectations, and emits concrete clarification questions. Live work must stop
before mutation when an explicitly supplied issue is not `ready`.

## `keel ledger <project.yaml> [--root DIR] [--limit N] [--json]`

Read the structured run ledger offline. The default path is
`.keel/state/run-ledger.jsonl`; projects can override it with
`policy_pack.reports.run_ledger`.

```bash
keel ledger .keel/project.yaml --root . --json
keel ledger .keel/project.yaml --root . --limit 10
```

Missing ledgers are not errors: JSON output returns `status: "missing"` and
`records: []`. Invalid JSONL or unsupported record schemas are errors because adapters
must not build morning/wrap/capture reports from corrupted history.

`ship` (in either profile) can append one `ship_run` record with:

```bash
keel ship .keel/project.yaml --root . --live --append-ledger \
  --run-id "$RUN_ID" --issue 123 --pull-request 456 \
  --capture-status applied --implementer "codex:gpt-5" \
  --reviewer-agent "reviewer-a:gpt-5" --tester "tester:gpt-5-mini" \
  --host-agent claude --transport gh --profile standard \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

`--pull-request` records a PR number without asking keel to query CI. `--pr` still means
"look up this PR's CI status" and therefore requires a GitHub transport that supports
check runs. Dry-run output includes the same would-be record but never writes the file.
`--capture-status` is required for live appends so offline capture verification never has
to infer status from closure comments.

`--host-agent` and `--transport` (`gh`|`mcp`) record the s0 preflight **run context** on
the `ship_run` record so it becomes durable PR evidence. `--transport` defaults to the
transport keel resolved for the run when the flag is omitted (e.g. `gh` when an
authenticated `gh` CLI is present, otherwise the host GitHub MCP/API). `--profile`
(`standard`|`compound`) is threaded into the record, the jury mode is derived from the
resolved review contract, and the consent summary (status + approved scopes) is derived
from the existing `--operator`/`--approve-scope` inputs. The s11 closure comment renders
all of this as a deterministic **Run context** block (host agent / transport / profile /
jury / consent); missing fields degrade gracefully (`unknown`/`off`/`none`).

## `keel capture-verify <project.yaml> --merged-pr <N> [--json]`

Verify that merged PRs have exactly one valid capture marker in the configured run ledger.
Missing, invalid, or duplicate markers make the command exit non-zero.

```bash
keel capture-verify .keel/project.yaml --root . --merged-pr 456 --json
```

## `keel capture-reconcile <project.yaml> --merged-pr <N> [--json]`

Plan idempotent post-merge recovery actions for merged PRs whose capture marker, closure
summary, or linked issue closeout is incomplete. Reconcile is a recovery plan, not a second
implementation path: it never reopens work, pushes code, or merges PRs.

```bash
keel capture-reconcile .keel/project.yaml --root . --merged-pr 456 --json
keel capture-reconcile .keel/project.yaml --root . \
  --merged-pr 456 --linked-issue 456=123 --capture-capability available
```

The JSON output lists exact actions an adapter may apply after its transport and consent
checks: `emit-capture-marker`, `run-capture-extension`, `post-closure-summary`,
`close-linked-issue`, and `record-skip`. Allowed skip markers include
`skipped:capability-unavailable`, `skipped:no-policy`, and `skipped:recursion-guard`.
`policy_pack.capture.mode: marker-only` plans an `applied` core marker without requiring a
project capture extension. Ambiguous linked issues or invalid/duplicate existing markers
block the plan instead of guessing.

## `keel evidence-verify <project.yaml> --pr <N> [--issue <N>] [--json]`

Verify that a PR has the public evidence required by the ship contract before merge.

The gate is **opt-in**: it engages only when the PR carries the configured
`evidence_gate_label` knob (default `keel:ship`), which `keel:ship` applies when it opens
the PR. A PR without that label reports `enforced: false`, `required: 0`, status `pass`
(exit 0) — so hand-authored PRs that never went through ship are not blocked. When the
label is present the verifier is fail-closed and accepts only durable GitHub artifacts:

- a `keel.closure-comment.v1` closure marker on both the PR and linked issue;
- the required count of distinct posted s7 reviewer verdicts from PR comments or reviews
  carrying `keel.review-verdict.v1`, `reviewer: <stable-id>`, and the current
  `head: <sha>` (formal PR reviews may use GitHub's review `commit_id` as the head
  binding);
- a posted jury verdict carrying `keel.jury-verdict.v1` and the current `head: <sha>`
  when jury is enabled in gating mode.

PR bodies, chat summaries, and the automated `keel ship` assessment comment are never
accepted as evidence. The PR body may only be used to infer `Closes #N` when `--issue` is
not supplied. In live mode, keel reads the PR changed files and head SHA through `gh`, then
derives the risk tier, reviewer count, and jury requirement from the same project policy
used by `keel ship`.

```bash
keel evidence-verify .keel/project.yaml --root . --pr 456
keel evidence-verify .keel/project.yaml --root . --pr 456 --reviewers 3 --jury --json
keel evidence-verify .keel/project.yaml --root . --pr 456 --no-jury
```

`--gate-label <name>` overrides the `evidence_gate_label` knob for a single run, and
`--pr-label <name>` (repeatable) injects PR label names that are merged with the labels read
from the live PR. Injected labels are additive — they can only make the gate *more*
enforced, never less. A live PR fetch still runs unless an offline fixture flag (below) is
also supplied. The JSON payload reports the resolved `gate_label`, the boolean `enforced`,
and the observed `pr_labels`. The `evidence_gate_label` knob must be non-empty (the schema
forbids a blank value, which would silently disable the gate for every PR).

A failed live fetch (for example, `gh` cannot read the PR or its labels) is **fail-closed**:
the command errors and exits non-zero rather than treating the gate as unenforced.

Use `--deferral <id|kind|all>` only for an explicit, recorded operator deferral; it applies
only within an enforced run and has no effect when the gate is unenforced. `--dry-run`
prints the contract shape without fetching live artifacts or requiring evidence; pass
`--pr-label` alongside it to preview the enforcement state a labelled PR would see. Tests and
offline CI harnesses can provide `--pr-comments-json`, `--issue-comments-json`,
`--pr-reviews-json`, `--pr-body-file`, `--changed-file`, and `--head-sha` fixtures; the same
verifier path is used either way.

## `keel checkpoint <project.yaml> [--root DIR] [--json]`

Read the current resumable checkpoint. The default path is
`.keel/state/checkpoint.json`; projects can override it with
`policy_pack.reports.checkpoint`.

```bash
keel checkpoint .keel/project.yaml --root . --json
```

Missing checkpoints are not errors: JSON output returns `status: "missing"` and
`checkpoint: null`. Invalid JSON or unsupported checkpoint schemas are errors because
adapters must not resume from corrupted state.

Adapters that own live `ship` or `overnight` runs can write the current safe
step boundary:

```bash
keel checkpoint .keel/project.yaml --root . --write \
  --run-id "$RUN_ID" --checkpoint-command ship --step s6 \
  --target "issue #123" --issue-queue 123 --active-issue 123 \
  --branch feat/issue-123-example --worktree worktrees/issue-123 \
  --pull-request 456 --head-sha "$HEAD_SHA" --last-check ci
```

The writer replaces the previous checkpoint. It is for the active resume point, not for
append-only shipped-run history; use `keel ledger` for history.

## `keel resume <project.yaml> [--root DIR] [--live-pr-state STATE] [--live-worktree-state STATE] [--json]`

Render a dry-run resume plan from the checkpoint. This command never mutates files, git,
GitHub, comments, or releases.

```bash
keel resume .keel/project.yaml --root . --json
keel resume .keel/project.yaml --root . --live-pr-state merged --json
keel resume .keel/project.yaml --root . --live-worktree-state missing --json
```

`STATE` values are adapter-supplied live-state reconciliation hints:

- PR state: `unknown`, `missing`, `open`, `merged`, or `closed`
- worktree state: `unknown`, `present`, or `missing`

`status: no-checkpoint` means there is nothing to resume. `status: ambiguous` exits
non-zero and includes warnings plus the reconciliation action, for example when a
checkpoint references a PR or worktree that live state reports missing. If the PR is
already merged, the plan resumes at capture or closeout and never repeats the merge.

## `keel status <project.yaml> [--root DIR] [--json]`

Show the operator-facing progress snapshot for the active or most recent run. `status`
reads the resumable checkpoint and the run ledger (same paths as `keel checkpoint` and
`keel ledger`, resolved under `--root DIR`, default `.`) and renders a single
`keel.progress-status.v1` snapshot. It never mutates files, git, GitHub, or reports.

```bash
keel status .keel/project.yaml --root .
keel status .keel/project.yaml --root . --json
```

The snapshot is taken at the **last safe step boundary**, not in real time. It reports an
overall run `status` (`no-active-run`, `active`, `waiting`, `interrupted`, or `completed`),
the current run's issue, step, wait reason, PR, branch, and worktree, the next queued issue
from the checkpoint queue, ledger counts of shipped / blocked / deferred / skipped runs, and
capture-health gaps. A missing checkpoint or ledger is not an error — the snapshot degrades
to `no-active-run`/`completed` and empty counts — but an invalid checkpoint or corrupted
ledger exits non-zero, since adapters must not report progress from corrupted state.

## `keel morning <project.yaml> [--root DIR] [--since WHEN] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--json]`

Render the standalone daily-brief contract for a project. The core owns the generic
brief shape: date/window context, deferral queue, shipped-since-last-brief and GitHub
status sections, project health provider metadata, priority sources, and report
destinations. Project-specific health commands and focus signals stay in
`policy_pack.health_providers`, `policy_pack.reports`, and extensions. Morning also
receives the structured run-ledger contract and can use `keel ledger` to read recent ship
outcomes without parsing closure comments.

```bash
keel morning .keel/project.yaml
keel morning .keel/project.yaml --since yesterday --json
keel morning .keel/project.yaml --live --approve-scope filesystem --operator "$USER" --json
```

Dry-run mode never runs project health commands and never writes reports. Missing optional
health-provider capabilities are shown as unavailable/degraded, not as a successful empty
health section. Live mode is only a preflight contract; adapters perform approved report
writes or provider execution after checking consent.

## `keel wrap <project.yaml> [TITLE] [--root DIR] [--since WHEN] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--json]`

Render the standalone session-wrap contract. The core owns the generic session closeout
shape: linked-worktree and base-branch guards, configured gate source, Conventional Commit
and `Closes #N` conventions, ready PR creation requirements, session recap destination,
and the shared deferral queue. Project-specific changed-file gates and recap destinations
stay in policy packs and extensions. Wrap also receives the run-ledger contract so session
recaps can include structured ship outcomes offline.

```bash
keel wrap .keel/project.yaml --json
keel wrap .keel/project.yaml "feat: finish queue contract" --live --approve-scope filesystem,git,github --operator "$USER" --json
```

Dry-run mode never runs gates, commits, pushes, opens PRs, or writes reports. Live mode is
only a preflight contract; adapters perform approved session closeout work after checking
consent and GitHub transport support.

## `keel work-block <project.yaml> [issues…] [--root DIR] [--queue SELECTOR] [--max N] [--hours H] [--review-comments inline|summary] [--reviewers 1|2|3] [--target TEXT] [--dry-run] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--json]`

Render the standalone daytime multi-issue work-block contract. The core owns the generic
`keel.work-block.v1` queue primitive: explicit issue numbers (processed in the order given)
or a `--queue SELECTOR` (ordered by priority then issue number), snapshotted once per session
with readiness re-checked between issues. Each item is handed off to `ship` in an isolated
branch/worktree, inheriting the operator consent scope and honoring the same capture, run
ledger, merge-lock, and merge-window contracts as a standalone ship.

```bash
keel work-block .keel/project.yaml 76 82 91 --json
keel work-block .keel/project.yaml --queue ready --max 3 --hours 6 --json
keel work-block .keel/project.yaml 76 --live --approve-scope filesystem,git,github --operator "$USER" --json
```

`--max` caps how many issues are attempted, `--hours` sets an optional time budget, and
`--review-comments` / `--reviewers` pass through to the per-issue ship handoffs. The contract
shares its queue primitive with `overnight`; the daytime mode lets the operator redirect
between items, while a blocked item stops the daytime block instead of continuing. Final
reporting buckets each issue as shipped, PR-open-not-merged, deferred, blocked, skipped, or
needs-input. Stop conditions include queue exhaustion, the max/time budget, an operator
pause, a consent gap, a non-ready or blocking finding, and merge-window close.

Dry-run mode never spawns ship runs, creates PRs, merges, or writes reports. Live mode is
only a preflight contract; adapters hand the approved consent scope to each ship delegate and
keep merge-window and merge-lock enforcement shared with `keel ship`.

## `keel overnight <project.yaml> [hours] [--max N] [--review-comments inline|summary] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--json]`

Render the standalone overnight-session contract. The core owns the generic unattended
session shape: merge-window mode from `keel window`, ship handoff, per-issue worktree
isolation, no-night-merge policy, blocker policy boundary, priority queue shape, session
or morning report destinations, stop conditions, and the shared deferral queue.

```bash
keel overnight .keel/project.yaml 8 --max 3 --json
keel overnight .keel/project.yaml --live --consent-mode standing --json
```

Dry-run mode never spawns ship runs, creates PRs, merges, or writes reports. Live mode is
only a preflight contract; adapters hand approved consent scope to ship/implementer
delegates and keep merge-window enforcement shared with `keel ship`.

## `keel regression <project.yaml> [--scope full|changed|since] [--since REF] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--json]`

Render the standalone scan-and-file regression contract. The core owns the generic scan
shape: canonical base scan target, clean-tree preflight, read-only worktree requirement,
area fan-out source, confidence filtering, issue dedupe, issue-create lock, ship handoff,
and final report sections. Project-specific areas, labels, branch patterns, and thresholds
stay in `policy_pack.scan`.

```bash
keel regression .keel/project.yaml --scope full --json
keel regression .keel/project.yaml --scope since --since origin/main --json
keel regression .keel/project.yaml --live --approve-scope filesystem,git,github --operator "$USER" --json
```

Dry-run mode never opens issues, edits code, pushes, or merges. Live mode is only a
preflight contract; adapters perform approved issue creation after checking consent and
GitHub transport support.

## `keel review-all-day <project.yaml> [days] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--json]`

Render the standalone time-window scan-and-file contract. The core owns the generic
time-window scan shape: merge-window timezone inputs, trunk plus active work branch scope,
remote-ref default, batch/fan-out threshold, file-boundary diff truncation, serious-finding
filter, exact issue title prefix, issue creation, and final report sections. Project-specific
active branch patterns, labels, and thresholds stay in `policy_pack.scan`.

```bash
keel review-all-day .keel/project.yaml --json
keel review-all-day .keel/project.yaml 7 --json
keel review-all-day .keel/project.yaml 1 --live --approve-scope github --operator "$USER" --json
```

Dry-run mode never opens issues, pushes, edits code, comments on PRs, or merges. Live mode
is only a preflight contract; adapters perform approved issue creation after checking
consent and GitHub transport support.

Example output:

```
keel plan — example-flutter
  base_branch: main   core_version: ^1.0
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

## `keel ship <project.yaml> [--root DIR] [--pr N] [--compound|--profile standard|compound] [--dry-run] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--target TARGET] [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL] [--review-comments inline|summary] [--reviewers 1|2|3] [--jury|--no-jury] [--jury-advisory] [--json]`

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
missing. `--approve-scope` can be repeated or comma-separated. Consent mode resolves as
`--consent-mode` > `KEEL_CONSENT_MODE` > project `consent_mode` > built-in `explicit`.
Trusted unattended runs may set `KEEL_APPROVE_SCOPE=filesystem,git,github` and
`KEEL_OPERATOR=automation:nightly`, or use `automation.approved_scopes` in project config
with `automation.operator`, when the resolved mode is `standing`. Env/config standing
approval requires an operator identity. Dry-run and read-only commands ignore standing
approval values. Approved live runs include a local
`consent_record` in JSON output with the approval source; secret values are never recorded.

Review and merge-gate parity is exposed through `review_merge_contract` in JSON output.
`--review-comments` selects inline or summary posting, `--reviewers` overrides the
risk-derived reviewer count, and jury precedence is `--no-jury` over `--jury` over tier-3
auto-jury over off. `--jury-advisory` keeps an enabled jury report-only. No-jury mode still
preserves the reviewer, CI, tester, merge-window, merge-lock, closeout, and capture gates.

Adapters should pass the selected issue text with `--issue-title`, `--issue-body`, and
`--issue-label` before branch/worktree creation. JSON output then includes
`contract.issue_intake` and `result.issue_intake`. If an explicitly supplied issue is
`needs-input`, `blocked`, or `out-of-scope`, `keel ship --live` exits non-zero before
running gates or mutating code, and the intake block contains the questions or skip reason.
Dry-run output records the same readiness decision without blocking inspection.

```bash
keel ship .keel/project.yaml --root .
keel ship .keel/project.yaml --root . --live --json
keel ship .keel/project.yaml --root . --live --approve-scope filesystem,git,github --operator "$USER" --target "issue #123" --json
keel ship .keel/project.yaml --root . --issue-title "Add setup docs" --issue-body "$ISSUE_BODY" --issue-label enhancement --json
KEEL_APPROVE_SCOPE=filesystem,git,github KEEL_OPERATOR=automation:nightly keel ship .keel/project.yaml --root . --live --consent-mode standing --json
# keel ship — keel  (base main)
#   changed files : 53
#   risk tier     : TIER-3  → 3 reviewer(s)
#   review posts  : inline
#   jury          : gating (tier-3 auto)
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
dry assessment. `result.artifact_bodies` contains canonical Markdown bodies for the PR body,
issue update, reviewer verdict, jury verdict, and extension result output; adapters should
post those rendered bodies verbatim when available instead of hand-authoring project-specific
variants. `--dry-run` is accepted for adapter clarity; this CLI command is already
non-mutating.

### `--compound` (compound workflow profile)

`--compound` (or `--profile compound`) runs the same deterministic ship assessment through
the **compound** workflow profile. The compound profile is a first-class profile of `ship`,
not a separate command: it shares the same backbone and safety gates, but its JSON contract
carries `workflow_profile.profile: "compound"` and step overrides for compound `implement`,
`review`, `fixloop`, and `capture`.

```bash
keel ship .keel/project.yaml --root . --compound --dry-run --json
# contract.workflow_profile.profile == "compound"
# contract.workflow_profile.inherits == "ship"
```

The same compound contract is available from `keel plan` via `--profile compound`:

```bash
keel plan .keel/project.yaml --root . --command ship --profile compound --json
```

Omit the flag (or pass `--profile standard`) for the standard delivery path; use
`--compound` when the operator wants the compound-engineering flavor while retaining the same
CI, review, merge-window, merge-lock, closeout, and capture safety gates.

## `keel implement <project.yaml> <issue> [--root DIR] [--delegate AGENT] [--dry-run] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL] [--json]`

Render the standalone implement-step contract for one issue. This is the direct-use form of
the s4 implement step: it resolves project config, implementer routing, branch/worktree
planning, consent scopes, and the handoff target without running the full ship lifecycle.

```bash
keel implement .keel/project.yaml 76 --root . --dry-run --json
keel implement .keel/project.yaml 76 --root . --live --consent-mode standing --approve-scope filesystem,git,github --operator "$USER" --json
keel implement .keel/project.yaml 76 --root . --live --issue-title "Add setup docs" --issue-body "$ISSUE_BODY" --issue-label enhancement --approve-scope filesystem,git,github --operator "$USER" --json
```

`implement` may create branches, worktrees, commits, pushes, PRs, and comments only in an
approved live adapter run. Its contract explicitly marks standalone implement as a
non-merge path and points the next step at `ship` or `pr-loop`.
When issue context is supplied, `contract.issue_intake` applies the same readiness gate as
`ship`: non-ready issues stop before branch/worktree creation or delegation, and the
generated questions or skip reason remain machine-readable.

## `keel ci-check <project.yaml> [--root DIR] [--pr N] [--json]`

Render the standalone CI diagnostic preflight contract. `ci-check` uses the shared runtime
capability and GitHub transport resolution, declares the latest-run context and diagnostic
result shape an adapter must produce, and stays read-only: the adapter diagnosis may propose
one fix, but this CLI surface never edits, pushes, re-runs, posts comments, or merges.

```bash
keel ci-check .keel/project.yaml --root . --pr 104 --json
```

The JSON result records the configured workflow map, latest-run context shape, available
transport, supported diagnostic classifications, one-fix policy, and next-command
recommendations.

## `keel init [--root DIR] [--force]`

Scaffold a default `.keel/project.yaml` for the repo. keel detects the stack from marker
files (`pubspec.yaml`→Flutter, `pyproject.toml`/`setup.py`→Python, `package.json`→Node,
`build.gradle*`→Android, else generic) and writes a config that already passes
`keel validate`. Refuses to overwrite an existing config unless `--force`.

`--force` replaces `.keel/project.yaml`; it does not delete or rewrite `.keel/extensions/*`.
Use it only when intentionally regenerating project config.

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
| `plugin` | `commands/<cmd>.md` (repo root) | the committed [Claude Code plugin](plugin.md) — `/plugin install keel` exposes `/keel:<cmd>` |

```bash
keel install-adapter claude          # → /keel:ship, /keel:regression, …
keel install-adapter skills          # → one shared keel-<cmd> skill set under .agents/skills/
keel install-adapter all             # both surfaces
keel install-adapter claude --force  # overwrite existing adapters
keel install-adapter plugin          # regenerate the committed plugin command files (commands/)
```

The `plugin` target is **repo-level**, not per-project: it regenerates the committed
`commands/<cmd>.md` files that the Claude Code plugin ships (see [plugin.md](plugin.md)).
`make plugin` is the same command; a drift test fails if the committed files diverge from the
`src/keel/adapters/commands/` source bodies.

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
`flake-audit`, `coverage`. Existing files are skipped unless `--force` (so your
edits are never clobbered).

The generated surface is covered as a release contract: tests install into a clean temporary
project, verify that every packaged command has a matching Claude command and shared skill,
validate skill frontmatter, check idempotent skip / `--force` overwrite behavior, and scan the
generated files for consumer-specific strings. PyPI release smoke tests can reuse the same
`keel install-adapter all --root <tmp-project>` flow.

Generated adapter files carry a trailing `keel-generated` marker with the surface, command,
keel version, source hash, and generated-body hash. That marker powers the safe update flow:

```bash
pipx upgrade keel-workflow
# or: python -m pip install --upgrade keel-workflow

keel adapter-status all --root <repo>
keel update-adapter all --root <repo> --dry-run
keel update-adapter all --root <repo>
keel sync --root <repo> --dry-run
keel sync --root <repo>
```

`adapter-status` reports:

| status | meaning |
|---|---|
| `current` | installed generated file matches the packaged source |
| `outdated` | installed generated file is unchanged locally, but packaged source changed |
| `missing` | expected generated file is absent |
| `locally-modified` | generated file has a marker, but its body changed after install |
| `unknown` | file exists without a keel generated marker |

### Orphan & unmanaged surfaces

`adapter-status` also scans the managed surface directories (`commands/`,
`.claude/commands/keel/`, `.claude/commands/`, `.agents/skills/keel-*`,
`.agents/skills/source-command-*`) for files keel no longer manages and reports them in two
deliberately separated confidence classes. Both are **advisory and diagnostic only** — keel
never auto-deletes a file and these findings never gate a run.

| category | flag | meaning |
|---|---|---|
| `orphan` | always on | a file carrying a `keel-generated` marker whose `command=` is **not** in the installed keel command set — e.g. a `.agents/skills/keel-ship-v2/` left behind after the `ship-v2` command was removed. Fully decidable from the marker; reason code names the unknown command (`stale-marker: command 'ship-v2' not in installed keel`). |
| `unmanaged` | `--include-unmanaged` | a command-like file with **zero** keel markers (e.g. a stray repo-root `commands/*.md` body). Heuristic and opt-in only, because it cannot be cleanly distinguished from a legitimate project-only command. Commands the project declares as project-only (via `policy_pack.project_commands` / `policy_pack.command_routing` in `.keel/project.yaml`) are **never** flagged. |

```bash
keel adapter-status all --root <repo>                     # freshness + orphan (stale-marker)
keel adapter-status all --root <repo> --include-unmanaged  # also marker-less surfaces
keel adapter-status all --root <repo> --json               # machine-readable (adapters + orphans)
```

`keel sync` (and `update-adapter`) prints a one-line heads-up when any orphan/unmanaged files
are present — `N unmanaged keel-like file(s) found — run keel adapter-status for details` — so
the count surfaces during routine refreshes without changing what `sync` writes. Acting on the
findings is always a human decision; no `keel` command removes these files.

`sync` is the short everyday name for the same safe adapter refresh as
`update-adapter all`. It updates only `missing` and `outdated` generated adapter files. It
refuses to overwrite `locally-modified` or `unknown` files; those need a human merge.
`--dry-run` prints the same planned changes as `would-update` rows without writing. Adapter
updates never touch project-owned config, `.keel/extensions/*`, project-provided commands,
or local compatibility wrappers unless those files are explicitly marked as generated keel
adapter surfaces.

`sync` uses the keel package that is already installed in the active Python environment. It
does not contact PyPI, choose the latest version, or change the package installation. Upgrade
`keel-workflow` with `pipx`/`pip` first, then run `sync` from the consumer repository.

If a new keel release adds a command, `adapter-status` reports its generated files as
`missing` and `sync` creates them. If a packaged adapter step changes, unchanged generated
files become `outdated` and `sync` refreshes them. If a release changes the project config or
extension contract, that is not an adapter sync; it must be handled as a documented migration
and verified with `keel validate .keel/project.yaml --root .` plus
`keel plan .keel/project.yaml --root .`.

Extension schema migrations are separate from adapter command updates and must be documented
as their own versioned migration.

## `keel install-legacy-wrappers <target> [--root DIR] [--command LEGACY=KEEL]`

Install thin compatibility shims for old command names after the parity matrix proves that
the corresponding `/keel:<command>` adapter is ready. This is the staged cutover tool for
projects that still expose legacy commands such as `/ship` or `source-command-ship`.

| target | installs into | generated wrapper |
|---|---|---|
| `claude` | `.claude/commands/<legacy>.md` | native legacy slash command that delegates to `/keel:<command>` |
| `skills` | `.agents/skills/source-command-<legacy>/SKILL.md` | shared non-Claude skill wrapper that delegates to `keel-<command>` |
| `all` | both of the above | |

```bash
keel install-legacy-wrappers all --command ship=ship
keel install-legacy-wrappers skills --command ship=ship --command morning=morning
keel install-legacy-wrappers all --force
```

By default, the command reads `docs/keel/parity-matrix.md`. Only rows whose status is
`parity-proven` or `deferred` may generate wrappers; missing or in-progress rows are blockers.
Use `--parity-matrix <path>` when running from another checkout or a downstream migration
tracking document. Existing files are skipped unless `--force`, so a project can keep its old
body until the replacement wrapper is reviewed.

The wrapper template intentionally contains no workflow copy. It preserves the user's original
issue/PR target and flags, including dry-run, jury/no-jury, review-comment mode, merge
behavior, and issue/PR targeting, then delegates to the installed keel adapter. The generated
files carry a `keel-generated` marker on the `legacy-*` surfaces so adapter updates and local
compatibility shims remain distinguishable.

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | a config was invalid/missing, or a plan target could not be loaded |
| 2 | no command given (help printed) |
