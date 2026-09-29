# Releasing keel-visual

keel-visual is a separate distribution from keel core (`keel-workflow`). It
depends on the core floor declared in `keel-visual/pyproject.toml`
(`keel-workflow>=1.15.0` as of 0.9.0), so **release a matching core first**.

That floor is the first core release carrying every `keel` name keel-visual
imports. It sat at 1.6.0 while `keel-visual swarm` imported `keel.swarm`, which
first shipped in 1.15.0. Before each release, grep `keel-visual/src` for
`from keel` / `import keel`, and run the built wheel against the floor version
from PyPI (step 3 below) as well as the latest.

## Recommended: automated trusted publishing (tag → CI)

The [`publish-visual.yml`](../.github/workflows/publish-visual.yml) workflow builds
and publishes keel-visual on a `keel-visual-v*` tag, using **OIDC trusted
publishing** — no API token in CI. It mirrors the core's `publish.yml` posture:
a tag-versus-version guard before the build, hash-locked build tools, a check that
every template shipped in the wheel, build-provenance attestation, SBOM + checksums,
and a GitHub Release.

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
2. Ensure the matching core (the `keel-workflow` floor in `pyproject.toml`) is
   already on PyPI.
3. Tag and push:

   ```
   git tag keel-visual-v0.1.0 && git push origin keel-visual-v0.1.0
   ```

   CI first runs `python scripts/release_check.py --package keel-visual --tag "$TAG"`
   and stops if the tag does not name the version in `keel-visual/pyproject.toml`
   (or the two version markers disagree). Run the same command locally before
   pushing the tag. Then it builds, attests, publishes to PyPI via OIDC, and cuts
   the GitHub Release.

The manual build/upload below is the **fallback** (e.g. before trusted publishing
is configured).

---

## Manual fallback

### Prerequisites

- keel core at the `pyproject.toml` floor (or newer) is published to PyPI, so
  `pip install keel-visual` can resolve its dependency.
- A PyPI account with upload rights and an API token. **The upload step requires
  your credentials — run it yourself; never paste a token into automation you
  do not control.**

### 1. Bump the version

```
python scripts/release_bump.py X.Y.Z --package keel-visual
```

Do not edit `pyproject.toml` by hand. keel-visual carries the version in **two**
files — `keel-visual/pyproject.toml` and `keel-visual/src/keel_visual/__init__.py`
— and this step used to name only the first. `__version__` was left at `0.6.0`
through the 0.7.0 and 0.8.0 releases as a result (#796); `keel_visual.__version__`
is public API, so it is what a bug report quotes.

`test_keel_visual_version_markers_agree` now fails if the two disagree, and
re-running the command above at the current version repairs whichever file has
drifted rather than reporting nothing to do.

Update `keel-visual/CHANGELOG.md`, and this repo's `CHANGELOG.md` companion note
if the surface changed. Commit on a branch and merge through the normal flow.

### 2. Build the distributions

From `keel-visual/`:

```
python -m pip install --upgrade build twine
python -m build            # writes dist/keel_visual-<version>-py3-none-any.whl + .tar.gz
python -m twine check dist/*
```

`build` uses the `hatchling` backend declared in `pyproject.toml`; the
`force-include` there ships the HTML templates (`board.html`, `dashboard.html`,
`runviz.html`, `swarm.html`) inside the wheel. Verify every template in the source
tree is present:

```
python -c "import zipfile,glob,pathlib; w=glob.glob('dist/*.whl')[0]; \
n=set(zipfile.ZipFile(w).namelist()); \
print('missing:', [p.name for p in pathlib.Path('src/keel_visual/templates').glob('*.html') \
if 'keel_visual/templates/' + p.name not in n])"
```

It should print `missing: []`.

### 3. Smoke-test the wheel in a clean venv

```
python -m venv /tmp/kv-rel && /tmp/kv-rel/bin/pip install dist/*.whl
/tmp/kv-rel/bin/keel-visual --help
```

(If core is not yet on PyPI, install it from source into the same venv first.)

Then check the floor: in a second venv, `pip install keel-workflow==<floor>` from
PyPI, install the wheel with `--no-deps`, and run the suite against it from a
directory whose `.keel/project.yaml` that core accepts (`keel init` writes one):

```
python -m unittest discover -s <repo>/keel-visual/tests -t <repo>/keel-visual
```

The swarm tests load `.keel/project.yaml` from the working directory, so running
them from the repo root against an older core fails on this repo's newer config
keys, not on keel-visual.

### 4. Upload (operator-run)

```
python -m twine upload dist/*          # prompts for your PyPI token
```

Use TestPyPI first if you want a dry run: `twine upload --repository testpypi dist/*`.

### 5. Tag

Tag the release (e.g. `keel-visual-v0.1.0`) so the published artifact is
traceable to the commit. From the repo root, `python scripts/release_check.py
--package keel-visual --tag keel-visual-v0.1.0` confirms the tag names the version
the tree declares.
