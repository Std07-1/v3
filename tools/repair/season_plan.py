"""tools/repair/season_plan.py — план заміни похідних part-файлів (ADR-0095 S7.1, спільний з ADR-0098 C3 і ADR-0101 C5).

Лише планування в пам'яті: SSOT читається так, як його бачить читач (`DiskLayer`, вибирач ADR-0094), а результат —
НОВИЙ вміст part-файлів похідних TF в області дії. На диск не пишеться нічого: staging, валідації й заміна — S7.2 і
S7.3. Області дії:

* `derived_from_m1` — усі TF ланцюга `core.derive` (M3, M5, D1 з M1; M15, M30, H1, H4 каскадом) в епосі M1, так само
  як живий DeriveEngine і `tools.rebuild_from_m1`: `derive_bar`, правило якоря символу, сезонний календар
  `calendar_for_symbol`, прихована M1 (`candle_chain.is_display_hidden`) у бакет не йде, фронтир ADR-0097 для D1,
  формуючий хвіст не фіналізується (критерій `rebuild_from_m1`). Зі `changed_m1` (settle ADR-0101 C5) — лише бакети
  кожного TF, що містять змінений ключ M1.
* `h4_from_h1` — H4 сезонної сітки з H1 на диску для епохи до першої M1 (MIGRATION §4.2).
* `d1_rekey` — D1 поза сезонною сіткою в епосі M1: перебудова з M1 на ключ сітки; поза епохою M1 — MANUAL_REVIEW.
* `holes` — сезонні діри M3..H1 (MIGRATION §5).

Правило рядка одне для всіх областей: свій рядок part-файла в області дії ⇔ бакет його open_time_ms (сезонна сітка
TF) — у наборі перебудови цього TF. Набір — зерна областей, піднесені ланцюгом угору (бакет, чиє джерело
перебудовано, перебудовується), без формуючого хвоста. Бакет без жодної торгової хвилини свого рядка більше не має
(DROP у звіті). Бакет з торговою хвилиною, для якого джерела немає (діра M1 до settle, H1 раніше початку історії),
не перебудовується: рядок на диску лишається як є і вищий TF бере саме його (KEPT_NO_SOURCE, гучно) — видалити
бар, якого нема з чого побудувати, план не вправі.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from core.config_loader import htf_anchor_rule_resolver
from core.derive import DERIVE_CHAIN, DERIVE_ORDER, DERIVE_SOURCE, GenericBuffer, derive_bar
from core.model.bars import CandleBar
from core.model.candle_chain import is_display_hidden
from core.session_anchor import D1_S, H4_S, SEASON_WINTER, htf_bucket_start_ms, htf_next_bucket_start_ms
from runtime.ingest.tick_common import calendar_for_symbol
from runtime.store.layers.disk_layer import DiskLayer
from runtime.store.ssot_jsonl import head_first_bar_time_ms, tail_last_bar_time_ms
from tools.rebuild_from_m1 import REBUILD_CHUNK_D1_BUCKETS, _bucket_awaits_source, _grid_bucket_opens
from tools.repair.partfile_io import Line, PartFile, day_of_ms, load_part, part_path, row_bytes, sha256_hex

log = logging.getLogger("season_plan")

M1_S = 60
M1_MS = M1_S * 1000
H1_S = 3600
SCOPE_DERIVED_FROM_M1 = "derived_from_m1"
SCOPE_H4_FROM_H1 = "h4_from_h1"
SCOPE_D1_REKEY = "d1_rekey"
SCOPE_HOLES = "holes"
SCOPES = (SCOPE_DERIVED_FROM_M1, SCOPE_H4_FROM_H1, SCOPE_D1_REKEY, SCOPE_HOLES)
ALL_TIME = (0, 1 << 62)  # вікно плану без меж, мс UTC
# Читач відбирає ключі за значенням у (since, to]; стелі ключів тут не треба — межу вікна дає `since`
_ALL_KEYS = 1 << 62
# Хвіст M1, що доводить фронтир ADR-0097: два тижні хвилин — прихованих барів поспіль стільки не буває
# (вихідні з перервою ~2.1 доби, зі святом — до ~4)
_VISIBLE_TAIL_PROBE_KEYS = 14 * 1440

Buckets = Dict[int, Set[int]]  # TF → відкриття бакетів сезонної сітки
PlannedBars = Dict[int, Dict[int, Optional[CandleBar]]]  # TF → бакет → новий бар або None


@dataclass
class SymbolContext:
    """Символ плану: правило якоря, сезонний календар, вікно і межі джерела (як їх бачить читач)."""

    symbol: str
    sym_dir: str
    rule: str
    calendar: Any  # SeasonalMarketCalendar: is_trading_minute, summer, winter, season_of
    window: Tuple[int, int]  # [from, to) плану, мс UTC
    m1_head_ms: Optional[int]
    m1_tail_ms: Optional[int]
    h1_tail_ms: Optional[int] = None

    @property
    def source_end_ms(self) -> int:
        """Кінець джерела: крок за хвостом M1 (без M1 — за хвостом H1), не далі вікна — хвіст не фіналізується."""
        tail, step = (self.m1_tail_ms, M1_MS) if self.m1_tail_ms is not None else (self.h1_tail_ms, H1_S * 1000)
        if tail is None:
            return self.window[0]
        return min(self.window[1], tail + step)

    def is_trading(self, minute_ms: int) -> bool:
        return self.calendar.is_trading_minute(minute_ms)

    def has_trading(self, bucket_ms: int, tf_s: int) -> bool:
        """У вікні бакета є торгова хвилина сезонного календаря."""
        return any(self.is_trading(t) for t in range(bucket_ms, self.next_bucket(bucket_ms, tf_s), M1_MS))

    def bucket_of(self, open_ms: int, tf_s: int) -> int:
        return htf_bucket_start_ms(open_ms, tf_s, self.rule)

    def next_bucket(self, bucket_ms: int, tf_s: int) -> int:
        return htf_next_bucket_start_ms(bucket_ms, tf_s, self.rule)


class SourceReader:
    """Бари SSOT символу так, як їх бачить читач: вибирач дублікатів ADR-0094, без чужих і нефінальних рядків."""

    def __init__(self, data_root: str, symbol: str) -> None:
        self._disk = DiskLayer(data_root)
        self._symbol = symbol
        self.rejected_rows = 0

    def read(self, tf_s: int, lo_ms: int, hi_ms: int) -> List[CandleBar]:
        """Бари TF з open у [lo, hi) за зростанням; рядок без OHLC — гучно відкинутий (I5)."""
        rows, _geom = self._disk.read_window_with_geom(
            self._symbol, tf_s, _ALL_KEYS, since_open_ms=lo_ms - 1, to_open_ms=hi_ms - 1, use_tail=True, final_only=True
        )
        return self._to_bars(rows, tf_s)

    def newest(self, tf_s: int, keys: int) -> List[CandleBar]:
        rows, _geom = self._disk.read_window_with_geom(self._symbol, tf_s, keys, use_tail=True, final_only=True)
        return self._to_bars(rows, tf_s)

    def _to_bars(self, rows: Iterable[Mapping[str, Any]], tf_s: int) -> List[CandleBar]:
        bars: List[CandleBar] = []
        for row in rows:
            try:
                bars.append(_row_to_bar(row, self._symbol, tf_s))
            except (KeyError, TypeError, ValueError):
                self.rejected_rows += 1
                log.warning("SEASON_PLAN_ROW_REJECTED symbol=%s tf_s=%d open_ms=%s — рядок без OHLC у джерело не йде",
                            self._symbol, tf_s, row.get("open_time_ms"))
        return bars


def _row_to_bar(row: Mapping[str, Any], symbol: str, tf_s: int) -> CandleBar:
    extensions = row.get("extensions")
    open_ms = int(row["open_time_ms"])
    return CandleBar(
        symbol=symbol, tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + tf_s * 1000,
        o=float(row["o"]), h=float(row["h"]), low=float(row["low"] if "low" in row else row["l"]), c=float(row["c"]),
        v=float(row.get("v", 0.0)), complete=True, src=str(row.get("src") or "derived"),
        extensions=dict(extensions) if isinstance(extensions, dict) else {},
    )


# ── Набір перебудови ───────────────────────────────────────────────────────────────────────────────────────────────
def m1_targets() -> List[int]:
    """TF, що будуються прямо з M1 (M3, M5, D1) — з ланцюга `core.derive`, а не літералом."""
    return [tf_s for tf_s, _bars_needed in DERIVE_CHAIN[M1_S]]


def seed_derived_from_m1(ctx: SymbolContext, changed_m1: Optional[Sequence[int]]) -> Buckets:
    """Зерна M3/M5/D1: усі бакети сітки вікна в епосі M1 або лише ті, що містять змінений ключ M1.

    Бакет із торговою хвилиною до першої M1 не зерно: джерело не покриває його початку (дзеркало формуючого хвоста).
    Вищий TF такий бакет однаково перебудує з бачення джерела (диск + план), якщо в ньому є перебудований дочірній.
    """
    if ctx.m1_head_ms is None:
        return {}
    lo = max(ctx.window[0], ctx.m1_head_ms)
    seeds: Buckets = {}
    for tf_s in m1_targets():
        if changed_m1 is None:
            buckets: Iterable[int] = _grid_bucket_opens(ctx.bucket_of(lo, D1_S), ctx.source_end_ms, tf_s, ctx.rule)
        else:
            buckets = {ctx.bucket_of(k, tf_s) for k in changed_m1 if ctx.window[0] <= k < ctx.window[1]}
        seeds[tf_s] = {b for b in buckets if not _bucket_precedes_source(ctx, b, tf_s)}
    return seeds


def _bucket_precedes_source(ctx: SymbolContext, bucket_ms: int, tf_s: int) -> bool:
    """У бакеті є торгова хвилина раніше першої M1 — джерело не покриває його початку."""
    head_ms = ctx.m1_head_ms if ctx.m1_head_ms is not None else bucket_ms
    return any(ctx.is_trading(t) for t in range(bucket_ms, min(ctx.next_bucket(bucket_ms, tf_s), head_ms), M1_MS))


def complete_rebuild_set(ctx: SymbolContext, seeds: Iterable[Buckets]) -> Tuple[Buckets, Dict[int, int]]:
    """Об'єднання зерен, піднесення ланцюгом угору і відсів формуючого хвоста; повертає (набір, відсіяно за TF).

    Бакет, у вікні якого джерело ще не дійшло до останньої торгової хвилини, не перебудовується на жодному TF
    (`rebuild_from_m1._bucket_awaits_source`): його фіналізує живий DeriveEngine, а наявний рядок лишається.
    """
    rebuild: Buckets = {}
    for seed in seeds:
        for tf_s, buckets in seed.items():
            rebuild.setdefault(tf_s, set()).update(buckets)
    for target_tf_s in DERIVE_ORDER:
        source_tf_s = DERIVE_SOURCE[target_tf_s][0]
        lifted = {ctx.bucket_of(b, target_tf_s) for b in rebuild.get(source_tf_s, ())}
        rebuild.setdefault(target_tf_s, set()).update(lifted)
    tail_kept: Dict[int, int] = {}
    for tf_s, buckets in rebuild.items():
        tail = {b for b in buckets if _bucket_awaits_source(b, ctx.next_bucket(b, tf_s), ctx.source_end_ms, ctx.is_trading)}
        buckets -= tail
        tail_kept[tf_s] = len(tail)
    return {tf_s: buckets for tf_s, buckets in rebuild.items() if buckets}, tail_kept


# ── Зерна інших областей ───────────────────────────────────────────────────────────────────────────────────────────
def seed_h4_from_h1(ctx: SymbolContext, h1_head_ms: Optional[int]) -> Buckets:
    """H4 сезонної сітки з H1 на диску до першої M1 (MIGRATION §4.2): бакети вікна з відкриттям раніше першої M1.

    Бакет, що містить першу M1, теж тут: його H1 — з диска до першої M1, далі — з плану, якщо `derived_from_m1`
    перебудовує H1. Без M1 — уся історія H1 до кінця джерела.
    """
    if h1_head_ms is None:
        return {}
    lo = max(ctx.window[0], h1_head_ms)
    hi = min(ctx.window[1], ctx.m1_head_ms if ctx.m1_head_ms is not None else ctx.source_end_ms)
    if lo >= hi:
        return {}
    return {H4_S: set(_grid_bucket_opens(ctx.bucket_of(lo, D1_S), hi, H4_S, ctx.rule))}


def seed_d1_rekey(ctx: SymbolContext, reader: SourceReader) -> Tuple[Buckets, List[CandleBar], List[CandleBar]]:
    """D1 поза сезонною сіткою: (зерна, рядки на перебудову, MANUAL_REVIEW).

    У епосі M1 (вікно бакета сітки перетинає джерело M1) бакет перебудовується з M1 на ключ сітки — навіть із
    торговими годинами до першої M1 (тонка доба, фронтир ADR-0097); рівність OHLCV видаленому рядку — у звіті
    (гейт V4). Поза епохою M1 рядок не змінюється: будувати нема з чого.
    """
    rekey: List[CandleBar] = []
    manual: List[CandleBar] = []
    for bar in reader.read(D1_S, *ctx.window):
        bucket_ms = ctx.bucket_of(bar.open_time_ms, D1_S)
        if bucket_ms == bar.open_time_ms:
            continue
        in_m1_era = ctx.m1_head_ms is not None and bucket_ms < ctx.source_end_ms and ctx.next_bucket(bucket_ms, D1_S) > ctx.m1_head_ms
        (rekey if in_m1_era else manual).append(bar)
    return {D1_S: {ctx.bucket_of(bar.open_time_ms, D1_S) for bar in rekey}}, rekey, manual


def seed_holes(ctx: SymbolContext, reader: SourceReader) -> Tuple[Buckets, Dict[int, List[int]]]:
    """Сезонні діри M3..H1 (MIGRATION §5): (зерна, поза областю за TF).

    Діра — усі умови разом: у бакеті є видима M1 на торговій хвилині, рядка TF немає, серед торгових хвилин бакета є
    неторгова в розкладі протилежного сезону, а в ту торгову добу TF уже має рядки (так відсікаються голови
    активації). Решта відсутніх бакетів з торговою M1 — поза областю: їх власники — ADR-0092, 0097, 0098.
    """
    seeds: Buckets = {}
    out_of_scope: Dict[int, List[int]] = {}
    if ctx.m1_head_ms is None:
        return seeds, out_of_scope
    hole_tfs = [tf_s for tf_s in DERIVE_ORDER if tf_s < H4_S]
    days = _grid_bucket_opens(ctx.bucket_of(max(ctx.window[0], ctx.m1_head_ms), D1_S), ctx.source_end_ms, D1_S, ctx.rule)
    for chunk_lo, chunk_hi in d1_runs(ctx, days):
        minutes = [bar.open_time_ms for bar in reader.read(M1_S, chunk_lo, chunk_hi)
                   if not is_display_hidden(bar.extensions) and ctx.is_trading(bar.open_time_ms)]
        for tf_s in hole_tfs:
            present = {bar.open_time_ms for bar in reader.read(tf_s, chunk_lo, chunk_hi)}
            days_with_rows = {ctx.bucket_of(open_ms, D1_S) for open_ms in present}
            for bucket_ms in sorted({ctx.bucket_of(open_ms, tf_s) for open_ms in minutes} - present):
                if _bucket_awaits_source(bucket_ms, ctx.next_bucket(bucket_ms, tf_s), ctx.source_end_ms, ctx.is_trading):
                    continue
                if _has_off_season_minute(ctx, bucket_ms, tf_s) and ctx.bucket_of(bucket_ms, D1_S) in days_with_rows:
                    seeds.setdefault(tf_s, set()).add(bucket_ms)
                else:
                    out_of_scope.setdefault(tf_s, []).append(bucket_ms)
    return seeds, out_of_scope


def _has_off_season_minute(ctx: SymbolContext, bucket_ms: int, tf_s: int) -> bool:
    """Серед торгових хвилин бакета є неторгова в розкладі протилежного сезону (для season_rule=none — ніколи)."""
    calendar = ctx.calendar
    for minute_ms in range(bucket_ms, ctx.next_bucket(bucket_ms, tf_s), M1_MS):
        if ctx.is_trading(minute_ms):
            opposite = calendar.summer if calendar.season_of(minute_ms) == SEASON_WINTER else calendar.winter
            if not opposite.is_trading_minute(minute_ms):
                return True
    return False


# ── Нові бари ──────────────────────────────────────────────────────────────────────────────────────────────────────
def plan_bars(ctx: SymbolContext, reader: SourceReader, rebuild: Buckets) -> PlannedBars:
    """Новий бар кожного бакета набору: `derive_bar` з джерела таким, яким воно стане після заміни.

    Джерело TF — бари читача, крім бакетів, чиї рядки план замінює (`replaced_buckets`), плюс нові бари. Порції —
    суцільні серії бакетів D1 по `REBUILD_CHUNK_D1_BUCKETS`: межа D1 — межа бакета кожного похідного TF, тож бакет лежить в одній
    порції. У джерело M1 додається найновіша видима M1 за порцією — фронтир ADR-0097 для D1, як у `rebuild_from_m1`.
    """
    planned: PlannedBars = {}
    visible_tail = _visible_m1_tail(reader)
    for source_tf_s in sorted(DERIVE_CHAIN):
        targets = [tf_s for tf_s, _n in DERIVE_CHAIN[source_tf_s] if rebuild.get(tf_s)]
        if not targets:
            continue
        days = sorted({ctx.bucket_of(b, D1_S) for tf_s in targets for b in rebuild[tf_s]})
        replaced = replaced_buckets(ctx, source_tf_s, rebuild, planned)
        for chunk_lo, chunk_hi in d1_runs(ctx, days):
            buf = _source_buffer(ctx, reader, source_tf_s, (chunk_lo, chunk_hi), replaced, planned)
            if source_tf_s == M1_S and visible_tail is not None and visible_tail.open_time_ms >= chunk_hi:
                buf.upsert(visible_tail)
            for tf_s in targets:
                for bucket_ms in sorted(b for b in rebuild[tf_s] if chunk_lo <= b < chunk_hi):
                    planned.setdefault(tf_s, {})[bucket_ms] = derive_bar(
                        symbol=ctx.symbol, target_tf_s=tf_s, source_buffer=buf, bucket_open_ms=bucket_ms,
                        is_trading_fn=ctx.is_trading, filter_calendar_pause=True, anchor_rule=ctx.rule,
                    )
    return planned


def d1_runs(ctx: SymbolContext, days: Iterable[int]) -> List[Tuple[int, int]]:
    """[(початок, кінець)) суцільних серій бакетів D1 (відсортованих), не довших за REBUILD_CHUNK_D1_BUCKETS."""
    runs: List[Tuple[int, int]] = []
    count = 0
    for day in days:
        day_end = ctx.next_bucket(day, D1_S)
        if runs and runs[-1][1] == day and count < REBUILD_CHUNK_D1_BUCKETS:
            runs[-1] = (runs[-1][0], day_end)
            count += 1
        else:
            runs.append((day, day_end))
            count = 1
    return runs


def replaced_buckets(ctx: SymbolContext, tf_s: int, rebuild: Buckets, planned: PlannedBars) -> Set[int]:
    """Бакети TF, чиї рядки план замінює: набір без бакетів з торговою хвилиною, для яких нового бару немає.

    Такий бакет (діра M1 до settle, H1 раніше початку історії) не перебудовується: його рядок лишається на диску,
    і вищий TF бере саме його, як після заміни бере читач. Бакет без торгової хвилини замінюється нічим (DROP).
    """
    bars = planned.get(tf_s, {})
    return {b for b in rebuild.get(tf_s, ()) if bars.get(b) is not None or not ctx.has_trading(b, tf_s)}


def _source_buffer(
    ctx: SymbolContext, reader: SourceReader, source_tf_s: int, chunk: Tuple[int, int], replaced: Set[int],
    planned: PlannedBars,
) -> GenericBuffer:
    bars = {
        bar.open_time_ms: bar for bar in reader.read(source_tf_s, chunk[0], chunk[1])
        if not is_display_hidden(bar.extensions) and ctx.bucket_of(bar.open_time_ms, source_tf_s) not in replaced
    }
    for bucket_ms, bar in planned.get(source_tf_s, {}).items():
        if bar is not None and chunk[0] <= bucket_ms < chunk[1]:
            bars[bucket_ms] = bar
    buf = GenericBuffer(source_tf_s, max_keep=len(bars) + 2)
    for open_ms in sorted(bars):
        buf.upsert(bars[open_ms])
    return buf


def _visible_m1_tail(reader: SourceReader) -> Optional[CandleBar]:
    visible = [bar for bar in reader.newest(M1_S, _VISIBLE_TAIL_PROBE_KEYS) if not is_display_hidden(bar.extensions)]
    return visible[-1] if visible else None


# ── Нові part-файли ────────────────────────────────────────────────────────────────────────────────────────────────
ROW_OLD = "old"  # свій рядок в області дії до заміни
ROW_UNCHANGED = "unchanged"  # новий бар байт у байт той самий — рядок лишається на місці зі своїм EOL
ROW_REPLACED = "replaced"  # той самий ключ, інший вміст — на місці старого, EOL файла
ROW_ADDED = "added"  # ключ, якого у файлі не було (діра, ключ сезонної сітки)
ROW_OFF_GRID = "removed_off_grid"  # ключ не на сітці TF — бакет має новий бар на ключі сітки
ROW_DROPPED = "dropped"  # бакет без жодної торгової хвилини, ключ будь-який
ROW_DUPLICATE = "duplicate_removed"  # другий і далі рядки одного ключа
ROW_KEPT_NO_SOURCE = "kept_no_source"  # бакет з торговою хвилиною без джерела — рядок лишається як є
ROW_KEPT_OFF_GRID = "kept_off_grid"  # з них — ключ поза сіткою TF (H4/D1): лишається дефектом, гейт V1


@dataclass
class FilePlan:
    """Новий вміст одного part-файла і що в ньому змінилось; `new_bytes` у JSON плану не йде."""

    sym_dir: str
    tf_s: int
    day: str
    path: str
    src_exists: bool
    src_sha256: Optional[str]
    src_size: int
    new_bytes: bytes
    removed_keys: List[int]
    added_keys: List[int]
    kept_lines: int
    eol_added: bool

    @property
    def new_sha256(self) -> str:
        return sha256_hex(self.new_bytes)

    def to_json(self) -> Dict[str, Any]:
        return {
            "path": "%s/tf_%d/part-%s.jsonl" % (self.sym_dir, self.tf_s, self.day), "src_exists": self.src_exists,
            "src_sha256": self.src_sha256, "src_size": self.src_size, "new_sha256": self.new_sha256,
            "new_size": len(self.new_bytes), "removed_keys": self.removed_keys, "added_keys": self.added_keys,
            "kept_lines": self.kept_lines, "eol_added": self.eol_added,
        }


def plan_part_file(
    part: PartFile, ctx: SymbolContext, tf_s: int, day: str, scope: "TfScope", new_rows: Mapping[int, bytes]
) -> Optional[FilePlan]:
    """Новий вміст part-файла; None — файл не змінюється.

    Рядок поза областю дії, чужий, нерозбірний чи порожній, а також рядок бакета без джерела (`replaced_buckets`,
    лічильник `kept_no_source`) — байт у байт на своєму місці. Свій рядок бакета, що замінюється:
    той самий байт у байт — лишається; той самий ключ з іншим вмістом — новий рядок на його місці; ключ поза сіткою,
    бакет без бару, повтор ключа — прибирається. Новий ключ стає перед першим своїм рядком з більшим ключем. Нові
    рядки мають EOL файла; рядок без переводу, за яким щось іде або до якого дописуватиме писар, отримує EOL файла
    (`eol_added`, гучно в плані).
    """
    eol = part.eol_style()
    rows = scope.rows
    out: List[Line] = []
    emitted: Set[int] = set()
    removed: List[int] = []
    added: List[int] = []
    for line in part.lines:
        key = line.own_key
        bucket_ms = None if key is None else ctx.bucket_of(key, tf_s)
        if bucket_ms is None or bucket_ms not in scope.replaced:
            if bucket_ms in scope.rebuild:
                scope.kept_keys.append(key)
                rows[ROW_KEPT_NO_SOURCE] += 1
                rows[ROW_KEPT_OFF_GRID] += int(bucket_ms != key)
            out.append(line)
            continue
        rows[ROW_OLD] += 1
        body = new_rows.get(key)
        if body is None or key in emitted:
            removed.append(key)
            rows[ROW_DUPLICATE if key in emitted else ROW_OFF_GRID if bucket_ms in scope.built else ROW_DROPPED] += 1
            continue
        emitted.add(key)
        if line.body == body:
            out.append(line)
            rows[ROW_UNCHANGED] += 1
        else:
            out.append(Line(body=body, eol=eol, obj={"open_time_ms": key}))
            removed.append(key)
            added.append(key)
            rows[ROW_REPLACED] += 1
    for key in sorted(set(new_rows) - emitted):
        at = next((i for i, line in enumerate(out) if line.own_key is not None and line.own_key > key), len(out))
        out.insert(at, Line(body=new_rows[key], eol=eol, obj={"open_time_ms": key}))
        added.append(key)
        rows[ROW_ADDED] += 1
    if b"".join(line.body + line.eol for line in out) == part.to_bytes():
        return None
    eol_added = any(not line.eol for line in out)
    out = [line if line.eol else Line(body=line.body, eol=eol, obj=line.obj, foreign=line.foreign) for line in out]
    original = {id(line) for line in part.lines}
    return FilePlan(
        sym_dir=ctx.sym_dir, tf_s=tf_s, day=day, path=part.path, src_exists=part.exists, src_sha256=part.sha256,
        src_size=part.size, new_bytes=b"".join(line.body + line.eol for line in out), removed_keys=removed,
        added_keys=added, kept_lines=sum(1 for line in out if id(line) in original), eol_added=eol_added,
    )


@dataclass
class TfScope:
    """Що план робить з TF: набір перебудови, бакети, чиї рядки замінюються, бакети з новим баром; лічильники."""

    rebuild: Set[int]
    replaced: Set[int]
    built: Set[int]
    rows: Counter = field(default_factory=Counter)
    kept_keys: List[int] = field(default_factory=list)  # рядки бакетів без джерела, що лишаються


def plan_symbol_files(
    ctx: SymbolContext, data_root: str, rebuild: Buckets, planned: PlannedBars
) -> Tuple[List[FilePlan], Dict[int, TfScope]]:
    """Плани part-файлів усіх TF набору: доби вікон бакетів і доби нових рядків; повертає (плани, TF → область)."""
    files: List[FilePlan] = []
    scopes: Dict[int, TfScope] = {}
    for tf_s in sorted(rebuild):
        new_by_day: Dict[str, Dict[int, bytes]] = {}
        for bucket_ms, bar in planned.get(tf_s, {}).items():
            if bar is not None:
                new_by_day.setdefault(day_of_ms(bucket_ms), {})[bucket_ms] = row_bytes(bar)
        scope = scopes[tf_s] = TfScope(
            rebuild=rebuild[tf_s], replaced=replaced_buckets(ctx, tf_s, rebuild, planned),
            built={bucket_ms for day_rows in new_by_day.values() for bucket_ms in day_rows},
        )
        days = set(new_by_day)
        for bucket_ms in rebuild[tf_s]:
            days.update((day_of_ms(bucket_ms), day_of_ms(ctx.next_bucket(bucket_ms, tf_s) - 1)))
        for day in sorted(days):
            part = load_part(part_path(data_root, ctx.sym_dir, tf_s, day), ctx.sym_dir)
            plan = plan_part_file(part, ctx, tf_s, day, scope, new_by_day.get(day, {}))
            if plan is not None:
                files.append(plan)
    return files, scopes


# ── План символу і всього прогону ──────────────────────────────────────────────────────────────────────────────────
@dataclass
class SymbolPlan:
    """План одного символу: набір перебудови, нові бари, зміни part-файлів і те, що лишилось поза областю."""

    context: SymbolContext
    rebuild: Buckets
    tail_kept: Dict[int, int]
    planned: PlannedBars
    files: List[FilePlan]
    scopes: Dict[int, TfScope]
    d1_rekey: List[CandleBar]
    manual_review: List[CandleBar]
    holes: Buckets
    holes_out_of_scope: Dict[int, List[int]]
    rejected_rows: int

    @property
    def rows(self) -> Dict[int, Counter]:
        """Лічильники рядків part-файлів за TF (ROW_*)."""
        return {tf_s: scope.rows for tf_s, scope in self.scopes.items()}

    def dropped(self, tf_s: int) -> Tuple[List[int], List[int]]:
        """(бакети без нового бару й без торгової хвилини, бакети з торговою хвилиною, але без джерела)."""
        no_trading: List[int] = []
        no_source: List[int] = []
        for bucket_ms in sorted(b for b, bar in self.planned.get(tf_s, {}).items() if bar is None):
            (no_source if self.context.has_trading(bucket_ms, tf_s) else no_trading).append(bucket_ms)
        return no_trading, no_source

    def rekey_results(self) -> List[Dict[str, Any]]:
        """На кожен D1 поза сіткою: ключ сітки, рівність OHLCV новому бару (гейт V4) і тонка доба."""
        results = []
        for old in self.d1_rekey:
            bucket_ms = self.context.bucket_of(old.open_time_ms, D1_S)
            new = self.planned.get(D1_S, {}).get(bucket_ms)
            results.append({
                "old_open_ms": old.open_time_ms, "new_open_ms": bucket_ms, "src": old.src,
                "ohlcv_equal": new is not None and (new.o, new.h, new.low, new.c, new.v) == (old.o, old.h, old.low, old.c, old.v),
                "thin_session": new is not None and "thin_session" in (new.extensions.get("partial_reasons") or []),
            })
        return results

    def to_json(self) -> Dict[str, Any]:
        ctx = self.context
        per_tf = {}
        for tf_s in sorted(set(self.rebuild) | set(self.rows)):
            built = [bar for bar in self.planned.get(tf_s, {}).values() if bar is not None]
            no_trading, no_source = self.dropped(tf_s)
            per_tf[str(tf_s)] = {
                "rebuild": len(self.rebuild.get(tf_s, ())), "new": len(built), "tail_kept": self.tail_kept.get(tf_s, 0),
                "partial": sum(1 for bar in built if bar.extensions.get("partial")), "rows": dict(self.rows.get(tf_s, {})),
                "no_bar_no_trading": len(no_trading), "no_bar_no_source": len(no_source),
                "kept_no_source": sorted(self.scopes[tf_s].kept_keys) if tf_s in self.scopes else [],
            }
        return {
            "symbol": ctx.symbol, "rule": ctx.rule, "season_rule": ctx.calendar.season_rule, "window": list(ctx.window),
            "m1_head_ms": ctx.m1_head_ms, "m1_tail_ms": ctx.m1_tail_ms, "source_end_ms": ctx.source_end_ms,
            "tf": per_tf, "files": [file_plan.to_json() for file_plan in self.files],
            "d1_rekey": self.rekey_results(), "manual_review": [bar.open_time_ms for bar in self.manual_review],
            "holes": {str(tf_s): sorted(b) for tf_s, b in self.holes.items()},
            "holes_out_of_scope": {str(tf_s): b for tf_s, b in self.holes_out_of_scope.items()},
            "rejected_rows": self.rejected_rows,
        }


@dataclass
class SeasonPlan:
    """План прогону: ревізія коду, корінь даних, області, вікно і плани символів; `files` — усі зміни part-файлів."""

    git_rev: str
    data_root: str
    scopes: Tuple[str, ...]
    window: Tuple[int, int]
    changed_m1: bool
    symbols: List[SymbolPlan]

    @property
    def files(self) -> List[FilePlan]:
        return [file_plan for symbol_plan in self.symbols for file_plan in symbol_plan.files]

    def to_json(self) -> Dict[str, Any]:
        return {"git_rev": self.git_rev, "data_root": self.data_root, "scopes": list(self.scopes),
                "window": list(self.window), "changed_m1": self.changed_m1,
                "symbols": [symbol_plan.to_json() for symbol_plan in self.symbols]}


def build_plan(
    cfg: Mapping[str, Any], data_root: str, symbols: Sequence[str], scopes: Sequence[str],
    window: Tuple[int, int] = ALL_TIME, changed_m1: Optional[Mapping[str, Sequence[int]]] = None,
) -> SeasonPlan:
    """План заміни для символів; `changed_m1` — {каталог символу: [open_ms M1]} від settle (ADR-0101 C5).

    Правило якоря і сезонний календар кожного символу резолвляться до планування: невиміряна група чи неповний
    календар — ValueError для всього прогону, а не план частини символів.
    """
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown or not scopes:
        raise ValueError("SEASON_PLAN_SCOPE_INVALID scopes=%s allowed=%s" % (list(scopes), list(SCOPES)))
    if changed_m1 is not None and SCOPE_DERIVED_FROM_M1 not in scopes:
        raise ValueError("SEASON_PLAN_CHANGED_M1_WITHOUT_SCOPE — changed_m1 звужує лише %s" % SCOPE_DERIVED_FROM_M1)
    rule_for_symbol = htf_anchor_rule_resolver(dict(cfg))
    resolved = [(symbol, rule_for_symbol(symbol), calendar_for_symbol(dict(cfg), symbol)) for symbol in symbols]
    plans = []
    for symbol, rule, calendar in resolved:
        sym_dir = symbol.replace("/", "_")
        ctx = SymbolContext(
            symbol=symbol, sym_dir=sym_dir, rule=rule, calendar=calendar, window=window,
            m1_head_ms=head_first_bar_time_ms(data_root, symbol, M1_S),
            m1_tail_ms=tail_last_bar_time_ms(data_root, symbol, M1_S),
            h1_tail_ms=tail_last_bar_time_ms(data_root, symbol, H1_S),
        )
        changed = None if changed_m1 is None else list(changed_m1.get(sym_dir, ()))
        plans.append(_plan_symbol(ctx, data_root, scopes, changed))
    return SeasonPlan(git_rev=_git_rev(), data_root=data_root, scopes=tuple(scopes), window=window,
                      changed_m1=changed_m1 is not None, symbols=plans)


def _plan_symbol(ctx: SymbolContext, data_root: str, scopes: Sequence[str], changed: Optional[List[int]]) -> SymbolPlan:
    reader = SourceReader(data_root, ctx.symbol)
    seeds: List[Buckets] = []
    rekey: List[CandleBar] = []
    manual: List[CandleBar] = []
    holes: Buckets = {}
    out_of_scope: Dict[int, List[int]] = {}
    if SCOPE_DERIVED_FROM_M1 in scopes:
        seeds.append(seed_derived_from_m1(ctx, changed))
    if SCOPE_H4_FROM_H1 in scopes:
        seeds.append(seed_h4_from_h1(ctx, head_first_bar_time_ms(data_root, ctx.symbol, H1_S)))
    if SCOPE_D1_REKEY in scopes:
        d1_seeds, rekey, manual = seed_d1_rekey(ctx, reader)
        seeds.append(d1_seeds)
    if SCOPE_HOLES in scopes:
        holes, out_of_scope = seed_holes(ctx, reader)
        seeds.append(holes)
    rebuild, tail_kept = complete_rebuild_set(ctx, seeds)
    planned = plan_bars(ctx, reader, rebuild)
    files, scopes = plan_symbol_files(ctx, data_root, rebuild, planned)
    return SymbolPlan(context=ctx, rebuild=rebuild, tail_kept=tail_kept, planned=planned, files=files, scopes=scopes,
                      d1_rekey=rekey, manual_review=manual, holes=holes, holes_out_of_scope=out_of_scope,
                      rejected_rows=reader.rejected_rows)


def _git_rev() -> str:
    """Ревізія коду плану (гейт STALE_SOURCE порівнює план і прогін); без git — `unknown` і WARNING."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        done = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("SEASON_PLAN_GIT_REV_UNKNOWN cause=%s", exc)
        return "unknown"
    if done.returncode != 0:
        log.warning("SEASON_PLAN_GIT_REV_UNKNOWN rc=%d stderr=%s", done.returncode, done.stderr.strip())
        return "unknown"
    return done.stdout.strip()
