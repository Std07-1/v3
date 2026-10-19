// ADR-0116 Ф1.1: мова глядача — стан, словник, t() / tn() / msg() для компонентів і рендерів.
// Словник завантажується ДО монтування застосунку (main.ts): перший кадр уже мовою глядача, без миготіння джерела.
// Реєстр і словники — дані в `i18n/` у корені репо (той самий словник читає сервер, ADR-0116 §4.2); мови, крім
// джерела, — окремими шматками збірки, тож кожна нова мова не важчає для глядачів інших мов.
// `t()` читає руни, тому розмітка, що його викликає, перемальовується сама, коли мова зміниться.

import registryJson from '../../../../i18n/languages.json';
import {
  type Dictionary,
  type LanguageInfo,
  type LanguageRegistry,
  type Params,
  isRegistered,
  localeTagFor,
  resolveInitialLang,
  translate,
  translatePlural,
} from './translate';

export const LANG_REGISTRY: LanguageRegistry = registryJson;
const QUERY_PARAM = 'lang';
const STORAGE_KEY = 'v3.lang';
const DICTIONARY_DIR = '../../../../i18n/';
const DICTIONARY_LOADERS = import.meta.glob<Dictionary>(
  ['../../../../i18n/*.json', '!../../../../i18n/languages.json'],
  { import: 'default' },
);
const SOURCE_LANGUAGE: LanguageInfo = LANG_REGISTRY.languages.find((l) => l.code === LANG_REGISTRY.source)!;

class I18nState {
  lang = $state(LANG_REGISTRY.source);
  dictionary = $state.raw<Dictionary | null>(null);
}

const i18n = new I18nState();
const reportedMissing = new Set<string>();
const langListeners = new Set<(lang: string) => void>();

export function currentLang(): string {
  return i18n.lang;
}

/** Текст мовою глядача; ключ — український текст (ADR-0116 L1), лише літерал: його бачить тест паритету. */
export function t(key: string, params?: Params): string {
  return translate(i18n.dictionary, key, params, reportMissing);
}

/** Множина: `tn(n, '{n} тиждень|{n} тижні|{n} тижнів')` — форми джерела в порядку його категорій. */
export function tn(n: number, forms: string, params?: Params): string {
  return translatePlural(i18n.dictionary, SOURCE_LANGUAGE, i18n.lang, n, forms, params, reportMissing);
}

/** Позначка ключа для тексту з мапи за кодом: тест паритету його бачить, перекладає `t()` у мить показу. */
export function msg(key: string): string {
  return key;
}

/** Локаль дат на шкалі LWC — мова глядача, а не мова браузера (ADR-0116 §5.6). */
export function currentLocaleTag(): string {
  return localeTagFor(i18n.lang, browserLanguages());
}

/** Мова при відкритті — до монтування. Не відхиляється ніколи: збій словника лишає джерело й каже про це гучно. */
export async function initI18n(): Promise<void> {
  const initial = resolveInitialLang(LANG_REGISTRY, {
    query: new URLSearchParams(window.location.search).get(QUERY_PARAM),
    stored: readStoredLang(),
    browser: browserLanguages(),
  });
  for (const rejected of initial.ignored) console.warn(`[i18n] LANG_UNSUPPORTED ${rejected}`);
  await applyLang(initial.lang);
}

/** Вибір перемикача (§5.2): мова одразу, без перезавантаження, і запам'ятовується для наступних візитів. */
export async function setLang(code: string): Promise<void> {
  if (!isRegistered(LANG_REGISTRY, code)) {
    console.warn(`[i18n] LANG_UNSUPPORTED switch:${code}`);
    return;
  }
  writeStoredLang(code);
  dropQueryLang(); // інакше перезавантаження поверне мову з адреси, а не вибрану
  await applyLang(code);
  for (const listener of langListeners) listener(i18n.lang);
}

/** Підписка рендерів поза розміткою (шкала LWC, полотно) на зміну мови; повертає відписку. */
export function onLangChange(listener: (lang: string) => void): () => void {
  langListeners.add(listener);
  return () => langListeners.delete(listener);
}

async function applyLang(lang: string): Promise<void> {
  const dictionary = lang === LANG_REGISTRY.source ? null : await loadDictionary(lang);
  const applied = dictionary === null ? LANG_REGISTRY.source : lang;
  i18n.dictionary = dictionary;
  i18n.lang = applied;
  document.documentElement.lang = applied;
}

async function loadDictionary(lang: string): Promise<Dictionary | null> {
  const loader = DICTIONARY_LOADERS[`${DICTIONARY_DIR}${lang}.json`];
  if (loader === undefined) {
    console.error(`[i18n] DICTIONARY_MISSING lang=${lang} — показ мовою джерела`);
    return null;
  }
  try {
    return await loader();
  } catch (err) {
    console.error(`[i18n] DICTIONARY_LOAD_FAILED lang=${lang} — показ мовою джерела`, err);
    return null;
  }
}

function reportMissing(key: string): void {
  const id = `${i18n.lang}\u0000${key}`;
  if (reportedMissing.has(id)) return;
  reportedMissing.add(id);
  console.warn(`[i18n] MISSING lang=${i18n.lang} key=${JSON.stringify(key)} — показано джерело`);
}

function readStoredLang(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // сховище недоступне (приватне вікно) — вибір просто не пам'ятається
  }
}

function writeStoredLang(code: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, code);
  } catch (err) {
    console.warn('[i18n] LANG_NOT_STORED — вибір діє до перезавантаження', err);
  }
}

function dropQueryLang(): void {
  const url = new URL(window.location.href);
  if (!url.searchParams.has(QUERY_PARAM)) return;
  url.searchParams.delete(QUERY_PARAM);
  try {
    history.replaceState(history.state, '', url.toString());
  } catch (err) {
    console.warn('[i18n] LANG_PARAM_STRIP_FAILED — перезавантаження поверне мову з адреси', err);
  }
}

function browserLanguages(): readonly string[] {
  if (typeof navigator === 'undefined') return []; // тести рушія в node без DOM
  return navigator.languages?.length ? navigator.languages : [navigator.language];
}
