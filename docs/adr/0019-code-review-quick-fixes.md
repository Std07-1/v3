# ADR-0019: Code Review Quick-Fixes Batch (Дефекти #1, #2, #4, #5, #8, #9)

- **Статус**: Implemented
- **Дата**: 2026-02-26
- **Автор**: code-review audit (111.md, research/leader/)
- **Initiative**: `code_review_hardening`

## Контекст і проблема

Зовнішній аудит коду (research/leader/111.md) виявив 14 дефектів різної важкості.
Шість з них класифіковані як безпечні quick-fix (≤30 LOC кожен), що не торкаються
інваріантів I0–I6, контрактів або протоколів, а лише покращують performance
та thread-safety на hot-path.

### Дефект #1 — GenericBuffer.upsert: sort() замість bisect

**Файл**: `core/derive.py`, метод `GenericBuffer.upsert()`

**Чому проблема**: коли новий бар не є найновішим (out-of-order, warmup, backfill),
fallback-гілка викликає `self._sorted_keys.sort()` — це O(n·log n) на масиві,
який вже відсортований окрім одного елемента. `bisect.insort()` робить те саме за O(n)
(один shift для вставки в правильну позицію).

**Що зроблено**: замінено `self._sorted_keys.append(k); self._sorted_keys.sort()`
на `bisect.insort(self._sorted_keys, k)`. Оптимістичний шлях (append найновішого) залишено.

**Чому безпечно**: pure-функція у `core/`, без I/O. Результат ідентичний — sorted list.
Інваріанти buсket ordering та GC не змінено.

---

### Дефект #2 — warmup_bars() без per-symbol lock

**Файл**: `runtime/ingest/derive_engine.py`, метод `DeriveEngine.warmup_bars()`

**Чому проблема**: `warmup_bars()` викликається з bootstrap (m1_poller._bootstrap_warmup),
а `on_bar()` — з polling threads, які можуть стартувати паралельно. Без lock між ними
можлива гонка на `GenericBuffer._by_open_ms` / `_sorted_keys` — відсутні елементи,
зіпсований порядок, неправильна деривація.

**Що зроблено**: warmup_bars тепер групує бари по символу і для кожного символу бере
`with self._locks[sym]:` перед buffer.upsert. Це те саме, що робить `on_bar()` —
уніфікований lock-протокол.

**Чому безпечно**: `self._locks` вже існують (створюються в `__init__`). Lock contention
мінімальний — warmup відбувається тільки раз при bootstrap, а потім лише `on_bar()`.
Один lock per symbol — без deadlock ризику.

---

### Дефект #4 — _last_trading_minute_ms: O(10080) цикл без кешу

**Файл**: `runtime/ingest/polling/m1_poller.py`, функція `_last_trading_minute_ms()`

**Чому проблема**: під час закритого ринку (вихідні) ця функція ітерує до 10080 кроків
(7 днів × 24 год × 60 хв) щоб знайти останню торгову хвилину. Викликається щоциклу
для кожного символу — зайве навантаження CPU.

**Що зроблено**: додано per-calendar кеш через `id(calendar)`. Якщо `now_ms` не змінився
від попереднього виклику — повертає кешований результат за O(1). Кеш інвалідується
автоматично при зміні `now_ms` (щохвилини).

**Чому безпечно**: кеш використовує `id(calendar)` як ключ (calendar — stable object).
Результат детермінований для того ж `now_ms`. Fallback при промазі — оригінальний цикл.

---

### Дефект #5 — JsonlAppender._open_files: необмежений ріст FD

**Файл**: `runtime/store/ssot_jsonl.py`, клас `JsonlAppender`

**Чому проблема**: кожен новий файл (symbol × tf × date) відкривається і ніколи не
закривається — тільки `close()` усього appender. За тиждень роботи 13 символів × 8 TF ×
~7 днів = 728 FD. На Linux/Windows є системний ліміт (зазвичай 1024), після чого
починаються помилки `OSError: Too many open files`.

**Що зроблено**: додано LRU eviction з `_MAX_OPEN_FILES = 64`. Коли ліміт досягнуто —
закривається найстаріший FD. При повторному зверненні до evicted файлу — він
перевідкривається в режимі append. Також додано LRU touch (переміщення в кінець) при
доступі до існуючого FD.

