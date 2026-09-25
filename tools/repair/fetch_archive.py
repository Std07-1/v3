"""tools/repair/fetch_archive.py — архів брокера для щоденного settle (ADR-0103 S3a2): M1 вікна і нативний D1.

Лише читання: окремий логін FXCM через `FxcmHistoryProvider.fetch_range_rows` — єдина дорога до SDK з явним
PREVIOUS_CLOSE (= бар TV `FX:`, ADR-0100; AST-гейт `tests/test_fxcm_open_price_mode.py`). Формат — той, що читають
`settle_m1` (`<SYM_DIR>_m1.json` + `meta.symbols[].chunks`) і `d1_native_settle` (`<SYM_DIR>_d1_full.json`):
`[[open_ms, o, h, low, c, v], ...]` за зростанням, сирі значення брокера.

- M1: добові чанки [D 00:00, D+1 01:00) з перекриттям 1 год і п'ятничний зонд [Пт 18:00, Пн 01:00) — хвилину закриття
  тижня (Пт 20:44) архів віддає лише, коли date_to після відкриття наступного тижня (вимір 22.09). Помилку чанка з
  торговою хвилиною календаря гейт `settle_m1` рахує як відмову; тут вона — `errors_trading` і код виходу 1.
- D1: річні чанки назад від `--to` до 1990 або двох порожніх років поспіль — уся історія брокера (d1_native_settle
  володіє всіма устояними добами; неповна історія — відмова, а не «натив без року»).

Кожен виклик SDK — під `LoopWatchdog`: get_history буває зависає без дедлайну (24.09 — D1 SPX500 13 хв, інцидент
06.09), тож завислий виклик завершує процес кодом 75 (EX_TEMPFAIL), оркестратор повторює забір. Помилка SDK
повторюється `--attempts` разів. `meta.json` пишеться атомарно ОСТАННІМ і лише для чистого забору (інакше
`meta.failed.json`): без нього архів не приймає жоден споживач, обірваний забір не стає «порожнім архівом».

Python 3.7 (`.venv37` від smc; cwd — окремий каталог, SDK пише туди логи й лок):
    python -m tools.repair.fetch_archive m1 --out <dir> --from 2026-09-24T12:00 --to 2026-09-25T21:00 [--symbols ...]
    python -m tools.repair.fetch_archive d1 --out <dir> [--to 2026-09-25T21:05] [--symbols ...]
Креди — FXCM_* оточення; `--creds-from-sidecar` копіює їх з /proc/<pid broker_sidecar>/environ (той самий uid) у
оточення цього процесу. Значення не друкуються і не йдуть в argv.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import logging
import os
import sys
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from core.config_loader import load_system_config, m1_settle_policy, pick_config_path
from runtime.ingest.loop_watchdog import LoopWatchdog
from tools.repair.settle_gate import has_trading

log = logging.getLogger("fetch_archive")
UTC = dt.timezone.utc
TOOL = "fetch_archive/1"
M1_S, D1_S = 60, 86400
D1_FLOOR_YEAR = 1990  # найдавніша доба брокера (NAS100/XAU D1 з 1990, забір 23.09)
D1_EMPTY_YEARS_STOP = 2  # два порожні роки поспіль — історія символу скінчилась
RETRY_PAUSE_S = 1.0
EXIT_FETCH_FAILED = 1
SIDECAR_ARGV_MARKER = b"runtime.ingest.broker_sidecar"

FetchRange = Callable[[str, int, dt.datetime, dt.datetime], List[Tuple[Any, ...]]]


def parse_utc(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)


def to_ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def m1_chunks(t_from: dt.datetime, t_to: dt.datetime) -> List[Tuple[str, dt.datetime, dt.datetime]]:
    """[(label, start, end)] — добові чанки з перекриттям 1 год і п'ятничні зонди до Пн 01:00, усе в межах вікна."""
    chunks = []
    day = t_from.replace(hour=0, minute=0)
    while day < t_to:
        chunks.append(("day", max(day, t_from), min(day + dt.timedelta(days=1, hours=1), t_to)))
        if day.weekday() == 4 and day.replace(hour=18) < t_to:
            chunks.append(("fri_probe", max(day.replace(hour=18), t_from), min(day + dt.timedelta(days=3, hours=1), t_to)))
        day += dt.timedelta(days=1)
    return chunks


def _year_back(t: dt.datetime) -> dt.datetime:
    try:
        return t.replace(year=t.year - 1)
    except ValueError:  # 29 лютого
        return t.replace(year=t.year - 1, day=28)


