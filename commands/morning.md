---
description: Daily morning briefing — CI status, open issues/PRs, priorities
allowed-tools: Bash(gh:*), Bash(python3:*), Bash(date:*), Bash(command:*), Bash(test:*), Bash(mkdir:*), Bash(cat:*), Bash(jq:*), Read, Grep, mcp__github__list_issues, mcp__github__list_pull_requests, PushNotification, schedule
argument-hint: (no args)
---

You are running the morning briefing. Output one structured report. Be terse.

## Language

The morning brief itself is written to `docs/reports/<DATE>-morning.md` and surfaces overnight `/ship` deferrals — write everything in English (see `AGENTS.md` § "Language Policy"). Free-form chat with the user may stay in any language.

## Step 0 — Detect environment

The signal-pulling step below has two code paths: a local path that uses the `gh` CLI, and a web/sandbox path that uses the GitHub MCP server tools. Detect once at the top via the Bash tool and remember the result for every signal:

```bash
if command -v gh >/dev/null 2>&1; then echo cli; else echo mcp; fi
```

- Output `cli` ⇒ `GH_MODE=cli`. Run the `gh ...` commands in Step 2 via the Bash tool as listed.
- Output `mcp` ⇒ `GH_MODE=mcp`. Run the GitHub MCP tool calls in Step 2 instead. State `Data source: GitHub MCP (no gh CLI in this runtime)` as a one-line note in the brief so the operator knows which path produced the data.

IMPORTANT: Do NOT embed bare bang-backtick `gh` markdown placeholders (the form `<bang><backtick>gh ...<backtick>`) in this file — the preprocessor expands them before Step 0 runs, defeating the GH_MODE gate. Always issue `gh` calls through the Bash tool, gated by the detected `GH_MODE`.

GitHub Actions runs are not available via the MCP server tools we have today. In `GH_MODE=mcp`, the **CI Health** section degrades to a one-line note: `CI Health unavailable in this runtime (no gh CLI, no MCP equivalent for Actions runs).` Do NOT error.

Also resolve the date and cutoff timestamp:

```bash
DATE=$(TZ='Etc/GMT-3' date +%Y-%m-%d)
CUTOFF_ISO=$(TZ='Etc/GMT-3' date -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || TZ='Etc/GMT-3' date -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)
```

`CUTOFF_ISO` is used in Step 1.5 (completed-work summary) and Step 2 (shipped-work queries).

## Step 1 — Read deferrals, last session, and priorities

### 1a — Persistent deferral store (cross-session)

Read `.claude/deferrals.json` if it exists. This file is written by `/ship` when issues are deferred outside the UTC+3 09:00–23:59 merge window and **persists across sessions** (unlike the session-local `morning-merge-queue-<DATE>.md` files). Format:

```json
[
  {
    "issue": 42,
    "pr": 99,
    "title": "…",
    "reason": "window:closed",
    "blocker": false,
    "tester_verdict": "LGTM",
    "deferred_at": "2026-05-23T23:45:00Z"
  }
]
```

After reading, **clear the file** by writing `[]` to it (the items will be surfaced in the brief; they are consumed on read). Use the Bash tool:

```bash
DEFERRALS_FILE=".claude/deferrals.json"
if [[ -f "$DEFERRALS_FILE" ]]; then
  DEFERRALS_JSON=$(cat "$DEFERRALS_FILE")
  echo '[]' > "$DEFERRALS_FILE"
else
  DEFERRALS_JSON='[]'
fi
```

### 1b — Legacy session-local queue (backward compatibility)

Also read `docs/reports/morning-merge-queue-<DATE>.md` (where `<DATE>` is today in UTC+3) if it exists. This file may have been written by an older `/ship` version that did not yet use the persistent store. Surface its contents in the deferrals section alongside the persistent-store items. Use the Bash tool to test for existence and read:

```bash
LEGACY_QUEUE_FILE="docs/reports/morning-merge-queue-${DATE}.md"
test -f "$LEGACY_QUEUE_FILE" && cat "$LEGACY_QUEUE_FILE" || echo "(none)"
```

### 1c — Last session and priorities

- Read the last entry in `.claude/sessions.md` if it exists.
- Read `.claude/priorities.md` if it exists. If the file was last modified 5+ days ago (check via `Bash: stat -f %m` on macOS / `stat -c %Y` on Linux), flag it as stale — it will still appear in the brief as a "Manual focus" subsection rather than driving the primary priority list (see Step 2.5).

