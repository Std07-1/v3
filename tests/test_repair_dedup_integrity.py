"""Ремонтний дедуп part-файлів не має права змінювати те, чого не мусить.

Навіщо цей файл. Перед SSOT-дедупом 97 файлів на проді (ADR-0094 §7 п.4–5) ремонтні шляхи виявились
тихо небезпечними: `dedup_jsonl_lastwins` мовчки викидав нерозбірні рядки й пересеріалізовував JSON
переможців; `dedup_derived_jsonl` обирав переможця власним рангом джерела, не бачачи partial;
`htf_rebuild_from_fxcm.rewrite_range` при перепису всього TF лишав ПЕРШИЙ запис дубліката і викидав
нерозбірні рядки з усієї історії; replay брав останній рядок. Кожен міг лишити на диску (або відтворити)
не той бар, що показують читачі. Тести поведінкові (переможець ремонту == переможець читача) і, крім
контролів, падають на коді до 2026-09-14.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from runtime.ingest.replay import _read_m1_bars_from_disk
from runtime.store.uds import _ensure_sorted_dedup
from tools import dedup_derived_jsonl
from tools.repair import dedup_jsonl_lastwins as dedup
from tools.repair.htf_rebuild_from_fxcm import _read_all_bars_raw, rewrite_range
from tools.repair.jsonl_rewrite import read_lines

OPEN_MS = 1_774_974_960_000
TF_MS = 180_000


def _bar(marker, *, open_ms=OPEN_MS, partial=None, complete=True, src="derived"):
    bar = {"symbol": "XAU/USD", "tf_s": 180, "open_time_ms": open_ms, "close_time_ms": open_ms + TF_MS,
           "o": 1.0, "h": 2.0, "low": 0.5, "c": 1.5, "v": 10.0, "complete": complete, "src": src,
           "marker": marker}
    if partial is not None:
        bar["extensions"] = {"partial": partial}
    return bar


def _write(directory: Path, lines, name="part-20260601.jsonl") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(line + "\n" for line in lines))
    return path


def _backups(path: Path):
    return sorted(p.name for p in path.parent.iterdir() if ".bak." in p.name)


# ── dedup_jsonl_lastwins ─────────────────────────────────────────────────────
def test_winner_line_is_written_byte_for_byte(tmp_path, capsys):
    """Порядок ключів, пробіли й формат чисел — як їх записав writer, а не як їх бачить json.dumps."""
    odd_winner = '{"open_time_ms": %d, "zzz": 1, "o": 1.50, "complete": true, "src": "derived"}' % OPEN_MS
    stale = json.dumps(_bar("stale", complete=False))
    other = '{"src":"derived",  "open_time_ms": %d, "c": 2.000}' % (OPEN_MS + TF_MS)
    path = _write(tmp_path, [other, stale, odd_winner])
    assert dedup.dedup_file(path) == (3, 2, 1)
    assert read_lines(str(path)) == [odd_winner, other]


def test_unparsable_line_blocks_the_rewrite(tmp_path, capsys):
    """Нерозбірний рядок — байти SSOT, яких інструмент не розуміє: не викидаємо мовчки, а зупиняємось."""
    lines = [json.dumps(_bar("whole")), '{"open_time_ms": 17749749', json.dumps(_bar("later"))]
    path = _write(tmp_path, lines)
    assert dedup.dedup_file(path) == (3, 3, 0)
    assert read_lines(str(path)) == lines
    assert _backups(path) == []
    assert "DEDUP_UNPARSABLE" in capsys.readouterr().out


def test_line_without_integer_open_ms_blocks_the_rewrite(tmp_path, capsys):
    """Ключ-рядок читачі пропускають; раніше дедуп змішував його з цілими ключами і падав на sorted()."""
    lines = [json.dumps(_bar("a")), json.dumps(dict(_bar("b"), open_time_ms=str(OPEN_MS))), json.dumps(_bar("c"))]
    path = _write(tmp_path, lines)
    assert dedup.dedup_file(path) == (3, 3, 0)
    assert read_lines(str(path)) == lines


def test_blank_line_is_not_a_duplicate(tmp_path, capsys):
    """Порожній рядок раніше рахувався дублікатом і запускав перепис файла без жодного дубліката."""
    path = tmp_path / "part-20260601.jsonl"
    path.write_text(json.dumps(_bar("a")) + "\n\n" + json.dumps(_bar("b", open_ms=OPEN_MS + TF_MS)) + "\n",
                    encoding="utf-8")
    assert dedup.dedup_file(path) == (2, 2, 0)
    assert _backups(path) == []


def test_file_without_duplicates_is_untouched_and_quiet(tmp_path, capsys):
    """Дедуп проходить тисячі чистих файлів (dedup_derived_jsonl --all): мовчки і без бекапів."""
    path = _write(tmp_path, [json.dumps(_bar("a")), json.dumps(_bar("b", open_ms=OPEN_MS + TF_MS))])
    before = path.read_bytes()
    assert dedup.dedup_file(path) == (2, 2, 0)
    assert path.read_bytes() == before and _backups(path) == []
    assert capsys.readouterr().out == ""


def test_rewrite_leaves_backup_with_the_original_bytes(tmp_path, capsys):
    lines = [json.dumps(_bar("whole", partial=False)), json.dumps(_bar("partial", partial=True))]
    path = _write(tmp_path, lines)
    original = path.read_bytes()
    dedup.dedup_file(path)
    (backup,) = _backups(path)
    assert (tmp_path / backup).read_bytes() == original
    assert not (tmp_path / (path.name + ".tmp")).exists()


@pytest.mark.skipif(os.name == "nt", reason="права доступу POSIX")
def test_rewrite_keeps_file_mode(tmp_path, capsys):
    path = _write(tmp_path, [json.dumps(_bar("a")), json.dumps(_bar("b"))])
    os.chmod(path, 0o666)
    dedup.dedup_file(path)
    assert os.stat(path).st_mode & 0o777 == 0o666


def test_dry_run_reports_what_commit_would_do_and_writes_nothing(tmp_path, capsys):
    path = _write(tmp_path, [json.dumps(_bar("a")), json.dumps(_bar("b"))])
    before = path.read_bytes()
    assert dedup.dedup_file(path, dry_run=True) == (2, 1, 1)
    assert path.read_bytes() == before and _backups(path) == []


def test_cli_exit_code_says_a_file_was_refused(tmp_path, monkeypatch, capsys):
    """Оператор скрипту ремонту мусить отримати не 0, якщо хоч один файл не переписано."""
    path = _write(tmp_path, [json.dumps(_bar("a")), "not json", json.dumps(_bar("b"))])
    monkeypatch.setattr("sys.argv", ["dedup_jsonl_lastwins", "--file", str(path)])
    assert dedup.main() == 1
    assert "refused_unparsable=1" in capsys.readouterr().out


# ── dedup_derived_jsonl: без власного вибирача ───────────────────────────────
DERIVED_GROUPS = {
    # Старий ранг: однакове джерело + пізніший complete → брав partial, який читачі відкидають.
    "partial_last": ([_bar("whole", partial=False), _bar("partial", partial=True)], "whole"),
    # Старий ранг: history > derived, навіть якщо history — незавершений бар.
    "history_preview_first": ([_bar("history_preview", src="history", complete=False), _bar("derived_final")],
                              "derived_final"),
    "full_tie": ([_bar("earlier"), _bar("later")], "later"),
}


def _reader_winner(bars):
    result, _geom = _ensure_sorted_dedup([dict(b) for b in bars], tf_ms=TF_MS)
    assert len(result) == 1
    return result[0]["marker"]


@pytest.mark.parametrize("name", sorted(DERIVED_GROUPS))
def test_derived_dedup_keeps_what_readers_show(tmp_path, capsys, name):
    bars, expected = DERIVED_GROUPS[name]
    path = _write(tmp_path / "XAU_USD" / "tf_180", [json.dumps(b) for b in bars])
    assert dedup_derived_jsonl.dedup_symbol(str(tmp_path), "XAU/USD", dry_run=False) == 1
    kept = [json.loads(line)["marker"] for line in read_lines(str(path))]
    assert kept == [expected] == [_reader_winner(bars)]


def test_htf_rewrite_range_keeps_what_readers_show_outside_the_range(tmp_path):
    """Перепис TF переписує ВСЮ історію; дублікат поза діапазоном раніше розвʼязувався першим записом."""
    h4_ms = 14_400_000
    base = 1_774_972_800_000 // h4_ms * h4_ms
    tf_dir = tmp_path / "XAU_USD" / "tf_14400"
    stale_partial = dict(_bar("partial", open_ms=base, partial=True, src="history"), tf_s=14400)
    whole = dict(_bar("whole", open_ms=base, partial=False, src="history"), tf_s=14400)
    _write(tf_dir, [json.dumps(stale_partial), json.dumps(whole)])
    fxcm = [dict(_bar("fxcm", open_ms=base + h4_ms, src="history"), tf_s=14400)]
    result = rewrite_range(str(tmp_path), "XAU/USD", 14400, fxcm, base + h4_ms, base + h4_ms, dry_run=False)
    assert result["status"] == "committed" and result["dup_removed"] == 1
    kept = {b["open_time_ms"]: b["marker"] for b in _read_all_bars_raw(str(tf_dir))}
    assert kept == {base: "whole", base + h4_ms: "fxcm"}
    assert kept[base] == _reader_winner([stale_partial, whole])


def test_htf_rewrite_range_refuses_a_tf_with_an_unparsable_line(tmp_path):
    """Перепис TF пише файли лише з розібраних барів — нерозбірний рядок раніше зникав з усієї історії."""
    tf_dir = tmp_path / "XAU_USD" / "tf_14400"
    path = _write(tf_dir, [json.dumps(_bar("a")), '{"open_time_ms": 17749', json.dumps(_bar("b", open_ms=OPEN_MS + 1))])
    before = path.read_bytes()
    with pytest.raises(ValueError, match="HTF_REWRITE_UNPARSABLE"):
        rewrite_range(str(tmp_path), "XAU/USD", 14400, [], OPEN_MS, OPEN_MS, dry_run=False)
    assert path.read_bytes() == before


def test_replay_replays_the_candle_the_chart_shows(tmp_path):
    """Replay відтворює свічку графіка, а не останній рядок (той може бути partial)."""
    whole = dict(_bar("whole", partial=False, src="history"), tf_s=60)
    partial = dict(_bar("partial", partial=True, src="history"), tf_s=60)
    _write(tmp_path / "XAU_USD" / "tf_60", [json.dumps(whole), json.dumps(partial)])
    replayed = _read_m1_bars_from_disk(str(tmp_path), "XAU/USD")
    assert [b["marker"] for b in replayed] == ["whole"] == [_reader_winner([whole, partial])]


def test_derived_dedup_dry_run_writes_nothing(tmp_path, capsys):
    bars, _expected = DERIVED_GROUPS["partial_last"]
    path = _write(tmp_path / "XAU_USD" / "tf_180", [json.dumps(b) for b in bars])
    before = path.read_bytes()
    assert dedup_derived_jsonl.dedup_symbol(str(tmp_path), "XAU/USD", dry_run=True) == 1
    assert path.read_bytes() == before