def call_with_retries(fetch: FetchRange, attempts: int, symbol: str, tf_s: int, start: dt.datetime,
                      end: dt.datetime) -> Tuple[List[Tuple[Any, ...]], Optional[str]]:
    """(рядки, None) або ([], "Тип: текст") після `attempts` спроб — помилку SDK фіксує викликач, гучно."""
    error = None
    for attempt in range(1, attempts + 1):
        try:
            return fetch(symbol, tf_s, start, end), None
        except Exception as exc:  # noqa: BLE001 — SDK кидає власні типи; відмову рахує гейт архіву
            error = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            log.warning("FETCH_RETRY symbol=%s tf_s=%d %s..%s attempt=%d/%d %s", symbol, tf_s, start, end, attempt,
                        attempts, error)
            if attempt < attempts:
                time.sleep(RETRY_PAUSE_S)
    return [], error


def fetch_m1(fetch: FetchRange, attempts: int, symbol: str, t_from: dt.datetime, t_to: dt.datetime,
             is_trading: Callable[[int], bool]) -> Tuple[Dict[int, List[float]], Dict[str, Any]]:
    rows: Dict[int, List[float]] = {}
    meta: Dict[str, Any] = {"chunks": [], "errors_trading": 0, "errors_nontrading": 0, "overlap_conflicts": 0}
    for label, start, end in m1_chunks(t_from, t_to):
        rec: Dict[str, Any] = {"label": label, "start": start.isoformat(), "end": end.isoformat()}
        got, error = call_with_retries(fetch, attempts, symbol, M1_S, start, end)
        if error is not None:
            trading = has_trading(is_trading, to_ms(start), to_ms(end))
            rec["error"] = error
            meta["errors_trading" if trading else "errors_nontrading"] += 1
            (log.error if trading else log.info)("FETCH_M1_CHUNK_ERROR symbol=%s %s %s..%s trading=%s %s", symbol,
                                                 label, rec["start"][:16], rec["end"][:16], trading, error)
        conflicts = 0
        for r in got:
            key, vals = int(r[0]), [float(x) for x in r[1:6]]
            conflicts += int(key in rows and rows[key] != vals)
            rows[key] = vals
        rec.update(rows=len(got), overlap_conflicts=conflicts)
        meta["overlap_conflicts"] += conflicts
        meta["chunks"].append(rec)
    meta["m1_rows"] = len(rows)
    return rows, meta


def fetch_d1(fetch: FetchRange, attempts: int, symbol: str, t_to: dt.datetime) -> Tuple[Dict[int, List[float]], Dict[str, Any]]:
    rows: Dict[int, List[float]] = {}
    chunks: List[Dict[str, Any]] = []
    empty_run, end = 0, t_to
    while end.year > D1_FLOOR_YEAR and empty_run < D1_EMPTY_YEARS_STOP:
        start = _year_back(end)
        got, error = call_with_retries(fetch, attempts, symbol, D1_S, start, end)
        chunks.append({"start": start.isoformat(), "end": end.isoformat(), "rows": len(got), "error": error})
        if error is not None:
            log.error("FETCH_D1_CHUNK_ERROR symbol=%s %s..%s %s", symbol, start.date(), end.date(), error)
        for r in got:
            rows.setdefault(int(r[0]), [float(x) for x in r[1:6]])
        empty_run = empty_run + 1 if not got and error is None else 0
        end = start
    keys = sorted(rows)
    return rows, {"chunks": chunks, "chunk_errors": sum(1 for c in chunks if c["error"]), "bars": len(keys),
                  "first": keys[0] if keys else None, "last": keys[-1] if keys else None}


def load_sidecar_credentials(proc_root: str = "/proc") -> int:
    """FXCM_* з environ живого broker_sidecar у os.environ цього процесу; кількість ключів (значення не логуються).
    Пошук за argv у /proc, не pgrep: той матчить власну обгортку sudo/bash (пастка самозбігу)."""
    for pid in sorted(p for p in os.listdir(proc_root) if p.isdigit()):
        try:
            with open(os.path.join(proc_root, pid, "cmdline"), "rb") as fh:
                argv = fh.read()
            if SIDECAR_ARGV_MARKER not in argv or b"python" not in argv:
                continue
            with open(os.path.join(proc_root, pid, "environ"), "rb") as fh:
                items = [item.split(b"=", 1) for item in fh.read().split(b"\0") if b"=" in item]
        except OSError:
            continue
        creds = {k.decode(): v.decode() for k, v in items if k.startswith(b"FXCM_")}
        if creds:
            os.environ.update(creds)
            log.info("FETCH_CREDS source=broker_sidecar pid=%s keys=%d", pid, len(creds))
            return len(creds)
    raise RuntimeError("FETCH_CREDS_NOT_FOUND — немає живого broker_sidecar з FXCM_* (забір — до зупинки записувачів)")


