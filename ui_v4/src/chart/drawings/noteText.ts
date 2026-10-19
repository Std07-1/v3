// src/chart/drawings/noteText.ts
// ADR-0111 §3.7 (власник 19.10: «Так усе»): текст нотатки — власним полем вводу (layout/NoteEditor.svelte), не
// системним вікном браузера. ПК — поле на місці нотатки; телефон — поле там, де капсула, над клавіатурою. Один рядок,
// до NOTE_MAX_CHARS символів; порожній — нотатки нема (нова) або текст не змінюється (редагування). Змінити: ПК —
// подвійний клік по нотатці або правий клік → «Змінити текст»; телефон — виділити й ✎ у капсулі.

/** Запит renderer-а до поля вводу. */
export interface NoteTextRequest {
  /** Якір нотатки — лівий нижній кут чіпа, px вікна (поле стає поверх нотатки). */
  readonly clientX: number;
  readonly clientY: number;
  readonly initial: string;
  /** Нотатку ставлять пальцем (курсор капсули): поле — на місці капсули, над клавіатурою. */
  readonly touch: boolean;
  /** Непорожній нормалізований текст (normalizeNoteText): нотатка стає / текст змінюється (undoable). */
  readonly commit: (text: string) => void;
}

/** Найдовша нотатка, символів: підпис («BSL», «H4 OB», «чекаю зняття Азії»), не абзац. */
export const NOTE_MAX_CHARS = 60;

/** Подвійний клік — другий клік по тій самій нотатці не пізніше, мс. */
export const NOTE_DOUBLE_CLICK_MS = 400;

/** Текст із поля: пробіли й переноси — один пробіл, краї обрізано, не довше за NOTE_MAX_CHARS (символів, не
 *  UTF-16-одиниць — емодзі не рветься); порожній — null. */
export function normalizeNoteText(raw: string): string | null {
  const oneLine = raw.replace(/\s+/g, ' ').trim();
  const text = Array.from(oneLine).slice(0, NOTE_MAX_CHARS).join('').trim();
  return text === '' ? null : text;
}

/** Подвійний клік по нотатці: true — другий клік тієї самої нотатки в межах `windowMs`; `id` null — клік не по
 *  нотатці (лічба з нуля). Після спрацювання — з нуля (третій клік не «подвійний»). */
export function createDoubleClickTracker(windowMs = NOTE_DOUBLE_CLICK_MS): (id: string | null, atMs: number) => boolean {
  let last: { id: string; atMs: number } | null = null;
  return (id, atMs) => {
    const isDouble = id !== null && last !== null && last.id === id && atMs - last.atMs <= windowMs;
    last = isDouble || id === null ? null : { id, atMs };
    return isDouble;
  };
}
