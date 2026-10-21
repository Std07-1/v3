import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pageUrl, renderOgVariant, type OgMeta } from './ogVariants';

const UI_ROOT = resolve(__dirname, '..');
const META = JSON.parse(readFileSync(resolve(__dirname, 'og_meta.json'), 'utf8')) as OgMeta;
const INDEX = readFileSync(resolve(UI_ROOT, 'index.html'), 'utf8');
const REGISTRY = JSON.parse(readFileSync(resolve(UI_ROOT, '..', 'i18n', 'languages.json'), 'utf8')) as {
    languages: Array<{ code: string }>;
};

function metaContent(html: string, attr: 'property' | 'name', key: string): string | undefined {
    return html.match(new RegExp(`<meta\\s+${attr}="${key}"\\s+content="([^"]*)"`))?.[1];
}

describe('прев\'ю посилання мовою ?lang= (ADR-0116 Ф3)', () => {
    it('кожна мова реєстру i18n має текст прев\'ю і картинку в public/, мова за замовчуванням — серед них', () => {
        const registered = REGISTRY.languages.map((language) => language.code).sort();
        expect(Object.keys(META.langs).sort()).toEqual(registered);
        expect(META.langs[META.default]).toBeDefined();
        for (const [code, lang] of Object.entries(META.langs)) {
            expect(existsSync(resolve(UI_ROOT, 'public', lang.image)), `${code}: ${lang.image}`).toBe(true);
        }
    });

    it('варіант мови — її заголовок, опис, картинка, адреса з ?lang= і <html lang>', () => {
        const html = renderOgVariant(INDEX, META, 'ru');
        expect(html).toContain('<html lang="ru"');
        expect(metaContent(html, 'property', 'og:title')).toBe(META.langs.ru.title);
        expect(metaContent(html, 'property', 'og:description')).toBe(META.langs.ru.description);
        expect(metaContent(html, 'name', 'twitter:description')).toBe(META.langs.ru.description);
        expect(metaContent(html, 'name', 'description')).toBe(META.langs.ru.description);
        expect(metaContent(html, 'property', 'og:locale')).toBe('ru_RU');
        expect(metaContent(html, 'property', 'og:url')).toBe('https://aione-smc.com/?lang=ru');
        expect(metaContent(html, 'property', 'og:image')).toBe('https://aione-smc.com/og-ru.png');
        expect(metaContent(html, 'name', 'twitter:image')).toBe('https://aione-smc.com/og-ru.png');
    });

    it('мова за замовчуванням — корінь сайту без ?lang', () => {
        const html = renderOgVariant(INDEX, META, META.default);
        expect(metaContent(html, 'property', 'og:url')).toBe(META.site_url);
        expect(pageUrl(META, META.default)).toBe(META.site_url);
    });

    it('решта сторінки не змінюється — лише мета-теги прев\'ю й <html lang>', () => {
        const strip = (html: string) =>
            html.replace(/<meta\s+(?:property|name)="[^"]*"\s+content="[^"]*"\s*\/?>/g, '').replace(/<html lang="[^"]*"/, '');
        for (const code of Object.keys(META.langs)) {
            expect(strip(renderOgVariant(INDEX, META, code))).toBe(strip(INDEX));
        }
    });

    it('лапки й амперсанд у тексті не ламають атрибут', () => {
        const meta: OgMeta = { ...META, langs: { ...META.langs, en: { ...META.langs.en, title: 'Gold & "silver" <x>' } } };
        const html = renderOgVariant(INDEX, meta, 'en');
        expect(metaContent(html, 'property', 'og:title')).toBe('Gold &amp; &quot;silver&quot; &lt;x&gt;');
    });

    it('тегу прев\'ю нема — помилка збірки, а не тихий пропуск', () => {
        const broken = INDEX.replace(/<meta\s+property="og:locale"\s+content="[^"]*"\s*\/>/, '');
        expect(() => renderOgVariant(broken, META, 'uk')).toThrow(/og:locale/);
    });
});
