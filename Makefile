# keel — developer entrypoints (mirrors ai-jury's ergonomics)
#
#   make test      run the offline unit suite (no network, no credentials)
#   make lint      ruff check
#   make coverage  coverage gate (fail_under in pyproject [tool.coverage.report])
#   make validate  validate every projects/*.yaml and .keel/project.yaml
#   make site      build the coverage report into website/ and serve it at :8000
#   make adapters  (re)install keel's own /keel:* adapters — both surfaces
#                  (.claude/commands/keel/ + the shared .agents/skills/keel-* skill set)

PY ?= python3

.PHONY: test lint coverage validate site adapters clean

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

clean:
	rm -rf .coverage htmlcov **/__pycache__ src/**/__pycache__
