import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
    base: process.env.GITHUB_ACTIONS
        ? "/probabilistic-ai-incident-commander/"
        : "/",
    plugins: [react()],
    build: { sourcemap: false, cssCodeSplit: true, chunkSizeWarningLimit: 400 },
    test: { environment: "jsdom", exclude: ["**/node_modules/**", "e2e/**"] },
});
