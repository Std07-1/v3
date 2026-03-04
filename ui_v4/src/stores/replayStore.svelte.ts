// src/stores/replayStore.svelte.ts
// ADR-0027: Client-side replay engine.
// Pure client state. Жодних WS round-trips. SSOT для replay UI.
// Svelte 5 runes ($state / $derived) — файл має розширення .svelte.ts.

import type { Candle, SmcData, SmcZone, SmcSwing, SmcLevel } from '../types';

// ────────────────────── Constants ──────────────────────
export const SPEED_OPTIONS = [1, 2, 5, 10, 25, 50] as const;
export type ReplaySpeed = typeof SPEED_OPTIONS[number];

const EMPTY_SMC: SmcData = { zones: [], swings: [], levels: [], trend_bias: null };

// ────────────────────── Store class ──────────────────────

class ReplayStore {
    // ── Reactive state (Svelte 5 runes) ──
    active = $state(false);
    allCandles = $state<Candle[]>([]);
    allSmcData = $state<SmcData>(EMPTY_SMC);
    cursorIndex = $state(0);          // 0..allCandles.length — скільки барів видно
    playing = $state(false);
    speed = $state<ReplaySpeed>(1);

    // ── Internal (non-reactive) ──
    private _timer: ReturnType<typeof setInterval> | null = null;
    /** Callback: ChartPane підписується щоб оновити chart при тіку */
    private _onTick: ((index: number) => void) | null = null;

    // ── Derived getters ──

    /** Timestamp поточного курсора (для TF sync) */
    get posMs(): number {
        if (this.cursorIndex > 0 && this.cursorIndex <= this.allCandles.length) {
            return this.allCandles[this.cursorIndex - 1].t_ms;
        }
        return 0;
    }

    get totalBars(): number {
        return this.allCandles.length;
    }

    /** Видимий зріз свічок (0..cursorIndex) */
    get visibleCandles(): Candle[] {
        return this.allCandles.slice(0, this.cursorIndex);
    }

    /** SMC дані, відфільтровані до курсора */
    get visibleSmcData(): SmcData {
        const ms = this.posMs;
        if (!ms || !this.active) return this.allSmcData;
        return {
            zones: this.allSmcData.zones.filter(z => z.start_ms <= ms),
            swings: this.allSmcData.swings.filter(s => s.time_ms <= ms),
            levels: this.allSmcData.levels.filter(l => !l.t_ms || l.t_ms <= ms),
            trend_bias: this.allSmcData.trend_bias,
        };
    }

    /** Прогрес 0..1 для scrubber */
    get progress(): number {
        return this.allCandles.length > 0
            ? this.cursorIndex / this.allCandles.length
            : 0;
    }

    // ── Lifecycle ──

    /** Активувати replay з поточними даними чарта */
    enter(candles: Candle[], smcData: SmcData): void {
        this.allCandles = candles;
        this.allSmcData = smcData;
        this.cursorIndex = candles.length;    // Починаємо з кінця (всі бари видні)
        this.playing = false;
        this.active = true;
        this._stopTimer();
    }

    /** Вийти з replay → відновити нормальний режим */
    exit(): void {
        this._stopTimer();
        this.playing = false;
        this.active = false;
        this.allCandles = [];
        this.allSmcData = EMPTY_SMC;
        this.cursorIndex = 0;
    }

    // ── Navigation ──

    /** Встановити курсор по індексу (scrubber drag) */
    seekIndex(idx: number): void {
        this.cursorIndex = Math.max(0, Math.min(idx, this.allCandles.length));
    }

    /** Встановити курсор по timestamp (TF switch sync) */
    seekMs(ms: number): void {
        this.seekIndex(this._findIndexByMs(ms));
    }

    /** Крок вперед на n барів */
    stepForward(n = 1): void {
        const prev = this.cursorIndex;
        this.seekIndex(this.cursorIndex + n);
        if (this.cursorIndex >= this.allCandles.length) {
            this.pause();
        }
        this._onTick?.(this.cursorIndex);
    }

    /** Крок назад на n барів */
    stepBack(n = 1): void {
        this.seekIndex(this.cursorIndex - n);
        this._onTick?.(this.cursorIndex);
    }

    // ── Playback ──

    play(): void {
        if (this.cursorIndex >= this.allCandles.length) {
            // Якщо в кінці — перемотати на початок
            this.cursorIndex = Math.min(1, this.allCandles.length);
        }
        this.playing = true;
        this._startTimer();
    }

    pause(): void {
        this.playing = false;
        this._stopTimer();
    }

    togglePlay(): void {
        if (this.playing) this.pause();
        else this.play();
    }

    setSpeed(speed: ReplaySpeed): void {
        this.speed = speed;
        if (this.playing) {
            this._stopTimer();
            this._startTimer();
        }
    }

    /** Циклічно переключити швидкість (для кнопки) */
    nextSpeed(): void {
        const idx = SPEED_OPTIONS.indexOf(this.speed);
        const next = SPEED_OPTIONS[(idx + 1) % SPEED_OPTIONS.length];
        this.setSpeed(next);
    }

    /** Підписка на tick (ChartPane використовує для update chart) */
    onTick(cb: (index: number) => void): void {
        this._onTick = cb;
    }

    /** Оновити дані при TF switch під час replay */
    updateDataForNewTf(candles: Candle[], smcData: SmcData): void {
        const prevMs = this.posMs;
        this.allCandles = candles;
        this.allSmcData = smcData;
        // Знайти еквівалентну позицію в новому TF
        if (prevMs > 0) {
            this.seekMs(prevMs);
        } else {
            this.cursorIndex = candles.length;
        }
    }

    // ── Private ──

    /** Binary search: скільки барів мають t_ms <= ms */
    private _findIndexByMs(ms: number): number {
        const arr = this.allCandles;
        let lo = 0, hi = arr.length;
        while (lo < hi) {
            const mid = (lo + hi) >>> 1;
            if (arr[mid].t_ms <= ms) lo = mid + 1;
            else hi = mid;
        }
        return lo;
    }

    private _startTimer(): void {
        this._stopTimer();
        const intervalMs = Math.max(16, Math.round(1000 / this.speed));
        this._timer = setInterval(() => {
            if (this.cursorIndex >= this.allCandles.length) {
                this.pause();
                return;
            }
            this.cursorIndex++;
            this._onTick?.(this.cursorIndex);
        }, intervalMs);
    }

    private _stopTimer(): void {
        if (this._timer !== null) {
            clearInterval(this._timer);
            this._timer = null;
        }
    }
}

// ── Singleton export ──
export const replayStore = new ReplayStore();
