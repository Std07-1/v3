"""tools/feed_lag_check.py — чи йдуть нові M1: сторож VPS будить телефон, коли потік мовчить (ADR-0106 §3.8).

Навіщо: 24.07–04.09 шість тижнів не йшли M1 по XAU/XAG — протух демо-доступ FXCM, а процеси лишались RUNNING.
`M1_STALE` у m1_poller лише пише в лог і після рестарту сліпий, а watchdog бачив тільки падіння процесів.

Що міряє: для кожного символу з ``config.json:symbols`` — скільки ТОРГОВИХ хвилин (сезонний календар символу,
``calendar_for_symbol``) минуло після останнього M1 на диску (``DiskLayer.last_open_ms``). Лише читання.

Два рівні (пороги — ``config.json:feed_lag_alert`` і ``m1_poller.stale_s``):

- потік мовчить — одночасно відстають на ≥ stale_s щонайменше ``feed_min_symbols`` символів. Мертвий доступ
  зупиняє всі символи; свято США, якого календар не знає (07.09.2026), — лише індекси;
- символ мовчить — один символ без нових M1 ≥ ``symbol_lag_min`` торгових хвилин (доба: одноденне свято не
  дотягує).

Пороги звірено з 60 днями M1 проду 26.10.2026: жодного спрацювання на двох тіках поспіль, тоді як «один символ,
12 хв» дав би 14 хибних (USD/JPY біля 21:00 UTC, GER30 уночі).

Вихід — рядки для сторожа:

    LAG <символ> <хв>            символ відстає на ≥ stale_s (хвилини обмежені symbol_lag_min)
    FEED_SILENT <n> <усього>     потік мовчить
    SYMBOL_SILENT <символ> <хв>  символ мовчить добу
    OK <усього>

Код виходу 0 — виміряно (навіть із відставанням); 2 — конфіг, календар або диск не прочитано: сторож повідомить,
що перевірка не працює, а не мовчатиме.

    python -m tools.feed_lag_check
    python -m tools.feed_lag_check --now-ms 1757264000000   # як виглядало б у минулий момент
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.health.measures import missing_trading_minutes
from runtime.ingest.tick_common import calendar_for_symbol
from runtime.store.layers.disk_layer import DiskLayer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M1_TF_S = 60
EXIT_OK = 0
EXIT_UNREADABLE = 2


@dataclass(frozen=True)
class Thresholds:
    lag_min: int
    feed_min_symbols: int
    symbol_lag_min: int


def thresholds_from_config(cfg: dict) -> Thresholds:
    """Пороги з config.json; відставання — той самий stale_s, що й у M1_STALE поллера (торгові секунди)."""
    section = cfg["feed_lag_alert"]
    return Thresholds(
        lag_min=int(cfg["m1_poller"]["stale_s"]) // 60,
        feed_min_symbols=int(section["feed_min_symbols"]),
        symbol_lag_min=int(section["symbol_lag_min"]),
    )


def verdict_lines(lags: Dict[str, int], thresholds: Thresholds) -> List[str]:
    """Відставання символів у торгових хвилинах → рядки для сторожа (порядок символів — як у config)."""
    lagging = [(symbol, minutes) for symbol, minutes in lags.items() if minutes >= thresholds.lag_min]
    lines = [f"LAG {symbol} {minutes}" for symbol, minutes in lagging]
    if len(lagging) >= thresholds.feed_min_symbols:
        lines.append(f"FEED_SILENT {len(lagging)} {len(lags)}")
    lines += [f"SYMBOL_SILENT {symbol} {minutes}" for symbol, minutes in lagging if minutes >= thresholds.symbol_lag_min]
    lines.append(f"OK {len(lags)}")
    return lines


def measure_lags(cfg: dict, data_root: str, now_ms: int, cap: int) -> Dict[str, int]:
    """Символ → торгових хвилин без нового M1 (символ без жодного M1 на диску — ``cap``)."""
    disk = DiskLayer(data_root)
    lags: Dict[str, int] = {}
    for symbol in cfg["symbols"]:
        calendar = calendar_for_symbol(cfg, symbol)
        last_open = disk.last_open_ms(symbol, M1_TF_S)
        lags[symbol] = cap if last_open is None else missing_trading_minutes(
            last_open, now_ms=now_ms, is_trading_fn=calendar.is_trading_minute, cap=cap
        )
    return lags


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Чи йдуть нові M1 (сторож VPS, ADR-0106 §3.8)")
    parser.add_argument("--config", default=os.path.join(REPO_ROOT, "config.json"))
    parser.add_argument("--now-ms", type=int, default=None, help="момент виміру (типово — зараз)")
    args = parser.parse_args(argv)
    try:
        with open(args.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
        thresholds = thresholds_from_config(cfg)
        data_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(args.config)), cfg["data_root"]))
        now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)
        lags = measure_lags(cfg, data_root, now_ms, cap=thresholds.symbol_lag_min)
    except (OSError, KeyError, ValueError) as exc:
        print(f"FEED_LAG_CHECK_FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    print("\n".join(verdict_lines(lags, thresholds)))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
