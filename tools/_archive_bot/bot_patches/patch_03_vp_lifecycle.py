#!/usr/bin/env python3
"""
Patch 03: VP lifecycle — structured close_reason + improved tool description.

Problem: VP close_reasons are free-text ("Стас вийшов...", "closed_replaced").
Need: standardized vocabulary + bot guidance on clean VP management.

Changes:
  1. VP tool description: add close_reason enum + lifecycle guidance
  2. _close_virtual: normalize free-text close_reason to canonical values

Usage: python3 patch_03_vp_lifecycle.py  (run from /opt/smc-trader-v3/)
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
    backup = fpath.with_suffix(fpath.suffix + ".bak03")
    if not backup.exists():
        import shutil

        shutil.copy2(fpath, backup)
    fpath.write_text(content, encoding="utf-8")
    print(f"  ✅ Patched {rel_path} ({description})")
    return True


# Canonical close_reasons
CLOSE_REASONS = [
    "tp_hit",  # target price reached
    "sl_hit",  # stop loss triggered
    "manual_exit",  # trader requested exit
    "invalidated",  # thesis broken, structure changed
    "replaced",  # new opposite position opened
    "session_end",  # killzone/session over
    "news_risk",  # high-impact news approaching
    "trailing_stop",  # trailing stop triggered
    "partial_tp",  # partial take profit
    "timeout",  # position held too long without resolution
]


def main():
    print("=" * 60)
    print("Patch 03: VP lifecycle — structured close_reason")
    print("=" * 60)

    f = "bot/state/directives.py"

    # 1. Upgrade VP tool description with close_reason enum + lifecycle guidance
    old_vp_desc = "\"Virtual trade position. action='close' для закриття.\""
    new_vp_desc = (
        '"Virtual trade position (VP). LIFECYCLE:\n"'
        "\n                    \"• action='open': Відкрий ТІЛЬКИ після Discipline Gate PASS + сценарій active.\n\""
        "\n                    \"• action='close': Закрий з ОБОВ'ЯЗКОВИМ close_reason і close_price.\n\""
        '\n                    "ПРАВИЛО: Кожне закриття = окрема дія. close ПОТІМ open, не одночасно.\n"'
        '\n                    "Не міняй VP без причини — це хаос (P3 pitfall)."'
    )
    ok1 = patch_file(f, [(old_vp_desc, new_vp_desc)], "VP tool description upgrade")

    # 2. Upgrade close_reason field with enum
    old_cr = '"close_reason": {"type": "string"},'
    new_cr = (
        '"close_reason": {\n'
        '                        "type": "string",\n'
        '                        "enum": ["tp_hit", "sl_hit", "manual_exit", "invalidated", "replaced", "session_end", "news_risk", "trailing_stop", "partial_tp", "timeout"],\n'
        '                        "description": "Причина закриття. tp_hit/sl_hit/invalidated/manual_exit — основні. Завжди вказуй!"\n'
        "                    },"
    )
    ok2 = patch_file(f, [(old_cr, new_cr)], "close_reason → enum with description")

    # 3. Normalize close_reason in _close_virtual
    old_close = '        vp.status = f"closed_{close_reason}"'
    new_close = (
        "        # Normalize free-text close_reason to canonical value\n"
        '        _CANONICAL_REASONS = {"tp_hit", "sl_hit", "manual_exit", "invalidated", "replaced", "session_end", "news_risk", "trailing_stop", "partial_tp", "timeout", "agent_cleared"}\n'
        "        if close_reason not in _CANONICAL_REASONS:\n"
        '            _log.warning("VP close_reason normalized: %r → manual_exit", close_reason)\n'
        '            close_reason = "manual_exit"\n'
        '        vp.status = f"closed_{close_reason}"'
    )
    ok3 = patch_file(f, [(old_close, new_close)], "normalize free-text close_reason")

    print("\n" + "=" * 60)
    results = [ok1, ok2, ok3]
    print(f"Results: {sum(results)}/{len(results)} patches applied")
    if all(results):
        print("✅ All patches applied successfully")
    else:
        print("⚠️  Some patches failed")
    print("=" * 60)


if __name__ == "__main__":
    main()
