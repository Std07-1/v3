import { defineConfig, searchForWorkspaceRoot } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// ADR-0066 PATCH 04: build-time metadata for AboutModal version line.
// All readers are pure-fs (no shell exec) so safe from injection.
function readVersion(): string {
    try {
        const pkg = JSON.parse(readFileSync('./package.json', 'utf8'));
        return `v${pkg.version ?? '3-dev'}`;
    } catch {
        return 'v3-dev';
    }
}
function readCommit(): string {
    // Resolve commit hash by walking .git/HEAD -> ref file. No shell.
    try {
        const head = readFileSync(resolve('..', '.git', 'HEAD'), 'utf8').trim();
        if (head.startsWith('ref: ')) {
            const refPath = head.slice(5).trim();
            const sha = readFileSync(resolve('..', '.git', refPath), 'utf8').trim();
            return sha.slice(0, 7);
        }
        // detached HEAD — head IS the sha
        return head.slice(0, 7);
    } catch {
        return '—';
    }
}
function buildDate(): string {
    return new Date().toISOString().slice(0, 10); // YYYY-MM-DD
}

export default defineConfig(() => {
    const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000';
    // ADR-0114: лінії-сповіщення — окремий процес smc-alerts (у проді nginx /api/alerts/ → :8020). Процес звіряє Origin
    // зі своїм site_url (CSRF), тож dev-проксі підставляє адресу сайту замість localhost-у dev-сервера.
    const alertsTarget = process.env.VITE_ALERTS_PROXY_TARGET ?? 'http://localhost:8020';
    const alertsOrigin = process.env.VITE_ALERTS_SITE_ORIGIN ?? 'https://aione-smc.com';
    return {
        plugins: [svelte()],
        define: {
            __APP_VERSION__: JSON.stringify(readVersion()),
            __BUILD_DATE__: JSON.stringify(buildDate()),
            __COMMIT_HASH__: JSON.stringify(readCommit()),
        },
        server: {
            port: 5173,
            open: false,
            // ADR-0116: словники мов лежать у корені репо (`i18n/`, їх читає і сервер) — dev-серверу треба дозволити
            fs: { allow: [searchForWorkspaceRoot(process.cwd()), resolve('..', 'i18n')] },
            proxy: {
                // специфічніший префікс — першим (vite бере перший збіг)
                '/api/alerts': {
                    target: alertsTarget,
                    headers: { origin: alertsOrigin },
                },
                '/api': {
                    target: apiTarget,
                    changeOrigin: true,
                },
            },
        },
    };
});
