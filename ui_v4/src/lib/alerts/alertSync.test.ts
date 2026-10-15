// src/lib/alerts/alertSync.test.ts — ADR-0114 §4.6: узгодження фігур цього браузера зі сповіщеннями сервера.
import { describe, expect, it } from 'vitest';
import type { Drawing } from '../../types';
import { alertLevelOf } from '../../chart/drawings/tools';
import type { AlertWire } from './alertClient';
import { planAlertSync, type AlertSyncInput } from './alertSync';

const NOW = 1_792_086_371_000;

function hline(id: string, price: number): Drawing {
  return { id, type: 'hline', points: [{ t_ms: NOW as Drawing['points'][number]['t_ms'], price }] };
}

function alert(drawingId: string, level: number, extra: Partial<AlertWire> = {}): AlertWire {
  return {
    alert_id: `a-${drawingId}`,
    drawing_id: drawingId,
    symbol: 'XAU/USD',
    level,
    tf_label: 'M15',
    active_from_ms: NOW - 60_000,
    created_at_ms: NOW - 60_000,
    version: 1,
    state: 'armed',
    side: 'below',
    fired: null,
    delivery: null,
    ...extra,
  };
}

function plan(overrides: Partial<AlertSyncInput>) {
  return planAlertSync({
    symbol: 'XAU/USD',
    drawings: [],
    alerts: [],
    owned: new Set(),
    levelOf: alertLevelOf,
    nowMs: NOW,
    ...overrides,
  });
}

describe('planAlertSync', () => {
  it('moved line → PUT with the new level and the tf the alert was set on', () => {
    const result = plan({ drawings: [hline('d1', 4385)], alerts: [alert('d1', 4380)], owned: new Set(['d1']) });
    expect(result.puts).toEqual([
      {
        drawingId: 'd1',
        body: { symbol: 'XAU/USD', level: 4385, tf_label: 'M15', active_from_ms: null, rearm: false },
      },
    ]);
    expect(result.deletes).toEqual([]);
    expect([...result.owned]).toEqual(['d1']);
  });

  it('unchanged line (even fired) → nothing: no hidden re-arming on every save', () => {
    const fired = alert('d1', 4380, { state: 'fired', delivery: 'delivered' });
    const result = plan({ drawings: [hline('d1', 4380)], alerts: [fired], owned: new Set(['d1']) });
    expect(result.puts).toEqual([]);
    expect(result.deletes).toEqual([]);
  });

  it('deleted own line → DELETE; foreign alert (another device, not owned, no local line) untouched', () => {
    const result = plan({
      drawings: [],
      alerts: [alert('mine', 4380), alert('phone', 4400)],
      owned: new Set(['mine']),
    });
    expect(result.deletes).toEqual(['mine']);
    expect(result.puts).toEqual([]);
    expect(result.owned.size).toBe(0);
  });

  it('lost owned memory self-heals: alert whose line exists here is ours by drawing UUID', () => {
    const result = plan({ drawings: [hline('d1', 4390)], alerts: [alert('d1', 4380)], owned: new Set() });
    expect(result.puts.map((p) => p.body.level)).toEqual([4390]);
    expect([...result.owned]).toEqual(['d1']);
  });

  it('alert gone on server (switched off elsewhere) → not recreated, forgotten', () => {
    const result = plan({ drawings: [hline('d1', 4380)], alerts: [], owned: new Set(['d1']) });
    expect(result).toEqual({ puts: [], deletes: [], owned: new Set() });
  });

  it('other symbols are ignored', () => {
    const nas = alert('d1', 20000, { symbol: 'NAS100' });
    const result = plan({ drawings: [], alerts: [nas], owned: new Set(['d1']) });
    expect(result.deletes).toEqual([]);
  });

  it('line whose type can no longer alert → DELETE', () => {
    const trend: Drawing = { ...hline('d1', 4380), type: 'trend' };
    const result = plan({ drawings: [trend], alerts: [alert('d1', 4380)], owned: new Set(['d1']) });
    expect(result.deletes).toEqual(['d1']);
  });

  it('ray anchor moved into the future → PUT; past anchor or same anchor → nothing', () => {
    const ray = (anchorMs: number) => ({ level: 4380, anchorMs });
    const future = NOW + 3_600_000;
    const withAnchor = (anchorMs: number) =>
      plan({
        drawings: [hline('r1', 4380)],
        alerts: [alert('r1', 4380, { active_from_ms: NOW })],
        owned: new Set(['r1']),
        levelOf: () => ray(anchorMs),
      }).puts;
    expect(withAnchor(future).map((p) => p.body.active_from_ms)).toEqual([future]);
    expect(withAnchor(NOW - 3_600_000)).toEqual([]);
    expect(withAnchor(NOW)).toEqual([]);
    // якір рівно «зараз» — не майбутнє (як у сервера: anchor > now), навіть коли збережений момент інший
    const atNow = plan({
      drawings: [hline('r1', 4380)],
      alerts: [alert('r1', 4380, { active_from_ms: NOW - 60_000 })],
      owned: new Set(['r1']),
      levelOf: () => ray(NOW),
    });
    expect(atNow.puts).toEqual([]);
  });
});
