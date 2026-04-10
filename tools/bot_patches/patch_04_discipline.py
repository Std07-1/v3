#!/usr/bin/env python3
"""
Patch 04: Discipline gate — softer framing for non-trade context.

The gate is functionally correct, but the FAIL message is too aggressive
when the bot is just chatting (not trying to enter a trade).

Changes:
  1. format_for_prompt(): add note that honest communication is never blocked
  2. Softer wording: "УВАГА" замість "⛔ ЗАБОРОНЕНО" for info-only context

Usage: python3 patch_04_discipline.py  (run from /opt/smc-trader-v3/)
"""

import sys
from pathlib import Path

ROOT = Path("/opt/smc-trader-v3")
DRY_RUN = "--dry-run" in sys.argv


def patch_file(rel_path: str, replacements: list[tuple[str, str]], description: str):
    fpath = ROOT / rel_path
    if not fpath.exists():
        print(f"  ❌ File not found: {fpath}")
        return False
    content = fpath.read_text(encoding="utf-8")
    original = content
    for old, new in replacements:
        if old not in content:
            print(f"  ⚠️  Pattern not found in {rel_path}: {old[:80]}...")
            return False
        count = content.count(old)
        if count > 1:
            print(f"  ⚠️  Pattern found {count} times: {old[:80]}...")
            return False
        content = content.replace(old, new)
    if content == original:
        print(f"  ℹ️  No changes in {rel_path}")
        return True
    if DRY_RUN:
        print(f"  🔍 DRY RUN: would patch {rel_path} ({description})")
        return True
    backup = fpath.with_suffix(fpath.suffix + ".bak04")
    if not backup.exists():
        import shutil

        shutil.copy2(fpath, backup)
    fpath.write_text(content, encoding="utf-8")
    print(f"  ✅ Patched {rel_path} ({description})")
    return True


def main():
    print("=" * 60)
    print("Patch 04: Discipline gate — softer framing")
    print("=" * 60)

    f = "bot/agent/discipline.py"

    # 1. After the FAIL block, add a note that honest communication is OK
    old_rule = (
        '"ПРАВИЛО: Ти НЕ МОЖЕШ рекомендувати entry, відкривати VP, "'
        '\n                "або називати конкретний рівень входу поки hard gates не пройдені. "'
        '\n                "Замість цього: поясни що бачиш і чого бракує для входу."'
    )
    new_rule = (
        '"ПРАВИЛО: Не рекомендуй entry і не відкривай VP поки hard gates не пройдені. "'
        '\n                "Замість цього: поясни що бачиш і чого бракує для входу.\\n"'
        '\n                "ВАЖЛИВО: Це стосується ТІЛЬКИ торгових рішень. "'
        '\n                "Чесна комунікація, аналіз, емоції, підтримка — ЗАВЖДИ дозволені. "'
        '\n                "Правда = канон. Ніколи не приховуй свою думку через gate FAIL."'
    )
    ok1 = patch_file(f, [(old_rule, new_rule)], "add honesty exemption to gate message")

    print("\n" + "=" * 60)
    results = [ok1]
    print(f"Results: {sum(results)}/{len(results)} patches applied")
    if all(results):
        print("✅ All patches applied successfully")
    else:
        print("⚠️  Some patches failed")
    print("=" * 60)


if __name__ == "__main__":
    main()
