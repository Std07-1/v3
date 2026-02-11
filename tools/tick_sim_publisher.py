from __future__ import annotations

import argparse
import json
import os
import random
import time
from typing import Optional

from env_profile import load_env_profile

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


def _env_str(key: str) -> Optional[str]:
    value = os.environ.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _pick_tick_channel() -> Optional[str]:
    channel = _env_str("FXCM_PRICE_TICK_CHANNEL")
    if channel:
        return channel
    legacy = _env_str("FXCM_PRICE_SNAPSHOT_CHANNEL")
    if legacy:
        return legacy
    return None


def _pick_config_path(raw_path: Optional[str]) -> Optional[str]:
    if raw_path:
        return raw_path
    env_path = (os.environ.get("AI_ONE_CONFIG_PATH") or "").strip()
    if env_path:
        return env_path
    return "config.json"


def _load_redis_cfg(config_path: str) -> Optional[dict]:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    raw = cfg.get("redis")
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("enabled", False)):
        return None
    return raw


def _build_payload(
    symbol: str,
    base_price: float,
    spread: float,
    jitter: float,
) -> dict:
    delta = random.uniform(-jitter, jitter) if jitter > 0 else 0.0
    mid = float(base_price + delta)
    half_spread = float(spread) / 2.0
    bid = mid - half_spread
    ask = mid + half_spread
    return {
        "v": 1,
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "tick_ts_ms": int(time.time() * 1000),
        "src": "sim",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=None)
    ap.add_argument("--symbol", default="XAU/USD")
    ap.add_argument("--hz", type=float, default=2.0)
    ap.add_argument("--base-price", type=float, default=2040.0)
    ap.add_argument("--spread", type=float, default=0.04)
    ap.add_argument("--jitter", type=float, default=0.05)
    ap.add_argument("--duration-s", type=float, default=10.0)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    load_env_profile()

    if redis_lib is None:
        print("redis бібліотека недоступна")
        return 2

    channel = args.channel or _pick_tick_channel()
    if not channel:
        print("tick-канал не заданий (FXCM_PRICE_TICK_CHANNEL)")
        return 2

    config_path = _pick_config_path(args.config)
    redis_cfg = _load_redis_cfg(config_path)
    if redis_cfg is None:
        print("Redis вимкнено або конфіг недоступний")
        return 2

    host = str(redis_cfg.get("host", "127.0.0.1"))
    port = int(redis_cfg.get("port", 6379))
    db = int(redis_cfg.get("db", 0))

    client = redis_lib.Redis(
        host=host,
        port=port,
        db=db,
        decode_responses=False,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
    )

    hz = float(args.hz)
    interval_s = 1.0 / hz if hz > 0 else 0.5
    end_ts = time.time() + float(args.duration_s)
    sent = 0

    while True:
        now = time.time()
        if args.duration_s > 0 and now >= end_ts:
            break
        payload = _build_payload(args.symbol, args.base_price, args.spread, args.jitter)
        raw = json.dumps(payload, ensure_ascii=False)
        client.publish(channel, raw.encode("utf-8"))
        sent += 1
        time.sleep(interval_s)

    print("sent", sent, "channel", channel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
