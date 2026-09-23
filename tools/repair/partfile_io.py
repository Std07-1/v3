"""tools/repair/partfile_io.py — побайтна модель part-файлів SSOT для заміни ADR-0095 S7 (спільна з ADR-0098 C3).

Файл — послідовність рядків (body, eol): body — байти без переводу рядка, eol — LF, CRLF або порожній (лише в
останнього рядка без переводу). Склеювання назад дає ті самі байти, і це перевіряється при читанні. Рядок, якого
заміна не чіпає (чужого символу за правилом читача `disk_layer.is_foreign_row`, нерозбірний, порожній, поза
областю дії), переноситься байт у байт разом зі своїм EOL: у XAU/XAG CRLF мають 557 з 705 і 547 з 703 part-файлів
H4, а нормалізація EOL змінила б рядки, яких ремонт не стосується. Новий рядок — як у писаря SSOT
(`ssot_jsonl.serialize_bar`) з EOL файла.

Part-файл — лише `part-YYYYMMDD.jsonl` верхнього рівня каталогу TF, як у читача (`DiskLayer.list_parts`); сусіди
`.bak.<ts>` і каталоги `_backup_*` — не part-файли. Ім'я — UTC-доба open_time_ms.

Запис (ADR-0095 §3.8 п.3): `backup_files` — tgz і sha256-маніфест поза data_root до запису; `writers_guard` — доказ
зі /proc, що записувачі зупинені (argv і FD part-файлів, відкриті на запис); `replace_part` — атомарна заміна зі
старим inode в `<tf>/_backup_adr0095_<ts>/`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import logging
import os
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.model.bars import CandleBar
from runtime.store.layers.disk_layer import is_foreign_row
from runtime.store.ssot_jsonl import serialize_bar
from tools.repair.jsonl_rewrite import replace_bytes_atomic

log = logging.getLogger("partfile_io")

PART_NAME_RE = re.compile(r"^part-(\d{8})\.jsonl$")
LF = b"\n"
CRLF = b"\r\n"


@dataclass
class Line:
    """Рядок part-файла: байти без переводу, свій EOL і розбір (лише для рядка з цілим open_time_ms)."""

    body: bytes
    eol: bytes
    obj: Optional[Dict[str, Any]] = None
    foreign: bool = False

    @property
    def own_key(self) -> Optional[int]:
        """open_time_ms свого розібраного рядка; None — рядок, якого заміна не чіпає (чужий, нерозбірний, порожній)."""
        if self.obj is None or self.foreign:
            return None
        return self.obj["open_time_ms"]


@dataclass
class PartFile:
    """Part-файл як на диску: рядки, sha256 і розмір вихідних байтів (`exists=False` — файла ще немає)."""

    path: str
    lines: List[Line] = field(default_factory=list)
    sha256: Optional[str] = None
    size: int = 0
    exists: bool = False

    def to_bytes(self) -> bytes:
        return b"".join(line.body + line.eol for line in self.lines)

    def eol_style(self) -> bytes:
        """EOL для нових рядків файла: перший EOL у файлі, у порожнього чи без переводів — LF (як у писаря на проді)."""
        return next((line.eol for line in self.lines if line.eol), LF)


def parse_body(body: bytes) -> Optional[Dict[str, Any]]:
    """Розбір рядка так, як його бачить читач: JSON-об'єкт із цілим open_time_ms, інакше None."""
    try:
        obj = json.loads(body.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError — теж ValueError: такий рядок читач пропускає, заміна не чіпає
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("open_time_ms"), int):
        return None
    return obj


def split_lines(data: bytes) -> List[Line]:
    """Рядки з EOL кожного; CR лише перед LF — частина EOL, а не тіла."""
    lines: List[Line] = []
    start = 0
    while start < len(data):
        end = data.find(LF, start)
        if end < 0:
            lines.append(Line(body=data[start:], eol=b""))
            break
        body, eol = data[start:end], LF
        if body.endswith(b"\r"):
            body, eol = body[:-1], CRLF
        lines.append(Line(body=body, eol=eol))
        start = end + 1
    return lines


