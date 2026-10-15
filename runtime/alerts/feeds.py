"""Джерела цін для двигуна сповіщень (ADR-0114 §4.2–4.3): тіки з PubSub і фінальні M1 з UDS.

- **Тіки** — канал `config.json:channels.price_tick` (той, у який публікує релей `broker_sidecar`), поле `mid` — ціна
  графіка за `tick_stream_price_mode` (bid). Обрив — прапорець `ticks_unavailable` і перепідключення з паузою.
- **Фінальні M1** — UDS reader `read_window` з диска, шматками `(after, to]` по `chunk_bars` хвилин (диск дає
  найновіші `limit` ключів діапазону, тож шматок не довший за `limit` — нічого не губиться). Свіжий хвіст у межах
  `final_lag_ms` читається повторно, поки пізні фінальні ще можуть прийти (переходи ідемпотентні).

Обидва цикли нескінченні, зупинка — `cancel()` задачі.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, AbstractSet, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from redis.exceptions import RedisError

from runtime.alerts.engine import M1_MS, AlertEngine, final_bars_from_lwc
from runtime.store.uds import DISK_POLICY_EXPLICIT, ReadPolicy, WindowSpec

_log = logging.getLogger(__name__)

DEGRADED_TICKS = "ticks_unavailable"
DEGRADED_M1_PREFIX = "m1_unavailable:"
_REJECTED_LOG_EVERY = 1000

ReadFinalM1 = Callable[[str, int, int, int], List[Dict[str, Any]]]  # (symbol, after_ms, to_ms, limit) → bars_lwc


class M1ReadError(RuntimeError):
    """UDS повернув деградоване вікно — свічок могло не бути прочитано."""


def parse_tick(data: Any, symbols: AbstractSet[str]) -> Optional[Tuple[str, float, int]]:
    """Повідомлення релея → (symbol, price, tick_ts_ms); None — тік чужого символу (фільтр, не помилка).
    Битий тік — `ValueError` з причиною."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if not isinstance(data, str):
        raise ValueError(f"дані не рядок: {type(data).__name__}")
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"не JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise ValueError(f"не об'єкт: {type(payload).__name__}")
    symbol, price, tick_ts_ms = payload.get("symbol"), payload.get("mid"), payload.get("tick_ts_ms")
    if not isinstance(symbol, str):
        raise ValueError(f"symbol: {symbol!r}")
    if symbol not in symbols:
        return None
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError(f"mid: {price!r}")
    if isinstance(tick_ts_ms, bool) or not isinstance(tick_ts_ms, int) or tick_ts_ms <= 0:
        raise ValueError(f"tick_ts_ms: {tick_ts_ms!r}")
    return symbol, float(price), tick_ts_ms


async def run_tick_feed(
    open_pubsub: Callable[[], Awaitable[Any]],
    engine: AlertEngine,
    symbols: AbstractSet[str],
    *,
    reconnect_s: float,
) -> None:
    """`open_pubsub` — підписаний PubSub (redis.asyncio). Битих повідомлень — лічильник і лог раз на тисячу."""
    rejected = 0
    while True:
        pubsub = None
        try:
            pubsub = await open_pubsub()
            engine.set_degraded(DEGRADED_TICKS, None)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    tick = parse_tick(message.get("data"), symbols)
                except ValueError as exc:
                    rejected += 1
                    if rejected % _REJECTED_LOG_EVERY == 1:
                        _log.warning("ALERTS_TICK_REJECTED total=%d reason=%s", rejected, exc)
                    continue
                if tick is not None:
                    engine.apply_tick(*tick)
            raise ConnectionError("PubSub: потік повідомлень завершився")
        except (RedisError, OSError) as exc:
            engine.set_degraded(DEGRADED_TICKS, f"{type(exc).__name__}: {exc}")
        finally:
            if pubsub is not None:
                await _close_pubsub(pubsub)
        await asyncio.sleep(reconnect_s)


