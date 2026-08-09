import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DetectionChart } from "./DetectionChart";

const points = [
    {
        observation_id: "one",
        period_start: "2026-01-01T00:00:00+00:00",
        metric_name: "conversion",
        observed_value: 10,
        expected_lower: 8,
        expected_upper: 12,
        is_anomaly: false,
    },
    {
        observation_id: "two",
        period_start: "2026-01-02T00:00:00+00:00",
        metric_name: "conversion",
        observed_value: 14,
        expected_lower: 8,
        expected_upper: 12,
        is_anomaly: true,
    },
];

describe("DetectionChart", () => {
    it("renders the exact projected values, expected band, event marks, and keyboard navigation", async () => {
        const user = userEvent.setup();
        render(
            <DetectionChart
                points={points}
                anomalyEvents={[]}
                changePoints={[{ metric_name: "conversion" }]}
            />,
        );

        expect(screen.getByLabelText("Expected range")).not.toBeNull();
        expect(document.querySelectorAll(".chartAnomaly")).toHaveLength(1);
        expect(document.querySelectorAll(".chartChange")).toHaveLength(2);
        const first = screen.getByRole("button", { name: /observed 10/ });
        await user.click(first);
        await user.keyboard("{ArrowRight}");
        expect(document.activeElement).toBe(
            screen.getByRole("button", { name: /observed 14/ }),
        );
        expect(screen.getByText(/Jan 2, 2026.*14/)).not.toBeNull();
    });
});
