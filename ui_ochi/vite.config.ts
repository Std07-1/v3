import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// «Очі Арчі» — окрема консоль спостереження за агентом. Живе під /ochi/ на VPS
// (nginx alias), тому base фіксований. Dev-проксі шле /api на ws_server (:8000),
// той самий auth-контур (_archi_auth), що й ui_archi_v2 — токен шериться (archi_token).
export default defineConfig(() => {
    const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000';
    return {
        base: '/ochi/',
        plugins: [svelte()],
        build: {
            outDir: 'dist',
            assetsDir: 'assets',
        },
        server: {
            port: 5176,
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
