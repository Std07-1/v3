"""Впорядкування part-файлів за open_time_ms — перестановка рядків, і нічого більше.

Найнебезпечніше тут не «не відсортувалось», а «відсортувалось і тихо змінило дані».
Тому кожен тест перевіряє одну з властивостей, порушення якої було б непомітним:
мультимножина рядків та сама, текст рядка байт-у-байт той самий, а записи з однаковим
`open_time_ms` зберігають взаємний порядок.

Останнє — несуче. Обидва дедупи (`disk_layer._dedup_open_ms` і `uds._ensure_sorted_dedup`)
стабільно сортують за ключем, а переможця обирає єдиний `core.model.bar_choice.choose_better_bar`
(ADR-0094): complete → final src → не-partial → ts, і лише при ПОВНІЙ нічиї — пізніший у
вхідному порядку. Тобто порядок рядків вирішує саме тоді, коли записи нерозрізненні за якістю; перестановка дублікатів
мовчки переписала б, який з них потрапить на графік. Near-dedup D1 (поріг tf_ms//12)
залежить лише від ключів і до порядку рядків байдужий.
"""
from __future__ import annotations

import json
import os
from collections import Counter

import pytest

from tools.repair import sort_jsonl_by_open_ms as srt

M1_MS = 60_000
BASE_MS = 1_780_000_000_000 // M1_MS * M1_MS


def _line(open_ms: int, marker: str = "a") -> str:
    return json.dumps({
        "symbol": "XAU/USD", "tf_s": 60, "open_time_ms": open_ms,
        "close_time_ms": open_ms + M1_MS, "o": 1.0, "h": 2.0, "low": 0.5,
        "c": 1.5, "v": 10.0, "complete": True, "src": marker,
    })


