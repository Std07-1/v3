#!/usr/bin/env python3
# Soften economy mode: graduated model override instead of hard Sonnet force at 80%
# 80-90%: warning only (Archi is now budget-aware via S15, let it decide)
# 90%+: force Sonnet (analyst)
# 95%+: force Haiku (sentinel) — survival mode
import shutil
from pathlib import Path

TARGET = Path("/opt/smc-trader-v3/bot/scheduling/monitor.py")
BACKUP = TARGET.with_suffix(".py.bak_economy")

OLD_BLOCK = """    # Economy mode: force Sonnet for proactive calls to save budget
    _budget_pct = (
        d.estimated_cost_usd_today / cfg.safety.max_daily_budget_usd * 100      
        if cfg.safety.max_daily_budget_usd > 0
        else 0
    )
    if (
        _budget_pct >= cfg.safety.budget_warn_pct
        and model == cfg.agent.model_strategist
    ):
        _log.info(
            "ECONOMY_MODE: proactive downgrade Opus → Sonnet (%.0f%%)", _budget_pct
        )
        model = cfg.agent.model_analyst"""

NEW_BLOCK = """    # Economy mode: graduated model downgrade based on budget spend
    # 80-90%: warning only — Archi has budget intelligence (S15), let it decide
    # 90%+: force Sonnet (analyst) — hard safety rail
    # 95%+: force Haiku (sentinel) — survival mode
    _budget_pct = (
        d.estimated_cost_usd_today / cfg.safety.max_daily_budget_usd * 100      
        if cfg.safety.max_daily_budget_usd > 0
        else 0
    )
    if _budget_pct >= 95:
        if model != cfg.agent.model_sentinel:
            _log.warning(
                "ECONOMY_SURVIVAL: force Haiku (%.0f%% budget spent)", _budget_pct
            )
            model = cfg.agent.model_sentinel
    elif _budget_pct >= 90 and model == cfg.agent.model_strategist:
        _log.info(
            "ECONOMY_HARD: proactive downgrade Opus → Sonnet (%.0f%%)", _budget_pct
        )
        model = cfg.agent.model_analyst
    elif (
        _budget_pct >= cfg.safety.budget_warn_pct
        and model == cfg.agent.model_strategist
    ):
        _log.info(
            "ECONOMY_SOFT: budget %.0f%% — Archi requested Opus, allowing (budget-aware agent)",
            _budget_pct,
        )"""


def patch():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found")
        return False

    content = TARGET.read_text(encoding="utf-8")

    if "ECONOMY_SURVIVAL" in content:
        print("ALREADY PATCHED: graduated economy mode exists")
        return True

    if OLD_BLOCK not in content:
        print("ERROR: Could not find exact OLD_BLOCK. Trying flexible match...")
        marker = "# Economy mode: force Sonnet"
        if marker not in content:
            print("ERROR: marker not found")
            return False

        idx = content.index(marker)
        # Find end: "model = cfg.agent.model_analyst" after the marker
        search = content[idx:]
        end_marker = "model = cfg.agent.model_analyst"
        if end_marker not in search:
            print("ERROR: end marker not found")
            return False
        end_idx = idx + search.index(end_marker) + len(end_marker)
        old = content[idx:end_idx]
        print(f"Flexible match found ({len(old)} chars)")

        shutil.copy2(TARGET, BACKUP)
        new_content = content[:idx] + NEW_BLOCK.lstrip() + content[end_idx:]
        TARGET.write_text(new_content, encoding="utf-8")
    else:
        shutil.copy2(TARGET, BACKUP)
        new_content = content.replace(OLD_BLOCK, NEW_BLOCK, 1)
        TARGET.write_text(new_content, encoding="utf-8")

    print(f"Backup: {BACKUP}")
    print("PATCHED: graduated economy mode")

    import py_compile

    try:
        py_compile.compile(str(TARGET), doraise=True)
        print("SYNTAX: OK")
        return True
    except py_compile.PyCompileError as e:
        print(f"SYNTAX ERROR: {e}")
        shutil.copy2(BACKUP, TARGET)
        print("RESTORED from backup")
        return False


if __name__ == "__main__":
    ok = patch()
    raise SystemExit(0 if ok else 1)
