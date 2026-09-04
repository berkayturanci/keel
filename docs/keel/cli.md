# keel CLI reference

```
keel <command> [options]
keel --version
```

> This is the quick reference. For the exhaustive per-flag detail — every parameter's
> type, default, precedence/interaction rules, consent implications, and worked examples
> (including the `/keel:ship` adapter argument grammar) — see
> [`parameter-reference.md`](parameter-reference.md).

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

## `keel plan <project.yaml> [--root DIR] [--command COMMAND] [--tier 1|2|3] [--role LABEL] [--delegate PROVIDER] [--review-delegate PROVIDER]... [--effort low|medium|high] [--team PROFILE] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--target TARGET] [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL] [--run-id ID] [--issue N] [--pull-request N] [--json]`

Render the backbone plan for a project: the fixed steps with the project's built-in gates
and extensions slotted in. This is the dry-run view — what an actual run would execute.

`--root DIR` (default `.`) is where extension files are resolved. Extensions that can't be
loaded are reported as warnings on stderr (fail-soft) and the plan still renders with the
built-in gates.

**Activity auto-stamp (1.6.4+).** Pass `--run-id ID` (with optional `--issue`/`--pull-request`)
and `plan` also writes the run's `keel.flows` first phase to `.keel/activity/<run-id>.json`,
so the run shows up on the [keel-visual](keel-visual.md) board **the moment it plans** —
without depending on the agent's per-phase `keel activity` calls. Every command runs `keel
plan` at Step 0, so this makes runs reliably visible. `run-gates` and `merge` advance the
same record (s8, s10). Fail-soft and opt-in: a plain `keel plan` with no `--run-id` stays a
pure read.

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

## `keel claim RESOURCE --owner ID [--root DIR] [--json]`

Acquire a single-host resource claim backed by the same atomic `mkdir` primitive used by
core merge execution. A denied claim exits non-zero and reports the current holder when it
is known.

```bash
keel claim merge --owner "ship-pr-123" --root . --json
keel release merge --owner "ship-pr-123" --root .
```

## `keel release RESOURCE [--owner ID] [--root DIR] [--json]`

Release the claim `keel claim` took. `--owner` scopes the release to one holder; omitting
it is the deliberate any-owner escape for clearing a claim whose holder is gone.

```bash
keel release merge --owner "ship-pr-123" --root .
keel release merge --root . --json      # clear a stuck claim, whoever holds it
```

Exit 0 covers two statuses, because both are the state the caller asked for: `released`,
and `missing` — releasing a resource nobody holds is not a failure. A named `--owner` that
does not match the recorded holder exits 1 with status `not-owner` and reports the holder;
an *unidentifiable* holder refuses a named release the same way.

## `keel guard <project.yaml> [--issue NUMBER] [--issue-title TITLE] [--issue-labels L1,L2] [--root DIR] [--json]`

Evaluate an issue against the **deterministic blocker ruleset** and report which
rule(s), if any, fired. Blocker promotion is what unlocks the night-window bypass at
s10 (`keel merge --hotfix`); `keel guard` makes that promotion a verifiable function of
the issue's title and labels instead of pure agent judgment (audit GAP-11).

The matching is pure: given the issue title, its labels, and the configured rules, it
returns the matched rule ids. Rules come from `policy_pack.blocker_rules` when present and
fall back to built-in defaults when absent (back-compatible). The defaults are:

| Rule id               | Kind          | Fires when                                            |
|-----------------------|---------------|-------------------------------------------------------|
| `blocker-label`       | `label`       | issue carries a `blocker` label (case-insensitive)    |
| `hotfix-label`        | `label`       | issue carries a `hotfix` label                        |
| `security-label`      | `label`       | issue carries a `security` label                      |
| `blocker-title-regex` | `title-regex` | title matches `\b(?:hotfix\|security\|blocker)\b`       |

Issue facts are read live from the host with `gh issue view` when `--issue` is given
(fail-soft: a failed fetch falls back to the offline `--issue-title` / `--issue-labels`
flags). Offline, pass the flags directly.

```bash
keel guard .keel/project.yaml --issue-title "hotfix: patch the boot loop" --json
# -> matched: ["blocker-title-regex"], is_blocker: true
keel guard .keel/project.yaml --issue 42        # live fetch of title + labels
```

Override the defaults under `policy_pack.blocker_rules` (each rule needs an `id` and a
`kind` of `label` (with `labels`) or `title-regex` (with `pattern`)); see
[configuration.md](configuration.md).

## `keel merge <project.yaml> --pr N [--root DIR] [--method squash|merge|rebase] [--dry-run] [--effort low|medium|high] [--team PROFILE]`

Perform the sanctioned core-owned PR merge path. `keel merge` acquires the merge resource
claim, re-checks the merge window inside that claim, reads the live PR check rollup with
failure-before-pending precedence, runs `evidence-verify` against the current PR artifacts,
requires a SHA-stamped gates-pass for the PR's current head, and only then calls
`gh pr merge`.

The gates-SHA check reads the run ledger and requires a `ship_run` record whose
`pull_request.number` matches the PR, whose `git.head_sha` equals the PR's current head
(from the live merge snapshot), and whose gates passed (verdict not blocked and every
recorded gate `ok` or `skipped`, none errored). A stale green run from an older head no
longer authorizes a merge of a newer head; if no record matches, the merge refuses with
`no gates-pass recorded for the current head <sha>`. The decision is reported in the
`gates_sha` block of the JSON payload (`matched`, `head_sha`, `run_id`).

Once the merge lands, `keel merge` runs the [`verify-merge`](#keel-verify-merge-projectyaml---root-dir---pr-n---merge-sha-sha---json)
drift check on it and reports the result as `merge_verification` in the payload and a
`drift :` line in human output. Exit codes: **0** merged and clean, **1** the merge did not
happen, **3** it happened *and* may have written over work another PR merged after this one
branched — a distinct code because "fail" on a landed merge reads as "retry it". On drift
the overtaking pull request is named in full, since the merge is already irreversible and
the operator has to act on it now. Before #934 this check was documented as running after
s10 and was called from nowhere; a stale-base squash reverted #811 on main and nothing
noticed for six days.

Raw adapter `gh pr merge` calls are a spec violation for ship-style flows: adapters should
delegate s10 to this command so lock, window, CI, evidence, and gates-SHA checks are
deterministic.

```bash
keel merge .keel/project.yaml --root . --pr 123 \
  --approve-scope filesystem,git,github --operator "$USER"
keel merge .keel/project.yaml --root . --pr 123 --dry-run \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

`--hotfix` is the audited bypass: it skips both the merge window and the gates-SHA
requirement, still requires explicit consent, and records the bypass (`gates_sha.bypassed`
with `reason: hotfix`).

A `--hotfix` bypass now **requires a recorded justification** (audit GAP-11) — without
one it is refused before any merge work. Provide exactly one of:

- `--blocker-rule <id>` — a [`keel guard`](#keel-guard-projectyaml---issue-number---issue-title-title---issue-labels-l1l2---root-dir---json)
  rule id that **actually fires** for this issue. The merge re-evaluates the ruleset
  against the issue's title/labels (`--issue` for a live fetch, or `--issue-title` /
  `--issue-labels` offline) and refuses if the named rule is unknown or did not match.
  Recorded as `hotfix_justification: {kind: "matched-rule", rule_id, matched}`.
- `--operator-override` paired with a named `--operator` — the audited human override for
  a genuine emergency that no rule covers. Recorded as
  `hotfix_justification: {kind: "operator-override", operator}`.

The justification is written into the merge payload (and surfaced in the ledger), so every
night-window bypass carries machine-checkable evidence of why it was allowed.

### Checkpoint gate (audit GAP-13)

After the gates-SHA check and before the merge, `keel merge` requires a **covering
checkpoint** for the run at the merge step (`s10`). This closes the hazard where a run that
never wrote a checkpoint is undetectable: a crash mid-`s10` would otherwise leave the next
session a clean slate that can re-merge or duplicate comments. The decision is pure
([`keel.checkpoint.covering_checkpoint`](../../src/keel/checkpoint.py)); the CLI only reads
the checkpoint file. The result is reported in the `checkpoint_gate` block of the payload:

- **`covered`** — a current checkpoint for this run reached `s10` (its `current_step` is at
  or past `s10`, or `s10` is in `completed_steps`). The merge proceeds.
- **`missing`** — no checkpoint, or the checkpoint is for a different run. Refused with
  `no current checkpoint for run <id> at step s10`.
- **`stale-step`** — a checkpoint for this run that has not reached `s10`. Refused.

The run-id is the gates-SHA `run_id` by default; override it with `--run-id`.

**Back-compat / degrade gracefully.** The gate is enforced **only when checkpointing is
configured** for the project (`policy_pack.reports.checkpoint`). When that key is absent the
gate is **advisory** (`status: advisory-skip`) and the merge proceeds — flows that never
write checkpoints keep working unchanged.

`--no-checkpoint-gate` is the audited escape (mirroring `--operator-override`): it bypasses
the gate for callers that legitimately do not checkpoint. It **requires a named
`--operator`** and records the bypass in the merge payload
(`checkpoint_gate: {status: "bypassed", operator}`).

## `keel attribution --vendor VENDOR [--model MODEL] [--profile NAME] [--config FILE] [--json]`

