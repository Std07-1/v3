"""Broker Sidecar — stateless FXCM M1 fetcher (Python 3.7, ADR-0016).

Працює у .venv37/. Отримує команди fetch через Redis list,
повертає бари через Redis list. Не має стану scheduling/watermark —
вся бізнес-логіка в m1_ingestion_worker (3.12+).

Redis queues (namespace з config.json):
  {ns}:broker:m1:cmd   — BLPOP (commands)
  {ns}:broker:m1:bars  — RPUSH (responses)

Command contract v1:
  {"v": 1, "cmd": "fetch_m1", "symbol": "XAU/USD", "n_bars": 5, "date_to_ms": 1741392060000}

Response contract v1:
  {"v": 1, "symbol": "XAU/USD", "bars": [{...}, ...], "error": null}
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time

from core.config_loader import pick_config_path, load_system_config, env_str
from core.model.bars import ms_to_utc_dt
from env_profile import load_env_secrets
from runtime.store.redis_spec import resolve_redis_spec

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CMD_QUEUE_SUFFIX = "broker:m1:cmd"
_BARS_QUEUE_SUFFIX = "broker:m1:bars"
_MAX_LIST_LEN = 10000  # LTRIM safety rail (ADR-0016 §F4)
_BLPOP_TIMEOUT_S = 5  # seconds
_RECONNECT_COOLDOWN_S = 30
_MAX_BARS_PER_CMD = 200  # guard against huge requests
_CONTRACT_VERSION = 1

_running = True


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _signal_handler(sig, _frame):
    global _running
    _running = False
    logging.info("BROKER_SIDECAR_SIGNAL sig=%s", sig)


def _build_provider(cfg):
    """Створити FxcmHistoryProvider з config + env."""
    from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider

    user_id = env_str("FXCM_USERNAME")
    password = env_str("FXCM_PASSWORD")
    url = env_str("FXCM_HOST_URL")
    connection = env_str("FXCM_CONNECTION") or "Demo"

    if not user_id or not password or not url:
        raise RuntimeError("FXCM credentials missing (FXCM_USERNAME/PASSWORD/HOST_URL)")

    anchor_s = int(cfg.get("day_anchor_offset_s", 0))
    d1_anchor_s = int(cfg.get("day_anchor_offset_s_d1", 0))

    return FxcmHistoryProvider(
        user_id=user_id,
        password=password,
        url=url,
        connection=connection,
        day_anchor_offset_s=anchor_s,
        day_anchor_offset_s_d1=d1_anchor_s,
    )


def _handle_command(provider, cmd_raw, redis_cli, bars_key):
    """Обробити одну команду fetch і записати результат у Redis."""
    try:
        cmd = json.loads(cmd_raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logging.warning(
            "BROKER_SIDECAR_CMD_PARSE_ERROR raw=%r err=%s", cmd_raw[:200], exc
        )
        return

    v = cmd.get("v", 0)
    if v != _CONTRACT_VERSION:
        logging.warning(
            "BROKER_SIDECAR_CMD_VERSION_MISMATCH v=%s expected=%d", v, _CONTRACT_VERSION
        )
        return

    action = cmd.get("cmd", "")
    symbol = cmd.get("symbol", "")
    n_bars = min(int(cmd.get("n_bars", 5)), _MAX_BARS_PER_CMD)
    date_to_ms = cmd.get("date_to_ms")

    if action != "fetch_m1" or not symbol:
        logging.warning("BROKER_SIDECAR_CMD_UNKNOWN cmd=%s symbol=%s", action, symbol)
        return

    # Convert date_to_ms → datetime (provider expects tz-aware UTC)
    date_to_utc = None
    if date_to_ms is not None:
        date_to_utc = ms_to_utc_dt(int(date_to_ms))

    try:
        bars = provider.fetch_last_n_m1(symbol, n=n_bars, date_to_utc=date_to_utc)
    except Exception as exc:
        # Повертаємо error response, worker вирішить що робити
        error_resp = json.dumps(
            {
                "v": _CONTRACT_VERSION,
                "symbol": symbol,
                "bars": [],
                "error": str(exc),
            }
        )
        redis_cli.rpush(bars_key, error_resp)
        redis_cli.ltrim(bars_key, -_MAX_LIST_LEN, -1)
        logging.warning("BROKER_SIDECAR_FETCH_ERROR symbol=%s err=%s", symbol, exc)
        return

    # Серіалізація: batch response
    bar_dicts = [b.to_dict() for b in bars] if bars else []
    resp = json.dumps(
        {
            "v": _CONTRACT_VERSION,
            "symbol": symbol,
            "bars": bar_dicts,
            "error": None,
        }
    )
    redis_cli.rpush(bars_key, resp)
    redis_cli.ltrim(bars_key, -_MAX_LIST_LEN, -1)

    if bars:
        logging.debug(
            "BROKER_SIDECAR_FETCHED symbol=%s n=%d returned=%d",
            symbol,
            n_bars,
            len(bars),
        )


def main():
    # type: () -> int
    _setup_logging()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    report = load_env_secrets()
    if report.loaded:
        logging.info(
            "ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count
        )

    config_path = pick_config_path()
    cfg = load_system_config(config_path)
    logging.info("BROKER_SIDECAR_CONFIG path=%s", config_path)

    # Redis
    if redis_lib is None:
        logging.error("BROKER_SIDECAR_NO_REDIS (pip install redis)")
        return 1

    spec = resolve_redis_spec(cfg, role="broker_sidecar", log=True)
    if spec is None:
        logging.error("BROKER_SIDECAR_REDIS_DISABLED")
        return 1

    cmd_key = "%s:%s" % (spec.namespace, _CMD_QUEUE_SUFFIX)
    bars_key = "%s:%s" % (spec.namespace, _BARS_QUEUE_SUFFIX)

    redis_cli = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=5,
    )

    # FXCM provider
    provider = _build_provider(cfg)
    connected = False

    logging.info(
        "BROKER_SIDECAR_START cmd_key=%s bars_key=%s",
        cmd_key,
        bars_key,
    )

    while _running:
        # Ensure FXCM session
        if not connected:
            try:
                provider.__enter__()
                connected = True
                logging.info("BROKER_SIDECAR_FXCM_CONNECTED")
            except Exception as exc:
                logging.warning(
                    "BROKER_SIDECAR_FXCM_CONNECT_FAIL err=%s cooldown=%ds",
                    exc,
                    _RECONNECT_COOLDOWN_S,
                )
                time.sleep(_RECONNECT_COOLDOWN_S)
                continue

        # Wait for command
        try:
            result = redis_cli.blpop([cmd_key], timeout=_BLPOP_TIMEOUT_S)
        except Exception as exc:
            logging.warning("BROKER_SIDECAR_REDIS_ERROR err=%s", exc)
            time.sleep(2)
            continue

        if result is None:
            continue  # timeout, loop back

        _key, cmd_raw = result  # type: ignore[misc]
        try:
            _handle_command(provider, cmd_raw, redis_cli, bars_key)
        except Exception as exc:
            logging.warning("BROKER_SIDECAR_HANDLE_ERROR err=%s", exc)
            # FXCM session може бути зламана
            connected = False
            try:
                provider.__exit__(None, None, None)
            except Exception:
                pass

    # Cleanup
    logging.info("BROKER_SIDECAR_SHUTDOWN")
    if connected:
        try:
            provider.__exit__(None, None, None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
