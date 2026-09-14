"""Рейки цілі запису для apply і rollback: prod чи копія, записувачі, закритий ринок, власник файлів (ADR-0096 §3.3 B).

Ціль «prod» — корінь, що збігається з data_root конфігу; все інше — «copy» і лише з явним --copy (копія не
всередині проду). Кожен шлях запису після realpath — усередині кореня цілі, а для копії — поза продом (symlink чи
junction у корені копії не веде запис у прод). На проді запис дозволено лише коли скан /proc довів: записувачів
немає, ринок кожного символу конфігу закритий у [now − guard, now + guard] (зупинка smc-fxcm зупиняє інжест усіх
символів), а цільові файли належать тому, хто пише (запуск від root зробив би їх root-власними — живі записувачі не
змогли б дописувати).
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from runtime.ingest.tick_common import resolve_symbol_calendars, symbols_from_cfg
from tools.repair.first_tick_m1 import common as c
from tools.repair.first_tick_m1.writers_guard import WriterScan
from tools.repair.jsonl_rewrite import rewrite_tmp_path


class TargetRefused(Exception):
    def __init__(self, rc: int, text: str, report: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(text)
        self.rc = rc
        self.text = text
        self.report = report  # опис скану записувачів для маніфесту, якщо відмова від скану


@dataclasses.dataclass(frozen=True)
class Target:
    data_root: str
    kind: str  # "prod" | "copy"


@dataclasses.dataclass(frozen=True)
class WriteDeps:
    """Зовнішній світ apply/rollback — ін'єкції для тестів; `main` фаз збирає справжні."""

    now_ms: Callable[[], int]
    scan_writers: Callable[[Sequence[str]], WriterScan]
    geteuid: Optional[Callable[[], int]]
    load_cfg: Callable[[], Dict[str, Any]]


def resolve_target(cfg: Dict[str, Any], data_root_arg: Optional[str], copy_flag: bool, prefix: str) -> Target:
    data_root, configured = c.resolve_data_root(cfg, data_root_arg), c.resolve_data_root(cfg, None)
    kind = "prod" if c.norm_path(data_root) == c.norm_path(configured) else "copy"
    if copy_flag and kind == "prod":
        raise refuse(2, prefix + "_COPY_FLAG_ON_PROD", data_root=data_root)
    if kind == "copy" and not copy_flag:
        raise refuse(2, prefix + "_ROOT_NOT_CONFIGURED", data_root=data_root, configured=configured)
    if kind == "copy" and c.paths_overlap(data_root, configured):
        raise refuse(2, prefix + "_COPY_OVERLAPS_PROD", data_root=data_root, configured=configured)
    return Target(data_root, kind)


def require_contained(cfg: Dict[str, Any], target: Target, prefix: str, paths: Sequence[str]) -> None:
    """Кожен шлях запису після realpath — усередині кореня цілі; для копії ще й не перетинає configured prod root.

    Перевірка кореня копії (`resolve_target`) не бачить symlink/junction усередині нього: каталог tf_60, part-файл
    чи його `.tmp`, що ведуть у прод, провели б `rewrite_atomic` у прод-файли повз рейки записувачів, ринку і
    власника. Перевіряється кожен шлях, яким піде запис, — перед першим записом і перед кожним файлом.
    """
    configured = c.resolve_data_root(cfg, None)
    for path in paths:
        real = os.path.realpath(path)
        if target.kind == "copy" and c.paths_overlap(real, configured):
            raise refuse(2, prefix + "_COPY_OVERLAPS_PROD", path=path, realpath=real, configured=configured)
        if not c.path_within(real, target.data_root):
            raise refuse(2, prefix + "_TARGET_OUTSIDE_ROOT", path=path, realpath=real, data_root=target.data_root)


def part_write_paths(target: Target, rel_part: str) -> List[str]:
    """Шляхи, якими `rewrite_atomic` пише part-файл: його каталог, сам файл і тимчасовий файл поруч."""
    path = os.path.join(target.data_root, *rel_part.split("/"))
    return [os.path.dirname(path), path, rewrite_tmp_path(path)]


def require_outside(target: Target, prefix: str, **paths: Optional[str]) -> None:
    for name, path in paths.items():
        if path and c.paths_overlap(path, target.data_root):
            raise refuse(2, prefix + "_PATH_INSIDE_DATA_ROOT", name=name, path=path, data_root=target.data_root)


def writers_check(deps: WriteDeps, target_dir: str, prefix: str) -> Dict[str, Any]:
    """Скан записувачів; недоступний або знайдено — TargetRefused(3). Повертає опис скану для маніфесту."""
    scan = deps.scan_writers([target_dir])
    report = {"available": scan.available, "reason": scan.reason, "scanned": scan.scanned,
              "writers": [dataclasses.asdict(m) for m in scan.writers],
              "fd_writers": [dataclasses.asdict(m) for m in scan.fd_writers], "fd_unreadable": scan.fd_unreadable}
    if not scan.available:
        raise refuse(3, prefix + "_WRITERS_CHECK_UNAVAILABLE", report, reason=scan.reason)
    if scan.writers or scan.fd_writers:
        found = scan.writers + scan.fd_writers
        raise refuse(3, prefix + "_WRITERS_RUNNING", report, pids=",".join(str(m.pid) for m in found),
                     match=",".join(m.match for m in found), argv=[" ".join(m.argv) for m in found])
    return report


def market_check(cfg: Dict[str, Any], now_ms: int, guard_minutes: int, prefix: str) -> Dict[str, Any]:
    """Ринок кожного символу конфігу закритий у [now − guard, now + guard], інакше TargetRefused(3)."""
    symbols = symbols_from_cfg(cfg)
    calendars, rejected = resolve_symbol_calendars(cfg, symbols, where="ft_m1_" + prefix.lower())
    if rejected:
        raise refuse(2, prefix + "_MARKET_CALENDAR_MISSING", symbols=",".join(rejected))
    guard_ms = guard_minutes * c.MINUTE_MS
    for symbol in symbols:
        minute = c.first_trading_minute(calendars[symbol], now_ms - guard_ms, now_ms + guard_ms)
        if minute is not None:
            raise refuse(3, prefix + "_REFUSED_MARKET_OPEN", symbol=symbol,
                         first_trading_minute_utc=c.utc_iso(minute), now_utc=c.utc_iso(now_ms))
    return {"checked": True, "first_trading_minute_utc": None, "guard_minutes": guard_minutes}


def owner_check(deps: WriteDeps, paths: Sequence[str], prefix: str) -> None:
    if deps.geteuid is None:
        return
    euid = deps.geteuid()
    foreign: List[str] = [path for path in paths if os.stat(path).st_uid != euid]
    if foreign:
        raise refuse(2, prefix + "_OWNER_MISMATCH", euid=euid, files=",".join(foreign))


def refuse(rc: int, code: str, report: Optional[Dict[str, Any]] = None, **fields: Any) -> TargetRefused:
    return TargetRefused(rc, c.log_event(logging.ERROR, code, **fields), report)
