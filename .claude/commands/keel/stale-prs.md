---
description: Find and nudge stale PRs — rebase, re-run CI, or flag for a decision.
argument-hint: "[--days <N>] [--rebase]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Edit
---

# /keel:stale-prs

Project-neutral stale-PR sweep. Reads `.keel/project.yaml`.

1. List open PRs with no activity for `--days` (default 3).
2. For each, diagnose: behind `base_branch`? CI red? awaiting review? merge-conflicted?
3. Act per diagnosis: `--rebase` updates the branch off `base_branch`; re-kick transient CI;
   hand review-ready ones to `/keel:review-cycle`; flag genuinely blocked ones for a decision.
4. Respect the merge window — never merge here; route clean PRs to `/keel:ship`.

Fail-soft per PR (one failure never aborts the sweep); deterministic ordering.
