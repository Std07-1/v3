"""Exit-gate: calendar_multi_break — перевірка підтримки multi-break у MarketCalendar.

Ціль: гарантувати що daily_breaks (список інтервалів) підтримується у коді,
присутній у конфігу для HKG33, і покритий unit-тестами.

Підгейти:
1. code_has_multi_break — market_calendar.py має daily_breaks / _all_break_intervals
2. config_has_multi_break — config.json має хоча б один календар з market_daily_breaks
3. unit_test_multi_break — tests/test_market_calendar.py містить TestMultiBreak
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List


def _load_config(root):
    # type: (Path) -> dict
    cfg_path = root / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_gate(inputs):
    # type: (dict) -> dict
    root = Path(str(inputs.get("root", "."))).resolve()
    results = []  # type: List[dict]

    # --- Підгейт 1: code_has_multi_break ---
    cal_py = root / "runtime" / "ingest" / "market_calendar.py"
    has_multi = False
    if cal_py.exists():
        try:
            code = cal_py.read_text(encoding="utf-8", errors="replace")
            has_field = "daily_breaks" in code
            has_method = "_all_break_intervals" in code or "_is_in_break" in code
            has_multi = has_field and has_method
        except Exception:
            pass
    results.append({
        "name": "code_has_multi_break",
        "ok": has_multi,
        "details": "ok" if has_multi else "market_calendar.py не має daily_breaks / multi-break logic",
    })

    # --- Підгейт 2: config_has_multi_break ---
    cfg = _load_config(root)
    groups = cfg.get("market_calendar_by_group", {})
    multi_groups = []  # type: List[str]
    for gname, gcfg in groups.items():
        if not isinstance(gcfg, dict):
            continue
        breaks = gcfg.get("market_daily_breaks", [])
        if isinstance(breaks, list) and len(breaks) > 0:
            multi_groups.append(gname)
    ok2 = len(multi_groups) > 0
    results.append({
        "name": "config_has_multi_break",
        "ok": ok2,
        "details": "ok: %s" % ", ".join(multi_groups) if ok2 else "жоден календар не має market_daily_breaks",
    })

    # --- Підгейт 3: unit_test_multi_break ---
    test_py = root / "tests" / "test_market_calendar.py"
    has_test = False
    if test_py.exists():
        try:
            test_code = test_py.read_text(encoding="utf-8", errors="replace")
            has_test = "TestMultiBreak" in test_code
        except Exception:
            pass
    results.append({
        "name": "unit_test_multi_break",
        "ok": has_test,
        "details": "ok" if has_test else "test_market_calendar.py відсутній або без multi-break тестів",
    })

    all_ok = all(r["ok"] for r in results)
    summary_parts = ["%s=%s" % (r["name"], "OK" if r["ok"] else "FAIL") for r in results]
    return {
        "ok": all_ok,
        "details": "; ".join(summary_parts),
        "sub_gates": results,
        "metrics": {
            "sub_gates_total": len(results),
            "sub_gates_ok": sum(1 for r in results if r["ok"]),
            "multi_break_groups": multi_groups,
        },
    }
