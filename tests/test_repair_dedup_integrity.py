"""Ремонтний дедуп part-файлів не має права змінювати те, чого не мусить.

Навіщо цей файл. Перед SSOT-дедупом 97 файлів на проді (ADR-0094 §7 п.4–5) ремонтні шляхи виявились
тихо небезпечними: `dedup_jsonl_lastwins` мовчки викидав нерозбірні рядки й пересеріалізовував JSON
переможців; `dedup_derived_jsonl` обирав переможця власним рангом джерела, не бачачи partial;
`htf_rebuild_from_fxcm.rewrite_range` при перепису всього TF лишав ПЕРШИЙ запис дубліката і викидав
нерозбірні рядки з усієї історії; replay брав останній рядок. Кожен міг лишити на диску (або відтворити)
не той бар, що показують читачі.

Адверсаріальне ревʼю перших фіксів додало ще три класи: відмова ремонту губилась у «0 прибрано» і rc=0;
`rewrite_range` з явним діапазоном почав перетирати бари диска ПОЗА ним; тести не відрізняли вибирач від
last-/first-wins. Тому групи тут перевіряються в ОБОХ порядках рядків, а кожна відмова — аж до rc.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from runtime.ingest.replay import _read_m1_bars_from_disk
from runtime.store.layers.disk_layer import _select_newest_keys
from runtime.store.uds import _ensure_sorted_dedup
from tools import dedup_derived_jsonl
from tools.rebuild_from_m1 import DedupRefused, dedup_derived_in_ranges
from tools.repair import dedup_jsonl_lastwins as dedup
from tools.repair import jsonl_rewrite
from tools.repair.htf_rebuild_from_fxcm import _read_all_bars_raw, rewrite_range
from tools.repair.jsonl_rewrite import read_lines

OPEN_MS = 1_774_974_960_000  # 2026-03-31 16:36 UTC
TF_MS = 180_000
H4_MS = 14_400_000
H4_BASE = 1_774_972_800_000  # 2026-03-31 16:00 UTC, на сітці H4
DAY_MS = 86_400_000


def _bar(marker, *, open_ms=OPEN_MS, partial=None, complete=True, src="derived", tf_s=180):
    bar = {"symbol": "XAU/USD", "tf_s": tf_s, "open_time_ms": open_ms, "close_time_ms": open_ms + tf_s * 1000,
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


def _reader_winner(bars):
    result, _geom = _ensure_sorted_dedup([dict(b) for b in bars])
    assert len(result) == 1
    return result[0]["marker"]


def _disk_reader_markers(tf_dir: Path):
    """Що покаже читач UDS для кожного ключа каталогу — його ж кодом вибору вікна і дедупу."""
    paths = sorted(str(p) for p in tf_dir.glob("part-*.jsonl"))
    window = _select_newest_keys(paths, None, None, 10**9, final_only=False, skip_preview=False, final_sources=None)
    result, _geom = _ensure_sorted_dedup(window)
    return {b["open_time_ms"]: b["marker"] for b in result}


# Той самий ключ у двох порядках рядків: last-wins і first-wins хибні кожен в одному з них.
WHOLE_VS_PARTIAL = {
    "partial_first": (lambda **kw: [_bar("partial", partial=True, **kw), _bar("whole", partial=False, **kw)]),
    "partial_last": (lambda **kw: [_bar("whole", partial=False, **kw), _bar("partial", partial=True, **kw)]),
}


# ── dedup_jsonl_lastwins ─────────────────────────────────────────────────────
def test_winner_line_is_written_byte_for_byte(tmp_path, capsys):
    """Порядок ключів, пробіли й формат чисел — як їх записав writer, а не як їх бачить json.dumps."""
    odd_winner = '{"open_time_ms": %d, "zzz": 1, "o": 1.50, "complete": true, "src": "derived"}' % OPEN_MS
    stale = json.dumps(_bar("stale", complete=False))
    other = '{"src":"derived",  "open_time_ms": %d, "c": 2.000}' % (OPEN_MS + TF_MS)
    path = _write(tmp_path, [other, stale, odd_winner])
    assert dedup.dedup_file(path) == (3, 2, 1)
    assert read_lines(str(path)) == [odd_winner, other]


@pytest.mark.parametrize("bad", ['{"open_time_ms": 17749749', json.dumps({"no_key": 1})])
def test_unparsable_line_blocks_the_rewrite(tmp_path, capsys, bad):
    """Нерозбірний рядок — байти SSOT, яких інструмент не розуміє: не викидаємо мовчки, а зупиняємось."""
    lines = [json.dumps(_bar("whole")), bad, json.dumps(_bar("later"))]
    path = _write(tmp_path, lines)
    assert dedup.dedup_file(path) == (3, 3, 0)
    assert read_lines(str(path)) == lines and _backups(path) == []
    out = capsys.readouterr().out
    assert "DEDUP_UNPARSABLE" in out
    out.encode("cp1251")  # відмова мусить дійти до оператора і через Windows-консоль, не впасти на print


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
    path = _write(tmp_path, [json.dumps(b) for b in WHOLE_VS_PARTIAL["partial_last"]()])
    original = path.read_bytes()
    dedup.dedup_file(path)
    (backup,) = _backups(path)
    assert (tmp_path / backup).read_bytes() == original
    assert not (tmp_path / (path.name + ".tmp")).exists()


def test_second_rewrite_in_the_same_second_keeps_the_first_backup(tmp_path, monkeypatch, capsys):
    """Бекап — єдина копія прибраних рядків; раніше другий перепис тієї ж секунди його затирав."""
    monkeypatch.setattr(jsonl_rewrite.time, "time", lambda: 1_757_800_001.0)
    path = _write(tmp_path, [json.dumps(_bar("a")), json.dumps(_bar("b"))])
    original = path.read_bytes()
    dedup.dedup_file(path)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(_bar("c")) + "\n")
    dedup.dedup_file(path)
    backups = _backups(path)
    assert len(backups) == 2
    assert original in {(tmp_path / name).read_bytes() for name in backups}


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


def test_cli_refuses_to_write_without_writers_stopped(tmp_path, monkeypatch, capsys):
    """os.replace відчіпляє відкритий FD живого writer'а — його дописи пішли б у .bak."""
    path = _write(tmp_path, [json.dumps(_bar("a")), json.dumps(_bar("b"))])
    before = path.read_bytes()
    monkeypatch.setattr("sys.argv", ["dedup_jsonl_lastwins", "--file", str(path)])
    assert dedup.main() == 2
    assert path.read_bytes() == before


