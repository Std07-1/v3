"""Replay-Mode — відтворення M1 барів з data_v3/ JSONL (ADR-0017).

Архітектурний шар: runtime/ingest (Layer 2).
Dependency Rule: runtime/ імпортує core/, не навпаки.

Потік даних:
  data_v3/{symbol}/tf_60/part-*.jsonl
    → parse CandleBar (src="history", complete=True)
    → UDS.commit_final_bar(M1)
    → DeriveEngine.on_bar(M1) → каскад M3→M5→…→H4+D1
    → Redis pub/sub → WS delta loop → UI

Режими швидкості:
  --speed 0    — dump all instantly (для CI/тестів)
  --speed 1    — real-time (1 бар/хвилину)
  --speed 10   — 10× швидкість (6 сек = 1 хвилина ринку)
  --speed 60   — 60× (1 сек = 1 хвилина ринку)

ADR: ADR-0017 (Replay-Mode з data_v3/ для Offline Demo).
"""
from __future__ import annotations

import glob
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from core.config_loader import pick_config_path, load_system_config
from core.model.bars import CandleBar
from env_profile import load_env_secrets
from runtime.ingest.derive_engine import DeriveEngine
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.tick_common import calendar_from_group
from runtime.store.uds import build_uds_from_config

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore

log = logging.getLogger("replay")

# ---------------------------------------------------------------------------
# JSONL reader
# ---------------------------------------------------------------------------

def _read_m1_bars_from_disk(
    data_root: str,
    symbol: str,
) -> List[Dict[str, Any]]:
    """Читає всі M1 бари з data_v3/{symbol}/tf_60/part-*.jsonl.

    Повертає list[dict] відсортований за open_time_ms asc.
    """
    # Канонічний шлях (XAU/USD → XAU_USD)
    sym_dir = symbol.replace("/", "_")
    pattern = os.path.join(data_root, sym_dir, "tf_60", "part-*.jsonl")
    paths = sorted(glob.glob(pattern))
    if not paths:
        log.warning("REPLAY_NO_FILES symbol=%s pattern=%s", symbol, pattern)
        return []

    bars: List[Dict[str, Any]] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    # Фільтр: тільки M1 (tf_s=60), complete=true
                    if obj.get("tf_s") != 60:
                        continue
                    if not obj.get("complete", False):
                        continue
                    bars.append(obj)
        except FileNotFoundError:
            continue

    # Сортування по open_time_ms (обов'язково для UDS watermark monotonicity)
    bars.sort(key=lambda x: x.get("open_time_ms", 0))

    # Дедуплікація по open_time_ms (last wins)
    seen: Dict[int, int] = {}
    unique: List[Dict[str, Any]] = []
    for bar in bars:
        oms = bar.get("open_time_ms", 0)
        if oms in seen:
            # Заміна дублікату (останній wins — як у production)
            unique[seen[oms]] = bar
        else:
            seen[oms] = len(unique)
            unique.append(bar)

    log.info(
        "REPLAY_LOADED symbol=%s files=%d bars=%d (deduped from %d)",
        symbol, len(paths), len(unique), len(bars),
    )
    return unique


def _dict_to_candle(d: Dict[str, Any]) -> Optional[CandleBar]:
    """Конвертує dict з JSONL у CandleBar."""
    try:
        return CandleBar(
            symbol=d["symbol"],
            tf_s=d["tf_s"],
            open_time_ms=d["open_time_ms"],
            close_time_ms=d["close_time_ms"],
            o=float(d["o"]),
            h=float(d["h"]),
            low=float(d["low"]),
            c=float(d["c"]),
            v=float(d.get("v", 0)),
            complete=True,
            src="history",
        )
    except (KeyError, ValueError, TypeError) as exc:
        log.warning("REPLAY_BAR_PARSE_ERROR err=%s data=%s", exc, d)
        return None


# ---------------------------------------------------------------------------
# Replay driver
# ---------------------------------------------------------------------------

_WARMUP_BARS = 1500  # Кількість M1 барів для warmup DeriveEngine буферів


