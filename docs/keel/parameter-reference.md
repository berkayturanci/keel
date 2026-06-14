# keel parameter reference

This is the exhaustive per-flag reference for the `keel` CLI and the `/keel:ship` adapter
argument grammar. [`cli.md`](cli.md) is the quick reference — per-command summaries with
worked examples; this document goes one level deeper: every flag's type, allowed values,
default, the exact contract fields and gates it changes, its precedence and interaction
rules, and what makes a command exit non-zero. Every claim here is grounded in
`src/keel/cli.py` and the pure modules it calls (`ship.py`, `consent.py`, `classify.py`,
`window.py`, `evidence.py`, `runcontrols.py`, `capture.py`, `checkpoint.py`).

## Shared flags

These flags recur across many subcommands with identical semantics. They are documented
once here in full; the per-command sections below only note deviations.

### `--root DIR`

- **Type / values:** directory path. **Default:** `.` (current directory), except
  `keel validate` where the default is *unset* (`None`).
- **What it changes:** the repository root used to resolve extension files
  (`.keel/extensions/`), run git operations and command gates, detect runtime
  capabilities, and resolve state paths — the run ledger
  (`.keel/state/run-ledger.jsonl`), the checkpoint (`.keel/state/checkpoint.json`), and
  the single-host lock store (`.keel/state/locks/`).
- **Accepted by:** every subcommand that touches the filesystem or git. On
  `keel validate` it is opt-in: passing `--root` additionally strict-validates the
  extensions the config references; omitting it checks the config schema only.
- **Example:** `keel plan .keel/project.yaml --root .`

### `--json`

- **Type / values:** boolean flag. **Default:** off (human-readable text).
- **What it changes:** output format only — never the decision logic or the exit code
  semantics. With `--json`, commands emit the structured command contract (and, where
  applicable, a deterministic `result` record) as indent-2, sorted-keys JSON. Adapters
  must parse the JSON output, never the human text.
- **Accepted by:** every contract-emitting subcommand. Not present on `version`,
  `validate`, `run-gates`, `window`, `init`, `setup`, `install-adapter`,
  `update-adapter`, `sync`, and `install-legacy-wrappers`.
- **Example:** `keel ship .keel/project.yaml --root . --json`

### `--live` / `--dry-run`

- **Type / values:** boolean flags. **Default:** both off — and the *default contract is
  the dry-run contract* (`dry_run = not --live` everywhere a contract is built).
- **What `--live` changes:** the command renders a **live preflight contract** instead of
  a dry-run one. The consent gate becomes enforceable: if the command declares live
  mutation side effects and the required consent scopes are not approved,
  `assert_operator_consent` fails and the command exits 1 (printing the consent prompt
  and the missing scopes). Under `--live`, supplied issue context that is not `ready`
  also blocks (see `--issue-title` below), and `keel ship --live --append-ledger` becomes
  a real ledger write. The keel CLI itself still performs no code mutation for
  ship/standalone contracts — "live" means the *contract* is live; adapters perform the
  approved work.
- **What `--dry-run` changes:** explicitly marks the assessment as non-mutating. For
  contract commands this is largely declarative (the default is already dry), but several
  commands attach real behavior to it: `keel merge --dry-run` runs the full
  lock/window/CI/evidence pipeline and stops before the merge (exit 0,
  `"dry-run: merge not performed"`); `keel post-comment --dry-run` plans `post` vs `edit`
  without mutating GitHub; `keel evidence-verify --dry-run` skips the live `gh` fetch and
  requires zero evidence items; `keel runcontrols --dry-run` evaluates without appending
  the event; `keel update-adapter --dry-run` / `keel sync --dry-run` print planned writes
  only.
- **Interaction:** `keel ship`, `keel implement`, and the other standalone contracts
  reject `--dry-run --live` together: `"--dry-run and --live cannot be used together"`,
  exit 1. `keel merge` has no `--live` flag — it forces `args.live = True` internally and
  is always a live, consent-gated command (`--dry-run` is its only rehearsal mode).
- **Consent implication:** dry-run contracts never *require* consent
  (`requires_operator_consent` is false; status is `not-required-dry-run` or
  `not-required-read-only`), but still report the scopes a live run would need.

### `--consent-mode MODE`

- **Type / values:** `explicit` | `standing` | `agent` (`consent.CONSENT_MODES`).
  **Default:** `None`, which resolves through the chain
  `--consent-mode` > `KEEL_CONSENT_MODE` env var > `consent_mode` in `.keel/project.yaml`
  > built-in `explicit`. An unknown mode anywhere in the chain is an error (exit 1).
- **What it changes:** how approved consent scopes are sourced for a live run
  (`_approved_consent` in `cli.py`):
  - `explicit` — only `--approve-scope` flags count. Env and config standing approvals
    are ignored.
  - `standing` — when no `--approve-scope` flag is given and the command has live
    mutation scopes, keel accepts `KEEL_APPROVE_SCOPE` from the environment (which then
    *requires* `KEEL_OPERATOR`, else exit 1) or `automation.approved_scopes` from project
    config (which requires `automation.operator`, else exit 1). Approval source is
    recorded as `env` or `config`.
  - `agent` — keel grants no scopes itself; the consent status becomes
    `agent-delegated` and the approval prompt is delegated to the host agent's own
    permission system. The structured contract is still emitted.
- **Accepted by:** `plan`, `merge`, `ship`, `implement`, `morning`, `wrap`,
  `work-block`, `overnight`, `regression`, `review-all-day`.
- **Example:** `KEEL_CONSENT_MODE=agent keel plan .keel/project.yaml --command ship --live --json`

### `--approve-scope SCOPE`

- **Type / values:** repeatable; each occurrence may also be a comma-separated list.
  Valid scope names: `filesystem`, `git`, `github`, `secrets`, `release`,
  `production-adjacent` (`consent.CONSENT_SCOPES`). An unknown scope name is an error
  (exit 1). **Default:** empty.
- **What it changes:** approves consent scopes for this run with approval source `flag`
  (the highest-precedence source — an explicit flag wins over env and config in every
  consent mode). The command's declared side effects map to required scopes (e.g.
  `git_worktree` → `filesystem, git`; `merge`/`comments`/`labels` → `github`;
  `secret_access` → `secrets`). A live run whose required scopes are not all approved
  has `requires_operator_consent: true` and exits 1. Approved live runs include a local
  `consent_record` (timestamp, operator, workflow, target, scopes) in JSON output;
  secret values are never recorded.
- **Accepted by:** the same set as `--consent-mode`.
- **Example:** `keel merge .keel/project.yaml --pr 123 --approve-scope filesystem,git,github --operator "$USER"`

### `--operator ID`

- **Type / values:** free-form operator identifier string. **Default:** `None`.
- **What it changes:** the operator identity recorded in the `consent_record` of an
  approved live run and in escalation/audit evidence. It does not by itself approve
  anything. Note the standing-mode counterparts: `KEEL_OPERATOR` is *mandatory* when
  `KEEL_APPROVE_SCOPE` is used, and `automation.operator` is mandatory when
  `automation.approved_scopes` is used — both fail with exit 1 when missing.
- **Accepted by:** the same set as `--consent-mode`.

### `--target TEXT`

- **Type / values:** free-form text. **Default:** `None`.
- **What it changes:** the human-readable task target embedded in the consent prompt and
  consent record, and (for standalone commands) in the rendered contract's
  `target` line. For `keel ship`, when `--target` is omitted but `--pr N` is given the
  target defaults to `PR #N`. For standalone commands the target is *composed*: an
  `--issue`/positional issue, `--since`, `--scope`, `days`, explicit issue lists,
  `--queue`, `title`, or `hours` each produce a canonical target string, and `--target`
  text is appended in parentheses. On `keel post-comment` the flag is **different**: it
  is required and must match `issue:<number>` or `pr:<number>` (see that command).
- **Accepted by:** `plan`, `ship`, `implement`, `ci-check`, `morning`, `wrap`,
  `work-block`, `overnight`, `regression`, `review-all-day`, `checkpoint`
  (stored verbatim), and `post-comment` (different semantics).

### `--issue-title TITLE` / `--issue-body BODY` / `--issue-label LABEL`

- **Type / values:** `--issue-title` and `--issue-body` take one string each;
  `--issue-label` is repeatable and each value may be comma-separated (labels are split,
  trimmed, and de-duplicated preserving order). **Default:** none supplied.
- **What they change:** supplying any of them (non-blank title/body or at least one
  label) marks "issue context provided" and causes the command contract to include a
  populated `issue_intake` block. Intake classifies the issue as `ready`,
  `needs-input`, `blocked`, or `out-of-scope`, extracts acceptance criteria and
  docs/test expectations, and generates concrete clarification questions. Under
  `--live`, a non-`ready` intake (`can_mutate_code` false) makes `keel ship` and
  `keel implement` exit 1 *before* gates run or any branch/worktree is created, printing
  the status, reason, and questions (or the full contract with `--json`). Dry runs record
  the same readiness decision without blocking. On `keel ship`, `--issue-title` and the
  labels are also stamped into the run-ledger record.
- **Accepted by:** `plan`, `ship`, `implement`.
- **Example:**