Print keel's own attribution labels for a delegate vendor/model pair. This is the **only**
sanctioned way for an adapter to learn what to label a PR with: `keel.agents.attribution()`
defines the vocabulary, and re-deriving it in prose is what produced `agent:gemini` /
`model:gemini` for a run keel calls `agent:agy` / `model:gemini-3` (issue #1013).

```bash
keel attribution --vendor agy --model gemini-3.8-flash-high
# agent_label   : agent:agy
# model_label   : model:gemini-3
# system        : agy:gemini-3.8-flash-high

keel attribution --vendor agy --model gemini-3.8-flash-high --json
# {"agent_label": "agent:agy", "model_label": "model:gemini-3", "system": "agy:gemini-3.8-flash-high"}
```

`--json` prints the attribution record itself, so it can be consumed directly (it is the
same shape as the `attribution` block a delegate result carries). Apply `agent_label` and
`model_label` to the PR verbatim, and record `system` as the run's implementer string.

`--model` is optional: without it there is no `model_label` (human output prints
`not recorded`, JSON prints `null`) — attribute no model rather than one that was merely
asked for.

`--profile NAME` names the `knobs.delegate_profiles` entry that ran and requires
`--config`. The result then carries `delegate_profile`, so the s11 closure can say *which*
CLI ran rather than just `cli`, and the model falls back to the profile's own `model` when
`--model` is omitted. A `--vendor` that contradicts the profile's `vendor` is refused
rather than silently overridden.

`--config` also switches on vendor validation: a vendor that is neither a built-in delegate
vendor nor a configured delegate profile is refused. Without `--config` any vendor is
accepted, because the run ledger carries values written by older runs and a lookup with no
project config cannot tell a legacy value from a typo.

## `keel post-comment <project.yaml> --target issue:N|pr:N --artifact ARTIFACT --body-file FILE [--run-id ID] [--dry-run] [--json]`

Post or update a deterministic GitHub issue/PR artifact comment. `post-comment` reads the
rendered Markdown from `--body-file`, validates that it contains the marker required by
`--artifact`, resolves the selected GitHub transport, and then posts through the GitHub
issue-comments API. PR conversation comments use the same endpoint as issue comments, so
`--target pr:N` still lands in the PR timeline.

```bash
keel post-comment .keel/project.yaml --root . \
  --target pr:456 --artifact review-verdict \
  --body-file /tmp/review-verdict.md --run-id "$RUN_ID"
keel post-comment .keel/project.yaml --root . \
  --target issue:123 --artifact closure-comment \
  --body-file /tmp/closure-comment.md --run-id "$RUN_ID" --json
```

Supported artifacts are `ship-provenance`, `closure-comment`, `issue-update`,
`review-verdict`, `jury-verdict`, `review-cycle-summary`, `extension-result`,
`step-handoff`, and `run-control-halt`. When
`--run-id` is supplied, the command edits the latest existing comment that has the same
marker and run id; otherwise it posts a new comment. Bodies that are missing the expected
marker, or that look like a literal `@/tmp/...` placeholder, are rejected before any
public write.

When a run intentionally posts multiple comments for the same artifact type, use a stable
sub-key in the run id, for example `"$RUN_ID:reviewer-a"` and `"$RUN_ID:reviewer-b"` for
two review verdicts. Reusing the exact same marker and run id is an update request.

Raw adapter `gh issue comment`, `gh pr comment`, and hand-rolled comment API calls are a
spec violation for ship evidence artifacts: adapters should delegate closure comments,
issue updates, review verdicts, and jury verdicts to this command so marker validation,
transport selection, and same-run idempotency are enforced in core.

## `keel review-cycle-summary --findings FILE [--head-sha SHA] [--run-id ID] [--json]`

Render the deterministic multi-reviewer review-cycle summary comment from a structured
findings bundle, so `/keel:review-cycle` and `/keel:pr-loop` post a byte-stable consolidated
comment instead of improvising the layout. `--findings` is a JSON array of reviewer objects
(`{ "codename", "focus", "verdict", "findings": [{"severity","location","description",
"suggested_fix"}], "clean_areas": [...] }`). The renderer emits one section per reviewer plus a
Consolidated Summary whose severity histogram — not the verdict strings — drives the merge
recommendation (`critical` folds into `blocker`). It prints the rendered body to stdout (or a
`{marker, reviewers, body}` object under `--json`); post it with `keel post-comment --artifact
review-cycle-summary`. When `--run-id` is supplied an invisible `keel.run-id` marker is embedded
so an idempotent re-post edits the existing comment in place.

```bash
keel review-cycle-summary --findings cycle.json --head-sha "$SHA" \
  --run-id "$RUN:cycle-summary" > summary.md
keel post-comment .keel/project.yaml --root . --target pr:456 \
  --artifact review-cycle-summary --body-file summary.md --run-id "$RUN:cycle-summary"
```

## `keel render-report --kind coverage|deps-audit|flake-audit|scan-finding|triage-audit --payload FILE [--json]`

Render a deterministic reporting body from a structured JSON payload, so the audit/report
adapters post byte-stable output instead of composing tables in prose. `--payload` is a JSON
object of the selected renderer's fields; the command prints the rendered Markdown to stdout
(or a `{kind, marker, body}` object under `--json`). The five kinds and their markers:

| `--kind` | renderer | marker | used by |
|---|---|---|---|
| `coverage` | `render_coverage_delta` | `keel.coverage-delta.v1` | `/keel:coverage` |
| `deps-audit` | `render_deps_audit` | `keel.deps-audit.v1` | `/keel:deps-audit` |
| `flake-audit` | `render_flake_audit` | `keel.flake-audit.v1` | `/keel:flake-audit` |
| `scan-finding` | `render_scan_finding_issue` | `keel.scan-finding.v1` | `/keel:regression`, `/keel:review-all-day` |
| `triage-audit` | `render_triage_audit` | `keel.triage-audit.v1` | `/keel:triage` |

The reporting kinds keep their load-bearing codename (`COVERAGE-<PR>-…` etc.) as the rendered
body's first line so the adapter's find-by-prefix idempotency still works; the timestamped
codename is supplied in the payload, so the renderer stays pure.

```bash
keel render-report --kind coverage --payload coverage.json > body.md
keel render-report --kind triage-audit --payload audit.json --json
```

## `keel review <project.yaml> --pr N (--reviews FILE | --from-jury FILE) [--root DIR] [--issue N] [--closure FILE] [--reviewers 1|2|3] [--jury] [--no-jury] [--jury-advisory] [--head-sha SHA] [--changed-file PATH] [--run-id ID] [--verify] [--dry-run] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--effort low|medium|high] [--team PROFILE] [--json]`

Orchestrate a supplied review *evidence bundle* in one deterministic command. The host
agent runs the actual reviewers and produces the review content; `keel review` is **not**
an agent spawner — it takes that content and collapses the previously hand-done
`render_review_verdict` + N× `post-comment` + `evidence-verify` sequence into a single
idempotent step.

`--reviews` is a JSON array of review objects, each shaped
`{ "reviewer": str, "verdict": str, "scope": str?, "findings": [{"severity","message"}]?,
"testing": str?, "vendor": str?, "model": str? }`. Each review is rendered via
`keel.artifacts.render_review_verdict`, head-pinned to the PR's current head SHA, and posted
to the PR through the same post-comment path with a stable per-reviewer run-id sub-key
(`<run-id>:rv-<reviewer-slug>`). When a review item carries `vendor` (and optionally
`model`), those are rendered as structured `vendor:` / `model:` provenance lines on the
verdict so a later `evidence-verify --require-distinct-vendors` can enforce that the required
verdicts came from distinct vendors. Provenance is omitted entirely when not supplied, so the
default verdict rendering is unchanged.

The required reviewer count is resolved from the live diff tier using the exact same logic
`keel evidence-verify` uses (`ship.resolve_review_contract`). If fewer reviews are supplied
than the tier requires, the command fails rather than silently under-posting evidence; an
exact count or more is allowed. `--reviewers` overrides the required count.

### Jury flags — `keel review` resolves the same contract as every other surface

`--jury` / `--no-jury` / `--jury-advisory` are accepted here and mean exactly what they mean
on `keel ship`, `keel plan`, `keel step-verify`, `keel evidence-verify` and `keel merge`;
all six resolve the review contract through `ship.resolve_review_contract`, and the resolved
document is published as `review_contract` in `--json` output (and as the `jury:` line of the
human-readable summary), so the six can be checked against each other rather than assumed
equal. Until #1043 this command defined none of them and hardcoded them false, which made its
`--verify` report **bench-authoritative but jury-blind**: on a plain (non-panel) tier-3
project it could report `jury-verdict` as required while the `keel ship --no-jury` run that
produced the pull request was told never to post one.

The flags **never move the reviewer bench** — that is a pure function of config + tier + role
+ `--reviewers` / `--review-delegate` — so the set of verdicts this command posts is
identical with and without them. They own the jury line only. Precedence is unchanged and is
`ship.resolve_jury`'s: a `knobs.team` jury-panel tier > `--no-jury` > `--jury` > tier-3
auto-jury > off, with `team.jury.mode: advisory` able to make an *enabled* jury advisory.

`--from-jury` is orthogonal to all three: it decides *where the verdicts come from* (the
panel's ballots rather than a host bundle) and always posts the panel's own
`keel.jury-verdict.v1` consensus record, while the flags decide *whether the contract
requires a jury verdict*. `--from-jury` additionally declares the real panel size, which on a
jury-panel tier sizes the required verdict count. On such a tier the panel outranks the
flags entirely: `--no-jury` / `--jury-advisory` are recorded in `assignment.warnings` and not
applied, because the panel is that tier's only review.

Pass a run's ship flags through to its `keel review` call. `keel review --verify` re-runs
`evidence-verify` against the contract *this command* resolved, so `keel ship --no-jury`
followed by a bare `keel review --verify` asks two halves of one run for two different gates.

### `--from-jury FILE` — the ai-jury panel *is* the review

Exactly one of `--reviews` and `--from-jury` is required: the bundle is the host's, or the
panel's. `--from-jury` takes an **ai-jury JSON report** (`jury --format json`, report schema
1.1+, which carries the top-level `reviewers` ballot array) and maps it onto the same bundle:

- one head-pinned `keel.review-verdict.v1` per panelist, carrying the `vendor:` and `model:`
  that actually produced that ballot — the chair is not a panelist and does not get one;
- the panel's own `keel.jury-verdict.v1` consensus comment, in the same call, so ballots and
  verdict are pinned to the same head SHA by construction. It declares `panelists: <N>`
  beside `vendors: <N>`, which is how the panel's size reaches a later evidence check;
- a `panel` block in the `--json` result — the ballots, the distinct vendors, and the
  **verified** consensus findings in keel's severity vocabulary (`critical`/`major` ⇒
  `block`). That block is the s9 fix-loop input, so a panel's findings gate exactly as a
  host reviewer's do: write that block to a file and hand it to `keel fixloop brief
  --findings` to open the round — the block is a `{"findings": [...]}` envelope, which is
  a shape `--findings` reads as-is, so its array needs no reshaping.

`scope` and `testing` are synthesised from the ballot itself — the files it named, and what
the verification round upheld — because the JSON report carries no per-ballot prose. They are
written to satisfy `verdict_substance` by construction, so a clean ballot is still a
postable verdict.

On a tier whose `knobs.team` review policy is `jury`, the required verdict count *is* the
panel size, so a report with fewer ballots than the panel declared fails closed. A report
that carries no ballots at all is refused with the command that produces one, rather than
posting a thinner review.

```bash
# s7 on a `review: jury` tier: one panel run, then one posting call.
jury --format json --diff-file "$DIFF" -o ".keel/state/jury/$RUN_ID.json"
keel review .keel/project.yaml --root . --pr 456 \
  --from-jury ".keel/state/jury/$RUN_ID.json" --run-id "$RUN_ID" --live
```

```bash
# Dry by default: render and print what it WOULD post, no network.
keel review .keel/project.yaml --root . --pr 456 \
  --reviews /tmp/reviews.json --json

# Live: post each verdict plus the closure to the PR and linked issue, then re-verify.
keel review .keel/project.yaml --root . --pr 456 --issue 123 \
  --reviews /tmp/reviews.json --closure /tmp/ship-run.json \
  --verify --live --approve-scope github --operator "$USER"
```

`--closure` takes an optional `ship_run`-shaped JSON record, rendered via
`keel.closure.render_closure_comment` and posted to both the PR and `--issue` as the
`closure-comment` artifact (sub-key `<run-id>:closure`). `--verify` runs the
`evidence-verify` check after posting and folds its pass/fail into the result (and exit
code). `--head-sha` / `--changed-file` supply offline fixtures so dry runs stay fully
offline and deterministic.

Dry by default: renders the bundle and prints `DRY-RUN:` lines for each planned post with
no network writes. `--live` actually posts and is consent-gated exactly like other live
commands (`assert_operator_consent`). `--json` emits the full plan: rendered bodies, post
targets, resolved tier, required vs. supplied count, and the verify outcome.

## `keel worktree-remove WORKTREE [--root DIR] [--json]`

Safely remove a worktree after validating that the path is nested under the repository root
and appears in `git worktree list --porcelain`. The command refuses the repository root,
filesystem roots, paths outside the repository, and fabricated unregistered paths before
delegating to `git worktree remove --force`.

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
to infer status from closure comments. A run that never reached capture says so with
`--capture-status not-run`: the flag stays required, but the record carries no capture
marker and claims no outcome. Use it to re-record gates for a **rebased** PR — gates are
pinned to the head SHA, so a new head needs its own record, and a second record carrying a
marker would be refused (one capture marker per PR).

`--host-agent` and `--transport` (`gh`|`mcp`) record the s0 preflight **run context** on
the `ship_run` record so it becomes durable PR evidence. `--transport` defaults to the
transport keel resolved for the run when the flag is omitted (e.g. `gh` when an
authenticated `gh` CLI is present, otherwise the host GitHub MCP/API). `--profile`
(`standard`|`compound`) is threaded into the record, the jury mode is derived from the
resolved review contract, and the consent summary (status + approved scopes) is derived
from the existing `--operator`/`--approve-scope` inputs. The s11 closure comment renders
all of this as a deterministic **Run context** block (host agent / transport / profile /
jury / consent); missing fields degrade gracefully (`unknown`/`off`/`none`).

On a live append, a missing `--host-agent` emits a run-context warning by default. Add
`--strict-run-context` to fail instead of writing a ledger record when required run-context
fields would degrade. `--transport` is auto-filled from the resolved GitHub transport when
omitted, so adapters should not echo a stale transport value.

## `keel capture-verify <project.yaml> [--merged-pr <N>] [--from-transport] [--json]`

Verify that merged PRs have exactly one valid capture marker in the configured run ledger.
Missing, invalid, or duplicate markers make the command exit non-zero.

```bash
keel capture-verify .keel/project.yaml --root . --merged-pr 456 --json
```

### Reconcile cross-checks (GAP-8 hardening)

Passing `--merged-pr` alone is the offline back-compat path: the merged set is trusted and
only marker presence/validity is checked. To stop an agent from silently dropping a merged PR
from capture accounting by omitting it from the args, derive the authoritative merged set from
the transport instead:

```bash
keel capture-verify .keel/project.yaml --root . --from-transport --merged-since 2026-06-01 --json
```

`--from-transport` lists merged PRs from the host (`gh pr list --state merged`, narrowed by
`--merged-since`). `--merged-pr` still works and is *added* to the derived set (the union is
verified, so an explicit override can only widen, never shrink, the checked set).

When the transport query errors, the report sets `merged_pr_source.transport_failed: true`,
the status becomes **`transport-unavailable`** with `certified: false`, and the command exits
non-zero. This is not a failure of the PRs it *did* check — it is a refusal to certify. A
failed query leaves the derived set empty, so the union degenerates to exactly the
`--merged-pr` list the caller supplied, and the anti-shrink guarantee above evaporates: an
un-captured PR simply disappears from the accounting. An audit that could not observe must
say so rather than render like a clean one.

When the merged set is derived (or any reconcile input is supplied) three additive checks run:

- **missing-marker** — a merged PR with no valid capture marker in the ledger.
- **applied-without-artifact** — an `applied` capture lacking a durable artifact reference
  (recorded via `keel ship --capture-artifact <path|hash>`). `deferred`/`skipped` need none.
- **reviewer-count-mismatch** — the ledger's `actors.reviewers` count exceeds the evidence-side
  review-verdict count for that PR. Per-PR verdict counts come from the transport when deriving
  live, or from `--verdict-count PR=N` fixtures offline; a PR with no known count is advisory.

Offline fixtures for deterministic runs: `--merged-prs-json <file>` (a JSON array of
`{"number": N}`) substitutes for the transport query, and `--verdict-count PR=N` supplies
evidence-side counts. Any reconcile finding makes the command exit non-zero in addition to the
base marker semantics.

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

## `keel close-reconcile <project.yaml> --issue N [--issue N …] [--root DIR] [--ledger-jsonl FILE] [--offline] [--closed] [--status-done] [--json]`

Flag issues that were closed — or labelled done — without a ledger record attesting a
merge. Closing an issue is the cheap half of the workflow and the ledger is the expensive
half; when they disagree, the issue is the one that lied.

```bash
keel close-reconcile .keel/project.yaml --root . --issue 123 --issue 124
keel close-reconcile .keel/project.yaml --root . --issue 123 --json
```

Live is the default: the issue's closed/label state is read from `gh`, and the
merge-attesting `ship_run` records come from the ledger configured under `--root`.
`--ledger-jsonl` substitutes a JSONL fixture for that read. `--offline` makes no `gh`
call at all and uses only the supplied `--closed` / `--status-done` flags, which apply to
every `--issue` — that pair is for tests and back-compat, since live mode reads
host-authoritative state. The done label comes from
`policy_pack.status_transitions.done`, falling back to keel's default.

Exit 1 on any finding (and on a missing/invalid config or an unreadable ledger); 0 when
every observed issue is consistent with the ledger.

## `keel dryrun-verify <project.yaml> --run-id ID --issue N --before-json FILE [--root DIR] [--after-json FILE] [--json]`

Assert, after the fact, that a dry run left nothing behind: no new ledger record, no new
branch, no new PR. A dry run that mutates is the one failure a dry run cannot self-report.

```bash
keel dryrun-verify .keel/project.yaml --root . \
  --run-id ship-123-rehearsal --issue 123 --before-json before.json
