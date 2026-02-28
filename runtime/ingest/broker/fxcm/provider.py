from __future__ import annotations

import datetime as dt
import logging
from typing import Any, List, Optional, Tuple

from core.model.bars import CandleBar, assert_invariants, utc_dt_to_ms
from runtime.ingest.market_calendar import MarketCalendar

# ⚠️ Імпорт ForexConnect може відрізнятись залежно від вашого SDK/обгортки.
# Цей варіант відповідає офіційному прикладу forexconnect (fxcorepy + ForexConnect) для python 3.7
try:
    from forexconnect import ForexConnect, fxcorepy  # type: ignore
except Exception:  # noqa: BLE001
    ForexConnect = None  # type: ignore
    fxcorepy = None  # type: ignore


def tf_s_to_fxcm_timeframe(tf_s: int) -> str:
    mapping = {
        60: "m1",
        180: "m3",
        300: "m5",
        900: "m15",
        1800: "m30",
        3600: "H1",
        14400: "H4",
        86400: "D1",
    }
    if tf_s not in mapping:
        raise ValueError(f"unsupported_tf_s_for_fxcm={tf_s}")
    return mapping[tf_s]


def anchor_offset_for_tf(
    tf_s: int,
    day_anchor_offset_s: int,
    day_anchor_offset_s_d1: Optional[int] = None,
) -> int:
    """Anchor-offset для старших TF (FX-сесія)."""
    if tf_s == 86400 and day_anchor_offset_s_d1 is not None:
        return max(0, int(day_anchor_offset_s_d1))
    if day_anchor_offset_s <= 0:
        return 0
    if tf_s >= 14400:
        return day_anchor_offset_s
    return 0


