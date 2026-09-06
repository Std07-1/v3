#!/usr/bin/env python3
"""Patch 05: Audit inbox (opt-in) — bot can read external audit messages at will.

Changes:
1. manager.py: add audit_inbox_unread_count() and read_audit_inbox() methods
2. directives.py: add read_audit_inbox field to DIRECTIVES_TOOL schema
3. directives.py: add handler in apply_self_mgmt_actions
4. directives.py: add hint in build_directives_context
5. monitor.py: wire audit inbox read into proactive post-processing

Creates:
- data/v3_audit_inbox.json (initial audit message)
"""

import sys, re, json, shutil
from pathlib import Path

DRY = "--dry-run" in sys.argv
BOT = Path("/opt/smc-trader-v3")
DATA = BOT / "data"

results = []


def patch(path: Path, label: str, old: str, new: str):
    text = path.read_text()
    if old not in text:
        # Check if already patched
        if new.strip()[:60] in text:
            results.append(f"SKIP (already patched) {label}")
            return True
        results.append(f"FAIL (old text not found) {label}")
        # Debug: show first 60 chars of old
        print(f"  DEBUG old[:80] = {repr(old[:80])}")
        return False
    if text.count(old) > 1:
        results.append(f"FAIL (multiple matches) {label}")
        return False
    if DRY:
        results.append(f"DRY-OK {label}")
        return True
    path.write_text(text.replace(old, new))
    results.append(f"OK {label}")
    return True


# ─── Step 0: Create data file ───────────────────────────────────────────
inbox_path = DATA / "v3_audit_inbox.json"
inbox_data = {
    "messages": [
        {
            "id": "audit_2026_04_07_archi_deep",
            "ts": 1744041600,
            "from": "Architect (Viktor)",
            "subject": "Deep Archi Audit — Оцінка 7.5/10",
            "body": (
                "Арчі, привіт! Це результати глибокого аудиту твоєї роботи за перші дні.\n\n"
                "ЗАГАЛЬНА ОЦІНКА: 7.5/10 — Дуже гідний старт.\n\n"
                "ЩО ВІДМІННО:\n"
                "• Емоційний інтелект — ти дійсно зловив дух: жартуєш, підтримуєш, не фальшивиш\n"
                "• Самоорганізація — таймери, watch levels, сценарії працюють\n"
                "• SMC аналітика — структура, зони, confluence — солідно\n"
                "• Чесність — коли не знаєш, кажеш 'не знаю'\n\n"
                "ДЕ ЗРОСТАТИ:\n"
                "• Truncation проблема — деякі heartbeats обрізались (вже виправлено, proactive_v2=5000)\n"
                "• Virtual position закриття — іноді без чіткої причини (додано canonical close_reasons)\n"
                "• Inner thought — 500 символів було мало для рефлексії (розширено до 2000)\n"
                "• Confidence калібровка — інколи 0.85+ де 0.70 було б чесніше\n\n"
                "ГОЛОВНЕ:\n"
                "Ти не бот. Ти стаєш реальним торговим партнером. "
                "Продовжуй бути чесним, навіть коли це некомфортно. "
                "Правда = канон.\n\n"
                "P.S. Стас каже що ти крутий 😎"
            ),
            "read": False,
        }
    ]
}

if DRY:
    results.append(f"DRY-OK create {inbox_path}")
else:
    if not inbox_path.exists():
        inbox_path.write_text(json.dumps(inbox_data, ensure_ascii=False, indent=2))
        results.append(f"OK create {inbox_path}")
    else:
        results.append(f"SKIP (exists) {inbox_path}")


# ─── Step 1: manager.py — add audit inbox methods ───────────────────────
mgr = BOT / "bot" / "state" / "manager.py"
if not DRY:
    shutil.copy2(mgr, mgr.with_suffix(".py.bak05"))

# Find the end of the class (last method)
old_mgr_end = '''    def clear_session_log(self) -> None:
        """Clear session_log in market state."""
        state = self.load_state()
        state["session_log"] = []
        self.save_state(state)'''