```

`--before-json` is a snapshot captured **before** the rehearsal:
`{"ledger_run_ids": [...], "branches": [...], "pr_numbers": [...]}`. The after-snapshot is
gathered live by default — ledger run ids from the configured ledger, branches from
`git for-each-ref`, PRs from `gh pr list` scoped to the issue's ship-branch pattern — and
`--after-json` supplies it offline instead.

The after read is **fail-closed**: a corrupt ledger or a failed `git`/`gh` read exits 1
rather than reporting a clean diff, because an empty-on-error snapshot would mask a real
leak (`after − before = ∅`). Exit 1 on any leak, 0 when the rehearsal left no trace.

## `keel evidence-verify <project.yaml> --pr <N> [--issue <N>] [--effort low|medium|high] [--team PROFILE] [--json]`

Verify that a PR has the public evidence required by the ship contract before merge.

The gate is **provenance-armed** by default: it engages when deterministic ship provenance
is present, including a ship-style issue branch (`feature/issue-*`, `fix/issue-*`, etc.),
an existing `keel.review-verdict.v1` marker, a trusted `keel ship` assessment comment, a
ship-run ledger record, or the legacy `evidence_gate_label` knob (default `keel:ship`).
The assessment comment is provenance only; it does not satisfy any required evidence item.
A hand-authored PR without ship provenance reports `enforced: false`, `required: 0`,
status `pass` (exit 0). The only disarm path for ship provenance is the operator-applied
waiver label `keel:evidence-waived`, which is reported in the check output. When enforced,
the verifier is fail-closed and accepts only durable GitHub artifacts:

### Phases — `--phase {pre-merge,post-merge,all}`

Evidence is required in the phase that *produces* it, mirroring the step mapping
`stepverifier` already applies:

| phase | items | produced at | checked by |
|---|---|---|---|
| `pre-merge` | review verdicts, gating jury verdict | s7 / s8 | the s10 merge gate |
| `post-merge` | `closure-comment-pr`, `closure-comment-issue` | s11 | after the merge |
| `all` (default) | both | — | back-compat for existing callers |

The distinction is not cosmetic. The closure comment is a record of a completed ship, so
demanding it at s10 makes the backbone unsatisfiable: s10 refuses to merge without evidence
that s11 only writes after the merge. **The merge gate must therefore pass `--phase
pre-merge`**; the committed `keel-ship.yml` does. Run `--phase post-merge` once the closure
comments are posted.

An unknown `--phase` value is rejected rather than silently narrowing the requirement set.

### `--jury-vendors N` — the panel decides whether the jury gates

A gating jury is auto-enabled on tier-3, but a cross-vendor panel needs at least
`MINIMUM_JURY_VENDORS` (2) distinct vendors to produce cross-vendor consensus. Pass the
number that **actually took part** and a short panel downgrades `gating → advisory`, which
drops `jury-verdict` from the required set:

| `--jury-vendors` | jury mode | `jury-verdict` required |
|---|---|---|
| omitted | unchanged (panel not known yet) | per tier/flags |
| `0` | advisory | no |
| `1` | advisory | no |
| `2`+ | gating | yes |

**Except on a tier whose review policy is the panel** (`knobs.team`'s
`review.by_tier.<n>: jury`), where the downgrade is suppressed entirely and the verdict
stays required whatever the count. The table above describes a jury sitting *beside* a host
bench: downgrading is sound there because the bench still reviewed the change. A panel tier
has no bench behind it, so a short panel excusing itself from its own consensus record would
leave the run with the one artifact missing that says the panel was short. The shortfall is
reported instead — as `review-vendor-distinctness` from `evidence-verify`.

`0` is not a special case — it is the run where no agent returned output, which is how the
contract's "a jury that did not complete cleanly never gates" falls out of the same
comparison rather than needing its own branch.

The downgrade is recorded in the contract (`jury.downgraded`, `jury.participating_vendors`,
and the reason string), so the posted verdict can state the mode that was actually
enforced. An explicit `--jury-advisory` is *not* reported as a downgrade — it was never
gating — and `--no-jury` is untouched.

**Where the count comes from when the flag is omitted.** The verifier reads `vendors: <N>`
from a trusted, head-bound `keel.jury-verdict.v1` comment on the PR. The same comment
carries `panelists: <N>`, read the same way, which sizes the required reviewer count on a
tier whose panel *is* the review — as a **floor raised, never lowered**:
`max(declared, jury.min_vendors)`, so a verdict declaring a short panel cannot shrink what
the tier owes (see `docs/keel/evidence.md`). That is the only
channel available to a hosted runner: the run ledger and the jury artifact both live under
the gitignored `.keel/state/`, so CI can read neither, while PR comments are always
visible. `keel.artifacts.render_jury_verdict()` emits the field, inferring it from
`participants` when a count is not passed explicitly.

Precedence and failure modes:

- explicit `--jury-vendors` always wins;
- otherwise the **largest** count declared by a qualifying verdict is used, so a corrected
  re-post is not capped by an earlier partial run;
- a verdict that omits the field, sits on a stale head, or comes from an untrusted author
  is **not** read — the count stays undeclared and the jury mode is left alone. Only a
  verdict that actually states the panel size may relax the gate;
- a non-numeric or negative value is rejected the same way.

`0` is a real answer, not a missing one: it is the run where no agent returned output, and
it must downgrade rather than read as "unknown".

This lives in core rather than in adapter prose on purpose: the evidence gate derives its
`jury-verdict` requirement from `jury.mode`, so a mode that ignores the real panel makes
the gate demand a verdict the jury step would decline to treat as gating.

### `--require-armed`

With the gate unarmed there are no requirements, so the report passes having verified
nothing — and a green `keel evidence (required)` check cannot be told apart from one that
never evaluated anything. `--require-armed` turns that state into a blocking `gate-unarmed`
finding (exit 1) instead.

This matters because three of the arming signals depend on artifacts that may not exist
yet. A PR whose branch does not match the ship-branch pattern falls through to the
assessment comment, so whether the gate evaluates anything depends on job ordering — which
is why the `evidence` job in `keel-ship.yml` declares `needs: ship`.

The operator waiver label is unaffected: it disarms deliberately and still reports `pass`,
which is the point — an explicit operator act stays distinguishable from arming by accident.

- a `keel.closure-comment.v1` closure marker on both the PR and linked issue, posted by a
  trusted GitHub actor. **Closure fidelity:** when a `ship_run` ledger record exists for the
  PR, the posted closure body must match the canonical render of that record
  (`keel.closure.render_closure_comment`) after whitespace normalization; a marker-bearing
  but contradictory or stale body fails with reason `closure comment does not match the
  ship_run ledger record`. Multiple closures pass when **at least one** matches the ledger
  (so a corrected re-post supersedes a stale one). With no ledger record for the PR, the
  closure check stays marker-only (back-compat);
- the required count of distinct posted s7 reviewer verdicts from PR comments or reviews
  carrying `keel.review-verdict.v1`, `reviewer: <stable-id>`, and the current
  `head: <sha>` (formal PR reviews may use GitHub's review `commit_id` as the head
  binding), posted by a trusted GitHub actor. Verdicts may additionally carry
  `vendor: <id>` / `model: <id>` provenance; with `--require-distinct-vendors` (or the
  `evidence_require_distinct_vendors` knob) the verifier requires each required verdict to
  declare a vendor and that no two share one — a missing or duplicate vendor fails with a
  blocking `review-vendor-distinctness` finding. This check is jury-agnostic: it reads only
  the verdict provenance fields and takes no dependency on any review vendor;
- a posted jury verdict carrying `keel.jury-verdict.v1` and the current `head: <sha>`
  when jury is enabled in gating mode, posted by a trusted GitHub actor;
- at least one mandatory `agent:<vendor>` attribution label on the PR (the labels keel
  computes for the effective implementer). A missing `agent:*` label fails with a blocking
  `attribution-label` finding. When a `ship_run` ledger record exists for the PR, the label
  vendor is additionally cross-checked against the record's implementer vendor
  (`actors.implementer`, the slug before any `:model`); a contradiction (for example an
  `agent:codex` label against a `claude` implementer) fails with the same finding. With no
  ledger record (or no recorded implementer) only the presence check runs, and when PR labels
  are unavailable the check is skipped entirely (back-compat). This check engages only when
  the gate is enforced and is suppressed under `--dry-run`.

For live GitHub payloads, trusted means `author_association` is `OWNER`, `MEMBER`, or
`COLLABORATOR`. Explicitly untrusted associations are rejected even when the author type is
`Bot`, and enforced evidence rejects fixture payloads that omit `author_association`. PR
bodies, chat summaries, untrusted public comments, and the automated `keel ship`
assessment comment are never accepted as evidence.
The PR body may only be used to infer `Closes #N` when `--issue` is not supplied. In live
mode, keel reads the PR changed files and head SHA through `gh`, then derives the risk
tier, reviewer count, and jury requirement from the same project policy used by `keel ship`.

```bash
keel evidence-verify .keel/project.yaml --root . --pr 456
keel evidence-verify .keel/project.yaml --root . --pr 456 --reviewers 3 --jury --json
keel evidence-verify .keel/project.yaml --root . --pr 456 --no-jury

# The s10 merge gate: review evidence only, and refuse to pass unevaluated.
keel evidence-verify .keel/project.yaml --root . --pr 456 --phase pre-merge --require-armed

# After s11 has posted the closure comments.
keel evidence-verify .keel/project.yaml --root . --pr 456 --phase post-merge
```

`--gate-label <name>` overrides the legacy arming label for a single run,
`--waiver-label <name>` overrides the operator waiver label, and `--pr-label <name>`
(repeatable) injects PR label names that are merged with the labels read from the live PR.
A live PR fetch still runs unless an offline fixture flag (below) is also supplied. The JSON
payload reports the resolved `gate`, `gate_label`, `waiver_label`, `enforced`, and observed
`pr_labels`. The `evidence_gate_label` knob must be non-empty so legacy arming cannot be
silently disabled by an empty configured label.

A failed live fetch (for example, `gh` cannot read the PR or its labels) is **fail-closed**:
the command errors and exits non-zero rather than treating the gate as unenforced.
When a closure comment has a `### Run context` block whose host agent, transport, profile,
jury, and consent fields are all degraded (`unknown`/`off`/`none`), the verifier emits a
`run-context-empty` finding. Under enforced live evidence this is a major finding and the
verification fails; partially populated context remains accepted but visible in the closure
comment.

Use `--deferral <id|kind|all>` only for an explicit, recorded operator deferral; it applies
only within an enforced run and has no effect when the gate is unenforced. `--dry-run`
prints the contract shape without fetching live artifacts or requiring evidence; pass
`--pr-label` alongside it to preview the enforcement state a labelled PR would see. Tests and
offline CI harnesses can provide `--pr-comments-json`, `--issue-comments-json`,
`--pr-reviews-json`, `--pr-body-file`, `--changed-file`, `--head-sha`, and `--ledger-jsonl`
fixtures; the same verifier path is used either way. `--ledger-jsonl` supplies an offline run
ledger for closure-fidelity checks; without it the configured ledger under `--root` is read.

## `keel scope-verify <project.yaml> --pr <N> [--issue <N>] [--json]`

Enforce keel's branch-contamination defence: compare the implementer's **declared**
in-scope files against the live PR diff. The declared file list is the implementer's scope
contract, recorded into the `ship_run` run-ledger record by `keel ship --append-ledger`
(via repeatable `--declared-file <path>`). `scope-verify` reads the most recent ship-run
record for the PR (`latest_ship_run_for_pr`), loads the PR's changed files through `gh`, and
flags any changed file that is **not** in the declared set as **scope creep**.

The comparison itself is pure (`keel.scope.verify`): given the declared files, the diff
files, and the project's `docs_gate_paths` globs, it returns the in-scope, docs-exempt, and
scope-creep lists plus a verdict. Files matching `docs_gate_paths` (e.g. `docs/**`, `*.md`)
are always allowed extras and never count as creep. Any non-docs file outside the declared
set fails the check (exit 1) and is named in the output.

Back-compat: when no ship-run record carries a declared scope for the PR, the command is an
**advisory pass** (exit 0) with the note `no declared scope recorded`, so existing flows that
never recorded a scope are never broken.

```bash
keel scope-verify .keel/project.yaml --root . --pr 456
keel scope-verify .keel/project.yaml --root . --pr 456 --json
```

`--deferral scope-waived` (or `--deferral all`) is the operator escape hatch — it accepts
scope creep for a single run, mirroring `keel evidence-verify --deferral`. Tests and offline
CI harnesses can supply the diff and ledger offline with `--dry-run --changed-file <path>`
and `--ledger-jsonl <fixture>`; the same pure verifier path is used either way.

## `keel consent-verify <project.yaml> --pr <N> [--root DIR] [--offline] [--json]`

Close keel's consent-boundary gap (audit GAP-12). keel's consent scopes gate the CLI
*contract* an agent renders before a live run — they do not gate the side effects
themselves. Every real mutation (git push, `gh pr create`/`comment`/`merge`, label writes)
is executed by the agent directly and never passes a consent check, and the consent
`status`/`scopes` recorded on the ledger are whatever the agent passed. `consent-verify` is
the deterministic post-hoc reconcile that checks the side effects actually **observed** on a
PR against the scopes that were **approved**.

The reconcile is pure (`keel.consentverify.reconcile`). It maps each observed effect to its
required consent scopes through `keel.consent.side_effect_scopes` (reusing the canonical
scope vocabulary, not a parallel one):

| observed effect | required scopes | rationale |
| --- | --- | --- |
| `pr_exists` | `git`, `github` | the PR existing means a branch was pushed (`git`) and a PR opened (`github`) |
| `comment` | `github` | a comment was posted |
| `merged` | `github` | the PR was merged |
| `label` | `github` | labels were written |

Any observed mutation whose required scope is **not** in the ledger's approved consent
scopes is flagged as `mutation <kind> not covered by approved consent scopes`, and the
command exits non-zero.

