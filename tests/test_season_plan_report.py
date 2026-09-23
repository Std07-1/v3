"""Звіт dry-run плану S7 (ADR-0095 S7.1, формат MIGRATION §5): таблиця за символом і TF, зрізи сітки H4, D1 re-key,
діри й поза областю, межі джерела, підсумок — і ті самі числа, що в плані.
"""
from __future__ import annotations

from season_plan_synthetic import CFG, dataset, ms
from tools.repair import season_plan as sp
from tools.repair.season_plan_report import format_report


def test_report_lines_match_the_plan(tmp_path):
    dataset(tmp_path)
    plan = sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], list(sp.SCOPES))
    lines = format_report(plan).splitlines()
    assert lines[0].startswith("S7_PLAN scopes=derived_from_m1,h4_from_h1,d1_rekey,holes git_rev=")
    assert "window=all changed_m1=no" in lines[0]
    assert lines[1].split() == ["SYM", "TF", "REBUILD", "OLD", "NEW", "SAME", "REPL", "ADD", "OFFGRID", "DROP_NT",
                                "DROP_NS", "DUP", "PARTIAL", "TAIL", "FILES"]
    table = {tuple(line.split()[:2]): line.split()[2:] for line in lines[2:] if line.startswith("XAU_USD ")}
    assert set(table) == {("XAU_USD", tf) for tf in ("M3", "M5", "M15", "M30", "H1", "H4", "D1")}
    d1 = dict(zip(["REBUILD", "OLD", "NEW", "SAME", "REPL", "ADD", "OFFGRID"], map(int, table[("XAU_USD", "D1")][:7])))
    assert d1["OFFGRID"] == 1 and d1["OLD"] == d1["SAME"] + 1
    assert any(line.startswith("SLICES XAU_USD H4 1[") and line.endswith("anchors=79200") for line in lines)
    assert "D1_REKEY XAU_USD 1->1 ohlcv_equal=0/1 thin_session=0()" in lines
    assert any(line.startswith("DROP_NO_SOURCE XAU_USD H4=") for line in lines), "торговий понеділок без H1 — гучно"
    assert any(line.startswith("SOURCE XAU_USD rule=ny_close_us_dst season_rule=us m1=2026-03-03T23:00..") for line in lines)
    total = lines[-1]
    created = [f for f in plan.files if not f.src_exists]
    assert {f.tf_s for f in created} == {14400} and len(created) == 6, "H4 тижня до M1 на диску не було: 22–27.02"
    assert total == "TOTAL files=%d created=6 emptied=0 eol_added=0 rows: remove %d add %d" % (
        len(plan.files), sum(len(f.removed_keys) for f in plan.files), sum(len(f.added_keys) for f in plan.files))


def test_report_window_and_holes_out_of_scope(tmp_path):
    dataset(tmp_path)
    plan = sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], ["holes"], window=(ms(2026, 3, 4), sp.ALL_TIME[1]))
    text = format_report(plan)
    assert "window=2026-03-04T00:00..* " in text
    assert "HOLES" not in text, "дір і відсутніх бакетів у f(M1) немає"
    assert text.splitlines()[-1].startswith("TOTAL files=0 ")
