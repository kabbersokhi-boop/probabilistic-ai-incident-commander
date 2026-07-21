.PHONY: install validate summary schemas schema-check simulate-smoke validate-smoke summarize-smoke simulate-standard validate-standard summarize-standard analytics-smoke validate-analytics-smoke summarize-analytics-smoke analytics-standard validate-analytics-standard summarize-analytics-standard detection-smoke validate-detection-smoke summarize-detection-smoke detection-standard validate-detection-standard summarize-detection-standard impact-smoke validate-impact-smoke summarize-impact-smoke impact-standard validate-impact-standard summarize-impact-standard evidence-smoke validate-evidence-smoke summarize-evidence-smoke evidence-standard validate-evidence-standard summarize-evidence-standard tools-list tools-smoke tools-audit investigation-smoke validate-investigation-smoke replay-investigation-smoke remediation-smoke validate-remediation-smoke recovery-source-smoke recovery-smoke validate-recovery-smoke evaluation-smoke evaluation-validate-smoke evaluation-replay-smoke evaluation-standard evaluation-compare-smoke evaluation-adversarial tui-smoke tui-snapshot tui-validate phase11-authoritative-soak test coverage lint format format-check typecheck check verify clean

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
EVIDENCE_SMOKE_DIR ?= .artifacts/evidence-smoke
EVIDENCE_STANDARD_DIR ?= .artifacts/evidence-standard
TOOL_AUDIT_DIR ?= .artifacts/tool-audit
INVESTIGATION_CONFIG ?= configs/investigation/smoke.yaml
INVESTIGATION_REQUEST ?= .artifacts/investigation-request.json
INVESTIGATION_SCRIPT ?= .artifacts/investigation-provider-script.json
INVESTIGATION_AUDIT_DIR ?= .artifacts/investigation-tool-audit
INVESTIGATION_SMOKE_DIR ?= .artifacts/investigation-smoke
REMEDIATION_CONFIG ?= configs/remediation/smoke.yaml
REMEDIATION_STATE_DIR ?= .artifacts/remediation-state
REMEDIATION_PLAN_DIR ?= .artifacts/remediation-plan
REMEDIATION_APPROVAL_DIR ?= .artifacts/remediation-approval
REMEDIATION_EXECUTION_DIR ?= .artifacts/remediation-execution
REMEDIATION_STATE_AFTER_DIR ?= .artifacts/remediation-state-after
REMEDIATION_STATE_STORE ?= .artifacts/remediation-state-store
RECOVERY_INPUT_DIR ?= .artifacts/recovery-inputs
RECOVERY_SOURCE_DIR ?= .artifacts/recovery-source
RECOVERY_ANALYTICS_DIR ?= .artifacts/recovery-analytics
RECOVERY_HEALTHY_DIR ?= .artifacts/recovery-healthy
RECOVERY_REGRESSION_DIR ?= .artifacts/recovery-regression
RECOVERY_STATE_STORE ?= .artifacts/recovery-state
RECOVERY_CONFIG ?= configs/recovery/smoke.yaml
SCHEMA_TMP ?= schemas-generated
EVALUATION_SMOKE_DIR ?= .artifacts/evaluation-smoke
EVALUATION_STANDARD_DIR ?= .artifacts/evaluation-standard
EVALUATION_ABLATION_DIR ?= .artifacts/evaluation-standard-no-lineage
EVALUATION_COMPARISON_DIR ?= .artifacts/evaluation-comparison
TUI_WORKSPACE ?= configs/tui/smoke.yaml
PHASE11_SOAK_DIR ?= .artifacts/phase11-authoritative-soak
PHASE11_SOAK_ITERATIONS ?= 25
PHASE11_SOAK_DURATION_SECONDS ?= inf

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

evidence-smoke: impact-smoke
	$(PYTHON) -m paic evidence build --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR) --impact-dir $(IMPACT_SMOKE_DIR) --config configs/evidence/smoke.yaml --output-dir $(EVIDENCE_SMOKE_DIR) --overwrite

validate-evidence-smoke:
	$(PYTHON) -m paic evidence validate --evidence-dir $(EVIDENCE_SMOKE_DIR) --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR) --impact-dir $(IMPACT_SMOKE_DIR)

summarize-evidence-smoke:
	$(PYTHON) -m paic evidence summary --evidence-dir $(EVIDENCE_SMOKE_DIR)

