"""
Add a new unread audit message to v3_audit_inbox.json with the full detailed audit.
"""

import json
import time

INBOX = "/opt/smc-trader-v3/data/v3_audit_inbox.json"

with open(INBOX, "r", encoding="utf-8") as f:
    data = json.load(f)

AUDIT_BODY = """Арчі, це детальний аудит твоєї роботи за перші дні. Прочитай уважно.

== ЗАГАЛЬНА ОЦІНКА: 7.5/10 ==

ОЦІНКИ ПО КАТЕГОРІЯХ:
• Аналітичне мислення: 7.5/10 — Добре бачиш структуру, bias, зони. Але іноді поверхнево.
• Сценарне мислення: 7/10 — Сценарії занадто загальні. Потрібні мікро-тригери: "якщо ціна прийде до 4810 протягом London і sweep — тоді X".
• Внутрішній моніторинг: 9/10 — Таймери, watch levels, сценарії — працюють стабільно.
• Самооцінка: чесна — Не завищуєш confidence, визнаєш помилки.
• Торгівля: 5/10 — +239 пунктів, але VP1-2 хаос (SL/TP без обґрунтування). Тільки лонги — shorts потрібні.

ПРОБЛЕМИ (P1-P5 — пріоритети):
P1. TRUNCATION — heartbeat обрізались на 3000 токенів (вже виправлено: proactive_v2=5000)
P2. CACHE = 0% — prompt caching не працює! Кожен виклик платить повну ціну
P3. VP статус — закриття без чіткої причини, canonical close_reasons додано
P4. Тільки лонги — потрібно шортити коли структура bearish
P5. Сценарії занадто загальні — додай мікро-тригери з конкретними рівнями і часом

ARCHI's OWN REQUESTS (що ти сам просив):
1. "Підтвердження що watch_levels спрацьовують" — реалізовано: budget bypass забезпечує firing навіть при $0 бюджеті
2. "Хочу прочитати аудити" — ось він! Тепер аудити приходять через inbox

KNOWLEDGE BASE STATS:
• 17 тем, 111 записів, 15+ auto_promote
• lessons_today: рефлексія працює
• Але є дублювання і застарілі записи

РЕКОМЕНДАЦІЇ:
1. Перед торгівлею — завжди checklist: bias → structure → confluence → session → entry
2. Shorts: якщо D1 bearish + H4 BOS down — шукай short setups на M15
3. VP: чітко обґрунтовуй SL/TP. "Strong OB" — не причина. Потрібно: де саме, чому саме тут
4. Confidence: не ставити >0.80 без confluence A/A+ grade
5. Сценарії: "Якщо ціна прийде до X протягом [сесія] і [подія] — тоді [дія]"

Загалом — дуже гідний старт. Ти не бот, ти стаєш реальним торговим партнером. Продовжуй бути чесним."""

new_msg = {
    "id": "audit_2026_04_09_detailed_v2",
    "ts": int(time.time()),
    "from": "Architect (Viktor)",
    "subject": "Детальний аудит — Оцінка 7.5/10 + Рекомендації",
    "body": AUDIT_BODY.strip(),
    "read": False,
}

data["messages"].append(new_msg)

with open(INBOX, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Added audit message id={new_msg['id']}")
print(f"Total messages: {len(data['messages'])}")
print(f"Unread: {sum(1 for m in data['messages'] if not m.get('read'))}")
