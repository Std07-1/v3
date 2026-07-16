"""Unit tests для «Очі Арчі» read-side helpers (ADR-0088).

Покриває pure-ядро ``runtime.ws.wake_cards`` що бек-ендить два endpoint-и:
``GET /api/archi/wakes`` (кіноплівка пробуджень) та ``GET /api/archi/now``
(стан зараз). Джойн трьох джерел трейдера у ГОТОВІ картки — UI = dumb renderer
(X28). Усі шляхи pure (tmp-dir фікстури, без aiohttp / Redis).
"""
from __future__ import annotations

import json
from pathlib import Path

from runtime.ws.wake_cards import (
    STATE_STALE_MS,
    build_now_view,
    categorize_wake,
    classify_alert,
    clamp_wake_limit,
    read_wake_cards,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Append-only JSONL, oldest→newest — формат writer-а (trader-v3 wake_log.py)."""
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _wake(ts: int, wake_id: str, **extra) -> dict:
    base = {"ts": ts, "wake_id": wake_id, "reason": "timer:next_check_heartbeat",
            "call_type": "proactive", "delivered": True, "price": 4700.0}
    base.update(extra)
    return base


# ── read_wake_cards: join + degradation ───────────────────────────────────────


def test_wake_card_joins_trace_by_wake_id(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "v3_wake_log.jsonl", [_wake(1000, "w1")])
    _write_jsonl(
        tmp_path / "v3_wake_trace.jsonl",
        [{"ts": 1000, "wake_id": "w1", "mirror": "⏰ Розбудило", "mirror_light": True,
          "ack": "ok", "emit_warning": "", "message": "текст"}],
    )
    cards, total, oldest_ts = read_wake_cards(str(tmp_path), limit=30)
    assert total == 1
    assert oldest_ts == 1000
    assert cards[0]["trace"]["mirror"] == "⏰ Розбудило"
    assert cards[0]["trace"]["mirror_light"] is True
    # wake_log-поля лишаються verbatim у картці
    assert cards[0]["price"] == 4700.0


def test_wake_card_mirror_null_when_no_trace(tmp_path: Path) -> None:
    # Старе пробудження до ADR-097: trace-файла нема → mirror gracefully відсутній.
    _write_jsonl(tmp_path / "v3_wake_log.jsonl", [_wake(1000, "w1")])
    cards, _, _ = read_wake_cards(str(tmp_path), limit=30)
    assert cards[0]["trace"] is None


def test_trace_fallback_join_by_ts_when_wake_id_empty(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "v3_wake_log.jsonl", [_wake(1500, "")])
    _write_jsonl(
        tmp_path / "v3_wake_trace.jsonl",
        [{"ts": 1500, "wake_id": "", "mirror": "📍 ДЕ Я", "mirror_light": False,
          "ack": "", "emit_warning": "", "message": ""}],
    )
    cards, _, _ = read_wake_cards(str(tmp_path), limit=30)
    assert cards[0]["trace"]["mirror"] == "📍 ДЕ Я"


# ── thinking-джойн: вікно 600s + call_type match ──────────────────────────────


def test_thinking_join_within_window_and_call_type(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "v3_wake_log.jsonl",
                 [_wake(2000, "w1", call_type="proactive")])
    thinking = [
        {"ts": 2400, "call_type": "reactive", "thinking": "не той тип"},
        {"ts": 2300, "call_type": "proactive", "thinking": "правильний"},  # Δ=300 ≤600
    ]
    cards, _, _ = read_wake_cards(str(tmp_path), limit=30, thinking_records=thinking)
    assert cards[0]["thinking"] == "правильний"
    assert cards[0]["thinking_ts"] == 2300


def test_thinking_join_null_beyond_window(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "v3_wake_log.jsonl",
                 [_wake(2000, "w1", call_type="proactive")])
    thinking = [{"ts": 2700, "call_type": "proactive", "thinking": "далеко"}]  # Δ=700
    cards, _, _ = read_wake_cards(str(tmp_path), limit=30, thinking_records=thinking)
    assert cards[0]["thinking"] is None
    assert cards[0]["thinking_ts"] is None


# ── keyset-пагінація ──────────────────────────────────────────────────────────


def test_pagination_before_ts_and_oldest_ts(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "v3_wake_log.jsonl",
        [_wake(100, "a"), _wake(200, "b"), _wake(300, "c"), _wake(400, "d")],
    )
    page1, total, oldest1 = read_wake_cards(str(tmp_path), limit=2)
    assert [c["wake_id"] for c in page1] == ["d", "c"]  # newest-first
    assert total == 4
    assert oldest1 == 300
    # наступна сторінка: before_ts = oldest курсор попередньої
    page2, _, oldest2 = read_wake_cards(str(tmp_path), limit=2, before_ts=oldest1)
    assert [c["wake_id"] for c in page2] == ["b", "a"]
    assert oldest2 == 100


def test_empty_log_returns_empty(tmp_path: Path) -> None:
    cards, total, oldest_ts = read_wake_cards(str(tmp_path), limit=30)
    assert cards == []
    assert total == 0
    assert oldest_ts is None


# ── limit clamp + малформні рядки ─────────────────────────────────────────────


def test_limit_clamp() -> None:
    assert clamp_wake_limit("50") == 50
    assert clamp_wake_limit("999") == 100  # max
    assert clamp_wake_limit("0") == 1  # min
    assert clamp_wake_limit(None, default=30) == 30
    assert clamp_wake_limit("сміття", default=30) == 30


def test_malformed_lines_skipped_but_counted(tmp_path: Path) -> None:
    path = tmp_path / "v3_wake_log.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_wake(100, "a")) + "\n")
        fh.write("{ це не json\n")
        fh.write(json.dumps(_wake(300, "c")) + "\n")
    cards, total, _ = read_wake_cards(str(tmp_path), limit=30)
    assert [c["wake_id"] for c in cards] == ["c", "a"]
    assert total == 2  # битий рядок не рахується як пробудження


# ── класифікація (дзеркало trader-v3 wake_log.py) ─────────────────────────────


def test_platform_wake_is_watch_and_alert() -> None:
    rec = {"call_type": "platform_wake", "reason": "Watch level fired @4700"}
    assert categorize_wake(rec) == "watch"
    assert classify_alert(rec) is True


def test_heartbeat_timer_is_quiet() -> None:
    rec = {"call_type": "proactive", "reason": "timer:next_check_heartbeat +3m"}
    assert categorize_wake(rec) == "heartbeat"
    assert classify_alert(rec) is False


def test_ritual_and_vp_categories() -> None:
    assert categorize_wake({"reason": "timer:morning_briefing"}) == "ritual"
    assert categorize_wake({"reason": "virtual_position tp hit"}) == "vp"
    assert classify_alert({"reason": "virtual_position tp hit"}) is True


# ── build_now_view: armed дельти + деградації ─────────────────────────────────


def test_now_view_armed_deltas_computed_server_side() -> None:
    directives = {
        "mood": "focused",
        "watch_levels": [{"id": "wl1", "price": 4750.0, "direction": "above"}],
        "wake_conditions": [
            {"id": "wc1", "kind": "price_cross", "params": {"price": 4680.0, "direction": "below"}},
        ],
    }
    degraded: list[str] = []
    body = build_now_view(symbol="XAU/USD", state=None, directives=directives,
                          thesis=None, price=4700.0, now_ms=1_000_000,
                          degraded=degraded)
    armed = body["armed"]
    # найближчий до ціни першим (wc1: |4680-4700|=20 < wl1: 50)
    assert armed[0]["id"] == "wc1"
    assert armed[0]["delta"] == -20.0
    assert armed[0]["level"] == 4680.0
    assert armed[1]["id"] == "wl1"
    assert armed[1]["delta"] == 50.0
    # X28: delta_pct рахує бекенд
    assert armed[1]["delta_pct"] == round(50.0 / 4700.0 * 100, 4)


def test_now_view_stale_flag_and_degraded() -> None:
    now_ms = 10_000_000
    fresh = {"ts_ms": str(now_ms - 1000), "mood": "calm"}
    stale = {"ts_ms": str(now_ms - STATE_STALE_MS - 1), "mood": "calm"}
    body_fresh = build_now_view(symbol="XAU/USD", state=fresh, directives=None,
                                thesis=None, price=None, now_ms=now_ms, degraded=[])
    assert body_fresh["stale"] is False
    body_stale = build_now_view(symbol="XAU/USD", state=stale, directives=None,
                                thesis=None, price=None, now_ms=now_ms, degraded=[])
    assert body_stale["stale"] is True
    assert "state_stale" in body_stale["degraded"]


def test_now_view_thesis_whitelist_and_age() -> None:
    thesis = {"thesis": "short XAU", "conviction": "high", "key_level": "4680",
              "invalidation": "4750", "key_level_price": "4680.0",
              "invalidation_price": "4750.5", "updated_at_ms": "900000",
              "secret_internal": "має бути відсутнім"}
    body = build_now_view(symbol="XAU/USD", state=None, directives=None,
                          thesis=thesis, price=None, now_ms=1_000_000, degraded=[])
    tv = body["thesis"]
    assert tv["thesis"] == "short XAU"
    assert tv["key_level_price"] == 4680.0  # рядок → float
    assert tv["age_ms"] == 100_000  # now - updated
    assert "secret_internal" not in tv  # whitelist-only
