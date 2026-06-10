# Ship Legacy Baseline

Issue #81 captures the latest legacy `ship` baseline before `/keel:ship` can be
declared parity-proven. This is a sanitized comparison record: it records portable
workflow behavior, not consumer project policy or private repository paths.

## Snapshot

| Field | Value |
|---|---|
| Captured on | 2026-06-07 |
| Source | Latest local legacy `ship` command skill available to the operator, from the consumer-owned command-skill surface |
| Source SHA-256 | `96cbea677eb3032f4646e74440a9be51c07c035155c651f9f8a185148433f2f7` |
| Source length | 876 lines |
| Compared against | `src/keel/adapters/commands/ship.md` and `keel plan --command ship --json` |
| Parity row | `ship` in `docs/keel/parity-matrix.md` |

The source command body contains project-local policy and must not be copied into keel.
Only portable behavior is classified below.

To refresh this baseline without adding private paths to public docs, run the equivalent of
`shasum -a 256 <legacy-ship-skill>` and `wc -l <legacy-ship-skill>` against the current
consumer-owned legacy `ship` skill, then update only the hash, line count, date, and portable
delta classification.

## Structured Contract Check

The packaged `/keel:ship` adapter is backed by the structured command contract from
`keel plan --command ship --json`. The contract provides the machine-readable pieces
needed by the baseline:

- fixed backbone graph and command-local steps
- project gates and extension hooks
- runtime capability requirements and degraded capability reporting
- GitHub transport selection
- dry-run and live consent metadata
- declared side effects and operator-consent scopes
- review, jury, posting, CI, fix-loop, merge-gate, and closeout metadata through
  `review_merge_contract`

This means baseline deltas should be implemented either in the core contract, the ship
adapter, project policy, project extensions, or generated wrappers. They should not be
reintroduced as project-specific prose in the packaged command body.

## Delta Classification

| Legacy baseline behavior | Classification | Keel representation |
|---|---|---|
| Runtime chooses between the GitHub CLI and an API/MCP-style fallback, with known raw-log gaps called out. | Runtime / transport | `github_transport.resolve`, runtime capability reporting, and `/keel:ship` transport instructions. |
| Operator consent is checked before live mutations, and delegated agents receive only the approved mutation scope. | Core invariant / adapter instruction | `keel plan --live --json`, `operator_consent`, and the delegated-agent scope paragraph in `/keel:ship`. |
| Issue selection is snapshotted; closed or unreadable issues are dropped or blocked before work begins. | Adapter instruction | `/keel:ship` s1 selection rules. |
| Work happens in an isolated worktree and every branch is cut from the project base branch. | Core invariant / adapter instruction | `/keel:ship` s2 plus worktree path validation in s10. |
| The orchestrator checks branch scope after implementation instead of trusting a delegated agent's declared file list. | Core invariant | `/keel:ship` s4 branch-scope validation gate. |
| Delegated agents may be unavailable, exceed quota, omit JSON, or ignore write boundaries; the orchestrator owns fallback and validation. | Core invariant / adapter instruction | s4 quota/unavailability fail-over, local-model constraints, consent scope checks, and branch-scope validation. |
| Reviewer count is risk-based; reduced reviewer counts merge focus areas rather than dropping coverage. | Core invariant / adapter instruction | s5 tiering and s7 coverage invariant. |
| Reviewers are read-only; the orchestrator posts review comments and owns all GitHub writes. | Core invariant | s7 orchestrator-only-writes rule and s7 posting contract. |
| Inline review posting should degrade per reviewer when anchoring fails, not collapse the whole review round. | Adapter instruction | s7 inline-vs-summary fallback. |
| Suggestions are gated: apply them or record explicit deferral; narrowed re-review verifies the applied fix only. | Core invariant / adapter instruction | s9 fixloop and narrowed re-review contract. |
| CI evaluation treats failure before pending, allows empty checks only for docs-only paths, and has bounded retry budgets. | Core invariant / runtime transport | s6 CI gate, shared check-run semantics, and runtime transport gap handling. |
| Tester loop-backs and merge-conflict loop-backs are defensive, but implementer fixes can still consume review budget. | Core invariant | s8 tester gate and s10 merge-prep rules. |
| Merge prep happens outside the merge lock; the lock covers only final sanity checks and the literal merge. | Core invariant | s10 pre-merge prep and literal merge block. |
| Mergeability is rechecked inside the lock; behind or dirty state aborts instead of being resolved while holding the lock. | Core invariant | s10 final sanity rules. |
| Merge-window rules apply only to merging; implementation, CI, review, and tester gates may run outside the window. | Core invariant | s10 window re-check and deferral behavior. |
| A successful merge is authoritative even if local cleanup after merge reports a non-critical error. | Adapter instruction | s10 PR-state authoritative rule. |
| Closeout comments must appear on both the issue and PR and include changed files, docs, manual checks, and capture outcome. | Core invariant / adapter instruction | s11 capture and s12 close. |
| Post-merge knowledge capture must be fail-soft and auditable, with one canonical capture marker per merged PR. | Extension / capture invariant | s11 capture Lego and marker discipline. Detailed project learning workflows stay outside keel core. |
| Session-level learning verifier rejects silent capture skips. | Project extension / deferred from core | Keel core preserves capture slots and marker vocabulary; project-specific verifier implementation belongs in a capture extension or follow-up parity work if still required. |
| Project-specific high-risk file lists, test commands, labels, report paths, and manual playbooks are embedded in the legacy command. | Project policy / extension / project command | Represented by `policy_pack`, extension hooks, and `policy_pack.project_commands`; intentionally not copied into keel core. |

## Ship Parity Evidence

#69 closes the remaining `ship` row gap by making the review, jury, merge-gate,
closeout, and wrapper-facing behavior visible in structured output instead of only in
adapter prose.

The `review_merge_contract` included by `keel plan --command ship --json` and by
`keel ship --json` records:

- reviewer count, source, independent reviewer slots, LGTM expectations, and focus
  merging when the reviewer count is reduced
- project-owned review additions and required PR/review sections from `policy_pack.review`
- `inline` versus `summary` posting mode and per-reviewer inline fallback behavior
- `--jury`, `--no-jury`, tier-3 auto-jury, and `--jury-advisory` precedence
- jury fail-soft behavior, minimum-vendor threshold, and verified-consensus gating
- critical/major/minor/nit finding actions and the bounded review fix loop
- CI failure-before-pending semantics and docs-only empty-check handling
- merge-window, hotfix, merge-lock, final mergeability, and PR-state authority rules
- issue plus pull-request closeout comments and the required capture marker

Unit coverage asserts these contract fields directly and CLI coverage asserts the JSON
surface for default, no-jury, explicit-jury, advisory, reviewer-override, and posting-mode
cases. The parity matrix can therefore mark `ship` as `parity-proven`; behavior unique to the
`ship --compound` profile or review-only feedback commands remains tracked by their own rows and issues.