def test_cli_exit_code_says_a_file_was_refused(tmp_path, monkeypatch, capsys):
    """Оператор скрипту ремонту мусить отримати не 0, якщо хоч один файл не переписано."""
    path = _write(tmp_path, [json.dumps(_bar("a")), "not json", json.dumps(_bar("b"))])
    monkeypatch.setattr("sys.argv", ["dedup_jsonl_lastwins", "--file", str(path), "--writers-stopped"])
    assert dedup.main() == 1
    assert "refused_unparsable=1" in capsys.readouterr().out


# ── dedup_derived_jsonl: без власного вибирача, відмови не губляться ─────────
DERIVED_GROUPS = {
    **{name: (make(), "whole") for name, make in WHOLE_VS_PARTIAL.items()},
    # Старий ранг: history > derived, навіть якщо history — незавершений бар.
    "history_preview_first": ([_bar("history_preview", src="history", complete=False), _bar("derived_final")],
                              "derived_final"),
    "full_tie": ([_bar("earlier"), _bar("later")], "later"),
}


@pytest.mark.parametrize("name", sorted(DERIVED_GROUPS))
def test_derived_dedup_keeps_what_readers_show(tmp_path, capsys, name):
    bars, expected = DERIVED_GROUPS[name]
    path = _write(tmp_path / "XAU_USD" / "tf_180", [json.dumps(b) for b in bars])
    assert dedup_derived_jsonl.dedup_symbol(str(tmp_path), "XAU/USD", dry_run=False) == (1, 0)
    kept = [json.loads(line)["marker"] for line in read_lines(str(path))]
    assert kept == [expected] == [_reader_winner(bars)]


def test_derived_dedup_dry_run_writes_nothing(tmp_path, capsys):
    path = _write(tmp_path / "XAU_USD" / "tf_180", [json.dumps(b) for b in WHOLE_VS_PARTIAL["partial_last"]()])
    before = path.read_bytes()
    assert dedup_derived_jsonl.dedup_symbol(str(tmp_path), "XAU/USD", dry_run=True) == (1, 0)
    assert path.read_bytes() == before


