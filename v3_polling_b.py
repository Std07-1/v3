from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# ⚠️ Імпорт ForexConnect може відрізнятись залежно від вашого SDK/обгортки.
# Цей варіант відповідає офіційному прикладу forexconnect (fxcorepy + ForexConnect) для python 3.7
try:
    from forexconnect import ForexConnect, fxcorepy  # type: ignore
except Exception:  # noqa: BLE001
    ForexConnect = None  # type: ignore
    fxcorepy = None  # type: ignore

# Конфіг

def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

# Канонічний контракт бару

@dataclasses.dataclass(frozen=True)
class CandleBar:
    """Канонічний бар (один формат для SSOT і derived).

    - Час строго UTC.
    - open_time_ms = початок bucket.
    - close_time_ms = open_time_ms + tf_s*1000.
    - complete=true лише для закритих барів (у версії B — всі збережені 1m/derived є complete).
    """

    symbol: str
    tf_s: int
    open_time_ms: int
    close_time_ms: int
    o: float
    h: float
    low: float
    c: float
    v: float
    complete: bool
    src: str  # "history" | "derived"

    def key(self) -> Tuple[str, int, int]:
        return (self.symbol, self.tf_s, self.open_time_ms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "tf_s": self.tf_s,
            "open_time_ms": self.open_time_ms,
            "close_time_ms": self.close_time_ms,
            "o": self.o,
            "h": self.h,
            "low": self.low,
            "c": self.c,
            "v": self.v,
            "complete": self.complete,
            "src": self.src,
        }


# Утиліти часу/бакетів

def utc_now_ms() -> int:
    return int(time.time() * 1000)


def ms_to_utc_dt(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc)


def utc_dt_to_ms(d: dt.datetime) -> int:
    if d.tzinfo is None:
        raise ValueError("Очікується datetime з tz=UTC.")
    return int(d.timestamp() * 1000)


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


def floor_bucket_start_ms(ts_ms: int, tf_s: int, anchor_offset_s: int = 0) -> int:
    """Початок bucket для tf_s з опційним anchor_offset_s (для D1 зазвичай)."""
    tf_ms = tf_s * 1000
    adj = ts_ms - anchor_offset_s * 1000
    b0 = (adj // tf_ms) * tf_ms
    return b0 + anchor_offset_s * 1000


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


def _d1_anchor_offsets(
    day_anchor_offset_s: int,
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
) -> Tuple[int, Optional[int]]:
    primary = day_anchor_offset_s_d1 if day_anchor_offset_s_d1 is not None else day_anchor_offset_s
    alt = day_anchor_offset_s_d1_alt
    if alt is not None and alt == primary:
        alt = None
    return primary, alt


def _h4_anchor_offsets(
    day_anchor_offset_s: int,
    day_anchor_offset_s_alt: Optional[int],
    day_anchor_offset_s_alt2: Optional[int],
) -> Tuple[int, Optional[int]]:
    primary = day_anchor_offset_s
    alt = day_anchor_offset_s_alt
    if alt is not None and alt == primary:
        alt = None
    alt2 = day_anchor_offset_s_alt2
    if alt2 is not None and alt2 in (primary, alt):
        alt2 = None
    return primary, alt, alt2


def select_anchor_offset_for_open_ms(
    tf_s: int,
    open_time_ms: int,
    day_anchor_offset_s: int,
    day_anchor_offset_s_alt: Optional[int],
    day_anchor_offset_s_alt2: Optional[int],
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
) -> int:
    if tf_s != 86400:
        tf_ms = tf_s * 1000
        primary, alt, alt2 = _h4_anchor_offsets(
            day_anchor_offset_s,
            day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2,
        )
        for off in (primary, alt, alt2):
            if off is None:
                continue
            if (open_time_ms - off * 1000) % tf_ms == 0:
                return off
        return primary
    tf_ms = tf_s * 1000
    primary, alt = _d1_anchor_offsets(
        day_anchor_offset_s,
        day_anchor_offset_s_d1,
        day_anchor_offset_s_d1_alt,
    )
    for off in (primary, alt):
        if off is None:
            continue
        if (open_time_ms - off * 1000) % tf_ms == 0:
            return off
    return primary


def select_anchor_offset_for_anchor_open_ms(
    tf_s: int,
    anchor_open_ms: int,
    day_anchor_offset_s: int,
    day_anchor_offset_s_alt: Optional[int],
    day_anchor_offset_s_alt2: Optional[int],
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
) -> int:
    if tf_s != 86400:
        tf_ms = tf_s * 1000
        primary, alt, alt2 = _h4_anchor_offsets(
            day_anchor_offset_s,
            day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2,
        )
        for off in (primary, alt, alt2):
            if off is None:
                continue
            b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=off)
            b1 = b0 + tf_ms
            if anchor_open_ms == (b1 - 60_000):
                return off
        return primary
    tf_ms = tf_s * 1000
    primary, alt = _d1_anchor_offsets(
        day_anchor_offset_s,
        day_anchor_offset_s_d1,
        day_anchor_offset_s_d1_alt,
    )
    for off in (primary, alt):
        if off is None:
            continue
        b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=off)
        b1 = b0 + tf_ms
        if anchor_open_ms == (b1 - 60_000):
            return off
    return primary


