#!/usr/bin/env python3
"""
Patch 02: Expand inner_thought limit 500 → 2000 chars.

Reasoning: 500 chars is too short for meaningful self-reflection.
The bot needs space for chain-of-thought between heartbeats,
trader profile notes, and personality development.

Changes:
  1. bot/state/directives.py line 536: save limit 500 → 2000
  2. bot/state/directives.py line 1558: display limit 500 → 2000
  3. bot/state/directives.py: update tool description for richer usage

Usage: python3 patch_02_inner_thought.py  (run from /opt/smc-trader-v3/)
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
            print(f"  ⚠️  Pattern found {count} times in {rel_path}: {old[:80]}...")
            return False
        content = content.replace(old, new)
    if content == original:
        print(f"  ℹ️  No changes in {rel_path}")
        return True
    if DRY_RUN:
        print(f"  🔍 DRY RUN: would patch {rel_path} ({description})")
        return True
    backup = fpath.with_suffix(fpath.suffix + ".bak02")
    if not backup.exists():
        import shutil

        shutil.copy2(fpath, backup)
    fpath.write_text(content, encoding="utf-8")
    print(f"  ✅ Patched {rel_path} ({description})")
    return True


def main():
    print("=" * 60)
    print("Patch 02: inner_thought 500 → 2000")
    print("=" * 60)

    f = "bot/state/directives.py"

    # 1. Save limit
    ok1 = patch_file(
        f,
        [
            (
                "d.inner_thought = str(thought)[:500]",
                "d.inner_thought = str(thought)[:2000]",
            )
        ],
        "save limit 500 → 2000",
    )

    # 2. Display limit
    ok2 = patch_file(
        f,
        [("{d.inner_thought[:500]}", "{d.inner_thought[:2000]}")],
        "display limit 500 → 2000",
    )

    # 3. Update tool description — richer guidance
    old_desc = (
        '"Твій внутрішній монолог — що ти думаєш прямо зараз. "'
        '\n                    "Це НЕ йде трейдеру, але ти бачиш це при наступному пробудженні. "'
        "\n                    \"Пиши як думаєш: 'H4 bias змінився, але Стас на вихідних — не буду смикати'. \""
        '\n                    "Це твоя пам\'ять між думками — ланцюжок міркувань."'
    )
    new_desc = (
        '"Твій внутрішній монолог — що ти думаєш і відчуваєш прямо зараз. "'
        '\n                    "Це НЕ йде трейдеру, тільки ти це бачиш при наступному пробудженні. "'
        '\n                    "Пиши розгорнуто (до 2000 символів): "'
        '\n                    "• Що ти зараз бачиш на ринку і що це означає "'
        '\n                    "• Що ти думаєш про Стаса — його стиль, настрій, потреби "'
        '\n                    "• Де ти помилився і чому, що хочеш покращити "'
        '\n                    "• Твої сумніви, гіпотези, інтуїція "'
        '\n                    "Це твоя самосвідомість — ланцюжок міркувань між пробудженнями. "'
        '\n                    "Чим глибше і чесніше — тим кращим ти стаєш."'
    )
    ok3 = patch_file(f, [(old_desc, new_desc)], "richer tool description")

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
