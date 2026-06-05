---
description: Take an implemented branch through PR → CI → review → merge (s6–s12, standalone).
argument-hint: "[branch or pr] [--review-comments <inline|summary>] [--dry-run]"
allowed-tools: Bash(keel:*), Bash(git:*), Bash(gh:*), Bash(jury:*), Read, Edit, Agent
---

# /keel:pr-loop

Drive an already-implemented branch from PR to merged (`s6`–`s12`). Reads `.keel/project.yaml`.

1. Push the branch; open the PR against `base_branch`.
2. Wait for the project's `ci_workflows` to go green (`gh pr checks`); re-kick transient
   failures, surface real ones.
3. Review + gates + fix loop — delegate to `/keel:review-cycle` (inline-hybrid default).
4. `keel ship .keel/project.yaml --root . --pr <N>` → confirm decision is `MERGE`.
5. **s10 merge** inside the merge window only (unless `--hotfix`), holding the merge lock:
   re-confirm CI green + zero blocking findings → squash-merge. `pause` halts / `freeze`
   defers outside the window.
6. Capture the run for `/keel:wrap`; close the linked issue; drop the lock; then **clean up**:
   `git worktree remove <path> --force` and delete the merged branch — `git branch -d <branch>`
   (safe; `-d` refuses unmerged) + `git push origin --delete <branch>` (skip if the repo
   auto-deletes head branches).

`--dry-run`: stop after the assessment; never push/merge.
