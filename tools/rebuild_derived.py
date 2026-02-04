from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from typing import Dict, Iterable, List, Optional, Set, Tuple

from v3_polling_b import (
    CandleBar,
    FxcmHistoryProvider,
    JsonlAppender,
    M1Buffer,
    select_anchor_offset_for_anchor_open_ms,
    derive_from_m1_for_anchor,
    floor_bucket_start_ms,
    head_first_bar_time_ms,
    iter_day_keys_utc,
    load_config,
    load_day_open_times,
    ms_to_utc_dt,
    tail_last_bar_time_ms,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def parse_iso_utc(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def default_state_path(config_path: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(config_path))
    return os.path.join(base_dir, "rebuild_state.json")


def load_rebuild_state(path: str) -> Dict[str, Dict[str, Dict[str, int]]]:
    if not os.path.isfile(path):
        return {"symbols": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict):
            return {"symbols": {}}
        symbols = obj.get("symbols")
        if not isinstance(symbols, dict):
            return {"symbols": {}}
        return {"symbols": symbols}
    except Exception:
        logging.warning("Rebuild: не вдалося прочитати state-файл: %s", path)
        return {"symbols": {}}


def save_rebuild_state(path: str, state: Dict[str, Dict[str, Dict[str, int]]]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        logging.warning("Rebuild: не вдалося зберегти state-файл: %s", path)


def iter_m1_bars(
    data_root: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> Iterable[CandleBar]:
    sym_dir = symbol.replace("/", "_")
    tf_dir = f"tf_{60}"
    for day in iter_day_keys_utc(start_ms, end_ms):
        path = os.path.join(data_root, sym_dir, tf_dir, f"part-{day}.jsonl")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    open_ms = obj.get("open_time_ms")
                    if not isinstance(open_ms, int):
                        continue
                    if open_ms < start_ms or open_ms > end_ms:
                        continue

                    o = float(obj.get("o"))
                    h = float(obj.get("h"))
                    low_val = obj.get("low", obj.get("l"))
                    low = float(low_val)
                    c = float(obj.get("c"))
                    v = float(obj.get("v", 0.0))
                    b = CandleBar(
                        symbol=symbol,
                        tf_s=60,
                        open_time_ms=open_ms,
                        close_time_ms=open_ms + 60_000,
                        o=o,
                        h=h,
                        low=low,
                        c=c,
                        v=v,
                        complete=True,
                        src="m1_disk",
                    )
                    yield b
        except Exception:
            logging.exception("Rebuild: помилка читання %s", path)


def has_on_disk(
    cache: Dict[str, set[int]],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> bool:
    day = ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")
    key = f"{tf_s}:{day}"
    idx = cache.get(key)
    if idx is None:
        idx = load_day_open_times(data_root, symbol, tf_s, day)
        cache[key] = idx
    return open_time_ms in idx


def mark_on_disk(
    cache: Dict[str, set[int]],
    data_root: str,
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> None:
    day = ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")
    key = f"{tf_s}:{day}"
    idx = cache.get(key)
    if idx is None:
        idx = load_day_open_times(data_root, symbol, tf_s, day)
        cache[key] = idx
    idx.add(open_time_ms)


def parse_tf_list(s: str) -> List[int]:
    out: List[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


def parse_tf_counts(s: str) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            tf_s_str, n_str = part.split(":", 1)
        elif "=" in part:
            tf_s_str, n_str = part.split("=", 1)
        else:
            raise ValueError("bad_tf_count")
        tf_s = int(tf_s_str.strip())
        n = int(n_str.strip())
        if tf_s <= 0 or n <= 0:
            raise ValueError("bad_tf_count")
        out[tf_s] = n
    return out


def parse_tf_offsets(s: str) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            tf_s_str, off_str = part.split(":", 1)
        elif "=" in part:
            tf_s_str, off_str = part.split("=", 1)
        else:
            raise ValueError("bad_tf_offset")
        tf_s = int(tf_s_str.strip())
        off = int(off_str.strip())
        if tf_s <= 0 or off < 0:
            raise ValueError("bad_tf_offset")
        out[tf_s] = off
    return out


def list_missing_m1(
    m1: M1Buffer,
    start_ms: int,
    end_ms: int,
    ignore_minutes_ms: Optional[Set[int]] = None,
) -> List[int]:
    missing: List[int] = []
    step = 60_000
    for t in range(start_ms, end_ms, step):
        if ignore_minutes_ms and t in ignore_minutes_ms:
            continue
        if t not in m1._by_open_ms:  # noqa: SLF001
            missing.append(t)
    return missing


def derive_from_m1_tolerant(
    symbol: str,
    tf_s: int,
    m1: M1Buffer,
    start_ms: int,
    end_ms: int,
    max_missing: int,
    ignore_minutes_ms: Optional[Set[int]] = None,
) -> Optional[CandleBar]:
    step = 60_000
    bars: List[CandleBar] = []
    missing = 0
    for t in range(start_ms, end_ms, step):
        if ignore_minutes_ms and t in ignore_minutes_ms:
            continue
        b = m1._by_open_ms.get(t)  # noqa: SLF001
        if b is None:
            missing += 1
            if missing > max_missing:
                return None
            continue
        bars.append(b)

    if not bars:
        return None

    o = bars[0].o
    c = bars[-1].c
    h = max(x.h for x in bars)
    low = min(x.low for x in bars)
    v = sum(x.v for x in bars)

    return CandleBar(
        symbol=symbol,
        tf_s=tf_s,
        open_time_ms=start_ms,
        close_time_ms=end_ms,
        o=o,
        h=h,
        low=low,
        c=c,
        v=v,
        complete=True,
        src="derived_partial",
    )


def rebuild_from_disk(
    data_root: str,
    symbol: str,
    tf_list: List[int],
    start_ms: int,
    end_ms: int,
    day_anchor_offset_s: int,
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
    day_anchor_offset_s_alt: Optional[int],
    day_anchor_offset_s_alt2: Optional[int],
    dry_run: bool,
    broker_backfill: bool,
    provider: Optional[FxcmHistoryProvider],
    tolerate_missing_minutes: int,
    ignore_minutes_ms: Optional[Set[int]] = None,
    ok_markers: Optional[Dict[int, int]] = None,
) -> Dict[int, int]:
    if end_ms < start_ms:
        logging.warning("Rebuild: end < start, нічого робити")
        return ok_markers or {}

    def _tolerant_partial_allowed(end_ms: int) -> bool:
        last_minute = ms_to_utc_dt(end_ms - 60_000)
        return last_minute.hour == 21 and last_minute.minute in (59, 44)

    range_minutes = int((end_ms - start_ms) // 60_000) + 10
    max_keep = max(2000, range_minutes)
    m1 = M1Buffer(max_keep=max_keep)
    writer = JsonlAppender(
        root=data_root,
        day_anchor_offset_s=day_anchor_offset_s,
        day_anchor_offset_s_d1=day_anchor_offset_s_d1,
        day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
    )
    cache: Dict[str, set[int]] = {}

    totals_written: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_existing: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_missing_m1: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_candidates: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_backfill_m1_written: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_backfill_tf_written: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_backfill_m1_found: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_backfill_tf_found: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_tolerated_missing: Dict[int, int] = {tf: 0 for tf in tf_list}
    totals_tolerated_partial: Dict[int, int] = {tf: 0 for tf in tf_list}
    attempted_missing: Set[Tuple[int, int]] = set()
    new_ok_markers: Dict[int, int] = dict(ok_markers or {})
    last_ok_end_ms_by_tf: Dict[int, Optional[int]] = {}
    allow_ok_advance_by_tf: Dict[int, bool] = {}
    for tf_s in tf_list:
        last_ok_end_ms_by_tf[tf_s] = (ok_markers or {}).get(tf_s)
        allow_ok_advance_by_tf[tf_s] = True

    def _update_ok_marker(tf_s: int, end_ms: int, bucket_ok: bool, bucket_failed: bool) -> None:
        if not allow_ok_advance_by_tf[tf_s]:
            return
        if bucket_ok:
            last_ok_end_ms_by_tf[tf_s] = end_ms
            prev = new_ok_markers.get(tf_s)
            if prev is None or end_ms > prev:
                new_ok_markers[tf_s] = end_ms
        elif bucket_failed:
            allow_ok_advance_by_tf[tf_s] = False

    m1_count = 0
    for b in iter_m1_bars(data_root, symbol, start_ms, end_ms):
        m1.upsert(b)
        m1_count += 1

        for tf_s in tf_list:
            anchor = select_anchor_offset_for_anchor_open_ms(
                tf_s,
                b.open_time_ms,
                day_anchor_offset_s,
                day_anchor_offset_s_alt,
                day_anchor_offset_s_alt2,
                day_anchor_offset_s_d1,
                day_anchor_offset_s_d1_alt,
            )
            tf_ms = tf_s * 1000
            b0 = floor_bucket_start_ms(b.open_time_ms, tf_s, anchor_offset_s=anchor)
            b1 = b0 + tf_ms
            if b.open_time_ms != (b1 - 60_000):
                continue

            prev_ok_end_ms = last_ok_end_ms_by_tf.get(tf_s)
            if prev_ok_end_ms is not None and b1 <= prev_ok_end_ms:
                continue

            totals_candidates[tf_s] += 1
            bucket_ok = False
            bucket_failed = False
            if has_on_disk(cache, data_root, symbol, tf_s, b0):
                totals_existing[tf_s] += 1
                _update_ok_marker(tf_s, b1, bucket_ok=True, bucket_failed=False)
                logging.debug(
                    "Rebuild: TF вже є tf=%ds open=%s",
                    tf_s,
                    ms_to_utc_dt(b0).isoformat(),
                )
                continue
            missing_list: List[int] = []
            if not m1.has_range_complete(b0, b1):
                missing_list = list_missing_m1(m1, b0, b1, ignore_minutes_ms)
            if missing_list:
                missing_count = len(missing_list)
                logging.debug(
                    "Rebuild: missing_m1 TF=%ds bucket=%s..%s missing=%s",
                    tf_s,
                    ms_to_utc_dt(b0).isoformat(),
                    ms_to_utc_dt(b1).isoformat(),
                    ",".join(ms_to_utc_dt(x).isoformat() for x in missing_list),
                )

                tolerate = tolerate_missing_minutes > 0 and missing_count <= tolerate_missing_minutes
                if tolerate:
                    totals_tolerated_missing[tf_s] += 1
                    logging.debug(
                        "Rebuild: tolerate_missing_m1 TF=%ds missing=%d<=%d",
                        tf_s,
                        missing_count,
                        tolerate_missing_minutes,
                    )

                if broker_backfill and provider is not None:
                    key = (tf_s, b0)
                    if key not in attempted_missing:
                        attempted_missing.add(key)
                        n = max(1, int(tf_s // 60))
                        date_to = ms_to_utc_dt(b1)
                        logging.debug(
                            "Rebuild: broker M1 запит n=%d date_to=%s",
                            n,
                            date_to.isoformat(),
                        )
                        bars = provider.fetch_last_n_m1(symbol, n=n, date_to_utc=date_to)
                        if not bars:
                            logging.debug("Rebuild: broker M1 порожній")
                        for mb in bars:
                            if mb.open_time_ms < b0 or mb.open_time_ms >= b1:
                                continue
                            totals_backfill_m1_found[tf_s] += 1
                            if has_on_disk(cache, data_root, symbol, 60, mb.open_time_ms):
                                logging.debug(
                                    "Rebuild: M1 вже є open=%s",
                                    ms_to_utc_dt(mb.open_time_ms).isoformat(),
                                )
                                continue
                            if not dry_run:
                                writer.append(mb)
                                mark_on_disk(cache, data_root, symbol, 60, mb.open_time_ms)
                            m1.upsert(mb)
                            totals_backfill_m1_written[tf_s] += 1
                            logging.debug(
                                "Rebuild: M1 дописано open=%s",
                                ms_to_utc_dt(mb.open_time_ms).isoformat(),
                            )

                if not m1.has_range_complete(b0, b1):
                    if tolerate:
                        if broker_backfill and provider is not None:
                            if not has_on_disk(cache, data_root, symbol, tf_s, b0):
                                date_to = ms_to_utc_dt(b1)
                                logging.debug(
                                    "Rebuild: broker TF запит tf=%ds date_to=%s",
                                    tf_s,
                                    date_to.isoformat(),
                                )
                                bars = provider.fetch_last_n_tf(
                                    symbol, tf_s=tf_s, n=1, date_to_utc=date_to
                                )
                                if not bars:
                                    logging.debug(
                                        "Rebuild: broker TF порожній tf=%ds",
                                        tf_s,
                                    )
                                if bars:
                                    tb = bars[-1]
                                    if tb.open_time_ms == b0:
                                        totals_backfill_tf_found[tf_s] += 1
                                        if not dry_run:
                                            writer.append(tb)
                                            mark_on_disk(cache, data_root, symbol, tf_s, b0)
                                        totals_backfill_tf_written[tf_s] += 1
                                        logging.debug(
                                            "Rebuild: TF дописано tf=%ds open=%s",
                                            tf_s,
                                            ms_to_utc_dt(b0).isoformat(),
                                        )
                                        bucket_ok = True
                                    else:
                                        logging.debug(
                                            "Rebuild: TF mismatch tf=%ds open=%s очікувалось=%s",
                                            tf_s,
                                            ms_to_utc_dt(tb.open_time_ms).isoformat(),
                                            ms_to_utc_dt(b0).isoformat(),
                                        )
                        if not m1.has_range_complete(b0, b1):
                            if not _tolerant_partial_allowed(b1):
                                logging.debug(
                                    "Rebuild: tolerant-partial пропущено (час=%s)",
                                    ms_to_utc_dt(b1 - 60_000).isoformat(),
                                )
                                bucket_failed = True
                                _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                                continue
                            d_partial = derive_from_m1_tolerant(
                                symbol=symbol,
                                tf_s=tf_s,
                                m1=m1,
                                start_ms=b0,
                                end_ms=b1,
                                max_missing=tolerate_missing_minutes,
                                ignore_minutes_ms=ignore_minutes_ms,
                            )
                            if d_partial is not None:
                                if not has_on_disk(cache, data_root, symbol, tf_s, b0):
                                    if not dry_run:
                                        writer.append(d_partial)
                                        mark_on_disk(cache, data_root, symbol, tf_s, b0)
                                    totals_tolerated_partial[tf_s] += 1
                                    logging.debug(
                                        "Rebuild: TF tolerated-partial tf=%ds open=%s missing=%d",
                                        tf_s,
                                        ms_to_utc_dt(b0).isoformat(),
                                        missing_count,
                                    )
                                bucket_ok = True
                                _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                                continue
                            bucket_failed = True
                            _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                    if not bucket_ok:
                        bucket_failed = True
                    _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                    continue

                    totals_missing_m1[tf_s] += 1
                    if broker_backfill and provider is not None:
                        if not has_on_disk(cache, data_root, symbol, tf_s, b0):
                            date_to = ms_to_utc_dt(b1)
                            logging.debug(
                                "Rebuild: broker TF запит tf=%ds date_to=%s",
                                tf_s,
                                date_to.isoformat(),
                            )
                            bars = provider.fetch_last_n_tf(
                                symbol, tf_s=tf_s, n=1, date_to_utc=date_to
                            )
                            if not bars:
                                logging.debug("Rebuild: broker TF порожній tf=%ds", tf_s)
                            if bars:
                                tb = bars[-1]
                                if tb.open_time_ms == b0:
                                    totals_backfill_tf_found[tf_s] += 1
                                    if not dry_run:
                                        writer.append(tb)
                                        mark_on_disk(cache, data_root, symbol, tf_s, b0)
                                    totals_backfill_tf_written[tf_s] += 1
                                    logging.debug(
                                        "Rebuild: TF дописано tf=%ds open=%s",
                                        tf_s,
                                        ms_to_utc_dt(b0).isoformat(),
                                    )
                                    bucket_ok = True
                                else:
                                    logging.debug(
                                        "Rebuild: TF mismatch tf=%ds open=%s очікувалось=%s",
                                        tf_s,
                                        ms_to_utc_dt(tb.open_time_ms).isoformat(),
                                        ms_to_utc_dt(b0).isoformat(),
                                    )
                    if not bucket_ok:
                        bucket_failed = True
                    _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                    continue

            if not m1.has_range_complete(b0, b1) and not missing_list:
                if not _tolerant_partial_allowed(b1):
                    logging.debug(
                        "Rebuild: ignored-m1 partial пропущено (час=%s)",
                        ms_to_utc_dt(b1 - 60_000).isoformat(),
                    )
                    bucket_failed = True
                    _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                    continue
                d_partial = derive_from_m1_tolerant(
                    symbol=symbol,
                    tf_s=tf_s,
                    m1=m1,
                    start_ms=b0,
                    end_ms=b1,
                    max_missing=0,
                    ignore_minutes_ms=ignore_minutes_ms,
                )
                if d_partial is not None:
                    if not has_on_disk(cache, data_root, symbol, tf_s, b0):
                        if not dry_run:
                            writer.append(d_partial)
                            mark_on_disk(cache, data_root, symbol, tf_s, b0)
                        totals_tolerated_partial[tf_s] += 1
                        logging.debug(
                            "Rebuild: TF ignored-m1 tf=%ds open=%s",
                            tf_s,
                            ms_to_utc_dt(b0).isoformat(),
                        )
                    bucket_ok = True
                    _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                    continue
                bucket_failed = True
                _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                continue

            d = derive_from_m1_for_anchor(
                symbol=symbol,
                tf_s=tf_s,
                m1=m1,
                anchor_open_ms=b.open_time_ms,
                anchor_offset_s=anchor,
            )
            if d is None:
                bucket_failed = True
                _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                continue

            if has_on_disk(cache, data_root, symbol, tf_s, d.open_time_ms):
                totals_existing[tf_s] += 1
                bucket_ok = True
                _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)
                continue

            if not dry_run:
                writer.append(d)
                mark_on_disk(cache, data_root, symbol, tf_s, d.open_time_ms)
            totals_written[tf_s] += 1
            bucket_ok = True
            _update_ok_marker(tf_s, b1, bucket_ok, bucket_failed)

    writer.close()

    logging.debug(
        "Rebuild: M1=%d діапазон [%s .. %s]",
        m1_count,
        ms_to_utc_dt(start_ms).isoformat(),
        ms_to_utc_dt(end_ms).isoformat(),
    )

    for tf_s in tf_list:
        logging.debug(
            "Rebuild TF=%ds: candidates=%d missing_m1=%d existing=%d written=%d backfill_m1=%d/%d backfill_tf=%d/%d dry_run=%s",
            tf_s,
            totals_candidates[tf_s],
            totals_missing_m1[tf_s],
            totals_existing[tf_s],
            totals_written[tf_s],
            totals_backfill_m1_written[tf_s],
            totals_backfill_m1_found[tf_s],
            totals_backfill_tf_written[tf_s],
            totals_backfill_tf_found[tf_s],
            dry_run,
        )
        if totals_tolerated_missing[tf_s]:
            logging.debug(
                "Rebuild TF=%ds: tolerated_missing=%d (threshold=%d)",
                tf_s,
                totals_tolerated_missing[tf_s],
                tolerate_missing_minutes,
            )
        if totals_tolerated_partial[tf_s]:
            logging.debug(
                "Rebuild TF=%ds: tolerated_partial=%d",
                tf_s,
                totals_tolerated_partial[tf_s],
            )

    total_candidates = sum(totals_candidates.values())
    total_missing_m1 = sum(totals_missing_m1.values())
    total_existing = sum(totals_existing.values())
    total_written = sum(totals_written.values())
    total_backfill_m1 = sum(totals_backfill_m1_written.values())
    total_backfill_tf = sum(totals_backfill_tf_written.values())
    total_tolerated_missing = sum(totals_tolerated_missing.values())
    total_tolerated_partial = sum(totals_tolerated_partial.values())
    logging.info(
        "Rebuild summary: tf=%s candidates=%d missing_m1=%d existing=%d written=%d backfill_m1=%d backfill_tf=%d tolerated_missing=%d tolerated_partial=%d dry_run=%s",
        ",".join(str(x) for x in tf_list),
        total_candidates,
        total_missing_m1,
        total_existing,
        total_written,
        total_backfill_m1,
        total_backfill_tf,
        total_tolerated_missing,
        total_tolerated_partial,
        dry_run,
    )

    return new_ok_markers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--tf", default=None, help="Один або кілька TF у секундах, напр. 180 або 180,300")
    ap.add_argument("--start-utc", default=None)
    ap.add_argument("--end-utc", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--broker-backfill", action="store_true")
    ap.add_argument("--broker-tf-counts", default=None, help="TF:count або TF=count, напр. 14400:300,86400:100")
    ap.add_argument("--broker-anchor-offset-s", default=None, help="Override anchor_offset_s для broker TF fetch")
    ap.add_argument("--broker-anchor-offsets", default=None, help="TF:offset або TF=offset, напр. 14400:68400,86400:75600")
    ap.add_argument("--state-path", default=None, help="Шлях до state-файлу rebuild міток")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except Exception:
        logging.exception("Rebuild: не вдалось завантажити config.json")
        return 2

    symbol = args.symbol or str(cfg.get("symbol", "XAU/USD"))
    data_root = str(cfg.get("data_root", "./data_v3"))
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
    tolerate_missing_minutes = int(cfg.get("derived_tolerate_missing_minutes", 0))
    ignore_minutes_ms: Set[int] = set()
    ignore_minutes_cfg = cfg.get("market_ignore_minutes_utc", [])
    if isinstance(ignore_minutes_cfg, list):
        for item in ignore_minutes_cfg:
            try:
                ts = parse_iso_utc(str(item))
                ignore_minutes_ms.add(int(ts.timestamp() * 1000))
            except Exception:
                logging.warning("Rebuild: ігнор хвилини має невірний формат: %s", item)

    if args.tf:
        tf_list = parse_tf_list(args.tf)
    else:
        tf_list = [int(x) for x in cfg.get("derived_tfs_s", [])]

    broker_base_raw = cfg.get("broker_base_tfs_s", [14400, 86400])
    broker_base_tfs_s = [int(x) for x in broker_base_raw]
    if broker_base_tfs_s:
        overlap = [tf for tf in tf_list if tf in broker_base_tfs_s]
        if overlap:
            logging.warning(
                "Rebuild: TF %s є broker_base_tfs_s, пропускаю. Використовуйте --broker-tf-counts.",
                ",".join(str(x) for x in overlap),
            )
            tf_list = [tf for tf in tf_list if tf not in broker_base_tfs_s]

    if not tf_list:
        logging.error("Rebuild: порожній список TF")
        return 2

    broker_tf_counts: Optional[Dict[int, int]] = None
    if args.broker_tf_counts:
        try:
            broker_tf_counts = parse_tf_counts(args.broker_tf_counts)
        except Exception:
            logging.error("Rebuild: неправильний формат --broker-tf-counts")
            return 2

    broker_tf_offsets: Optional[Dict[int, int]] = None
    if args.broker_anchor_offsets:
        try:
            broker_tf_offsets = parse_tf_offsets(args.broker_anchor_offsets)
        except Exception:
            logging.error("Rebuild: неправильний формат --broker-anchor-offsets")
            return 2

    start_ms: Optional[int]
    end_ms: Optional[int]

    if args.start_utc:
        start_ms = int(parse_iso_utc(args.start_utc).timestamp() * 1000)
    else:
        start_ms = head_first_bar_time_ms(data_root, symbol, tf_s=60)

    if args.end_utc:
        end_ms = int(parse_iso_utc(args.end_utc).timestamp() * 1000)
    else:
        end_ms = tail_last_bar_time_ms(data_root, symbol, tf_s=60)

    if start_ms is None or end_ms is None:
        logging.error("Rebuild: немає M1 на диску")
        return 2

    if broker_tf_counts:
        try:
            user_id = str(cfg["user_id"])
            password = str(cfg["password"])
            url = str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
            connection = str(cfg.get("connection", "Demo"))
        except Exception:
            logging.exception("Rebuild: немає доступу до FXCM конфігу")
            return 2

        default_anchor_offset_s = day_anchor_offset_s
        if args.broker_anchor_offset_s is not None:
            try:
                default_anchor_offset_s = int(args.broker_anchor_offset_s)
            except Exception:
                logging.error("Rebuild: неправильний --broker-anchor-offset-s")
                return 2
        default_anchor_offset_s_d1 = day_anchor_offset_s_d1
        default_anchor_offset_s_d1_alt = day_anchor_offset_s_d1_alt
        if broker_tf_offsets and 86400 in broker_tf_offsets:
            default_anchor_offset_s_d1 = broker_tf_offsets[86400]

        writers_by_offset: Dict[int, JsonlAppender] = {}
        cache: Dict[str, set[int]] = {}
        totals_written: Dict[int, int] = {}
        totals_existing: Dict[int, int] = {}
        totals_found: Dict[int, int] = {}
        totals_offset: Dict[int, int] = {}

        with FxcmHistoryProvider(
            user_id=user_id,
            password=password,
            url=url,
            connection=connection,
            day_anchor_offset_s=default_anchor_offset_s,
            day_anchor_offset_s_d1=default_anchor_offset_s_d1,
            day_anchor_offset_s_d1_alt=default_anchor_offset_s_d1_alt,
            day_anchor_offset_s_alt=day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
        ) as provider:
            date_to = dt.datetime.now(dt.timezone.utc)
            for tf_s, n in broker_tf_counts.items():
                totals_written[tf_s] = 0
                totals_existing[tf_s] = 0
                totals_found[tf_s] = 0
                if broker_tf_offsets and tf_s in broker_tf_offsets:
                    tf_offset_s = broker_tf_offsets[tf_s]
                elif tf_s == 86400 and default_anchor_offset_s_d1 is not None:
                    tf_offset_s = default_anchor_offset_s_d1
                else:
                    tf_offset_s = default_anchor_offset_s
                totals_offset[tf_s] = tf_offset_s
                writer = writers_by_offset.get(tf_offset_s)
                if writer is None:
                    writer = JsonlAppender(
                        root=data_root,
                        day_anchor_offset_s=tf_offset_s,
                        day_anchor_offset_s_d1=default_anchor_offset_s_d1,
                        day_anchor_offset_s_d1_alt=default_anchor_offset_s_d1_alt,
                        day_anchor_offset_s_alt=day_anchor_offset_s_alt,
                        day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
                    )
                    writers_by_offset[tf_offset_s] = writer
                bars = provider.fetch_last_n_tf(symbol, tf_s=tf_s, n=n, date_to_utc=date_to)
                if not bars:
                    logging.debug("Broker TF: порожньо tf=%ds", tf_s)
                    continue
                for b in bars:
                    totals_found[tf_s] += 1
                    if has_on_disk(cache, data_root, symbol, tf_s, b.open_time_ms):
                        totals_existing[tf_s] += 1
                        continue
                    if not args.dry_run:
                        writer.append(b)
                        mark_on_disk(cache, data_root, symbol, tf_s, b.open_time_ms)
                    totals_written[tf_s] += 1

        for w in writers_by_offset.values():
            w.close()

        for tf_s, n in broker_tf_counts.items():
            logging.info(
                "Broker TF summary: tf=%ds requested=%d found=%d written=%d existing=%d anchor_offset_s=%d dry_run=%s",
                tf_s,
                n,
                totals_found.get(tf_s, 0),
                totals_written.get(tf_s, 0),
                totals_existing.get(tf_s, 0),
                totals_offset.get(tf_s, default_anchor_offset_s),
                bool(args.dry_run),
            )
        return 0

    state_path = args.state_path or default_state_path(args.config)
    state = load_rebuild_state(state_path)
    sym_state = state.get("symbols", {}).get(symbol, {})
    ok_markers: Dict[int, int] = {}
    if isinstance(sym_state, dict):
        for k, v in sym_state.items():
            try:
                ok_markers[int(k)] = int(v)
            except Exception:
                continue

    logging.debug(
        "Rebuild: старт symbol=%s tf=%s dry_run=%s",
        symbol,
        ",".join(str(x) for x in tf_list),
        args.dry_run,
    )

    if args.broker_backfill:
        try:
            user_id = str(cfg["user_id"])
            password = str(cfg["password"])
            url = str(cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp"))
            connection = str(cfg.get("connection", "Demo"))
        except Exception:
            logging.exception("Rebuild: немає доступу до FXCM конфігу")
            return 2

        with FxcmHistoryProvider(
            user_id=user_id,
            password=password,
            url=url,
            connection=connection,
            day_anchor_offset_s=day_anchor_offset_s,
            day_anchor_offset_s_d1=day_anchor_offset_s_d1,
            day_anchor_offset_s_alt=day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
        ) as provider:
            new_ok = rebuild_from_disk(
                data_root=data_root,
                symbol=symbol,
                tf_list=tf_list,
                start_ms=start_ms,
                end_ms=end_ms,
                day_anchor_offset_s=day_anchor_offset_s,
                day_anchor_offset_s_d1=day_anchor_offset_s_d1,
                day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
                day_anchor_offset_s_alt=day_anchor_offset_s_alt,
                day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
                dry_run=bool(args.dry_run),
                broker_backfill=True,
                provider=provider,
                tolerate_missing_minutes=tolerate_missing_minutes,
                ignore_minutes_ms=ignore_minutes_ms or None,
                ok_markers=ok_markers,
            )
    else:
        new_ok = rebuild_from_disk(
            data_root=data_root,
            symbol=symbol,
            tf_list=tf_list,
            start_ms=start_ms,
            end_ms=end_ms,
            day_anchor_offset_s=day_anchor_offset_s,
            day_anchor_offset_s_d1=day_anchor_offset_s_d1,
            day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
            day_anchor_offset_s_alt=day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
            dry_run=bool(args.dry_run),
            broker_backfill=False,
            provider=None,
            tolerate_missing_minutes=tolerate_missing_minutes,
            ignore_minutes_ms=ignore_minutes_ms or None,
            ok_markers=ok_markers,
        )

    if not args.dry_run:
        symbols_state = state.setdefault("symbols", {})
        sym_state_out: Dict[str, int] = {}
        for tf_s, end_ms in new_ok.items():
            sym_state_out[str(tf_s)] = int(end_ms)
        symbols_state[symbol] = sym_state_out
        save_rebuild_state(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
