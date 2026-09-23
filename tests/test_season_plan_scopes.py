"""План S7.1 (ADR-0095): області h4_from_h1, d1_rekey і holes (MIGRATION §4.2, §2, §3, §5).

* H4 до першої M1 будується з H1 на диску на сезонній сітці; H1 у перерві сезонного календаря (тіки брокера в
  перерві) в бакет не йде, H4 старої сітки і вихідних прибираються.
* D1 поза сіткою в епосі M1 перебудовується з M1 на ключ сітки (OHLCV рівний видаленому рядку, тонка доба — з
  фронтиром); поза епохою M1 — MANUAL_REVIEW без змін.
* Сезонні діри M3..H1 (зимова година 21:00 XAU, якої немає в літньому розкладі) добудовуються, H4 над ними
  перераховується; несезонна діра — поза областю.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from core.session_anchor import D1_S, H4_S
from season_plan_synthetic import CAL, context, ms, run_rebuild_tool, write_m1
from tools.repair import season_plan as sp

H1_MS = 3_600_000


def write_rows(root: Path, tf_s: int, rows: List[dict]) -> None:
    by_day: Dict[str, List[str]] = {}
    for row in rows:
        by_day.setdefault(sp.day_of_ms(row["open_time_ms"]), []).append(json.dumps(row, separators=(",", ":")))
    folder = root / "XAU_USD" / ("tf_%d" % tf_s)
    folder.mkdir(parents=True, exist_ok=True)
    for day, lines in by_day.items():
        with open(folder / ("part-%s.jsonl" % day), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")


def bar(tf_s: int, open_ms: int, o: float, v: float = 10.0, src: str = "history") -> dict:
    return {"symbol": "XAU/USD", "tf_s": tf_s, "open_time_ms": open_ms, "close_time_ms": open_ms + tf_s * 1000,
            "o": o, "h": o + 1.0, "low": o - 1.0, "c": o + 0.5, "v": v, "complete": True, "src": src}


def plan(root: Path, scopes):
    ctx = context(root)
    reader = sp.SourceReader(str(root), "XAU/USD")
    seeds, extra = [], {}
    if "h4_from_h1" in scopes:
        seeds.append(sp.seed_h4_from_h1(ctx, reader.read(3600, *sp.ALL_TIME)[0].open_time_ms))
    if "d1_rekey" in scopes:
        d1_seeds, extra["rekey"], extra["manual"] = sp.seed_d1_rekey(ctx, reader)
        seeds.append(d1_seeds)
    if "holes" in scopes:
        hole_seeds, extra["out_of_scope"] = sp.seed_holes(ctx, reader)
        seeds.append(hole_seeds)
        extra["holes"] = hole_seeds
    rebuild, _tail = sp.complete_rebuild_set(ctx, seeds)
    planned = sp.plan_bars(ctx, reader, rebuild)
    files, tf_scopes = sp.plan_symbol_files(ctx, str(root), rebuild, planned)
    rows = {tf_s: scope.rows for tf_s, scope in tf_scopes.items()}
    return rebuild, planned, files, rows, extra


def apply(files) -> None:
    for file_plan in files:
        Path(file_plan.path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_plan.path).write_bytes(file_plan.new_bytes)


def test_h4_before_m1_from_h1_on_summer_grid_without_h1_in_the_break(tmp_path):
    h1 = [bar(3600, k, 2300.0 + i) for i, k in enumerate(range(ms(2024, 6, 16, 22), ms(2024, 6, 21, 21), H1_MS))
          if any(CAL.is_trading_minute(t) for t in range(k, k + H1_MS, 60_000))]
    h1.append(bar(3600, ms(2024, 6, 18, 21), 9999.0, v=2.0))  # тіки брокера в перерві 21:00–22:00 (MIGRATION §4.3)
    write_rows(tmp_path, 3600, h1)
    old_grid = [bar(H4_S, k, 2300.0) for k in range(ms(2024, 6, 16, 22), ms(2024, 6, 21, 22), 4 * H1_MS)]
    write_rows(tmp_path, H4_S, old_grid + [bar(H4_S, ms(2024, 6, 22, 2), 1.0)])  # стара сітка 22/02/.. і субота
    write_m1(tmp_path, ms(2024, 6, 23, 22), ms(2024, 6, 24, 2, 59))

    rebuild, planned, files, rows, _extra = plan(tmp_path, ["h4_from_h1"])
    new_h4 = {k for k, b in planned[H4_S].items() if b is not None}
    assert all((k // H1_MS) % 4 == 1 for k in new_h4), "літня сітка 21/01/05/09/13/17"
    assert min(new_h4) == ms(2024, 6, 16, 21) and max(new_h4) == ms(2024, 6, 21, 17)
    assert ms(2024, 6, 23, 21) in rebuild[H4_S], "бакет з першою M1 — ще з H1"
    tue = planned[H4_S][ms(2024, 6, 18, 21)]
    assert tue.o == next(b["o"] for b in h1 if b["open_time_ms"] == ms(2024, 6, 18, 22)), "H1 21:00 у перерві — не в бакеті"
    assert rows[H4_S][sp.ROW_OFF_GRID] == len(old_grid) and rows[H4_S][sp.ROW_DROPPED] == 1
    assert rows[H4_S][sp.ROW_ADDED] == len(new_h4) and {f.tf_s for f in files} == {H4_S}
    apply(files)
    assert plan(tmp_path, ["h4_from_h1"])[2] == []


def test_d1_off_grid_in_m1_era_is_rekeyed_and_before_it_goes_to_manual_review(tmp_path):
    write_m1(tmp_path, ms(2026, 6, 9, 9, 41), ms(2026, 6, 12, 20, 44))
    minutes = sp.SourceReader(str(tmp_path), "XAU/USD").read(60, ms(2026, 6, 9, 21), ms(2026, 6, 10, 21))
    wed = {"o": minutes[0].o, "h": max(m.h for m in minutes), "low": min(m.low for m in minutes),
           "c": minutes[-1].c, "v": sum(m.v for m in minutes)}
    write_rows(tmp_path, D1_S, [
        dict(bar(D1_S, ms(2026, 6, 1, 22), 4100.0, src="derived")),  # до епохи M1
        dict(bar(D1_S, ms(2026, 6, 8, 22), 4200.0, src="derived")),  # тонка доба: M1 з 09.06 09:41
        dict(bar(D1_S, ms(2026, 6, 9, 22), 0.0, src="derived"), **wed),
    ])
    before = (tmp_path / "XAU_USD" / "tf_86400" / "part-20260601.jsonl").read_bytes()

    rebuild, planned, files, rows, extra = plan(tmp_path, ["d1_rekey"])
    assert rebuild == {D1_S: {ms(2026, 6, 8, 21), ms(2026, 6, 9, 21)}}
    assert [b.open_time_ms for b in extra["manual"]] == [ms(2026, 6, 1, 22)]
    new = planned[D1_S][ms(2026, 6, 9, 21)]
    assert (new.o, new.h, new.low, new.c, new.v) == (wed["o"], wed["h"], wed["low"], wed["c"], wed["v"])
    assert "thin_session" in planned[D1_S][ms(2026, 6, 8, 21)].extensions["partial_reasons"]
    assert rows[D1_S][sp.ROW_OFF_GRID] == 2 and rows[D1_S][sp.ROW_ADDED] == 2
    apply(files)
    assert (tmp_path / "XAU_USD" / "tf_86400" / "part-20260601.jsonl").read_bytes() == before
    assert plan(tmp_path, ["d1_rekey"])[2] == []


def test_winter_2100_hour_holes_are_built_and_h4_above_them_is_recomputed(tmp_path):
    write_m1(tmp_path, ms(2026, 3, 3, 23), ms(2026, 3, 6, 21, 44))
    run_rebuild_tool(tmp_path, ms(2026, 3, 3, 23), ms(2026, 3, 6, 21, 45))
    hour, non_seasonal = ms(2026, 3, 5, 21), ms(2026, 3, 5, 10)
    for tf_s in (180, 300, 900, 1800, 3600):
        path = tmp_path / "XAU_USD" / ("tf_%d" % tf_s) / "part-20260305.jsonl"
        keep = [line for line in path.read_text(encoding="utf-8").splitlines()
                if not hour <= json.loads(line)["open_time_ms"] < hour + H1_MS
                and not (tf_s == 180 and json.loads(line)["open_time_ms"] == non_seasonal)]
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    h4 = tmp_path / "XAU_USD" / "tf_14400" / "part-20260305.jsonl"
    h4.write_text("\n".join(json.dumps(dict(json.loads(line), c=1.0)) if json.loads(line)["open_time_ms"] == ms(2026, 3, 5, 18)
                            else line for line in h4.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")

    rebuild, _planned, files, rows, extra = plan(tmp_path, ["holes"])
    assert {tf_s: len(b) for tf_s, b in extra["holes"].items()} == {180: 20, 300: 12, 900: 4, 1800: 2, 3600: 1}
    assert extra["out_of_scope"] == {180: [non_seasonal]}, "10:00 торгова в обох сезонах — не сезонна діра"
    assert rebuild[H4_S] == {ms(2026, 3, 5, 18)} and rows[H4_S][sp.ROW_REPLACED] == 1
    assert D1_S not in rebuild, "D1 будується з M1, дір у M3..H1 не бачить"
    assert rows[180][sp.ROW_ADDED] == 20 and rows[3600][sp.ROW_ADDED] == 1
    apply(files)
    rebuild_again, _p, files_again, _r, extra_again = plan(tmp_path, ["holes"])
    assert files_again == [] and extra_again["holes"] == {}