evidence-standard: impact-standard
	$(PYTHON) -m paic evidence build --dataset-dir $(IMPACT_SOURCE_STANDARD_DIR) --impact-dir $(IMPACT_STANDARD_DIR) --config configs/evidence/standard.yaml --output-dir $(EVIDENCE_STANDARD_DIR) --overwrite

validate-evidence-standard:
	$(PYTHON) -m paic evidence validate --evidence-dir $(EVIDENCE_STANDARD_DIR) --dataset-dir $(IMPACT_SOURCE_STANDARD_DIR) --impact-dir $(IMPACT_STANDARD_DIR)

summarize-evidence-standard:
	$(PYTHON) -m paic evidence summary --evidence-dir $(EVIDENCE_STANDARD_DIR)

tools-list:
	$(PYTHON) -m paic tools list

tools-smoke: detection-smoke
	@mkdir -p .artifacts
	@echo '{"tool":"artifacts.summary","incident_id":"smoke","role":"investigator","dataset_dir":"$(SMOKE_DIR)","analytics_dir":"$(ANALYTICS_SMOKE_DIR)","detection_dir":"$(DETECTION_SMOKE_DIR)","audit_dir":"$(TOOL_AUDIT_DIR)","arguments":{}}' > .artifacts/tool-request.json
	$(PYTHON) -m paic tools invoke --request .artifacts/tool-request.json

tools-audit:
	$(PYTHON) -m paic tools audit validate --audit-dir $(TOOL_AUDIT_DIR)

investigation-smoke: evidence-smoke
	$(PYTHON) examples/build_scripted_investigation_inputs.py --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR) --evidence-dir $(EVIDENCE_SMOKE_DIR) --config $(INVESTIGATION_CONFIG) --impact-dir $(IMPACT_SMOKE_DIR) --request $(INVESTIGATION_REQUEST) --script $(INVESTIGATION_SCRIPT) --audit-dir $(INVESTIGATION_AUDIT_DIR)
	$(PYTHON) -m paic investigate run --request $(INVESTIGATION_REQUEST) --config $(INVESTIGATION_CONFIG) --provider-script $(INVESTIGATION_SCRIPT) --output-dir $(INVESTIGATION_SMOKE_DIR) --overwrite

validate-investigation-smoke:
	$(PYTHON) -m paic investigate validate --investigation-dir $(INVESTIGATION_SMOKE_DIR) --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR) --impact-dir $(IMPACT_SMOKE_DIR) --evidence-dir $(EVIDENCE_SMOKE_DIR)
	$(PYTHON) -m paic tools audit validate --audit-dir $(INVESTIGATION_AUDIT_DIR)

replay-investigation-smoke:
	$(PYTHON) -m paic investigate replay --investigation-dir $(INVESTIGATION_SMOKE_DIR) --config $(INVESTIGATION_CONFIG) --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR) --impact-dir $(IMPACT_SMOKE_DIR) --evidence-dir $(EVIDENCE_SMOKE_DIR)

