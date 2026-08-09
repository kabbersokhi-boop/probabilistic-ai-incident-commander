import { describe, expect, it } from "vitest";

import { loadBundle } from "./loadBundle";

describe("loadBundle", () => {
    it("reports fetch, contract, and shape failures without inventing data", async () => {
        expect(
            await loadBundle(async () => new Response(null, { status: 404 })),
        ).toEqual({ error: "Public bundle is unavailable." });
        expect(
            await loadBundle(
                async () =>
                    new Response(JSON.stringify({ schema_version: "2.0" })),
            ),
        ).toEqual({ error: "Unsupported public bundle contract." });
        expect(
            await loadBundle(
                async () =>
                    new Response(
                        JSON.stringify({
                            schema_version: "1.0",
                            bundle_kind: "paic-public-demo",
                        }),
                    ),
            ),
        ).toEqual({ error: "Malformed public bundle." });
    });
});
