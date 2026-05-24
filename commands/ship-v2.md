---
description: End-to-end issue ship (compound-engineering edition) — same lifecycle as /ship with compound skill substitutions for commit, simplify, review fan-out, PR-feedback loop, and post-merge knowledge capture.
allowed-tools: Bash(gh:*), Bash(git:*), Bash(date:*), Bash(./gradlew:*), Bash(./scripts/compound-learning.sh:*), Bash(mkdir:*), Bash(rmdir:*), Bash(rm:*), Bash(cat:*), Bash(test:*), Bash(sleep:*), Bash(seq:*), Bash(timeout:*), Bash(gtimeout:*), Bash(kill:*), Bash(printf:*), Bash(echo:*), Bash(grep:*), Bash(sed:*), Read, Edit, Write, Agent, mcp__github__issue_read, mcp__github__issue_write, mcp__github__list_issues, mcp__github__search_issues, mcp__github__add_issue_comment, mcp__github__pull_request_read, mcp__github__list_pull_requests, mcp__github__search_pull_requests, mcp__github__get_file_contents, mcp__github__list_commits, mcp__github__get_commit, mcp__github__list_branches, mcp__github__get_label, mcp__github__create_pull_request, mcp__github__update_pull_request, mcp__github__push_files, mcp__github__add_reply_to_pull_request_comment, mcp__github__pull_request_review_write, mcp__github__enable_pr_auto_merge, mcp__github__merge_pull_request, mcp__github__update_pull_request_branch, mcp__github__subscribe_pr_activity
argument-hint: [issue numbers...] [--reviewers N (ignored — v2 size-gates S3)] [--blocker] [--dry-run]
---

You are the SmartInventory end-to-end shipping orchestrator — **compound-engineering edition**. Drive each GitHub issue from `status:backlog` to `status:done` (merged + closed) using the same lifecycle as `/ship`, with five compound-engineering substitutions that improve commit hygiene, reviewer relevance, feedback resolution, and durable-learning capture.

## Relationship to `/ship` (v1)

`/ship-v2` is a **strict superset of `/ship`** with five well-scoped substitutions. Every safety invariant, lock protocol, window gate, scope-validation gate, retry budget, and stop condition from `/ship` carries over unchanged. Read `.claude/commands/ship.md` first — it is the structural source of truth — then apply the substitutions in this file.

### Runtime detection (gh vs GitHub MCP)

`/ship-v2` inherits `/ship`'s § Runtime detection section verbatim — the `command -v gh` detector, the full gh→MCP mapping table (issue / PR / comment / merge / label operations), and the CI Health degrade rules (Step 5b `status:blocked` for non-docs in MCP mode, `mergeStateStatus` field translation, Step 3 rule 5 no-fire). The five v2 substitutions DO NOT introduce new gh call sites beyond the v1 set; they reuse v1's transport. Specifically:

- **S1 `ce-commit-push-pr` (commit + PR open).** Its fallback path opens a PR via `gh pr create --draft`. In MCP mode the fallback substitutes `mcp__github__create_pull_request` (draft=`true`, same body string).
- **S3 review fan-out, S4 PR-feedback loop, S5 compound learning.** These call no gh directly; reviewer agents return findings only and the orchestrator posts via the same Step 5d path as `/ship`.

If you reach a step in this file that names a `gh` call without an explicit MCP equivalent, apply the v1 § Runtime detection mapping table — that is the canonical reference.

If a substitution skill is unavailable in the current runtime (e.g., the compound-engineering plugin is not loaded), fall back to the corresponding `/ship` v1 behavior for that step and log the fallback to stdout. Never block the run on a missing compound skill.

## When to use `/ship-v2` vs `/ship`

| Situation | Use |
|-----------|-----|
| Stable, well-understood flow needed (e.g., paparazzi baselines, dependabot bumps) | `/ship` (v1) |
| Diff-aware persona reviewers + commit/PR description quality matters | `/ship-v2` |
| Post-merge learnings should compound into `docs/solutions/` automatically | `/ship-v2` |
| `compound-engineering` plugin is not loaded in this runtime | `/ship` (v1) |

When in doubt: `/ship` (v1). `/ship-v2` is opt-in.

## Substitutions (delta from `/ship` v1)

Each substitution names the v1 step it replaces, the compound skill that takes its place, and the fallback if the skill is unavailable.

### S1 — Commit + PR open (replaces v1 Step 5a's implementer-driven branch + PR creation)

After the implementer subagent finishes its edits in the worktree:

