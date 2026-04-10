#!/usr/bin/env python3
"""
Patch 01: Fix kb_summary crash + increase proactive_v2 token limit.

Root cause: sorted() in kb_summary() fails when Claude returns `importance`
as string instead of int (e.g. "high" instead of 3).

Changes:
  1. bot/state/manager.py — add _safe_int() helper, fix 3 sorted() calls
  2. bot/config.py — proactive_v2: 3000 → 5000

Usage: python3 patch_01_crash_and_tokens.py  (run from /opt/smc-trader-v3/)
"""

import re
import sys
from pathlib import Path

ROOT = Path("/opt/smc-trader-v3")
DRY_RUN = "--dry-run" in sys.argv


def patch_file(rel_path: str, replacements: list[tuple[str, str]], description: str):
    """Apply text replacements to a file."""
    fpath = ROOT / rel_path
    if not fpath.exists():
        print(f"  ❌ File not found: {fpath}")
        return False

    content = fpath.read_text(encoding="utf-8")
    original = content

    for old, new in replacements:
        if old not in content:
            print(f"  ⚠️  Pattern not found in {rel_path}: {old[:60]}...")
            return False
        count = content.count(old)
        if count > 1:
            print(
                f"  ⚠️  Pattern found {count} times (expected 1) in {rel_path}: {old[:60]}..."
            )
            return False
        content = content.replace(old, new)

    if content == original:
        print(f"  ℹ️  No changes needed in {rel_path}")
        return True

    if DRY_RUN:
        print(f"  🔍 DRY RUN: would patch {rel_path} ({description})")
        return True

    # Backup
    backup = fpath.with_suffix(fpath.suffix + ".bak01")
    fpath.rename(backup)
    fpath.write_text(content, encoding="utf-8")
    print(f"  ✅ Patched {rel_path} ({description})")
    return True


def main():
    print("=" * 60)
    print("Patch 01: Fix crash + token limit")
    print("=" * 60)

    # ── 1. manager.py: add _safe_int helper ──
    print("\n[1/2] Patching bot/state/manager.py...")

    # 1a. Add _safe_int after imports
    ok1 = patch_file(
        "bot/state/manager.py",
        [
            (
                "_DEFAULT_MAX_HISTORY = 40",
                '_DEFAULT_MAX_HISTORY = 40\n\n\ndef _safe_int(val, default: int = 0) -> int:\n    """Safely cast to int; Claude sometimes returns str for numeric fields."""\n    try:\n        return int(val)\n    except (ValueError, TypeError):\n        return default',
            )
        ],
        "add _safe_int helper",
    )

    if not ok1:
        print("  ❌ Failed to add _safe_int helper")
        sys.exit(1)

    # 1b. Fix sorted() in journal trimming (line ~491)
    ok2 = patch_file(
        "bot/state/manager.py",
        [
            (
                'journal.sort(key=lambda e: (e.get("importance", 3), e.get("ts", 0)))',
                'journal.sort(key=lambda e: (_safe_int(e.get("importance", 3), 3), _safe_int(e.get("ts", 0))))',
            )
        ],
        "fix journal sort — safe int cast",
    )

    # 1c. Fix sorted() in journal re-sort (line ~494)
    ok3 = patch_file(
        "bot/state/manager.py",
        [
            (
                '            journal.sort(key=lambda e: e.get("ts", 0))',
                '            journal.sort(key=lambda e: _safe_int(e.get("ts", 0)))',
            )
        ],
        "fix journal re-sort — safe int cast",
    )

    # 1d. Fix sorted() in dedup_profile_observations (line ~652)
    ok4 = patch_file(
        "bot/state/manager.py",
        [
            (
                'deduped = sorted(seen.values(), key=lambda x: x.get("ts", 0))',
                'deduped = sorted(seen.values(), key=lambda x: _safe_int(x.get("ts", 0)))',
            )
        ],
        "fix dedup sort — safe int cast",
    )

    # 1e. Fix sorted() in kb_summary (line ~718) — THE CRASH SITE
    ok5 = patch_file(
        "bot/state/manager.py",
        [
            (
                'key=lambda e: (e.get("importance", 3), int(e.get("ts", 0))),',
                'key=lambda e: (_safe_int(e.get("importance", 3), 3), _safe_int(e.get("ts", 0))),',
            )
        ],
        "fix kb_summary sort — THE CRASH (int vs str importance)",
    )

    if not all([ok2, ok3, ok4, ok5]):
        print("\n  ⚠️  Some manager.py patches failed — check output above")

    # ── 2. config.py: proactive_v2 3000 → 5000 ──
    print("\n[2/2] Patching bot/config.py...")

    ok6 = patch_file(
        "bot/config.py",
        [("proactive_v2: int = 3000", "proactive_v2: int = 5000")],
        "proactive_v2: 3000 → 5000 (fix truncation)",
    )

    if not ok6:
        print("  ❌ Failed to patch config.py")

    # ── Summary ──
    print("\n" + "=" * 60)
    results = [ok1, ok2, ok3, ok4, ok5, ok6]
    print(f"Results: {sum(results)}/{len(results)} patches applied")
    if all(results):
        print("✅ All patches applied successfully")
    else:
        print("⚠️  Some patches failed — review output above")
    print("=" * 60)


if __name__ == "__main__":
    main()
