"""tools/repair/partfile_io.py — побайтна модель part-файлів SSOT для заміни ADR-0095 S7 (спільна з ADR-0098 C3).

Файл — послідовність рядків (body, eol): body — байти без переводу рядка, eol — LF, CRLF або порожній (лише в
останнього рядка без переводу). Склеювання назад дає ті самі байти, і це перевіряється при читанні. Рядок, якого
заміна не чіпає (чужого символу за правилом читача `disk_layer.is_foreign_row`, нерозбірний, порожній, поза
областю дії), переноситься байт у байт разом зі своїм EOL: у XAU/XAG CRLF мають 557 з 705 і 547 з 703 part-файлів
H4, а нормалізація EOL змінила б рядки, яких ремонт не стосується. Новий рядок — як у писаря SSOT
(`ssot_jsonl.serialize_bar`) з EOL файла.

Part-файл — лише `part-YYYYMMDD.jsonl` верхнього рівня каталогу TF, як у читача (`DiskLayer.list_parts`); сусіди
`.bak.<ts>` і каталоги `_backup_*` — не part-файли. Ім'я — UTC-доба open_time_ms.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.model.bars import CandleBar
from runtime.store.layers.disk_layer import is_foreign_row
from runtime.store.ssot_jsonl import serialize_bar

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
