"""Побайтна модель part-файла (ADR-0095 S7.0, `tools.repair.partfile_io`): що прочитано, те й записується назад.

Заміна S7 переносить рядки поза областю дії байт у байт разом з EOL — у XAU/XAG більшість H4 з CRLF, трапляються
файли без переводу в кінці, рядки чужого символу і нерозбірні. Новий рядок — рівно як у писаря SSOT.
"""
from __future__ import annotations

import json

from core.model.bars import CandleBar
from runtime.store.ssot_jsonl import JsonlAppender
from tools.repair import partfile_io as pio

H1_MS = 3_600_000
OPEN_MS = 1_772_989_200_000  # Нд 08.03.2026 17:00 UTC


def _row(symbol: str, open_ms: int, c: float = 1.5) -> bytes:
    return json.dumps({"symbol": symbol, "tf_s": 3600, "open_time_ms": open_ms, "close_time_ms": open_ms + H1_MS,
                       "o": 1.0, "h": 2.0, "low": 0.5, "c": c, "v": 3.0, "complete": True, "src": "derived"},
                      separators=(",", ":")).encode()


def _write(tmp_path, name: str, data: bytes):
    folder = tmp_path / "XAU_USD" / "tf_3600"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def test_mixed_eol_no_trailing_newline_round_trips_exactly(tmp_path):
    data = _row("XAU/USD", OPEN_MS) + b"\r\n" + _row("XAU/USD", OPEN_MS + H1_MS) + b"\n" + _row("XAU/USD", OPEN_MS + 2 * H1_MS)
    path = _write(tmp_path, "part-20260308.jsonl", data)
    part = pio.load_part(str(path), "XAU_USD")
    assert [line.eol for line in part.lines] == [pio.CRLF, pio.LF, b""]
    assert part.to_bytes() == data
    assert part.size == len(data) and part.sha256 == pio.sha256_hex(data)
    assert part.eol_style() == pio.CRLF


def test_foreign_unparsable_and_blank_lines_are_not_own(tmp_path):
    data = b"\n".join([
        _row("XAU/USD", OPEN_MS),
        _row("XAG/USD", OPEN_MS),  # чужий символ, той самий ключ
        b'{"no_key": 1}',
        b"not json",
        b"",
        _row("XAU/USD", OPEN_MS + H1_MS).replace(b'"symbol":"XAU/USD",', b""),  # легасі без symbol — свій
    ]) + b"\n"
    part = pio.load_part(str(_write(tmp_path, "part-20260308.jsonl", data)), "XAU_USD")
    assert [line.own_key for line in part.lines] == [OPEN_MS, None, None, None, None, OPEN_MS + H1_MS]
    assert part.lines[1].foreign and part.lines[1].obj is not None
    assert part.to_bytes() == data


def test_empty_and_missing_files(tmp_path):
    empty = pio.load_part(str(_write(tmp_path, "part-20260308.jsonl", b"")), "XAU_USD")
    assert empty.exists and empty.lines == [] and empty.to_bytes() == b"" and empty.eol_style() == pio.LF
    missing = pio.load_part(str(tmp_path / "XAU_USD" / "tf_3600" / "part-20260309.jsonl"), "XAU_USD")
    assert not missing.exists and missing.sha256 is None and missing.lines == []


def test_only_strict_top_level_part_names_are_part_files(tmp_path):
    _write(tmp_path, "part-20260308.jsonl", b"")
    _write(tmp_path, "part-20260308.jsonl.bak.1758000000", b"")
    _write(tmp_path, "part-20260309.jsonl.tmp", b"")
    _write(tmp_path, "part-2026031.jsonl", b"")
    backup = tmp_path / "XAU_USD" / "tf_3600" / "_backup_before_rebuild"
    backup.mkdir()
    (backup / "part-20260310.jsonl").write_bytes(b"")
    assert pio.list_part_days(str(tmp_path), "XAU_USD", 3600) == ["20260308"]
    assert pio.list_part_days(str(tmp_path), "XAU_USD", 14400) == []


def test_new_row_bytes_equal_the_ssot_writer(tmp_path):
    bar = CandleBar(symbol="XAU/USD", tf_s=3600, open_time_ms=OPEN_MS, close_time_ms=OPEN_MS + H1_MS, o=5130.1,
                    h=5131.25, low=5129.0, c=5130.7, v=812.0, complete=True, src="derived",
                    extensions={"partial": True, "note": "ґ"})
    writer = JsonlAppender(root=str(tmp_path))
    writer.append(bar)
    writer.close()
    written = (tmp_path / "XAU_USD" / "tf_3600" / ("part-%s.jsonl" % pio.day_of_ms(OPEN_MS))).read_bytes()
    assert written.rstrip(b"\r\n") == pio.row_bytes(bar)
    assert pio.part_path(str(tmp_path), "XAU_USD", 3600, "20260308").endswith("part-20260308.jsonl")
