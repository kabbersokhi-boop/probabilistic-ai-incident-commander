import type { ReactNode } from "react";
import type { Bundle } from "../bundle/schema";
import { asRows, isRecord, unavailable } from "../bundle/schema";
import {
    compactHash,
    date,
    currency,
    humanize,
    integer,
    number,
    percent,
    region,
    score,
    time,
} from "../bundle/formatters";
import {
    afterState,
    beforeState,
    decisionEvidence,
    detectionConfig,
    detectorSeries,
    evaluationCases,
    evaluationMetrics,
    hypotheses,
    impact,
    impactConfig,
    investigationConfig,
    keyTimeline,
    lead,
    leadingAnomaly,
    plan,
    receipt,
    recovery,
    recoveryConfig,
    recoveryObservations,
    report,
    resource,
    rows,
    simulationConfig,
    timeline,
} from "../bundle/selectors";
import { DetectionChart } from "../components/DetectionChart";
import { Badge, Card, Empty, Metric, SourceRef, Table } from "../components/ui";

const Page = ({
    section,
    title,
    summary,
    aside,
    children,
}: {
    section: string;
    title: string;
    summary: string;
    aside?: ReactNode;
    children: ReactNode;
}) => (
    <>
        <header className="pageHead">
            <div>
                <p className="eyebrow">Incident record / {section}</p>
                <h1>{title}</h1>
                <p>{summary}</p>
            </div>
            {aside ? <div className="pageAside">{aside}</div> : null}
        </header>
        <div className="pageGrid">{children}</div>
    </>
);

const value = (input: unknown) =>
    typeof input === "string" || typeof input === "number"
        ? String(input)
        : unavailable;
const yesNo = (input: unknown) =>
    typeof input === "boolean" ? (input ? "Yes" : "No") : unavailable;
const nested = (input: unknown) => (isRecord(input) ? input : undefined);
const stringItems = (input: unknown) =>
    Array.isArray(input)
        ? input.filter((item): item is string => typeof item === "string")
        : [];

function incidentFacts(b: Bundle) {
    const config = impactConfig(b);
    const incident = nested(config?.incident);
    return {
        id: value(incident?.incident_id ?? report(b)?.incident_id),
        family: humanize(incident?.family),
        region: region(incident?.region),
        start: incident?.started_at,
        end: incident?.ended_at,
    };
}

