---
description: 3-reviewer parallel cycle for one or more PRs — code-quality / bugs+security / tests+regression. Each reviewer posts directly to the PR; orchestrator posts a consolidated summary and adds `review-cycle-complete`.
allowed-tools: Bash(gh:*), Bash(git:*), Bash(date:*), Bash(grep:*), Bash(sed:*), Bash(awk:*), Bash(printf:*), Bash(echo:*), Bash(test:*), Bash(command:*), Read, Agent, mcp__github__pull_request_read, mcp__github__add_issue_comment, mcp__github__update_pull_request, mcp__github__get_label
argument-hint: <pr_number> [pr_number ...]
---

You are running the SmartInventory 3-reviewer cycle for one or more pull requests. For each PR, three `code-reviewer` subagents review the same diff in parallel (same fan-out pattern as `/ship` Step 5c), each posts its own findings DIRECTLY to the PR as a review comment, and the orchestrator follows up with a consolidated summary plus a `review-cycle-complete` label.

## Runtime detection (gh vs GitHub MCP)

```bash
if command -v gh >/dev/null 2>&1; then
  GH_MODE=cli
else
  GH_MODE=mcp
fi
```

State the mode in the first user-facing line. Mapping for the gh call sites in this command:

| gh CLI | GitHub MCP equivalent |
|---|---|
| `gh pr view <PR> --json number,state,isDraft,headRefName,baseRefName,title,url` | `mcp__github__pull_request_read` (method=`get`). |
| `gh pr diff <PR>` (reviewer context) | `mcp__github__pull_request_read` (method=`get_files`) for the file list plus `mcp__github__get_file_contents` at the PR head SHA for any file the reviewer needs in full. The reviewer's prompt already names this fallback (it has no diff endpoint). |
| `gh pr comment <P> --body "..."` (reviewer post + consolidated summary) | `mcp__github__add_issue_comment`. **Subagent transport propagation:** when each `code-reviewer` is dispatched, pass `GH_MODE=<value>` in its prompt; in MCP mode the reviewer calls `mcp__github__add_issue_comment` directly. The "each reviewer posts to the PR" invariant holds in both modes; this command is the documented divergence from `/ship`'s orchestrator-only-writes override. |
| `gh pr edit <P> --add-label review-cycle-complete` | Read current labels via `mcp__github__pull_request_read` (method=`get`) → `labels[]`, compute the new full set (append `review-cycle-complete` if absent), then `mcp__github__update_pull_request` (labels=`[<new full set>]`). MCP overwrites; compute the union explicitly. |
| `gh label create review-cycle-complete --color ... --description ...` (idempotent label setup) | Detect with `mcp__github__get_label`; if 404, the existing fallback note is to ask the operator to create it once via `gh label create` locally. The MCP server does not expose label-creation; this is a one-time setup gap, not a per-run blocker. |

The CI/state-changing-via-reviewer divergence from `/ship` is preserved in both modes — reviewers post findings directly to the PR, and the orchestrator's only writes are the consolidated summary comment and the `review-cycle-complete` label.

This command reuses the `code-reviewer` subagent definition — it does NOT redefine review heuristics. Read `AGENTS.md` § "code-reviewer" first; that file is the source of truth for severity vocabulary and review focus areas.

## Runtime model (read this first)

`/review-cycle-to-pr` runs as a single Claude Code (or Codex) turn loop. PRs are processed **sequentially** — only one PR is in active review at a time so the GitHub timeline stays readable and rate-limit pressure is bounded. Within a single PR, the 3-reviewer fan-out at Step 3 is genuinely parallel (three Agent calls in a single assistant message, run concurrently).

Crucial divergence from `/ship` Step 5c: in `/ship`, reviewers are instructed to RETURN findings to the orchestrator only — the orchestrator posts the per-reviewer comments. In `/review-cycle-to-pr`, **each reviewer posts its own findings directly to the PR as a review comment**, per the issue body's contract: "each reviewer posts its own findings to the PR as a review comment." The orchestrator only posts the consolidated summary at the end and applies the label. This is the opposite of the `/ship` return-only override and must be made explicit in every reviewer prompt so reviewers do not silently inherit the `/ship` default.

