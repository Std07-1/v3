import { mount } from 'svelte';
import App from './App.svelte';
import { initViewportVars } from './app/viewport';
import { initI18n } from './lib/i18n/i18n.svelte';

// ADR-0066 PATCH 02a: visual identity tokens SSOT (palette + typography + spacing).
// Imported once at entry; consumed by chart/themes.ts (PATCH 02b) and components (PATCH 06).
import './styles/tokens.css';

// P1: Initialize JS-based viewport height before mount (TG WebView needs --app-vh)
initViewportVars();

const el = document.getElementById('app');
if (!el) throw new Error('Mount target #app not found');
// ADR-0116 Ф1.1: словник мови глядача — до монтування, щоб перший кадр уже був його мовою (без миготіння джерела)
void initI18n()
  .catch((err) => console.error('[i18n] INIT_FAILED — показ мовою джерела', err))
  .then(() => mount(App, { target: el }));

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
