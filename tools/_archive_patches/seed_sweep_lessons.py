#!/usr/bin/env python3
"""Seed lessons learned from XAU/USD 2026-04-05/07 sweep-to-reversal.

Case study: price swept Prev Lon Lo + Prev NY Lo (~4604) → rallied +231 pips to 4836.
Bot recommended tightening stop to BE → got stopped out before the rocket.

Knowledge entries for topics:
- управління_стопами (stop management after sweep)
- sweep_reversal (sweep pattern recognition)
- помилки_та_уроки (lessons learned from missed moves)
"""

import json
import os
import sys
from datetime import datetime

KB_PATH = "/opt/smc-trader-v3/data/v3_knowledge.json"
MAX_PER_TOPIC = 20


def load_kb():
    if os.path.exists(KB_PATH):
        with open(KB_PATH) as f:
            return json.load(f)
    return {}


def save_kb(kb):
    with open(KB_PATH, "w") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)


ENTRIES = [
    # --- Topic: sweep_reversal ---
    {
        "topic": "sweep_reversal",
        "entry": {
            "id": "sweep_session_lows_reversal",
            "title": "Sweep Session Lows = High-Probability Reversal",
            "content": (
                "Коли ціна забирає ліквідність під Prev London Low + Prev NY Low одночасно — "
                "це класичний institutional sweep. Smart Money збирають стопи ритейлу щоб набрати позицію. "
                "Приклад: XAU/USD 04-05 22:00-23:00 — sweep 4604, потім +231 pips до 4836. "
                "Ознаки справжнього sweep: (1) Довгий lower wick на H1, (2) Закриття вище рівня, "
                "(3) BOS BULL на M15 після sweep, (4) Об'єм на відбій."
            ),
            "source": "seed",
            "created": datetime.utcnow().isoformat(),
            "tags": ["sweep", "reversal", "session_levels", "liquidity"],
        },
    },
    {
        "topic": "sweep_reversal",
        "entry": {
            "id": "sweep_confluence_multiple_levels",
            "title": "Sweep кількох рівнів = максимальна ймовірність reversal",
            "content": (
                "Якщо ціна sweep'нула 2+ session levels одночасно (Prev Lon Lo + Prev NY Lo, "
                "або Prev Asia Lo + Prev Lon Lo) — ймовірність reversal різко зростає. "
                "Це означає що Smart Money зібрали ліквідність з КІЛЬКОХ сесій. "
                "При такому confluence: тримай позицію ширше, не підтягуй до BE раніше ніж M15 CHoCH у зворотню сторону."
            ),
            "source": "seed",
            "created": datetime.utcnow().isoformat(),
            "tags": ["sweep", "confluence", "session_levels", "multi_level"],
        },
    },
    {
        "topic": "sweep_reversal",
        "entry": {
            "id": "sweep_d1_context",
            "title": "Sweep на D1 support area = macro reversal (не micro scalp)",
            "content": (
                "Коли sweep відбувається на рівні D1 range low або weekly support — це не micro reversal на 20 pips. "
                "Це потенційний macro move на 100-300 pips. Приклад: D1 04-01 range 4553-4800, sweep 04-05 @ 4604 "
                "(нижня зона D1 range) → rally +231 pips. "
                "Правило: якщо sweep на D1 level + session level confluence → держи target на протилежний edge D1 range."
            ),
            "source": "seed",
            "created": datetime.utcnow().isoformat(),
            "tags": ["sweep", "D1", "macro", "range"],
        },
    },
    # --- Topic: управління_стопами ---
    {
        "topic": "управління_стопами",
        "entry": {
            "id": "stop_after_sweep_patience",
            "title": "Після sweep session levels — НЕ підтягуй стоп до BE",
            "content": (
                "КРИТИЧНИЙ УРОК (04-05/07 XAU/USD): після sweep Prev Lon Lo + Prev NY Lo бот порадив "
                "підтягнути стоп під BE. Результат: вибило на BE, а ціна поїхала +231 pips без нас. "
                "ПРАВИЛО: Після підтвердженого sweep (M15 BOS BULL / wick rejection) — тримай structural SL "
                "(під sweep low) мінімум до: (1) H1 закриття вище попереднього resistance, (2) Перший pullback + continuation. "
                "BE тільки після того як move вже дав +1.5R."
            ),
            "source": "seed",
            "created": datetime.utcnow().isoformat(),
            "tags": ["stop_loss", "sweep", "patience", "breakeven"],
        },
    },
    {
        "topic": "управління_стопами",
        "entry": {
            "id": "stop_structural_vs_tight",
            "title": "Structural SL vs Tight SL — коли що використовувати",
            "content": (
                "TIGHT SL (під останній swing low): Для entry в established trend, коли є clear structure. "
                "STRUCTURAL SL (під sweep zone / OB low): Для reversal entry після sweep. "
                "Проблема tight SL після sweep: ціна часто retestує sweep zone перед імпульсом. "
                "Tight SL вибиває САМЕ перед ракетою. "
                "ПРАВИЛО: Sweep entry → structural SL → move to BE тільки після +1.5R або H1 BOS continuation."
            ),
            "source": "seed",
            "created": datetime.utcnow().isoformat(),
            "tags": ["stop_loss", "structural", "tight", "sweep"],
        },
    },
    # --- Topic: помилки_та_уроки ---
    {
        "topic": "помилки_та_уроки",
        "entry": {
            "id": "lesson_20260405_sweep_missed",
            "title": "04-05/07 XAU/USD: Sweep 4604 → +231 pips — missed через ранній BE",
            "content": (
                "ФАКТИ: Sweep Prev Lon Lo + Prev NY Lo @ 4604 (04-05 23:00). "
                "Вхід був правильний (long після sweep). Бот порадив підтягнути стоп під BE. "
                "Вибило на BE. Ціна пішла до 4836 (+231 pips). "
                "ПРИЧИНА: Бот мислить reactive (що зараз), не anticipatory (що далі). "
                "Не врахував що sweep 2+ session levels на D1 support = macro event. "
                "ВИСНОВКИ: (1) Після sweep — structural SL, не BE, (2) D1 context визначає target magnitude, "
                "(3) Потрібен strategic layer в боті що бачить 'де ми в контексті тижня'."
            ),
            "source": "seed",
            "created": datetime.utcnow().isoformat(),
            "tags": ["lesson", "missed_trade", "sweep", "stop_management", "2026-04"],
        },
    },
    {
        "topic": "помилки_та_уроки",
        "entry": {
            "id": "lesson_reactive_vs_anticipatory",
            "title": "Reactive vs Anticipatory — чому бот пропускає великі мувы",
            "content": (
                "Reactive бот: аналізує 'що відбувається зараз'. Бачить pullback → рекомендує тighten stop. "
                "Anticipatory трейдер: бачить 'ми на D1 support після sweep 3 session levels → "
                "це accumulation, pullback НОРМАЛЬНИЙ, тримай позицію'. "
                "ПРАВИЛО для бота: Перед рекомендацією tighten/close — перевір: "
                "(1) Чи був sweep key levels? (2) Чи є D1/H4 macro context для continuation? "
                "(3) Чи був structural break (M15 CHoCH) проти позиції — ТІЛЬКИ тоді tighten."
            ),
            "source": "seed",
            "created": datetime.utcnow().isoformat(),
            "tags": ["lesson", "reactive", "anticipatory", "strategy"],
        },
    },
]


def main():
    kb = load_kb()
    added = 0
    for item in ENTRIES:
        topic = item["topic"]
        entry = item["entry"]
        if topic not in kb:
            kb[topic] = []
        # Check duplicate
        existing_ids = {e.get("id") for e in kb[topic]}
        if entry["id"] in existing_ids:
            print(f"  SKIP (exists): {entry['id']}")
            continue
        if len(kb[topic]) >= MAX_PER_TOPIC:
            print(f"  SKIP (topic full): {topic} has {len(kb[topic])}/{MAX_PER_TOPIC}")
            continue
        kb[topic].append(entry)
        added += 1
        print(f"  ADDED: [{topic}] {entry['id']}")

    save_kb(kb)
    # Summary
    print(f"\n{'=' * 40}")
    print(f"Added {added} entries")
    for t in sorted(set(e["topic"] for e in ENTRIES)):
        print(f"  {t}: {len(kb.get(t, []))}/{MAX_PER_TOPIC}")


if __name__ == "__main__":
    main()
