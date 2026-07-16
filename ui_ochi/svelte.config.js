import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

// Явний svelte config: svelte-check ≥4.5 більше не витягує Svelte-конфіг із
// функціонального defineConfig(() => …) у vite.config.ts. Наявність цього файлу
// дає typecheck стабільне джерело preprocess незалежно від версії svelte-check.
export default {
    preprocess: vitePreprocess(),
};
