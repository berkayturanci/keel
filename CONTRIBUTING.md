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
  replace a backbone step. `on_fail: block` is valid only in the `pre-merge` slot.
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
4. The pure core is held at **100% line + branch coverage**. New core logic needs tests.
5. Update docs (`docs/keel/`, README) and `CHANGELOG.md` (`[Unreleased]`) when behaviour
   changes. If you change the `/keel:<command>` adapters, re-install with
   `keel install-adapter all --force`.
6. Write a real PR description: a **Summary** in your own prose plus a **Related issues**
   reference (`Closes #N` / `Relates to #N`, or `no issue` for a pure chore). The
   [PR description lint](.github/workflows/pr-lint.yml) check enforces this — a PR template
   only pre-fills the body, it can't stop an empty PR.

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
