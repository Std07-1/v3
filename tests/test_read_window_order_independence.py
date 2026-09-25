"""ADR-0094 P2: вікно читання — N найновіших РІЗНИХ ключів, а не останні N рядків.

До P2 обидва читачі брали останні N рядків (TAIL — з кінця файлів, RANGE — через deque(maxlen)).
Part-файли не зобовʼязані бути відсортованими: `tools/fetch_tf_backfill` лишає шов на кожному кроці
ланцюжка сіяння (435 таких файлів на проді). На шві вікно мало дірку — на XAG part-20260218 заміряно
775 M1-барів відставання — а читання з since мовчки губило найсвіжіші бари.

Інваріант, який тут перевіряється (ADR-0094 §3.1): результат читання не залежить від взаємного
порядку рядків з РІЗНИМИ open_time_ms. Порядок записів ОДНОГО ключа — це порядок дозапису, і він
значущий (нічия вибирача → пізніший запис), тому властивісний тест його зберігає.
"""
from __future__ import annotations

import json
import logging
import random

import pytest

from runtime.store.layers import disk_layer as dl_mod
from runtime.store.layers.disk_layer import DiskLayer
from runtime.store.uds import _ensure_sorted_dedup

M1 = 60_000
DAY = 86_400_000
BASE = 1_780_012_800_000  # 2026-05-29 00:00 UTC — початок доби, щоб бари лягали у свій part-файл
SYMBOL = "XAU/USD"


def _day_key(open_ms):
    import datetime as dt
    return dt.datetime.fromtimestamp(open_ms / 1000, tz=dt.timezone.utc).strftime("%Y%m%d")


def _bar(open_ms, marker="x", *, partial=None):
    bar = {"symbol": SYMBOL, "tf_s": 60, "open_time_ms": open_ms, "close_time_ms": open_ms + M1,
           "o": 1.0, "h": 2.0, "low": 0.5, "c": 1.5, "v": 10.0, "complete": True, "src": "history",
           "marker": marker}
    if partial is not None:
        bar["extensions"] = {"partial": partial}
    return bar


def _write_corpus(root, bars_in_file_order):
    """Розкласти бари по part-файлах їхньої доби, зберігаючи відносний порядок усередині доби."""
    by_day = {}
    for bar in bars_in_file_order:
        by_day.setdefault(_day_key(bar["open_time_ms"]), []).append(bar)
    d = root / SYMBOL.replace("/", "_") / "tf_60"
    d.mkdir(parents=True, exist_ok=True)
    for day, bars in by_day.items():
        (d / ("part-%s.jsonl" % day)).write_text("".join(json.dumps(b) + "\n" for b in bars), encoding="utf-8")
    return DiskLayer(str(root))


def _opens(bars):
    return [b["open_time_ms"] for b in bars]


def _seam_day(n=1379, max_index=984):
    """Одна доба M1 у порядку, який лишає сіяння: максимум усередині, хвіст файла старіший."""
    opens = [BASE + i * M1 for i in range(n)]
    return [_bar(o) for o in opens[n - max_index - 1:] + opens[:n - max_index - 1]]


# ── виміряні класи дефекту ───────────────────────────────────────────────────
def test_tail_window_on_seam_returns_the_newest_bars(tmp_path):
    """XAG part-20260218: останні 300 РЯДКІВ відставали від максимуму на 775 барів."""
    layer = _write_corpus(tmp_path, _seam_day())
    bars, _geom = layer.read_window_with_geom(SYMBOL, 60, 300, use_tail=True)
    assert _opens(bars) == [BASE + i * M1 for i in range(1379 - 300, 1379)]


def test_range_window_on_seam_returns_the_newest_bars_before_to(tmp_path):
    layer = _write_corpus(tmp_path, _seam_day())
    to = BASE + 1000 * M1
    bars, _geom = layer.read_window_with_geom(SYMBOL, 60, 200, to_open_ms=to, use_tail=False)
    assert _opens(bars) == [BASE + i * M1 for i in range(801, 1001)]


