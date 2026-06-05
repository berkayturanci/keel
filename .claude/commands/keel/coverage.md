---
description: Report test coverage, find under-covered hot spots, and open issues to close gaps.
argument-hint: "[--threshold <pct>] [--changed] [--open-issues]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Edit
---

# /keel:coverage

Project-neutral coverage report. Reads `.keel/project.yaml`.

1. Run the project's coverage tooling (the project's test/coverage command via its toolchain;
   keel itself uses `make site` / `coverage`).
2. Report overall + per-area coverage. `--changed` scopes to `git diff base...HEAD`.
3. Flag **hot spots**: low-coverage files that also match `tier3_globs` (high risk × low
   coverage first). Compare against `--threshold` if given.
4. `--open-issues` → open a deduped issue per hot spot, tiered, and hand the fix to
   `/keel:ship`.

Read-only unless `--open-issues`; deterministic for identical coverage data.
