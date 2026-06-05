---
description: Audit dependencies for updates and vulnerabilities; open fix issues through keel.
argument-hint: "[--security-only] [--open-issues]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Edit
---

# /keel:deps-audit

Project-neutral dependency audit. Reads `.keel/project.yaml`.

1. Run the project's dependency tooling (the audit/update command is the project's; invoke it
   via the build toolchain referenced in `build_gate_cmd`'s ecosystem).
2. Classify findings: security (CVE) vs. routine updates; pin vs. range bumps.
3. `--security-only` → report only vulnerabilities. Tier each by blast radius vs. `tier3_globs`.
4. `--open-issues` → open a deduped issue per finding, labelled by tier, and hand the fix to
   `/keel:ship`.

Read-only unless `--open-issues`; never bump deps and merge directly — that goes through
`/keel:ship` (window + lock + review).
