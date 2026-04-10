#!/usr/bin/env python3
"""
seed_structure_v2_knowledge.py — ADR-0047: оновити знання бота про BOS/CHoCH V2.

Додає нові записи в категорію `структура_ринку` файлу v3_knowledge.json.
Запускати на VPS: python3 /opt/smc-trader-v3/seed_structure_v2_knowledge.py
"""

import json
import time
import os

KB_PATH = "/opt/smc-trader-v3/data/v3_knowledge.json"
TOPIC = "структура_ринку"

NEW_ENTRIES = [
    # V2 canonical definitions
    {
        "text": (
            "BOS (Break of Structure) V2 — CONTINUATION тренду. "
            "В uptrend: бар закривається ВИЩЕ останнього HH (swing high). "
            "В downtrend: бар закривається НИЖЧЕ останнього LL (swing low). "
            "BOS підтверджує що тренд діє — це НОРМАЛЬНА ринкова поведінка. "
            "BOS зустрічається частіше ніж CHoCH (40-60% vs 20-40% від усіх structure events)."
        ),
        "source": "seed",
    },
    {
        "text": (
            "CHoCH (Change of Character) V2 — РОЗВОРОТ тренду через злам ВНУТРІШНЬОЇ структури. "
            "В uptrend: бар закривається НИЖЧЕ останнього HL (higher low) → CHoCH_BEAR. "
            "В downtrend: бар закривається ВИЩЕ останнього LH (lower high) → CHoCH_BULL. "
            "CHoCH = РІДКІСНА подія (20-40%). Якщо бачиш CHoCH — це серйозний сигнал розвороту."
        ),
        "source": "seed",
    },
    # Trading implications
    {
        "text": (
            "BOS як торговий сигнал: серія BOS_BULL = сильний uptrend, шукай dip-buy (OB/FVG нижче). "
            "Серія BOS_BEAR = сильний downtrend, шукай sell-rally. "
            "Кількість послідовних BOS показує силу тренду. 3+ BOS поспіль = strong trending."
        ),
        "source": "seed",
    },
    {
        "text": (
            "CHoCH як торговий сигнал: CHoCH_BEAR в uptrend = потенційний розворот вниз. "
            "Але один CHoCH ≠ гарантований розворот! Шукай підтвердження: "
            "1) CHoCH + FVG поруч = entry zone, 2) CHoCH + sweep = A+ setup, "
            "3) CHoCH на HTF (H4/D1) > CHoCH на M15."
        ),
        "source": "seed",
    },
    # Key difference from old understanding
    {
        "text": (
            "ВАЖЛИВО V2: CHoCH тепер ламає ВНУТРІШНЮ структуру (HL в uptrend, LH в downtrend), "
            "а НЕ протилежний extreme (HH/LL). Це означає що CHoCH виникає БЛИЖЧЕ до поточної ціни "
            "і раніше сигналізує про зміну характеру ринку. Ціна break = рівень HL або LH свінгу."
        ),
        "source": "seed",
    },
    # BOS+CHoCH sequence patterns
    {
        "text": (
            "Типові послідовності V2: "
            "BOS→BOS→BOS = healthy trend, trade with trend. "
            "BOS→CHoCH = trend reversal attempt, wait for confirmation. "
            "BOS→CHoCH→BOS = confirmed reversal, new trend established. "
            "CHoCH alone (без наступного BOS) = можливий false reversal, обережно."
        ),
        "source": "seed",
    },
    # Practical rule
    {
        "text": (
            "Правило BOS/CHoCH для entry: НІКОЛИ не входь проти серії BOS. "
            "Якщо H1 показує 3× BOS_BULL — не шукай short на M15. "
            "Якщо H1 показує CHoCH_BEAR — це дозвіл шукати short setup на M15, "
            "але тільки після підтвердження (OB/FVG + sweep + session alignment)."
        ),
        "source": "seed",
    },
]


def main():
    if not os.path.exists(KB_PATH):
        print(f"ERROR: {KB_PATH} not found")
        return

    with open(KB_PATH, "r", encoding="utf-8") as f:
        kb = json.load(f)

    if TOPIC not in kb:
        kb[TOPIC] = []

    ts = int(time.time())
    added = 0
    for entry in NEW_ENTRIES:
        # Skip if similar text already exists
        existing_texts = [e.get("text", "")[:60] for e in kb[TOPIC]]
        if entry["text"][:60] in existing_texts:
            print(f"SKIP (duplicate): {entry['text'][:60]}...")
            continue

        kb[TOPIC].append(
            {
                "text": entry["text"],
                "ts": ts,
                "source": entry["source"],
            }
        )
        added += 1
        print(f"ADDED: {entry['text'][:60]}...")

    # Cap at 20 entries (keep last 20)
    if len(kb[TOPIC]) > 20:
        kb[TOPIC] = kb[TOPIC][-20:]
        print(f"TRIMMED to 20 entries")

    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)

    print(f"\nDone: added {added} entries to '{TOPIC}'. Total: {len(kb[TOPIC])}")


if __name__ == "__main__":
    main()
