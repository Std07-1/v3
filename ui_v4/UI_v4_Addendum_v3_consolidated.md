# UI v4 — Addendum v3 (SSOT)
## Selection Engine, Hit-Testing та Редагування Drawings

**Статус:** Engineering Specification (SSOT)  
**Контекст:** Професійна взаємодія з намальованими об'єктами (CAD/TV-рівень) поверх Lightweight Charts.

Цей документ є єдиною правдою (SSOT) для:
- hover/selection
- hit-testing (влучання курсором)
- drag-and-drop редагування (ручки/тіло)
- точного eraser (видалення)
- інтеграції з CommandStack (Undo/Redo) без фейкових обіцянок

> **Залежності:** Addendum v2 (SSOT) про DPR (`setTransform`), RAF-колапс (1 render/кадр), Snap-to-OHLC, Keyboard shortcuts, та канонічний `id=UUID`.

---

## 0. Терміни та інваріанти простору (SSOT Space)

### 0.1. Канон часу
- У домені (дані малюнків, WS payload): **`t_ms` (Unix milliseconds)**.
- Для Lightweight Charts (LWC): **`t_sec = t_ms / 1000`** (UTCTimestamp у секундах).

### 0.2. Критичне правило hit-testing
**Hit-testing робиться ВИКЛЮЧНО в Screen Space (CSS px), а не в доменних координатах (`t_ms`, `price`).**

Причина:
- time scale може бути “нерівномірною” (пропуски/сесії/вихідні)
- price scale змінюється zoom/auto-scale
- “5 px” на екрані не має стабільного відповідника у ms/price

**Флоу:**
`Domain (t_ms, price) -> toPixel(x,y) -> HitTest(cursorX,cursorY) -> Interaction -> Pixel delta -> Domain -> WS update`

### 0.3. DPR інваріант (узгодження з Addendum v2)
Canvas налаштований так, щоб **1 одиниця координат в контексті = 1 CSS px**:
- `ctx.setTransform(dpr, 0, 0, dpr, 0, 0)`
- Уся геометрія (cursorX/Y, HIT_TOLERANCE_PX, dist) — в CSS px.

---

## 1. Дані та контракти

### 1.1. Drawing (клієнтський контракт)
```ts
type DrawingType = 'hline' | 'trend' | 'rect';

type DrawingPoint = { t_ms: number; price: number };

type Drawing = {
  id: string;          // UUID (клієнт генерує)
  type: DrawingType;
  points: DrawingPoint[];  // hline:1, trend:2, rect:2 (діагональні кути)
  meta?: { color?: string; lineWidth?: number; locked?: boolean };
};
```

### 1.2. WS Actions (мінімум для v3)
- `drawingAdd(drawing: Drawing)`
- `drawingUpdate(drawing: Drawing)`
- `drawingRemove(id: string)`

> **Примітка:** Оскільки `id` клієнтський і канонічний, CommandStack може одразу створювати команди (Undo/Redo) з реальним ID.

---

## 2. Адаптери перетворення координат (Domain <-> Screen)

