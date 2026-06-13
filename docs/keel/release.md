# Release Runbook

This runbook keeps keel's PyPI release path repeatable and auditable.

## Package Identity

- PyPI distribution: `keel-workflow`
- Console script: `keel`
- Python package import: `keel`
- Runtime dependency: `PyYAML>=6`
- Supported Python versions: 3.11, 3.12, 3.13

The bare `keel` distribution name is already used by an unrelated PyPI project, so this
project intentionally publishes as `keel-workflow`.

## Current Release State

As of 2026-06-13, the current production PyPI release is `keel-workflow==1.2.3`,
owned by `berkayturanci`. Verify the current state before every new release with:

```bash
python -m pip index versions keel-workflow
```

A production release must contain both expected distributions:

- `keel_workflow-<version>-py3-none-any.whl`
- `keel_workflow-<version>.tar.gz`

The matching GitHub Release must also contain:

- the wheel
- the source distribution
- `sbom.cdx.json`
- `SHA256SUMS`

The PyPI wheel and source-distribution SHA256 digests must match the GitHub Release asset
digests. TestPyPI does not currently contain `keel-workflow`; use the rehearsal flow below
before the next production publish if TestPyPI trusted publishing has been configured.

## Preflight Checklist

Before tagging a release:

- Update `CHANGELOG.md` with the release notes.
- Confirm `pyproject.toml` metadata: name, version, description, readme, license, authors,
  Python version, dependencies, classifiers, URLs, and `keel = "keel.cli:main"`.
- Confirm package data includes `schema/*.json` and `adapters/commands/*.md`.
- Run the local gates:

```bash
make lint
make coverage
make validate
```

## Local Build And Smoke Test

Build the source distribution and wheel:

```bash
python -m pip install --upgrade build
rm -rf dist/
python -m build --sdist --wheel --outdir dist/
```

Then install the wheel into a clean virtual environment and smoke-test the installed package:

```bash
python scripts/release_smoke.py --dist-dir dist
```

The smoke test verifies:

- `keel --help`
- `keel version`
- `keel setup --root <tmp-project>`
- `keel sync --root <tmp-project> --dry-run`
- packaged adapter command markdown is present
- generated shared skills have YAML frontmatter and non-empty bodies
- generated Claude commands have non-empty bodies
- generated surfaces do not contain consumer-specific names or project policy

## TestPyPI Rehearsal

Configure a TestPyPI trusted publisher for this repository before running this step:

- repository owner/name: `berkayturanci/keel`
- workflow filename: `.github/workflows/publish.yml`
- environment: leave unset unless the workflow uses an environment

Then dispatch the publish workflow manually from GitHub Actions. Manual dispatch publishes to
TestPyPI only; it does not create a GitHub Release.

After the workflow succeeds, install from TestPyPI while resolving dependencies from PyPI:

```bash
python scripts/release_smoke.py \
  --requirement "keel-workflow==<version>" \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/
```

Do not treat a TestPyPI pass as production evidence unless the package version, commit, and
artifact hashes match the intended release.

## Production Publish

Production publishes are tag-driven through `.github/workflows/publish.yml`.

One-time PyPI trusted publisher settings:

- project: `keel-workflow`
- owner: `berkayturanci`
- repository: `keel`
- workflow: `publish.yml`
- environment: leave unset unless the workflow is changed to require one

## Tag Signing Policy

Signed release tags are preferred, but they are not currently a production publish blocker.
The required release evidence chain is:

- PyPI trusted publishing through OIDC
- GitHub build-provenance attestation
- GitHub Release assets for the wheel, source distribution, SBOM, and `SHA256SUMS`
- post-publish smoke testing of the package installed from PyPI

Use a signed tag when GPG is available:

```bash
git tag -s v<version> -m "v<version>"
```

If the release environment cannot sign tags, use an annotated tag and record the fallback in
the release notes or release issue:

```bash
git tag -a v<version> -m "v<version>"
```

Do not use a lightweight tag for production releases. If the project later decides to make
signed tags mandatory, add workflow enforcement before changing this policy.

Publish by pushing a version tag:

```bash
# Prefer `git tag -s`; use `git tag -a` only when signing is unavailable.
git tag -s v<version> -m "v<version>"
git push origin v<version>
```

The workflow must produce:

- wheel
- source distribution
- CycloneDX SBOM
- `SHA256SUMS`
- build-provenance attestation
- GitHub Release

After publish, verify PyPI and smoke-test the production package:

```bash
python -m pip index versions keel-workflow
python scripts/release_smoke.py --requirement "keel-workflow==<version>"
gh release view "v<version>" --json assets
```

Confirm the PyPI wheel and source-distribution SHA256 digests match the GitHub Release asset
digests before announcing the release.

## Rollback And Re-Run Notes

PyPI files are immutable. If a release is wrong:

- yank the bad version on PyPI
- leave the GitHub Release visible with a correction note, or mark it as prerelease if it was
  not intended for production
- fix forward with a new version

The publish workflow uses `skip-existing: true`, so a tag workflow re-run does not overwrite
already-uploaded distributions. Re-run only after confirming that the existing artifacts are
the intended ones.
