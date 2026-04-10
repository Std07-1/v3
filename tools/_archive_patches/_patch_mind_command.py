#!/usr/bin/env python3
"""Patch: add /mind command to handlers.py — shows Archi's inner state (zero API cost)."""

import re

FILE = "/opt/smc-trader-v3/bot/transport/handlers.py"

# The /mind command handler code to insert after /state command
MIND_HANDLER = """
    # ─── /mind — Inner state (zero API cost) ──────────────────────────────

    @dp.message(Command("mind"))
    async def cmd_mind(msg: Message) -> None:
        if not is_authorized(cfg, msg):
            return
        d = deps.directives_store.load()
        if not d:
            await msg.answer("Directives не завантажені")
            return

        lines = ["🧠 *Внутрішній стан Арчі*\\n"]

        # Mood
        mood_icons = {
            "focused": "🎯", "alert": "⚡", "cautious": "🛡",
            "frustrated": "😤", "excited": "🔥", "calm": "😌",
        }
        m = d.mood or "—"
        icon = mood_icons.get(m, "💭")
        lines.append(f"{icon} *Mood*: {m}")

        # Inner thought (last monologue)
        if d.inner_thought:
            thought = d.inner_thought[:500]
            lines.append(f"\\n💬 *Думка*:\\n_{thought}_")

        # Scratchpad
        if d.scratchpad:
            sp = "\\n".join(f"  • {s[:100]}" for s in d.scratchpad[:7])
            lines.append(f"\\n📝 *Нотатки* ({len(d.scratchpad)}):\\n{sp}")

        # Watch levels
        if d.watch_levels:
            wl = "\\n".join(
                f"  📍 {w.label or w.id}: {w.price}"
                for w in d.watch_levels[:5]
            )
            lines.append(f"\\n👁 *Watch levels* ({len(d.watch_levels)}):\\n{wl}")

        # Active scenario
        if d.active_scenario:
            sc = d.active_scenario
            conf = int(sc.confidence * 100)
            lines.append(
                f"\\n🎯 *Сценарій*: {sc.direction.upper()} "
                f"[{sc.status}] conf={conf}%"
            )
            if sc.thesis:
                lines.append(f"  _{sc.thesis[:200]}_")

        # Last 3 thoughts
        if d.thought_history:
            lines.append(f"\\n🔄 *Останні думки* ({len(d.thought_history)}):")
            for th in d.thought_history[-3:]:
                ts = th.get("ts", 0)
                text = th.get("text", "")[:150]
                mood_t = th.get("mood", "")
                if ts:
                    from datetime import datetime, timezone, timedelta
                    kyiv = timezone(timedelta(hours=3))
                    t_s = datetime.fromtimestamp(ts, kyiv).strftime("%H:%M")
                    lines.append(f"  [{t_s}] ({mood_t}) {text}")
                else:
                    lines.append(f"  ({mood_t}) {text}")

        # Self model highlights
        if d.self_model:
            sm_items = []
            for k, v in d.self_model.items():
                sm_items.append(f"  {k}: {str(v)[:80]}")
            if sm_items:
                lines.append(f"\\n🪞 *Самооцінка*:")
                lines.extend(sm_items[:5])

        # Internal findings (last 3)
        if d.internal_findings:
            lines.append(f"\\n🔍 *Знахідки* ({len(d.internal_findings)}):")
            for f_item in d.internal_findings[-3:]:
                if isinstance(f_item, dict):
                    lines.append(f"  • {str(f_item.get('text', f_item))[:120]}")
                else:
                    lines.append(f"  • {str(f_item)[:120]}")

        # Budget quick view
        budget_limit = cfg.safety.max_daily_budget_usd
        cost = d.estimated_cost_usd_today
        pct = cost / budget_limit * 100 if budget_limit else 0
        calls = d.agent_calls_today
        lines.append(
            f"\\n💰 Бюджет: ${cost:.2f}/${budget_limit:.2f} "
            f"({pct:.0f}%) | Виклики: {calls}"
        )

        text = "\\n".join(lines)
        # Telegram limit 4096 chars
        if len(text) > 4000:
            text = text[:3990] + "\\n…(обрізано)"
        await msg.answer(text, parse_mode="Markdown")

"""

with open(FILE, "r") as fh:
    content = fh.read()

# Find the /context section marker to insert /mind before it
marker = "    # ─── /context"
if marker not in content:
    print("ERROR: marker '# ─── /context' not found")
    exit(1)

if "/mind" in content:
    print("SKIP: /mind already exists in handlers.py")
    exit(0)

# Insert before /context
content = content.replace(marker, MIND_HANDLER + marker)

# Also add /mind to the /help text
help_marker = '            f"/state — ринковий стан (bias, narrative)\\n"'
if help_marker in content:
    content = content.replace(
        help_marker,
        help_marker + '\n            f"/mind — внутрішній стан Арчі (0 API cost)\\n"',
    )

with open(FILE, "w") as fh:
    fh.write(content)

print("OK: /mind command added to handlers.py")
print(f"File size: {len(content)} bytes")
