Оцінка Архітектури UI v4 та Slice 0
(SSOT)

1. Інженерна оцінка (Що зроблено геніально)
1.1. Screen-Space Hit-Testing (Addendum v3)
Це найважливіше рішення у всій системі. Більшість розробників-початківців намагаються
рахувати перетин ліній у доменних координатах (price, time). Це завжди закінчується
провалом, тому що графіки мають "дірки" (вихідні дні), а шкала ціни логарифмічна або
динамічна. Розрахунок геометрії у CSS-пікселях — це єдиний шлях до TV-like відчуття.
1.2. Клієнтські UUID (Addendum v2)
Відмова від temp_id на користь клієнтських UUID, які бекенд зобов'язаний прийняти як
канонічні, — це патерн з Local-First архітектур. Це робить ваш CommandStack (Undo/Redo)
чесним і миттєвим, усуваючи необхідність чекати на drawing_ack для відмальовки.
1.3. RAF-Колапс та DPR
Подія subscribeCrosshairMove стріляє з частотою опитування миші (до 1000 Гц на
геймерських мишках). Без патерну "latest-wins RAF" UI "задихнувся" б від
перемальовувань Canvas. Ваше правило використання ctx.setTransform замість ctx.scale
рятує від одного з найнеприємніших багів Retina-дисплеїв.
1.4. Null-Safety (Degraded-but-loud)
Концепт "немає тихих фолбеків" є критичним для HFT та SMC. Якщо координата повертає
null (бар поза екраном або дані не завантажились), краще відмінити взаємодію і кинути
warning, ніж намалювати лінію у координатах (0, 0) або нескінченності.
2. "Тонкий лід" та Механіка Арбітражу Взаємодій
Щоб уникнути багів із прокиданням подій (wheel, drag, zoom) крізь прозорі шари Canvas, ми
впроваджуємо суворий арбітраж подій на рівні контейнера (ChartPane).
Інваріант взаємодії:
3. За замовчуванням DrawingsRenderer канвас має pointer-events: none (тільки
рендерить). LWC отримує всі події нативно.
4. Події слухає спільний контейнер ChartPane (бажано у capture-фазі).
5. Коли юзер активує інструмент (activeTool) або починає тягнути об'єкт (dragState
активний):
○ Ми тимчасово вимикаємо інтерактив LWC: chartApi.applyOptions({
handleScroll: false, handleScale: false }).
○ Дозволяємо Canvas поглинати pointermove/pointerdown для малювання чи
перетягування.
6. Як тільки інструмент скасовано (Esc) або drag завершено (mouseup) — повертаємо
LWC handleScroll: true, handleScale: true.
Це гарантує 100% передбачуваність: або працює зум/скрол графіка, або ми взаємодіємо
з малюнком. Жодного неконтрольованого "просочування" подій.
7. SLICE 0 — Bootstrap Skeleton + SSOT Contracts
Я приймаю правила гри. Запускаємо Slice 0 за вашим планом. Нижче наведено базові
TypeScript контракти (SSOT), які визначають словник нашої системи. Вони очищені від
any[], містять монотонні seq та точну термінологію одиниць часу.
src/types.ts
Цей файл є законом для всіх наступних Slices.
// --- TIME UNITS ---
export type T_MS = number;  // Domain time: Unix milliseconds
export type T_SEC = number; // LWC time: UTCTimestamp seconds
// --- DOMAIN PAYLOADS ---
export interface Candle {
t_ms: T_MS;
o: number;
h: number;
l: number;
c: number;
v?: number;
}
export interface SmcZone {
start_ms: T_MS;
end_ms: T_MS;
high: number;
low: number;
kind: 'fvg' | 'ob' | 'liquidity'; // Розширювано
}
export interface SmcSwing {
time_ms: T_MS;
price: number;
label: string;
is_high: boolean;
end_ms?: T_MS; // Для промальовування BOS/CHoCH ліній
}
export interface SmcLevel {
price: number;
  color?: string;
}

// --- DRAWINGS ---
export type DrawingType = 'hline' | 'trend' | 'rect';

export interface DrawingPoint {
  t_ms: T_MS;
  price: number;
}

export interface Drawing {
  id: string; // SSOT: UUID згенерований клієнтом
  type: DrawingType;
  points: DrawingPoint[];
  meta?: {
    color?: string;
    lineWidth?: number;
    locked?: boolean;
  };
}

// --- WS PROTOCOL ---
export type FrameType = 'full' | 'delta' | 'scrollback' |
'drawing_ack' | 'replay' | 'heartbeat' | 'warming';

export interface RenderFrame {
  type: 'render_frame';
  frame_type: FrameType; // Дискримінатор (деталізується у Slice 1-2)
  symbol?: string;
  tf?: string;

  candles?: Candle[];
  zones?: SmcZone[];
  swings?: SmcSwing[];
  levels?: SmcLevel[];
  drawings?: Drawing[];

  replay?: {
    mode: 'off' | 'on';
    pos_ms: T_MS;
    playing: boolean;
  };

  meta: {
    schema_v: 'ui_v4_v2';  // Фіксація контракту
    seq: number;           // Монотонний ідентифікатор кадру для дропу
stale даних
    server_ts_ms: T_MS;    // Для розрахунку latency
    status: 'connecting' | 'live' | 'warming' | 'error';
    latency_ms?: number;
    ready_pct?: number;
    warnings: string[];
  };
}

// --- WS ACTIONS (Client -> Server) ---
export type WsAction =  
  | { action: 'switch'; symbol: string; tf: string } // Уніфіковано tf
замість tf_s
  | { action: 'scrollback'; to_ms: T_MS } // Уніфіковано to_ms
(before)
  | { action: 'drawing_add'; drawing: Drawing }
  | { action: 'drawing_update'; drawing: Drawing }
  | { action: 'drawing_remove'; id: string }
  | { action: 'overlay_toggle'; layer: string; visible: boolean }
  | { action: 'replay_seek'; to_ms: T_MS }
  | { action: 'replay_step'; delta_bars: number }
  | { action: 'replay_play' }
  | { action: 'replay_pause' }
  | { action: 'replay_exit' };

// --- INTERNAL UI STATE ---
export type ActiveTool = DrawingType | 'eraser' | null;

export interface HitState {
  id: string;
  handleIdx: number | null; // null означає клік по тілу (body)
}

export interface DragState extends HitState {
  startX: number; // CSS px
  startY: number; // CSS px
  startObj: Drawing; // Глибока копія для undo та дельти
}

export interface ScreenAabb {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

Структура проєкту для старту (Commands)

# 1. Ініціалізація

npm create vite@latest ui_v4 -- --template svelte-ts
cd ui_v4

# ФІКСУЄМО ВЕРСІЮ, щоб захистити API від дрейфу (SemVer pin)

npm install lightweight-charts@5.0.0-rc.3  
npm install uuid  
npm install

# 2. Очищення та створення директорій

rm -rf src/assets src/lib
mkdir -p src/ws src/chart/overlay src/chart/drawings
src/chart/interaction src/layout src/stores
Exit Gates для Slice 0 (Перевірено)
● [x] Структура створена.
● [x] types.ts зафіксовано, суперечностей щодо одиниць часу та назв полів (tf, to_ms)
немає.
● [x] RenderFrame.meta доповнено захисними полями (schema_v, seq, server_ts_ms).
● [x] Усунуто any[] (додано SmcSwing та SmcLevel).
● [x] Всі контракти узгоджені з UI_v4_Addendum_v3_consolidated.