remediation-smoke: investigation-smoke
	rm -rf $(REMEDIATION_STATE_DIR) $(REMEDIATION_PLAN_DIR) $(REMEDIATION_APPROVAL_DIR) $(REMEDIATION_EXECUTION_DIR) $(REMEDIATION_STATE_AFTER_DIR) $(REMEDIATION_STATE_STORE) .artifacts/remediation-approval.token
	$(PYTHON) examples/build_remediation_smoke_inputs.py --investigation-dir $(INVESTIGATION_SMOKE_DIR) --state-input .artifacts/remediation-state-input.json --proposal .artifacts/remediation-proposal.json
	$(PYTHON) -m paic remediate state build --input .artifacts/remediation-state-input.json --output-dir $(REMEDIATION_STATE_DIR) --overwrite
	$(PYTHON) -m paic remediate plan build --investigation-dir $(INVESTIGATION_SMOKE_DIR) --investigation-config $(INVESTIGATION_CONFIG) --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR) --impact-dir $(IMPACT_SMOKE_DIR) --evidence-dir $(EVIDENCE_SMOKE_DIR) --state-dir $(REMEDIATION_STATE_DIR) --proposal .artifacts/remediation-proposal.json --config $(REMEDIATION_CONFIG) --output-dir $(REMEDIATION_PLAN_DIR) --overwrite
	$(PYTHON) examples/build_remediation_smoke_inputs.py --plan-dir $(REMEDIATION_PLAN_DIR) --decision-one .artifacts/remediation-decision-one.json --decision-two .artifacts/remediation-decision-two.json --execution-request .artifacts/remediation-execution-request.json
	@secret="$$($(PYTHON) -c 'import secrets; print(secrets.token_urlsafe(48))')"; primary="$$($(PYTHON) -c 'import secrets; print(secrets.token_urlsafe(48))')"; manager="$$($(PYTHON) -c 'import secrets; print(secrets.token_urlsafe(48))')"; export PAIC_APPROVAL_SECRET="$$secret" PAIC_APPROVER_ONCALL_PRIMARY_KEY="$$primary" PAIC_APPROVER_CHANGE_MANAGER_KEY="$$manager"; $(PYTHON) -m paic remediate approval record --plan-dir $(REMEDIATION_PLAN_DIR) --approval-dir $(REMEDIATION_APPROVAL_DIR) --decision .artifacts/remediation-decision-one.json; $(PYTHON) -m paic remediate approval record --plan-dir $(REMEDIATION_PLAN_DIR) --approval-dir $(REMEDIATION_APPROVAL_DIR) --decision .artifacts/remediation-decision-two.json; $(PYTHON) -m paic remediate token issue --plan-dir $(REMEDIATION_PLAN_DIR) --approval-dir $(REMEDIATION_APPROVAL_DIR) --at 2026-07-18T00:07:00+00:00 --output .artifacts/remediation-approval.token; $(PYTHON) -m paic remediate execute --plan-dir $(REMEDIATION_PLAN_DIR) --state-dir $(REMEDIATION_STATE_DIR) --state-store $(REMEDIATION_STATE_STORE) --approval-dir $(REMEDIATION_APPROVAL_DIR) --token-file .artifacts/remediation-approval.token --request .artifacts/remediation-execution-request.json --output-state-dir $(REMEDIATION_STATE_AFTER_DIR) --output-dir $(REMEDIATION_EXECUTION_DIR) --overwrite
	rm -f .artifacts/remediation-approval.token

validate-remediation-smoke:
	$(PYTHON) -m paic remediate state validate --state-dir $(REMEDIATION_STATE_DIR)
	$(PYTHON) -m paic remediate plan validate --plan-dir $(REMEDIATION_PLAN_DIR) --investigation-dir $(INVESTIGATION_SMOKE_DIR) --investigation-config $(INVESTIGATION_CONFIG) --dataset-dir $(IMPACT_SOURCE_SMOKE_DIR) --impact-dir $(IMPACT_SMOKE_DIR) --evidence-dir $(EVIDENCE_SMOKE_DIR) --state-dir $(REMEDIATION_STATE_DIR)
	$(PYTHON) -m paic remediate state validate --state-dir $(REMEDIATION_STATE_AFTER_DIR)
	$(PYTHON) -m paic remediate execution validate --execution-dir $(REMEDIATION_EXECUTION_DIR) --plan-dir $(REMEDIATION_PLAN_DIR) --before-state-dir $(REMEDIATION_STATE_DIR) --after-state-dir $(REMEDIATION_STATE_AFTER_DIR)

recovery-source-smoke:
	$(PYTHON) -m paic simulate --config configs/simulation/recovery.yaml --output-dir $(RECOVERY_SOURCE_DIR) --overwrite
	$(PYTHON) -m paic dataset validate --dataset-dir $(RECOVERY_SOURCE_DIR)
	$(PYTHON) -m paic analytics build --dataset-dir $(RECOVERY_SOURCE_DIR) --config configs/analytics/recovery.yaml --output-dir $(RECOVERY_ANALYTICS_DIR) --overwrite
	$(PYTHON) -m paic analytics validate --analytics-dir $(RECOVERY_ANALYTICS_DIR) --dataset-dir $(RECOVERY_SOURCE_DIR)