class FxcmHistoryProvider:
    """History provider поверх ForexConnect.get_history()."""

    def __init__(
        self,
        user_id: str,
        password: str,
        url: str,
        connection: str,
        day_anchor_offset_s: int = 0,
        day_anchor_offset_s_d1: Optional[int] = None,
        day_anchor_offset_s_d1_alt: Optional[int] = None,
        day_anchor_offset_s_alt: Optional[int] = None,
        day_anchor_offset_s_alt2: Optional[int] = None,
    ) -> None:
        if ForexConnect is None:
            raise RuntimeError(
                "Не вдалося імпортувати forexconnect. Перевірте встановлення SDK/обгортки."
            )
        self._user_id = user_id
        self._password = password
        self._url = url
        self._connection = connection
        self._day_anchor_offset_s = day_anchor_offset_s
        self._day_anchor_offset_s_d1 = day_anchor_offset_s_d1
        self._day_anchor_offset_s_d1_alt = day_anchor_offset_s_d1_alt
        self._day_anchor_offset_s_alt = day_anchor_offset_s_alt
        self._day_anchor_offset_s_alt2 = day_anchor_offset_s_alt2
        self._fx: Optional[Any] = None
        self._last_error: Optional[Tuple[str, str]] = None

    def _set_last_error(self, context: str, exc: Exception) -> None:
        self._last_error = (context, str(exc))

    def consume_last_error(self) -> Optional[Tuple[str, str]]:
        err = self._last_error
        self._last_error = None
        return err

    def _anchor_offset_for_tf(self, tf_s: int) -> int:
        return anchor_offset_for_tf(
            tf_s,
            self._day_anchor_offset_s,
            self._day_anchor_offset_s_d1,
        )

    def _anchor_offset_alts_for_tf(self, tf_s: int) -> List[int]:
        out: List[int] = []
        if tf_s >= 14400 and tf_s != 86400:
            for v in (self._day_anchor_offset_s_alt, self._day_anchor_offset_s_alt2):
                if v is not None:
                    out.append(int(v))
        if tf_s == 86400 and self._day_anchor_offset_s_d1_alt is not None:
            out.append(int(self._day_anchor_offset_s_d1_alt))
        return out

    def __enter__(self) -> "FxcmHistoryProvider":
        self._fx = ForexConnect()
        # Не логуємо пароль.
        self._fx.login(self._user_id, self._password, self._url, self._connection)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._fx is not None:
                self._fx.logout()
        finally:
            self._fx = None

    def is_market_open(self, symbol: str, now_ms: int, calendar: MarketCalendar) -> bool:
        _ = symbol
        return calendar.is_trading_minute(now_ms)

    def fetch_last_n_m1(
        self, symbol: str, n: int, date_to_utc: Optional[dt.datetime] = None
    ) -> List[CandleBar]:
        """Отримує n барів m1, що закінчуються на date_to (або 'now')."""
        if self._fx is None:
            raise RuntimeError("FXCM сесія не відкрита.")
        if date_to_utc is not None and date_to_utc.tzinfo is None:
            raise ValueError("date_to_utc має бути UTC tz-aware.")

        # timeframe = "m1" (стандартний ідентифікатор) :contentReference[oaicite:2]{index=2}
        # FIRST_TICK mode (default): кожен бар має своє реальне BID open.
        # PREVIOUS_CLOSE від FXCM створює артефакт: парне чергування volume (high/low)
        # та непослідовне stitching. UI stitching (open[i]=close[i-1]) робиться окремо.
        try:
            arr = self._fx.get_history(
                symbol,
                "m1",
                None,
                date_to_utc,
                n,
            )
        except Exception as e:  # noqa: BLE001
            self._set_last_error(f"помилка запиту {symbol}", e)
            logging.debug("History: помилка запиту %s: %s", symbol, str(e))
            return []

        return normalize_history_to_bars(
            symbol=symbol,
            tf_s=60,
            history_rows=arr,
            src="history",
            anchor_offset_s=self._anchor_offset_for_tf(60),
            anchor_offset_s_alts=self._anchor_offset_alts_for_tf(60),
        )

    def fetch_last_n_tf(
        self,
        symbol: str,
        tf_s: int,
        n: int,
        date_to_utc: Optional[dt.datetime] = None,
    ) -> List[CandleBar]:
        """Отримує n барів довільного TF, що закінчуються на date_to (або 'now')."""
        if self._fx is None:
            raise RuntimeError("FXCM сесія не відкрита.")
        if date_to_utc is not None and date_to_utc.tzinfo is None:
            raise ValueError("date_to_utc має бути UTC tz-aware.")

        tf_name = tf_s_to_fxcm_timeframe(tf_s)
        # FIRST_TICK mode — без PREVIOUS_CLOSE (артефакт volume alternation).
        try:
            arr = self._fx.get_history(
                symbol,
                tf_name,
                None,
                date_to_utc,
                n,
            )
        except Exception as e:  # noqa: BLE001
            self._set_last_error(f"помилка TF={tf_name} {symbol}", e)
            logging.debug("History: помилка TF=%s %s: %s", tf_name, symbol, str(e))
            return []

        return normalize_history_to_bars(
            symbol=symbol,
            tf_s=tf_s,
            history_rows=arr,
            src="history",
            anchor_offset_s=self._anchor_offset_for_tf(tf_s),
            anchor_offset_s_alts=self._anchor_offset_alts_for_tf(tf_s),
        )


