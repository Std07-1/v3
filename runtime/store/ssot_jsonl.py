from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.model.bars import CandleBar, FINAL_SOURCES, assert_invariants, ms_to_utc_dt
from core.session_anchor import H4_S, assert_on_season_grid, htf_anchor_offset_s
from runtime.store.layers.disk_layer import DiskLayer

_M1_S = 60
_M1_MS = _M1_S * 1000
# Скільки часу навколо партії читає пакетний записувач M1, щоб знайти видимих сусідів для ланцюга (ADR-0101 C3).
# Найдовша пауза календарів груп — вихідні з перервою (~2.1 доби), зі святом Різдва чи Нового року — до ~4 діб.
# Сусід далі — межа історії або діра даних; тоді серія починає ланцюг з open брокера, а діру закриває settle.
M1_CHAIN_NEIGHBOR_SPAN_MS = 5 * 86_400_000


def serialize_bar(bar: CandleBar) -> str:
    """Рядок SSOT для бару без переводу рядка — як його пише писар. Одне визначення для писаря і пакетних
    інструментів, що складають part-файли самі (заміна ADR-0095 S7, ремонт дірок M1)."""
    return json.dumps(bar.to_dict(), ensure_ascii=False, separators=(",", ":"))


class AnchorRuleMissingError(ValueError):
    """Правило якоря H4/D1 символу недоступне: писар без резолвера або група календаря з невиміряною сіткою.

    Окремий тип, щоб UDS відрізняв відмову бару від збою диска і не публікував бар у Redis/pubsub (ADR-0095 §3.3).
    """


class JsonlAppender:
    """Append-only JSONL writer із ротацією по даті open_time_utc (YYYYMMDD).

    H4/D1 пишуться лише на сезонній сітці символу (ADR-0095 §3.3): рівність, а не членство в наборі якорів.
    `anchor_rule_for_symbol` — резолвер `core.config_loader.htf_anchor_rule_resolver(cfg)`. Без нього HTF-бар —
    гучна відмова `anchor_rule_missing`, а не тихий якір 0. M1..H1 резолвера не потребують.
    """

    _MAX_OPEN_FILES = 64  # LRU-ліміт відкритих FD (запобігає витоку)

    def __init__(
        self,
        root: str,
        anchor_rule_for_symbol: Optional[Callable[[str], str]] = None,
        fsync: bool = False,
    ) -> None:
        self._root = root
        self._open_files: Dict[str, Any] = {}
        self._open_files_order: List[str] = []  # LRU order
        self._fsync = fsync
        self._anchor_rule_for_symbol = anchor_rule_for_symbol
        self._drop_preview_total = 0
        self._drop_log_last_ts = 0.0
        self._drop_log_suppressed = 0

    def drop_preview_total(self) -> int:
        return int(self._drop_preview_total)

    def _assert_bucket(self, bar: CandleBar) -> None:
        """Геометрія бакета: M1..H1 — від епохи; H4/D1 — рівність сезонній сітці, потім close = open + tf (I2)."""
        if bar.tf_s < H4_S:
            assert_invariants(bar, anchor_offset_s=0)
            return
        if self._anchor_rule_for_symbol is None:
            raise AnchorRuleMissingError(
                "anchor_rule_missing symbol=%s tf_s=%d open_ms=%d — JsonlAppender без резолвера правила якоря "
                "(ADR-0095 §3.3)" % (bar.symbol, bar.tf_s, bar.open_time_ms)
            )
        try:
            rule = self._anchor_rule_for_symbol(bar.symbol)
        except ValueError as exc:  # символ без групи або група без виміряної сітки (htf_anchor_rule_resolver)
            raise AnchorRuleMissingError(
                "anchor_rule_missing symbol=%s tf_s=%d open_ms=%d cause=%s" % (bar.symbol, bar.tf_s, bar.open_time_ms, exc)
            ) from exc
        assert_on_season_grid(bar.open_time_ms, bar.tf_s, rule)
        assert_invariants(bar, anchor_offset_s=htf_anchor_offset_s(bar.tf_s, bar.open_time_ms, rule))

    def _path_for(self, symbol: str, tf_s: int, open_time_ms: int) -> str:
        day = ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")
        sym_dir = symbol.replace("/", "_")
        tf_dir = f"tf_{tf_s}"
        out_dir = os.path.join(self._root, sym_dir, tf_dir)
        # SEC-02: path traversal guard
        resolved = os.path.abspath(out_dir)
        root_resolved = os.path.abspath(self._root)
        if (
            not resolved.startswith(root_resolved + os.sep)
            and resolved != root_resolved
        ):
            raise ValueError("SSOT_PATH_TRAVERSAL symbol=%s" % symbol)
        os.makedirs(out_dir, exist_ok=True)
        return os.path.join(out_dir, f"part-{day}.jsonl")

    def append(self, bar: CandleBar) -> None:
        if not bar.complete or bar.src not in FINAL_SOURCES:
            self._drop_preview_total += 1
            self._drop_log_suppressed += 1
            import time as _time

            now = _time.monotonic()
            if now - self._drop_log_last_ts >= 30.0:
                logging.error(
                    "SSOT_DROP_NON_FINAL symbol=%s tf_s=%s open_ms=%s complete=%s src=%s drop_total=%s suppressed=%s",
                    bar.symbol,
                    bar.tf_s,
                    bar.open_time_ms,
                    bar.complete,
                    bar.src,
                    self._drop_preview_total,
                    self._drop_log_suppressed,
                )
                self._drop_log_last_ts = now
                self._drop_log_suppressed = 0
            return
        self._assert_bucket(bar)
        path = self._path_for(bar.symbol, bar.tf_s, bar.open_time_ms)
        fh = self._open_files.get(path)
        if fh is None:
            # LRU eviction: закрити найстаріший FD якщо ліміт досягнуто
            if len(self._open_files) >= self._MAX_OPEN_FILES:
                evict_path = self._open_files_order.pop(0)
                evict_fh = self._open_files.pop(evict_path, None)
                if evict_fh is not None:
                    try:
                        evict_fh.close()
                    except Exception:
                        logging.debug(
                            "SSOT_EVICT_CLOSE_FAIL path=%s", evict_path, exc_info=True
                        )
                        pass
            fh = open(path, "a", encoding="utf-8")
            self._open_files[path] = fh
            self._open_files_order.append(path)
        else:
            # Touch: перемістити в кінець LRU
            if path in self._open_files_order:
                self._open_files_order.remove(path)
                self._open_files_order.append(path)
        fh.write(serialize_bar(bar) + "\n")
        fh.flush()
        if self._fsync:
            os.fsync(fh.fileno())

    def close(self) -> None:
        for fh in self._open_files.values():
            try:
                fh.close()
            except Exception:
                logging.debug("SSOT_CLOSE_FAIL", exc_info=True)
                pass
        self._open_files.clear()