def expected_last_closed_m1_open_ms(now_ms: int) -> int:
    """Очікуваний open_time для останньої ЗАКРИТОЇ 1m свічки."""
    # bucket boundary на хвилині: ..., 12:34:00.000
    # остання закрита — попередня хвилина
    minute = 60_000
    b = (now_ms // minute) * minute
    return b - minute


def _parse_hm(hm: str) -> Optional[Tuple[int, int]]:
    if not hm:
        return None
    try:
        h, m = hm.split(":", 1)
        return int(h), int(m)
    except Exception:
        return None


def sleep_to_next_minute(safety_delay_s: int) -> None:
    now = time.time()
    next_min = (int(now // 60) + 1) * 60
    target = next_min + safety_delay_s
    delay = max(0.0, target - now)
    time.sleep(delay)


def assert_invariants(b: CandleBar, anchor_offset_s: int = 0) -> None:
    tf_ms = b.tf_s * 1000
    if (b.open_time_ms - anchor_offset_s * 1000) % tf_ms != 0:
        raise ValueError(
            f"bar_bucket_misaligned tf_s={b.tf_s} open_time_ms={b.open_time_ms}"
        )
    if b.close_time_ms != b.open_time_ms + tf_ms:
        raise ValueError(
            f"bar_close_time_invalid tf_s={b.tf_s} open_time_ms={b.open_time_ms}"
        )
    if b.tf_s == 60 and b.src == "derived":
        raise ValueError("derived_1m_forbidden")


# JSONL append-only сховище

class JsonlAppender:
    """Append-only JSONL writer із ротацією по даті open_time_utc (YYYYMMDD)."""

    def __init__(
        self,
        root: str,
        day_anchor_offset_s: int = 0,
        day_anchor_offset_s_d1: Optional[int] = None,
        day_anchor_offset_s_d1_alt: Optional[int] = None,
        day_anchor_offset_s_alt: Optional[int] = None,
        day_anchor_offset_s_alt2: Optional[int] = None,
    ) -> None:
        self._root = root
        self._open_files: Dict[str, Any] = {}
        self._day_anchor_offset_s = day_anchor_offset_s
        self._day_anchor_offset_s_d1 = day_anchor_offset_s_d1
        self._day_anchor_offset_s_d1_alt = day_anchor_offset_s_d1_alt
        self._day_anchor_offset_s_alt = day_anchor_offset_s_alt
        self._day_anchor_offset_s_alt2 = day_anchor_offset_s_alt2

    def _path_for(self, symbol: str, tf_s: int, open_time_ms: int) -> str:
        day = ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")
        sym_dir = symbol.replace("/", "_")
        tf_dir = f"tf_{tf_s}"
        out_dir = os.path.join(self._root, sym_dir, tf_dir)
        os.makedirs(out_dir, exist_ok=True)
        return os.path.join(out_dir, f"part-{day}.jsonl")

    def append(self, bar: CandleBar) -> None:
        anchor_offset_s = select_anchor_offset_for_open_ms(
            bar.tf_s,
            bar.open_time_ms,
            self._day_anchor_offset_s,
            self._day_anchor_offset_s_alt,
            self._day_anchor_offset_s_alt2,
            self._day_anchor_offset_s_d1,
            self._day_anchor_offset_s_d1_alt,
        )
        assert_invariants(bar, anchor_offset_s=anchor_offset_s)
        path = self._path_for(bar.symbol, bar.tf_s, bar.open_time_ms)
        fh = self._open_files.get(path)
        if fh is None:
            fh = open(path, "a", encoding="utf-8")
            self._open_files[path] = fh
        line = json.dumps(bar.to_dict(), ensure_ascii=False, separators=(",", ":"))
        fh.write(line + "\n")
        fh.flush()

    def close(self) -> None:
        for fh in self._open_files.values():
            try:
                fh.close()
            except Exception:
                pass
        self._open_files.clear()


def tail_last_bar_time_ms(data_root: str, symbol: str, tf_s: int) -> Optional[int]:
    """Знаходить останній open_time_ms для (symbol,tf_s) через tail JSONL файлів.

    Мінімізує читання: беремо найновіший part-*.jsonl і читаємо його з кінця.
    """
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{tf_s}"
    dir_path = os.path.join(data_root, sym_dir, tf_dir)
    if not os.path.isdir(dir_path):
        return None

    parts = [
        p
        for p in os.listdir(dir_path)
        if p.startswith("part-") and p.endswith(".jsonl")
    ]
    if not parts:
        return None
    parts.sort()  # YYYYMMDD => лексикографічно ок
    latest = os.path.join(dir_path, parts[-1])

    # Читаємо "хвіст" (останній валідний JSON рядок).
    try:
        with open(latest, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = 8192
            buf = b""
            pos = size
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                if b"\n" in buf:
                    break
            lines = buf.splitlines()
            for raw in reversed(lines):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8"))
                    return int(obj["open_time_ms"])
                except Exception:
                    continue
    except Exception:
        return None

    return None


def head_first_bar_time_ms(data_root: str, symbol: str, tf_s: int) -> Optional[int]:
    """Знаходить найперший open_time_ms для (symbol,tf_s) через head JSONL файлів."""
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{tf_s}"
    dir_path = os.path.join(data_root, sym_dir, tf_dir)
    if not os.path.isdir(dir_path):
        return None

    parts = [
        p
        for p in os.listdir(dir_path)
        if p.startswith("part-") and p.endswith(".jsonl")
    ]
    if not parts:
        return None
    parts.sort()
    earliest = os.path.join(dir_path, parts[0])

    try:
        with open(earliest, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    return int(obj["open_time_ms"])
                except Exception:
                    continue
    except Exception:
        return None

    return None


def iter_day_keys_utc(start_ms: int, end_ms: int) -> List[str]:
    """Повертає список YYYYMMDD між start_ms та end_ms (UTC, включно)."""
    if end_ms < start_ms:
        return []
    start_day = ms_to_utc_dt(start_ms).date()
    end_day = ms_to_utc_dt(end_ms).date()
    out: List[str] = []
    cur = start_day
    while cur <= end_day:
        out.append(cur.strftime("%Y%m%d"))
        cur += dt.timedelta(days=1)
    return out


def load_day_open_times(
    data_root: str, symbol: str, tf_s: int, day: str
) -> set[int]:
    """Завантажує open_time_ms з part-YYYYMMDD.jsonl для (symbol, tf_s)."""
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{tf_s}"
    path = os.path.join(data_root, sym_dir, tf_dir, f"part-{day}.jsonl")
    out: set[int] = set()
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    out.add(int(obj["open_time_ms"]))
                except Exception:
                    continue
    except Exception:
        return out
    return out


# FXCM history provider (ForexConnect)

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

    def fetch_last_n_m1(
        self, symbol: str, n: int, date_to_utc: Optional[dt.datetime] = None
    ) -> List[CandleBar]:
        """Отримує n барів m1, що закінчуються на date_to (або 'now')."""
        if self._fx is None:
            raise RuntimeError("FXCM сесія не відкрита.")
        if date_to_utc is not None and date_to_utc.tzinfo is None:
            raise ValueError("date_to_utc має бути UTC tz-aware.")

        # timeframe = "m1" (стандартний ідентифікатор) :contentReference[oaicite:2]{index=2}
        try:
            arr = self._fx.get_history(
                symbol,
                "m1",
                None,
                date_to_utc,
                n,
                candle_open_price_mode=getattr(
                    fxcorepy.O2GCandleOpenPriceMode, "PREVIOUS_CLOSE", None
                ),
            )
        except Exception as e:  # noqa: BLE001
            logging.warning("History: помилка запиту %s: %s", symbol, str(e))
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
        try:
            arr = self._fx.get_history(
                symbol,
                tf_name,
                None,
                date_to_utc,
                n,
                candle_open_price_mode=getattr(
                    fxcorepy.O2GCandleOpenPriceMode, "PREVIOUS_CLOSE", None
                ),
            )
        except Exception as e:  # noqa: BLE001
            logging.warning("History: помилка TF=%s %s: %s", tf_name, symbol, str(e))
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
            v = extract_volume(r)

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


# In-memory буфер 1m + derived

class M1Buffer:
    """Буфер закритих 1m барів у памʼяті для побудови derived TF.

    Зберігаємо останні max_keep барів (за замовчуванням вистачає на 2-4 дні).
    """

    def __init__(self, max_keep: int = 6000) -> None:
        self._max_keep = max_keep
        self._by_open_ms: Dict[int, CandleBar] = {}
        self._sorted_keys: List[int] = []

    def upsert(self, bar: CandleBar) -> None:
        if bar.tf_s != 60:
            raise ValueError("M1Buffer приймає тільки tf_s=60")
        k = bar.open_time_ms
        if k in self._by_open_ms:
            self._by_open_ms[k] = bar
            return
        self._by_open_ms[k] = bar
        self._sorted_keys.append(k)
        self._sorted_keys.sort()
        self._gc()

    def _gc(self) -> None:
        if len(self._sorted_keys) <= self._max_keep:
            return
        drop = len(self._sorted_keys) - self._max_keep
        to_drop = self._sorted_keys[:drop]
        self._sorted_keys = self._sorted_keys[drop:]
        for k in to_drop:
            self._by_open_ms.pop(k, None)

    def has_range_complete(self, start_ms: int, end_ms: int) -> bool:
        """Перевіряє, що є всі 1m бари на [start_ms, end_ms) без пропусків."""
        step = 60_000
        for t in range(start_ms, end_ms, step):
            if t not in self._by_open_ms:
                return False
        return True

    def range_bars(self, start_ms: int, end_ms: int) -> List[CandleBar]:
        step = 60_000
        out: List[CandleBar] = []
        for t in range(start_ms, end_ms, step):
            b = self._by_open_ms.get(t)
            if b is None:
                return []
            out.append(b)
        return out

    def missing_count(self, start_ms: int, end_ms: int) -> int:
        step = 60_000
        missing = 0
        for t in range(start_ms, end_ms, step):
            if t not in self._by_open_ms:
                missing += 1
        return missing

    def earliest_open_ms(self) -> Optional[int]:
        if not self._sorted_keys:
            return None
        return self._sorted_keys[0]

    def latest_open_ms(self) -> Optional[int]:
        if not self._sorted_keys:
            return None
        return self._sorted_keys[-1]


def derive_from_m1_for_anchor(
    symbol: str,
    tf_s: int,
    m1: M1Buffer,
    anchor_open_ms: int,
    anchor_offset_s: int = 0,
) -> Optional[CandleBar]:
    tf_ms = tf_s * 1000

    b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=anchor_offset_s)
    b1 = b0 + tf_ms

    # Емітимо derived рівно на останній хвилині бакету
    if anchor_open_ms != (b1 - 60_000):
        return None

    if not m1.has_range_complete(b0, b1):
        return None

    bars = m1.range_bars(b0, b1)
    if not bars:
        return None

    o = bars[0].o
    c = bars[-1].c
    h = max(x.h for x in bars)
    low = min(x.low for x in bars)
    v = sum(x.v for x in bars)

    out = CandleBar(
        symbol=symbol,
        tf_s=tf_s,
        open_time_ms=b0,
        close_time_ms=b1,
        o=o, h=h, low=low, c=c, v=v,
        complete=True,
        src="derived",
    )
    assert_invariants(out, anchor_offset_s=anchor_offset_s)
    return out


# Оркестратор B: warmup + polling + backfill + derive

class PollingConnectorB:
    def __init__(
        self,
        provider: FxcmHistoryProvider,
        data_root: str,
        symbol: str,
        config_path: str,
        warmup_bars: int,
        safety_delay_s: int,
        derived_tfs_s: List[int],
        broker_base_tfs_s: List[int],
        broker_base_fetch_on_close: bool,
        broker_base_max_tf_per_poll: int,
        broker_base_cold_start_counts: Dict[int, int],
        broker_base_cold_start_enabled: bool,
        day_anchor_offset_s: int,
        day_anchor_offset_s_d1: Optional[int],
        day_anchor_offset_s_d1_alt: Optional[int],
        day_anchor_offset_s_alt: Optional[int],
        day_anchor_offset_s_alt2: Optional[int],
        backfill_step_bars: int,
        backfill_every_n_polls: int,
        derived_rebuild_lookback_bars: int,
        derived_tolerate_missing_minutes: int,
        derived_backfill_from_broker: bool,
        derived_force_close_from_broker: bool,
        derived_force_close_max_tf_per_poll: int,
        derived_rebuild_use_tool: bool,
        derived_rebuild_tool_dry_run: bool,
        calendar_gate_enabled: bool,
        poll_diag_enabled: bool,
        market_weekend_close_dow: int,
        market_weekend_close_hm: str,
        market_weekend_open_dow: int,
        market_weekend_open_hm: str,
        market_daily_break_start_hm: str,
        market_daily_break_end_hm: str,
        market_daily_break_enabled: bool,
    ) -> None:
        self._provider = provider
        self._data_root = data_root
        self._symbol = symbol
        self._config_path = config_path
        self._warmup_bars = warmup_bars
        self._safety_delay_s = safety_delay_s
        self._derived_tfs_s = derived_tfs_s
        self._broker_base_tfs_s = sorted({int(x) for x in broker_base_tfs_s if int(x) > 0})
        overlap = sorted(set(self._broker_base_tfs_s) & set(self._derived_tfs_s))
        if overlap:
            logging.warning(
                "Polling: derived_tfs_s перетинається з broker_base_tfs_s, видаляю з derived: %s",
                ",".join(str(x) for x in overlap),
            )
            self._derived_tfs_s = [x for x in self._derived_tfs_s if x not in overlap]
        self._broker_base_fetch_on_close = bool(broker_base_fetch_on_close)
        self._broker_base_max_tf_per_poll = max(0, int(broker_base_max_tf_per_poll))
        self._broker_base_cold_start_counts = {
            int(k): int(v)
            for k, v in broker_base_cold_start_counts.items()
            if int(k) > 0 and int(v) > 0
        }
        self._broker_base_cold_start_enabled = bool(broker_base_cold_start_enabled)
        self._day_anchor_offset_s = day_anchor_offset_s
        self._day_anchor_offset_s_d1 = day_anchor_offset_s_d1
        self._day_anchor_offset_s_d1_alt = day_anchor_offset_s_d1_alt
        self._day_anchor_offset_s_alt = day_anchor_offset_s_alt
        self._day_anchor_offset_s_alt2 = day_anchor_offset_s_alt2
        self._backfill_step_bars = backfill_step_bars
        self._backfill_every_n_polls = max(1, backfill_every_n_polls)
        self._derived_rebuild_lookback_bars = derived_rebuild_lookback_bars
        self._derived_tolerate_missing_minutes = max(0, int(derived_tolerate_missing_minutes))
        self._derived_backfill_from_broker = bool(derived_backfill_from_broker)
        self._derived_force_close_from_broker = bool(derived_force_close_from_broker)
        self._derived_force_close_max_tf_per_poll = max(0, int(derived_force_close_max_tf_per_poll))
        self._derived_rebuild_use_tool = bool(derived_rebuild_use_tool)
        self._derived_rebuild_tool_dry_run = bool(derived_rebuild_tool_dry_run)
        # CALENDAR_GATE: швидко вимкнути через config.
        self._calendar_gate_enabled = bool(calendar_gate_enabled)
        self._poll_diag_enabled = bool(poll_diag_enabled)
        self._market_weekend_close_dow = int(market_weekend_close_dow)
        self._market_weekend_close_hm = market_weekend_close_hm
        self._market_weekend_open_dow = int(market_weekend_open_dow)
        self._market_weekend_open_hm = market_weekend_open_hm
        self._market_daily_break_start_hm = market_daily_break_start_hm
        self._market_daily_break_end_hm = market_daily_break_end_hm
        self._market_daily_break_enabled = bool(market_daily_break_enabled)

        self._writer = JsonlAppender(
            root=data_root,
            day_anchor_offset_s=day_anchor_offset_s,
            day_anchor_offset_s_d1=day_anchor_offset_s_d1,
            day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
            day_anchor_offset_s_alt=day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
        )
        m1_keep = max(6000, warmup_bars + 2000, self._derived_rebuild_lookback_bars + 2000)
        self._m1 = M1Buffer(max_keep=m1_keep)

        self._last_saved_m1_open_ms: Optional[int] = None
        self._last_saved_derived: Dict[int, int] = {}  # tf_s -> open_time_ms
        self._last_saved_base: Dict[int, int] = {}  # tf_s -> open_time_ms
        self._oldest_saved_m1_open_ms: Optional[int] = None
        self._backfill_cursor_open_ms: Optional[int] = None
        self._poll_counter: int = 0
        self._day_index_cache: Dict[str, set[int]] = {}
        self._missing_derived_backfill_attempted: Set[Tuple[int, int]] = set()

    def run_forever(self) -> None:
        self._bootstrap_from_disk()
        self._cold_start_base_from_broker()
        self._warmup_history()
        self._loop()

    def bootstrap_and_warmup(self) -> None:
        self._bootstrap_from_disk()
        self._cold_start_base_from_broker()
        self._warmup_history()

    def poll_iteration(self) -> None:
        self._poll_once()
        self._poll_counter += 1
        if self._poll_counter % self._backfill_every_n_polls == 0:
            written = self._backfill_step()
            if written > self._backfill_step_bars:
                self._rebuild_derived_recent()

    def close(self) -> None:
        self._writer.close()

    def _bootstrap_from_disk(self) -> None:
        last = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=60)
        self._last_saved_m1_open_ms = last
        first = head_first_bar_time_ms(self._data_root, self._symbol, tf_s=60)
        self._oldest_saved_m1_open_ms = first
        self._backfill_cursor_open_ms = first
        if last is not None:
            logging.info(
                "Старт: знайдено останній 1m бар на диску open_time_utc=%s",
                ms_to_utc_dt(last).isoformat(),
            )
        else:
            logging.info("Старт: на диску немає 1m історії для %s", self._symbol)

        if first is not None:
            logging.info(
                "Старт: знайдено перший 1m бар на диску open_time_utc=%s",
                ms_to_utc_dt(first).isoformat(),
            )

        for tf_s in self._derived_tfs_s:
            last_d = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=tf_s)
            if last_d is not None:
                self._last_saved_derived[tf_s] = last_d

        for tf_s in self._broker_base_tfs_s:
            last_b = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=tf_s)
            if last_b is not None:
                self._last_saved_base[tf_s] = last_b

    def _cold_start_base_from_broker(self) -> None:
        if not self._broker_base_cold_start_enabled:
            return
        if not self._broker_base_cold_start_counts:
            logging.warning("Cold-start base: порожні counts, пропуск.")
            return

        date_to = dt.datetime.now(dt.timezone.utc)
        total_written = 0
        total_existing = 0
        total_found = 0

        for tf_s, n in sorted(self._broker_base_cold_start_counts.items()):
            if self._broker_base_tfs_s and tf_s not in self._broker_base_tfs_s:
                continue
            if self._last_saved_base.get(tf_s) is not None:
                logging.info("Cold-start base: TF=%ds вже є на диску, пропуск.", tf_s)
                continue
            bars = self._provider.fetch_last_n_tf(self._symbol, tf_s=tf_s, n=n, date_to_utc=date_to)
            if not bars:
                logging.warning("Cold-start base: broker пустий TF=%ds", tf_s)
                continue

            for b in bars:
                total_found += 1
                if self._has_on_disk(tf_s, b.open_time_ms):
                    total_existing += 1
                    continue
                self._writer.append(b)
                self._mark_on_disk(tf_s, b.open_time_ms)
                total_written += 1
                last = self._last_saved_base.get(tf_s)
                if last is None or b.open_time_ms > last:
                    self._last_saved_base[tf_s] = b.open_time_ms

        logging.info(
            "Cold-start base: written=%d existing=%d found=%d",
            total_written,
            total_existing,
            total_found,
        )

    def _warmup_history(self) -> None:
        """Підтягує warmup_bars останніх 1m барів і пише лише те, чого не було."""
        logging.info(
            "Warmup: запит %d барів m1 (чанками при потребі).", self._warmup_bars
        )

        cutoff_open = self._last_closed_cutoff_open_ms()
        date_to = ms_to_utc_dt(cutoff_open + 60_000)

        # Спроба: один запит на N барів “до останнього закритого”.
        bars = self._provider.fetch_last_n_m1(
            self._symbol, self._warmup_bars, date_to_utc=date_to
        )
        if not bars:
            logging.warning(
                "Warmup: history повернула 0 барів (ринок може бути закритий)."
            )
            return

        self._ingest_m1_bars(
            bars,
            allow_older=True,
            write_missing_older=False,
            log_summary=True,
            context="warmup",
        )
        return

    def _loop(self) -> None:
        logging.info(
            "Polling loop: режим B активний (тільки закриті 1m через history)."
        )
        try:
            while True:
                self._sleep_to_next_minute()
                self.poll_iteration()
        except KeyboardInterrupt:
            logging.info("Зупинено користувачем (KeyboardInterrupt). Завершую.")
        finally:
            self._writer.close()

    def _sleep_to_next_minute(self) -> None:
        sleep_to_next_minute(self._safety_delay_s)

    def _last_closed_cutoff_open_ms(self) -> int:
        return expected_last_closed_m1_open_ms(utc_now_ms())

    def _is_trading_minute(self, now_ms: int) -> bool:
        # CALENDAR_GATE: легко видалити разом з конфігом.
        dt_now = ms_to_utc_dt(now_ms)
        dow = dt_now.weekday()
        hm_break_start = _parse_hm(self._market_daily_break_start_hm)
        hm_break_end = _parse_hm(self._market_daily_break_end_hm)
        if self._market_daily_break_enabled and hm_break_start and hm_break_end:
            start_min = hm_break_start[0] * 60 + hm_break_start[1]
            end_min = hm_break_end[0] * 60 + hm_break_end[1]
            cur_min = dt_now.hour * 60 + dt_now.minute
            if start_min <= cur_min < end_min:
                return False

        hm_close = _parse_hm(self._market_weekend_close_hm)
        hm_open = _parse_hm(self._market_weekend_open_hm)
        if hm_close and hm_open:
            close_min = self._market_weekend_close_dow * 1440 + hm_close[0] * 60 + hm_close[1]
            open_min = self._market_weekend_open_dow * 1440 + hm_open[0] * 60 + hm_open[1]
            cur_min = dow * 1440 + dt_now.hour * 60 + dt_now.minute
            if close_min < open_min:
                if close_min <= cur_min < open_min:
                    return False
            else:
                if cur_min >= close_min or cur_min < open_min:
                    return False
        return True

    def _last_trading_minute_open_ms(self, now_ms: int) -> int:
        # Повертає open_time останньої торгової хвилини до now_ms.
        cur = (now_ms // 60_000) * 60_000 - 60_000
        for _ in range(7 * 24 * 60):
            if self._is_trading_minute(cur):
                return cur
            cur -= 60_000
        return (now_ms // 60_000) * 60_000 - 60_000

    def _poll_once(self) -> None:
        now_ms = utc_now_ms()
        exp_open = expected_last_closed_m1_open_ms(now_ms)
        date_to = ms_to_utc_dt(exp_open + 60_000)

        market_open = self._is_trading_minute(now_ms)
        base_anchor_open = exp_open
        if not self._is_trading_minute(exp_open):
            base_anchor_open = self._last_trading_minute_open_ms(exp_open)
        if self._broker_base_fetch_on_close:
            self._fetch_base_from_broker_on_close(base_anchor_open)
        if self._calendar_gate_enabled and not market_open:
            logging.info(
                "Polling: calendar_closed now=%s exp_open=%s",
                ms_to_utc_dt(now_ms).isoformat(),
                ms_to_utc_dt(exp_open).isoformat(),
            )
            return

        # Беремо 2 останні бари, щоб мати шанс побачити exp_open (на практиці інколи вистачає 1, але 2 надійніше).
        bars = self._provider.fetch_last_n_m1(self._symbol, n=2, date_to_utc=date_to)

        if self._poll_diag_enabled:
            last_open = bars[-1].open_time_ms if bars else None
            logging.info(
                "Polling: diag now=%s exp_open=%s last_open=%s market_open=%s bars=%d",
                ms_to_utc_dt(now_ms).isoformat(),
                ms_to_utc_dt(exp_open).isoformat(),
                ms_to_utc_dt(last_open).isoformat() if last_open is not None else "None",
                market_open,
                len(bars),
            )

        if not bars:
            logging.info("Polling: барів немає (ймовірно market closed).")
            return

        # Шукаємо бар із open_time_ms == exp_open.
        target = None
        for b in bars:
            if b.open_time_ms == exp_open:
                target = b
                break

        if target is None:
            # Якщо не знайшли — це або затримка “finalization”, або gap більший.
            # Робимо короткий backfill на 30 хвилин назад як “страховку”.
            logging.warning(
                "Polling: не знайдено очікуваний last-closed m1 open=%s; роблю короткий backfill.",
                ms_to_utc_dt(exp_open).isoformat(),
            )
            start_ms = exp_open - 30 * 60_000
            end_ms = exp_open
            written = self._backfill_range(start_ms, end_ms)
            if written > self._backfill_step_bars:
                self._rebuild_derived_recent()
        else:
            self._ingest_m1_bars([target], log_summary=True, context="poll")
            if self._broker_base_fetch_on_close:
                self._fetch_base_from_broker_on_close(target.open_time_ms)

    def _fetch_base_from_broker_on_close(self, anchor_open_ms: int) -> None:
        written = 0
        tried = 0
        for tf_s in self._broker_base_tfs_s:
            if (
                self._broker_base_max_tf_per_poll > 0
                and tried >= self._broker_base_max_tf_per_poll
            ):
                break
            anchor = select_anchor_offset_for_anchor_open_ms(
                tf_s,
                anchor_open_ms,
                self._day_anchor_offset_s,
                self._day_anchor_offset_s_alt,
                self._day_anchor_offset_s_alt2,
                self._day_anchor_offset_s_d1,
                self._day_anchor_offset_s_d1_alt,
            )
            tf_ms = tf_s * 1000
            b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=anchor)
            b1 = b0 + tf_ms
            expected_last = self._last_trading_minute_open_ms(b1 - 60_000)
            if anchor_open_ms != expected_last:
                continue
            if self._has_on_disk(tf_s, b0):
                continue

            tried += 1
            date_to = ms_to_utc_dt(b1)
            bars = self._provider.fetch_last_n_tf(
                self._symbol, tf_s=tf_s, n=1, date_to_utc=date_to
            )
            if not bars:
                logging.debug(
                    "Base TF: broker пустий TF=%ds bucket=%s",
                    tf_s,
                    ms_to_utc_dt(b0).isoformat(),
                )
                continue
            b = bars[-1]
            if b.open_time_ms != b0:
                logging.debug(
                    "Base TF: mismatch TF=%ds open=%s очікувалось=%s",
                    tf_s,
                    ms_to_utc_dt(b.open_time_ms).isoformat(),
                    ms_to_utc_dt(b0).isoformat(),
                )
                continue

            self._writer.append(b)
            self._mark_on_disk(tf_s, b0)
            last = self._last_saved_base.get(tf_s)
            if last is None or b0 > last:
                self._last_saved_base[tf_s] = b0
            written += 1

        if written:
            logging.info(
                "Base TF: дописано=%d tried=%d",
                written,
                tried,
            )

    def _day_key(self, open_time_ms: int) -> str:
        return ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")

    def _day_index_key(self, tf_s: int, day: str) -> str:
        return f"{tf_s}:{day}"

    def _load_day_index(self, tf_s: int, day: str) -> set[int]:
        key = self._day_index_key(tf_s, day)
        cached = self._day_index_cache.get(key)
        if cached is not None:
            return cached

        out = load_day_open_times(self._data_root, self._symbol, tf_s, day)
        self._day_index_cache[key] = out
        return out

    def _has_on_disk(self, tf_s: int, open_time_ms: int) -> bool:
        day = self._day_key(open_time_ms)
        idx = self._load_day_index(tf_s, day)
        return open_time_ms in idx

    def _mark_on_disk(self, tf_s: int, open_time_ms: int) -> None:
        day = self._day_key(open_time_ms)
        idx = self._load_day_index(tf_s, day)
        idx.add(open_time_ms)

    def _ingest_m1_bars(
        self,
        bars: List[CandleBar],
        allow_older: bool = False,
        write_missing_older: bool = False,
        log_summary: bool = False,
        context: str = "",
    ) -> int:
        """Дедуп + gap-detect + append SSOT + derive."""
        cutoff = self._last_closed_cutoff_open_ms()
        cutoff_iso = ms_to_utc_dt(cutoff).isoformat()
        written = 0
        skipped_cutoff = 0
        skipped_dedup = 0
        derived_written = 0

        for b in bars:
            if b.open_time_ms > cutoff:
                logging.info(
                    "M1: пропуск не закритого бару open=%s (cutoff=%s)",
                    ms_to_utc_dt(b.open_time_ms).isoformat(),
                    cutoff_iso,
                )
                skipped_cutoff += 1
                continue

            if self._last_saved_m1_open_ms is not None and b.open_time_ms <= self._last_saved_m1_open_ms:
                if allow_older:
                    self._m1.upsert(b)
                    if write_missing_older and not self._has_on_disk(60, b.open_time_ms):
                        self._writer.append(b)
                        self._mark_on_disk(60, b.open_time_ms)
                        written += 1
                    if self._oldest_saved_m1_open_ms is None or b.open_time_ms < self._oldest_saved_m1_open_ms:
                        self._oldest_saved_m1_open_ms = b.open_time_ms
                    if self._backfill_cursor_open_ms is None or b.open_time_ms < self._backfill_cursor_open_ms:
                        self._backfill_cursor_open_ms = b.open_time_ms
                    continue
                skipped_dedup += 1
                continue

            # gap detect: якщо пропущено > 1 хв
            if self._last_saved_m1_open_ms is not None and b.open_time_ms > self._last_saved_m1_open_ms:
                gap_ms = b.open_time_ms - self._last_saved_m1_open_ms
                if gap_ms > 60_000:
                    # backfill пропущених хвилин
                    missing_start = self._last_saved_m1_open_ms + 60_000
                    missing_end = b.open_time_ms - 60_000
                    logging.warning(
                        "Gap: пропущено %d хв; backfill [%s .. %s].",
                        gap_ms // 60_000 - 1,
                        ms_to_utc_dt(missing_start).isoformat(),
                        ms_to_utc_dt(missing_end).isoformat(),
                    )
                    self._backfill_range(missing_start, missing_end)

            # пишемо 1m SSOT
            self._writer.append(b)
            self._mark_on_disk(60, b.open_time_ms)
            self._m1.upsert(b)
            written += 1
            if self._last_saved_m1_open_ms is None or b.open_time_ms > self._last_saved_m1_open_ms:
                self._last_saved_m1_open_ms = b.open_time_ms
            if self._oldest_saved_m1_open_ms is None or b.open_time_ms < self._oldest_saved_m1_open_ms:
                self._oldest_saved_m1_open_ms = b.open_time_ms
            if self._backfill_cursor_open_ms is None or b.open_time_ms < self._backfill_cursor_open_ms:
                self._backfill_cursor_open_ms = b.open_time_ms

            logging.debug(
                "M1: записано open=%s o=%.5f h=%.5f low=%.5f c=%.5f v=%.2f",
                ms_to_utc_dt(b.open_time_ms).isoformat(),
                b.o,
                b.h,
                b.low,
                b.c,
                b.v,
            )

            # derive після кожного нового 1m
            derived_written += self._try_derive_all(anchor_open_ms=b.open_time_ms)

        if log_summary:
            ctx = context or "ingest"
            logging.info(
                "M1: %s записано=%d derived=%d skip_cutoff=%d skip_dedup=%d",
                ctx,
                written,
                derived_written,
                skipped_cutoff,
                skipped_dedup,
            )
        return written

    def _backfill_range(self, start_ms: int, end_ms: int) -> int:
        """Backfill діапазону [start_ms..end_ms] включно, чанками через date_to + quotes_count."""
        if start_ms > end_ms:
            return 0

        # cursor_end рухається назад
        cursor_end = (
            end_ms + 60_000
        )  # date_to на close наступного, щоб шанс включити end_ms
        chunk = 300  # ForexConnect часто “комфортно” на 300; більше може працювати, але 300 стабільно.

        collected: List[CandleBar] = []
        loops = 0

        while True:
            loops += 1
            if loops > 200:
                logging.error(
                    "Backfill: занадто багато ітерацій; зупиняюсь щоб не зависнути."
                )
                break

            date_to = ms_to_utc_dt(cursor_end)
            bars = self._provider.fetch_last_n_m1(
                self._symbol, n=chunk, date_to_utc=date_to
            )
            if not bars:
                break

            # фільтр по діапазону
            for b in bars:
                if start_ms <= b.open_time_ms <= end_ms:
                    collected.append(b)

            oldest = bars[0].open_time_ms
            if oldest <= start_ms:
                break

            # рухаємо cursor_end до “перед oldest”
            cursor_end = oldest - 60_000

        if not collected:
            logging.info(
                "Backfill: у діапазоні немає барів (можливо ринок був закритий)."
            )
            return 0

        collected.sort(key=lambda x: x.open_time_ms)

        # інжестимо як backfill (старі бари)
        written = self._ingest_m1_bars(
            collected,
            allow_older=True,
            write_missing_older=True,
            log_summary=True,
            context="backfill",
        )
        return written

    def _backfill_step(self) -> int:
        if self._backfill_cursor_open_ms is None:
            return 0

        end_ms = self._backfill_cursor_open_ms - 60_000
        if end_ms <= 0:
            return 0

        step = max(1, self._backfill_step_bars)
        date_to = ms_to_utc_dt(end_ms + 60_000)

        logging.info(
            "Backfill: крок %d барів (date_to=%s)",
            step,
            date_to.isoformat(),
        )

        before_oldest = self._oldest_saved_m1_open_ms
        bars = self._provider.fetch_last_n_m1(
            self._symbol, n=step, date_to_utc=date_to
        )
        if not bars:
            logging.info("Backfill: нових барів не знайдено; зупиняю backfill.")
            self._backfill_cursor_open_ms = None
            return 0

        written = self._ingest_m1_bars(bars, allow_older=True, write_missing_older=True)

        after_oldest = self._oldest_saved_m1_open_ms
        if before_oldest == after_oldest:
            logging.info("Backfill: нових барів не знайдено; зупиняю backfill.")
            self._backfill_cursor_open_ms = None
            return 0

        oldest_fetched = bars[0].open_time_ms
        self._backfill_cursor_open_ms = oldest_fetched
        return written

    def _rebuild_derived_recent(self) -> None:
        earliest = self._m1.earliest_open_ms()
        latest = self._m1.latest_open_ms()
        if earliest is None or latest is None:
            return

        lookback = max(0, int(self._derived_rebuild_lookback_bars))
        start_ms = max(earliest, latest - lookback * 60_000)

        if self._derived_rebuild_use_tool:
            self._rebuild_derived_via_tool(start_ms, latest)
            return

        logging.warning(
            "Rebuild derived: вимкнено (use_tool=false), пропуск rebuild у core"
        )

    def _rebuild_derived_via_tool(self, start_ms: int, end_ms: int) -> None:
        tf_list = ",".join(str(x) for x in self._derived_tfs_s)
        project_root = os.path.dirname(__file__)
        args = [
            sys.executable,
            os.path.join(project_root, "tools", "rebuild_derived.py"),
            "--config",
            self._config_path,
            "--symbol",
            self._symbol,
            "--tf",
            tf_list,
            "--start-utc",
            ms_to_utc_dt(start_ms).isoformat(),
            "--end-utc",
            ms_to_utc_dt(end_ms).isoformat(),
        ]
        if self._derived_rebuild_tool_dry_run:
            args.append("--dry-run")

        logging.info(
            "Rebuild derived (tool): запуск %s",
            " ".join(args),
        )
        try:
            env = os.environ.copy()
            prev = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                project_root if not prev else f"{project_root}{os.pathsep}{prev}"
            )
            subprocess.run(args, check=True, cwd=project_root, env=env)
        except Exception:
            logging.exception("Rebuild derived (tool): помилка виконання")

    def _try_derive_all(self, anchor_open_ms: int) -> int:
        """Пробуємо збудувати всі derived TF на основі останнього 1m (anchor_open_ms)."""
        written = 0
        for tf_s in self._derived_tfs_s:
            anchor = select_anchor_offset_for_anchor_open_ms(
                tf_s,
                anchor_open_ms,
                self._day_anchor_offset_s,
                self._day_anchor_offset_s_alt,
                self._day_anchor_offset_s_alt2,
                self._day_anchor_offset_s_d1,
                self._day_anchor_offset_s_d1_alt,
            )
            tf_ms = tf_s * 1000
            b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=anchor)
            b1 = b0 + tf_ms
            if anchor_open_ms != (b1 - 60_000):
                continue

            missing = self._m1.missing_count(b0, b1)
            if missing != 0:
                continue

            d = derive_from_m1_for_anchor(
                self._symbol,
                tf_s=tf_s,
                m1=self._m1,
                anchor_open_ms=anchor_open_ms,
                anchor_offset_s=anchor,
            )

            if d is None:
                continue

            last = self._last_saved_derived.get(tf_s)
            if last is not None and d.open_time_ms <= last:
                if self._has_on_disk(tf_s, d.open_time_ms):
                    continue

            if not self._has_on_disk(tf_s, d.open_time_ms):
                self._writer.append(d)
                self._mark_on_disk(tf_s, d.open_time_ms)
                written += 1
            if last is None or d.open_time_ms > last:
                self._last_saved_derived[tf_s] = d.open_time_ms

            logging.debug(
                "DERIVED tf=%ds: записано open=%s o=%.5f h=%.5f low=%.5f c=%.5f v=%.2f",
                tf_s,
                ms_to_utc_dt(d.open_time_ms).isoformat(),
                d.o,
                d.h,
                d.low,
                d.c,
                d.v,
            )

        return written


