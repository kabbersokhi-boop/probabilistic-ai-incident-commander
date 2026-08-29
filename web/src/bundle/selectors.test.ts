import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import type { Bundle } from "./schema";
import {
    decisionEvidence,
    evaluationCases,
    impactConfig,
    keyTimeline,
    recovery,
} from "./selectors";

describe("public presentation selectors", () => {
    it("derives the incident narrative only from validated bundle records", async () => {
        const bundle = JSON.parse(
            await readFile("public/data/bundle.json", "utf8"),
        ) as Bundle;
        const incident = impactConfig(bundle)?.incident as Record<
            string,
            unknown
        >;
        const evidence = decisionEvidence(bundle);

        expect(incident.region).toBe("IN-SOUTH");
        expect(evidence.map((item) => item.id)).toContain(
            "EVD-57254dcba871b9c0a361",
        );
        expect(evidence.every((item) => item.event)).toBe(true);
        expect(keyTimeline(bundle).length).toBeGreaterThan(4);
        expect(recovery(bundle)?.decision).toBe("recovered");
        expect(evaluationCases(bundle)).toHaveLength(3);
    });
});
