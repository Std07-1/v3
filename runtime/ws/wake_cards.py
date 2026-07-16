"""runtime.ws.wake_cards — «Очі Арчі»: read-side складання спостережуваності агента.

Платформа джойнить приватні джерела трейдера Арчі (data_dir консолі) у ГОТОВІ картки
для SPA «Очі Арчі» — UI лишається dumb renderer (X28): фронт нічого не перераховує,
лише малює готові поля. Дві поверхні:

  • **Кіноплівка пробуджень** (`read_wake_cards`) — кожне пробудження = одна картка:
    рядок ``v3_wake_log.jsonl`` (SSOT списку) verbatim + класифікація (category/alert)
    + дзеркало ``v3_wake_trace.jsonl`` (ADR-097) + найближчий thinking-запис.
  • **Стан зараз** (`build_now_view`) — знімок фокусу: presence, директиви, теза, ціна
    та армовані рівні з СЕРВЕРНИМИ delta/delta_pct до current price (X28: фронт не рахує).

Усе pure (без aiohttp/Redis/файлів у ядрі — I/O лишається у ws_server): картки/знімок
збираються з уже прочитаних dict-ів, тому тестуються tmp-фікстурами без мережі.

Інваріанти: I5 degraded-but-loud (малформні рядки — скіп із лічильником; недоступне
джерело → поле null + запис у ``degraded[]``), I7/S1 read-only (платформа лише читає
стан Арчі, ніколи не пише за нього), X28 (класифікація/дельти рахуються тут, не у UI),
X31 (нуль імпортів trader-коду — контракт дзеркалиться, див. блок класифікації).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("wake_cards")

# ── Файли-джерела (пишуться ботом Арчі у agent_console.data_dir) ──────────────
WAKE_LOG_FILE = "v3_wake_log.jsonl"  # SSOT списку пробуджень (trader-v3 wake_log.py)
WAKE_TRACE_FILE = "v3_wake_trace.jsonl"  # durable дзеркало пробудження (trader-v3 ADR-097)

# ── Межі пагінації кіноплівки ────────────────────────────────────────────────
WAKE_LIMIT_MIN = 1
WAKE_LIMIT_MAX = 100

# thinking-джойн: nearest запис по ts з |Δ| ≤ вікна ТА збігом call_type.
_THINKING_JOIN_WINDOW_S = 600  # ±10 хв — типовий лаг між wake-логом і thinking-архівом

# «стан зараз» вважається застарілим, якщо agent:state не оновлювався довше цього.
# Бот перезаписує agent:state кожен цикл (~30с) і при кожному reactive-виклику; TTL
# ключа = 6h. >15 хв тиші → бот ймовірно завис/спить → фронт показує «зв'язок застиг».
STATE_STALE_MS = 15 * 60 * 1000

# ── Класифікація тригерів — ДЗЕРКАЛО trader-v3 bot/state/wake_log.py ──────────
# Платформа НЕ може імпортувати trader-код (X31 cross-repo isolation), тому pure-логіка
# classify_wake / categorize_wake продубльована тут як read-side дзеркало контракту.
# КОНТРАКТ-ДРЕЙФ: якщо змінюється категоризація у trader-v3 wake_log.py — оновити тут
# (consistency перевіряється tests/test_ochi_wake_cards.py). Джерело: ADR-079 §11.2/§11.
_ALERT_REASON = re.compile(r"fired|virtual|hit!", re.IGNORECASE)
_RITUAL_TIMER_IDS = frozenset(
    {"morning_briefing", "evening_consciousness", "curator_review"}
)


def classify_alert(record: dict) -> bool:
    """True iff справжній тригер спрацював (alert), False = рутина (тихо).

    Дзеркало ``wake_log.classify_wake`` (ADR-079 §11.2): platform-умова
    (``call_type=platform_wake``) або fire/event-reason (``fired``/``virtual``/``hit!``).
    Waiting-reasons («чекаю CHoCH») навмисно НЕ alert — ще нічого не сталось.
    """
    if (record.get("call_type") or "") == "platform_wake":
        return True
    return bool(_ALERT_REASON.search(str(record.get("reason") or "")))


def categorize_wake(record: dict) -> str:
    """Назва типу тригера: heartbeat / wake_at / watch / ritual / vp / other.

    Дзеркало ``wake_log.categorize_wake`` (ADR-079 §11). heartbeat = self-armed
    ``next_check`` каденс; wake_at = названий one-off таймер; watch = ринковий рівень
    (platform); ritual = фіксований щоденний; vp = подія virtual-position.
    """
    call_type = record.get("call_type") or ""
    reason = str(record.get("reason") or "")
    if call_type == "platform_wake" or reason.startswith("Watch level fired"):
        return "watch"
    if reason.startswith("virtual_position"):
        return "vp"
    if reason.startswith("timer:"):
        rest = reason[6:].strip()
        if not rest:
            return "wake_at"  # malformed «timer:» без назви — все ще сім'я таймерів
        tid = rest.split()[0].rstrip("-:")
        if tid == "next_check_heartbeat":
            return "heartbeat"
        if tid in _RITUAL_TIMER_IDS:
            return "ritual"
        return "wake_at"
    return "other"


# ── Кіноплівка пробуджень ─────────────────────────────────────────────────────
_TRACE_FIELDS = ("mirror", "mirror_light", "ack", "emit_warning", "message")


def _load_jsonl(path: Path) -> list[dict]:
    """Прочитати JSONL у list[dict], скіпнувши малформні рядки тихо-але-раховано (I5).

    Відсутній файл → ``[]`` (стара інсталяція без trace). Порядок = порядок файла
    (append-only: oldest→newest).
    """
    try:
        if not path.exists():
            return []
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        _log.warning("WAKE_CARDS_READ_FILE_FAIL: %s (%s)", path.name, exc)
        return []
    out: list[dict] = []
    skipped = 0
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            skipped += 1
            continue
        if isinstance(rec, dict):
            out.append(rec)
        else:
            skipped += 1
    if skipped:
        _log.warning("WAKE_CARDS_BAD_LINES: %s skipped=%d", path.name, skipped)
    return out


def _index_traces(records: list[dict]) -> tuple[dict[str, dict], dict[int, dict]]:
    """Побудувати індекси trace: за wake_id та (fallback) за точним ts."""
    by_id: dict[str, dict] = {}
    by_ts: dict[int, dict] = {}
    for rec in records:
        wid = str(rec.get("wake_id") or "").strip()
        if wid:
            by_id[wid] = rec
        ts = rec.get("ts")
        if isinstance(ts, int):
            by_ts[ts] = rec
    return by_id, by_ts


def _match_trace(
    wake: dict, by_id: dict[str, dict], by_ts: dict[int, dict]
) -> Optional[dict]:
    """Дзеркало пробудження: join по wake_id; якщо id порожній — fallback по точному ts.

    Старі пробудження (до ADR-097) trace не мають → повертає ``None`` (картка
    gracefully деградує: ``mirror`` тощо будуть відсутні).
    """
    wid = str(wake.get("wake_id") or "").strip()
    trace = by_id.get(wid) if wid else by_ts.get(wake.get("ts"))
    if not trace:
        return None
    return {field: trace.get(field) for field in _TRACE_FIELDS}


# thinking-архів трейдера штампує лише ТРИ мітки call_type [VERIFIED trader-v3
# bot/agent/core.py:1694 'reactive' / :2679 'proactive' / :3309 'daily_review'] —
# 'proactive' = парасолька для ВСІХ проактивних пробуджень (platform_wake, observation,
# curator, awareness_accumulator, …), тоді як wake_log пише ФАКТИЧНИЙ call_type.
# Без нормалізації thinking не джойнився б саме для alert-карток (platform_wake).
_ARCHIVE_EXACT_LABELS = frozenset({"reactive", "daily_review"})


def _archive_call_type(wake_call_type: Any) -> str:
    """Мітка, під якою thinking-архів зберіг би запис для цього wake call_type."""
    label = str(wake_call_type or "")
    return label if label in _ARCHIVE_EXACT_LABELS else "proactive"


def _assign_thinking(
    page: list[dict], thinking_records: Optional[list[dict]]
) -> dict[int, tuple[Optional[str], Optional[int]]]:
    """1:1 джойн: кожен thinking-запис віддається щонайбільше ОДНІЙ картці сторінки.

    Наївний per-wake nearest тягнув ОДИН запис на кілька сусідніх пробуджень
    (mis-attribution: сусідній heartbeat без власного thinking показував чуже
    reasoning — ADR-0088 R3). Тут навпаки: для кожного thinking-запису шукаємо
    найближчий wake (|Δts| ≤ вікна + family call_type), потім кожен wake бере
    найближчий із «своїх» записів. Повертає {index сторінки: (thinking, ts)}.

    ``thinking_records`` — заздалегідь прочитаний зріз архіву (ws_server передає
    результат ``_read_thinking_records``). Best-effort: архів не несе wake_id
    (точного 1:1 ключа не існує на боці writer-а), тож ts-skew лишається
    теоретично можливим — але дублювання одного запису виключене структурно.
    """
    if not thinking_records:
        return {}
    best_wake_for_rec: dict[int, tuple[float, int]] = {}
    for wake_idx, wake in enumerate(page):
        wake_ts = wake.get("ts")
        if not isinstance(wake_ts, (int, float)):
            continue
        family = _archive_call_type(wake.get("call_type"))
        for rec_idx, rec in enumerate(thinking_records):
            if rec.get("call_type") != family:
                continue
            rec_ts = rec.get("ts")
            if not isinstance(rec_ts, (int, float)):
                continue
            delta = abs(rec_ts - wake_ts)
            if delta > _THINKING_JOIN_WINDOW_S:
                continue
            current = best_wake_for_rec.get(rec_idx)
            if current is None or delta < current[0]:
                best_wake_for_rec[rec_idx] = (delta, wake_idx)
    assigned: dict[int, tuple[Optional[str], Optional[int]]] = {}
    assigned_delta: dict[int, float] = {}
    for rec_idx, (delta, wake_idx) in best_wake_for_rec.items():
        if wake_idx in assigned_delta and delta >= assigned_delta[wake_idx]:
            continue
        rec = thinking_records[rec_idx]
        assigned_delta[wake_idx] = delta
        assigned[wake_idx] = (rec.get("thinking"), rec.get("ts"))
    return assigned


def read_wake_cards(
    data_dir: str,
    limit: int,
    before_ts: Optional[int] = None,
    thinking_records: Optional[list[dict]] = None,
) -> tuple[list[dict], int, Optional[int]]:
    """Зібрати кіноплівку пробуджень — ГОТОВІ картки для UI (X28).

    Джерела (усі read-only, I7): ``v3_wake_log.jsonl`` = SSOT списку; ``v3_wake_trace.jsonl``
    = durable дзеркало (ADR-097); thinking-архів (переданий ``thinking_records``).

    Картка = ВСІ поля wake_log-запису verbatim + ``{category, alert, trace|null,
    thinking|null, thinking_ts|null}``.

    Пагінація — keyset newest-first: повертає ≤``limit`` карток з ``ts < before_ts``
    (``None`` = найновіша сторінка). ``oldest_ts`` = ts найстарішої картки сторінки
    (курсор наступної сторінки), ``None`` якщо сторінка порожня.

    Повертає ``(cards, total, oldest_ts)`` де ``total`` — повна кількість пробуджень
    у логу (для «показано X з N»).
    """
    root = Path(data_dir)
    wakes = _load_jsonl(root / WAKE_LOG_FILE)
    valid = [w for w in wakes if isinstance(w.get("ts"), (int, float))]
    total = len(valid)

    newest_first = sorted(valid, key=lambda w: w["ts"], reverse=True)
    if before_ts is not None:
        newest_first = [w for w in newest_first if w["ts"] < before_ts]
    page = newest_first[:limit]

    by_id, by_ts = _index_traces(_load_jsonl(root / WAKE_TRACE_FILE))

    thinking_by_idx = _assign_thinking(page, thinking_records)
    cards: list[dict] = []
    for wake_idx, wake in enumerate(page):
        card = dict(wake)  # усі поля wake_log verbatim
        card["category"] = categorize_wake(wake)
        card["alert"] = classify_alert(wake)
        card["trace"] = _match_trace(wake, by_id, by_ts)
        thinking, thinking_ts = thinking_by_idx.get(wake_idx, (None, None))
        card["thinking"] = thinking
        card["thinking_ts"] = thinking_ts
        cards.append(card)

    oldest_ts = page[-1]["ts"] if page else None
    return cards, total, oldest_ts


def clamp_wake_limit(raw: Any, default: int = 30) -> int:
    """Затиснути ?limit до [WAKE_LIMIT_MIN, WAKE_LIMIT_MAX]; сміття → ``default``."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(WAKE_LIMIT_MIN, min(WAKE_LIMIT_MAX, value))


