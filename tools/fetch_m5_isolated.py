from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Optional

from env_profile import load_env_profile
from app.composition import load_config
from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
from runtime.store.ssot_jsonl import JsonlAppender


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _env_str(key: str) -> Optional[str]:
    value = os.environ.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _resolve_config_path(raw_path: str | None) -> str:
    base_dir = Path(__file__).resolve().parents[1]
    raw_value = (raw_path or "").strip()
    if not raw_value:
        return str((base_dir / "config.json").resolve())
    if Path(raw_value).is_absolute():
        return str(Path(raw_value).resolve())
    return str((base_dir / raw_value).resolve())


def _pick_config_path() -> str:
    env_path = (os.environ.get("AI_ONE_CONFIG_PATH") or "").strip()
    if env_path:
        return _resolve_config_path(env_path)
    return _resolve_config_path("config.json")


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
    load_env_profile()
    config_path = _pick_config_path()
    cfg = load_config(config_path)

    symbol = _pick_symbol(cfg)
    data_root = str(cfg.get("data_root", "./data_v3"))
    out_root = os.path.join(data_root, "isolated_m5_warmup")

    user_id = _env_str("FXCM_USERNAME") or str(cfg.get("user_id") or "").strip()
    password = _env_str("FXCM_PASSWORD") or str(cfg.get("password") or "").strip()
    url = _env_str("FXCM_HOST_URL") or str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
    connection = _env_str("FXCM_CONNECTION") or str(cfg.get("connection", "Demo"))
    if not user_id or not password:
        logging.error("Isolated M5 warmup: відсутні FXCM креденшіали (ENV або config)")
        return 2

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
