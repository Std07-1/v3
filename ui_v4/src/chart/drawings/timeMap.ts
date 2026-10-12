// src/chart/drawings/timeMap.ts
// ADR-0082 D6: час ↔ дробовий logical-індекс для cross-TF рендеру малювань.
//
// LWC `timeToCoordinate(t)` повертає null, якщо t НЕ існує серед барів TF —
// якір, поставлений на M15 (10:15), зникав на H1 (бар лише 10:00). Fix:
// мапимо t у ДРОБОВИЙ logical-індекс (бінарний пошук сусідніх барів +
// лінійна інтерполяція), далі `fractionalLogicalToX` — координата існує
// для будь-якого t. За межами даних — екстраполяція за кроком крайніх барів
// (це ж відкриває draw-into-future).
//
// ⚠ LWC 5.1.0 `logicalToCoordinate` рахує лише ЦІЛИЙ індекс: `_internal_indexToCoordinate`
// → `if (!isInteger(index)) return 0` (dist mjs). Дробовий напряму = x=0, фігура на лівому
// краї (12.10.2026, відтворено: тренд з M15 10:15→13:45 на H1 — вертикаль на x=0). Тому —
// лише через `fractionalLogicalToX` (інтерполяція між двома цілими).
//
// Pure math, без I/O і без LWC-типів — тестується юнітами як специфікація.

/** ADR-0083: типовий крок барів (сек) = МЕДІАНА останніх `sample` дельт.
 *  Стійкий до session/weekend gap-ів на краю (остання пара може бути 48h
 *  замість 1h — медіана 32 дельт її відкидає). Використовується для
 *  екстраполяції за краями даних (draw-into-future): якорі не «злітають»
 *  від крайового gap-у і не зміщуються з приходом нових live-барів. */
export function medianStep(timesSec: readonly number[], sample = 32): number {
  const n = timesSec.length;
  if (n < 2) return 0;
  const from = Math.max(1, n - sample);
  const deltas: number[] = [];
  for (let i = from; i < n; i++) deltas.push(timesSec[i] - timesSec[i - 1]);
  deltas.sort((a, b) => a - b);
  return deltas[Math.floor(deltas.length / 2)];
}

/** t (сек) → дробовий індекс у відсортованому масиві часів барів (сек).
 *  Порожньо → null. Один бар → 0 (спред невідомий, все мапиться на нього).
 *  Всередині: i + (t-t[i])/(t[i+1]-t[i]). За краями: лінійна екстраполяція
 *  за `stepSec` (медіанний крок від viклика; 0/відсутній → крок крайньої
 *  пари як fallback). Симетрично до fractionalIndexToTime — той самий
 *  stepSec гарантує точний roundtrip. */
export function timeToFractionalIndex(
  timesSec: readonly number[],
  tSec: number,
  stepSec = 0,
): number | null {
  const n = timesSec.length;
  if (n === 0) return null;
  if (n === 1) return 0;

  const first = timesSec[0];
  const last = timesSec[n - 1];

  if (tSec <= first) {
    const step = stepSec > 0 ? stepSec : timesSec[1] - first;
    return step > 0 ? (tSec - first) / step : 0;
  }
  if (tSec >= last) {
    const step = stepSec > 0 ? stepSec : last - timesSec[n - 2];
    return step > 0 ? n - 1 + (tSec - last) / step : n - 1;
  }

  // Бінарний пошук: найбільший i з timesSec[i] <= tSec (invariant: lo відповідає йому).
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (timesSec[mid] <= tSec) lo = mid;
    else hi = mid;
  }
  const span = timesSec[hi] - timesSec[lo];
  return span > 0 ? lo + (tSec - timesSec[lo]) / span : lo;
}

/** Дробовий logical → x: інтерполяція між двома ЦІЛИМИ індексами, бо `integerToX` (LWC
 *  `logicalToCoordinate`) дає координату лише для цілого (дробовий → 0, див. шапку). null — цілий
 *  індекс без координати (порожня шкала); бракує лише сусіда справа — координата лівого. */
export function fractionalLogicalToX(idx: number, integerToX: (index: number) => number | null): number | null {
  const i0 = Math.floor(idx);
  const c0 = integerToX(i0);
  if (c0 === null) return null;
  const frac = idx - i0;
  if (frac === 0) return c0;
  const c1 = integerToX(i0 + 1);
  return c1 === null ? c0 : c0 + frac * (c1 - c0);
}

/** Час якоря (сек), зсунутий на `bars` барів цієї сітки зі збереженням хвилин усередині бару: якір з M15
 *  (10:15), зсунутий на H1 на +1 бар, стає 11:15, а не 11:00. Зсув — від бару-власника на ціле число барів
 *  плюс та сама відстань від його відкриття, обмежена кроком сітки: через вихідні якір не падає в неторговий
 *  час (частка ІНТЕРВАЛУ разом з гепом поставила б Пт 18:30 +3 бари на Сб 22:00 — рецензія 12.10).
 *  `bars` = 0 — час без змін (клік без руху нічого не переписує, і на сітці з одного бару теж).
 *  Перетягування тіла фігури — через цю функцію, а не через x → час: x якоря поза сіткою TF ненадійний
 *  (S0 12.10.2026 — x=0 переписував час від лівого краю). */
export function shiftTimeByBars(
  timesSec: readonly number[],
  tSec: number,
  bars: number,
  stepSec = 0,
): number | null {
  if (bars === 0) return tSec;
  const idx = timeToFractionalIndex(timesSec, tSec, stepSec);
  if (idx === null) return null;
  const owner = Math.floor(idx);
  const ownerTime = fractionalIndexToTime(timesSec, owner, stepSec);
  const targetTime = fractionalIndexToTime(timesSec, owner + bars, stepSec);
  if (ownerTime === null || targetTime === null) return null;
  const step = stepSec > 0 ? stepSec : medianStep(timesSec);
  const offset = Math.min(Math.max(tSec - ownerTime, 0), Math.max(step, 0));
  return targetTime + offset;
}

/** Дробовий індекс → час (сек). Інверсія timeToFractionalIndex з тими самими
 *  крайовими правилами (stepSec медіанний; fallback — крок крайньої пари). */
export function fractionalIndexToTime(
  timesSec: readonly number[],
  idx: number,
  stepSec = 0,
): number | null {
  const n = timesSec.length;
  if (n === 0) return null;
  if (n === 1) return timesSec[0];

  if (idx <= 0) {
    const step = stepSec > 0 ? stepSec : timesSec[1] - timesSec[0];
    return timesSec[0] + idx * step;
  }
  if (idx >= n - 1) {
    const step = stepSec > 0 ? stepSec : timesSec[n - 1] - timesSec[n - 2];
    return timesSec[n - 1] + (idx - (n - 1)) * step;
  }

  const i = Math.floor(idx);
  const frac = idx - i;
  return timesSec[i] + frac * (timesSec[i + 1] - timesSec[i]);
}
