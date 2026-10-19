// Формат таймера «до закриття бару» в CommandRail (X28: лише відображення різниці `close − now`).
// ≥ 1 доби — «3д 04г» (W1/MN, ADR-0115 §4.4, рішення власника 19.10); менше — «хв:сс», як завжди було на M1…D1.

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

export function formatBarCountdown(remainMs: number | null): string {
  if (remainMs == null || !Number.isFinite(remainMs) || remainMs < 0) return '—:—';
  if (remainMs >= DAY_MS) {
    const days = Math.floor(remainMs / DAY_MS);
    const hours = Math.floor((remainMs % DAY_MS) / HOUR_MS);
    return `${days}д ${String(hours).padStart(2, '0')}г`;
  }
  const totalSec = Math.floor(remainMs / 1000);
  const mm = Math.floor(totalSec / 60);
  const ss = totalSec % 60;
  return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}
