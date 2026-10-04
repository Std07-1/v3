// ADR-0108 S1: закріплене мобільне перехрестя — сценарії жесту.
import { describe, it, expect } from 'vitest';
import {
    IDLE,
    LONG_PRESS_MS,
    pinArmFired,
    pinTouchEnd,
    pinTouchMove,
    pinTouchStart,
    type PinState,
    type Point,
} from './crosshairPin';

const bounds = { width: 400, height: 600 };
const pt = (x: number, y: number): Point => ({ x, y });

/** Довгий тап у точці `at`: дотик → 300 мс без руху → закріплено. */
function longPress(at: Point): PinState {
    const armed = pinTouchStart(IDLE, 1, at, 0).state;
    return pinArmFired(armed).state;
}

describe('довгий тап (CP1)', () => {
    it('дотик запускає таймер, спрацювання ставить перехрестя під пальцем і заморожує графік', () => {
        const start = pinTouchStart(IDLE, 1, pt(100, 200), 0);
        expect(start.effects.arm).toBe(true);
        const fired = pinArmFired(start.state);
        expect(fired.effects).toMatchObject({ enter: true, crosshair: pt(100, 200) });
        expect(fired.state.mode).toBe('pinned');
    });

    it('палець поплив до 300 мс — це прокрутка: таймер скасовано, події не блокуються', () => {
        const armed = pinTouchStart(IDLE, 1, pt(100, 200), 0).state;
        const step = pinTouchMove(armed, 1, pt(115, 200), bounds);
        expect(step.state).toEqual(IDLE);
        expect(step.effects).toEqual({ disarm: true });
    });

    it('тремтіння менше 8 px не скасовує довгий тап, перехрестя стає туди, де палець зараз', () => {
        let s = pinTouchStart(IDLE, 1, pt(100, 200), 0).state;
        s = pinTouchMove(s, 1, pt(104, 203), bounds).state;
        expect(pinArmFired(s).effects.crosshair).toEqual(pt(104, 203));
    });
});

describe('перехрестя лишається після відпускання (CP2)', () => {
    it('відпустив палець довгого тапу — перехрестя на місці, кінець дотику йде до LWC, точку поставлено повторно', () => {
        const pinned = longPress(pt(100, 200));
        const end = pinTouchEnd(pinned, 900);
        expect(end.state.mode).toBe('pinned');
        expect(end.effects.exit).toBeUndefined();
        expect(end.effects.block).toBe(false);
        expect(end.effects.reassert).toEqual(pt(100, 200));
    });

    it('довгий тап із рухом під пальцем: перехрестя йде за пальцем і лишається там, де відпустили', () => {
        let s = longPress(pt(100, 200));
        s = pinTouchMove(s, 1, pt(140, 180), bounds).state;
        const end = pinTouchEnd(s, 1500);
        expect(end.state).toMatchObject({ mode: 'pinned', cross: pt(140, 180) });
    });
});

describe('рух 1:1 як тачпад (CP3)', () => {
    it('новий дотик будь-де і тягнення рухають перехрестя на ту саму відстань від точки, де воно стояло', () => {
        let s = pinTouchEnd(longPress(pt(100, 200)), 900).state;
        const start = pinTouchStart(s, 1, pt(300, 500), 2000);
        expect(start.effects.block).toBe(true);
        s = start.state;
        const move = pinTouchMove(s, 1, pt(305, 490), bounds);
        expect(move.effects).toMatchObject({ crosshair: pt(105, 190), block: true });
    });

    it('перехрестя не виходить за область графіка', () => {
        let s = pinTouchEnd(longPress(pt(10, 10)), 900).state;
        s = pinTouchStart(s, 1, pt(200, 300), 2000).state;
        expect(pinTouchMove(s, 1, pt(100, 200), bounds).effects.crosshair).toEqual(pt(0, 0));
        expect(pinTouchMove(s, 1, pt(900, 900), bounds).effects.crosshair).toEqual(pt(399, 599));
    });

    it('після тягнення відпускання не виходить — перехрестя лишається в новій точці', () => {
        let s = pinTouchEnd(longPress(pt(100, 200)), 900).state;
        s = pinTouchStart(s, 1, pt(300, 500), 2000).state;
        s = pinTouchMove(s, 1, pt(330, 470), bounds).state;
        const end = pinTouchEnd(s, 2600);
        expect(end.state).toMatchObject({ mode: 'pinned', cross: pt(130, 170) });
        expect(end.effects.block).toBe(true);
    });
});

describe('вихід (CP4–CP5)', () => {
    it('короткий тап без руху — перехрестя зникає, графік рухається знову', () => {
        let s = pinTouchEnd(longPress(pt(100, 200)), 900).state;
        s = pinTouchStart(s, 1, pt(250, 250), 2000).state;
        const end = pinTouchEnd(s, 2000 + LONG_PRESS_MS - 1);
        expect(end.state).toEqual(IDLE);
        expect(end.effects).toMatchObject({ exit: true, block: true });
    });

    it('утримання без руху довше за 300 мс і відпускання — не вихід', () => {
        let s = pinTouchEnd(longPress(pt(100, 200)), 900).state;
        s = pinTouchStart(s, 1, pt(250, 250), 2000).state;
        expect(pinTouchEnd(s, 2000 + LONG_PRESS_MS + 50).state.mode).toBe('pinned');
    });

    it('тремтіння в тапі до 2 px — усе ще тап', () => {
        let s = pinTouchEnd(longPress(pt(100, 200)), 900).state;
        s = pinTouchStart(s, 1, pt(250, 250), 2000).state;
        s = pinTouchMove(s, 1, pt(252, 251), bounds).state;
        expect(pinTouchEnd(s, 2100).effects.exit).toBe(true);
    });

    it('швидка точна наводка на 3 px — не вихід, перехрестя зрушило на 3 px (стенд 04.10)', () => {
        let s = pinTouchEnd(longPress(pt(100, 200)), 900).state;
        s = pinTouchStart(s, 1, pt(250, 250), 2000).state;
        const move = pinTouchMove(s, 1, pt(253, 252), bounds);
        expect(move.effects.crosshair).toEqual(pt(103, 202));
        const end = pinTouchEnd(move.state, 2150);
        expect(end.state).toMatchObject({ mode: 'pinned', cross: pt(103, 202) });
    });

    it('щипок двома пальцями — вихід, подія йде до LWC (зум)', () => {
        const pinned = pinTouchEnd(longPress(pt(100, 200)), 900).state;
        const pinch = pinTouchStart(pinned, 2, pt(200, 200), 2000);
        expect(pinch.state).toEqual(IDLE);
        expect(pinch.effects.exit).toBe(true);
        expect(pinch.effects.block).toBeUndefined();
    });

    it('скасований системою дотик — не тап, перехрестя лишається', () => {
        let s = pinTouchEnd(longPress(pt(100, 200)), 900).state;
        s = pinTouchStart(s, 1, pt(250, 250), 2000).state;
        expect(pinTouchEnd(s, 2050, true).state.mode).toBe('pinned');
    });
});

describe('звичайний графік поза режимом', () => {
    it('короткий тап без довгого тапу нічого не блокує і не ставить', () => {
        const start = pinTouchStart(IDLE, 1, pt(100, 200), 0);
        const end = pinTouchEnd(start.state, 120);
        expect(end.state).toEqual(IDLE);
        expect(end.effects).toEqual({ disarm: true });
    });
});
