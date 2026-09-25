from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any, Callable, List, Optional, Tuple

from core.model.bars import CandleBar, assert_invariants, ms_to_utc_dt, utc_dt_to_ms
from core.session_anchor import (
    H4_S,
    HTF_ANCHOR_RULES,
    OffSeasonGridError,
    assert_on_season_grid,
    htf_anchor_offset_s,
)
from runtime.ingest.market_calendar import MarketCalendar

# ⚠️ Імпорт ForexConnect може відрізнятись залежно від вашого SDK/обгортки.
# Цей варіант відповідає офіційному прикладу forexconnect (fxcorepy + ForexConnect) для python 3.7
try:
    from forexconnect import ForexConnect, fxcorepy  # type: ignore
except Exception:  # noqa: BLE001
    ForexConnect = None  # type: ignore
    fxcorepy = None  # type: ignore

# Ціна відкриття свічки = close попередньої свічки (ADR-0100: контракт паритету з TradingView).
# Вимір власника 21–22.09: TV FX:<символ> показує саме бари FXCM у режимі PREVIOUS_CLOSE — open == close
# попереднього бару і на 15m, і на D1, і через денну перерву 21–22 UTC, і через вихідні (XAU 15m 21.09 22:00
# O 4342.62 H 4350.60 L 4342.62 C 4349.61). Режим передається явно, бо дефолт SDK
# (forexconnect/ForexConnect.py:423) — не контракт: саме залежність від дефолту закрив ADR-0096 §3.1.
OPEN_PRICE_MODE_NAME = "PREVIOUS_CLOSE"
# Допуск ланцюжкової рейки: у PREVIOUS_CLOSE брокер копіює close попередньої свічки в open, тож розбіжність
# може бути лише на представленні float, а не на кроці ціни (кроки символів — config.json
# m1_poller.session_open_rebuild.price_step_by_symbol).
_PREV_CLOSE_CHAIN_REL_TOL = 1e-9

# Тікова історія брокера (ADR-0096 слайс E). Свічки FXCM у нас — Bid: у dtype свічки SDK немає поля «Open»,
# тож extract_ohlc бере BidOpen/BidHigh/BidLow/BidClose (forexconnect/ForexConnect.py:471). Тіки, з яких
# перебудовується свічка, мусять бути тією самою стороною ціни.
TICK_TIMEFRAME = "t1"
TICK_PRICE_FIELD = "Bid"
# quotes_count у SDK: -1 = усі тіки проміжку [date_from, date_to], без ліміту за кількістю.
_ALL_QUOTES_IN_RANGE = -1


def _resolve_open_price_mode() -> Any:
    """Enum режиму PREVIOUS_CLOSE з SDK; без нього — гучна відмова, а не режим за дефолтом SDK."""
    mode = getattr(getattr(fxcorepy, "O2GCandleOpenPriceMode", None), OPEN_PRICE_MODE_NAME, None)
    if mode is None:
        raise RuntimeError(
            "FXCM_OPEN_PRICE_MODE_UNAVAILABLE: у forexconnect немає "
            "fxcorepy.O2GCandleOpenPriceMode.%s — режим свічок звівся б на дефолт SDK, а саме цю "
            "залежність закрито (ADR-0096 §3.1, ADR-0100)"
            % OPEN_PRICE_MODE_NAME
        )
    return mode


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


