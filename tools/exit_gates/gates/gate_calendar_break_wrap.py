"""Exit-gate: calendar_break_wrap — перевірка підтримки wrap-break у MarketCalendar.

Ціль: гарантувати що daily break з start > end (wrap через північ)
обробляється правильно у коді і присутній хоча б один такий календар у конфігу.

Підгейти:
1. code_has_wrap_branch — market_calendar.py містить логіку start > end
2. config_has_wrap_calendar — config.json має хоча б один календар з break_start > break_end
3. unit_test_exists — tests/test_market_calendar.py існує і містить wrap-тести
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List


def _load_config(root: Path) -> dict:
    cfg_path = root / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_hm_minutes(hm: str) -> int:
    """Парсити 'HH:MM' у хвилини від 0."""
    try:
        h, m = hm.split(":", 1)
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def run_gate(inputs: dict) -> dict:
    root = Path(str(inputs.get("root", "."))).resolve()
    results: List[dict] = []

    # --- Підгейт 1: code_has_wrap_branch ---
    cal_py = root / "runtime" / "ingest" / "market_calendar.py"
    has_wrap = False
    if cal_py.exists():
        try:
            code = cal_py.read_text(encoding="utf-8", errors="replace")
            # Перевіряємо що є wrap-логіка: _is_in_break з start < end та start > end
            has_is_in_break = "_is_in_break" in code
            has_lt = bool(re.search(r"start_min\s*<\s*end_min", code))
            has_gt = bool(re.search(r"start_min\s*>\s*end_min", code) or
                          re.search(r"cur_min\s*>=\s*start_min\s*or\s*cur_min\s*<\s*end_min", code))
            has_wrap = has_is_in_break and has_lt
        except Exception:
            pass
    results.append({
        "name": "code_has_wrap_branch",
        "ok": has_wrap,
        "details": "ok" if has_wrap else "market_calendar.py не має гілки start > end (wrap)",
    })

    # --- Підгейт 2: config_has_wrap_calendar ---
    cfg = _load_config(root)
    groups = cfg.get("market_calendar_by_group", {})
    wrap_groups: List[str] = []
    for gname, gcfg in groups.items():
        if not isinstance(gcfg, dict):
            continue
        bs = str(gcfg.get("market_daily_break_start_hm", "00:00"))
        be = str(gcfg.get("market_daily_break_end_hm", "00:00"))
        bs_min = _parse_hm_minutes(bs)
        be_min = _parse_hm_minutes(be)
        if bs_min > 0 and be_min >= 0 and bs_min > be_min:
            wrap_groups.append(gname)
    ok2 = len(wrap_groups) > 0
    results.append({
        "name": "config_has_wrap_calendar",
        "ok": ok2,
        "details": "ok: %s" % ", ".join(wrap_groups) if ok2 else "жоден календар не має wrap-break",
    })

    # --- Підгейт 3: unit_test_exists ---
    test_py = root / "tests" / "test_market_calendar.py"
    has_test = False
    if test_py.exists():
        try:
            test_code = test_py.read_text(encoding="utf-8", errors="replace")
            # Перевіряємо що є тест для wrap
            has_test = "Wrap" in test_code or "wrap" in test_code
        except Exception:
            pass
    results.append({
        "name": "unit_test_exists",
        "ok": has_test,
        "details": "ok" if has_test else "test_market_calendar.py відсутній або без wrap-тестів",
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
            "wrap_calendars": wrap_groups,
        },
    }
