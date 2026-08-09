import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";

describe("public demo bundle", () => {
    it("uses the validated public contract and does not contain browser authority", async () => {
        const bundle = JSON.parse(
            await readFile("public/data/bundle.json", "utf8"),
        ) as {
            schema_version: string;
            bundle_kind: string;
            files: { path: string }[];
        };
        expect(bundle.schema_version).toBe("1.0");
        expect(bundle.bundle_kind).toBe("paic-public-demo");
        expect(bundle.files.length).toBeGreaterThan(0);
        expect(bundle.files.some((file) => /answer/i.test(file.path))).toBe(
            false,
        );
    });
});
