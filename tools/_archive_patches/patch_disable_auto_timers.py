#!/usr/bin/env python3
"""Patch: disable auto-send morning_briefing and daily_review timers.

Archi's own decision (2026-04-13): "прибираю автоматичний morning_briefing таймер.
Ти пишеш 'доброго ранку' — я відповідаю. Ніяких авто-листів."

What this does:
1. _seed_journaling_timers(): skip morning_briefing and daily_review seeding
2. _reseed_after_fire(): skip morning_briefing and daily_review re-seeding
3. Clears morning_briefing + daily_review from directives.json

weekly_review (Friday 21:00) is kept — it's once a week and optional.
"""

import json
import re
import shutil
import sys
from datetime import datetime


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 patch_disable_auto_timers.py /path/to/monitor.py")
        sys.exit(1)

    monitor_path = sys.argv[1]
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # ── Read monitor.py ──
    with open(monitor_path, "r", encoding="utf-8") as f:
        code = f.read()

    # Backup
    backup_path = monitor_path + f".bak_timers.{ts}"
    shutil.copy2(monitor_path, backup_path)
    print(f"Backup: {backup_path}")

    patches_applied = 0

    # ── PATCH 1: Disable morning_briefing seeding in _seed_journaling_timers ──
    # Find:  # ── Morning briefing at 07:00 Kyiv (ADR-033 P5) ──
    #        if "morning_briefing" not in existing_ids:
    # Replace with: skip block
    old_seed_mb = """    # ── Morning briefing at 07:00 Kyiv (ADR-033 P5) ──
    if "morning_briefing" not in existing_ids:"""

    new_seed_mb = """    # ── Morning briefing DISABLED (2026-04-13, Archi decision) ──
    # Auto-send removed: user writes "доброго ранку" → Archi responds.
    # Re-enable: remove the `if False and` guard below.
    if False and "morning_briefing" not in existing_ids:"""

    if old_seed_mb in code:
        code = code.replace(old_seed_mb, new_seed_mb, 1)
        patches_applied += 1
        print("PATCH 1 OK: morning_briefing seeding disabled")
    elif "if False and" in code and "morning_briefing" in code:
        print("PATCH 1 SKIP: already patched")
    else:
        print("PATCH 1 FAIL: marker not found")

    # ── PATCH 2: Disable daily_review seeding in _seed_journaling_timers ──
    old_seed_dr = """    # ── Daily review at 18:00 Kyiv (ADR-033 P5, was 19:00) ──
    if "daily_review" not in existing_ids:"""

    new_seed_dr = """    # ── Daily review DISABLED (2026-04-13, Archi decision) ──
    # Auto-send removed: review only on user request.
    if False and "daily_review" not in existing_ids:"""

    if old_seed_dr in code:
        code = code.replace(old_seed_dr, new_seed_dr, 1)
        patches_applied += 1
        print("PATCH 2 OK: daily_review seeding disabled")
    elif "Daily review DISABLED" in code:
        print("PATCH 2 SKIP: already patched")
    else:
        print("PATCH 2 FAIL: marker not found")

    # ── PATCH 3: Disable morning_briefing re-seeding in _reseed_after_fire ──
    old_reseed_mb = """    if timer_id == "morning_briefing":
        target = (now_kyiv + timedelta(days=1)).replace(
            hour=7, minute=0, second=0, microsecond=0
        )
        d.wake_at.append("""

    new_reseed_mb = """    if timer_id == "morning_briefing":
        # DISABLED (2026-04-13): no auto-reseed, user triggers manually
        _log.info("morning_briefing fired — NOT re-seeding (auto-send disabled)")
        return
        target = (now_kyiv + timedelta(days=1)).replace(
            hour=7, minute=0, second=0, microsecond=0
        )
        d.wake_at.append("""

    if old_reseed_mb in code:
        code = code.replace(old_reseed_mb, new_reseed_mb, 1)
        patches_applied += 1
        print("PATCH 3 OK: morning_briefing re-seeding disabled")
    elif "NOT re-seeding" in code and "morning_briefing" in code:
        print("PATCH 3 SKIP: already patched")
    else:
        print("PATCH 3 FAIL: marker not found")

    # ── PATCH 4: Disable daily_review re-seeding in _reseed_after_fire ──
    old_reseed_dr = """    elif timer_id == "daily_review":
        target = (now_kyiv + timedelta(days=1)).replace(
            hour=18, minute=0, second=0, microsecond=0
        )
        d.wake_at.append("""

    new_reseed_dr = """    elif timer_id == "daily_review":
        # DISABLED (2026-04-13): no auto-reseed, user triggers manually
        _log.info("daily_review fired — NOT re-seeding (auto-send disabled)")
        return
        target = (now_kyiv + timedelta(days=1)).replace(
            hour=18, minute=0, second=0, microsecond=0
        )
        d.wake_at.append("""

    if old_reseed_dr in code:
        code = code.replace(old_reseed_dr, new_reseed_dr, 1)
        patches_applied += 1
        print("PATCH 4 OK: daily_review re-seeding disabled")
    elif "daily_review fired — NOT re-seeding" in code:
        print("PATCH 4 SKIP: already patched")
    else:
        print("PATCH 4 FAIL: marker not found")

    # ── Write patched monitor.py ──
    with open(monitor_path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"\n{patches_applied}/4 patches applied to {monitor_path}")

    # ── PATCH 5: Clear morning_briefing and daily_review from directives.json ──
    directives_path = monitor_path.replace(
        "bot/scheduling/monitor.py", "data/v3_agent_directives.json"
    )
    try:
        with open(directives_path, "r", encoding="utf-8") as f:
            directives = json.load(f)

        wake_at = directives.get("wake_at", [])
        original_count = len(wake_at)
        wake_at_cleaned = [
            wt
            for wt in wake_at
            if wt.get("id") not in ("morning_briefing", "daily_review")
        ]
        removed = original_count - len(wake_at_cleaned)
        directives["wake_at"] = wake_at_cleaned

        # Backup
        dir_backup = directives_path + f".bak_timers.{ts}"
        shutil.copy2(directives_path, dir_backup)

        with open(directives_path, "w", encoding="utf-8") as f:
            json.dump(directives, f, ensure_ascii=False, indent=2)

        print(
            f"PATCH 5 OK: removed {removed} timers from directives "
            f"(remaining: {len(wake_at_cleaned)})"
        )
        remaining_ids = [wt.get("id") for wt in wake_at_cleaned]
        print(f"  Remaining timers: {remaining_ids}")
    except Exception as e:
        print(f"PATCH 5 FAIL: {e}")


if __name__ == "__main__":
    main()