```bash
keel ship .keel/project.yaml --root . --live \
  --issue-title "Add setup docs" --issue-body "$ISSUE_BODY" --issue-label enhancement \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

### Review / jury flag family (`--review-comments`, `--reviewers`, `--jury`, `--no-jury`, `--jury-advisory`)

These five flags feed `ship.resolve_review_contract` and appear on `plan`, `merge`,
`ship`, `step-verify`, and `evidence-verify` (`work-block` and `overnight` carry
`--review-comments`/`--reviewers` only, as pass-throughs to per-issue ship handoffs).

- `--review-comments inline|summary` (default `inline`) — the s7 review posting mode in
  the contract: `inline` anchors critical/major findings as inline review comments with
  a per-reviewer soft fallback to summary; `summary` posts one consolidated comment per
  reviewer. It changes how findings are *posted*, never how they gate.
- `--reviewers 1|2|3` (default: unset → derived from the risk tier) — overrides the
  reviewer count. Tier mapping (`ship.reviewer_count`): TIER-3 → 3, TIER-2 → 2,
  TIER-1 → 1; when no tier is resolvable the default is 2. With an override, the
  contract records `reviewers.source: "override"` (vs `"risk-tier"` / `"unresolved"`),
  and `minimum_lgtm` equals the count. Reviewer focus dimensions merge when the count
  drops; none are ever dropped. The override does **not** suppress tier-3 jury
  auto-enablement in the contract resolution.
- `--jury` / `--no-jury` / `--jury-advisory` — control the cross-vendor jury gate.
  Precedence (`ship.resolve_jury`): `--no-jury` > `--jury` > tier-3 auto-on > off
  (default). When enabled, the mode is `gating` unless `--jury-advisory` makes it
  `advisory` (report-only). On `keel ship` the resolved jury mode is also passed to the
  built-in `jury` gate runner, so an advisory jury produces non-blocking findings.
  Disabling the jury never weakens the reviewer, CI, tester, merge-window, merge-lock,
  closeout, or capture gates (`no_jury_preserves_review_and_test_gates: true`).

## `keel version`

Print the installed keel version. No flags (besides the global `--version` on the parser
itself, which prints the same string).

```bash
keel version
keel --version
```

## `keel setup`

One-pass project setup: scaffold `.keel/project.yaml`, install adapters,
strict-validate, and render the plan.

```
keel setup [--root DIR] [--adapter-target all|claude|skills] [--force] [--wizard]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `--root DIR` | path | `.` | Project root to set up. |
| `--adapter-target` | `all` \| `claude` \| `skills` | `all` | Which adapter surface to install. |
| `--force` | flag | off | Overwrite existing config and generated adapters. |
| `--wizard` | flag | off | Prompt interactively for config values. |

### Details

Without `--force`, an existing `.keel/project.yaml` is reused (the step prints
`using existing`); adapters that already exist are skipped. With `--force`, the config is
re-scaffolded from the detected stack and generated adapters are overwritten —
`.keel/extensions/*` is never deleted or rewritten either way. `--wizard` runs the same
interactive collector as `keel init --wizard` (stack-detected defaults, Enter to accept).
After install, setup strict-validates config + extensions and renders the plan; a
validation or plan failure exits 1.

### Examples

```bash
# First-time setup with all adapter surfaces
keel setup --root .
# Interactive config values, Claude-only adapter surface
keel setup --root . --wizard --adapter-target claude
# Regenerate config and adapters (extensions untouched)
keel setup --root . --force
```

## `keel validate`

Validate project config(s) against the bundled schema; with `--root`, strict-validate
extensions too.

