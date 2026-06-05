---
description: Daily morning briefing — production health (crashes/vitals/reviews/analytics) + GitHub status + priorities
allowed-tools: Bash(scripts/morning.sh:*), Bash(gh:*), Bash(date:*), Bash(command:*), Bash(test:*), Bash(stat:*), Bash(ls:*), Bash(sort:*), Bash(tail:*), Bash(cat:*), Read, Grep, Write, PushNotification, mcp__github__list_issues, mcp__github__list_pull_requests
argument-hint: (no args)
---

You are running the morning briefing for **SmartInventory** (repo `berkayturanci/smartinventory`). Output one structured report. Be terse, English only (AGENTS.md § Language Policy).

> IMPORTANT — sync resilience: the live signal logic lives in `scripts/morning.sh`,
> NOT inline in this file. This command is a thin agent-side wrapper. Earlier this
> file was clobbered by a `sync-to-ai-infra` push from another project (it began
> targeting `ingreview` + Supabase and lost the #783 Crashlytics/Vitals section).
> Keeping the pipeline in `scripts/morning.sh` makes the brief immune to that
> churn. If you ever see non-SmartInventory references reappear here, re-derive
> from `scripts/morning.sh` + `docs/development/morning-brief-spec.md`.

## Step 1 — Generate the data brief

Run the agent-neutral pipeline via Bash and capture its full markdown output:

```bash
scripts/morning.sh
```

This prints the production-health sections (Crashlytics crashes, Sentry web
crashes, Cloud Functions errors, Android Vitals, Play reviews, GA4 analytics
pulse) plus a GitHub Status block and a heuristic Suggested Focus. Wrappers
without credentials degrade to an `_unavailable_` note — that is expected until
the operator completes `docs/operations/monitoring-setup.md`; do NOT treat it as
an error.

If `scripts/morning.sh` is missing or `gh` is unavailable (web/sandbox runtime),
fall back to the GitHub MCP tools for the GitHub section and note
`Data source: GitHub MCP` at the top.

## Step 2 — Overnight `/ship` deferrals (surface at top)

Read `.claude/deferrals.json` if present — the cross-session deferral queue from
`/ship`. Surface any entries at the **top** as "🌙 Overnight /ship deferrals",
then clear the file by writing `[]`. (Legacy fallback:
`docs/reports/morning-merge-queue-<DATE>.md` in UTC+3.)

## Step 3 — Model-side enrichment

`scripts/morning.sh` gives you the data. Add the judgement the script cannot:

- **Refine Suggested Focus** to a ranked 3-item list, weighting: production
  fires (Crashlytics alert / Vitals breach / Sentry spike) > review-approved +
  CI-green PRs ready to merge > stale PRs (>3 days) > unassigned `type:bug`.
- **Triage new signals**: if a Crashlytics group shows ≥10 impacted users or a
  Vitals metric is over the Play threshold and no matching GitHub issue exists,
  note it — `scripts/auto-issue-from-vitals.sh` (#784) is the automated path,
  but flag anything it would miss.
- Cross-reference the Active Fires list against the new crash/error signals.

## Step 4 — Output, save, notify

Emit the brief in this order: 🌙 Deferrals (if any) → `scripts/morning.sh`
output (Bug Reports → Vitals → Reviews → Analytics → GitHub) → 🎯 refined
Suggested Focus.

Save to `docs/reports/<YYYY-MM-DD>-morning.md` (gitignored — do NOT `git add`).

Fire a push notification:

```
PushNotification(
  title: "SmartInventory morning brief — <YYYY-MM-DD>",
  body: "<crashes> crashes · <reviews_avg>★ · <fires_summary> · <deferred_summary>"
)
```

- `<fires_summary>` = `🚨 <K> fires` if any active fires, else `✅ no fires`.
- `<deferred_summary>` = `🌙 <K> deferred` if overnight deferrals exist, else omit.

## Step 5 — Scheduling note (first run only)

If no previous `docs/reports/*-morning.md` exists, offer:

> Want me to schedule this daily? Type `/schedule daily 09:00 UTC+3 /morning`.
