# Releasing keel-visual

keel-visual is a separate distribution from keel core (`keel-workflow`). It
depends on `keel-workflow >= 1.3.0`, so **release a matching core first**.

## Recommended: automated trusted publishing (tag → CI)

The [`publish-visual.yml`](../.github/workflows/publish-visual.yml) workflow builds
and publishes keel-visual on a `keel-visual-v*` tag, using **OIDC trusted
publishing** — no API token in CI. It mirrors the core's `publish.yml` posture:
hash-locked build tools, a template-presence check, build-provenance attestation,
SBOM + checksums, and a GitHub Release.

**One-time PyPI setup (operator):**

1. Create the `keel-visual` project on PyPI (and TestPyPI for rehearsals).
2. Add a **trusted publisher** to it:
   - Owner: `berkayturanci`, Repository: `keel`
   - Workflow filename: `publish-visual.yml`
   - Environment: leave blank (none configured)
3. (Optional) Rehearse against TestPyPI: run the workflow via **workflow_dispatch**
   (it publishes to TestPyPI on manual runs).

**Each release (operator):**

1. Bump `keel-visual/pyproject.toml` `[project] version` and merge through the
   normal flow.
2. Ensure the matching core (`keel-workflow >= 1.3.0`) is already on PyPI.
3. Tag and push:

   ```
   git tag keel-visual-v0.1.0 && git push origin keel-visual-v0.1.0
   ```

   CI builds, attests, publishes to PyPI via OIDC, and cuts the GitHub Release.

The manual build/upload below is the **fallback** (e.g. before trusted publishing
is configured).

---

## Manual fallback

### Prerequisites

- keel core `1.3.0` (or newer) is published to PyPI, so `pip install keel-visual`
  can resolve its dependency.
- A PyPI account with upload rights and an API token. **The upload step requires
  your credentials — run it yourself; never paste a token into automation you
  do not control.**

### 1. Bump the version

Edit `keel-visual/pyproject.toml` → `[project] version`. Update this repo's
`CHANGELOG.md` companion note if the surface changed. Commit on a branch and
merge through the normal flow.

### 2. Build the distributions

From `keel-visual/`:

```
python -m pip install --upgrade build twine
python -m build            # writes dist/keel_visual-<version>-py3-none-any.whl + .tar.gz
python -m twine check dist/*
```

`build` uses the `hatchling` backend declared in `pyproject.toml`; the
`force-include` there ships the HTML template (`runviz.html`) inside the wheel.
Verify it is present:

```
python -c "import zipfile,glob; w=glob.glob('dist/*.whl')[0]; \
print('template:', 'keel_visual/templates/runviz.html' in zipfile.ZipFile(w).namelist())"
```

### 3. Smoke-test the wheel in a clean venv

```
python -m venv /tmp/kv-rel && /tmp/kv-rel/bin/pip install dist/*.whl
/tmp/kv-rel/bin/keel-visual --help
```

(If core is not yet on PyPI, install it from source into the same venv first.)

### 4. Upload (operator-run)

```
python -m twine upload dist/*          # prompts for your PyPI token
```

Use TestPyPI first if you want a dry run: `twine upload --repository testpypi dist/*`.

### 5. Tag

Tag the release (e.g. `keel-visual-v0.1.0`) so the published artifact is
traceable to the commit.