def _write(tmp_path, lines) -> str:
    d = tmp_path / "XAU_USD" / "tf_60"
    d.mkdir(parents=True, exist_ok=True)
    path = str(d / "part-20260601.jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for ln in lines:
            fh.write(ln + "\n")
    return path


def _seam(n_new: int = 50, n_old: int = 50):
    """Порядок, який лишає сіяння: свіжа сторінка, за нею старіша."""
    newer = [_line(BASE_MS + i * M1_MS) for i in range(n_old, n_old + n_new)]
    older = [_line(BASE_MS + i * M1_MS) for i in range(0, n_old)]
    return newer + older


def test_seam_is_reordered_into_time_order(tmp_path):
    path = _write(tmp_path, _seam())
    ordered, bars, inversions = srt.plan_file(path)
    assert bars == 100 and inversions == 1
    keys = [srt.open_ms_of(ln) for ln in ordered]
    assert keys == sorted(keys)


def test_result_is_exactly_a_permutation(tmp_path):
    """Жодного рядка не додано, не втрачено і не переписано."""
    original = _seam()
    path = _write(tmp_path, original)
    ordered, _bars, _inv = srt.plan_file(path)
    assert Counter(ordered) == Counter(original)


def test_duplicate_open_ms_keeps_relative_order(tmp_path):
    """Стабільність: і first-wins, і last-wins після сортування беруть той самий запис."""
    dup_ms = BASE_MS + 10 * M1_MS
    original = [
        _line(BASE_MS + 40 * M1_MS),
        _line(dup_ms, "перший"),
        _line(BASE_MS + 5 * M1_MS),
        _line(dup_ms, "другий"),
        _line(dup_ms, "третій"),
    ]
    path = _write(tmp_path, original)
    ordered, _bars, _inv = srt.plan_file(path)
    dups = [json.loads(ln)["src"] for ln in ordered if srt.open_ms_of(ln) == dup_ms]
    assert dups == ["перший", "другий", "третій"]


def test_already_sorted_file_is_not_rewritten(tmp_path):
    """23 тисячі файлів переважно впорядковані — їх не можна чіпати взагалі."""
    path = _write(tmp_path, [_line(BASE_MS + i * M1_MS) for i in range(10)])
    ordered, _bars, inversions = srt.plan_file(path)
    assert ordered is None and inversions == 0


def test_line_text_is_preserved_byte_for_byte(tmp_path):
    """Ніякого re-serialize JSON: порядок ключів і формат чисел лишаються як були."""
    odd = '{"open_time_ms": %d, "zzz": 1, "o": 1.50, "src": "history"}' % (BASE_MS + M1_MS)
    original = [odd, _line(BASE_MS)]
    path = _write(tmp_path, original)
    ordered, _bars, _inv = srt.plan_file(path)
    srt.rewrite_atomic(path, ordered)
    assert srt.read_lines(path) == [_line(BASE_MS), odd]


def test_rewrite_is_atomic_and_leaves_a_backup(tmp_path):
    original = _seam()
    path = _write(tmp_path, original)
    ordered, _bars, _inv = srt.plan_file(path)
    backup = srt.rewrite_atomic(path, ordered)
    assert os.path.isfile(backup)
    assert srt.read_lines(backup) == original, "бекап мусить бути ДОПАТЧЕВИМ вмістом"
    assert not os.path.exists(path + ".tmp")
    assert Counter(srt.read_lines(path)) == Counter(original)


@pytest.mark.skipif(os.name == "nt", reason="права доступу POSIX")
def test_rewrite_keeps_file_mode(tmp_path):
    """Лише перестановка рядків: режим доступу оригіналу мусить зберегтись."""
    path = _write(tmp_path, _seam())
    os.chmod(path, 0o666)
    ordered, _bars, _inv = srt.plan_file(path)
    srt.rewrite_atomic(path, ordered)
    assert os.stat(path).st_mode & 0o777 == 0o666


def test_unparsable_line_blocks_the_file(tmp_path):
    """Контроль: файл, який ми не можемо повністю пояснити, не переписуємо."""
    path = _write(tmp_path, [_line(BASE_MS + M1_MS), '{"немає": "ключа"}', _line(BASE_MS)])
    before = srt.read_lines(path)
    ordered, _bars, _inv = srt.plan_file(path)
    assert ordered is None
    assert srt.read_lines(path) == before


def test_commit_without_writers_stopped_is_refused(tmp_path, monkeypatch):
    """Перепис під живим writer'ом губить бари (os.replace відчіпляє відкритий FD)."""
    monkeypatch.setattr("sys.argv", ["sort_jsonl_by_open_ms", "--commit", "--root", str(tmp_path)])
    assert srt.main() == 2


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    original = _seam()
    path = _write(tmp_path, original)
    monkeypatch.setattr("sys.argv", ["sort_jsonl_by_open_ms", "--root", str(tmp_path)])
    assert srt.main() == 0
    assert srt.read_lines(path) == original


def test_commit_fixes_only_unsorted_files(tmp_path, monkeypatch):
    seam_path = _write(tmp_path, _seam())
    clean_dir = tmp_path / "US30" / "tf_60"
    clean_dir.mkdir(parents=True)
    clean_path = str(clean_dir / "part-20260601.jsonl")
    clean_lines = [_line(BASE_MS + i * M1_MS) for i in range(10)]
    with open(clean_path, "w", encoding="utf-8", newline="\n") as fh:
        for ln in clean_lines:
            fh.write(ln + "\n")

    monkeypatch.setattr(
        "sys.argv",
        ["sort_jsonl_by_open_ms", "--commit", "--writers-stopped", "--root", str(tmp_path)],
    )
    assert srt.main() == 0
    keys = [srt.open_ms_of(ln) for ln in srt.read_lines(seam_path)]
    assert keys == sorted(keys)
    assert srt.read_lines(clean_path) == clean_lines
    assert not [p for p in os.listdir(clean_dir) if ".bak." in p], "чистий файл не мусить мати бекапу"


@pytest.mark.parametrize("n_old,n_new", [(1, 1), (1, 500), (500, 1)])
def test_seam_shapes(tmp_path, n_old, n_new):
    """Шов будь-якої форми лишається перестановкою."""
    original = _seam(n_new=n_new, n_old=n_old)
    path = _write(tmp_path, original)
    ordered, _bars, _inv = srt.plan_file(path)
    assert Counter(ordered) == Counter(original)
    keys = [srt.open_ms_of(ln) for ln in ordered]
    assert keys == sorted(keys)


def test_explicit_dry_run_flag_works_and_changes_nothing(tmp_path, monkeypatch):
    """Докстрінг документує --dry-run — отже команда з нього мусить запускатись."""
    original = _seam()
    path = _write(tmp_path, original)
    monkeypatch.setattr("sys.argv", ["sort_jsonl_by_open_ms", "--dry-run", "--root", str(tmp_path)])
    assert srt.main() == 0
    assert srt.read_lines(path) == original


def test_commit_together_with_dry_run_is_refused(tmp_path, monkeypatch):
    """Контроль: суперечлива пара прапорців — відмова, а не мовчазний вибір одного з них."""
    original = _seam()
    path = _write(tmp_path, original)
    monkeypatch.setattr(
        "sys.argv",
        ["sort_jsonl_by_open_ms", "--commit", "--dry-run", "--writers-stopped", "--root", str(tmp_path)],
    )
    assert srt.main() == 2
    assert srt.read_lines(path) == original


def test_file_never_disappears_during_rewrite(tmp_path, monkeypatch):
    """Читач не мусить спіймати мить, коли part-файла за його іменем немає.

    Ловимо це так: підміняємо os.replace і в момент виклику перевіряємо, що шлях
    призначення ще/вже існує під своїм іменем.
    """
    original = _seam()
    path = _write(tmp_path, original)
    ordered, _bars, _inv = srt.plan_file(path)

    real_replace = os.replace
    seen = []

    def spy(src, dst):
        seen.append((str(src), str(dst), os.path.exists(path)))
        return real_replace(src, dst)

    monkeypatch.setattr(srt.os, "replace", spy)
    backup = srt.rewrite_atomic(path, ordered)

    assert len(seen) == 1, "має бути рівно одна підміна — саме tmp -> part"
    src, dst, existed = seen[0]
    assert src.endswith(".tmp") and dst == path
    assert existed, "у мить підміни part-файл мусить існувати"
    assert srt.read_lines(backup) == original
    assert Counter(srt.read_lines(path)) == Counter(original)

