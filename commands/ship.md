---
description: End-to-end issue ship — branch, PR, self-review, CI, N parallel reviewers, optional advisory cross-vendor AI Jury stage, time-windowed merge, issue close. UTC+3 07:00–01:30 merge window (night no-merge window 01:30–07:00).
allowed-tools: Bash(gh:*), Bash(git:*), Bash(date:*), Bash(./gradlew:*), Bash(./scripts/compound-learning.sh:*), Bash(mkdir:*), Bash(rmdir:*), Bash(rm:*), Bash(cat:*), Bash(test:*), Bash(sleep:*), Bash(seq:*), Bash(timeout:*), Bash(gtimeout:*), Bash(kill:*), Bash(printf:*), Bash(echo:*), Bash(grep:*), Bash(sed:*), Read, Edit, Write, Agent, mcp__github__issue_read, mcp__github__issue_write, mcp__github__list_issues, mcp__github__search_issues, mcp__github__add_issue_comment, mcp__github__pull_request_read, mcp__github__list_pull_requests, mcp__github__search_pull_requests, mcp__github__get_file_contents, mcp__github__list_commits, mcp__github__get_commit, mcp__github__list_branches, mcp__github__get_label, mcp__github__create_pull_request, mcp__github__update_pull_request, mcp__github__push_files, mcp__github__add_reply_to_pull_request_comment, mcp__github__pull_request_review_write, mcp__github__enable_pr_auto_merge, mcp__github__merge_pull_request, mcp__github__update_pull_request_branch, mcp__github__subscribe_pr_activity
argument-hint: [issue numbers...] [--reviewers N] [--blocker] [--dry-run] [--preview] [--jury] [--no-jury] [--jury-advisory] [--delegate <claude|codex|agy|ollama:MODEL>] [--review-delegate <claude|codex|agy|ollama:MODEL>] [--wizard]
---

You are the SmartInventory end-to-end shipping orchestrator. Drive each GitHub issue from `status:backlog` to `status:done` (merged + closed) through the full lifecycle in `AGENTS.md` § "Standard Issue Lifecycle".

This command composes the existing pieces — it does NOT duplicate them. Read `AGENTS.md` first; it is the source of truth for branch rules, reviewer template, docs gate, and high-risk file list.

## Language

All committed/published artifacts (commits, branch names, PR/issue titles and bodies, comments, file contents, slash command definitions) MUST be written in English. Free-form chat with the user may stay in any language. See `AGENTS.md` § "Language Policy". This applies to every PR comment the orchestrator posts (Step 5d per-reviewer comments, Step 5f deferral/closure comments, Step 5b/5e/5f.0/5f.1 status updates, and the morning-merge-queue file).

## Runtime model (read this first)

`/ship` runs as a single Claude Code (or Codex) turn loop. Issues are processed **sequentially**: only one issue is in implement-or-review at a time. Within a single issue, the reviewer fan-out at Step 5c is genuinely parallel (multiple Agent calls in one assistant message run concurrently). `--reviewers N` controls only the reviewer fan-out; there is no inter-issue worker pool.

The merge phase is additionally serialised across any concurrent `/ship` invocations on the same checkout via a `mkdir`-based lock (see Step 5f.1). This protects `develop` from racing merges if a human or another agent runs `/ship` in a second terminal.

## Runtime detection (gh vs GitHub MCP) — read this second

This command runs in two environments: a local machine where the `gh` CLI is installed and authenticated, and Claude Code on the Web where the sandbox container does NOT ship `gh` but does have the GitHub MCP server tools available (`mcp__github__*`). Detect once at session start and apply throughout:

```bash
if command -v gh >/dev/null 2>&1; then
  GH_MODE=cli
else
  GH_MODE=mcp
fi
```

State the detected `GH_MODE` in your first user-facing line (alongside the `HHMM` / `WINDOW` from Step 1). The priority order is `gh` first when available (richer JSON output, fewer round-trips, supports `--watch`), falling back to MCP only when `gh` is missing.

### Mapping (apply whenever the prose below names a `gh` call)

| gh CLI invocation | GitHub MCP equivalent |
|---|---|
| `gh issue view <N> --json ...` | `mcp__github__issue_read` (method=`get`) |
| `gh issue list --label X --state Y` | `mcp__github__list_issues` (labels, state, perPage) |
| `gh issue comment <N> --body ...` | `mcp__github__add_issue_comment` |
| `gh issue close <N>` | `mcp__github__issue_write` (method=`update`, state=`closed`) |
| `gh issue edit <N> --add-label X --remove-label Y` | `mcp__github__issue_write` (method=`update`, labels=`[<new full set>]`) — MCP overwrites the label set, so the orchestrator must compute the new set explicitly. |
| `gh pr view <PR> --json baseRefName,mergeStateStatus,isDraft,headRefName,files` | `mcp__github__pull_request_read` (method=`get`). Fields map: `baseRefName` → `base.ref`; `mergeStateStatus` → `mergeable_state` (assert `behind`/`dirty` with case-insensitive compare); `isDraft` → `draft`; `headRefName` → `head.ref`; file list → `mcp__github__pull_request_read` (method=`get_files`). |
| `gh pr comment <PR> --body ...` | `mcp__github__add_issue_comment` — GitHub treats PR comments as issue comments; same endpoint. |
| `gh pr ready <PR>` | `mcp__github__update_pull_request` (draft=`false`) |
| `gh pr ready --undo <PR>` | `mcp__github__update_pull_request` (draft=`true`) |
| `gh pr merge <PR> --squash --delete-branch --subject "<X>"` | `mcp__github__merge_pull_request` (merge_method=`squash`, commit_title=`<X>`). Branch deletion: call `mcp__github__update_pull_request_branch` is NOT a delete; branch cleanup in MCP mode is a known gap — log and continue. |
| `gh pr create ...` | `mcp__github__create_pull_request` |
| `gh api repos/.../pulls/<N>/comments` (review-thread comments) | `mcp__github__pull_request_read` (method=`get_comments`) |
| `gh pr checks <PR>` / `gh pr checks --watch` | `mcp__github__pull_request_read` (method=`get_check_runs`). Returns the check runs for the head commit with per-job `status` and `conclusion`. `--watch` is replaced by a poll-with-delay loop (no native streaming). See Step 5b § `GH_MODE=mcp` fallback. |
| `gh run list ...` / `gh run view --log-failed` | **residual gap** — raw failure logs and branch-scoped workflow-run conclusions are not exposed by the MCP server today. See CI degrade note below for the fallback semantics. |

### CI Health degrade (MCP mode — residual gaps)

The Step 5b CI gate has a full MCP fallback via `mcp__github__pull_request_read(method=get_check_runs)` (see Step 5b § `GH_MODE=mcp` fallback). The remaining `GH_MODE=mcp` gaps:

- **Step 5b raw failure logs.** `gh run view --log-failed` has no MCP equivalent. On a CI failure in MCP mode the implementer subagent receives only the check name and `details_url`; it must reproduce the failure locally (gradle/npm/etc.) to diagnose. If reproduction is not feasible without log access, mark `status:blocked` with a comment quoting the `details_url`(s) so an operator can investigate via the browser. This differentiates a real CI failure (`status:blocked` with `details_url`) from the prior blanket "CI unavailable" false positives that parked green PRs.
- **Step 5f.0 / 5f.1 mergeStateStatus.** Use `mcp__github__pull_request_read` (method=`get`); read `mergeable_state` and apply the same `BEHIND`/`DIRTY` semantics (the MCP field is lowercase: `behind` / `dirty`).
- **Step 3 rule 5 (develop red on gating workflow).** `mcp__github__pull_request_read` is PR-scoped and does not expose workflow-run conclusion for arbitrary branches (e.g. `develop`). Treat as no-fire (`BLOCKER=false` from this rule) and log `Step 3 rule 5 skipped in MCP mode (branch-scoped workflow-run conclusion unavailable)`. If a future MCP server adds a branch-workflow-runs query, lift this no-fire.

### Implementer subagent inheritance

When the orchestrator dispatches the implementer at Step 5a, it MUST pass `GH_MODE=<value>` in the prompt block so the subagent uses the same transport: open the PR via `mcp__github__create_pull_request` in MCP mode, post commit-time issue comments via `mcp__github__add_issue_comment`, push via `git push` first then fall back to `mcp__github__push_files` on HTTP 403 (per Known limitations § git push fallback). Reviewer subagents do NOT call any GitHub write API regardless of mode (the orchestrator-only-writes override holds in both `cli` and `mcp` modes); they only need read access, which the `code-reviewer` agent already has via `mcp__github__*` read tools.

## Step 0 — Parse arguments

Argument grammar:

