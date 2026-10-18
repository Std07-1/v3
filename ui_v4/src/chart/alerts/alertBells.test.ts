// src/chart/alerts/alertBells.test.ts — ADR-0114 S8: розкладка дзвіночків на лініях-сповіщеннях.
import { describe, expect, it } from 'vitest';
import type { Drawing } from '../../types';
import type { AlertWire } from '../../lib/alerts/alertClient';
import { layoutBells, sameBells, type Bell, type BellsInput } from './alertBells';

const NOW = 1_792_086_371_000;
const SYMBOL = 'XAU/USD';

function hline(id: string, price: number, meta?: Drawing['meta']): Drawing {
  return { id, type: 'hline', points: [{ t_ms: NOW as Drawing['points'][number]['t_ms'], price }], meta };
}

function alert(drawingId: string, overrides: Partial<AlertWire> = {}): AlertWire {
  return {
    alert_id: `a-${drawingId}`,
    drawing_id: drawingId,
    symbol: SYMBOL,
    level: 0,
    tf_label: 'M30',
    active_from_ms: NOW,
    created_at_ms: NOW,
    version: 1,
    state: 'armed',
    side: 'below',
    fired: null,
    delivery: null,
    ...overrides,
  };
}

const FIRED = { at_ms: NOW + 1, price: 1, via: 'tick', direction: 'up', after_gap: false } as const;

/** ціна → y: 1 пункт = 1 px від 1000 донизу (ціна 900 → y 100). */
function input(overrides: Partial<BellsInput>): BellsInput {
  return {
    drawings: [],
    alerts: [],
    symbol: SYMBOL,
    degraded: [],
    pushGone: false,
    priceToY: (price) => 1000 - price,
    paneHeight: 500,
    topReserved: 0,
    ...overrides,
  };
}

describe('layoutBells', () => {
  it('test_bell_only_for_drawing_with_alert_of_current_symbol', () => {
    const bells = layoutBells(
      input({
        drawings: [hline('own', 900), hline('no-alert', 850), hline('other-symbol', 800)],
        alerts: [alert('own'), alert('other-symbol', { symbol: 'EUR/USD' }), alert('gone')],
      }),
    );
    expect(bells.map((b) => b.drawingId)).toEqual(['own']);
    expect(bells[0].y).toBe(100);
  });

  it('test_bell_skipped_for_figure_without_alert_level', () => {
    const trend: Drawing = { ...hline('t', 900), type: 'trend' };
    expect(layoutBells(input({ drawings: [trend], alerts: [alert('t')] }))).toEqual([]);
  });

  it('test_bell_position_follows_drawing_price_not_alert_level', () => {
    const bells = layoutBells(input({ drawings: [hline('d', 900)], alerts: [alert('d', { level: 700 })] }));
    expect(bells[0].y).toBe(100);
  });

  it('test_bell_y_is_rounded', () => {
    const bells = layoutBells(input({ drawings: [hline('d', 899.6)], alerts: [alert('d')] }));
    expect(bells[0].y).toBe(100);
  });

  it('test_bell_skipped_when_line_off_pane', () => {
    const offscreen = (y: number | null) =>
      layoutBells(input({ drawings: [hline('d', 900)], alerts: [alert('d')], priceToY: () => y }));
    expect(offscreen(null)).toEqual([]);
    expect(offscreen(Number.NaN)).toEqual([]);
    expect(offscreen(501)).toEqual([]);
    expect(offscreen(-20)).toEqual([]);
    expect(offscreen(500)).toHaveLength(1);
  });

  it('test_bell_hidden_under_top_buttons_row_including_half_height', () => {
    const at = (y: number) =>
      layoutBells(input({ drawings: [hline('d', 900)], alerts: [alert('d')], priceToY: () => y, topReserved: 26 }));
    expect(at(20)).toEqual([]);
    expect(at(32)).toEqual([]); // центр нижче рядка, але верхня половина дзвіночка ще в ньому
    expect(at(33)).toHaveLength(1);
  });

  it('test_bell_negative_top_reserved_treated_as_zero', () => {
    const at = (y: number) =>
      layoutBells(input({ drawings: [hline('d', 900)], alerts: [alert('d')], priceToY: () => y, topReserved: -40 }));
    expect(at(6)).toEqual([]);
    expect(at(7)).toHaveLength(1);
  });

  it('test_bell_state_armed_fired_undelivered', () => {
    const drawings = [hline('a', 900), hline('f', 850), hline('p', 800), hline('u', 750)];
    const alerts = [
      alert('a'),
      alert('f', { state: 'fired', fired: FIRED, delivery: 'delivered' }),
      alert('p', { state: 'fired', fired: FIRED, delivery: 'pending' }),
      alert('u', { state: 'fired', fired: FIRED, delivery: 'pending' }),
    ];
    const healthy = layoutBells(input({ drawings, alerts }));
    expect(healthy.map((b) => b.state)).toEqual(['armed', 'fired', 'fired', 'fired']);
    const telegramDown = layoutBells(input({ drawings, alerts, degraded: ['telegram_unreachable'] }));
    expect(telegramDown.map((b) => b.state)).toEqual(['armed', 'fired', 'undelivered', 'undelivered']);
  });

  it('test_bell_color_from_role_then_legacy_hex_then_neutral', () => {
    const bells = layoutBells(
      input({
        drawings: [hline('role', 900, { colorRole: 'bear' }), hline('hex', 850, { color: '#123456' }), hline('none', 800)],
        alerts: [alert('role'), alert('hex'), alert('none')],
      }),
    );
    expect(bells.map((b) => b.color)).toEqual([
      'var(--bear, #ed4554)',
      '#123456',
      'var(--drawing-base-color, #c8cdd6)',
    ]);
  });
});

describe('sameBells', () => {
  const bell: Bell = { drawingId: 'd', y: 100, state: 'armed', color: '#fff' };

  it('test_same_bells_equal_lists', () => {
    expect(sameBells([bell], [{ ...bell }])).toBe(true);
    expect(sameBells([], [])).toBe(true);
  });

  it('test_same_bells_detects_any_field_or_length_change', () => {
    expect(sameBells([bell], [])).toBe(false);
    expect(sameBells([bell], [{ ...bell, drawingId: 'x' }])).toBe(false);
    expect(sameBells([bell], [{ ...bell, y: 101 }])).toBe(false);
    expect(sameBells([bell], [{ ...bell, state: 'fired' }])).toBe(false);
    expect(sameBells([bell], [{ ...bell, color: '#000' }])).toBe(false);
  });
});

describe('layoutBells — push (ADR-0114 Rev 2)', () => {
  it('test_bell_undelivered_for_failed_or_pending_while_push_is_stuck', () => {
    const drawings = [hline('f', 900), hline('p', 850), hline('d', 800)];
    const alerts = [
      alert('f', { state: 'fired', fired: FIRED, delivery: 'failed' }),
      alert('p', { state: 'fired', fired: FIRED, delivery: 'pending' }),
      alert('d', { state: 'fired', fired: FIRED, delivery: 'delivered' }),
    ];
    const states = (over: Partial<BellsInput>) => layoutBells(input({ drawings, alerts, ...over })).map((b) => b.state);
    expect(states({})).toEqual(['undelivered', 'fired', 'fired']);
    expect(states({ pushGone: true })).toEqual(['undelivered', 'undelivered', 'fired']);
    expect(states({ degraded: ['push_unreachable'] })).toEqual(['undelivered', 'undelivered', 'fired']);
  });
});
