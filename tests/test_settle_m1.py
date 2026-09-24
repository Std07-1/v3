"""Settle M1 з архіву брокера (ADR-0103 S2, `tools.repair.settle_m1`): порт синтетики settle_prev/3 (сценарії B, C, W).

B — правила ключів і запис: заміна, вставка (у CRLF-файл без \\n у кінці і в добу без part-файла), пауза, класифікатор,
дублікат ключа, чужий символ, провенанс, гейт до запису, ідемпотентність, прапори вилучення з захистом.
C — ADR-0101: вкладення застарілого краю 21:00 у 20:59, ланцюг, наступник після вікна, межа через діру.
W — тік вихідних не рве ланцюг: неділя від close п'ятниці, як TV H1.
Календар — справжній сезонний XAU/USD з config.json.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from tools.repair import settle_m1

REPO = Path(__file__).resolve().parents[1]
CONFIG = str(REPO / "config.json")
UTC = dt.timezone.utc
PROV = "prev/20260922T0509Z"


def _ms(y, mo, d, h, mi):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def _row(k, o, h, low, c, v, ext=None, symbol="XAU/USD") -> bytes:
    obj = {"symbol": symbol, "tf_s": 60, "open_time_ms": k, "close_time_ms": k + 60000, "o": o, "h": h, "low": low,
           "c": c, "v": v, "complete": True, "src": "history"}
    if ext:
        obj["extensions"] = ext
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def _archive(folder: Path, rows, fetched_at: str, window, chunks=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "XAU_USD_m1.json").write_text(json.dumps(sorted(rows)))
    chunks = chunks if chunks is not None else [{"label": "day", "start": window[0], "end": window[1], "rows": 1}]
    (folder / "meta.json").write_text(json.dumps({"mode": "PREVIOUS_CLOSE", "fetched_at": fetched_at,
                                                  "window": window, "symbols": {"XAU_USD": {"chunks": chunks}}}))
    return folder


def _run(tmp_path, data_root, arc, t_from, t_to, *extra, apply=False):
    report = tmp_path / ("report_%d.json" % len(list(tmp_path.glob("report_*.json"))))
    argv = ["--data-root", str(data_root), "--archive-dir", str(arc), "--config", CONFIG, "--symbols", "XAU/USD",
            "--from", t_from, "--to", t_to, "--report", str(report), *extra]
    if apply:
        argv += ["--apply", "--backup-dir", str(tmp_path / "backups")]
    rc = settle_m1.main(argv)
    return rc, json.loads(report.read_text(encoding="utf-8"))


def _stats(report):
    return report["symbols"]["XAU_USD"]["stats"]


def _rows(path: Path):
    return {json.loads(x)["open_time_ms"]: json.loads(x) for x in path.read_bytes().splitlines() if x.strip()}


# ── B ────────────────────────────────────────────────────────────────────────────────────────────────────────────
K2059, K2201, K2202 = _ms(2026, 9, 21, 20, 59), _ms(2026, 9, 21, 22, 1), _ms(2026, 9, 21, 22, 2)
K2203, K_NEW_DAY = _ms(2026, 9, 21, 22, 3), _ms(2026, 9, 22, 0, 5)
K_PAUSE, K_REOPEN_FLAT = _ms(2026, 9, 21, 21, 30), _ms(2026, 9, 22, 22, 0)
K2200, K2201B = _ms(2026, 9, 20, 22, 0), _ms(2026, 9, 20, 22, 1)
K_ONLY_FLAT, K_OUR_STUB = _ms(2026, 9, 21, 22, 4), _ms(2026, 9, 21, 22, 0)
B_WINDOW = ["2026-09-20T22:00:00+00:00", "2026-09-22T23:00:00+00:00"]
B_ARCHIVE = [
    [K2059, 4343.62, 4343.76, 4342.62, 4342.62, 258.0],   # лише o інакше → REPLACE
    [K2201, 4342.62, 4348.66, 4342.62, 4347.95, 352.0],   # REPLACE (обидва рядки дубліката)
    [K2202, 4347.95, 4349.0, 4347.5, 4348.5, 300.0],      # SAME
    [K2203, 4348.5, 4348.9, 4348.1, 4348.7, 120.0],       # INSERT у CRLF-файл без \n у кінці
    [K_NEW_DAY, 4350.0, 4350.5, 4349.5, 4350.2, 80.0],    # INSERT у добу без part-файла
    [K_PAUSE, 4340.0, 4340.0, 4340.0, 4340.0, 2.0],       # пауза → не вставляється
    [K_REOPEN_FLAT, 4350.2, 4350.2, 4350.2, 4350.2, 3.0], # плаский v=3 на відкритті → класифікатор не пише
    [K2200, 4380.77, 4380.77, 4373.41, 4374.2, 388.0],    # REPLACE (v)
    [K2201B, 4374.2, 4375.07, 4373.38, 4374.21, 479.0],   # REPLACE (o)
]
B_0921 = [
    _row(K2059, 4343.64, 4343.76, 4342.62, 4342.62, 258.0),
    _row(K2201, 4342.62, 4348.66, 4342.62, 4347.95, 358.0),
    _row(K2201, 1.0, 1.0, 1.0, 1.0, 1.0, symbol="XAG/USD"),  # чужий символ з тим самим ключем
    _row(K2201, 4342.62, 4348.66, 4342.62, 4347.95, 358.0),  # дублікат ключа
    _row(K2202, 4347.95, 4349.0, 4347.5, 4348.5, 300.0),
    _row(K_ONLY_FLAT, 4348.7, 4348.7, 4348.7, 4348.7, 1.0, {"trading_flat": True}),  # лише в нас, плаский
    _row(K_OUR_STUB, 4342.62, 4342.62, 4342.62, 4342.62, 3.0, {"trading_flat": True}),  # лише в нас, заглушка відкриття
]


@pytest.fixture()
def b_tree(tmp_path):
    tf60 = tmp_path / "data_v3" / "XAU_USD" / "tf_60"
    tf60.mkdir(parents=True)
    (tf60 / "part-20260921.jsonl").write_bytes(b"\r\n".join(B_0921))  # CRLF, без \n у кінці
    (tf60 / "part-20260920.jsonl").write_bytes(
        _row(K2200, 4380.77, 4380.77, 4373.41, 4374.2, 394.0, {"session_open_rebuilt": True, "open_before": 4375.62})
        + b"\n" + _row(K2201B, 4374.22, 4375.07, 4373.38, 4374.21, 479.0) + b"\n")
    arc = _archive(tmp_path / "archive", B_ARCHIVE, "2026-09-22T05:09:00+00:00", B_WINDOW)
    return tmp_path, tmp_path / "data_v3", tf60, arc


def test_b_gate_refuses_thin_archive_before_any_write(b_tree):
    tmp_path, data_root, tf60, arc = b_tree
    before = (tf60 / "part-20260921.jsonl").read_bytes()
    rc, report = _run(tmp_path, data_root, arc, "2026-09-20T22:00", "2026-09-22T23:00", apply=True)
    assert rc == 2 and report["archive_gate_verdict"] == "REFUSED"
    assert any(p.startswith("COVERAGE_BELOW_MIN") for p in report["symbols"]["XAU_USD"]["gate"]["problems"])
    assert (tf60 / "part-20260921.jsonl").read_bytes() == before and not (tf60 / "part-20260922.jsonl").exists()


def test_b_apply_replaces_inserts_and_keeps_bytes_of_untouched_rows(b_tree):
    tmp_path, data_root, tf60, arc = b_tree
    rc, report = _run(tmp_path, data_root, arc, "2026-09-20T22:00", "2026-09-22T23:00", "--baseline-archive", str(arc),
                      apply=True)
    stats = _stats(report)
    assert rc == 0 and report["verify_problems"] == []
    assert (stats["REPLACE"], stats["SAME"], stats["INSERT"]) == (4, 1, 2)
    assert stats["SETTLE_BROKER_PAUSE_SKIPPED"] == 1 and stats["INSERT_DROPPED_BY_CLASSIFIER"] == 1
    assert stats["SETTLE_KEY_ONLY_OURS"] == 2
    data = (tf60 / "part-20260921.jsonl").read_bytes()
    assert data.endswith(b"\r\n") and not data.endswith(b"\r\n\r\n")  # EOL файла додано у кінець рівно один
    parts = data[:-2].split(b"\r\n")
    assert len(parts) == 8 and json.loads(parts[-1])["open_time_ms"] == K2203
    first = json.loads(parts[0])
    assert first["o"] == 4343.62 and first["extensions"] == {"settled": PROV}
    assert json.loads(parts[1])["v"] == json.loads(parts[3])["v"] == 352.0
    assert parts[2] == B_0921[2] and parts[4] == B_0921[4]  # чужий символ і SAME — байт у байт
    rebuilt = json.loads((tf60 / "part-20260920.jsonl").read_bytes().splitlines()[0])
    assert rebuilt["extensions"] == {"settled": PROV}  # маркери ремонту знято значеннями архіву
    assert (tf60 / "part-20260922.jsonl").read_bytes() == _row(K_NEW_DAY, 4350.0, 4350.5, 4349.5, 4350.2, 80.0,
                                                                {"settled": PROV}) + b"\n"
    rc, again = _run(tmp_path, data_root, arc, "2026-09-20T22:00", "2026-09-22T23:00", "--baseline-archive", str(arc))
    assert rc == 0 and not ({"REPLACE", "INSERT"} & set(_stats(again)))


def test_b_drop_flags_spare_unarchived_week_and_drop_after_it_is_archived(b_tree):
    tmp_path, data_root, tf60, arc = b_tree
    flags = ("--baseline-archive", str(arc), "--drop-only-ours-by-classifier", "--drop-only-ours-flat")
    rc, report = _run(tmp_path, data_root, arc, "2026-09-20T22:00", "2026-09-22T23:00", *flags, apply=True)
    assert rc == 0 and _stats(report)["SETTLE_DROP_PROTECTED"] == 2 and "SETTLE_ONLY_OURS_DROPPED" not in _stats(report)
    assert report["symbols"]["XAU_USD"]["unarchived_from"] == "Sun 2026-09-20 22:00"
    _archive(arc, B_ARCHIVE, "2026-09-28T05:00:00+00:00", B_WINDOW)  # тиждень 39 засвідчено забором після 27.09 22:00
    rc, report = _run(tmp_path, data_root, arc, "2026-09-20T22:00", "2026-09-22T23:00", *flags, apply=True)
    assert rc == 0 and _stats(report)["SETTLE_ONLY_OURS_DROPPED"] == 2
    keys = set(_rows(tf60 / "part-20260921.jsonl"))
    assert K_OUR_STUB not in keys and K_ONLY_FLAT not in keys and K2203 in keys


def test_b_week_close_minute_is_protected_from_drop_flags(b_tree):
    tmp_path, data_root, tf60, arc = b_tree
    k_close = _ms(2026, 9, 18, 20, 44)
    (tf60 / "part-20260918.jsonl").write_bytes(_row(k_close, 4380.0, 4380.0, 4380.0, 4380.0, 1.0,
                                                    {"trading_flat": True}) + b"\n")
    rc, report = _run(tmp_path, data_root, arc, "2026-09-18T20:44", "2026-09-18T20:45", "--min-coverage", "0",
                      "--drop-only-ours-flat")
    assert _stats(report)["SETTLE_DROP_PROTECTED"] == 1 and "SETTLE_ONLY_OURS_DROPPED" not in _stats(report)
    assert report["symbols"]["XAU_USD"]["log"]["SETTLE_DROP_PROTECTED"][0][-1] == "week_close_minute"


def test_b_provenance_none_is_refused_on_prod_path(tmp_path):
    arc = _archive(tmp_path / "archive", B_ARCHIVE, "2026-09-22T05:09:00+00:00", B_WINDOW)
    argv = ["--data-root", "/opt/smc-v3/data_v3", "--archive-dir", str(arc), "--config", CONFIG,
            "--from", "2026-09-20T22:00", "--to", "2026-09-22T23:00", "--provenance", "none"]
    assert settle_m1.main(argv) == 2


# ── C ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _t22(h, m):
    return _ms(2026, 9, 22, h, m)


C_ARCHIVE = [
    [_t22(20, 55), 4358.00, 4358.10, 4357.90, 4358.05, 100.0],
    [_t22(20, 56), 4358.05, 4358.20, 4358.00, 4358.10, 100.0],
    [_t22(20, 57), 4358.10, 4358.30, 4358.10, 4358.15, 100.0],
    [_t22(20, 58), 4358.15, 4358.40, 4358.10, 4358.20, 100.0],
    [_t22(20, 59), 4358.33, 4358.73, 4355.37, 4357.63, 516.0],  # ревізія: open ≠ close 20:58
    [_t22(21, 0), 4357.63, 4357.74, 4357.63, 4357.74, 4.0],     # застарілий край — у 20:59, як у TV
    [_t22(22, 1), 4357.74, 4363.07, 4357.74, 4363.06, 397.0],
    [_t22(22, 2), 4363.06, 4364.10, 4363.00, 4364.00, 200.0],   # нема в нас → вставка
    [_t22(22, 3), 4364.00, 4365.10, 4363.90, 4365.00, 200.0],
    [_t22(22, 4), 4365.00, 4366.10, 4364.90, 4366.00, 200.0],   # брокер переписав close (у нас 4365.90)
]
C_WINDOW = ["2026-09-22T20:55:00+00:00", "2026-09-22T22:05:00+00:00"]


def test_c_folds_stale_edge_chains_window_and_successor(tmp_path):
    tf60 = tmp_path / "data_v3" / "XAU_USD" / "tf_60"
    tf60.mkdir(parents=True)
    ours = [_row(_t22(20, 54), 4357.90, 4358.00, 4357.80, 4358.00, 90.0)] + [_row(*r) for r in C_ARCHIVE[:4]] + [
        _row(_t22(20, 59), 4358.20, 4358.73, 4355.37, 4357.63, 516.0, {"open_chained_from": 4358.33}),
        _row(_t22(21, 0), 4357.63, 4357.74, 4357.63, 4357.74, 4.0, {"calendar_pause_nonflat_anomaly": True}),
        _row(_t22(22, 1), 4357.63, 4363.07, 4357.63, 4363.06, 397.0, {"open_chained_from": 4357.74}),
        _row(_t22(22, 3), 4364.00, 4365.10, 4363.90, 4365.00, 200.0),
        _row(_t22(22, 4), 4365.00, 4366.10, 4364.90, 4365.90, 200.0),
        _row(_t22(22, 5), 4365.90, 4366.50, 4365.80, 4366.40, 180.0),  # після вікна: open = старий close 22:04
    ]
    part = tf60 / "part-20260922.jsonl"
    part.write_bytes(b"\n".join(ours) + b"\n")
    arc = _archive(tmp_path / "archive", C_ARCHIVE, "2026-09-23T04:20:00+00:00", C_WINDOW)
    base = ("--baseline-archive", str(arc))
    rc, report = _run(tmp_path, tmp_path / "data_v3", arc, "2026-09-22T20:55", "2026-09-22T22:05", *base, apply=True)
    stats = _stats(report)
    assert rc == 0 and report["verify_problems"] == []
    assert (stats["FOLDED"], stats["FOLD_STALE_ROW_REMOVED"], stats["CHAINED"], stats["CHAIN_SUCCESSOR"],
            stats["INSERT"]) == (1, 1, 2, 1, 1)
    rows = _rows(part)
    r2059 = rows[_t22(20, 59)]
    assert (r2059["o"], r2059["h"], r2059["low"], r2059["c"], r2059["v"]) == (4358.20, 4358.73, 4355.37, 4357.74, 520.0)
    assert r2059["extensions"] == {"settled": "prev/20260923T0420Z", "late_ticks_folded": 4.0,
                                   "open_chained_from": 4358.33}
    assert _t22(21, 0) not in rows
    assert rows[_t22(22, 1)]["o"] == 4357.74 and "open_chained_from" not in rows[_t22(22, 1)]["extensions"]
    succ = rows[_t22(22, 5)]
    assert succ["o"] == 4366.00 and succ["extensions"] == {"open_chained_from": 4365.90}
    assert rows[_t22(20, 54)] == json.loads(ours[0])
    rc, again = _run(tmp_path, tmp_path / "data_v3", arc, "2026-09-22T20:55", "2026-09-22T22:05", *base)
    assert rc == 0 and _stats(again)["SAME"] == 9
    assert not ({"REPLACE", "INSERT", "CHAIN_ONLY_OURS", "CHAIN_SUCCESSOR", "FOLD_STALE_ROW_REMOVED"} & set(_stats(again)))


def test_c_chain_does_not_cross_a_possible_hole_and_ours_gate_counts_trading_bars(tmp_path):
    tf60 = tmp_path / "data_v3" / "XAU_USD" / "tf_60"
    tf60.mkdir(parents=True)
    (tf60 / "part-20260922.jsonl").write_bytes(_row(_t22(20, 40), 4350.0, 4350.5, 4349.5, 4350.0, 80.0) + b"\n"
                                               + b"\n".join(_row(*r) for r in C_ARCHIVE[:4]) + b"\n")
    arc = _archive(tmp_path / "archive", C_ARCHIVE, "2026-09-23T04:20:00+00:00", C_WINDOW)
    rc, report = _run(tmp_path, tmp_path / "data_v3", arc, "2026-09-22T20:55", "2026-09-22T20:59",
                      "--baseline-archive", str(arc))
    assert _stats(report)["CHAIN_GAP_SKIPPED"] == 1 and "CHAINED" not in _stats(report)
    ours_gate = ("--baseline-ours", "--ours-tolerance")
    rc, report = _run(tmp_path, tmp_path / "data_v3", arc, "2026-09-22T20:40", "2026-09-22T20:59", *ours_gate, "0")
    assert rc == 2 and report["symbols"]["XAU_USD"]["gate"]["problems"] == [
        "COVERAGE_BELOW_OURS archive_trading=4 ours_trading=5 tol=0 day=2026-09-22"]
    rc, report = _run(tmp_path, tmp_path / "data_v3", arc, "2026-09-22T20:40", "2026-09-22T20:59", *ours_gate, "1")
    assert rc == 0 and report["archive_gate_verdict"] == "OK"


# ── W ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_w_weekend_tick_does_not_break_chain_sunday_opens_from_friday_close(tmp_path):
    fri_close, tick, sun_open = _ms(2026, 3, 6, 21, 44), _ms(2026, 3, 8, 17, 22), _ms(2026, 3, 8, 22, 1)
    w_rows = [
        [_ms(2026, 3, 6, 21, 43), 5170.44, 5170.97, 5170.08, 5170.76, 63.0],
        [fri_close, 5170.76, 5170.76, 5168.18, 5170.03, 116.0],  # закриття тижня (Пт 16:45 NY)
        [tick, 5170.03, 5171.76, 5170.03, 5171.76, 1.0],         # тік глибоко в паузі — не на графік
        [sun_open, 5171.76, 5191.38, 5171.76, 5182.99, 978.0],   # сирий open брокера = close тіку
        [_ms(2026, 3, 8, 22, 2), 5182.99, 5183.48, 5171.2, 5177.21, 2041.0],
    ]
    tf60 = tmp_path / "data_v3" / "XAU_USD" / "tf_60"
    tf60.mkdir(parents=True)
    (tf60 / "part-20260306.jsonl").write_bytes(b"\n".join(_row(*r) for r in w_rows[:2]) + b"\n")
    (tf60 / "part-20260308.jsonl").write_bytes(  # живий полер: тік відкинуто, open прив'язано до п'ятниці
        _row(sun_open, 5170.03, 5191.38, 5170.03, 5182.99, 978.0, {"open_chained_from": 5171.76}) + b"\n"
        + _row(*w_rows[4]) + b"\n")
    arc = _archive(tmp_path / "archive", w_rows, "2026-09-23T15:52:00+00:00",
                   ["2026-03-06T21:43:00+00:00", "2026-03-08T22:03:00+00:00"])
    rc, report = _run(tmp_path, tmp_path / "data_v3", arc, "2026-03-06T21:43", "2026-03-08T22:03",
                      "--baseline-archive", str(arc), apply=True)
    stats = _stats(report)
    assert rc == 0 and stats["CHAINED"] == 1 and stats["SETTLE_BROKER_PAUSE_SKIPPED"] == 1 and "REPLACE" not in stats
    sunday = _rows(tf60 / "part-20260308.jsonl")
    assert (sunday[sun_open]["o"], sunday[sun_open]["low"]) == (5170.03, 5170.03)
    assert sunday[sun_open]["extensions"]["open_chained_from"] == 5171.76 and tick not in sunday