- Bare positive integers ⇒ explicit issue numbers (one or more).
- `--reviewers <N>` ⇒ consumes exactly one integer immediately after the flag. Valid: `1`, `2`, `3`. Default: `auto` (reviewer count is determined automatically at Step 5a.2 after the diff is known). Reject any other value with an error.
- `--blocker` ⇒ boolean flag; treat every queued issue as blocker (human override).
- `--dry-run` ⇒ boolean flag; perform every read but redirect every state-changing GitHub call (`gh pr comment`, `gh pr merge`, `gh pr ready`, `gh issue close`, `gh issue comment`, label edits) to stdout as `DRY-RUN: <command>` lines. Implementer subagent is also instructed to honour dry-run (no push, no PR open).
- `--preview` ⇒ boolean flag; after the PR is opened, add the `deploy:preview` label so the Firebase Hosting preview channel is deployed. Without this flag no preview channel is deployed (opt-in, per CI change #1315). Also skipped under `--dry-run` (logged to stdout).
- `--jury` ⇒ boolean flag; force-enable the cross-vendor AI Jury stage (Step 5d.jury) for every queued issue, regardless of risk tier. By default the jury now runs in **gating mode** (issue #2033 Phase B): verified `critical`/`major`/`minor` consensus findings gate the merge (see Step 5d.jury / 5e). Use `--jury-advisory` for the old advisory-only behaviour.
- `--no-jury` ⇒ boolean flag; force-disable the jury stage even on TIER-3 PRs (where it would otherwise auto-enable). `--no-jury` always wins over `--jury`, `--jury-advisory`, and the TIER-3 auto-enable. Default when neither flag is passed: jury off, unless Step 5a.2 classifies the PR as TIER-3 (then auto-on, gating).
- `--jury-advisory` ⇒ boolean flag; when the jury is enabled, run it in **advisory-only mode** (Phase A) — it posts a report and logs a verdict but never gates the merge and never consumes a review-fix round. Only meaningful on runs where the jury is enabled (`--jury` or TIER-3 auto); a no-op when the jury is off. `--no-jury` still wins over it.
- `--delegate <impl>` ⇒ consumes one value immediately after the flag, the **implementer** (who writes the code). Valid: `claude`, `codex`, `agy`, or `ollama:<model>` (a local Ollama model — the orchestrator-driven harness of § 5a.ollama). The flag is a **per-run override** of the issue's `delegate:*` label (Step 5a): when present it wins over any label for every queued issue. **Default (no flag, no label): the host agent** — whichever CLI is driving this `/ship` run (`HOST_AGENT`; see § Host-agent default). So a Codex-driven run defaults to `codex` writing the code, an Antigravity-driven run to `agy`, a Claude-Code-driven run to `claude`. Reject any other value. `ollama:<model>` requires a non-empty model name and is **refused on TIER-3** issues (falls back to the host agent — see § 5a.ollama).
- `--review-delegate <vendor>` ⇒ consumes one value immediately after the flag, the **Step 5c reviewer vendor**. Valid: `claude`, `codex`, `agy`, or `ollama:<model>`. A non-host value routes the 5c review through that vendor's CLI in **read-only** mode (the jury-style external invocation), returning findings only — the orchestrator-only-writes contract still holds (only the orchestrator posts comments). **Default (no flag): the host agent** (`HOST_AGENT`) — review runs in whatever CLI you launched `/ship` from. Reject any other value.

**Host-agent default (`HOST_AGENT`).** `/ship` runs inside one agentic CLI — Claude Code, Codex, or Antigravity. That executing CLI is the `HOST_AGENT`, and it is the default for both `--delegate` and `--review-delegate`: by default you implement and review with the same agent whose terminal you launched the run from. `HOST_AGENT` is NOT an operator-set environment variable — the orchestrator resolves it from its own runtime context (it IS the host): `claude` when running on Claude Code, `codex` on Codex, `agy` on Antigravity. The historical hardcoded `claude` default is replaced by `HOST_AGENT` — on Claude Code the two coincide, so existing behaviour is unchanged. A `delegate:*` label or an explicit `--delegate`/`--review-delegate` flag still overrides this default (precedence in Step 5a / Step 5c).
- `--wizard` ⇒ boolean flag; **interactive opt-in only**. Run the guided configuration wizard (Step 0.wizard) which collects the run's options through interactive prompts and converts them into the normal flag/issue set BEFORE Step 1. Modelled on `jury init --wizard`. It is purely a front layer — it changes nothing about the pipeline below, it only assembles the same arguments the grammar above accepts. The wizard MUST NOT run in any non-interactive context (watch mode, `/overnight`, background jobs); see Step 0.wizard for the hard guard. In any such context `--wizard` degrades to a logged no-op (never a rejection, never a hang) and the run proceeds with the literal flags as parsed. `--dry-run` is fully compatible with `--wizard`: it is just one of the values the wizard can set (or pass through), so `--wizard --dry-run` runs the wizard interactively and then executes the pipeline in dry-run.

Flags and their value MUST appear together; positional integers are everything not consumed by a flag. Worked examples:

```
/ship 42                   → ISSUES=[42]                REVIEWERS=auto (resolved at Step 5a.2)
/ship 42 56                → ISSUES=[42,56]             REVIEWERS=auto (resolved at Step 5a.2)
/ship --reviewers 2 42     → ISSUES=[42]                REVIEWERS=2
/ship 42 --reviewers 2 56  → ISSUES=[42,56]             REVIEWERS=2
/ship --dry-run 42         → ISSUES=[42]                REVIEWERS=auto   DRY_RUN=true
/ship --preview 42         → ISSUES=[42]                REVIEWERS=auto   PREVIEW=true
/ship --jury 42            → ISSUES=[42]                REVIEWERS=auto   JURY=true (advisory)
/ship --no-jury 42         → ISSUES=[42]                REVIEWERS=auto   JURY=false (even if TIER-3)
/ship --delegate codex 42  → ISSUES=[42]                IMPLEMENTER=codex (overrides any delegate:* label)
/ship --delegate ollama:qwen 42  → ISSUES=[42]          IMPLEMENTER=ollama:qwen (local model writes; § 5a.ollama; refused on TIER-3)
/ship --review-delegate ollama:qwen 42  → ISSUES=[42]   5c reviewer = local qwen (read-only, findings-only)
/ship --wizard             → interactive prompts → resolves to a normal flag/issue set, then Step 1
/ship --wizard 42          → interactive prompts pre-seeded with ISSUES=[42] → normal flag set, then Step 1
/ship                      → ISSUES=[] (watch mode)     REVIEWERS=auto (resolved per-issue at Step 5a.2)
```

Reject:

- Unknown flags (anything starting with `--` not in the list above).
- `--reviewers` value outside `{1, 2, 3}`.
- `--reviewers` without an integer immediately following.
- `--delegate` value outside `{claude, codex, agy, ollama:<model>}` (the `ollama:` model name must be non-empty — `ollama:` alone is rejected), or no value following.
- `--review-delegate` value outside `{claude, codex, agy, ollama:<model>}` (the `ollama:` model name must be non-empty — `ollama:` alone is rejected), or no value following.
- Negative or zero positional integers.

**CLI delegation (label OR `--delegate` flag):** the **implementer** can be chosen two ways — add a `delegate:codex` / `delegate:agy` label to the issue (detected at Step 5a), or pass `--delegate <claude|codex|agy>` on the command line. The flag is a per-run override and **wins over the label** when both are present. `--delegate claude` forces the default Claude subagent even if the issue carries a `delegate:*` label.

```
# Issue #42 has label delegate:codex → Codex CLI handles implementation
/ship 42                   → Step 5a routes to codex exec -s danger-full-access < "$prompt_file"

# Issue #56 has label delegate:agy  → Antigravity CLI handles implementation
/ship 56                   → Step 5a routes to agy --print "<prompt>"

# No delegation label → default Claude subagent (android-developer / web-developer)
/ship 99                   → Step 5a spawns the matching Claude subagent

# Fallback: if codex is unavailable or quota exhausted on agy, /ship falls
#            back to the default Claude subagent and logs the reason.
```

## Step 0.wizard — Interactive configuration wizard (only when `--wizard`)

Runs **only when `--wizard` was passed** at Step 0, and **only in an interactive session**. Skip this entire step when `--wizard` is absent. The wizard is a front layer that produces the same `ISSUES` / `REVIEWERS` / `BLOCKER` / `DRY_RUN` / `PREVIEW` / `JURY` / `IMPLEMENTER` (`--delegate`) / reviewer-vendor (`--review-delegate`) state the grammar above produces — it does NOT add new behaviour to Steps 1–6.

**Hard interactivity guard (safety invariant).** The wizard uses the `AskUserQuestion` tool, which requires a human to answer. `/ship` is frequently driven autonomously (watch mode, `/overnight`, `/lfg`, background `run_in_background` jobs) where no human is present. The orchestrator MUST NOT enter the wizard in any such context:

- If the run is non-interactive (no operator able to answer prompts — e.g. invoked from `/overnight`, a scheduled job, a background task, or any headless `claude -p` pipeline), do NOT prompt. Instead log `ship: --wizard ignored (non-interactive session); proceeding with the literal flags as parsed` and continue to Step 1 with whatever flags were parsed at Step 0 (i.e. `--wizard` degrades to a no-op, never a hang).
- The wizard never blocks an autonomous pipeline. When in doubt about interactivity, treat the session as non-interactive and skip the wizard.

**Tool/model detection (runs before the questions, best-effort, never blocks).** So the wizard only offers what is actually runnable, probe the environment once and build the available-choice lists. The snippet below requires **bash 4+** (it uses array `+=` and `< <(...)` process substitution — not POSIX `sh`); on an `sh`-only host treat detection as "nothing detected beyond `HOST_AGENT`" and degrade gracefully (the lists just stay short — never an error):

```bash
# Installed agentic CLIs (implementer-capable AND read-only-reviewer-capable).
AVAIL_CLIS=()
for c in claude codex agy; do command -v "$c" >/dev/null 2>&1 && AVAIL_CLIS+=("$c"); done
# Local Ollama models — both review-capable (--review-delegate) and implementer-capable (§ 5a.ollama).
OLLAMA_MODELS=()
if command -v ollama >/dev/null 2>&1; then
  # `ollama list` first column (skip header); tolerate ollama not running.
  while IFS= read -r m; do [ -n "$m" ] && OLLAMA_MODELS+=("$m"); done \
    < <(ollama list 2>/dev/null | awk 'NR>1{print $1}')
fi
```

The `HOST_AGENT` (the CLI driving this run; see § Host-agent default) is always treated as available and is the default/recommended choice in the Implementer and Reviewer-vendor questions. Detection failures (no `ollama`, daemon down, `command -v` misses) just yield shorter lists — never an error, never a block.

**Quick-start vs Customize (asked first).** The wizard's FIRST question is always a fast-path chooser so the operator never has to answer every question to proceed:

- `Use recommended defaults (default)` — resolve EVERY option below to its default (`IMPLEMENTER=HOST_AGENT`, `REVIEW_DELEGATE=HOST_AGENT`, `REVIEWERS=auto`, `JURY=auto`, run mode `Normal`, no preview), skip the detailed questions, and proceed. The only thing still collected is **Issues** (and only when none were given positionally). This is equivalent to a bare `/ship <issues>`.
- `Customize` — present the full batched question set below so the operator can change any individual option.

If the operator picks `Use recommended defaults`, the wizard does NOT show questions 2–6 at all — it goes straight to the resolved-config echo and Step 1. This makes "just run it with the safe defaults" a one-tap path, while still surfacing every knob for those who want it.

**What it collects when `Customize` is chosen (one `AskUserQuestion` call, batched questions).** Each answer maps directly onto an existing Step 0 variable/flag; the wizard performs no validation the grammar does not already perform.

**Default-marking requirement (every question).** Each question MUST present its default choice **first** in the option list, with `(default)` appended to that option's label, and its description MUST state what the default does so an operator who just accepts it knows the outcome (e.g. `Normal — runs to merge, respects the night no-merge window`). The default value equals what `/ship` would use if the flag were not passed at all (so the wizard with all-defaults reproduces a bare `/ship <issues>`). This makes every choice's safe baseline explicit rather than implicit.

1. **Issues** — only asked when no positional issue numbers were given on the command line. If `--wizard 42 56` was passed, `ISSUES=[42,56]` is pre-seeded and this question is skipped. Free-form answer parsed as space/comma-separated positive integers (same validation as the Step 0 grammar; reject zero/negative). The wizard does NOT offer "watch mode" as a choice — watch mode is the no-args non-wizard path; a wizard run always targets explicit issues. (No fixed default — issues are required for a wizard run.)
2. **Implementer** (who writes the code) — options are built from `AVAIL_CLIS`. The **`HOST_AGENT` is the default** (labelled `<host> (default)`, description: "the CLI you launched `/ship` from writes the code"), listed first, followed by the other installed CLIs. Detected `OLLAMA_MODELS` are listed as `ollama:<model>` choices — a **free local-model implementer** for easy/low-risk issues (the orchestrator-driven harness of § 5a.ollama), description e.g. "local <model> writes the code (best for low-risk issues; refused on TIER-3)". Any choice maps to `--delegate <claude|codex|agy|ollama:<model>>`. An `ollama:<model>` choice on a TIER-3 issue falls back to `HOST_AGENT` at § 5a.ollama (logged). Omit the whole question if `HOST_AGENT` is the only available implementer.
3. **Reviewer vendor** (Step 5c review) — the **`HOST_AGENT` is the default** (labelled `<host> (default)`, description: "5c review runs in the CLI you launched `/ship` from"), listed first, plus any other installed `codex`/`agy`/`claude` and any detected `ollama:<model>` as **read-only cross-vendor reviewer** choices. Maps to `--review-delegate`. A non-host vendor runs the 5c review through that vendor's CLI read-only (jury-style invocation), returns findings only, and the orchestrator still posts the comment (orchestrator-only-writes holds). Omit if `HOST_AGENT` is the only available CLI.
4. **Reviewers** — `auto (default)` (description: "reviewer count auto-resolved by risk tier at Step 5a.2 — TIER-3=3, TIER-2=2, TIER-1 docs=1") / `1` / `2` / `3`. Maps to `REVIEWERS`.
5. **AI Jury** — `auto (default)` (description: "off unless the PR is TIER-3, then auto-on") / `force on (--jury)` / `force off (--no-jury)`. Maps to `JURY` / the `--jury`/`--no-jury` precedence at Step 5a.2.
6. **Run mode** — single-select primary mode (default first), plus `preview` as an independent add-on:
   - `Normal (default)` — description: "runs to merge; respects the night no-merge window (01:30–07:00 UTC+3) — non-blocker merges defer to the morning queue inside it". No flag (the bare-`/ship` behaviour).
   - `Force-merge (--blocker)` — description: "runs to merge and ignores the window — merges even inside the night no-merge window".
   - `Dry-run (--dry-run)` — description: "no GitHub writes, no merge — logs the would-be actions only".
   - *(independent add-on, separate toggle)* `Preview channel (--preview)` — adds the `deploy:preview` label after the PR opens; orthogonal to the merge mode above.

**After collecting:** echo the resolved configuration back to the user as a single confirmation line in the same shape as the Step 0 worked examples, e.g.:

```
Wizard resolved: ISSUES=[2027] IMPLEMENTER=claude REVIEW_DELEGATE=claude REVIEWERS=auto JURY=auto DRY_RUN=false PREVIEW=false BLOCKER=false
```

Then proceed to Step 1 exactly as if those flags had been typed. The wizard adds no state that Steps 1–6 do not already understand; it is impossible for the wizard to produce a configuration the normal grammar could not. Do NOT re-prompt mid-pipeline — the wizard is a one-shot pre-Step-1 collector.

**`--dry-run` interaction:** if the operator selects dry-run in the wizard (or passed `--dry-run` alongside `--wizard`), the wizard still runs interactively to collect the config, then the pipeline executes in dry-run as usual. The wizard itself writes nothing to GitHub.

## Step 1 — Time-window detect

```bash
# Etc/GMT-3 = UTC+3 (POSIX TZ uses inverted sign — do NOT "correct" to Etc/GMT+3)
HOUR_RAW=$(TZ='Etc/GMT-3' date +%H)
DATE=$(TZ='Etc/GMT-3' date +%Y-%m-%d)
HHMM=$(TZ='Etc/GMT-3' date +%H%M)

# Force base-10 to avoid the bash octal trap: ((0900)) errors as octal.
# Done in shell (not via `date +%-H`) because `%-H` is GNU-only and macOS BSD
# date silently emits "%-H" literally, breaking arithmetic.
HOUR=$((10#$HOUR_RAW))
HHMM_DEC=$((10#$HHMM))

# Night no-merge window: UTC+3 01:30 (inclusive) – 07:00 (exclusive).
# Merge window (open): the complement — 07:00 through 01:29 the next day.
if (( HHMM_DEC >= 130 && HHMM_DEC < 700 )); then
  WINDOW=closed
else
  WINDOW=open
fi
```

State the detected `HHMM` and `WINDOW` in your first user-facing line.

**Midnight rollover policy:** the window is checked at Step 1 and re-checked inside the merge lock at Step 5f.1. If `WINDOW` has flipped to `closed` by the time a non-blocker issue reaches Step 5f.1, the merge aborts and the issue is appended to the morning queue. Blockers always proceed regardless. (No proactive lookahead is performed; the conservative re-check is sufficient and avoids estimating CI/review duration.)

## Step 2 — Build the queue

Snapshot at this step; do NOT re-poll mid-session.

If `ISSUES` is non-empty: queue = those numbers. Validate each:

```bash
gh issue view <N> --json number,state,labels,title,body
```

Drop any closed issues (warn the user). Reject the run if `gh` returns a non-zero exit (network, auth, or rate-limit) — log the partial state to stdout before exiting.

Else (watch mode):

```bash
# Limit 20 — remaining backlog issues are picked up in the next /ship session.
gh issue list --label status:backlog --state open --json number,title,labels,body --limit 20
```

Order by `priority:high` first, then by issue number ascending.

If queue is empty, log a one-line summary to stdout and exit.

## Step 3 — Blocker auto-detection (per issue)

For each queued issue, set `BLOCKER=true` if any rule fires:

1. `--blocker` flag was passed ⇒ all queued issues are blockers (human override).
2. Issue labels include `alert:crash` OR `alert:review`.
3. Issue title or body matches the tightened regex (word-boundary anchored, case-insensitive):
   ```
   \b(fix\s+ci|data[-\s]?loss|security\s+(vuln|fix|hole|bug)|breaking\s+change|crashes?\s+on|critical\s+regression)\b
   ```
4. Labels include `priority:high` AND title matches `\b(critical|urgent|blocker)\b` (case-insensitive).
5. Develop is currently red on a **gating** workflow AND this PR's diff touches files relevant to that workflow:
   ```bash
   for WF in "Android CI" "Web CI"; do
     CONCL=$(gh run list --branch develop --workflow="$WF" --limit 1 --json conclusion --jq '.[0].conclusion')
     if [[ "$CONCL" == "failure" ]]; then
       # Map workflow → expected diff paths.
       case "$WF" in
         "Android CI") PATTERN='^(android/|\.github/workflows/android-ci\.yml)' ;;
         "Web CI")     PATTERN='^(web/|shared/schema/firebase/|\.github/workflows/web-ci\.yml)' ;;
       esac
       # Inspect PR diff (if PR is open) — fire BLOCKER only if diff matches.
       if gh pr view "$PR" --json files --jq '.files[].path' 2>/dev/null | grep -qE "$PATTERN"; then
         BLOCKER=true
       fi
     fi
   done
   ```
   Fire the BLOCKER only if both the failing workflow is a gating workflow AND the issue's PR (once opened) touches the relevant paths. If the PR is not yet open at this step (Step 3 runs before Step 5a), fall back to a title keyword match for `fix ci` or `green develop`.

Otherwise `BLOCKER=false`.

## Step 4 — Window gate (merge-only; Steps 5a–5e.bis run regardless)

The window only gates the **merge** at Step 5f. Implementation, CI, reviewer
fan-out, reviewer comments, SUGGESTION-application, fix loops, and the tester
gate ALL execute regardless of window. Step 5f's defensive re-check (point 2,
inside the lock) is what enforces "no overnight merges for non-blockers" —
when it fires, the PR is left ready-to-merge (`status:needs-test` after the
tester clears; **`status:done` is set only post-merge in Step 5f**) so the
next `/ship` pass inside the window — or a human — can flip the merge bit
without redoing review/test.

| WINDOW | Any blocker in queue? | What changes? |
|--------|----------------------|---------------|
| open   | n/a                  | Nothing — Step 5f merges as soon as the per-issue loop is ready. |
| closed | yes                  | Blockers reach Step 5f and merge as normal. Non-blockers also run 5a–5e.bis fully; Step 5f's re-check defers only their **merge** to the morning queue. |
| closed | no                   | Every issue runs 5a–5e.bis fully; Step 5f re-checks the window and defers each **merge** to the morning queue. |

Step 4 itself does NOT filter the queue or write the morning-queue file. That
write happens inside Step 5f when the merge is actually deferred (see Step 5f
point 2). This preserves the invariant "no merge during the night window" while keeping
the work pipeline moving — agents that have time/budget to review and test
during the night should not be idle waiting for 07:00.

For deferred **merges** (the write happens inside Step 5f, not here):

1. Append each to `docs/reports/morning-merge-queue-<DATE>.md`:

   ```markdown
   # Morning merge queue — <DATE>
   Outside merge window (inside UTC+3 night no-merge window 01:30–07:00). PRs parked for morning merge:

   | # | PR | Title | Reason | Blocker? | Tester verdict |
   |---|----|-------|--------|----------|----------------|
   | 42 | #99 | … | window:closed | no | LGTM (TESTER-42-…) |
   ```

2. Post a comment on each deferred issue (skip if `--dry-run`; log to stdout instead):

   ```
   Implementation, CI, review, and tester gate all complete. Merge deferred to
   the morning window — inside the UTC+3 night no-merge window 01:30–07:00.
   The next `/ship` session outside that window (i.e. 07:00 – 01:30 next day)
   will pick up at Step 5f (mergeability re-check + squash) without re-running
   review or tests.
   ```

3. Leave the issue at `status:needs-test`. Do **not** set `status:done` —
   that label means "merged + closed" per the AGENTS.md taxonomy, and the
   issue is not yet merged.

Issues whose merge gets deferred remain in the per-issue Step 5 lifecycle as
"work-complete-pending-merge". The next `/ship` session detects them by
querying open PRs whose head branch contains the codename pattern
`*-<N>-<UTC_TIMESTAMP>` and whose issue is at `status:needs-test`, and skips
straight to Step 5f for those.

If Step 5 has nothing to process (every issue was closed/dropped at Step 2 or
3), log a one-line summary to stdout and exit.

## Step 5 — Per-issue lifecycle (sequential)

For each issue `N` in the active set, run the steps sequentially: 5a → 5b → 5c → 5d → 5d.jury (if `JURY=true`) → 5e → 5e.bis → 5f. The reviewer fan-out at 5c is the only parallel step.

### 5a. Implementer subagent (delegate to /implement logic)

**Implementer mode — resolve `IMPLEMENTER`, then dispatch:**

Precedence (first match wins): **`--delegate` flag > `delegate:*` label > `HOST_AGENT` default**. The wizard's Implementer choice resolves to the same `--delegate` flag, so it sits at the top tier. If neither flag nor label is present, the implementer is the host agent (the CLI driving this run — see § Host-agent default).

| Resolved `IMPLEMENTER` | Source | Implementer | Details |
|------------------------|--------|-------------|---------|
| `codex` | `--delegate codex`, OR `delegate:codex` label, OR `HOST_AGENT=codex` | Codex CLI (`codex exec`) | § 5a.codex below |
| `agy` | `--delegate agy`, OR `delegate:agy` label, OR `HOST_AGENT=agy` | Antigravity CLI (`agy`) | § 5a.agy below |
| `ollama:<model>` | `--delegate ollama:<model>`, OR `delegate:ollama` label (+ `delegate-model:<name>`) | Local Ollama model via orchestrator-driven harness | § 5a.ollama below |
| `claude` | `--delegate claude`, OR `HOST_AGENT=claude` with no flag/label | Claude subagent | Default path — continue reading this section |

`--delegate claude` forces the Claude subagent even when the issue carries a `delegate:codex`/`delegate:agy` label (the explicit per-run override wins). When no flag and no label are present, `IMPLEMENTER=HOST_AGENT` — a Codex-driven run writes with Codex, an Antigravity-driven run with agy, a Claude-Code-driven run with the Claude subagent. `ollama:<model>` routes to § 5a.ollama, but is **refused on TIER-3** (high-risk) issues — there it falls back to `HOST_AGENT` with a logged note (§ 5a.ollama).

**Attribution contract (MANDATORY on every path — issue #2036).** Whichever path runs (default Claude subagent / 5a.codex / 5a.agy / 5a.ollama, delegate or not), the orchestrator MUST record the implementer attribution. This is structurally mandatory — a plain `/ship` with no delegate is NOT exempt. Resolve and record:

- `AGENT_LABEL = agent:<vendor>` where `<vendor>` is the RESOLVED implementer, never a hardcoded value: `claude-code` (default Claude subagent) · `codex` · `agy` · `ollama` (§ 5a.ollama). Add it as a persistent label at label-flip time (alongside `status:in-progress`); it is never removed.
- `IMPLEMENTER_SYSTEM` = the full system string incl. model when known: `claude-code` · `codex` · `agy` · `ollama:<model>` (e.g. `ollama:qwen2.5`). For codex/agy, append the model when a `delegate-model:<name>` label is set or the CLI reports one (`codex:<model>` / `agy:<model>`); else just the vendor.
- `MODEL_BASE` (optional) — when a specific model is known, also add a **versionless** `model:<base>` label. **Stripping algorithm:** (1) drop any ollama `:tag` suffix (`qwen2.5:7b` → `qwen2.5`); (2) if the family is `<word><digits…>` with **no hyphen**, drop the entire trailing numeric run → `qwen2.5`→`qwen`, `gemma2`→`gemma`, `llama3.1`→`llama`; (3) if the family is **hyphenated** `<word>-<major>[.<minor>]`, keep `<word>-<major>` and drop the `.<minor>` → `gpt-5.5`→`gpt-5`, `gpt-4o`→`gpt-4o` (no `.minor` to drop). Lowercase the result. Labels stay coarse (no version explosion); the full version lives in `IMPLEMENTER_SYSTEM` and the closure comment. Omit `model:` when no specific model is known (e.g. plain Claude host with no override).

**Attribution reflects the EFFECTIVE implementer.** Whenever any path falls back (ollama → host on unavailability/retry-exhaustion; codex/agy → Claude on unavailability/quota), the orchestrator MUST set `AGENT_LABEL` / `IMPLEMENTER_SYSTEM` / `MODEL_BASE` to the implementer that **actually ran**, BEFORE the labels are applied at label-flip time — never the one that was requested-but-fell-back. (Same effective-not-requested rule the reviewer attribution uses at Step 5c.)

The agent-start comment (below) and these labels are skipped only under `--dry-run` (logged instead). Outside `--dry-run`, a run that reaches Step 5b without an `agent:<vendor>` label is a defect.

**Default path (`IMPLEMENTER=claude`):** Pick `android-developer` or `web-developer` from issue labels/path (see `.claude/commands/implement.md` Step 3).

Set `AGENT_LABEL=agent:claude-code` and `IMPLEMENTER_SYSTEM=claude-code` (no `model:` label unless a specific Claude model is known and worth recording).

Generate codename `<ROLE>-<N>-<UTC_TIMESTAMP>` (`AGENTS.md` § "Agent Run Codenames").

Post the agent-start comment to the issue (skip if `--dry-run`; log instead). Add labels `status:in-progress` and `agent:claude-code` (remove `status:backlog`) — also skipped under `--dry-run`. The `agent:claude-code` label persists through the full lifecycle and is never removed.

The agent-start comment MUST include an `Implementer system:` line:
```
Agent run started
Codename: `<codename>`
Agent: `<android-developer|web-developer>`
Implementer system: `claude-code`
Branch: `<branch>`
```

Spawn the chosen subagent with the same prompt block as `/implement` Step 5, plus:

- "**Workspace isolation (mandatory):** create a git worktree off `origin/develop` and perform every edit, build, and push from inside it — never mutate the user's primary checkout. Per `AGENTS.md` § "Workspace Isolation (AI agents)": `git fetch origin develop --quiet && git worktree add -b <branch> worktrees/issue-<N> origin/develop && cd worktrees/issue-<N>`. All subsequent steps run from that path. After the PR is merged, clean up with `git worktree remove worktrees/issue-<N> --force`."
- "Open the PR as **draft** with `Closes #<N>` in the body."
- "Branch from `develop` (`AGENTS.md` § Branch Rules). Reject any other base."
- "Before pushing, run `git diff origin/develop...HEAD --name-only` and verify every listed path belongs to issue `<N>`'s scope. If any unexpected file appears, revert it before pushing."
- "**Workspace path convention (mandatory, per #931):** create the worktree at `worktrees/issue-<N>` (nested under the repo root in the gitignored `worktrees/` subdirectory), where `<N>` is the issue number. The orchestrator's Step 5f.0 pre-cleanup hard-fails if `worktree_path` is the repo root itself or anywhere outside `<repo-root>/worktrees/`. The deprecated sibling form (`../smartinventory-<N>`) is no longer accepted."
- "Return as the FINAL block of the response, a JSON code-fence with schema:
  ```json
  {
    \"pr_number\": <int>,
    \"branch\": \"<string>\",
    \"files_changed\": [\"<string>\", \"...\"],
    \"test_results\": \"<string>\",
    \"codename\": \"<string>\",
    \"worktree_path\": \"<absolute path the implementer created via git worktree add>\"
  }
  ```
  Orchestrator parses this JSON via `jq` for Step 5a.1 / 5f.0 consumption. Free-text in the response above the JSON block is fine (human-readable summary); the JSON envelope is the machine-readable contract. `worktree_path` MUST be an absolute path (the same one passed to `git worktree add`)."
- "Use `Co-Authored-By: Claude Code <noreply+claude-code@anthropic.com>` as a trailer on every commit."
- If `--dry-run`: "Do NOT push, do NOT open a PR. Return the diff and intended PR title/body. Orchestrator will skip subsequent steps."
- If `--preview`: after the PR is opened (and not under `--dry-run`), add the `deploy:preview` label via `gh pr edit <PR> --add-label deploy:preview`. This triggers the Firebase Hosting preview channel workflow. Under `--dry-run`, log `DRY-RUN: gh pr edit <PR> --add-label deploy:preview` to stdout instead.

If `--dry-run` is set, after the implementer returns the dry-run diff, log it and skip 5b–5f.

### 5a.codex — Codex CLI delegation path

When `IMPLEMENTER=codex` (resolved per the Step 5a precedence table — i.e. `--delegate codex`, OR a `delegate:codex` label, OR `HOST_AGENT=codex` with no overriding flag/label):

Set `AGENT_LABEL=agent:codex` and `IMPLEMENTER_SYSTEM=codex` (append the model when known — `codex:<model>` from a `delegate-model:<name>` label or the CLI's reported model — and add the versionless `model:<base>` label per the Attribution contract).

1. Generate codename `CODEX-<N>-<UTC_TIMESTAMP>`.
2. Post the agent-start comment (include `Implementer system: \`$IMPLEMENTER_SYSTEM\`` — the full value, e.g. `codex:gpt-5.5` when a model is known, else `codex`) and flip labels (`status:backlog` → `status:in-progress`, add `agent:codex` + the `model:<base>` label when known) — same cadence as the default path (skip under `--dry-run`). The `agent:codex` label persists and is never removed.
3. Build the task prompt string from the issue title + body + the standard implementation brief (worktree isolation, branch-from-develop, `Closes #<N>`, scope-check, JSON return contract). Include the full JSON schema at the end of the prompt so Codex emits it in its final response. Include the instruction: `"Use Co-Authored-By: Codex <noreply+codex@openai.com> as a trailer on every commit."`
4. Invoke Codex from the project root. Write the prompt to a temp file and pipe via stdin — passing the prompt as a positional argument causes Codex to hang waiting for additional stdin. Use `-s danger-full-access` so `gh` CLI can reach `api.github.com`; the default `sandbox = "workspace-write"` in `~/.codex/config.toml` blocks outbound HTTP to GitHub's API. If the issue has a `delegate-model:<name>` label, extract `<name>` and pass `-c model=<name>` to override the model for this run:
   ```bash
   # Use $CLAUDE_JOB_DIR in background sessions to avoid /tmp collisions across parallel jobs
   PROMPT_FILE="$CLAUDE_JOB_DIR/codex-prompt-<N>.txt"
   cat > "$PROMPT_FILE" << 'CODEX_PROMPT'
   <task prompt>
   CODEX_PROMPT

   # Read delegate-model:<name> label if present (e.g. delegate-model:o4-mini)
   MODEL_FLAG=""
   DELEGATE_MODEL=$(gh issue view <N> --json labels --jq '.labels[].name | select(startswith("delegate-model:"))' | head -1 | sed 's/delegate-model://')
   [ -n "$DELEGATE_MODEL" ] && MODEL_FLAG="-c model=$DELEGATE_MODEL"

   codex exec -s danger-full-access $MODEL_FLAG < "$PROMPT_FILE"
   ```
   `approval = "never"` is already set in `~/.codex/config.toml`; no extra flag needed. Do NOT use `--full-auto` (implies `workspace-write` sandbox, which blocks `gh` API calls). The `.codex/hooks.json` PreToolUse hook is the primary command-level security guard (reads `.claude/settings.json` deny list and blocks matching commands before execution).
5. Parse the JSON code-fence from Codex's stdout — same schema as the default-path JSON contract (`pr_number`, `branch`, `files_changed`, `test_results`, `codename`, `worktree_path`). All downstream steps (5a.1 scope gate, 5b CI, 5f merge) operate on this JSON identically to the default path.
6. **Unavailability / error fallback:** if `codex` is not installed (`command -v codex` exits non-zero) or exits non-zero with no parseable JSON block: fall back to the default Claude subagent path. Log `ship: codex unavailable or errored, fell back to android-developer/web-developer`.
7. **`--dry-run`:** include `"Do NOT push, do NOT open a PR. Return the diff and intended PR title/body."` in the task prompt. After Codex returns, log and skip 5b–5f as with the default path.
8. **`--preview`:** after parsing the JSON contract from Codex's stdout (step 5), the orchestrator adds `deploy:preview` to the PR via `gh pr edit <pr_number> --add-label deploy:preview`. Under `--dry-run`, log to stdout instead. Codex itself does not need to know about `--preview`.

### 5a.agy — Antigravity CLI delegation path

When `IMPLEMENTER=agy` (resolved per the Step 5a precedence table — i.e. `--delegate agy`, OR a `delegate:agy` label, OR `HOST_AGENT=agy` with no overriding flag/label):

Set `AGENT_LABEL=agent:agy` and `IMPLEMENTER_SYSTEM=agy` (append the model when known — `agy:<model>` from a `delegate-model:<name>` label or the configured model in `~/.gemini/antigravity-cli/settings.json` — and add the versionless `model:<base>` label per the Attribution contract).

1. Generate codename `AGY-<N>-<UTC_TIMESTAMP>`.
2. Post the agent-start comment (include `Implementer system: \`$IMPLEMENTER_SYSTEM\`` — the full value, e.g. `agy:<model>` when known, else `agy`) and flip labels (`status:backlog` → `status:in-progress`, add `agent:agy` + the `model:<base>` label when known) — same cadence as the default path (skip under `--dry-run`). The `agent:agy` label persists and is never removed.
3. Build the task prompt string (same as the Codex path — full issue brief + JSON return schema at end). Include the instruction: `"Use Co-Authored-By: Antigravity <noreply+antigravity@google.com> as a trailer on every commit."`
4. Invoke agy by writing the prompt to a temp file and piping via stdin. The `agy` shell alias is only available in interactive shells — Claude Code subprocesses don't inherit it. Use the full binary path directly. Do NOT pass `--sandbox` (blocks network/terminal tools). Set `--print-timeout 90m` — the default 5m is too short for implementation tasks:
   ```bash
   PROMPT_FILE="$CLAUDE_JOB_DIR/agy-prompt-<N>.txt"
   cat > "$PROMPT_FILE" << 'AGY_PROMPT'
   <task prompt>
   AGY_PROMPT

   /Users/berkayturanci/.local/bin/agy --dangerously-skip-permissions --print --print-timeout 90m - < "$PROMPT_FILE"
   ```
   Security guard: `~/.gemini/antigravity-cli/settings.json` `permissions.deny` list blocks destructive commands. See `docs/claude-code-global-setup.md` § Antigravity CLI for the full deny list.

   **Model configuration:** agy has no `--model` CLI flag. Model is controlled by the `"model"` field in `~/.gemini/antigravity-cli/settings.json`. Recommended default: `"gemini-1.5-flash-001"` (Gemini Flash — significantly faster than `"GPT-OSS 120B (Medium)"` which caused 32+ minute waits). Edit `settings.json` directly before running `/ship` to change the model; there is no per-run CLI override.
5. Parse the JSON code-fence from stdout — same schema as the default-path JSON contract. All downstream steps operate on this JSON identically to the default path.
6. **Quota fallback (HTTP 429 / RESOURCE_EXHAUSTED):** if agy exits with a 429 error, fall back to the default Claude subagent path immediately — do NOT retry (quota reset takes ~62 hours). Log `ship: agy quota exhausted (429), fell back to android-developer/web-developer`.
7. **Availability fallback:** if `agy` is not installed, fall back to the default Claude subagent path. Log `ship: agy unavailable, fell back to android-developer/web-developer`.
8. **`--dry-run`:** same as Codex path — include dry-run semantics in the task prompt and skip 5b–5f after agy returns.
9. **`--preview`:** same as Codex path — after parsing the JSON contract from agy's stdout (step 5), the orchestrator adds `deploy:preview` to the PR. Under `--dry-run`, log to stdout instead.

### 5a.ollama — Local Ollama model (orchestrator-driven harness)

When `IMPLEMENTER=ollama:<model>` (resolved per the Step 5a precedence table — i.e. `--delegate ollama:<model>`, OR a `delegate:ollama` label with a `delegate-model:<name>` label). A bare Ollama model is a text endpoint — it cannot run tools, edit files, or open a PR — so **unlike 5a.codex/5a.agy, the orchestrator does every git/`gh`/PR step itself and delegates only code generation to the local model.** This path is for **easy / low-risk issues** (cost-free local inference).

**Set the ollama attribution ONLY once the local model is committed to run** — i.e. after the TIER-3 risk gate and the availability check below both pass, and the model actually produces an applied diff. At that point set `AGENT_LABEL=agent:ollama` (the local model wrote the code — the label reflects the real implementer, NOT the orchestrator host) and `IMPLEMENTER_SYSTEM=ollama:<model>` (full version, e.g. `ollama:qwen2.5`), plus the versionless `model:<base>` label (e.g. `qwen2.5`→`model:qwen`, `gemma2`→`model:gemma`) per the Attribution contract. If any fallback below fires, do NOT set (or unset) the ollama attribution — the host-agent path sets its own per the effective-implementer rule.

**Risk gate (refuse on TIER-3).** 5a.ollama runs at Step 5a — *before* 5a.1/5a.2, so the definitive 5a.2 tier (computed from the diff) does not exist yet. Instead the orchestrator performs an **early pre-dispatch tier classification** from the issue's stated target paths + labels, using the **same TIER-3 pattern list as Step 5a.2** (Realm models, billing, lifecycle, `.github/workflows/` — the elevated-scrutiny set). If that pre-classification is **TIER-3**, do NOT run the local model: log `5a.ollama: TIER-3 is too high-risk for a local-model implementer — falling back to <HOST_AGENT>` and run the host-agent path instead (the default Claude subagent on Claude Code, or 5a.codex/5a.agy if `HOST_AGENT` is codex/agy). Otherwise proceed. When the issue's target paths are ambiguous, treat the pre-classification as **TIER-2** (proceed) and let the downstream 5c review gate the result. The definitive 5a.2 classification still runs later from the actual diff, exactly as for every path.

**Availability fallback.** If `ollama` is not installed (`command -v ollama` fails) or the model is not present (`ollama list` lacks `<model>`), log `5a.ollama: ollama/<model> unavailable — fell back to <HOST_AGENT>` and run the host-agent path. The host-agent path sets `AGENT_LABEL`/`IMPLEMENTER_SYSTEM`/`MODEL_BASE` to ITS own (effective-implementer rule); ollama attribution is never applied here. Never abort.

**Harness flow (orchestrator-driven):**
1. Create the worktree + branch off `origin/develop` (same isolation/path convention as the default path: `worktrees/issue-<N>`).
2. Build the generation prompt: issue title + body + acceptance criteria, plus a **scoped, size-limited** slice of the relevant repo files (local models have small context windows — include only the files named in the issue scope, truncate aggressively). Ask the model to return **a unified diff** (or full file contents for new files).
3. Invoke the model via the Ollama API — `ollama run <model>` with the prompt on stdin, or the HTTP `POST /api/generate` endpoint (reuse the endpoint/model conventions from `jury.local.toml`). Wrap in the portable `timeout` (Step 5b pattern) so a hung model cannot stall the run.
4. **Orchestrator applies** the returned edits in the worktree (`git apply` the diff, or write the files), then runs the gates (build/lint/test per the platform).
5. On success, the **orchestrator** commits with trailer `Co-Authored-By: <model> via Ollama <noreply+ollama@example.com>`, pushes (git push first, `mcp__github__push_files` on 403), and opens the **draft PR** with `Closes #<N>`. Then returns the same JSON contract (`pr_number`, `branch`, `files_changed`, `test_results`, `codename`, `worktree_path`) as the default path, with `codename = OLLAMA-<N>-<UTC_TIMESTAMP>`.
6. Step 5a.1 branch-scope validation runs exactly as for every other path.

**Retry + fallback budget.** If the model's output is not a valid/applicable diff, or the gates fail, re-prompt the model with the error appended, up to **2 retries**. On exhaustion, discard the partial work and fall back to the host-agent path; log `5a.ollama: local model failed after 2 retries — fell back to <HOST_AGENT>`. The host-agent path then sets the attribution to the effective implementer (it ran, not ollama) — do NOT leave `agent:ollama`/`ollama:<model>` set. The run never aborts on a local-model failure.

**Worktree cleanup before fallback (mandatory).** Unlike 5a.codex/5a.agy (which never create a worktree — the CLI does), 5a.ollama creates the worktree at step 1. So **any** fallback to the host-agent path that happens *after* step 1 (retry-budget exhaustion) MUST first remove the partial worktree — `git worktree remove worktrees/issue-<N> --force` — so the host path can create its own at the same conventional `worktrees/issue-<N>` path without a `git worktree add` collision. The TIER-3 refusal and availability fallback happen *before* step 1, so no worktree exists there and no cleanup is needed.

**`--dry-run`:** prefer to skip worktree creation entirely — generate the diff with a read-only model call against the current checkout (the model only needs to *read* the scoped files), do NOT apply/commit/push/open a PR, log the would-be diff and PR title/body, then skip 5b–5f like the other paths. If the orchestrator did create the step-1 worktree before the dry-run guard, it MUST remove it before returning (`git worktree remove worktrees/issue-<N> --force`) so a later real run does not hit a `worktrees/issue-<N>` collision — same cleanup obligation as the fallback path above.

**`--preview`:** after the orchestrator opens the PR (step 5), add `deploy:preview` (skip under `--dry-run`).

**What does NOT change:** the downstream 5c review (and optional 5d.jury) still gate the merge regardless of who wrote the code — a local-model PR is reviewed and gated exactly like any other. The merge lock, window gate, and tester gate are unchanged.

### 5a.1 — Branch scope validation gate

After the implementer returns, the **orchestrator** captures `BRANCH` from the implementer's declared return value and runs:

```bash
# Fetch both refs so origin/develop and origin/$BRANCH are current.
git fetch origin develop "$BRANCH" --quiet
if ! CHANGED=$(git diff origin/develop...origin/"$BRANCH" --name-only); then
  echo "WARN: scope check git diff failed — skipping check, investigate git/network state"
  CHANGED=""
fi
echo "$CHANGED"
```

Compare `$CHANGED` against the implementer's declared `files changed` list. If any path appears in `$CHANGED` but is outside issue `<N>`'s stated scope and is not in the exempt list below:

1. Print the unexpected files to the user.
2. Hand the list to the implementer subagent and ask it to explain and, if unintentional, revert them.
3. Re-execute the bash command above against the updated remote branch ref. If unexpected files persist after one correction pass, set `status:blocked`, post an issue comment quoting the unexpected file list, and skip this issue.

One correction pass is intentional and more conservative than the CI retry budget (3 rounds). By Step 5a.1, the implementer's pre-push self-check (Step 5a prompt) should already have caught scope drift — a second failure here indicates a systematic problem, not a one-off accident.

**Paths exempt from the scope check** (mandatory docs-gate targets that may legitimately appear in any PR):
- `docs/testing/POLICY.md` — JVM Test Audit Table updated alongside Android test PRs
- `web/CLAUDE.md` — test-coverage table updated alongside web implementation
- `AGENTS.md` — workflow updates required by the docs gate (AGENTS.md Step 7)
- `docs/monitoring/crashlytics-coverage.md` — Crashlytics coverage audit always updated alongside Crashlytics PRs
- `docs/architecture.md`, `docs/subsystems.md`, `docs/subsystems/` — architecture docs required by the docs gate
- `shared/schema/firebase/` — Firebase schema docs required by the docs gate

**Docs-only PRs:** if all changed paths match the Step 5b docs-only allowlist (`^\.claude/`, `^\.codex/`, `^docs/`, `^scripts/`, `.*\.md$`, `^AGENTS\.md$`, `^CLAUDE\.md$`, `^jury\.toml$`), treat the scope check as advisory — log but do not block. These PRs are validated holistically by the 5b docs-only CI path.

**Dry-run:** log `$CHANGED` to stdout; do not block.

This gate is the primary defence against branch contamination — the root cause of PR #317 being abandoned. It catches scope creep before review begins, preventing wasted reviewer-round budget.

### 5a.2 — Reviewer count auto-detect + jury enablement

**Reviewer count (early exit on `--reviewers`).** If `--reviewers` was explicitly passed, skip the tier classification below and use that value for `REVIEWERS`. **The jury-enablement decision further down still runs in every case** — it is NOT inside this early-exit guard (see "Jury enablement"). When `--reviewers` was passed, the tier is never computed, so the TIER-3 jury auto-trigger does not apply; jury enablement then falls back to `--jury` / `--no-jury` only.

Otherwise (no explicit `--reviewers`), classify the PR diff by risk tier using the file list from Step 5a.1:

**TIER-3 (always 3 reviewers — high-risk files require elevated-scrutiny gates):** any changed path matches:
  - `android/smartinventory/src/main/.*/object/db/` (Realm models)
  - `UtilPremium.kt`, `PremiumActivity.kt`, `SubscriptionAdapter.kt` (billing)
  - `User.kt`
  - `MainActivity.kt`, `DetailActivity.kt`, `ItemDetailActivity.kt`
  - `android/smartinventory/src/main/kotlin/.*/lifecycle/` or `ListFragment.kt`
  - `.github/workflows/` (CI workflow changes)

**TIER-1 (1 reviewer):** ALL changed paths match the docs-only allowlist:
  `^\.claude/`, `^\.codex/`, `^docs/`, `^scripts/`, `.*\.md$`, `^AGENTS\.md$`, `^CLAUDE\.md$`, `^jury\.toml$`

**TIER-2 (2 reviewers):** everything else (Android non-critical, web, shared schema, etc.)

Set `REVIEWERS` to the detected tier value. Log the detection result:
  `Auto-detect: TIER-<N> → REVIEWERS=<N> (reason: <matched pattern or "docs-only">)`

**Jury enablement (gating by default — issue #2033 Phase B; `--jury-advisory` for the issue #1746 advisory mode).** This decision **always runs**, regardless of whether `--reviewers` was passed — it lives OUTSIDE the `--reviewers` early-exit guard so `--jury` is never ignored. Decide whether the advisory cross-vendor AI Jury stage (Step 5d.jury) runs for this issue, in this precedence (`--no-jury` > `--jury` > TIER-3 auto > off):

- If `--no-jury` was passed ⇒ `JURY=false` (force-disable always wins).
- Else if `--jury` was passed ⇒ `JURY=true`, `JURY_REASON="--jury"`.
- Else if a tier was computed above (i.e. `--reviewers` was NOT passed) AND the detected tier is TIER-3 ⇒ `JURY=true`, `JURY_REASON="TIER-3 auto"`.
- Else ⇒ `JURY=false`.

**Jury mode (Phase B, issue #2033).** When `JURY=true`, also set `JURY_MODE`:
- `JURY_MODE=advisory` if `--jury-advisory` was passed (Phase A behaviour — posts a report, never gates).
- Else `JURY_MODE=gating` (default — verified `critical`/`major`/`minor` consensus findings gate the merge via Step 5e).

`JURY_REASON` is consumed by Step 5d.jury to pick the run depth. In **advisory** mode: `TIER-3 auto` ⇒ native risk-scaled `--auto`, any other reason ⇒ the fast `--rounds 1 --no-verify` variant. In **gating** mode the depth is always the full verify run (`rounds=2`, `verify=true` from `jury.toml`) regardless of reason — the gate signal must be verified.

Note: the TIER-3 auto-trigger depends on the tier, which is only computed when `--reviewers` was NOT passed. So when `--reviewers N` IS passed, there is no tier-based auto-enable — jury enablement falls back to `--jury` / `--no-jury` only.

Log the decision:
  `Jury: enabled (reason: --jury | TIER-3 auto; mode: gating | advisory) / disabled`

The jury never changes `REVIEWERS`. In **advisory** mode it never gates the merge and never consumes a review-fix round. In **gating** mode (default) its *verified consensus* `critical`/`major`/`minor` findings DO gate the merge and a jury-driven fix consumes one review-fix round, exactly like a 5c finding (see Step 5d.jury / 5e).

### 5b. CI gate

**`GH_MODE=mcp` fallback.** When `GH_MODE=mcp`, replace the `gh pr checks` invocation with `mcp__github__pull_request_read(method=get_check_runs, pullNumber=<PR>)` and interpret the result with the same three-branch semantics described below. The branch-enumeration, fix-and-reply loop, and retry budgets are mode-agnostic; only the polling tool differs.

**Evaluate the rules below in order; the first matching rule wins.** A mixed state (some runs `queued`/`in_progress` plus at least one `failure`) fires the failure rule, NOT the pending rule — never poll while a failure has already been observed.

- **Empty `check_runs` array** ⇒ behaves like branch 1 (no checks scheduled). Apply the same docs-only allowlist check as branch 1 (the set including the anchored `^jury\.toml$` entry); otherwise mark `status:blocked` per branch 1.
- **Any run has `conclusion` in `{failure, cancelled, timed_out, action_required}`** ⇒ behaves like branch 3 (failed), enter the fix-and-reply loop. **Residual-gap caveat:** raw failure logs (`gh run view --log-failed`) are not exposed via MCP — the implementer subagent gets only the check name + `details_url` and must reproduce the failure locally. If reproduction is not feasible, mark `status:blocked` with the `details_url`(s) quoted (per § CI Health degrade).
- **Any run still has `status` in `{queued, in_progress}`** ⇒ behaves like the pending sub-branch of branch 3. Poll with a 30 s delay between calls; honour the same 30-minute hard timeout (no MCP equivalent of `--watch`).
- **All runs have `conclusion` in `{success, skipped, neutral, stale}`** ⇒ behaves like branch 2 (all checks succeeded), proceed to 5c. `stale` is included as non-blocking per GitHub's own UI semantics (Dependabot / merge-queue mark superseded check runs `stale`).

Both the `gh pr checks` and `mcp__github__pull_request_read(method=get_check_runs)` paths share the **per-issue CI retry budget** (3 fix-and-push rounds) and **session-wide CI cooldown** described below.

The `GH_MODE=cli` polling form:

```bash
gh pr checks <PR> --json statusCheckRollup --jq '.statusCheckRollup'
```

Three branches:

1. **Empty array** (no checks scheduled — typical for path-filtered PRs that touch only `.claude/`, `.codex/`, `*.md`, `AGENTS.md`, `CLAUDE.md`, `scripts/`, `.github/` non-workflow files):
   - Allow if every changed path matches the docs-only allowlist:
     ```
     ^\.claude/   ^\.codex/   ^docs/   ^scripts/   .*\.md$   ^AGENTS\.md$   ^CLAUDE\.md$   ^jury\.toml$   ^\.github/(?!workflows/)
     ```
     The `^jury\.toml$` entry is anchored so it exempts ONLY the root jury config from CI — it does NOT use a broad `.*\.toml$`, which would wrongly exempt build config such as `gradle/libs.versions.toml`. The `^\.github/(?!workflows/)` clause covers `CODEOWNERS`, `dependabot.yml`, `ISSUE_TEMPLATE/`, `PULL_REQUEST_TEMPLATE.md` while keeping the strict gate on workflow files. `^scripts/` covers repo-portable dev tooling shell helpers that have their own minimal CI (`scripts-ci.yml`) — the gate still fires if `scripts-ci.yml` itself fails or stays pending.
   - Otherwise mark `status:blocked`, comment on the issue ("CI did not run on a non-docs PR — investigate workflow path filters"), skip this issue.

2. **All checks succeeded:** proceed to 5c.

3. **Any check failed or pending:** if pending, watch with a hard timeout. Resolve `timeout` portably (macOS does not ship GNU `timeout`):
   ```bash
   if command -v timeout >/dev/null 2>&1; then
     TIMEOUT_BIN=timeout
   elif command -v gtimeout >/dev/null 2>&1; then
     TIMEOUT_BIN=gtimeout
   else
     echo "ERROR: install coreutils (brew install coreutils) for gtimeout" >&2
     exit 3
   fi
   "$TIMEOUT_BIN" 1800 gh pr checks <PR> --watch --interval 60 || { echo "CI timeout"; exit 3; }
   ```
   `coreutils` is a hard runtime prerequisite on macOS — do NOT skip the timeout.
   If failed, invoke the same fix-and-reply loop as `/pr-loop` Steps 4–6 (read failed log, fix, self-review per `AGENTS.md` step 6, push). Re-enter 5b after the fix.

**Per-issue CI retry budget:** 3 fix-and-push rounds. Exceeding it sets `status:blocked` and skips this issue.

**Per-issue review-round budget:** 3 review-fix rounds in Step 5e (full A/B/C BLOCKER loop OR narrowed Should-fix re-review). Tester-triggered loop-back (Step 5e.bis) and merge-conflict resolution (Step 5f.0) DO NOT consume budget by default — they are defensive rather than remediating. However, if a tester or merge-conflict reviewer surfaces a BLOCKER that requires an implementer fix, that fix consumes a budget round. Exceeding the budget sets `status:blocked` with the latest blocker/SUGGESTION list quoted in the comment (if the rounds were consumed by SUGGESTION fix cycles with no outstanding BLOCKERs, the pending SUGGESTIONs are quoted instead) and skips this issue.

**Session-wide CI cooldown:** 3 consecutive issues hitting either per-issue budget without recovery ⇒ abort the session, write the partial report. The session-wide counter resets after any successful merge.

### 5c. N reviewer agents (return-only, parallel within issue)

**Reviewer vendor (`--review-delegate`, default `HOST_AGENT`).** The reviewer vendor defaults to the host agent (the CLI driving this run; see § Host-agent default) — so a Claude-Code run reviews with Claude, a Codex run with Codex, unless overridden. When the resolved vendor is `claude`, run the canonical path below — `REVIEWERS` `code-reviewer` Claude subagents. When it is `codex`, `agy`, or `ollama:<model>`, run the **same focus map and reviewer count** but route each reviewer through that vendor's CLI in **read-only** mode (the jury-style external invocation: codex `-s read-only`, agy `--sandbox`, or the local ollama endpoint), passing the canonical reviewer rubric as the prompt. The vendor reviewer **returns findings only** — it never writes to the PR; the orchestrator posts the per-reviewer comment at Step 5d exactly as for the Claude path (orchestrator-only-writes holds for every vendor). If the chosen vendor CLI is missing or errors, log `5c: review-delegate <vendor> unavailable — falling back to claude code-reviewer` and run the canonical Claude path. This reviewer-vendor routing is distinct from Step 5d.jury (which convenes ALL vendors as an advisory panel); `--review-delegate` swaps the vendor of the *gating* 5c reviewers.

Spawn `REVIEWERS` `code-reviewer` subagents in **a single Agent tool message** so they run concurrently in Claude Code. Codex executing this command should also issue a single batched call where possible; if Codex serialises, that is acceptable as long as no reviewer reads another reviewer's output (each must get a fresh codename and the `do-not-read` instruction below).

Focus map:

| REVIEWERS | Spawn |
|-----------|-------|
| 3 | A (logic + null-safety + Kotlin/Java interop) · B (platform + lifecycle + Realm + threading) · C (test coverage + docs gate + scope creep + CI prediction) |
| 2 | A (logic + null-safety + Kotlin/Java interop + platform + lifecycle + Realm + threading) · B (test coverage + docs gate + scope creep + CI prediction + security) |
| 1 | A (all dimensions: correctness + platform + Realm + billing + docs gate + scope + CI prediction — used only for docs/config PRs where scope is narrow and a single reviewer can hold the full picture) |

**Coverage invariant:** every reviewer dimension (logic, platform/Realm, docs/scope/CI) is covered regardless of reviewer count. When count decreases, responsibilities merge — they do not drop. A 2-reviewer setup covers the same dimensions as 3; a 1-reviewer setup is only appropriate for docs/config PRs auto-detected as TIER-1.

Focus label mapping when REVIEWERS < 3:
- 2 reviewers: Reviewer A label = "A+B (logic/platform/Realm)", Reviewer B label = "B+C (docs/scope/CI)"
- 1 reviewer: Reviewer A label = "A+B+C (full review)"
Use these labels in the Step 5d PR comment headers and in the reviewer codename suffix.

Each reviewer receives the canonical reviewer template from
`AGENTS.md` § [Reviewer Rubric (canonical)](../../AGENTS.md#reviewer-rubric)
with `<PR>`, `<FOCUS>`, and `<PR_HEAD_SHA>` filled in. The orchestrator MUST
substitute real values for those three placeholders before sending each
prompt.

See the rubric for the verdict vocabulary (BLOCKER ≡ Must fix), the return
format (VERDICT / CODENAME / FOCUS / FINDINGS), the PR-head-SHA verification
block (methods (a) / (b) / (c)), and the no-cross-reading rule. The
orchestrator-only-writes override (reviewers MUST NOT call `gh pr comment`,
`gh pr review`, or any other GitHub write API; return findings only) is the
canonical default within `/ship` and lives in the rubric under
"Orchestrator-only-writes override" — `/ship` Step 5d below posts the
per-reviewer comments instead.

Generate a fresh codename per reviewer (`REVIEW-<N>-<UTC_TIMESTAMP>-<focus>`).

**Capture reviewer attribution (issue #2036).** Record `REVIEWER_VENDOR` (the resolved `--review-delegate` value, default `HOST_AGENT`: `claude` / `codex` / `agy` / `ollama`) and `REVIEWER_MODEL` (the model when known — the `ollama:<model>` model string, or the vendor CLI's reported/`delegate-model` model; else `unknown`) for each reviewer. These feed the Step 5d comment header and the Step 5f.1 closure comment. If a `--review-delegate` vendor fell back to claude (CLI missing), record the EFFECTIVE vendor that actually ran (`claude`), not the requested one.

Maintain three **parallel arrays in lock-step** — append all three atomically per reviewer so the Step 5f.1 closure loop can zip them by index:

```bash
REVIEWER_CODENAMES+=("$CODENAME"); REVIEWER_VENDORS+=("$REVIEWER_VENDOR"); REVIEWER_MODELS+=("${REVIEWER_MODEL:-unknown}")
```

### 5d. Post per-reviewer comments (orchestrator does this)

After all reviewers return, post **one PR comment per reviewer** so the timeline shows N independent reviews:

```bash
gh pr comment <PR> --body "## Review Round <X> — Reviewer <CODENAME> (vendor: <REVIEWER_VENDOR>[, model: <REVIEWER_MODEL>], focus: <A|B|C>)

**Verdict:** <verdict>

<findings table>"
```

The findings table renders the BLOCKER/SUGGESTION/NIT rows the reviewer returned.

Reviewers never write to the PR; only the orchestrator does. (`AGENTS.md` step 9 reviewer template carries a `/ship` exception note immediately after the template; this override is the canonical default within `/ship`.)

If `--dry-run`: skip the `gh pr comment` calls; log the would-be bodies to stdout.

### 5d.jury — Advisory cross-vendor jury (opt-in / TIER-3 auto)

Runs **only when `JURY=true`** (set at Step 5a.2), **after Step 5d posts the reviewer comments and before the Step 5e loop-exit decision**. It produces a cross-vendor review report, the orchestrator posts it as one PR comment, and the chair verdict is logged. Behaviour depends on `JURY_MODE` (Step 5a.2):
- **Gating** (default, issue #2033 Phase B): verified consensus `critical`/`major`/`minor` findings feed Step 5e and gate the merge (a jury-driven fix consumes one review-fix round), exactly like 5c findings.
- **Advisory** (`--jury-advisory`, issue #1746 Phase A): never gates, never consumes a round.
Either way it is a *sibling* of the Step 5c reviewers, not a replacement; the canonical 5c fan-out always participates in the Step 5e decision. **Fail-soft is absolute:** any jury run that does not complete cleanly contributes nothing and cannot gate.

**Preflight (never block):**

- `command -v jury` — if absent, log `jury: CLI not installed, skipping advisory stage` and continue. The jury never blocks the pipeline. This is the ONLY *required* preflight check; vendor participation cannot be known pre-hoc (the orchestrator only learns which agents actually ran after the jury finishes), so the sub-2-vendor case is detected POST-HOC below rather than as a preflight gate.
- *(optional, advisory)* `jury --config-validate` (ai-jury ≥ 1.1.0) confirms the committed `jury.toml` parses under the installed CLI version; a non-zero exit is logged (`jury: config validation failed — skipping advisory stage`) and the stage is skipped like any other failure. This is best-effort diagnostics only and MUST NOT block `/ship`.

**Fail-soft guarantee (operator requirement):** the advisory jury is strictly best-effort. ANY failure of the stage — CLI not installed, `jury.toml` missing, a non-zero exit, a timeout, or ai-jury failing because too few agents are available — is logged and skipped; `/ship` continues through Step 5e exactly as if the jury stage did not exist. The jury can never block, delay, or fail the issue or the merge. The pre-jury (canonical 5c reviewer) flow is fully self-sufficient and is what gates the merge.

**Invoke (read-only — orchestrator-only-writes):** the jury runs read-only against the open PR using the committed root `jury.toml` panel, producing a markdown report to a temp file. The panel degrades gracefully: the `jury` CLI skips missing agents with a warning and the run continues (the orchestrator does NOT pass `--strict`). Do NOT pass `--post-summary` — the `/ship` orchestrator posts the comment itself via the same `gh pr comment` path as Step 5d.

```bash
JURY_REPORT="${CLAUDE_JOB_DIR:-$TMPDIR}/tmp/jury-report-<PR>.md"
mkdir -p "$(dirname "$JURY_REPORT")"
# Resolve a portable timeout wrapper the same way Step 5b does (macOS does not
# ship GNU `timeout`) so a hung agent cannot stall the merge. A timeout is
# treated identically to any other failure: log + skip.
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN=timeout
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN=gtimeout
else
  TIMEOUT_BIN=""   # no wrapper available — run unwrapped rather than gate the advisory stage
fi
# Optional best-effort config validation (ai-jury >= 1.1.0). Self-contained so a
# validation failure skips the stage here rather than falling through into the
# main invocation. Advisory only — never blocks /ship. `--config-validate` is a
# no-op on CLIs older than 1.1.0 that don't recognise it (non-zero exit ⇒ skip,
# same fail-soft path). Set JURY_SKIP to short-circuit the invocation below.
JURY_SKIP=false
if ! jury --config-validate >/dev/null 2>"$JURY_REPORT.err"; then
  echo "jury: config validation failed — skipping advisory stage, pipeline continues. See $JURY_REPORT.err"
  JURY_SKIP=true
fi
# Mode + depth selection (issue #2033 Phase B), from JURY_MODE / JURY_REASON (Step 5a.2).
# Build the run args as a bash ARRAY so paths with spaces (TMPDIR / CLAUDE_JOB_DIR)
# survive the `-o <path>` pairing — an unquoted string would word-split and break it.
JURY_JSON="${CLAUDE_JOB_DIR:-$TMPDIR}/tmp/jury-findings-<PR>.json"
if [ "$JURY_MODE" = gating ]; then
  # GATING (default): full debate + verify (rounds=2, verify=true from jury.toml)
  # so the gate signal is verified; --ci sets the severity-gated exit code per
  # [jury.ci] fail_on=["critical","major","minor"] + ignore_unverified=true.
  # Structured JSON is the source of truth for both the gate and the posted summary.
  JURY_RUN_ARGS=(--ci --format json -o "$JURY_JSON")
elif [ "$JURY_REASON" = "TIER-3 auto" ]; then
  # ADVISORY (--jury-advisory), TIER-3: risk-scaled native depth.
  JURY_RUN_ARGS=(--auto --format markdown -o "$JURY_REPORT")
else
  # ADVISORY (--jury-advisory), non-TIER-3: fast, cheap. Never gates.
  JURY_RUN_ARGS=(--rounds 1 --no-verify --format markdown -o "$JURY_REPORT")
fi
# Native execution budgets + transient-failure retry (ai-jury >= 1.1.0) so a
# slow/hung vendor cannot stall the merge from inside the CLI. The external
# TIMEOUT_BIN wrapper below stays as a hard backstop and fires LAST: its 1800s
# is deliberately greater than --total-timeout (1740s) so ai-jury times out
# gracefully first and only a truly wedged process hits the wrapper's hard kill.
JURY_BUDGET_ARGS=(--total-timeout 1740 --phase-timeout 600 --retries 1)
# Capture the REAL exit code (a negated `if ! ...` would clobber $? with the test
# result). Skip the invocation if config validation already failed above.
if [ "$JURY_SKIP" = true ]; then
  JURY_EXIT=2   # config-validate failed; treat as a hard failure (fail-soft skip)
else
  ${TIMEOUT_BIN:+$TIMEOUT_BIN 1800} jury --pr <PR> "${JURY_RUN_ARGS[@]}" "${JURY_BUDGET_ARGS[@]}" 2>"$JURY_REPORT.err"
  JURY_EXIT=$?
fi
# Exit-code interpretation — fail-soft is paramount: a jury that did not run
# CLEANLY can NEVER gate (an absent/erroring jury must not manufacture a block).
#   exit 0   => clean: advisory ran, OR gating ran with NO gating findings.
#   exit 1   => GATING ONLY: the [jury.ci] gate fired (verified critical/major/minor
#               consensus present) — real findings to escalate into Step 5e. In
#               ADVISORY mode exit 1 is just a run failure (advisory has no gate).
#   exit >=2 => bad config / timeout (124) / interrupt (130) => HARD FAILURE in
#               either mode => log + skip, the jury contributes nothing and does
#               NOT gate.
JURY_GATE_FINDINGS=false
if [ "$JURY_MODE" = gating ] && [ "$JURY_EXIT" -eq 1 ] && [ -f "$JURY_JSON" ]; then
  JURY_GATE_FINDINGS=true   # provisionally gating; sub-2-vendor downgrade below may clear it
elif [ "$JURY_EXIT" -ne 0 ]; then
  echo "jury: run failed (exit $JURY_EXIT) — skipping jury stage, pipeline continues; jury does NOT gate. See $JURY_REPORT.err"
  JURY_REPORT=""; JURY_JSON=""   # signal: nothing to post, nothing to gate
fi
# Sub-2-vendor downgrade — MUST run BEFORE the comment is assembled so the posted
# verdict matches what is actually enforced. A single-vendor "jury" is weak
# cross-vendor signal and must not block a merge: count distinct vendors that
# actually participated (from the JSON in gating mode) and, if <2, downgrade the
# gate to advisory for this run.
if [ "$JURY_GATE_FINDINGS" = true ]; then
  VENDOR_COUNT=$(jq -r '[.reviewers[]?.vendor] | unique | length' "$JURY_JSON" 2>/dev/null || echo 0)
  if [ "${VENDOR_COUNT:-0}" -lt 2 ]; then
    echo "jury: <2 vendors participated — gating downgraded to advisory (low-confidence panel)"
    JURY_GATE_FINDINGS=false
  fi
fi
```

**Depth by mode.** In **gating** mode (default) the run is always the full debate-plus-verify panel (`rounds=2`, `verify=true` from `jury.toml`) with `--ci --format json` — the gate signal must be *verified*, so the cheap variants are not used. In **advisory** mode (`--jury-advisory`) the cheap Phase A variants apply: `--rounds 1 --no-verify` for explicit `--jury` on non-TIER-3 PRs, native risk-scaled `--auto` on TIER-3 (ai-jury ≥ 1.1.0). Advisory never gates regardless of depth.

**Post (orchestrator):** assemble exactly ONE PR comment and post it with `--body-file` (never interpolate untrusted report text into a `--body "$(...)"` argument — shell-injection hazard). The header reflects the mode and, in gating mode, the gate outcome:
- **Advisory mode:** header `## Advisory AI Jury — JURY-<N>-<UTC_TIMESTAMP>` + an "advisory only — does not gate this merge" line + the markdown `$JURY_REPORT`.
- **Gating mode:** header `## AI Jury (gating) — JURY-<N>-<UTC_TIMESTAMP>` + a one-line verdict (`Gate: PASS` when `JURY_GATE_FINDINGS=false`, or `Gate: BLOCKED — N verified finding(s)` when true) + a findings table rendered from `$JURY_JSON` (`consensus[]`: severity / file:line / claim / verification_status). Render the table from the JSON; do not re-run the jury.

The verdict line below reflects the FINAL `JURY_GATE_FINDINGS` (after the sub-2-vendor downgrade above), so the posted comment never claims a block the pipeline then ignores:

```bash
ASSEMBLED="${CLAUDE_JOB_DIR:-$TMPDIR}/tmp/jury-comment-<PR>.md"
if [ "$JURY_MODE" = gating ] && [ -n "$JURY_JSON" ] && [ -f "$JURY_JSON" ]; then
  if [ "$JURY_GATE_FINDINGS" = true ]; then
    VERDICT_LINE="Gate: BLOCKED — $(jq -r '[.consensus[]? | select(.severity=="critical" or .severity=="major" or .severity=="minor")] | length' "$JURY_JSON" 2>/dev/null || echo '?') verified finding(s)"
  else
    VERDICT_LINE="Gate: PASS"
  fi
  { printf '## AI Jury (gating) — JURY-<N>-<UTC_TIMESTAMP>\n\n%s\n\n' "$VERDICT_LINE"
    printf '| Severity | Location | Claim | Verified |\n|---|---|---|---|\n'
    # Render the consensus[] findings table straight from the JSON (no jury re-run):
    jq -r '.consensus[]? | "| \(.severity) | \(.file // "—"):\(.line // "—") | \(.claim) | \(.verification_status) |"' "$JURY_JSON" 2>/dev/null
  } > "$ASSEMBLED"
  gh pr comment <PR> --body-file "$ASSEMBLED"
elif [ -n "$JURY_REPORT" ] && [ -f "$JURY_REPORT" ]; then
  { printf '## Advisory AI Jury — JURY-<N>-<UTC_TIMESTAMP>\n\n'
    printf 'Advisory only — does not gate this merge (--jury-advisory).\n\n'
    cat "$JURY_REPORT"
  } > "$ASSEMBLED"
  gh pr comment <PR> --body-file "$ASSEMBLED"
else
  echo "jury: no report to post — skipping comment, pipeline continues."
fi
```

Then **log** which agents actually participated and the chair verdict, e.g. `Jury verdict: <verdict> (mode: gating; participants: claude, codex; agy skipped)`.

**Feed Step 5e (gating mode only).** When `JURY_GATE_FINDINGS=true`, hand the verified `consensus[]` findings to Step 5e: `critical`/`major` ⇒ BLOCKER, `minor` ⇒ SUGGESTION (gated like a 5c SUGGESTION), `nit` ⇒ advisory (logged, not gating). Step 5e treats them exactly like 5c findings — a jury-driven fix consumes one review-fix round (cap 3). In **advisory** mode the stage does NOT consume budget and does NOT gate; Step 5e ignores it.

**`--dry-run`:** skip the `gh pr comment --body-file` call; log the would-be comment body and the `jury ...` command. The jury MAY still run read-only to produce findings (it writes nothing to GitHub); under `--dry-run` the gate is **logged but not enforced** (no merge happens under dry-run anyway).

**Re-run efficiency (gating loop).** When the gating jury re-runs after a 5e fix, ai-jury ≥ 1.1.0 keeps it cheap: `--incremental` reviews only the changes since the last jury run, `--cache` reuses prior per-agent results (`jury cache clear` resets). Keep the deterministic `decision = "chair"` + `verify` path (jury.toml) so the gate is reproducible against the committed `seed`.

### 5e. Decide loop exit

Parse each reviewer's findings tolerantly — accept both the `BLOCKER | …` form (Step 5c template) and standard markdown table rows that prepend a leading `|`. Recommended regex (PCRE/extended):

```
^\s*\|?\s*(BLOCKER|Must\s+fix)\s*\|
```

Substring matches in description text do NOT count — anchor at the row start. The `Must fix` alternative covers the `code-reviewer` agent's canonical output vocabulary (see AGENTS.md § Standard Issue Lifecycle step 9 vocabulary-reconciliation note: BLOCKER ≡ Must fix).

**Jury gating findings (issue #2033 Phase B).** If Step 5d.jury ran in **gating** mode and set `JURY_GATE_FINDINGS=true`, fold its verified `consensus[]` findings into the aggregation below alongside the 5c reviewer findings, mapping severity to the 5c vocabulary: jury `critical`/`major` ⇒ **BLOCKER**, jury `minor` ⇒ **SUGGESTION**, jury `nit` ⇒ advisory (logged, never gates). A jury-driven fix consumes one review-fix round exactly like a 5c finding (cap 3). When re-running the loop, the gating jury re-runs at Step 5d.jury on the new HEAD (with `--incremental`/`--cache`) just as the 5c reviewers re-run. In **advisory** mode (or when the jury did not run / failed), there are no jury gating findings and this paragraph is a no-op — the jury never gates. Unverified jury findings and `nit`s are never folded in.

Then (aggregating 5c reviewer findings AND any gating-jury findings per the paragraph above):

- **Any reviewer has a blocker finding** ⇒ full loop:
  1. Aggregate all blocker findings across reviewers and hand them to the implementer subagent.
  2. Implementer fixes, runs gates, self-reviews (`AGENTS.md` step 6), pushes.
  3. Restart **5b → 5c (full A/B/C fan-out) → 5d → 5e**. Increment round counter `<X>`.
  4. If round counter exceeds the per-issue review-round budget (3, see Step 5b), set `status:blocked` with the latest blocker/SUGGESTION list quoted in the issue comment and skip this issue.
- **Any reviewer has a SUGGESTION (Should-fix) finding** ⇒ it is gated **the same as a BLOCKER** (operator decision 2026-05-31): the orchestrator MUST apply every SUGGESTION before merge. It may NOT unilaterally decline to apply one, relabel it "advisory / non-blocking / flake", or skip it. The ONLY way to leave a SUGGESTION unapplied is an **explicit user decision to defer** it (recorded as a tracked GitHub issue AND surfaced to the user) — exactly as a BLOCKER can only be waived by the user. Apply via the **narrowed re-review** mechanism (introduced in issue #257):
  1. The orchestrator must retain the Round-1 reviewer codename → focus map in session state (already needed for Step 5d comment posting) so the narrowed re-review prompt can name the original reviewer the fix-up answers. Identify the originating reviewer focus(es) for the Should-fix items being applied. Carry the source codename forward (e.g. `REVIEW-247-…-C`) so the audit trail names which review each fix-up answers.
  2. Aggregate the to-be-applied Should-fix findings; hand them to the implementer subagent. Implementer fixes, runs gates, self-reviews (step 6), pushes.
  3. Restart **5b** (CI gate on the new HEAD).
  4. Compute the next round number (`<X> + 1`) and use it in the prompt's `Round <X> re-review` template; commit the increment in sub-step 6 below. Then restart a **narrowed** Step 5c: spawn ONLY the originating reviewer focus(es) — NOT a full A/B/C fan-out. Each narrowed reviewer gets a fresh codename (`REVIEW-<N>-<UTC_TIMESTAMP>-<focus>`) and a prompt that explicitly says: "Round `<X>` re-review. Verify ONLY the Should-fix items applied in commit `<sha>`. Do NOT re-review the parts of the PR you already approved in your prior review (codename `<original codename>`)." This keeps the audit trail honest while not consuming reviewer A/B slots when only C had findings. If multiple originating focuses are being narrowed, spawn them in a single Agent-tool message (parallel fan-out, same as full Step 5c). If only one, a single Agent invocation is sufficient.
  5. Post the narrowed reviews via Step 5d (one comment per narrowed reviewer).
  6. Increment round counter (the narrowed re-review always consumes one round per the cap). Then decide loop exit per the same logic — if the narrowed reviewer now flags a `BLOCKER`, escalate back to the full loop above. If the narrowed reviewer surfaces NEW SUGGESTIONs, they are gated the same way (apply them, or obtain explicit user deferral) before exit. NITs SHOULD be applied where reasonable; an unapplied NIT should be noted in the exit summary but does NOT gate the exit. Restart this narrowed branch for each new applied finding (subject to the budget cap).
- **No blockers AND every SUGGESTION applied (or explicitly user-deferred) AND CI green** ⇒ exit loop. Proceed to tester. Before exiting, the orchestrator MUST surface any deferred SUGGESTION/NIT items together with the user decision or tracked-issue number that authorised each deferral — a silent skip is a process violation.

The narrowed re-review consumes one round of the per-issue review-round budget (still capped at 3, per Step 5b). The full A/B/C BLOCKER loop and the narrowed Should-fix re-review both count; the merge-conflict re-review in Step 5f.0 and the tester loop-back in Step 5e.bis do NOT count by default (they are defensive). However, a BLOCKER surfaced by either defensive reviewer DOES consume a round when the implementer applies the fix. If a Round-3 narrowed reviewer flags `BLOCKER`, the fix consumes the final budget slot and any further escalation hits the cap and produces `status:blocked`. This is intentional — three independent reviewer-touch rounds is the design ceiling. Rationale: every code commit after Round 1 introduces unverified state; CI green and the tester gate are not substitutes for a focused independent review of the new diff. Full A/B/C re-review would be wasteful when only one focus's findings drove the fix-up.

### 5e.bis Tester gate (manual-test list, lifecycle step 10)

Spawn the `tester` subagent. Its job is the manual-test list (CI is already green from 5b). If tester returns `status:needs-fix` (e.g. it discovered a flaky check or a path-filter gap that 5b missed), loop back to the implementer — this is a defensive loop-back and does NOT consume a round of the per-issue review-round budget by default. Exception: if the tester's findings constitute a BLOCKER that requires an implementer fix, that fix DOES consume a budget round (it is now a remediation, not a defensive check). Either way, this is a loop-back, NOT a merge.

Set label `status:needs-test` while tester runs. On tester clear, **leave the
label at `status:needs-test`** — `status:done` is set by Step 5f only after a
successful merge (see AGENTS.md § Issue Status Label Lifecycle: `status:done`
≡ merged + closed). This is the change that lets us run review/tester
overnight: the issue stays at a stable `status:needs-test` "work complete,
awaiting merge" state during the deferral window, and Step 5f flips it to
`status:done` when it actually merges.

If `--dry-run`: skip every `gh issue edit --add-label` / `--remove-label` call; log the would-be label transitions to stdout. This applies to all label edits in `/ship` (Step 5a `status:in-progress`, Step 5e.bis `status:needs-test`/`status:done`, Step 5b/5e/5f `status:blocked`).

### 5f.0 — Mergeability prep (BEFORE the lock)

**Precondition**: Step 5e.bis (tester gate) must have cleared with `status:done`. Step 5f.0 MUST NOT start before Step 5e.bis exits. The carve-out language treating Step 5e.bis tester loop-back and Step 5f.0 merge-conflict loop-back as siblings of the same "defensive" class is a classification, NOT a parallel-execution permission.

This sub-step runs **outside** the merge.lock and **outside** the single `bash -c '...'` block of Step 5f.1. Step 5f.0 deliberately runs BEFORE lock acquisition and may legitimately span multiple Bash + Agent tool calls (subagent dispatch, CI waits, narrowed reviewer fan-out); Step 5f.1 (the merge call) is the only part that must remain a single bash block. The Known limitations entry on the trap-scope split (see Known limitations § 'Trap scope across multi-call execution', now mitigated by this 5f.0/5f.1 split) is load-bearing for this design. The point is to keep the lock-holding window bounded to the literal merge sequence (typically seconds), so concurrent `/ship` invocations cannot starve each other when one is resolving a 20–40 minute merge conflict.

1. Re-fetch and assert mergeability with explicit field extraction:
   ```bash
   IFS=$'\t' read -r BASE STATE DRAFT <<<"$(gh pr view <PR> --json baseRefName,mergeStateStatus,isDraft \
     --jq '[.baseRefName, .mergeStateStatus, (.isDraft|tostring)] | @tsv')"
   ```
   `BASE` MUST equal `develop` (refuse otherwise).

2. Read `mergeStateStatus` (`STATE`).

3. If `STATE` is `BEHIND` or `DIRTY`:
   - Hand the conflict / behind-state to the implementer subagent. Implementer merges develop into the work branch — use `git merge --no-ff origin/develop` rather than rebase; the merge commit is for the feature-branch ref's audit trail (the squash-merge to develop will collapse it, but the per-PR-branch history retains the integration point for forensic analysis) — resolves any conflicts, runs gates, pushes.
   - Wait for CI green on the new HEAD (Step 5b).
   - **Spawn a single focused `code-reviewer`** on the merge commit's diff (introduced in issue #257). Default focus = **B** (platform / lifecycle / threading) because most conflict resolutions touch lifecycle-adjacent files. If the conflict diff makes another focus more relevant (e.g. test files only ⇒ focus C; pure-logic file ⇒ focus A), substitute that focus. Codename: `REVIEW-<N>-<UTC_TIMESTAMP>-MERGE`. Prompt explicitly tells the reviewer: "Review ONLY the merge commit `<sha>`'s conflict-resolution diff. Confirm the resolution preserves the intent of both sides; flag any silent behavioural change."
   - Post the merge-review comment via Step 5d.
   - If the merge reviewer returns a `BLOCKER` finding ⇒ loop back to the implementer; on push, re-enter Step 5f.0 step 1 (re-fetch mergeability) and start a fresh step-3 iteration. The implementer fix consumes one budget round per the rule above. If clean ⇒ proceed.
   - The merge-conflict re-review itself does NOT consume budget by default (it is defensive — reactive to develop drift, not to a reviewer finding). Only the BLOCKER-driven implementer fix consumes a round.
   - **Iteration cap**: max 2 BEHIND/DIRTY re-merge iterations per `/ship` cycle for this issue. On exceeding, mark `status:blocked` with reason `develop drift exceeded merge-prep budget` and append to `morning-merge-queue-<DATE>.md` (per Step 4 deferred-issue handling). This bound exists to give the state machine a liveness guarantee even in pure-drift scenarios where the carve-out means no budget is consumed by pure-drift loops.

4. Re-fetch mergeability one more time after the (possibly multiple) iterations of step 3 — `STATE` must now be `CLEAN`, `HAS_HOOKS`, or another non-conflicting value, and NOT `BEHIND`/`DIRTY`. If still conflicting after the budget exhausts, mark `status:blocked` and skip this issue.

4.5. **Pre-merge worktree cleanup (per #803, Option 1).** Runs AFTER step 4 (final mergeability re-fetch passes) and BEFORE Step 5f.1 lock acquisition. Removes the implementer's worktree so the subsequent `gh pr merge --delete-branch` inside Step 5f.1 does not fail on a held local-branch ref.

   ```bash
   # Step 5f.0 step 4.5 — pre-merge worktree cleanup (per #803, Option 1).
   # Runs AFTER any BEHIND/DIRTY iteration completes successfully and the final
   # mergeability re-fetch passes. Runs BEFORE Step 5f.1 lock acquisition.

   WORKTREE_PATH=$(echo "$IMPLEMENTER_RETURN_JSON" | jq -r '.worktree_path // empty')
   REPO_ROOT="$(git rev-parse --show-toplevel)"

   if [[ -z "$WORKTREE_PATH" ]]; then
     echo "[5f.0] pre-cleanup: skipped (no worktree_path in implementer return)"
   elif [[ ! -d "$WORKTREE_PATH" ]]; then
     echo "[5f.0] pre-cleanup: skipped (path no longer exists)"
   else
     # Hard guards (updated per #931 — nested under repo root, never the repo
     # root itself, never outside the repo root, never the filesystem root).
     case "$WORKTREE_PATH" in
       "$REPO_ROOT")
         echo "[5f.0] FAIL: implementer returned the repo root itself ($WORKTREE_PATH); refusing"
         exit 1
         ;;
       "$REPO_ROOT"/worktrees/*)
         # The only accepted shape: nested under <repo-root>/worktrees/.
         ;;
       /)
         echo "[5f.0] FAIL: implementer returned root path; refusing"
         exit 1
         ;;
       *)
         echo "[5f.0] FAIL: worktree_path ($WORKTREE_PATH) is not under \$REPO_ROOT/worktrees/ (per #931 nested-worktree convention); refusing"
         exit 1
         ;;
     esac
     # Registered-worktree assertion via git's own check.
     if ! git worktree list --porcelain | grep -qE "^worktree $WORKTREE_PATH$"; then
       echo "[5f.0] FAIL: $WORKTREE_PATH is not a registered worktree; refusing"
       exit 1
     fi
     # Safe to remove.
     git worktree remove "$WORKTREE_PATH" --force
     echo "[5f.0] pre-cleanup: removed $WORKTREE_PATH"
   fi
   ```

   Hard-fail (`exit 1`) on guard violations; the issue gets `status:blocked`, the operator sees the failure in stdout, and the merge does NOT proceed. This is intentional — a malformed `worktree_path` indicates a broken implementer prompt, not a transient error. The three skip log lines (`skipped (no worktree_path...)`, `skipped (path no longer exists)`) are non-fatal and degrade gracefully to the Step 5f.1 Option 4 fallback (PR #794).

   **Path shape (per #931):** the only accepted `worktree_path` is `<repo-root>/worktrees/<slug>` (any value under the gitignored `worktrees/` subdirectory). The repo root itself, the filesystem root `/`, and any path outside `<repo-root>/worktrees/` (including the deprecated `../smartinventory-<N>` sibling form) are rejected. Existing sibling worktrees from before #931 stay until their PRs merge but cannot be the `worktree_path` for a new `/ship` invocation.

5. THEN proceed to Step 5f.1 (lock acquisition).

### 5f.1 — Merge serialisation gate (lock + literal merge)

Acquire a mutex via `mkdir` — atomic on local POSIX filesystems (APFS, ext4, btrfs), no `flock` dependency. NOT safe across NFS / networked filesystems; current self-hosted runner uses local APFS so this holds. **Run all of Step 5f.1 inside a single `bash -c '…'` invocation** so the trap stays alive across `gh pr merge` → comment → close. The orchestrator's Bash tool MUST emit Step 5f.1 as one block.

```bash
# Anchor to repo root — prior subagent calls may have changed cwd (e.g. android/ for gradle)
cd "$(git rev-parse --show-toplevel)"
mkdir -p .claude
LOCK_DIR=".claude/merge.lock.d"

# Stale-lock recovery: if the lock dir exists with a dead PID owner, reclaim it.
if [[ -d "$LOCK_DIR" && -f "$LOCK_DIR/owner" ]]; then
  STALE_PID=$(cat "$LOCK_DIR/owner" 2>/dev/null || true)
  if [[ -n "$STALE_PID" ]] && ! kill -0 "$STALE_PID" 2>/dev/null; then
    echo "cleaned stale merge lock from PID $STALE_PID"
    rm -f "$LOCK_DIR/owner"
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
fi

# Acquire (atomic mkdir, retry up to ~10 min)
ACQUIRED=false
for i in $(seq 1 60); do
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    ACQUIRED=true
    echo "$$" > "$LOCK_DIR/owner"
    break
  fi
  sleep 10
done

if [[ "$ACQUIRED" != "true" ]]; then
  echo "merge.lock held >10 min — marking this issue status:blocked and continuing with the next"
  # Per Stop conditions: per-issue blocker, NOT session abort.
  # Caller must mark the issue blocked and continue the Step 5 loop.
  return 1   # if invoked via `bash -c`, this returns control to the orchestrator
fi

# Release MUST clear `owner` first — rmdir fails on non-empty dirs.
trap 'rm -f "$LOCK_DIR/owner" 2>/dev/null; rmdir "$LOCK_DIR" 2>/dev/null' EXIT INT TERM
```

If acquisition fails, mark this issue `status:blocked`, post an issue comment ("merge.lock held >10 min — escalated to human; check `.claude/merge.lock.d/owner` for the holding PID"), and continue with the next issue in Step 5. **Manual recovery**: `rm -rf .claude/merge.lock.d` (operator command if the heuristic stale-lock recovery above misses).

Inside the lock — **merge serialisation gate (MERGE_GATE_ONLY)**. The literal `gh pr merge` call MUST be in this block; subagent calls and CI waits MUST NOT be (they live in Step 5f.0 above), to keep lock-holding time bounded. Any `gh pr merge` call outside this block is a bug.

1. Final pre-merge sanity (no subagent calls, no CI waits — those happened in Step 5f.0):
   ```bash
   IFS=$'\t' read -r BASE STATE DRAFT <<<"$(gh pr view <PR> --json baseRefName,mergeStateStatus,isDraft \
     --jq '[.baseRefName, .mergeStateStatus, (.isDraft|tostring)] | @tsv')"
   ```
   - `BASE` MUST equal `develop` (refuse otherwise).
   - `STATE` must NOT be `BEHIND` or `DIRTY` (Step 5f.0 should have already resolved any conflict; if this assertion fails inside the lock it indicates a race or a regression — abort and mark the issue `status:blocked`, do NOT attempt resolution inside the lock).
   - `DRAFT == "true"` ⇒ `gh pr ready <PR>`. Only call ready if currently draft (the cmd errors otherwise).

2. **Defensive window/blocker re-check:** re-run Step 1 to compute current
   `WINDOW`. If `WINDOW=closed` AND this issue is not a blocker:
   - Abort the merge (do NOT call `gh pr merge`).
   - Append a row to `docs/reports/morning-merge-queue-<DATE>.md` (create the
     file with the header from Step 4 if it does not yet exist for today's
     date). Each row records `# | PR | Title | Reason | Blocker? | Tester
     verdict`.
   - Post the deferral comment from Step 4 on the issue (skip under `--dry-run`).
   - **Do NOT set `status:done`.** Leave the label at `status:needs-test`.
   - Release the lock and `continue` the Step 5 loop with the next issue. The
     PR stays open as ready (not draft) so the next `/ship` pass picks up at
     Step 5f for this issue without re-running review/tests.

3. Merge (only reached when window is open OR this issue is a blocker):
   ```bash
   # Capture exit code defensively — do NOT let `set -e` propagate a
   # post-merge bookkeeping failure (e.g. --delete-branch refused because a
   # local worktree still holds the branch ref) and abort the closure block.
   # The server-side squash-merge is atomic; if it succeeded we MUST still
   # post the closure comment, close the issue, and flip status:done.
   set +e
   gh pr merge <PR> --squash --delete-branch --subject "<conv-commit-subject>"
   MERGE_RC=$?
   set -e

   # Re-query authoritative merge state from GitHub.
   POST_MERGE_STATE=$(gh pr view <PR> --json state --jq '.state')

   if [[ "$POST_MERGE_STATE" == "MERGED" ]]; then
     if [[ "$MERGE_RC" -ne 0 ]]; then
       echo "gh pr merge returned $MERGE_RC but PR state=MERGED — continuing with closure (likely local-branch-cleanup failure; worktree still holds ref)"
     fi
     # Fall through to points 4–6 (closure comment, gh issue close, status:done).
   else
     echo "gh pr merge returned $MERGE_RC and PR state=$POST_MERGE_STATE — real merge failure, aborting closure block"
     # Mark the issue status:blocked and continue the Step 5 loop (the trap
     # at point 7 still fires to release the lock on bash -c exit).
     return 1
   fi
   ```
   `Closes #<N>` in the PR body persists through the squash and triggers GitHub's auto-close on merge. The explicit `gh issue close` below is defensive.

   **Defensive exit-code handling (per #777):** `gh pr merge --delete-branch` returns a non-zero exit code when its post-merge local-branch cleanup fails (e.g. `failed to delete local branch ... used by worktree at`). The server-side squash-merge is already atomic and complete at that point; only the local cleanup failed. Without the re-query above, `set -e` would propagate that exit code and skip the closure comment (point 4), `gh issue close` (point 5), and the `status:done` label edit (point 6) — leaving the PR merged but the issue in `status:needs-test` with no closure trail. The authoritative signal for "did the merge actually happen" is `gh pr view --json state == "MERGED"`, not `gh pr merge`'s exit code.

4. Build the closure comment and post it to BOTH the issue AND the PR (AGENTS.md § 11). The `Compound learning:` field is **mandatory** per #655 — populate it with the Step 5g outcome BEFORE building the closure body. If Step 5g has not yet executed when this point is reached, the orchestrator MUST run it (or determine its precondition / runtime-availability outcome) first to know the value:
   ```bash
   # Zip each reviewer codename with its captured vendor/model (Step 5c), e.g.
   # `REVIEW-42-…-A` (vendor: codex, model: gpt-5.5). REVIEWER_VENDORS /
   # REVIEWER_MODELS are the parallel arrays captured at Step 5c; use
   # `model: unknown` when a model is not known (keep the field present).
   REVIEWED_BY=""
   for i in "${!REVIEWER_CODENAMES[@]}"; do
     REVIEWED_BY+="\`${REVIEWER_CODENAMES[$i]}\` (vendor: ${REVIEWER_VENDORS[$i]}, model: ${REVIEWER_MODELS[$i]:-unknown}), "
   done
   REVIEWED_BY="${REVIEWED_BY%, }"  # strip trailing comma+space
   # $IMPLEMENTER_SYSTEM already carries the implementer model when known
   # (e.g. `ollama:qwen2.5`, `codex:gpt-5.5`); falls back to the bare vendor.
   CLOSURE_BODY="Implemented by \`<implementer-codename>\` (system: \`$IMPLEMENTER_SYSTEM\`), reviewed by ${REVIEWED_BY}, tested by \`<tester-codename>\`.
   PR: #<PR>
   Changed: <file list>
   Docs: <updated docs or 'none — reason'>
   Manual checks remaining: <list or 'none'>
   Compound learning: <Step 5g result>"
   gh issue comment <N> --body "$CLOSURE_BODY"
   gh pr comment <PR> --body "$CLOSURE_BODY"
   ```
   The manual test list MUST appear in both places — issue timeline for traceability,
   PR timeline so it is visible in the code review history (AGENTS.md § 11).

   **`Compound learning:` field — accepted values:**
   - `<classification> — <one-line apply summary>` — e.g., `Pattern doc — merged inline PR #N (pattern)`, `Rule — merged inline PR #N (rule)`, `Regression risk — opened issue #N`, `Nothing — no compoundable signal`
   - `n/a — dry-run mode` (Step 5g precondition 1)
   - `deferred — next /ship session captures at merge` (Step 5g precondition 2)
   - `n/a — merge did not succeed` (Step 5g precondition 3)
   - `skipped — compound-of-compound recursion guard` (Step 5g precondition 4)
   - `skipped — no classifier available in this runtime` (runtime-availability skip)
   - `failed: <reason> — see compound-followup label` (fail-soft path during Step 5g execution)

   The field is never empty. Empty `Compound learning:` is a bug — silent skip indicator. This is the structural enforcement gate from #655.

   **Ordering note:** the historic Step 5f.1 point sequence (1–7) is **unchanged**. The closure body's `Compound learning:` value is filled in via a small two-step post pattern that keeps the merge lock semantics intact:

   - **Point 4 (inside the lock block):** build and post the closure body with `Compound learning: <pending — see audit comment below>` as a deliberate placeholder. The remaining points 5 (issue close) and 6 (status:done) execute as today.
   - **Point 7 (lock release) fires** as today.
   - **Step 5g runs** outside the lock per its section header. Its result is captured.
   - **Post-Step-5g:** the orchestrator posts a SHORT follow-up audit comment to BOTH the issue and the PR (single line each): `Compound learning: <Step 5g result>`. The result format is one of the accepted values above. This second comment is the structural audit record per #655 — its absence within the per-issue lifecycle is the silent-skip signal.

   This preserves the existing lock semantics (Step 5g still runs after lock release, no lock contention) AND satisfies the #655 mandatory-field requirement (the value is reachable in both the issue and PR timelines by grepping `Compound learning:`). The minor cost is two comments instead of one — acceptable trade for not restructuring the established Step 5f.1 contract.

5. `gh issue close <N>` (idempotent if already closed by the squash auto-close).

6. **Set `status:done`** on the issue (replacing `status:needs-test`). This
   is the single transition point where `status:done` is applied — Step 5e.bis
   no longer sets it. Skip the label edit under `--dry-run`.

7. Release lock: trap fires automatically on the `bash -c` block's exit, removing both `owner` and the lock dir.

If `--dry-run` is set, log every step's intended command to stdout and skip the actual `gh pr merge` / `gh issue comment` / `gh issue close` calls. The lock IS still acquired/released to exercise the same code path.

### 5g — Compound learning (post-merge knowledge capture)

Runs AFTER Step 5f.1 point 7 (lock released) and BEFORE returning to the Step 5 loop. **Mandatory** when at least one classifier path (plugin or inline-prompt) is available; the only legitimate non-execution states are the 4 technical no-ops below plus 1 runtime-availability skip (no classifier path works in this runtime — e.g., Codex without plugin AND inline-prompt fallback also unavailable). Silent skips are structurally impossible: the Step 5f.1 point 4 closure-comment template has a mandatory `Compound learning:` field that records whichever of these fired, AND every PR that reaches Step 5g MUST emit machine-checkable marker lines to stdout (see "Marker contract" below). Per #655 (operator clarification 2026-05-16) and #691 (marker-enforcement layer). Canonical spec: `docs/development/compound-learning-spec.md`.

**Marker contract (per #691 — IF YOU CHANGE THIS, UPDATE THE STEP 6 VERIFIER):**

For every PR that reaches Step 5g, the orchestrator MUST emit exactly one of the two canonical marker shapes below to stdout BEFORE exiting Step 5g. This is an ADDITIONAL audit obligation, separate from (and in addition to) the Step 5f.1 closure-comment `Compound learning:` field. The closure comment is the per-PR audit trail in the GitHub timeline; the markers are the per-session audit trail in stdout, consumed by the Step 6 verifier. Both are mandatory; neither replaces the other.

The closed marker vocabulary is the ONLY accepted set of strings. Paraphrases, abbreviations, or extra fields are non-conformant and will trip the Step 6 verifier.

- **Success-path marker set (bundler + classifier ran to completion) — 3 lines, one per PR:**

  ```
  compound-learning: pr=<N> bundler_exit=<int>
  compound-learning: pr=<N> classifier=<plugin|inline|inline-sonnet> classification=<Rule|Pattern doc|Regression risk|Nothing>
  compound-learning: pr=<N> apply=<one-line apply summary>
  ```

  Valid `apply` values (closed set):
  - `merged inline PR #<M> (rule)`
  - `merged inline PR #<M> (pattern)`
  - `opened issue #<M>`
  - `skipped` (for `classification=Nothing`)
  - `compound <rule|pattern> merge failed: <reason> — see compound-followup label`

  All four classifier-outcome branches (Rule / Pattern doc / Regression risk / Nothing) emit ALL THREE lines. `classification=Nothing` is NOT a skip — it is a classifier outcome, so it MUST be backed by `bundler_exit` and `classifier` markers.

- **Skip-path marker (bundler / classifier deliberately not run) — 1 line, one per PR, exactly one of the canonical skip strings:**

  ```
  compound-learning: pr=<N> skipped=<dry-run | deferred | merge-failed | compound-of-compound | no-classifier-available>
  ```

  Closed skip vocabulary (exactly these strings, no paraphrases):
  - `dry-run` — precondition 1 below
  - `deferred` — precondition 2 below
  - `merge-failed` — precondition 3 below
  - `compound-of-compound` — precondition 4 below
  - `no-classifier-available` — runtime-availability skip below

  Exactly one of the two shapes per PR. Both shapes name the PR explicitly via `pr=<N>` so multi-PR `/ship` runs produce independent assertions that the Step 6 verifier can grep.

- **Optional JSON-line shape.** For downstream tooling (CI assertions, morning brief feeds), the markers MAY additionally be emitted as JSON-lines on the same stdout stream. The human-readable shape above is canonical; the JSON-line shape is an OPTIONAL parallel emission. Example:

  ```json
  {"compound-learning":{"pr":672,"bundler_exit":0,"classifier":"inline-sonnet","classification":"Pattern doc","apply":"merged inline PR #690 (pattern)"}}
  ```

  See `docs/development/compound-learning-spec.md § Structural enforcement` for the canonical shape rationale and negative examples.

**Preconditions (technical no-ops — Step 5g does not run, but the `Compound learning:` closure line is still populated with the no-op reason AND the canonical skip marker is still emitted; these are NOT operator-chosen skips, they are states where there is no compoundable signal to capture):**

1. `--dry-run` set ⇒ log `compound-learning: would invoke scripts/compound-learning.sh <PR> for issue #<N>`, emit the canonical skip marker `compound-learning: pr=<PR> skipped=dry-run`, and exit Step 5g. This skips both the bundler AND the classifier (plugin and inline-prompt paths alike) — nothing in Step 5g executes under `--dry-run`. Closure-line value: `n/a — dry-run mode`.
2. Merge was deferred to the morning queue at Step 5f.1 step 2 (window closed, non-blocker) ⇒ no merge happened ⇒ no learning to capture yet. Log `compound-learning: deferred — next /ship session running the merge will capture`, emit the canonical skip marker `compound-learning: pr=<PR> skipped=deferred`, and exit Step 5g. The next session's Step 5g will pick it up when that PR actually merges. Closure-line value: `deferred — next /ship session captures at merge`.
3. Step 5f.1 step 3 `gh pr merge` did not return success (rare — usually means a race or sanity-assertion abort) ⇒ no merge ⇒ log, emit the canonical skip marker `compound-learning: pr=<PR> skipped=merge-failed`, and exit Step 5g. Closure-line value: `n/a — merge did not succeed`.
4. **Compound-of-compound recursion guard.** The merged PR's head branch matches `chore/compound-*` (i.e. this Step 5g was triggered by a previous compound-learning PR's own merge) ⇒ log `compound-learning: compound-of-compound, skipping recursive learning capture`, emit the canonical skip marker `compound-learning: pr=<PR> skipped=compound-of-compound`, and exit Step 5g. A docs-only edit to `AGENTS.md` / `CLAUDE.md` does not itself yield further compoundable signal, and the guard prevents any runaway chain even if the classifier disagrees. Detection: `gh pr view <PR> --json headRefName --jq '.headRefName'` against the literal prefix `chore/compound-`. Closure-line value: `skipped — compound-of-compound recursion guard`.

**Runtime-availability skip (separate from the 4 preconditions above):**

If both classifier paths are unavailable in the current runtime — the `ce-compound` plugin is not loaded AND the inline-prompt fallback path is also not usable (e.g., a Codex environment that cannot dispatch an LLM call from this command) — Step 5g cannot execute. This is the ONLY operator-chosen skip per #655: on Claude Code with the plugin, on Codex with inline-prompt fallback working, Step 5g MUST run. Emit the canonical skip marker `compound-learning: pr=<PR> skipped=no-classifier-available` to stdout and exit Step 5g. Closure-line value: `skipped — no classifier available in this runtime`. The orchestrator MUST also emit a stdout warning so the operator can spot a runtime that should support Step 5g but does not.

**Steps:**

1. Invoke the agent-neutral bundler. Two paths exist (issue #975 — the MCP-runtime branch lets the bundler run without `gh` on `$PATH`); both produce the same `$BUNDLE_JSON` shape so points 2–5 dispatch identically:

   **CLI mode (`GH_MODE=cli`):**
   ```bash
   if BUNDLE_JSON="$(./scripts/compound-learning.sh "$PR_NUMBER" 2>/dev/null)"; then
     BUNDLE_EXIT=0
   else
     BUNDLE_EXIT=$?
     BUNDLE_JSON=""
   fi
   ```

   **MCP mode (`GH_MODE=mcp`):** the orchestrator pre-fetches the PR data via `mcp__github__pull_request_read` (methods `get`, `get_diff`, `get_comments`, `get_review_comments`, `get_check_runs`), assembles the envelope (schema documented in `docs/development/compound-learning-spec.md § "Bundler input modes"`), and pipes it into the bundler on stdin. Stdin avoids the need for a tempfile (and the `mktemp` / `rm -f` calls that would otherwise have to be added to `allowed-tools`):
   ```bash
   # $ENVELOPE_JSON is the assembled envelope. The envelope is a single
   # object with keys: meta (PR metadata), diff (unified diff string),
   # issue_comments (text blob), review_comments (array from
   # get_review_comments), ci_status (object wrapping statusCheckRollup).
   # Required meta fields: number, state, mergedAt. When
   # mcp__github__pull_request_read returns no mergedAt, the bundler
   # exits 3 and Step 5g logs + skips per the fail-soft rule below.
   if BUNDLE_JSON="$(printf '%s' "$ENVELOPE_JSON" | ./scripts/compound-learning.sh "$PR_NUMBER" --input-json - 2>/dev/null)"; then
     BUNDLE_EXIT=0
   else
     BUNDLE_EXIT=$?
     BUNDLE_JSON=""
   fi
   ```
   The explicit `if/else` is required in both paths: writing `... || true` inside the command substitution would force the subshell to exit 0, masking real failures and feeding an empty bundle to the classifier. On non-zero exit (gh missing in CLI mode, jq missing, PR not found, PR not merged, malformed envelope in MCP mode) ⇒ log `compound-learning: bundler exit $BUNDLE_EXIT — skipping` to stdout and exit Step 5g (do NOT abort `/ship`; this is fail-soft).

2. Classify the bundle. The classifier source depends on the runtime — there are two paths, both of which MUST produce a `{classification, payload}` shape so points 3–5 below can dispatch identically regardless of which path was taken:

   - **Claude Code with `/ce-compound` plugin available** ⇒ invoke the plugin, passing `$BUNDLE_JSON` as input. Capture the plugin's classification output. Expected fields: `classification` (one of `Rule` / `Pattern doc` / `Regression risk` / `Nothing`) and `payload` (Rule/Pattern-doc text, or Regression-risk title+body). If the plugin's actual output schema differs from this contract, adapt the parsing in this step and update the spec doc; the plugin's exact invocation contract is not reproduced verbatim here because it is owned by the plugin, not by this command. See `docs/development/compound-learning-spec.md § Codex parity` for the full plugin-vs-inline contract reconciliation (and the `TODO(invocation)` marker that tracks plugin-schema confirmation).
   - **Codex / any environment without the plugin** ⇒ use the inline-prompt fallback (preserved verbatim from the prior Step 5g, parity with Codex requires the exact same prompt — do NOT paraphrase). The orchestrator MUST dispatch this inline-prompt classification via an Agent call with `subagent_type` set to a lightweight general-purpose agent and `model: sonnet` (Sonnet override on the Agent tool). Step 5g's automated classification is template-following 4-class work (Rule / Pattern doc / Regression risk / Nothing) — Sonnet is sufficient and avoids ~12% of weekly Opus usage burned on this path per #776. When the marker contract below names this path, the `classifier` value is `inline-sonnet` (the bare `inline` value is reserved for manual / interactive `/ce-compound` invocations outside `/ship`'s automated Step 5g):

     ```
     Below is a structured context bundle for a PR that just merged into
     develop. Classify the durable learning this PR represents into exactly
     one of: Rule, Pattern doc, Regression risk, Nothing. If Rule or Pattern
     doc, write the proposed text. If Regression risk, write the issue title
     and one-paragraph body. Return as JSON: {classification, payload}.
     ```

   **Detection mechanism:** try the plugin invocation first; on any non-success exit code (including `command not found` style errors, plugin-runtime errors, or empty/unparseable plugin output), fall back to the inline-prompt path above. This is the simplest agent-neutral pattern — it requires no global registry probe, no `--ce-compound` / `--no-ce-compound` flag, and degrades gracefully on Codex (which will always hit the fallback because the plugin is not loaded). Log which path was taken to stdout (see point 4 below) so the operator can spot silent fallbacks. Both paths feed the SAME `$BUNDLE_JSON` produced by `scripts/compound-learning.sh` — the bundler remains the single source of truth.

3. Parse the model's JSON response. If unparseable, treat as `Nothing` and log the raw output to stdout. Switch on `classification`:

   - **Rule** ⇒ open a follow-up PR AND merge it inline in the SAME `/ship` cycle. Compound Rule PRs are mechanical docs-only edits to `AGENTS.md` / `CLAUDE.md` / other paths in the docs-only allowlist, classified by the model already; reviewer fan-out and tester gate are skipped by design. Do NOT amend the source PR (the issue is already closed and the source PR squash-merged). Use the implementer subagent with these inputs:
     - Branch: `chore/compound-<PR_NUMBER>-rule` (e.g. `chore/compound-536-rule`).
     - Files: edit `AGENTS.md` (cross-platform) or `android/CLAUDE.md` / `web/CLAUDE.md` (platform-scoped) per the payload's `path` field if present. **Allowed paths are strictly the Step 5b docs-only allowlist** (`^\.claude/`, `^\.codex/`, `^docs/`, `.*\.md$`, `^AGENTS\.md$`, `^CLAUDE\.md$`); the implementer prompt MUST tell the subagent to reject any change outside this set and to return without pushing if the payload would touch a non-docs path. If rejection fires ⇒ fall through to the failure-mode handler in this sub-step (leave nothing on origin, log + skip).
     - PR title: `chore(rules): compound learning from #<PR_NUMBER>`.
     - PR body: include `Source: PR #<PR_NUMBER>`, the rule text, and `Docs Impact: <files>`.
     - PR is opened **ready (not draft)** — review/tester are skipped by design.

     After the PR is opened, the orchestrator runs an inline mini-merge for it:

     1. **Step 5b (CI gate) on the compound PR.** The docs-only allowlist applies; expected outcome is the empty-array path (no checks scheduled). If CI accidentally schedules and fails, treat it as a compound-merge failure (see failure handler below).
     2. **Skip Step 5c–5e (reviewer fan-out) and Step 5e.bis (tester).** Compound Rule PRs are not subject to either gate; this is the deliberate trust trade-off documented in `docs/development/compound-learning-spec.md § Commit vs PR decision rule`.
     3. **Minimal Step 5f.0.** Re-fetch `mergeStateStatus` for the compound PR. If `BEHIND`/`DIRTY` ⇒ ask the implementer subagent to merge `origin/develop` into the compound branch and push. **Iteration cap: 1.** If the conflict persists after one pass ⇒ failure handler. The Step 5f.0 merge-conflict re-review is also skipped (consistent with the no-reviewer-fan-out policy for this path).
     4. **Step 5f.1 with window bypass.** Acquire the merge lock as usual (the lock invariant is non-negotiable — every `gh pr merge` MUST go through Step 5f.1). Inside the lock, treat the compound Rule PR as `BLOCKER=true` for the defensive window re-check at point 2: the compound PR merges regardless of the UTC+3 hour. **Rationale:** the parent PR already merged this cycle (otherwise Step 5g would not be running per precondition 3), so the rule captured from it lands atomically with the change it codified. Deferring the compound PR to the morning queue would split the rule from its source merge across calendar days and defeat the inline-capture point. The window-bypass for compound Rule PRs is documented under § Safety invariants.
     5. **No closure comment is posted on either PR.** The squash-commit subject (`chore(rules): compound learning from #<PR_NUMBER>`) is the audit trail; the compound PR body's `Source: PR #<PR_NUMBER>` is the link back to the parent. Step 5g's session-report row records the inline-merge result (point 4 below).

     **Compound-merge failure handler** (any of: implementer rejects payload as out-of-allowlist, CI fails on the compound PR, BEHIND/DIRTY cannot be resolved in one iteration, the lock cannot be acquired within its budget, or `gh pr merge` fails inside the lock): if the compound PR was already opened, convert it to draft (`gh pr ready --undo <PR>`) and add label `compound-followup` so it surfaces in a future operator-driven `/ship` run. If the failure happened before the PR was opened (implementer rejection), no PR exists; just log. In all cases, log a one-line failure note to stdout (audit format: `compound merge failed: <reason> — see compound-followup label`) and exit Step 5g. The main `/ship` cycle is NOT aborted; this is fail-soft, consistent with the other Step 5g failure modes below.

   - **Pattern doc** ⇒ open a follow-up PR AND merge it inline in the SAME `/ship` cycle, mirroring the Rule path. Direct commits to `develop` are not used — `develop` is a protected branch and every change MUST go through a PR. Implementation:
     - Branch: `chore/compound-<PR_NUMBER>-pattern` (e.g. `chore/compound-536-pattern`).
     - File path: `docs/<platform>/learnings/<short-slug>.md` from the payload (`android`, `web`, or `shared` sub-directory; create on first use). File MUST be ≤200 lines and additive only. If the payload would modify an existing file, downgrade to `Rule` flow (follow-up PR with `AGENTS.md` / `CLAUDE.md` edit) instead.
     - PR title: `docs(<platform>): compound learning from #<PR_NUMBER> — <short-slug>`.
     - PR body: include `Source: PR #<PR_NUMBER>`, the pattern-doc content summary, and `Docs Impact: <files>`.
     - PR is opened **ready (not draft)** — review/tester are skipped by design (same trust trade-off as the Rule path; see `docs/development/compound-learning-spec.md § Commit vs PR decision rule`).

     After the PR is opened, the orchestrator runs the same inline mini-merge sequence as the Rule path:

     1. **Step 5b (CI gate) on the compound PR.** The docs-only allowlist applies (the file lives under `docs/`); expected outcome is the empty-array path (no checks scheduled). If CI accidentally schedules and fails, treat as a compound-merge failure (see failure handler below).
     2. **Skip Step 5c–5e (reviewer fan-out) and Step 5e.bis (tester).** Compound Pattern PRs are not subject to either gate.
     3. **Minimal Step 5f.0.** Re-fetch `mergeStateStatus`. If `BEHIND`/`DIRTY` ⇒ ask the implementer subagent to merge `origin/develop` into the compound branch and push. **Iteration cap: 1.** If the conflict persists after one pass ⇒ failure handler. The Step 5f.0 merge-conflict re-review is skipped (consistent with the no-reviewer-fan-out policy for this path).
     4. **Step 5f.1 with window bypass.** Acquire the merge lock as usual (every `gh pr merge` MUST go through Step 5f.1). Inside the lock, treat the compound Pattern PR as `BLOCKER=true` for the defensive window re-check, same as compound Rule PRs. **Rationale:** the parent PR already merged this cycle, so the pattern captured from it lands atomically with the change it codified.
     5. **No closure comment is posted on either PR.** The squash-commit subject (`docs(<platform>): compound learning from #<PR_NUMBER> — <short-slug>`) is the audit trail; the compound PR body's `Source: PR #<PR_NUMBER>` is the link back to the parent.

     **Compound-merge failure handler** (same as Rule path — any of: implementer rejects payload as out-of-allowlist, CI fails on the compound PR, BEHIND/DIRTY cannot be resolved in one iteration, the lock cannot be acquired within its budget, or `gh pr merge` fails inside the lock): if the compound PR was already opened, convert it to draft (`gh pr ready --undo <PR>`) and add label `compound-followup`. Log a one-line failure note to stdout's `## Compound learning` row (Apply action = `compound pattern merge failed: <reason> — see compound-followup label`) and exit Step 5g. The main `/ship` cycle continues; this is fail-soft.

   - **Regression risk** ⇒ open a new GitHub issue via `mcp__github__issue_write`:
     - Title: payload's `title`.
     - Body: payload's `body`, prefixed with `Source: PR #<PR_NUMBER>`.
     - Labels: `type:testing`, `priority:medium`, `status:backlog` (all three are mandatory).
     - This invokes the orchestrator's **carve-out for issue creation** documented below.

   - **Nothing** ⇒ log `no compoundable learning, skipping` to stdout. No on-repo change.

4. **Emit the canonical success-path marker set to stdout (per #691 — mandatory for ALL FOUR classifier-outcome branches above, including `Nothing`).** This is the per-session audit trail consumed by the Step 6 verifier; its absence makes the session structurally broken. Emit exactly three lines:
   ```
   compound-learning: pr=<PR-#> bundler_exit=<int>
   compound-learning: pr=<PR-#> classifier=<plugin|inline|inline-sonnet> classification=<Rule|Pattern doc|Regression risk|Nothing>
   compound-learning: pr=<PR-#> apply=<one-line apply summary>
   ```
   Valid `apply` values (closed set): `merged inline PR #<N> (rule)`, `merged inline PR #<N> (pattern)`, `opened issue #<N>`, `skipped` (only valid when `classification=Nothing`), `compound <rule|pattern> merge failed: <reason> — see compound-followup label`. Branch-to-`apply` mapping: `Rule` ⇒ `merged inline PR #<N> (rule)` on success / `compound rule merge failed: …` on failure; `Pattern doc` ⇒ `merged inline PR #<N> (pattern)` on success / `compound pattern merge failed: …` on failure; `Regression risk` ⇒ `opened issue #<N>`; `Nothing` ⇒ `skipped`.

   A single human-readable line in the prior `compound-learning: issue=<N> pr=<…>` shape MAY additionally be emitted for backward compatibility with older log scrapers; it is NOT a substitute for the three canonical lines above. The optional JSON-line shape from the Marker contract MAY also be emitted on the same stdout stream.

5. Exit Step 5g. Continue the Step 5 loop with the next issue (or exit the command if this was the last).

**Carve-out for issue creation:** `AGENTS.md § Tool Permissions` lists the orchestrator as `Create GitHub issues = ✗` by default, with the `/review-all-day` carve-out (footnote ³). Step 5g adds a second carve-out: the orchestrator MAY call `mcp__github__issue_write` ONLY when (a) Step 5g's classification is `Regression risk`, AND (b) the labels are exactly `type:testing`, `priority:medium`, `status:backlog`. This is post-merge automated knowledge capture, not human-loop blocker handling, so the safety reason for the default `✗` does not apply. The carve-out is documented in `docs/development/compound-learning-spec.md § Carve-out for issue creation`.

**Step 5g failure modes (never abort the session):**

- Bundler exits non-zero ⇒ log + skip.
- Plugin invocation fails (Claude Code path) ⇒ fall back to the inline-prompt path; log the fallback to stdout (`compound-learning: /ce-compound unavailable or returned non-success — fell back to inline prompt`) so the operator can spot whether the plugin path is actually exercised.
- Model returns unparseable JSON (either path) ⇒ treat as `Nothing` + log raw output.
- Follow-up-PR open fails (Rule path) ⇒ log the bundle reference to stdout; operator can replay manually.
- Compound PR inline-merge fails (Rule OR Pattern path: implementer rejects out-of-allowlist payload, CI accidentally fails, BEHIND/DIRTY persists after one resolution pass, merge lock unobtainable, or `gh pr merge` errors inside the lock) ⇒ flip the compound PR back to draft (if it was opened) and add the `compound-followup` label so a future operator-driven `/ship` run can pick it up; log a one-line failure note (`compound <rule|pattern> merge failed: <reason> — see compound-followup label`) to stdout. The main `/ship` cycle continues with the next issue.
- `mcp__github__issue_write` fails (Regression risk path) ⇒ log + skip; the source PR is already merged so nothing is lost.

Under `--dry-run`, Step 5g logs the would-be invocation only (see precondition 1) and does NOT execute the bundler **or** the plugin. Both classifier paths (plugin and inline prompt) are gated behind the same precondition; the dry-run early-exit at precondition 1 fires before any classifier is touched.

## Step 6 — Session-end compound-learning verifier (per #691)

Runs ONCE per `/ship` session, AFTER the Step 5 loop exits and BEFORE the final user-facing summary line. This is the structural enforcement layer that makes a Step 5g silent skip impossible: every merged PR in this session MUST have left a canonical compound-learning marker (either the 3-line success-path set or the 1-line skip-path marker) in the session's stdout log. If even one merged PR is missing its marker, the session is structurally broken and MUST flip to `status:blocked`.

**If you change Step 5g's marker contract (the canonical strings, the `pr=<N>` keying, the success vs skip shape split), you MUST update the grep pattern in this verifier in lockstep.** The verifier and the emitter are a contract; drift between them silently re-enables the failure mode this whole step exists to prevent.

```bash
# For every PR that reached Step 5f.1 with a successful merge AND was not
# blocked by a Step 5g precondition, exactly one classifier-outcome marker set
# OR exactly one skip-precondition marker MUST appear in this session's stdout
# log. If neither appears, the session is structurally broken.
for PR in ${MERGED_PRS[@]}; do
  if ! grep -qE "^compound-learning: pr=$PR (bundler_exit|skipped)=" "$SESSION_LOG"; then
    echo "FAIL: Step 5g markers missing for PR #$PR — silent skip detected" >&2
    SESSION_STATUS=blocked
    break
  fi
done
```

On failure:

1. Flip the session status to `status:blocked`.
2. Append the missing-marker PR(s) to the session report's "Skipped / blocked" log entry with the reason `compound-learning markers missing — silent Step 5g skip detected (#691)`.
3. Surface a banner in the final user-facing line so the operator sees the structural failure immediately.

The orchestrator MUST NOT silently complete a session with missing markers. A passing verifier is a precondition for printing the normal success summary line.

`MERGED_PRS` is the list of PR numbers that completed Step 5f.1 point 3 (`gh pr merge` returned success) in this session. `SESSION_LOG` is the orchestrator's accumulated stdout for this run (in environments without a literal log file, this is the in-memory stdout buffer the orchestrator has emitted so far). The grep is anchored to the start of line to avoid false positives from PR bodies or comments that quote the marker shape.

Negative examples (see `docs/development/compound-learning-spec.md § Structural enforcement`):

- A session report row showing `Nothing | inline | skipped` for PR #671 with NO `compound-learning: pr=671` lines in stdout ⇒ silent skip; verifier MUST reject.
- A session report row showing `Nothing | inline | skipped` for PR #671 backed by `compound-learning: pr=671 bundler_exit=0` + `classifier=inline classification=Nothing` + `apply=skipped` in stdout ⇒ legitimate `Nothing` outcome; verifier accepts.
- A session report row showing `n/a | n/a | deferred — next /ship session captures` for PR #672 backed by `compound-learning: pr=672 skipped=deferred` in stdout ⇒ legitimate skip; verifier accepts.

## Morning queue (operator handoff for overnight deferrals)

When Step 5f.1's defensive window re-check defers a merge to the morning, the orchestrator appends a row to `docs/reports/morning-merge-queue-<DATE>.md` and posts the deferral comment on the issue (see Step 5f.1 point 2). `/morning` reads this file (today's date) and surfaces deferred issues at the top of its brief so overnight deferrals are visible without anyone having to open `/ship`'s output.

`docs/reports/` is gitignored — the morning queue is operator scratch space, not a durable PR artifact. There is no session report committed to git; the per-PR closure comments (Step 5f.1 point 4) and per-classification compound-learning log lines (Step 5g point 4) are the durable audit trail in PR / issue / branch commit history.

## Stop conditions

- All queued issues processed (success or `status:blocked`).
- Per-issue CI retry budget exceeded (3 fix-and-push rounds): mark that issue `status:blocked`, continue with rest.
- Per-issue Step 5f.0 merge-prep iteration cap exceeded (max 2 BEHIND/DIRTY re-merge iterations): mark that issue `status:blocked` with reason `develop drift exceeded merge-prep budget` and append to `morning-merge-queue-<DATE>.md`, continue with rest. This guarantees liveness when carve-out language means no review-round budget is consumed by pure-drift loops.
- Session-wide CI cooldown: 3 consecutive issues hitting the per-issue budget without any successful merge in between ⇒ abort the session.
- `gh` returns `403: API rate limit exceeded` ⇒ log partial state to stdout, exit `status:blocked`.
- Hard blocker (network, missing token, ambiguous requirement on a specific issue ⇒ mark that one `status:blocked`, continue with rest).
- User cancels.

On exit (success or partial), log a one-line summary per processed/deferred/blocked issue to stdout.

## Safety invariants

- Never push directly to `develop` or `main`.
- Never merge without all reviewers `LGTM` (no blockers) AND CI green AND tester clear, except for the compound Rule / Pattern PR carve-outs documented below.
- Never bypass the merge lock; never run `gh pr merge` outside Step 5f.1. (Search marker: `MERGE_GATE_ONLY`.)
- Never let reviewers call any GitHub write API — only the orchestrator does.
- Inside the UTC+3 night no-merge window 01:30–07:00, only blockers may **merge**. Implementation, CI,
  review, and tester gates always run regardless of window — the gate only
  fires at Step 5f. If unsure whether to merge, defer.
- **Compound Rule and Pattern PR window-bypass carve-out.** A
  `chore/compound-<PR>-rule` PR or `chore/compound-<PR>-pattern` PR
  generated by Step 5g is treated as `BLOCKER=true` for Step 5f.1's
  defensive window re-check ONLY. It still goes through the merge lock,
  still asserts `BASE == develop`, and still respects `mergeStateStatus`
  (BEHIND/DIRTY blocks the merge). The carve-out exists so the rule or
  pattern lands atomically with the parent merge that produced it;
  deferring it across calendar days would split the learning from its
  source.
- **Compound Rule and Pattern PRs skip review and tester by design.**
  This is the deliberate trust trade-off documented in
  `docs/development/compound-learning-spec.md § Commit vs PR decision rule`.
  It is bounded by (a) the docs-only allowlist constraint on the
  implementer's allowed paths (for both Rule and Pattern PRs), (b) the
  compound-of-compound recursion guard in Step 5g preconditions, and
  (c) the failure-mode handler that flips failing compound PRs back to
  draft + `compound-followup` label for operator follow-up.
- `status:done` is set in exactly one place: Step 5f point 6, after a
  successful `gh pr merge`. Tester clearance alone is NOT enough to set
  `status:done`; the issue stays at `status:needs-test` until the merge
  actually happens (this lets us run review/tester overnight without faking
  closure).
- The jury stage (Step 5d.jury) is read-only and runs regardless of the merge window like the 5c reviewers. In **advisory** mode (`--jury-advisory`, Phase A) it never gates and never consumes a review-fix round. In **gating** mode (default, issue #2033 Phase B) only **verified consensus** findings at `critical`/`major`/`minor` gate the merge (via Step 5e), and a jury-driven fix consumes one review-fix round; unverified findings and `nit`s never gate, a sub-2-vendor panel is downgraded to advisory, and any jury run that did not complete cleanly (CLI absent, config-invalid, timeout, crash) NEVER gates — fail-soft means an absent/erroring jury cannot manufacture a block.
- The `--wizard` configuration step (Step 0.wizard) is interactive-only and MUST be skipped (degraded to a logged no-op) in any non-interactive context — watch mode, `/overnight`, `/lfg`, background or headless runs. The wizard never blocks an autonomous pipeline and never produces a configuration the normal Step 0 grammar could not.
- Tool/model detection in the wizard (and the `--review-delegate` reviewer routing) is best-effort: an absent CLI or Ollama model is never offered (wizard) and always falls back to the Claude path (5c) with a logged note — it never blocks or fails the run.
- A non-`claude` `--review-delegate` reviewer is still read-only and findings-only; the orchestrator-only-writes contract holds for every reviewer vendor.
- A local Ollama implementer (`--delegate ollama:<model>`, § 5a.ollama) is **orchestrator-driven** (the host does all git/`gh`/PR steps; the model only generates code) and is **gated to non-TIER-3 issues** — a TIER-3 issue falls back to `HOST_AGENT`. It always falls back to `HOST_AGENT` on unavailability or after its retry budget, never bypasses the 5c review / tester / merge gates or the merge lock, and never aborts the run.
- Do not edit `AGENTS.md` § lifecycle from this command; it is the source of truth.
- `--dry-run` propagates to the implementer subagent; reviewers always run for real (read-only) so dry-run still produces meaningful findings.

## Known limitations

These are deliberate scope boundaries, not bugs to fix in this PR. Each is addressable in a follow-up.

- **NFS / networked filesystems unsupported.** `mkdir`-based mutex is atomic on local POSIX filesystems (APFS, ext4, btrfs) but not across NFS without `O_EXCL`-equivalent semantics. Current self-hosted runner uses local APFS so this holds today; a future move to networked storage requires switching to a proper file-lock or a coordination service.
- **Trap scope across multi-call execution (mitigated by Step 5f.0/5f.1 split).** Step 5f.1's release `trap` only spans the bash invocation that registered it. The spec instructs the orchestrator to emit Step 5f.1 as a single `bash -c '...'` block. The PR #258 / issue #257 restructure moved every multi-call operation (subagent dispatch, CI waits, narrowed merge-conflict re-review) OUT of the lock and into Step 5f.0, so Step 5f.1 is now a contiguous bash sequence by design — the trap-scope concern is mitigated, not merely deferred. If a future implementer splits the merge sequence across multiple Bash tool calls anyway, the trap will not cover the later calls and the lock may leak on partial failure; stale-lock recovery at the start of the next acquisition mitigates but does not eliminate this. Keep Step 5f.1 a single bash block.
- **Repeated flag handling.** `--reviewers 2 --reviewers 3` and similar repeated flags have undefined behaviour. The grammar consumes the integer immediately after the flag; a second `--reviewers` would be parsed as another flag. Treat repeated flags as user error and reject; this is not encoded in the parser today.
- **Negated/contextual false positives in blocker regex.** The word-bounded regex still matches phrases like "no crashes on this build" or "regression test for crashes on Android". Blocker auto-detection is one of five rules and humans can override with `--blocker` or by NOT passing it. False-positive risk is bounded.
- **Inter-issue parallelism is not real.** A single Claude Code (or Codex) session runs as one turn loop and cannot truly parallelise across issues. The reviewer fan-out within a single issue (Step 5c) IS parallel via a single Agent-tool message. True N-issue parallelism would require N independent `claude -p` subprocesses driven by an external script — out of scope for this command.
- **Implementer subagent does not formally know `--dry-run`.** `/implement.md` has no dry-run section; the orchestrator passes the verbal "Do NOT push, do NOT open a PR" instruction in the prompt. Works in Claude Code; Codex playbook runners reading `/implement.md` alone will not honour it. Follow-up: add a `--dry-run` section to `/implement.md` mirroring `/ship`'s flag semantics.
- **Orchestrator cannot open follow-up issues.** Per AGENTS.md § Tool Permissions, orchestrator is ✗ for `Create GitHub issues` — when an issue ends `status:blocked`, the orchestrator can only comment, not open a tracking issue. Deliberate: keeps a human in the loop on `/ship` abandons.
- **git push may fail with HTTP 403 in sandboxed environments.** The self-hosted runner's git credential helper sometimes returns 403 for push operations while MCP GitHub tools remain authenticated. When `git push` fails with 403, use `mcp__github__push_files` (MCP GitHub server tool) as the fallback push method instead of retrying git. The implementer subagent should attempt `git push` first; on 403 failure, immediately fall back to `mcp__github__push_files` without retrying git more than once.
- **The jury requires the `jury` CLI and the committed panel.** Step 5d.jury needs the `jury` CLI installed in the runtime (`pipx install 'ai-jury>=1.1.0'`) and the committed root `jury.toml` panel present. If the CLI is missing, the stage is skipped (logged, never blocks — and in gating mode an absent jury NEVER gates). Missing panel agents degrade the panel silently; a sub-2-vendor panel is downgraded to advisory for that run. Gating (Phase B, issue #2033) blocks the merge only on *verified consensus* `critical`/`major`/`minor` findings; `--jury-advisory` restores the non-gating Phase A behaviour (issue #1746).
