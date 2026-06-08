# Keel vision

Keel is an agentic work-ownership backbone. Its job is not to be another isolated
coding command, review bot, or merge queue; its job is to make an agent accountable for
the whole path a strong software teammate would normally own.

That path starts before code is written. A good teammate reads the issue, decides whether
the scope is ready, asks for clarification when it is not, creates an isolated branch,
implements the change, keeps CI and tests green, gets reviewed, fixes feedback, merges
inside policy, closes the loop, and records what should be remembered next time. Keel
turns that sequence into a fixed, project-neutral backbone with project-owned config and
extensions.

## v1: one-agent work ownership

Keel v1 is about one accountable agent path. The operator can hand Keel one issue, or a
bounded work block, and expect the same quality loop every time:

- readiness before mutation;
- isolated branch and worktree ownership;
- deterministic gates and runtime capability checks;
- independent review and optional jury;
- merge-window and merge-lock safety;
- structured ledger and checkpoint state;
- closeout and post-merge capture hooks;
- morning and wrap visibility for deferred or skipped work.

The point is not full autonomy for its own sake. The point is that the work is observable,
recoverable, reviewable, and governed by policy while the agent owns the execution details.

## Future: autonomous software team layer

The long-term product direction is an autonomous software team layer built on the same
contracts. This is not the existing `/keel:ship-v2` command/profile, which remains a
compound-engineering variant of the one-agent ship workflow. In the future team layer,
multiple agents can create issues from signals, claim work, review each other, hand off
blocked items, and keep a project moving.

That is intentionally future scope. A human product owner, team lead, or maintainer remains
the decision point for ambiguous requirements, credentials, approvals, product tradeoffs,
and policy changes. Keel should make autonomous teamwork safer and more legible, not hide
ownership behind opaque automation.

## What stays project-neutral

Keel owns the generic workflow mechanism: backbone steps, contracts, resume state, gates,
consent, review shape, merge policy, and capture plumbing. Projects own the values:
branches, labels, path rules, local commands, health providers, domain-specific checks,
report destinations, and extension bodies.

That boundary is what lets the same Keel package run in many repositories without leaking
one project's policy into another.
