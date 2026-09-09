// src/app/frameRouter.test.ts
// Регресія 06.09.2026 (NAS100 D1): HUD брав ціну з scrollback-кадру.
import { describe, it, expect } from 'vitest';
import { frameCarriesCurrentPrice } from './frameRouter';

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