# ── Стан зараз ────────────────────────────────────────────────────────────────
# Директиви: whitelist полів «стану зараз» (решта файла — приватна історія/пам'ять).
# Реальний файл (trader-v3/data/v3_agent_directives.json) не завжди має нові поля
# (inner_thought_ts — ADR-094; last_emit_ack — новіше за last_emit_warning), тому
# .get() з дефолтом + optional-набір «if key present».
_DIRECTIVES_PASSTHROUGH = (
    "mood",
    "inner_thought",
    "active_scenario",
    "virtual_position",
    "kill_switch_active",
    "consecutive_errors",
    "budget_strategy",
    "next_check_minutes",
    "next_check_reason",
)
_DIRECTIVES_OPTIONAL = (
    "inner_thought_ts",
    "last_emit_ack",
    "last_emit_warning",
    "token_usage_today",
    "estimated_cost_usd_today",
    "agent_calls_today",
    "economy_mode_active",
)
_THESIS_PASSTHROUGH = ("thesis", "conviction", "key_level", "invalidation")
_THESIS_FLOAT_FIELDS = ("key_level_price", "invalidation_price")


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _directives_view(directives: dict) -> dict:
    """Whitelist «стану зараз» з директив — pass-through (X28: без перерахунку)."""
    view: dict[str, Any] = {key: directives.get(key) for key in _DIRECTIVES_PASSTHROUGH}
    view["thought_history"] = (directives.get("thought_history") or [])[-3:]
    view["watch_levels"] = directives.get("watch_levels") or []
    view["wake_at"] = directives.get("wake_at") or []
    view["wake_conditions"] = directives.get("wake_conditions") or []
    for key in _DIRECTIVES_OPTIONAL:
        if key in directives:
            view[key] = directives[key]
    return view


