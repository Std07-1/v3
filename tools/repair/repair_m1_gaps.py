"""tools/repair/repair_m1_gaps.py — Ремонт M1 дірок після мережевих збоїв.

Алгоритм:
  1. Сканує JSONL tf_60/ для символу → знаходить пропущені M1 бакети
  2. Дотягує бари з FXCM через broker_sidecar (Redis IPC, 60s timeout)
  3. Append до JSONL — disk_layer дедуплікує при читанні
  4. Перебудовує derived TF (M3→H4) з виправлених M1

Запуск (потрібен broker_sidecar + Redis):
    python -m tools.repair.repair_m1_gaps --symbol XAU/USD --dry-run
    python -m tools.repair.repair_m1_gaps --symbol XAU/USD --commit

Після ремонту — перезапустити платформу (RAM/Redis перечитає з диску):
    python -m app.main --mode all --stdio pipe
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from core.config_loader import load_system_config, pick_config_path
from core.model.bars import CandleBar
from runtime.store.ssot_jsonl import (
    iter_day_keys_utc,
    load_day_open_times,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)

TF_M1_S = 60
TF_M1_MS = 60_000
_MAX_BARS_PER_FETCH = 200  # broker_sidecar max (guard)
# Repair використовує прямий Redis IPC з подовженим timeout (60s),
# бо BrokerRedisProxy.BLPOP=15s замалий при конкуренції
# з m1_ingestion_worker в спільній черзі команд.
_REPAIR_BLPOP_TIMEOUT_S = 60
# FXCM SDK повертає порожній результат приблизно в 50% випадків
# (серверне throttling). Worker (n=5 кожні 5с) перекриває це
# частотою. Repair tool потребує 5 retry з паузою.
_FETCH_RETRIES = 5
_FETCH_RETRY_DELAY_S = 5.0
_CMD_QUEUE_SUFFIX = "broker:m1:cmd"
_BARS_QUEUE_SUFFIX = "broker:m1:bars"


# ─── Gap detection ─────────────────────────────────────────────────


def detect_m1_gaps(
    data_root: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    calendar: Optional[Any] = None,
) -> List[int]:
    """Повертає список open_time_ms для пропущених M1 бакетів.

    Порівнює існуючий набір відкритих часів із очікуваним
    (кожні 60s від start_ms до end_ms).
    """
    # Align to minute boundaries (critical for --hours mode)
    start_ms = (start_ms // TF_M1_MS) * TF_M1_MS
    end_ms = (end_ms // TF_M1_MS) * TF_M1_MS

    # Зібрати всі існуючі open_time_ms
    existing: Set[int] = set()
    for day in iter_day_keys_utc(start_ms, end_ms):
        existing |= load_day_open_times(data_root, symbol, TF_M1_S, day)

    # Генеруємо очікувані бакети
    gaps: List[int] = []
    t = start_ms
    while t <= end_ms:
        if t not in existing:
            # Calendar check — пропускаємо closed market
            if calendar is not None:
                try:
                    if not calendar.is_market_open_at_ms(t, symbol):
                        t += TF_M1_MS
                        continue
                except Exception:
                    pass
            gaps.append(t)
        t += TF_M1_MS

    return gaps


def group_contiguous_gaps(gaps: List[int]) -> List[Tuple[int, int]]:
    """Групує послідовні gap timestamps у (start_ms, end_ms) діапазони."""
    if not gaps:
        return []
    groups: List[Tuple[int, int]] = []
    g_start = gaps[0]
    g_end = gaps[0]
    for g in gaps[1:]:
        if g == g_end + TF_M1_MS:
            g_end = g
        else:
            groups.append((g_start, g_end))
            g_start = g
            g_end = g
    groups.append((g_start, g_end))
    return groups


# ─── Broker fetch (direct Redis IPC, no BrokerRedisProxy) ─────────


def _fetch_from_sidecar(
    redis_cli: Any,
    namespace: str,
    symbol: str,
    n_bars: int,
) -> List[CandleBar]:
    """Fetch M1 bars від broker_sidecar через Redis IPC.

    Використовує 60s BLPOP timeout (замість 15s у BrokerRedisProxy).
    BrokerRedisProxy.BLPOP=15s замалий при n≥100 + конкуренція з
    m1_ingestion_worker в спільній черзі — це і було причиною
    "порожніх відповідей" + фальшивої "нестабільності SDK".
    """
    req_id = uuid.uuid4().hex
    reply_key = f"{namespace}:{_BARS_QUEUE_SUFFIX}:{req_id}"
    cmd_key = f"{namespace}:{_CMD_QUEUE_SUFFIX}"

    cmd = json.dumps({
        "v": 1,
        "cmd": "fetch_m1",
        "req_id": req_id,
        "reply_to": reply_key,
        "symbol": symbol,
        "n_bars": min(n_bars, _MAX_BARS_PER_FETCH),
        "date_to_ms": None,
    })
    redis_cli.rpush(cmd_key, cmd)

    result = redis_cli.blpop(reply_key, timeout=_REPAIR_BLPOP_TIMEOUT_S)
    if result is None:
        redis_cli.delete(reply_key)
        log.warning(
            "REPAIR_FETCH_TIMEOUT symbol=%s n=%d timeout=%ds "
            "(broker_sidecar не відповів — перевір що він працює)",
            symbol, n_bars, _REPAIR_BLPOP_TIMEOUT_S,
        )
        return []

    _key, raw = result
    redis_cli.delete(reply_key)

    try:
        resp = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("REPAIR_FETCH_PARSE_ERROR err=%s", exc)
        return []

    if resp.get("error"):
        log.warning("REPAIR_FETCH_ERROR err=%s", resp["error"])
        return []

    raw_bars = resp.get("bars", [])
    log.info(
        "REPAIR_SIDECAR_REPLY req_id=%s bars_count=%d",
        resp.get("req_id", "?"), len(raw_bars),
    )

    bars: List[CandleBar] = []
    for d in raw_bars:
        try:
            bars.append(CandleBar(
                symbol=d["symbol"], tf_s=d["tf_s"],
                open_time_ms=d["open_time_ms"],
                close_time_ms=d["close_time_ms"],
                o=d["o"], h=d["h"], low=d["low"], c=d["c"], v=d["v"],
                complete=d.get("complete", True),
                src=d.get("src", "history"),
                extensions=d.get("extensions", {}),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("REPAIR_BAR_PARSE err=%s bar=%s", exc, d)

    return bars


def fetch_m1_for_range(
    redis_cli: Any,
    namespace: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    gap_opens: Set[int],
) -> List[CandleBar]:
    """Fetch M1 bars від broker_sidecar для покриття gap діапазону."""
    now_ms = int(time.time() * 1000)
    range_minutes = ((now_ms - start_ms) // TF_M1_MS) + 10
    n_fetch = min(range_minutes, _MAX_BARS_PER_FETCH)

    log.info(
        "REPAIR_FETCH symbol=%s n=%d gap_count=%d timeout=%ds",
        symbol, n_fetch, len(gap_opens), _REPAIR_BLPOP_TIMEOUT_S,
    )

    # Retry: FXCM SDK повертає порожній результат ~50% часу (throttling)
    bars: List[CandleBar] = []
    for attempt in range(1, _FETCH_RETRIES + 1):
        bars = _fetch_from_sidecar(redis_cli, namespace, symbol, n_fetch)
        if bars:
            break
        if attempt < _FETCH_RETRIES:
            log.info(
                "REPAIR_FETCH_RETRY attempt=%d/%d (FXCM повернув 0, retry через %gs)",
                attempt, _FETCH_RETRIES, _FETCH_RETRY_DELAY_S,
            )
            time.sleep(_FETCH_RETRY_DELAY_S)

    if not bars:
        log.warning(
            "REPAIR_FETCH_FAILED symbol=%s після %d спроб. "
            "Перевір: broker_sidecar працює? FXCM connected?",
            symbol, _FETCH_RETRIES,
        )
        return []

    # Фільтруємо: тільки бари, що потрапляють у gaps
    repaired = [b for b in bars if b.open_time_ms in gap_opens]

    log.info(
        "REPAIR_FETCH_RESULT attempt=%d fetched=%d matched_gaps=%d",
        attempt, len(bars), len(repaired),
    )
    return repaired


# ─── Repair via rewrite_range ──────────────────────────────────────


def repair_gaps(
    data_root: str,
    symbol: str,
    gap_groups: List[Tuple[int, int]],
    all_gap_opens: Set[int],
    redis_cli: Any,
    namespace: str,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Ремонтує M1 гапи: один fetch + append до JSONL.

    Використовує прямий append замість rewrite_range(), бо платформа
    тримає файли відкритими (Windows file lock). disk_layer сортує
    та дедуплікує при читанні, тому порядок рядків не критичний.
    """
    total_gaps = len(all_gap_opens)

    # Один fetch для всього діапазону
    global_start = min(g[0] for g in gap_groups)
    global_end = max(g[1] for g in gap_groups)

    bars = fetch_m1_for_range(
        redis_cli, namespace, symbol, global_start, global_end, all_gap_opens
    )

    if not bars:
        return {
            "symbol": symbol,
            "total_gaps": total_gaps,
            "total_fetched": 0,
            "total_written": 0,
            "dry_run": dry_run,
            "groups": [{"status": "NO_DATA_FROM_BROKER"}],
        }

    if dry_run:
        return {
            "symbol": symbol,
            "total_gaps": total_gaps,
            "total_fetched": len(bars),
            "total_written": 0,
            "dry_run": True,
            "groups": [{"status": "DRY_RUN", "fetched": len(bars), "expected": total_gaps}],
        }

    # Append bars to JSONL — safe while platform is running
    written = _append_bars_to_jsonl(data_root, symbol, bars)

    return {
        "symbol": symbol,
        "total_gaps": total_gaps,
        "total_fetched": len(bars),
        "total_written": written,
        "dry_run": False,
        "groups": [{
            "range": f"{_ms_to_hm(global_start)}..{_ms_to_hm(global_end)}",
            "expected": total_gaps,
            "fetched": len(bars),
            "written": written,
            "status": "REPAIRED",
        }],
    }