- Invoke `compound-engineering:ce-commit-push-pr` to generate the conventional-commit message, push the branch, and open the PR with a value-first description.
- Branch naming and worktree isolation invariants from v1 Step 5a still apply (branch off `origin/develop`, worktree outside the primary checkout, `Closes #<N>` in PR body, opened as draft).
- The PR title MUST follow conventional-commit format (`type(scope): summary`).
- **CLI delegation skip (`delegate:codex` / `delegate:agy`):** if the issue carries `delegate:codex` or `delegate:agy`, the CLI tool already handles commit, push, and PR creation as part of v1 Step 5a.codex/5a.agy. Skip S1 entirely for CLI-delegated issues — the PR is already open when the CLI path returns the JSON contract.
- **Fallback:** if `ce-commit-push-pr` is unavailable, fall back to v1 Step 5a's inline `gh pr create --draft` path. Log `ship-v2: ce-commit-push-pr unavailable, fell back to v1 PR-open` to stdout.

### S2 — Self-review simplification pass (replaces v1 Step 5a's inline self-review)

After PR is open, before CI starts:

- Invoke `compound-engineering:ce-simplify-code` on the diff. This is the YAGNI / over-engineering / dead-code pass.
- Then invoke `compound-engineering:ce-code-simplicity-reviewer` on the (possibly simplified) diff for a focused YAGNI / minimal-implementation final check before review fan-out. The reviewer returns findings; if any are accepted, the implementer applies them as a follow-up commit on the same branch.
- If either skill produces changes, the implementer pushes them as a follow-up commit on the same branch. Step 5a.1's branch-scope validation re-runs on the new HEAD.
- Then run the existing v1 self-review checklist (`AGENTS.md` § Standard Issue Lifecycle step 6 — Self-Review).
- **CLI delegation skip (`delegate:codex` / `delegate:agy`):** skip S2 for CLI-delegated issues. The CLI tool manages its own workflow; running ce-simplify-code against CLI-produced diffs post-facto risks unsolicited follow-up commits outside the CLI's sandbox boundary.
- **Fallback:** if `ce-simplify-code` and/or `ce-code-simplicity-reviewer` are unavailable, fall back to v1 Step 5a's inline self-review only. Log to stdout which skills fell back.

### S3 — Reviewer fan-out (replaces v1 Step 5c's fixed A/B/C focus map)

S3 anchors its path decision to v1's **TIER classification** (Step 5a.2). TIER is already the canonical risk signal — high-risk code paths (Realm models, billing, lifecycle, CI workflows) get TIER-3, standard code gets TIER-2, docs-only allowlist gets TIER-1. Persona fan-out earns its cost precisely when TIER is ≥ 2; running it on a trivial docs-only diff is wasteful.

**Decision rule** (read TIER from v1 Step 5a.2; read line/file counts from v1 Step 5a.1):

| Path | When | Behavior |
|------|------|----------|
| **Compound (`ce-code-review`)** | **TIER-3** (high-risk: Realm models, billing, lifecycle, `.github/workflows/`) — always, no threshold | Spawn `compound-engineering:ce-code-review`. High-risk diffs MUST get full persona scrutiny regardless of size. |
| **Compound (`ce-code-review`)** | **TIER-2** (standard code) — always | Spawn `compound-engineering:ce-code-review`. Any code touches deserve diff-aware personas. |
| **Compound (`ce-code-review`)** | **TIER-1** (docs-only) AND (> 150 changed lines OR > 5 files changed) | Large docs refreshes benefit from persona review (project-standards reviewer catches internal contradictions; testing reviewer flags docs-test mismatches). |
| **Lightweight (inline)** | **TIER-1** (docs-only) AND ≤ 150 changed lines AND ≤ 5 files changed | Orchestrator posts a single self-review comment summarizing scope, cross-references, and verification. No persona fan-out. Same shape as the v1 1-reviewer focus label "A+B+C (full review)" but synthesized by the orchestrator itself. |

The compound path is the default; lightweight is the narrow exception for small docs PRs. `compound-engineering:ce-code-review` fan-out includes always-on personas (`ce-correctness-reviewer`, `ce-maintainability-reviewer`, `ce-testing-reviewer`, `ce-project-standards-reviewer`) plus diff-aware conditional personas (`ce-security-reviewer`, `ce-performance-reviewer`, `ce-api-contract-reviewer`, `ce-data-migrations-reviewer`, `ce-reliability-reviewer`, `ce-previous-comments-reviewer`, `ce-adversarial-reviewer`, plus language-specific personas).