def _thesis_view(thesis: dict, now_ms: int) -> dict:
    """Whitelist тези + серверний ``age_ms`` (X28: вік рахує бекенд, не UI)."""
    view: dict[str, Any] = {key: thesis.get(key, "") for key in _THESIS_PASSTHROUGH}
    for key in _THESIS_FLOAT_FIELDS:
        parsed = _to_float(thesis.get(key))
        if parsed is not None:
            view[key] = parsed
    updated_ms = _to_int(thesis.get("updated_at_ms")) or 0
    view["updated_at_ms"] = updated_ms
    view["age_ms"] = (now_ms - updated_ms) if updated_ms > 0 else None
    return view


def _cond_level(kind: str, params: dict) -> Optional[float]:
    """Числовий рівень price-умови. Схема директив історично використовує ключ
    ``price`` для price_cross (реальні дані), Redis-canonical (ADR-078) — ``level``;
    підтримуємо обидва. price_zone_touch обробляється окремо (два ребра зони)."""
    if kind in ("price_cross", "candle_close"):
        return _to_float(params.get("level")) or _to_float(params.get("price"))
    return None


def _armed_item(
    level: float,
    direction: str,
    source: str,
    kind: str,
    ident: Any,
    price: Optional[float],
) -> dict:
    """Один армований рівень + СЕРВЕРНІ delta/delta_pct до current price (X28)."""
    delta: Optional[float] = None
    delta_pct: Optional[float] = None
    if price is not None and price > 0:
        delta = round(level - price, 5)
        delta_pct = round(delta / price * 100, 4)
    return {
        "level": level,
        "direction": direction,
        "source": source,
        "kind": kind,
        "id": ident,
        "delta": delta,
        "delta_pct": delta_pct,
    }


