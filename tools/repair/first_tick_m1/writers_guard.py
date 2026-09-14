"""Доказ, що записувачі SSOT зупинені: скан /proc, а не прапорець оператора (ADR-0096 §3.3 B, §6 специфікації).

`os.replace` відчіпляє відкритий FD живого записувача: його дописи підуть у старий inode (бекап), а на диску
їх не буде. Тому apply на проді пише лише після скану: жодного процесу-записувача за argv
(`python -m <модуль>`, `app.main --mode <writer>`, запуск скриптом) і жодного FD на запис у цільовому
каталозі (сильніша, довідкова ознака — FD процесів інших користувачів можуть бути нечитабельні). /proc
недоступний або прихований (hidepid) — зупинку довести неможливо, це відмова, а не «мабуть, ок».
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

from tools.repair.first_tick_m1.common import log_event, norm_path

# Модулі, що пишуть part-файли SSOT (перелік звірено з кодом; гейт у tests/test_first_tick_m1_writers_guard.py
# знаходить кожен модуль з JsonlAppender / rewrite_atomic / build_uds_from_config без role="reader").
WRITER_MODULES = frozenset({
    "runtime.ingest.m1_ingestion_worker", "runtime.ingest.broker_sidecar", "runtime.ingest.tick_preview_worker",
    "runtime.ingest.polling.m1_poller", "runtime.ingest.replay",
    "tools.fetch_tf_backfill", "tools.rebuild_from_m1", "tools.purge_broken_bars", "tools.cleanup_d1_weekend",
    "tools.dedup_derived_jsonl", "tools.stress_jsonl_parallel",
    "tools.repair.repair_m1_gaps", "tools.repair.dedup_jsonl_lastwins", "tools.repair.sort_jsonl_by_open_ms",
    "tools.repair.htf_rebuild_from_fxcm", "tools.repair.htf_tail_sync_from_fxcm",
})
WRITER_PACKAGES = ("tools.repair.first_tick_m1",)  # інший apply/rollback того самого інструмента
APP_MAIN_WRITER_MODES = frozenset({"all", "m1_poller", "broker_sidecar", "m1_ingestion_worker", "tick_preview", "replay"})
# Пишуть, але не FXCM tf_60 цілі (кожне твердження перевірено кодом).
NON_TARGET_WRITERS = {
    "runtime.store.uds": "бібліотека: пише лише процес-записувач, що її створив",
    "runtime.store.ssot_jsonl": "бібліотека JsonlAppender",
    "tools.repair.jsonl_rewrite": "бібліотека rewrite_atomic",
    "runtime.ingest.binance_ingest_worker": "пише лише cfg.binance.symbols; plan/apply відмовляють для них "
                                            "(common.fxcm_symbol_problem)",
}


@dataclasses.dataclass(frozen=True)
class ProcMatch:
    pid: int
    match: str
    argv: Tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class WriterScan:
    available: bool
    reason: Optional[str]
    scanned: int
    writers: Tuple[ProcMatch, ...]
    fd_writers: Tuple[ProcMatch, ...]
    fd_unreadable: int

    @property
    def clear(self) -> bool:
        return self.available and not self.writers and not self.fd_writers


def match_writer(argv: Sequence[str]) -> Optional[str]:
    """Ім'я модуля-записувача, якщо argv — його запуск; згадка модуля аргументом (grep/tail) — не запуск."""
    tokens = list(argv)
    if "-m" in tokens[:-1]:
        index = tokens.index("-m")
        module = tokens[index + 1]
        if module in WRITER_MODULES or any(module == p or module.startswith(p + ".") for p in WRITER_PACKAGES):
            return module
        if module == "app.main":
            mode = _app_main_mode(tokens[index + 2:])
            return "app.main:%s" % mode if mode in APP_MAIN_WRITER_MODES else None
        return None
    if tokens and os.path.basename(tokens[0].replace("\\", "/")).startswith("python"):
        for token in tokens[1:]:
            path = token.replace("\\", "/")
            for module in WRITER_MODULES:
                script = module.replace(".", "/") + ".py"
                if path == script or path.endswith("/" + script):
                    return module
    return None


