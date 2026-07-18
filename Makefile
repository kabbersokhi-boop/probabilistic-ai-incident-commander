.PHONY: install validate summary schemas schema-check simulate-smoke validate-smoke summarize-smoke simulate-standard validate-standard summarize-standard analytics-smoke validate-analytics-smoke summarize-analytics-smoke analytics-standard validate-analytics-standard summarize-analytics-standard detection-smoke validate-detection-smoke summarize-detection-smoke detection-standard validate-detection-standard summarize-detection-standard impact-smoke validate-impact-smoke summarize-impact-smoke impact-standard validate-impact-standard summarize-impact-standard test coverage lint format format-check typecheck check verify clean

PYTHON ?= python
PYTEST_ENV ?= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
SMOKE_DIR ?= .artifacts/smoke
STANDARD_DIR ?= .artifacts/standard
ANALYTICS_SMOKE_DIR ?= .artifacts/analytics-smoke
ANALYTICS_STANDARD_DIR ?= .artifacts/analytics-standard
DETECTION_SMOKE_DIR ?= .artifacts/detection-smoke
DETECTION_STANDARD_DIR ?= .artifacts/detection-standard
IMPACT_SOURCE_SMOKE_DIR ?= .artifacts/impact-source-smoke
IMPACT_SOURCE_STANDARD_DIR ?= .artifacts/impact-source-standard
IMPACT_SMOKE_DIR ?= .artifacts/impact-smoke
IMPACT_STANDARD_DIR ?= .artifacts/impact-standard
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

analytics-smoke: simulate-smoke
	$(PYTHON) -m paic analytics build --dataset-dir $(SMOKE_DIR) --config configs/analytics/smoke.yaml --output-dir $(ANALYTICS_SMOKE_DIR) --overwrite

validate-analytics-smoke:
	$(PYTHON) -m paic analytics validate --analytics-dir $(ANALYTICS_SMOKE_DIR) --dataset-dir $(SMOKE_DIR)

summarize-analytics-smoke:
	$(PYTHON) -m paic analytics summary --analytics-dir $(ANALYTICS_SMOKE_DIR)

analytics-standard: simulate-standard
	$(PYTHON) -m paic analytics build --dataset-dir $(STANDARD_DIR) --config configs/analytics/standard.yaml --output-dir $(ANALYTICS_STANDARD_DIR) --overwrite

validate-analytics-standard:
	$(PYTHON) -m paic analytics validate --analytics-dir $(ANALYTICS_STANDARD_DIR) --dataset-dir $(STANDARD_DIR)

summarize-analytics-standard:
	$(PYTHON) -m paic analytics summary --analytics-dir $(ANALYTICS_STANDARD_DIR)

detection-smoke: analytics-smoke
	$(PYTHON) -m paic detection build --analytics-dir $(ANALYTICS_SMOKE_DIR) --config configs/detection/smoke.yaml --output-dir $(DETECTION_SMOKE_DIR) --overwrite

validate-detection-smoke:
	$(PYTHON) -m paic detection validate --detection-dir $(DETECTION_SMOKE_DIR) --analytics-dir $(ANALYTICS_SMOKE_DIR)

summarize-detection-smoke:
	$(PYTHON) -m paic detection summary --detection-dir $(DETECTION_SMOKE_DIR)

detection-standard: analytics-standard
	$(PYTHON) -m paic detection build --analytics-dir $(ANALYTICS_STANDARD_DIR) --config configs/detection/standard.yaml --output-dir $(DETECTION_STANDARD_DIR) --overwrite

validate-detection-standard:
	$(PYTHON) -m paic detection validate --detection-dir $(DETECTION_STANDARD_DIR) --analytics-dir $(ANALYTICS_STANDARD_DIR)

summarize-detection-standard:
	$(PYTHON) -m paic detection summary --detection-dir $(DETECTION_STANDARD_DIR)

impact-smoke:
	$(PYTHON) -m paic simulate --config configs/simulation/impact-smoke.yaml --output-dir $(IMPACT_SOURCE_SMOKE_DIR) --overwrite
	$(PYTHON) -m paic impact build --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR) --config configs/impact/smoke.yaml --output-dir $(IMPACT_SMOKE_DIR) --overwrite

validate-impact-smoke:
	$(PYTHON) -m paic impact validate --impact-dir $(IMPACT_SMOKE_DIR) --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR)

summarize-impact-smoke:
	$(PYTHON) -m paic impact summary --impact-dir $(IMPACT_SMOKE_DIR)

impact-standard:
	$(PYTHON) -m paic simulate --config configs/simulation/impact-standard.yaml --output-dir $(IMPACT_SOURCE_STANDARD_DIR) --overwrite
	$(PYTHON) -m paic impact build --dataset-dir $(IMPACT_SOURCE_STANDARD_DIR) --config configs/impact/standard.yaml --output-dir $(IMPACT_STANDARD_DIR) --overwrite

validate-impact-standard:
	$(PYTHON) -m paic impact validate --impact-dir $(IMPACT_STANDARD_DIR) --dataset-dir $(IMPACT_SOURCE_STANDARD_DIR)

summarize-impact-standard:
	$(PYTHON) -m paic impact summary --impact-dir $(IMPACT_STANDARD_DIR)

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

verify: check detection-smoke validate-detection-smoke impact-smoke validate-impact-smoke

clean:
	rm -rf .artifacts .coverage .mypy_cache .pytest_cache .ruff_cache build dist htmlcov schemas-generated
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
