import { mount } from 'svelte';
import App from './App.svelte';
import { initViewportVars } from './app/viewport';

// P1: Initialize JS-based viewport height before mount (TG WebView needs --app-vh)
initViewportVars();

const el = document.getElementById('app');
if (!el) throw new Error('Mount target #app not found');
const app = mount(App, { target: el });

export default app;
