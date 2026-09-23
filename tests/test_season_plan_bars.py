"""План S7.1 (ADR-0095): набір перебудови і нові бари похідних TF з M1 — так само, як `tools.rebuild_from_m1`.

Нові бари будуються тим самим `derive_bar` на сезонній сітці й сезонному календарі, з фронтиром ADR-0097 для D1 і
без формуючого хвоста; перевіряємо рівність з інструментом перебудови через обидва вихідні DST 2026, зимовий H4
18:00 з H1 21:00, межі епохи M1 і режим змінених ключів M1 (settle ADR-0101 C5).
"""
from __future__ import annotations

import shutil

import pytest

from core.derive import DERIVE_ORDER
from core.session_anchor import D1_S, H4_S, RULE_NY_CLOSE_US_DST, htf_bucket_start_ms
from season_plan_synthetic import M1_MS, context, disk_rows, ms, run_rebuild_tool, write_m1
from tools.repair import season_plan as sp


def plan_full(root, changed=None):
    ctx = context(root)
    rebuild, tail_kept = sp.complete_rebuild_set(ctx, [sp.seed_derived_from_m1(ctx, changed)])
    return rebuild, tail_kept, sp.plan_bars(ctx, sp.SourceReader(str(root), "XAU/USD"), rebuild)


@pytest.mark.parametrize("first_ms,last_ms", [
    (ms(2026, 3, 5, 22), ms(2026, 3, 10, 13, 17)),  # весна: зимова сітка → Нд 08.03 21:00 літня; хвіст посеред H1
    (ms(2026, 10, 29, 22), ms(2026, 11, 3, 9, 40)),  # осінь: обрубка Нд 01.11 21:00 немає, перший зимовий H4 22:00
])
def test_planned_bars_equal_rebuild_tool_across_dst_weekend(tmp_path, first_ms, last_ms):
    tool_root, plan_root = tmp_path / "tool", tmp_path / "plan"
    write_m1(tool_root, first_ms, last_ms)
    shutil.copytree(tool_root, plan_root)
    run_rebuild_tool(tool_root, first_ms, last_ms + M1_MS)
    _rebuild, _tail, planned = plan_full(plan_root)
    for tf_s in DERIVE_ORDER:
        built = {k: bar.to_dict() for k, bar in planned.get(tf_s, {}).items() if bar is not None}
        assert built == disk_rows(tool_root, tf_s), "TF %d" % tf_s
    assert all(htf_bucket_start_ms(k, H4_S, RULE_NY_CLOSE_US_DST) == k for k in planned[H4_S])


def test_fall_dst_weekend_has_no_stub_h4_and_first_winter_h4_is_2200(tmp_path):
    write_m1(tmp_path, ms(2026, 10, 29, 22), ms(2026, 11, 3, 9, 40))
    _rebuild, _tail, planned = plan_full(tmp_path)
    h4 = {k for k, bar in planned[H4_S].items() if bar is not None}
    assert ms(2026, 11, 1, 21) not in h4 and ms(2026, 11, 1, 22) in h4 and ms(2026, 11, 2, 2) in h4
    assert ms(2026, 10, 30, 17) in h4  # останній літній H4 п'ятниці


def test_winter_h4_1800_contains_h1_2100(tmp_path):
    """Зимою 21:00–21:59 торгова (перерва 22:00–23:00): H4 18:00 закривається close M1 21:59 (S6a, MIGRATION §4.1)."""
    closes = write_m1(tmp_path, ms(2025, 11, 3, 23), ms(2025, 11, 5, 21, 59))
    _rebuild, _tail, planned = plan_full(tmp_path)
    h4 = planned[H4_S][ms(2025, 11, 4, 18)]
    assert h4.c == closes[ms(2025, 11, 4, 21, 59)]
    assert not h4.extensions.get("partial")


def test_tail_buckets_are_not_rebuilt(tmp_path):
    write_m1(tmp_path, ms(2026, 3, 9, 22), ms(2026, 3, 10, 13, 17))
    rebuild, tail_kept, planned = plan_full(tmp_path)
    for tf_s, bucket in ((3600, ms(2026, 3, 10, 13)), (H4_S, ms(2026, 3, 10, 13)), (D1_S, ms(2026, 3, 9, 21))):
        assert bucket not in rebuild.get(tf_s, set()) and bucket not in planned.get(tf_s, {})
        assert tail_kept[tf_s] >= 1
    assert ms(2026, 3, 10, 12) in rebuild[3600]


def test_head_bucket_is_not_seeded_but_higher_tf_is_lifted_from_its_children(tmp_path):
    write_m1(tmp_path, ms(2026, 3, 10, 10, 7), ms(2026, 3, 10, 12, 59))
    rebuild, _tail, _planned = plan_full(tmp_path)
    assert ms(2026, 3, 10, 10, 5) not in rebuild[300], "у M5 10:05 торгові 10:05–10:06 раніше першої M1"
    assert ms(2026, 3, 10, 10, 10) in rebuild[300]
    assert ms(2026, 3, 10, 10, 0) in rebuild[900], "M15 10:00 перебудовується з M5 (диск + план)"
    assert ms(2026, 3, 9, 21) not in rebuild.get(D1_S, set()), "D1 з торговими годинами до першої M1 — не зерно"


def test_changed_m1_rebuilds_one_bucket_per_tf_with_the_same_bars_as_full_plan(tmp_path):
    write_m1(tmp_path, ms(2026, 3, 8, 22), ms(2026, 3, 11, 20, 59))
    _rebuild, _tail, full = plan_full(tmp_path)
    run_rebuild_tool(tmp_path, ms(2026, 3, 8, 22), ms(2026, 3, 11, 21))  # похідні на диску = f(M1)
    changed = ms(2026, 3, 10, 14, 37)
    rebuild, _tail, planned = plan_full(tmp_path, changed=[changed])
    assert {tf_s: sorted(b) for tf_s, b in rebuild.items()} == {
        tf_s: [htf_bucket_start_ms(changed, tf_s, RULE_NY_CLOSE_US_DST)] for tf_s in DERIVE_ORDER}
    for tf_s, bars in planned.items():
        for bucket_ms, bar in bars.items():
            assert bar == full[tf_s][bucket_ms]
