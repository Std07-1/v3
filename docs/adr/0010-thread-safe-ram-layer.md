# ADR-0009: Thread-safe RAM Layer (P3.2 Concurrency Hardening)

**initiative**: `concurrency_hardening` (P3)  
**Батьківський документ**: [system_current_overview.md](system_current_overview.md)  
**Дата**: 2026-02-24  
**Статус**: ✅ **DONE**

---

## Проблема

У підготовці до production (10-50+ одночасних користувачів UI v4) виявлено **HIGH ризик (P3.2)**: `ram_layer.py` не має жодної синхронізації (Locks) для доступу до даних `OrderedDict` і списків барів.

1. **Архітектура читання/запису (Data Race)**:
   - Обробник поллінгу / агрегації (ingest workers) працює в Python-потоках і записує дані через `upsert_bar()`.
   - `ws_server.py` використовує `req.run_in_executor()` для виклику `uds.read_window()` для кожного клієнта (початкове завантаження, scrollback), що створює фонові потоки.
2. **Симптоми**: Зміна розміру або мутація масивів (`append`, `sort`, `popitem`) під час ітерації в іншому потоці викличе `RuntimeError: dictionary changed size during iteration` або `IndexError`, що призведе до падіння запиту, WS з'єднання або навіть усього сервісу.

---

## Рішення

Додати `threading.Lock` всередині класу `RamLayer`. 
Ми використовуємо базовий `Lock` замість `RLock`, оскільки методи `RamLayer` не викликають один одного (немає рекурсії), що робить `Lock` трохи швидшим [1].

Всі публічні методи (`get_window`, `set_window`, `upsert_bar`, `stats`) будуть обгорнуті в контекстний менеджер `with self._lock:`.

### Оцінка Performance
Операції в `RamLayer` — це швидкі маніпуляції зі словниками та списками (O(1) або O(N) для малих N). Тривалість утримання локу мікроскопічна (десятки мікросекунд), тому вузького місця (bottleneck) при 50 клієнтах не очікується. 
Якщо у майбутньому це стане проблемою, можливий перехід на Read-Write lock (через сторонню бібліотеку на зразок `readerwriterlock` або `asyncio` міграцію). Поки що звичайного `Lock` достатньо для 100% захисту пам'яті.

---

## Реалізація

1. **`ram_layer.py`**:
   - `import threading`
   - Ініціалізація `self._lock = threading.Lock()` у `__init__`.
   - Захист `_evict_if_needed`, `get_window`, `set_window`, `upsert_bar`, `stats` блоком `with self._lock:`. (Приватний `_touch` викликається тільки зсередини захищених методів).
   
2. **Аудит**: P3.2 в `audit/progress.md` переведений у ✅ **DONE**.

---

## Альтернативні розглядані варіанти (Rejected)

1. **Copy-on-read**: При `get_window` завжди робити `copy.deepcopy()` без локу. 
   *Відхилено*: Не захищає від `RuntimeError` при ітерації `OrderedDict`, і глибоке копіювання тисяч словників серйозно перевантажить CPU (high I/O & latency).
2. **Перехід на `asyncio`**: Переписати весь UDS і `ram_layer` як асинхронні.
   *Відхилено*: Занадто великий обсяг рефакторингу `uds.py`, існуючих worker'ів та API (MODE=ADR+MASSIVE).
3. **`threading.RLock`**: 
   *Відхилено*: Не потрібен, оскільки внутрішні методи не перетинаються. Звичайний `Lock` швидший.

---

## Верифікація

`python -m unittest tests/test_ram_layer.py` (якщо є) або базовий стрес-тест 50 клієнтами покаже відсутність `RuntimeError` при одночасному `upsert_bar` та `get_window`. 

[1] https://docs.python.org/3/library/threading.html#threading.Lock
