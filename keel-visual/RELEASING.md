# Releasing keel-visual

keel-visual is a separate distribution from keel core (`keel-workflow`). It
depends on `keel-workflow >= 1.3.0`, so **release a matching core first**.

## Prerequisites

- keel core `1.3.0` (or newer) is published to PyPI, so `pip install keel-visual`
  can resolve its dependency. (Core is released by the repo-root `publish.yml`
  workflow / its own process.)
- A PyPI account with upload rights and an API token. **The upload step requires
  your credentials — run it yourself; never paste a token into automation you
  do not control.**

## 1. Bump the version

Edit `keel-visual/pyproject.toml` → `[project] version`. Update this repo's
`CHANGELOG.md` companion note if the surface changed. Commit on a branch and
merge through the normal flow.

## 2. Build the distributions

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

## 3. Smoke-test the wheel in a clean venv

```
python -m venv /tmp/kv-rel && /tmp/kv-rel/bin/pip install dist/*.whl
/tmp/kv-rel/bin/keel-visual --help
```

(If core is not yet on PyPI, install it from source into the same venv first.)

## 4. Upload (operator-run)

```
python -m twine upload dist/*          # prompts for your PyPI token
```

Use TestPyPI first if you want a dry run: `twine upload --repository testpypi dist/*`.

## 5. Tag

Tag the release (e.g. `keel-visual-v0.1.0`) so the published artifact is
traceable to the commit.