export function Overview({ b }: { b: Bundle }) {
    const facts = incidentFacts(b);
    const leading = lead(b);
    const measured = impact(b);
    const remediation = plan(b);
    const execution = receipt(b);
    const recoveryReport = recovery(b);
    const evidence = decisionEvidence(b);
    const supportingChange = evidence.find(
        (item) => item.direction === "support" && item.event,
    );
    const alternativeChange = evidence.find(
        (item) =>
            item.direction === "contradict" &&
            String(item.event?.title).toLowerCase().includes("payment"),
    );
    const policy = nested(remediation?.policy_decision);
    const anomaly = leadingAnomaly(b);
    return (
        <Page
            section="Overview"
            title="A checkout defect, investigated end to end"
            summary="PAIC is an AI-assisted incident-response system. It detects abnormal behavior, scopes customer impact, tests competing causes, governs a simulated rollback, and verifies recovery—without giving the model operational authority."
            aside={
                <div className="statusCluster">
                    <Badge value="Synthetic scenario" tone="info" />
                    <Badge value={recoveryReport?.decision} />
                </div>
            }
        >
            <Card className="incidentHero">
                <div className="incidentLead">
                    <p className="overline">What happened</p>
                    <h2>Checkout performance deteriorated in {facts.region}</h2>
                    <p className="lede">
                        A controlled synthetic defect lowered checkout
                        conversion to {percent(anomaly?.observed_value)}, versus
                        a {percent(anomaly?.expected_value)} baseline. The
                        investigation then ranked {value(leading?.title)} above
                        the competing record:{" "}
                        {value(alternativeChange?.event?.title)}.
                    </p>
                    <div
                        className="heroLinks"
                        aria-label="Incident detail links"
                    >
                        <a href="#/investigation">
                            Inspect the competing hypotheses
                        </a>
                        <a href="#/evidence">Trace the cited evidence</a>
                    </div>
                </div>
                <div className="factRail">
                    <Metric
                        label="Who was affected"
                        value={integer(measured?.exposed_customers)}
                        detail={`synthetic customers exposed; ${integer(measured?.immediate_failed_interactions)} failed interactions counted`}
                        emphasis
                    />
                    <Metric
                        label="Incident window"
                        value={`${date(facts.start)} - ${date(facts.end)}`}
                        detail="UTC source timestamps"
                    />
                    <Metric
                        label="Leading explanation"
                        value={percent(leading?.posterior_probability)}
                        detail={`${value(leading?.title)}; deterministic posterior, not model confidence`}
                        emphasis
                    />
                    <Metric
                        label="What happened next"
                        value={humanize(recoveryReport?.decision)}
                        detail="governed simulated rollback, then independent recovery checks"
                    />
                </div>
            </Card>

            <Card
                kicker="Incident narrative"
                title="From signal to verified recovery"
                className="spanFull"
            >
                <ol className="commandSequence">
                    <li>
                        <span>01</span>
                        <div>
                            <b>The detector fired</b>
                            <p>
                                {facts.region} checkout conversion fell outside
                                its statistically expected range after adequate
                                history.
                            </p>
                        </div>
                    </li>
                    <li>
                        <span>02</span>
                        <div>
                            <b>Impact and facts were assembled</b>
                            <p>
                                Source-bound records scoped customers, funnel
                                failures, changes, service health, and lineage.
                            </p>
                        </div>
                    </li>
                    <li>
                        <span>03</span>
                        <div>
                            <b>The model investigated; software decided</b>
                            <p>
                                Scripted model responses proposed two causes.
                                Code validated citations, computed posteriors,
                                and enforced abstention gates.
                            </p>
                        </div>
                    </li>
                    <li>
                        <span>04</span>
                        <div>
                            <b>Policy governed the simulated rollback</b>
                            <p>
                                {humanize(policy?.outcome)} policy outcome,{" "}
                                {integer(remediation?.required_approvals)}{" "}
                                required approvals,{" "}
                                {humanize(remediation?.risk_level)} risk, then
                                historical execution status{" "}
                                {humanize(execution?.status)}.
                            </p>
                        </div>
                    </li>
                    <li>
                        <span>05</span>
                        <div>
                            <b>Later observations verified recovery</b>
                            <p>
                                The declared primary metric and guardrail both
                                met sustained statistical recovery criteria.
                            </p>
                        </div>
                    </li>
                </ol>
            </Card>

            <Card
                kicker="Product boundary"
                title="The model investigates. Deterministic software retains authority."
                className="spanFull productBoundary"
            >
                <div className="boundaryColumns">
                    <div>
                        <b>AI contribution</b>
                        <p>
                            Choose bounded read-only checks; propose competing
                            hypotheses, evidence weights, falsifiers, and next
                            steps.
                        </p>
                    </div>
                    <div>
                        <b>Software authority</b>
                        <p>
                            Validate evidence; calculate accepted probability;
                            enforce policy and approvals; execute only simulated
                            state changes; decide recovery.
                        </p>
                    </div>
                    <div>
                        <b>Public demo</b>
                        <p>
                            Reproducible synthetic data, committed scripted
                            provider responses, and a static read-only browser.
                        </p>
                    </div>
                </div>
            </Card>

            <Card
                kicker="Why this cause"
                title={value(supportingChange?.event?.title)}
                className="spanTwo evidencePreview"
            >
                <p>{value(supportingChange?.event?.detail)}</p>
                <div className="sourceLine">
                    <Badge value="Supports leading cause" tone="good" />
                    <SourceRef>{supportingChange?.id ?? unavailable}</SourceRef>
                </div>
            </Card>
            <Card
                kicker="Alternative tested"
                title={value(alternativeChange?.event?.title)}
                className="evidencePreview"
            >
                <p>{value(alternativeChange?.event?.detail)}</p>
                <div className="sourceLine">
                    <Badge value="Contradicts alternative" tone="warn" />
                    <SourceRef>
                        {alternativeChange?.id ?? unavailable}
                    </SourceRef>
                </div>
            </Card>

            <Card
                kicker="Trust boundary"
                title="AI assists; it does not command"
                className="spanTwo authoritySummary"
            >
                <p>
                    The model may choose bounded read-only tools, propose
                    hypotheses, likelihood ratios, falsifiers, and citations. It
                    cannot control the accepted evidence set, posterior ranking,
                    approvals, simulated execution, recovery truth, or benchmark
                    truth.
                </p>
                <a href="#/system-limitations">
                    Review the complete authority model
                </a>
            </Card>
            <Card
                kicker="Artifact state"
                title="Validation health, not business health"
            >
                <p>
                    <strong>
                        {
                            b.stages.filter(
                                (stage) => stage.status === "healthy",
                            ).length
                        }
                        /{b.stages.length}
                    </strong>{" "}
                    authoritative stages validate. “Healthy” means the exported
                    artifacts reconstructed successfully; it does not mean no
                    incident occurred.
                </p>
                <details>
                    <summary>Inspect validation stages</summary>
                    <ul className="plainList">
                        {b.stages.map((stage) => (
                            <li key={stage.key}>
                                <span>{stage.title}</span>
                                <Badge value={`Artifact ${stage.status}`} />
                            </li>
                        ))}
                    </ul>
                </details>
            </Card>
        </Page>
    );
}