def _selftest_ssot_guard() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        app = JsonlAppender(tmp)
        preview = CandleBar(
            symbol="XAU/USD",
            tf_s=60,
            open_time_ms=0,
            close_time_ms=60_000,
            o=1.0,
            h=1.0,
            low=1.0,
            c=1.0,
            v=0.0,
            complete=False,
            src="preview_tick",
        )
        app.append(preview)
        has_files = any(os.scandir(tmp))
        return app.drop_preview_total() == 1 and not has_files


def _part_paths_sorted(data_root: str, symbol: str, tf_s: int) -> List[str]:
    """Шляхи part-файлів за зростанням доби. Ім'я part-YYYYMMDD = календарна доба open_time_ms."""
    dir_path = os.path.join(data_root, symbol.replace("/", "_"), f"tf_{tf_s}")
    if not os.path.isdir(dir_path):
        return []
    parts = [
        p for p in os.listdir(dir_path)
        if p.startswith("part-") and p.endswith(".jsonl")
    ]
    parts.sort()  # YYYYMMDD => лексикографічно ок
    return [os.path.join(dir_path, p) for p in parts]


def _scan_bounds(path: str) -> Optional[Tuple[int, int, int, int]]:
    """(min, max, перший-у-файлі, останній-у-файлі) open_time_ms; None якщо барів немає."""
    lo = hi = first = last = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    open_ms = int(json.loads(line)["open_time_ms"])
                except Exception:
                    logging.debug("SSOT_BOUNDS_PARSE_FAIL path=%s", path)
                    continue
                if first is None:
                    first = lo = hi = open_ms
                last = open_ms
                lo = min(lo, open_ms)
                hi = max(hi, open_ms)
    except Exception:
        logging.debug("SSOT_BOUNDS_READ_FAIL path=%s", path, exc_info=True)
        return None
    if first is None:
        return None
    return lo, hi, first, last