```
keel validate <project.yaml…> [--root DIR]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `paths` | one or more file paths | required | Config file(s) to validate. |
| `--root DIR` | path | `None` | When set, also strict-validate the referenced extensions under `DIR`. |

### Details

Each file is reported as `OK` / `INVALID` / `MISSING`; any non-OK file makes the final
exit code 1 (the command keeps validating the remaining files). With `--root`, an
extension that cannot be loaded fails the file as `INVALID (extensions)`. Without
`--root`, only the config schema is checked.

### Examples

```bash
# Schema only, many configs
keel validate projects/*.yaml
# Schema + strict extension validation (CI gate form)
keel validate .keel/project.yaml --root .
```

## `keel plan`

Render the backbone plan and (with `--json`) the full structured command contract for any
adapter command — the dry-run view of what a run would execute, plus the operator-consent
preflight.

```
keel plan <project.yaml> [--root DIR] [--command COMMAND] [--profile standard|compound]
          [--live] [--approve-scope SCOPE] [--operator ID] [--consent-mode MODE]
          [--target TEXT] [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL]
          [--review-comments inline|summary] [--reviewers 1|2|3]
          [--jury] [--no-jury] [--jury-advisory] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config to plan. |
| `--root DIR` | path | `.` | Extension/capability resolution root. |
| `--command COMMAND` | command name | `ship` | Which adapter command contract to embed in JSON output. |
| `--profile` | `standard` \| `compound` | `standard` | Workflow profile for ship command contracts. |
| `--live` | flag | off | Render the live preflight contract; exit 1 if consent is missing. |
| `--approve-scope` | consent scopes, repeatable | none | Approve scopes for this run (shared semantics). |
| `--operator ID` | string | `None` | Operator identity for the consent record. |
| `--consent-mode` | `explicit` \| `standing` \| `agent` | resolved chain | Consent sourcing mode (shared semantics). |
| `--target TEXT` | string | `None` | Target text for the consent prompt/record. |
| `--issue-title/-body/-label` | strings / repeatable | none | Populate `contract.issue_intake` (shared semantics). |
| `--review-comments` | `inline` \| `summary` | `inline` | Review posting mode in ship-like contracts. |
| `--reviewers` | `1` \| `2` \| `3` | tier-derived | Reviewer count override in the contract. |
| `--jury` / `--no-jury` / `--jury-advisory` | flags | off | Jury contract control (shared precedence). |
| `--json` | flag | off | Emit `{contract, plan, capabilities, github_transport}`. |

### Details

`--command` selects which command's contract is built (`ship`, `morning`, `regression`,
a project command, …); `regression` and `review-all-day` get the scan capability
requirement, every other command the gate/worktree-derived one. `--profile compound`
makes the ship contract carry `workflow_profile.profile: "compound"` with
`step_overrides` for s4/s7/s9/s11 (see the `/keel:ship` adapter section). The path
resolution for the run ledger and checkpoint is validated up front — an invalid
configured path exits 1 before any plan renders.

Under `--live`, after printing the resolved contract the command runs
`consent.assert_operator_consent` and exits 1 when any required scope is missing — this
is the adapter's s0 operator-consent preflight. Extensions that fail to load are
warnings on stderr (fail-soft); the plan still renders with built-in gates.

Failure behavior: missing/invalid config, invalid ledger/checkpoint path, gate
configuration errors, unknown consent mode/scope, and missing live consent all exit 1.

### Examples

```bash
# Human plan view
keel plan .keel/project.yaml --root .
# Full ship contract, live preflight (the s0 consent gate)
keel plan .keel/project.yaml --root . --command ship --live --json
# Approve scopes inline for a live ship preflight
keel plan .keel/project.yaml --root . --command ship --live \
  --approve-scope filesystem,git,github --operator "$USER" --target "issue #123" --json
# Compound-profile ship contract with an advisory jury
keel plan .keel/project.yaml --root . --command ship --profile compound --jury-advisory --json
# Re-run the preflight with the selected issue for intake classification
keel plan .keel/project.yaml --root . --command ship --live \
  --issue-title "$ISSUE_TITLE" --issue-body "$ISSUE_BODY" --issue-label "$ISSUE_LABELS" --json
```

## `keel claim`

Acquire a single-host resource claim (atomic `mkdir` under
`<root>/.keel/state/locks/`) — the same primitive `keel merge` uses internally.

```
keel claim RESOURCE --owner ID [--root DIR] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `resource` | string, e.g. `merge` | required | Resource name to claim. |
| `--owner ID` | string | **required** | Claim owner id, reported on contention. |
| `--root DIR` | path | `.` | Root for the claim store. |
| `--json` | flag | off | Structured claim result. |

### Details

Exit 0 when the claim is granted, 1 when denied; a denied claim reports the current
holder when known. The claim survives until `keel release` (or manual cleanup) — it is
intentionally not auto-expiring.

### Examples

```bash
# Claim the merge resource for one ship run
keel claim merge --owner "ship-pr-123" --root . --json
```

## `keel release`

Release a single-host resource claim.

```
keel release RESOURCE [--owner ID] [--root DIR] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `resource` | string | required | Resource name to release. |
| `--owner ID` | string | `None` | Owner id; omit to release regardless of holder. |
| `--root DIR` | path | `.` | Root for the claim store. |
| `--json` | flag | off | Structured release result. |

### Details

Exit 0 when the result is `released` or `missing` (releasing an unheld resource is not
an error); any other status (e.g. owned by someone else when `--owner` is given) exits 1.

### Examples

```bash
keel release merge --owner "ship-pr-123" --root .
```

## `keel merge`

The fail-closed, core-owned PR merge (backbone s10): lock → window re-check → CI rollup →
evidence verification → `gh pr merge`. Always a live, consent-gated command.

```
keel merge <project.yaml> --pr N [--root DIR] [--issue N] [--method squash|merge|rebase]
           [--owner ID] [--hotfix] [--dry-run]
           [--approve-scope SCOPE] [--operator ID] [--consent-mode MODE]
           [--risk-tier T] [--trust-signal S] [--retry-count N] [--conflicting-sources]
           [--changed-lines N] [--escalation-side-effect EFFECT]
           [--review-comments inline|summary] [--reviewers 1|2|3]
           [--jury] [--no-jury] [--jury-advisory] [--gate-label NAME] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for git/GitHub operations and the lock store. |
| `--pr N` | positive int | **required** | Pull request to merge. |
| `--issue N` | positive int | `None` | Linked issue for evidence verification; otherwise inferred from the PR body's `Closes #N`. |
| `--method` | `squash` \| `merge` \| `rebase` | `squash` | GitHub merge method passed to `gh`. |
| `--owner ID` | string | `keel-merge-pr-<N>` | Merge-lock claim owner id. |
| `--hotfix` | flag | off | Audited merge-window bypass (window check skipped; recorded as `window: {bypassed: true, reason: "hotfix"}`). |
| `--dry-run` | flag | off | Run every check stage, stop before the merge (exit 0). |
| `--approve-scope` / `--operator` / `--consent-mode` | shared | — | Consent for side effects `git_worktree` + `merge` (scopes `filesystem`, `git`, `github`). |
| `--risk-tier` | `tier-1` \| `tier-2` \| `tier-3` | `tier-1` | Risk input to the deterministic escalation evaluation. |
| `--trust-signal` | `high` \| `medium` \| `low` | `medium` | Trust input to escalation. |
| `--retry-count N` | int | `0` | Escalation trigger: `>= 2` fires `repeated-retry`. |
| `--conflicting-sources` | flag | off | Escalation trigger: conflicting sources. |
| `--changed-lines N` | int | `0` | Escalation trigger: `>= 500` fires `large-diff`. |
| `--escalation-side-effect` | string, repeatable | none | Extra side-effect signals added to escalation evaluation. |
| `--review-comments` / `--reviewers` / `--jury` / `--no-jury` / `--jury-advisory` | shared | — | Shape the review contract used by the embedded evidence verification. |
| `--gate-label NAME` | string | `knobs.evidence_gate_label` | Override the legacy evidence arming label for this run. |
| `--json` | flag | off | Structured `keel.merge.v1` payload. |

### Details

**Pipeline and failure points (all exit 1 without merging):** missing consent scopes;
operator escalation required with an unapproved escalation scope; lock denied
(`resource lock is already held`); merge window closed (unless `--hotfix`); unreadable PR
snapshot; PR merge state not in `CLEAN`/`HAS_HOOKS`/`UNKNOWN`; CI rollup not `pass`
(failure-before-pending precedence: any failing check fails the rollup even with pending
checks); evidence gate not enforced (`evidence gate is not enforced` — a PR without ship
provenance cannot pass through `keel merge`); missing evidence artifacts; and finally a
failed `gh` merge. The lock is always released in a `finally` block.

**`--hotfix`** bypasses *only* the window check. It never bypasses the lock, CI rollup,
or evidence verification, and the bypass is recorded in the JSON payload (`window:
{bypassed: true, reason: "hotfix"}`) for audit.

**Escalation** (`consent.evaluate_escalation`) is deterministic: side-effecting commands
always trigger `irreversible-or-side-effecting`; the required escalation scopes must be
covered by `--approve-scope` or the command exits 1 with the missing scopes listed.
`--risk-tier`/`--trust-signal`/`--retry-count`/`--conflicting-sources`/`--changed-lines`
exist so an orchestrator can pass through its real run signals; tier-3 risk with non-high
trust, or tier-2 with low trust, also gates.

**Evidence verification** re-derives the risk tier from the PR's live changed files and
resolves the same review contract as `keel ship` (honoring `--reviewers`, the jury flags,
and `--review-comments`), then requires the closure markers, the reviewer verdict count,
and (gating jury) the jury verdict bound to the current head SHA.

**Outcome:** on success the payload reports `merged: true` and exit 0. With `--dry-run`,
every stage runs and the command stops with `"dry-run: merge not performed"` and exit 0.

### Examples

```bash
# Standard windowed merge with explicit consent
keel merge .keel/project.yaml --root . --pr 123 \
  --approve-scope filesystem,git,github --operator "$USER"
# Rehearse the full claim/window/rollup/evidence path without merging
keel merge .keel/project.yaml --root . --pr 123 --dry-run \
  --approve-scope filesystem,git,github --operator "$USER" --json
# Audited window bypass for a blocker (still consent- and evidence-gated)
keel merge .keel/project.yaml --root . --pr 123 --hotfix \
  --approve-scope filesystem,git,github --operator "$USER"
# Tier-3 evidence expectations: 3 reviewer verdicts + gating jury verdict
keel merge .keel/project.yaml --root . --pr 123 --reviewers 3 --jury \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

## `keel post-comment`

Post or update a deterministic GitHub issue/PR artifact comment with marker validation
and same-run idempotency. The sanctioned write path for ship evidence artifacts.

```
keel post-comment <project.yaml> --target issue:N|pr:N --artifact ARTIFACT
                  --body-file FILE [--root DIR] [--run-id ID] [--dry-run] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config (owner/repo must be set). |
| `--root DIR` | path | `.` | Root for GitHub operations. |
| `--target` | `issue:<N>` or `pr:<N>` | **required** | Comment target. Anything else exits 1 (`--target must use issue:<number> or pr:<number>`). |
| `--artifact` | `closure-comment` \| `issue-update` \| `review-verdict` \| `jury-verdict` \| `extension-result` \| `step-handoff` \| `run-control-halt` | **required** | The artifact contract; selects the marker the body must contain. |
| `--body-file FILE` | path | **required** | Rendered Markdown to post. |
| `--run-id ID` | string | `None` | Same-marker, same-run-id comments are *edited* instead of duplicated. |
| `--dry-run` | flag | off | Report the planned action (`post` or `edit`) without mutating GitHub. |
| `--json` | flag | off | Structured `keel.post-comment.v1` payload. |

### Details

Each artifact maps to a stable marker (e.g. `closure-comment` →
`keel.closure-comment.v1`, `review-verdict` → `keel.review-verdict.v1`,
`jury-verdict` → `keel.jury-verdict.v1`). The body is rejected before any public write
when (a) it does not contain the required marker, or (b) it looks like a literal
`@/path` placeholder (a shell-expansion mistake). The run id is matched in the existing
comment body via a `run-id: <id>` line or a `<!-- keel.run-id: <id> -->` HTML comment;
the *latest* matching comment is edited. Without `--run-id`, a new comment is always
posted. To post multiple comments of one artifact type in one run, use sub-keyed run ids
(`"$RUN_ID:reviewer-a"`). Requires an authenticated `gh` transport with comment support;
a missing capability, unreadable body file, or failed mutation exits 1. PR conversation
comments use the issue-comments endpoint, so `--target pr:N` lands in the PR timeline.

### Examples

```bash
# Post one reviewer verdict, idempotent per reviewer
keel post-comment .keel/project.yaml --root . --target pr:456 \
  --artifact review-verdict --body-file /tmp/review-verdict.md --run-id "$RUN_ID:reviewer-a"
# Closure comment on the linked issue
keel post-comment .keel/project.yaml --root . --target issue:123 \
  --artifact closure-comment --body-file /tmp/closure.md --run-id "$RUN_ID" --json
# Preview whether this would post or edit
keel post-comment .keel/project.yaml --root . --target pr:456 \
  --artifact jury-verdict --body-file /tmp/jury.md --run-id "$RUN_ID" --dry-run --json
```

## `keel worktree-remove`

Safely remove a registered, repo-nested git worktree.

```
keel worktree-remove WORKTREE [--root DIR] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `worktree` | path (relative to root or absolute) | required | Worktree to remove. |
| `--root DIR` | path | `.` | Repository root used for validation. |
| `--json` | flag | off | Structured removal result. |

### Details

Validation is fail-closed: the resolved path must be strictly nested under the resolved
repository root (the root itself, filesystem roots, and outside paths are refused) and
must appear in `git worktree list`. Only then is `git worktree remove --force`
delegated. Validation failure or a failed removal exits 1. This is the sanctioned
pre-merge cleanup path for implementer-supplied worktree paths.

### Examples

```bash
keel worktree-remove worktrees/issue-123 --root .
```

## `keel ledger`

Read the structured run ledger offline.

```
keel ledger <project.yaml> [--root DIR] [--limit N] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config (resolves the ledger path). |
| `--root DIR` | path | `.` | Root for resolving the ledger path. |
| `--limit N` | positive int | `None` (all) | Return only the newest N records. |
| `--json` | flag | off | Structured payload with records and capture health. |

### Details

The default ledger path is `.keel/state/run-ledger.jsonl`; projects override it with
`policy_pack.reports.run_ledger`. A missing ledger is not an error (`status: "missing"`,
`records: []`, exit 0) — adapters treat it as empty history. Invalid JSONL or an
unsupported record schema is an error (exit 1) because morning/wrap/capture reports must
never be built from corrupted history. The payload includes a `capture_health` summary
with `needs_reconcile` counts.

### Examples

```bash
keel ledger .keel/project.yaml --root . --json
keel ledger .keel/project.yaml --root . --limit 10
```

## `keel capture-verify`

Verify that merged PRs each have exactly one valid capture marker in the run ledger.

```
keel capture-verify <project.yaml> [--merged-pr N…] [--from-transport] [--merged-since DATE]
                     [--merged-prs-json FILE] [--verdict-count PR=N…] [--root DIR] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for resolving the ledger path. |
| `--merged-pr N` | positive int, repeatable | — | Merged PR(s) expected to carry a marker; explicit override added to the derived set. |
| `--from-transport` | flag | off | Derive the merged set from the host and run reconcile cross-checks. |
| `--merged-since DATE` | `YYYY-MM-DD` | none | With `--from-transport`, only PRs merged on/after this date. |
| `--merged-prs-json FILE` | file path | none | Offline transport fixture: JSON array of `{"number": N}`. |
| `--verdict-count PR=N` | `PR=N`, repeatable | none | Offline evidence-side review-verdict count per PR. |
| `--json` | flag | off | Structured verification report. |

One of `--merged-pr` or `--from-transport` (or `--merged-prs-json`) is required.

### Details

For each PR, the ledger must contain exactly one valid
`compound-learning: pr=<N> status=<applied|deferred|skipped:reason>` marker record.
Missing, invalid, or duplicate markers fail that PR; any failing PR makes the overall
status non-`complete` and the exit code 1. This is the session-end verifier adapters run
after s11.

When the merged set is derived from the transport (or any reconcile input is supplied),
three additive cross-checks also run and a finding makes the exit code 1: `missing-marker`,
`applied-without-artifact` (an `applied` capture with no `--capture-artifact` reference in the
ledger), and `reviewer-count-mismatch` (ledger reviewer count > evidence-side verdict count).
The transport query and per-PR verdict fetch are fail-soft. Passing only `--merged-pr` keeps
the legacy offline behavior (marker checks only).

### Examples

```bash
keel capture-verify .keel/project.yaml --root . --merged-pr 456 --json
keel capture-verify .keel/project.yaml --root . --merged-pr 456 --merged-pr 457
keel capture-verify .keel/project.yaml --root . --from-transport --merged-since 2026-06-01
```

## `keel capture-reconcile`

Plan idempotent post-merge recovery actions for merged PRs with incomplete capture,
closure, or issue closeout. Never mutates anything itself.

```
keel capture-reconcile <project.yaml> --merged-pr N [--linked-issue PR=ISSUE]
                       [--capture-capability available|unavailable] [--live]
                       [--root DIR] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for resolving the ledger path. |
| `--merged-pr N` | positive int, repeatable | **required** | Merged PR(s) to reconcile. |
| `--linked-issue PR=ISSUE` | `N=M` mapping, repeatable | none | Unambiguous PR→issue mapping; repeat for multiple issues per PR. Malformed values are argparse errors. |
| `--capture-capability` | `available` \| `unavailable` | `unavailable` | Whether the project capture extension capability is currently runnable; gates whether `run-capture-extension` actions are planned vs `record-skip`. |
| `--live` | flag | off | Labels the output `live-plan` instead of `dry-run`. The command still performs no mutations (`no_mutations: true` always). |
| `--json` | flag | off | Structured reconcile plan. |

### Details

Planned action types: `emit-capture-marker`, `run-capture-extension`,
`post-closure-summary`, `close-linked-issue`, `record-skip` — each with an idempotency
key, for the adapter to apply after its own transport and consent checks. Ambiguous
linked issues or invalid/duplicate existing markers make the plan `blocked` (exit 1)
instead of guessing. `policy_pack.capture.mode: marker-only` plans an `applied` core
marker without requiring a capture extension.

### Examples

```bash
# Plan recovery for one merged PR
keel capture-reconcile .keel/project.yaml --root . --merged-pr 456 --json
# Disambiguate the linked issue and mark the capture capability available
keel capture-reconcile .keel/project.yaml --root . \
  --merged-pr 456 --linked-issue 456=123 --capture-capability available --json
```

## `keel evidence-verify`

Verify that a PR carries the public, durable GitHub evidence the ship contract requires
before merge. This is the same verifier `keel merge` runs internally and the CI
`keel evidence (required)` check runs in GitHub Actions.

```
keel evidence-verify <project.yaml> --pr N [--issue N] [--root DIR]
                     [--review-comments inline|summary] [--reviewers 1|2|3]
                     [--jury] [--no-jury] [--jury-advisory]
                     [--dry-run] [--deferral ID|KIND|all]
                     [--pr-comments-json FILE] [--issue-comments-json FILE]
                     [--pr-reviews-json FILE] [--pr-body-file FILE]
                     [--changed-file PATH] [--head-sha SHA] [--head-ref REF]
                     [--pr-label NAME] [--gate-label NAME] [--waiver-label NAME] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for live `gh` fetches. |
| `--pr N` | positive int | **required** | PR to verify. |
| `--issue N` | positive int | `None` | Linked issue; otherwise inferred from `Closes #N` in the PR body. |
| `--review-comments` | `inline` \| `summary` | `inline` | Posting mode in the resolved review contract (shape only; does not change required counts). |
| `--reviewers` | `1` \| `2` \| `3` | tier-derived | Required count of distinct posted reviewer verdicts. |
| `--jury` / `--no-jury` / `--jury-advisory` | flags | off | Whether a gating jury verdict is required (gating + enabled ⇒ required; advisory ⇒ not required). |
| `--dry-run` | flag | off | Contract shape only: no live fetch, zero required items. |
| `--deferral` | evidence id (e.g. `review-verdict-2`), kind (`closure` \| `review` \| `jury`), or `all`; repeatable | none | Explicit, recorded operator deferral of specific evidence items. Only meaningful when the gate is enforced. |
| `--pr-comments-json` / `--issue-comments-json` / `--pr-reviews-json` / `--pr-body-file` | file paths | `None` | Offline fixtures; supplying any of them disables the live `gh` fetch. |
| `--changed-file PATH` | repeatable | none | Offline changed-file paths used to derive the risk tier from fixtures. |
| `--head-sha SHA` | string | `None` | Offline head SHA for verdict head-binding. |
| `--head-ref REF` | string | `None` | Offline head branch used to detect ship provenance (`feature/issue-*` etc.). |
| `--pr-label NAME` | repeatable | none | Inject PR label names, merged with live labels. A live fetch still runs unless an offline fixture flag is also supplied. |
| `--gate-label NAME` | string | `knobs.evidence_gate_label` | Override the legacy evidence arming label for this run. |
| `--waiver-label NAME` | string | `keel:evidence-waived` | Override the operator-applied waiver label. |
| `--json` | flag | off | Structured payload (`gate`, `enforced`, `verification`, …). |

### Details

**Gate arming.** The gate is provenance-armed: it engages when deterministic ship
provenance exists — a ship-style issue branch on the head ref, an existing
`keel.review-verdict.v1` marker, a trusted `keel ship` assessment comment, a ship-run
ledger record reference, or the legacy arming label (`evidence_gate_label`, default
`keel:ship`). The assessment comment is provenance only; it is still never accepted as
closure, review, or jury evidence. A hand-authored PR without provenance reports
`enforced: false`, `required: 0`, status `pass`, exit 0. The only disarm path for armed
ship provenance is the operator-applied waiver label
(`--waiver-label`, default `keel:evidence-waived`), which is reported in the output.

**Required items** (when enforced, derived from the resolved review contract): a
`closure-comment-pr` and `closure-comment-issue` marker (`keel.closure-comment.v1`,
posted by a trusted actor — `author_association` of `OWNER`/`MEMBER`/`COLLABORATOR`);
`review-verdict-1..N` distinct posted reviewer verdicts (`keel.review-verdict.v1` +
`reviewer: <id>` + current `head: <sha>`); and `jury-verdict` when the jury is enabled
in gating mode. PR bodies, chat summaries, untrusted comments, and the automated
`keel ship` assessment comment are never accepted. A closure comment whose entire
`Run context` block is degraded triggers a `run-context-empty` major finding, which
fails enforced verification.

**Tier derivation.** In live mode, keel reads the PR changed files and head SHA through
`gh`, then computes the tier (and thus the default reviewer count and tier-3 jury
auto-on) from `tier3_globs`/`docs_gate_paths`. With fixtures, the tier comes from
`--changed-file` paths (no files ⇒ tier unresolved ⇒ default 2 reviewers).

**`--deferral`** marks an item `ok` despite being absent and records it in the contract
(`deferrals`). It is the machine half of an explicit operator deferral (the public half
is a `keel.deferral.v1` comment); it has no effect when the gate is unenforced or under
`--dry-run` (which already requires nothing).

**Failure behavior:** a failed live fetch is fail-closed (exit 1, never "unenforced");
status `fail` (missing items or blocking run-context findings) exits 1; an unenforced
gate exits 0 with `enforced: false` clearly reported.

### Examples

```bash
# Live verification with policy-derived tier and reviewer count
keel evidence-verify .keel/project.yaml --root . --pr 456
# Tier-3 expectations made explicit: 3 verdicts + gating jury verdict
keel evidence-verify .keel/project.yaml --root . --pr 456 --reviewers 3 --jury --json
# Explicit recorded operator deferral of one reviewer verdict
keel evidence-verify .keel/project.yaml --root . --pr 456 --deferral review-verdict-2
# Contract shape only — preview what a labelled PR would enforce
keel evidence-verify .keel/project.yaml --root . --pr 456 --dry-run --pr-label keel:ship --json
# Offline fixture run (CI/tests) — same verifier path, no gh
keel evidence-verify .keel/project.yaml --root . --pr 456 \
  --pr-comments-json comments.json --pr-reviews-json reviews.json \
  --pr-body-file body.md --changed-file src/keel/cli.py --head-sha "$SHA" --json
```

## `keel step-verify`

Verify a persisted step handoff against an evidence report before an adapter advances
the backbone.

```
keel step-verify --step sN --handoff-file FILE --evidence-report FILE
                 [--review-comments inline|summary] [--reviewers 1|2|3]
                 [--jury] [--no-jury] [--jury-advisory]
                 [--dry-run] [--not-enforced] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `--step sN` | backbone step id (`s0`–`s12`) | **required** | Which step's completion to verify. An unknown step id exits 1. |
| `--handoff-file FILE` | JSON object file | **required** | The handoff produced by `keel.stepverifier.build_handoff`. |
| `--evidence-report FILE` | JSON object file | **required** | The verification block from `keel evidence-verify` (or an equivalent report with `results`). |
| `--review-comments` | `inline` \| `summary` | `inline` | Posting mode in the resolved review contract. |
| `--reviewers` | `1` \| `2` \| `3` | `2` (tier is `None` here, so the unresolved default applies) | Required reviewer verdict count. |
| `--jury` / `--no-jury` / `--jury-advisory` | flags | off | Jury requirement. Note: with no tier, the jury is off unless `--jury` is passed. |
| `--dry-run` | flag | off | Verify with dry-run evidence requirements (zero required items). |
| `--not-enforced` | flag | off | Verify with evidence requirements disabled. |
| `--json` | flag | off | Structured contract + verification report. |

### Details

No project config is read — the review contract is resolved purely from the flags
(`tier=None`), which is why `--reviewers`/`--jury` should mirror the values the ship run
actually used. The command exits 1 when the handoff schema/status/renderer marker is
invalid or when the step's required evidence ids are not ok in the report. A failed step
verification is a backbone BLOCKER for adapters: do not advance, merge, or mark the step
complete from chat prose alone.

### Examples

```bash
keel step-verify --step s7 \
  --handoff-file .keel/run/handoffs/s7.json \
  --evidence-report .keel/run/evidence.json \
  --reviewers 2 --json
```

## `keel runcontrols`

Append one run-control event to a JSON array file and evaluate deterministic work caps:
run budget, per-step/slot caps, repeated identical actions, alternating diff
fingerprints.

```
keel runcontrols EVENTS.json [--event-json FILE] [--step ID] [--slot NAME]
                 [--action ACTION] [--output-fingerprint FP] [--diff-fingerprint FP]
                 [--work-units N] [--soft-failure]
                 [--max-work-units N] [--default-step-cap N] [--step-cap SLOT=N]
                 [--identical-action-threshold N] [--alternating-diff-window N]
                 [--dry-run] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `events_file` | path to a JSON array file | required | The run-events store. A missing file is treated as empty. |
| `--event-json FILE` | path to a JSON object | `None` | Append this whole object as the event (overrides the field flags below). |
| `--step ID` | string | `None` | Event step id field. |
| `--slot NAME` | string | `None` | Event slot name field. |
| `--action ACTION` | string | `None` | Event action field. |
| `--output-fingerprint FP` | string | `None` | Output fingerprint, for identical-action detection. |
| `--diff-fingerprint FP` | string | `None` | Diff fingerprint, for alternation detection. |
| `--work-units N` | int | `None` | Work units this event consumes. |
| `--soft-failure` | flag | off | Mark the event as a soft failure. |
| `--max-work-units N` | int | `250` | Run-budget hard cap. |
| `--default-step-cap N` | int | `1` | Default per-step/slot iteration cap. |
| `--step-cap SLOT=N` | `SLOT=N`, repeatable | none | Per-slot cap override; `N` must be a positive integer (else exit 1). |
| `--identical-action-threshold N` | int | `3` | Oscillation threshold for repeated identical actions. |
| `--alternating-diff-window N` | int | `4` | Oscillation window for alternating diff fingerprints. |
| `--dry-run` | flag | off | Evaluate without appending the event. |
| `--json` | flag | off | Structured report. |

### Details

When any event fields (or `--event-json`) are supplied, the event is appended to the
file (unless `--dry-run`), then the whole event list is evaluated. A hard halt makes the
status non-`pass` and the exit code 1, with a structured halt reason (`reason`,
`scope`). The same evaluation is embedded in `keel ship --run-events-file` (see ship).
Hard halts are fail-closed by design: a ship run must stop until an operator chooses an
explicit override.

### Examples

```bash
# Append one fixloop event and evaluate
keel runcontrols .keel/run/events.json --slot fixloop --action fix
# Cap the fixloop slot at 3 iterations
keel runcontrols .keel/run/events.json --step-cap fixloop=3 --json
# Evaluate only, never write
keel runcontrols .keel/run/events.json --dry-run --json
```

## `keel checkpoint`

Read or write the resumable checkpoint (the active resume point — not run history).

```
keel checkpoint <project.yaml> [--root DIR] [--write …write fields…] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config (resolves the checkpoint path). |
| `--root DIR` | path | `.` | Root for resolving the checkpoint path. |
| `--write` | flag | off | Write a checkpoint record instead of reading. |
| `--run-id ID` | string | `run` | Run id for `--write`. |
| `--checkpoint-command` | `ship` \| `work-block` \| `overnight` | `ship` | Workflow command being checkpointed. |
| `--step sN` | backbone step id (`s0`–`s12`) | `s0` | Current step for `--write`. |
| `--target TEXT` | string | `None` | Target text stored verbatim. |
| `--issue-queue N` | positive int, repeatable | none | Queued issue numbers. |
| `--active-issue N` | positive int | `None` | The active issue. |
| `--branch NAME` | string | `None` | Recorded branch. |
| `--worktree PATH` | string | `None` | Recorded worktree path. |
| `--pull-request N` | positive int | `None` | Recorded PR number. |
| `--head-sha SHA` | string | `None` | Recorded head SHA. |
| `--completed-step sN` | step id, repeatable | none | Completed backbone steps. |
| `--last-gate ID` | string | `None` | Last completed gate id. |
| `--last-review MARK` | string | `None` | Last completed review marker. |
| `--last-check MARK` | string | `None` | Last completed CI/check marker. |
| `--merge-state` | `not-started` \| `pending` \| `merged` \| `failed` \| `skipped` | `not-started` | Merge progress. |
| `--capture-state` | `not-started` \| `applied` \| `deferred` \| `skipped` \| `failed` | `not-started` | Capture progress. |
| `--close-state` | `not-started` \| `closed` \| `failed` | `not-started` | Issue-close progress. |
| `--stop-reason TEXT` | string | `None` | Why the run stopped here. |
| `--json` | flag | off | Structured contract + checkpoint record. |

### Details

The default path is `.keel/state/checkpoint.json` (override:
`policy_pack.reports.checkpoint`). Reading a missing checkpoint is not an error
(`status: "missing"`, `checkpoint: null`, exit 0); an invalid/corrupt checkpoint exits 1
on read or write. `--write` replaces the previous checkpoint — it is for the active
resume point only; use `keel ledger` for append-only history. Adapters write a
checkpoint at every safe step boundary during live ship runs.

### Examples

```bash
# Read the current checkpoint
keel checkpoint .keel/project.yaml --root . --json
# Write the s6 safe boundary for an active ship run
keel checkpoint .keel/project.yaml --root . --write \
  --run-id "$RUN_ID" --checkpoint-command ship --step s6 \
  --target "issue #123" --issue-queue 123 --active-issue 123 \
  --branch feat/issue-123-example --worktree worktrees/issue-123 \
  --pull-request 456 --head-sha "$HEAD_SHA" --last-check ci
```

## `keel resume`

Render a dry-run resume plan from the checkpoint. Never mutates anything.

```
keel resume <project.yaml> [--root DIR] [--live-pr-state STATE]
            [--live-worktree-state STATE] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for resolving the checkpoint path. |
| `--live-pr-state` | `unknown` \| `missing` \| `open` \| `merged` \| `closed` | `unknown` | Adapter-supplied live PR state for reconciliation. |
| `--live-worktree-state` | `unknown` \| `present` \| `missing` | `unknown` | Adapter-supplied live worktree state. |
| `--json` | flag | off | Structured resume plan. |

### Details

`status: no-checkpoint` means nothing to resume (exit 0). `status: ambiguous` exits 1
and includes warnings plus the reconciliation action — for example when the checkpoint
references a PR or worktree that the supplied live state reports missing. When the live
PR state is `merged`, the plan resumes at capture or closeout and never repeats the
merge. Adapters call this at the start of every run before any mutation.

### Examples

```bash
keel resume .keel/project.yaml --root . --json
keel resume .keel/project.yaml --root . --live-pr-state merged --json
keel resume .keel/project.yaml --root . --live-worktree-state missing --json
```

## `keel status`

Operator-facing progress snapshot from the checkpoint + ledger. Read-only.

```
keel status <project.yaml> [--root DIR] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for resolving checkpoint and ledger paths. |
| `--json` | flag | off | Structured `keel.progress-status.v1` snapshot. |

### Details

The snapshot is taken at the last safe step boundary, not in real time: overall run
status (`no-active-run` / `active` / `waiting` / `interrupted` / `completed`), the
current issue/step/PR/branch/worktree, the next queued issue, ledger counts, and
capture-health gaps. Missing checkpoint or ledger degrades gracefully (exit 0); an
*invalid* checkpoint or corrupted ledger exits 1.

### Examples

```bash
keel status .keel/project.yaml --root .
keel status .keel/project.yaml --root . --json
```

## `keel morning`

Render the standalone daily-brief preflight contract.

```
keel morning <project.yaml> [--root DIR] [--since WHEN] [--target TEXT]
             [--dry-run] [--live] [--approve-scope SCOPE] [--operator ID]
             [--consent-mode MODE] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for capability checks. |
| `--since WHEN` | free-form label or timestamp | `None` | Brief window start. Stored as an opaque label in the contract target (`since <WHEN>`); core does not parse it as a date — the adapter interprets it. |
| `--target TEXT` | string | `None` | Extra target text, appended in parentheses. |
| `--dry-run` / `--live` | shared | dry | Live preflight gates on consent (mutually exclusive). |
| `--approve-scope` / `--operator` / `--consent-mode` | shared | — | Consent flags. |
| `--json` | flag | off | Structured contract + result. |

### Details

The capability requirement is derived from `policy_pack.health_providers`
(provider-declared required/optional capabilities) plus optional `gh`/`gh-auth`.
Dry-run mode never runs project health commands and never writes reports; unavailable
health providers are reported as `blocked`/`unavailable`, never as a silently empty
section. Live mode is only a preflight: adapters perform approved report writes after
checking consent.

### Examples

```bash
keel morning .keel/project.yaml
keel morning .keel/project.yaml --since yesterday --json
keel morning .keel/project.yaml --live --approve-scope filesystem --operator "$USER" --json
```

## `keel wrap`

Render the standalone session-wrap preflight contract.

```
keel wrap <project.yaml> [TITLE] [--root DIR] [--since WHEN] [--target TEXT]
          [--dry-run] [--live] [--approve-scope SCOPE] [--operator ID]
          [--consent-mode MODE] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `TITLE` | optional positional string | `None` | PR title override; when present it becomes the contract target. |
| `--root DIR` | path | `.` | Root for git and capability checks. |
| `--since WHEN` | free-form label or timestamp | `None` | Session start label — opaque to core, same semantics as `morning --since`. |
| `--target TEXT` | string | `None` | Extra target text. |
| `--dry-run` / `--live` | shared | dry | Mutually exclusive. |
| `--approve-scope` / `--operator` / `--consent-mode` | shared | — | Consent flags. |
| `--json` | flag | off | Structured contract + result. |

### Details

The contract carries the session-closeout shape: linked-worktree and base-branch guards
(`must_run_from_linked_worktree`), the configured gate source, Conventional Commit and
`Closes #N` conventions, ready-PR requirements, recap destination, and the shared
deferral queue. Dry-run never runs gates, commits, pushes, opens PRs, or writes reports.

### Examples

```bash
keel wrap .keel/project.yaml --json
keel wrap .keel/project.yaml "feat: finish queue contract" --live \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

## `keel work-block`

Render the standalone daytime multi-issue work-block contract.

```
keel work-block <project.yaml> [issues…] [--root DIR] [--queue SELECTOR] [--max N]
                [--hours H] [--review-comments inline|summary] [--reviewers 1|2|3]
                [--target TEXT] [--dry-run] [--live] [--approve-scope SCOPE]
                [--operator ID] [--consent-mode MODE] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `issues` | zero or more positive ints | none | Explicit issue numbers, processed in the order given. Zero/negative are argparse errors. |
| `--queue SELECTOR` | free-form selector string (e.g. `ready`) | `None` | Project queue selector used **only when no explicit issues are given**; the queue is ordered by priority then ascending issue number and snapshotted once per session. |
| `--max N` | positive int | `None` | Cap on issues attempted (recorded as `max N` in the target). |
| `--hours H` | float | `None` | Optional time budget. |
| `--review-comments` / `--reviewers` | shared | `inline` / unset | Passed through to each per-issue ship handoff contract. |
| `--target TEXT` | string | `None` | Extra target text. |
| `--root` / `--dry-run` / `--live` / consent flags / `--json` | shared | — | Shared semantics. |

### Details

Each queue item is handed to `ship` in an isolated branch/worktree, inheriting the
operator consent scope and the same capture, run-ledger, merge-lock, and merge-window
contracts as a standalone ship. The daytime mode is operator-visible: the operator can
redirect between items, and a blocked item stops the block (unlike overnight, which
continues). Final reporting buckets each issue as shipped, PR-open-not-merged, deferred,
blocked, skipped, or needs-input. Stop conditions: queue exhaustion, the max/time
budget, an operator pause, a consent gap, a non-ready or blocking finding, and
merge-window close. Dry-run never spawns ship runs, creates PRs, merges, or writes
reports.

### Examples

```bash
# Explicit ordered issue list
keel work-block .keel/project.yaml 76 82 91 --json
# Queue selector with caps
keel work-block .keel/project.yaml --queue ready --max 3 --hours 6 --json
# Live preflight for one issue
keel work-block .keel/project.yaml 76 --live \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

## `keel overnight`

Render the standalone overnight unattended-session contract.

```
keel overnight <project.yaml> [hours] [--root DIR] [--max N]
               [--review-comments inline|summary] [--reviewers 1|2|3] [--target TEXT]
               [--dry-run] [--live] [--approve-scope SCOPE] [--operator ID]
               [--consent-mode MODE] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `hours` | optional positional float | `None` | Time budget in hours (target shows `<H>h session`). |
| `--max N` | positive int | `None` | Maximum issues attempted in the session. |
| `--review-comments` / `--reviewers` | shared | `inline` / unset | Pass-through to per-issue ship handoffs. |
| `--root` / `--target` / `--dry-run` / `--live` / consent flags / `--json` | shared | — | Shared semantics. |

### Details

The contract owns the unattended-session shape: merge-window mode from `keel window`
(`pause` halts outside the window, `freeze` defers merges to the morning queue),
per-issue worktree isolation, no-night-merge policy, blocker policy boundary, priority
queue shape, report destinations, stop conditions, and the shared deferral queue.
Standing consent (`--consent-mode standing` with `KEEL_APPROVE_SCOPE`/`KEEL_OPERATOR` or
`automation.approved_scopes`/`automation.operator`) is the intended pattern for trusted
unattended runs. Dry-run never spawns ship runs, creates PRs, merges, or writes reports.

### Examples

```bash
keel overnight .keel/project.yaml 8 --max 3 --json
keel overnight .keel/project.yaml --live --consent-mode standing --json
```

## `keel regression`

Render the standalone scan-and-file regression contract.

```
keel regression <project.yaml> [--root DIR] [--scope full|changed|since] [--since REF]
                [--target TEXT] [--dry-run] [--live] [--approve-scope SCOPE]
                [--operator ID] [--consent-mode MODE] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `--scope` | `full` \| `changed` \| `since` | `full` | Scan scope recorded in the preflight target. |
| `--since REF` | git ref or timestamp string | `None` | Start ref/time when `--scope since` is used; recorded in the target. Core does not validate the ref. |
| `--root` / `--target` / `--dry-run` / `--live` / consent flags / `--json` | shared | — | Shared semantics. |

### Details

The capability requirement is scan-specific: required `git` + `worktree`, optional
`gh`/`gh-auth`/`github-mcp`/`parallel-subagents`. The contract carries the generic scan
shape — canonical base scan target, clean-tree preflight, read-only worktree
requirement, area fan-out, confidence filtering, issue dedupe (the rendered output shows
the `near_text_similarity` threshold), issue-create lock, ship handoff routing, and
report sections. Project specifics live in `policy_pack.scan`. The only live state
change is issue creation, after consent; dry-run opens no issues and edits no code.

### Examples

```bash
keel regression .keel/project.yaml --scope full --json
keel regression .keel/project.yaml --scope since --since origin/main --json
keel regression .keel/project.yaml --live \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

## `keel review-all-day`

Render the standalone time-window diff-review scan contract.

```
keel review-all-day <project.yaml> [days] [--root DIR] [--target TEXT]
                    [--dry-run] [--live] [--approve-scope SCOPE] [--operator ID]
                    [--consent-mode MODE] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `days` | optional positional positive int | `1` | Number of merge-window days to scan (target shows `<N> day scan`). |
| `--root` / `--target` / `--dry-run` / `--live` / consent flags / `--json` | shared | — | Shared semantics. |

### Details

Capability requirement: required `git`, optional
`gh`/`gh-auth`/`github-mcp`/`parallel-subagents`. The contract carries the time-window
scan shape: merge-window timezone inputs, trunk + active work branch scope, remote-ref
default, batch/fan-out threshold, file-boundary diff truncation, serious-finding filter,
the exact issue title prefix (rendered in the output), and issue creation. Read-only
with respect to git/PRs — the only state change is issue creation after consent.

### Examples

```bash
keel review-all-day .keel/project.yaml --json
keel review-all-day .keel/project.yaml 7 --json
keel review-all-day .keel/project.yaml 1 --live --approve-scope github --operator "$USER" --json
```

## `keel run-gates`

Run the project's deterministic command gates (the runnable slice of s8).

```
keel run-gates <project.yaml> [--root DIR]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for commands and extensions. |

### Details

Each configured command gate runs its shell command; non-zero exit becomes a blocking
`gate:<name>` finding, output tail captured. The built-in `jury` gate (when `gates:`
includes `jury`) runs the ai-jury CLI on `git diff base...HEAD` in **gating** mode here;
a missing `jury` CLI is a fail-soft no-op, and diffs over 1 MB skip the jury with a
visible non-blocking `jury:skipped-oversize` nit. Missing *required* runtime
capabilities exit 1 before any gate runs; missing optional capabilities print a degraded
notice and continue. Exits 1 when the summarized verdict blocks (`BLOCKED — merge is
gated by the findings above`), so it wires directly into CI.

### Examples

```bash
keel run-gates .keel/project.yaml --root .
```

## `keel capabilities`

Print the runtime capability report; optionally evaluate a command's requirements.

```
keel capabilities [--root DIR] [--project project.yaml] [--for COMMAND] [--pr N] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `--root DIR` | path | `.` | Where detection runs. |
| `--project FILE` | path | `None` | Evaluate a command's required/optional capabilities against the report. |
| `--for COMMAND` | command name | `ship` | Which command's requirement to evaluate (only with `--project`). |
| `--pr N` | int | `None` | Include ship PR-lookup capability requirements. |
| `--json` | flag | off | Structured report + transport + evaluation. |

### Details

Output includes the detected capability report, the selected GitHub transport (`gh` vs
MCP fallback) with any degraded operations, and (with `--project`) the evaluation:
missing required capabilities exit 1; missing optional ones are reported as degraded
(`missing_optional` in JSON) with exit 0.

### Examples

```bash
keel capabilities --root .
keel capabilities --project .keel/project.yaml --for ship --root . --json
keel capabilities --project .keel/project.yaml --for morning --root .
```

## `keel project-commands`

List project-provided commands declared by `policy_pack.project_commands` (or the older
`policy_pack.command_routing` compatibility map).

```
keel project-commands <project.yaml> [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--json` | flag | off | Structured command metadata. |

### Details

Read-only metadata listing (name, runner command, required/optional capabilities). Use
`keel plan --command <project-command> --json` to get the full contract for one of these
commands.

### Examples

```bash
keel project-commands .keel/project.yaml
keel project-commands .keel/project.yaml --json
```

## `keel window`

Report whether the merge window is open right now in the project timezone.

```
keel window <project.yaml>
```

No flags besides the positional config path.

### Details

The configured `merge_window` (`HH:MM-HH:MM`) is the *open* window; it may wrap midnight
(`07:00-01:30` is open from 07:00 through 01:30 the next day). Prints `OPEN` or
`CLOSED (night no-merge)` with the timezone and window; if the project sets no
`timezone` + `merge_window`, prints a notice and exits 0. Always exits 0 on a valid
config — the window *state* is in the text, not the exit code.

### Examples

```bash
keel window .keel/project.yaml
# merge window OPEN  [Europe/Istanbul 07:00-01:30]
```

## `keel ship`

The deterministic slice of a ship against the current checkout: tier → reviewer count,
window state, gate results, optional CI, and the final merge decision
(`MERGE`/`DEFER`/`BLOCK`). It never calls coding agents and never performs the merge.

```
keel ship <project.yaml> [--root DIR] [--pr N] [--hotfix] [--dry-run] [--live]
          [--approve-scope SCOPE] [--operator ID] [--consent-mode MODE] [--target TEXT]
          [--append-ledger] [--run-id ID] [--run-events-file FILE] [--max-rounds N]
          [--issue N] [--pull-request N] [--branch NAME] [--head-sha SHA]
          [--capture-status STATUS] [--capture-reason TEXT] [--capture-artifact REF]
          [--implementer LABEL] [--reviewer-agent LABEL] [--tester LABEL]
          [--host-agent NAME] [--transport gh|mcp] [--strict-run-context]
          [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL]
          [--review-comments inline|summary] [--reviewers 1|2|3]
          [--jury] [--no-jury] [--jury-advisory]
          [--profile standard|compound] [--compound] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `path` | file path | required | Project config. |
| `--root DIR` | path | `.` | Root for git, gates, and extensions. |
| `--pr N` | int | `None` | Read this PR's CI check-rollup (requires a transport with `check_runs`; exit 1 otherwise). Also becomes the default target (`PR #N`) and the ledger PR. |
| `--hotfix` | flag | off | Mark an emergency change: the merge decision may bypass a closed window (audit line printed). Never bypasses failing gates, blocking findings, or failing CI. |
| `--dry-run` / `--live` | shared | dry | Mutually exclusive; `--live` arms consent, intake blocking, and ledger appends. |
| `--approve-scope` / `--operator` / `--consent-mode` / `--target` | shared | — | Consent flags. |
| `--append-ledger` | flag | off | With `--live`, append one structured `ship_run` record to the run ledger. Without `--live`, the would-be record is shown but not written. |
| `--run-id ID` | string | `None` | Operator/session run id stored in the ledger record. |
| `--run-events-file FILE` | JSON array path | `None` | Evaluate run-control events and stamp the summary into the ledger record. A missing file is empty; a hard halt makes the whole command exit 1. |
| `--max-rounds N` | positive int | `250` (run-control default budget) | Explicit run-control work-unit budget override for the events evaluation. |
| `--issue N` | positive int | `None` | Issue number for the ledger record. |
| `--pull-request N` | positive int | `None` | PR number for the ledger record **without** a CI lookup (distinct from `--pr`). Ledger precedence: `--pull-request` wins over `--pr`. |
| `--branch NAME` | string | `None` | Branch stored in the ledger record. |
| `--head-sha SHA` | string | `None` | Head SHA stored in the ledger record. |
| `--capture-status` | `applied` \| `deferred` \| `skipped:<reason>` | `None` | Capture outcome for the ledger record. **Required** when `--live --append-ledger` (exit 1 otherwise). Allowed skip reasons: `dry-run`, `deferred`, `merge-failed`, `recursion-guard`, `capability-unavailable`, `no-policy`. |
| `--capture-reason TEXT` | string | `None` | Capture outcome reason. |
| `--capture-artifact REF` | string (path or hash) | `None` | Durable capture artifact reference proving an `applied` capture; `keel capture-verify` reconcile flags `applied` records with no artifact. |
| `--implementer LABEL` | string | `None` | Effective implementer codename or `vendor:model` label for attribution. Its vendor slug is what `keel evidence-verify` cross-checks against the PR's `agent:<vendor>` label when the gate is enforced (`attribution-label` finding on mismatch). |
| `--reviewer-agent LABEL` | string, repeatable | none | Effective reviewer labels (order-preserving parallel array). |
| `--tester LABEL` | string | `None` | Effective tester label. |
| `--host-agent NAME` | string | `None` | Host agent codename (`claude`/`codex`/`agy`) for the Run context block. Missing on a live append ⇒ warning. |
| `--transport` | `gh` \| `mcp` | resolved transport | Detected GitHub transport for the Run context; auto-filled from the resolved transport when omitted. |
| `--strict-run-context` | flag | off | Fail (exit 1) instead of appending a degraded ledger record when required run-context fields are missing on a live append. |
| `--issue-title/-body/-label` | shared | none | Issue intake (live non-ready intake exits 1 before gates). Title/labels are also stamped into the ledger record. |
| `--review-comments` / `--reviewers` / jury flags | shared | — | Shape `review_merge_contract`; the jury mode also drives the built-in jury gate's gating-vs-advisory behavior. |
| `--profile` | `standard` \| `compound` | `standard` | Workflow profile in the contract. |
| `--compound` | flag | off | Alias for `--profile compound`. |
| `--json` | flag | off | `{contract, result}` with `result.artifact_bodies` (canonical PR body, issue update, review/jury verdict templates, extension result). |

### Details

**Decision pipeline.** Changed files vs `base_branch` → tier
(`classify.tier_for_files`: any `tier3_globs` match ⇒ TIER-3; else all paths in
`docs_gate_paths` ⇒ TIER-1; else TIER-2, including an empty changeset) → reviewer count
(3/2/1, or `--reviewers`) → gates run (with the jury gate in the resolved jury mode) →
window check → CI interpretation (`--pr` rollup; `None` = unknown) → merge decision:
blocking findings ⇒ `BLOCK`; failing CI ⇒ `BLOCK`; closed window and not `--hotfix` ⇒
`DEFER`; otherwise `MERGE` (with `blocker bypass` as the reason when `--hotfix` merged
outside the window). `merge_window_mode: pause` additionally reports the pipeline as
HALTED outside the window.

**Exit code:** 1 when the decision is `BLOCK` or a run-control hard halt fired; 0 for
`MERGE`/`DEFER`. This makes the command usable as a pre-merge gate for runners.

**Ledger append (`--live --append-ledger`).** Builds one `ship_run` record carrying the
assessment, gates, intake, attribution (`--implementer`/`--reviewer-agent`/`--tester`),
run context (`--host-agent`/`--transport`/`--profile`/jury mode/consent summary), capture
status/reason, and run-control summary; sanitizes it through the capture redaction
policy (an invalid policy blocks the live append with exit 1; non-append runs fall back
to a partial redaction record); then appends to the resolved ledger path. The record is
the machine-readable source for `/keel:morning`, `/keel:wrap`, overnight summaries, and
capture verification — closure comments are mirrors, not the parser source.

**`--dry-run`** is accepted for adapter clarity; the CLI assessment is already
non-mutating. The meaningful split is `--live` (consent + intake + ledger arming) vs
default.

### Examples

```bash
# Plain dry assessment of the current checkout
keel ship .keel/project.yaml --root .
# Live preflight with consent and CI lookup for PR 456
keel ship .keel/project.yaml --root . --live --pr 456 \
  --approve-scope filesystem,git,github --operator "$USER" --json
# Compound profile — same gates/window/lock, s4/s7/s9/s11 step overrides in the contract
keel ship .keel/project.yaml --root . --compound --dry-run --json
# Hotfix: window bypass in the decision, never a gate/CI bypass
keel ship .keel/project.yaml --root . --hotfix --json
# Advisory jury: jury findings report but never block
keel ship .keel/project.yaml --root . --jury --jury-advisory --json
# The s11 ledger append (one structured ship_run record)
keel ship .keel/project.yaml --root . --live --append-ledger \
  --run-id "$RUN_ID" --issue 123 --pull-request 456 \
  --capture-status applied --implementer "codex:gpt-5" \
  --reviewer-agent "reviewer-a:gpt-5" --tester "tester:gpt-5-mini" \
  --host-agent claude --transport gh --profile standard \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

## `keel implement`

Standalone s4 implement-step preflight contract for one issue. Never merges.

```
keel implement <project.yaml> <issue> [--root DIR] [--delegate AGENT]
               [--dry-run] [--live] [--approve-scope SCOPE] [--operator ID]
               [--consent-mode MODE] [--target TEXT]
               [--issue-title TITLE] [--issue-body BODY] [--issue-label LABEL] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `issue` | positive int | required | Issue number to implement. |
| `--delegate AGENT` | free-form delegate label (adapters use `claude`/`codex`/`agy`/`ollama:MODEL`) | `None` | Explicit implementer override, echoed in the contract/result. The CLI does not validate the value; the adapter grammar does. |
| `--root` / `--dry-run` / `--live` / consent flags / `--target` / intake flags / `--json` | shared | — | Shared semantics. |

### Details

The contract resolves implementer routing, the branch pattern and worktree path pattern
(rendered in the output), consent scopes, and the handoff target. It explicitly marks
standalone implement as a non-merge path (`merge: never in standalone implement`) and
points the next step at `ship` or `pr-loop`. When issue context is supplied, the same
intake readiness gate as ship applies: a live non-`ready` issue exits 1 before any
branch/worktree creation or delegation, with the questions machine-readable in the
contract.

### Examples

```bash
keel implement .keel/project.yaml 76 --root . --dry-run --json
keel implement .keel/project.yaml 76 --root . --live --consent-mode standing \
  --approve-scope filesystem,git,github --operator "$USER" --json
keel implement .keel/project.yaml 76 --root . --live --delegate codex \
  --issue-title "Add setup docs" --issue-body "$ISSUE_BODY" --issue-label enhancement \
  --approve-scope filesystem,git,github --operator "$USER" --json
```

## `keel ci-check`

Standalone read-only CI diagnostic preflight contract.

```
keel ci-check <project.yaml> [--root DIR] [--pr N] [--target TEXT] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `--pr N` | positive int | `None` | PR whose latest checks should be diagnosed (becomes the target `PR #N`). |
| `--root` / `--target` / `--json` | shared | — | Shared semantics. |

### Details

`ci-check` has no `--live`/`--dry-run`/consent flags — it is read-only by construction:
the adapter diagnosis may propose one fix but never edits, pushes, re-runs, posts, or
merges. Its capability requirement is all-optional (`gh`, `gh-auth`, plus
`raw-actions-logs` when `ci_workflows` is configured), so it degrades rather than fails
on a limited transport. The JSON result records the workflow map, latest-run context
shape, supported diagnostic classifications, the one-fix policy, and next-command
recommendations.

### Examples

```bash
keel ci-check .keel/project.yaml --root . --pr 104 --json
```

## `keel init`

Scaffold a default `.keel/project.yaml` from the detected stack.

```
keel init [--root DIR] [--force] [--wizard]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `--root DIR` | path | `.` | Repo root to scaffold into. |
| `--force` | flag | off | Overwrite an existing config (warning printed; `.keel/extensions/` untouched). |
| `--wizard` | flag | off | Prompt interactively (base branch, timezone, merge window `HH:MM-HH:MM`, build/lint commands); Enter accepts the stack default. |

### Details

Stack detection: `pubspec.yaml` → Flutter, `pyproject.toml`/`setup.py` → Python,
`package.json` → Node, `build.gradle*` → Android, else generic. Refuses to overwrite an
existing config without `--force` (exit 1). The generated config passes `keel validate`.

### Examples

```bash
keel init
keel init --root ../app
keel init --wizard
```

## `keel install-adapter`

Install the agentic `/keel:<command>` adapters into a project (or regenerate the
committed plugin files).

```
keel install-adapter <agent> [--root DIR] [--force]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `agent` | `all` \| `plugin` \| `claude` \| `skills` | required | Target surface: `claude` → `.claude/commands/keel/<cmd>.md`; `skills` → `.agents/skills/keel-<cmd>/SKILL.md` (one shared copy for all non-Claude agents); `all` → both; `plugin` → repo-root `commands/<cmd>.md` for the committed Claude Code plugin. Unknown targets exit 1. |
| `--root DIR` | path | `.` | Project root to install into. |
| `--force` | flag | off | Overwrite existing adapter files (otherwise existing files are skipped, preserving local edits). |

### Examples

```bash
keel install-adapter all
keel install-adapter claude --force
keel install-adapter plugin   # regenerate committed plugin command files
```

## `keel adapter-status`

Report generated-adapter freshness plus orphan/unmanaged surface findings (advisory
only).

```
keel adapter-status [agent] [--root DIR] [--include-unmanaged] [--json]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `agent` | `all` \| `claude` \| `skills` \| `legacy-claude` | `all` | Surface(s) to inspect. Unknown targets exit 1. |
| `--root DIR` | path | `.` | Project root. |
| `--include-unmanaged` | flag | off | Also scan for marker-less command-like surfaces (heuristic, opt-in). Project-declared project-only commands are never flagged. |
| `--json` | flag | off | Machine-readable adapters + orphans payload. |

### Details

Freshness statuses: `current`, `outdated`, `missing`, `locally-modified`, `unknown`.
Orphans (always on) are files with a `keel-generated` marker whose command is no longer
in the installed keel set; unmanaged (opt-in) are marker-less keel-like files. Both are
diagnostic — keel never auto-deletes. Absent opt-in `legacy-claude` wrappers are
reported as not-installed, not `missing`.

### Examples

```bash
keel adapter-status all --root .
keel adapter-status all --root . --include-unmanaged --json
```

## `keel update-adapter`

Safely refresh generated adapters from the installed keel package.

```
keel update-adapter [agent] [--root DIR] [--dry-run]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `agent` | `all` \| `claude` \| `skills` | `all` | Surface(s) to update. Unknown targets exit 1. |
| `--root DIR` | path | `.` | Project root. |
| `--dry-run` | flag | off | Print planned updates (`would-update` rows) without writing. |

### Details

Updates only `missing` and `outdated` generated files; refuses to overwrite
`locally-modified` or `unknown` files (those need a human merge). Never touches project
config, `.keel/extensions/*`, project commands, or wrappers not marked as generated.

## `keel sync`

The everyday short name for `update-adapter all` plus next-step hints and an orphan
heads-up.

```
keel sync [--root DIR] [--target all|claude|skills] [--dry-run]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `--root DIR` | path | `.` | Project root. |
| `--target` | `all` \| `claude` \| `skills` | `all` | Surface to sync. |
| `--dry-run` | flag | off | Plan only. |

### Details

`sync` uses the keel package already installed in the active Python environment — it
never contacts PyPI or upgrades the package; upgrade `keel-workflow` with `pipx`/`pip`
first. After a successful sync it prints the orphan count (if any) and the recommended
`keel validate` / `keel plan` follow-ups.

### Examples

```bash
pipx upgrade keel-workflow
keel sync --root . --dry-run
keel sync --root .
```

## `keel install-legacy-wrappers`

Install thin legacy command shims that delegate to `/keel:<command>` after parity is
proven.

```
keel install-legacy-wrappers <agent> [--root DIR] [--force]
                             [--parity-matrix PATH] [--command LEGACY=KEEL]
```

| Flag | Type / values | Default | Effect |
| --- | --- | --- | --- |
| `agent` | `all` \| `claude` \| `skills` | required | Wrapper surface: `claude` → `.claude/commands/<legacy>.md`; `skills` → `.agents/skills/source-command-<legacy>/SKILL.md`. Unknown targets exit 1. |
| `--root DIR` | path | `.` | Project root. |
| `--force` | flag | off | Overwrite existing wrappers. |
| `--parity-matrix PATH` | markdown file | `docs/keel/parity-matrix.md` | Matrix whose `parity-proven`/`deferred` rows allow wrapper generation. A missing matrix exits 1. |
| `--command LEGACY=KEEL` | `name=name` mapping, repeatable | all ready commands, identity-mapped | Install specific wrapper mappings. Malformed values are argparse errors; a mapping to a non-ready command exits 1. |

### Examples

```bash
keel install-legacy-wrappers all --command ship=ship
keel install-legacy-wrappers skills --command ship=ship --command morning=morning
keel install-legacy-wrappers all --force
```

## `/keel:ship` adapter arguments

The `/keel:ship` adapter (source: `src/keel/adapters/commands/ship.md`) is the
agent-facing flow on top of the CLI. Its argument grammar is stricter than argparse:
unknown flags, out-of-range values, a flag missing its value, repeated single-value
flags (`--reviewers 2 --reviewers 3`), an empty `ollama:` model, and zero/negative
positionals are all rejected as user error.

```
/keel:ship [issue numbers...] [--compound|--profile <standard|compound>]
           [--delegate <claude|codex|agy|ollama:MODEL>]
           [--review-delegate <claude|codex|agy|ollama:MODEL>]
           [--review-comments <inline|summary>] [--reviewers <1|2|3>]
           [--jury|--no-jury|--jury-advisory] [--hotfix] [--dry-run] [--wizard]
```

| Argument | Type / values | Default | Effect |
| --- | --- | --- | --- |
| issue numbers | bare positive integers | none → **watch mode** | Explicit issue(s) to ship. With none, the run takes the top of the backlog (highest priority first, then ascending issue number), with a capped watch-mode batch. |
| `--compound` / `--profile` | flag / `standard` \| `compound` | `standard` | Select the workflow profile. `--compound` ≡ `--profile compound`. |
| `--delegate` | `claude` \| `codex` \| `agy` \| `ollama:MODEL` | host agent | The s4 implementer; per-run override of any issue role/delegate label. |
| `--review-delegate` | same value set | host agent | The s7 reviewer vendor. |
| `--review-comments` | `inline` \| `summary` | `inline` | How reviewer findings post in s7. |
| `--reviewers` | `1` \| `2` \| `3` | auto (from tier) | Override the tier-derived reviewer count. |
| `--jury` / `--no-jury` / `--jury-advisory` | flags | tier-driven | Cross-vendor jury gate control (s8). |
| `--hotfix` | flag | off | Audited merge-window bypass at s10. |
| `--dry-run` | flag | off | Read-only rehearsal of s0–s8. |
| `--wizard` | flag | off | Interactive pre-s1 config collector (interactive contexts only). |

### `--compound` / `--profile` — in depth

The compound profile is a **first-class profile of ship, not a second backbone and not a
project extension**. The JSON contract's `workflow_profile` block reports
`profile: "compound"`, `inherits: "ship"`, `first_class_variant: true`, and
`step_overrides` for exactly four steps:

| step | compound behavior |
| --- | --- |
| `s4 implement` | Compound implement pass emphasizing PR quality, scope simplification, and value-first change shaping before handoff. |
| `s7 review` | Compound/persona reviewer fan-out when available — while preserving the reviewer count, posting mode, and gating semantics (including jury) from `review_merge_contract`. |
| `s9 fixloop` | Structured compound feedback-resolution loop, keeping the shared blocker/suggestion policy and the ≤3-round review-fix budget. |
| `s11 capture` | Durable-learning capture through the capture slot, with the same canonical `compound-learning: pr=…` marker requirement. |

**What stays identical** (the point of `step_overrides`): selection, worktree isolation,
guard, classification, CI, gates, the review/jury/merge-gate contract, the merge window,
the merge lock, closeout, attribution, and capture-marker discipline. If a compound
helper is unavailable for a step, the run falls back to standard behavior for that step,
logs the degradation, and continues unless an extension marks it blocking. Under
`--dry-run`, compound must show the same non-mutating contract as standard plus the
compound `workflow_profile`. `--compound` composes with every other flag
(e.g. `--compound --jury`).

### `--delegate` / `--review-delegate` — in depth

Value set `claude | codex | agy | ollama:MODEL`; `ollama:` requires a non-empty model
(per-issue model overrides can also come from a `delegate-model:<name>` label).
Implementer precedence at s4: `--delegate` flag > issue `delegate:*` label >
`HOST_AGENT` (the CLI driving the run, resolved from the runtime). Delegated CLI
implementers are fed the prompt via stdin and run network-enabled; a bare local (Ollama)
model cannot run tools, so the orchestrator does every git/PR step itself and delegates
only diff generation (≤2 retries on a bad diff, then fall back). **Local-model
implementers are refused on tier-3** (pre-classified from the issue's target
paths/labels; ambiguous ⇒ tier-2 and let s7 gate) — the run falls back to `HOST_AGENT`
there. Quota errors (HTTP 429 / RESOURCE_EXHAUSTED), missing CLIs, and unparseable JSON
returns all fail over to `HOST_AGENT` with the reason logged. A non-host
`--review-delegate` vendor runs strictly read-only/findings-only; the orchestrator owns
all GitHub writes on every path. Attribution always records the **effective**
vendor+model that actually ran, never the requested-but-fell-back one.

### `--review-comments inline|summary`

`inline` (default): each reviewer's critical/major findings are anchored as inline
review comments on their `file:line` in **one submitted review per reviewer**;
non-anchorable findings go to the summary; an inline-API error falls soft to a summary
comment *for that one reviewer only*. `summary`: one consolidated review comment per
reviewer. The s9 loop-exit parser reads the reviewer's returned findings, not the
comment shape, so gating is mode-independent. The jury honours the same mode (its native
inline flag in inline mode, never under `--dry-run`).

### `--reviewers 1|2|3`

Overrides the tier-derived count (TIER-3→3, TIER-2→2, TIER-1→1) for the s7 fan-out.
Coverage invariant: focus dimensions merge when the count drops, never drop — a
1-reviewer slot covers all dimensions and is suitable only for narrow tier-1 PRs. When
`--reviewers` is passed the adapter does not compute the tier, so the *tier-3 jury
auto-trigger does not apply on that path* (jury enablement is still evaluated via the
flags). **CI caveat:** the required `keel evidence (required)` CI check runs
`keel evidence-verify` against the live PR and recomputes the reviewer requirement from
the tier derived from the PR's changed files — an operator lowering `--reviewers`
locally does not lower the CI evidence gate; the PR will still be held for the
tier-derived verdict count unless an operator records explicit deferrals or applies the
`keel:evidence-waived` waiver label.

### `--jury` / `--no-jury` / `--jury-advisory`

Precedence: `--no-jury` > `--jury` > tier-3 auto-on > off. Mode is **gating** by default
when enabled; `--jury-advisory` makes it report-only. The jury never changes the
reviewer count. In gating mode only **verified consensus** findings fold into s9
(critical/major ⇒ block, minor ⇒ gated suggestion, nit ⇒ advisory; a jury-driven fix
consumes one fix round). A sub-2-vendor panel is downgraded to advisory, and a jury run
that did not complete cleanly never gates (fail-soft: an absent or erroring jury can
never manufacture a block). The single jury verdict comment is posted through
`keel post-comment --artifact jury-verdict`.

### `--hotfix`

Promotes the issue to a window-bypassing blocker. At s10 the bypass happens *inside*
`keel merge --hotfix`: it skips only the window check — the merge lock, CI rollup,
evidence verification, and consent scopes all still apply, and the bypass is recorded in
the merge payload and the run ledger for audit. Blocker status can also be
auto-detected at s3 (alert labels, word-boundary blocker regex on title/body,
high-priority + urgent keyword, or a red gating workflow on `base_branch`); `--hotfix`
is the explicit human override. Use sparingly.

### `--dry-run` (adapter obligations)

Runs s0–s8 read-only and prints the plan plus the `keel ship` assessment. Obligations:
no push, no PR, no comments/labels/ready-flips/merges/closes — every would-be write is
logged as `DRY-RUN: …`. The implementer is told not to push or open a PR; reviewers
still run for real (read-only) so findings stay meaningful. `keel merge --dry-run` may
still be exercised for the claim/window/rollup path. The capture step is a logged no-op
with the `skipped:dry-run` marker reason, and attribution label flips are logged instead
of written. A local-model harness that created a worktree under dry-run must still
remove it.

### `--wizard`

Interactive opt-in only: a pre-s1 front layer that collects the same options the grammar
produces — it adds no pipeline behavior and cannot produce a config the grammar could
not. **Hard interactivity guard:** in any non-interactive context (watch mode,
overnight/background/headless) it degrades to a logged no-op and proceeds with the
literal flags as parsed — never a hang, never a rejection. It probes installed CLIs and
local models best-effort to build the offered choices, offers a Quick-start vs Customize
fast path, shows each question's default first, then echoes the resolved config and
proceeds to s1.

### Examples

```bash
# Watch mode: take the top of the backlog
/keel:ship
# Ship two issues with a delegated implementer and summary review posting
/keel:ship 76 82 --delegate codex --review-comments summary
# Compound profile with a gating jury
/keel:ship 76 --compound --jury
# Read-only rehearsal — reviewers run for real, nothing is written
/keel:ship 76 --dry-run
# Emergency blocker: audited window bypass at s10
/keel:ship 91 --hotfix
# Local model implementer (refused on tier-3, falls back to the host agent)
/keel:ship 76 --delegate ollama:qwen2.5-coder --review-delegate claude
```
