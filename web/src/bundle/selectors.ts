import { asRows, isRecord, type Bundle, type RecordValue } from "./schema";

export const file = (b: Bundle, suffix: string): RecordValue | undefined => {
    const value = b.files.find((f) => f.path.endsWith(suffix))?.content;
    return isRecord(value) ? value : undefined;
};
export const rows = (b: Bundle, name: string) => asRows(b.presentation?.[name]);
export const report = (b: Bundle) => file(b, "investigation-smoke/report.json");
export const hypotheses = (b: Bundle) =>
    asRows(report(b)?.hypotheses).sort(
        (a, z) =>
            Number(z.posterior_probability ?? -Infinity) -
            Number(a.posterior_probability ?? -Infinity),
    );
export const lead = (b: Bundle) => hypotheses(b)[0];
export const impact = (b: Bundle) => rows(b, "impact")[0];
export const timeline = (b: Bundle) =>
    rows(b, "timeline").sort((a, z) =>
        String(a.occurred_at).localeCompare(String(z.occurred_at)),
    );
export const recovery = (b: Bundle) => file(b, "report-recovered/report.json");
export const plan = (b: Bundle) => file(b, "remediation-plan/plan.json");
export const receipt = (b: Bundle) =>
    file(b, "remediation-execution/receipt.json");
export const proposal = (b: Bundle) =>
    file(b, "remediation-plan/proposal.json");
export const beforeState = (b: Bundle) =>
    file(b, "remediation-state/state.json");
export const afterState = (b: Bundle) =>
    file(b, "remediation-state-after/state.json");
export const impactConfig = (b: Bundle) =>
    file(b, "impact-smoke/impact.config.resolved.json");
export const investigationConfig = (b: Bundle) =>
    file(b, "investigation-smoke/investigation.config.resolved.json");
export const recoveryConfig = (b: Bundle) =>
    file(b, "report-recovered/recovery.config.resolved.json");
export const recoveryObservations = (b: Bundle) =>
    file(b, "obs-recovered/observation-set.json");
export const detectionConfig = (b: Bundle) =>
    file(b, "detection-showcase/detection.config.resolved.json");
export const simulationConfig = (b: Bundle) =>
    file(b, "impact-source-smoke/config.resolved.json");
export const evaluationMetrics = (b: Bundle) =>
    file(b, "evaluation-smoke/aggregate-metrics.json");
export const evaluationCases = (b: Bundle) => {
    const value = b.files.find((entry) =>
        entry.path.endsWith(
            "evaluation-smoke/benchmark.visible.effective.json",
        ),
    )?.content;
    return asRows(value);
};

const stringList = (value: unknown) =>
    Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string")
        : [];

export const evidenceIds = (event: RecordValue) => {
    if (typeof event.evidence_record_ids_json !== "string") return [];
    try {
        return stringList(JSON.parse(event.evidence_record_ids_json));
    } catch {
        return [];
    }
};

export const evidenceEvent = (b: Bundle, evidenceId: string) =>
    timeline(b).find((event) => evidenceIds(event).includes(evidenceId));

export type DecisionEvidence = {
    id: string;
    direction: "support" | "contradict";
    explanation?: string;
    likelihoodRatio?: number;
    hypothesisId: string;
    event?: RecordValue;
};

export const decisionEvidence = (b: Bundle): DecisionEvidence[] => {
    const result: DecisionEvidence[] = [];
    const proposalValue = report(b)?.proposal;
    if (!isRecord(proposalValue)) return result;
    for (const hypothesis of asRows(proposalValue.hypotheses)) {
        for (const assessment of asRows(hypothesis.evidence)) {
            if (
                typeof assessment.evidence_record_id !== "string" ||
                (assessment.direction !== "support" &&
                    assessment.direction !== "contradict")
            )
                continue;
            result.push({
                id: assessment.evidence_record_id,
                direction: assessment.direction,
                explanation:
                    typeof assessment.explanation === "string"
                        ? assessment.explanation
                        : undefined,
                likelihoodRatio:
                    typeof assessment.likelihood_ratio === "number"
                        ? assessment.likelihood_ratio
                        : undefined,
                hypothesisId: String(hypothesis.hypothesis_id ?? ""),
                event: evidenceEvent(b, assessment.evidence_record_id),
            });
        }
    }
    return result;
};

export const keyTimeline = (b: Bundle) =>
    timeline(b).filter((event) => event.event_type !== "service_health");

export const leadingAnomaly = (b: Bundle) =>
    rows(b, "detectors").find(
        (item) =>
            item.is_anomaly === true && typeof item.scenario_id === "string",
    ) ?? rows(b, "detectors").find((item) => item.is_anomaly === true);

export const detectorSeries = (b: Bundle) => {
    const leading = leadingAnomaly(b);
    if (!leading) return [];
    const dimensions = [
        "region",
        "device",
        "channel",
        "customer_type",
        "app_version",
        "issuer",
        "payment_method",
        "service",
    ];
    return rows(b, "detectors").filter(
        (item) =>
            item.metric_name === leading.metric_name &&
            item.time_grain === leading.time_grain &&
            item.cohort_name === leading.cohort_name &&
            dimensions.every((name) => item[name] === leading[name]),
    );
};

export const resource = (state: RecordValue | undefined, resourceId: string) =>
    asRows(state?.resources).find((item) => item.resource_id === resourceId);