@pytest.mark.parametrize("use_tail", [True, False])
def test_since_on_seam_loses_nothing(tmp_path, use_tail):
    """Колишнє репро: з since TAIL повертав 39 барів замість 89 і мовчки губив найсвіжіші."""
    newer = [_bar(BASE + i * M1) for i in range(50, 100)]
    older = [_bar(BASE + i * M1) for i in range(0, 50)]
    layer = _write_corpus(tmp_path, newer + older)
    bars, _geom = layer.read_window_with_geom(SYMBOL, 60, 1000, since_open_ms=BASE + 10 * M1, use_tail=use_tail)
    assert _opens(bars) == [BASE + i * M1 for i in range(11, 100)]


def test_window_spanning_several_days_is_exact(tmp_path):
    day1 = [_bar(BASE + i * M1) for i in range(1440)]
    day2 = [_bar(BASE + DAY + i * M1) for i in range(1440)]
    # Межа вікна (560 барів) падає всередину day1 — саме там порядок рядків і вирішував.
    random.Random(7).shuffle(day1)
    random.Random(8).shuffle(day2)
    layer = _write_corpus(tmp_path, day1 + day2)
    bars, _geom = layer.read_window_with_geom(SYMBOL, 60, 2000, use_tail=True)
    expected = [BASE + i * M1 for i in range(1440 - 560, 1440)] + [BASE + DAY + i * M1 for i in range(1440)]
    assert _opens(bars) == expected


def test_limit_counts_distinct_keys_and_keeps_whole_duplicate_groups(tmp_path):
    """Вибирач мусить бачити всю групу дублікатів; дублікати не з'їдають місця у вікні."""
    corpus = [_bar(BASE + i * M1) for i in range(10)]
    corpus.insert(3, _bar(BASE + 9 * M1, "whole", partial=False))
    corpus.append(_bar(BASE + 9 * M1, "partial", partial=True))
    layer = _write_corpus(tmp_path, corpus)
    window = dl_mod._select_newest_keys(layer.list_parts(SYMBOL, 60), None, None, 3,
                                        final_only=False, skip_preview=False, final_sources=None)
    assert sorted(set(_opens(window))) == [BASE + 7 * M1, BASE + 8 * M1, BASE + 9 * M1]
    # Порядок файла для цього ключа: "whole" вставлено на позицію 3, тобто раніше за початковий "x".
    assert [b["marker"] for b in window if b["open_time_ms"] == BASE + 9 * M1] == ["whole", "x", "partial"]
    tail, _geom = layer.read_window_with_geom(SYMBOL, 60, 3, use_tail=True)
    # "whole" і "x" обидва цілі → нічия → пізніший у файлі ("x"); partial програє обом.
    assert len(tail) == 3 and tail[-1]["marker"] == "x"


def test_reading_stops_before_older_part_files(tmp_path, monkeypatch):
    """Старіші доби не читаються, коли вікно вже заповнене — ціна P2 обмежена одним файлом."""
    layer = _write_corpus(tmp_path, [_bar(BASE + d * DAY + i * M1) for d in range(4) for i in range(100)])
    opened = []
    real_open = open

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(dl_mod, "open", spy, raising=False)
    bars, _geom = layer.read_window_with_geom(SYMBOL, 60, 150, use_tail=True)
    assert len(bars) == 150
    assert len(opened) == 2, opened


def test_scrollback_does_not_open_days_after_to(tmp_path, monkeypatch):
    """Доба, що починається після `to`, не може дати жодного ключа ≤ to — її файл не відкривається."""
    layer = _write_corpus(tmp_path, [_bar(BASE + d * DAY + i * M1) for d in range(5) for i in range(100)])
    opened = []
    real_open = open

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(dl_mod, "open", spy, raising=False)
    to = BASE + 1 * DAY + 50 * M1
    bars, _geom = layer.read_window_with_geom(SYMBOL, 60, 80, to_open_ms=to, use_tail=False)
    expected = sorted([BASE + i * M1 for i in range(100)] + [BASE + DAY + i * M1 for i in range(51)])[-80:]
    assert _opens(bars) == expected
    # Доба `to` дала 51 ключ (< 80), тож дочитано ще попередню; три пізніші доби не відкривались.
    assert sorted(opened) == sorted(layer.list_parts(SYMBOL, 60)[:2]), opened


