import { mount } from 'svelte';
import App from './App.svelte';
import { initViewportVars } from './app/viewport';

// ADR-0066 PATCH 02a: visual identity tokens SSOT (palette + typography + spacing).
// Imported once at entry; consumed by chart/themes.ts (PATCH 02b) and components (PATCH 06).
import './styles/tokens.css';

// P1: Initialize JS-based viewport height before mount (TG WebView needs --app-vh)
initViewportVars();

const el = document.getElementById('app');
if (!el) throw new Error('Mount target #app not found');
const app = mount(App, { target: el });

// ADR-0071 P5 — Service Worker registration (PWA shell-only V1).
// Skipped у dev mode (Vite localhost:5173 + HMR) — SW конфліктує з HMR.
// Production registers /sw.js → enables install prompt + offline fallback.
// No skipWaiting у V1 — new SW activates на user reload.
if ('serviceWorker' in navigator && !import.meta.env.DEV) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .then((reg) => {
        console.log('[PWA] SW registered, scope:', reg.scope);
      })
      .catch((err) => {
        console.warn('[PWA] SW register failed:', err);
      });
  });
}

export default app;
