"""Exit-gate: tick preview calendar gate + volume zero + UI polling cap (P2X.6-T1)."""

from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
RESULTS = []  # type: list


def _check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})


def gate_worker_has_calendar_gate():
    """tick_preview_worker.py містить calendar gate перед agg.update."""
    path = os.path.join(REPO, "runtime", "ingest", "tick_preview_worker.py")
    src = open(path, encoding="utf-8").read()
    has_cal_import = "MarketCalendar" in src
    has_gate = "ticks_dropped_calendar_closed" in src
    has_cal_param = "calendars" in src
    _check(
        "worker_has_calendar_gate",
        has_cal_import and has_gate and has_cal_param,
        f"import={has_cal_import} gate={has_gate} param={has_cal_param}",
    )


def gate_config_has_calendar_mapping():
    """config.json має market_calendar_symbol_groups для всіх символів."""
    path = os.path.join(REPO, "config.json")
    cfg = json.load(open(path, encoding="utf-8"))
    symbols = cfg.get("symbols", [])
    groups = cfg.get("market_calendar_symbol_groups", {})
    by_group = cfg.get("market_calendar_by_group", {})
    missing = [s for s in symbols if s not in groups]
    bad_group = [s for s, g in groups.items() if g not in by_group]
    ok = len(missing) == 0 and len(bad_group) == 0
    _check(
        "config_has_calendar_mapping",
        ok,
        f"missing={missing} bad_group={bad_group}",
    )


def gate_tick_agg_volume_zero():
    """tick_agg.py _to_bar використовує v=0.0 та extensions ticks_n."""
    path = os.path.join(REPO, "runtime", "ingest", "tick_agg.py")
    src = open(path, encoding="utf-8").read()
    has_v_zero = "v=0.0" in src
    has_ticks_n = "ticks_n" in src
    _check(
        "tick_agg_volume_zero",
        has_v_zero and has_ticks_n,
        f"v_zero={has_v_zero} ticks_n={has_ticks_n}",
    )


# gate_ui_preview_polling_cap() видалено 2026-06-12: перевіряв
# UPDATES_BACKOFF_PREVIEW_MS — HTTP-polling backoff зі старого vanilla app.js
# (P2X.6-T1 era). UI v4 отримує дані через WS push; константа в ui_v4 ніколи
# не існувала (git log -S по всій історії — нуль), тож підгейт перевіряв
# мертвий механізм і перманентно валив CI.


def run_gate(inputs):
    # type: (dict) -> dict
    """Точка входу для run_exit_gates runner."""
    RESULTS.clear()
    gate_worker_has_calendar_gate()
    gate_config_has_calendar_mapping()
    gate_tick_agg_volume_zero()

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)

    report_dir = str(inputs.get("report_dir", ""))
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, "gate_tick_preview_calendar.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "gate": "gate_tick_preview_calendar",
                    "results": RESULTS,
                    "passed": passed,
                    "total": total,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

    details = "; ".join(
        "{} {}".format(r["name"], "OK" if r["ok"] else "FAIL") for r in RESULTS
    )
    return {
        "ok": passed == total,
        "details": details,
        "metrics": {"passed": passed, "total": total},
    }


if __name__ == "__main__":
    out = run_gate(
        {
            "root": REPO,
            "report_dir": os.path.join(REPO, "reports", "exit_gates", "manual"),
        }
    )
    print(out["details"])
    print("ok={}".format(out["ok"]))
    sys.exit(0 if out["ok"] else 1)
