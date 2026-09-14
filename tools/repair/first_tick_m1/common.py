"""Спільні константи і утиліти first_tick_m1 (ADR-0096 §3.3 B). Python 3.7, без платформних залежностей."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from runtime.ingest.tick_common import symbols_from_cfg

logger = logging.getLogger("first_tick_m1")

TOOL_VERSION = 1
TF_S = 60
MINUTE_MS = 60_000
DAY_MS = 86_400_000
# Запит доби D: date_from = D − 60 с, date_to = D + 1 доба; рядки поза [D, D+1d) відкидаються і рахуються.
# Включність меж get_history SDK не задокументована (forexconnect/ForexConnect.py:417) — відступ знімає
# неоднозначність, а пілот доводить повноту (MISSING_IN_STAGING≈0, EXTRA_IN_STAGING≈0 на повній добі).
REQUEST_MARGIN_BEFORE_S = 60
MAX_CALLS_DEFAULT = 30  # логінів за прогін: один вихідний пакет, ліміт частоти логінів брокера не виміряно
MAX_CALLS_CEILING = 300
CALL_TIMEOUT_DEFAULT_S = 180  # get_history — синхронний нативний виклик без таймауту (ADR-0054 §3.6)
CALL_TIMEOUT_RANGE_S = (30, 900)
KILL_WAIT_S = 15
CALL_INTERVAL_DEFAULT_S = 5  # пауза між логінами; нуль заборонено — частоту логінів брокера не виміряно
CALL_INTERVAL_RANGE_S = (1, 600)
# Рейка закритого ринку: з 60 хв не пропускає денну перерву 21:00–22:00 і покриває зимовий зсув +1 год
# (календар cfd_us_22_23 лише літній, ADR-0095) — фактично лише вихідні Пт≥21:45 … Нд≤~20:56 UTC.
GUARD_MINUTES_DEFAULT = 60
GUARD_MINUTES_RANGE = (0, 240)
APPLY_GUARD_MINUTES_DEFAULT = 30
# Доба молодша за тиждень не забирається: «запечений» FIRST_TICK бачили саме на поточному тижні, минулі
# тижні — справжні (ADR-0096 §1.4); свіжу добу план однаково відсік би як SKIP_BAKED — виклик згорів би.
MIN_AGE_DAYS_DEFAULT = 7
MIN_AGE_DAYS_RANGE = (0, 60)
MAX_CONSECUTIVE_FAILURES_DEFAULT = 3
CLOSE_EPS_DEFAULT = 1e-9
CLOSE_EPS_MAX = 1e-6  # нижче за найменший крок ціни 5 символів (0.001): більший eps пропустив би інший close
# «Запечений» проміжок: o == prev_c у 100% хвилин; справжній FIRST_TICK — 0/60; Binance — 52% (§1.3–1.4).
SUSPECT_EQ_PREV_SHARE = 0.9
SUSPECT_MIN_ROWS = 30
PLAN_MAX_DAYS = 400
VERIFY_WINDOW_LIMIT = 2000  # > 1440 хвилин доби: вікно читача ніколи не обрізає добу

REPO_ROOT = Path(__file__).resolve().parents[3]


def sym_dir(symbol: str) -> str:
    return symbol.replace("/", "_")


def parse_day(text: str) -> dt.date:
    """`YYYY-MM-DD` з CLI."""
    return dt.datetime.strptime(text, "%Y-%m-%d").date()


def parse_day_key(text: str) -> dt.date:
    """`YYYYMMDD` з імен файлів і маніфестів."""
    return dt.datetime.strptime(text, "%Y%m%d").date()


def day_key(day: dt.date) -> str:
    return day.strftime("%Y%m%d")


def day_start_ms(day: dt.date) -> int:
    return int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp()) * 1000


def day_of_ms(open_ms: int) -> dt.date:
    """UTC-доба мітки часу — так само, як `ssot_jsonl` обирає part-файл."""
    return dt.datetime.fromtimestamp(open_ms // 1000, tz=dt.timezone.utc).date()


def days_between(first: dt.date, last: dt.date) -> List[dt.date]:
    return [first + dt.timedelta(days=offset) for offset in range((last - first).days + 1)]


def utc_iso(open_ms: int) -> str:
    """Час у форматі курсора `tools.fetch_tf_backfill` — один формат дат у звітах ремонтних інструментів.

    Імпорт ледачий: модуль інструмента засіву тягне провайдер і JsonlAppender, а `common` імпортує кожна фаза.
    """
    from tools.fetch_tf_backfill import CURSOR_FMT

    return dt.datetime.fromtimestamp(open_ms / 1000, tz=dt.timezone.utc).strftime(CURSOR_FMT)


def now_utc_iso() -> str:
    return utc_iso(int(time.time() * 1000))


def request_window(day: dt.date) -> Dict[str, Any]:
    """Запит однієї доби D до get_history: [D − REQUEST_MARGIN_BEFORE_S, D + 1 доба], усі рядки (-1)."""
    start = day_start_ms(day)
    return {"date_from_utc": utc_iso(start - REQUEST_MARGIN_BEFORE_S * 1000), "date_to_utc": utc_iso(start + DAY_MS),
            "quotes_count": -1}


def resolve_data_root(cfg: Dict[str, Any], override: Optional[str]) -> str:
    """Абсолютний realpath кореня SSOT; відносний шлях — від кореня репо, не від cwd (fetch іде з іншого cwd)."""
    raw = override if override else str(cfg.get("data_root") or "./data_v3")
    path = raw if os.path.isabs(raw) else os.path.join(str(REPO_ROOT), raw)
    return os.path.realpath(path)


def norm_path(path: Any) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def paths_overlap(first: Any, second: Any) -> bool:
    """Шляхи рівні або один усередині іншого (після realpath)."""
    a, b = norm_path(first), norm_path(second)
    try:
        common = os.path.commonpath([a, b])
    except ValueError:  # різні диски Windows — перетину немає
        return False
    return common in (a, b)


def path_within(path: Any, root: Any) -> bool:
    """Шлях (після realpath) дорівнює кореню або лежить усередині нього."""
    inner, outer = norm_path(path), norm_path(root)
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:  # різні диски Windows
        return False


def fxcm_symbol_problem(cfg: Dict[str, Any], symbol: str) -> Optional[str]:
    """Чому символ не є FXCM-символом конфігу (None — є). Binance-символи пише інший записувач."""
    if symbol not in symbols_from_cfg(cfg):
        return "not_in_config_symbols"
    binance = cfg.get("binance")
    if isinstance(binance, dict) and symbol in (binance.get("symbols") or []):
        return "binance_symbol"
    return None


def first_trading_minute(calendar: Any, start_ms: int, end_ms: int) -> Optional[int]:
    """Перша торгова за календарем хвилина у [floor(start), end] або None — ринок закритий усе вікно."""
    minute = start_ms // MINUTE_MS * MINUTE_MS
    while minute <= end_ms:
        if calendar.is_trading_minute(minute):
            return minute
        minute += MINUTE_MS
    return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Any) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (text + "\n").encode("utf-8")


def write_json_atomic(path: Any, obj: Any) -> None:
    """Канонічний JSON через .tmp + fsync + один os.replace: читач бачить старий або новий файл, не половину."""
    tmp = "%s.tmp" % path
    with open(tmp, "wb") as fh:
        fh.write(canonical_json_bytes(obj))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, str(path))


def create_json_exclusive(path: Any, obj: Any) -> None:
    """Канонічний JSON у НОВИЙ файл (O_CREAT|O_EXCL + fsync); файл уже є — FileExistsError, байти не зачеплено."""
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(canonical_json_bytes(obj))
        fh.flush()
        os.fsync(fh.fileno())


def read_json(path: Any) -> Any:
    with open(str(path), "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def log_event(level: int, code: str, **fields: Any) -> str:
    """Подія з кодом англійським ідентифікатором і полями `k=v`; повертає текст (для stop_reason/звітів)."""
    text = " ".join([code] + ["%s=%s" % (key, value) for key, value in fields.items()])
    logger.log(level, "%s", text)
    return text


class LockHeld(RuntimeError):
    def __init__(self, path: str, holder: str) -> None:
        super().__init__("lock held: %s holder=%s" % (path, holder))
        self.path = path
        self.holder = holder


@contextlib.contextmanager
def exclusive_lock(path: str) -> Iterator[None]:
    """Лок O_CREAT|O_EXCL з {pid, host, started_at_utc}; зайнятий — LockHeld (зняття — вручну після перевірки pid)."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise LockHeld(path, _read_holder(path))
    with os.fdopen(fd, "wb") as fh:
        fh.write(canonical_json_bytes({"pid": os.getpid(), "host": socket.gethostname(),
                                       "started_at_utc": now_utc_iso()}))
        fh.flush()
        os.fsync(fh.fileno())
    try:
        yield
    finally:
        try:
            os.remove(path)
        except OSError as exc:
            log_event(logging.ERROR, "FT_LOCK_RELEASE_FAILED", path=path, err=exc)