### 2.1. Нормалізація часу з LWC
`chart.timeScale().coordinateToTime(x)` повертає `HorzScaleItem | null`, де `HorzScaleItem` може бути:
- `number` (UTCTimestamp у секундах)
- або `BusinessDay` (об'єкт з `year/month/day`)

Тому потрібна нормалізація.

```ts
type HorzScaleItem = number | { year: number; month: number; day: number };

function timeToSec(time: HorzScaleItem): number {
  if (typeof time === 'number') return time;
  // BusinessDay -> UTC seconds at 00:00:00
  return Date.UTC(time.year, time.month - 1, time.day, 0, 0, 0, 0) / 1000;
}
```

### 2.2. Канонічні конвертери (в межах DrawingsRenderer)
```ts
const toX = (t_ms: number): number | null =>
  chartApi.timeScale().timeToCoordinate(t_ms / 1000);

const toY = (price: number): number | null =>
  seriesApi.priceToCoordinate(price);

const fromX = (x: number): number | null => {
  const t = chartApi.timeScale().coordinateToTime(x);
  if (t === null) return null;
  return timeToSec(t) * 1000;
};

const fromY = (y: number): number | null => {
  const p = seriesApi.coordinateToPrice(y);
  // coordinateToPrice повертає BarPrice (number | null), залежно від серії/стану
  if (p === null || Number.isNaN(p)) return null;
  return p as number;
};
```

**Degraded-but-loud інваріант:** якщо будь-який converter повернув `null` — interaction цієї події **не виконується**, лог/diagnostic додається у warnings (без silent fallback).

---

## 3. Геометричне ядро (Math Core)

### 3.1. Модуль (НЕ utils hell)
Замість загального `math.js` розміщуємо код у вузькому доменному модулі:

- `src/chart/interaction/geometry.ts`

Публічний API лише для screen-space примітивів.

### 3.2. Константи
```ts
export const HIT_TOLERANCE_PX = 6;   // зона “захоплення”
export const HANDLE_RADIUS_PX = 3.5; // ручка
export const HANDLE_RADIUS_HOVER_PX = 5;
```

### 3.3. Відстані (все в CSS px)
```ts
export function distToHLine(cursorY: number, lineY: number): number {
  return Math.abs(cursorY - lineY);
}

export function distToPoint(px: number, py: number, x: number, y: number): number {
  return Math.hypot(px - x, py - y);
}

export function distToSegment(
  px: number, py: number,
  x1: number, y1: number,
  x2: number, y2: number,
): number {
  const l2 = (x2 - x1) ** 2 + (y2 - y1) ** 2;
  if (l2 === 0) return Math.hypot(px - x1, py - y1);

  let t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2;
  t = Math.max(0, Math.min(1, t));

  const projX = x1 + t * (x2 - x1);
  const projY = y1 + t * (y2 - y1);
  return Math.hypot(px - projX, py - projY);
}

// Влучання у ребро прямокутника (rect) з допуском
export function distToRectEdge(
  px: number, py: number,
  xMin: number, yMin: number,
  xMax: number, yMax: number,
  tol: number,
): number {
  const insideExpanded =
    px >= xMin - tol && px <= xMax + tol && py >= yMin - tol && py <= yMax + tol;
  if (!insideExpanded) return Infinity;

  const insideInner =
    px > xMin + tol && px < xMax - tol && py > yMin + tol && py < yMax - tol;

  return insideInner ? Infinity : 0; // “на ребрі” в межах толерансу
}
```

---

## 4. Selection Engine (стани та FSM)

### 4.1. Стан (в DrawingsRenderer)
```ts
type HitState = { id: string; handleIdx: number | null };

selectedId: string | null = null;
hovered: HitState | null = null;

dragState: null | {
  id: string;
  handleIdx: number | null;   // null -> body drag
  startX: number;
  startY: number;
  startObj: Drawing;          // snapshot для Undo + стабільної дельти
};
```

**Rail:** `startObj` створюємо через `structuredClone(d)` (або швидку глибоку копію), без JSON stringify.

### 4.2. Пріоритети hit
1) Handles (для selectedId) — найвищий пріоритет
2) Body (для всіх) — вибір найближчого в межах `HIT_TOLERANCE_PX`
3) Z-index: скан з кінця `drawings[]` (верхні клікаються першими)

---

## 5. Hit-Testing Loop

### 5.1. Пре-фільтр продуктивності (обов'язково)
Щоб не робити дорогу геометрію для кожного об’єкта, кожен Drawing має мати кешовану **screen-space AABB** на поточний кадр:

```ts
type ScreenAabb = { minX: number; minY: number; maxX: number; maxY: number };

drawing._aabb?: ScreenAabb;
```

AABB оновлюється в `render()` (коли ми вже конвертуємо точки в пікселі).  
Hit-test спершу відсікає об’єкти, де курсор поза AABB ± tolerance.

### 5.2. Основний алгоритм
```ts
function performHitTest(cursorX: number, cursorY: number): HitState | null {
  let best: HitState | null = null;
  let minDist = HIT_TOLERANCE_PX;

  // 1) Handles для selectedId
  if (selectedId) {
    const d = drawings.find(x => x.id === selectedId);
    if (d) {
      for (let j = 0; j < d.points.length; j++) {
        const hx = toX(d.points[j].t_ms);
        const hy = toY(d.points[j].price);
        if (hx === null || hy === null) continue;
        if (distToPoint(cursorX, cursorY, hx, hy) <= HIT_TOLERANCE_PX) {
          return { id: d.id, handleIdx: j };
        }
      }
    }
  }

  // 2) Body scan (z-index from end)
  for (let i = drawings.length - 1; i >= 0; i--) {
    const d = drawings[i];

    // AABB reject
    const aabb = d._aabb;
    if (aabb) {
      if (cursorX < aabb.minX - HIT_TOLERANCE_PX || cursorX > aabb.maxX + HIT_TOLERANCE_PX ||
          cursorY < aabb.minY - HIT_TOLERANCE_PX || cursorY > aabb.maxY + HIT_TOLERANCE_PX) {
        continue;
      }
    }

    let dist = Infinity;

    if (d.type === 'hline') {
      const y = toY(d.points[0].price);
      if (y !== null) dist = distToHLine(cursorY, y);
    } else if (d.type === 'trend') {
      const x1 = toX(d.points[0].t_ms), y1 = toY(d.points[0].price);
      const x2 = toX(d.points[1].t_ms), y2 = toY(d.points[1].price);
      if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
        dist = distToSegment(cursorX, cursorY, x1, y1, x2, y2);
      }
    } else if (d.type === 'rect') {
      const x1 = toX(d.points[0].t_ms), y1 = toY(d.points[0].price);
      const x2 = toX(d.points[1].t_ms), y2 = toY(d.points[1].price);
      if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
        const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
        const minY = Math.min(y1, y2), maxY = Math.max(y1, y2);
        dist = distToRectEdge(cursorX, cursorY, minX, minY, maxX, maxY, HIT_TOLERANCE_PX);
      }
    }

    if (dist <= minDist) {
      minDist = dist;
      best = { id: d.id, handleIdx: null };
      // Опція: early-exit при dist==0 для rect edge
      if (dist === 0) break;
    }
  }

  return best;
}
```