## Step 1.5 — Completed-work summary (shipped since last brief)

Determine the cutoff: find the most recent prior brief via Bash and extract the date from its filename (format `YYYY-MM-DD-morning.md`). Convert that date to an ISO-8601 timestamp (start of that day, UTC+3). Use the Bash tool:

```bash
PREV_BRIEF=$(ls -t docs/reports/*-morning.md 2>/dev/null | head -2 | tail -1)
if [[ -n "$PREV_BRIEF" && "$PREV_BRIEF" != *"$DATE"* ]]; then
  # Extract date from filename, e.g. "docs/reports/2026-05-23-morning.md" → "2026-05-23"
  PREV_DATE=$(basename "$PREV_BRIEF" | sed 's/-morning\.md//')
  CUTOFF_ISO="${PREV_DATE}T09:00:00Z"
fi
# If no prior brief found, CUTOFF_ISO remains as set in Step 0 (24h ago)
```

Query GitHub for work completed since the cutoff. Run in parallel:

If `GH_MODE=cli`:

```bash
# Issues closed since cutoff
gh issue list --state closed --limit 20 --json number,title,closedAt,labels \
  --jq "[.[] | select(.closedAt >= \"$CUTOFF_ISO\")]"

# PRs merged since cutoff
gh pr list --state merged --limit 20 --json number,title,mergedAt,headRefName \
  --jq "[.[] | select(.mergedAt >= \"$CUTOFF_ISO\")]"
```

If `GH_MODE=mcp`:

- `mcp__github__list_issues` with `state=closed`, `perPage=20` — then filter client-side by `closedAt >= CUTOFF_ISO`.
- `mcp__github__list_pull_requests` with `state=closed`, `perPage=20` — then filter client-side by `mergedAt >= CUTOFF_ISO`.

Surface results in the `Shipped since last brief` section (see Step 3). If both queries return empty, omit the section.

## Step 2 — Pull live signals (run in parallel)

Issue every call below through a tool — Bash for `gh ...`, the named MCP tool otherwise. Make all calls for a given mode in parallel.

### GitHub Status

If `GH_MODE=cli`, run via Bash:
- `gh issue list --state=open --label=bug --limit=10`
- `gh pr list --state=open`
- `gh run list --limit=5 --json status,conclusion,name,headBranch,createdAt`

If `GH_MODE=mcp`, call:
- `mcp__github__list_issues` with `owner=berkayturanci`, `repo=smartinventory`, `state=open`, `labels=["bug"]`, `perPage=10`.
- `mcp__github__list_pull_requests` with `owner=berkayturanci`, `repo=smartinventory`, `state=open`.
- GitHub Actions runs: unavailable — note in CI Health section below.

### Active Fires (run in parallel)

If `GH_MODE=cli`, run via Bash:
- `gh issue list --state open --label severity:blocker`
- `gh issue list --state open --label alert:crash`
- `gh issue list --state open --label alert:review`
- `gh run list --workflow="Monitor — Firebase Analytics & Play Console" --limit=1 --json status,conclusion,createdAt,url`

If `GH_MODE=mcp`, call:
- `mcp__github__list_issues` with `owner=berkayturanci`, `repo=smartinventory`, `labels=["severity:blocker"]`, `state=open`.
- `mcp__github__list_issues` with `owner=berkayturanci`, `repo=smartinventory`, `labels=["alert:crash"]`, `state=open`.
- `mcp__github__list_issues` with `owner=berkayturanci`, `repo=smartinventory`, `labels=["alert:review"]`, `state=open`.
- Monitor workflow run: unavailable — note in CI Health section below.

### CI Health

If `GH_MODE=cli`, run via Bash:
- `gh run list --limit=1 --json status,conclusion,name,headBranch`
- If the last run failed: `gh run view --log-failed | tail -100`

If `GH_MODE=mcp`:
- Emit a single line in the report: `CI Health unavailable in this runtime (no gh CLI, no MCP equivalent for Actions runs).` Do NOT attempt to fetch run data.

### Stale PRs (run in parallel with the above)

If `GH_MODE=cli`:

