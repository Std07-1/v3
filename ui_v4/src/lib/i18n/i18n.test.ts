// ADR-0116 Ф1.2: мова глядача від відкриття до перемикача — справжні словники з `i18n/`, браузер підмінено.
import { afterEach, describe, expect, it, vi } from 'vitest';

type I18nModule = typeof import('./i18n.svelte');

function stubBrowser(search: string, stored: string | null, languages: string[]) {
  const storage = new Map<string, string>(stored === null ? [] : [['v3.lang', stored]]);
  const location = { search, href: `http://localhost/${search}` };
  vi.stubGlobal('window', { location });
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  vi.stubGlobal('history', {
    state: null,
    replaceState: (_state: unknown, _title: string, url: string) => {
      location.href = url;
    },
  });
  vi.stubGlobal('document', { documentElement: { lang: 'uk' } });
  vi.stubGlobal('navigator', { languages, language: languages[0] });
  return { storage, location };
}

async function freshI18n(): Promise<I18nModule> {
  vi.resetModules(); // стан мови — модульний синглтон; кожен тест — як нове відкриття сторінки
  return import('./i18n.svelte');
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('мова глядача (ADR-0116 §5.2)', () => {
  it('?lang=ru — словник до монтування, <html lang>, вибір не запам\'ятовується', async () => {
    const browser = stubBrowser('?lang=ru', null, ['uk-UA']);
    const i18n = await freshI18n();
    await i18n.initI18n();
    expect(i18n.currentLang()).toBe('ru');
    expect(i18n.t('Мова')).toBe('Язык');
    expect(document.documentElement.lang).toBe('ru');
    expect(browser.storage.has('v3.lang')).toBe(false);
  });

  it('без адреси й вибору — мова браузера; джерело показується як є', async () => {
    stubBrowser('', null, ['uk-UA', 'en-US']);
    const i18n = await freshI18n();
    await i18n.initI18n();
    expect(i18n.currentLang()).toBe('uk');
    expect(i18n.t('Мова')).toBe('Мова');
  });

  it('перемикач: мова одразу, запам\'ятовується, ?lang= зникає з адреси, підписники чують', async () => {
    const browser = stubBrowser('?lang=ru', null, ['uk-UA']);
    const i18n = await freshI18n();
    await i18n.initI18n();
    const heard: string[] = [];
    i18n.onLangChange((lang) => heard.push(lang));

    await i18n.setLang('en');

    expect(i18n.t('Мова')).toBe('Language');
    expect(document.documentElement.lang).toBe('en');
    expect(browser.storage.get('v3.lang')).toBe('en');
    expect(browser.location.href).toBe('http://localhost/');
    expect(heard).toEqual(['en']);
  });

  it('збережений вибір перемагає мову браузера на наступному відкритті', async () => {
    stubBrowser('', 'en', ['ru-RU']);
    const i18n = await freshI18n();
    await i18n.initI18n();
    expect(i18n.currentLang()).toBe('en');
  });

  it('незареєстрована мова в перемикачі — гучно і без зміни', async () => {
    const browser = stubBrowser('', null, ['uk-UA']);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const i18n = await freshI18n();
    await i18n.initI18n();
    await i18n.setLang('de');
    expect(i18n.currentLang()).toBe('uk');
    expect(browser.storage.has('v3.lang')).toBe(false);
    expect(warn).toHaveBeenCalledWith('[i18n] LANG_UNSUPPORTED switch:de');
  });
});
