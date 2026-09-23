"""D1 до епохи M1 — нативний D1 брокера (FXCM PREVIOUS_CLOSE = TV `FX:` D1) на всю глибину історії.

Рішення власника 23.09.2026: «для кожного активу має бути максимальна історія 1D, вся ідеальна, 1:1 з TV». Епоха M1
символу (перший повний D1-бакет, покритий M1 на диску) — зона S7 (D1 = агрегат відремонтованого M1); усе раніше —
тут: ключ і значення нативного D1 брокера з архіву `tools/fetch_d1_history` (`<SYM>_d1_full.json` + `meta.json`).

Правила рядків D1 свого символу з ключем раніше за епоху M1:
- ключ є в нативі: значення = натив (REPLACE, якщо відрізняється; SAME — не пишеться);
- нативного ключа немає у файлах: INSERT (рядок писаря SSOT, `src=history`, маркер `extensions.settled`);
- наш ключ у межах нативної історії, якого натив не має: поза сіткою — REMOVE (той самий торговий день натив має на
  ключі сітки: старий сід із фіксованим 21:00 узимку); на сітці — KEEP з гучним звітом (NOT_IN_NATIVE);
- наш ключ раніше за першу нативну добу (брокер цієї давнини вже не віддає): на сітці — KEEP (максимальна історія),
  поза сіткою — REKEY на ключ сітки тієї самої UTC-доби (значення без змін), якщо він вільний, інакше REMOVE;
- вихідний огризок (`weekend_stub_keys`: бакет, що починається в Пт/Сб UTC, без жодної торгової хвилини календаря
  символу, у тижні з недільною сесією брокера) — не вставляється, свій рядок на цьому ключі — REMOVE. TV таких барів
  не має: XAU тік нд 08.03.2026 17:22 (v=1) брокер кладе в окремий D1, а TV показує п'ятницю і неділю з o=5171.76 =
  close огризка (перевірено 23.09 через JS графіка). Бари старої конвенції (ключі Пн–Пт, тиждень без неділі) лишаються.
Рядки епохи M1, чужі, нерозбірні й порожні — байт у байт. Кожен новий/замінений рядок проходить інваріант бару і
`assert_on_season_grid`. Запис — лише з `--apply` при доведено зупинених записувачах (`writers_guard`), tgz-бекап із
sha-маніфестом до запису, заміна part-файла зі старим inode в `_backup_adr0095_<stamp>`, повторний план = 0 дій.

    python -m tools.repair.d1_native_settle --data-root data_v3 --archive <dir> [--symbols XAU/USD ...]
        [--apply --backup-dir <поза data_root>] [--report out.json]
"""

from __future__ import annotations

import argparse
import collections
import bisect
import datetime as dt
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.config_loader import htf_anchor_rule_resolver, load_system_config, pick_config_path
from core.model.bars import CandleBar, assert_invariants
from core.session_anchor import (
    assert_on_season_grid, htf_anchor_offset_s, htf_bucket_start_ms, htf_next_bucket_start_ms,
)
from runtime.ingest.tick_common import calendar_for_symbol
from tools.repair.partfile_io import (
    Line, PartFile, backup_files, day_of_ms, list_part_days, load_part, part_path, replace_part, row_bytes, sha256_hex,
    utc_stamp, writers_guard,
)

log = logging.getLogger("d1_native_settle")
D1_S = 86_400
M1_S = 60
TOOL = "d1_native_settle/2"

ACT_SAME, ACT_REPLACE, ACT_INSERT, ACT_REMOVE, ACT_REKEY, ACT_KEEP = (
    "same", "replace", "insert", "remove_off_grid", "rekey", "keep")
ACT_REMOVE_STUB = "remove_weekend_stub"
_WEEKEND_WEEKDAYS = (4, 5)  # Пт, Сб — день UTC ключа D1, з якого починається бакет вихідних
_WEEK_MS = 7 * 86_400_000


@dataclass
class SymbolPlan:
    symbol: str
    sym_dir: str
    era_ms: int
    native_first_ms: int
    files: Dict[str, bytes] = field(default_factory=dict)  # path → нові байти (лише змінені файли)
    sources: Dict[str, PartFile] = field(default_factory=dict)
    counts: collections.Counter = field(default_factory=collections.Counter)
    samples: Dict[str, List[str]] = field(default_factory=lambda: collections.defaultdict(list))