```bash
STALE_CUTOFF=$(TZ='Etc/GMT-3' date -v-3d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || TZ='Etc/GMT-3' date -d '3 days ago' +%Y-%m-%dT%H:%M:%SZ)
gh pr list --state open --json number,title,updatedAt,author \
  --jq "[.[] | select(.updatedAt < \"$STALE_CUTOFF\")]"
```

If `GH_MODE=mcp`: call `mcp__github__list_pull_requests` with `state=open`, `perPage=30` — then filter client-side by `updatedAt < STALE_CUTOFF`.

### Ready-to-merge PRs (run in parallel)

Identify open PRs that are review-approved and CI-green. These are high-priority merge candidates.

If `GH_MODE=cli`:

```bash
gh pr list --state open --json number,title,reviewDecision,statusCheckRollup,author \
  --jq '[.[] | select(.reviewDecision == "APPROVED")]'
```

If `GH_MODE=mcp`: call `mcp__github__list_pull_requests` with `state=open`, `perPage=30` — then filter client-side by `reviewDecision == "APPROVED"` where available.

### Open unassigned bugs (run in parallel)

If `GH_MODE=cli`:

```bash
gh issue list --state open --label bug --json number,title,assignees \
  --jq '[.[] | select(.assignees | length == 0)]'
```

If `GH_MODE=mcp`: call `mcp__github__list_issues` with `state=open`, `labels=["bug"]`, `perPage=20` — then filter client-side for items with empty `assignees`.

## Step 2.5 — Dynamic priority synthesis

Replace the static `priorities.md` as the primary source for the priority list. Compute a **ranked live priority list** from the signals gathered in Steps 2 and 1.5:

1. **Open `severity:blocker` issues** (from Active Fires query) — highest priority.
2. **PRs that are review-approved and CI-green** (Ready-to-merge query) — merge these next.
3. **Stale PRs** (no activity > 3 days, from Stale PRs query).
4. **Open `bug` issues with no assignee** (from unassigned bugs query).
5. **CI failures on develop/main** (from CI Health query) — if develop/main is red.

Format each item as: `[rank]. <type> — #N <title>` (e.g., `1. blocker — #42 App crashes on startup`).

If `.claude/priorities.md` exists and was modified within the last 24h (check via `stat`), append its contents as a **"Manual focus"** subsection below the dynamic list. If the file is stale (>24h but exists), note it as stale and still append as manual focus.

If `.claude/priorities.md` does not exist, skip the manual-focus subsection entirely.

## Step 2.6 — Production health cache (Crashlytics + Vitals, issue #783)

The daily prefetch jobs installed by `scripts/install-launchd-jobs.sh` write
two JSON files at 08:55 local:

- `docs/reports/cache/production-health-crashlytics-<YYYY-MM-DD>.json`
- `docs/reports/cache/production-health-vitals-<YYYY-MM-DD>.json`

Read both for **today's** UTC date. Treat the cache as **fresh** when the
file exists and its `generated_at` is within the last 24h.

- **Fresh cache** → render the `Production health` block per the ASCII
  mock in `docs/research/crashlytics-vitals-ingestion.md` §4, using the
  threshold table in §5 to assign `ok` / `watch` / `alert` badges.
  Deltas are computed against the 7-day mean of the Vitals timeline rows
  and against the Crashlytics report baseline. Surface cache age in minutes
  in the section header (`Source: ... (cache age: Nm)`).
- **Stale or missing cache** (file absent, or `generated_at` >24h old, or
  the file contains `{"dry_run": true}`) → render exactly one line:
  `Production health — no data — run scripts/fetch-crashlytics.sh and scripts/fetch-vitals.sh (see docs/operations/crashlytics-vitals-setup.md)`.
  Do NOT attempt a live fetch from inside `/morning`; the prefetch path is
  the contract.

Setup, IAM, and rotation are documented in
`docs/operations/crashlytics-vitals-setup.md`. Design rationale lives in
`docs/research/crashlytics-vitals-ingestion.md` — do not redesign here.

## Step 3 — Output format

