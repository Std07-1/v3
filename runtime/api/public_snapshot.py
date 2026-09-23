"""Публічний санітизований знімок Арчі — ADR-0086 (gorn_public_v1).

Єдина точка, де внутрішній стан Арчі перетворюється на публічний контракт для
ui_archi_v2 (ГОРН). Правило: whitelist-only — жодне поле не проходить «бо було
в джерелі»; нове публічне поле = правка ADR-0086 §D1.

Джерела (усі read-only, I7):
  - Redis HASH   ``{ns}:agent:state``               → presence
  - файл         ``{data_dir}/v3_agent_directives.json`` → active_scenario (числа)
  - Redis STRING ``{ns}:wake:conditions:{sym_/→_}`` → армовані рівні (ADR-078 canonical)
    fallback: ``directives["wake_conditions"]`` (той самий формат)

Pure-ядро ``build_public_snapshot`` не має I/O — тестується без Redis/файлів.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from aiohttp import web

_log = logging.getLogger("public_snapshot")

# presence: рівно ті ключі agent:state, що потрібні кільцю/голосу ГОРНа (ADR-0086 §D1).
# inner_thought ВИЛУЧЕНО (rev1, owner 2026-07-12): сирі думки не для інвесторів —
# публічний голос = теза сценарію; майбутнє public_thought/воркспейс = trader-v3 ADR.
_PRESENCE_STATE_KEYS = (
    "ts_ms",
    "mood",
    "health",
    "circuit_breaker",
    "has_virtual_position",
    "active_scenarios",
    "next_wake_ms",
    "next_wake_reason",
)

_PRICE_KINDS = ("price_cross", "candle_close")
_LEVEL_MATCH_EPS = 0.5  # звірка рівня wake-умови зі структурним полем сценарію


def _label_for_level(level: float, scenario: dict[str, Any]) -> tuple[str, str]:
    """Роль+підпис рівня через звірку зі сценарієм — НЕ вигаданий текст (X28)."""
    invalidation = scenario.get("invalidation")
    trigger_level = (scenario.get("trigger") or {}).get("level")
    entry_high = scenario.get("entry_zone_high")
    entry_low = scenario.get("entry_zone_low")
    targets = scenario.get("targets") or []

    def _near(ref: Any) -> bool:
        return ref is not None and abs(level - float(ref)) < _LEVEL_MATCH_EPS

    if _near(invalidation):
        return "invalidation", "інвалідація — теза скасовується"
    if _near(trigger_level):
        return "trigger", "тригер входу"
    if _near(entry_high) or _near(entry_low):
        return "entry", "зона входу"
    if any(_near(t) for t in targets):
        return "target", "ціль"
    return "watch", "рівень уваги"


def _armed_levels(
    scenario: dict[str, Any], wake_conditions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Армовані price-рівні, підписані звіркою; дедуп за рівнем, сорт зверху вниз."""
    by_level: dict[float, dict[str, Any]] = {}
    for cond in wake_conditions:
        if cond.get("kind") not in _PRICE_KINDS:
            continue
        params = cond.get("params") or {}
        raw_level = params.get("level")
        if raw_level is None:
            continue
        level = float(raw_level)
        role, label = _label_for_level(level, scenario)
        prev = by_level.get(level)
        # структурна роль важливіша за «рівень уваги» при дедупі одного рівня
        if prev is None or (prev["role"] == "watch" and role != "watch"):
            by_level[level] = {
                "level": level,
                "direction": params.get("direction", ""),
                "role": role,
                "label": label,
            }
    # Інваріант ADR-0086: активний сценарій → watching непорожній. Якщо wake-умови
    # ще не озброєні — синтезуємо позначки зі структурних рівнів самого сценарію.
    if not by_level and scenario:
        synth: list[tuple[Any, str, str]] = []
        if scenario.get("invalidation") is not None:
            synth.append(
                (scenario["invalidation"], "invalidation", "інвалідація — теза скасовується")
            )
        if scenario.get("entry_zone_high") is not None:
            synth.append((scenario["entry_zone_high"], "entry", "зона входу"))
        for target in scenario.get("targets") or []:
            synth.append((target, "target", "ціль"))
        for raw_level, role, label in synth:
            level = float(raw_level)
            by_level[level] = {"level": level, "direction": "", "role": role, "label": label}
    return sorted(by_level.values(), key=lambda item: item["level"], reverse=True)