recovery-smoke: remediation-smoke recovery-source-smoke
	@rm -rf $(RECOVERY_INPUT_DIR) $(RECOVERY_HEALTHY_DIR) $(RECOVERY_REGRESSION_DIR) $(RECOVERY_STATE_STORE) .artifacts/recovery-state-severe; \
	for name in insufficient recovering recovered regression-one regression-two severe; do \
		$(PYTHON) -m paic recovery observations build --scenario configs/recovery/observation-$$name.json --analytics-dir $(RECOVERY_ANALYTICS_DIR) --execution-dir $(REMEDIATION_EXECUTION_DIR) --output-dir .artifacts/obs-$$name --overwrite || exit $$?; \
		$(PYTHON) -m paic recovery observations validate --observations-dir .artifacts/obs-$$name --analytics-dir $(RECOVERY_ANALYTICS_DIR) --execution-dir $(REMEDIATION_EXECUTION_DIR) || exit $$?; \
		$(PYTHON) -m paic recovery evaluate --config $(RECOVERY_CONFIG) --observations-dir .artifacts/obs-$$name --analytics-dir $(RECOVERY_ANALYTICS_DIR) --execution-dir $(REMEDIATION_EXECUTION_DIR) --output-dir .artifacts/report-$$name --overwrite; code=$$?; \
		case $$name in insufficient|recovering|regression-one|regression-two|severe) test $$code -eq 1;; recovered) test $$code -eq 0;; esac || exit $$?; \
	 done; \
	for name in insufficient recovering recovered; do \
		$(PYTHON) -m paic recovery state apply --recovery-dir .artifacts/report-$$name --state-store $(RECOVERY_STATE_STORE) || exit $$?; \
		$(PYTHON) -m paic recovery state validate --state-store $(RECOVERY_STATE_STORE) || exit $$?; \
	 done; \
	set +e; $(PYTHON) -m paic recovery state apply --recovery-dir .artifacts/report-recovered --state-store $(RECOVERY_STATE_STORE); duplicate=$$?; \
	$(PYTHON) -m paic recovery state apply --recovery-dir .artifacts/report-insufficient --state-store $(RECOVERY_STATE_STORE); stale=$$?; set -e; \
	test $$duplicate -eq 2 && test $$stale -eq 2; \
	$(PYTHON) -m paic recovery state apply --recovery-dir .artifacts/report-regression-one --state-store $(RECOVERY_STATE_STORE); \
	$(PYTHON) -m paic recovery state validate --state-store $(RECOVERY_STATE_STORE); \
	set +e; $(PYTHON) -m paic recovery state apply --recovery-dir .artifacts/report-regression-two --state-store $(RECOVERY_STATE_STORE); reopened=$$?; set -e; test $$reopened -eq 1; \
	$(PYTHON) -m paic recovery state validate --state-store $(RECOVERY_STATE_STORE); \
	$(PYTHON) -m paic recovery state apply --recovery-dir .artifacts/report-recovered --state-store .artifacts/recovery-state-severe; \
	set +e; $(PYTHON) -m paic recovery state apply --recovery-dir .artifacts/report-severe --state-store .artifacts/recovery-state-severe; severe_reopened=$$?; set -e; test $$severe_reopened -eq 1; \
	$(PYTHON) -m paic recovery state validate --state-store .artifacts/recovery-state-severe

validate-recovery-smoke:
	$(PYTHON) -m paic recovery validate --recovery-dir .artifacts/report-recovered --observations-dir .artifacts/obs-recovered --analytics-dir $(RECOVERY_ANALYTICS_DIR) --execution-dir $(REMEDIATION_EXECUTION_DIR)
	$(PYTHON) -m paic recovery state validate --state-store $(RECOVERY_STATE_STORE)

evaluation-smoke:
	rm -rf $(EVALUATION_SMOKE_DIR)
	$(PYTHON) -m paic evaluation benchmark-validate --visible-dir configs/evaluation/smoke --answers-dir configs/evaluation/answers
	$(PYTHON) -m paic evaluation run --visible-dir configs/evaluation/smoke --answers-dir configs/evaluation/answers --predictions configs/evaluation/smoke/predictions.json --config configs/evaluation/smoke/evaluation.json --output-dir $(EVALUATION_SMOKE_DIR)

evaluation-validate-smoke:
	$(PYTHON) -m paic evaluation validate --run-dir $(EVALUATION_SMOKE_DIR)

evaluation-replay-smoke:
	$(PYTHON) -m paic evaluation replay --run-dir $(EVALUATION_SMOKE_DIR) --visible-dir configs/evaluation/smoke --answers-dir configs/evaluation/answers --predictions configs/evaluation/smoke/predictions.json --config configs/evaluation/smoke/evaluation.json

