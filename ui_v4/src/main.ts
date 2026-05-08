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

export default app;
