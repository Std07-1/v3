// src/lib/touchDrawingFlag.ts
// ADR-0112 D1 → D10 (власник 15.10: «щоб і для інших»): малювання на телефоні — для всіх за замовчуванням.
// `?draw=0` вимикає на цьому пристрої (пам'ятається в localStorage), `?draw=1` вмикає знову.

const STORAGE_KEY = 'v4_touch_drawing';
const DISABLED = '0';
const QUERY_PARAM = 'draw';

type FlagStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

/** localStorage цього браузера або null: сам доступ кидає в sandbox / приватному режимі — тоді гучно в консолі. */
export function deviceStorage(): FlagStorage | null {
  try {
    return window.localStorage;
  } catch (err) {
    console.warn('[touchDrawing] TOUCH_DRAWING_STORAGE_UNAVAILABLE', err);
    return null;
  }
}

/** Рішення з адреси: true — увімкнути, false — вимкнути, null — параметра нема (лишити як є). */
export function touchDrawingFromQuery(search: string): boolean | null {
  const value = new URLSearchParams(search).get(QUERY_PARAM);
  if (value === '1') return true;
  if (value === '0') return false;
  return null;
}

/** Чи ввімкнено малювання на цьому пристрої (з урахуванням `?draw=` поточної адреси): так, якщо його тут не вимикали
 *  `?draw=0`. Сховище недоступне (приватний режим) — діє лише параметр цієї сторінки, з попередженням у консолі. */
export function resolveTouchDrawing(search: string, storage: FlagStorage | null): boolean {
  const fromQuery = touchDrawingFromQuery(search);
  try {
    if (fromQuery === false) storage?.setItem(STORAGE_KEY, DISABLED);
    else if (fromQuery === true) storage?.removeItem(STORAGE_KEY);
    if (fromQuery !== null) return fromQuery;
    return storage?.getItem(STORAGE_KEY) !== DISABLED; // «1» з пробного періоду — теж увімкнено
  } catch (err) {
    console.warn('[touchDrawing] TOUCH_DRAWING_FLAG_STORAGE_FAILED — лише ?draw цієї сторінки', err);
    return fromQuery !== false;
  }
}
