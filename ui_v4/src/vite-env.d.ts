/// <reference types="svelte" />
/// <reference types="vite/client" />

interface ImportMetaEnv {
    readonly VITE_WS_URL: string;
    readonly VITE_EDGE_PROBE_URL: string;
}
interface ImportMeta {
    readonly env: ImportMetaEnv;
}

// ADR-0066 PATCH 04: build-time constants injected via vite.config.ts define()
declare const __APP_VERSION__: string;
declare const __BUILD_DATE__: string;
declare const __COMMIT_HASH__: string;