def load_part(path: str, sym_dir: str) -> PartFile:
    """Part-файл каталогу символу `sym_dir` (XAU_USD); файла немає — порожній PartFile з `exists=False`."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return PartFile(path=path)
    part = PartFile(path=path, lines=split_lines(data), sha256=sha256_hex(data), size=len(data), exists=True)
    for line in part.lines:
        line.obj = parse_body(line.body)
        line.foreign = line.obj is not None and is_foreign_row(line.obj, sym_dir)
    if part.to_bytes() != data:
        raise AssertionError("PARTFILE_ROUNDTRIP_MISMATCH path=%s" % path)
    return part


def row_bytes(bar: CandleBar) -> bytes:
    """Тіло нового рядка — байти писаря SSOT без EOL."""
    return serialize_bar(bar).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def day_of_ms(open_ms: int) -> str:
    """UTC-доба open_time_ms — ім'я part-файла (`YYYYMMDD`)."""
    return dt.datetime.fromtimestamp(open_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")


def part_path(data_root: str, sym_dir: str, tf_s: int, day: str) -> str:
    return os.path.join(data_root, sym_dir, "tf_%d" % tf_s, "part-%s.jsonl" % day)


def list_part_days(data_root: str, sym_dir: str, tf_s: int) -> List[str]:
    """Доби part-файлів TF за зростанням: лише строгі імена `part-YYYYMMDD.jsonl` верхнього рівня."""
    folder = os.path.join(data_root, sym_dir, "tf_%d" % tf_s)
    if not os.path.isdir(folder):
        return []
    days = []
    for name in os.listdir(folder):
        match = PART_NAME_RE.match(name)
        if match and os.path.isfile(os.path.join(folder, name)):
            days.append(match.group(1))
    return sorted(days)


# ── Бекап і заміна (ADR-0095 §3.8 п.3, §3.9 кроки 2 і 5) ───────────────────────────────────────────────────────────
BACKUP_DIR_PREFIX = "_backup_adr0095_"  # каталог старих inode поруч із part-файлами; `_backup_before_rebuild/` — чужий


def utc_stamp() -> str:
    """Мітка часу бекапу й каталогу старих inode: `YYYYMMDDTHHMMSSZ`."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_files(paths: Iterable[str], backup_dir: str, *, data_root: str, tag: str, stamp: str) -> Tuple[str, str]:
    """tgz part-файлів і sha256-маніфест у `backup_dir` поза `data_root`; повертає (tgz, маніфест).

    Імена в архіві — відносно батька `data_root` (`data_v3/XAU_USD/tf_14400/...`), тож відкат —
    `tar xzf <tgz> -C <батько data_root>`. Файл, якого ще немає, лягає в маніфест як None (відкат = видалити).
    Після запису архів перечитується: кожен член — sha з маніфесту, кількість членів — кількість наявних файлів
    (гейт G2), інакше RuntimeError BACKUP_VERIFY_FAILED.
    """
    root = os.path.abspath(data_root)
    if os.path.commonpath([root, os.path.abspath(backup_dir)]) == root:
        raise ValueError("BACKUP_INSIDE_DATA_ROOT backup_dir=%s data_root=%s" % (backup_dir, data_root))
    os.makedirs(backup_dir, exist_ok=True)
    arc_root = os.path.dirname(root)
    tgz = os.path.join(backup_dir, "%s_%s.tgz" % (tag, stamp))
    manifest_path = os.path.join(backup_dir, "%s_%s.sha256.json" % (tag, stamp))
    files: Dict[str, Optional[Dict[str, Any]]] = {}
    with tarfile.open(tgz, "x:gz") as tar:
        for path in sorted(set(os.path.abspath(p) for p in paths)):
            arcname = os.path.relpath(path, arc_root).replace(os.sep, "/")
            if not os.path.exists(path):
                files[arcname] = None
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            info = tar.gettarinfo(path, arcname=arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            files[arcname] = {"sha256": sha256_hex(data), "size": len(data)}
    _verify_backup(tgz, files)
    with open(manifest_path, "x", encoding="utf-8") as fh:
        json.dump({"tag": tag, "stamp": stamp, "arc_root": arc_root, "files": files}, fh, ensure_ascii=False, indent=1)
    return tgz, manifest_path


def _verify_backup(tgz: str, files: Dict[str, Optional[Dict[str, Any]]]) -> None:
    expected = {name: meta["sha256"] for name, meta in files.items() if meta is not None}
    with tarfile.open(tgz, "r:gz") as tar:
        members = tar.getmembers()
        actual = {}
        for member in members:
            extracted = tar.extractfile(member)
            actual[member.name] = sha256_hex(extracted.read()) if extracted is not None else None
    if len(members) != len(expected) or actual != expected:
        raise RuntimeError("BACKUP_VERIFY_FAILED tgz=%s members=%d expected=%d" % (tgz, len(members), len(expected)))


def replace_part(path: str, new_bytes: bytes, *, stage_sha256: str, stamp: str) -> Optional[str]:
    """Замінити part-файл байтами, зібраними в staging; старий inode — у `<tf>/_backup_adr0095_<stamp>/`.

    Байти мусять мати sha staging (`stage_sha256`), інакше ValueError PARTFILE_STAGE_SHA_MISMATCH до запису.
    Нового part-файла ще немає — режим і власник беруться з найновішого part-файла того самого каталогу TF, а якщо
    TF у символу ще нема (натив D1 для символу, що мав лише M1, — GER30 26.09), — з part-файла іншого TF того самого
    символу; каталог TF тоді створюється з режимом і власником каталогу того TF (гучно PARTFILE_TF_DIR_CREATED), бо
    живий писар мусить у нього дописувати. Символ без жодного part-файла — FileNotFoundError PARTFILE_NO_SIBLING.
    Бекапу для нового файла немає (None). Заміна, fsync, власник і перечитування — `jsonl_rewrite.replace_bytes_atomic`.
    """
    if sha256_hex(new_bytes) != stage_sha256:
        raise ValueError("PARTFILE_STAGE_SHA_MISMATCH path=%s — байти не ті, що в staging" % path)
    folder = os.path.dirname(path)
    like = None
    if not os.path.exists(path):
        like = _newest_part(folder) or _part_of_other_tf(folder)
        if like is None:
            raise FileNotFoundError("PARTFILE_NO_SIBLING path=%s — власника і режим нового файла нема звідки взяти" % path)
        if not os.path.isdir(folder):
            _make_dir_like(folder, os.path.dirname(like))
    return replace_bytes_atomic(path, new_bytes, backup_dir=os.path.join(folder, BACKUP_DIR_PREFIX + stamp), like=like)


def _newest_part(folder: str) -> Optional[str]:
    if not os.path.isdir(folder):
        return None
    names = [name for name in sorted(os.listdir(folder)) if PART_NAME_RE.match(name)]
    return os.path.join(folder, names[-1]) if names else None


def _part_of_other_tf(folder: str) -> Optional[str]:
    """Найновіший part-файл іншого каталогу TF того самого символу (`<sym>/tf_*`), M1 першим."""
    sym_dir = os.path.dirname(folder)
    if not os.path.isdir(sym_dir):
        return None
    tf_dirs = sorted((name for name in os.listdir(sym_dir) if name.startswith("tf_") and name[3:].isdigit()),
                     key=lambda name: int(name[3:]))
    for name in tf_dirs:
        like = _newest_part(os.path.join(sym_dir, name))
        if like is not None:
            return like
    return None


def _make_dir_like(folder: str, model_dir: str) -> None:
    os.mkdir(folder)
    st = os.stat(model_dir)
    os.chmod(folder, st.st_mode & 0o7777)
    if hasattr(os, "chown") and (os.stat(folder).st_uid, os.stat(folder).st_gid) != (st.st_uid, st.st_gid):
        try:
            os.chown(folder, st.st_uid, st.st_gid)
        except OSError as exc:
            os.rmdir(folder)
            raise PermissionError("PARTFILE_TF_DIR_OWNER_NOT_PRESERVED dir=%s model=%s — %s" % (folder, model_dir, exc))
    log.warning("PARTFILE_TF_DIR_CREATED dir=%s mode=%o like=%s", folder, st.st_mode & 0o7777, model_dir)


# ── Рейка ADR-0098 §3.6: записувачі SSOT доведено зупинені (скан /proc, не прапорець оператора) ────────────────────
PROD_DATA_ROOTS = ("/opt/smc-v3/data_v3",)  # прод-каталог SSOT (ранбук вікна ADR-0095 §3.9)
# Модулі, що пишуть part-файли: живий інжест і ws (supervisor smc:smc-fxcm/smc-preview/smc-ws, Binance) та ремонтні
# інструменти. smc-ticks (`tick_publisher_fxcm`) SSOT не пише і у вікні не зупиняється.
WRITER_ARGV_MARKERS = (
    "app.main", "runtime.ws.ws_server", "tick_preview_worker", "binance_ingest_worker", "m1_poller",
    "m1_ingestion_worker", "broker_sidecar", "tools.rebuild_from_m1", "fetch_tf_backfill", "repair_m1_gaps",
    "dedup_jsonl_lastwins", "sort_jsonl_by_open_ms", "purge_derived_window", "settle_prev",
)
_ACCESS_MODE_MASK = 0o3  # O_ACCMODE Linux: 1 — O_WRONLY, 2 — O_RDWR


class WritersGuardRefused(RuntimeError):
    """Записувачі SSOT не доведено зупиненими — заміна part-файлів заборонена."""


def writers_guard(data_root: str, *, proc_root: str = "/proc", prod_roots: Sequence[str] = PROD_DATA_ROOTS) -> None:
    """Прод-каталог: відмова, якщо /proc показує живого записувача (argv) або FD part-файла, відкритий на запис.

    Поза прод-каталогом (копія, репетиція, Windows) — гучний пропуск WARNING WRITERS_GUARD_SKIPPED: живі процеси
    пишуть прод, а не копію. На проді без /proc чи з процесом, чиї FD не прочитати (запуск не від root), — відмова:
    доказу немає.
    """
    root = _posix_abspath(data_root)
    if not any(root == prod or root.startswith(prod + "/") for prod in prod_roots):
        log.warning("WRITERS_GUARD_SKIPPED data_root=%s reason=not_prod_path — живі записувачі пишуть прод", data_root)
        return
    if not os.path.isdir(proc_root):
        raise WritersGuardRefused("WRITERS_GUARD_REFUSED reason=no_proc data_root=%s" % data_root)
    # /proc/<pid>/fd показує справжній шлях — каталог порівнюється без символьних лінків
    found, uninspectable = find_live_writers(_posix_abspath(os.path.realpath(data_root)), proc_root=proc_root)
    for pid, reason, detail in found:
        log.error("WRITERS_GUARD_LIVE pid=%d reason=%s detail=%s", pid, reason, detail)
    if found or uninspectable:
        raise WritersGuardRefused(
            "WRITERS_GUARD_REFUSED live=%d fd_uninspectable_pids=%s — зупиніть smc:smc-ws smc:smc-preview smc:smc-fxcm "
            "і ремонтні інструменти; FD інших користувачів читає лише root" % (len(found), uninspectable[:10])
        )
    log.info("WRITERS_GUARD_OK data_root=%s", data_root)


def find_live_writers(
    data_root: str, *, proc_root: str = "/proc", readlink: Optional[Callable[[str], str]] = None
) -> Tuple[List[Tuple[int, str, str]], List[int]]:
    """([(pid, argv|write_fd, деталь)], [pid, чиї FD не прочитати]) — без себе і своїх предків (самозбіг argv).

    `readlink` — ціль FD (типово `os.readlink`); тести підставляють таблицю замість символьних лінків /proc.
    """
    readlink = readlink if readlink is not None else os.readlink
    skip = _ancestor_pids(proc_root)
    found: List[Tuple[int, str, str]] = []
    uninspectable: List[int] = []
    for name in sorted(os.listdir(proc_root)):
        if not name.isdigit() or int(name) in skip:
            continue
        pid, pid_dir = int(name), os.path.join(proc_root, name)
        try:
            with open(os.path.join(pid_dir, "cmdline"), "rb") as fh:
                argv = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            if "python" in argv and any(marker in argv for marker in WRITER_ARGV_MARKERS):
                found.append((pid, "argv", argv[:160]))
            for fd in os.listdir(os.path.join(pid_dir, "fd")):
                target = _fd_write_target(pid_dir, fd, data_root, readlink)
                if target is not None:
                    found.append((pid, "write_fd", target))
        except FileNotFoundError:  # процес завершився між переліком і читанням — не записувач
            continue
        except PermissionError:
            uninspectable.append(pid)
    return found, uninspectable


def _fd_write_target(pid_dir: str, fd: str, data_root: str, readlink: Callable[[str], str]) -> Optional[str]:
    """Шлях part-каталогу TF під `data_root`, відкритий цим FD на запис; інакше None."""
    try:
        target = readlink(os.path.join(pid_dir, "fd", fd)).replace("\\", "/")
        with open(os.path.join(pid_dir, "fdinfo", fd), encoding="utf-8") as fh:
            flags_line = next((line for line in fh if line.startswith("flags:")), "flags:\t0")
    except FileNotFoundError:  # FD закрито між переліком і читанням
        return None
    if not target.startswith(data_root + "/") or "/tf_" not in target:
        return None
    flags = int(flags_line.split(":", 1)[1].strip(), 8)
    return target if flags & _ACCESS_MODE_MASK else None


def _ancestor_pids(proc_root: str) -> Set[int]:
    """Власний pid і предки: sudo/timeout-обгортки власного запуску несуть у argv ім'я інструмента (пастка pgrep -f)."""
    pids: Set[int] = set()
    pid = os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            with open(os.path.join(proc_root, str(pid), "stat"), "rb") as fh:
                stat_line = fh.read().decode("utf-8", "replace")
        except OSError:  # немає /proc/<pid>/stat (не Linux або процес зник) — ланцюг предків закінчено
            break
        pid = int(stat_line.rsplit(")", 1)[1].split()[1])  # ppid — друге поле після «(comm)»
    return pids


def _posix_abspath(path: str) -> str:
    """Абсолютний шлях у POSIX-формі без літери диска — прод-шлях порівнюється однаково на Linux і в тестах."""
    return re.sub(r"^[A-Za-z]:", "", os.path.abspath(path).replace("\\", "/"))
