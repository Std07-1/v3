import { mount } from 'svelte';
import App from './App.svelte';

// Mobile hardening: 2026-03-28 (DPR cap, touch-action, dvh, CORS)
const el = document.getElementById('app');
if (!el) throw new Error('Mount target #app not found');
const app = mount(App, { target: el });

export default app;