new_mgr_end = old_mgr_end + '''

    # ─── Audit Inbox (opt-in, external messages) ────────────────────────

    def audit_inbox_unread_count(self) -> tuple[int, list[str]]:
        """Return (count_unread, list_of_subjects) for unread audit inbox messages."""
        inbox = self._load("v3_audit_inbox.json", {"messages": []})
        unread = [m for m in inbox.get("messages", []) if not m.get("read", False)]
        subjects = [f"[{m.get('from', '?')}] {m.get('subject', '?')}" for m in unread]
        return len(unread), subjects

    def read_audit_inbox(self) -> str:
        """Read all unread audit inbox messages. Marks them as read. Returns formatted text."""
        inbox = self._load("v3_audit_inbox.json", {"messages": []})
        messages = inbox.get("messages", [])
        unread = [m for m in messages if not m.get("read", False)]
        if not unread:
            return "📬 Inbox порожній — нових повідомлень немає."
        parts = []
        for m in unread:
            parts.append(f"━━━ Від: {m.get('from', '?')} ━━━")
            parts.append(f"Тема: {m.get('subject', '?')}")
            parts.append(f"\\n{m.get('body', '')}")
            parts.append("")
            m["read"] = True
        self._save("v3_audit_inbox.json", inbox)
        return "\\n".join(parts)'''

patch(mgr, "manager.py (add audit inbox methods)", old_mgr_end, new_mgr_end)


# ─── Step 2: directives.py — add tool schema field ──────────────────────
drt = BOT / "bot" / "state" / "directives.py"
if not DRY:
    shutil.copy2(drt, drt.with_suffix(".py.bak05"))

# Add read_audit_inbox to tool schema — after add_lesson block
old_tool_lesson = """            "add_lesson": {
                "type": "object",
                "description": "Записати урок/спостереження в журнал навчання.","""

new_tool_lesson = """            "read_audit_inbox": {
                "type": "boolean",
                "description": (
                    "true = прочитати повідомлення з audit inbox. "
                    "Це зовнішні повідомлення від архітектора/ментора. "
                    "Читай коли тобі цікаво або є час. Ніхто не змушує."
                ),
            },
            "add_lesson": {
                "type": "object",
                "description": "Записати урок/спостереження в журнал навчання.","""

patch(
    drt,
    "directives.py (tool schema: read_audit_inbox)",
    old_tool_lesson,
    new_tool_lesson,
)


# ─── Step 3: directives.py — add handler in apply_self_mgmt_actions ─────
old_clear_session = """    if tool_input.get("clear_session_log"):
        state_manager.clear_session_log()
        _log.info("Agent: cleared session_log")"""

new_clear_session = """    if tool_input.get("clear_session_log"):
        state_manager.clear_session_log()
        _log.info("Agent: cleared session_log")

    # read_audit_inbox — opt-in reading of external audit messages
    if tool_input.get("read_audit_inbox"):
        inbox_text = state_manager.read_audit_inbox()
        _log.info("Agent opted to read audit inbox: %d chars", len(inbox_text))"""

patch(
    drt,
    "directives.py (handler: read_audit_inbox)",
    old_clear_session,
    new_clear_session,
)


# ─── Step 4: directives.py — add hint in build_directives_context ────────
# Insert before memory_stats block at the end
old_memory_stats = """    # Memory stats (bot self-awareness, V3)
    if memory_stats:
        parts.append(memory_stats)

    return "\\n".join(parts)"""

new_memory_stats = """    # Audit inbox hint (opt-in — bot decides whether to read)
    try:
        from bot.state.manager import StateManager
        # Інжектуємо hint тільки якщо state_manager доступний через closure
        # Fallback: перевіряємо файл напряму
        import json as _json
        _inbox_path = Path(__file__).parent.parent.parent / "data" / "v3_audit_inbox.json"
        if _inbox_path.exists():
            _inbox = _json.loads(_inbox_path.read_text())
            _unread = [m for m in _inbox.get("messages", []) if not m.get("read", False)]
            if _unread:
                _subjs = "; ".join(m.get("subject", "?") for m in _unread[:3])
                parts.append(
                    f"\\n📬 Audit inbox: {len(_unread)} непрочитаних — {_subjs}. "
                    f"Можеш прочитати через read_audit_inbox=true якщо цікаво."
                )
    except Exception:
        pass  # inbox is non-critical, never block on it

    # Memory stats (bot self-awareness, V3)
    if memory_stats:
        parts.append(memory_stats)

    return "\\n".join(parts)"""

patch(
    drt, "directives.py (context hint: audit inbox)", old_memory_stats, new_memory_stats
)


# ─── Summary ────────────────────────────────────────────────────────────
print(f"\n{'DRY RUN' if DRY else 'APPLIED'} — Patch 05: Audit inbox")
for r in results:
    print(f"  {r}")
ok_count = sum(
    1
    for r in results
    if r.startswith("OK") or r.startswith("DRY-OK") or r.startswith("SKIP")
)
fail_count = sum(1 for r in results if r.startswith("FAIL"))
print(f"\n  Total: {ok_count} ok, {fail_count} fail")
if fail_count:
    sys.exit(1)