def _armed_levels(directives: dict, price: Optional[float]) -> list[dict]:
    """Армовані рівні з watch_levels + wake_conditions, з дельтами до ціни (X28).

    Сортування: найближчі до ціни першими (корисний порядок для UI). Рівні без
    валідного числа — скіп.
    """
    items: list[dict] = []
    for watch in directives.get("watch_levels") or []:
        level = _to_float(watch.get("price"))
        if level is None:
            continue
        items.append(
            _armed_item(
                level, str(watch.get("direction") or ""), "watch_level",
                "watch_level", watch.get("id"), price,
            )
        )
    for cond in directives.get("wake_conditions") or []:
        kind = str(cond.get("kind") or "")
        params = cond.get("params") or {}
        if kind == "price_zone_touch":
            for edge in ("zone_high", "zone_low"):
                level = _to_float(params.get(edge))
                if level is not None:
                    items.append(
                        _armed_item(level, edge, "wake_condition", kind, cond.get("id"), price)
                    )
            continue
        level = _cond_level(kind, params)
        if level is None:
            continue
        items.append(
            _armed_item(
                level, str(params.get("direction") or ""), "wake_condition",
                kind, cond.get("id"), price,
            )
        )
    items.sort(key=lambda a: abs(a["delta"]) if a["delta"] is not None else float("inf"))
    return items


