.PHONY: install validate summary schemas schema-check simulate-smoke validate-smoke summarize-smoke simulate-standard validate-standard summarize-standard test coverage lint typecheck check verify clean

PYTHON ?= python
PYTEST_ENV ?= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
SMOKE_DIR ?= .artifacts/smoke
STANDARD_DIR ?= .artifacts/standard
SCHEMA_TMP ?= schemas-generated

install:
	$(PYTHON) -m pip install -e ".[dev]"

validate:
	$(PYTHON) -m paic validate --spec-dir specs

summary:
	$(PYTHON) -m paic summary --spec-dir specs

schemas:
	$(PYTHON) -m paic export-schemas --output-dir schemas

schema-check:
	rm -rf $(SCHEMA_TMP)
	$(PYTHON) -m paic export-schemas --output-dir $(SCHEMA_TMP)
	diff -ru schemas $(SCHEMA_TMP)
	rm -rf $(SCHEMA_TMP)

simulate-smoke:
	$(PYTHON) -m paic simulate --config configs/simulation/smoke.yaml --output-dir $(SMOKE_DIR) --overwrite

validate-smoke:
	$(PYTHON) -m paic dataset validate --dataset-dir $(SMOKE_DIR)

summarize-smoke:
	$(PYTHON) -m paic dataset summary --dataset-dir $(SMOKE_DIR)

simulate-standard:
	$(PYTHON) -m paic simulate --config configs/simulation/standard.yaml --output-dir $(STANDARD_DIR) --overwrite

validate-standard:
	$(PYTHON) -m paic dataset validate --dataset-dir $(STANDARD_DIR)

summarize-standard:
	$(PYTHON) -m paic dataset summary --dataset-dir $(STANDARD_DIR)

test:
	env $(PYTEST_ENV) $(PYTHON) -m pytest -q

coverage:
	env $(PYTEST_ENV) $(PYTHON) -m pytest -p pytest_cov.plugin --cov=paic --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy src tests

check: validate format-check lint typecheck coverage schema-check

verify: check simulate-smoke validate-smoke

clean:
	rm -rf .artifacts .coverage .mypy_cache .pytest_cache .ruff_cache build dist htmlcov schemas-generated
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
