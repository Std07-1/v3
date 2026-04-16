#!/usr/bin/env python3
"""
Case Study #1: XAU/USD Sweep-to-Breakout Knowledge Seeding
~15 entries across 4 topics — deep institutional analysis.

Topics:
  - case_study_001: Specific XAU/USD Apr 2026 case with hard data (NEW, 5 entries)
  - антиципація_сетапів: Framework for anticipating setups (NEW, 4 entries)
  - sweep_reversal: Extending with specific mechanical insights (3 entries → 6/20)
  - управління_стопами: Extending with post-sweep SL rules (2 entries → 5/20)
"""

import json, os, sys
from datetime import datetime, timezone

KNOWLEDGE_FILE = "/opt/smc-trader-v3/data/v3_knowledge.json"
MAX_PER_TOPIC = 20


def load_knowledge():
    if not os.path.exists(KNOWLEDGE_FILE):
        return {}
    with open(KNOWLEDGE_FILE) as f:
        return json.load(f)


def save_knowledge(data):
    with open(KNOWLEDGE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_entry(data, topic, content, source="case_study_1_analysis"):
    if topic not in data:
        data[topic] = []
    entries = data[topic]
    if len(entries) >= MAX_PER_TOPIC:
        print(f"  SKIP: topic '{topic}' full ({len(entries)}/{MAX_PER_TOPIC})")
        return False
    # Check for near-duplicate (first 80 chars)
    prefix = content[:80]
    for e in entries:
        if e.get("text", "")[:80] == prefix:
            print(f"  SKIP: duplicate in '{topic}'")
            return False
    entries.append(
        {
            "text": content,
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
    )
    print(f"  ADDED to '{topic}' ({len(entries)}/{MAX_PER_TOPIC})")
    return True


# ============================================================
# ENTRIES
# ============================================================

ENTRIES = [
    # ── CASE STUDY 001: The complete narrative with data ──
    (
        "case_study_001",
        "CS-001 ОГЛЯД: XAU/USD Sweep-to-Breakout, 02-07 квітня 2026. "
        "D1 uptrend (03-26→03-31) від 4380 до 4792 (+412 pips за 5 днів). "
        "04-01: D1 rejection at 4800 — range 247 pips, body лише 33% = масивна індесижн. "
        "04-02 Asia: агресивний sell-off 8h (-230 pips, від 4784 до 4553). "
        "Sweep зібрав ВСІ session lows за 48h (6 рівнів: від 4529 до 4741). "
        "Post-sweep: displacement M15 body 89%, FVG 4608-4617. "
        "Accumulation 8h (4553-4699). Weekend higher low 4604 (+51 pip vs sweep). "
        "04-07 NY: expansion breakout через D1 high 4800, new high 4836. "
        "Повний рух від sweep low: +283 pips за 5 днів. R:R від entry 4598 з SL 4550 = 1:6.",
    ),
    (
        "case_study_001",
        "CS-001 ФАЗА 1 — ЛИКВІДАЦІЙНИЙ ІНЖИНІРИНГ: "
        "04-01 22:00 — 04-02 06:00 (overnight+Asia, 8 годин). "
        "Sell-off характеристики: 3 momentum bars (body>60%), total drop 230 pips. "
        "Ключова деталь: відбувся під час ASIA SESSION — найнижча ліквідність. "
        "Інституціонал обирає low-liquidity sessions щоб рухати ціну з мінімальними витратами. "
        "04-02 01:00: monster candle range 136.7 пунктів (bear 64% body) — один бар забрав 138 пунктів. "
        "04-02 05:00: другий monster range 95.1 (bear 82% body) — фінальний push до sweep zone. "
        "СИГНАЛ ДО SWEEP: коли бачиш 2+ momentum bear candles підряд в Asia session після touch premium zone на D1 — "
        "це не 'обвал', це інженерія ліквідності. Очікуй sweep session lows і reversal.",
    ),
    (
        "case_study_001",
        "CS-001 ФАЗА 2 — SWEEP ТА DISPLACEMENT: "
        "04-02 06:00: Sweep candle — range 55.4, але body тільки 8%! "
        "Lower wick 40.6 пунктів = 73% всієї свічки = wick. "
        "Ціна впала до 4553, але закрилась на 4598 = buyers поглинули весь sell pressure. "
        "Що було swept: Asia low 4666, London low 4710, NY low 4716, Late NY low 4741 — "
        "ВСІ session lows за 48 годин зібрані одним рухом. "
        "DISPLACEMENT підтвердження: 04-02 07:00 M15 candle body 89% (22.6 з 25.3 range), "
        "від 4598 до 4621. Створив FVG 4608-4617 (8.4 pip gap). "
        "ПРАВИЛО: sweep candle з body <15% + наступна candle body >80% = DISPLACEMENT CONFIRMED. "
        "Institutional entry підтверджена.",
    ),
    (
        "case_study_001",
        "CS-001 ФАЗА 3 — АКУМУЛЯЦІЯ ТА WEEKEND TEST: "
        "04-02 06:00–14:00: 8 годин 'нудного' бокового руху в range 4553-4699 (146 pips). "
        "London session: переважно bear/indecision — ретейл продовжує продавати. "
        "НЕ ВЕДИСЬ: це не слабкість — це інституціонал тихо набирає позицію по cheap prices. "
        "04-02 13:00 NY open: EXPLOSION — H1 range 71 пунктів, bullish. Акумуляція закінчена. "
        "WEEKEND TEST: 04-05/06 gap low = 4604 vs sweep low 4553 = higher low на +51 pip. "
        "Якщо smart money продали б позицію — ціна пробила б 4553 на gap. "
        "Замість цього: demand на вищому рівні = інституціонал тримає long і ДОДАЄ. "
        "H4 ascending lows: 4553 → 4600 → 4615 → 4645 = кожен pullback зупиняється вище.",
    ),
    (
        "case_study_001",
        "CS-001 ФАЗА 4 — EXPANSION ТА BREAKOUT: "
        "04-07 re-accumulation: ще один день range 4607-4706. "
        "19:00 Late NY: H1 bull range 56.7, body 68% — перший momentum push. "
        "22:00: THE CANDLE — H1 range 113.3, body 99%!! Від 4706 до 4819. "
        "Body 99% означає: відкрився на low, закрився на high — ZERO pullback. "
        "Це textbook institutional expansion candle — all stops above D1 high (4800) triggered. "
        "23:00: continuation до 4836 = new all-time local high. "
        "ВІД SWEEP ДО BREAKOUT: 5 днів, від low 4553 до high 4836 = +283 pips. "
        "ENTRY MATH: entry 4598, SL 4550 (48 pip risk), TP D1 high 4800 = R:R 1:4.2. "
        "З trailing по H4 structure: captured 4836 = R:R 1:5.9.",
    ),
    # ── АНТИЦИПАЦІЯ СЕТАПІВ: Framework ──
    (
        "антиципація_сетапів",
        "FRAMEWORK: 5 передвісників Sweep-to-Expansion (чеклист антиципації). "
        "1) D1 КОНТЕКСТ: ціна в uptrend торкнулась premium zone (>70% D1 range) і отримала rejection — "
        "D1 candle з range >200 pips і body <40% = індесижн на хаях = потенційний sweep setup. "
        "2) SESSION LOW CLUSTER: подивись session lows за 48h — якщо є 3+ рівні в зоні 100-200 pips нижче поточної ціни, "
        "це 'магніт ліквідності' для свіп-руху. "
        "3) TIMING: sell-off починається в overnight/Asia (low liquidity). "
        "Якщо бачиш aggressive selling в Asia після D1 rejection — не панікуй, СПОСТЕРІГАЙ. "
        "4) SWEEP CANDLE: body <15% від range + lower wick >60% від range = sweep confirmed. "
        "5) DISPLACEMENT: наступна M15 candle body >75% = institutional entry confirmed.",
    ),
    (
        "антиципація_сетапів",
        "ENTRY TIMING: коли входити після sweep? НЕ на sweep candle, а на DISPLACEMENT. "
        "В CS-001: sweep candle 06:00 закрилась на 4598, displacement 07:00 закрилась на 4621. "
        "Різниця входу: 4598 vs 4621 = 23 pips, але displacement дає CONFIRMATION. "
        "Aggressive entry: на close displacement candle (4621 в CS-001). "
        "Conservative entry: на retest FVG зони (4608-4617 в CS-001). "
        "SL: нижче sweep low (4553 в CS-001 = 45-68 pips risk залежно від entry). "
        "НІКОЛИ не ставити SL 'tight' одразу після sweep — ціна може retrace в accumulation phase. "
        "Перші 4-8 годин після sweep = АКУМУЛЯЦІЯ, не MARKUP. Терпіння.",
    ),
    (
        "антиципація_сетапів",
        "TARGET FRAMEWORK: куди ціна йде після sweep-reversal? "
        "TP1 (найближчий): D1 EQ (50% range) — в CS-001 це 4641, hit в той же день. "
        "TP2 (основний): D1 range high — в CS-001 це 4800, hit на 5-й день. "
        "TP3 (розширений): вище D1 high якщо momentum підтверджує expansion. "
        "SCALING: 30% на TP1, 40% на TP2, 30% trail з H4 structure lows. "
        "Trailing SL: після досягнення TP1, рухай SL під останній H4 swing low. "
        "В CS-001: після TP1 (4641), SL під H4 low 4600. "
        "Після weekend hold, SL під 4615. Після markup push, SL під 4645. "
        "Кожен higher low на H4 = новий рівень trailing SL.",
    ),
    (
        "антиципація_сетапів",
        "PATIENCE FRAMEWORK: чому sweep-to-expansion займає 3-7 днів, а не 3 години. "
        "Фази руху і їхня тривалість (з CS-001 data): "
        "1. Liquidity Engineering: 6-12 годин (sell-off до sweep). "
        "2. Sweep + Displacement: 1-2 години. "
        "3. Accumulation: 6-24 години (boring sideways). "
        "4. Initial markup: 4-8 годин (NY session push). "
        "5. Consolidation/re-accumulation: 1-3 дні. "
        "6. Expansion: 4-12 годин (breakout + momentum). "
        "TOTAL: 3-5 днів від sweep до expansion breakout D1 high. "
        "Ретейл-помилка #1: входять на sweep, виходять на accumulation. "
        "Ретейл-помилка #2: ставлять BE після першого push, закриваються в re-accumulation pullback. "
        "Інституціонал тримає 3-5 днів — і ти маєш тримати 3-5 днів.",
    ),
    # ── SWEEP REVERSAL: extending (3/20 → 6/20) ──
    (
        "sweep_reversal",
        "ASIA SESSION SWEEP MECHANISM: sweep session lows часто відбувається в Asia session (00:00-08:00 UTC). "
        "Причина: Asia має найнижчу ліквідність для XAU/USD (gold торгується переважно в London/NY). "
        "Інституціонал використовує low liquidity щоб рухати ціну з меншими витратами. "
        "В CS-001: sell-off почався 04-02 01:00 Asia, sweep low 06:00 Asia. "
        "Reversal і displacement відбулись до London open (08:00). "
        "СИГНАЛ: aggressive Asia sell-off після D1 premium rejection = очікуй sweep. "
        "Якщо бачиш 2+ bear momentum candles в Asia що зносять попередні session lows — "
        "НЕ ПРОДАВАЙ. Готуйся до long entry на displacement confirmation.",
    ),
    (
        "sweep_reversal",
        "MULTI-LEVEL SWEEP IDENTIFICATION: як визначити 'глибину' sweep заздалегідь. "
        "Крок 1: знайди ВСІ session lows за 48h (Asia/London/NY lows кожного дня). "
        "Крок 2: визнач кластер — якщо 3+ lows в зоні 50 pips, це strong liquidity pool. "
        "Крок 3: визнач найнижчий session low — sweep зазвичай заходить на 10-30 pips нижче. "
        "В CS-001 кластер: London low 4529, NY lows 4567-4716. "
        "Sweep пішов до 4553 — нижче London low на 24 пункти. "
        "ПРАВИЛО: sweep = найнижчий session low мінус 10-30 pips. "
        "Це дає тобі ПРИБЛИЗНУ зону Entry ЗАЗДАЛЕГІДЬ.",
    ),
    (
        "sweep_reversal",
        "WEEKEND GAP ЯК ПІДТВЕРДЖЕННЯ SWEEP: якщо після sweep відбувається weekend, "
        "gap open дає КРИТИЧНЕ підтвердження. "
        "HIGHER LOW після weekend = інституціонал ТРИМАЄ позицію і ready для markup. "
        "В CS-001: sweep low 4553 (середа), weekend gap low 4604 (неділя) = HL на +51 pip. "
        "LOWER LOW після weekend = sweep FAILED, smart money не набрали, sell pressure continues. "
        "ПРАВИЛО: якщо weekend low > sweep low + 30 pips — bullish confirmation, hold long. "
        "Якщо weekend low < sweep low — close long, wait for new setup. "
        "Це одна з найнадійніших confirmations: ніхто не тримає losing position через weekend.",
    ),
    # ── УПРАВЛІННЯ СТОПАМИ: extending (2/20 → 5/20) ──
    (
        "управління_стопами",
        "POST-SWEEP SL MANAGEMENT: НІКОЛИ не ставити BE в перші 24 години після sweep. "
        "Чому? Accumulation phase (6-24h) створює pullbacks що можуть зачепити BE. "
        "В CS-001: після entry 4598, ціна падала до 4580 (London session pullback). "
        "BE на 4598 = вибило б позицію. Але структурний SL під 4553 — тримає. "
        "ПРАВИЛО: протягом accumulation phase (перші 24h) — SL залишається під sweep low. "
        "Рухай SL тільки після: 1) NY markup push підтвердив бичачий momentum, "
        "2) або H4 сформував новий higher swing low вище sweep. "
        "В CS-001 перший safe SL move: під 4580 (H4 low після NY markup 04-02 14:00).",
    ),
    (
        "управління_стопами",
        "H4 TRAILING SL SYSTEM для Swing trades (3-7 днів): "
        "Після досягнення TP1 (D1 EQ) — переключись на H4 trailing: "
        "SL = під останній H4 confirmed swing low. "
        "В CS-001 послідовність trailing SL: "
        "Day 1 (post-sweep): SL під 4553 (sweep low). "
        "Day 1 (після NY push): SL під 4580 (H4 higher low). "
        "Day 3 (після weekend hold): SL під 4600 (weekend gap low). "
        "Day 4: SL під 4615 (next H4 swing low). "
        "Day 5: SL під 4645 (re-accumulation H4 low). "
        "КОЖЕН H4 higher low = SL рухається вгору. Ніколи не рухай SL внизну. "
        "Якщо H4 зробив lower low — це STOP SIGNAL, не trailing.",
    ),
    (
        "управління_стопами",
        "ACCUMULATION PHASE PATIENCE: як пережити 'нудні' години/дні. "
        "Accumulation виглядає як: бокові рухи, bear candles в London, дожі, indecision. "
        "Ретейл-реакція: 'не росте = помилковий сетап, закрию'. "
        "Institutional reality: smart money НАБИРАЮТЬ позицію. Їм потрібен час і ліквідність. "
        "ОЗНАКИ ЗДОРОВОЇ АКУМУЛЯЦІЇ: "
        "1) H4 swing lows НЕ ламають sweep low (в CS-001: 4580, 4592 — обидва вище 4553). "
        "2) Кожен pullback менший за попередній (contracting range). "
        "3) NY sessions показують bids (bullish closes в NY). "
        "В CS-001: London 04-02 = bear/indecision, NY 04-02 = 2 massive bull candles. "
        "ПРАВИЛО: якщо London слабкий але NY bullish після sweep — це accumulation. Тримай.",
    ),
]

# ============================================================
# SEED
# ============================================================


def main():
    data = load_knowledge()
    added = 0
    skipped = 0

    for topic, content in ENTRIES:
        ok = add_entry(data, topic, content)
        if ok:
            added += 1
        else:
            skipped += 1

    save_knowledge(data)

    print(f"\n{'='*50}")
    print(f"RESULTS: {added} added, {skipped} skipped")
    print(f"\nTopic summary:")
    for topic, entries in sorted(data["topics"].items()):
        print(f"  {topic}: {len(entries)}/{MAX_PER_TOPIC}")
    print(f"\nTotal knowledge entries: {sum(len(e) for e in data['topics'].values())}")


if __name__ == "__main__":
    main()
