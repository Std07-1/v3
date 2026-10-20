// Таймер «до закриття бару» в CommandRail (X28: лише відображення різниці `close − now`, межі бару — бекенд).

import { t } from './i18n/i18n.svelte';

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

export type BarCountdownInput = {
  /** Секунди TF (мітка → `TF_LABEL_TO_S`); 0 — невідомий TF. */
  tfS: number;
  /** Якір сітки M1…D1: відкриття останньої свічки цього TF mod tf (сезонний якір H4/D1 — з даних бекенда). */
  anchorMs: number | null;
  /** Кінець бару від бекенда (`frame.bar_close_ms`). */
  barCloseMs: number | null;
  /** Календарний TF (W1/MN): межі не кратні tf — лише `barCloseMs`, без арифметики сітки (ADR-0115 §4.4). */
  calendar: boolean;
  nowMs: number;
};

/** Мілісекунди до закриття бару або null («—:—»). */
export function barCountdownMs({ tfS, anchorMs, barCloseMs, calendar, nowMs }: BarCountdownInput): number | null {
  if (calendar || barCloseMs != null) {
    return barCloseMs != null && barCloseMs > nowMs ? barCloseMs - nowMs : null;
  }
  const tfMs = tfS * 1000;
  if (tfMs === 0 || anchorMs == null) return null;
  const bucketOpen = Math.floor((nowMs - anchorMs) / tfMs) * tfMs + anchorMs;
  const remain = bucketOpen + tfMs - nowMs;
  // Bounded: 0..tfMs always (wallclock anchor guarantees this); guard anyway.
  return remain >= 0 && remain <= tfMs ? remain : null;
}

/** ≥ 1 доби — «3д 04г» (W1/MN, рішення власника 19.10); менше — «хв:сс», як завжди було на M1…D1. */
export function formatBarCountdown(remainMs: number | null): string {
  if (remainMs == null || !Number.isFinite(remainMs) || remainMs < 0) return '—:—';
  if (remainMs >= DAY_MS) {
    const days = Math.floor(remainMs / DAY_MS);
    const hours = Math.floor((remainMs % DAY_MS) / HOUR_MS);
    return t('{days}д {hours}г', { days, hours: String(hours).padStart(2, '0') });
  }
  const totalSec = Math.floor(remainMs / 1000);
  const mm = Math.floor(totalSec / 60);
  const ss = totalSec % 60;
  return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}