@pytest.mark.parametrize("name, reason", [
    ("part-latest.jsonl", "not_yyyymmdd"),
    ("part-20260231.jsonl", "invalid_date"),  # вісім цифр, але 31 лютого не існує
])
def test_non_canonical_part_name_is_still_read_and_reported_once(tmp_path, caplog, name, reason):
    """Файл з імʼям, з якого доби не взяти, не пропускається за датою; про саме імʼя — WARN раз на шлях (I5)."""
    d = tmp_path / "XAU_USD" / "tf_60"
    d.mkdir(parents=True)
    (d / name).write_text(json.dumps(_bar(BASE + 5 * M1)) + "\n", encoding="utf-8")
    layer = DiskLayer(str(tmp_path))
    with caplog.at_level(logging.WARNING, logger="disk_layer"):
        for _ in range(2):
            bars, _geom = layer.read_window_with_geom(SYMBOL, 60, 10, to_open_ms=BASE + 10 * M1)
            assert _opens(bars) == [BASE + 5 * M1]
    reports = [r.getMessage() for r in caplog.records if "DISK_PART_NAME_NONCANONICAL" in r.getMessage()]
    assert len(reports) == 1 and ("reason=%s" % reason) in reports[0], reports


def test_unreadable_part_file_is_loud_and_skipped(tmp_path, caplog):
    layer = _write_corpus(tmp_path, [_bar(BASE + i * M1) for i in range(10)])
    (tmp_path / "XAU_USD" / "tf_60" / ("part-%s.jsonl" % _day_key(BASE + DAY))).mkdir()
    with caplog.at_level(logging.WARNING):
        bars, _geom = layer.read_window_with_geom(SYMBOL, 60, 5, use_tail=True)
    assert _opens(bars) == [BASE + i * M1 for i in range(5, 10)]
    assert "DISK_PART_READ_FAILED" in caplog.text


# ── P2.3: властивість ───────────────────────────────────────────────────────
def _interleave_preserving_key_order(bars, rng):
    """Випадкова перестановка, що зберігає відносний порядок записів ОДНОГО ключа."""
    groups = {}
    for bar in bars:
        groups.setdefault(bar["open_time_ms"], []).append(bar)
    slots = [key for key, members in groups.items() for _ in members]
    rng.shuffle(slots)
    cursors = {key: iter(members) for key, members in groups.items()}
    return [next(cursors[key]) for key in slots]


def _corpus_with_seams_and_duplicates(rng):
    bars = []
    for day in range(3):
        for i in range(0, 1440, 7):
            bars.append(_bar(BASE + day * DAY + i * M1, "d%d_%d" % (day, i)))
    for _ in range(40):
        key = rng.choice(bars)["open_time_ms"]
        bars.append(_bar(key, "dup", partial=rng.choice([True, False, None])))
    return bars


def _read_all(layer, limit, to, since):
    tail, _ = layer.read_window_with_geom(SYMBOL, 60, limit, to_open_ms=to, since_open_ms=since, use_tail=True)
    raw_range, _ = layer.read_window_with_geom(SYMBOL, 60, limit, to_open_ms=to, since_open_ms=since, use_tail=False)
    range_bars, _ = _ensure_sorted_dedup(raw_range)
    return [json.dumps(b, sort_keys=True) for b in tail], [json.dumps(b, sort_keys=True) for b in range_bars]


@pytest.mark.parametrize("seed", range(6))
def test_read_result_does_not_depend_on_order_of_different_keys(tmp_path, seed):
    rng = random.Random(seed)
    corpus = _corpus_with_seams_and_duplicates(rng)
    sorted_layer = _write_corpus(tmp_path / "sorted", sorted(corpus, key=lambda b: b["open_time_ms"]))
    shuffled_layer = _write_corpus(tmp_path / "shuffled", _interleave_preserving_key_order(corpus, rng))
    last = max(b["open_time_ms"] for b in corpus)
    queries = [(50, None, None), (500, None, None), (300, last - DAY, None),
               (1000, None, BASE + DAY // 2), (120, last - 30 * M1, BASE + DAY)]
    for limit, to, since in queries:
        tail_a, range_a = _read_all(sorted_layer, limit, to, since)
        tail_b, range_b = _read_all(shuffled_layer, limit, to, since)
        assert tail_a == tail_b, ("TAIL", limit, to, since)
        assert range_a == range_b, ("RANGE", limit, to, since)
        assert tail_a == range_a, ("TAIL≠RANGE", limit, to, since)
