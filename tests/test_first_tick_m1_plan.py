"""Фаза plan: детермінований план, звʼязаний sha з кожним входом, і жодного запису в SSOT (ADR-0096 §3.3 B, B5).

Навіщо. apply виконує план на проді, а доводимо його на копії: план, збудований на точній копії, мусить бути
байт-у-байт тим самим, інакше «доказ на копії» нічого не доводить. Кожен вхід, що вплинув на класифікацію
(включно з незачепленими файлами, відсутніми добами і контекстною добою staging), звʼязаний sha — його зміна
після плану мусить бути видна apply. sha після заміни — рівно ті байти, які запише apply.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import shutil
from pathlib import Path

import pytest

from ft_m1_support import Scenario, at, line, ssot_bar, staged_row, tree_digest
from tools.repair.first_tick_m1 import plan_io
from tools.repair.first_tick_m1.common import day_key, sha256_file
from tools.repair.first_tick_m1.plan import PlanOptions, run_plan
from tools.repair.first_tick_m1.ssot_part import Patch, render_patched_lines
from tools.repair.first_tick_m1.staging import day_paths
from tools.repair.jsonl_rewrite import read_lines, rewrite_atomic

SUN, MON, TUE, WED = (dt.date(2026, 7, 26) + dt.timedelta(days=i) for i in range(4))


def _scenario(tmp_path):
    sc = Scenario(tmp_path / "prod")
    close = sc.session(SUN, 22, 0, 4, 4055.42)
    close = sc.session(MON, 0, 0, 6, close)
    sc.session(TUE, 0, 0, 3, close)
    # Уже виправлена хвилина вівторка: SSOT == FIRST_TICK → SAME, файл без заміни.
    sc.add(TUE, ssot_bar(at(TUE, 9, 0), 10.0, 10.5, 9.5, 10.2), staged_row(at(TUE, 9, 0), 10.0, 10.5, 9.5, 10.2))
    for minute, (o, h, low, c) in enumerate([(11.0, 11.4, 10.9, 11.2), (11.3, 11.5, 11.1, 11.4)]):
        key = at(WED, 0, minute)  # середа цілком уже FIRST_TICK — файл без заміни
        sc.add(WED, ssot_bar(key, o, h, low, c), staged_row(key, o, h, low, c))
    return sc.write()


def _opts(sc, plan_dir, day_from=MON, day_to=TUE, **kw):
    return PlanOptions(symbol=sc.symbol, day_from=day_from, day_to=day_to, staging_root=str(sc.staging),
                       plan_dir=str(plan_dir), **kw)


def _load(plan_dir):
    return plan_io.load_plan(str(plan_dir))


def test_plan_bytes_deterministic_and_root_independent(tmp_path):
    sc = _scenario(tmp_path)
    copy_root = tmp_path / "copy"
    shutil.copytree(sc.data, copy_root / "data")
    shutil.copytree(sc.staging, copy_root / "staging")
    assert run_plan(_opts(sc, tmp_path / "plan_a"), sc.cfg()) == 0
    copy_opts = dataclasses.replace(_opts(sc, tmp_path / "plan_b"), staging_root=str(copy_root / "staging"),
                                    data_root=str(copy_root / "data"))
    assert run_plan(copy_opts, sc.cfg()) == 0
    assert tree_digest(tmp_path / "plan_a") == tree_digest(tmp_path / "plan_b")
    plan = _load(tmp_path / "plan_a").plan
    assert plan["totals"]["REPLACE"] == 9 and plan["totals"]["SAME"] == 1
    text = (tmp_path / "plan_a" / "PLAN.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text


def test_plan_binds_sha_of_every_input_including_untouched_absent_and_context(tmp_path):
    sc = _scenario(tmp_path)
    assert run_plan(_opts(sc, tmp_path / "plan", day_from=MON, day_to=dt.date(2026, 7, 29)), sc.cfg()) == 0
    plan = _load(tmp_path / "plan").plan
    assert [p["day"] for p in plan["inputs"]["parts"]] == [day_key(d) for d in (MON, TUE, WED)]
    assert all(p["sha256"] for p in plan["inputs"]["parts"])
    staging_inputs = {s["day"]: s for s in plan["inputs"]["staging"]}
    assert set(staging_inputs) == {day_key(d) for d in (SUN, MON, TUE, WED, dt.date(2026, 7, 30))}
    assert staging_inputs["20260726"]["role"] == "context" and staging_inputs["20260726"]["sha256"]
    assert staging_inputs["20260730"]["sha256"] is None
    assert plan_io.input_mismatches(plan, str(sc.data), str(sc.staging)) == []

    untouched = next(f for f in plan["files"] if f["day"] == day_key(WED))
    assert untouched["rewrite"] is False
    wed_part = sc.data / "XAU_USD" / "tf_60" / "part-20260729.jsonl"
    wed_part.write_bytes(wed_part.read_bytes() + b"\n")
    ctx_manifest = Path(day_paths(sc.staging, sc.symbol, SUN)[1])
    ctx_manifest.write_bytes(ctx_manifest.read_bytes().replace(b'"call_seq":1', b'"call_seq":2'))
    absent = sc.data / "XAU_USD" / "tf_60" / "part-20260730.jsonl"
    absent.write_text(line(ssot_bar(at(dt.date(2026, 7, 30), 0, 0), 1.0, 2.0, 0.5, 1.5)) + "\n", encoding="utf-8")
    new_plan = dict(plan, inputs=dict(plan["inputs"], parts=plan["inputs"]["parts"] + [
        {"day": "20260730", "path": plan_io.rel_part(sc.symbol, "20260730"), "sha256": None, "bytes": None}]))
    changed = {Path(path).name for path, _expected, _actual in plan_io.input_mismatches(new_plan, str(sc.data),
                                                                                         str(sc.staging))}
    assert changed == {"part-20260729.jsonl", "day-20260726.manifest.json", "part-20260730.jsonl"}


def test_plan_sha_after_equals_bytes_that_apply_writes(tmp_path):
    sc = _scenario(tmp_path)
    assert run_plan(_opts(sc, tmp_path / "plan"), sc.cfg()) == 0
    loaded = _load(tmp_path / "plan")
    rewritten = [f for f in loaded.plan["files"] if f["rewrite"]]
    assert {f["day"] for f in rewritten} == {day_key(MON), day_key(TUE)}
    for item in rewritten:
        part = plan_io.under(str(sc.data), item["part"])
        patches = {e["line"]: Patch(e["new"]["o"], e["new"]["h"], e["new"]["low"], e["trading_flat_add"])
                   for e in loaded.entries[item["day"]] if e["cat"] == "REPLACE"}
        target = tmp_path / ("copy-" + item["day"] + ".jsonl")
        shutil.copyfile(part, target)
        rewrite_atomic(str(target), render_patched_lines(read_lines(part), patches))
        assert sha256_file(target) == item["sha256_after"] != item["sha256_before"] == sha256_file(part)


def test_plan_refuses_invalid_staging_rc2_no_plan_dir_content(tmp_path):
    sc = _scenario(tmp_path)
    Path(day_paths(sc.staging, sc.symbol, SUN)[0]).write_bytes(b"{}\n")  # контекстна доба, sha зламано
    assert run_plan(_opts(sc, tmp_path / "plan"), sc.cfg()) == 2
    assert not (tmp_path / "plan").exists() or os.listdir(tmp_path / "plan") == []


def test_plan_refuses_symbol_without_calendar_rc2(tmp_path):
    sc = _scenario(tmp_path)
    cfg = sc.cfg()
    del cfg["market_calendar_symbol_groups"][sc.symbol]
    assert run_plan(_opts(sc, tmp_path / "plan"), cfg) == 2
    assert not (tmp_path / "plan").exists()


def test_plan_does_not_modify_data_root_or_staging(tmp_path):
    sc = _scenario(tmp_path)
    before = (tree_digest(sc.data), tree_digest(sc.staging))
    assert run_plan(_opts(sc, tmp_path / "plan"), sc.cfg()) == 0
    assert (tree_digest(sc.data), tree_digest(sc.staging)) == before


def test_plan_uses_context_day_for_baked_run_across_midnight(tmp_path):
    """Ловить план без контекстних діб: «запечений» рядок Нд 23:59 (поза межами) тягне ранкові хвилини Пн."""
    sc = Scenario(tmp_path / "prod")
    sunday_late = at(SUN, 23, 59)
    sc.add(SUN, ssot_bar(sunday_late, 20.0, 20.0, 19.0, 19.5), staged_row(sunday_late, 20.0, 19.8, 19.0, 19.5))
    close = 19.5
    for minute in range(3):  # o == prev_c, open у межах — без ланцюжка це SAME
        key = at(MON, 0, minute)
        sc.add(MON, ssot_bar(key, close, close + 0.3, close - 0.2, close + 0.1),
               staged_row(key, close, close + 0.3, close - 0.2, close + 0.1))
        close = round(close + 0.1, 2)
    sc.write()
    assert run_plan(_opts(sc, tmp_path / "plan", day_from=MON, day_to=MON), sc.cfg()) == 0
    entries = _load(tmp_path / "plan").entries[day_key(MON)]
    assert [(e["cat"], e.get("reason")) for e in entries] == [("SKIP_BAKED", "baked_run")] * 3
    assert _load(tmp_path / "plan").plan["baked_runs"][0]["open_outside_range_rows"] == 1


def test_plan_rc1_with_refused_crlf_part_and_warning(tmp_path):
    sc = _scenario(tmp_path)
    part = sc.data / "XAU_USD" / "tf_60" / "part-20260728.jsonl"
    part.write_bytes(part.read_bytes().replace(b"\n", b"\r\n"))
    assert run_plan(_opts(sc, tmp_path / "plan"), sc.cfg()) == 1
    plan = _load(tmp_path / "plan").plan
    refused = next(f for f in plan["files"] if f["day"] == "20260728")
    assert (refused["status"], refused["refuse_reason"], refused["rewrite"]) == ("refused", "not_canonical:crlf", False)
    assert {"code": "PLAN_PART_REFUSED", "day": "20260728", "detail": "not_canonical:crlf"} in plan["warnings"]
    assert plan["totals"]["keys_in_refused_files"] == 4


@pytest.mark.parametrize("where", ["inside_data_root", "non_empty", "inside_staging"])
def test_plan_dir_inside_data_root_or_non_empty_refused_rc2(tmp_path, where):
    sc = _scenario(tmp_path)
    plan_dir = {"inside_data_root": sc.data / "plan", "non_empty": tmp_path / "plan",
                "inside_staging": sc.staging / "plan"}[where]
    if where == "non_empty":
        plan_dir.mkdir()
        (plan_dir / "old.txt").write_text("x", encoding="utf-8")
    before = tree_digest(sc.data)
    assert run_plan(_opts(sc, plan_dir), sc.cfg()) == 2
    assert tree_digest(sc.data) == before
    assert not (plan_dir / "PLAN.json").exists()
