# keel — developer entrypoints (mirrors ai-jury's ergonomics)
#
#   make test      run the offline unit suite (no network, no credentials)
#   make lint      ruff check
#   make coverage  coverage gate (fail_under in pyproject [tool.coverage.report])
#   make validate  validate every projects/*.yaml against the schema

PY ?= python3

.PHONY: test lint coverage validate clean

test:
	PYTHONPATH=src $(PY) -m unittest discover -s tests -v

lint:
	ruff check .

coverage:
	PYTHONPATH=src $(PY) -m coverage run -m unittest discover -s tests
	PYTHONPATH=src $(PY) -m coverage report

validate:
	PYTHONPATH=src $(PY) -m keel.cli validate projects/*.yaml

clean:
	rm -rf .coverage htmlcov **/__pycache__ src/**/__pycache__
