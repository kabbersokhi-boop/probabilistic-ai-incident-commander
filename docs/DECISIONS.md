# Architecture Decisions

## ADR-0001: Statistical detection is outside the LLM

**Decision:** Deterministic statistical or machine-learning code detects anomalies.  
**Reason:** Detection must be reproducible, measurable, and benchmarkable.  
**Status:** Accepted.

## ADR-0002: One principal agent before multi-agent expansion

**Decision:** Begin with one Incident Commander and add a critic only if evaluation proves a benefit.  
**Reason:** Fewer coordination failures, lower cost, easier tracing, and clearer ablations.  
**Status:** Accepted.

## ADR-0003: Root-cause probabilities are calculated externally

**Decision:** The LLM may propose evidence and hypotheses but cannot manufacture numeric confidence.  
**Reason:** Probabilities need explicit assumptions and calibration.  
**Status:** Accepted.

## ADR-0004: Churn is part of incident impact

**Decision:** Churn and survival analysis estimate the downstream effect of incident exposure.  
**Reason:** This keeps customer modelling connected to the operational product rather than becoming a separate notebook.  
**Status:** Accepted.

## ADR-0005: Repository contracts are authoritative

**Decision:** Machine-readable specs and versioned docs are the source of truth across ChatGPT, Codex, and GitHub.  
**Reason:** Tool sessions do not share perfect memory.  
**Status:** Accepted.

## ADR-0006: Develop phase by phase

**Decision:** Each phase receives its own branch, pull request, tests, and review; releases group completed phases.  
**Reason:** Smaller integration risk and clearer public engineering history.  
**Status:** Accepted.
