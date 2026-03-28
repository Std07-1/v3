"""
SMC v3 → Cloudflare Worker Signal Relay
Надсилає сигнали (BOS/CHoCH/bias) у Cloudflare KV через Worker API.
"""
import asyncio
import logging
import os
from typing import Any

import aiohttp
from dotenv import load_dotenv

load_dotenv()

RELAY_URL = os.getenv("SMC_RELAY_URL", "").rstrip("/")
RELAY_TOKEN = os.getenv("SMC_RELAY_TOKEN", "")
TIMEOUT = aiohttp.ClientTimeout(total=5)

logger = logging.getLogger(__name__)


async def _post(endpoint: str, payload: dict[str, Any]) -> bool:
    if not RELAY_URL or not RELAY_TOKEN:
        logger.warning("relay: SMC_RELAY_URL or SMC_RELAY_TOKEN not set — skipping")
        return False
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(
                f"{RELAY_URL}/{endpoint}",
                json=payload,
                headers={"X-Auth-Token": RELAY_TOKEN},
            ) as resp:
                ok = resp.status == 200
                if not ok:
                    logger.warning("relay: %s → HTTP %s", endpoint, resp.status)
                return ok
    except Exception as exc:
        logger.warning("relay: %s failed — %s", endpoint, exc)
        return False


async def push_signal(
    symbol: str,
    tf: int,
    signal_type: str,       # "BOS_BULL" | "BOS_BEAR" | "CHoCH_BULL" | "CHoCH_BEAR" | "OB"
    price: float,
    direction: str,         # "bullish" | "bearish"
    details: dict[str, Any] | None = None,
) -> bool:
    """Надіслати торговий сигнал при зміні структури ринку."""
    payload = {
        "symbol": symbol,
        "tf": tf,
        "type": signal_type,
        "price": price,
        "direction": direction,
        "details": details or {},
    }
    return await _post("signal", payload)


async def push_bias(
    symbol: str,
    tf: int,
    bias: str,              # "bullish" | "bearish" | "neutral"
    confidence: float,      # 0.0 – 1.0
    key_levels: dict[str, float] | None = None,
    context: str = "",
) -> bool:
    """Оновити поточний bias символу."""
    payload = {
        "symbol": symbol,
        "tf": tf,
        "bias": bias,
        "confidence": confidence,
        "key_levels": key_levels or {},
        "context": context,
    }
    return await _post("bias", payload)


def fire_signal(symbol, tf, signal_type, price, direction, details=None):
    """Sync wrapper — для виклику з не-async контексту."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(push_signal(symbol, tf, signal_type, price, direction, details))
        else:
            loop.run_until_complete(push_signal(symbol, tf, signal_type, price, direction, details))
    except Exception as exc:
        logger.warning("relay fire_signal: %s", exc)


def fire_bias(symbol, tf, bias, confidence, key_levels=None, context=""):
    """Sync wrapper — для виклику з не-async контексту."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(push_bias(symbol, tf, bias, confidence, key_levels, context))
        else:
            loop.run_until_complete(push_bias(symbol, tf, bias, confidence, key_levels, context))
    except Exception as exc:
        logger.warning("relay fire_bias: %s", exc)
