---
description: Delegate a GitHub issue to the right local subagent for implementation
allowed-tools: Bash(gh:*), Bash(git:*), Bash(date:*), Bash(command:*), Read, mcp__github__issue_read, mcp__github__add_issue_comment
argument-hint: <issue-number>
---

You are orchestrating the implementation of a GitHub issue.

## Language

All committed/published artifacts (commits, branch names, PR/issue titles and bodies, comments, file contents, slash command definitions) MUST be written in English. Free-form chat with the user may stay in any language. See `AGENTS.md` § "Language Policy".

## Step 0 — Detect environment

Run via the Bash tool:

```bash
if command -v gh >/dev/null 2>&1; then echo cli; else echo mcp; fi
```

- Output `cli` ⇒ `GH_MODE=cli`. Run the `gh ...` commands below via the Bash tool.
- Output `mcp` ⇒ `GH_MODE=mcp`. Call the GitHub MCP tools below instead.

IMPORTANT: Do NOT embed bare bang-backtick `gh` markdown placeholders (the form `<bang><backtick>gh ...<backtick>`) in this file — the preprocessor expands them before Step 0 runs, defeating the GH_MODE gate. Always issue `gh` calls through the Bash tool, gated by the detected `GH_MODE`.

Mappings used below: `gh issue view` → `mcp__github__issue_read` (method=`get`); `gh issue comment` → `mcp__github__add_issue_comment`.

## Step 1 — Fetch the issue
- `GH_MODE=cli`: run via the Bash tool: `gh issue view $ARGUMENTS --json number,title,body,labels`
- `GH_MODE=mcp`: call `mcp__github__issue_read` (method=`get`, owner=`berkayturanci`, repo=`smartinventory`, issue_number=`$ARGUMENTS`). The returned JSON contains `number`, `title`, `body`, and `labels` (plus extra fields you can ignore).

## Step 2 — Check for existing branch
- !`git branch -r | grep "issue-$ARGUMENTS"`
- If a branch already exists, report it and ask the human whether to continue on it or start fresh.

## Step 3 — Choose the implementation subagent

- Use `android-developer` when the issue touches `android/**`, Realm, billing, Kotlin conversion, SDK/build, or Android UI.
- Use `web-developer` when the issue touches `web/**`, Firebase Hosting, Firebase Functions, Web CI, or Web docs.
- If the issue touches shared Firebase schemas plus a platform, include `shared/schema/firebase/` in the task context.

## Step 4 — Create an agent run codename

Follow `AGENTS.md` → "Agent Run Codenames".

Set:
- `ROLE_PREFIX=ANDROID` for `android-developer`
- `ROLE_PREFIX=WEB` for `web-developer`
- `UTC_TIMESTAMP=$(date -u +%Y%m%d-%H%M%S)`
- `CODENAME="$ROLE_PREFIX-$ARGUMENTS-$UTC_TIMESTAMP"`

Post a start comment on the issue before delegating:

```
Agent run started
Codename: `$CODENAME`
Agent: `<chosen-agent>`
Implementer system: `claude-code`
Branch: `feature/issue-$ARGUMENTS-[slug]`
```

Use `date -u +%Y%m%d-%H%M%S` to generate the timestamp, then post the start comment only after replacing `<CODENAME>`, `<chosen-agent>`, and `[slug]`:

- `GH_MODE=cli`:
  ```bash
  gh issue comment "$ARGUMENTS" --body "Agent run started
  Codename: \`<CODENAME>\`
  Agent: \`<chosen-agent>\`
  Implementer system: \`claude-code\`
  Branch: \`feature/issue-$ARGUMENTS-[slug]\`"
  ```
- `GH_MODE=mcp`: call `mcp__github__add_issue_comment` (owner=`berkayturanci`, repo=`smartinventory`, issue_number=`$ARGUMENTS`, body=the same multi-line string used in the cli branch).

### Paparazzi-specific routing

If the issue body mentions Paparazzi (recording baselines, screenshot tests, `recordPaparazziDebug`, layout PNG snapshots), the implementer MUST use `scripts/paparazzi-record-and-validate.sh <FQ-test-class> [--commit]` instead of invoking `./gradlew recordPaparazziDebug` directly. See `docs/development/paparazzi-validation-checklist.md` for the per-PNG validation criteria and the PASS/FAIL/UNCERTAIN output schema, and `docs/testing/POLICY.md` (Recording baselines: manual vs autonomous) for when this path applies.

## Step 5 — Delegate to the chosen subagent

Spawn the chosen subagent with this context:

```
Task: Implement GitHub Issue #$ARGUMENTS
Agent run codename: <CODENAME>

Issue title: [from Step 1]
Issue body: [from Step 1]

Instructions:
1. Read `AGENTS.md`, `CLAUDE.md`, and the platform `CLAUDE.md`.
2. **Workspace isolation (mandatory):** before any code-modifying work, create a git worktree off `origin/develop` and perform every edit, build, and push from inside it — never mutate the user's primary checkout. Per `AGENTS.md` § "Workspace Isolation (AI agents)" (path convention per #931 — nested under repo root, never a sibling):
   ```bash
   git fetch origin develop --quiet
   git worktree add -b feature/issue-$ARGUMENTS-[slug] worktrees/issue-$ARGUMENTS origin/develop
   cd worktrees/issue-$ARGUMENTS
   ```
   Run the gates below from that path. When the PR is merged, clean up with `git worktree remove worktrees/issue-$ARGUMENTS --force` (the `--force` covers untracked build artefacts that may remain in the worktree). The `worktrees/` directory is gitignored, so the worktree itself never appears in `git status` of the primary checkout.
3. Implement all acceptance criteria with focused commits.
4. Run the applicable gates from inside the worktree:
   - Android: `cd android && ./gradlew test lint --no-daemon -Pcom.google.firebase.perf.instrumentationEnabled=false`
   - Realm changes: `cd android && ./gradlew test --tests "*Migration*" --no-daemon`
   - Billing changes: `cd android && ./gradlew test --tests "*Billing*" --no-daemon`
   - Web config changes: parse `web/firebase.json`, `web/database.rules.json`, `web/functions/package.json`, and `web/functions/package-lock.json`
   - Web dependency changes: `cd web/functions && npm ci`
5. Include the codename in commits/PR body/final summaries when practical.
6. Return: codename, branch/commit, files changed, test results, docs impact, anything needing device/Firebase verification.
```

## Step 6 — Report back
After the subagent completes, summarize:
- Codename
- Branch/commit
- What was implemented
- Test gate results
- Anything that needs manual verification

Post a completion comment to the issue with the same codename and the test results if the subagent did not already do so.