class MultiSymbolRunner:
    def __init__(self, engines: List[PollingConnectorB]) -> None:
        self._engines = engines
        self._safety_delay_s = max((e._safety_delay_s for e in engines), default=0)  # noqa: SLF001

    def run_forever(self) -> None:
        symbols = [e._symbol for e in self._engines]  # noqa: SLF001
        logging.info("Polling loop: multi активний symbols=%s", ",".join(symbols))
        try:
            for e in self._engines:
                e.bootstrap_and_warmup()
            while True:
                sleep_to_next_minute(self._safety_delay_s)
                for e in self._engines:
                    e.poll_iteration()
        except KeyboardInterrupt:
            logging.info("Зупинено користувачем (KeyboardInterrupt). Завершую.")
        finally:
            for e in self._engines:
                e.close()


def main() -> int:
    setup_logging(verbose=False)
    logging.info("Запуск PollingConnectorB")

    def _parse_tf_counts_cfg(raw: Any) -> Dict[int, int]:
        out: Dict[int, int] = {}
        if raw is None:
            return out
        if isinstance(raw, dict):
            for k, v in raw.items():
                try:
                    tf_s = int(k)
                    cnt = int(v)
                except Exception:
                    continue
                if tf_s > 0 and cnt > 0:
                    out[tf_s] = cnt
            return out
        if isinstance(raw, list):
            for item in raw:
                try:
                    s = str(item)
                    if ":" in s:
                        tf_s_str, n_str = s.split(":", 1)
                    elif "=" in s:
                        tf_s_str, n_str = s.split("=", 1)
                    else:
                        continue
                    tf_s = int(tf_s_str.strip())
                    cnt = int(n_str.strip())
                    if tf_s > 0 and cnt > 0:
                        out[tf_s] = cnt
                except Exception:
                    continue
            return out
        return out

    try:
        cfg = load_config("config.json")
    except Exception:
        logging.exception("Не вдалось завантажити config.json")
        return 2

    # Не логувати пароль
    masked = dict(cfg)
    if "password" in masked:
        masked["password"] = "***"
    logging.debug("Конфіг завантажено: %s", json.dumps(masked, ensure_ascii=False))

    try:
        user_id = str(cfg["user_id"])
        password = str(cfg["password"])
        url = str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
        connection = str(cfg.get("connection", "Demo"))

        symbols_raw = cfg.get("symbols", None)
        if isinstance(symbols_raw, list) and symbols_raw:
            symbols = [str(x) for x in symbols_raw if str(x).strip()]
        else:
            symbols = [str(cfg.get("symbol", "XAU/USD"))]
        data_root = str(cfg.get("data_root", "./data_v3"))
        warmup_bars = int(cfg.get("warmup_bars", 3000))
        safety_delay_s = int(cfg.get("safety_delay_s", 2))

        derived = cfg.get(
            "derived_tfs_s", [180, 300, 900, 1800, 3600]
        )
        derived_tfs_s = [int(x) for x in derived]

        broker_base_raw = cfg.get("broker_base_tfs_s", [14400, 86400])
        broker_base_tfs_s = [int(x) for x in broker_base_raw]

        broker_base_fetch_on_close = bool(cfg.get("broker_base_fetch_on_close", True))
        broker_base_max_tf_per_poll = int(cfg.get("broker_base_max_tf_per_poll", 0))
        broker_base_cold_start_enabled = bool(
            cfg.get("broker_base_cold_start_enabled", True)
        )
        broker_base_cold_start_counts = _parse_tf_counts_cfg(
            cfg.get("broker_base_cold_start_counts", {"14400": 1080, "86400": 180})
        )

        day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
        day_anchor_offset_s_alt_raw = cfg.get("day_anchor_offset_s_alt", None)
        day_anchor_offset_s_alt = (
            None if day_anchor_offset_s_alt_raw is None else int(day_anchor_offset_s_alt_raw)
        )
        day_anchor_offset_s_alt2_raw = cfg.get("day_anchor_offset_s_alt2", None)
        day_anchor_offset_s_alt2 = (
            None if day_anchor_offset_s_alt2_raw is None else int(day_anchor_offset_s_alt2_raw)
        )
        day_anchor_offset_s_d1_raw = cfg.get("day_anchor_offset_s_d1", None)
        day_anchor_offset_s_d1 = (
            None if day_anchor_offset_s_d1_raw is None else int(day_anchor_offset_s_d1_raw)
        )
        day_anchor_offset_s_d1_alt_raw = cfg.get("day_anchor_offset_s_d1_alt", None)
        day_anchor_offset_s_d1_alt = (
            None if day_anchor_offset_s_d1_alt_raw is None else int(day_anchor_offset_s_d1_alt_raw)
        )
        backfill_step_bars = int(cfg.get("history_backfill_step_bars", 300))
        backfill_every_n_polls = int(cfg.get("history_backfill_every_n_polls", 5))
        derived_rebuild_lookback_bars = int(cfg.get("derived_rebuild_lookback_bars", 60000))
        derived_rebuild_use_tool = bool(cfg.get("derived_rebuild_use_tool", False))
        derived_rebuild_tool_dry_run = bool(cfg.get("derived_rebuild_tool_dry_run", False))
        derived_tolerate_missing_minutes = int(
            cfg.get("derived_tolerate_missing_minutes", 0)
        )
        derived_backfill_from_broker = bool(
            cfg.get("derived_backfill_from_broker", True)
        )
        derived_force_close_from_broker = bool(
            cfg.get("derived_force_close_from_broker", False)
        )
        derived_force_close_max_tf_per_poll = int(
            cfg.get("derived_force_close_max_tf_per_poll", 0)
        )
        live_candle_enabled = bool(cfg.get("live_candle_enabled", False))
        live_candle_autostart = bool(cfg.get("live_candle_autostart", True))
        calendar_gate_enabled = bool(cfg.get("calendar_gate_enabled", False))
        poll_diag_enabled = bool(cfg.get("poll_diag_enabled", False))
        market_weekend_close_dow = int(cfg.get("market_weekend_close_dow", 4))
        market_weekend_close_hm = str(cfg.get("market_weekend_close_hm", "21:44"))
        market_weekend_open_dow = int(cfg.get("market_weekend_open_dow", 6))
        market_weekend_open_hm = str(cfg.get("market_weekend_open_hm", "22:00"))
        market_daily_break_start_hm = str(cfg.get("market_daily_break_start_hm", "21:59"))
        market_daily_break_end_hm = str(cfg.get("market_daily_break_end_hm", "23:01"))
        market_daily_break_enabled = bool(cfg.get("market_daily_break_enabled", True))
    except Exception:
        logging.exception("Невірна конфігурація")
        return 2

    logging.debug(
        "Параметри: user=%s url=%s connection=%s symbols=%s data_root=%s warmup_bars=%d safety_delay_s=%d derived=%s broker_base=%s broker_base_fetch_on_close=%s broker_base_max_tf_per_poll=%d broker_base_cold_start_enabled=%s broker_base_cold_start_counts=%s day_anchor_offset_s=%d day_anchor_offset_s_alt=%s day_anchor_offset_s_alt2=%s day_anchor_offset_s_d1=%s day_anchor_offset_s_d1_alt=%s",
        user_id,
        url,
        connection,
        symbols,
        data_root,
        warmup_bars,
        safety_delay_s,
        derived_tfs_s,
        broker_base_tfs_s,
        str(broker_base_fetch_on_close),
        broker_base_max_tf_per_poll,
        str(broker_base_cold_start_enabled),
        broker_base_cold_start_counts,
        day_anchor_offset_s,
        str(day_anchor_offset_s_alt),
        str(day_anchor_offset_s_alt2),
        str(day_anchor_offset_s_d1),
        str(day_anchor_offset_s_d1_alt),
    )

    if derived_backfill_from_broker:
        logging.warning(
            "Config: derived_backfill_from_broker=true ігнорується (похідні тільки з M1)."
        )
    if derived_force_close_from_broker:
        logging.warning(
            "Config: derived_force_close_from_broker=true ігнорується (base TF беруться окремо)."
        )

    try:
        os.makedirs(data_root, exist_ok=True)
        logging.debug("Каталог даних готовий: %s", data_root)
    except Exception:
        logging.exception("Не вдалось створити data_root: %s", data_root)
        return 2

    live_proc: Optional[subprocess.Popen] = None
    if live_candle_enabled and live_candle_autostart:
        try:
            project_root = os.path.dirname(os.path.abspath(__file__))
            env = dict(os.environ)
            prev = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = project_root + (os.pathsep + prev if prev else "")
            live_proc = subprocess.Popen(
                [sys.executable, "-m", "tools.live_candle"],
                cwd=project_root,
                env=env,
            )
            logging.info("LIVE_BAR: автозапуск tools.live_candle pid=%s", live_proc.pid)
        except Exception:
            logging.exception("LIVE_BAR: не вдалося запустити tools.live_candle")

    try:
        try:
            with FxcmHistoryProvider(
                user_id=user_id,
                password=password,
                url=url,
                connection=connection,
                day_anchor_offset_s=day_anchor_offset_s,
                day_anchor_offset_s_d1=day_anchor_offset_s_d1,
                day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
                day_anchor_offset_s_alt=day_anchor_offset_s_alt,
                day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
            ) as prov:
                engines: List[PollingConnectorB] = []
                for symbol in symbols:
                    engines.append(
                        PollingConnectorB(
                            provider=prov,
                            data_root=data_root,
                            symbol=symbol,
                            config_path="config.json",
                            warmup_bars=warmup_bars,
                            safety_delay_s=safety_delay_s,
                            derived_tfs_s=derived_tfs_s,
                            broker_base_tfs_s=broker_base_tfs_s,
                            broker_base_fetch_on_close=broker_base_fetch_on_close,
                            broker_base_max_tf_per_poll=broker_base_max_tf_per_poll,
                            broker_base_cold_start_counts=broker_base_cold_start_counts,
                            broker_base_cold_start_enabled=broker_base_cold_start_enabled,
                            day_anchor_offset_s=day_anchor_offset_s,
                            day_anchor_offset_s_d1=day_anchor_offset_s_d1,
                            day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
                            day_anchor_offset_s_alt=day_anchor_offset_s_alt,
                            day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
                            backfill_step_bars=backfill_step_bars,
                            backfill_every_n_polls=backfill_every_n_polls,
                            derived_rebuild_lookback_bars=derived_rebuild_lookback_bars,
                            derived_tolerate_missing_minutes=derived_tolerate_missing_minutes,
                            derived_backfill_from_broker=derived_backfill_from_broker,
                            derived_force_close_from_broker=derived_force_close_from_broker,
                            derived_force_close_max_tf_per_poll=derived_force_close_max_tf_per_poll,
                            derived_rebuild_use_tool=derived_rebuild_use_tool,
                            derived_rebuild_tool_dry_run=derived_rebuild_tool_dry_run,
                            calendar_gate_enabled=calendar_gate_enabled,
                            poll_diag_enabled=poll_diag_enabled,
                            market_weekend_close_dow=market_weekend_close_dow,
                            market_weekend_close_hm=market_weekend_close_hm,
                            market_weekend_open_dow=market_weekend_open_dow,
                            market_weekend_open_hm=market_weekend_open_hm,
                            market_daily_break_start_hm=market_daily_break_start_hm,
                            market_daily_break_end_hm=market_daily_break_end_hm,
                            market_daily_break_enabled=market_daily_break_enabled,
                        )
                    )

                try:
                    if len(engines) == 1:
                        logging.debug("Engine створено, стартую run_forever()")
                        engines[0].run_forever()
                    else:
                        logging.debug("Engines створено: %d, стартую MultiSymbolRunner", len(engines))
                        MultiSymbolRunner(engines).run_forever()
                except Exception:
                    logging.exception("Помилка в роботі engine")
                    return 1
        except KeyboardInterrupt:
            logging.info("Зупинено користувачем (KeyboardInterrupt) у main.")
            return 0
        except Exception:
            logging.exception("Не вдалось ініціалізувати FxcmHistoryProvider або запустити engine")
            return 3
    finally:
        if live_proc is not None and live_proc.poll() is None:
            logging.info("LIVE_BAR: зупиняю tools.live_candle pid=%s", live_proc.pid)
            try:
                live_proc.terminate()
                live_proc.wait(timeout=5)
            except Exception:
                try:
                    live_proc.kill()
                except Exception:
                    pass

    logging.info("Завершення роботи (main)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
