from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from collections import Counter
from typing import List, Set, Tuple

from env_profile import load_env_secrets
from core.config_loader import pick_config_path, load_system_config, env_str
from core.derive import DERIVE_SOURCE
from core.model.bars import CandleBar
from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
from runtime.ingest.m1_session_filter import (
    VERDICT_PAUSE_EDGE_STALE_DROPPED,
    VERDICT_PAUSE_FLAT_DROPPED,
    VERDICT_PAUSE_NOISE_DROPPED,
    VERDICT_PAUSE_NONFLAT_ANOMALY,
    PausePolicy,
    classify_m1_by_calendar,
    resolve_close_safety_ms,
    resolve_flat_max_volume,
    resolve_pause_policy,
    split_closed_bars,
)
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.tick_common import resolve_symbol_calendars
from runtime.store.ssot_jsonl import JsonlAppender


# Усе, що будує DeriveEngine з M1, заборонено тягнути з брокера напряму.
# frozenset(DERIVE_SOURCE) = {180, 300, 900, 1800, 3600, 14400, 86400}; M1 (60) —
# єдиний source-TF ланцюга, тому в множині його немає за побудовою.
DERIVED_ONLY_TFS = frozenset(DERIVE_SOURCE)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _pick_symbol(cfg: dict) -> str:
    symbol = str(cfg.get("symbol", "")).strip()
    if symbol:
        return symbol
    symbols = cfg.get("symbols")
    if isinstance(symbols, list) and symbols:
        return str(symbols[0])
    return "XAU/USD"


# Курсор ланцюжка засіву: `first=` цієї партії = `--date-to` наступної. Один формат для друку і розбору.
# До 2026-09-14 курсор друкувався без секунд, а `--date-to` приймав лише з секундами або саму дату —
# оператор обрізав курсор до доби, і кожен крок лишав дірку від 00:00 до першого бару попередньої
# партії: у XAU/XAG по 10 дірок на символ, до ~12 год кожна (ADR-0094 P4, root/uncovered).
CURSOR_FMT = "%Y-%m-%dT%H:%M:%SZ"
_ACCEPTED_DATE_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


def _format_cursor(open_ms: int) -> str:
    return dt.datetime.fromtimestamp(open_ms / 1000, tz=dt.timezone.utc).strftime(CURSOR_FMT)


def _parse_date_utc(s: str) -> dt.datetime:
    s = s.replace("Z", "").replace("+00:00", "").strip()
    for fmt in _ACCEPTED_DATE_FMTS:
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError("Невідомий формат дати: %s" % s)


# Засів «до зараз» (без --date-to або з курсором у поточній хвилині) отримує від брокера і бар, що ще
# формується: FXCM віддає його як звичайний рядок історії, нормалізація ставить complete=true. 15.09.2026
# так на диск ліг GER30 17:12 з v=41 проти ~130 у сусідів. Повторний засів його не виправить: бари, чиї
# open вже є на диску, пропускаються. Тому бар пишемо лише закритим — правило і запас спільні для всіх
# записувачів M1: `runtime/ingest/m1_session_filter.resolve_close_safety_ms` / `split_closed_bars`.


# Скільки хвилин поза календарем у партії ще можна списати на межу сесії (брокер віддає хвилину-дві навколо межі),
# а не на хибний календар. Понад це — засів відмовляється писати: інакше при хибному календарі (GER30 у config
# 07–21 проти справжніх 00:31–19:59) тихо зникало б ~390 справжніх хвилин на добу, і жоден детектор цього не
# побачив би, бо детектор — той самий календар.
_OFF_CALENDAR_ALLOWANCE = 3


def _filter_m1_by_session(
    bars: List[CandleBar], calendar: MarketCalendar, flat_max_volume: int, pause_policy: PausePolicy
) -> Tuple[List[CandleBar], Counter, List[int]]:
    """Те саме правило SSOT, що в живому M1-полері (`m1_session_filter.classify_m1_by_calendar`): шум глибоко в паузі
    і пласкі бари поза сесією не пишуться, неплаский біля краю сесії — з маркером anomaly.

    Засів раніше писав усе, що віддав брокер: NAS100 і US30 мають пласкі хвилини Сб 22:00 саме з засіву (15.09).
    Третій елемент — open_ms усіх хвилин поза календарем (зокрема відкинутих як шум), щоб оператор бачив, ЯКІ саме,
    а не лише скільки: допуск _OFF_CALENDAR_ALLOWANCE рахує їх усі, інакше хибний календар (справжні хвилини глибоко
    в «паузі») тихо пішов би у шум.
    """
    kept: List[CandleBar] = []
    verdicts: Counter = Counter()
    off_calendar: List[int] = []
    for bar in bars:
        trading = calendar.is_trading_minute(bar.open_time_ms)
        classified, verdict = classify_m1_by_calendar(bar, calendar.is_trading_minute, flat_max_volume, pause_policy)
        verdicts[verdict] += 1
        if not trading:
            off_calendar.append(bar.open_time_ms)
        if classified is not None:
            kept.append(classified)
    return kept, verdicts, off_calendar


