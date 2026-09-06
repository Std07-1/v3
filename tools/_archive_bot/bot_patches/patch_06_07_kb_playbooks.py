#!/usr/bin/env python3
"""Patch 06+07: Add VP5 playbook + short trading playbooks to KB.

This adds knowledge base entries — NOT code changes.
Writes directly to v3_knowledge.json in the same format as kb_add().
"""

import sys, json, time
from pathlib import Path

DRY = "--dry-run" in sys.argv
kb_path = Path("/opt/smc-trader-v3/data/v3_knowledge.json")
kb = json.loads(kb_path.read_text())

ts = int(time.time())
added = 0


def add_entry(topic: str, text: str, source: str = "architect_inject"):
    global added
    topic = topic.lower().strip()
    if topic not in kb:
        kb[topic] = []
    # Check for duplicate (same text already exists)
    existing_texts = {e.get("text", "")[:100] for e in kb[topic]}
    if text[:100] in existing_texts:
        print(f"  SKIP (dup) {topic}: {text[:60]}...")
        return
    if DRY:
        print(f"  DRY-OK {topic}: {text[:60]}...")
        added += 1
        return
    kb[topic].append({"text": text, "ts": ts, "source": source})
    kb[topic] = kb[topic][-20:]  # same trim as kb_add
    added += 1
    print(f"  OK {topic}: {text[:60]}...")


# ─── VP5 Playbook (5-step VP lifecycle) ──────────────────────────────────────
add_entry(
    "playbooks",
    "VP5 PLAYBOOK — 5 кроків virtual position: "
    "1) Discipline Gate PASS (G1-G6 зелені). "
    "2) Сценарій active + zone confluent (A+ або A grade). "
    "3) action=open з direction/entry/SL/TP/reason. "
    "4) Моніторинг: price у зоні → confidence update, invalidation check щохвилини. "
    "5) action=close з canonical close_reason (tp_hit/sl_hit/manual_exit/invalidated/session_end) + close_price. "
    "НІКОЛИ: open+close в одному tool call. Close ПОТІМ open (якщо новий trade).",
    "architect_inject",
)

add_entry(
    "playbooks",
    "VP CLOSE REASONS — канонічні причини закриття: "
    "tp_hit = ціна досягла TP, sl_hit = ціна досягла SL, manual_exit = ручний вихід з причиною, "
    "invalidated = зона/сценарій більше не валідні, replaced = замінено новим сценарієм, "
    "session_end = кінець торгової сесії, news_risk = новини/волатильність, "
    "trailing_stop = трейлінг стоп, partial_tp = часткове закриття, timeout = час вичерпано.",
    "architect_inject",
)

# ─── Short Trading Playbooks ────────────────────────────────────────────────
add_entry(
    "playbooks",
    "LONDON OPEN PLAYBOOK: "
    "1) 06:55 UTC: перевір Asian range (H/L), ліквідність зверху та знизу. "
    "2) 07:00-07:30: чекай sweep Asian H або L — це тригер. "
    "3) Після sweep: шукай M5 BOS/CHoCH у напрямку sweep → OB/FVG entry. "
    "4) SL за Asian extreme, TP1 = 50% протилежного range, TP2 = протилежний extreme. "
    "5) Якщо немає sweep до 08:00 — пропускай, чекай 08:00-09:00 displacement.",
    "architect_inject",
)

add_entry(
    "playbooks",
    "NY SESSION PLAYBOOK: "
    "1) 12:00 UTC: перевір London range (H/L), невібрані ліквідності зверху/знизу. "
    "2) 12:30-13:00: класичний NY Open sweep London H або L. "
    "3) Після sweep: M5-M15 CHoCH/BOS у зворотному напрямку. "
    "4) Entry на OB/FVG в зоні 62-79% (OTE) of the impulse. "
    "5) SL за swing high/low, TP = Daily POI або протилежна ліквідність. "
    "6) Після 15:00 UTC — тільки management, нові entries заборонені (G2).",
    "architect_inject",
)

add_entry(
    "playbooks",
    "REJECTION PLAYBOOK (коли НЕ торгувати): "
    "1) Поза killzone (до 07:00 або після 17:00 UTC). "
    "2) П'ятниця після 14:00 UTC. "
    "3) 2+ loss streak за день (G4 hard gate). "
    "4) Немає CHoCH/BOS на M15+ (G3 structure). "
    "5) Zone grade B або C (G6 quality). "
    "6) Невизначений HTF bias (G1). "
    "7) Великі новини (FOMC, NFP, CPI) — за 30хв до та 30хв після. "
    "ПРАВИЛО: краще 0 trades ніж 1 bad trade. Пропуск = теж рішення.",
    "architect_inject",
)

add_entry(
    "playbooks",
    "SWEEP+REVERSAL PLAYBOOK: "
    "1) Ідентифікуй ліквідність (EQH/EQL, session H/L, round numbers). "
    "2) Чекай sweep (price takes liquidity, wick beyond level). "
    "3) Confirmation: M5 CHoCH або BOS у зворотному напрямку від sweep. "
    "4) Entry: перший OB або FVG після CHoCH, в OTE zone (62-79%). "
    "5) SL: за sweep high/low + buffer (5-10 pips для XAU). "
    "6) TP: наступний liquidity pool або key level на протилежній стороні. "
    "7) R:R мінімум 2.0 (hard gate G5).",
    "architect_inject",
)

add_entry(
    "playbooks",
    "VP MANAGEMENT DURING TRADE: "
    "1) Кожні 5-15хв перевіряй: чи ціна все ще в структурі? "
    "2) Якщо ціна пройшла 50% до TP — розглянь trailing SL до entry (BE). "
    "3) Якщо з'явився CHoCH проти позиції на M5 — manual_exit з причиною. "
    "4) Якщо сесія закінчується (>17:00 UTC) — session_end close. "
    "5) Ніколи не розширюй SL. Якщо SL хіт — sl_hit і все. "
    "6) Після closed: ОБОВ'ЯЗКОВО evaluate_trade з process_score 1-7.",
    "architect_inject",
)

# ─── Save ────────────────────────────────────────────────────────────────────
if not DRY:
    kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=2))

print(f"\n{'DRY RUN' if DRY else 'APPLIED'} — Patch 06+07: KB playbooks")
print(f"  Added: {added} entries")
print(
    f"  Total KB topics: {len(kb)}, total entries: {sum(len(v) for v in kb.values())}"
)