**Чому безпечно**: файли відкриваються з `mode="a"` (append) — перевідкриття не втрачає
дані, cursor автоматично встановлюється в кінець. Реальний hot-set (активні symbol×tf
за поточний день) зазвичай ≤52 файлів (13 sym × 4 active TF), тобто eviction рідкісний.

---

### Дефект #8 — time.sleep() блокує graceful shutdown

**Файл**: `runtime/ingest/polling/m1_poller.py`, клас `M1PollerRunner`

**Чому проблема**: `_sleep_to_next_minute()` використовує `time.sleep(delay)` де delay
може бути до 60+ секунд. Під час sleep `shutdown()` не може перервати очікування —
процес зависає на десятки секунд після SIGTERM/KeyboardInterrupt.

**Що зроблено**:

1. Додано `self._stop_event = threading.Event()` в `__init__`.
2. `_sleep_to_next_minute()` замінено `time.sleep(delay)` → `self._stop_event.wait(delay)`.
3. `run_forever()` замінено `while True:` → `while not self._stop_event.is_set():`.
4. `shutdown()` додано `self._stop_event.set()` — негайно пробуджує sleep.

**Чому безпечно**: `threading.Event.wait(timeout)` семантично ідентичний `time.sleep(timeout)`
у нормальному режимі — повертає через timeout секунд. Різниця лише при `set()` —
повертає негайно, що дозволяє чистий вихід з циклу. Polling logic не змінено.

---

### Дефект #9 — ThreadPoolExecutor max_workers=2

**Файл**: `runtime/ws/ws_server.py`, функція `build_app()`

**Чому проблема**: WS сервер використовує `ThreadPoolExecutor(max_workers=2)` для UDS I/O.
При паралельних запитах `/api/bars` + `/api/updates` від UI v4 (Svelte) обидва workers
зайняті — третій запит блокується. Це bottleneck на p95 latency для UI.

**Що зроблено**: замінено фіксовані `max_workers=2` на `min(4, os.cpu_count() or 4)`.
На типовому сервері (2-4 ядра) це дає 2-4 workers — достатньо для parallel reads
без ризику thread explosion.

**Чому безпечно**: збільшення workers з 2 до 4 — стандартний тюнінг. UDS read-path
thread-safe (RamLayer має threading.Lock з ADR-0010). Ліміт `min(4, cpu_count)` запобігає
надмірному навантаженню на слабких машинах.

---

## Розглянуті варіанти

Для кожного дефекту розглядався варіант "відкласти до окремого initiative".
Відхилено: всі зміни ≤30 LOC, не торкаються інваріантів, мають чистий rollback.

## Рішення

Застосувати всі 6 quick-fix одним batch. Кожен patch незалежний від інших.

## Наслідки

- **Performance**: bisect.insort та LTM-кеш зменшують CPU waste на hot-path
- **Reliability**: LRU FD запобігає FD leak; warmup lock запобігає data race
- **Operations**: stop_event дозволяє graceful shutdown m1_poller (автоматизація, CI)
- **Latency**: 4 workers замість 2 зменшує queue wait для UI

## Файли змінені

| Файл | Дефект | LOC зміна |
|------|--------|-----------|
| `core/derive.py` | #1 | +2 −3 |
| `runtime/ingest/derive_engine.py` | #2 | +12 −5 |
| `runtime/ingest/polling/m1_poller.py` | #4 | +20 −5 |
| `runtime/ingest/polling/m1_poller.py` | #8 | +8 −3 |
| `runtime/store/ssot_jsonl.py` | #5 | +14 −2 |
| `runtime/ws/ws_server.py` | #9 | +4 −1 |

## Rollback

Кожен patch незалежний:

1. **#1**: повернути `self._sorted_keys.sort()` замість `bisect.insort()`, прибрати `import bisect`
2. **#2**: прибрати `with lock:` обгортку в `warmup_bars()`, повернути flat loop
3. **#4**: прибрати `_ltm_cache*`, повернути простий цикл
4. **#5**: прибрати `_MAX_OPEN_FILES`, `_open_files_order`, LRU eviction
5. **#8**: замінити `self._stop_event.wait(delay)` на `time.sleep(delay)`, прибрати `_stop_event`
6. **#9**: повернути `max_workers=2`
