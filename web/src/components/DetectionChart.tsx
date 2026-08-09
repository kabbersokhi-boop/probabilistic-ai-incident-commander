import { useId, useState } from "react";

import type { RecordValue } from "../bundle/schema";
import { number, time } from "../bundle/formatters";

type ChartProps = {
    points: RecordValue[];
    anomalyEvents: RecordValue[];
    changePoints: RecordValue[];
};

type Point = {
    row: RecordValue;
    value: number;
    lower?: number;
    upper?: number;
};

type RawPoint = Omit<Point, "value"> & { value?: number };

const dimensions = {
    width: 720,
    height: 220,
    left: 34,
    right: 18,
    top: 18,
    bottom: 44,
};

function numeric(value: unknown): number | undefined {
    return typeof value === "number" && Number.isFinite(value)
        ? value
        : undefined;
}

function eventMatches(point: RecordValue, event: RecordValue): boolean {
    const eventMetric = event.metric_name ?? event.metric_id;
    return (
        eventMetric === point.metric_name || eventMetric === point.display_name
    );
}

export function DetectionChart({
    points,
    anomalyEvents,
    changePoints,
}: ChartProps) {
    const chartId = useId();
    const rawPoints: RawPoint[] = points.map((row) => ({
        row,
        value: numeric(row.observed_value),
        lower: numeric(row.expected_lower),
        upper: numeric(row.expected_upper),
    }));
    const chartPoints = rawPoints.filter(
        (point): point is Point => point.value !== undefined,
    );
    const [selected, setSelected] = useState(0);

    if (!chartPoints.length) return null;

    const values = chartPoints.flatMap(({ value, lower, upper }) =>
        [value, lower, upper].filter(
            (candidate): candidate is number => candidate !== undefined,
        ),
    );
    const minimum = Math.min(...values);
    const span = Math.max(...values) - minimum || 1;
    const plotWidth = dimensions.width - dimensions.left - dimensions.right;
    const plotHeight = dimensions.height - dimensions.top - dimensions.bottom;
    const x = (index: number) =>
        dimensions.left +
        plotWidth *
            (chartPoints.length === 1 ? 0.5 : index / (chartPoints.length - 1));
    const y = (value: number) =>
        dimensions.top + plotHeight - ((value - minimum) / span) * plotHeight;
    const selectedPoint =
        chartPoints[Math.min(selected, chartPoints.length - 1)];
    const observedPath = chartPoints
        .map(
            ({ value }, index) =>
                `${index ? "L" : "M"} ${x(index)} ${y(value)}`,
        )
        .join(" ");
    const bandPoints = chartPoints.every(
        ({ lower, upper }) => lower !== undefined && upper !== undefined,
    )
        ? `${chartPoints.map(({ upper }, index) => `${x(index)},${y(upper!)}`).join(" ")} ${[
              ...chartPoints,
          ]
              .reverse()
              .map(
                  ({ lower }, reverseIndex) =>
                      `${x(chartPoints.length - 1 - reverseIndex)},${y(lower!)}`,
              )
              .join(" ")}`
        : undefined;

    function select(index: number) {
        const next = (index + chartPoints.length) % chartPoints.length;
        setSelected(next);
        document.getElementById(`${chartId}-${next}`)?.focus();
    }

    return (
        <figure className="chart">
            <figcaption id={`${chartId}-caption`}>
                Exact detector values in UTC. Arrow keys move between
                observations; the selected value is announced below.
            </figcaption>
            <svg
                viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
                role="group"
                aria-label="Interactive detector observations"
            >
                {bandPoints ? (
                    <polygon
                        className="chartBand"
                        points={bandPoints}
                        aria-label="Expected range"
                    />
                ) : null}
                <path
                    className="chartLine"
                    d={observedPath}
                    aria-hidden="true"
                />
                {chartPoints.map(({ row, value, lower, upper }, index) => {
                    const anomaly =
                        Boolean(row.is_anomaly) ||
                        anomalyEvents.some((event) => eventMatches(row, event));
                    const change =
                        Boolean(row.change_detected) ||
                        changePoints.some((event) => eventMatches(row, event));
                    const label = `${time(row.period_start)}: observed ${number(value)}${lower !== undefined && upper !== undefined ? `; expected ${number(lower)} to ${number(upper)}` : ""}${anomaly ? "; anomaly" : ""}${change ? "; change point" : ""}`;
                    return (
                        <g key={String(row.observation_id ?? index)}>
                            {anomaly ? (
                                <circle
                                    className="chartAnomaly"
                                    cx={x(index)}
                                    cy={y(value)}
                                    r="10"
                                    aria-hidden="true"
                                />
                            ) : null}
                            {change ? (
                                <path
                                    className="chartChange"
                                    d={`M ${x(index)} ${dimensions.top} V ${dimensions.top + plotHeight}`}
                                    aria-hidden="true"
                                />
                            ) : null}
                            <circle
                                id={`${chartId}-${index}`}
                                className="chartPoint"
                                cx={x(index)}
                                cy={y(value)}
                                r="6"
                                tabIndex={selected === index ? 0 : -1}
                                role="button"
                                aria-label={label}
                                aria-pressed={selected === index}
                                onClick={() => setSelected(index)}
                                onFocus={() => setSelected(index)}
                                onKeyDown={(event) => {
                                    if (
                                        event.key === "ArrowRight" ||
                                        event.key === "ArrowDown"
                                    ) {
                                        event.preventDefault();
                                        select(index + 1);
                                    }
                                    if (
                                        event.key === "ArrowLeft" ||
                                        event.key === "ArrowUp"
                                    ) {
                                        event.preventDefault();
                                        select(index - 1);
                                    }
                                    if (event.key === "Home") {
                                        event.preventDefault();
                                        select(0);
                                    }
                                    if (event.key === "End") {
                                        event.preventDefault();
                                        select(chartPoints.length - 1);
                                    }
                                }}
                            />
                        </g>
                    );
                })}
            </svg>
            <p
                id={`${chartId}-value`}
                aria-live="polite"
            >{`${time(selectedPoint.row.period_start)}: ${number(selectedPoint.value)}`}</p>
        </figure>
    );
}
