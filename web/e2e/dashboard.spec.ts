import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const routes = [
    ["overview", "Checkout Failure in India South"],
    ["detection", "What the detector can prove"],
    ["investigation", "Competing causes, inspected"],
    ["evidence", "An auditable incident record"],
    ["impact", "Who was affected, and what is provable"],
    ["remediation-recovery", "Governed action, verified outcome"],
    ["evaluation", "Ground truth, not persuasive prose"],
    ["system-limitations", "Useful AI, bounded authority"],
] as const;

test("all deep links render source-bound, accessible read-only records", async ({
    page,
}) => {
    const errors: string[] = [];
    page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
    });

    for (const [route, heading] of routes) {
        await page.goto(`/#/${route}`);
        await expect(
            page.getByRole("heading", { name: heading }),
        ).toBeVisible();
        await expect(page).toHaveTitle(
            `PAIC — ${route === "remediation-recovery" ? "Remediation & Recovery" : route === "system-limitations" ? "System & Limitations" : heading === "Checkout Failure in India South" ? "Overview" : route[0].toUpperCase() + route.slice(1)}`,
        );
        await expect(page.locator("footer")).toContainText(
            "Read-only public artifact",
        );
        const horizontalOverflow = await page.evaluate(
            () => document.documentElement.scrollWidth - window.innerWidth,
        );
        expect(
            horizontalOverflow,
            `${route} horizontal overflow`,
        ).toBeLessThanOrEqual(1);
        const accessibility = await new AxeBuilder({ page }).analyze();
        expect(accessibility.violations, `${route} axe violations`).toEqual([]);
    }

    await expect(
        page.getByRole("button", {
            name: /execute|approve|rollback|recover|retry/i,
        }),
    ).toHaveCount(0);
    expect(errors).toEqual([]);
});

test("dark theme preserves contrast and source-bound content", async ({
    page,
}, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-1440");
    await page.goto("/#/system-limitations");
    await page.getByLabel("Color theme").selectOption("dark");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(
        page.getByText("Read-only, with no mutation API"),
    ).toBeVisible();
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
});

test("skip link exposes and moves focus to main content", async ({
    page,
}, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-1440");
    await page.goto("/#/overview");
    await expect(
        page.getByRole("heading", { name: "Checkout Failure in India South" }),
    ).toBeVisible();
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.locator("main")).toBeFocused();
});

test("overview passes the 30-second comprehension facts", async ({ page }) => {
    await page.goto("/#/overview");
    await expect(
        page.getByText("AI-assisted investigation", { exact: false }),
    ).toBeVisible();
    await expect(page.getByText("53", { exact: true }).first()).toBeVisible();
    await expect(
        page.getByText("99.5%", { exact: true }).first(),
    ).toBeVisible();
    await expect(
        page.getByText(/payment\.retry_timeout_ms/i).first(),
    ).toBeVisible();
    await expect(
        page.getByText(
            /No approvals, execution, rollback, or recovery decisions/i,
        ),
    ).toBeVisible();
    await expect(
        page.getByText(/Validation health, not business health/i),
    ).toBeVisible();
});

test("mobile navigation is keyboard dismissible and restores focus", async ({
    page,
}, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-360");
    await page.goto("/#/overview");
    const menu = page.getByRole("button", { name: "Menu" });
    await menu.click();
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    await expect(
        page.getByRole("link", { name: /System & Limitations/ }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(menu).toBeFocused();
});

test("theme selection persists without changing incident data", async ({
    page,
}) => {
    await page.goto("/#/overview");
    await page.getByLabel("Color theme").selectOption("light");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await page.reload();
    await expect(page.getByLabel("Color theme")).toHaveValue("light");
    await expect(page.getByText("53", { exact: true }).first()).toBeVisible();
});

test("bundle failure remains fail-closed", async ({ page }) => {
    await page.route("**/data/bundle.json", (route) =>
        route.fulfill({ status: 503, body: "" }),
    );
    await page.goto("/#/overview");
    await expect(
        page.getByRole("heading", { name: "Public bundle unavailable" }),
    ).toBeVisible();
    await expect(
        page.getByText(/does not substitute missing incident data/i),
    ).toBeVisible();
    await expect(page.getByText("53", { exact: true })).toHaveCount(0);
});

test("representative pages have reviewed visual baselines", async ({
    page,
}, testInfo) => {
    for (const route of [
        "overview",
        "investigation",
        "system-limitations",
    ] as const) {
        await page.goto(`/#/${route}`);
        await expect(page).toHaveScreenshot(`${route}.png`, {
            fullPage: true,
            animations: "disabled",
            maxDiffPixelRatio: 0.001,
        });
    }
    expect(testInfo.project.name).toBeTruthy();
});
