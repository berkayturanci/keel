# Codex Adapter

`AGENTS.md` is the canonical cross-AI instruction file for this repository.

Codex-specific files in this directory are thin adapters. They should point back to
`AGENTS.md`, `.agents/skills/`, and `.claude/commands/keel/` instead of duplicating
workflow rules.

This directory intentionally does not set `sandbox_mode = "danger-full-access"` or
`approval_policy = "never"`. Those are user-local runtime choices and should not be
recommended as repository defaults for an open source project.

## Command Mapping

Codex does not have native Claude-style slash commands. When asked to run a keel command,
load the matching skill under `.agents/skills/` and follow it exactly:

- `ship` or `/keel:ship` (compound profile: `ship --compound`) -> `.agents/skills/keel-ship/SKILL.md`
- `implement` or `/keel:implement` -> `.agents/skills/keel-implement/SKILL.md`
- `review-cycle` or `/keel:review-cycle` -> `.agents/skills/keel-review-cycle/SKILL.md`
- `pr-loop` or `/keel:pr-loop` -> `.agents/skills/keel-pr-loop/SKILL.md`
- `wrap` or `/keel:wrap` -> `.agents/skills/keel-wrap/SKILL.md`
- `morning` or `/keel:morning` -> `.agents/skills/keel-morning/SKILL.md`
- `work-block` or `/keel:work-block` -> `.agents/skills/keel-work-block/SKILL.md`
- `overnight` or `/keel:overnight` -> `.agents/skills/keel-overnight/SKILL.md`
- `triage` or `/keel:triage` -> `.agents/skills/keel-triage/SKILL.md`
- `ci-check` or `/keel:ci-check` -> `.agents/skills/keel-ci-check/SKILL.md`
- `coverage` or `/keel:coverage` -> `.agents/skills/keel-coverage/SKILL.md`
- `deps-audit` or `/keel:deps-audit` -> `.agents/skills/keel-deps-audit/SKILL.md`
- `flake-audit` or `/keel:flake-audit` -> `.agents/skills/keel-flake-audit/SKILL.md`
- `regression` or `/keel:regression` -> `.agents/skills/keel-regression/SKILL.md`
- `review-all-day` or `/keel:review-all-day` -> `.agents/skills/keel-review-all-day/SKILL.md`
- `stale-prs` or `/keel:stale-prs` -> `.agents/skills/keel-stale-prs/SKILL.md`
- `swarm` or `/keel:swarm` -> `.agents/skills/keel-swarm/SKILL.md`

The matching Claude command files under `.claude/commands/keel/` are compatibility
entry points. The skill files are the Codex entry points.

The skills resolve structured run data through the keel CLI (`keel plan --command <cmd>
--json`, and `--live --json` for live runs). For mutating live workflows, obey the
`operator_consent` block and stop before any write when the contract reports missing
consent.

## Safety

`hooks.json` wires a `PreToolUse` hook to `.codex/hooks/deny-dangerous-shell.sh`.
The hook blocks destructive shell commands such as force-push, hard reset, recursive
delete of root/home paths, secret deletion, package publishing, and pipe-to-shell
installers.

The hook is a guardrail, not a replacement for the repository rules in `AGENTS.md`.
