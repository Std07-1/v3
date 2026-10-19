import { describe, expect, it, vi } from 'vitest';
import registryJson from '../../../../i18n/languages.json';
import {
  type Dictionary,
  type LanguageRegistry,
  format,
  localeTagFor,
  resolveInitialLang,
  translate,
  translatePlural,
} from './translate';

const REGISTRY: LanguageRegistry = registryJson;
const UK = REGISTRY.languages.find((language) => language.code === 'uk')!;
const WEEKS = '{n} тиждень|{n} тижні|{n} тижнів';

describe('resolveInitialLang (ADR-0116 §5.2)', () => {
  const none = { query: null, stored: null, browser: [] as string[] };

  it('?lang= перемагає збережений вибір і браузер', () => {
    expect(resolveInitialLang(REGISTRY, { query: 'ru', stored: 'en', browser: ['uk-UA'] }))
      .toEqual({ lang: 'ru', source: 'query', ignored: [] });
  });

  it('без ?lang= — збережений вибір перемикача', () => {
    expect(resolveInitialLang(REGISTRY, { ...none, stored: 'en', browser: ['uk-UA'] }).lang).toBe('en');
  });

  it('мова браузера — перша зареєстрована за порядком, за основним підтегом', () => {
    expect(resolveInitialLang(REGISTRY, { ...none, browser: ['de-DE', 'ru-RU', 'uk-UA'] }))
      .toEqual({ lang: 'ru', source: 'browser', ignored: [] });
  });

  it('нічого не збіглося — англійська', () => {
    expect(resolveInitialLang(REGISTRY, { ...none, browser: ['de-DE', 'pl'] }))
      .toEqual({ lang: 'en', source: 'fallback', ignored: [] });
  });

  it('незареєстрована мова в адресі чи сховищі — не тиша, а пропуск зі звітом', () => {
    expect(resolveInitialLang(REGISTRY, { query: 'de', stored: 'xx', browser: ['uk-UA'] }))
      .toEqual({ lang: 'uk', source: 'browser', ignored: ['query:de', 'stored:xx'] });
  });
});

describe('translate (ADR-0116 L1, L3)', () => {
  const en: Dictionary = { 'Сповіщати': 'Set alert', '{tf}: кілзона активна': '{tf}: killzone active' };

  it('мова-джерело — ключ як є, без звіту про пропуск', () => {
    const onMissing = vi.fn();
    expect(translate(null, 'Сповіщати', undefined, onMissing)).toBe('Сповіщати');
    expect(onMissing).not.toHaveBeenCalled();
  });

  it('переклад з плейсхолдером', () => {
    expect(translate(en, '{tf}: кілзона активна', { tf: 'H1' }, vi.fn())).toBe('H1: killzone active');
  });

  it('перекладу нема — текст джерела і звіт', () => {
    const onMissing = vi.fn();
    expect(translate(en, 'Ринок закрито', undefined, onMissing)).toBe('Ринок закрито');
    expect(onMissing).toHaveBeenCalledWith('Ринок закрито');
  });

  it('плейсхолдер без параметра лишається видимим', () => {
    expect(format('Стоп {price} · ціль {target}', { price: '4380.00' })).toBe('Стоп 4380.00 · ціль {target}');
  });
});

describe('translatePlural', () => {
  it('джерело uk: one / few / many і 11–14 — «many»', () => {
    const forms = [1, 3, 5, 11, 21, 24].map((n) => translatePlural(null, UK, 'uk', n, WEEKS, undefined, vi.fn()));
    expect(forms).toEqual(['1 тиждень', '3 тижні', '5 тижнів', '11 тижнів', '21 тиждень', '24 тижні']);
  });

  it('переклад бере категорію мови перекладу', () => {
    const en: Dictionary = { [WEEKS]: { one: '{n} week', other: '{n} weeks' } };
    expect(translatePlural(en, UK, 'en', 1, WEEKS, undefined, vi.fn())).toBe('1 week');
    expect(translatePlural(en, UK, 'en', 5, WEEKS, undefined, vi.fn())).toBe('5 weeks');
  });

  it('множини нема в словнику — форма джерела і звіт', () => {
    const onMissing = vi.fn();
    expect(translatePlural({}, UK, 'en', 2, WEEKS, undefined, onMissing)).toBe('2 тижні');
    expect(onMissing).toHaveBeenCalledWith(WEEKS);
  });
});

describe('localeTagFor (ADR-0116 §5.6)', () => {
  it('тег браузера тієї самої мови зберігається, інакше — код мови', () => {
    expect(localeTagFor('en', ['uk-UA', 'en-GB'])).toBe('en-GB');
    expect(localeTagFor('ru', ['uk-UA'])).toBe('ru');
  });
});
