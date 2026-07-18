# Customer Impact, Survival, and Revenue at Risk

The customer-impact layer translates an operational incident into measurable customer and financial consequences. It is deterministic and operates on the validated synthetic commerce dataset; no language model is involved.

## Questions answered

- Which customers interacted with the affected workflow?
- How do exposed and comparable unexposed customers differ?
- Did exposure increase the probability of churn?
- How quickly do customers return after the incident?
- Which segments carry the most future revenue risk?
- What immediate loss, support cost, future revenue, and contribution margin are at risk?

## Outcome definition

Churn is explicit and configurable: a customer is churned when no purchase occurs within the configured number of days after the incident ends. The standard benchmark uses a 60-day horizon, a 45-day pre-incident feature window, and a 180-day value horizon.

## Features

Customer-level features are calculated strictly before the incident:

- tenure and recency,
- order frequency, spend, and average order value,
- failed checkout and payment-decline history,
- late delivery, return, and refund history,
- discount dependence and category diversity,
- baseline forward value.

Exposure is derived from actual customer interactions during the configured incident window and can be restricted by region and device.

## Survival analysis

The artifact includes:

- Kaplan–Meier curves for exposed, control, and overall cohorts,
- Greenwood confidence intervals,
- a regularized Cox proportional-hazards model,
- hazard ratios and coefficient uncertainty,
- concordance index and horizon-specific Brier score.

The event is the next purchase. Survival therefore represents the probability that a customer has not yet returned.

## Causal analysis

The layer reports several deliberately separate estimates:

- unadjusted churn difference,
- one-to-one propensity-score matched ATT,
- stabilized inverse-probability-weighted ATE,
- difference-in-differences for daily purchase rates,
- a shifted-window placebo estimate.

Observed-covariate balance is measured using standardized mean differences before and after weighting. Propensity scores are clipped to limit extreme weights. Bootstrap intervals quantify sampling uncertainty.

These estimates do not prove causality in real production data. They are evaluated against a deterministic synthetic benchmark where both potential outcomes are known for exposed customers. The benchmark perturbation changes only the analysis outcome copy; source commerce tables remain unchanged.

## Financial model

The report separates:

1. immediate failed-interaction revenue,
2. support and recovery costs,
3. incremental churn customers,
4. future revenue at risk,
5. contribution margin at risk,
6. total financial impact with a bootstrap interval.

All assumptions are stored in the resolved configuration and every exported table is bound to the source dataset and protected by cryptographic hashes.

## Commands

```bash
paic simulate \
  --config configs/simulation/impact-standard.yaml \
  --output-dir data/generated/impact-source

paic impact build \
  --dataset-dir data/generated/impact-source \
  --config configs/impact/standard.yaml \
  --output-dir data/generated/customer-impact

paic impact validate \
  --impact-dir data/generated/customer-impact \
  --dataset-dir data/generated/impact-source

paic impact summary \
  --impact-dir data/generated/customer-impact
```

## Limitations

- Exposure is defined by observed interaction rules, not randomized assignment.
- Propensity methods adjust only for measured pre-incident features.
- The synthetic benchmark is evaluator-only and should not be presented as a real commercial result.
- Cox proportional-hazards assumptions require separate diagnostics before production use.
- Financial estimates are scenario estimates, not accounting forecasts.