This command never pushes code, never opens or closes PRs, never merges. Its only state-changing actions are `gh pr comment` (the consolidated summary; reviewer comments come from the subagents themselves) and `gh pr edit --add-label review-cycle-complete`.

## Step 0 — Parse arguments

Argument grammar:

- One or more positive integers, each treated as a PR number.

Reject:

- Empty argument list (must specify at least one PR).
- Any argument that does not match `^[0-9]+$` (no flags, no negative numbers, no comma-separated lists — space-separated only).
- Zero or negative integers.

State the parsed `PRS=[...]` list in your first user-facing line.

## Step 1 — Validate PRs

For each PR number, validate it exists and is open:

```bash
gh pr view <PR> --json number,state,isDraft,headRefName,baseRefName,title,url
```

Drop PRs that are already merged or closed (warn the user, list them in the final report under "Skipped"). Continue with the remaining open or draft PRs.

If `gh` returns a non-zero exit (network, auth, rate limit), stop the run and surface the error — Step 5 still writes a partial report covering what completed.

## Step 2 — Per-PR loop (sequential across PRs, parallel reviewers within a PR)

For each surviving PR `P`, run Steps 3 → 4 in order. Do NOT advance to the next PR until both steps complete (or the PR is marked failed and skipped, see Stop conditions).

State the current `PR=<P>` and a one-line title summary as you start each PR so the user can follow progress.

## Step 3 — Spawn 3 reviewers in parallel (single Agent-tool message)

Spawn THREE `code-reviewer` subagents in **a single Agent tool message** so they run concurrently in Claude Code. Codex executing this command should also issue a single batched call where possible; if Codex serialises, that is acceptable as long as no reviewer reads another reviewer's output (each must get a fresh codename and the `do-not-read` instruction below).

Focus map (fixed at 3, no reviewer-count flag in this command):

| Reviewer | Focus |
|----------|-------|
| Reviewer 1 | **Code quality & architecture** — refactoring opportunities, naming, abstraction layers, duplication, single-responsibility, Kotlin idioms vs Java holdovers |
| Reviewer 2 | **Bugs & security** — logic errors, edge cases, null/nullability, OWASP categories, race conditions, input validation, secret hygiene |
| Reviewer 3 | **Tests & regression** — coverage of new/changed logic, behavior preservation, backward compatibility, Realm migration safety, billing regression, flake risk |

