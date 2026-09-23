"""D1 до епохи M1 = нативний D1 брокера (tools/repair/d1_native_settle): кожен клас рядка, байти решти, повтор = 0."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from tools.repair import d1_native_settle as dns

UTC = dt.timezone.utc


def _ms(y, mo, d, h=0):
    return int(dt.datetime(y, mo, d, h, tzinfo=UTC).timestamp() * 1000)


def _row(key, o, h, low, c, v, src="history", ext=None):
    obj = {"symbol": "XAU/USD", "tf_s": 86400, "open_time_ms": key, "close_time_ms": key + 86_400_000, "o": o, "h": h,
           "low": low, "c": c, "v": v, "complete": True, "src": src}
    if ext:
        obj["extensions"] = ext
    return json.dumps(obj, separators=(",", ":")).encode()


def _write(path, lines, eol=b"\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(eol.join(lines) + eol)


# Сезонна сітка ny_close_us_dst: 1995-03-30 — ще зима за законом 1995 (22:00), 1995-06-12 — літо (21:00)
SAME = _ms(1995, 6, 12, 21)
DIFFER = _ms(1995, 6, 13, 21)
OLD_WINTER_WRONG = _ms(1995, 3, 30, 21)  # старий сід з фіксованим 21:00 — натив має 22:00
NATIVE_WINTER = _ms(1995, 3, 30, 22)
PRE_NATIVE_OFF = _ms(1987, 10, 26, 21)  # до першої нативної доби, поза сіткою → ключ сітки тієї доби (22:00)
PRE_NATIVE_ON = _ms(1987, 6, 15, 21)  # до першої нативної доби, на сітці → лишається
NATIVE_ONLY = _ms(1995, 6, 14, 21)
ERA_ROW = _ms(2025, 10, 16, 21)  # епоха M1 — не чіпається
FIRST_M1 = _ms(2025, 10, 15, 0)


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / "data_v3"
    d1 = root / "XAU_USD" / "tf_86400"
    _write(str(root / "XAU_USD" / "tf_60" / "part-20251015.jsonl"),
           [json.dumps({"symbol": "XAU/USD", "tf_s": 60, "open_time_ms": FIRST_M1, "close_time_ms": FIRST_M1 + 60000,
                        "o": 1, "h": 1, "low": 1, "c": 1, "v": 1, "complete": True, "src": "history"}).encode()])
    _write(str(d1 / "part-19950612.jsonl"), [_row(SAME, 10.0, 11.0, 9.0, 10.5, 100.0)], eol=b"\r\n")
    foreign = json.dumps({"symbol": "XAG/USD", "tf_s": 86400, "open_time_ms": DIFFER}).encode()
    _write(str(d1 / "part-19950613.jsonl"), [_row(DIFFER, 1.0, 1.0, 1.0, 1.0, 0.0), foreign, b"not json"])
    _write(str(d1 / "part-19950330.jsonl"), [_row(OLD_WINTER_WRONG, 5.0, 5.0, 5.0, 5.0, 0.0)])
    _write(str(d1 / "part-19871026.jsonl"), [_row(PRE_NATIVE_OFF, 3.0, 3.0, 3.0, 3.0, 0.0)])
    _write(str(d1 / "part-19870615.jsonl"), [_row(PRE_NATIVE_ON, 2.0, 2.0, 2.0, 2.0, 0.0)])
    era_line = _row(ERA_ROW, 7.0, 8.0, 6.0, 7.5, 50.0, src="derived")
    _write(str(d1 / "part-20251016.jsonl"), [era_line])
    arc = tmp_path / "arc"
    arc.mkdir()
    native = [[NATIVE_WINTER, 5.1, 5.2, 5.0, 5.15, 900.0], [SAME, 10.0, 11.0, 9.0, 10.5, 100.0],
              [DIFFER, 20.0, 21.0, 19.0, 20.5, 300.0], [NATIVE_ONLY, 30.0, 31.0, 29.0, 30.5, 400.0],
              [ERA_ROW, 99.0, 99.0, 99.0, 99.0, 9.0]]
    json.dump(sorted(native), open(arc / "XAU_USD_d1_full.json", "w"))
    json.dump({"mode": "PREVIOUS_CLOSE", "fetched_at": "2026-09-23T15:52:00+00:00"}, open(arc / "meta.json", "w"))
    return root, arc, era_line


def _rows(root, day):
    path = root / "XAU_USD" / "tf_86400" / ("part-%s.jsonl" % day)
    if not path.exists():
        return None
    return path.read_bytes()


def test_every_row_class_before_the_m1_era_and_nothing_else(tree, tmp_path):
    root, arc, era_line = tree
    rc = dns.main(["--data-root", str(root), "--archive", str(arc), "--symbols", "XAU/USD", "--apply",
                   "--backup-dir", str(tmp_path / "bak"), "--report", str(tmp_path / "r.json")])
    report = json.load(open(tmp_path / "r.json", encoding="utf-8"))
    counts = report["symbols"]["XAU/USD"]["counts"]

    assert rc == 0 and report["verify_replan_files"] == 0
    assert counts == {"same": 1, "replace": 1, "insert": 2, "remove_off_grid": 1, "rekey": 1, "rekey_inserted": 1,
                      "keep_before_native": 1}
    assert _rows(root, "19950612") == _row(SAME, 10.0, 11.0, 9.0, 10.5, 100.0) + b"\r\n"  # SAME — байт у байт, CRLF
    differ = _rows(root, "19950613").split(b"\n")
    assert json.loads(differ[0])["c"] == 20.5 and json.loads(differ[0])["extensions"]["settled"].startswith("d1native/")
    assert differ[1] == json.dumps({"symbol": "XAG/USD", "tf_s": 86400, "open_time_ms": DIFFER}).encode()
    assert differ[2] == b"not json"
    winter = [json.loads(x) for x in _rows(root, "19950330").splitlines()]
    assert [r["open_time_ms"] for r in winter] == [NATIVE_WINTER]  # хибний 21:00 прибрано, натив 22:00 вставлено
    rekeyed = json.loads(_rows(root, "19871026"))
    assert rekeyed["open_time_ms"] == _ms(1987, 10, 26, 22) and rekeyed["extensions"]["rekeyed_from"] == PRE_NATIVE_OFF
    assert json.loads(_rows(root, "19870615"))["open_time_ms"] == PRE_NATIVE_ON
    assert json.loads(_rows(root, "19950614"))["c"] == 30.5
    assert _rows(root, "20251016") == era_line + b"\n"  # епоха M1 — зона S7, байт у байт


def test_dry_run_writes_nothing(tree, tmp_path):
    root, arc, _era_line = tree
    before = {p: open(p, "rb").read() for p in (root / "XAU_USD" / "tf_86400").glob("*.jsonl")}
    assert dns.main(["--data-root", str(root), "--archive", str(arc), "--symbols", "XAU/USD"]) == 0
    after = {p: open(p, "rb").read() for p in (root / "XAU_USD" / "tf_86400").glob("*.jsonl")}
    assert before == after


def test_apply_refuses_without_backup_dir(tree):
    root, arc, _era_line = tree
    assert dns.main(["--data-root", str(root), "--archive", str(arc), "--symbols", "XAU/USD", "--apply"]) == 2


def test_archive_not_in_previous_close_is_refused(tree):
    root, arc, _era_line = tree
    json.dump({"mode": "FIRST_TICK"}, open(arc / "meta.json", "w"))
    with pytest.raises(ValueError, match="D1_NATIVE_ARCHIVE_MODE"):
        dns.main(["--data-root", str(root), "--archive", str(arc), "--symbols", "XAU/USD"])