class FxcmHistoryProvider:
    """History provider поверх ForexConnect.get_history().

    H4/D1 брокера приймаються лише на сезонній сітці символу (ADR-0095 §3.3). `anchor_rule_for_symbol` — резолвер
    `core.config_loader.htf_anchor_rule_resolver(cfg)`; без нього запит H4/D1 — гучна відмова
    `FXCM_HTF_ANCHOR_RULE_MISSING`, а не тихий якір 0. M1..H1 і тіки резолвера не потребують (сайдкар, полер).
    """

    def __init__(
        self,
        user_id: str,
        password: str,
        url: str,
        connection: str,
        anchor_rule_for_symbol: Optional[Callable[[str], str]] = None,
    ) -> None:
        if ForexConnect is None:
            raise RuntimeError(
                "Не вдалося імпортувати forexconnect. Перевірте встановлення SDK/обгортки."
            )
        self._open_price_mode = _resolve_open_price_mode()
        self._user_id = user_id
        self._password = password
        self._url = url
        self._connection = connection
        self._anchor_rule_for_symbol = anchor_rule_for_symbol
        self._fx: Optional[Any] = None
        self._last_error: Optional[Tuple[str, str]] = None

    def _set_last_error(self, context: str, exc: Exception) -> None:
        self._last_error = (context, str(exc))

    def consume_last_error(self) -> Optional[Tuple[str, str]]:
        err = self._last_error
        self._last_error = None
        return err

    def _htf_anchor_rule(self, symbol: str, tf_s: int) -> Optional[str]:
        """Правило сезонної сітки символу для H4/D1; для M1..H1 — None. Відмова — до запиту в SDK."""
        if tf_s < H4_S:
            return None
        if self._anchor_rule_for_symbol is None:
            raise ValueError(
                "FXCM_HTF_ANCHOR_RULE_MISSING symbol=%s tf_s=%d — провайдер без резолвера правила якоря "
                "(anchor_rule_for_symbol), H4/D1 брокера не перевіряються тихим якорем 0 (ADR-0095 §3.3)"
                % (symbol, tf_s)
            )
        try:
            return self._anchor_rule_for_symbol(symbol)
        except ValueError as exc:  # символ без групи або група без виміряної сітки (htf_anchor_rule_resolver)
            raise ValueError(
                "FXCM_HTF_ANCHOR_RULE_MISSING symbol=%s tf_s=%d cause=%s" % (symbol, tf_s, exc)
            ) from exc

    def __enter__(self) -> "FxcmHistoryProvider":
        self._fx = ForexConnect()
        # Не логуємо пароль.
        self._fx.login(self._user_id, self._password, self._url, self._connection)
        logging.info("FXCM_HISTORY_OPEN_MODE mode=%s", OPEN_PRICE_MODE_NAME)
        return self

    def _get_history(
        self,
        symbol: str,
        timeframe: str,
        date_to_utc: Optional[dt.datetime],
        n: int,
        date_from_utc: Optional[dt.datetime] = None,
    ) -> Any:
        """Єдиний виклик SDK за історією (свічки і тіки): режим ціни відкриття передається явно, не дефолтом SDK."""
        return self._fx.get_history(  # type: ignore[union-attr]
            symbol,
            timeframe,
            date_from_utc,
            date_to_utc,
            n,
            candle_open_price_mode=self._open_price_mode,
        )

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._fx is not None:
                self._fx.logout()
        finally:
            self._fx = None

    def is_market_open(
        self, symbol: str, now_ms: int, calendar: MarketCalendar
    ) -> bool:
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

        try:
            arr = self._get_history(symbol, "m1", date_to_utc, n)
        except Exception as e:  # noqa: BLE001
            self._set_last_error(f"помилка запиту {symbol}", e)
            logging.warning("FXCM_HISTORY_ERROR symbol=%s err=%s", symbol, e)
            return []

        # Diagnostic: raw SDK response
        raw_len = 0
        try:
            raw_len = len(arr) if arr is not None else -1
        except Exception:
            raw_len = -2
        if raw_len <= 0:
            logging.warning(
                "FXCM_HISTORY_RAW_EMPTY symbol=%s n=%d date_to=%s raw_len=%d type=%s",
                symbol,
                n,
                date_to_utc,
                raw_len,
                type(arr).__name__,
            )

        return normalize_history_to_bars(
            symbol=symbol,
            tf_s=60,
            history_rows=arr,
            src="history",
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
        anchor_rule = self._htf_anchor_rule(symbol, tf_s)
        try:
            arr = self._get_history(symbol, tf_name, date_to_utc, n)
        except Exception as e:  # noqa: BLE001
            self._set_last_error(f"помилка TF={tf_name} {symbol}", e)
            logging.warning(
                "FXCM_HISTORY_ERROR symbol=%s tf=%s err=%s", symbol, tf_name, e
            )
            return []

        return normalize_history_to_bars(
            symbol=symbol,
            tf_s=tf_s,
            history_rows=arr,
            src="history",
            anchor_rule=anchor_rule,
        )

    def fetch_range_rows(
        self, symbol: str, tf_s: int, date_from_utc: dt.datetime, date_to_utc: dt.datetime
    ) -> List[Tuple[int, float, float, float, float, float]]:
        """Сирі бари брокера [date_from, date_to] — (open_ms, o, h, low, c, v) як їх віддав SDK у PREVIOUS_CLOSE.

        Архів для settle M1 і нативного D1 (ADR-0103 S3): без нормалізації, без перевірки сітки й без ковтання помилки —
        виняток SDK летить до викликача, гейт архіву рахує його за календарем (ADR-0098 §3.8).
        """
        if self._fx is None:
            raise RuntimeError("FXCM сесія не відкрита.")
        if date_from_utc.tzinfo is None or date_to_utc.tzinfo is None:
            raise ValueError("date_from_utc/date_to_utc мають бути UTC tz-aware.")
        arr = self._get_history(
            symbol, tf_s_to_fxcm_timeframe(tf_s), date_to_utc, _ALL_QUOTES_IN_RANGE, date_from_utc
        )
        rows = arr if arr is not None else []
        return [(extract_open_time_ms(r),) + extract_ohlc(r) + (extract_volume(r),) for r in rows]

    def fetch_t1_bid_ticks(
        self, symbol: str, from_ms: int, to_ms: int
    ) -> Optional[List[Tuple[int, float]]]:
        """Тіки брокера (Bid) за проміжок [from_ms, to_ms]: [(tick_ts_ms, bid), ...] у порядку брокера.

        None — запит не вдався (лог + consume_last_error); [] — у проміжку тіків немає. Точне вікно
        хвилини ріже споживач (runtime/ingest/m1_session_open.py), провайдер — лише транспорт.
        """
        if self._fx is None:
            raise RuntimeError("FXCM сесія не відкрита.")
        try:
            rows = self._get_history(
                symbol,
                TICK_TIMEFRAME,
                ms_to_utc_dt(to_ms),
                _ALL_QUOTES_IN_RANGE,
                date_from_utc=ms_to_utc_dt(from_ms),
            )
        except Exception as e:  # noqa: BLE001
            self._set_last_error(f"помилка t1 {symbol}", e)
            logging.warning(
                "FXCM_TICK_HISTORY_ERROR symbol=%s from_ms=%s to_ms=%s err=%s",
                symbol, from_ms, to_ms, e,
            )
            return None
        return normalize_tick_rows(symbol, rows)


def normalize_tick_rows(symbol: str, tick_rows: Any) -> List[Tuple[int, float]]:
    """Рядки t1 з ForexConnect.get_history() (Date, Bid, Ask) → [(tick_ts_ms, bid)].

    Битий рядок (без часу чи Bid) не вгадується: його пропущено, а кількість пропусків — у WARN.
    """
    ticks: List[Tuple[int, float]] = []
    if tick_rows is None:
        return ticks
    seen = 0
    skipped = 0
    for row in tick_rows:
        seen += 1
        try:
            ticks.append((extract_open_time_ms(row), float(row[TICK_PRICE_FIELD])))
        except (KeyError, IndexError, TypeError, ValueError):
            skipped += 1
    if skipped:
        logging.warning(
            "FXCM_TICK_ROWS_SKIPPED symbol=%s skipped=%d of=%d — рядки t1 без часу чи %s",
            symbol, skipped, seen, TICK_PRICE_FIELD,
        )
    return ticks


def normalize_history_to_bars(
    symbol: str,
    tf_s: int,
    history_rows: Any,
    src: str,
    anchor_rule: Optional[str] = None,
) -> List[CandleBar]:
    """Нормалізує rows з ForexConnect.get_history() у CandleBar.

    Очікування:
    - history_rows: numpy.ndarray зі структурованими полями.
    - Дата/час може зватись по-різному. Робимо allowlist ключів.
    - OHLC беремо по пріоритету: Open/High/Low/Close → BidOpen/BidHigh/... → Ask...

    Геометрія бакета: M1..H1 — від епохи; H4/D1 — рівність сезонній сітці правила `anchor_rule` (ADR-0095 §3.3),
    а не членство в наборі якорів. Рядок поза сіткою відкидається й рахується: один агрегований WARN
    `FXCM_HISTORY_OFF_SEASON_GRID` на виклик. H4/D1 без відомого правила — ValueError
    `FXCM_HTF_ANCHOR_RULE_MISSING`, а не тихий якір 0.

    Рейка: розрив ланцюжка `o == prev.c` у батчі — WARN `FXCM_OPEN_NOT_PREV_CLOSE` (див. `open_chain_breaks`);
    значення не змінюються.
    """
    if tf_s >= H4_S and anchor_rule not in HTF_ANCHOR_RULES:
        raise ValueError(
            "FXCM_HTF_ANCHOR_RULE_MISSING symbol=%s tf_s=%d anchor_rule=%r — H4/D1 брокера без правила сезонної "
            "сітки (ADR-0095 §3.3)" % (symbol, tf_s, anchor_rule)
        )
    out: List[CandleBar] = []
    if history_rows is None:
        return out

    # Перетворюємо на ітерований список рядків.
    try:
        rows = list(history_rows)
    except Exception:
        logging.debug(
            "FXCM_HISTORY_ROWS_COERCE_FAILED tf_s=%s src=%s", tf_s, src, exc_info=True
        )
        rows = []

    htf_rule = anchor_rule if tf_s >= H4_S else None  # M1..H1 — сітка від епохи, правило не діє
    off_grid: List[OffSeasonGridError] = []
    for r in rows:
        try:
            open_ms = extract_open_time_ms(r)
            close_ms = open_ms + tf_s * 1000
            anchor_offset_s = 0
            if htf_rule is not None:
                try:
                    assert_on_season_grid(open_ms, tf_s, htf_rule)
                except OffSeasonGridError as exc:
                    off_grid.append(exc)
                    continue
                anchor_offset_s = htf_anchor_offset_s(tf_s, open_ms, htf_rule)

            o, h, low, c = extract_ohlc(r)
            # Нормалізація OHLC: у PREVIOUS_CLOSE open (= close попередньої свічки) законно лежить поза
            # [low, high] на гепі — H/L розтягуються до нього, і саме так бар показує TV (XAU 21.09 22:00:
            # L == O == 4342.62). Плюс брокер може повернути h < close (bid/ask артефакт).
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
            assert_invariants(b, anchor_offset_s=anchor_offset_s)
            out.append(b)
        except Exception as e:
            logging.warning("Пропуск history-row: %s", str(e))

    if off_grid:
        off_grid.sort(key=lambda exc: exc.open_ms)
        logging.warning(
            "FXCM_HISTORY_OFF_SEASON_GRID symbol=%s tf_s=%s dropped=%d of=%d last_open_ms=%d first: %s — бари "
            "брокера поза сезонною сіткою відкинуто, споживач їх не отримає (ADR-0095 §3.3)",
            symbol, tf_s, len(off_grid), len(rows), off_grid[-1].open_ms, off_grid[0],
        )

    out.sort(key=lambda x: x.open_time_ms)
    chain_breaks = open_chain_breaks(out)
    if chain_breaks:
        logging.warning(
            "FXCM_OPEN_NOT_PREV_CLOSE symbol=%s tf_s=%s bars=%d of=%d first_open_ms=%s last_open_ms=%s "
            "— open ≠ close попереднього бару: ланцюжок PREVIOUS_CLOSE розірвано, свічка не 1:1 з TV (ADR-0100)",
            symbol, tf_s, len(chain_breaks), len(out), min(chain_breaks), max(chain_breaks),
        )
    return out


def open_chain_breaks(bars: List[CandleBar]) -> List[int]:
    """`open_time_ms` барів, чий open ≠ close попереднього бару послідовності (розрив ланцюжка PREVIOUS_CLOSE).

    Це і є вимір паритету з TV: у PREVIOUS_CLOSE брокер копіює close попередньої свічки в open, тож на 1:1-даних
    список майже порожній. Вимір на копії проду 22.09: доба PREV-епохи 14.09 — XAU M1 2 розриви з 1378 пар,
    SPX500 1 з 1379 (обидва класи відомі: округлений бар 20:59 ADR-0098 і `session_open_rebuilt` ремонту -010);
    доба FIRST_TICK-епохи 18.09 — 1244/1244 і 1243/1243, тобто кожен бар.
    Перший бар послідовності не перевіряється — його попередник лишився за межею запиту. Бари мусять бути
    відсортовані за часом.
    """
    return [
        bar.open_time_ms
        for prev, bar in zip(bars, bars[1:])
        if not math.isclose(bar.o, prev.c, rel_tol=_PREV_CLOSE_CHAIN_REL_TOL, abs_tol=0.0)
    ]


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
            logging.debug("FXCM_OPEN_TIME_KEY_MISS key=%s", k, exc_info=True)
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
                # Переводимо в ms (numpy datetime64 без tz; трактуємо як UTC). Цілочисельне ділення: float
                # губить 1 мс на часі тіку з мілісекундами (M8[ns] ~1.8e18 > 2^53) — тік 06:01:00.000 став би 06:00:59.999.
                epoch = np.datetime64("1970-01-01T00:00:00")
                return int((val - epoch) // np.timedelta64(1, "ms"))
        except Exception:
            logging.debug("FXCM_NUMPY_DT64_PARSE_FAIL val=%r", val, exc_info=True)

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
                logging.debug(
                    "FXCM_OPEN_TIME_ISO_PARSE_FAILED raw=%r", val, exc_info=True
                )
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
            logging.debug(
                "FXCM_OHLC_EXTRACT_FAILED keys=%s/%s/%s/%s",
                a,
                b,
                c,
                d,
                exc_info=True,
            )
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
            logging.debug("FXCM_VOLUME_EXTRACT_FAILED key=%s", k, exc_info=True)
            continue
    return 0.0
