from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import List, Optional, Set

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


def _parse_date_utc(s):
    # type: (str) -> dt.datetime
    """Парсинг дати UTC з CLI (Python 3.7–safe)."""
    s = s.replace("Z", "").replace("+00:00", "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError("Невідомий формат дати: %s" % s)


def _load_existing_opens(data_root, symbol, start_ms, end_ms):
    # type: (str, str, int, int) -> Set[int]
    """Зібрати set(open_time_ms) з part-*.jsonl для деdup."""
    opens = set()  # type: Set[int]
    sym_dir = os.path.join(data_root, symbol.replace("/", "_"), "tf_300")
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
        description="Fetch M5 bars з FXCM. Warmup або targeted backfill.",
    )
    ap.add_argument("--symbol", default=None, help="Символ (override config)")
    ap.add_argument("--all", action="store_true", help="Усі symbols[] з конфігу")
    ap.add_argument("--date-to", default=None, help="Кінцева дата UTC ISO (default: now)")
    ap.add_argument("--n", type=int, default=5000, help="Кількість M5 барів (default: 5000)")
    ap.add_argument("--backfill", action="store_true",
                    help="Писати у головний data_root з деdup (інакше — isolated_m5_warmup)")
    args = ap.parse_args()

    load_env_secrets()
    config_path = pick_config_path()
    cfg = load_system_config(config_path)
    data_root = str(cfg.get("data_root", "./data_v3"))

    if args.backfill:
        out_root = data_root
    else:
        out_root = os.path.join(data_root, "isolated_m5_warmup")

    # --- Список символів ---
    if getattr(args, "all", False):
        sym_list = [str(s) for s in cfg.get("symbols", []) if str(s).strip()]
    elif args.symbol:
        sym_list = [args.symbol]
    else:
        sym_list = [_pick_symbol(cfg)]
    if not sym_list:
        logging.error("Порожній список символів")
        return 2

    # --- Date ---
    if args.date_to:
        date_to = _parse_date_utc(args.date_to)
    else:
        date_to = dt.datetime.now(dt.timezone.utc)

    # --- Credentials ---
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

    logging.info("Fetch M5: symbols=%d date_to=%s n=%d backfill=%s out=%s",
                 len(sym_list), date_to.isoformat(), args.n, args.backfill, out_root)

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
        root=out_root,
        day_anchor_offset_s=day_anchor_offset_s,
        day_anchor_offset_s_d1=None if day_anchor_offset_s_d1 is None else int(day_anchor_offset_s_d1),
        day_anchor_offset_s_d1_alt=None if day_anchor_offset_s_d1_alt is None else int(day_anchor_offset_s_d1_alt),
        day_anchor_offset_s_alt=None if day_anchor_offset_s_alt is None else int(day_anchor_offset_s_alt),
        day_anchor_offset_s_alt2=None if day_anchor_offset_s_alt2 is None else int(day_anchor_offset_s_alt2),
    )

    total_written = 0
    total_skipped = 0
    errors = []  # type: List[str]

    try:
        with provider:
            for symbol in sym_list:
                logging.info("%s: запит %d M5 барів до %s …",
                             symbol, args.n, date_to.strftime("%Y-%m-%dT%H:%MZ"))
                bars = provider.fetch_last_n_tf(symbol, tf_s=300, n=args.n,
                                               date_to_utc=date_to)
                if not bars:
                    logging.warning("%s: брокер не повернув бари", symbol)
                    errors.append(symbol)
                    continue

                # Dedup (тільки --backfill)
                if args.backfill:
                    first_ms = bars[0].open_time_ms
                    last_ms = bars[-1].open_time_ms
                    existing = _load_existing_opens(out_root, symbol, first_ms, last_ms)
                    before = len(bars)
                    bars = [b for b in bars if b.open_time_ms not in existing]
                    skipped = before - len(bars)
                    total_skipped += skipped
                    if skipped:
                        logging.info("%s: dedup — пропущено %d, нових %d",
                                     symbol, skipped, len(bars))

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

    logging.info("=== ПІДСУМОК: записано=%d пропущено(dedup)=%d помилок=%d ===",
                 total_written, total_skipped, len(errors))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