def _describe_off_calendar(off_calendar: List[int]) -> str:
    """Години UTC з кількостями + перша й остання хвилина — щоб хибний календар було видно з одного рядка."""
    hours = Counter(dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%H") for ms in off_calendar)
    return "n=%d first=%s last=%s by_hour=%s" % (
        len(off_calendar), _format_cursor(min(off_calendar)), _format_cursor(max(off_calendar)),
        dict(sorted(hours.items())),
    )


def _load_existing_opens(data_root: str, symbol: str, tf_s: int, start_ms: int, end_ms: int) -> Set[int]:
    opens: Set[int] = set()
    sym_dir = os.path.join(data_root, symbol.replace("/", "_"), "tf_%d" % tf_s)
    if not os.path.isdir(sym_dir):
        return opens
    day_start = dt.datetime.fromtimestamp(start_ms / 1000, tz=dt.timezone.utc).replace(tzinfo=None).date()
    day_end = dt.datetime.fromtimestamp(end_ms / 1000, tz=dt.timezone.utc).replace(tzinfo=None).date()
    d = day_start
    while d <= day_end:
        path = os.path.join(sym_dir, "part-%s.jsonl" % d.strftime("%Y%m%d"))
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ot = json.loads(line).get("open_time_ms")
                            if isinstance(ot, int) and start_ms <= ot <= end_ms:
                                opens.add(ot)
                        except Exception:
                            continue
            except Exception:
                pass
        d += dt.timedelta(days=1)
    return opens


def main() -> int:
    _setup_logging()
    ap = argparse.ArgumentParser(
        description="Backfill TF bars з FXCM у SSOT (data_root).",
    )
    ap.add_argument("--tf", type=int, required=True, help="TF у секундах (60/180/300/900/1800/3600/86400)")
    ap.add_argument("--symbol", default=None, help="Символ (override config)")
    ap.add_argument("--all", action="store_true", help="Усі symbols[] з конфігу")
    ap.add_argument("--date-to", default=None, help="Кінцева дата UTC ISO (default: now)")
    ap.add_argument("--n", type=int, required=True, help="Кількість барів")
    ap.add_argument("--force-derived-tf", action="store_true", default=False,
                    help="Дозволити fetch derived-only TF. Небезпечно — anchor mismatch!")
    ap.add_argument("--allow-off-calendar", action="store_true", default=False,
                    help=("Писати партію, навіть якщо брокер віддав більше за %d хвилин поза календарем групи "
                          "(інакше засів відмовляється: ймовірно хибний календар)" % _OFF_CALENDAR_ALLOWANCE))
    args = ap.parse_args()

    # Guard: з брокера тягнемо ТІЛЬКИ M1. Усе інше будує DeriveEngine на своїй
    # сітці якорів (ADR-0002 H4, ADR-0023 D1); прямий fetch дає anchor mismatch —
    # інцидент з D1 у runbook fxcm_credential_rotation.md §«D1 anchor роз'їзд».
    # SSOT множини — core.derive.DERIVE_CHAIN (через DERIVE_SOURCE), а не літерал:
    # новий TF у ланцюзі має автоматично потрапляти під guard (ADR-0054 §3.1 P0.1).
    if args.tf in DERIVED_ONLY_TFS and not args.force_derived_tf:
        logging.error(
            "TF=%ds є derived-only (деривується з %ds за core.derive.DERIVE_CHAIN). "
            "FXCM має інший anchor grid → бари роз'їдуться. "
            "Правильний шлях: fetch --tf 60, далі rebuild_from_m1. "
            "Якщо дійсно потрібно — додайте --force-derived-tf.",
            args.tf,
            DERIVE_SOURCE[args.tf][0],
        )
        return 1

    load_env_secrets()
    config_path = pick_config_path()
    cfg = load_system_config(config_path)
    data_root = str(cfg.get("data_root", "./data_v3"))

    if getattr(args, "all", False):
        sym_list = [str(s) for s in cfg.get("symbols", []) if str(s).strip()]
    elif args.symbol:
        sym_list = [args.symbol]
    else:
        sym_list = [_pick_symbol(cfg)]
    if not sym_list:
        logging.error("Порожній список символів")
        return 2
    # Календар обовʼязковий: без нього не відрізнити хвилину сесії від шуму брокера після закриття (fail-closed,
    # як у живих воркерах). Хибний календар видно з лічильників pause_nonflat_anomaly / pause_noise_dropped у лозі
    # кожного кроку, а понад _OFF_CALENDAR_ALLOWANCE хвилин поза календарем засів відмовляється писати.
    calendars, rejected = resolve_symbol_calendars(cfg, sym_list, where="fetch_tf_backfill")
    if rejected:
        logging.error("BACKFILL_REFUSED symbols=%s — немає календаря сесії (market_calendar_symbol_groups)", ",".join(rejected))
        return 2
    flat_max_volume = resolve_flat_max_volume(cfg)
    pause_policy = resolve_pause_policy(cfg)

    if args.date_to:
        date_to = _parse_date_utc(args.date_to)
    else:
        date_to = dt.datetime.now(dt.timezone.utc)

    user_id = env_str("FXCM_USERNAME") or str(cfg.get("user_id") or "").strip()
    password = env_str("FXCM_PASSWORD") or str(cfg.get("password") or "").strip()
    url = env_str("FXCM_HOST_URL") or str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
    connection = env_str("FXCM_CONNECTION") or str(cfg.get("connection", "Demo"))
    if not user_id or not password:
        logging.error("Відсутні FXCM креденшіали (ENV або config)")
        return 2

    day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
    day_anchor_offset_s_alt = cfg.get("day_anchor_offset_s_alt", None)
    day_anchor_offset_s_alt2 = cfg.get("day_anchor_offset_s_alt2", None)
    day_anchor_offset_s_d1 = cfg.get("day_anchor_offset_s_d1", None)
    day_anchor_offset_s_d1_alt = cfg.get("day_anchor_offset_s_d1_alt", None)
    close_safety_ms = resolve_close_safety_ms(cfg)

    logging.info(
        "Backfill TF=%d: symbols=%d date_to=%s n=%d out=%s",
        args.tf, len(sym_list), date_to.isoformat(), args.n, data_root,
    )

    provider = FxcmHistoryProvider(
        user_id=user_id,
        password=password,
        url=url,
        connection=connection,
        day_anchor_offset_s=day_anchor_offset_s,
        day_anchor_offset_s_d1=None if day_anchor_offset_s_d1 is None else int(day_anchor_offset_s_d1),
        day_anchor_offset_s_d1_alt=None if day_anchor_offset_s_d1_alt is None else int(day_anchor_offset_s_d1_alt),
        day_anchor_offset_s_alt=None if day_anchor_offset_s_alt is None else int(day_anchor_offset_s_alt),
        day_anchor_offset_s_alt2=None if day_anchor_offset_s_alt2 is None else int(day_anchor_offset_s_alt2),
    )

    writer = JsonlAppender(
        root=data_root,
        day_anchor_offset_s=day_anchor_offset_s,
        day_anchor_offset_s_d1=None if day_anchor_offset_s_d1 is None else int(day_anchor_offset_s_d1),
        day_anchor_offset_s_d1_alt=None if day_anchor_offset_s_d1_alt is None else int(day_anchor_offset_s_d1_alt),
        day_anchor_offset_s_alt=None if day_anchor_offset_s_alt is None else int(day_anchor_offset_s_alt),
        day_anchor_offset_s_alt2=None if day_anchor_offset_s_alt2 is None else int(day_anchor_offset_s_alt2),
    )

    total_written = 0
    total_skipped = 0
    total_verdicts: Counter = Counter()
    errors: List[str] = []

    try:
        with provider:
            for symbol in sym_list:
                logging.info(
                    "%s: запит %d TF=%d барів до %s …",
                    symbol, args.n, args.tf, date_to.strftime(CURSOR_FMT),
                )
                if args.tf == 60:
                    bars = provider.fetch_last_n_m1(symbol, n=args.n, date_to_utc=date_to)
                else:
                    bars = provider.fetch_last_n_tf(symbol, tf_s=args.tf, n=args.n, date_to_utc=date_to)
                if not bars:
                    logging.warning("%s: брокер не повернув бари", symbol)
                    errors.append(symbol)
                    continue

                first_ms = bars[0].open_time_ms
                # Точний курсор наступного кроку — з ПОВЕРНЕНИХ брокером барів, до dedup: навіть
                # якщо все вже є на диску, ланцюжок мусить іти від справжнього першого бару.
                logging.info(
                    "%s: BACKFILL_NEXT --date-to %s (перший бар цієї партії; дублікат межі прибере dedup)",
                    symbol, _format_cursor(first_ms),
                )
                bars, unclosed = split_closed_bars(bars, int(time.time() * 1000), close_safety_ms)
                if unclosed:
                    logging.warning(
                        "%s: BACKFILL_UNCLOSED_DROPPED n=%d first=%s — бар ще формується, його допише полер або наступний засів",
                        symbol, len(unclosed), _format_cursor(unclosed[0].open_time_ms),
                    )
                if args.tf == 60:
                    bars, verdicts, off_calendar = _filter_m1_by_session(
                        bars, calendars[symbol], flat_max_volume, pause_policy
                    )
                    total_verdicts.update(verdicts)
                    logging.log(
                        logging.WARNING if off_calendar else logging.INFO,
                        "%s: BACKFILL_SESSION_FILTER %s%s",
                        symbol, dict(sorted(verdicts.items())),
                        " | поза календарем: " + _describe_off_calendar(off_calendar) if off_calendar else "",
                    )
                    if len(off_calendar) > _OFF_CALENDAR_ALLOWANCE and not args.allow_off_calendar:
                        logging.error(
                            "%s: BACKFILL_CALENDAR_SUSPECT %s — брокер віддає хвилини поза календарем групи; "
                            "або календар символу хибний (перевірте market_calendar_by_group), або це справді "
                            "позасесійний шум. Нічого не записано. Свідомо продовжити: --allow-off-calendar",
                            symbol, _describe_off_calendar(off_calendar),
                        )
                        errors.append(symbol)
                        continue
                else:
                    logging.info(
                        "%s: фільтр сесії не застосовано (TF=%d ≠ 60; правило M1→SSOT — лише для хвилин)",
                        symbol, args.tf,
                    )
                if not bars:
                    logging.info(
                        "%s: після відсіву нічого не лишилось (закритих барів у партії 0)", symbol,
                    )
                    continue
                last_ms = bars[-1].open_time_ms
                existing = _load_existing_opens(data_root, symbol, args.tf, first_ms, last_ms)
                before = len(bars)
                bars = [b for b in bars if b.open_time_ms not in existing]
                skipped = before - len(bars)
                total_skipped += skipped
                if skipped:
                    logging.info("%s: dedup — пропущено %d, нових %d", symbol, skipped, len(bars))

                for b in bars:
                    writer.append(b)
                total_written += len(bars)

                if bars:
                    logging.info(
                        "%s: записано=%d first=%s last=%s",
                        symbol, len(bars),
                        _format_cursor(bars[0].open_time_ms),
                        _format_cursor(bars[-1].open_time_ms),
                    )
                else:
                    logging.info("%s: 0 нових барів (усе вже є)", symbol)
    finally:
        writer.close()

    dropped = total_verdicts[VERDICT_PAUSE_FLAT_DROPPED]
    noise = total_verdicts[VERDICT_PAUSE_NOISE_DROPPED]
    edge_stale = total_verdicts[VERDICT_PAUSE_EDGE_STALE_DROPPED]
    anomalies = total_verdicts[VERDICT_PAUSE_NONFLAT_ANOMALY]
    logging.log(
        logging.WARNING if dropped or noise or edge_stale or anomalies else logging.INFO,
        "=== ПІДСУМОК: записано=%d пропущено(dedup)=%d відсіяно(пласкі поза сесією)=%d "
        "відсіяно(шум глибоко в паузі, margin=%s хв)=%d відсіяно(застарілий край, v<=%s)=%d "
        "аномалій(непласкі біля краю сесії)=%d помилок=%d ===",
        total_written, total_skipped, dropped, pause_policy.noise_margin_min, noise,
        pause_policy.edge_stale_max_volume, edge_stale, anomalies, len(errors),
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