evaluation-standard:
	rm -rf $(EVALUATION_STANDARD_DIR) $(EVALUATION_ABLATION_DIR) $(EVALUATION_COMPARISON_DIR)
	$(PYTHON) -m paic evaluation benchmark-validate --visible-dir configs/evaluation/standard --answers-dir configs/evaluation/standard-hidden
	$(PYTHON) -m paic evaluation run --visible-dir configs/evaluation/standard --answers-dir configs/evaluation/standard-hidden --predictions configs/evaluation/standard/predictions.json --config configs/evaluation/standard/evaluation.json --output-dir $(EVALUATION_STANDARD_DIR)
	$(PYTHON) -m paic evaluation run --visible-dir configs/evaluation/standard --answers-dir configs/evaluation/standard-hidden --predictions configs/evaluation/standard/predictions-no-lineage.json --config configs/evaluation/standard/evaluation-no-lineage.json --output-dir $(EVALUATION_ABLATION_DIR)
	$(PYTHON) -m paic evaluation validate --run-dir $(EVALUATION_STANDARD_DIR)
	$(PYTHON) -m paic evaluation validate --run-dir $(EVALUATION_ABLATION_DIR)
	$(PYTHON) -m paic evaluation replay --run-dir $(EVALUATION_STANDARD_DIR) --visible-dir configs/evaluation/standard --answers-dir configs/evaluation/standard-hidden --predictions configs/evaluation/standard/predictions.json --config configs/evaluation/standard/evaluation.json
	$(PYTHON) -m paic evaluation replay --run-dir $(EVALUATION_ABLATION_DIR) --visible-dir configs/evaluation/standard --answers-dir configs/evaluation/standard-hidden --predictions configs/evaluation/standard/predictions-no-lineage.json --config configs/evaluation/standard/evaluation-no-lineage.json

evaluation-compare-smoke: evaluation-standard
	$(PYTHON) -m paic evaluation compare --left-dir $(EVALUATION_STANDARD_DIR) --right-dir $(EVALUATION_ABLATION_DIR) --visible-dir configs/evaluation/standard --answers-dir configs/evaluation/standard-hidden --left-predictions configs/evaluation/standard/predictions.json --left-config configs/evaluation/standard/evaluation.json --right-predictions configs/evaluation/standard/predictions-no-lineage.json --right-config configs/evaluation/standard/evaluation-no-lineage.json --output-dir $(EVALUATION_COMPARISON_DIR)
	$(PYTHON) -m paic evaluation compare-replay --comparison-dir $(EVALUATION_COMPARISON_DIR) --left-dir $(EVALUATION_STANDARD_DIR) --right-dir $(EVALUATION_ABLATION_DIR) --visible-dir configs/evaluation/standard --answers-dir configs/evaluation/standard-hidden --left-predictions configs/evaluation/standard/predictions.json --left-config configs/evaluation/standard/evaluation.json --right-predictions configs/evaluation/standard/predictions-no-lineage.json --right-config configs/evaluation/standard/evaluation-no-lineage.json

evaluation-adversarial:
	$(PYTHON) -m paic evaluation adversarial-suite --cases configs/evaluation/adversarial/cases.json
	env $(PYTEST_ENV) $(PYTHON) -m pytest -q tests/test_remediation_hardening.py -k "plan_semantic_tampering or token_verification_fails_closed or execution_rechecks_state_binding"
	env $(PYTEST_ENV) $(PYTHON) -m pytest -q tests/test_recovery_artifact.py -k "semantic_tamper_is_rejected_even_after_file_hash_refresh or wrong_execution_binding_is_rejected or authoritative_recovery_validation"

tui-smoke: detection-smoke remediation-smoke recovery-smoke evaluation-smoke
	$(PYTHON) -m paic tui validate --workspace $(TUI_WORKSPACE)

tui-snapshot:
	$(PYTHON) -m paic tui snapshot --workspace $(TUI_WORKSPACE) --format json

tui-validate:
	$(PYTHON) -m paic tui validate --workspace $(TUI_WORKSPACE)

phase11-authoritative-soak:
	$(PYTHON) scripts/phase11_authoritative_soak.py --workspace $(TUI_WORKSPACE) --output-dir $(PHASE11_SOAK_DIR) --iterations $(PHASE11_SOAK_ITERATIONS) --duration-seconds $(PHASE11_SOAK_DURATION_SECONDS)

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

verify: check detection-smoke validate-detection-smoke impact-smoke validate-impact-smoke evidence-smoke validate-evidence-smoke tools-smoke tools-audit investigation-smoke validate-investigation-smoke replay-investigation-smoke remediation-smoke validate-remediation-smoke

clean:
	rm -rf .artifacts .coverage .mypy_cache .pytest_cache .ruff_cache build dist htmlcov schemas-generated
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
