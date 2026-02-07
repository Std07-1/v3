from __future__ import annotations

import datetime as dt
import logging
import os

from app.composition import load_config
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


def main() -> int:
    _setup_logging()
    cfg = load_config("config.json")

    symbol = _pick_symbol(cfg)
    data_root = str(cfg.get("data_root", "./data_v3"))
    out_root = os.path.join(data_root, "isolated_m5_warmup")

    user_id = str(cfg["user_id"])
    password = str(cfg["password"])
    url = str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
    connection = str(cfg.get("connection", "Demo"))

    day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
    day_anchor_offset_s_alt = cfg.get("day_anchor_offset_s_alt", None)
    day_anchor_offset_s_alt2 = cfg.get("day_anchor_offset_s_alt2", None)
    day_anchor_offset_s_d1 = cfg.get("day_anchor_offset_s_d1", None)
    day_anchor_offset_s_d1_alt = cfg.get("day_anchor_offset_s_d1_alt", None)

    date_to = dt.datetime.now(dt.timezone.utc)
    n = 5000

    logging.info("Isolated M5 warmup: symbol=%s n=%d out_root=%s", symbol, n, out_root)

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

    try:
        with provider:
            bars = provider.fetch_last_n_tf(symbol, tf_s=300, n=n, date_to_utc=date_to)
            if not bars:
                logging.warning("Isolated M5 warmup: брокер не повернув бари")
                return 2

            for b in bars:
                writer.append(b)

            first_open = bars[0].open_time_ms
            last_open = bars[-1].open_time_ms
            logging.info(
                "Isolated M5 warmup: записано=%d first_open=%s last_open=%s",
                len(bars),
                dt.datetime.fromtimestamp(first_open / 1000, dt.timezone.utc).isoformat(),
                dt.datetime.fromtimestamp(last_open / 1000, dt.timezone.utc).isoformat(),
            )
            return 0
    finally:
        writer.close()


if __name__ == "__main__":
    raise SystemExit(main())
