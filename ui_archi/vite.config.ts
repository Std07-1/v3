import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig(() => {
    const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000';
    // ADR-0090 S1: /api/archi, /api/agent обслуговує окремий процес smc-agent-bridge
    const bridgeTarget = process.env.VITE_BRIDGE_PROXY_TARGET ?? 'http://localhost:8010';
    return {
        plugins: [svelte()],
        build: {
            outDir: 'dist',
            assetsDir: 'assets',
        },
        server: {
            port: 5174,
            open: false,
            proxy: {
                '/api/archi': {
                    target: bridgeTarget,
                    changeOrigin: true,
                },
                '/api/agent': {
                    target: bridgeTarget,
                    changeOrigin: true,
                },
                '/api/status': {
                    target: apiTarget,
                    changeOrigin: true,
                },
            },
        },
    };
});
