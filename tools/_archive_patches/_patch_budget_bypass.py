"""
Patch: Budget-exhausted watch level + VP bypass.

Problem: When budget is exhausted (line ~350-370 in monitor.py), the `continue`
skips ALL price checks — including watch levels and VP SL/TP that cost $0 (simple alerts).
Archi sets watch levels but they never fire when budget is done.

Fix: Insert a lightweight price check block inside the budget exhaustion gate,
BEFORE the sleep/continue. This handles:
- Watch levels: fire simple alert (downgrade needs_analysis to text warning)
- VP SL/TP: fire simple alert (skip full Claude analysis)
"""

import re
from pathlib import Path

MONITOR = Path("/opt/smc-trader-v3/bot/scheduling/monitor.py")

# ── Read current file ──
text = MONITOR.read_text(encoding="utf-8")
lines = text.split("\n")

# ── Find the budget exhaustion block ──
# Pattern: the exact pair of lines
#     await asyncio.sleep(BASE_CYCLE)
#     continue
# that appears RIGHT AFTER the budget_exhausted_notified block
# We identify it by the unique "Команди (/analyze, /deep) працюють." string nearby

# Find the "Команди" line
budget_block_end = None
for i, line in enumerate(lines):
    if "/analyze, /deep" in line:
        # Found the notification message. Now find the next "await asyncio.sleep(BASE_CYCLE)"
        for j in range(i + 1, min(i + 10, len(lines))):
            if (
                "await asyncio.sleep(BASE_CYCLE)" in lines[j]
                and "continue" in lines[j + 1]
            ):
                budget_block_end = j  # line index of the sleep
                break
        break

if budget_block_end is None:
    print("ERROR: Could not find budget exhaustion block insertion point")
    exit(1)

print(
    f"Found insertion point at line {budget_block_end + 1} (0-indexed: {budget_block_end})"
)
print(f"  Before: {lines[budget_block_end].rstrip()}")
print(f"  After:  {lines[budget_block_end + 1].rstrip()}")

# ── Build the bypass code ──
# Same indentation as the sleep line (16 spaces)
BYPASS_CODE = """
                # ─── BUDGET BYPASS: price levels + VP (0-token alerts) ──────
                if current_price and current_price > 0:
                    # Watch levels — fire simple alerts even at $0 budget
                    _bfired = [wl for wl in d.watch_levels if wl.is_triggered(current_price)]
                    if _bfired:
                        _bfired.sort(key=lambda wl: -wl.priority)
                        _blv = _bfired[0]
                        _log.info(
                            "BUDGET_BYPASS watch_level: [%s] price=%.2f %s %.2f",
                            _blv.id, current_price, _blv.direction, _blv.price,
                        )
                        d.fired_events.append({
                            "ts": time.time(), "type": "watch_level",
                            "id": _blv.id, "price": _blv.price,
                            "direction": _blv.direction, "fire_price": current_price,
                        })
                        d.fired_events = d.fired_events[-20:]
                        d.watch_levels = [wl for wl in d.watch_levels if wl.id != _blv.id]
                        _bmsg = (
                            _blv.alert_text
                            or f"\\U0001f514 {_blv.id}: ціна {current_price:.2f} {_blv.direction} {_blv.price:.2f}"
                        )
                        if _blv.needs_analysis:
                            _bmsg += "\\n\\u26a0\\ufe0f Бюджет вичерпано — детальний аналіз відкладено."
                        try:
                            await send_safe_fn(bot, chat_id, _bmsg)
                            d.messages_sent_today += 1
                            state_manager.add_conv("assistant", _bmsg)
                        except Exception:
                            pass
                        directives_store.save(d)

                    # VP SL/TP — simple alert only (skip Claude analysis)
                    _bvp = directives_store.check_virtual_position(d, current_price)
                    if _bvp:
                        _log.info("BUDGET_BYPASS VP event: %s", _bvp)
                        d.fired_events.append({
                            "ts": time.time(), "type": "vp_close",
                            "event": _bvp[:200],
                        })
                        d.fired_events = d.fired_events[-20:]
                        directives_store.save(d)
                        try:
                            await send_safe_fn(
                                bot, chat_id,
                                f"\\U0001f514 VP: {_bvp}\\n\\u26a0\\ufe0f Бюджет вичерпано — аналіз відкладено.",
                            )
                            d.messages_sent_today += 1
                        except Exception:
                            pass
"""

# ── Insert the bypass code BEFORE the sleep/continue ──
new_lines = (
    lines[:budget_block_end]
    + BYPASS_CODE.rstrip().split("\n")
    + [""]
    + lines[budget_block_end:]
)
new_text = "\n".join(new_lines)

# ── Write back ──
MONITOR.write_text(new_text, encoding="utf-8")
print(
    f"\nPATCH APPLIED: {len(BYPASS_CODE.strip().splitlines())} lines inserted before line {budget_block_end + 1}"
)
print(
    "Budget-exhausted price checks (watch levels + VP) now fire simple alerts at $0 cost."
)