def scan_writers(proc_root: str = "/proc", target_dirs: Iterable[str] = (), exclude_self: bool = True,
                 readlink: Callable[[str], str] = os.readlink, self_pid: Optional[int] = None) -> WriterScan:
    if not os.path.isdir(proc_root):
        return WriterScan(False, "no_proc", 0, (), (), 0)
    if not os.path.exists(os.path.join(proc_root, "1")):
        return WriterScan(False, "proc_hidepid", 0, (), (), 0)
    excluded = _self_and_ancestors(proc_root, self_pid or os.getpid()) if exclude_self else set()
    targets = [norm_path(path) for path in target_dirs]
    writers: List[ProcMatch] = []
    fd_writers: List[ProcMatch] = []
    scanned = fd_unreadable = 0
    for name in sorted((n for n in os.listdir(proc_root) if n.isdigit()), key=int):
        if int(name) in excluded:
            continue
        try:
            with open(os.path.join(proc_root, name, "cmdline"), "rb") as fh:
                raw = fh.read()
        except (FileNotFoundError, ProcessLookupError):
            continue  # процес завершився між лістингом і читанням
        except PermissionError:
            return WriterScan(False, "cmdline_unreadable:pid=%s" % name, scanned, (), (), fd_unreadable)
        if not raw:
            continue  # потік ядра
        scanned += 1
        parts = raw.split(b"\0")
        if parts[-1] == b"":
            parts.pop()
        argv = tuple(part.decode("utf-8", errors="replace") for part in parts)
        match = match_writer(argv)
        if match:
            writers.append(ProcMatch(int(name), match, argv))
        if targets:
            target, unreadable = _write_fd_on_targets(proc_root, name, targets, readlink)
            fd_unreadable += unreadable
            if target:
                fd_writers.append(ProcMatch(int(name), "fd:%s" % target, argv))
    return WriterScan(True, None, scanned, tuple(writers), tuple(fd_writers), fd_unreadable)


def _app_main_mode(rest: Sequence[str]) -> str:
    for index, token in enumerate(rest):
        if token == "--mode" and index + 1 < len(rest):
            return rest[index + 1]
        if token.startswith("--mode="):
            return token.split("=", 1)[1]
    return "all"


def _self_and_ancestors(proc_root: str, pid: int) -> Set[int]:
    """Власний pid і всі предки: `timeout … -m tools.repair.first_tick_m1 apply` інакше знайде сам себе."""
    chain: Set[int] = set()
    while pid > 0 and pid not in chain:
        chain.add(pid)
        try:
            with open(os.path.join(proc_root, str(pid), "status"), encoding="utf-8", errors="replace") as fh:
                ppid = next((int(ln.split(":", 1)[1]) for ln in fh if ln.startswith("PPid:")), 0)
        except OSError:
            break
        pid = ppid
    return chain


def _write_fd_on_targets(proc_root: str, pid: str, targets: Sequence[str],
                         readlink: Callable[[str], str]) -> Tuple[Optional[str], int]:
    fd_dir = os.path.join(proc_root, pid, "fd")
    try:
        names = os.listdir(fd_dir)
    except PermissionError:
        return None, 1
    except OSError:
        return None, 0  # процес зник або fd недоступний як каталог
    for fd in names:
        try:
            link = readlink(os.path.join(fd_dir, fd))
        except OSError as exc:
            log_event(logging.DEBUG, "FT_GUARD_FD_GONE", pid=pid, fd=fd, err=exc)
            continue
        if not os.path.isabs(link) or not any(_inside(norm_path(link), target) for target in targets):
            continue
        flags = _fd_flags(proc_root, pid, fd)
        if flags is None or flags & 0o3:  # нечитабельний fdinfo — не доведено, що лише читання
            return link, 0
    return None, 0


def _fd_flags(proc_root: str, pid: str, fd: str) -> Optional[int]:
    try:
        with open(os.path.join(proc_root, pid, "fdinfo", fd), encoding="utf-8") as fh:
            for text in fh:
                if text.startswith("flags:"):
                    return int(text.split(":", 1)[1].strip(), 8)
    except (OSError, ValueError) as exc:
        log_event(logging.WARNING, "FT_GUARD_FDINFO_UNREADABLE", pid=pid, fd=fd, err=exc)
    return None


def _inside(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False
