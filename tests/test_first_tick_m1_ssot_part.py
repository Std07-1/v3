"""Part-файл M1 під ремонтом значень: змінюються лише o/h/low переможця, решта байтів — як була (ADR-0096 §3.3 B, B4).

Навіщо. `rewrite_atomic` пише рядки + "\\n": файл із CRLF чи порожнім рядком після ремонту змінився б у КОЖНОМУ
рядку (у локальному знімку 355 із 415 M1-файлів мають CRLF), а рядок, пересеріалізований іншим стилем (XAG
part-20260213 записаний з ", "/": "), — у кожному байті. Чужий запис у файлі доби означав би, що план
класифікує не той ключ. І головне: патч мусить лягти в рядок, який показують читачі.
"""
from __future__ import annotations

import json

import pytest

from ft_m1_support import DAY, SYMBOL, at, line, ssot_bar, write_part
from runtime.store.layers.disk_layer import _dedup_open_ms, _select_newest_keys
from tools.repair.first_tick_m1.ssot_part import (
    Patch, detect_line_style, lines_bytes, patch_winner_line, render_patched_lines, scan_part,
)
from tools.repair.jsonl_rewrite import read_lines

KEY = at(DAY, 22, 1)
PREV = ssot_bar(KEY, 4055.42, 4093.19, 4055.42, 4092.36, v=31.0)


def test_patch_changes_only_o_h_low_exact_bytes_compact():
    original = line(PREV)
    assert detect_line_style(original) == "compact"
    patched = patch_winner_line(original, 4089.98, 4093.19, 4086.33, add_trading_flat=False)
    expected = original.replace('"o":4055.42', '"o":4089.98').replace('"low":4055.42', '"low":4086.33')
    assert patched == expected and patched != original


def test_patch_keeps_default_separator_style():
    original = json.dumps(PREV, ensure_ascii=False)  # ", " і ": " — як XAG part-20260213
    assert detect_line_style(original) == "default"
    patched = patch_winner_line(original, 4089.98, 4093.19, 4086.33, add_trading_flat=False)
    assert patched == original.replace('"o": 4055.42', '"o": 4089.98').replace('"low": 4055.42', '"low": 4086.33')


def test_patch_trading_flat_appended_to_existing_extensions_or_created_last():
    with_ext = line(dict(PREV, extensions={"calendar_pause_nonflat_anomaly": True}))
    patched = json.loads(patch_winner_line(with_ext, 4092.36, 4092.36, 4092.36, add_trading_flat=True))
    assert list(patched["extensions"].items()) == [("calendar_pause_nonflat_anomaly", True), ("trading_flat", True)]
    assert list(patched)[-1] == "extensions"
    without_ext = patch_winner_line(line(PREV), 4092.36, 4092.36, 4092.36, add_trading_flat=True)
    assert without_ext.endswith(',"src":"history","extensions":{"trading_flat":true}}')


@pytest.mark.parametrize("body, reason", [
    (lambda text: (text + "\r\n").encode(), "not_canonical:crlf"),
    (lambda text: (text + "\n\n" + text.replace("22:01", "x") + "\n").encode(), "not_canonical:blank_line"),
    (lambda text: text.encode(), "not_canonical:no_final_newline"),
])
def test_scan_refuses_non_canonical_file(tmp_path, body, reason):
    path = write_part(tmp_path, DAY, [])
    path.write_bytes(body(line(PREV)))
    scan = scan_part(str(path), SYMBOL, DAY)
    assert (scan.status, scan.refuse_reason, scan.winners) == ("refused", reason, {})


@pytest.mark.parametrize("bar", [
    dict(PREV, open_time_ms=KEY + 86_400_000, close_time_ms=KEY + 86_460_000),
    dict(PREV, symbol="XAG/USD"),
    dict(PREV, tf_s=180),
    dict(PREV, open_time_ms=KEY + 1_000),
])
def test_scan_refuses_foreign_record(tmp_path, bar):
    path = write_part(tmp_path, DAY, [line(PREV), line(bar)])
    scan = scan_part(str(path), SYMBOL, DAY)
    assert scan.status == "refused" and scan.refuse_reason.startswith("foreign_record")


def test_scan_keeps_unparsable_lines_counted_not_refused(tmp_path):
    path = write_part(tmp_path, DAY, [line(PREV), "not json", json.dumps({"no_key": 1})])
    scan = scan_part(str(path), SYMBOL, DAY)
    assert (scan.status, scan.unparsable_lines, list(scan.winners)) == ("ok", 2, [KEY])
    assert lines_bytes(scan.lines) == path.read_bytes()


@pytest.mark.parametrize("order", ["whole_first", "partial_first"])
def test_patched_winner_is_still_the_reader_winner(tmp_path, order):
    """Ловить патч не того рядка: після render_patched_lines читач показує нові o/h/low, partial-рядок недоторканий."""
    whole = dict(PREV, extensions={"partial": False})
    partial = dict(PREV, o=1.0, h=5000.0, low=1.0, extensions={"partial": True})
    bars = [whole, partial] if order == "whole_first" else [partial, whole]
    path = write_part(tmp_path, DAY, [line(b) for b in bars])
    scan = scan_part(str(path), SYMBOL, DAY)
    winner = scan.winners[KEY]
    assert winner.bar["extensions"] == {"partial": False} and winner.members == 2
    new_lines = render_patched_lines(scan.lines, {winner.line_index: Patch(4089.98, 4093.19, 4086.33, False)})
    path.write_bytes(lines_bytes(new_lines))
    window = _select_newest_keys([str(path)], None, None, 10, final_only=False, skip_preview=False, final_sources=None)
    (shown,), _dropped = _dedup_open_ms(window)
    assert (shown["o"], shown["h"], shown["low"], shown["extensions"]) == (4089.98, 4093.19, 4086.33, {"partial": False})
    partial_index = 1 - winner.line_index
    assert read_lines(str(path))[partial_index] == line(partial)
