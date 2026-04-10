#!/usr/bin/env python3
# Enrich budget context in build_directives_context()
# Replaces simple budget line with detailed budget intelligence block
import shutil
from pathlib import Path

TARGET = Path("/opt/smc-trader-v3/bot/state/directives.py")
BACKUP = TARGET.with_suffix(".py.bak_budget")

OLD_BLOCK = """    # Stats + Budget
    if budget_limit > 0:
        _bpct = d.estimated_cost_usd_today / budget_limit * 100
        _bmode = " — ECONOMY MODE" if _bpct >= 80 else ""
        parts.append(
            f"\\n\\U0001f4b0 Бюджет: ${d.estimated_cost_usd_today:.2f} / ${budget_limit:.2f} ({_bpct:.0f}%){_bmode}"
        )
        parts.append(
            f"Статистика: {d.agent_calls_today} calls, {d.messages_sent_today} msgs"
        )
    else:
        parts.append(
            f"\\nСтатистика за сьогодні: {d.agent_calls_today} calls, "
            f"{d.messages_sent_today} messages, ~${d.estimated_cost_usd_today:.3f} spent"
        )"""

NEW_BLOCK = """    # Stats + Budget (enriched for budget intelligence)
    if budget_limit > 0:
        _bpct = d.estimated_cost_usd_today / budget_limit * 100
        _remaining = max(0.0, budget_limit - d.estimated_cost_usd_today)
        _avg_cost = d.estimated_cost_usd_today / max(d.agent_calls_today, 1)
        _calls_left = int(_remaining / _avg_cost) if _avg_cost > 0.005 else 999

        # Energy level indicator
        if _bpct < 30:
            _energy = "\\U0001f7e2 FULL"
        elif _bpct < 50:
            _energy = "\\U0001f7e1 OK"
        elif _bpct < 70:
            _energy = "\\U0001f7e0 MODERATE"
        elif _bpct < 85:
            _energy = "\\U0001f534 LOW"
        else:
            _energy = "\\u26a0\\ufe0f CRITICAL"

        # Session context (UTC hour)
        _utc_h = _kyiv_now.hour - 3  # approximate UTC from Kyiv
        if _utc_h < 0:
            _utc_h += 24
        if 0 <= _utc_h < 7:
            _session = "Asia \\U0001f30f (economy mode recommended)"
        elif 7 <= _utc_h < 12:
            _session = "London \\U0001f1ec\\U0001f1e7 (key session, budget worthy)"
        elif 12 <= _utc_h < 17:
            _session = "NY \\U0001f1fa\\U0001f1f8 (key session, budget worthy)"
        else:
            _session = "Off-hours \\U0001f319 (economy mode recommended)"

        parts.append(f"\\n\\U0001f4b0 Бюджет: ${d.estimated_cost_usd_today:.2f} / ${budget_limit:.2f} ({_bpct:.0f}%) | Energy: {_energy}")
        parts.append(f"   Залишок: ${_remaining:.2f} (~{_calls_left} calls estimated)")
        parts.append(f"   Середня вартість виклику: ${_avg_cost:.3f}")
        parts.append(f"   Сесія: {_session}")
        parts.append(f"   Статистика: {d.agent_calls_today} calls, {d.messages_sent_today} msgs")

        if _bpct >= 85:
            parts.append("   \\u26a0\\ufe0f BUDGET CRITICAL: працюй в мінімальному режимі, тільки A+ моменти")
        elif _bpct >= 70:
            parts.append("   \\U0001f534 Бюджет LOW: Analyst mode для рутини, Strategist тільки для A+")
    else:
        parts.append(
            f"\\nСтатистика за сьогодні: {d.agent_calls_today} calls, "
            f"{d.messages_sent_today} messages, ~${d.estimated_cost_usd_today:.3f} spent"
        )"""


def patch():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found")
        return False

    content = TARGET.read_text(encoding="utf-8")

    if "Energy:" in content and "calls estimated" in content:
        print("ALREADY PATCHED: enriched budget block exists")
        return True

    if OLD_BLOCK not in content:
        # Try to find the block with different whitespace
        print("Trying flexible match...")
        # Find the marker
        marker = "# Stats + Budget"
        if marker not in content:
            print("ERROR: Could not find Stats + Budget marker")
            return False

        # Find from marker to the else block end
        idx_start = content.index(marker)
        # Find the next section after the else block
        search_after = content[idx_start:]
        # Look for "# Recent virtual position" which follows
        next_section = "# Recent virtual position"
        if next_section not in search_after:
            print("ERROR: Could not find next section marker")
            return False
        idx_end = idx_start + search_after.index(next_section)

        old_section = content[idx_start:idx_end]
        print(f"Found block ({len(old_section)} chars), replacing...")

        # Build replacement with consistent indent
        new_section = NEW_BLOCK.lstrip()
        new_section = "    " + new_section  # ensure 4-space indent
        new_section += "\n\n    "  # add spacing before next section

        shutil.copy2(TARGET, BACKUP)
        new_content = content[:idx_start] + new_section + content[idx_end:]
        TARGET.write_text(new_content, encoding="utf-8")
    else:
        shutil.copy2(TARGET, BACKUP)
        new_content = content.replace(OLD_BLOCK, NEW_BLOCK, 1)
        TARGET.write_text(new_content, encoding="utf-8")

    print(f"Backup: {BACKUP}")
    print("PATCHED: budget context enriched")

    # Syntax check
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
