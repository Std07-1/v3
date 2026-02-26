# ADR-0022: WS Server Rate-Limit, Graceful Shutdown та Operational Docs

- **Статус**: Proposed
- **Дата**: 2026-02-26
- **Автор**: code-review audit (111.md, дефекти #10–#14)
- **Initiative**: `code_review_hardening`

## Контекст і проблема

Зовнішній аудит порушив 5 питань (#10–#14) щодо WS server та operational reliability.
Три з них виявились FALSE при перевірці, але потребують документального підтвердження.

---

## Дефект #10: has_range не враховує calendar pause — **FALSE**

**Доказ**: `core/derive.py:134-150` — `GenericBuffer.has_range()` приймає
`is_trading_fn: Optional[Callable[[int], bool]]` і пропускає non-trading слоти:

```python
def has_range(self, start_ms, end_ms, is_trading_fn=None):
    step = self._tf_ms
    for t in range(start_ms, end_ms, step):
        if is_trading_fn is not None and not is_trading_fn(t):
            continue            # ← calendar pause пропускається
        if t not in self._by_open_ms:
            return False
    return True
```

Caller (`derive_bar()` в `core/derive.py:320-370`) передає `calendar.is_trading_minute`
як `is_trading_fn`. Calendar pause коректно обробляється.

---

## Дефект #11: TF mapping uses hardcoded dict — **BY DESIGN**

**Факт**: `runtime/ws/ws_server.py` використовує hardcoded dict для TF label→seconds
mapping (наприклад, `"M1": 60, "M5": 300, ...`). Це зручний alias-layer для HTTP API.

**Чому by design**: SSOT TF allowlist живе в `config.json` і перевіряється через
`tf_allowlist_from_cfg()` (core/config_loader.py). WS server dict — це лише зворотній
маппінг label→int для парсингу HTTP параметрів. Значення синхронізовані з config.json.

**Рекомендація**: додати коментар у ws_server.py, що dict має відповідати config.json.
Але окремий initiative не потрібен — зміна TF allowlist (яка практично ніколи не буде)
потребує ревізії всіх місць.

---

## Дефект #12: Немає prometheus metrics — **FALSE**

**Доказ**: `runtime/store/uds.py:46-65` — три prometheus метрики існують:

```python
_METRIC_REDIS_WRITE_FAIL_TOTAL = Counter(
    "ai_one_uds_redis_write_fail_total", ...
)
_METRIC_PUBSUB_FAIL_TOTAL = Counter(
    "ai_one_uds_pubsub_fail_total", ...
)
_METRIC_SPLIT_BRAIN_ACTIVE = Gauge(
    "ai_one_uds_split_brain_active", ...
)
```

З graceful fallback на `_NoopCounter`/`_NoopGauge` якщо `prometheus_client` не встановлено.
Додаткові метрики (latency p95, write throughput) — scope ADR-0018.

---

## Дефект #13: Немає rate-limit на WS broadcast — **PARTIAL (документація)**

**Факт**: WS broadcast (`_global_delta_loop`) вже має throttle:

```python
# runtime/ws/ws_server.py — _global_delta_loop()
await asyncio.sleep(poll_interval_s)  # default 1.0s (config: ws_delta_poll_s)
```

Це обмежує частоту оновлень до ~1/с. Але для clients (окremих WS підключень)
немає per-client backpressure або drop policy. Якщо client не встигає читати —
aiohttp буферизує повідомлення в пам'яті.

**Рекомендація**: для MVP достатньо. Для production з декількома clients —
потрібен per-client watermark та drop-oldest policy. Це scope окремого initiative.

---

## Дефект #14: Немає graceful shutdown — **FALSE**

**Доказ**: `runtime/ws/ws_server.py:1017-1035` — cleanup callback існує:

```python
async def _cleanup_bg_tasks(app_ctx):
    task = app_ctx.get("_global_delta_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

app.on_cleanup.append(_cleanup_bg_tasks)
```

Також aiohttp.web_runner автоматично закриває всі WS з'єднання при shutdown.
Port bind retry (`_run_with_retry`) обробляє `KeyboardInterrupt` з `runner.cleanup()`.

---

## Рішення

1. **#10, #12, #14**: Закрити як FALSE — задокументовано в цьому ADR з доказами.
2. **#11**: Додати коментар у ws_server.py (by design, sync з config.json).
3. **#13**: Залишити поточний throttle (1/с). Per-client backpressure — окремий initiative
   якщо з'являться multiple clients.

## Наслідки

Ніяких змін коду. Цей ADR — proof-document для зовнішнього аудиту.

## Rollback

N/A — документ.
