# Statistical Anomaly Detection

The detector converts versioned metric observations into auditable anomaly scores, change events, and benchmark results. It does not call a language model and it never scores a chart by visual impression.

## Inputs

The detector reads a validated analytical artifact containing:

- metric value,
- numerator and denominator when available,
- sample size,
- time grain and period boundaries,
- cohort dimensions,
- metric semantics and expected range,
- upstream quality status,
- source and configuration hashes.

A strict YAML configuration selects the monitored metrics, time grains, cohorts, history windows, policy thresholds, and optional evaluator-only benchmark perturbations.

## Baselines without lookahead

For each series, observations are sorted by time. The current point is excluded from its own history.

The baseline uses:

1. a seasonal history when enough comparable periods exist, such as the same hour of day or weekday;
2. otherwise, a bounded rolling history;
3. a median location and median-absolute-deviation scale;
4. IQR and standard-deviation fallbacks when the MAD is degenerate;
5. an explicit absolute and relative scale floor.

Every scored row records the baseline method, number of history points, seasonal history count, last period used, expected value, interval, residual, relative change, and robust z-score.

## Distribution-aware predictive tests

A single Gaussian test is not appropriate for every business metric.

### Proportions

Conversion and approval metrics use an empirical-Bayes beta-binomial predictive distribution. Historical numerators and denominators estimate both the expected proportion and between-period variability. Prior concentration is capped to avoid treating small cohorts as nearly certain after a short run of perfect observations.

### Counts

Order and traffic counts use a Poisson predictive model when historical variance is close to the mean. Overdispersed histories use a method-of-moments negative-binomial model.

### Positive skewed values

Currency and duration metrics are scored on a `log1p` scale with a robust Student-t predictive test. This reduces sensitivity to the long right tails typical of order value and latency.

### Other continuous metrics

Remaining continuous metrics use a robust Student-t score derived from the historical median and robust scale.

## Multiple testing

The system can monitor many cohorts in the same period. Raw p-values are adjusted within each time-grain and period using the monotone Benjamini-Hochberg procedure. Alert policy uses the resulting q-value, not an uncorrected p-value.

## Change and sequential evidence

The detector combines four independent evidence channels:

- robust deviation from the expected value,
- FDR-adjusted predictive significance,
- two-sided CUSUM accumulation,
- two-sided sequential likelihood accumulation.

A point becomes an alert only when it is eligible, statistically significant after FDR control, exceeds its configured effect-size requirement, and receives the required number of supporting detector signals.

Eligibility and thresholds can be overridden per metric. This is important because an hourly conversion proportion, a daily pipeline retention rate, and gross order value have different variance and decision costs.

Each detector observation publishes the four support flags, explicit history, sample-size, and effect-size gate results, and an ordered JSON `alert_reason_codes` array. These fields are derived from the existing policy only; they do not add a voter or alter an alert decision.

## Event formation

Contiguous anomalous points for the same series are grouped into an event. Each event records:

- start and end,
- first detection and peak,
- evidence and severity maxima,
- minimum q-value,
- direction and business-impact direction,
- linked evaluator scenario identifiers when applicable.

CUSUM threshold crossings are also published as separate change-point events with an estimated start time.

## Ground-truth benchmark

The standard configuration applies ten deterministic perturbations to a copy of selected metric observations. The underlying synthetic dataset and analytical artifact remain unchanged.

The benchmark spans:

- overall checkout conversion decline,
- regional checkout conversion decline,
- payment approval decline,
- payment decline spike,
- completed-order decline,
- gross order value spike,
- inventory accuracy decline,
- on-time delivery decline,
- pipeline row-retention drift,
- seller-feed rejection increase.

The current reference run reports:

| Measure | Result |
|---|---:|
| Scenarios | 10 |
| Scenario recall | 100% |
| Observation precision | 81.25% |
| False-positive rate | 0.24% |
| Mean detection delay | 1.2 periods |

Scenario recall is the primary event-level measure. Point recall is retained for transparency but is not optimized: a detector should raise one timely incident rather than repeat the same alert for every affected interval.

## Artifact contents

An exported detector artifact contains:

```text
detection-output/
├── _SUCCESS
├── detection.config.resolved.json
├── manifest.json
└── tables/
    ├── detector_observations.parquet
    ├── anomaly_events.parquet
    ├── change_point_events.parquet
    ├── benchmark_ground_truth.parquet
    ├── benchmark_results.parquet
    ├── benchmark_summary.parquet
    └── detection_quality_results.parquet
```

The manifest binds the output to the exact source analytics manifest and configuration. It records runtime dependency versions, schemas, row counts, timestamp bounds, file sizes, SHA-256 hashes, benchmark results, and quality-error counts.

## Commands

```bash
paic detection build \
  --analytics-dir data/generated/analytics-standard \
  --config configs/detection/standard.yaml \
  --output-dir data/generated/detection-standard

paic detection validate \
  --detection-dir data/generated/detection-standard \
  --analytics-dir data/generated/analytics-standard

paic detection summary \
  --detection-dir data/generated/detection-standard
```

## Boundaries

- Benchmark perturbations operate on metric observations; raw-event incident injection is a separate future capability.
- The detector identifies statistically unusual series. It does not claim a root cause.
- Correlated detector signals are treated as support channels, not as independent probabilities.
- Thresholds are benchmark policies, not universal production defaults.
- Seasonal models are deliberately interpretable. More complex forecasting methods should be added only when out-of-sample evaluation demonstrates a material benefit.
- Additional business-threshold voters and hierarchical cohort traversal remain future evaluated enhancements; neither is part of the current alert policy.
