import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig(() => {
    const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000';
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
                    target: apiTarget,
                    changeOrigin: true,
                },
                '/api/agent': {
                    target: apiTarget,
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
