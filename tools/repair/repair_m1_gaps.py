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
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.tick_common import resolve_symbol_calendars
from runtime.store.redis_spec import (
    REDIS_PASSWORD_ENV,
    REDIS_USERNAME_ENV,
    resolve_redis_spec,
)
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
# Запас сторінок понад довжину вікна: брокер інколи віддає менше 200 барів навіть
# у торгові хвилини — тоді курсор посувається менше, і сторінок треба більше.
_PAGE_BUDGET_SLACK = 2


# ─── Gap detection ─────────────────────────────────────────────────


def detect_m1_gaps(
    data_root: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    calendar: MarketCalendar,
) -> List[int]:
    """Повертає список open_time_ms для пропущених M1 бакетів.

    Дірка — торгова хвилина (за ``calendar``) без бару в SSOT. Календар обов'язковий:
    без нього кожна хвилина закритого ринку ставала «діркою». Свят календар не знає,
    тож святкова хвилина для нього торгова.
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
        if t not in existing and calendar.is_trading_minute(t):
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
    date_to_ms: Optional[int] = None,
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

    cmd = json.dumps(
        {
            "v": 1,
            "cmd": "fetch_m1",
            "req_id": req_id,
            "reply_to": reply_key,
            "symbol": symbol,
            "n_bars": min(n_bars, _MAX_BARS_PER_FETCH),
            "date_to_ms": date_to_ms,
            # ADR-0054 §3.6: вік команди — протухлу sidecar дропне, а не обслужить пізно
            "ts_ms": int(time.time() * 1000),
        }
    )
    redis_cli.rpush(cmd_key, cmd)

    result = redis_cli.blpop(reply_key, timeout=_REPAIR_BLPOP_TIMEOUT_S)
    if result is None:
        redis_cli.delete(reply_key)
        log.warning(
            "REPAIR_FETCH_TIMEOUT symbol=%s n=%d timeout=%ds "
            "(broker_sidecar не відповів — перевір що він працює)",
            symbol,
            n_bars,
            _REPAIR_BLPOP_TIMEOUT_S,
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
        resp.get("req_id", "?"),
        len(raw_bars),
    )

    bars: List[CandleBar] = []
    for d in raw_bars:
        try:
            bars.append(
                CandleBar(
                    symbol=d["symbol"],
                    tf_s=d["tf_s"],
                    open_time_ms=d["open_time_ms"],
                    close_time_ms=d["close_time_ms"],
                    o=d["o"],
                    h=d["h"],
                    low=d["low"],
                    c=d["c"],
                    v=d["v"],
                    complete=d.get("complete", True),
                    src=d.get("src", "history"),
                    extensions=d.get("extensions", {}),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("REPAIR_BAR_PARSE err=%s bar=%s", exc, d)

    return bars


def _page_budget(start_ms: int, end_ms: int) -> int:
    """Скільки сторінок треба, щоб курсор дійшов від ``end_ms`` назад до ``start_ms``.

    Рахуємо від ДОВЖИНИ вікна, а не від кількості дірок: курсор іде назад по
    ``_MAX_BARS_PER_FETCH`` барів незалежно від того, скільки з них — дірки. Стара
    формула ``дірок // 200 + 5`` на добовому вікні з 43 дірками давала 5 сторінок
    (~1000 хв із 1380) і тихо обривалась, не дійшовши до ранніх дірок.
    """
    span_min = (end_ms - start_ms) // TF_M1_MS + 1
    return -(-span_min // _MAX_BARS_PER_FETCH) + _PAGE_BUDGET_SLACK


def fetch_m1_for_range(
    redis_cli: Any,
    namespace: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    gap_opens: Set[int],
) -> List[CandleBar]:
    """Fetch M1 bars від broker_sidecar для покриття gap діапазону.

    Пагінує назад: від end_ms до start_ms, 200 барів за запит,
    зменшуючи date_to_ms до найстарішого бару кожного batch.
    """
    all_bars: List[CandleBar] = []
    cursor_ms: Optional[int] = end_ms + TF_M1_MS  # exclusive end
    fetched_opens: Set[int] = set()
    page = 0
    max_pages = _page_budget(start_ms, end_ms)
    stop_reason: Optional[str] = None  # None після циклу = вичерпали бюджет сторінок
    # Скільки барів брокер реально віддав: відрізняє «хвилин нема» від «відповіді нема».
    broker_bars_total = 0

    while page < max_pages:
        page += 1
        log.info(
            "REPAIR_FETCH_PAGE page=%d symbol=%s date_to=%s gap_remaining=%d",
            page,
            symbol,
            _ms_to_hm(cursor_ms) if cursor_ms else "latest",
            len(gap_opens - fetched_opens),
        )

        # Retry: FXCM SDK повертає порожній результат ~50% часу (throttling)
        bars: List[CandleBar] = []
        for attempt in range(1, _FETCH_RETRIES + 1):
            bars = _fetch_from_sidecar(
                redis_cli,
                namespace,
                symbol,
                _MAX_BARS_PER_FETCH,
                date_to_ms=cursor_ms,
            )
            if bars:
                break
            if attempt < _FETCH_RETRIES:
                log.info(
                    "REPAIR_FETCH_RETRY page=%d attempt=%d/%d (retry через %gs)",
                    page,
                    attempt,
                    _FETCH_RETRIES,
                    _FETCH_RETRY_DELAY_S,
                )
                time.sleep(_FETCH_RETRY_DELAY_S)

        if not bars:
            log.warning(
                "REPAIR_FETCH_PAGE_FAILED page=%d symbol=%s після %d спроб",
                page,
                symbol,
                _FETCH_RETRIES,
            )
            stop_reason = "page_failed"
            break

        broker_bars_total += len(bars)

        # Фільтруємо: тільки бари, що потрапляють у gaps
        matched = [
            b
            for b in bars
            if b.open_time_ms in gap_opens and b.open_time_ms not in fetched_opens
        ]
        all_bars.extend(matched)
        for b in matched:
            fetched_opens.add(b.open_time_ms)

        oldest_ms = min(b.open_time_ms for b in bars)
        log.info(
            "REPAIR_FETCH_PAGE_RESULT page=%d fetched=%d matched=%d oldest=%s total_matched=%d",
            page,
            len(bars),
            len(matched),
            _ms_to_hm(oldest_ms),
            len(all_bars),
        )

        # Перевіряємо чи покрили весь діапазон
        if fetched_opens >= gap_opens:
            log.info("REPAIR_FETCH_COMPLETE всі %d гапів покрито", len(gap_opens))
            stop_reason = "complete"
            break

        # Зсуваємо cursor для наступної сторінки
        if oldest_ms <= start_ms:
            log.info(
                "REPAIR_FETCH_REACHED_START oldest=%s <= start=%s",
                _ms_to_hm(oldest_ms),
                _ms_to_hm(start_ms),
            )
            stop_reason = "reached_start"
            break

        cursor_ms = oldest_ms  # date_to = exclusive, fetch bars before this
        time.sleep(1.0)  # throttle між сторінками

    if stop_reason is None:
        # Не дійшли до start і не покрили всі дірки: «fetched < gaps» тут означає
        # «не догребли», а не «у брокера цих хвилин немає».
        log.warning(
            "REPAIR_FETCH_PAGE_BUDGET_EXHAUSTED symbol=%s pages=%d remaining=%d "
            "— звузь вікно --start/--end",
            symbol,
            page,
            len(gap_opens - fetched_opens),
        )

    missing_at_broker = len(gap_opens - fetched_opens)
    if stop_reason == "reached_start" and missing_at_broker:
        # Курсор пройшов усе вікно: дірки, яких немає серед барів брокера, у брокера й
        # не існують. Це відповідь на питання ремонту, а не збій sidecar чи FXCM.
        log.warning(
            "REPAIR_BROKER_LACKS_MINUTES symbol=%s missing_at_broker=%d of gaps=%d "
            "broker_bars=%d — бекфіл їх не поверне",
            symbol,
            missing_at_broker,
            len(gap_opens),
            broker_bars_total,
        )

    if not all_bars:
        log.warning(
            "REPAIR_FETCH_FAILED symbol=%s жоден бар не покриває гапи.",
            symbol,
        )

    log.info(
        "REPAIR_FETCH_TOTAL symbol=%s pages=%d bars=%d / gaps=%d",
        symbol,
        page,
        len(all_bars),
        len(gap_opens),
    )
    return all_bars


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
            "groups": [
                {"status": "DRY_RUN", "fetched": len(bars), "expected": total_gaps}
            ],
        }

    # Append bars to JSONL — safe while platform is running
    written = _append_bars_to_jsonl(data_root, symbol, bars)

    return {
        "symbol": symbol,
        "total_gaps": total_gaps,
        "total_fetched": len(bars),
        "total_written": written,
        "dry_run": False,
        "groups": [
            {
                "range": f"{_ms_to_hm(global_start)}..{_ms_to_hm(global_end)}",
                "expected": total_gaps,
                "fetched": len(bars),
                "written": written,
                "status": "REPAIRED",
            }
        ],
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
                line = json.dumps(
                    bar.to_dict(), ensure_ascii=False, separators=(",", ":")
                )
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
    calendar = _load_calendar(cfg, symbol)
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
        end_iso = dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        log.info(
            '  python -m tools.repair.repair_m1_gaps --symbol "%s" '
            '--start "%s" --end "%s" --commit',
            symbol,
            start_iso,
            end_iso,
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
        log.info("✓ M1 записано на диск. Derived TF і Redis цього ще НЕ бачать.")
        # Готову команду rebuild не друкуємо: «круглі» дати вікна тихо пишуть
        # партіальний крайній H4, а --force дає конфліктні дублікати, які два шляхи
        # читання розв'язують протилежно (tail = last-wins, range = first-wins).
        log.info(
            "  1) rebuild_from_m1 БЕЗ --force, вікно по D1-якорю з config "
            "(літо: --start <дата>T21:00:00 --end <дата+2>T21:00:00), writer'и зупинені"
        )
        log.info("  2) рестарт smc-fxcm — Redis перепрайміться з диску (smc-ticks не чіпати)")
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
        # Без probe висновок «у брокера немає цих хвилин» неможливий — кажемо це прямо,
        # щоб відсутність рядка PROBE ніхто не прочитав як відповідь.
        log.warning(
            "PROBE_NOT_RUN symbol=%s — Redis недоступний; про наявність хвилин у брокера "
            "нічого не відомо",
            symbol,
        )
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
            len(bars),
            len(all_gap_opens),
        )
    else:
        log.warning(
            "PROBE: жодної з %d дірок не отримано — причина вище: "
            "REPAIR_BROKER_LACKS_MINUTES = у брокера їх немає; "
            "REPAIR_FETCH_PAGE_FAILED / TIMEOUT = брокер не відповів",
            len(all_gap_opens),
        )


def _connect_redis(cfg: dict) -> Tuple[Any, str]:
    """Redis-клієнт з тими самими ACL-креденшелами, що й у сервісів (ADR-0091 P2).

    Креденшели — з env ``AI_ONE_REDIS_USERNAME``/``AI_ONE_REDIS_PASSWORD``, не з
    config.json. На VPS вони живуть в ``environment=`` програм supervisor, а НЕ в
    ``.env``, тож запуск із голого shell їх не має — і тоді кажемо про це прямо,
    замість мовчки пропускати probe. Повертає (redis_cli, namespace) або (None, "").
    """
    try:
        import redis as redis_lib
    except ImportError:
        log.error("redis package не встановлено")
        return None, ""

    spec = resolve_redis_spec(cfg, role="repair_m1_gaps")
    if spec is None:
        log.error("REPAIR_REDIS_DISABLED — секції redis немає або redis.enabled=false")
        return None, ""

    try:
        redis_cli = redis_lib.Redis(
            host=spec.host,
            port=spec.port,
            db=spec.db,
            **spec.auth_kwargs(),
            decode_responses=True,
        )
        redis_cli.ping()
    except redis_lib.exceptions.AuthenticationError as exc:
        log.error(
            "REPAIR_REDIS_AUTH_FAILED user=%s err=%s — задай env %s і %s тієї ж ролі, що "
            "в supervisor (значення: конфіг supervisor або /root/redis-acl-*.txt; не друкуй їх)",
            spec.username or "default",
            exc,
            REDIS_USERNAME_ENV,
            REDIS_PASSWORD_ENV,
        )
        return None, ""
    except redis_lib.exceptions.RedisError as exc:
        log.error("REPAIR_REDIS_CONNECT_FAILED err=%s", exc)
        return None, ""

    return redis_cli, spec.namespace


def _load_calendar(cfg: dict, symbol: str) -> MarketCalendar:
    """Календар символу за SSOT-мапою config (fail-fast, як у воркерів).

    Без календаря інструмент не працює взагалі: він рахував би кожну хвилину
    закритого ринку діркою. Раніше саме так і було — функція читала ключ
    ``symbol_groups``, якого в config немає, і глушила виняток, тож календар
    завжди був None.
    """
    calendars, rejected = resolve_symbol_calendars(cfg, [symbol], where="repair_m1_gaps")
    if rejected or symbol not in calendars:
        raise SystemExit(
            f"REPAIR_CALENDAR_MISSING symbol={symbol} — додай його у "
            "market_calendar_symbol_groups (config.json)"
        )
    return calendars[symbol]


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
