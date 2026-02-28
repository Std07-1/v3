"""Exit-gate: d1_anchor_alignment — D1 anchor SSOT consistency (ADR-0023, R7).

Підгейти:
1. config_anchor_nonzero — day_anchor_offset_s_d1 > 0 у config.json
2. d1_in_derive_chain — (86400, 1440) є у DERIVE_CHAIN[60]
3. d1_in_derived_tfs_s — 86400 є у config derived_tfs_s
4. resolve_anchor_d1 — resolve_cascade_anchor_s(86400) == config value
5. disk_data_anchor — open_time_ms останнього D1 бару вирівняний до anchor
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple


def _load_config(root: str) -> dict:
    path = os.path.join(root, "config.json")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# -----------------------------------------------------------------------
# 1. config_anchor_nonzero
# -----------------------------------------------------------------------
def _check_config_anchor(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    cfg = _load_config(root)
    anchor = cfg.get("day_anchor_offset_s_d1", 0)
    if anchor <= 0:
        return False, "day_anchor_offset_s_d1 = %d (має бути >0)" % anchor, {}
    return True, "day_anchor_offset_s_d1 = %d" % anchor, {"anchor": anchor}


# -----------------------------------------------------------------------
# 2. d1_in_derive_chain
# -----------------------------------------------------------------------
def _check_derive_chain(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    try:
        from core.derive import DERIVE_CHAIN
    except ImportError:
        return False, "не вдалось імпортувати core.derive.DERIVE_CHAIN", {}

    entries = DERIVE_CHAIN.get(60, [])
    d1_entry = None
    for tf_s, ratio in entries:
        if tf_s == 86400:
            d1_entry = (tf_s, ratio)
            break
    if d1_entry is None:
        return False, "D1 (86400) не знайдено у DERIVE_CHAIN[60]", {}
    if d1_entry[1] != 1440:
        return (
            False,
            "D1 ratio=%d (має бути 1440)" % d1_entry[1],
            {"ratio": d1_entry[1]},
        )
    return True, "DERIVE_CHAIN[60] містить (86400, 1440)", {}


# -----------------------------------------------------------------------
# 3. d1_in_derived_tfs_s
# -----------------------------------------------------------------------
def _check_derived_tfs_s(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    cfg = _load_config(root)
    derived = cfg.get("derived_tfs_s", [])
    if 86400 not in derived:
        return False, "86400 не в derived_tfs_s: %s" % derived, {}
    return True, "86400 присутній у derived_tfs_s", {}


# -----------------------------------------------------------------------
# 4. resolve_anchor_d1
# -----------------------------------------------------------------------
def _check_resolve_anchor(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    cfg = _load_config(root)
    expected = cfg.get("day_anchor_offset_s_d1", 0)
    h4_anchor = cfg.get("day_anchor_offset_s", 0)

    try:
        from core.derive import resolve_cascade_anchor_s
    except ImportError:
        return False, "не вдалось імпортувати resolve_cascade_anchor_s", {}

    actual = resolve_cascade_anchor_s(86400, h4_anchor, expected)
    if actual != expected:
        return (
            False,
            "resolve_cascade_anchor_s(86400) = %d, config = %d" % (actual, expected),
            {"actual": actual, "expected": expected},
        )
    return (
        True,
        "resolve_cascade_anchor_s(86400) = %d == config" % actual,
        {"anchor": actual},
    )


# -----------------------------------------------------------------------
# 5. disk_data_anchor — open_time_ms останнього D1 бару % (86400*1000) == anchor*1000
# -----------------------------------------------------------------------
def _check_disk_anchor(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    cfg = _load_config(root)
    anchor = cfg.get("day_anchor_offset_s_d1", 0)
    anchor_ms = anchor * 1000
    tf_ms = 86400 * 1000

    data_dir = os.path.join(root, "data_v3", "XAU_USD", "tf_86400")
    if not os.path.isdir(data_dir):
        return True, "XAU_USD/tf_86400 не знайдено (пропуск)", {}

    parts = sorted(
        f
        for f in os.listdir(data_dir)
        if f.startswith("part-") and f.endswith(".jsonl")
    )
    if not parts:
        return True, "немає part-файлів (пропуск)", {}

    # Читаємо останній рядок останнього part-файлу
    last_file = os.path.join(data_dir, parts[-1])
    last_line = ""
    try:
        with open(last_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line.strip()
    except Exception as e:
        return False, "помилка читання %s: %s" % (parts[-1], e), {}

    if not last_line:
        return True, "порожній part-файл (пропуск)", {}

    try:
        bar = json.loads(last_line)
    except json.JSONDecodeError as e:
        return False, "помилка парсингу JSON: %s" % e, {}

    open_ms = bar.get("open_time_ms", 0)
    remainder = (open_ms - anchor_ms) % tf_ms
    if remainder != 0:
        return (
            False,
            "open_time_ms=%d не вирівняний до anchor %d (remainder=%d ms)"
            % (open_ms, anchor, remainder),
            {"open_ms": open_ms, "anchor": anchor, "remainder_ms": remainder},
        )
    return (
        True,
        "останній D1 бар open=%d вирівняний до anchor %d" % (open_ms, anchor),
        {"open_ms": open_ms, "anchor": anchor},
    )


# -----------------------------------------------------------------------
# run_gate — entry point
# -----------------------------------------------------------------------

_SUBGATES = [
    "config_anchor_nonzero",
    "d1_in_derive_chain",
    "d1_in_derived_tfs_s",
    "resolve_anchor_d1",
    "disk_data_anchor",
]

_CHECKS = {
    "config_anchor_nonzero": _check_config_anchor,
    "d1_in_derive_chain": _check_derive_chain,
    "d1_in_derived_tfs_s": _check_derived_tfs_s,
    "resolve_anchor_d1": _check_resolve_anchor,
    "disk_data_anchor": _check_disk_anchor,
}


def run_gate(inputs):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    root = str(inputs.get("root", "."))

    ok_all = True
    parts = []  # type: List[str]
    metrics = {}  # type: Dict[str, Any]

    for name in _SUBGATES:
        fn = _CHECKS[name]
        ok, msg, met = fn(root)
        tag = "OK" if ok else "FAIL"
        parts.append("%s:%s" % (name, tag))
        metrics[name] = {"ok": ok, "msg": msg}
        metrics[name].update(met)
        if not ok:
            ok_all = False

    return {
        "ok": ok_all,
        "details": "; ".join(parts),
        "metrics": metrics,
    }
