import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig(() => {
    const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8089';
    return {
        plugins: [svelte()],
        server: {
            port: 5173,
            open: false,
            proxy: {
                '/api': {
                    target: apiTarget,
                    changeOrigin: true,
                },
            },
        },
    };
});
