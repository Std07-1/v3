// ADR-0116 Ф1.1: чисті функції мовного шару UI — мова глядача при відкритті, переклад ключа, множина, локаль дат.
// Ключ перекладу — сам український текст (L1): мова-джерело словника не має, її «переклад» — тотожність.
// Стан (поточна мова, завантажений словник) живе в i18n.svelte.ts; тут — лише функції без побічних ефектів:
// про пропущений переклад вони кажуть через `onMissing`, а наскільки голосно — вирішує викликач (L3).

export interface LanguageInfo {
  readonly code: string;
  readonly name: string; // назва мови нею самою — для перемикача
  readonly plural: readonly string[]; // категорії CLDR у порядку форм ключа-джерела (uk: one, few, many)
}

export interface LanguageRegistry {
  readonly source: string; // мова-джерело: ключі словників — її тексти
  readonly fallback: string; // мова, коли ні адреса, ні вибір, ні браузер не дали зареєстрованої
  readonly languages: readonly LanguageInfo[];
}

/** Переклад: рядок або форми множини за категоріями CLDR мови перекладу. */
export type Message = string | Readonly<Partial<Record<Intl.LDMLPluralRule, string>>>;
export type Dictionary = Readonly<Record<string, Message>>;
export type Params = Readonly<Record<string, string | number>>;
export type LangSource = 'query' | 'stored' | 'browser' | 'fallback';

export interface InitialLang {
  readonly lang: string;
  readonly source: LangSource;
  readonly ignored: readonly string[]; // `?lang=` чи збережений вибір, яких немає в реєстрі — показати гучно
}

/** Роздільник форм множини в ключі: «{n} тиждень|{n} тижні|{n} тижнів». */
export const PLURAL_SEPARATOR = '|';

const PLACEHOLDER = /\{(\w+)\}/g;

export function primarySubtag(tag: string): string {
  return tag.trim().toLowerCase().split('-')[0];
}

export function isRegistered(registry: LanguageRegistry, code: string): boolean {
  return registry.languages.some((language) => language.code === code);
}

/** Мова при відкритті: `?lang=` (лише цей візит) → вибір перемикача → мова браузера → fallback (ADR-0116 §5.2). */
export function resolveInitialLang(
  registry: LanguageRegistry,
  input: { query: string | null; stored: string | null; browser: readonly string[] },
): InitialLang {
  const ignored: string[] = [];
  for (const [raw, source] of [[input.query, 'query'], [input.stored, 'stored']] as const) {
    if (raw === null || raw === '') continue;
    const code = primarySubtag(raw);
    if (isRegistered(registry, code)) return { lang: code, source, ignored };
    ignored.push(`${source}:${raw}`);
  }
  const browserLang = input.browser.map(primarySubtag).find((code) => isRegistered(registry, code));
  if (browserLang !== undefined) return { lang: browserLang, source: 'browser', ignored };
  return { lang: registry.fallback, source: 'fallback', ignored };
}

/** Локаль дат (шкала LWC): тег браузера тієї самої мови (`en-GB` лишається `en-GB`), інакше сам код мови. */
export function localeTagFor(lang: string, browser: readonly string[]): string {
  return browser.find((tag) => primarySubtag(tag) === lang) ?? lang;
}

/** `{name}` → значення параметра; плейсхолдер без параметра лишається видимим, а не зникає тихо. */
export function format(template: string, params?: Params): string {
  if (params === undefined) return template;
  return template.replace(PLACEHOLDER, (whole, name: string) => (name in params ? String(params[name]) : whole));
}

/** Текст ключа. Словника нема (мова-джерело) — сам ключ; перекладу нема — ключ і `onMissing` (L3). */
export function translate(
  dictionary: Dictionary | null,
  key: string,
  params: Params | undefined,
  onMissing: (key: string) => void,
): string {
  if (dictionary === null) return format(key, params);
  const message = dictionary[key];
  if (typeof message === 'string') return format(message, params);
  onMissing(key);
  return format(key, params);
}

/** Форма множини для `n` (параметр `{n}` додається сам). Ключ — форми джерела через «|» у порядку `source.plural`. */
export function translatePlural(
  dictionary: Dictionary | null,
  source: LanguageInfo,
  lang: string,
  n: number,
  forms: string,
  params: Params | undefined,
  onMissing: (key: string) => void,
): string {
  const withCount = { ...params, n };
  const message = dictionary === null ? undefined : dictionary[forms];
  if (message !== undefined && typeof message !== 'string') {
    const form = message[new Intl.PluralRules(lang).select(n)] ?? message.other;
    if (form !== undefined) return format(form, withCount);
  }
  if (dictionary !== null) onMissing(forms);
  return format(sourceForm(source, n, forms), withCount);
}

function sourceForm(source: LanguageInfo, n: number, forms: string): string {
  const variants = forms.split(PLURAL_SEPARATOR);
  const index = source.plural.indexOf(new Intl.PluralRules(source.code).select(n));
  // Дробове n (категорія «other» у східнослов'янських мовах) — остання форма джерела
  return variants[index >= 0 && index < variants.length ? index : variants.length - 1];
}