def build_now_view(
    *,
    symbol: str,
    state: Optional[dict],
    directives: Optional[dict],
    thesis: Optional[dict],
    price: Optional[float],
    now_ms: int,
    degraded: list[str],
) -> dict:
    """Pure: (presence, директиви, теза, ціна) → знімок «стан зараз» (ГОТОВИЙ для UI).

    Кожне джерело незалежне: недоступне → відповідне поле ``null`` + запис у
    ``degraded`` (handler передає причини I/O-збоїв; тут додаються семантичні,
    напр. ``state_stale``). HTTP лишається 200 — деградація гучна, не 500 (I5).
    ``armed`` рахується сервером (X28): фронт лише малює delta/delta_pct.
    """
    if state is None:
        # agent:state зник (Redis TTL 6h минув на мертвому боті) = точно НЕ свіжий.
        # Без цього UI показував би «живого» Арчі саме коли він мертвий найдовше —
        # інвертований degraded-loud (I5); причину (state_no_data/…) кладе handler.
        stale = True
    else:
        ts_ms = _to_int(state.get("ts_ms"))
        stale = ts_ms is None or (now_ms - ts_ms) > STATE_STALE_MS
        if stale:
            degraded.append("state_stale")

    directives_view = _directives_view(directives) if directives else None
    thesis_view = _thesis_view(thesis, now_ms) if thesis else None
    armed = _armed_levels(directives, price) if directives else []

    return {
        "symbol": symbol,
        "generated_ms": now_ms,
        "price": price,
        "stale": stale,
        "state": state,
        "directives": directives_view,
        "thesis": thesis_view,
        "armed": armed,
        "degraded": degraded,
    }
