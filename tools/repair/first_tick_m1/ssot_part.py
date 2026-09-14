"""Part-файл M1 для ремонту значень: канонічність, чужі записи, переможці ключів, стиль рядка, патч (ADR-0096 §3.3 B).

Ремонт змінює у файлі лише o/h/low (і extensions.trading_flat) рядка-переможця; усі інші байти лишаються як були.
Тому файл, який `rewrite_atomic` не може відтворити байт-у-байт (CRLF, порожні рядки, без фінального \\n), і
рядок, чий стиль серіалізації не впізнано, план не переписує — лише рахує.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tools.repair.first_tick_m1.common import MINUTE_MS, TF_S, day_of_ms, sha256_bytes
from tools.repair.jsonl_rewrite import key_groups, open_ms_of, read_lines

# Порядок перевірки: compact — стиль `ssot_jsonl`; default — стиль, яким записано XAG part-20260213 (E12).
LINE_STYLES: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("compact", {"separators": (",", ":"), "ensure_ascii": False}),
    ("default", {"ensure_ascii": False}),
    ("compact_ascii", {"separators": (",", ":")}),
    ("default_ascii", {}),
)


class LineStyleUnknown(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Winner:
    open_ms: int
    line_index: int
    line: str
    bar: Dict[str, Any]
    members: int


@dataclasses.dataclass(frozen=True)
class PartScan:
    status: str  # "ok" | "refused"
    refuse_reason: Optional[str]
    sha256: str
    size: int
    lines: Tuple[str, ...]
    winners: Dict[int, Winner]
    unparsable_lines: int


@dataclasses.dataclass(frozen=True)
class Patch:
    new_o: float
    new_h: float
    new_low: float
    add_trading_flat: bool


def lines_bytes(lines: Sequence[str]) -> bytes:
    """Байти, які запише `rewrite_atomic(path, lines)`."""
    return "".join(text + "\n" for text in lines).encode("utf-8")


def scan_part(path: str, symbol: str, day: dt.date) -> PartScan:
    with open(path, "rb") as fh:
        raw = fh.read()
    sha = sha256_bytes(raw)
    try:
        lines = read_lines(path)
    except UnicodeDecodeError:
        return PartScan("refused", "not_canonical:not_utf8", sha, len(raw), (), {}, 0)
    problem = canonical_problem(raw, lines)
    if problem:
        return PartScan("refused", "not_canonical:" + problem, sha, len(raw), tuple(lines), {}, 0)
    unparsable = 0
    for index, text in enumerate(lines):
        if open_ms_of(text) is None:
            unparsable += 1
        elif not _own_record(json.loads(text), symbol, day):
            return PartScan("refused", "foreign_record:line=%d" % index, sha, len(raw), tuple(lines), {}, unparsable)
    winners = {
        key: Winner(key, group.winner, lines[group.winner], json.loads(lines[group.winner]), len(group.members))
        for key, group in key_groups(lines).items()
    }
    return PartScan("ok", None, sha, len(raw), tuple(lines), winners, unparsable)


def canonical_problem(raw: bytes, lines: Sequence[str]) -> Optional[str]:
    """Чому перепис рядків цього файла не збереже решту байтів; порівняння ще й ловить зміну файла між читаннями."""
    if b"\r" in raw:
        return "crlf"
    if raw and not raw.endswith(b"\n"):
        return "no_final_newline"
    if lines_bytes(lines) != raw:
        return "blank_line"
    return None


def detect_line_style(line: str) -> Optional[str]:
    """Стиль, у якому `json.dumps(json.loads(line))` відтворює рядок байт-у-байт, або None."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    for name, kwargs in LINE_STYLES:
        if json.dumps(obj, **kwargs) == line:
            return name
    return None


def patch_winner_line(line: str, new_o: float, new_h: float, new_low: float, add_trading_flat: bool) -> str:
    """Рядок з новими o/h/low (порядок ключів і стиль незмінні); trading_flat — у кінець extensions або новим ключем."""
    style = detect_line_style(line)
    if style is None:
        raise LineStyleUnknown("FT_PATCH_LINE_STYLE_UNKNOWN line=%r" % line[:120])
    obj = json.loads(line)
    for field, value in (("o", new_o), ("h", new_h), ("low", new_low)):
        if field not in obj:
            raise ValueError("FT_PATCH_FIELD_MISSING field=%s" % field)
        obj[field] = float(value)
    if add_trading_flat:
        if "extensions" not in obj:
            obj["extensions"] = {"trading_flat": True}
        elif isinstance(obj["extensions"], dict):
            obj["extensions"]["trading_flat"] = True
        else:
            raise ValueError("FT_PATCH_EXTENSIONS_NOT_OBJECT")
    return json.dumps(obj, **dict(LINE_STYLES)[style])


def render_patched_lines(lines: Sequence[str], patches: Mapping[int, Patch]) -> List[str]:
    """Новий вміст файла: змінені лише рядки з індексами `patches` — спільне для плану (sha_after) і apply."""
    patched = list(lines)
    for index, patch in patches.items():
        patched[index] = patch_winner_line(lines[index], patch.new_o, patch.new_h, patch.new_low,
                                           patch.add_trading_flat)
    return patched


def _own_record(bar: Dict[str, Any], symbol: str, day: dt.date) -> bool:
    tf_s, open_ms = bar.get("tf_s"), bar["open_time_ms"]
    return (bar.get("symbol") == symbol and isinstance(tf_s, int) and not isinstance(tf_s, bool) and tf_s == TF_S
            and not isinstance(open_ms, bool) and open_ms % MINUTE_MS == 0 and day_of_ms(open_ms) == day)
