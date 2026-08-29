import { describe, expect, it } from "vitest";
import {
    compactHash,
    currency,
    humanize,
    integer,
    percent,
    region,
    score,
} from "./formatters";
describe("metric formatters", () => {
    it("formats values by semantic unit without treating all scores as percentages", () => {
        expect(percent(0.25)).toBe("25.0%");
        expect(integer(12.6)).toBe("13");
        expect(score(0.175)).toBe("0.175");
        expect(currency(12.5, "USD")).toBe("$12.50");
        expect(region("IN-SOUTH")).toBe("India South");
        expect(humanize("checkout_failure")).toBe("Checkout Failure");
        expect(compactHash("a".repeat(40))).toBe("aaaaaaaa...aaaaaa");
    });
});
