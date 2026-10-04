// src/chart/crosshairPin.ts
//
// ADR-0108 S1: закріплене мобільне перехрестя — чиста машина станів жесту (без DOM і LWC).
//
//   idle ── дотик одним пальцем ──▶ arming ── 300 мс без руху ≥ 8 px ──▶ pinned (перехрестя під пальцем, графік стоїть)
//     ▲        рух ≥ 8 px / щипок ──┘                                     │
//     └──────── короткий тап (CP4) або щипок (CP5) ◀─────────────────────┘
//
// У pinned перехрестя лишається після відпускання (CP2); новий дотик будь-де рухає його 1:1 від точки, де воно стояло
// (CP3). Дотики, що почались у pinned, належать перехрестю — оболонка не пускає їх до LWC (CP6).
// Оболонка (longPressLock.ts) перекладає DOM-події в ці функції і виконує повернуті дії.

/** Утримання перед закріпленням. 300 мс = власний поріг long-tap у LWC; коротше — тапи стають довгими. */
export const LONG_PRESS_MS = 300;

/** Тремтіння пальця, яке ще не рух, для розпізнавання довгого тапу. 8 px = Material touch-slop (8 dp). */
export const MOVE_THRESHOLD_PX = 8;

/**
 * Тап-вихід у режимі перехрестя (CP4): палець зрушив не більше ніж на стільки. Менше за MOVE_THRESHOLD_PX навмисно —
 * точна наводка на 3–7 px не має гасити перехрестя (на стенді 04.10 швидка наводка +3 px виходила з режиму).
 */
export const TAP_SLOP_PX = 2;

export interface Point {
    x: number;
    y: number;
}

/** Розмір області графіка (без шкал) у CSS px — перехрестя не виходить за неї. */
export interface Bounds {
    width: number;
    height: number;
}

interface Gesture {
    fingerStart: Point;
    crossStart: Point;
    startMs: number;
    /** Палець зрушив більше ніж на TAP_SLOP_PX — це наводка, не тап */
    moved: boolean;
}

export type PinState =
    | { mode: 'idle' }
    | { mode: 'arming'; start: Point; last: Point }
    | {
          mode: 'pinned';
          cross: Point;
          gesture: Gesture | null;
          /** Палець довгого тапу, що закріпив перехрестя, ще на екрані: LWC бачив його початок, кінець теж віддаємо LWC */
          initial: boolean;
      };

export interface PinEffects {
    /** Запустити таймер довгого тапу (оболонка спершу скасовує попередній) */
    arm?: boolean;
    /** Скасувати таймер довгого тапу */
    disarm?: boolean;
    /** Увійти в режим перехрестя: заморозити графік */
    enter?: boolean;
    /** Вийти: зняти перехрестя, розморозити графік */
    exit?: boolean;
    /** Поставити перехрестя в цю точку (координати контейнера графіка) */
    crosshair?: Point;
    /** Поставити перехрестя повторно після того, як LWC обробить кінець дотику */
    reassert?: Point;
    /** Подія належить перехрестю — не пускати до LWC */
    block?: boolean;
}

export interface PinStep {
    state: PinState;
    effects: PinEffects;
}

export const IDLE: PinState = { mode: 'idle' };

function clampToBounds(p: Point, bounds: Bounds): Point {
    return {
        x: Math.min(Math.max(p.x, 0), Math.max(0, bounds.width - 1)),
        y: Math.min(Math.max(p.y, 0), Math.max(0, bounds.height - 1)),
    };
}

function movedBeyond(a: Point, b: Point, slopPx: number): boolean {
    return Math.abs(a.x - b.x) > slopPx || Math.abs(a.y - b.y) > slopPx;
}

/** Дотик. `touches` — скільки пальців на екрані після нього. */
export function pinTouchStart(state: PinState, touches: number, finger: Point, nowMs: number): PinStep {
    if (touches !== 1) {
        // Щипок: вихід (CP5); подію не блокуємо — зум веде LWC
        if (state.mode === 'pinned') return { state: IDLE, effects: { exit: true, disarm: true } };
        return { state: IDLE, effects: { disarm: true } };
    }
    if (state.mode === 'pinned') {
        // Новий дотик у режимі перехрестя: тягнення 1:1 від поточної точки (CP3) або тап-вихід (CP4)
        return {
            state: {
                ...state,
                initial: false,
                gesture: { fingerStart: finger, crossStart: state.cross, startMs: nowMs, moved: false },
            },
            effects: { block: true },
        };
    }
    return { state: { mode: 'arming', start: finger, last: finger }, effects: { arm: true } };
}

/** Спрацював таймер довгого тапу: перехрестя під пальцем, графік стоїть (CP1). */
export function pinArmFired(state: PinState): PinStep {
    if (state.mode !== 'arming') return { state, effects: {} };
    const at = state.last;
    return {
        state: {
            mode: 'pinned',
            cross: at,
            // Палець довгого тапу: рух від точки закріплення = рух під пальцем; його відпускання — не тап
            gesture: { fingerStart: at, crossStart: at, startMs: Number.NEGATIVE_INFINITY, moved: true },
            initial: true,
        },
        effects: { enter: true, crosshair: at },
    };
}

/** Рух пальця. */
export function pinTouchMove(state: PinState, touches: number, finger: Point, bounds: Bounds): PinStep {
    if (touches !== 1) {
        if (state.mode === 'pinned') return { state: IDLE, effects: { exit: true } };
        if (state.mode === 'arming') return { state: IDLE, effects: { disarm: true } };
        return { state, effects: {} };
    }
    if (state.mode === 'arming') {
        // Палець поплив до 300 мс — це звичайна прокрутка графіка, не довгий тап
        if (movedBeyond(finger, state.start, MOVE_THRESHOLD_PX)) return { state: IDLE, effects: { disarm: true } };
        return { state: { ...state, last: finger }, effects: {} };
    }
    if (state.mode === 'pinned') {
        const g = state.gesture;
        if (!g) return { state, effects: { block: true } };
        const cross = clampToBounds(
            { x: g.crossStart.x + finger.x - g.fingerStart.x, y: g.crossStart.y + finger.y - g.fingerStart.y },
            bounds,
        );
        const moved = g.moved || movedBeyond(finger, g.fingerStart, TAP_SLOP_PX);
        return { state: { ...state, cross, gesture: { ...g, moved } }, effects: { crosshair: cross, block: true } };
    }
    return { state, effects: {} };
}

/** Палець відпущено (`cancelled` — система перервала дотик: ніколи не тап). */
export function pinTouchEnd(state: PinState, nowMs: number, cancelled: boolean = false): PinStep {
    if (state.mode === 'arming') return { state: IDLE, effects: { disarm: true } };
    if (state.mode !== 'pinned' || !state.gesture) return { state, effects: {} };
    const ownedByPin = !state.initial;
    const isTap = !cancelled && !state.gesture.moved && nowMs - state.gesture.startMs < LONG_PRESS_MS;
    if (isTap) return { state: IDLE, effects: { exit: true, block: ownedByPin } };
    // Перехрестя лишається (CP2); кінець дотику довгого тапу віддаємо LWC, тож точку ставимо ще раз після нього
    return {
        state: { ...state, gesture: null, initial: false },
        effects: { block: ownedByPin, reassert: state.cross },
    };
}