@contextlib.contextmanager
def guarded_session(provider: Any, watchdog: LoopWatchdog) -> Iterator[FetchRange]:
    """Сесія FXCM, де login, кожен забір і logout — під watchdog (зависання → exit 75)."""

    def step(label: str, fn: Callable[[], Any]) -> Any:
        watchdog.enter(label)
        try:
            return fn()
        finally:
            watchdog.leave()

    step("login", provider.__enter__)
    try:
        yield lambda symbol, tf_s, start, end: step(
            "fetch %s tf=%d %s..%s" % (symbol, tf_s, start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")),
            lambda: provider.fetch_range_rows(symbol, tf_s, start, end))
    finally:
        step("logout", lambda: provider.__exit__(None, None, None))


def write_json_atomic(path: str, payload: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def run(kind: str, out: str, symbols: Sequence[str], fetch: FetchRange, attempts: int, t_to: dt.datetime,
        t_from: Optional[dt.datetime], calendars: Dict[str, Any], mode: str, fetched_at: dt.datetime) -> int:
    """Забір усіх символів у `out`; 0 — чистий архів (meta.json), EXIT_FETCH_FAILED — meta.failed.json."""
    meta: Dict[str, Any] = {"tool": TOOL, "kind": kind, "mode": mode, "fetched_at": fetched_at.isoformat(),
                            "fetched_at_ms": to_ms(fetched_at), "to": t_to.isoformat(), "symbols": {}}
    if kind == "m1":
        meta["window"] = [t_from.isoformat(), t_to.isoformat()]
    failed = []
    for symbol in symbols:
        sym_dir = symbol.replace("/", "_")
        if kind == "m1":
            rows, meta_sym = fetch_m1(fetch, attempts, symbol, t_from, t_to, calendars[symbol].is_trading_minute)
            bad = meta_sym["errors_trading"]
        else:
            rows, meta_sym = fetch_d1(fetch, attempts, symbol, t_to)
            bad = meta_sym["chunk_errors"]
        write_json_atomic(os.path.join(out, "%s_%s.json" % (sym_dir, "m1" if kind == "m1" else "d1_full")),
                          [[k] + rows[k] for k in sorted(rows)])
        meta["symbols"][sym_dir] = meta_sym
        failed += [sym_dir] if bad else []
        log.info("FETCH_%s symbol=%s rows=%d errors=%d", kind.upper(), symbol, len(rows), bad)
    meta["failed_symbols"] = failed
    write_json_atomic(os.path.join(out, "meta.failed.json" if failed else "meta.json"), meta)
    log.log(logging.ERROR if failed else logging.INFO, "FETCH_ARCHIVE_%s kind=%s out=%s failed=%s",
            "FAILED" if failed else "OK", kind, out, failed)
    return EXIT_FETCH_FAILED if failed else 0


def main(argv: Optional[List[str]] = None, provider_factory: Optional[Callable[[], Any]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=("m1", "d1"))
    ap.add_argument("--out", required=True, help="новий або порожній каталог архіву")
    ap.add_argument("--config", default=None)
    ap.add_argument("--from", dest="t_from", default=None, help="m1: UTC YYYY-MM-DDTHH:MM")
    ap.add_argument("--to", dest="t_to", default=None, help="UTC YYYY-MM-DDTHH:MM (d1 — типово зараз)")
    ap.add_argument("--symbols", nargs="*", default=None, help="типово — усі config.symbols")
    ap.add_argument("--creds-from-sidecar", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_system_config(args.config or pick_config_path())
    policy = m1_settle_policy(cfg)
    symbols = list(args.symbols or cfg["symbols"])
    unknown = sorted(set(symbols) - set(cfg["symbols"]))
    fetched_at = dt.datetime.now(UTC).replace(microsecond=0)
    t_to = parse_utc(args.t_to) if args.t_to else fetched_at
    t_from = parse_utc(args.t_from) if args.t_from else None
    if unknown or (args.kind == "m1" and (t_from is None or args.t_to is None or t_to <= t_from)):
        ap.error("символи поза config.symbols %s або m1 без вікна --from < --to" % unknown)
    if os.path.isdir(args.out) and os.listdir(args.out):
        ap.error("каталог %s не порожній — архіви різних заборів не змішуються" % args.out)
    os.makedirs(args.out, exist_ok=True)
    calendars: Dict[str, Any] = {}
    if args.kind == "m1":
        from runtime.ingest.tick_common import resolve_symbol_calendars
        calendars, rejected = resolve_symbol_calendars(cfg, symbols, where="fetch_archive")
        if rejected:
            ap.error("CALENDAR_MISSING %s" % rejected)
    if args.creds_from_sidecar:
        load_sidecar_credentials()
    from runtime.ingest.broker.fxcm import provider as provider_mod
    provider = (provider_factory or provider_mod.FxcmHistoryProvider.from_env)()
    watchdog = LoopWatchdog(policy.fetch_call_timeout_s)
    watchdog.start_thread(log=log)
    with guarded_session(provider, watchdog) as fetch:
        return run(args.kind, args.out, symbols, fetch, policy.fetch_attempts, t_to, t_from, calendars,
                   provider_mod.OPEN_PRICE_MODE_NAME, fetched_at)


if __name__ == "__main__":
    sys.exit(main())
