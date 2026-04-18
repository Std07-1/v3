"""
runtime/smc/wake_engine.py вЂ” WakeEngine: I/O orchestration for wake system (ADR-0049).

Lives in ws_server process (in-process, like SmcRunner).
Ticks every 2 seconds from _global_delta_loop.
Redis ops вЂ” via run_in_executor (existing pattern in ws_server.py).

Lifecycle:
    engine = WakeEngine(redis_client, namespace, executor, smc_runner)
    # in delta_loop, after SMC computations:
    await engine.tick(ts_ms)
    # for wire frame enrichment:
    presence = engine.get_presence()

Architecture:
    1. Load bot-defined conditions from Redis (cached 30s)
    2. Generate platform conditions via auto_wake (pure, $0)
    3. Merge (bot overrides platform for same kind+level)
    4. Check all via wake_check ($0)
    5. Tick accumulator ($0)
    6. If match в†’ LPUSH event to Redis list (via run_in_executor)
    7. Build PresenceStatus for wire frame

Invariants:
    - S1: read-only, does NOT write to UDS
    - I7: platform doesn't decide for РђСЂС‡С–, only informs
    - K2 fix: all Redis ops via run_in_executor (non-blocking in async loop)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from core.smc.auto_wake import generate_platform_conditions
from core.smc.wake_check import check_condition
from core.smc.wake_types import (
    AwarenessAccumulator,
    PresenceStatus,
    WakeCondition,
    WakeConditionKind,
    WakeEvent,
)

_log = logging.getLogger(__name__)

# в”Ђв”Ђ Timing constants в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
_BOT_CACHE_TTL = 30.0  # refresh bot conditions from Redis every 30s
_EVENT_LIST_MAX = 100  # max events in Redis list (LTRIM)
_PRESENCE_REFRESH_S = 60.0  # rebuild presence every 60s (for UI, not critical)


class WakeEngine:
    """Platform-side wake condition checker + event publisher.

    Reads SmcRunner state (snapshots, prices, ATR) вЂ” all in-memory, $0.
    Checks conditions. Fires events to Redis list for bot consumption.
    """

    def __init__(
        self,
        redis_client: Any,
        namespace: str,
        executor: ThreadPoolExecutor,
        smc_runner: Any,
        symbols: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._redis = redis_client
        self._ns = namespace
        self._executor = executor
        self._smc = smc_runner
        self._symbols = symbols or []
        self._config = config or {}

        # Bot-defined conditions cache (refreshed every 30s from Redis)
        self._bot_conditions: Dict[str, List[WakeCondition]] = {}  # {symbol: [...]}
        self._bot_cache_ts: float = 0.0

        # Accumulator per symbol
        self._accumulators: Dict[str, AwarenessAccumulator] = {}
        self._prev_prices: Dict[str, float] = {}
        self._last_wake_ts: Dict[str, int] = {}  # ms

        # Event dedup: {dedup_key: (ts_ms, price)} вЂ” suppress repeated events
        self._event_dedup: Dict[str, tuple] = {}

        # Presence cache
        self._presence: Dict[str, PresenceStatus] = {}

        _log.info(
            "WakeEngine initialized (ns=%s, symbols=%s)",
            namespace,
            self._symbols,
        )

    # в”Ђв”Ђ Main tick (called from delta_loop every 2s) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

    async def tick(self, ts_ms: int) -> None:
        """One WakeEngine tick. Called from _global_delta_loop."""
        loop = asyncio.get_event_loop()

        # Refresh bot conditions from Redis (cached 30s)
        now = time.time()
        if now - self._bot_cache_ts > _BOT_CACHE_TTL:
            try:
                await self._refresh_bot_conditions(loop)
                self._bot_cache_ts = now
            except Exception as exc:
                _log.debug("WakeEngine bot conditions refresh error: %s", exc)

        for symbol in self._symbols:
            try:
                await self._tick_symbol(symbol, ts_ms, loop)
            except Exception as exc:
                _log.debug("WakeEngine tick error sym=%s: %s", symbol, exc)

    async def _tick_symbol(
        self, symbol: str, ts_ms: int, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Tick one symbol: check conditions, accumulator, fire events."""
        # в”Ђв”Ђ Gather SMC state (all in-memory, $0) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        price = self._smc.get_last_price(symbol)
        if price <= 0:
            return

        prev_price = self._prev_prices.get(symbol, 0.0)
        self._prev_prices[symbol] = price

        # ATR: prefer H4, fallback to any available
        atr = 0.0
        for tf_s in (14400, 3600, 86400):
            atr = self._smc._engine.get_atr(symbol, tf_s)
            if atr > 1.0:
                break
        if atr <= 0:
            return

        # Snapshots for AutoWakeGenerator (multi-TF dict)
        snapshots = {}
        for tf_s in (14400, 3600, 86400):  # H4, H1, D1
            snap = self._smc.get_snapshot(symbol, tf_s)
            if snap is not None:
                snapshots[tf_s] = snap

        # Bias map (returns {"900": "bullish", ...} with string keys)
        bias_map_str = self._smc.get_bias_map(symbol)
        bias_map_int = {int(k): v for k, v in bias_map_str.items()}

        # Zone grades for H4 (primary analysis TF)
        zone_grades = self._smc.get_zone_grades(symbol, 14400)

        # Session info (from calendar if available)
        session_info = self._get_session_info(symbol)

        # в”Ђв”Ђ 1. Bot-defined conditions в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        bot_conds = self._bot_conditions.get(symbol, [])

        # в”Ђв”Ђ 2. Platform-generated conditions (pure, $0) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        platform_conds = generate_platform_conditions(
            snapshots=snapshots,
            bias_map=bias_map_int,
            atr=atr,
            current_price=price,
            session_info=session_info,
            ts_ms=ts_ms,
            zone_grades=zone_grades,
            config=self._config.get("wake_engine", {}),
        )

        # в”Ђв”Ђ 3. Merge (bot overrides platform for same kind) в”Ђв”Ђв”Ђв”Ђв”Ђ
        all_conditions = list(bot_conds) + [
            pc
            for pc in platform_conds
            if not any(
                bc.kind == pc.kind and bc.params.get("level") == pc.params.get("level")
                for bc in bot_conds
            )
        ]

        # ── 4. Check all conditions ($0) ─────────────────────────
        last_wake = self._last_wake_ts.get(symbol, 0)
        # GAP #9 / ADR-040 errata A2: drain recent BOS/CHoCH events for STRUCTURE_BREAK.
        # SmcRunner buffers events per symbol; we read events newer than last_wake.
        # Symbol-agnostic — same path for XAU/XAG/BTC/ETH.
        try:
            structure_events = self._smc.get_recent_structure_events(
                symbol, since_ts_ms=last_wake
            )
        except AttributeError:
            # Older SmcRunner without buffer — degraded-but-loud (I5)
            structure_events = []
            _log.warning(
                "WAKE_STRUCTURE_EVENTS_UNAVAILABLE sym=%s reason=smc_runner_missing_method",
                symbol,
            )
        fired: List[WakeCondition] = []
        for cond in all_conditions:
            if check_condition(
                cond,
                price,
                atr,
                session_info,
                ts_ms,
                last_wake_ts_ms=last_wake,
                structure_events=structure_events,
            ):
                fired.append(cond)

        # в”Ђв”Ђ 5. Tick accumulator ($0) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        acc = self._accumulators.get(symbol, AwarenessAccumulator())
        from core.smc.wake_check import accumulator_tick

        acc = accumulator_tick(acc, price, prev_price, atr, ts=time.time())
        self._accumulators[symbol] = acc

        accumulator_fired = acc.score >= acc.threshold

        # в”Ђв”Ђ 6. Fire events if needed (with dedup cooldown) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        if fired or accumulator_fired:
            reason = fired[0].reason if fired else f"accumulator_score={acc.score:.2f}"
            kind = fired[0].kind.value if fired else "accumulator"

            # Dedup: per-kind cooldown + zone aggregation (ADR-037)
            _we_cfg = self._config.get("wake_engine", {})
            _cooldowns = _we_cfg.get("event_cooldown_s", {})
            _cooldown_s = _cooldowns.get(kind, _cooldowns.get("_default", 600))
            _min_interval_ms = int(_cooldown_s) * 1000

            # Dedup key: zone aggregation when dedup_include_zone_id=false (ADR-037)
            _dedup_key = f"{symbol}:{kind}"
            if fired and _we_cfg.get("dedup_include_zone_id", False):
                _zone_id = fired[0].params.get("zone_id", "")
                if _zone_id:
                    _dedup_key = f"{symbol}:{kind}:{_zone_id}"
            _prev_event = self._event_dedup.get(_dedup_key)
            if _prev_event is not None:
                _prev_ts, _prev_price = _prev_event
                _elapsed_ms = ts_ms - _prev_ts
                _price_delta = abs(price - _prev_price)
                if _elapsed_ms < _min_interval_ms and _price_delta < atr * 0.5:
                    return  # suppress вЂ” same condition, no significant price change

            event = WakeEvent(
                ts_ms=ts_ms,
                symbol=symbol,
                kind=kind,
                reason=reason,
                price=price,
                meta={
                    "atr": round(atr, 2),
                    "session": session_info.get("current_session", ""),
                    "accumulator_score": round(acc.score, 2),
                    "conditions_total": len(all_conditions),
                    "conditions_fired": len(fired),
                    # ADR-037: aggregate all fired zones into meta
                    "fired_zones": (
                        [
                            {
                                "zone_id": c.params.get("zone_id", ""),
                                "zone_kind": c.params.get("zone_kind", ""),
                                "zone_high": c.params.get("zone_high", 0),
                                "zone_low": c.params.get("zone_low", 0),
                            }
                            for c in fired
                            if c.params.get("zone_id")
                        ]
                        if fired
                        else []
                    ),
                },
            )

            # LPUSH to Redis list (via executor вЂ” non-blocking)
            try:
                event_json = json.dumps(
                    {
                        "ts_ms": event.ts_ms,
                        "symbol": event.symbol,
                        "kind": event.kind,
                        "reason": event.reason,
                        "price": event.price,
                        "meta": event.meta,
                    },
                    ensure_ascii=False,
                )

                key = f"{self._ns}:wake:events"
                await loop.run_in_executor(
                    self._executor,
                    self._redis_push_event,
                    key,
                    event_json,
                )
                _log.info(
                    "WAKE_EVENT fired: sym=%s kind=%s price=%.2f reason='%s'",
                    symbol,
                    kind,
                    price,
                    reason[:80],
                )
            except Exception as exc:
                _log.warning("WakeEngine Redis push error: %s", exc)

            # Reset accumulator + update last wake + dedup
            self._accumulators[symbol] = AwarenessAccumulator(
                threshold=acc.threshold,
                decay=acc.decay,
                last_wake_price=price,
            )
            self._last_wake_ts[symbol] = ts_ms
            self._event_dedup[_dedup_key] = (ts_ms, price)

        # в”Ђв”Ђ 7. Build presence status в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        self._presence[symbol] = PresenceStatus(
            status="watching" if all_conditions else "sleeping",
            focus=(
                fired[0].reason[:100]
                if fired
                else (all_conditions[0].reason[:100] if all_conditions else "")
            ),
            silence_since_h=(ts_ms - last_wake) / 3_600_000 if last_wake > 0 else 0.0,
            next_possible_wake=self._describe_next_wake(all_conditions),
            active_conditions=len(all_conditions),
            accumulator_score=round(acc.score, 2),
            accumulator_threshold=acc.threshold,
        )

    # в”Ђв”Ђ Public API в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

    def get_presence(self, symbol: str = "") -> PresenceStatus:
        """Return cached presence for wire frame enrichment."""
        if symbol:
            return self._presence.get(symbol, PresenceStatus())
        # Return first symbol's presence if no symbol specified
        if self._presence:
            return next(iter(self._presence.values()))
        return PresenceStatus()

    # в”Ђв”Ђ Redis helpers (sync вЂ” called via run_in_executor) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

    def _redis_push_event(self, key: str, event_json: str) -> None:
        """Sync Redis LPUSH + LTRIM. Called via executor."""
        self._redis.lpush(key, event_json)
        self._redis.ltrim(key, 0, _EVENT_LIST_MAX - 1)

    def _redis_load_bot_conditions(self) -> Dict[str, List[WakeCondition]]:
        """Sync Redis GET for bot-defined conditions. Called via executor."""
        result: Dict[str, List[WakeCondition]] = {}
        for symbol in self._symbols:
            key = f"{self._ns}:wake:conditions:{symbol.replace('/', '_')}"
            try:
                raw = self._redis.get(key)
                if raw:
                    data = json.loads(raw)
                    conditions = []
                    for item in data:
                        kind_str = item.get("kind", "")
                        try:
                            kind = WakeConditionKind(kind_str)
                        except ValueError:
                            continue
                        conditions.append(
                            WakeCondition(
                                kind=kind,
                                params=item.get("params", {}),
                                reason=item.get("reason", ""),
                                source=item.get("source", "bot"),
                                created_at_ms=item.get("created_at_ms", 0),
                            )
                        )
                    result[symbol] = conditions
            except Exception as exc:
                _log.debug("Bot conditions load error sym=%s: %s", symbol, exc)
        return result

    async def _refresh_bot_conditions(self, loop: asyncio.AbstractEventLoop) -> None:
        """Refresh bot conditions from Redis (via executor)."""
        self._bot_conditions = await loop.run_in_executor(
            self._executor,
            self._redis_load_bot_conditions,
        )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _get_session_info(self, symbol: str) -> Dict[str, Any]:
        """Resolve current session + market-open state for ``symbol``.

        GAP #10 (ADR-040 §session, ADR-0035): MarketCalendar exposes only
        ``is_trading_minute(now_ms)`` — no session attributes. Earlier
        implementation looked for ``current_session`` / ``is_open`` attrs that
        do not exist → always returned ``{}`` → SESSION_OPEN wake conditions
        could never fire and ``meta.session`` was always empty string.

        Now: derive ``current_session`` + ``in_killzone`` via
        ``core.smc.sessions.get_current_session`` (SSOT, ADR-0035) using
        ``SmcEngine._session_windows``. Market-open state via the per-symbol
        ``MarketCalendar.is_trading_minute``.
        """
        info: Dict[str, Any] = {
            "current_session": "",
            "in_killzone": False,
            "is_open": True,  # default open for 24/7 symbols (BTCUSDT etc.)
        }
        try:
            engine = getattr(self._smc, "_engine", None)
            sess_windows = getattr(engine, "_session_windows", None) if engine else None
            sess_cfg = getattr(getattr(engine, "_config", None), "sessions", None)
            if sess_windows and getattr(sess_cfg, "enabled", False):
                from core.smc.sessions import get_current_session

                now_ms = int(time.time() * 1000)
                name, in_kz = get_current_session(now_ms, sess_windows)
                if name and name != "off_session":
                    info["current_session"] = name
                    info["in_killzone"] = bool(in_kz)
        except Exception as e:  # I5: degraded-but-loud (rate-limited at logger level)
            _log.debug("WAKE_SESSION_LOOKUP_FAIL symbol=%s err=%s", symbol, e)

        try:
            calendars = getattr(self._smc, "_calendars", {})
            cal = calendars.get(symbol) if isinstance(calendars, dict) else None
            if cal is not None and hasattr(cal, "is_trading_minute"):
                info["is_open"] = bool(cal.is_trading_minute(int(time.time() * 1000)))
        except Exception as e:
            _log.debug("WAKE_CALENDAR_LOOKUP_FAIL symbol=%s err=%s", symbol, e)

        return info

    @staticmethod
    def _describe_next_wake(conditions: List[WakeCondition]) -> str:
        """Human-readable description of next possible wake for presence."""
        if not conditions:
            return ""
        parts = []
        for c in conditions[:3]:
            if c.kind == WakeConditionKind.PRICE_ZONE_TOUCH:
                zl = c.params.get("zone_low", 0)
                zh = c.params.get("zone_high", 0)
                parts.append(f"zone {zl:.0f}-{zh:.0f}")
            elif c.kind == WakeConditionKind.SESSION_OPEN:
                parts.append(f"{c.params.get('session', '?')} open")
            elif c.kind == WakeConditionKind.MAX_SILENCE:
                parts.append(f"max {c.params.get('hours', '?')}h silence")
            else:
                parts.append(c.kind.value)
        return " | ".join(parts)
