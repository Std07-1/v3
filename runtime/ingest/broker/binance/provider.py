"""BinanceHistoryProvider — Binance Futures M1 klines fetcher (ADR-0037).

Drop-in аналог FxcmHistoryProvider для M1PollerRunner:
- REST GET /fapi/v1/klines → List[CandleBar]
- Authenticated (HMAC-SHA256 через python-binance Client)
- Backoff retry (3 спроби)
- Crypto 24/7 — calendar не потрібен

Binance kline row: [open_time, o, h, l, c, v, close_time, ...]
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, List, Optional, Tuple

from core.model.bars import CandleBar

try:
    from binance.client import Client as BinanceClient  # type: ignore
except ImportError:
    BinanceClient = None  # type: ignore

logger = logging.getLogger("binance_provider")

_M1_MS = 60_000
_MAX_RETRIES = 3
_BACKOFF_S = 2.0
_MAX_KLINES_PER_REQUEST = 1500  # Binance API limit


class BinanceHistoryProvider:
    """Binance Futures M1 history provider.

    Інтерфейс аналогічний FxcmHistoryProvider:
    - __enter__ / __exit__ для context manager
    - fetch_last_n_m1(symbol, n, date_to_utc) → List[CandleBar]
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        if BinanceClient is None:
            raise RuntimeError(
                "python-binance не встановлений. pip install python-binance"
            )
        self._api_key = api_key
        self._api_secret = api_secret
        self._client: Optional[Any] = None
        self._last_error: Optional[Tuple[str, str]] = None

    @property
    def _fx(self) -> Optional[Any]:
        """Compat з M1PollerRunner._try_connect() — сигнал active session."""
        return self._client

    def __enter__(self) -> BinanceHistoryProvider:
        self._client = BinanceClient(self._api_key, self._api_secret)
        logger.info("BINANCE_PROVIDER_CONNECTED")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._client = None

    def consume_last_error(self) -> Optional[Tuple[str, str]]:
        err = self._last_error
        self._last_error = None
        return err

    def fetch_last_n_m1(
        self,
        symbol: str,
        n: int,
        date_to_utc: Optional[dt.datetime] = None,
    ) -> List[CandleBar]:
        """Отримує n фінальних M1 барів від Binance Futures.

        Args:
            symbol: Binance symbol (e.g. "BTCUSDT")
            n: кількість барів
            date_to_utc: верхня межа (inclusive). None = now.

        Returns:
            List[CandleBar] sorted by open_time_ms ascending.
        """
        if self._client is None:
            raise RuntimeError("Binance client not initialized. Use context manager.")

        limit = min(n, _MAX_KLINES_PER_REQUEST)

        kwargs: dict[str, Any] = {
            "symbol": symbol,
            "interval": BinanceClient.KLINE_INTERVAL_1MINUTE,
            "limit": limit,
        }
        if date_to_utc is not None:
            kwargs["endTime"] = int(date_to_utc.timestamp() * 1000)

        raw = self._fetch_with_retry(kwargs)
        if raw is None:
            return []

        return self._parse_klines(symbol, raw)

    def _fetch_with_retry(self, kwargs: dict[str, Any]) -> Optional[List[list]]:
        """REST GET з backoff retry."""
        client = self._client
        assert client is not None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return client.futures_klines(**kwargs)
            except Exception as exc:
                self._last_error = (
                    f"binance_klines attempt={attempt}",
                    str(exc),
                )
                logger.warning(
                    "BINANCE_KLINES_RETRY symbol=%s attempt=%d/%d err=%s",
                    kwargs.get("symbol"),
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_S * attempt)
        logger.error(
            "BINANCE_KLINES_EXHAUSTED symbol=%s retries=%d",
            kwargs.get("symbol"),
            _MAX_RETRIES,
        )
        return None

    @staticmethod
    def _parse_klines(symbol: str, raw: List[list]) -> List[CandleBar]:
        """Binance kline row → CandleBar.

        Row format: [open_time, o, h, l, c, v, close_time, ...]
        """
        bars: List[CandleBar] = []
        for row in raw:
            try:
                open_time_ms = int(row[0])
                o = float(row[1])
                h = float(row[2])
                low = float(row[3])
                c = float(row[4])
                v = float(row[5])

                bars.append(
                    CandleBar(
                        symbol=symbol,
                        tf_s=60,
                        open_time_ms=open_time_ms,
                        close_time_ms=open_time_ms + _M1_MS,
                        o=o,
                        h=h,
                        low=low,
                        c=c,
                        v=v,
                        complete=True,
                        src="history",
                        extensions={"broker": "binance"},
                    )
                )
            except (IndexError, ValueError, TypeError) as exc:
                logger.warning(
                    "BINANCE_KLINE_PARSE_ERROR symbol=%s row=%s err=%s",
                    symbol,
                    str(row)[:120],
                    exc,
                )
        return bars