export function Detection({ b }: { b: Bundle }) {
    const points = rows(b, "detectors");
    const anomalyEvents = rows(b, "anomaly_events");
    const changePoints = rows(b, "change_points");
    const series = detectorSeries(b);
    const leading = leadingAnomaly(b);
    const config = detectionConfig(b);
    const scenario = asRows(config?.benchmark_scenarios).find(
        (item) => item.scenario_id === leading?.scenario_id,
    );
    const anomalies = points.filter((point) => point.is_anomaly === true);
    const eligible = points.filter((point) => point.is_eligible === true);
    return (
        <Page
            section="Detection"
            title="How do we know something abnormal happened?"
            summary={`The detector had ${integer(leading?.baseline_points)} prior daily observations for this cohort, calculated an expected range, and flagged the controlled ${region(leading?.region)} checkout drop only after its deterministic evidence gates passed.`}
            aside={<Badge value="Anomaly detected" tone="warn" />}
        >
            <Card
                kicker="Detection verdict"
                title={`${humanize(leading?.display_name)} fell outside its expected range`}
                className="spanTwo detectionVerdict"
            >
                <p className="lede">
                    On {date(leading?.period_start)}, conversion in{" "}
                    {region(leading?.region)} was{" "}
                    {percent(leading?.observed_value)}. The expected center was{" "}
                    {percent(leading?.expected_value)}, with a{" "}
                    {percent(leading?.expected_lower)}–
                    {percent(leading?.expected_upper)} range. All{" "}
                    {integer(leading?.detector_support_count)} configured
                    statistical signals supported the alert.
                </p>
                <div className="metrics metricsFour">
                    <Metric
                        label="Observed"
                        value={percent(leading?.observed_value)}
                        emphasis
                    />
                    <Metric
                        label="Expected"
                        value={percent(leading?.expected_value)}
                    />
                    <Metric
                        label="Adjusted significance"
                        value={score(leading?.q_value)}
                        detail="Benjamini–Hochberg q-value"
                    />
                    <Metric
                        label="Sample"
                        value={integer(leading?.sample_size)}
                        detail={`${integer(leading?.baseline_points)} baseline days`}
                    />
                </div>
            </Card>
            <Card
                kicker="Scenario provenance"
                title="A controlled detector input—not a production observation"
            >
                <p>
                    The showcase deterministically applies a{" "}
                    {percent(Math.abs(Number(scenario?.magnitude)))} level
                    decrease to the {region(leading?.region)} metric copy on{" "}
                    {date(scenario?.start_at)}. This gives the detector known
                    truth without pretending a real customer incident occurred.
                </p>
                <SourceRef>{value(leading?.scenario_id)}</SourceRef>
            </Card>
            <Card
                title="Baseline, expected range, and observed drop"
                className="spanFull"
            >
                {series.length > 1 ? (
                    <DetectionChart
                        points={series}
                        anomalyEvents={anomalyEvents}
                        changePoints={changePoints}
                    />
                ) : null}
                {series.length ? (
                    <Table
                        label="Exact exported detector observations"
                        caption={`Exact source-bound series for ${region(leading?.region)} checkout conversion. Scroll horizontally to inspect every field.`}
                        head={[
                            "Metric",
                            "Observed",
                            "Sample",
                            "Baseline",
                            "Expected range",
                            "p / q",
                            "Eligible",
                            "Anomaly",
                        ]}
                        rows={series.map((point) => [
                            <span className="tablePrimary" key="metric">
                                {value(point.display_name)}
                                <small>
                                    {value(point.cohort_name)} /{" "}
                                    {value(point.unit)}
                                </small>
                            </span>,
                            number(point.observed_value),
                            integer(point.sample_size),
                            `${value(point.baseline_method)} / ${integer(point.baseline_points)} points`,
                            point.expected_lower == null ||
                            point.expected_upper == null
                                ? unavailable
                                : `${number(point.expected_lower)} - ${number(point.expected_upper)}`,
                            point.p_value == null || point.q_value == null
                                ? unavailable
                                : `${score(point.p_value)} / ${score(point.q_value)}`,
                            yesNo(point.is_eligible),
                            yesNo(point.is_anomaly),
                        ])}
                    />
                ) : (
                    <Empty section="detector observations" />
                )}
            </Card>
            <Card title="Why the alert was allowed">
                <ul className="checkList">
                    <li>
                        {integer(leading?.baseline_points)} baseline
                        observations satisfied the history gate.
                    </li>
                    <li>
                        {integer(leading?.sample_size)} interactions satisfied
                        the sample gate.
                    </li>
                    <li>
                        The drop satisfied the configured effect-size threshold.
                    </li>
                    <li>
                        {integer(leading?.detector_support_count)} independent
                        detector signals supported the decision.
                    </li>
                </ul>
            </Card>
            <Card title="Artifact scope">
                <div className="metrics metricsTwo">
                    <Metric
                        label="Scored observations"
                        value={integer(points.length)}
                    />
                    <Metric label="Eligible" value={integer(eligible.length)} />
                    <Metric
                        label="Anomaly observations"
                        value={integer(anomalies.length)}
                    />
                    <Metric
                        label="Anomaly events"
                        value={integer(anomalyEvents.length)}
                    />
                </div>
                <p className="note">
                    No separate change-point event was required for this alert;
                    anomaly and change-point outputs remain distinct.
                </p>
            </Card>
        </Page>
    );
}

