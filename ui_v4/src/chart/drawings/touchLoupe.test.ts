// src/chart/drawings/touchLoupe.test.ts — ADR-0112 D6: лупа не закриває точку, не виходить за панель і бере з
// полотна саме ту ділянку, що навколо кільця.
import { describe, expect, it } from 'vitest';
import { LOUPE_GAP_PX, LOUPE_SIZE_PX, loupePlacement, sourceRect } from './touchLoupe';

const PANE = { width: 315, height: 700 };

describe('loupePlacement', () => {
  it('над точкою, по центру, з відступом — точку не закриває', () => {
    const box = loupePlacement({ x: 150, y: 400 }, PANE);
    expect(box).toEqual({ left: 150 - LOUPE_SIZE_PX / 2, top: 400 - LOUPE_GAP_PX - LOUPE_SIZE_PX });
    expect(box.top + LOUPE_SIZE_PX).toBeLessThan(400 - 11); // низ лупи вище кільця (радіус 11)
  });

  it('згори нема місця (під рядками HUD) — лупа під точкою', () => {
    const box = loupePlacement({ x: 150, y: 120 }, PANE, 50);
    expect(box.top).toBe(120 + LOUPE_GAP_PX);
  });

  it('по горизонталі в межах панелі біля обох країв', () => {
    expect(loupePlacement({ x: 4, y: 400 }, PANE).left).toBe(0);
    expect(loupePlacement({ x: 312, y: 400 }, PANE).left).toBe(PANE.width - LOUPE_SIZE_PX);
  });

  it('низька панель — лупа під точкою, але не нижче краю', () => {
    const box = loupePlacement({ x: 150, y: 100 }, { width: 315, height: 180 });
    expect(box.top).toBe(180 - LOUPE_SIZE_PX);
  });
});

describe('sourceRect', () => {
  const rect = { left: 0, top: 50, width: 375, height: 700 };

  it('ділянка навколо точки в пікселях полотна (DPR 3)', () => {
    expect(sourceRect(rect, { width: 1125, height: 2100 }, 200, 450, 52)).toEqual({
      sx: (200 - 26) * 3,
      sy: (450 - 26 - 50) * 3,
      sw: 52 * 3,
      sh: 52 * 3,
    });
  });

  it('квадрат повз полотно або полотно без розміру — копіювати нічого', () => {
    expect(sourceRect(rect, { width: 1125, height: 2100 }, 200, 10, 52)).toBeNull();
    expect(sourceRect({ left: 0, top: 0, width: 0, height: 0 }, { width: 0, height: 0 }, 10, 10, 52)).toBeNull();
  });
});