---

## 6. Життєвий цикл взаємодій (Pointer Events)

### 6.1. Події
Використовуємо **Pointer Events** (не mouse), щоб отримати:
- однакову поведінку для миші/тачу
- `setPointerCapture()` під час drag

### 6.2. PointerMove (hover або drag)
- Якщо `dragState != null` -> виконати drag math (розділ 7), `scheduleRender()`.
- Якщо не drag і не activeTool (не малюємо новий об'єкт) -> `hovered = performHitTest(...)`, курсор, `scheduleRender()`.

**Важливо:** не викликати `preventDefault()` у звичайному hover, щоб не ламати LWC.

### 6.3. PointerDown (select / start drag)
```ts
if (!activeTool && hovered) {
  selectedId = hovered.id;
  const d = drawings.find(x => x.id === selectedId);
  if (d) {
    dragState = {
      ...hovered,
      startX: x, startY: y,
      startObj: structuredClone(d),
    };
    canvas.setPointerCapture(pointerId);
  }
} else if (!activeTool) {
  selectedId = null;
}
```

### 6.4. PointerUp (commit)
- Якщо `dragState` був активний:
  - Сформувати `updatedDrawing` (поточний drawing)
  - Якщо зміни є (epsilon check) -> `commandStack.pushUpdate({ drawing: updated, previousState: dragState.startObj })`
  - `dragState = null`

**Rail:** не слати update, якщо не змінилось нічого.

---

## 7. Smart Drag (математика редагування)

### 7.1. Drag Handle (переміщення однієї точки)
Алгоритм:
1) `t_ms = fromX(x)` (якщо null -> abort)
2) `rawPrice = fromY(y)` (якщо null -> abort)
3) `price = getSnappedPrice(...)` (Snap-to-OHLC з v2, за radius_px)
4) Оновити `points[handleIdx] = { t_ms, price }`

> **Примітка:** Snap застосовуємо тут (handle drag) як “точний” режим.

### 7.2. Drag Body (переміщення всього об'єкта)
Ціль: зберегти кут/форму у screen-space.

1) `dx = x - startX`, `dy = y - startY`
2) Для кожної точки беремо startObj.point -> переводимо в пікселі `(oldX, oldY)`
3) Нові пікселі `(oldX+dx, oldY+dy)`
4) Конвертуємо назад `new_t_ms = fromX(oldX+dx)`, `new_price = fromY(oldY+dy)`
5) Оновлюємо точки

**Degraded-but-loud:** якщо будь-яка точка не конвертується (`null`) — abort drag для цього кадру + warning.

> **Примітка:** Snap під час body drag вимикаємо (інакше “тремтіння”). Snap повертається для handle drag.

---

## 8. Ідеальний Eraser (точне видалення)

У режимі `activeTool === 'eraser'`:
- hover через `performHitTest`
- на pointerdown, якщо `hovered` є:
  - видалити локально
  - `commandStack.pushDelete(targetObj)` (Undo = add snapshot)
  - відправити `drawingRemove(id)`

**Exit-gate:** клік в межах 6 px по лінії видаляє; клік поруч — ні.

---

## 9. Візуалізація selection/hover (render)

### 9.1. Hover highlight (тіло)
- hovered.id != null -> змінити strokeStyle/alpha для hovered об’єкта (але без зміни доменних даних)

### 9.2. Selected handles (поверх усього)
- для selectedId відмалювати кола-ручки в точках
- якщо hovered.handleIdx збігається — радіус збільшити

---

## 10. Продуктивність та бюджети (v3 gates)

### 10.1. Бюджет подій
- `performHitTest()` викликається тільки через RAF-колапс (з v2), тобто максимум 1 раз/кадр.

### 10.2. Бюджет кількості об’єктів
- Обов’язковий AABB pre-filter (розділ 5.1).
- Ціль: плавний hover/drag при **N=300** drawings на mid-tier CPU.

---

## 11. Exit-gates (Definition of Done для Addendum v3)

1) **Selection:** наведення на тренд в межах 6 px стабільно дає hovered.id.  
2) **Handles:** для selected об’єкта вузли клікаються та тягнуться, `handleIdx` точний.  
3) **Body drag:** форма/кут на екрані зберігається при перетягуванні (dx/dy pixel math).  
4) **Eraser:** видаляє тільки те, у що реально “влучили” (hit-test).  
5) **Undo/Redo:** move/delete додає реальні команди в стек і коректно відміняється.  
6) **No interference:** wheel/zoom/scroll LWC не ламається (жодних зайвих preventDefault/stopPropagation поза drag).  
7) **Null safety:** якщо `timeToCoordinate`/`coordinateToTime`/`coordinateToPrice` повертають `null`, система деградує loud (warning) і не робить silent помилок.

---

## 12. Відомі обмеження (свідомо)
- Немає multi-select (лише один selectedId).
- Немає rotate/складних transform’ів (тільки прямі маніпуляції точками).
- Немає snap-to-time під час body drag (умисно, щоб не тремтіло).