async def run_m1_poller(
    read_final_m1: ReadFinalM1,
    engine: AlertEngine,
    symbols: Sequence[str],
    *,
    poll_s: float,
    final_lag_ms: int,
    chunk_bars: int,
    clock_ms: Callable[[], int],
) -> None:
    while True:
        await poll_m1_once(
            read_final_m1, engine, symbols, final_lag_ms=final_lag_ms, chunk_bars=chunk_bars, clock_ms=clock_ms
        )
        engine.retry_unsaved()
        await asyncio.sleep(poll_s)


async def poll_m1_once(
    read_final_m1: ReadFinalM1,
    engine: AlertEngine,
    symbols: Sequence[str],
    *,
    final_lag_ms: int,
    chunk_bars: int,
    clock_ms: Callable[[], int],
) -> None:
    """Один прохід по символах із взведеними сповіщеннями; читання з диска — у executor (цикл не блокується)."""
    loop = asyncio.get_running_loop()
    for symbol in symbols:
        flag = DEGRADED_M1_PREFIX + symbol
        after = engine.m1_after_ms(symbol)
        now = clock_ms()
        try:
            while after is not None and after < now:
                to = min(after + chunk_bars * M1_MS, now)
                rows = await loop.run_in_executor(None, read_final_m1, symbol, after, to, chunk_bars)
                bars, rejected = final_bars_from_lwc(rows)
                if rejected:
                    _log.warning("ALERTS_M1_ROWS_REJECTED sym=%s n=%d after=%d to=%d", symbol, rejected, after, to)
                engine.apply_m1_bars(symbol, bars, safe_until_ms=min(to, now - final_lag_ms))
                after = to
        except (M1ReadError, RedisError, OSError, ValueError) as exc:
            engine.set_degraded(flag, f"{type(exc).__name__}: {exc}")
            continue
        engine.set_degraded(flag, None)


def uds_final_m1_reader(uds: Any) -> ReadFinalM1:
    """Читач фінальних M1 з диска через UDS (I1). Деградоване вікно — `M1ReadError`, не тихий порожній список."""
    policy = ReadPolicy(disk_policy=DISK_POLICY_EXPLICIT)

    def read(symbol: str, after_ms: int, to_ms: int, limit: int) -> List[Dict[str, Any]]:
        spec = WindowSpec(symbol=symbol, tf_s=60, limit=limit, since_open_ms=after_ms, to_open_ms=to_ms)
        result = uds.read_window(spec, policy)
        if result.meta.get("source") == "degraded":
            raise M1ReadError(f"{symbol} ({after_ms}, {to_ms}]: {result.warnings}")
        return result.bars_lwc

    return read


def uds_last_close_reader(uds: Any) -> Callable[[str, int], Optional[float]]:
    """Close останньої фінальної M1 до `now_ms` — запасна ціна для взведення, коли тіків ще не було (закритий ринок,
    щойно стартував процес). Діапазон `(None, now]`, а не хвіст: хвіст без меж UDS кешує в RAM, і наступне читання
    віддало б застарілий бар."""
    policy = ReadPolicy(disk_policy=DISK_POLICY_EXPLICIT)

    def read(symbol: str, now_ms: int) -> Optional[float]:
        result = uds.read_window(WindowSpec(symbol=symbol, tf_s=60, limit=1, to_open_ms=now_ms), policy)
        if result.meta.get("source") == "degraded":
            raise M1ReadError(f"{symbol} last close: {result.warnings}")
        closes = [row.get("close") for row in result.bars_lwc if row.get("complete") is True]
        value = closes[-1] if closes else None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    return read


async def _close_pubsub(pubsub: Any) -> None:
    try:
        await pubsub.aclose()
    except (RedisError, OSError) as exc:
        _log.warning("ALERTS_PUBSUB_CLOSE_FAILED err=%s", exc)
