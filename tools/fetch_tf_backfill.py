from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from typing import List, Set

from env_profile import load_env_secrets
from core.config_loader import pick_config_path, load_system_config, env_str
from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
from runtime.store.ssot_jsonl import JsonlAppender


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


def _parse_date_utc(s: str) -> dt.datetime:
    s = s.replace("Z", "").replace("+00:00", "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError("Невідомий формат дати: %s" % s)


def _load_existing_opens(data_root: str, symbol: str, tf_s: int, start_ms: int, end_ms: int) -> Set[int]:
    opens: Set[int] = set()
    sym_dir = os.path.join(data_root, symbol.replace("/", "_"), "tf_%d" % tf_s)
    if not os.path.isdir(sym_dir):
        return opens
    day_start = dt.datetime.utcfromtimestamp(start_ms / 1000).date()
    day_end = dt.datetime.utcfromtimestamp(end_ms / 1000).date()
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
    ap.add_argument("--tf", type=int, required=True, help="TF у секундах (60/180/300/14400/86400)")
    ap.add_argument("--symbol", default=None, help="Символ (override config)")
    ap.add_argument("--all", action="store_true", help="Усі symbols[] з конфігу")
    ap.add_argument("--date-to", default=None, help="Кінцева дата UTC ISO (default: now)")
    ap.add_argument("--n", type=int, required=True, help="Кількість барів")
    args = ap.parse_args()

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
    errors: List[str] = []

    try:
        with provider:
            for symbol in sym_list:
                logging.info(
                    "%s: запит %d TF=%d барів до %s …",
                    symbol, args.n, args.tf, date_to.strftime("%Y-%m-%dT%H:%MZ"),
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
                        dt.datetime.utcfromtimestamp(bars[0].open_time_ms / 1000).strftime("%Y-%m-%dT%H:%MZ"),
                        dt.datetime.utcfromtimestamp(bars[-1].open_time_ms / 1000).strftime("%Y-%m-%dT%H:%MZ"),
                    )
                else:
                    logging.info("%s: 0 нових барів (усе вже є)", symbol)
    finally:
        writer.close()

    logging.info(
        "=== ПІДСУМОК: записано=%d пропущено(dedup)=%d помилок=%d ===",
        total_written, total_skipped, len(errors),
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
