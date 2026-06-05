---
description: Iterate on PR review comments until CI passes and reviewers are satisfied
allowed-tools: Bash(gh:*), Bash(git:*), Bash(./gradlew:*), Bash(command:*), Read, Edit, Write, Agent, mcp__github__pull_request_read, mcp__github__list_pull_requests, mcp__github__add_issue_comment
argument-hint: [PR number, defaults to current branch's PR]
---

You are running an iterative PR review loop for SmartInventory.

## Runtime detection (gh vs GitHub MCP)

Run via the Bash tool:

```bash
if command -v gh >/dev/null 2>&1; then echo cli; else echo mcp; fi
```

- Output `cli` ⇒ `GH_MODE=cli`. Run the `gh ...` commands below via the Bash tool.
- Output `mcp` ⇒ `GH_MODE=mcp`. Call the GitHub MCP tools below instead.

IMPORTANT: Do NOT embed bare bang-backtick `gh` markdown placeholders (the form `<bang><backtick>gh ...<backtick>`) in this file — the preprocessor expands them before Step 0 runs, defeating the GH_MODE gate. Always issue `gh` calls through the Bash tool, gated by the detected `GH_MODE`.

State the mode in the first user-facing line. Mapping:

| gh CLI | GitHub MCP equivalent |
|---|---|
| `gh pr view --json number,headRefName,state` (auto-detect from branch) | `mcp__github__list_pull_requests` (state=`open`, head=current branch); take the first match. |
| `gh pr view $PR --comments` | `mcp__github__pull_request_read` (method=`get_comments`) — issue-conversation timeline. Plus `mcp__github__pull_request_read` (method=`get_review_comments`) for inline review threads if you need both. |
| `gh pr view $PR --json reviews,reviewDecision,statusCheckRollup` | `mcp__github__pull_request_read` (method=`get`) returns reviews and review decision; `statusCheckRollup` field has no direct MCP equivalent (see degrade below). |
| `gh pr comment $PR --body "..."` | `mcp__github__add_issue_comment` |

### CI-related degrade (`gh pr checks`, `gh run list`, `gh run view --log-failed`)

`gh pr checks $PR`, `gh pr checks --watch`, `gh run list`, and `gh run view --log-failed` have **no MCP equivalent today**. Under `GH_MODE=mcp`:

- **CI watch loop:** cannot poll. Convert to a single-check mode — on each iteration, fetch the latest review/comment state via MCP; do NOT block waiting on CI. The operator (or the next webhook event) is responsible for re-running `/pr-loop` once CI state advances.
- **Failed-log fetch:** if a reviewer or comment refers to a CI failure, surface a one-line note: `CI log fetch unavailable in MCP mode — open the GitHub Actions tab for run details`. Do NOT attempt to diagnose without the log; ask the operator to paste the relevant excerpt or re-run locally.
- The fix-and-push half of the loop (read comment → make change → `git push` → wait) still runs; only the CI-polling half degrades. The loop's correctness rests on reviewer comments and `mergeable_state`, both of which MCP can return.

## Language

All committed/published artifacts (commits, branch names, PR/issue titles and bodies, comments, file contents, slash command definitions) MUST be written in English. Free-form chat with the user may stay in any language. See `AGENTS.md` § "Language Policy". This applies to every PR comment posted by this loop (review-round summaries, fix-up commit messages, the Step 10 summary comment).

## Step 1 — Find the PR
- If $1 given, use that PR number. Otherwise resolve via the runtime mode:
  - `GH_MODE=cli`: run via the Bash tool: `gh pr view --json number,headRefName,state`
  - `GH_MODE=mcp`: call `mcp__github__list_pull_requests` (owner=`berkayturanci`, repo=`smartinventory`, state=`open`, head=current branch from `git rev-parse --abbrev-ref HEAD`); take the first match.
- If no PR exists for the current branch, halt and report — do not proceed.
- **Workspace isolation check** (`AGENTS.md` § "Workspace Isolation (AI agents)"): `/pr-loop` runs `git commit` / `git push` against the current working tree, so it MUST be invoked from a linked worktree, not the main worktree (the user's primary checkout). Detect with `git rev-parse --git-dir`: main worktree returns `.git`; linked worktrees return an absolute path containing `/.git/worktrees/<name>`. If the value is `.git`, ABORT and tell the user to re-run from the PR's worktree (find it with `git worktree list`). When `/pr-loop` itself creates a worktree for the PR (e.g. operator invokes the skill against an open PR that does not yet have one locally), the path MUST follow the #931 nested convention: `git worktree add worktrees/pr-<N> <branch>`. The legacy sibling form (`../smartinventory-<N>`) is deprecated. This check is portable across OSes/home directories and immune to symlink trickery. Step 4's commits and Step 6's push would otherwise mutate the user's primary tree.

## Step 2 — Read everything

If `GH_MODE=cli`, run via the Bash tool:

- `gh pr view $PR --comments`
- `gh pr view $PR --json reviews,reviewDecision,statusCheckRollup`
- `gh run list --branch $(git branch --show-current) --limit 3 --json status,conclusion,name`
- If CI failing: `gh run view --log-failed | tail -150`

If `GH_MODE=mcp`:

- Call `mcp__github__pull_request_read` (method=`get_comments`) and `mcp__github__pull_request_read` (method=`get_review_comments`) for the conversation and inline-review timelines.
- Call `mcp__github__pull_request_read` (method=`get`) for reviews and review decision. `statusCheckRollup` and the CI-failed-log fetch fall under the **CI-related degrade** above — surface the documented one-line note instead of attempting to fetch run data.

## Step 3 — Categorize feedback
Group review comments into:
- **Must-fix** — correctness, Realm rules, billing safety, security
- **Should-fix** — style, naming, perf
- **Skip** — opinion differences, wontfix (note reason)
- **Reply-only** — questions needing text reply, not code change

## Step 4 — Fix and reply
For each must-fix and should-fix:
1. Read the relevant source file
2. Make the minimal targeted fix
3. Run relevant tests: `cd android && ./gradlew test --no-daemon 2>&1 | tail -30`
4. Commit: `git commit -m "fix: address review comment — <short description>"`

## Step 5 — Self-review before push (AGENTS.md step 6 — mandatory before push)
Read `git diff origin/develop...HEAD` from top to bottom and verify:
- **Scope**: only files within the issue/CI fix scope changed?
- **Security**: XSS, SQL injection, hard-coded credentials?
- **Dead code / stale refs**: any leftover traces from deleted/moved code?
- **CI prediction**: for every changed file, answer "what would make CI fail here?"
- **Test coverage**: does changed logic have new or existing test coverage?

If self-review finds an issue (out-of-scope change, security risk, CI risk) → return to Step 4, fix, re-commit, then restart Step 5.

## Step 6 — Push
!`git push`

## Step 7 — 3 independent code-reviewer agents (parallel — AGENTS.md step 9b)
Required after every code-change push — CI fixes included. Spawn 3 code-reviewer agents
in PARALLEL in a single Agent-tool message.

Focus split (kept here for at-a-glance readability of this command):

| Agent | Focus |
|-------|-------|
| Agent A | logic correctness, null safety, Kotlin/Java interop (ordinal, lateinit…) |
| Agent B | platform/lifecycle/Realm/API compat, minSdk guards, threading |
| Agent C | test coverage, docs gate (CLAUDE.md updated?), scope creep, CI prediction |

See `AGENTS.md` § [Reviewer Rubric (canonical)](../../AGENTS.md#reviewer-rubric)
for verdict vocabulary (BLOCKER ≡ Must fix), the PR-head-SHA verification
rule, the no-cross-reading rule, and the return format. The
orchestrator-only-writes override (reviewers MUST NOT call any GitHub write
API; they return findings only — this command, like `/ship`, posts the
per-reviewer comments itself at Step 10) comes from there too.

Substitute `<PR>`, `<FOCUS>`, and `<PR_HEAD_SHA>` in the rubric template
before sending each prompt. Generate a fresh codename per reviewer.

## Step 8 — Re-check CI

If `GH_MODE=cli`, run via the Bash tool:

- `gh pr checks $PR`

Confirm CI is green. If checks are still running, wait and re-run. Do not proceed to Step 9 with red CI.

If `GH_MODE=mcp`, CI polling is unavailable (see the **CI-related degrade** above). Skip the check, leave the loop in single-pass mode, and document the deferral in the Step 10 summary.

## Step 9 — Collect findings and close loop
- **BLOCKER found** → return to Step 4, fix, re-commit; then run Steps 5 → 6 → 7 → 8 → 9 again
- **SUGGESTION (Should-fix) found** → gated the same as a BLOCKER (operator decision 2026-05-31): every SUGGESTION MUST be applied (return to Step 4, fix, re-commit, re-run Steps 5 → 6 → 7 → 8 → 9) or be explicitly user-deferred (recorded as a tracked GitHub issue AND surfaced to the user) before exit. The loop may NOT exit on "Should-fix resolved" while any SUGGESTION remains unapplied and not user-deferred.
- **No blockers AND every SUGGESTION applied (or explicitly user-deferred) AND CI green** → exit loop. NITs SHOULD be applied where reasonable; an unapplied NIT should be noted in the Step 10 summary but does NOT gate the exit.

CI green is required on ALL exit paths. Never exit this loop with CI red.

## Step 10 — Summary
Post a summary comment on the PR listing:
- What was addressed (with file refs)
- What was intentionally skipped and why
- Review round results (Agent A / B / C verdicts)

Then post the comment via the runtime mode:

- `GH_MODE=cli`: run via the Bash tool: `gh pr comment $PR --body "<summary>"`
- `GH_MODE=mcp`: call `mcp__github__add_issue_comment` (owner=`berkayturanci`, repo=`smartinventory`, issue_number=`$PR`, body=`<summary>`).
