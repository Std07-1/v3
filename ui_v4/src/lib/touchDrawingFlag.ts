// src/lib/touchDrawingFlag.ts
// ADR-0112 D1: пробний режим малювання на телефоні — лише на пристрої, де його ввімкнули адресою `?draw=1`
// (`?draw=0` вимикає). Стан тримається в localStorage цього браузера; решта відвідувачів не бачить нічого нового.

const STORAGE_KEY = 'v4_touch_drawing';
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

/** Чи ввімкнено пробний режим на цьому пристрої (з урахуванням `?draw=` поточної адреси). Сховище недоступне
 *  (приватний режим) — діє лише параметр цієї сторінки, з попередженням у консолі. */
export function resolveTouchDrawing(search: string, storage: FlagStorage | null): boolean {
  const fromQuery = touchDrawingFromQuery(search);
  try {
    if (fromQuery === true) storage?.setItem(STORAGE_KEY, '1');
    else if (fromQuery === false) storage?.removeItem(STORAGE_KEY);
    if (fromQuery !== null) return fromQuery;
    return storage?.getItem(STORAGE_KEY) === '1';
  } catch (err) {
    console.warn('[touchDrawing] TOUCH_DRAWING_FLAG_STORAGE_FAILED — лише ?draw цієї сторінки', err);
    return fromQuery === true;
  }
}
