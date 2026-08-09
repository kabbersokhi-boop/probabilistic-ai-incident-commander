import { describe, expect, it } from "vitest";
import { currency, integer, percent, score } from "./formatters";
describe("metric formatters", () => {
    it("formats values by semantic unit without treating all scores as percentages", () => {
        expect(percent(0.25)).toBe("25.0%");
        expect(integer(12.6)).toBe("13");
        expect(score(0.175)).toBe("0.175");
        expect(currency(12.5, "USD")).toBe("$12.50");
    });
});