def _focus_from_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    """Публічний фокус: числа сценарію БЕЗ reasoning/checkpoints/id/trigger (§D1)."""
    confidence = scenario.get("confidence")
    entry_low = scenario.get("entry_zone_low")
    entry_high = scenario.get("entry_zone_high")
    return {
        "symbol": scenario.get("symbol", ""),
        "direction": scenario.get("direction"),
        "thesis": scenario.get("thesis", ""),
        "session": scenario.get("session", ""),
        "grade": scenario.get("grade", ""),
        "confidence_pct": (
            round(confidence * 100) if isinstance(confidence, (int, float)) else None
        ),
        "bias": scenario.get("bias", ""),
        "entry_zone": (
            [entry_low, entry_high]
            if entry_low is not None and entry_high is not None
            else None
        ),
        "invalidation": scenario.get("invalidation"),
        "targets": scenario.get("targets", []),
        "status": scenario.get("status", ""),
    }


def _scenario_symbol(scenario: dict[str, Any], wake_conditions: list[dict[str, Any]]) -> str:
    if scenario.get("symbol"):
        return str(scenario["symbol"])
    for cond in wake_conditions:
        symbol = (cond.get("params") or {}).get("symbol")
        if symbol:
            return str(symbol)
    return ""


def build_public_snapshot(
    state: dict[str, Any],
    directives: dict[str, Any],
    wake_conditions: list[dict[str, Any]],
    now_ms: int,
) -> dict[str, Any]:
    """Pure: (agent:state, директиви, wake-умови) → публічний знімок (ADR-0086 §D1)."""
    scenario = directives.get("active_scenario") or {}
    if scenario and not scenario.get("symbol"):
        scenario = {**scenario, "symbol": _scenario_symbol(scenario, wake_conditions)}

    presence: dict[str, Any] = {k: state.get(k, "") for k in _PRESENCE_STATE_KEYS}
    presence["mood"] = directives.get("mood") or state.get("mood") or ""
    presence["kill_switch_active"] = bool(directives.get("kill_switch_active"))
    # last_error РЕДАКТОВАНО до маркера: текст може нести шляхи/внутрішнє (§D1)
    presence["last_error"] = "1" if state.get("last_error") else ""
    presence["has_active_scenario"] = bool(scenario)
    presence["session"] = state.get("market_session", "")

    return {
        "generated_ms": now_ms,
        "presence": presence,
        "focus": _focus_from_scenario(scenario) if scenario else None,
        "watching": _armed_levels(scenario, wake_conditions),
    }


def register_public_snapshot(
    app: web.Application,
    redis_client: Any,
    namespace: str,
    data_dir: str,
    ttl_s: float = 5.0,
) -> None:
    """Маунтить GET /api/public/snapshot — БЕЗ auth (рішення ADR-0086, не недогляд).

    In-proc TTL-кеш: будь-який трафік коштує ≤1 читання джерел на ``ttl_s``.
    Redis/файл недоступні → 503 (I5; фронт має чесний стан «зв'язок втрачено»).
    """
    cache: dict[str, Any] = {"at": 0.0, "body": None}

    def _read_wake_conditions(directives: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
        if symbol and redis_client is not None:
            key = f"{namespace}:wake:conditions:{symbol.replace('/', '_')}"
            try:
                raw = redis_client.get(key)
                if raw:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return parsed
            except Exception as exc:  # noqa: BLE001 — деградуємо на fallback гучно
                _log.warning("PUBLIC_SNAPSHOT_WAKE_READ_FAIL: %s", exc)
        fallback = directives.get("wake_conditions")
        return fallback if isinstance(fallback, list) else []

    async def _handler(_request: web.Request) -> web.Response:
        now = time.time()
        if cache["body"] is not None and now - cache["at"] < ttl_s:
            return web.json_response(cache["body"])
        if redis_client is None:
            return web.json_response({"error": "redis_not_configured"}, status=503)
        try:
            state = redis_client.hgetall(f"{namespace}:agent:state") or {}
            directives: dict[str, Any] = {}
            directives_path = os.path.join(data_dir, "v3_agent_directives.json")
            if data_dir and os.path.exists(directives_path):
                with open(directives_path, "r", encoding="utf-8") as fh:
                    directives = json.loads(fh.read())
            scenario = directives.get("active_scenario") or {}
            symbol = _scenario_symbol(scenario, [])
            wake_conditions = _read_wake_conditions(directives, symbol)
            body = build_public_snapshot(
                state, directives, wake_conditions, int(now * 1000)
            )
            cache["at"], cache["body"] = now, body
            return web.json_response(body)
        except Exception as exc:  # noqa: BLE001 — I5: 503 замість тихого сміття
            _log.warning("PUBLIC_SNAPSHOT_FAIL: %s", exc)
            return web.json_response({"error": "snapshot_build_failed"}, status=503)

    app.router.add_get("/api/public/snapshot", _handler)
    _log.info("PUBLIC_SNAPSHOT: mounted /api/public/snapshot ttl=%.1fs (ADR-0086)", ttl_s)