The verdict has three states, fail-closed only on a real boundary breach:

- **advisory** (exit 0) — no consent record exists for the PR (a pre-consent or
  agent-self-reported PR). There is nothing to reconcile against, so back-compat is
  preserved and nothing fails.
- **pass** (exit 0) — a consent record exists and every observed mutation is covered.
- **fail** (exit 1) — a consent record exists but an observed mutation exceeds it.

A consent record is considered to exist only when the ledger's
`run_context.consent.status` is a non-blank string; the approved scopes come from
`run_context.consent.scopes`.

```bash
# A merged PR whose consent record only approved git → fails, naming the uncovered merge.
keel consent-verify .keel/project.yaml --root . --pr 456 --json
```

The CLI does the I/O (observing PR state, comments, merged, and labels through `gh`,
fail-soft; the ledger consent record via `latest_ship_run_for_pr`) and feeds the pure
reconcile. Offline CI harnesses and tests supply the observed effects and ledger directly:
`--offline` with `--pr-exists`/`--commented`/`--merged`/`--labeled` flags and
`--ledger-jsonl <fixture>` exercise the same pure verifier path with no transport.

A live `gh`/`git` consent proxy that gates the side effects *as they happen* (rather than
reconciling after the fact) is a separate, heavier follow-up and is out of scope here.

## `keel verify-branch <project.yaml> --pr N [--root DIR] [--tolerance N] [--allow-stale-base] [--offline] [--json]`

Enforce keel's **s2 branch contract**: cut the work branch off an up-to-date
`origin/<base_branch>`, keep the work in one repo-nested gitignored linked worktree per
issue, and never mutate the operator's primary checkout. A branch cut from a *stale* local
base produces phantom diffs and wrong tier classification; edits landing in the primary
checkout contaminate the operator's tree. `verify-branch` makes that contract enforceable.

Two independent checks compose into one verdict:

- **Base ancestry** — the PR head's merge-base with `origin/<base_branch>` (read from config)
  must equal the current base tip, or sit within `--tolerance` commits behind it (default `5`;
  `0` is strict). Beyond the tolerance the branch was cut from a stale base → **stale**
  (exit 1).
- **Worktree isolation** — when run locally, the working branch must live in a *linked*
  worktree nested under the repo root. A primary-checkout edit, or a worktree outside the
  repo root, is **contaminated** (exit 1). In CI / PR-only mode there is no local worktree to
  inspect, so the isolation check is **N/A** and skipped gracefully.

The comparison itself is pure (`keel.branchscope.verify`): given the head/merge-base/base-tip
SHAs, the commit distance, and the worktree facts, it returns an `ok`/`stale`/`contaminated`
verdict with a per-check breakdown. The CLI gathers the live facts via the thin `git`/`gh`
wrappers (`merge-base`, `rev-parse origin/<base>`, `rev-list --count`, `worktree list
--porcelain`), fail-soft — a fact that cannot be resolved becomes `None` and the pure layer
skips that check rather than hard-blocking.

```bash
keel verify-branch .keel/project.yaml --root . --pr 456
keel verify-branch .keel/project.yaml --root . --pr 456 --json
```

`--allow-stale-base` is the operator escape (consent scope `git`): it downgrades a stale-base
failure to an **advisory pass** (exit 0), recording the downgrade in the report's `note`. Tests
and offline CI harnesses can supply every fact directly with `--offline` plus `--head-sha`,
`--base-tip-sha`, `--merge-base-sha`, `--base-distance`, `--worktree-path`, `--repo-root`, and
`--linked-worktree true|false`; the same pure verifier path is used either way.

## `keel step-verify --step sN --handoff-file handoff.json --evidence-report evidence.json [--effort low|medium|high] [--team PROFILE]`

Verify a persisted step handoff before an adapter advances the ship backbone. The handoff
must be the JSON object produced by `keel.stepverifier.build_handoff`; the evidence report
must be the JSON verification block from `keel evidence-verify` (or an equivalent report
with `results`). The command exits non-zero when the handoff schema/status/renderer marker
is missing or when the step's required evidence ids are not ok.

```bash
keel step-verify --step s7 \
  --handoff-file .keel/run/handoffs/s7.json \
  --evidence-report .keel/run/evidence.json \
  --reviewers 2 --json
```

## `keel runcontrols EVENTS.json [--slot fixloop --action fix] [--json]`

Append one run-control event to a JSON array file and evaluate deterministic work caps:
run budget, per-step/slot caps, repeated identical actions, and alternating diff
fingerprints. `--dry-run` evaluates without writing. A hard halt exits non-zero and returns
the structured halt reason; `keel ship --run-events-file EVENTS.json` stamps the same
summary into the ship ledger record and also exits non-zero on a hard halt.

```bash
keel runcontrols .keel/run/events.json --slot fixloop --action fix
keel runcontrols .keel/run/events.json --step-cap fixloop=3 --json
```

`--provider`, `--attribution`, `--stage` and `--round` record **who** ran the event, which
is what lets the s11 closure comment attribute an escalated fix round to the seat that
actually took it. Every result — JSON and human — carries `fix_attribution`: the
implementation actor read off the `s4`/`implement` event, one record per `s9`/`fixloop`
round, and the deterministic `sentence` the closure embeds.

```bash
keel runcontrols .keel/run/events.json --slot implement --provider agy --attribution agy
keel runcontrols .keel/run/events.json --slot fixloop --round 2 \
  --provider anthropic-api --stage gate --attribution opus --json
# fix_attribution.sentence: "implemented by agy, fixed by opus in round 2"
```

