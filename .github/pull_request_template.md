## Summary

Describe the change and the problem it solves.

## Related issues

<!-- REQUIRED. List every issue this PR addresses, one reference per item.
     Use "Closes #N" to auto-close on merge, or "Relates to #N" for partial work.
     If this PR genuinely touches no issue (e.g. a pure chore), write "no issue".
     The PR-description lint check enforces a real summary above + a reference here. -->
Closes #

## Type of Change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Code polish / refactor
- [ ] Release / packaging
- [ ] Other

## Fix evidence

<!-- REQUIRED for a fix. One line per source hunk, naming a test that fails when THAT HUNK
     ALONE is reverted:
       "Removing /worktrees/ from the ignore tuple in workspace.py fails
        test_git_ignores_a_swarm_worktree_at_the_path_swarm_writes_to, which passed before."
     A whole-fix revert is not enough: two past closures stated a true revert result while
     half the fix sat unguarded.
     N/A — docs | pure refactor | dependency bump | packaging. NOT available if this PR
     closes a type:bug issue or ticks "Bug fix" above; use "Relates to #N" instead and
     leave the issue open.
     "Maintained 100% coverage" is NOT evidence: fail_under=100 is enforced in CI, so it was
     already true before your change. See CONTRIBUTING.md step 7 and #1289. -->

- [ ] Each source hunk, reverted alone, makes a named test fail — stated above
- [ ] Any hunk left unpinned is listed with the reason
- [ ] Or `N/A — <category>`, and this PR does not close a bug
- [ ] The fixture is one where the fix **changes the outcome** — not one that would assert
      the same thing anyway (#1268 passed a revert check and still shipped a regression)

## Verification

- [ ] `make test`
- [ ] `make lint`
- [ ] `make validate`
- [ ] `make coverage` (pure core stays at 100% line + branch)
- [ ] `CHANGELOG` updated
- [ ] #63 parity matrix row updated (if a command changed)
- [ ] Docs / `CHANGELOG.md` updated if behaviour changed

## Project-Agnostic Check

- [ ] This change introduces **no** downstream project names, private workflow names, or
      organization-specific assumptions into the reusable core / adapters / docs.
