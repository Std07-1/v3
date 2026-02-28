"""
Exit-gate: htf_available — перевірка доступності HTF (H4/D1) даних.

Статичний gate: аналізує код і конфіг, щоб гарантувати:
1. H4/D1 є в allowlist (config.json)
2. Cold-start для H4/D1 ввімкнений і має catch-up (без пропуску)
3. On-close для H4/D1 ввімкнений і без подвійного застосування _last_trading_minute_open_ms
4. Redis priming включає H4/D1 з tail_n > 0
5. JSONL disk має бари для H4/D1 (runtime check, лише якщо data_root існує)

initiative: P2X.7-D1
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# sub-gate helpers
# ---------------------------------------------------------------------------

_SUBGATES: List[str] = [
    "allowlist_htf",
    "coldstart_catchup",
    "onclose_no_double_apply",
    "redis_priming_htf",
    "disk_data_htf",
]


def _load_config(root: str) -> dict:
    path = os.path.join(root, "config.json")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_file(root: str, rel_path: str) -> str:
    path = os.path.join(root, rel_path)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. allowlist_htf: H4/D1 в tf_allowlist_s та broker_base_tfs_s
# ---------------------------------------------------------------------------
def _check_allowlist_htf(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    cfg = _load_config(root)
    allowlist = cfg.get("tf_allowlist_s", [])
    broker_base = cfg.get("broker_base_tfs_s", [])
    derived_tfs = cfg.get("derived_tfs_s", [])

    missing_allow = []
    missing_source = []  # TF має бути або в broker_base, або в derived_tfs
    for tf in [14400, 86400]:
        if tf not in allowlist:
            missing_allow.append(tf)
        if tf not in broker_base and tf not in derived_tfs:
            missing_source.append(tf)

    if missing_allow or missing_source:
        return (
            False,
            f"missing in allowlist: {missing_allow}, missing in broker/derived: {missing_source}",
            {"missing_allow": missing_allow, "missing_source": missing_source},
        )
    return True, "H4/D1 в tf_allowlist_s та broker_base/derived_tfs_s", {}


# ---------------------------------------------------------------------------
# 2. coldstart_catchup: cold-start не пропускає коли дані є на диску
# ---------------------------------------------------------------------------
def _check_coldstart_catchup(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    cfg = _load_config(root)
    # ADR-0023: connector disabled → cold-start N/A
    if not cfg.get("broker_base_tfs_s", []):
        return True, "broker_base_tfs_s=[] — connector disabled (ADR-0023), skip", {}
    if not cfg.get("broker_base_cold_start_enabled", False):
        return False, "broker_base_cold_start_enabled=false", {}

    src = _read_file(root, "runtime/ingest/polling/engine_b.py")
    if not src:
        return False, "engine_b.py не знайдено", {}

    # Шукаємо стару логіку: пропуск коли дані є
    skip_pattern = re.compile(
        r"if\s+self\._last_saved_base\.get\(tf_s\)\s+is\s+not\s+None\s*:\s*\n"
        r"\s*.*пропуск.*\n"
        r"\s*continue",
        re.MULTILINE,
    )
    if skip_pattern.search(src):
        return (
            False,
            "cold-start пропускає коли дані на диску (немає catch-up)",
            {"found_skip_pattern": True},
        )

    # Перевіряємо наявність catch-up логіки
    has_catchup = "catch-up" in src or "gap_bars" in src
    if not has_catchup:
        return (
            False,
            "catch-up логіка не знайдена у cold-start",
            {"has_catchup": False},
        )

    return True, "cold-start працює як catch-up (gap-based N)", {}


# ---------------------------------------------------------------------------
# 3. onclose_no_double_apply: on-close без подвійного _last_trading_minute_open_ms
# ---------------------------------------------------------------------------
def _check_onclose_no_double_apply(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    cfg = _load_config(root)
    # ADR-0023: connector disabled → on-close N/A
    if not cfg.get("broker_base_tfs_s", []):
        return True, "broker_base_tfs_s=[] — connector disabled (ADR-0023), skip", {}
    if not cfg.get("broker_base_fetch_on_close", False):
        return False, "broker_base_fetch_on_close=false", {}

    src = _read_file(root, "runtime/ingest/polling/engine_b.py")
    if not src:
        return False, "engine_b.py не знайдено", {}

    # Знаходимо тіло _fetch_base_from_broker_on_close
    fn_match = re.search(
        r"def _fetch_base_from_broker_on_close\(self.*?\).*?:\n(.*?)(?=\n    def |\nclass |\Z)",
        src,
        re.DOTALL,
    )
    if not fn_match:
        return False, "_fetch_base_from_broker_on_close не знайдено", {}

    body = fn_match.group(1)

    # Перевірка: не має бути подвійного застосування
    double_apply = re.search(
        r"last_trading_open\s*=\s*self\._last_trading_minute_open_ms\s*\(\s*anchor_open",
        body,
    )
    if double_apply:
        return (
            False,
            "подвійне застосування _last_trading_minute_open_ms (зсув на 1 хв)",
            {"double_apply": True},
        )

    # Перевірка: expected_last має використовувати b1, не b1 - 60_000
    old_expected = re.search(r"_last_trading_minute_open_ms\s*\(\s*b1\s*-\s*60", body)
    if old_expected:
        return (
            False,
            "expected_last використовує b1-60_000 замість b1",
            {"old_expected_last": True},
        )

    return True, "on-close без подвійного застосування, expected_last=ltm(b1)", {}


# ---------------------------------------------------------------------------
# 4. redis_priming_htf: H4/D1 в redis_priming_tfs_s з tail_n > 0
# ---------------------------------------------------------------------------
def _check_redis_priming_htf(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    cfg = _load_config(root)
    if not cfg.get("redis_priming_enabled", False):
        return False, "redis_priming_enabled=false", {}

    priming_tfs = cfg.get("redis_priming_tfs_s", [])
    redis_cfg = cfg.get("redis", {})
    tail_n = redis_cfg.get("tail_n_by_tf_s", {})

    issues = []
    for tf in [14400, 86400]:
        if tf not in priming_tfs:
            issues.append(f"tf={tf} not in redis_priming_tfs_s")
        n = tail_n.get(str(tf), 0)
        if n <= 0:
            issues.append(f"tf={tf} tail_n={n} (має бути >0)")

    if issues:
        return False, "; ".join(issues), {"issues": issues}

    h4_n = tail_n.get("14400", 0)
    d1_n = tail_n.get("86400", 0)
    return True, f"H4 tail_n={h4_n}, D1 tail_n={d1_n}", {"h4_tail_n": h4_n, "d1_tail_n": d1_n}


# ---------------------------------------------------------------------------
# 5. disk_data_htf: JSONL part-файли існують для H4/D1
# ---------------------------------------------------------------------------
def _check_disk_data_htf(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    data_root = os.path.join(root, "data_v3")
    if not os.path.isdir(data_root):
        return True, "data_v3/ не знайдено (пропуск — можливо чистий репо)", {}

    # Перевіряємо XAU_USD як reference symbol
    symbol_dir = os.path.join(data_root, "XAU_USD")
    if not os.path.isdir(symbol_dir):
        return True, "XAU_USD не знайдено (пропуск)", {}

    results = {}
    all_ok = True
    for tf_name, tf_s in [("H4", "tf_14400"), ("D1", "tf_86400")]:
        tf_dir = os.path.join(symbol_dir, tf_s)
        if not os.path.isdir(tf_dir):
            results[tf_name] = "немає директорії"
            all_ok = False
            continue
        parts = sorted(
            f for f in os.listdir(tf_dir) if f.startswith("part-") and f.endswith(".jsonl")
        )
        results[tf_name] = f"{len(parts)} part-файлів"
        if len(parts) < 5:
            all_ok = False
            results[tf_name] += " (< 5, замало)"

    if not all_ok:
        return False, str(results), results
    return True, str(results), results


# ---------------------------------------------------------------------------
# run_gate — entry point
# ---------------------------------------------------------------------------

_CHECKS = {
    "allowlist_htf": _check_allowlist_htf,
    "coldstart_catchup": _check_coldstart_catchup,
    "onclose_no_double_apply": _check_onclose_no_double_apply,
    "redis_priming_htf": _check_redis_priming_htf,
    "disk_data_htf": _check_disk_data_htf,
}


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Запуск gate з усіма sub-gates."""
    root = inputs.get("root", ".")

    ok_all = True
    parts: list[str] = []
    metrics: Dict[str, Any] = {}

    for name in _SUBGATES:
        fn = _CHECKS[name]
        ok, msg, met = fn(root)
        tag = "OK" if ok else "FAIL"
        parts.append(f"{name}:{tag}")
        metrics[name] = {"ok": ok, "msg": msg, **met}
        if not ok:
            ok_all = False

    return {
        "ok": ok_all,
        "details": "; ".join(parts),
        "metrics": metrics,
    }
