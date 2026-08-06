import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ base: process.env.GITHUB_ACTIONS ? '/probabilistic-ai-incident-commander/' : '/', plugins: [react()], build: { sourcemap: false, cssCodeSplit: true, chunkSizeWarningLimit: 400 } });
