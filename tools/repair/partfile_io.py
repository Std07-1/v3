"""tools/repair/partfile_io.py — побайтна модель part-файлів SSOT для заміни ADR-0095 S7 (спільна з ADR-0098 C3).

Файл — послідовність рядків (body, eol): body — байти без переводу рядка, eol — LF, CRLF або порожній (лише в
останнього рядка без переводу). Склеювання назад дає ті самі байти, і це перевіряється при читанні. Рядок, якого
заміна не чіпає (чужого символу за правилом читача `disk_layer.is_foreign_row`, нерозбірний, порожній, поза
областю дії), переноситься байт у байт разом зі своїм EOL: у XAU/XAG CRLF мають 557 з 705 і 547 з 703 part-файлів
H4, а нормалізація EOL змінила б рядки, яких ремонт не стосується. Новий рядок — як у писаря SSOT
(`ssot_jsonl.serialize_bar`) з EOL файла.

Part-файл — лише `part-YYYYMMDD.jsonl` верхнього рівня каталогу TF, як у читача (`DiskLayer.list_parts`); сусіди
`.bak.<ts>` і каталоги `_backup_*` — не part-файли. Ім'я — UTC-доба open_time_ms.

Запис (ADR-0095 §3.8 п.3): `backup_files` — tgz і sha256-маніфест поза data_root до запису; `replace_part` —
атомарна заміна зі старим inode в `<tf>/_backup_adr0095_<ts>/`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import tarfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.model.bars import CandleBar
from runtime.store.layers.disk_layer import is_foreign_row
from runtime.store.ssot_jsonl import serialize_bar
from tools.repair.jsonl_rewrite import replace_bytes_atomic

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
    Нового part-файла ще немає — режим і власник беруться з найновішого part-файла того самого каталогу TF, бекапу
    немає (None). Заміна, fsync, власник і перечитування — `jsonl_rewrite.replace_bytes_atomic`.
    """
    if sha256_hex(new_bytes) != stage_sha256:
        raise ValueError("PARTFILE_STAGE_SHA_MISMATCH path=%s — байти не ті, що в staging" % path)
    folder = os.path.dirname(path)
    like = None
    if not os.path.exists(path):
        siblings = [name for name in sorted(os.listdir(folder)) if PART_NAME_RE.match(name)]
        if not siblings:
            raise FileNotFoundError("PARTFILE_NO_SIBLING path=%s — власника і режим нового файла нема звідки взяти" % path)
        like = os.path.join(folder, siblings[-1])
    return replace_bytes_atomic(path, new_bytes, backup_dir=os.path.join(folder, BACKUP_DIR_PREFIX + stamp), like=like)