def normalize_history_to_bars(
    symbol: str,
    tf_s: int,
    history_rows: Any,
    src: str,
    anchor_offset_s: int = 0,
    anchor_offset_s_alts: Optional[List[int]] = None,
) -> List[CandleBar]:
    """Нормалізує rows з ForexConnect.get_history() у CandleBar.

    Очікування:
    - history_rows: numpy.ndarray зі структурованими полями.
    - Дата/час може зватись по-різному. Робимо allowlist ключів.
    - OHLC беремо по пріоритету: Open/High/Low/Close → BidOpen/BidHigh/... → Ask...
    """
    out: List[CandleBar] = []
    if history_rows is None:
        return out

    # Перетворюємо на ітерований список рядків.
    try:
        rows = list(history_rows)
    except Exception:
        rows = []

    for r in rows:
        try:
            open_ms = extract_open_time_ms(r)
            close_ms = open_ms + tf_s * 1000

            o, h, low, c = extract_ohlc(r)
            # Нормалізація OHLC: broker може повернути h < close (bid/ask артефакт)
            h = max(o, h, low, c)
            low = min(o, h, low, c)
            v = extract_volume(r)
            if v < 0.0:
                v = 0.0

            b = CandleBar(
                symbol=symbol,
                tf_s=tf_s,
                open_time_ms=open_ms,
                close_time_ms=close_ms,
                o=o,
                h=h,
                low=low,
                c=c,
                v=v,
                complete=True,
                src=src,
            )
            try:
                assert_invariants(b, anchor_offset_s=anchor_offset_s)
            except ValueError as e:
                if anchor_offset_s_alts:
                    ok = False
                    for alt in anchor_offset_s_alts:
                        try:
                            assert_invariants(b, anchor_offset_s=alt)
                            ok = True
                            break
                        except ValueError:
                            continue
                    if not ok:
                        raise e
                else:
                    raise
            out.append(b)
        except Exception as e:
            logging.warning("Пропуск history-row: %s", str(e))

    out.sort(key=lambda x: x.open_time_ms)
    return out


def extract_open_time_ms(row: Any) -> int:
    """Витягує open_time_ms з history row.

    Підтримує datetime або epoch-значення.
    """
    # allowlist можливих назв поля часу
    keys = [
        "Date",
        "date",
        "datetime",
        "DateTime",
        "time",
        "Time",
        "timestamp",
        "Timestamp",
    ]
    for k in keys:
        try:
            val = row[k]  # numpy.void / dict-подібні
        except Exception:
            continue

        if val is None:
            continue

        # datetime
        if isinstance(val, dt.datetime):
            if val.tzinfo is None:
                # ForexConnect декларує UTC; якщо tz не заданий — трактуємо як UTC, але loud.
                logging.warning("row datetime без tzinfo; трактую як UTC.")
                val = val.replace(tzinfo=dt.timezone.utc)
            return utc_dt_to_ms(val.astimezone(dt.timezone.utc))

        # numpy datetime64
        try:
            import numpy as np  # type: ignore

            if isinstance(val, np.datetime64):
                # Переводимо в ms (numpy datetime64 без tz; трактуємо як UTC)
                epoch = np.datetime64("1970-01-01T00:00:00")
                ts = (val - epoch) / np.timedelta64(1, "ms")
                return int(ts)
        except Exception:
            pass

        # epoch seconds/ms
        if isinstance(val, (int, float)):
            # евристика: якщо дуже велике — вже ms
            if val > 10_000_000_000:
                return int(val)
            return int(val * 1000)

        # рядок дати
        if isinstance(val, str):
            # мінімальна підтримка ISO
            try:
                d = dt.datetime.fromisoformat(val.replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=dt.timezone.utc)
                return utc_dt_to_ms(d.astimezone(dt.timezone.utc))
            except Exception:
                continue

    raise ValueError("history_row_missing_datetime")


def extract_ohlc(row: Any) -> Tuple[float, float, float, float]:
    """Витягує OHLC з рядка (пріоритет Open/High/Low/Close, потім Bid*, потім Ask*)."""
    candidates = [
        ("Open", "High", "Low", "Close"),
        ("open", "high", "low", "close"),
        ("BidOpen", "BidHigh", "BidLow", "BidClose"),
        ("AskOpen", "AskHigh", "AskLow", "AskClose"),
    ]
    for a, b, c, d in candidates:
        try:
            o = float(row[a])
            h = float(row[b])
            low = float(row[c])
            cl = float(row[d])
            return o, h, low, cl
        except Exception:
            continue
    raise ValueError("history_row_missing_ohlc")


def extract_volume(row: Any) -> float:
    """Витягує volume (якщо відсутній — 0.0, але loud один раз/часто не робимо)."""
    for k in ["Volume", "volume", "TickVolume", "tick_volume", "V", "v"]:
        try:
            val = row[k]
            if val is None:
                continue
            return float(val)
        except Exception:
            continue
    return 0.0