class _NullJsonlAppender:
    """No-op JSONL appender для replay mode (skip disk writes).

    UDS._append_to_disk() перевіряє self._jsonl is None → повертає False.
    Цей клас підміняє справжній appender: append() нічого не робить,
    але UDS вважає disk write успішним → commit ok → watermark оновлюється.
    """

    def append(self, bar: Any) -> None:
        pass

    def close(self) -> None:
        pass

    def drop_preview_total(self) -> int:
        return 0


def _flush_redis_namespace(cfg: Dict[str, Any]) -> int:
    """Очистити Redis namespace перед replay (видаляє ohlcv snapshots, preview, updates).

    Без цього UDS watermark ініціалізується з диску і всі бари "stale".
    Returns: кількість видалених ключів.
    """
    if redis_lib is None:
        log.warning("REPLAY_FLUSH_SKIP reason=redis_lib_missing")
        return 0

    from runtime.store.redis_spec import resolve_redis_spec
    spec = resolve_redis_spec(cfg, role="replay_flush", log=False)
    if spec is None:
        log.warning("REPLAY_FLUSH_SKIP reason=redis_disabled")
        return 0

    try:
        client = redis_lib.Redis(
            host=spec.host,
            port=spec.port,
            db=spec.db,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        pattern = f"{spec.namespace}:*"
        keys = client.keys(pattern)
        if keys:
            client.delete(*keys)
        log.info("REPLAY_REDIS_FLUSH namespace=%s keys_deleted=%d", spec.namespace, len(keys))
        return len(keys)
    except Exception as exc:
        log.warning("REPLAY_REDIS_FLUSH_FAILED err=%s", exc)
        return 0


def run_replay(
    *,
    config_path: str,
    symbols: List[str],
    speed: float = 10.0,
    skip_disk_write: bool = True,
    start_ms: Optional[int] = None,
) -> int:
    """Головний replay драйвер.

    Args:
        config_path: шлях до config.json
        symbols: список символів для replay
        speed: множник швидкості (0 = instant, 1 = real-time, 10 = 10x)
        skip_disk_write: True → не писати на диск (дані вже є)
        start_ms: фільтр (пропустити бари до цієї дати epoch ms)

    Returns:
        0 = success, 1 = error
    """
    cfg = load_system_config(config_path)
    data_root = str(cfg.get("data_root", "./data_v3"))

    # --- Flush Redis namespace (clean slate для replay) ---
    _flush_redis_namespace(cfg)

    # --- UDS (writer, з Redis + pub/sub, але БЕЗ disk write якщо skip) ---
    boot_id = "replay-" + uuid.uuid4().hex[:8]
    uds = build_uds_from_config(
        config_path=config_path,
        data_root=data_root,
        boot_id=boot_id,
        role="writer",
        writer_components=True,
    )

    # Якщо skip_disk_write — підміняємо JSONL appender на no-op.
    # UDS._append_to_disk() повертає True (commit працює), але диск не чіпаємо.
    # Дані на диску вже існують, дублювати непотрібно.
    if skip_disk_write:
        uds._jsonl = _NullJsonlAppender()  # noqa: SLF001
        log.info("REPLAY_SKIP_DISK_WRITE (NullJsonlAppender)")

    # Pre-populate watermarks to 0 для всіх (symbol, tf_s) пар.
    # Без цього _init_watermark_for_key() читає disk last_open_ms
    # → watermark = останній бар на диску → всі replay бари "stale".
    tf_all = [60, 180, 300, 900, 1800, 3600, 14400, 86400]
    for sym in symbols:
        for tf_s in tf_all:
            uds._wm_by_key[(sym, tf_s)] = 0  # noqa: SLF001
    log.info("REPLAY_WATERMARKS_RESET symbols=%d tfs=%d", len(symbols), len(tf_all))

    # Performance: зменшуємо Redis tail size для replay.
    # Production tail M1=10080 → кожен commit серіалізує ~1MB JSON.
    # Replay: tail=500 → ~50KB per write → ~20x speedup.
    _REPLAY_TAIL_MAX = 500
    writer = getattr(uds, "_redis_writer", None)
    if writer is not None:
        tail_cfg = getattr(writer, "_tail_n_by_tf_s", None)
        if isinstance(tail_cfg, dict):
            for tf_key in list(tail_cfg.keys()):
                old_val = tail_cfg[tf_key]
                tail_cfg[tf_key] = min(_REPLAY_TAIL_MAX, int(old_val))
            log.info("REPLAY_TAIL_REDUCED max=%d (was up to 10080)", _REPLAY_TAIL_MAX)

    # --- DeriveEngine ---
    anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
    d1_anchor_offset_s = int(cfg.get("day_anchor_offset_s_d1", 0))

    cal_by_group = cfg.get("market_calendar_by_group", {})
    cal_sym_groups = cfg.get("market_calendar_symbol_groups", {})
    calendars: Dict[str, MarketCalendar] = {}
    for sym in symbols:
        group = cal_sym_groups.get(sym)
        if group and isinstance(cal_by_group.get(group), dict):
            cal_obj = calendar_from_group(cal_by_group[group])
            if cal_obj is not None:
                calendars[sym] = cal_obj

    engine = DeriveEngine(
        symbols=symbols,
        anchor_offset_s=anchor_offset_s,
        d1_anchor_offset_s=d1_anchor_offset_s,
        calendars=calendars,
    )
    for sym in symbols:
        engine.register_symbol_uds(sym, uds)

    log.info(
        "REPLAY_ENGINE_READY symbols=%s anchor=%d d1_anchor=%d speed=%s",
        symbols, anchor_offset_s, d1_anchor_offset_s,
        "instant" if speed == 0 else f"{speed}x",
    )

    # --- Завантаження M1 барів ---
    all_bars: List[Dict[str, Any]] = []
    for sym in symbols:
        sym_bars = _read_m1_bars_from_disk(data_root, sym)
        all_bars.extend(sym_bars)

    if not all_bars:
        log.error("REPLAY_NO_BARS symbols=%s data_root=%s", symbols, data_root)
        return 1

    # Сортування всіх барів по часу (для multi-symbol)
    all_bars.sort(key=lambda x: x.get("open_time_ms", 0))

    # Фільтр по start_ms (--start параметр)
    if start_ms is not None:
        before = len(all_bars)
        all_bars = [b for b in all_bars if b.get("open_time_ms", 0) >= start_ms]
        log.info("REPLAY_START_FILTER from=%d to=%d skipped=%d", start_ms, len(all_bars), before - len(all_bars))

    total = len(all_bars)
    log.info("REPLAY_TOTAL_BARS %d", total)

    # --- Warmup: перші N барів → buffer fill без cascade ---
    warmup_n = min(_WARMUP_BARS, total // 2)  # не більше половини
    if warmup_n > 0:
        warmup_candles = []
        for d in all_bars[:warmup_n]:
            cb = _dict_to_candle(d)
            if cb is not None:
                warmup_candles.append(cb)
        if warmup_candles:
            engine.warmup_bars(warmup_candles)
            # Також commit warmup бари в UDS для Redis snapshot
            committed_warmup = 0
            for cb in warmup_candles:
                result = uds.commit_final_bar(cb)
                if result.ok:
                    committed_warmup += 1
            log.info(
                "REPLAY_WARMUP bars=%d committed=%d",
                len(warmup_candles), committed_warmup,
            )

    # --- Prime ready signal (UI може стартувати) ---
    tfs = [60, 180, 300, 900, 1800, 3600, 14400, 86400]
    payload = {
        "v": 1,
        "ready": True,
        "component": "replay",
        "ts_ms": int(time.time() * 1000),
        "symbols": symbols,
        "tfs": tfs,
    }
    try:
        uds.set_prime_ready(payload, 21600, component="m1")
        log.info("REPLAY_PRIME_READY symbols=%d", len(symbols))
    except Exception as exc:
        log.warning("REPLAY_PRIME_READY_FAILED err=%s", exc)

    # --- Replay loop ---
    replay_bars = all_bars[warmup_n:]
    replay_total = len(replay_bars)
    log.info(
        "REPLAY_START bars=%d speed=%s",
        replay_total,
        "instant" if speed == 0 else f"{speed}x",
    )

    committed_m1 = 0
    committed_derived = 0
    last_log_ts = time.time()
    last_open_ms: Optional[int] = None
    start_wall = time.time()

    for i, bar_dict in enumerate(replay_bars):
        cb = _dict_to_candle(bar_dict)
        if cb is None:
            continue

        # Speed control: пауза між барами
        if speed > 0 and last_open_ms is not None:
            gap_ms = cb.open_time_ms - last_open_ms
            if gap_ms > 0:
                sleep_s = (gap_ms / 1000.0) / speed
                # Обмежуємо паузу: не більше 5 сек (weekend gap → не чекати годинами)
                sleep_s = min(sleep_s, 5.0)
                if sleep_s > 0.001:
                    time.sleep(sleep_s)

        # 1) Commit M1 в UDS
        result = uds.commit_final_bar(cb)
        if result.ok:
            committed_m1 += 1

            # 2) Cascade через DeriveEngine
            derived = engine.on_bar(cb)
            committed_derived += len(derived)

        last_open_ms = cb.open_time_ms

        # Progress log кожні 10 секунд
        now = time.time()
        if now - last_log_ts >= 10.0:
            pct = ((i + 1) / replay_total) * 100.0
            elapsed = now - start_wall
            bars_per_sec = committed_m1 / elapsed if elapsed > 0 else 0
            log.info(
                "REPLAY_PROGRESS %d/%d (%.1f%%) m1=%d derived=%d %.0f bars/s",
                i + 1, replay_total, pct, committed_m1, committed_derived,
                bars_per_sec,
            )
            last_log_ts = now

    elapsed_total = time.time() - start_wall
    log.info(
        "REPLAY_DONE total=%d m1_committed=%d derived=%d elapsed=%.1fs",
        replay_total, committed_m1, committed_derived, elapsed_total,
    )

    return 0


# ---------------------------------------------------------------------------
# Entrypoint  (python -m runtime.ingest.replay)
# ---------------------------------------------------------------------------

def _parse_replay_args() -> Dict[str, Any]:
    """Парсить аргументи для replay mode."""
    import argparse

    ap = argparse.ArgumentParser(description="Replay M1 bars from data_v3/ JSONL")
    ap.add_argument(
        "--symbols",
        default="XAU/USD",
        help="Символи через кому (default: XAU/USD)",
    )
    ap.add_argument(
        "--speed",
        type=float,
        default=10.0,
        help="Множник швидкості: 0=instant, 1=realtime, 10=10x (default: 10)",
    )
    ap.add_argument(
        "--start",
        default=None,
        help="Start date YYYY-MM-DD (skip data before). Ex: --start 2026-02-01",
    )
    ap.add_argument(
        "--skip-disk-write",
        action="store_true",
        default=True,
        help="Не писати M1 на диск (дані вже є, default: True)",
    )
    args = ap.parse_args()

    start_ms = None
    if args.start:
        try:
            import datetime
            dt = datetime.datetime.strptime(args.start, "%Y-%m-%d")
            start_ms = int(dt.timestamp() * 1000) if hasattr(dt, 'timestamp') else int(
                (dt - datetime.datetime(1970, 1, 1)).total_seconds() * 1000
            )
        except ValueError:
            log.error("REPLAY_INVALID_START_DATE format=%s expected=YYYY-MM-DD", args.start)
            raise SystemExit(1)

    return {
        "symbols": [s.strip() for s in args.symbols.split(",") if s.strip()],
        "speed": args.speed,
        "skip_disk_write": args.skip_disk_write,
        "start_ms": start_ms,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    report = load_env_secrets()
    if report.loaded:
        log.info("ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count)

    config_path = pick_config_path()
    log.info("REPLAY config=%s", config_path)

    parsed = _parse_replay_args()
    symbols = parsed["symbols"]
    speed = parsed["speed"]
    skip_disk = parsed["skip_disk_write"]
    start_ms = parsed.get("start_ms")

    log.info(
        "REPLAY_INIT symbols=%s speed=%s skip_disk=%s start_ms=%s",
        symbols, speed, skip_disk, start_ms,
    )

    return run_replay(
        config_path=config_path,
        symbols=symbols,
        speed=speed,
        skip_disk_write=skip_disk,
        start_ms=start_ms,
    )


if __name__ == "__main__":
    raise SystemExit(main())
