import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Bundle } from "../bundle/schema";
import { Detection } from "./pages";

const bundle = {
    files: [
        {
            path: "metrics.detection/detection-reference/detection.config.resolved.json",
            content: {
                benchmark_scenarios: [
                    {
                        scenario_id: "checkout-drop",
                        magnitude: -0.45,
                        start_at: "2026-01-16T00:00:00Z",
                    },
                ],
            },
        },
    ],
    presentation: {
        detectors: [
            {
                observation_id: "first",
                period_start: "2026-01-16T00:00:00+00:00",
                display_name: "Checkout conversion rate",
                cohort_name: "region",
                metric_name: "checkout_conversion_rate",
                time_grain: "day",
                region: "IN-SOUTH",
                observed_value: 0.456,
                expected_value: 0.914,
                expected_lower: 0.88,
                expected_upper: 0.948,
                sample_size: 79,
                baseline_points: 14,
                detector_support_count: 4,
                p_value: 0.01,
                q_value: 0.02,
                is_eligible: true,
                is_anomaly: true,
                scenario_id: "checkout-drop",
            },
        ],
        anomaly_events: [{ metric_name: "conversion" }],
        change_points: [{ metric_name: "conversion" }],
    },
} as unknown as Bundle;

describe("Detection", () => {
    it("explains the source-bound anomaly and controlled scenario provenance", () => {
        render(<Detection b={bundle} />);

        expect(
            screen.getByRole("heading", {
                name: "How do we know something abnormal happened?",
            }),
        ).not.toBeNull();
        expect(
            screen.getByRole("region", {
                name: "Exact exported detector observations",
            }),
        ).not.toBeNull();
        expect(screen.getAllByText("45.6%").length).toBeGreaterThan(0);
        expect(screen.getByText(/controlled detector input/i)).not.toBeNull();
        expect(screen.getByText("checkout-drop")).not.toBeNull();
    });
});
