// ui_v4/og/ogVariants.ts
// ADR-0116 Ф3: прев'ю посилання (Open Graph / Twitter) мовою посилання. Месенджери й соцмережі сторінку не виконують —
// читають мета-теги HTML, тож мова прев'ю може бути лише в самому файлі. Після збірки плагін пише dist/index.<код>.html
// на кожну мову з `og_meta.json` і dist/index.html мовою за замовчуванням; nginx віддає варіант за `?lang=`
// (tools/nginx/conf.d/smc_og_lang.conf). Графік від варіанта не залежить: мову інтерфейсу обирає сам застосунок.
// Тег, якого нема або який трапляється двічі, — помилка збірки, а не тихе англійське прев'ю (I5).

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Plugin } from 'vite';

export interface OgLang {
    locale: string;
    image: string;
    title: string;
    description: string;
    image_alt: string;
}

export interface OgMeta {
    site_url: string;
    default: string;
    langs: Record<string, OgLang>;
}

type MetaAttr = 'property' | 'name';

/** Значення атрибута HTML: лапки й кутові дужки тексту не ламають розмітку. */
function escapeAttr(value: string): string {
    return value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** `<meta {attr}="{key}" content="…">` → новий content; тег має бути рівно один (атрибути можуть бути на різних рядках). */
function setMeta(html: string, attr: MetaAttr, key: string, content: string): string {
    const tag = new RegExp(`(<meta\\s+${attr}="${escapeRegExp(key)}"\\s+content=")[^"]*(")`, 'g');
    const found = html.match(tag)?.length ?? 0;
    if (found !== 1) {
        throw new Error(`OG_VARIANT: <meta ${attr}="${key}"> у index.html — ${found} шт., має бути 1`);
    }
    return html.replace(tag, (_whole, head: string, tail: string) => `${head}${escapeAttr(content)}${tail}`);
}

function setHtmlLang(html: string, lang: string): string {
    const tag = /<html\s+lang="[^"]*"/;
    if (!tag.test(html)) throw new Error('OG_VARIANT: нема <html lang="…"> у index.html');
    return html.replace(tag, `<html lang="${escapeAttr(lang)}"`);
}

/** Адреса сторінки мови: для мови за замовчуванням — корінь сайту, інакше `?lang=<код>` (саме її кладуть у пост). */
export function pageUrl(meta: OgMeta, code: string): string {
    return code === meta.default ? meta.site_url : `${meta.site_url}?lang=${encodeURIComponent(code)}`;
}

/** HTML сторінки мовою `code`: мета-теги прев'ю, опис і `<html lang>`. Решта файлу (скрипти збірки) — без змін. */
export function renderOgVariant(html: string, meta: OgMeta, code: string): string {
    const lang = meta.langs[code];
    if (lang === undefined) throw new Error(`OG_VARIANT: мови ${code} нема в og_meta.json`);
    const image = new URL(lang.image, meta.site_url).href;
    let out = setHtmlLang(html, code);
    out = setMeta(out, 'name', 'description', lang.description);
    out = setMeta(out, 'property', 'og:locale', lang.locale);
    out = setMeta(out, 'property', 'og:title', lang.title);
    out = setMeta(out, 'property', 'og:description', lang.description);
    out = setMeta(out, 'property', 'og:url', pageUrl(meta, code));
    out = setMeta(out, 'property', 'og:image', image);
    out = setMeta(out, 'property', 'og:image:alt', lang.image_alt);
    out = setMeta(out, 'name', 'twitter:title', lang.title);
    out = setMeta(out, 'name', 'twitter:description', lang.description);
    out = setMeta(out, 'name', 'twitter:image', image);
    return out;
}

/** Плагін збірки: після `vite build` — index.html мовою за замовчуванням і index.<код>.html на кожну мову. */
export function ogVariants(metaFile: string): Plugin {
    let outDir = '';
    return {
        name: 'v3-og-variants',
        apply: 'build',
        configResolved(config) {
            outDir = resolve(config.root, config.build.outDir);
        },
        closeBundle() {
            const meta = JSON.parse(readFileSync(metaFile, 'utf8')) as OgMeta;
            const indexPath = resolve(outDir, 'index.html');
            const built = readFileSync(indexPath, 'utf8');
            for (const code of Object.keys(meta.langs)) {
                writeFileSync(resolve(outDir, `index.${code}.html`), renderOgVariant(built, meta, code));
            }
            writeFileSync(indexPath, renderOgVariant(built, meta, meta.default));
        },
    };
}