def _read_holder(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace").strip()
    except OSError as exc:
        return "<unreadable: %s>" % exc


# Сигнали, якими оператор чи supervisor зупиняють процес; SIGHUP — закрита SSH-сесія (на Windows його немає).
STOP_SIGNAL_NAMES = ("SIGTERM", "SIGHUP", "SIGINT")


class StopSignal(BaseException):
    """Сигнал зупинки як виняток: робота переривається тим самим шляхом фіналізації, що й будь-яка відмова.

    BaseException, а не Exception: гілки `except Exception` посеред роботи не мають права його проковтнути.
    """

    def __init__(self, signum: int) -> None:
        super().__init__("signal %d" % signum)
        self.signum = signum

    @property
    def exit_code(self) -> int:
        """Код виходу за конвенцією shell для процесу, зупиненого сигналом."""
        return 128 + self.signum


class StopSignals:
    """Обробники SIGTERM/SIGHUP/SIGINT на час роботи, що пише стан (маніфест, staging).

    Перший сигнал піднімає StopSignal у головному потоці. Після цього (або після `disarm`, коли почалась
    фіналізація) сигнали лише фіксуються в лозі: другий Ctrl+C не має права обірвати запис маніфесту. Поза
    головним потоком обробники поставити неможливо — це видно в лозі, а не мовчки.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.armed = True
        self.received: Optional[int] = None
        self._previous: Dict[int, Any] = {}

    def __enter__(self) -> "StopSignals":
        if threading.current_thread() is not threading.main_thread():
            log_event(logging.WARNING, self.prefix + "_SIGNAL_HANDLERS_UNAVAILABLE", reason="not_main_thread")
            return self
        for name in STOP_SIGNAL_NAMES:
            signum = getattr(signal, name, None)
            if signum is not None:
                self._previous[signum] = signal.signal(signum, self._handle)
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        for signum, previous in self._previous.items():
            signal.signal(signum, previous if previous is not None else signal.SIG_DFL)
        self._previous = {}
        return False

    def disarm(self) -> None:
        """Почалась фіналізація: далі сигнали не перериваються, а фіксуються."""
        self.armed = False

    def _handle(self, signum: int, frame: Any) -> None:
        if self.received is None:
            self.received = signum
        if self.armed:
            self.armed = False
            raise StopSignal(signum)
        log_event(logging.WARNING, self.prefix + "_SIGNAL_DEFERRED", signal=signum)