def test_derived_dedup_refusal_is_not_zero_duplicates(tmp_path, monkeypatch, capsys):
    """Гейт ADR-0054 «dry-run = 0 дублікатів» не мусить пройти на файлі, який інструмент відмовився читати."""
    lines = [json.dumps(_bar("a")), json.dumps(_bar("b")), '{"open_time_ms": 17749']
    _write(tmp_path / "XAU_USD" / "tf_300", lines)
    assert dedup_derived_jsonl.dedup_symbol(str(tmp_path), "XAU/USD", dry_run=True) == (0, 1)
    monkeypatch.setattr("sys.argv", ["dedup_derived_jsonl", "--symbols", "XAU/USD", "--data-root", str(tmp_path),
                                     "--dry-run"])
    assert dedup_derived_jsonl.main() == 1


def test_derived_cli_refuses_to_write_without_writers_stopped(tmp_path, monkeypatch, capsys):
    path = _write(tmp_path / "XAU_USD" / "tf_180", [json.dumps(b) for b in WHOLE_VS_PARTIAL["partial_last"]()])
    before = path.read_bytes()
    monkeypatch.setattr("sys.argv", ["dedup_derived_jsonl", "--symbols", "XAU/USD", "--data-root", str(tmp_path)])
    assert dedup_derived_jsonl.main() == 2
    assert path.read_bytes() == before


def test_rebuild_dedup_on_finish_fails_loud_after_deduping_the_rest(tmp_path, capsys):
    """rebuild_from_m1 --force: відмовлений файл лишає дублікати перебудови — це не «DEDUP_TOTAL 0, rc 0»."""
    day_ms = OPEN_MS // DAY_MS * DAY_MS
    refused = _write(tmp_path / "XAU_USD" / "tf_300", [json.dumps(_bar("a")), json.dumps(_bar("b")), "not json"],
                     name="part-20260331.jsonl")
    fine = _write(tmp_path / "XAU_USD" / "tf_900", [json.dumps(_bar("a")), json.dumps(_bar("b"))],
                  name="part-20260331.jsonl")
    refused_before = refused.read_bytes()
    with pytest.raises(DedupRefused) as caught:
        dedup_derived_in_ranges(str(tmp_path), {"XAU/USD": (day_ms, day_ms + DAY_MS)})
    assert caught.value.paths == [str(refused)] and caught.value.dupes_removed == 1
    assert refused.read_bytes() == refused_before
    assert len(read_lines(str(fine))) == 1


# ── htf_rebuild_from_fxcm.rewrite_range ──────────────────────────────────────
def _h4(marker, open_ms, **kw):
    return _bar(marker, open_ms=open_ms, tf_s=14400, **kw)


@pytest.mark.parametrize("order", sorted(WHOLE_VS_PARTIAL))
def test_htf_rewrite_range_keeps_what_readers_show_outside_the_range(tmp_path, order):
    """Перепис TF переписує ВСЮ історію: дублікат поза діапазоном — тим самим вибирачем, і на диску один запис."""
    tf_dir = tmp_path / "XAU_USD" / "tf_14400"
    group = WHOLE_VS_PARTIAL[order](open_ms=H4_BASE, src="history", tf_s=14400)
    _write(tf_dir, [json.dumps(b) for b in group], name="part-20260331.jsonl")
    expected = _disk_reader_markers(tf_dir)[H4_BASE]
    fxcm = [_h4("fxcm", H4_BASE + H4_MS, src="history")]
    result = rewrite_range(str(tmp_path), "XAU/USD", 14400, fxcm, H4_BASE + H4_MS, H4_BASE + H4_MS, dry_run=False)
    assert result["status"] == "committed" and result["dup_removed"] == 1
    assert _disk_reader_markers(tf_dir) == {H4_BASE: expected, H4_BASE + H4_MS: "fxcm"}
    assert expected == "whole"
    on_disk = _read_all_bars_raw(str(tf_dir))
    assert len(on_disk) == len({b["open_time_ms"] for b in on_disk}), "дублікати не мусять лишитись на диску"


