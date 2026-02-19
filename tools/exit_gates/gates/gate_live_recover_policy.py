"""Exit-gate: live_recover_policy — перевірка LiveRecover в m1_poller.

Ціль: гарантувати що live-recover має rate-limit, bounded n,
і конфігурований через config.json → m1_poller.*.

Підгейти:
1. code_has_recover_check — m1_poller.py має _live_recover_check
2. config_has_recover_params — config.json.m1_poller має live_recover_* параметри
3. rate_limit_present — код містить cooldown та max_total перевірки
"""
from __future__ import annotations

import json
from pathlib import Path


def _read_text(path):
    # type: (Path) -> str
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def run_gate(inputs):
    # type: (dict) -> dict
    root = Path(str(inputs.get("root", "."))).resolve()

    engine_path = root / "runtime" / "ingest" / "polling" / "m1_poller.py"
    config_path = root / "config.json"

    engine_src = _read_text(engine_path)
    sub_gates = []

    # --- 1. code_has_recover_check ---
    has_check = "def _live_recover_check" in engine_src
    has_finish = "def _live_recover_finish" in engine_src
    has_hook = "_live_recover_check()" in engine_src
    ok1 = has_check and has_finish and has_hook
    sub_gates.append({
        "name": "code_has_recover_check",
        "ok": ok1,
        "details": "check=%s finish=%s hook=%s" % (has_check, has_finish, has_hook),
    })

    # --- 2. config_has_recover_params (m1_poller section) ---
    cfg = {}
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    m1_cfg = cfg.get("m1_poller", {})
    if not isinstance(m1_cfg, dict):
        m1_cfg = {}
    required_keys = [
        "live_recover_threshold_bars",
        "live_recover_max_bars_per_cycle",
        "live_recover_cooldown_s",
        "live_recover_max_total_bars",
        "live_recover_log_interval_s",
    ]
    present = [k for k in required_keys if k in m1_cfg]
    missing = [k for k in required_keys if k not in m1_cfg]
    ok2 = len(missing) == 0
    sub_gates.append({
        "name": "config_has_recover_params",
        "ok": ok2,
        "details": "present=%d missing=%s" % (len(present), ",".join(missing) or "none"),
    })

    # --- 3. rate_limit_present ---
    has_cooldown = "_live_recover_cooldown_s" in engine_src
    has_max_total = "_live_recover_max_total_bars" in engine_src
    has_max_per_cycle = "_live_recover_max_bars_per_cycle" in engine_src
    ok3 = has_cooldown and has_max_total and has_max_per_cycle
    sub_gates.append({
        "name": "rate_limit_present",
        "ok": ok3,
        "details": "cooldown=%s max_total=%s max_per_cycle=%s" % (
            has_cooldown, has_max_total, has_max_per_cycle,
        ),
    })

    all_ok = all(sg["ok"] for sg in sub_gates)
    return {
        "ok": all_ok,
        "details": "%d/%d sub-gates OK" % (
            sum(1 for sg in sub_gates if sg["ok"]),
            len(sub_gates),
        ),
        "sub_gates": sub_gates,
    }
