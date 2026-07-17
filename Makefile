.PHONY: install validate summary schemas test coverage lint typecheck check clean

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev]"

validate:
	$(PYTHON) -m paic validate --spec-dir specs

summary:
	$(PYTHON) -m paic summary --spec-dir specs

schemas:
	$(PYTHON) -m paic export-schemas --output-dir schemas

test:
	$(PYTHON) -m pytest -q

coverage:
	$(PYTHON) -m pytest --cov=paic --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy src tests

check: validate format-check lint typecheck coverage

clean:
	rm -rf .coverage .mypy_cache .pytest_cache .ruff_cache build dist htmlcov schemas-generated
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