def test_htf_rewrite_range_leaves_disk_bars_outside_an_explicit_range(tmp_path):
    """Батч FXCM ширший за явне вікно: бари диска поза вікном оператор міняти не просив."""
    tf_dir = tmp_path / "XAU_USD" / "tf_14400"
    disk = [_h4("disk_%d" % i, H4_BASE + i * H4_MS, src="history") for i in range(4)]
    _write(tf_dir, [json.dumps(b) for b in disk], name="part-20260331.jsonl")
    fxcm = [_h4("fxcm_%d" % i, H4_BASE + i * H4_MS, src="history") for i in range(5)]
    lo, hi = H4_BASE + H4_MS, H4_BASE + 2 * H4_MS
    result = rewrite_range(str(tmp_path), "XAU/USD", 14400, fxcm, lo, hi, dry_run=False)
    assert result["status"] == "committed"
    markers = {b["open_time_ms"]: b["marker"] for b in _read_all_bars_raw(str(tf_dir))}
    assert [markers[H4_BASE + i * H4_MS] for i in range(5)] == ["disk_0", "fxcm_1", "fxcm_2", "disk_3", "fxcm_4"]
    assert (result["kept_outside"], result["fxcm_inserted"], result["dup_removed"]) == (2, 3, 2)


@pytest.mark.parametrize("bad", ['{"open_time_ms": 17749', json.dumps({"no_key": 1})])
def test_htf_rewrite_range_refuses_a_tf_with_an_unparsable_line(tmp_path, bad):
    """Перепис TF пише файли лише з розібраних барів — нерозбірний рядок раніше зникав з усієї історії.

    Відмова — статус, а не виняток: CLI рахує її як помилку валідації, оновлює Redis для вже закомічених
    TF і пише звіт, замість обірватись посеред прогону.
    """
    tf_dir = tmp_path / "XAU_USD" / "tf_14400"
    path = _write(tf_dir, [json.dumps(_h4("a", H4_BASE)), bad, json.dumps(_h4("b", H4_BASE + H4_MS))])
    before = path.read_bytes()
    result = rewrite_range(str(tmp_path), "XAU/USD", 14400, [], H4_BASE, H4_BASE, dry_run=False)
    assert result["status"] == "validation_error" and "HTF_REWRITE_UNPARSABLE" in result["error"]
    assert path.read_bytes() == before


def _cross_file_tie(tf_dir: Path, open_ms: int, tf_s: int):
    """Аномалія, якої не створює жоден writer: той самий ключ у двох part-файлах, повна нічия."""
    _write(tf_dir, [json.dumps(_bar("own_day_file", open_ms=open_ms, src="history", tf_s=tf_s))],
           name="part-20260331.jsonl")
    _write(tf_dir, [json.dumps(_bar("next_day_file", open_ms=open_ms, src="history", tf_s=tf_s))],
           name="part-20260401.jsonl")


def test_htf_rewrite_range_cross_file_tie_matches_readers(tmp_path):
    tf_dir = tmp_path / "XAU_USD" / "tf_14400"
    key = H4_BASE  # 2026-03-31 — «своя» доба ключа саме part-20260331
    _cross_file_tie(tf_dir, key, 14400)
    shown = _disk_reader_markers(tf_dir)[key]
    far = H4_BASE - 1000 * H4_MS
    rewrite_range(str(tmp_path), "XAU/USD", 14400, [], far, far, dry_run=False)
    assert _disk_reader_markers(tf_dir)[key] == shown


# ── replay ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("order", sorted(WHOLE_VS_PARTIAL))
def test_replay_replays_the_candle_the_chart_shows(tmp_path, order):
    """Replay відтворює свічку графіка, а не останній чи перший рядок (котрийсь із них — partial)."""
    group = WHOLE_VS_PARTIAL[order](src="history", tf_s=60)
    _write(tmp_path / "XAU_USD" / "tf_60", [json.dumps(b) for b in group])
    replayed = _read_m1_bars_from_disk(str(tmp_path), "XAU/USD")
    assert [b["marker"] for b in replayed] == ["whole"] == [_reader_winner(group)]


def test_replay_cross_file_tie_matches_readers(tmp_path):
    tf_dir = tmp_path / "XAU_USD" / "tf_60"
    key = OPEN_MS // 60_000 * 60_000
    _cross_file_tie(tf_dir, key, 60)
    replayed = {b["open_time_ms"]: b["marker"] for b in _read_m1_bars_from_disk(str(tmp_path), "XAU/USD")}
    assert replayed[key] == _disk_reader_markers(tf_dir)[key]