def fmt(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%a %Y-%m-%d %H:%M")


def load_native(archive_dir: str, sym_dir: str) -> Tuple[Dict[int, List[float]], Dict[str, Any]]:
    """Нативний D1 символу з архіву забору і meta; режим мусить бути PREVIOUS_CLOSE (контракт TV, ADR-0100)."""
    meta = json.load(open(os.path.join(archive_dir, "meta.json"), encoding="utf-8"))
    if meta.get("mode") != "PREVIOUS_CLOSE":
        raise ValueError("D1_NATIVE_ARCHIVE_MODE mode=%r — очікується PREVIOUS_CLOSE" % meta.get("mode"))
    rows = json.load(open(os.path.join(archive_dir, "%s_d1_full.json" % sym_dir), encoding="utf-8"))
    return {int(r[0]): [float(x) for x in r[1:6]] for r in rows}, meta


def m1_era_start_ms(data_root: str, sym_dir: str, rule: str) -> Optional[int]:
    """Перший D1-бакет, повністю покритий M1 на диску (відкриття бакета ≥ першої M1); None — M1 немає."""
    for day in list_part_days(data_root, sym_dir, M1_S):
        keys = [ln.own_key for ln in load_part(part_path(data_root, sym_dir, M1_S, day), sym_dir).lines
                if ln.own_key is not None]
        if keys:
            first = min(keys)
            bucket = htf_bucket_start_ms(first, D1_S, rule)
            return bucket if bucket == first else htf_next_bucket_start_ms(bucket, D1_S, rule)
    return None


def native_bar(symbol: str, open_ms: int, vals: List[float], provenance: str) -> CandleBar:
    bar = CandleBar(symbol=symbol, tf_s=D1_S, open_time_ms=open_ms, close_time_ms=open_ms + D1_S * 1000, o=vals[0],
                    h=vals[1], low=vals[2], c=vals[3], v=vals[4], complete=True, src="history",
                    extensions={"settled": provenance})
    return bar


def validated(bar: CandleBar, rule: str) -> CandleBar:
    """Та сама перевірка, що в писаря SSOT для H4/D1 (`ssot_jsonl`): сітка сезону, потім інваріант з якорем доби."""
    assert_on_season_grid(bar.open_time_ms, D1_S, rule)
    assert_invariants(bar, anchor_offset_s=htf_anchor_offset_s(D1_S, bar.open_time_ms, rule))
    return bar


def _weekday(ms: int) -> int:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).weekday()


def weekend_stub_keys(candidates, native_keys, rule: str, is_trading) -> set:
    """Ключі вихідного огризка серед `candidates`: бакет D1 починається в Пт/Сб UTC, жодної торгової хвилини календаря
    в ньому немає, а за 6 діб до ключа брокер має недільний ключ (сучасна конвенція Нд–Чт). Стара конвенція індексів
    (ключі Пн–Пт на кінці сесії, 1990–2008) недільних ключів не має — її п'ятниці не огризки."""
    sundays = sorted(k for k in native_keys if _weekday(k) == 6)
    stubs = set()
    for k in candidates:
        if _weekday(k) not in _WEEKEND_WEEKDAYS:
            continue
        i = bisect.bisect_left(sundays, k)
        if i == 0 or k - sundays[i - 1] >= _WEEK_MS:
            continue
        if not any(is_trading(t) for t in range(k, htf_next_bucket_start_ms(k, D1_S, rule), M1_S * 1000)):
            stubs.add(k)
    return stubs


