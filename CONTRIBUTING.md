# Contributing to keel

Thanks for your interest in **keel**. The goal is to keep a *project-neutral* workflow
core — a fixed backbone plus per-project config and add-only Lego extensions — small,
inspectable, and easy to consume from any repository.

## Code of Conduct

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Design rules (please match these)

- **Project-agnostic core.** No downstream/private project names, paths, agents, or
  organization-specific workflows in `src/keel`, the adapters, or the docs. A consumer's
  specifics live in *its* `.keel/project.yaml` + `.keel/extensions/`, never in keel.
- **Pure core + thin I/O.** Put deterministic logic in a pure, unit-tested function; keep
  subprocess/network/prompting in a thin wrapper with an injectable `_run` seam.
- **Add-only backbone.** Extensions snap into named slots; they never remove, reorder, or
  replace a backbone step. `on_fail: block` is permitted only in documented blocking slots:
  `guard`, `tester`, `test`, and `pre-merge`.
- **Single runtime dependency.** PyYAML only on Linux/macOS. Dev-only tools (`ruff`,
  `coverage`, `build`) live in the `dev` extra. The one platform exception is `tzdata` on
  Windows (`sys_platform == 'win32'`), where the stdlib `zoneinfo` has no system IANA
  database to read; it is never installed on Linux/macOS.
- **Python ≥ 3.11.** `requires-python` in `pyproject.toml` is the source of truth.

## Pull Requests

1. Fork and branch from `main`.
2. Set up locally:
   ```bash
   python3 -m venv venv && source venv/bin/activate
   pip install -e ".[dev]"
   ```
3. Run the checks before submitting:
   ```bash
   make test        # offline unit suite (no network, no credentials)
   make lint        # ruff
   make coverage    # coverage gate (fail_under in pyproject)
   make validate    # validate every projects/*.yaml
   ```
   These targets resolve their own interpreter (`scripts/find_python.sh`: the repo venv,
   then the newest `python3.x` on PATH that is ≥ 3.11 and can import yaml) rather than
   assuming `python3` is one — on macOS it is Xcode's 3.9, where the suite fails with a
   hundred syntax errors that look like a regression. `PY=/path/to/python make test`
   overrides the resolver; `make doctor-python` prints what it picked, and
   `keel doctor` reports the same interpreter under its `python_toolchain` check.
4. The pure core is held at **100% line + branch coverage**. New core logic needs tests —
   and for a *fix*, coverage is not the bar; see step 7.
   `make test` also fails if any tracked file — `CHANGELOG.md` most often, since it conflicts
   on nearly every PR — still carries an unresolved `<<<<<<<`/`=======`/`>>>>>>>` marker after
   a merge or rebase.
5. Update docs (`docs/keel/`, README) and `CHANGELOG.md` (`[Unreleased]`) when behaviour
   changes. If you change the `/keel:<command>` adapters, re-install with
   `keel install-adapter all --force`.
6. Write a real PR description: a **Summary** in your own prose plus a **Related issues**
   reference (`Closes #N` / `Relates to #N`, or `no issue` for a pure chore). The
   [PR description lint](.github/workflows/pr-lint.yml) check enforces this — a PR template
   only pre-fills the body, it can't stop an empty PR.