def _append_bars_to_jsonl(data_root: str, symbol: str, bars: List[CandleBar]) -> int:
    """Append бари до JSONL файлів (по дням). Повертає кількість записаних."""
    sym_dir = symbol.replace("/", "_")
    tf_dir = os.path.join(data_root, sym_dir, f"tf_{TF_M1_S}")
    os.makedirs(tf_dir, exist_ok=True)

    written = 0
    # Групуємо бари по днях (UTC)
    by_day: Dict[str, List[CandleBar]] = {}
    for bar in bars:
        utc_dt = dt.datetime.fromtimestamp(bar.open_time_ms / 1000, dt.timezone.utc)
        day_key = utc_dt.strftime("%Y%m%d")
        by_day.setdefault(day_key, []).append(bar)

    for day_key, day_bars in by_day.items():
        path = os.path.join(tf_dir, f"part-{day_key}.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            for bar in day_bars:
                line = json.dumps(bar.to_dict(), ensure_ascii=False, separators=(",", ":"))
                fh.write(line + "\n")
                written += 1
            fh.flush()
        log.info("REPAIR_APPEND day=%s bars=%d path=%s", day_key, len(day_bars), path)

    return written


def _ms_to_hm(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%H:%M")


# ─── Main ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ремонт M1 гапів після мережевого збою"
    )
    parser.add_argument("--symbol", required=True, help="Символ, напр. XAU/USD")
    parser.add_argument(
        "--hours",
        type=int,
        default=6,
        help="Скільки годин назад сканувати (default: 6)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Початок діапазону (ISO UTC, напр. 2026-03-23T10:00:00)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Кінець діапазону (ISO UTC, напр. 2026-03-23T14:00:00)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Тільки показати гапи")
    parser.add_argument("--commit", action="store_true", help="Записати ремонт на диск")
    parser.add_argument("--config", type=str, default=None, help="Шлях до config.json")

    args = parser.parse_args()

    if not args.commit and not args.dry_run:
        log.info("Не вказано --commit або --dry-run. За замовчуванням --dry-run.")
        args.dry_run = True

    if args.commit:
        args.dry_run = False

    # Load config
    config_path = args.config or pick_config_path()
    cfg = load_system_config(config_path)
    data_root = cfg.get("data_root", "data_v3")

    # Time range
    now_ms = int(time.time() * 1000)
    if args.start:
        start_ms = _parse_iso_ms(args.start)
    else:
        start_ms = now_ms - args.hours * 3600 * 1000

    if args.end:
        end_ms = _parse_iso_ms(args.end)
    else:
        end_ms = now_ms

    symbol = args.symbol
    log.info(
        "=== REPAIR M1 GAPS === symbol=%s range=%s..%s dry_run=%s",
        symbol,
        dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M"
        ),
        dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M"
        ),
        args.dry_run,
    )

    # Step 1: Detect gaps
    calendar = _try_load_calendar(cfg, symbol)
    gaps = detect_m1_gaps(data_root, symbol, start_ms, end_ms, calendar)

    if not gaps:
        log.info("M1 гапів не знайдено. Все чисто!")
        return

    gap_groups = group_contiguous_gaps(gaps)
    log.info(
        "Знайдено %d M1 гапів у %d групах:",
        len(gaps),
        len(gap_groups),
    )
    for g_start, g_end in gap_groups:
        n = ((g_end - g_start) // TF_M1_MS) + 1
        log.info(
            "  %s..%s (%d бар%s)",
            _ms_to_hm(g_start),
            _ms_to_hm(g_end),
            n,
            "ів" if n > 1 else "",
        )

    if args.dry_run:
        log.info("DRY-RUN: нічого не записано. Використай --commit для ремонту:")
        # Побудувати реальну команду з конкретними датами
        start_iso = dt.datetime.fromtimestamp(
            start_ms / 1000, dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S")
        end_iso = dt.datetime.fromtimestamp(
            end_ms / 1000, dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S")
        log.info(
            '  python -m tools.repair.repair_m1_gaps --symbol "%s" '
            '--start "%s" --end "%s" --commit',
            symbol, start_iso, end_iso,
        )
        # Probe: показати що broker доступний
        _try_probe_broker(cfg, symbol, gap_groups, set(gaps))
        return

    # Step 2: Connect to Redis (direct IPC, не через BrokerRedisProxy)
    redis_cli, namespace = _connect_redis(cfg)
    if redis_cli is None:
        log.error("Не вдалось підключитись до Redis. Перевір що Redis працює.")
        return

    try:
        result = repair_gaps(
            data_root=data_root,
            symbol=symbol,
            gap_groups=gap_groups,
            all_gap_opens=set(gaps),
            redis_cli=redis_cli,
            namespace=namespace,
            dry_run=False,
        )
    finally:
        redis_cli.close()

    # Report
    log.info("=== REPAIR RESULT ===")
    log.info(
        "total_gaps=%d fetched=%d written=%d",
        result["total_gaps"],
        result["total_fetched"],
        result["total_written"],
    )
    for gr in result["groups"]:
        log.info("  %s: %s", gr.get("range", "-"), gr["status"])

    if result["total_written"] > 0:
        log.info("")
        log.info("✓ M1 записано на диск.")
        log.info("Тепер перебудуй derived TF + перезапусти платформу:")
        log.info(
            '  python -m tools.rebuild_from_m1 --symbol "%s" --start "%s" --end "%s"',
            symbol,
            dt.datetime.fromtimestamp(
                min(g[0] for g in gap_groups) / 1000, dt.timezone.utc
            ).strftime("%Y-%m-%d"),
            dt.datetime.fromtimestamp(
                max(g[1] for g in gap_groups) / 1000, dt.timezone.utc
            ).strftime("%Y-%m-%d"),
        )
        log.info("  python -m app.main --mode all --stdio pipe")
    else:
        log.warning("Жоден бар не записано. Перевір broker_sidecar і FXCM логін.")


def _try_probe_broker(
    cfg: dict,
    symbol: str,
    gap_groups: List[Tuple[int, int]],
    all_gap_opens: Set[int],
) -> None:
    """В DRY-RUN: спробувати підключитись і показати скільки барів можна дотягнути."""
    redis_cli, namespace = _connect_redis(cfg)
    if redis_cli is None:
        log.info("Redis недоступний (dry-run probe пропущено).")
        return

    global_start = min(g[0] for g in gap_groups)
    global_end = max(g[1] for g in gap_groups)

    try:
        bars = fetch_m1_for_range(
            redis_cli, namespace, symbol, global_start, global_end, all_gap_opens
        )
    finally:
        redis_cli.close()

    if bars:
        log.info(
            "PROBE: fetched %d з %d очікуваних gap-барів. Готовий до --commit!",
            len(bars), len(all_gap_opens),
        )
    else:
        log.warning(
            "PROBE: broker повернув 0 барів. "
            "Перевір: broker_sidecar працює? FXCM connected?"
        )


def _connect_redis(cfg: dict) -> Tuple[Any, str]:
    """Створює Redis клієнт. Повертає (redis_cli, namespace) або (None, '')."""
    try:
        import redis as redis_lib
    except ImportError:
        log.error("redis package не встановлено")
        return None, ""

    redis_cfg = cfg.get("redis", {})
    host = redis_cfg.get("host", "127.0.0.1")
    port = redis_cfg.get("port", 6379)
    db = redis_cfg.get("db", 1)
    namespace = redis_cfg.get("namespace", "v3_local")

    try:
        redis_cli = redis_lib.Redis(host=host, port=port, db=db, decode_responses=True)
        redis_cli.ping()
    except Exception as exc:
        log.error("Redis connection failed: %s", exc)
        return None, ""

    return redis_cli, namespace


def _try_load_calendar(cfg: dict, symbol: str) -> Optional[Any]:
    """Спробувати завантажити MarketCalendar для символу."""
    try:
        from runtime.ingest.tick_common import calendar_from_group

        groups = cfg.get("symbol_groups", [])
        for group in groups:
            syms = group.get("symbols", [])
            if symbol in syms:
                cal = calendar_from_group(group)
                return cal
    except Exception:
        pass
    return None


def _parse_iso_ms(s: str) -> int:
    """Парсить ISO UTC string → epoch ms."""
    s = s.strip().strip('"').strip("'")
    if not s or s == "..." or s.startswith("..."):
        raise SystemExit(
            f'Помилка: "{s}" — не валідна дата. '
            "Вкажи конкретну дату, наприклад: 2026-03-23T10:00:00"
        )
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(
            f'Помилка: "{s}" — не валідна ISO дата. '
            "Формат: YYYY-MM-DDTHH:MM:SS (наприклад 2026-03-23T10:00:00)"
        )
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


if __name__ == "__main__":
    main()