def plan_symbol(data_root: str, symbol: str, rule: str, native: Dict[int, List[float]], provenance: str,
                era_ms: Optional[int], is_trading) -> SymbolPlan:
    sym_dir = symbol.replace("/", "_")
    native_first = min(native)
    era = era_ms if era_ms is not None else max(native) + 1
    plan = SymbolPlan(symbol=symbol, sym_dir=sym_dir, era_ms=era, native_first_ms=native_first)
    days = set(list_part_days(data_root, sym_dir, D1_S))
    days |= {day_of_ms(k) for k in native if k < era}
    stubs = weekend_stub_keys([k for k in native if k < era], native, rule, is_trading)
    if stubs:
        plan.counts["native_weekend_stub_skipped"] = len(stubs)
        plan.samples["native_weekend_stub_skipped"] = [fmt(k) for k in sorted(stubs)[-5:]]
    target_by_day: Dict[str, Dict[int, bytes]] = collections.defaultdict(dict)
    for k, vals in native.items():
        if k < era and k not in stubs:
            target_by_day[day_of_ms(k)][k] = row_bytes(validated(native_bar(symbol, k, vals, provenance), rule))
    # ключі, зайняті на диску будь-де (для REKEY: не наїхати на наявний рядок іншої доби-файла)
    occupied = set()
    for day in sorted(days):
        path = part_path(data_root, sym_dir, D1_S, day)
        part = load_part(path, sym_dir)
        plan.sources[path] = part
        occupied |= {ln.own_key for ln in part.lines if ln.own_key is not None}
    own_keys = {ln.own_key for part in plan.sources.values() for ln in part.lines
                if ln.own_key is not None and native_first <= ln.own_key < era}
    stubs |= weekend_stub_keys(own_keys - set(native), native, rule, is_trading)
    rekeys: Dict[str, Dict[int, bytes]] = collections.defaultdict(dict)
    decisions: Dict[Tuple[str, int], str] = {}
    for path, part in plan.sources.items():
        for i, ln in enumerate(part.lines):
            key = ln.own_key
            if key is None or key >= era:
                continue
            on_grid = htf_bucket_start_ms(key, D1_S, rule) == key
            if key in stubs:
                act = ACT_REMOVE_STUB
            elif key in native:
                continue  # SAME/REPLACE — нижче, за ціллю доби
            elif key >= native_first:
                act = ACT_KEEP if on_grid else ACT_REMOVE
            elif on_grid:
                act = ACT_KEEP
            else:
                new_key = htf_bucket_start_ms(key + 2 * 3_600_000, D1_S, rule)
                if day_of_ms(new_key) == day_of_ms(key) and new_key not in occupied and new_key not in native:
                    obj = dict(ln.obj)
                    obj["open_time_ms"], obj["close_time_ms"] = new_key, new_key + D1_S * 1000
                    moved = CandleBar(symbol=symbol, tf_s=D1_S, open_time_ms=new_key, close_time_ms=new_key + D1_S * 1000,
                                      o=float(obj["o"]), h=float(obj["h"]), low=float(obj["low"]), c=float(obj["c"]),
                                      v=float(obj["v"]), complete=True, src=str(obj.get("src") or "history"),
                                      extensions={**(obj.get("extensions") or {}), "rekeyed_from": key})
                    rekeys[day_of_ms(new_key)][new_key] = row_bytes(validated(moved, rule))
                    occupied.add(new_key)
                    act = ACT_REKEY
                else:
                    act = ACT_REMOVE
            decisions[(path, i)] = act
            plan.counts[act if act != ACT_KEEP else ("keep_before_native" if key < native_first else "keep_not_in_native")] += 1
            if len(plan.samples[act]) < 5:
                plan.samples[act].append(fmt(key))
    for day in sorted(days):
        path = part_path(data_root, sym_dir, D1_S, day)
        part = plan.sources[path]
        want = dict(target_by_day.get(day, {}))
        want.update(rekeys.get(day, {}))
        new_lines = _rebuild_lines(part, want, decisions, path, era, plan)
        new_bytes = b"".join(ln.body + ln.eol for ln in new_lines)
        if new_bytes != part.to_bytes():
            plan.files[path] = new_bytes
    return plan


def _rebuild_lines(part: PartFile, want: Dict[int, bytes], decisions: Dict[Tuple[str, int], str], path: str,
                   era: int, plan: SymbolPlan) -> List[Line]:
    eol = part.eol_style()
    out: List[Line] = []
    emitted = set()
    for i, ln in enumerate(part.lines):
        key = ln.own_key
        if key is None or key >= era:
            out.append(ln)
            continue
        act = decisions.get((path, i))
        if act in (ACT_REMOVE, ACT_REKEY, ACT_REMOVE_STUB):
            continue
        if act == ACT_KEEP:
            out.append(ln)
            continue
        body = want.get(key)
        if body is None or key in emitted:  # повтор ключа — лишається перший, як у вибирача читача
            plan.counts["duplicate_removed"] += 1
            continue
        emitted.add(key)
        if ln.body == body or _same_values(ln.obj, body):
            plan.counts[ACT_SAME] += 1
            out.append(ln)
        else:
            plan.counts[ACT_REPLACE] += 1
            if len(plan.samples[ACT_REPLACE]) < 5:
                plan.samples[ACT_REPLACE].append(fmt(key))
            out.append(Line(body=body, eol=ln.eol or eol, obj=json.loads(body)))
    for key in sorted(k for k in want if k not in emitted):
        kind = "rekey_inserted" if b'"rekeyed_from"' in want[key] else ACT_INSERT
        plan.counts[kind] += 1
        if len(plan.samples[kind]) < 5:
            plan.samples[kind].append(fmt(key))
        pos = next((j for j, ln in enumerate(out) if ln.own_key is not None and ln.own_key > key), len(out))
        if pos == len(out) and out and out[-1].eol == b"":
            out[-1] = Line(body=out[-1].body, eol=eol, obj=out[-1].obj, foreign=out[-1].foreign)
        out.insert(pos, Line(body=want[key], eol=eol, obj=json.loads(want[key])))
    return out