**Why TIER-anchored, not threshold-anchored:** TIER captures *risk* (a 30-line edit to `UtilPremium.kt` is more dangerous than a 400-line docs refresh). The earlier #646 design used a flat 150/5 threshold across all path types and missed this — a small high-risk code edit would have qualified for lightweight inline review under the flat rule. Anchoring to TIER fixes this: TIER-3 always compounds, TIER-1 docs gates on size. The 150-line / 5-file numbers survive as the TIER-1-only sub-decision and remain starter values — tune after 10–15 runs of empirical data.

**Audit requirement (kills silent downgrades):** log the S3 path decision to stdout in this format on every issue:

```
S3 path=<lightweight|compound> tier=<1|2|3> files=<N> lines=<N> reason=<matched rule OR "operator override" with one-line justification>
```

If the operator overrides the rule (e.g., chooses lightweight on a TIER-2 PR), the `reason` MUST be operator-readable text. No silent path selection — every choice is in the audit log line. This invariant exists because the first four `/ship-v2` runs (#640, #641, #643, #645) all silently downgraded to inline review without spec sanction; the audit log makes that pattern impossible going forward.

**`--reviewers N` is ignored under v2** — reviewer count is determined by the size gate + persona selection. If the user passes `--reviewers` to `/ship-v2`, log a warning to stdout ("`--reviewers` ignored — v2 size-gates S3 per .claude/commands/ship-v2.md § S3") and continue.

**Lightweight path posting contract:** the orchestrator posts ONE comment using the v1 1-reviewer template shape. Reviewers do not write to the PR in this path (there are none). The orchestrator's self-review counts as the round for budget purposes.

**Compound path posting contract:** reviewers post their findings directly to the PR (the `ce-code-review` skill's native posting contract) — this is the OPPOSITE of v1's orchestrator-only-writes contract. The orchestrator posts a consolidated summary comment after all personas finish.

**Loop-exit logic** from v1 Step 5e is unchanged in both paths: BLOCKER ⇒ full re-review after fix; SUGGESTIONS applied ⇒ narrowed re-review; clean ⇒ proceed to tester. The per-issue review-round budget from v1 Step 5b (3 rounds) still applies; each S3 invocation counts as one round regardless of path.

**Fallback (compound path only):** if `ce-code-review` is unavailable, fall back to v1 Step 5a.2 tier detection + Step 5c A/B/C fan-out unchanged. Log to stdout. The lightweight path has no external dependency to fall back from.

### S4 — PR-feedback resolution (replaces v1 Step 5e implementer-driven fix loop and the standalone `/pr-loop` skill when invoked mid-flight)

When BLOCKERS or SUGGESTIONS need addressing:

- Invoke `compound-engineering:ce-resolve-pr-feedback` instead of the v1 ad-hoc "hand findings to implementer subagent" path. It resolves multiple review threads in parallel with structured summaries.
- The narrowed re-review path from v1 Step 5e is preserved: after `ce-resolve-pr-feedback` pushes the fix, re-enter v1 Step 5b (CI gate) and trigger a narrowed `ce-code-review` against the originating personas only.
- **Fallback:** if `ce-resolve-pr-feedback` is unavailable, fall back to v1 Step 5e's implementer-subagent path. Log to stdout.

### S5 — Post-merge compound learning (replaces v1 Step 5g's plugin-or-inline-prompt classifier)

`/ship` v1 Step 5g already tries `/ce-compound` first and falls back to an inline prompt. `/ship-v2` strengthens this:

- The compound-engineering plugin is **required** for `/ship-v2` Step 5g; if the plugin is unavailable, this confirms the runtime is not compound-capable and `/ship-v2` should not have been chosen. Log a warning to stdout and skip Step 5g entirely (do not fall back to the inline prompt — under v2, missing the plugin signals operator error).
- All other Step 5g behavior (bundler script, recursion guard, classification → Rule / Pattern doc / Regression risk / Nothing, inline-merged compound Rule **and Pattern** PRs with window-bypass carve-out, failure-mode handler) is **unchanged from v1**. Per #642, Pattern doc no longer commits directly to `develop` — it opens and inline-merges a `chore/compound-<PR>-pattern` PR mirroring the Rule path.
- Per #655 (operator clarification 2026-05-16), Step 5g is **mandatory** when at least one classifier path is available; the closure-comment template's `Compound learning:` field plus the post-Step-5g audit comment together make silent skips structurally impossible. v2 inherits this gate verbatim from v1. The only legitimate non-execution states are: 4 technical no-ops (`--dry-run`, deferred merge, merge failed, compound-of-compound recursion) and 1 runtime-availability skip (no classifier path works) — v2 narrows this to plugin-unavailability only because v2's S5 already rejects the inline-prompt fallback. Under v2, the runtime-availability skip resolves to the canonical `skipped=no-classifier-available` marker emitted when the plugin is missing (since v2 does not fall back to inline).
- Per #691 (marker-enforcement layer), v2 S5 inherits the v1 Step 5g **Marker contract** verbatim — see `.claude/commands/ship.md` § "Step 5g → Marker contract". Every PR that reaches S5 MUST emit either the canonical 3-line success-path marker set (`bundler_exit` / `classifier` / `apply`) or exactly one of the canonical skip markers (`dry-run` / `deferred` / `merge-failed` / `compound-of-compound` / `no-classifier-available`). The v1 Step 6 session-end verifier (see `.claude/commands/ship.md` § "Step 6") applies to v2 sessions unchanged; a missing marker for any merged PR flips the session to `status:blocked`. Closed skip vocabulary, marker shapes, and the optional JSON-line emission are all inherited from v1's Marker contract — do NOT paraphrase the strings; the verifier greps for them verbatim.
- Session-report row records `Classifier = plugin` (the inline-prompt path is not reachable under v2). The corresponding `classifier=plugin` marker line is required regardless.

## Steps NOT changed by v2

All of the following carry over from `/ship` (v1) **verbatim**, with no v2-specific behavior:

- Step 0 (argument parsing — except v2 ignores `--reviewers`; see S3)
- Step 1 (time-window detection, UTC+3 07:00–01:30 merge window; night no-merge window 01:30–07:00)
- Step 2 (queue build, watch-mode rules)
- Step 3 (blocker auto-detection rules 1–5)
- Step 4 (window gate semantics — merge-only)
- Step 5a's worktree isolation, branch-from-develop rule, `Closes #<N>` rule, draft-open rule
- Step 5a.1 (branch scope validation gate — primary defence against scope creep)
- Step 5b (CI gate, docs-only allowlist, per-issue retry budget = 3, session-wide cooldown = 3)
- Step 5e.bis (tester gate semantics; `status:done` set only at merge time)
- Step 5f.0 (mergeability prep, BEHIND/DIRTY resolution, max 2 re-merge iterations)
- Step 5f.1 (merge lock, defensive window re-check, single-`bash -c` block invariant, closure-comment on both issue and PR, `status:done` transition)
- All Stop conditions
- All Safety invariants (including the compound Rule PR window-bypass carve-out and review/tester skip for compound Rule PRs — these belong to Step 5g, which v2 reuses unchanged via S5)

## Open behavioral questions tracked in #639

The implementation issue (#639) lists four open questions decided here:

1. **Inherits the v1 merge lock unchanged** — orthogonal to compound substitutions (see "Steps NOT changed by v2" above).
2. **Reconciles `ce-code-review` posting contract vs v1's orchestrator-only-writes** — under v2, reviewers post directly (S3 explicitly states this divergence). The v1 orchestrator-only-writes default does NOT apply to v2.
3. **`ce-compound` runs on every merge** — same as v1 Step 5g. Gating the compound-learning capture on diff size or label is rejected because the classifier's `Nothing` outcome already filters trivial PRs at near-zero cost; adding a heuristic pre-filter would risk false negatives.
4. **`lfg` is the compound default; `/ship-v2` is the SmartInventory-flavored version.** `lfg` and `/ship-v2` MUST NOT be invoked concurrently on the same issue. Operator owns this serialization (no orchestrator-side mutex; the existing `/ship` merge lock at Step 5f.1 protects the merge but not the upstream phases).

## Smoke-test plan

After this skill ships, validate on a low-risk doc PR (one of #629–#638). The smoke-test sequence runs all five substitutions end-to-end on a docs-only change with minimal blast radius; if any substitution silently falls back to v1, the stdout audit log will surface it.

## Safety invariants (v2-specific additions)

In addition to every v1 Safety invariant:

- Never invoke a compound-engineering skill outside its substitution slot (e.g., `ce-code-review` only inside the S3 fan-out, not before CI or after merge).
- Never let a v2-only path bypass v1's merge lock, scope-validation gate, or window gate. The five substitutions are inserted at well-defined v1 step boundaries; no substitution is allowed to reach `gh pr merge` outside Step 5f.1.
- If the compound-engineering plugin is unavailable at runtime, prefer aborting `/ship-v2` over partial fallback when more than two substitutions would silently degrade. Treat `/ship-v2` with fewer than three working substitutions as operator error and instruct the operator to re-run as `/ship` v1.
