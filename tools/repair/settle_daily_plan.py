"""tools/repair/settle_daily_plan.py — чисті рішення нічного settle (ADR-0103 §3.2, S3c): перерва, вікна, стан, ретеншн.

Нічний прогін зупиняє записувачів SSOT, тож стартує лише в спільній перерві всіх символів: жоден не торгує зараз, а
дедлайн — перша торгова хвилина будь-якого символу мінус запас `deadline_guard_min`. Календар — сезонний
(`calendar_for_symbol`, той самий, що в `settle_m1`), не плоский живий: узимку до S6b плоский тримає літню перерву
21:00–22:00, а брокер торгує до 21:59. Cron `5 21,22 * * 1-5` + `5 9 * * 6` так сам потрапляє в перерву і влітку, і
взимку, а Субота добирає хвіст п'ятниці.

Вікно settle символу: `to` = забір − лаг ревізій групи (хвилини, які брокер ще правитиме, не беруться); `from` =
`to − lookback_h`, або раніше — з межі попереднього успішного прогону, якщо прогони пропускались. Settle ідемпотентний:
перекриття з уже устояним дає SAME.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

M1_MS = 60_000
HOUR_MS = 3_600_000
BREAK_SEARCH_HORIZON_MS = 8 * 24 * HOUR_MS  # довше за найдовші вихідні зі святом
STATE_FILE = "state.json"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

IsTrading = Callable[[int], bool]


def floor_minute(ms: int) -> int:
    return ms - ms % M1_MS


def iso_minute(ms: int) -> str:
    """UTC `YYYY-MM-DDTHH:MM` — формат `--from/--to` інструментів settle і забору."""
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")


def parse_iso_minute(text: str) -> int:
    return int(dt.datetime.strptime(text, "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def utc_stamp(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime(STAMP_FORMAT)


@dataclass(frozen=True)
class BreakWindow:
    reopen_ms: int  # перша торгова хвилина будь-якого символу після зараз
    deadline_ms: int  # прогін завершується або відкочується до цієї миті


def break_window(is_trading_by_symbol: Mapping[str, IsTrading], now_ms: int, guard_min: int) -> Optional[BreakWindow]:
    """Спільна перерва всіх символів зараз; None — хоч один символ торгує (прогін не стартує)."""
    now = floor_minute(now_ms)
    if any(is_trading(now) for is_trading in is_trading_by_symbol.values()):
        return None
    reopen = min(_next_trading_minute(is_trading, now) for is_trading in is_trading_by_symbol.values())
    return BreakWindow(reopen_ms=reopen, deadline_ms=reopen - guard_min * M1_MS)


def _next_trading_minute(is_trading: IsTrading, from_ms: int) -> int:
    t = from_ms
    while not is_trading(t):
        t += M1_MS
        if t - from_ms > BREAK_SEARCH_HORIZON_MS:
            raise ValueError("SETTLE_NO_SESSION_AHEAD — календар без торгової хвилини на 8 діб уперед")
    return t


@dataclass(frozen=True)
class SymbolWindow:
    symbol: str
    from_ms: int
    to_ms: int

    @property
    def sym_dir(self) -> str:
        return self.symbol.replace("/", "_")

    @property
    def settles(self) -> bool:
        return self.to_ms > self.from_ms


def symbol_windows(lag_h_by_symbol: Mapping[str, int], fetched_ms: int, lookback_h: int,
                   settled_to: Mapping[str, int]) -> List[SymbolWindow]:
    """Вікно settle кожного символу: [min(межа попереднього прогону, to − lookback), забір − лаг групи]."""
    out = []
    for symbol, lag_h in lag_h_by_symbol.items():
        to_ms = floor_minute(fetched_ms - lag_h * HOUR_MS)
        from_ms = to_ms - lookback_h * HOUR_MS
        previous = settled_to.get(symbol.replace("/", "_"))
        out.append(SymbolWindow(symbol, min(from_ms, previous) if previous is not None else from_ms, to_ms))
    return out


def m1_fetch_window(windows: Sequence[SymbolWindow], fetched_ms: int) -> Tuple[int, int]:
    """Вікно забору M1: від найранішого `from` (з початку години) до забору — хвилина закриття тижня й ланцюг
    відкриття наступної сесії потребують архіву й після `to` символу."""
    lo = min(w.from_ms for w in windows)
    return lo - lo % HOUR_MS, floor_minute(fetched_ms)


def load_settled_to(work_dir: str) -> Dict[str, int]:
    """Межі попереднього успішного прогону по символу; стану немає — порожньо (перший прогін — лише lookback)."""
    path = os.path.join(work_dir, STATE_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh).get("settled_to") or {}
    return {sym_dir: parse_iso_minute(text) for sym_dir, text in raw.items()}


def save_settled_to(work_dir: str, windows: Sequence[SymbolWindow], previous: Mapping[str, int], run_id: str) -> None:
    """Атомарно: межа символу — max(попередня, `to` цього прогону); пишеться лише після успішного прогону."""
    merged = dict(previous)
    for w in windows:
        merged[w.sym_dir] = max(merged.get(w.sym_dir, w.to_ms), w.to_ms)
    path = os.path.join(work_dir, STATE_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"settled_to": {k: iso_minute(v) for k, v in sorted(merged.items())}, "run": run_id}, fh, indent=1)
    os.replace(path + ".tmp", path)


def expired(names: Sequence[str], keep: int) -> List[str]:
    """Найстаріші понад `keep` серед імен з UTC-штампом `STAMP_FORMAT` (лексикографічний порядок = хронологія)."""
    ordered = sorted(names)
    return ordered[:max(0, len(ordered) - keep)]
