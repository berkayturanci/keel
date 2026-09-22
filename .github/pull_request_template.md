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

<!-- REQUIRED for a fix. Name the source hunk you reverted and the test that then failed:
     "Reverting the wave_idx guard in swarm_runtime.py makes
      test_orchestration_rebalance_drops_subsequent_wave_on_failure fail."
     For docs, a pure refactor or a dependency bump: "N/A — <reason>".
     "Maintained 100% coverage" is NOT evidence: fail_under=100 is enforced in CI, so it was
     already true before your change. See CONTRIBUTING.md and #1289. -->

- [ ] A revert of the fix makes a named test fail — stated above
- [ ] Or `N/A`, with the reason stated above
- [ ] If the change alters control flow, there is a test case for **each** path through it

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