def tail_last_bar_time_ms(data_root: str, symbol: str, tf_s: int) -> Optional[int]:
    """Найбільший open_time_ms для (symbol, tf_s) на диску.

    МАКСИМУМ у найновішому part-файлі, а не його останній рядок: файл не зобов'язаний
    бути відсортованим за часом — `tools/fetch_tf_backfill` дописує сторінки історії у
    зворотному порядку і лишає шов на кожному кроці ланцюжка. Раніше функція читала
    лише останній 8 КБ чанк і брала з нього останній валідний рядок, тож на шві
    повертала занижене значення; `tools/rebuild_from_m1` бере це значення як типовий
    `--end`, тобто перебудова мовчки не доходила до найсвіжіших барів.
    """
    for path in reversed(_part_paths_sorted(data_root, symbol, tf_s)):
        bounds = _scan_bounds(path)
        if bounds is None:
            continue
        _lo, hi, _first, last = bounds
        if hi != last:
            logging.warning(
                "SSOT_PART_UNSORTED path=%s max_open_ms=%d last_line_open_ms=%d "
                "behind_ms=%d — межу історії взято з максимуму",
                path, hi, last, hi - last,
            )
        return hi
    return None


def head_first_bar_time_ms(data_root: str, symbol: str, tf_s: int) -> Optional[int]:
    """Найменший open_time_ms для (symbol, tf_s) на диску.

    МІНІМУМ у найстарішому part-файлі, а не його перший рядок — з тієї ж причини, що
    й у `tail_last_bar_time_ms`. Завищений початок історії робить типовий `--start`
    у `tools/rebuild_from_m1` пізнішим за справжній, тобто найстаріші бари мовчки
    лишаються поза перебудовою.
    """
    for path in _part_paths_sorted(data_root, symbol, tf_s):
        bounds = _scan_bounds(path)
        if bounds is None:
            continue
        lo, _hi, first, _last = bounds
        if lo != first:
            logging.warning(
                "SSOT_PART_UNSORTED path=%s min_open_ms=%d first_line_open_ms=%d "
                "ahead_ms=%d — межу історії взято з мінімуму",
                path, lo, first, first - lo,
            )
        return lo
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


def read_m1_chain_context(data_root: str, symbol: str, start_ms: int, end_ms: int) -> List[CandleBar]:
    """Бари SSOT M1 у [start − span, end + span] так, як їх бачать читачі: вибирач дублікатів ADR-0094 і відсів
    чужих та нефінальних рядків — через `DiskLayer`, з `extensions` (маркер `calendar_pause_flat`).

    Пакетний записувач M1 бере з них сусідів партії для ланцюга ADR-0101 (`m1_session_filter.plan_m1_append`).
    Рядок без OHLC у ланцюг не йде — гучно (I5), а не мовчки.
    """
    first_ms = start_ms - M1_CHAIN_NEIGHBOR_SPAN_MS
    last_ms = end_ms + M1_CHAIN_NEIGHBOR_SPAN_MS
    rows, _geom = DiskLayer(data_root).read_window_with_geom(
        symbol, _M1_S, (last_ms - first_ms) // _M1_MS + 1, since_open_ms=first_ms - 1, to_open_ms=last_ms,
        use_tail=True, final_only=True,
    )
    bars: List[CandleBar] = []
    rejected: List[Any] = []
    for row in rows:
        try:
            bars.append(_m1_row_to_bar(row, symbol))
        except (KeyError, TypeError, ValueError):
            rejected.append(row.get("open_time_ms"))
    if rejected:
        logging.warning(
            "SSOT_M1_CONTEXT_ROWS_REJECTED symbol=%s rejected=%d first_open_ms=%s — рядок без OHLC у ланцюг не йде",
            symbol, len(rejected), rejected[0],
        )
    return bars


def _m1_row_to_bar(row: Dict[str, Any], symbol: str) -> CandleBar:
    extensions = row.get("extensions")
    return CandleBar(
        symbol=symbol,
        tf_s=_M1_S,
        open_time_ms=int(row["open_time_ms"]),
        close_time_ms=int(row["close_time_ms"]),
        o=float(row["o"]),
        h=float(row["h"]),
        low=float(row["low"] if "low" in row else row["l"]),  # CandleBar — `.low`, легасі-рядок диска — "l"
        c=float(row["c"]),
        v=float(row.get("v", 0.0)),
        complete=True,
        src=str(row.get("src") or "history"),
        extensions=dict(extensions) if isinstance(extensions, dict) else {},
    )


def load_day_open_times(data_root: str, symbol: str, tf_s: int, day: str) -> set[int]:
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
                    logging.debug("SSOT_DAY_PARSE_FAIL path=%s", path)
                    continue
    except Exception:
        logging.debug("SSOT_DAY_READ_FAIL path=%s", path, exc_info=True)
        return out
    return out
