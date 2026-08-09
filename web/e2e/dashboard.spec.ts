import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("deep links, preserves read-only controls, has no console errors, and is accessible", async ({
    page,
}) => {
    const errors: string[] = [];
    page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
    });
    await page.goto("/#/detection");
    await expect(
        page.getByRole("heading", { name: "Detection" }),
    ).toBeVisible();
    await expect(page).toHaveTitle("PAIC — Detection");
    await expect(
        page.getByText(
            "Read-only · source-bound · deterministic public bundle",
        ),
    ).toBeVisible();
    if (test.info().project.name === "mobile") {
        await expect(page.getByRole("button", { name: "Menu" })).toBeVisible();
    } else {
        await expect(
            page.getByRole("navigation", { name: "Primary navigation" }),
        ).toBeVisible();
    }
    expect(errors).toEqual([]);
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
});

test("overview has a stable visual baseline", async ({ page }) => {
    await page.goto("/#/overview");
    const snapshot =
        process.env.GITHUB_ACTIONS === "true"
            ? "overview-github-actions.png"
            : "overview.png";

    await expect(page).toHaveScreenshot(snapshot, {
        fullPage: true,
        animations: "disabled",
        maxDiffPixelRatio: 0.001,
    });
});
