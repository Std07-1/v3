"""Exit-gate: derived_partial — перевірка підтримки partial derived барів.

Ціль: гарантувати що rebuild_from_m1 будує partial бари з extensions
для bucket-ів де break перетинає годину, і що classify_h1_gaps
використовує partial_built замість structural_break.

Підгейти:
1. rebuild_has_partial — rebuild_from_m1.py підтримує partial logic (calendar + extensions)
2. bars_has_extensions — CandleBar має поле extensions
3. classify_uses_partial — classify_h1_gaps.py використовує partial_built/partial_no_bar
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def run_gate(inputs):
    # type: (dict) -> dict
    root = Path(str(inputs.get("root", "."))).resolve()
    results = []  # type: List[dict]

    # --- Підгейт 1: rebuild_has_partial ---
    rebuild_py = root / "tools" / "rebuild_from_m1.py"
    has_partial = False
    if rebuild_py.exists():
        try:
            code = rebuild_py.read_text(encoding="utf-8", errors="replace")
            has_calendar_param = "calendar:" in code or "calendar=" in code or "is_trading" in code
            has_partial_ext = "derive_bar" in code or "GenericBuffer" in code
            has_partial = has_calendar_param and has_partial_ext
        except Exception:
            pass
    results.append({
        "name": "rebuild_has_partial_logic",
        "ok": has_partial,
        "details": "ok" if has_partial else "rebuild_from_m1.py не має partial logic з calendar + derive",
    })

    # --- Підгейт 2: bars_has_extensions ---
    bars_py = root / "core" / "model" / "bars.py"
    has_ext = False
    if bars_py.exists():
        try:
            code = bars_py.read_text(encoding="utf-8", errors="replace")
            has_field = "extensions" in code
            has_to_dict = "self.extensions" in code
            has_ext = has_field and has_to_dict
        except Exception:
            pass
    results.append({
        "name": "bars_has_extensions",
        "ok": has_ext,
        "details": "ok" if has_ext else "CandleBar не має поля extensions або to_dict не включає його",
    })

    # --- Підгейт 3: classify_uses_partial ---
    classify_py = root / "tools" / "diag" / "classify_h1_gaps.py"
    has_classify = False
    if classify_py.exists():
        try:
            code = classify_py.read_text(encoding="utf-8", errors="replace")
            has_built = "partial_built" in code
            has_no_bar = "partial_no_bar" in code
            no_structural = "structural_break" not in code
            has_classify = has_built and has_no_bar and no_structural
        except Exception:
            pass
    results.append({
        "name": "classify_uses_partial",
        "ok": has_classify,
        "details": "ok" if has_classify else "classify_h1_gaps.py ще має structural_break або не має partial_built",
    })

    ok_count = sum(1 for r in results if r["ok"])
    return {
        "gate": "derived_partial",
        "ok": ok_count == len(results),
        "sub_gates": results,
        "summary": "%d/%d OK" % (ok_count, len(results)),
    }
