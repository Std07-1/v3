// src/chart/drawings/timeMap.ts
// ADR-0082 D6: час ↔ дробовий logical-індекс для cross-TF рендеру малювань.
//
// LWC `timeToCoordinate(t)` повертає null, якщо t НЕ існує серед барів TF —
// якір, поставлений на M15 (10:15), зникав на H1 (бар лише 10:00). Fix:
// мапимо t у ДРОБОВИЙ logical-індекс (бінарний пошук сусідніх барів +
// лінійна інтерполяція), далі `logicalToCoordinate(idx)` — координата існує
// для будь-якого t. За межами даних — екстраполяція за кроком крайніх барів
// (це ж відкриває draw-into-future). Інверсія — для точних якорів при
// малюванні/перетягуванні (sub-bar точність замість квантування до бару).
//
// Pure math, без I/O і без LWC-типів — тестується юнітами як специфікація.

/** t (сек) → дробовий індекс у відсортованому масиві часів барів (сек).
 *  Порожньо → null. Один бар → 0 (спред невідомий, все мапиться на нього).
 *  Всередині: i + (t-t[i])/(t[i+1]-t[i]). За краями: лінійна екстраполяція
 *  за кроком крайньої пари барів. */
export function timeToFractionalIndex(
  timesSec: readonly number[],
  tSec: number,
): number | null {
  const n = timesSec.length;
  if (n === 0) return null;
  if (n === 1) return 0;

  const first = timesSec[0];
  const last = timesSec[n - 1];

  if (tSec <= first) {
    const step = timesSec[1] - first;
    return step > 0 ? (tSec - first) / step : 0;
  }
  if (tSec >= last) {
    const step = last - timesSec[n - 2];
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

/** Дробовий індекс → час (сек). Інверсія timeToFractionalIndex з тими самими
 *  крайовими правилами (екстраполяція за кроком крайньої пари). */
export function fractionalIndexToTime(
  timesSec: readonly number[],
  idx: number,
): number | null {
  const n = timesSec.length;
  if (n === 0) return null;
  if (n === 1) return timesSec[0];

  if (idx <= 0) {
    const step = timesSec[1] - timesSec[0];
    return timesSec[0] + idx * step;
  }
  if (idx >= n - 1) {
    const step = timesSec[n - 1] - timesSec[n - 2];
    return timesSec[n - 1] + (idx - (n - 1)) * step;
  }

  const i = Math.floor(idx);
  const frac = idx - i;
  return timesSec[i] + frac * (timesSec[i + 1] - timesSec[i]);
}
