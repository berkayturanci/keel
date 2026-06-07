---
description: Finish the current work session — run the configured gates, commit, push, open a PR, and record a session recap. Project-neutral; reads .keel/project.yaml.
argument-hint: "[optional PR title override] [--since <ref|timestamp>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Edit, Write, mcp__github__create_pull_request
---

# /keel:wrap

Wrap up the current work session. This adapter is project-neutral: it contains no
branch name, build/lint command, or path literal. Read every project specific
from `.keel/project.yaml` via the `keel` CLI (`base_branch`, `build_gate_cmd`,
`lint_cmd`).

## Language

All committed/published artifacts (commits, branch names, PR/issue titles and
bodies, comments, file contents) MUST be written in English. Free-form chat may
stay in any language (`knobs.sot_doc` § language policy).

## Step 0 — Resolve config

```bash
keel validate .keel/project.yaml --root .
keel plan     .keel/project.yaml --root .   # base_branch, build_gate_cmd, lint_cmd
```

Resolve GitHub access through the shared runtime contract (`keel capabilities --json` →
`github_transport`). GitHub writes use the selected transport. The PR is opened **ready**
(not draft) only when `pr_write` is supported; otherwise stop with the degraded operation
listed instead of falling through to an implicit best effort.

## Step 1 — Sanity check

1. `git status --short`
2. If on `base_branch` (or any protected base), ABORT — tell the user to switch
   to a feature branch.
3. `git diff --stat HEAD`
4. **Workspace isolation check:** `/keel:wrap` MUST run from a **linked
   worktree**, not the main worktree (the user's primary checkout). Detect with
   `git rev-parse --git-dir`: the main worktree returns the literal `.git`; a
   linked worktree returns an absolute path containing `/.git/worktrees/<name>`.
   If the value is `.git`, ABORT and tell the user to re-run from a linked
   worktree (list candidates with `git worktree list`). This check is portable
   across OSes/home directories and immune to symlink trickery (`.git` resolution
   is performed by git, not by shell-level path matching).

## Step 2 — Quality gates (do NOT skip)

Run the configured gates via the keel CLI so the command strings stay
config-driven (`build_gate_cmd` + `lint_cmd` plus any `tester` Lego):

```bash
keel run-gates .keel/project.yaml --root .
```

Any file-change-conditional suites (schema migration, entitlement, or
config-validation checks gated on which paths changed) are **project-specific;
stay in the project** — express them as a `.keel/extensions/` Lego that
`run-gates` picks up, never inline a project command here.

If any gate FAILS — STOP. Report the failure. Do not commit broken code.

## Step 3 — Commit

- `git add -A`
- Write a commit message in Conventional Commits format
  (feat/fix/chore/docs/refactor/test).
- Include `Closes #N` if this implements an issue.
- `git commit -m "<message>"`

## Step 4 — Push + PR

- `git push -u origin HEAD`
- If a PR-title argument was provided, use it as the PR title; otherwise derive
  from the commit message.
- Include the agent run codename in the PR body when the branch was produced by
  an agent run.
- Open the PR with `base=<base_branch>` (or the current PR target),
  `head=<current branch>`, the resolved title, and a body covering: `Closes #N`,
  a Summary of what changed, the agent run codename (or none), docs impact, and a
  test plan. Open it ready, not draft, in both `gh` and MCP modes.

## Step 5 — Session recap

Append a session recap to the project's session log: what was accomplished,
what's still open, and what to pick up next session. Hand deferred items to the
cross-session morning queue for `/keel:morning`.