def _same_values(obj: Optional[Dict[str, Any]], body: bytes) -> bool:
    """Значення рядка = натив (без мітки settled): не переписуємо лише заради мітки."""
    if obj is None:
        return False
    new = json.loads(body)
    return all(obj.get(k) == new.get(k) for k in ("o", "h", "low", "c", "v", "close_time_ms"))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_system_config(args.config or pick_config_path())
    rule_of = htf_anchor_rule_resolver(cfg)
    data_root = os.path.abspath(args.data_root)
    symbols = args.symbols or list(cfg.get("symbols") or [])
    if args.apply:
        if not args.backup_dir:
            print("D1_NATIVE_REFUSED --apply потребує --backup-dir", file=sys.stderr)
            return 2
        writers_guard(data_root)
    stamp = utc_stamp()
    report: Dict[str, Any] = {"tool": TOOL, "data_root": data_root, "archive": os.path.abspath(args.archive),
                              "mode": "apply" if args.apply else "dry-run", "symbols": {}}
    plans: List[SymbolPlan] = []
    for symbol in symbols:
        sym_dir = symbol.replace("/", "_")
        rule = rule_of(symbol)
        native, meta = load_native(args.archive, sym_dir)
        provenance = "d1native/%s" % str(meta.get("fetched_at", ""))[:16].replace("-", "").replace(":", "")
        era = m1_era_start_ms(data_root, sym_dir, rule)
        is_trading = calendar_for_symbol(dict(cfg), symbol).is_trading_minute
        plan = plan_symbol(data_root, symbol, rule, native, provenance, era, is_trading)
        plans.append(plan)
        report["symbols"][symbol] = {"m1_era_start": fmt(plan.era_ms) if era is not None else None,
                                     "native_first": fmt(plan.native_first_ms), "counts": dict(plan.counts),
                                     "files_changed": len(plan.files), "samples": dict(plan.samples)}
        print("%-8s era=%s native_from=%s files=%d %s" % (
            symbol, fmt(plan.era_ms) if era is not None else "-", fmt(plan.native_first_ms)[4:14], len(plan.files),
            dict(plan.counts)))
        for act, samples in plan.samples.items():
            print("    %s: %s" % (act, samples))
    if args.apply:
        paths = sorted({p for plan in plans for p in plan.files})
        if paths:
            tgz, manifest = backup_files(paths, args.backup_dir, data_root=data_root, tag="d1_native_settle", stamp=stamp)
            report["backup"] = {"tgz": tgz, "manifest": manifest, "files": len(paths)}
            print("BACKUP %s files=%d" % (tgz, len(paths)))
            for plan in plans:
                for path, data in sorted(plan.files.items()):
                    replace_part(path, data, stage_sha256=sha256_hex(data), stamp=stamp)
        again = 0
        for plan in plans:
            native, meta = load_native(args.archive, plan.sym_dir)
            provenance = "d1native/%s" % str(meta.get("fetched_at", ""))[:16].replace("-", "").replace(":", "")
            replan = plan_symbol(data_root, plan.symbol, rule_of(plan.symbol), native, provenance, plan.era_ms,
                                 calendar_for_symbol(dict(cfg), plan.symbol).is_trading_minute)
            again += len(replan.files)
            print("VERIFY_REPLAN %s files=%d" % (plan.symbol, len(replan.files)))
        report["verify_replan_files"] = again
        if again:
            print("D1_NATIVE_VERIFY_FAILED повторний план не порожній — відкат: tar xzf <tgz> -C <батько data_root>",
                  file=sys.stderr)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
    return 1 if report.get("verify_replan_files") else 0


if __name__ == "__main__":
    sys.exit(main())
