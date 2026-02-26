# ADR-0021: JsonlAppender Thread-Safety

- **Статус**: Proposed
- **Дата**: 2026-02-26
- **Автор**: code-review audit (111.md, дефект #6)
- **Initiative**: `code_review_hardening`

## Контекст і проблема

`JsonlAppender.append()` (runtime/store/ssot_jsonl.py:114-150) — єдиний write-path
для OHLCV на диск (SSOT JSONL, інваріант I1). Цей метод не має `threading.Lock`.

**Чому це потенційна проблема**:

Теоретично, якщо append() викликається з різних threads для різних символів/файлів —
вони пишуть у різні FD, тому no conflict. Але якщо два threads пишуть в один файл
(наприклад, два M1 бари одного символу від різних sources), то:

1. `json.dumps()` + `fh.write(line + "\n")` — не atomic на рівні OS.
2. При interleaving можна отримати corrupted JSONL line.

**Поточна архітектура write-path**:

```
m1_poller (per-symbol thread)
  → UDS.commit_final_bar()
    → self._append_to_disk(bar)        # UDS._commit_lock.acquire()
      → self._ssot.append(bar)         # JsonlAppender.append()
```

**Ключовий факт**: UDS.commit_final_bar() вже має `self._commit_lock` (threading.Lock)
в uds.py. Перевіримо:

```python
# runtime/store/uds.py — метод commit_final_bar
with self._commit_lock:
    ...
    ssot_written = self._append_to_disk(bar, ...)
```

**Висновок**: UDS вже серіалізує виклики до JsonlAppender.append() через свій
`_commit_lock`. Тому data race неможливий при поточній архітектурі (всі записи
йдуть через UDS → I1).

## Однак

Якщо хтось колись викличе JsonlAppender.append() напряму (поза UDS) — race можливий.
Також, `_open_files` dict mutation (LRU eviction, new FD open) не захищений.

## Розглянуті варіанти

### Варіант A: Додати threading.Lock у JsonlAppender

```python
class JsonlAppender:
    def __init__(self, ...):
        ...
        self._lock = threading.Lock()

    def append(self, bar):
        with self._lock:
            ... # existing code
```

**Плюси**: defense-in-depth, незалежність від зовнішнього lock.
**Мінуси**: додаткова синхронізація (мікросекунди) на кожен write. Але write = disk I/O,
тому overhead lock незначний.

### Варіант B: Per-path lock (гранулярніший)

threading.Lock per file path — паралельний запис у різні файли.

**Плюси**: максимальний паралелізм.
**Мінуси**: складність, LRU eviction потребує global lock все одно, overhead
управління dict of locks.

### Варіант C: Залишити як є + документувати

UDS._commit_lock вже захищає. Додати docstring assertion.

## Рекомендація

**Варіант A** — один global Lock у JsonlAppender. Причини:

1. Defense-in-depth (не залежить від external caller).
2. Overhead мізерний (disk I/O >> lock contention).
3. ≤5 LOC зміна.
4. Захищає LRU eviction в _open_files (додано в ADR-0019 Дефект #5).

## Реалізація (коли PATCH буде затверджено)

```python
import threading

class JsonlAppender:
    def __init__(self, ...):
        ...
        self._lock = threading.Lock()

    def append(self, bar):
        with self._lock:
            ... # весь існуючий код append
```

## Чому ще не реалізовано

Потрібно повне розуміння всіх callers JsonlAppender — чи немає reentrancy,
чи lock не створить deadlock з UDS._commit_lock (nested locking).  
Поточний аналіз: UDS._commit_lock → JsonlAppender._lock — завжди в одному порядку,
deadlock неможливий. Але це потрібно підтвердити ревізією всіх call-sites.

## Наслідки

- Додатковий threading.Lock у JsonlAppender.
- LRU eviction (_open_files) також буде під lock.
- Тести: concurrent write test (2 threads → різні символи).

## Rollback

Прибрати `self._lock` та `with self._lock:` з JsonlAppender.**init**() та append().
