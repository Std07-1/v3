// src/lib/alerts/alertUi.test.ts — ADR-0114 S8: дія для лінії, підписи стану, текст спливного повідомлення.
import { describe, expect, it } from 'vitest';
import type { Drawing } from '../../types';
import type { AlertWire } from './alertClient';
import type { AlertsView } from './alertsController';
import {
  alertActionFor,
  alertErrorText,
  alertOfDrawing,
  alertStateText,
  alertTitle,
  firedToastText,
} from './alertUi';

const NOW = 1_792_086_371_000;
const SYMBOL = 'XAU/USD';

function drawing(id: string, type: Drawing['type'] = 'hline', price = 4380): Drawing {
  return { id, type, points: [{ t_ms: NOW as Drawing['points'][number]['t_ms'], price }] };
}

function wire(overrides: Partial<AlertWire> = {}): AlertWire {
  return {
    alert_id: 'a-1',
    drawing_id: 'd-1',
    symbol: SYMBOL,
    level: 4380,
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

function fired(direction: 'up' | 'down', delivery: AlertWire['delivery'] = 'delivered'): AlertWire {
  return wire({
    state: 'fired',
    fired: { at_ms: NOW + 1, price: 4380.4, via: 'tick', direction, after_gap: false },
    delivery,
  });
}

function view(overrides: Partial<AlertsView> = {}): AlertsView {
  return {
    status: 'ready',
    channel: 'telegram',
    pushSupport: null,
    pushGone: false,
    pushEnabling: false,
    alerts: [],
    degraded: [],
    lastError: null,
    ...overrides,
  };
}

describe('alertActionFor', () => {
  it('test_action_without_drawing_or_symbol_is_null', () => {
    expect(alertActionFor(null, view(), SYMBOL)).toBeNull();
    expect(alertActionFor(drawing('d-1'), view(), null)).toBeNull();
  });

  it('test_action_for_figure_without_alert_level_is_null', () => {
    expect(alertActionFor(drawing('d-1', 'trend'), view(), SYMBOL)).toBeNull();
  });

  it('test_action_when_service_not_usable_is_null', () => {
    for (const status of ['unknown', 'unavailable', 'disabled'] as const) {
      expect(alertActionFor(drawing('d-1'), view({ status }), SYMBOL)).toBeNull();
    }
  });

  it('test_action_when_unpaired_is_hint_to_pair', () => {
    const action = alertActionFor(drawing('d-1'), view({ status: 'unpaired' }), SYMBOL);
    expect(action).toMatchObject({ kind: 'pair', on: false, hintOnly: true });
    expect(action?.label).toContain('☰ → Сповіщення');
  });

  it('test_action_ready_without_alert_enables', () => {
    expect(alertActionFor(drawing('d-1'), view(), SYMBOL)).toEqual({
      kind: 'enable',
      label: 'Сповіщати',
      on: false,
      hintOnly: false,
    });
  });

  it('test_action_armed_alert_disables_and_is_on', () => {
    const action = alertActionFor(drawing('d-1'), view({ alerts: [wire()] }), SYMBOL);
    expect(action).toEqual({ kind: 'disable', label: 'Сповіщає — вимкнути', on: true, hintOnly: false });
  });

  it('test_action_fired_alert_rearms', () => {
    const action = alertActionFor(drawing('d-1'), view({ alerts: [fired('up')] }), SYMBOL);
    expect(action).toMatchObject({ kind: 'rearm', on: false, hintOnly: false });
  });

  it('test_action_alert_of_other_symbol_with_same_drawing_id_is_ignored', () => {
    const action = alertActionFor(drawing('d-1'), view({ alerts: [wire({ symbol: 'EUR/USD' })] }), SYMBOL);
    expect(action?.kind).toBe('enable');
  });
});

describe('alertOfDrawing', () => {
  it('test_alert_of_drawing_matches_symbol_and_id', () => {
    const own = wire({ alert_id: 'own' });
    const alerts = [wire({ alert_id: 'other-id', drawing_id: 'd-2' }), wire({ alert_id: 'other-sym', symbol: 'GER30' }), own];
    expect(alertOfDrawing(alerts, SYMBOL, 'd-1')).toBe(own);
    expect(alertOfDrawing(alerts, SYMBOL, 'missing')).toBeNull();
  });
});

describe('texts', () => {
  it('test_title_uses_configured_digits_else_magnitude', () => {
    expect(alertTitle(wire({ level: 4380 }), 2)).toBe('XAU/USD 4380.00');
    expect(alertTitle(wire({ symbol: 'USD/JPY', level: 152.1 }), 3)).toBe('USD/JPY 152.100');
    expect(alertTitle(wire({ symbol: 'EUR/USD', level: 1.0834 }), null)).toBe('EUR/USD 1.08340');
  });

  it('test_state_text_armed_waits', () => {
    expect(alertStateText(wire(), [])).toBe('чекає перетину');
  });

  it('test_state_text_fired_names_direction', () => {
    expect(alertStateText(fired('up'), [])).toBe('перетнуто знизу вгору');
    expect(alertStateText(fired('down'), [])).toBe('перетнуто згори вниз');
  });

  it('test_state_text_undelivered_only_when_pending_and_telegram_down', () => {
    expect(alertStateText(fired('up', 'pending'), ['telegram_unreachable'])).toBe(
      'перетнуто знизу вгору · Telegram не доставив',
    );
    expect(alertStateText(fired('up', 'pending'), [])).toBe('перетнуто знизу вгору');
    expect(alertStateText(fired('up', 'delivered'), ['telegram_unreachable'])).toBe('перетнуто знизу вгору');
  });

  it('test_error_text_known_code_is_human_unknown_is_raw', () => {
    expect(alertErrorText('no_price')).toBe('Сповіщення: немає ціни, щоб визначити бік — ринок закритий?');
    expect(alertErrorText('weird_code')).toBe('Сповіщення: weird_code');
  });

  it('test_toast_text_has_title_and_direction', () => {
    expect(firedToastText(fired('down'), 2)).toBe('XAU/USD 4380.00 — перетнуто згори вниз');
    expect(firedToastText(wire(), 2)).toBe('XAU/USD 4380.00');
  });
});

describe('push channel (ADR-0114 Rev 2)', () => {
  it('test_unpaired_push_device_gets_one_click_enable_or_a_reason', () => {
    const action = (support: AlertsView['pushSupport']) =>
      alertActionFor(drawing('d-1'), view({ status: 'unpaired', channel: 'push', pushSupport: support }), SYMBOL);
    expect(action('ok')).toEqual({ kind: 'enable', label: 'Сповіщати', on: false, hintOnly: false });
    expect(action('denied')).toMatchObject({ hintOnly: true, label: expect.stringContaining('дозвольте') });
    expect(action('ios_home_screen')).toMatchObject({ hintOnly: true, label: expect.stringContaining('початковий екран') });
    expect(action('unsupported')).toMatchObject({ hintOnly: true, label: expect.stringContaining('не вміє') });
    expect(action(null)).toMatchObject({ hintOnly: true });
  });

  it('test_gone_subscription_turns_every_action_into_resubscribe', () => {
    const action = alertActionFor(drawing('d-1'), view({ channel: 'push', pushGone: true, alerts: [wire()] }), SYMBOL);
    expect(action).toMatchObject({ kind: 'resubscribe', on: false, hintOnly: false });
  });

  it('test_state_text_names_undelivered_and_waiting_for_resubscribe', () => {
    expect(alertStateText({ ...fired('up'), delivery: 'failed' }, [])).toBe('перетнуто знизу вгору · не доставлено');
    expect(alertStateText(fired('up', 'pending'), [], true)).toBe('перетнуто знизу вгору · чекає, доки ввімкнуть сповіщення');
    expect(alertStateText(fired('up', 'pending'), ['push_unreachable'])).toBe('перетнуто знизу вгору · ще не доставлено');
    expect(alertStateText(fired('up', 'delivered'), [], true)).toBe('перетнуто знизу вгору');
  });

  it('test_push_error_codes_have_human_text', () => {
    for (const code of ['push_denied', 'push_failed', 'no_service_worker', 'devices_per_ip', 'devices_full']) {
      expect(alertErrorText(code)).not.toBe(`Сповіщення: ${code}`);
    }
  });
});