```
Morning Brief — <date>
{data_source_banner}

--- Deferred /ship items  <- only show if any deferrals exist (persistent store OR legacy queue file)
- #<issue> PR #<pr> — <title> — <reason> — blocker: <yes|no> — tester: <verdict> — deferred: <timestamp>
- (If no deferred items in either source, omit this section entirely)

--- Shipped since last brief  <- only show if any closed issues or merged PRs found
Closed issues: #N <title> (closed <time>), ...
Merged PRs:    #N <title> (merged <time>), ...
(If empty, omit this section)

--- Active Fires  <- only show if any available query returns results
- severity:blocker — #N <title>
- alert:crash — #N <title>
- alert:review — #N <title>
- {monitor_row}
(If all available queries return empty, omit this section entirely)

{production_health_block}

--- Top Priorities (live)
1. <type> — #N <title>
2. <type> — #N <title>
3. <type> — #N <title>
(Manual focus: <priorities.md contents if file exists and fresh/stale>)

--- GitHub Status
Open bugs: N | Open PRs: M
{ci_health_line}
(If failed: one-line failure summary)

--- Open PRs
- #N <title> — <author> — <status>

--- Suggested focus today
- (one specific suggestion based on dynamic priorities + CI state)
```

### Conditional substitutions (apply based on `GH_MODE`)

Substitute the placeholders above with the row matching the detected mode. "Available queries" in the Active Fires omit-rule means the queries that actually executed under the current mode (4 in CLI mode, 3 in MCP mode because the Monitor workflow row has no MCP equivalent).

| Placeholder | `GH_MODE=cli` | `GH_MODE=mcp` |
|---|---|---|
| `{data_source_banner}` | omit the line entirely | `Data source: GitHub MCP (no gh CLI in this runtime)` |
| `{monitor_row}` | `Last Monitor — Firebase Analytics & Play Console run: failure on <createdAt> — <url>` (only if the run failed; otherwise omit) | `Monitor workflow status unavailable in MCP mode` |
| `{ci_health_line}` | `Last CI: <pass / fail> on <branch>` | `CI Health unavailable in this runtime (no gh CLI, no MCP equivalent for Actions runs).` |
| `{production_health_block}` | rendered per Step 2.6 (full block on fresh cache; one-line `no data` notice on stale/missing cache) | same — independent of `GH_MODE` |

## Step 4 — Save the brief

Write the report to `docs/reports/<YYYY-MM-DD>-morning.md` (create docs/reports/ if needed). `docs/reports/` is gitignored (see `docs/reports/README.md`) — do NOT `git add` or commit this file.

## Step 5 — Push notification

After writing the report, send a push notification so the user can see the brief without opening Claude Code manually. Use the `PushNotification` tool with:

- `title`: `Morning Brief — <DATE>`
- `message`: One-line summary: `N fires, M stale PRs, K deferred items, CI <pass/fail>`. Omit zero-count items. Example: `2 fires, 1 stale PR, CI pass`. If everything is clean: `All clear. CI pass.`

## Step 6 — Scheduling (first-run setup)

When `/morning` runs for the first time in a project (detect by checking whether a `docs/reports/*-morning.md` file exists from a prior run), offer to schedule itself for daily 09:00 execution:

```
Would you like to schedule /morning to run automatically at 09:00 (UTC+3) every day?
Reply "yes" to set up the schedule.
```

If the user confirms (or if `/morning --schedule` was passed as an argument), invoke the `/schedule` skill to register the recurring job:

- Cron expression: `0 9 * * *` (daily at 09:00 UTC+3 — the `/schedule` skill accepts UTC+3 timezone if supported; otherwise convert to UTC `0 6 * * *`).
- Command: `/morning`
- Description: `Daily morning brief for <project>`

If the `/schedule` skill is not available in this runtime, emit a one-line note:
`Scheduling unavailable in this runtime. Run /morning manually at 09:00 or set up a cron job: crontab -e → 0 6 * * * claude -p /morning`

---

## Contract for `/ship` deferral writes (informational — not executed by `/morning`)

When `/ship` defers a merge to the morning queue, it MUST append to `.claude/deferrals.json` in addition to (or instead of) the session-local `docs/reports/morning-merge-queue-<DATE>.md`. The JSON schema for each entry is:

```json
{
  "issue": <int>,
  "pr": <int>,
  "title": "<string>",
  "reason": "<string>",
  "blocker": <bool>,
  "tester_verdict": "<string>",
  "deferred_at": "<ISO-8601 UTC timestamp>"
}
```

`/morning` reads and clears this file at Step 1a. If the file does not exist, `/ship` must create it with the first entry as a JSON array. `.claude/deferrals.json` MUST be listed in `.gitignore` (or the `.claude/` directory must be gitignored) so deferral state is never committed.