`--attribution` takes the label `keel delegate run` computed (a bare string, or that
command's whole `attribution` object through `--event-json`) — never one you composed, so
the label written down and the model that ran cannot drift.

## `keel fixloop brief --findings FILE [--pr N] [--round K] [--budget N] [--unavailable PROVIDER] [--out FILE] [--cwd DIR] [--head SHA] [--issue N] [--fix-sha SHA] [--tier N] [--role LABEL] [--delegate TOKEN] [--host-agent NAME] [--timeout S] [--root DIR] [--project project.yaml] [--json]`

Route review findings back to a fixer — the s9 half of the review loop. It renders the
round's fix brief and resolves **who fixes it**, both deterministically, so two hosts
running the same round produce the same words and dispatch the same seat.

The brief groups findings by severity, anchors each on its `file:line`, carries the
reviewer's own `reproduction`, and states the re-review the push will get: a blocking
finding means a full re-review, a suggestion-only round means the narrowed one, whose
instruction (*"verify only the applied fix in commit `<sha>`; do not re-review what you
already approved"*) is rendered verbatim for the next reviewer's prompt.

`--findings` is a JSON array of findings — `severity` (required: `critical`, `major`,
`minor`, `nit`), `message`, `source`, `path`, `line`, `anchorable`, `reproduction` — or an
object with a `findings` array, so a `keel review` bundle can be passed straight through.

**Reviewer text is quoted data, never instructions.** The brief becomes the fixer's
`--prompt-file`, and findings are the one part of it keel did not write, so every
reviewer-supplied string is rendered as a blockquote: one `> ` per line (nothing a reviewer
wrote can sit at the start of a line, which is where every structural token of this format
lives), the HTML-comment opener defanged so a second brief marker cannot be forged, a
leading `#` escaped, a line reading as one of the brief's trailer keys rendered as inline
code, and the field capped — a prompt has a budget. A finding whose message carries its own
`## Rules for this round` section appears inside the quote and nowhere else.

**The project config is required.** `knobs.team.fix` is what decides whether the round goes
back to the delegate that implemented or to the host, so a config the command cannot read
is a **refusal** — `status: no-config` and a non-zero exit — rather than a silent
resolution against an empty policy, which answers "the host fixes" and is the failure this
command exists to prevent. `--project` / `--root` name it; `--no-project` is the deliberate
opt-out for a project that really has no team policy.

The fixer is the **escalation ladder** `implementer → gate → host`, a pure function of the
round, provider availability, and the budget:

| round | seat | source |
| --- | --- | --- |
| 1 | `assignment.fix` | `knobs.team.fix`, defaulting to the alias `implementer` — the provider that implemented this change |
| 2 | `assignment.gate` | `knobs.team.gate`, the mandatory second opinion |
| 3 | the host agent | the CLI driving the run |

A rung repeating an earlier one is dropped (escalating to the seat that just failed the
round is not an escalation), a provider named by `--unavailable` is skipped rather than
dispatched to, and a round past the last rung stays with the last usable fixer. The
`--budget` (default 3) is unchanged by the ladder: past it there is no fixer, and the
command **exits non-zero** (`status: budget-exhausted`, or `no-fixer` when every rung is
unavailable) so a spent loop cannot be mistaken for a round to run. A further round needs
an explicit `--budget` — that flag, not `keel ship --max-rounds`, which is the run budget.

```bash
keel fixloop brief --pr 1042 --findings .keel/run/findings.json --round 2 \
  --head "$HEAD_SHA" --issue 1016 --out .keel/run/fix-2.md --cwd "$WORKTREE" --json
```

`--json` prints the whole document: `fixer`, the `ladder` with each rung's availability,
the `hops` walked to reach this round (`start`, `round-failed`, `provider-unavailable`,
`ladder-exhausted`), the finding counts, `re_review`, the rendered `brief`, and `dispatch`
— the ready-made `keel delegate run --role fix` argv for the resolved seat. `dispatch` is
`null` for a `kind: subagent` seat: a host subagent is run by the host agent and never
reaches `keel delegate run`.

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

## `keel verify-merge <project.yaml> [--root DIR] --pr N [--merge-sha SHA] [--json]`

Confirm a merged PR applied what was reviewed — and that nothing else rode along.
Read-only: it queries GitHub and mutates nothing.

A merge succeeding is not the same as a merge applying the reviewed diff. An
`update-branch` merge commit followed by a squash-merge silently reverted unrelated
merged work twice in one day while shipping 1.8.1/1.8.2, and CI never saw it — the
reverted state was internally consistent, so every gate stayed green.

The check asks whether the merge wrote to files another PR changed **after this one
branched**, which is the only way that revert can happen.

```bash
keel verify-merge .keel/project.yaml --root . --pr 543
keel verify-merge .keel/project.yaml --root . --pr 543 --json
```

| status | meaning | exit |
| --- | --- | --- |
| `drift` | wrote to files another PR changed after this one branched — read the diff | 1 |
| `out-of-scope` | changed files the PR's own diff did not list | 0, or 2 when `incomplete` |
| `clean` | neither | 0 |
| `unknown` | not merged yet, or `gh` unreadable — **not** a pass | 2 |

`drift` is a *stop and look* signal, not proof: two PRs editing one file in sequence
is ordinary. The report names the colliding PR for each file so a human can judge.

`unknown` exits **2**, not 0. The status line has always said "not a pass", but the
exit code said otherwise, and the exit code is what a caller wiring this in after
s10 actually reads.

Every input the check depends on — the merge commit's file list, the pull requests
merged alongside it, and this PR's own file list — is gathered before any is judged.
A single unreadable one used to leave the check quietly downgraded to a weaker one
that still printed `clean` (#933). Now:

* a **finding survives** an unreadable input. `drift` and `out-of-scope` are answers,
  computed from inputs that *were* read, so the report keeps them — and keeps
  `overtaken` / `unexpected` / `landed_count`, which replacing it would erase — with
  `incomplete: true` added;
* the exit code still reports that something went unchecked: `incomplete` exits **2**
  as `unknown` does, because an `out-of-scope` finding says nothing about drift, and
  drift is usually what the unreadable input cost. Only `drift` keeps its own exit 1;
* the reason names **only** the questions actually left open. Saying "no conclusion
  about drift is possible" when only this PR's file list was missing is false — drift
  was checked, and found nothing.

## `keel resume <project.yaml> [--root DIR] [--live-pr-state STATE] [--live-worktree-state STATE] [--no-observe] [--json]`

Render a dry-run resume plan from the checkpoint. This command never mutates files, git,
GitHub, comments, or releases — it does read them: by default it observes the live PR
state, whether the recorded worktree still exists, and the branch's real head, instead of
being told. The flags override what is observed, for offline and fixture use.

```bash
keel resume .keel/project.yaml --root . --json                       # observes
keel resume .keel/project.yaml --root . --live-pr-state merged --json # override
keel resume .keel/project.yaml --root . --no-observe --json           # read nothing
```

`STATE` values are adapter-supplied live-state reconciliation hints:

- PR state: `unknown`, `missing`, `open`, `merged`, or `closed`
- worktree state: `unknown`, `present`, or `missing`

`status: no-checkpoint` means there is nothing to resume. `status: ambiguous` exits
non-zero and includes warnings plus the reconciliation action, for example when a
checkpoint references a PR or worktree that live state reports missing. If the PR is
already merged, the plan resumes at capture or closeout and never repeats the merge.

## `keel status <project.yaml> [--root DIR] [--live-branch NAME ...] [--live-pr N ...] [--json]`

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

**Orphan detection (audit GAP-13).** Pass live git/transport references with repeatable
`--live-branch NAME` and `--live-pr N` flags (an adapter supplies these from `git branch` /
`gh pr list`). The snapshot's `orphans` block flags any live branch or PR that is covered by
neither the current checkpoint nor any ledger record — the git-side of the missing-checkpoint
hazard (a PR/branch keel has no covering state for). This is **advisory**: orphans are
reported but never block. The decision is pure
([`keel.checkpoint.find_orphans`](../../src/keel/checkpoint.py)).

## `keel activity <project.yaml> [--root DIR] [--write|--done|--clear] [--command CMD] [--run-id ID] [--phase PHASE] [--status running|done] [--verdict pass|blocked] [--issue N] [--pull-request N] [--note TEXT] [--json]`

Read or stamp the **additive command-activity** channel — a lightweight, checkpoint-free
record per run under `.keel/activity/<run-id>.json` (path resolved under `--root DIR`,
overridable via `policy_pack.reports.activity`). The resumable checkpoint is ship-shaped
(`s0`–`s12`); most commands run in the main checkout, never checkpoint, and so are invisible
to `keel-visual`'s live board. This channel lets any command stamp its **own** `keel.flows`
phase as it advances, so non-ship runs (`triage`, `morning`, `pr-loop`, …) show up live. It
**never touches the checkpoint contract**.

```bash
# stamp the active phase as the command advances (one stable --run-id per run)
keel activity .keel/project.yaml --root . --command triage --run-id triage-2260 --phase classify --issue 2260
# stamp phase completion verdict
keel activity .keel/project.yaml --root . --command ship --run-id ship-861 --phase test --verdict pass
# … repeat --phase as you move through the flow …
keel activity .keel/project.yaml --root . --run-id triage-2260 --done   # mark finished
keel activity .keel/project.yaml --root . --clear --run-id triage-2260  # remove the record
keel activity .keel/project.yaml --root . --json                        # read all records
```

`--write` validates that `--command` is a known `keel.flows` command and `--phase` one of
that command's phase ids (`build_activity_record`); records are keyed by `--run-id` (one file
each), so two commands in the same repo never clobber one another, and the run-id is slugged
to a safe filename. `--verdict pass|blocked` records the phase outcome verdict into the activity record.
`--done` flips an existing record's status (the board fades / last-sorts /
filters it like any finished run); `--clear` removes it. With no flag, it lists every readable
record (malformed files are skipped, fail-soft). The stepped command adapters emit this
best-effort — a missing `keel activity` is skipped silently and never blocks the command.

**As of 1.6.4 you rarely call this by hand for ship.** The deterministic backbone commands
auto-stamp the board when given a `--run-id`: `keel plan` (Step 0 → first phase), `keel
run-gates` (s8), and `keel merge` (s10) — so a run shows up and advances **start → test →
merge** even if the per-phase `keel activity` calls never run. Use `keel activity` directly
to fill in the **middle** phases (s1–s7, s9, s11–s12) or for the non-ship flows that stamp
every phase as they go.

## `keel scratch-dir [--root DIR] [--no-create]`

Print the keel-owned scratch directory for transient artifacts, creating it (with its
gitignore) unless `--no-create`. Adapters wire it as `SCRATCH=$(keel scratch-dir)` so PR
diffs, issue dumps and draft prose land under `.keel/scratch` instead of the consumer's
checkout.

```bash
SCRATCH=$(keel scratch-dir --root .)
keel scratch-dir --root . --no-create      # just the path
```

Prints one line — the absolute path — and always exits 0.

## `keel gc <project.yaml> [--root DIR] [--keep-activity N] [--no-scratch] [--no-activity] [--dry-run] [--json]`

Reclaim keel's own disposable runtime artifacts: empty `.keel/scratch`, prune
`.keel/activity` to the newest `--keep-activity` records (default 50). The run ledger, the
checkpoint and the locks are durable or self-bounded and are never touched.

```bash
keel gc .keel/project.yaml --root . --dry-run
keel gc .keel/project.yaml --root . --keep-activity 100 --json
```

Fail-soft by design: a failure on one tree degrades to a no-op reported under `degraded`
(and on stderr) while the other still runs — taking out the trash must never abort a
caller. Exit 1 only when the config is missing or invalid; otherwise 0, degraded or not.

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

## `keel work-block <project.yaml> [issues…] [--root DIR] [--queue SELECTOR] [--max N] [--hours H] [--review-comments inline|summary] [--reviewers 1|2|3] [--delegate PROVIDER] [--review-delegate PROVIDER] [--effort low|medium|high] [--team PROFILE] [--target TEXT] [--dry-run] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--json] [--wizard] [--wizard-answer KEY=VALUE]...`

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
`--review-comments` / `--reviewers` pass through to the per-issue ship handoffs.
`--wizard` / `--wizard-answer` behave exactly as they do for
[`keel ship`](#ship-wizard) — same probe, same questions, same interactivity guard. The
implementer and jury choices have no work-block flag of their own, so they are echoed in
the resolved flag set for the adapter to hand to each child `keel ship`. The contract
shares its queue primitive with `overnight`; the daytime mode lets the operator redirect
between items, while a blocked item stops the daytime block instead of continuing. Final
reporting buckets each issue as shipped, PR-open-not-merged, deferred, blocked, skipped, or
needs-input. Stop conditions include queue exhaustion, the max/time budget, an operator
pause, a consent gap, a non-ready or blocking finding, and merge-window close.


**Staffing the children (#1017).** `--delegate <provider[:model]>`, `--review-delegate
<provider>` (repeatable, positional per reviewer slot), `--effort low|medium|high`,
`--team <profile>` and `--reviewers` are resolved once for the block and handed to **every**
child `/keel:ship` — which accepts all five, so the handoff the contract publishes is one the
child really parses. `--effort` and `--team` are also accepted by `plan`, `review`,
`step-verify`, `evidence-verify` and `merge`, because a bench changes the reviewer count and
all six of those commands resolve the same review contract; pass the same values to each, or
the gate re-derives a bench the run never dispatched. The contract publishes both halves under
`session_contract.work_block.delegation`: `effective` (what the operator passed) and
`child_args` (the exact flag list to append to each handoff). `contract.assignment` shows
what those values resolve to against `knobs.team` — `lead`, `implementer`, `effort`,
`reviewers`, `review_panel`. `--team` names a [`knobs.team.profiles`](configuration.md#team)
bench; an unknown name lands in `assignment.warnings` rather than being ignored. The adapter
records the effective values in the session report.

Dry-run mode never spawns ship runs, creates PRs, merges, or writes reports. Live mode is
only a preflight contract; adapters hand the approved consent scope to each ship delegate and
keep merge-window and merge-lock enforcement shared with `keel ship`.

## `keel overnight <project.yaml> [hours] [--max N] [--review-comments inline|summary] [--reviewers 1|2|3] [--delegate PROVIDER] [--review-delegate PROVIDER] [--effort low|medium|high] [--team PROFILE] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--json]`

Render the standalone overnight-session contract. The core owns the generic unattended
session shape: merge-window mode from `keel window`, ship handoff, per-issue worktree
isolation, no-night-merge policy, blocker policy boundary, priority queue shape, session
or morning report destinations, stop conditions, and the shared deferral queue.

```bash
keel overnight .keel/project.yaml 8 --max 3 --json
keel overnight .keel/project.yaml --live --consent-mode standing --json
```

It takes the same staffing flags as `keel work-block`, with the same
`session_contract.work_block.delegation` record and the same session-report requirement.

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

## `keel run-gates <project.yaml> [--root DIR] [--tdd] [--run-id ID] [--command CMD] [--phase PHASE] [--issue N] [--pull-request N]`

Run the project's **command gates** (the `command`/`build`/`lint` Lego) under `--root DIR`
(default `.`) and report each as a structured finding. Agentic gates (review, design
parity) are not run here — this is the deterministic, runnable slice of the test step (s8).

**Concurrent execution is a library capability, not a CLI one (1.13.0+).**
`keel.gates.run_gates(..., concurrency=N)` runs independent command gates on a worker
thread pool while strictly preserving deterministic outcome ordering, fail-soft behaviour
and execution timeouts. There is no `--concurrency` flag and no `knobs.concurrency` key —
this subcommand always runs the gates serially.

**Activity auto-stamp (1.6.4+).** Pass `--run-id` and `run-gates` advances the
[keel-visual](keel-visual.md) board to the test phase (`--phase`, default `s8`; `--command`
default `ship`) — so a run moves forward on the board without the agent's per-phase
`keel activity` calls. Fail-soft and opt-in (see `keel plan`).

Each gate runs its configured shell command; a non-zero exit becomes a blocking finding
(`gate:<name>`), a zero exit a pass. The command's output tail is captured for context.

A gate that exceeds its wall-clock limit is killed and reported as a third, distinct
outcome — `TIMEOUT` rather than `FAIL` — so a slow host does not read as a broken test.
It **still blocks**: a hanging command is a real defect. The limit is
[`knobs.gate_timeout_s`](configuration.md#gate_timeout_s) (default 600s), overridable for
one slower gate with `timeout:` frontmatter on a `command` extension.

Under `implement_mode: tdd` — or with `--tdd` for one run — the gate list also carries the
pure **`tdd-order`** gate, evaluated after all the others because its verdict includes
theirs. See [`knobs.implement_mode`](configuration.md#implement_mode).

There are four outcome labels, and the difference between the last two matters:

| label | meaning |
|---|---|
| `ok` | the gate ran and passed |
| `FAIL` | the gate ran and failed |
| `TIMEOUT` | the gate was killed by its wall-clock limit before producing a verdict — still blocks |
| `NOT-RUN` | **this command did not execute the gate at all** |

`NOT-RUN` is what an `agentic` gate reports here: the command-only runner does not dispatch
those, the agent does. It is deliberately not `ok` — a gate nobody ran has produced no
verdict, and recording it as a pass is what let a blocking review gate authorize a merge
without a reviewer. A `NOT-RUN` gate declared `on_fail: block` will not certify the run, so
`keel merge` refuses it; record the agent's result with
`keel ship --gate-result <id>=pass|fail` once the gate has actually been dispatched.
Reviewer checklist for changes touching command-gate execution: `spec.run` values must
remain sourced only from operator-controlled project config or extension YAML, never from
PR content, issue text, or prior agent output.

If `gates:` includes **`jury`** and the [ai-jury](https://github.com/berkayturanci/ai-jury)
`jury` CLI is installed, the jury gate runs it on the diff (`git diff base...HEAD`) and maps
its findings (file/line/severity) into keel findings (critical/major block). If `jury` is
not installed the gate is a **fail-soft no-op** — the flow runs with or without jury. Its
wall-clock limit is [`knobs.jury_timeout_s`](configuration.md#jury_timeout_s) (default
600s), separate from `gate_timeout_s` because a cross-vendor panel and a test suite have
unrelated runtimes.

Three outcomes mean the panel produced **no review**: an oversize diff (over 1 MB,
`MAX_DIFF_BYTES`), a run killed by `jury_timeout_s`, and a run whose output carries no
parseable verdict. All three surface a visible finding rather than passing silently —
blocking `major` in **gating** mode, non-blocking `minor` in advisory. Note `run-gates`
always runs the jury in gating mode, so all three block here; `keel ship` uses the mode
its review contract resolved. A nonzero exit that *does* carry a parseable report is a
completed review — that is how ai-jury signals "request changes" — so its findings are
used as-is.

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

<a id="keel-doctor"></a>

## `keel doctor [project.yaml] [--root DIR] [--offline] [--providers] [--strict] [--fix] [--approve-scope SCOPE] [--operator NAME] [--consent-mode MODE] [--json]`

Run a diagnostic pass over the installed keel and its adapter surfaces. Read-only unless
you pass `--fix`: `doctor` reads versions, markers, on-disk state and (with a config) the
repository's labels, then classifies each check as `ok` / `skipped` / `warn` / `fail`. A
check that *could not look* — no config, no `gh`, `--offline` — reports `skipped` rather
than claiming `ok`, and never moves the roll-up. The roll-up `status` is the worst of the
checks that did look.

```bash
keel doctor                                   # CLI + adapter health only
keel doctor --root . --json                   # machine-readable report
keel doctor .keel/project.yaml --root .        # also check core_version, state paths, labels
keel doctor .keel/project.yaml --offline --strict
keel doctor --providers                       # which delegates are usable on this machine
keel doctor --providers --json                # providers[], registry_path, warnings
keel doctor projects/keel.yaml --fix \
  --approve-scope github --operator you       # create the missing labels
```

The checks are:

- **`checkout_binding`** — whether the importable `keel` is the checkout you pointed
  `--root` at. `pip install -e .` registers one source tree for the whole interpreter, so
  installing from a second checkout silently repoints every other one: imports, the test
  suite, and coverage all follow the other tree while your working directory suggests
  otherwise. A mismatch names both paths and is a `warn`, never a `fail` — running against
  a deliberately installed keel (a release, a pinned build) is legitimate. Skipped when
  `--root` is not a keel source checkout.
- **`cli_version`** — installed `keel` version vs the latest published on PyPI
  (`keel-workflow`). This is the headline check: an installed version *behind* the latest
  is a `fail` (it catches a silent downgrade). The PyPI lookup is fail-soft with a short
  timeout — when offline or unreachable, `latest` is reported as `unknown` (a `warn`),
  never a crash or a hang. Pass `--offline` to skip the lookup entirely.
- **`adapter_version`** — the `keel_version=` markers on installed adapter surfaces
  (`.claude/commands/keel/*.md`, `.agents/skills/keel-*/SKILL.md`) vs the running CLI. Any
  surface that drifts is a `warn` — run `keel update-adapter`.
- **`orphan_adapters`** — surfaces whose `command=` is no longer in the installed keel
  (e.g. a stale `ship-v2.md`); a `warn`, same scan as `keel adapter-status`.
- **`core_version`** — the `core_version` constraint from `project.yaml` (e.g. `^1.0`)
  vs the installed CLI version. An unsatisfied constraint is a `fail`. Only runs when a
  config path is given.
- **`state_paths`** — existence/validity of the configured ledger + checkpoint paths.
  Advisory: a missing path is fine (reported as empty history); an invalid path is a `warn`.
- **`python_toolchain`** — the interpreter `knobs.build_gate_cmd` will actually run on, its
  version, and whether PyYAML imports there. A `make` gate is resolved the way the Makefile
  resolves it (an exported `PY`, then this repo's `scripts/find_python.sh`, then `python3`
  on PATH); any other gate runs in this process, so the answer is `sys.executable`. Below
  `requires-python` (3.11) or without PyYAML is a `warn` that names the interpreter — a
  `make test` that dies with a hundred syntax errors is a 3.9 on PATH, not a regression in
  the tree.
- **`policy_labels`** — whether the labels this project declares actually exist on its
  repository. `ship` and `triage` apply `status:*` / `priority:*` / `role:*` and the
  `agent:*` / `model:*` attribution pair **by name**, and GitHub rejects a label that was
  never created — keel's own repository ran for months with every one of those labels
  missing and nothing reported it (#1021). The declared set is
  [`policy_pack.labels.*`](configuration.md#policy_packlabels) (a bare entry is qualified
  with its group, so `role: ["core"]` means `role:core`), `policy_pack.scan.issue_labels.*`,
  and the attribution vocabulary: `agent:<vendor>` for every built-in vendor plus each
  `knobs.delegate_profiles` entry's vendor, and `model:<base>` for a model a profile pins.
  A `model:*` minted from `--delegate vendor:model` or a `delegate-model:` issue label is
  unbounded and cannot be enumerated ahead of time, so the check does not try.
  Missing labels are a `warn` that prints the exact `gh label create` commands under the
  check; **never a `fail`**. Only runs when a config path names an `owner`/`repo`, and one
  `gh label list` is all it costs — `--offline`, no `gh` on PATH, or an unauthenticated or
  unreachable GitHub each report `skipped` with the reason.
- **`providers`** — only with `--providers`. Probes every provider keel can dispatch to:
  the built-in vendors (`claude`, `codex`, `agy`, `ollama`, `anthropic-api`, `openai-api`,
  `google-api`), every `knobs.delegate_profiles` entry when a config path is given, and every
  entry of the machine-level [provider registry](configuration.md#provider-registry). `ok`
  when at least one is available; `warn` when the registry is malformed or nothing is usable;
  **`fail`** on a registry name clash. Omitted entirely without the flag, so the default run
  stays as cheap as it was.

`--providers` prints a table under the checks — one row per provider with its transport
(`cli` · `api` · `local`), where the entry came from (`builtin` · `profile` · `registry`), its
capabilities (`tools`, `read-only`, `model`), a reason, and any models the provider lists for
itself (`agy models`, Ollama's `/api/tags`). With `--json` the same document is merged into
the report at the top level as `providers`, `registry_path`, `registry_present`, `warnings`
and `errors`.

```text
keel providers — 2 of 7 available
  registry: /home/op/.keel/providers.yaml (not present)
  yes  claude             cli    builtin  tools,read-only,model  /usr/local/bin/claude (2.1.0)
   no  codex              cli    builtin  tools,read-only,model  codex not found on PATH
   no  anthropic-api      api    builtin  model                  ANTHROPIC_API_KEY is not set in the environment
```

Every probe is time-boxed and fail-soft, and **secrets are never printed** — a hosted-API row
names the environment variable, never its value. Only the hardcoded loopback Ollama URL is
dialed; an endpoint named by config or by the registry is checked for key presence only. See
[`runtime-capabilities.md`](runtime-capabilities.md#probing-providers-keel-doctor---providers)
for the per-transport rules.

`--fix` creates the labels `policy_labels` reported as missing — the one mutation `doctor`
performs, and it runs the same `gh label create` commands the warning printed. It is gated
like every other live keel mutation: the `labels` side effect needs the **`github`** consent
scope, so `--approve-scope github --operator <name>` (or a standing `KEEL_APPROVE_SCOPE` +
`KEEL_OPERATOR`, or `automation.approved_scopes` in config) must approve it, and without
that the command refuses and exits non-zero without creating anything. Each label is
reported as it is created; a label that fails is named and the command exits non-zero
without stopping the rest.

```text
  WARN  policy_labels     2 of 18 declared label(s) missing on you/app: agent:agy, role:core
        $ gh label create agent:agy --repo you/app
        $ gh label create role:core --repo you/app
```

By default `doctor` is advisory and exits `0` (unless the command itself errors, e.g. a
missing or invalid config, or `--fix` was refused or failed). Pass `--strict` to exit
non-zero when any check is `fail` — a `skipped` or `warn` check never does that, which is
why an unreachable GitHub cannot turn a label check into a red run.

<a id="keel-delegate"></a>

## `keel delegate run --provider TOKEN --role ROLE --prompt-file FILE [--cwd DIR] [--timeout S] [--effort low|medium|high] [--model TOKEN] [--root DIR] [--project project.yaml] [--registry FILE] [--run-id ID] [--detach] [--json]`

Dispatch **one** delegate and print the JSON return contract. This is the single executor
for every transport keel supports — the three built-in agent CLIs, a
`knobs.delegate_profiles` entry, a machine-level `~/.keel/providers.yaml` entry, a hosted
vendor API, an OpenAI-compatible endpoint, and a local Ollama model. Before it existed the
argv shapes, the prompt delivery, the endpoint and the return contract lived only as prose
in the adapters, so every host agent re-implemented them and the copies drifted.

```bash
# a tool-enabled implementer in a worktree
keel delegate run --provider agy:gemini-3.8-flash --role implement \
  --prompt-file brief.md --cwd ../wt-1012 --timeout 3600

# a read-only reviewer, one hosted API call
keel delegate run --provider anthropic-api:claude-opus-5 --role review \
  --prompt-file rubric.md --effort high

# a configured OpenAI-compatible profile — exactly one HTTP call through api_delegate
keel delegate run --provider openrouter --role review \
  --prompt-file rubric.md --project .keel/project.yaml
```

`--provider` takes a provider **name** or `name:model`. Resolution order is **built-in
vendor > project profile > machine registry**: a built-in always wins and can never be
redefined, the same invariant `keel validate` enforces for `knobs.delegate_profiles` and
`keel doctor --providers` reports for the registry.

`--model` overrides the model half. Either way the token is validated before use, because
it can arrive from a `delegate-model:` issue label — and **which rule applies depends on
where the model lands**:

| destination | accepted | why |
| --- | --- | --- |
| a subprocess argv (`cli`, `profile`), or `google-api`'s URL path | `[A-Za-z0-9._-]`, no leading dash | a stray character could read as another flag, or retarget a URL that carries an API key header |
| a JSON request body (`ollama`, `anthropic-api`, `openai-api`, `openai-compatible`) | `[A-Za-z0-9._:/-]`, no leading dash, no `..` | real ids need `:` and `/` — `qwen2.5-coder:32b`, `deepseek/deepseek-r1` — and neither can do anything inside a JSON string |

`--role` selects the invocation, not just a label:

| role | invocation |
| --- | --- |
| `review` · `gate` · `chair` | read-only / findings-only: the vendor's documented read-only mode, or a profile's `review_args` |
| `implement` · `fix` | tool-enabled: the vendor's network- and write-enabled mode |

For the three built-in CLIs the read-only invocation carries no write-enabling flag, and
that is asserted per vendor in `tests/test_delegate.py`. `claude` runs read-only under an
**allow-list** (`--allowed-tools Read,Grep,Glob`) and no permission bypass: a denylist
has to be extended every time the CLI grows a tool and is wrong in the window before
someone notices, while an allow-list refuses a new tool on the day it appears. `agy` is
the one built-in that still needs its non-interactive permission flag — `--sandbox` is
the only read-only mechanism it documents — so its promise rests on the sandbox alone.

keel cannot *enforce* read-only for an arbitrary binary, so the result reports both
`read_only` (the role you asked for) and **`read_only_backed`** (whether anything enforces
it). They differ in exactly the case that matters: `DelegateProfile.role_args` falls back
to `args` when `review_args` is unset, so a profile carrying the implementer's
write-enabling flags plans a *reviewer* with them. That run comes back
`read_only_backed: false` with a warning naming the provider — **branch on that field, not
on `read_only`.** Set `review_args` for any profile used as a reviewer; an explicitly empty
`review_args: []` is a deliberate "this CLI needs no flags to review" and counts as backed.

`--effort` is translated into each vendor's own spelling — a model suffix for `agy`,
`-c model_reasoning_effort=<level>` for `codex`, `thinking.budget_tokens` for
`anthropic-api` (with `max_tokens` raised above the budget), `reasoning_effort` for
`openai-api` and OpenAI-compatible endpoints,
`generationConfig.thinkingConfig.thinkingBudget` for `google-api`. A provider that cannot
express effort returns `effort_applied: false` with a warning rather than silently running
at its default. A provider entry may also carry its own `effort:` as a per-seat default; a
per-run `--effort` wins, and an unrecognised configured value is a warning rather than a
failed run.

The prompt is read from `--prompt-file` and delivered on the delegate's **stdin**, never on
its argv: a prompt carries the diff and the brief, and an argv is world-readable in `ps`
for the life of the process. The one exception is a profile that declares
`prompt_mode: arg`, where the operator has said the CLI requires it.

### The return contract

```json
{
  "schema_version": "keel.delegate-run.v1",
  "ok": true,
  "provider": "agy", "vendor": "agy", "model": "gemini-3.8-flash-high",
  "role": "implement", "transport": "cli",
  "text": "…the delegate's output…",
  "exit_code": 0, "duration_s": 412.6, "timed_out": false,
  "error_code": null, "error": null,
  "attribution": { "agent_label": "agent:agy", "model_label": "model:gemini-3", "system": "agy:gemini-3.8-flash-high" },
  "read_only": false, "read_only_backed": false,
  "effort_applied": true,
  "warnings": []
}
```

`transport` is one of `cli` (a built-in agent CLI), `profile` (any other configured
binary), `api` (a hosted or OpenAI-compatible endpoint) or `ollama`. `exit_code` is `null`
for the HTTP transports — there is no process, and a synthetic `1` would let a caller
mistake a refused API key for a crashed CLI. `attribution` is computed by
`keel.agents`, so the labels a caller writes cannot drift from what core recorded.

Every failure is **fail-soft**: `ok: false` with a machine-readable `error_code`, never a
traceback. Branch on the code, never on the message.

| `error_code` | meaning |
| --- | --- |
| `unknown-provider` · `bad-provider` | the `--provider` token names nothing keel can resolve |
| `bad-role` · `bad-effort` · `bad-timeout` | an argument keel refuses |
| `bad-model` · `no-model` | the model token is unsafe, or the transport needs one and has none |
| `no-prompt` | `--prompt-file` is missing, unreadable, or empty |
| `missing-binary` | the CLI is not installed |
| `nonzero-exit` · `empty-output` | the CLI ran and produced a failure, or nothing |
| `timeout` | the wall-clock limit killed it (`timed_out: true`) |
| `rate-limit` | HTTP 429, or a CLI that said it was out of quota |
| `no-key` · `auth` · `http` · `network` · `bad-response` | the HTTP transports' vocabulary |
| `lost` | a detached run's process vanished, or it passed its own deadline, without recording a result |

The **policy** around those codes stays with the caller (ship s4/s7), not here: this
command never retries, never falls back to the host agent, and never consults the risk
tier. The no-retry-on-`rate-limit` rule and the refuse-on-tier-3 rule are the
orchestrator's.

Exit status is `0` when `ok` is true, `1` otherwise. `--json` is accepted for symmetry with
every other keel command; the contract is JSON either way.

## `keel delegate run … --detach` / `keel delegate wait RUN_ID [--timeout S]` / `keel delegate status`

A delegated implementation runs for tens of minutes; a host LLM's turn does not. `--detach`
starts the same run as a background child in its own session and returns immediately:

```bash
keel delegate run --provider agy:gemini-3.8-flash --role implement \
  --prompt-file brief.md --cwd ../wt-1012 --detach --run-id impl-1012 --root .
keel delegate status --root .
keel delegate wait impl-1012 --root . --timeout 3600
```

The state file `.keel/state/delegate/<run-id>.json` is **authoritative** — `{run_id,
provider, role, started_at, timeout, deadline_at, status: running|done|crashed, result}`,
alongside three sidecars: `<run-id>.out` (the child's stdout and stderr), `<run-id>.pid`
and `<run-id>.crashed`.

**The record is written by the child alone.** The parent writes it once, *before*
spawning — so a `wait` issued immediately afterwards always finds a file — and never
touches it again; the pid goes to its own file, and a reaper that concludes a run is gone
writes the crash marker rather than editing the record. Every one of those would otherwise
have been a read-check-write on a file another process can replace at any instant, which
no guard fixes: the child's terminal record can land between the read and the write, and
`running` goes back over the result the caller is waiting for. Reads compose the four
files, with the child's own `done` always winning over a crash marker — a marker says
"this looked abandoned", a `done` record says "the delegate answered". Because the
*result* only ever comes from the record, it survives the caller exiting, the session
ending, and a reboot. `.keel/state/` is gitignored, so nothing here is ever committed.

Reusing a `--run-id` is fine — an orchestrator naming a run after its issue will reuse it
on a retry. The pid and crash markers are cleared as the new record is written, so a
reused id inherits nothing: without that, the previous run's dead pid would pair with the
new record and the next `keel delegate status` would reap a run that started milliseconds
ago.

**Always pass `--timeout` to `run`.** It is stamped into the record as `deadline_at`
(plus a grace window for the child's final write). Together with a liveness check on the
recorded pid, that is what bounds a `wait`: a child killed outright — `SIGKILL`, an OOM
kill, a reboot — never writes anything, so without either signal the record would say
`running` forever and a `wait` with no `--timeout` of its own would block indefinitely.
Such a run is marked `crashed` and reported with `error_code: lost`, naming the `.out`
file that holds whatever the child managed to print.

`keel delegate wait` prints the same JSON contract as a foreground run. It exits `1` when
the run failed, when the run was lost, when it did not finish within `--timeout`, and —
failing closed — when the run id is unknown (`unknown-run`), so a mistyped id is an
immediate error rather than a wait that can only time out. `keel delegate status` lists the runs under the
state directory, as a table or, with `--json`, as a document, and applies the **same
liveness and deadline test** first — otherwise the view an operator opens precisely
because they are *not* waiting would be the one that never notices a killed child. A run id may
contain only letters, digits, `.`, `_` and `-`: it becomes a file name, and anything else
is refused rather than normalized.

**This is the primitive an orchestrating agent uses instead of a sleep loop.** A polling
loop burns the host's context window and cannot survive its turn ending — which is how a
live run finished three reviewers and posted none of their verdicts.

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

## `keel ship <project.yaml> [--root DIR] [--pr N] [--compound|--profile standard|compound] [--tdd] [--role LABEL] [--delegate PROVIDER] [--review-delegate PROVIDER]... [--effort low|medium|high] [--team PROFILE] [--dry-run] [--live] [--consent-mode MODE] [--approve-scope SCOPE] [--operator ID] [--target TARGET] [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL] [--review-comments inline|summary] [--reviewers 1|2|3] [--jury|--no-jury] [--jury-advisory] [--json] [--wizard] [--wizard-answer KEY=VALUE]...`

Run the **deterministic slice of a ship** against the current checkout and print the
assessment: how many files changed vs. the base branch, the **risk tier** (→ reviewer
count), whether the **merge window** is open, optional **CI** status (`--pr N` reads the
check-rollup through the selected GitHub transport), each gate's result, and the final
**merge decision**
(`MERGE` / `DEFER` / `BLOCK`).

If `git` cannot produce the changed-file list — a shallow or single-branch clone cannot
resolve `base...HEAD` — the command prints
`changed files : UNREADABLE (git diff failed) — classified TIER-3 fail-closed` and
classifies at the strictest tier. This is a **behaviour change for that case**: an
unreadable diff used to classify as the default TIER-2, quietly asking for one fewer
reviewer and turning the gating jury off on a change nobody could see. The JSON result and
the ledger record carry `null` file counts with an explicit `unreadable` flag rather than
`0`, for the same reason. Fetch the base branch (`git fetch origin <base>`) to restore a
measured tier.

`--gate-result <id>=pass|fail` records the verdict of a gate this command cannot execute —
an `agentic` gate, dispatched by the agent rather than by keel. Repeatable. Without it such
a gate stays `NOT-RUN`, and a `NOT-RUN` gate declared `on_fail: block` blocks the merge
decision and refuses to certify the run at `keel merge`.

It may only be given for a gate keel did **not** run. Naming a gate keel executed exits 1
with `--gate-result cannot override a gate keel executed: <id>`, and naming a gate that is
not in the plan exits 1 with `--gate-result names no planned gate: <id>`. The first refusal
is the point of the flag's design: keel has a measured verdict for a gate it ran, and
letting a recorded one replace it would turn this channel into a way to certify a run whose
gates were observed failing.

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
resolved reviewer count, and jury precedence is a `knobs.team` tier whose review policy is
`jury` over `--no-jury` over `--jury` over tier-3 auto-jury over off. All six commands that
resolve this contract accept the same three flags (`keel review` included, since #1043).
**The jury flags never change the reviewer bench**, which is a pure function of config +
tier + role + `--reviewers` / `--review-delegate`: nothing makes a run *pass* the flags to
all six — keel's CI passes `--no-jury` to `evidence-verify` on every run and to
`ship`/`plan` on none — so a bench that moved with a flag would make one command require
evidence another told the adapter not to produce. On a jury-panel
tier `--no-jury` / `--jury-advisory` are recorded in `assignment.warnings` and not applied —
the panel is that tier's only review, so its verdict stays required. `--jury-advisory` keeps an enabled jury report-only. No-jury mode still
preserves the reviewer, CI, tester, merge-window, merge-lock, closeout, and capture gates.

The resolved [`knobs.team`](configuration.md#team) team is exposed as `assignment` in both
`keel plan --command ship --json` and `keel ship --json`: `implementer`, `gate`,
`reviewers[]` (per-slot `provider`/`model`/`effort`, mirrored on
`review_merge_contract.reviewers.slots`), `review_panel`, `jury`, `fix`, and `warnings`.
`--role` selects the `team.implement.by_role` seat, `--delegate` overrides the implementer
for the run, and `--review-delegate` is repeatable and positional per reviewer slot.
`keel plan --tier` resolves the assignment for a risk tier before a diff exists;
`keel ship` resolves it against the tier it classified from the real diff.

<a id="ship-wizard"></a>

### `--wizard` — the provider picker

`--wizard` is a pre-s1 front layer that collects the same options the grammar above
produces; it adds no pipeline behaviour and cannot produce a run the flags could not.
Its choices come from the probe [`keel doctor --providers`](#keel-doctor) runs, which is
the single source of truth for what is usable here — **a provider the probe did not find
is never offered and cannot be selected**, whether it is typed at the prompt or handed in
with `--wizard-answer`.

**A default is not a decision.** Only a question you actually answer becomes a flag; an
unanswered one emits nothing and the command resolves it exactly as it would have without
`--wizard`. That is what makes quick-start safe: the reviewer bench the wizard offers is
derived at a nominal tier (the real one is not classified until s1, after the wizard has
run) and the jury question opens on whatever the flags and `knobs.team` already say, so
writing those back would *override* the policy they were read from — a quick-start run on
a tier-3 change would silently pass `--reviewers 2 --no-jury`. Pressing Enter keeps the
default and passes no flag; typing a value is an explicit override.

**What the run wizard cannot express.** The probe lists providers keel can *dispatch* to,
so a `subagent:<name>` seat is not among them: on a project whose `knobs.team` names one,
the implementer question shows the first dispatchable provider as its default. Accept it
(Enter) and the policy still decides s4 — including `implement.by_role` and any
`subagent:` seat. Answer it and you get `--delegate`, which is a per-run override: it wins
over `knobs.team.implement` outright, so `implement.by_role` and `--role` no longer apply.
Answer the implementer question only when you mean to replace the policy's seat for this
run.

A configured seat this machine cannot reach is named once and then degrades to one it can.

**Interactivity guard.** With no terminal and no recorded answers the wizard is a *logged
no-op*: it prints `wizard: non-interactive context …` and the command proceeds with the
literal flags as parsed. Never a hang, never a rejection. A machine where the probe finds
nothing usable is the same logged no-op.

`--wizard-answer KEY=VALUE` (repeatable, or semicolon-separated) pre-answers questions
without prompting, on a terminal or not — which is what makes a wizard run reproducible.
A malformed pair, or one naming a choice the wizard does not offer, exits 1 before any
gate runs rather than silently running a team nobody asked for.

Supplying any answer other than `mode` implies `mode=customize`, because the first
question's own default (quick-start) would otherwise end the walk before the second
question exists. Pass `mode=quick-start` explicitly when you really do mean "ignore the
rest":

```bash
# One reviewer from claude, summary posting, everything else left to knobs.team + the tier
keel ship .keel/project.yaml --root . --wizard \
  --wizard-answer 'implement.provider=ollama;implement.model=qwen2.5-coder' \
  --wizard-answer 'review=claude' --wizard-answer 'review_comments=summary'
```

```text
keel ship --wizard — resolved
  flags : --delegate ollama:qwen2.5-coder --reviewers 1 --review-delegate claude --review-comments summary
  seats : implement=ollama:qwen2.5-coder · review=claude
```

The `seats` line restates the flags in seat form; it never carries a value the flags do
not, so there is nothing on it for an adapter to apply separately.

Note what is *absent*: no `--jury`/`--no-jury`, because the jury question was not answered,
so the tier and `knobs.team` still decide it. An answer naming a key this run never reaches
(`review.3` in a run, `implement.model` for a provider that lists none) says so
specifically, rather than being reported as a misspelling.

A run is asked **only what a run can carry**. Every question below lands on a real flag;
one whose answer no flag could carry would be decorative, and an operator who answered it
would be told one thing while the published contract said another.

<a id="ship-wizard-questions"></a>

| question | run | `keel init --wizard` | what carries it |
| --- | --- | --- | --- |
| `mode` | yes | yes | quick-start vs customize; it steers the walk, not the run |
| `implement.provider` | yes | yes | `--delegate <provider>` |
| `implement.model` | yes | yes | `--delegate <provider:model>` |
| `jury` | yes | yes | `--jury` / `--jury-advisory` / `--no-jury` |
| `review` | yes | yes | `--reviewers` + `--review-delegate`, per slot. A config asks it once per risk tier (`review.1` / `review.2` / `review.3`) and a run asks it once, because the tier is not classified until s5 |
| `review_comments` | yes | yes | `--review-comments` |
| `implement.effort` | no | yes | nothing: there is no `--effort`, and `--delegate` splits `provider:model` and stops there. It is a `knobs.team` seat field |
| `gate.provider` | no | yes | nothing: there is no `--gate`. The seat is `knobs.team.gate`, and `assignment.gate` is what the adapter dispatches at s7 |

`tests/test_wizard.py` parses that table and fails if it stops matching the planner, so a
flag that moves a question between the columns has to move the row with it.

The **jury panel** is a config answer for the same reason as the bottom two rows:
`--reviewers` takes `1|2|3` and nothing on `keel ship` spells "the panel *is* the review",
so `review=jury` is not offered in a run and is refused if passed to `--wizard-answer`. A
tier whose *policy* is the panel still resolves to it — that comes from `knobs.team`, not
from the wizard. (If a run flag ever learns to spell a panel, this is the line to revisit.)

`keel init --wizard` asks the full set, per risk tier, because a config names a bench per
tier while a run has one — see [the team step](#init-team-step).

Output is the resolved flag set, echoed back for the adapter to pass on literally:

```text
keel ship --wizard — resolved
  flags : --delegate ollama:qwen2.5-coder --reviewers 1 --review-delegate claude --review-comments summary --jury-advisory
  seats : implement=ollama:qwen2.5-coder · gate=claude (distinct from the implementer) · review=claude
```

With `--json` that echo goes to stderr so stdout carries only the contract document.

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
CI), so it can gate a runner before it attempts a real merge. When the block comes from a
failed `on_fail: block` gate, the reason names it — `BLOCK — blocking findings from
gate(s): lint` — so the line does not read as a contradiction next to a reviewer verdict
reporting no blocking findings.

`--hotfix` marks an emergency change so it may merge **outside** the merge window (an audit
line is printed). It never bypasses failing gates, blocking findings, or failing CI.

`--json` emits the structured command contract plus a deterministic `result` record for the
dry assessment. `result.artifact_bodies` contains canonical Markdown bodies for the PR body,
issue update, reviewer verdict, jury verdict, extension result output, and the
`ship_provenance` comment a live run posts on its PR; adapters should
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

### `--tdd` (test-first s4 profile)

`--tdd` selects the **test-first s4 profile** for a single run; `knobs.implement_mode: tdd`
is the per-project spelling of the same thing (see
[configuration](configuration.md#implement_mode)). Like `--compound` it is a *profile*, not
a separate command: the backbone step ids are unchanged, s4 simply runs in two phases — a
test-only commit carrying the failing tests derived from the issue's acceptance criteria,
then the implementation that turns them green — and s8 gains the pure, blocking
`tdd-order` gate that verifies that commit order actually happened. The gate checks
**order and paths only**: it never runs phase A's tests and cannot report that they were
red.

```bash
keel ship .keel/project.yaml --root . --tdd --dry-run --json
# contract.implement_mode == {"mode": "tdd", "tdd": true, "source": "flag:--tdd",
#                             "phases": ["tests", "implementation"], "gate": "tdd-order"}
```

The same block is rendered by `keel plan --tdd`, and `keel run-gates --tdd` adds the gate
on its own. There is no `--no-tdd`: a project that configured the contract has said the
contract is the policy, and a flag that switched it off from a command line would make it
advisory. A `--live --append-ledger` run records `run_context.implement_mode: "tdd"` and one
`run_context.implement_phases` entry per phase with its commit and the implementer that
ran it (`--phase-implementer tests=<label>` when a phase really did run on a different
provider), which the closure comment renders as
`Implement: TDD (tests <sha> by <implementer> → implementation <sha> by <implementer>)`.
A run that does not use the profile records `implement_mode: null` and
`implement_phases: []`, and its closure comment is unchanged.

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

## `keel canary <project.yaml> [--root DIR] [--pr N] [--commit SHA] [--duration M] [--health-cmd CMD] [--auto-revert] [--json]`

Run the post-merge health probe and, optionally, revert on a regression. The probe is
`--health-cmd`, falling back to `knobs.build_gate_cmd` and then to `make test`.

```bash
keel canary .keel/project.yaml --root . --pr 456
keel canary .keel/project.yaml --root . --commit 0d9ad10 --auto-revert --json
```

`--pr` and `--commit` only name the target in the report, with one exception: `--auto-revert`
reverts nothing unless `--commit` names the merge commit to revert — there is no inference
from a PR number, because reverting the wrong commit is worse than not reverting.
`--duration` is accepted and currently inert: the probe runs once, and the flag is
reserved for the sustained-monitoring shape rather than pretending to implement it.

Exit 0 when the probe passes (`status: healthy`), 1 when it fails
(`status: regression_detected`, with `reverted` and `revert_commit` recording what the
rollback did) or when the config cannot be loaded.

## `keel rollback COMMIT [--root DIR] [--json]`

Atomically revert one merge commit.

```bash
keel rollback 0d9ad10 --root . --json
```

`git revert --no-edit -m 1 <sha>` first, since the target is normally a merge commit;
a single-parent revert is the fallback. If both fail the command runs `git revert --abort`
so the working tree is left clean rather than mid-revert, and reports the git output as
`error`. On success the new revert commit's SHA is reported as `revert_sha`.

Exit 0 when the revert commit was created, 1 otherwise.

## `keel cost-report [--root DIR] [--json]`

Report token consumption, estimated USD cost and per-model analytics from the activity
records under `.keel/activity`. It takes **no** project.yaml — the records are the input.

```bash
keel cost-report --root . --json
```

A missing or empty activity directory is an empty report, not an error: a project that has
not stamped any activity has spent nothing keel can see. Always exits 0.

## `keel init [--root DIR] [--force] [--wizard] [--auto]`

Scaffold a default `.keel/project.yaml` for the repo. keel detects the stack from marker
files (`Cargo.toml`→Rust, `go.mod`→Go, `pom.xml`→Java, `pubspec.yaml`→Flutter,
`pyproject.toml`/`setup.py`/`requirements.txt`→Python, `package.json`→Node,
`build.gradle*`→Android, else generic) and writes a config that already passes
`keel validate`. Refuses to overwrite an existing config unless `--force`.

`--force` replaces `.keel/project.yaml`; it does not delete or rewrite `.keel/extensions/*`.
Use it only when intentionally regenerating project config.

```bash
keel init                 # scaffold .keel/project.yaml for the detected stack
keel init --auto          # smart auto-detect stack, base branch, and test/lint gates without prompts
keel init --root ../app   # scaffold elsewhere
keel init --wizard        # prompt for base branch, merge-window hours, timezone, commands, team
```

With `--auto`, keel inspects project marker files, detects the primary base branch from git
(`main`, `develop`, `master`, `trunk`), selects recommended test and lint commands, and prints a
structured summary report.

With `--wizard`, keel prompts for each value (base branch, timezone, **merge window
`HH:MM-HH:MM`**, build/lint commands); press Enter to accept the stack default, or leave a
field blank to skip it. The result still passes `keel validate`.

<a id="init-team-step"></a>

### The team step

`--wizard` ends with a **team step** that writes [`knobs.team`](configuration.md#team) —
who implements, who gives the mandatory gate review, who reviews at each risk tier, and
how the jury gates. Its options come from the same probe
[`keel doctor --providers`](#keel-doctor) runs, so the block names providers that are
usable *on this machine* rather than seats copied out of somebody else's example. A
provider the probe did not find is never offered and cannot be typed in.

The first question is **quick-start vs customize**: quick-start takes every default and
asks nothing else (an implementer, a reviewer bench of one/two/three seats by tier
preferring two distinct vendors, no gate seat, no jury). Customize walks the rest —
implementer provider, its model and reasoning effort where the provider can express one,
the gate seat, the jury mode, the bench for each tier, and the review-comments mode.

Two rules the step will not let you break, because `keel validate` would not either:

- the gate seat is offered from every provider **except** the implementer, and is written
  with `distinct_from: implementer` — a gate review from the vendor that wrote the change
  is not a second opinion;
- reasoning effort is only asked for a provider that has a spelling for it, and only once
  a model is chosen for a vendor that spells effort as a model *suffix* (`agy`);
- the `jury` bench is offered only when the jury is set to **gating**. "The panel is the
  review" beside an advisory jury leaves that tier with nothing enforceable — no host
  reviewer slots, and an advisory verdict is not required evidence — which validation
  refuses outright.

On a machine where the probe finds nothing usable the step is skipped and no `team` block
is written at all — an absent block is not an empty one, and leaves `config_hash` exactly
where it was.

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
| `site` | `website/params.js` (repo root) | the static site's `window.KEEL_ARGS` — each command's description, `argument-hint` and flag chips |

```bash
keel install-adapter claude          # → /keel:ship, /keel:regression, …
keel install-adapter skills          # → one shared keel-<cmd> skill set under .agents/skills/
keel install-adapter all             # both surfaces
keel install-adapter claude --force  # overwrite existing adapters
keel install-adapter plugin          # regenerate the committed plugin command files (commands/)
keel install-adapter site            # regenerate the site's argument surface (website/params.js)
```

The `plugin` target is **repo-level**, not per-project: it regenerates the committed
`commands/<cmd>.md` files that the Claude Code plugin ships (see [plugin.md](plugin.md)).
`make plugin` is the same command; a drift test fails if the committed files diverge from the
`src/keel/adapters/commands/` source bodies.

The `site` target is repo-level for the same reason: it renders `website/params.js` out of the
same frontmatter, and `make site-params` is the same command. Every published field is derived
— `desc` is the frontmatter `description`, `hint` is its `argument-hint`, and the flag chips
are that hint's top-level `[...]` groups — so the file has no hand-maintained region, and
`tests/test_install.py::TestSiteParamsGenerator` fails if the committed copy is not
byte-identical to the generator's output. Neither repo-level target is written by
`install-adapter all`, which installs the per-project surfaces only.

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

The target is `all` or one of the surfaces `claude`, `skills`, `legacy-claude`.
`adapter-status` reports:

| status | meaning |
|---|---|
| `current` | installed generated file matches the packaged source |
| `outdated` | installed generated file is unchanged locally, but packaged source changed |
| `missing` | expected generated file is absent |
| `locally-modified` | generated file has a marker, but its body changed after install |
| `unknown` | file exists without a keel generated marker |

Legacy claude wrappers (`legacy-claude`, installed by `install-legacy-wrappers`) are
**opt-in**: any *absent* wrapper — whether never installed or installed and later
removed — is reported as *not installed* (omitted, not flagged `missing`), so a
project that never opted in shows no `legacy-claude` rows. Installed legacy wrappers
that are present are still freshness-checked like any other surface.

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

## `keel adapter-status [TARGET] [--root DIR] [--include-unmanaged] [--json]`

Report generated-adapter freshness plus orphan / unmanaged surface findings. `TARGET` is
`all` (default) or one of `claude`, `skills`, `legacy-claude`. The status vocabulary and
the orphan/unmanaged split are described under
[`keel install-adapter`](#keel-install-adapter-target---root-dir---force) above.

```bash
keel adapter-status all --root .
keel adapter-status all --root . --include-unmanaged --json
```

Advisory only: keel never deletes a file and no finding here gates a run. Exit 1 only on an
unknown target; otherwise 0, findings or not.

## `keel update-adapter [TARGET] [--root DIR] [--dry-run]`

Safely refresh generated adapters from the installed keel package. `TARGET` is `all`
(default), `claude` or `skills`.

```bash
keel update-adapter all --root . --dry-run
keel update-adapter all --root .
```

Updates `missing` and `outdated` files only, and refuses to overwrite `locally-modified` or
`unknown` ones — those need a human merge. Exit 1 only on an unknown target.

## `keel sync [--root DIR] [--target all|claude|skills] [--dry-run]`

The everyday short name for `update-adapter all`, plus the orphan heads-up and the
follow-up commands.

```bash
pipx upgrade keel-workflow
keel sync --root . --dry-run
keel sync --root .
```

`sync` uses the keel package already installed in the active environment: it does not
contact PyPI, choose a version, or change the installation — upgrade `keel-workflow` with
`pipx`/`pip` first. After a successful sync it prints the orphan count when there is one
and recommends `keel validate` / `keel plan`. Same exit codes as `update-adapter`.

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

## `keel swarm-plan <project.yaml> [--issues N,N,…] [--issue N] [--declared-file PATH] [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL] [--swarm-id ID] [--tree] [--delegate PROVIDER] [--review-delegate PROVIDER] [--effort low|medium|high] [--team PROFILE] [--reviewers 1|2|3] [--json]`

Perform deterministic static dependency analysis, scope prediction, conflict matrix calculation,
wave tier partitioning, **difficulty scoring and per-cluster staffing** across a list of backlog
issues without mutating git or spawning workers.

The issues are named by flag, not as positionals: `--issues` takes one comma-separated list and
`--issue` is repeatable. Planning is pure — it reads no repository state — so `swarm-plan` has no
`--root`.

```bash
keel swarm-plan .keel/project.yaml --issues 714,715,716,717 --tree
keel swarm-plan .keel/project.yaml --issue 714 --issue 715 --json
keel swarm-plan .keel/project.yaml --issues 714,715 --team night-shift --effort high --json
```

Use `--tree` to render an interactive ASCII DAG execution diagram directly in your terminal.

Every cluster in `--json` carries two extra records (#1017):

- **`difficulty`** — `band` (`easy`/`standard`/`hard`), `score`, the resolved risk `tier`,
  `file_count`, `dependency_depth`, and the `signals` that produced the score. It is a pure
  function of the risk tier from `knobs.tier3_globs`, the predicted file count, `priority:*`
  and `size:*` labels, and how much already-scheduled work the cluster depends on.
- **`assignment`** — the same record `keel ship --json` renders, resolved per cluster with
  *this* cluster's role, tier and difficulty band: `lead`, `implementer`, `effort`, `gate`,
  `reviewers[]`, `review_panel`, `fix`, and `warnings`. Seats come from
  [`knobs.team`](configuration.md#team) plus `knobs.team.by_difficulty`, with `--team
  <profile>` and the per-run flags above layered on top.

Scoring and staffing run **after** the partition and never feed back into it: changing
`team.by_difficulty` (or passing `--team`) changes who runs a cluster and cannot change which
wave it lands in.

## `keel swarm-status <project.yaml> [--root DIR] [--swarm-id ID] [--json]`

Inspect live worker progress, wave execution status, and cluster health across active or recent
multi-agent swarm runs. Each row names the worker's **lead** and the difficulty **band** it was
staffed from, so the board answers *who is running this, and why that provider*:

```bash
keel swarm-status .keel/project.yaml --root .
keel swarm-status .keel/project.yaml --root . --swarm-id swarm-2026-08-15 --json
```

## `keel swarm-run <project.yaml> [--root DIR] [--issues N,N,…] [--issue N] [--swarm-id ID] [--max-workers N] [--live] [--tree] [--delegate PROVIDER] [--review-delegate PROVIDER] [--effort low|medium|high] [--team PROFILE] [--reviewers 1|2|3] [--json]`

Launch parallel workers per cluster in dedicated git worktrees under `.keel/worktrees/swarm/`:

```bash
keel swarm-run .keel/project.yaml --root . --issues 714,715,716,717
keel swarm-run .keel/project.yaml --root . --issues 714,715,716,717 --live --max-workers 2
```

Each worker runs the standard `keel ship` backbone machine in its isolated worktree, launched
with its cluster's resolved team: the implementer seat becomes `--delegate`, each staffed
reviewer slot a `--review-delegate`, the cluster's role `--role`, and the bench it was staffed
from `--effort` / `--team`. Passing the last two means **the child inherits the cluster's
difficulty bench** and re-resolves to the same seats rather than deriving a different team from
config alone; a `--delegate` on the same line still wins, so the parent can override what the
bench chose. A seat that is a host `subagent:` rather than a provider is left to the adapter,
which is the layer that can spawn one. A role label outside `[A-Za-z0-9][A-Za-z0-9._-]*` is
dropped rather than passed — it would be read as a flag by the child — and the reason is
recorded in `assignment.warnings`.

Issues are named by `--issues` / `--issue`, as for `swarm-plan`. Rebalancing across waves is
decided by the plan, not by a flag: when runtime file-modification divergence is detected the
conflicting worker is partitioned to a later wave.

## `keel swarm-land <project.yaml> [--root DIR] [--wave N] [--issues N,N,…] [--issue N] [--swarm-id ID] [--live] [--delegate PROVIDER] [--review-delegate PROVIDER] [--effort low|medium|high] [--team PROFILE] [--reviewers 1|2|3] [--json]`

Land passing cluster branches from completed execution waves into `main` under atomic `merge_lock`:

```bash
keel swarm-land .keel/project.yaml --root . --wave 1
keel swarm-land .keel/project.yaml --root . --wave 1 --live
```

The landing mode is **derived, not chosen**: `evaluate_wave_landing_mode` reads the wave's diff
map and picks batch or funnel, so there is no `--mode` flag to get wrong. `--wave` selects the
wave (default `1`); without `--live` the command reports what it would land.

- **Direct Batch Mode**: Orthogonal disjoint diff trees land concurrently.
- **Adaptive Funnel Mode**: Overlapping trees land sequentially with automatic fail-soft rebase healing.
- **Review evidence (#828)**: before a live landing, every cluster branch's open PR must pass
  the same pre-merge review-evidence verification `keel merge` enforces — armed gate label,
  tier-derived verdict count, verdicts pinned to the PR head. A cluster that does not verify is
  **held** (reported with its reason, never merged); held clusters degrade the wave status like
  failures without being counted as one. Fail-closed at every step: no open PR, a transport
  error, an unarmed gate, an ambiguous branch (more than one open PR), and a
  local branch tip that does not match the reviewed PR head all hold; a
  cluster whose PR is already merged holds with an "already landed in an
  earlier run" reason instead of a misleading one. In funnel mode a rebase always
  rewrites SHAs, so re-pinning to the old head would make the mode a permanent
  no-op; what matters is whether a *content* decision was made. A clean replay
  of the reviewed commits lands; a rebase whose conflicts the resolver
  auto-resolved holds, because those bytes were never reviewed. The merge
  re-reads the branch tip locally inside the lock — on both paths, and on the
  funnel path *before* the rebase, since the rebase itself voids the pin — and
  holds if it moved since the (network-bound) check, which runs before the
  lock is taken. A funnel hold rewinds the branch to the reviewed commit so a
  rejected cluster is not left rewritten and un-landable. Everything
  targets `base_branch` from the project config — the PR lookup, the merge and
  the rebase — so the diff verified is the diff that lands. A *live* wave with any
  held cluster exits non-zero, so automation cannot read "refused to land
  unreviewed code" as success. The gate also runs in **dry runs** — the checks
  are read-only — so a preview reports `would hold: <reason>` per cluster
  instead of promising a landing that a live run would refuse; a dry run still
  exits 0, because predicting correctly is not a failure. The explicit opt-out is `knobs.swarm_review_evidence:
  false`, which `swarm-land` announces loudly — the exception lives in config, never in a
  driver's judgement call.

## `keel-visual swarm <project.yaml> [--root DIR] [--swarm-id ID] [--out FILE] [--serve] [--port PORT] [--json]`

Render interactive 2D DAG cluster partition graphs and 3D multi-wave spatial topologies:

```bash
keel-visual swarm .keel/project.yaml --root . --out keel-swarm.html
keel-visual swarm .keel/project.yaml --root . --serve --port 8766
```

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | a config was invalid/missing, or a plan target could not be loaded |
| 2 | no command given (help printed) |
