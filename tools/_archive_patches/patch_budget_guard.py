#!/usr/bin/env python3
"""
Хірургічний патч monitor.py: 3 фікси для бюджетного контролю.

Фікс 1: _do_agent_call — hard downgrade Opus→Sonnet при >=80% бюджету (proactive calls only).
         I7 compliant: reactive (user messages) не downgradeються.
         Safety rail: budget hard cap дозволений ADR-024.

Фікс 2: Timer budget guard — якщо budget exhausted, таймер зі needs_analysis=True
         відкладається на +4 години замість спалювання $1+.

Фікс 3: Після патчу — встановити model_preference=analyst у directives.

Використання: python3 patch_budget_guard.py /opt/smc-trader-v3/bot/scheduling/monitor.py
"""

import sys
import re
import json
import os
import shutil
from datetime import datetime


def patch_file(path: str) -> bool:
    """Apply all patches to monitor.py. Returns True if successful."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    original = content
    changes = 0

    # ═══════════════════════════════════════════════════════════════════
    # FIX 1: Hard downgrade Opus→Sonnet at >=80% budget (proactive only)
    # ═══════════════════════════════════════════════════════════════════
    # Find the BUDGET_ADVISORY block and add hard downgrade before it
    old_advisory = (
        "    # ADR-024 P2: economy = advisory. Арчі сам обирає модель.\n"
        "    # Система лише додає budget context — без force-downgrade.\n"
    )
    new_advisory = (
        "    # ADR-024 P2 + SAFETY RAIL: hard downgrade Opus→Sonnet at >=80% budget.\n"
        "    # I7 compliant: reactive calls (user messages) are never downgraded.\n"
        "    # Only proactive/timer calls get downgraded — this is a budget hard cap,\n"
        "    # not an autonomy restriction (ADR-024 allows kill switch + budget cap).\n"
    )
    if old_advisory in content:
        content = content.replace(old_advisory, new_advisory)
        changes += 1
    else:
        print("WARNING: Could not find BUDGET_ADVISORY comment block")

    # Now add the hard downgrade logic after _budget_pct calculation
    old_pct_check = (
        "    if _budget_pct >= 95:\n"
        "        _log.warning(\n"
        '            "BUDGET_ADVISORY: %.0f%% spent — agent requested %s, allowing (agent decides)",\n'
        "            _budget_pct,\n"
        "            model,\n"
        "        )\n"
    )
    new_pct_check = (
        "    # HARD DOWNGRADE: proactive Opus calls → Sonnet when budget tight\n"
        '    _is_reactive = (call_type == "reactive")\n'
        '    _is_opus = ("opus" in model.lower())\n'
        "    if _budget_pct >= 80 and _is_opus and not _is_reactive:\n"
        "        _downgrade_to = cfg.agent.model_analyst  # Sonnet\n"
        "        _log.warning(\n"
        '            "BUDGET_HARD_DOWNGRADE: %.0f%% spent — %s → %s (proactive, safety rail)",\n'
        "            _budget_pct, model, _downgrade_to,\n"
        "        )\n"
        "        model = _downgrade_to\n"
        "    elif _budget_pct >= 95:\n"
        "        _log.warning(\n"
        '            "BUDGET_ADVISORY: %.0f%% spent — agent requested %s, allowing (agent decides)",\n'
        "            _budget_pct,\n"
        "            model,\n"
        "        )\n"
    )
    if old_pct_check in content:
        content = content.replace(old_pct_check, new_pct_check)
        changes += 1
    else:
        print("WARNING: Could not find budget percentage check block")

    # ═══════════════════════════════════════════════════════════════════
    # FIX 2: Timer budget guard — defer needs_analysis timers when exhausted
    # ═══════════════════════════════════════════════════════════════════
    # The timer fires at line ~710, before the _do_agent_call at line ~793.
    # After "if timer.needs_analysis:" add a budget check.
    old_timer_comment = "                    # Full Claude call — guaranteed delivery (timers are never gated)\n"
    new_timer_comment = (
        "                    # Budget gate: defer expensive timer calls when exhausted\n"
        "                    if d.estimated_cost_usd_today >= cfg.safety.max_daily_budget_usd:\n"
        "                        _defer_hours = 4\n"
        "                        _defer_epoch = time.time() + _defer_hours * 3600\n"
        "                        _log.warning(\n"
        '                            "TIMER_BUDGET_DEFER: [%s] deferred %dh (budget $%.2f >= $%.2f)",\n'
        "                            timer.id, _defer_hours,\n"
        "                            d.estimated_cost_usd_today, cfg.safety.max_daily_budget_usd,\n"
        "                        )\n"
        "                        d.wake_at.append(WakeTimer(\n"
        "                            id=timer.id,\n"
        "                            time_epoch=_defer_epoch,\n"
        '                            reason=f"[deferred: budget] {timer.reason}",\n'
        "                            prompt=timer.prompt,\n"
        "                            needs_analysis=timer.needs_analysis,\n"
        "                            retry_count=timer.retry_count,\n"
        "                        ))\n"
        "                        directives_store.save(d)\n"
        "                        continue\n"
    )
    if old_timer_comment in content:
        content = content.replace(old_timer_comment, new_timer_comment)
        changes += 1
    else:
        print("WARNING: Could not find timer guaranteed delivery comment")

    if changes < 3:
        print(f"ERROR: Only {changes}/3 patches applied. Aborting.")
        return False

    # Backup
    backup = path + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(path, backup)
    print(f"Backup: {backup}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"OK: {changes}/3 patches applied to {path}")
    return True


def fix_directives(data_dir: str) -> bool:
    """Fix model_preference in directives file."""
    fpath = os.path.join(data_dir, "v3_agent_directives.json")
    if not os.path.exists(fpath):
        print(f"WARNING: {fpath} not found")
        return False

    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)

    old_pref = data.get("model_preference", "")
    if old_pref == "strategist":
        data["model_preference"] = "analyst"
        backup = fpath + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(fpath, backup)
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"OK: model_preference changed: strategist → analyst")
        print(f"Backup: {backup}")
        return True
    else:
        print(f"INFO: model_preference already '{old_pref}', no change needed")
        return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 patch_budget_guard.py <path_to_monitor.py>")
        sys.exit(1)

    monitor_path = sys.argv[1]
    data_dir = os.environ.get("DATA_DIR", "/opt/smc-trader-v3/data")

    if not os.path.exists(monitor_path):
        print(f"ERROR: {monitor_path} not found")
        sys.exit(1)

    ok1 = patch_file(monitor_path)
    ok2 = fix_directives(data_dir)

    if ok1 and ok2:
        print(
            "\n✅ All patches applied. Restart bot: sudo supervisorctl restart smc_trader_v3"
        )
        sys.exit(0)
    else:
        print("\n❌ Some patches failed. Check output above.")
        sys.exit(1)
