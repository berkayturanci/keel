# keel — developer entrypoints (mirrors ai-jury's ergonomics)
#
#   make test      run the offline unit suite (no network, no credentials)
#   make lint      ruff check
#   make coverage  coverage gate (fail_under in pyproject [tool.coverage.report])
#   make validate  validate every projects/*.yaml and .keel/project.yaml
#   make site      build the coverage report into website/ and serve it at :8000
#   make adapters  (re)install keel's own /keel:* adapters — both surfaces
#                  (.claude/commands/keel/ + the shared .agents/skills/keel-* skill set)
#   make plugin    regenerate the committed Claude Code plugin command files (commands/*.md)
#                  from src/keel/adapters/commands/ — the drift test locks these byte-for-byte
#   make release-check
#                  refuse a release that does not agree with itself: CHANGELOG top
#                  released section == declared version, every release surface in
#                  scripts/release_surfaces.py on that version, keel-visual markers
#                  in step. Same guards publish.yml runs before it builds anything.
#   make release-bump VERSION=x.y.z
#                  bump the version everywhere a release must touch (pyproject, __init__,
#                  Claude + Codex plugin manifests, pinned-install refs) and regenerate all
#                  adapter surfaces
#   make doctor-python
#                  print the interpreter these targets resolved, and its version

# PY — the interpreter every target below runs on.
#
# `PY=/path/to/python make test`, or an exported PY (what CI's setup-python step
# effectively provides), always wins: the resolver only runs when PY is unset.
# Otherwise scripts/find_python.sh picks the first interpreter that is >= 3.11
# *and* can import yaml, instead of assuming `python3` is one — on macOS that is
# Xcode's 3.9, where `make test` fails with ~110 import errors that read like a
# regression rather than a missing toolchain (#1022).
ifeq ($(origin PY),undefined)
PY := $(shell scripts/find_python.sh)
ifeq ($(strip $(PY)),)
# The resolver already printed the one-line install hint on stderr. Failing here
# is deferred to the first target that actually expands PY, so `make clean` and
# `make lint` still work on a machine with no usable interpreter.
PY = $(error no usable Python — see the find_python message above, or set PY=/path/to/python)
endif
endif

.PHONY: test lint coverage validate site adapters plugin release-check release-bump doctor-python clean

test:
	PYTHONPATH=src $(PY) -m unittest discover -s tests -v

lint:
	ruff check .

coverage:
	PYTHONPATH=src $(PY) -m coverage run -m unittest discover -s tests
	PYTHONPATH=src $(PY) -m coverage report

validate:
	PYTHONPATH=src $(PY) -m keel validate projects/*.yaml .keel/project.yaml

site:
	PYTHONPATH=src $(PY) -m coverage run -m unittest discover -s tests
	PYTHONPATH=src $(PY) -m coverage html -d website/coverage
	@echo "serving keel site at http://localhost:8000  (Ctrl-C to stop)"
	cd website && $(PY) -m http.server 8000

adapters:
	PYTHONPATH=src $(PY) -m keel install-adapter all --force

plugin:
	PYTHONPATH=src $(PY) -m keel install-adapter plugin --root . --force

release-check:
	$(PY) scripts/release_check.py

release-bump:
	@test -n "$(VERSION)" || { echo "usage: make release-bump VERSION=x.y.z"; exit 1; }
	$(PY) scripts/release_bump.py "$(VERSION)" --strict
	$(MAKE) plugin
	$(MAKE) adapters
	@echo "release-bump done. Add a CHANGELOG.md entry for $(VERSION), run the gates, then follow docs/keel/release.md to tag."

doctor-python:
	@echo "interpreter : $(PY)"
	@$(PY) -c 'import sys, yaml; print("version     :", sys.version.split()[0]); print("pyyaml      :", yaml.__version__)'

clean:
	rm -rf .coverage htmlcov **/__pycache__ src/**/__pycache__
