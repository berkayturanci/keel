# Security Audit — 2026-06-08

## Scope

This audit reviewed keel at `62ded2f960fb28777f4fceb3a8c5063157aff44b`
(`v0.6.1` source state), with focus on:

- Python package supply chain and dependency exposure.
- GitHub Actions permissions, pinned actions, publishing, and release artifacts.
- Runtime trust boundaries for command gates, extensions, adapters, and optional `jury`.
- Public repository hygiene for secrets, private project names, and open-source security files.
- GitHub repository security settings visible through the authenticated GitHub API.

## Summary

No critical or high-severity application security issue was found in the keel source tree.
The deterministic core has a small dependency surface, uses `yaml.safe_load`, keeps GitHub
and git subprocess calls on argv-based wrappers, documents the command-gate trust boundary,
and publishes releases through OIDC trusted publishing with provenance attestations.

The audit opened two concrete hardening items and one release-policy decision:

1. GitHub vulnerability alerts are currently disabled for the repository.
2. `.pre-commit-config.yaml` is not covered by Dependabot update automation.
3. Future release tag signing expectations should be documented.

One accepted risk remains documented rather than treated as a vulnerability: command gates
execute project-configured shell commands. This is keel's intended model and is already
covered in `SECURITY.md`.

## Evidence

### Automated checks

| Check | Result |
|---|---|
| `pip-audit` over the audit virtual environment | No known vulnerabilities found |
| `bandit -r src scripts` | No high-severity findings |
| Bandit medium finding | `B604` on `src/keel/runner.py` because command gates intentionally use `shell=True` |
| Release smoke test for PyPI `keel-workflow==0.6.1` | Passed; `keel version` returned `keel 0.6.1` |

Bandit's additional low findings were either subprocess import/call reminders or false
positives on consent metadata strings such as `secrets` and `False`.

### Repository and workflow controls

- Repository is public and has `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, issue templates, a PR template, `CODEOWNERS`, CodeQL, Scorecard,
  Dependabot, and release documentation.
- GitHub Actions use least-privilege top-level permissions and job-scoped elevations where
  needed.
- Workflow `uses:` references are pinned to exact commit SHAs.
- The publish workflow uses PyPI trusted publishing (OIDC), creates a GitHub Release,
  uploads wheel, sdist, SBOM, and `SHA256SUMS`, and emits build-provenance attestations.
- CodeQL and Scorecard latest runs on `main` completed successfully.
- Branch protection for `main` has strict required CI checks and force-push/delete
  protection disabled; required PR approvals are not currently enforced.

### Trust boundaries reviewed

- `src/keel/config.py` and `src/keel/install.py` parse YAML with `yaml.safe_load`.
- `src/keel/git.py` and `src/keel/github.py` invoke `git` / `gh` through argv wrappers, not a
  shell.
- `src/keel/runner.py` intentionally executes project-provided command gates through a shell.
  This is documented in `SECURITY.md` and should be treated like running a Makefile or CI
  script from the target repository.
- `src/keel/jury.py` writes the diff to a temporary file and passes the file path to the
  optional `jury` CLI through argv. The temporary diff file is unlinked after execution.
- Operator consent contracts record approved scopes and explicitly avoid recording secret
  values.

## Findings

### Medium — GitHub vulnerability alerts are disabled

**Evidence:** `gh api repos/berkayturanci/keel/vulnerability-alerts` returned
`404 Not Found` with the message `Vulnerability alerts are disabled`.

**Impact:** Dependabot version-update PRs can still run from `.github/dependabot.yml`, but
GitHub will not surface repository-level Dependabot security alerts for vulnerable dependency
versions. For an open-source package, this weakens the maintainer's early-warning channel for
newly disclosed dependency issues.

**Recommendation:** Enable Dependabot alerts, and preferably Dependabot security updates, in
the repository security settings.

**Tracking:** [#123](https://github.com/berkayturanci/keel/issues/123) because this is a
repository setting gap, not a private vulnerability disclosure.

**Status:** Resolved on 2026-06-08 by enabling Dependabot vulnerability alerts and automated
security fixes through the GitHub repository API.

### Low — Pre-commit hook updates are not automated

**Evidence:** `.github/dependabot.yml` covers `github-actions` and `pip`, but not the
`pre-commit` package ecosystem. `.pre-commit-config.yaml` pins hook revisions separately.

**Impact:** Local development hooks can drift behind security and compatibility fixes.
This is lower risk than runtime dependencies because the hooks are developer tooling, but it
is still open-source supply-chain maintenance.

**Recommendation:** Add a Dependabot `pre-commit` ecosystem entry for `/`.

**Tracking:** [#124](https://github.com/berkayturanci/keel/issues/124).

**Status:** Addressed by adding Dependabot `pre-commit` ecosystem coverage.

### Informational — `v0.6.1` release tag is annotated, not signed

**Evidence:** The local release attempt could not create a signed tag because `gpg` was not
available in the release environment, so `v0.6.1` was pushed as an annotated tag.

**Impact:** The release still has GitHub build-provenance attestations and PyPI OIDC
publishing evidence, but the git tag itself does not add a maintainer signature signal.

**Recommendation:** Document the expected signed-tag release path and decide whether future
production releases should require a signed tag before publish.

**Tracking:** [#125](https://github.com/berkayturanci/keel/issues/125).

**Status:** Addressed by documenting the release tag signing policy in
`docs/keel/release.md`: signed tags are preferred, annotated tags are accepted when signing is
unavailable, and lightweight production release tags are not allowed.

## Accepted Risk

### Command gates execute configured shell commands

`src/keel/runner.py` uses `shell=True` for command gates. This is intentional: projects
configure build, lint, and command extension bodies as shell snippets. The risk is equivalent
to running a repository's Makefile, package scripts, or CI commands. The boundary is
documented in `SECURITY.md`, and keel should continue treating untrusted project configs as
untrusted executable input.

No issue was opened for this item because it is core behavior, not an implementation bug.

## No Issue Opened For

- Bandit low false positives on consent metadata strings.
- Optional `jury` CLI invocation; it is argv-based, fail-soft, and diff-file scoped.
- TestPyPI trusted-publisher setup; it is already tracked separately.

## Recommended Next Steps

1. Re-run the GitHub repository security-settings check after future settings changes.
2. Re-run this audit after the next release workflow.
