// src/app/frameRouter.test.ts
// Регресія 06.09.2026 (NAS100 D1): HUD брав ціну з scrollback-кадру.
import { describe, it, expect } from 'vitest';
import { get } from 'svelte/store';
import { frameCarriesCurrentPrice, handleWSFrame, resetFrameRouter, serverConfig, uiWarnings } from './frameRouter';

describe('frameCarriesCurrentPrice', () => {
  it('scrollback_не_несе_поточну_ціну', () => {
    // Сервер віддає межу inclusive → останній бар scrollback = найстаріший
    // догружений. На закритому ринку HUD залипав на ньому назавжди.
    expect(frameCarriesCurrentPrice('scrollback')).toBe(false);
  });

  it('full_і_delta_несуть_поточну_ціну', () => {
    expect(frameCarriesCurrentPrice('full')).toBe(true);
    expect(frameCarriesCurrentPrice('delta')).toBe(true);
  });

  it('replay_несе_поточну_ціну_свого_моменту', () => {
    expect(frameCarriesCurrentPrice('replay')).toBe(true);
  });

  it('невідомий_або_відсутній_тип_не_блокує_HUD', () => {
    // Denylist, не allowlist: новий тип кадру зі свічками не має тихо
    // залишити HUD без ціни — блокуємо тільки те, що явно є історією.
    expect(frameCarriesCurrentPrice(undefined)).toBe(true);
    expect(frameCarriesCurrentPrice('warming')).toBe(true);
  });
});

describe('serverConfig.displayBudget — бюджет Focus з сервера (ADR-0028 v2 §3.4)', () => {
  const configFrame = (seq: number, displayBudget?: unknown) => ({
    type: 'render_frame',
    frame_type: 'config',
    config: { symbols: ['XAU/USD'], tfs: ['M15'], ...(displayBudget !== undefined ? { display_budget: displayBudget } : {}) },
    meta: { schema_v: 'ui_v4_v2', seq, server_ts_ms: 0 },
  });

  it('кадр конфігу з display_budget задає бюджет', () => {
    resetFrameRouter();
    handleWSFrame(configFrame(1, { zones_per_side: 2, structure_max: 5, levels_per_side: 4 }));
    expect(get(serverConfig).displayBudget).toEqual({ perSide: 2, structureMax: 5, levelsPerSide: 4 });
  });

  it('кривий display_budget — попередження і без бюджету (рендер лишає типовий)', () => {
    resetFrameRouter();
    handleWSFrame(configFrame(1, { zones_per_side: 'три' }));
    expect(get(serverConfig).displayBudget).toBeUndefined();
    expect(get(uiWarnings).some((w) => w.details.includes('display_budget'))).toBe(true);
  });

  it('сервер без SMC (поля немає) — без бюджету і без попередження', () => {
    resetFrameRouter();
    handleWSFrame(configFrame(1));
    expect(get(serverConfig)).toEqual({ symbols: ['XAU/USD'], tfs: ['M15'] });
    expect(get(uiWarnings)).toEqual([]);
  });
});