Each reviewer receives the canonical reviewer template from
`AGENTS.md` § [Reviewer Rubric (canonical)](../../AGENTS.md#reviewer-rubric)
with `<PR>` and `<FOCUS>` filled in (`<FOCUS>` = the focus row above), plus
this command's posting-contract REVERSAL of the `/ship` Step 5c return-only
rule. The rubric is the source of truth for the prompt body (focus, PR-head-
SHA verification block, vocabulary, return format, no-cross-reading rule);
the override below is the only addition required here:

```
You are reviewing PR #<P> for SmartInventory.
Codename: REVIEW-CYCLE-<P>-<UTC_TIMESTAMP>-<1|2|3>
Focus: <Reviewer focus from the table above>

POSTING CONTRACT (this command's divergence from /ship):
You MUST post your findings DIRECTLY to PR #<P> as a review comment via
`gh pr comment <P> --body "..."` (or equivalent). Do NOT return findings
silently to the orchestrator and rely on the orchestrator to post them —
that is the /ship Step 5c override (see AGENTS.md § Reviewer Rubric
(canonical) "Orchestrator-only-writes override") and it does NOT apply
here. The issue body for this command (#261) requires each reviewer to
post its own comment so the PR timeline shows three independent reviewer
entries before the consolidated summary.

Comment body format:

  ## Review (focus: <focus>) — codename `REVIEW-CYCLE-<P>-<UTC_TIMESTAMP>-<N>`

  **Verdict:** LGTM | LGTM with suggestions | Needs fixes

  **Findings**

  | Severity | File:Line | Description | Suggested fix |
  | -------- | --------- | ----------- | ------------- |
  | BLOCKER  | …         | …           | …             |
  | SUGGESTION (major) | … | …         | …             |
  | SUGGESTION (minor) | … | …         | …             |
  | NIT      | …         | …           | …             |

  You MUST emit `major` / `minor` explicitly in the SUGGESTION rows of the
  comment table — the same major/minor split the COUNTS line uses. This
  keeps the comment table and the orchestrator-facing histogram in lockstep.
  (omit severity rows with no findings; if no findings at all, write
   "No findings — PR looks clean from this focus.")

After posting, return to the orchestrator the SAME findings in this
machine-readable block so the orchestrator can build the consolidated
summary without re-parsing GitHub:

  CODENAME: REVIEW-CYCLE-<P>-<UTC_TIMESTAMP>-<N>
  FOCUS: <Reviewer 1 focus | Reviewer 2 focus | Reviewer 3 focus>
  VERDICT: LGTM | LGTM with suggestions | Needs fixes
  COUNTS: blocker=<n> major=<n> minor=<n> nit=<n>
  (COUNTS values use the same major/minor split as the comment table —
   the reviewer commits to one classification per finding rather than
   maintaining two parallel taxonomies.)
  CLEAN_AREAS: <comma-separated areas the reviewer explicitly checked
              and found clean, e.g. "billing flows, Realm field names">
  FINDINGS:
    <severity> | <file:line> | <description> | <suggested fix>
    …

Independence: per AGENTS.md § Reviewer Rubric (canonical) "Independent-
review rule (no cross-reading)" — your review must be fully independent
of the other two reviewers on PR #<P>.

Implementation: when reading PR context, prefer `gh pr view <P> --json
title,body,commits,files,headRefName,baseRefName` (no comments) plus
`gh pr diff <P>` for the diff. If you DO read PR comments for any reason,
apply the rubric's codename-prefix isolation pin — skip every comment
whose body contains the substring `REVIEW-CYCLE-<P>-` (note the trailing
hyphen and that this is the PR-scoped form, NOT the per-cycle
`REVIEW-CYCLE-<P>-<UTC_TIMESTAMP>-` form). The PR-scoped form covers BOTH
the current cycle's three siblings AND every prior `/review-cycle-to-pr`
cycle on this PR (each cycle's codenames all begin with
`REVIEW-CYCLE-<P>-`). The shared codename prefix is the canonical
isolation pin — do NOT rely on first-line heading match alone, because
previous cycles' summary comments and reviewer comments use the same
`## Review (focus: …)` format.

Tradeoff: the PR-scoped substring will also skip any unrelated comment
that quotes a `REVIEW-CYCLE-<P>-…` codename (e.g., a human commenter
referencing a prior review). This is acceptable: the reviewer can still
see the diff and the PR description, and quoting a sibling reviewer's
codename in a comment usually means that comment is itself a meta-
comment about review correctness, which is exactly the content we want
to keep out of an independent reviewer's context.
```

Generate a fresh codename per reviewer (`REVIEW-CYCLE-<P>-<UTC_TIMESTAMP>-1`, `…-2`, `…-3`).

`major`, `minor`, `nit` correspond to: BLOCKER ≡ Must fix (severity=blocker), SUGGESTION ≡ Should fix (severity=major or minor depending on the reviewer's judgment of remediation urgency), NIT (severity=nit). The histogram in Step 4 sums these counts across all three reviewers.

Severity-mapping clarification for the prompt: when a reviewer emits `SUGGESTION` they MUST also pick `major` vs `minor` for the histogram counts. A SUGGESTION is `major` if not addressing it would degrade maintainability, robustness, or test confidence in a meaningful way; it is `minor` otherwise. NITs are always counted as `nit`. BLOCKER is always counted as `blocker`.

## Step 4 — Consolidated summary comment + label

After all 3 reviewers finish (each has posted its own PR comment AND returned the machine-readable block), the orchestrator builds and posts ONE consolidated summary comment.

Severity histogram is the column-wise sum of each reviewer's `COUNTS` line:

```
blocker = sum of blocker across R1+R2+R3
major   = sum of major   across R1+R2+R3
minor   = sum of minor   across R1+R2+R3
nit     = sum of nit     across R1+R2+R3
```

Merge recommendation rule:

| Condition | Recommendation |
|-----------|----------------|
| Any reviewer's `VERDICT` is `Needs fixes` OR `blocker > 0` | ❌ block |
| `blocker == 0` AND `major + minor > 0` | ⚠️ request changes |
| `blocker + major + minor == 0` AND any reviewer's `VERDICT` is `LGTM with suggestions` (NITs only) | ✅ approve (with cosmetic nits) |
| `blocker + major + minor == 0` AND all 3 reviewers `LGTM` (no NITs) | ✅ approve |

Note: a reviewer returning `LGTM` while still emitting `minor` findings
downgrades the recommendation to ⚠️ via the count clause — this is
intentional. Verdict strings are advisory; the histogram is the source
of truth for the recommendation. SUGGESTIONs (`major`/`minor`) are
gated like blockers per the reviewer rubric (`AGENTS.md` § Reviewer
Rubric): a non-zero `major + minor` count is never an `approve`. This
command is review-only (no merge), so resolution/deferral is the
operator's follow-up; it does not approve while any SUGGESTION stands.

Post the consolidated summary:

```bash
gh pr comment <P> --body "$(cat <<EOF
## Review-Cycle Summary — PR #<P>

Codename: \`REVIEW-CYCLE-<P>-<UTC_TIMESTAMP>\`
Reviewers: 3 (code-quality, bugs+security, tests+regression)

### Severity histogram
| Blocker | Major | Minor | Nit |
| ------- | ----- | ----- | --- |
| \$BLOCKER | \$MAJOR | \$MINOR | \$NIT |

### Clean areas
\$CLEAN_AREAS_AGGREGATED

### Per-reviewer verdicts
- R1 (code quality): \$VERDICT_1 — \`<codename 1>\`
- R2 (bugs & security): \$VERDICT_2 — \`<codename 2>\`
- R3 (tests & regression): \$VERDICT_3 — \`<codename 3>\`

### Merge recommendation
\$RECOMMENDATION  (✅ approve / ⚠️ request changes / ❌ block)

This summary was posted by \`/review-cycle-to-pr\`. Each reviewer's full
findings are in the three PR comments immediately above this one.
EOF
)"
```

Then add the label:

```bash
gh pr edit <P> --add-label review-cycle-complete
```

If the label does not exist on the repo, fall back to:

    LABEL_LOG=$(mktemp -t label-create.XXXXXX) || LABEL_LOG=/tmp/label-create.$$.log
    gh label create review-cycle-complete --color BFD4F2 \
      --description "All reviewers in /review-cycle-to-pr have posted" \
      >"$LABEL_LOG" 2>&1
    LABEL_RC=$?

Treat any `gh label create` outcome where `LABEL_RC` is non-zero AND the
log file content contains the substring `already exists` as success
(the label exists, race-tolerant) and proceed to retry `gh pr edit
--add-label`. Use `grep -q "already exists" "$LABEL_LOG"` to check.
This avoids the pipeline-exit-status trap (`tee` always returns 0) and
gives every concurrent invocation its own private log file via `mktemp`,
eliminating the multi-user collision and symlink-attack risks of a
fixed `/tmp/label-create.log` path. If creation fails for another
reason (lacks repo admin, network), continue without the label and note
the omission in the final report (Step 5).

## Step 5 — Final report (printed to user)

After the queue drains, emit a terse report:

```
Review cycle complete.
PRs requested: <count>
PRs reviewed:  <count - skipped - failed>
Skipped (not open): <list>
Failed (reviewer error): <list with reason>
Per-PR results:
  - #<P> — recommendation=<approve|request changes|block> — blocker=<n> major=<n> minor=<n> nit=<n>
  - …
```

Include URLs to the consolidated summary comments where practical.

## Stop conditions

- A reviewer subagent fails to return its machine-readable block AND fails to post its PR comment (timeout, agent error) ⇒ mark THIS PR as failed in the per-PR result table, skip Step 4 for this PR, and continue with the next PR. Do NOT abort the whole run.
- Reviewer posted its PR comment BUT failed to return the machine-readable block (process crash after `gh pr comment`): the orchestrator falls back to re-fetching the comment via `gh pr view <P> --json comments` and parses the comment body using the same severity-table layout. If parsing succeeds, treat as a successful reviewer; if parsing fails, treat as failed reviewer per the existing rule.
- Reviewer returned the machine-readable block BUT `gh pr comment` failed (rate limit, transient 5xx): the orchestrator re-attempts the post on the reviewer's behalf with the comment body reconstructed from the returned block. If still failing after 3 retries with exponential backoff (2s/4s/8s), treat as failed reviewer.
- Partial reviewer failure (1 of 3 fails) ⇒ post the consolidated summary with the surviving reviewer outputs and a clear note: "Reviewer N (focus: ...) failed: <reason>. Histogram counts may be incomplete." Add the label only if at least 2 of 3 reviewers succeeded; otherwise skip the label and mark the PR as failed.
- `gh` returns `403: API rate limit exceeded` ⇒ stop processing further PRs. Print the rate-limit message and the per-PR result table built so far. The remaining PRs are listed under "Not started (rate-limited)" so the user can re-run later.
- `gh pr view` reports the PR is merged or closed ⇒ skip (already filtered at Step 1; this is the live re-check guard).
- User cancels.

Always print the final report on exit, even if partial.

## Safety invariants

- This command is read-only with respect to git: never `git commit`, `git push`, `git checkout`, `git merge`, `git rebase`, or any working-tree modification.
- This command is read-only with respect to PR state: never `gh pr merge`, `gh pr close`, `gh pr ready`, `gh pr review --approve`, or `gh pr review --request-changes`. The reviewers post regular comments only (`gh pr comment`), NOT formal `gh pr review` approvals — the human still owns the merge gate.
- The 3-reviewer divergence from `/ship` Step 5c (reviewers post their own comments) MUST be stated explicitly in every reviewer prompt at Step 3. A reviewer that silently inherits the `/ship` return-only contract is a contract violation; surface it in the PR's failed-reviewer note and skip the label.
- Reviewer subagents must NOT read other reviewers' output — same isolation invariant as `/ship` Step 5c and AGENTS.md step 9b. The prompt must include the explicit "skip every PR comment whose body contains the substring `REVIEW-CYCLE-<P>-`" rule when reading PR context (the PR-scoped codename pin, NOT the unreliable `## Review (focus: …)` heading match — see Step 3 for the rationale).
- The `review-cycle-complete` label is applied by the orchestrator only after Step 4 succeeds. Never pre-apply the label before reviewers finish.
- The consolidated summary posts AFTER the 3 reviewer comments so the timeline reads top-down: R1 → R2 → R3 → summary. Do NOT post the summary before the three Step-3 comments have completed.
- Codename format `REVIEW-CYCLE-<PR>-<UTC_TIMESTAMP>[-<N>]` is part of the audit trail; do not abbreviate.
- Concurrent `/review-cycle-to-pr` invocations on the same PR are unsupported in this version; the human is responsible for serialising. The 3-comment + summary + label sequence per PR has no orchestrator-side mutex (unlike `/ship` Step 5f.1's `mkdir`-based merge lock). A future change may add a per-PR `mkdir /tmp/review-cycle-pr-<P>.lock` mutex; until then, two concurrent runs on the same PR can interleave comments and produce a malformed timeline.
