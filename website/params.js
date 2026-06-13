/* generated from src/keel/adapters/commands frontmatter */
window.KEEL_ARGS = {
 "ship": {
  "desc": "Drive a GitHub issue end-to-end through the keel backbone (select → branch → implement → CI → review → test → merge → close → capture), reading every project value from .keel/project.yaml via the keel CLI.",
  "hint": "[issue numbers...] [--delegate <claude|codex|agy|ollama:MODEL>] [--review-delegate <claude|codex|agy|ollama:MODEL>] [--review-comments <inline|summary>] [--reviewers <1|2|3>] [--jury|--no-jury|--jury-advisory] [--hotfix] [--dry-run] [--wizard]",
  "flags": [
   "issue numbers...",
   "--delegate <claude|codex|agy|ollama:MODEL>",
   "--review-delegate <claude|codex|agy|ollama:MODEL>",
   "--review-comments <inline|summary>",
   "--reviewers <1|2|3>",
   "--jury|--no-jury|--jury-advisory",
   "--compound",
   "--hotfix",
   "--dry-run",
   "--wizard"
  ]
 },
 "implement": {
  "desc": "Delegate a single issue to the right implementer and drive the s4 implement step standalone. Project-neutral — every project specific is read from .keel/project.yaml via the keel CLI.",
  "hint": "[issue number] [--delegate <claude|codex|agy|ollama:MODEL>]",
  "flags": [
   "issue number",
   "--delegate <claude|codex|agy|ollama:MODEL>"
  ]
 },
 "review-cycle": {
  "desc": "Multi-reviewer review→fix cycle over one or more open PRs — parallel reviewers, structured findings, inline-vs-summary posting, capped fix rounds (s7+s9, standalone). Does not merge.",
  "hint": "[pr number ...] [--review-delegate <...>] [--review-comments <inline|summary>] [--dry-run]",
  "flags": [
   "pr number ...",
   "--review-delegate <...>",
   "--review-comments <inline|summary>",
   "--dry-run"
  ]
 },
 "review-all-day": {
  "desc": "Time-window diff review sweep — scan every commit in a configurable merge-window-aligned span, classify each diff via parallel reviewers, and open one GitHub issue per serious finding. Read-only w.r.t. git/PRs; the only state change is issue creation.",
  "hint": "[days] [--review-delegate <...>] [--dry-run]",
  "flags": [
   "days",
   "--review-delegate <...>",
   "--dry-run"
  ]
 },
 "pr-loop": {
  "desc": "Iterate on an open PR's review comments + CI until checks are green and reviewers are satisfied, then hand off to the windowed merge (s6–s12, standalone).",
  "hint": "[pr number, defaults to current branch's PR] [--review-delegate <...>] [--review-comments <inline|summary>] [--dry-run]",
  "flags": [
   "pr number, defaults to current branch's PR",
   "--review-delegate <...>",
   "--review-comments <inline|summary>",
   "--dry-run"
  ]
 },
 "regression": {
  "desc": "Codebase-wide regression scan — fan out per-area review subagents in parallel, dedupe against existing issues, open fix issues for high-confidence findings, and hand each to keel:ship.",
  "hint": "[--scope <changed|full>] [--since <ref>] [--dry-run]",
  "flags": [
   "--scope <changed|full>",
   "--since <ref>",
   "--dry-run"
  ]
 },
 "triage": {
  "desc": "Auto-classify open issues missing a status label by spawning a classifier subagent and applying role/priority/status labels from the existing label set; risk-tier from tier3_globs. Project-neutral; reads .keel/project.yaml.",
  "hint": "[--dry-run] [--label <name>] [--assign]",
  "flags": [
   "--dry-run",
   "--label <name>",
   "--assign"
  ]
 },
 "morning": {
  "desc": "Daily morning briefing — cross-session deferrals, shipped-since-last-brief, production/health signals, GitHub status, and a ranked focus list. Project-neutral; reads .keel/project.yaml.",
  "hint": "[--since <ref|timestamp>]",
  "flags": [
   "--since <ref|timestamp>"
  ]
 },
 "work-block": {
  "desc": "Daytime multi-issue work block — process an explicit issue list or queue selector through ship with per-issue isolation and operator-visible stopping points.",
  "hint": "[issue numbers...] [--queue <selector>] [--max <N>] [--hours <H>] [--review-comments <inline|summary>]",
  "flags": [
   "issue numbers...",
   "--queue <selector>",
   "--max <N>",
   "--hours <H>",
   "--review-comments <inline|summary>"
  ]
 },
 "overnight": {
  "desc": "Unattended overnight work block — time-aware merge mode keyed on the merge window; runs /keel:ship over the queue until the window closes, then writes a session/morning report. Project-neutral; reads .keel/project.yaml.",
  "hint": "[hours] [--max <N>] [--review-comments <inline|summary>]",
  "flags": [
   "hours",
   "--max <N>",
   "--review-comments <inline|summary>"
  ]
 },
 "wrap": {
  "desc": "Finish the current work session — run the configured gates, commit, push, open a PR, and record a session recap. Project-neutral; reads .keel/project.yaml.",
  "hint": "[optional PR title override] [--since <ref|timestamp>]",
  "flags": [
   "optional PR title override",
   "--since <ref|timestamp>"
  ]
 },
 "ci-check": {
  "desc": "Check the latest CI run's status; on failure, locate the failing job/step, diagnose the root cause, and propose one fix — never auto-apply.",
  "hint": "[pr number]",
  "flags": [
   "pr number"
  ]
 },
 "coverage": {
  "desc": "Compute and post the per-PR test-coverage delta (base → head), flag low-coverage × high-risk hot spots, and open issues to close gaps — routed to keel:ship.",
  "hint": "[pr number] [--base <branch>] [--threshold <pct>] [--changed] [--open-issues] [--dry-run]",
  "flags": [
   "pr number",
   "--base <branch>",
   "--threshold <pct>",
   "--changed",
   "--open-issues",
   "--dry-run"
  ]
 },
 "deps-audit": {
  "desc": "On-demand dependency security + licence audit across the project's ecosystems; classify security vs. routine, append findings to today's tracking issue, and route fixes to keel:ship.",
  "hint": "[<ecosystem>|all] [--severity low|moderate|high|critical] [--open-issues] [--dry-run]",
  "flags": [
   "<ecosystem>|all",
   "--severity low|moderate|high|critical",
   "--open-issues",
   "--dry-run"
  ]
 },
 "flake-audit": {
  "desc": "Detect intermittently-failing tests from recent CI history (or repeated local runs); dedupe against tracked flakes and open one tracking issue per newly-detected flake — routed to keel:ship.",
  "hint": "[--days <N>] [--runs <N>] [--threshold <P>] [--open-issues] [--dry-run]",
  "flags": [
   "--days <N>",
   "--runs <N>",
   "--threshold <P>",
   "--open-issues",
   "--dry-run"
  ]
 },
 "stale-prs": {
  "desc": "Find open PRs that have gone quiet or drifted off the base branch; triage, comment, and optionally refresh from the configured base branch — respecting the merge window.",
  "hint": "[--days <N>] [--rebase|--merge-develop] [--dry-run]",
  "flags": [
   "--days <N>",
   "--rebase|--merge-develop",
   "--dry-run"
  ]
 }
};
