# Codex Integration Prompt - Phase 0

You are integrating **Phase 0: Product Thesis and Evaluation Contract** for the **Probabilistic AI Incident Commander**.

The supplied archive contains a single repository root named:

```text
probabilistic-ai-incident-commander/
```

Your job is to integrate, verify, commit, push, and open a draft pull request. Do not begin Phase 1.

## 1. Repository setup

1. Inspect the current working directory, Git status, remotes, authenticated GitHub account, and available Python versions.
2. Use the existing target repository when one is already open.
3. When no target repository exists, create a new **public** GitHub repository named `probabilistic-ai-incident-commander` under the authenticated user, initialize `main`, and use it as the remote. If repository creation is unavailable, stop and report the exact limitation rather than pushing elsewhere.
4. Create branch:

```text
feat/00-phase-0-contracts
```

5. Extract or copy the contents of the supplied repository root into the Git repository root. Do not add an extra nested directory.
6. Preserve unrelated existing files. Resolve collisions by comparing intent; do not silently overwrite established project behaviour.

## 2. Authoritative project files

Read these before modifying anything:

```text
README.md
docs/PROJECT_VISION.md
docs/ARCHITECTURE.md
docs/DEVELOPMENT_ROADMAP.md
docs/EVALUATION_CONTRACT.md
docs/SECURITY_MODEL.md
docs/DECISIONS.md
docs/CURRENT_STATUS.md
docs/HANDOFF.md
docs/PHASE_0_ACCEPTANCE.md
specs/project.yaml
specs/evaluation.yaml
specs/safety.yaml
specs/incidents/*.yaml
```

The machine-readable contracts are authoritative.

## 3. Scope

Phase 0 must contain:

- strict project, evaluation, safety, and incident contracts,
- five deterministic seed incidents,
- hidden ground truth and decoy changes,
- competing falsifiable hypotheses,
- evidence expectations,
- remediation and recovery contracts,
- Pydantic models,
- a loader and cross-contract validator,
- CLI validation, summary, and JSON Schema export,
- tests, CI, and project documentation.

Do not add the simulator, databases, LLM calls, agent framework, frontend, Docker services, or Phase 1 implementation.

## 4. Approved repairs

You may repair:

- packaging or import problems,
- Python 3.11 or 3.12 compatibility,
- Ruff formatting or lint findings,
- strict mypy findings,
- failing tests or coverage configuration,
- generated-schema drift,
- GitHub Actions syntax,
- documentation inconsistencies caused by verified results.

For every substantive defect, add or improve a regression test.

## 5. Changes that require a hard stop

Do not silently change:

- the approved incident workflow,
- incident hidden ground truth,
- required evaluation metrics or security hard gates,
- SQL read-only and default-deny boundaries,
- risk-level 2 approval requirements,
- risk-level 3 blocking,
- the decision to use deterministic anomaly detection,
- the decision to calculate probabilities outside the LLM,
- the single-principal-agent-first architecture,
- benchmark definitions merely to make tests pass.

When one of these appears necessary, stop and report the conflict with file and line references.

Never remove, skip, weaken, or mark tests as expected failures merely to obtain a green build.

## 6. Verification commands

Prefer Python 3.11. Python 3.12 is also supported.

Run from a clean environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m compileall -q src tests
python -m paic validate --spec-dir specs
python -m paic validate --spec-dir specs --format json
python -m paic summary --spec-dir specs
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
python -m pytest --cov=paic --cov-report=term-missing
rm -rf schemas-generated
python -m paic export-schemas --output-dir schemas-generated
diff -ru schemas schemas-generated
rm -rf schemas-generated
python -m build
```

Then install the built wheel in a second clean virtual environment and verify the installed command:

```bash
python -m venv .wheel-venv
source .wheel-venv/bin/activate
python -m pip install dist/*.whl
paic-contract validate --spec-dir specs
paic-contract summary --spec-dir specs
```

Remove temporary virtual environments and build artifacts only when they are ignored; do not commit them.

No Docker check is required in Phase 0.

## 7. Expected baseline

The supplied build was verified before handoff with:

- 5 valid incident contracts,
- 24 evaluation metrics,
- 4 generated JSON Schemas,
- 25 passing tests,
- 98 percent or greater total coverage,
- Ruff format and lint passing,
- strict mypy passing,
- generated-schema diff clean,
- editable installation and installed CLI working.

Investigate any regression from this baseline.

## 8. Documentation updates

After verification:

1. Update `docs/CURRENT_STATUS.md` with the exact Python version and exact results obtained in Codex.
2. Update `docs/HANDOFF.md` only when an integration detail changed.
3. Check completed items in `docs/PHASE_0_ACCEPTANCE.md` only when actually verified.
4. Record any architectural deviation in `docs/DECISIONS.md`; ordinary formatting or compatibility fixes do not need an ADR.

## 9. Git and GitHub

1. Review `git diff` and ensure no secret, virtual environment, cache, build output, or generated temporary directory is staged.
2. Use meaningful commit messages. A suitable initial commit is:

```text
feat: establish phase 0 executable project contracts
```

3. Push `feat/00-phase-0-contracts`.
4. Open a **draft pull request** against `main` titled:

```text
Phase 0: executable product and evaluation contracts
```

5. The pull request description must include:

- summary,
- repository setup performed,
- files or areas changed,
- exact commands run,
- exact test and coverage results,
- Python version,
- schema-diff result,
- package-build and clean-wheel-install result,
- deviations from supplied files,
- known limitations,
- remaining risks,
- confirmation that Phase 1 was not started.

6. After pushing, inspect GitHub Actions. Fix CI-only integration defects on the same branch, rerun checks, and update the pull request.
7. Leave the pull request in draft state for independent review.

## 10. Final response

Return:

- repository URL,
- branch name,
- commit SHA or SHAs,
- draft pull request URL and number,
- GitHub Actions status,
- exact local verification results,
- every file materially changed from the supplied package,
- every deviation and why it was necessary,
- remaining limitations or unresolved failures.

Do not claim success for a command that was not run.
