import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Bundle } from "../bundle/schema";
import { Detection } from "./pages";

const bundle = {
    presentation: {
        detectors: [
            {
                observation_id: "first",
                period_start: "2026-01-01T00:00:00+00:00",
                display_name: "Conversion",
                cohort_name: "all",
                metric_name: "conversion",
                observed_value: 10,
                expected_lower: 8,
                expected_upper: 12,
                p_value: 0.01,
                q_value: 0.02,
                is_anomaly: true,
            },
        ],
        anomaly_events: [{ metric_name: "conversion" }],
        change_points: [{ metric_name: "conversion" }],
    },
} as unknown as Bundle;

describe("Detection", () => {
    it("uses an exact table rather than plotting unrelated metric observations", () => {
        render(<Detection b={bundle} />);

        expect(
            screen.queryByLabelText("Interactive detector observations"),
        ).toBeNull();
        expect(
            screen.getByRole("region", {
                name: "Exact exported detector observations",
            }),
        ).not.toBeNull();
        expect(screen.getByText("Conversion")).not.toBeNull();
        expect(screen.getByText("8 - 12")).not.toBeNull();
        expect(
            screen.getByText(/Connecting the two different metrics/),
        ).not.toBeNull();
    });
});