7. **If the PR fixes something, say what fails without it — hunk by hunk.** For *each*
   source hunk, name a test that fails when that hunk alone is reverted:

   > Fix evidence: removing `/worktrees/` from the ignore tuple in `workspace.py` fails
   > `test_git_ignores_a_swarm_worktree_at_the_path_swarm_writes_to`, which passed before.

   One line per hunk. A hunk you cannot pin this way is listed with the reason — that is a
   normal outcome, and naming it is the point. Whole-fix reverts hide exactly the failure this
   is for: two of the closures below stated a true revert result while half the fix sat
   unguarded, because reverting *everything* failed a test that the unguarded half did not own.

   `Fix evidence: N/A — <docs | pure refactor | dependency bump | packaging>` is available for
   changes with nothing to revert-test. **It is not available to a PR that closes a `type:bug`
   issue or ticks `Bug fix`** — a refactor that closes a bug report either names a hunk and a
   test, or says `Relates to #N` and leaves the issue open. That one restriction is what would
   have kept #877 open; its closing PR was a genuine refactor that removed two redundant
   frozensets and closed a bug it never touched.

   **"Maintained 100 % coverage" is not evidence.** `pyproject.toml` sets `fail_under = 100`
   and CI enforces it, so the sentence was already true before your change — it describes the
   repository, not your test. Coverage cannot carry this weight in any case: it proves a line
   *ran*, not that an assertion depends on it.

   This is measured, not theoretical. An audit reverted each of 14 closed swarm fixes and re-ran
   its guarding tests: today all 14 fail without their fix, but **three survived at the time
   their issue was closed** — #877 (the fix was never written), and half of each of #871 and
   #879. All three were caught by later audits, never by the closing procedure, and all three
   carried "Maintained 100 % line + branch coverage" as their evidence. Full rationale, and a
   proposal to mechanise the check, in [#1289](https://github.com/berkayturanci/keel/issues/1289).

   **A revert check is necessary and not sufficient.** #873's fix passes it — its test does fail
   without it — and it still shipped the regression now filed as
   [#1268](https://github.com/berkayturanci/keel/issues/1268), because the fixture used the same
   issue in both waves, so the fix and the bug produced the same result. The rule that would have
   caught it cannot be scripted: **the fixture has to be one where the fix changes the outcome.**
   If your test would assert the same thing for some other reason, it is not evidence yet.

## Bot-owned branches are read-only

A pull request branch opened by an automation is a **read-only input**. The registered
prefixes are `jules`, `bolt`, `palette`, `sentinel`, `dependabot`, `copilot` and
`renovate`, in any spelling (`bolt-x`, `palette/x`, `jules-1234-abcd`).

**Do not rebase such a branch, amend it, or push fixes to it.** The bot pushes from its
own checkout, so its next push replaces the branch with that stale copy and silently
reverts anything that landed in between. Re-land the reviewed changes on a fresh `fix/`,
`perf/` or `docs/` branch cut from `main` — cherry-pick the bot's commit unchanged so its
authorship survives — and close the bot's pull request with a link to the replacement.

This is not hypothetical. On [#1125](https://github.com/berkayturanci/keel/pull/1125) a
review found four defects in a Palette change and the fixes were pushed onto the
`jules-…` branch; while the last gate round was running the bot pushed *Acknowledge
reviewer verdicts*, which reverted all of them — **222 deletions**, the entire 189-line
test class among them, restoring a `var` above `"use strict"` that silently un-stricts a
420-line IIFE. A few minutes' different timing and it would have merged under a green
suite, because the tests that would have caught it were in the commit it deleted. The
work was re-landed as [#1126](https://github.com/berkayturanci/keel/pull/1126). The
sibling repository adopted the same rule after an equivalent incident cost it two
already-merged pull requests.

Branches a person drives from a working copy (`claude/…`, `codex/…`, `cursor/…`, `fix/…`)
are deliberately outside the rule — it is about a branch something else holds the only
copy of, not about who wrote the code.

## Dependency and tooling updates

GitHub Actions are pinned to commit SHAs and the runtime dependency footprint stays at one
(PyYAML). Updates are proposed automatically and reviewed by hand:

- **Automation.** [Dependabot](.github/dependabot.yml) opens grouped weekly PRs for GitHub
  Actions and for Python tooling declared in `pyproject.toml`.
- **Review policy.** Action bumps stay pinned to a full SHA with a trailing `# vX.Y.Z`
  comment (never a floating tag); CI + CodeQL must be green; major bumps get a behaviour
  check, not just a version merge.
- **Security posture.** [CodeQL](.github/workflows/codeql.yml) scans on every push/PR and
  weekly; [OpenSSF Scorecard](.github/workflows/scorecard.yml) publishes a repo-health
  result from `main`. Treat new high-severity findings as release blockers (see
  [SECURITY.md](SECURITY.md)).

## Releases

User-visible changes update [CHANGELOG.md](CHANGELOG.md). A release is a version bump in
`pyproject.toml` + `src/keel/__init__.py`, a promoted CHANGELOG section, and a `vX.Y.Z`
tag — the tag triggers [`publish.yml`](.github/workflows/publish.yml) (PyPI trusted
publishing + a GitHub Release with SBOM, checksums, and build provenance).

The version lives in more places than that pair, so use `make release-bump VERSION=x.y.z`
rather than editing by hand, and check the result:

```bash
make release-check   # offline; refuses a release that does not agree with itself
```

It compares the declared version against the top released `## [x.y.z]` CHANGELOG section
(the guard for a CHANGELOG never renamed from `## [Unreleased]`), against every surface
listed in `scripts/release_surfaces.py` — plugin manifests, pinned-install references, the
site fallbacks — and checks `keel-visual`'s two version markers agree with each other.
`publish.yml` runs the same command before it builds anything, and verifies the
published package afterwards: a clean-venv install from PyPI, the release
smoke test, and a SHA256 cross-check against the GitHub Release. See
[the release runbook](docs/keel/release.md).