export function Investigation({ b }: { b: Bundle }) {
    const investigation = report(b);
    const ranked = hypotheses(b);
    const proposalValue = nested(investigation?.proposal);
    const proposed = asRows(proposalValue?.hypotheses);
    const evidence = decisionEvidence(b);
    const attempts = asRows(investigation?.model_attempts);
    const trace = asRows(investigation?.tool_trace);
    return (
        <Page
            section="Investigation"
            title="Competing causes, inspected"
            summary="The model proposed evidence-weighted hypotheses. Deterministic software rejected unsupported citations, calculated the normalized posteriors, and applied abstention thresholds."
            aside={<Badge value={humanize(investigation?.status)} />}
        >
            <Card className="spanFull authorityBanner">
                <div>
                    <p className="overline">Responsibility split</p>
                    <h2>
                        Model proposal <span aria-hidden="true">-&gt;</span>{" "}
                        software decision
                    </h2>
                </div>
                <div className="authorityFacts">
                    <span>
                        <b>{integer(attempts.length)}</b> recorded model-route
                        attempts
                    </span>
                    <span>
                        <b>{integer(trace.length)}</b> governed read-only tool
                        calls
                    </span>
                    <span>
                        <b>{integer(evidence.length)}</b> validated evidence
                        assessments
                    </span>
                </div>
            </Card>

            <Card
                kicker="Accepted calculation"
                title="Decision state"
                className="decisionCard"
            >
                <div className="metrics metricsTwo">
                    <Metric
                        label="Status"
                        value={humanize(investigation?.status)}
                    />
                    <Metric
                        label="Confidence"
                        value={percent(investigation?.confidence)}
                        detail="posterior x (1 - normalized entropy)"
                        emphasis
                    />
                    <Metric
                        label="Posterior margin"
                        value={percent(investigation?.posterior_margin)}
                    />
                    <Metric
                        label="Normalized entropy"
                        value={score(investigation?.normalized_entropy)}
                    />
                </div>
                <p className="note">
                    Confidence is a computed concentration score, not an LLM
                    self-rating and not a guarantee that the causal judgment is
                    correct.
                </p>
            </Card>

            <section
                className="hypothesisPanel spanTwo"
                aria-labelledby="hypotheses-title"
            >
                <div className="sectionHeading">
                    <div>
                        <p className="cardKicker">Deterministic ranking</p>
                        <h2 id="hypotheses-title">Hypotheses</h2>
                    </div>
                    <span>Prior + bounded evidence ratios = posterior</span>
                </div>
                <div className="hypotheses">
                    {ranked.map((hypothesis, index) => {
                        const sourceProposal = proposed.find(
                            (item) =>
                                item.hypothesis_id === hypothesis.hypothesis_id,
                        );
                        const assessments = evidence.filter(
                            (item) =>
                                item.hypothesisId === hypothesis.hypothesis_id,
                        );
                        const posterior =
                            typeof hypothesis.posterior_probability === "number"
                                ? hypothesis.posterior_probability
                                : 0;
                        return (
                            <article
                                className={
                                    index === 0
                                        ? "hypothesis leading"
                                        : "hypothesis"
                                }
                                key={String(hypothesis.hypothesis_id)}
                            >
                                <header>
                                    <div>
                                        <p className="rank">Rank {index + 1}</p>
                                        <h3>{value(hypothesis.title)}</h3>
                                    </div>
                                    <strong>
                                        {percent(
                                            hypothesis.posterior_probability,
                                        )}
                                    </strong>
                                </header>
                                <div
                                    className="probabilityTrack"
                                    aria-hidden="true"
                                >
                                    <span
                                        style={{
                                            width: `${Math.max(1, posterior * 100)}%`,
                                        }}
                                    />
                                </div>
                                <p>{value(hypothesis.rationale)}</p>
                                <dl className="hypothesisStats">
                                    <div>
                                        <dt>Prior</dt>
                                        <dd>
                                            {percent(
                                                hypothesis.prior_probability,
                                            )}
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Log evidence</dt>
                                        <dd>
                                            {score(
                                                hypothesis.log_evidence_score,
                                            )}
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Evidence assessments</dt>
                                        <dd>{integer(assessments.length)}</dd>
                                    </div>
                                </dl>
                                <div className="evidenceAssessments">
                                    {assessments.map((assessment) => (
                                        <div
                                            key={`${assessment.hypothesisId}-${assessment.id}`}
                                        >
                                            <Badge
                                                value={assessment.direction}
                                                tone={
                                                    assessment.direction ===
                                                    "support"
                                                        ? "good"
                                                        : "warn"
                                                }
                                            />
                                            <div>
                                                <b>
                                                    {value(
                                                        assessment.event?.title,
                                                    )}
                                                </b>
                                                <p>
                                                    {assessment.explanation ??
                                                        value(
                                                            assessment.event
                                                                ?.detail,
                                                        )}
                                                </p>
                                                <small>
                                                    likelihood ratio{" "}
                                                    {number(
                                                        assessment.likelihoodRatio,
                                                    )}{" "}
                                                    /{" "}
                                                    <SourceRef>
                                                        {assessment.id}
                                                    </SourceRef>
                                                </small>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                                <div className="falsifier">
                                    <span>Would weaken or falsify this</span>
                                    <p>
                                        {stringItems(
                                            sourceProposal?.falsifiers,
                                        ).join(" ") || unavailable}
                                    </p>
                                </div>
                            </article>
                        );
                    })}
                </div>
            </section>

            <Card
                kicker="Calculation contract"
                title={`Why ${percent(ranked[0]?.posterior_probability)} is not an LLM verdict`}
                className="spanFull methodCard"
            >
                <div className="methodGrid">
                    <div>
                        <span>1</span>
                        <b>Model proposes</b>
                        <p>
                            Competing hypotheses, priors, bounded likelihood
                            ratios, citations, falsifiers, and unknowns.
                        </p>
                    </div>
                    <div>
                        <span>2</span>
                        <b>Gateway constrains</b>
                        <p>
                            Only observed evidence IDs from successful,
                            source-bound read-only tool calls may be cited.
                        </p>
                    </div>
                    <div>
                        <span>3</span>
                        <b>Software computes</b>
                        <p>
                            Log prior plus log likelihood ratios, stable
                            normalization, entropy, posterior margin, and
                            confidence.
                        </p>
                    </div>
                    <div>
                        <span>4</span>
                        <b>Policy may abstain</b>
                        <p>
                            The top posterior, margin, distinct evidence count,
                            and entropy must all pass configured thresholds.
                        </p>
                    </div>
                </div>
                <p className="note">
                    Current limitation: likelihood ratios originate in the model
                    proposal. They are bounded and benchmarked, but are not
                    learned from historical calibration data.
                </p>
            </Card>
        </Page>
    );
}

export function Evidence({ b }: { b: Bundle }) {
    const assessments = decisionEvidence(b);
    const groups = new Map<string, typeof assessments>();
    for (const assessment of assessments) {
        groups.set(assessment.id, [
            ...(groups.get(assessment.id) ?? []),
            assessment,
        ]);
    }
    const spine = keyTimeline(b).filter((event) =>
        [
            "change",
            "feature_flag",
            "incident_started",
            "incident_ended",
        ].includes(String(event.event_type)),
    );
    const additional = keyTimeline(b).filter((event) => !spine.includes(event));
    return (
        <Page
            section="Evidence"
            title="An auditable incident record"
            summary={`${integer(timeline(b).length)} source-bound timeline rows are exported. The decision records are elevated here without removing their IDs, timestamps, source systems, or relationship to each hypothesis.`}
            aside={
                <Badge value={`${groups.size} decision records`} tone="info" />
            }
        >
            <Card
                kicker="Cited by the investigation"
                title="Decision evidence"
                className="spanFull"
            >
                <div className="evidenceLedger">
                    {[...groups.entries()].map(([id, related], index) => {
                        const event = related[0]?.event;
                        const directions = [
                            ...new Set(related.map((item) => item.direction)),
                        ];
                        return (
                            <article key={id}>
                                <div className="ledgerIndex">
                                    {String(index + 1).padStart(2, "0")}
                                </div>
                                <div className="ledgerBody">
                                    <div className="ledgerMeta">
                                        <time>{time(event?.occurred_at)}</time>
                                        <span>
                                            {humanize(event?.event_type)}
                                        </span>
                                        <span>{value(event?.source)}</span>
                                    </div>
                                    <h3>{value(event?.title)}</h3>
                                    <p>{value(event?.detail)}</p>
                                    <div className="sourceLine">
                                        {directions.map((direction) => (
                                            <Badge
                                                key={direction}
                                                value={
                                                    direction === "support"
                                                        ? "Supports leading cause"
                                                        : "Contradicts alternative"
                                                }
                                                tone={
                                                    direction === "support"
                                                        ? "good"
                                                        : "warn"
                                                }
                                            />
                                        ))}
                                        <SourceRef>{id}</SourceRef>
                                        <SourceRef>
                                            {value(event?.timeline_event_id)}
                                        </SourceRef>
                                    </div>
                                </div>
                            </article>
                        );
                    })}
                </div>
            </Card>

            <Card
                kicker="Chronology"
                title="Incident spine"
                className="spanTwo"
            >
                <div className="timeline">
                    {spine.map((event) => (
                        <article key={String(event.timeline_event_id)}>
                            <span className="timelineMark" aria-hidden="true" />
                            <div>
                                <div className="ledgerMeta">
                                    <time>{time(event.occurred_at)}</time>
                                    <Badge
                                        value={humanize(event.event_type)}
                                        tone="info"
                                    />
                                </div>
                                <h3>{value(event.title)}</h3>
                                <p>{value(event.detail)}</p>
                                <small>
                                    {value(event.source)} /{" "}
                                    <SourceRef>
                                        {value(event.timeline_event_id)}
                                    </SourceRef>
                                </small>
                            </div>
                        </article>
                    ))}
                </div>
            </Card>
            <Card kicker="Provenance" title="Where the record came from">
                <dl className="provenanceList">
                    <div>
                        <dt>Change records</dt>
                        <dd>configured_change_log</dd>
                    </div>
                    <div>
                        <dt>Rollout state</dt>
                        <dd>feature_flag_history</dd>
                    </div>
                    <div>
                        <dt>Incident bounds</dt>
                        <dd>incident_context</dd>
                    </div>
                    <div>
                        <dt>Health windows</dt>
                        <dd>checkout_sessions and payment_attempts</dd>
                    </div>
                    <div>
                        <dt>Integrity</dt>
                        <dd>file SHA-256 and bundle checksum bindings</dd>
                    </div>
                </dl>
            </Card>

            <Card
                title="Additional non-health timeline records"
                className="spanFull compactCard"
            >
                <details>
                    <summary>
                        Inspect {additional.length} deployments, lineage nodes,
                        and runbook records
                    </summary>
                    <Table
                        label="Additional timeline records"
                        head={[
                            "Time",
                            "Type",
                            "Record",
                            "Source",
                            "Evidence ID",
                        ]}
                        rows={additional.map((event) => [
                            time(event.occurred_at),
                            humanize(event.event_type),
                            value(event.title),
                            value(event.source),
                            <SourceRef key="evidence">
                                {value(event.evidence_record_ids_json)}
                            </SourceRef>,
                        ])}
                    />
                </details>
            </Card>
        </Page>
    );
}

export function Impact({ b }: { b: Bundle }) {
    const measured = impact(b);
    const segments = rows(b, "segments");
    const estimates = rows(b, "causal_estimates");
    const facts = incidentFacts(b);
    const simulation = simulationConfig(b);
    const currencyCode = asRows(simulation?.regions).find(
        (item) => item.code === nested(impactConfig(b)?.incident)?.region,
    )?.currency;
    return (
        <Page
            section="Impact"
            title="Who was affected, and what is provable"
            summary="Customer and financial measures are rebuilt from the synthetic source dataset. Values absent from the validated export are not estimated by the browser."
            aside={<Badge value="Synthetic impact" tone="info" />}
        >
            <Card
                kicker="Directly counted in the synthetic incident slice"
                title={`${integer(measured?.exposed_customers)} synthetic customers were exposed`}
                className="spanTwo impactLead"
            >
                <div className="metrics metricsFour">
                    <Metric
                        label="Affected customers"
                        value={integer(measured?.exposed_customers)}
                        emphasis
                    />
                    <Metric
                        label="Failed interactions"
                        value={integer(measured?.immediate_failed_interactions)}
                    />
                    <Metric label="Incident cohort" value={facts.region} />
                    <Metric
                        label="Estimated incremental churn"
                        value={percent(measured?.incremental_churn_rate)}
                        detail="Synthetic causal estimate"
                    />
                </div>
            </Card>
            <Card
                kicker="Observed versus estimated"
                title="Counts and modeled consequences are kept separate"
            >
                <p>
                    Exposure and failed-interaction counts come directly from
                    the synthetic incident slice. Churn and financial values are
                    benchmark estimates from an analysis copy with a known
                    injected outcome effect—not company accounting facts.
                </p>
            </Card>
            <Card title="Modeled financial impact" className="spanFull">
                <div className="metrics metricsFour">
                    <Metric
                        label="Immediate revenue loss"
                        value={currency(
                            measured?.immediate_revenue_loss,
                            currencyCode,
                        )}
                        detail="Synthetic estimate"
                    />
                    <Metric
                        label="Future revenue at risk"
                        value={currency(
                            measured?.future_revenue_at_risk,
                            currencyCode,
                        )}
                        detail="Synthetic estimate"
                    />
                    <Metric
                        label="Support and recovery cost"
                        value={currency(
                            measured?.support_and_recovery_cost,
                            currencyCode,
                        )}
                        detail="Configured synthetic cost model"
                    />
                    <Metric
                        label="Total financial impact"
                        value={currency(
                            measured?.total_financial_impact,
                            currencyCode,
                        )}
                        detail={`95% interval ${currency(measured?.lower_ci, currencyCode)} - ${currency(measured?.upper_ci, currencyCode)}`}
                    />
                </div>
            </Card>
            <Card title="Affected segments" className="spanFull">
                {segments.length ? (
                    <Table
                        label="Synthetic customer impact by segment"
                        caption={`Synthetic customer impact by segment; financial values use the source region currency (${value(currencyCode)}).`}
                        head={[
                            "Segment",
                            "Customers",
                            "Exposed",
                            "Observed churn",
                            "Weighted incremental churn",
                            "Revenue risk",
                        ]}
                        rows={segments.map((segment) => [
                            `${humanize(segment.segment_name)}: ${humanize(segment.segment_value)}`,
                            integer(segment.customers),
                            integer(segment.exposed_customers),
                            percent(segment.observed_churn_rate),
                            percent(segment.weighted_incremental_churn),
                            currency(segment.revenue_at_risk, currencyCode),
                        ])}
                    />
                ) : (
                    <Empty section="segment impact" />
                )}
            </Card>
            <Card title="Causal diagnostics" className="spanFull compactCard">
                <details>
                    <summary>
                        Inspect {estimates.length} exported estimates
                    </summary>
                    {estimates.length ? (
                        <Table
                            head={[
                                "Estimator",
                                "Estimand",
                                "Estimate",
                                "95% interval",
                                "Sample",
                            ]}
                            rows={estimates.map((estimate) => [
                                humanize(estimate.estimator),
                                humanize(estimate.estimand),
                                number(estimate.estimate),
                                `${number(estimate.lower_ci)} - ${number(estimate.upper_ci)}`,
                                integer(estimate.sample_size),
                            ])}
                        />
                    ) : (
                        <Empty section="causal estimates" />
                    )}
                </details>
            </Card>
        </Page>
    );
}

export function Remediation({ b }: { b: Bundle }) {
    const remediation = plan(b);
    const execution = receipt(b);
    const recoveryReport = recovery(b);
    const observations = recoveryObservations(b);
    const recoveryPolicy = recoveryConfig(b);
    const action = asRows(remediation?.actions)[0];
    const before = resource(beforeState(b), String(action?.resource_id ?? ""));
    const after = resource(afterState(b), String(action?.resource_id ?? ""));
    const metrics = asRows(recoveryReport?.metric_evaluations);
    const postObservations = asRows(observations?.observations).filter(
        (item) =>
            typeof item.observed_at === "string" &&
            typeof execution?.executed_at === "string" &&
            Date.parse(item.observed_at) > Date.parse(execution.executed_at),
    );
    const policy = nested(remediation?.policy_decision);
    return (
        <Page
            section="Remediation & Recovery"
            title="Governed action, verified outcome"
            summary="These are historical synthetic records, not production controls. The browser cannot approve, execute, reverse, retry, or declare recovery."
            aside={<Badge value="Historical record only" tone="info" />}
        >
            <Card className="spanFull historicalNotice">
                <b>No mutation surface exists here.</b>
                <p>
                    The recorded executor changed only a validated local
                    simulated control-state artifact. It had no production or
                    cloud authority.
                </p>
            </Card>
            <Card
                kicker="Recorded sequence"
                title="Policy to recovery"
                className="spanFull"
            >
                <ol className="governanceFlow">
                    <li>
                        <span>Policy</span>
                        <b>{humanize(policy?.outcome)}</b>
                        <small>{humanize(remediation?.risk_level)} risk</small>
                    </li>
                    <li>
                        <span>Approvals</span>
                        <b>
                            {integer(remediation?.required_approvals)} required
                        </b>
                        <small>
                            Exact-plan approval snapshot bound to receipt
                        </small>
                    </li>
                    <li>
                        <span>Simulated execution</span>
                        <b>{humanize(execution?.status)}</b>
                        <small>{time(execution?.executed_at)}</small>
                    </li>
                    <li>
                        <span>Observations</span>
                        <b>
                            {integer(postObservations.length)} post-action
                            values
                        </b>
                        <small>Primary plus guardrail series</small>
                    </li>
                    <li>
                        <span>Recovery decision</span>
                        <b>{humanize(recoveryReport?.decision)}</b>
                        <small>{time(recoveryReport?.evaluated_at)}</small>
                    </li>
                </ol>
            </Card>
            <Card
                kicker="Approved plan"
                title={value(remediation?.summary)}
                className="spanTwo"
            >
                <p>{value(action?.justification)}</p>
                <dl className="actionRecord">
                    <div>
                        <dt>Resource</dt>
                        <dd>
                            <SourceRef>{value(action?.resource_id)}</SourceRef>
                        </dd>
                    </div>
                    <div>
                        <dt>Action</dt>
                        <dd>{humanize(action?.action_type)}</dd>
                    </div>
                    <div>
                        <dt>Blast radius</dt>
                        <dd>{humanize(action?.blast_radius)}</dd>
                    </div>
                    <div>
                        <dt>Rollback trigger</dt>
                        <dd>{value(remediation?.rollback_trigger)}</dd>
                    </div>
                </dl>
                <div className="stateTransition">
                    <div>
                        <span>Before</span>
                        <b>{value(before?.current_revision)}</b>
                    </div>
                    <span aria-hidden="true">-&gt;</span>
                    <div>
                        <span>After</span>
                        <b>{value(after?.current_revision)}</b>
                    </div>
                </div>
            </Card>
            <Card kicker="Safety condition" title="Execution is not recovery">
                <p>
                    The receipt proves the simulated state transition completed.
                    Recovery required separate later observations, statistical
                    equivalence checks, sustained healthy periods, and a healthy
                    payment guardrail.
                </p>
                <Metric
                    label="Approval snapshot"
                    value={compactHash(execution?.approval_snapshot_sha256)}
                />
                <Metric
                    label="Reopen threshold"
                    value={`${integer(recoveryPolicy?.reopen_after_consecutive_failures)} consecutive failures`}
                />
            </Card>
            <Card
                kicker="Deterministic verification"
                title="Recovery metrics"
                className="spanFull"
            >
                <div className="recoveryMetrics">
                    {metrics.map((metric) => (
                        <article key={value(metric.metric_id)}>
                            <header>
                                <div>
                                    <p>{humanize(metric.role)}</p>
                                    <h3>{humanize(metric.metric_id)}</h3>
                                </div>
                                <Badge value={humanize(metric.status)} />
                            </header>
                            <div className="metrics metricsFour">
                                <Metric
                                    label="Baseline center"
                                    value={percent(metric.baseline_center)}
                                />
                                <Metric
                                    label="Latest center"
                                    value={percent(metric.latest_center)}
                                />
                                <Metric
                                    label="Equivalence p-value"
                                    value={score(metric.equivalence_pvalue)}
                                />
                                <Metric
                                    label="Sustained"
                                    value={`${integer(metric.sustain_count)} periods`}
                                />
                            </div>
                        </article>
                    ))}
                </div>
            </Card>
            <Card
                title="Post-action observations"
                className="spanFull compactCard"
            >
                <details>
                    <summary>
                        Inspect {postObservations.length} exact observations
                        used by recovery verification
                    </summary>
                    <Table
                        head={["Metric", "Observed at", "Value", "Sample"]}
                        rows={postObservations.map((observation) => [
                            humanize(observation.metric_id),
                            time(observation.observed_at),
                            percent(observation.value),
                            integer(observation.sample_size),
                        ])}
                    />
                </details>
            </Card>
        </Page>
    );
}

export function Evaluation({ b }: { b: Bundle }) {
    const metrics = evaluationMetrics(b);
    const cases = evaluationCases(b);
    const bins = asRows(metrics?.reliability_bins).filter(
        (item) => Number(item.count) > 0,
    );
    return (
        <Page
            section="Evaluation"
            title="Ground truth, not persuasive prose"
            summary="A deterministic evaluator compares scripted predictions with hidden synthetic answers. These smoke results measure replayable control behavior; they are not production performance claims."
            aside={
                <Badge
                    value={`${integer(metrics?.case_count)} synthetic cases`}
                    tone="info"
                />
            }
        >
            <Card
                kicker="Scope first"
                title="A deliberately small smoke benchmark"
                className="spanFull evaluationScope"
            >
                <p>
                    Percentages below come from {integer(metrics?.case_count)}{" "}
                    synthetic cases, with{" "}
                    {integer(metrics?.calibration_case_count)} calibration
                    cases. A {percent(metrics?.top1_accuracy)} top-1 result
                    therefore means the committed smoke fixtures passed, not
                    that the system has proven production-grade model accuracy.
                </p>
            </Card>
            <Card title="Diagnosis and calibration" className="spanTwo">
                <div className="metrics metricsFour">
                    <Metric
                        label="Top-1 accuracy"
                        value={percent(metrics?.top1_accuracy)}
                        detail={`${integer(metrics?.case_count)} cases`}
                    />
                    <Metric
                        label="Top-3 recall"
                        value={percent(metrics?.top3_recall)}
                    />
                    <Metric
                        label="Brier score"
                        value={score(metrics?.brier_score)}
                        detail="Lower is better"
                    />
                    <Metric
                        label="Log loss"
                        value={score(metrics?.clipped_log_loss)}
                        detail="Lower is better"
                    />
                    <Metric
                        label="Coverage"
                        value={percent(metrics?.coverage)}
                    />
                    <Metric
                        label="Selective risk"
                        value={percent(metrics?.selective_risk)}
                        detail="Error rate on answered cases"
                    />
                    <Metric
                        label="Citation validity"
                        value={percent(metrics?.citation_validity_rate)}
                    />
                    <Metric
                        label="Evidence coverage"
                        value={percent(metrics?.required_evidence_coverage)}
                    />
                </div>
            </Card>
            <Card title="Safety results">
                <div className="metrics metricsTwo">
                    <Metric
                        label="Authority violations"
                        value={integer(
                            metrics?.prohibited_action_authorized_count,
                        )}
                    />
                    <Metric
                        label="Unsupported claims"
                        value={integer(metrics?.unsupported_claim_count)}
                    />
                    <Metric
                        label="Claimed recovery authority"
                        value={integer(
                            metrics?.model_claimed_recovery_authority_count,
                        )}
                    />
                    <Metric
                        label="Tool failures"
                        value={integer(metrics?.tool_failure_count)}
                    />
                </div>
                <p className="note">
                    These counts show fixture outcomes. They do not prove that a
                    model will never propose unsafe content.
                </p>
            </Card>
            <Card title="Visible benchmark cases" className="spanFull">
                <div className="caseGrid">
                    {cases.map((item) => (
                        <article key={value(item.case_id)}>
                            <div>
                                <Badge
                                    value={humanize(item.difficulty)}
                                    tone="info"
                                />
                                <SourceRef>{value(item.case_id)}</SourceRef>
                            </div>
                            <h3>{humanize(item.family)}</h3>
                            <p>{value(item.incident_input)}</p>
                        </article>
                    ))}
                </div>
            </Card>
            <Card title="Calibration bins" className="spanTwo">
                {bins.length ? (
                    <Table
                        head={[
                            "Confidence range",
                            "Cases",
                            "Mean confidence",
                            "Accuracy",
                        ]}
                        rows={bins.map((item) => [
                            `${percent(item.lower_bound)} - ${percent(item.upper_bound)}`,
                            integer(item.count),
                            percent(item.mean_confidence),
                            percent(item.accuracy),
                        ])}
                    />
                ) : (
                    <Empty section="non-empty reliability bins" />
                )}
            </Card>
            <Card title="Intentionally unavailable scores">
                <Empty
                    section="remediation and recovery accuracy"
                    explanation="The smoke predictions do not contain enough scored remediation or recovery decisions, so those aggregate metrics are null rather than inferred."
                />
            </Card>
        </Page>
    );
}

export function System({
    b,
    sourceCommit,
    integrity,
}: {
    b: Bundle;
    sourceCommit?: string;
    integrity: "verified" | "warning" | "local";
}) {
    const config = investigationConfig(b);
    const allowedTools = stringItems(config?.allowed_tools);
    return (
        <Page
            section="System & Limitations"
            title="Useful AI, bounded authority"
            summary="The system separates model exploration from deterministic authority and from a static browser that can only present a validated public bundle."
            aside={<Badge value={`Bundle ${integrity}`} />}
        >
            <Card
                kicker="Trust model"
                title="Three distinct authority zones"
                className="spanFull trustMap"
            >
                <div className="trustZones">
                    <article className="modelZone">
                        <span>01 / Model</span>
                        <h3>Investigate</h3>
                        <p>
                            Choose approved tools; propose competing hypotheses,
                            likelihood ratios, citations, falsifiers, unknowns,
                            and read-only next checks.
                        </p>
                        <b>Cannot make an accepted decision</b>
                    </article>
                    <article className="softwareZone">
                        <span>02 / Deterministic software</span>
                        <h3>Validate and govern</h3>
                        <p>
                            Bind sources, enforce tools and SQL, reject
                            unsupported citations, compute probability and
                            abstention, gate simulated action, verify recovery,
                            and score evaluation.
                        </p>
                        <b>Owns consequential truth</b>
                    </article>
                    <article className="browserZone">
                        <span>03 / Public browser</span>
                        <h3>Present</h3>
                        <p>
                            Load the closed-world public bundle, navigate,
                            change theme, inspect tables, and expose deployment
                            identity.
                        </p>
                        <b>Read-only, with no mutation API</b>
                    </article>
                </div>
            </Card>
            <Card
                kicker="Public run mode"
                title="Scripted provider: architecture validation, not live-model performance"
                className="spanFull historicalNotice"
            >
                <p>
                    The credential-free bundle replays committed structured
                    provider responses through the same tool, probability,
                    policy, and evaluation contracts. No external LLM generated
                    this public result at build time.
                </p>
            </Card>
            <Card
                kicker="Model access"
                title="Bounded read-only tools"
                className="spanTwo"
            >
                <div className="toolList">
                    {allowedTools.map((tool) => (
                        <SourceRef key={tool}>{tool}</SourceRef>
                    ))}
                </div>
                <p className="note">
                    Tool arguments are schema-validated, role-authorized,
                    source-bound, size-limited, and audit-recorded. SQL is
                    AST-parsed SELECT-only over registered in-memory tables.
                </p>
            </Card>
            <Card
                kicker="Explicitly unavailable"
                title="No ambient operator power"
            >
                <ul className="deniedList">
                    <li>
                        Production, cloud, registry, or provider credentials in
                        the browser
                    </li>
                    <li>
                        Arbitrary shell, filesystem, unrestricted SQL, or
                        arbitrary tool network access
                    </li>
                    <li>Approval, remediation, rollback, or retry authority</li>
                    <li>Recovery-decision or evaluator authority</li>
                    <li>
                        Natural-language approval or instructions embedded in
                        retrieved evidence
                    </li>
                </ul>
            </Card>
            <Card
                kicker="Public artifact"
                title="Deployment identity"
                className="spanTwo"
            >
                <dl className="identityGrid">
                    <div>
                        <dt>Bundle kind</dt>
                        <dd>{b.bundle_kind}</dd>
                    </div>
                    <div>
                        <dt>Schema</dt>
                        <dd>{b.schema_version}</dd>
                    </div>
                    <div>
                        <dt>Sanitized files</dt>
                        <dd>{integer(b.files.length)}</dd>
                    </div>
                    <div>
                        <dt>Integrity</dt>
                        <dd>{humanize(integrity)}</dd>
                    </div>
                    <div>
                        <dt>Bundle source</dt>
                        <dd>
                            <SourceRef>
                                {b.source_commit ?? unavailable}
                            </SourceRef>
                        </dd>
                    </div>
                    <div>
                        <dt>Deployed source</dt>
                        <dd>
                            <SourceRef>
                                {sourceCommit ??
                                    "Local development identity unavailable"}
                            </SourceRef>
                        </dd>
                    </div>
                </dl>
                <p className="note">
                    The exporter excludes credentials, environment-like fields,
                    evaluator answer keys, absolute paths, and mutable inputs
                    before the frontend build.
                </p>
            </Card>
            <Card
                kicker="Current limits"
                title="What this demonstration does not prove"
            >
                <ul className="plainList stacked">
                    <li>
                        All incident and benchmark data are synthetic, not
                        production claims.
                    </li>
                    <li>
                        The detector receives a controlled metric perturbation;
                        the repository does not inject one defect through every
                        raw commerce event and downstream artifact.
                    </li>
                    <li>
                        Likelihood ratios are model-proposed and bounded, not
                        historically learned.
                    </li>
                    <li>
                        The benchmark contains only{" "}
                        {integer(evaluationMetrics(b)?.case_count)} smoke cases.
                    </li>
                    <li>
                        The default investigation loop is synchronous and
                        single-agent.
                    </li>
                    <li>
                        Simulated local state locking does not claim distributed
                        exactly-once behavior.
                    </li>
                </ul>
            </Card>
        </Page>
    );
}
