---
description: Morning briefing — deferrals, overnight-shipped work, and a ranked focus list.
argument-hint: "[--since <ref|timestamp>]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Read, Write
---

# /keel:morning

Project-neutral daily brief. Reads `.keel/project.yaml` for timezone + repo.

1. **Deferred queue** — read the cross-session deferral store (items deferred by `/keel:ship`
   outside the merge window) and surface them first.
2. **Shipped since last brief** — `gh` query for issues closed / PRs merged since `--since`
   (default: the last brief's timestamp, else 24h). Section: "✅ Shipped".
3. **Ranked focus** — compute live, not from a static file: blocker issues → review-approved
   + CI-green PRs (ready to merge) → stale PRs (no activity > 3 days) → unassigned bugs →
   CI failures on `base_branch`. Append any project `priorities.md` as a "Manual focus" note.
4. **Window** — `keel window .keel/project.yaml` so the brief states whether merges are open.

Write the brief to the project's reports path; keep it deterministic for identical state.
