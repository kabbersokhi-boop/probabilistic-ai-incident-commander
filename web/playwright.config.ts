import { defineConfig } from "@playwright/test";

export default defineConfig({
    testDir: "./e2e",
    fullyParallel: true,
    reporter: "list",
    use: { baseURL: "http://127.0.0.1:4173", trace: "retain-on-failure" },
    webServer: {
        command: "npm run preview -- --host 127.0.0.1 --port 4173",
        port: 4173,
        reuseExistingServer: !process.env.CI,
    },
    projects: [
        {
            name: "desktop-1440",
            use: {
                browserName: "chromium",
                viewport: { width: 1440, height: 900 },
            },
        },
        {
            name: "desktop-1024",
            use: {
                browserName: "chromium",
                viewport: { width: 1024, height: 768 },
            },
        },
        {
            name: "tablet-768",
            use: {
                browserName: "chromium",
                viewport: { width: 768, height: 900 },
            },
        },
        {
            name: "mobile-360",
            use: {
                browserName: "chromium",
                viewport: { width: 360, height: 800 },
                isMobile: true,
                hasTouch: true,
            },
        },
    ],
});
