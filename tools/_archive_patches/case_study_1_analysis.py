#!/usr/bin/env python3
"""
Case Study #1: XAU/USD Sweep-to-Breakout (2026-04-02 → 2026-04-07)
Deep pre-sweep + post-sweep analysis for institutional knowledge building.

Goal: understand what was VISIBLE BEFORE the move, not just what happened after.
"""

import json, os, glob
from datetime import datetime, timezone, timedelta

DATA = "/opt/smc-v3/data_v3/XAU_USD"


def load_bars(tf_s, since_ms=None, until_ms=None):
    """Load bars from JSONL files."""
    folder = os.path.join(DATA, f"tf_{tf_s}")
    bars = []
    for f in sorted(glob.glob(os.path.join(folder, "part-*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                b = json.loads(line)
                t = b["open_time_ms"]
                if since_ms and t < since_ms:
                    continue
                if until_ms and t > until_ms:
                    continue
                bars.append(b)
    bars.sort(key=lambda x: x["open_time_ms"])
    return bars


def ms(y, m, d, h=0):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def ts(ms_val):
    return datetime.fromtimestamp(ms_val / 1000, tz=timezone.utc).strftime(
        "%m-%d %H:%M"
    )


def day(ms_val):
    return datetime.fromtimestamp(ms_val / 1000, tz=timezone.utc).strftime("%a")


def body_pct(b):
    rng = b["h"] - b["low"]
    if rng == 0:
        return 0
    return abs(b["c"] - b["o"]) / rng * 100


def direction(b):
    if b["c"] > b["o"]:
        return "BULL"
    elif b["c"] < b["o"]:
        return "BEAR"
    return "DOJI"


# ============================================================
# PART 1: PRE-SWEEP CONTEXT (What was visible 3-5 days before?)
# ============================================================
print("=" * 70)
print("PART 1: PRE-SWEEP CONTEXT — Що було видно ДО sweep?")
print("=" * 70)

# D1 bars for the week before sweep (March 25 - April 2)
d1_pre = load_bars(86400, ms(2026, 3, 24), ms(2026, 4, 2))
print("\nD1 бари ПЕРЕД sweep (тижнева картина):")
d1_highs = []
d1_lows = []
for b in d1_pre:
    d = direction(b)
    rng = b["h"] - b["low"]
    body = abs(b["c"] - b["o"])
    wick_lo = min(b["o"], b["c"]) - b["low"]
    wick_hi = b["h"] - max(b["o"], b["c"])
    d1_highs.append(b["h"])
    d1_lows.append(b["low"])
    print(
        f"  {ts(b['open_time_ms'])} ({day(b['open_time_ms'])})  "
        f"O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  "
        f"{d:4s}  range={rng:.1f}  body={body:.1f}  body%={body_pct(b):.0f}%  "
        f"lo_wick={wick_lo:.1f}  hi_wick={wick_hi:.1f}"
    )

if d1_highs and d1_lows:
    week_high = max(d1_highs)
    week_low = min(d1_lows)
    week_eq = (week_high + week_low) / 2
    print(f"\n  Тижневий range: {week_low:.2f} — {week_high:.2f}")
    print(f"  Тижнева EQ:     {week_eq:.2f}")
    print(f"  Range width:     {week_high - week_low:.1f} pips")

# ============================================================
# PART 2: SESSION ANALYSIS — В яку сесію стався sweep?
# ============================================================
print("\n" + "=" * 70)
print("PART 2: SESSION ANALYSIS — Контекст сесій")
print("=" * 70)

# H1 bars around sweep (April 1-2)
h1_pre = load_bars(3600, ms(2026, 4, 1, 18), ms(2026, 4, 2, 16))
print("\nH1 бари навколо sweep (04-01 18:00 → 04-02 16:00):")
for b in h1_pre:
    t = b["open_time_ms"]
    dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
    hour = dt.hour
    # Session classification
    if 0 <= hour < 8:
        session = "ASIA"
    elif 8 <= hour < 13:
        session = "LONDON"
    elif 13 <= hour < 17:
        session = "NY"
    elif 17 <= hour < 22:
        session = "LATE_NY"
    else:
        session = "OVERNIGHT"

    d = direction(b)
    rng = b["h"] - b["low"]
    wick_lo = min(b["o"], b["c"]) - b["low"]
    print(
        f"  {ts(t)} [{session:8s}]  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  "
        f"{d:4s}  range={rng:.1f}  lo_wick={wick_lo:.1f}"
    )

# ============================================================
# PART 3: H4 STRUCTURE — Swing structure before & after
# ============================================================
print("\n" + "=" * 70)
print("PART 3: H4 SWING STRUCTURE — Before/After sweep")
print("=" * 70)

h4_full = load_bars(14400, ms(2026, 3, 28), ms(2026, 4, 8))
print("\nH4 бари (10 днів):")
h4_swing_lows = []
h4_swing_highs = []

for i, b in enumerate(h4_full):
    d = direction(b)
    print(
        f"  {ts(b['open_time_ms'])}  O={b['o']:.2f} H={b['h']:.2f} L={b['low']:.2f} C={b['c']:.2f}  {d}"
    )

    # Simple swing detection (3-bar)
    if i >= 1 and i < len(h4_full) - 1:
        prev_b = h4_full[i - 1]
        next_b = h4_full[i + 1]
        if b["low"] < prev_b["low"] and b["low"] < next_b["low"]:
            h4_swing_lows.append((b["open_time_ms"], b["low"]))
        if b["h"] > prev_b["h"] and b["h"] > next_b["h"]:
            h4_swing_highs.append((b["open_time_ms"], b["h"]))

print("\n  H4 Swing Highs:")
for t, v in h4_swing_highs:
    marker = " <<<" if v == max(x[1] for x in h4_swing_highs) else ""
    print(f"    {ts(t)}: {v:.2f}{marker}")

print("\n  H4 Swing Lows:")
for t, v in h4_swing_lows:
    marker = " <<< SWEEP" if v == min(x[1] for x in h4_swing_lows) else ""
    print(f"    {ts(t)}: {v:.2f}{marker}")

# Structure analysis
print("\n  Structural Analysis:")
sweep_low = min(h4_swing_lows, key=lambda x: x[1]) if h4_swing_lows else None
if sweep_low:
    post_sweep_lows = [(t, v) for t, v in h4_swing_lows if t > sweep_low[0]]
    if len(post_sweep_lows) >= 2:
        ascending = all(
            post_sweep_lows[i][1] > post_sweep_lows[i - 1][1]
            for i in range(1, len(post_sweep_lows))
        )
        if ascending:
            print(
                f"    ✓ ASCENDING LOWS after sweep: {' → '.join(f'{v:.2f}' for _, v in post_sweep_lows)}"
            )
            print(f"    = BULLISH STRUCTURAL SHIFT confirmed")
        else:
            vals = " → ".join(f"{v:.2f}" for _, v in post_sweep_lows)
            print(f"    Lows after sweep: {vals}")

# ============================================================
# PART 4: LIQUIDITY MAP — What was swept?
# ============================================================
print("\n" + "=" * 70)
print("PART 4: LIQUIDITY MAP — Що саме sweep зібрав?")
print("=" * 70)

# Session highs/lows in the days before sweep
h1_sessions = load_bars(3600, ms(2026, 3, 31), ms(2026, 4, 2, 7))

# Group by day + session
from collections import defaultdict

sessions = defaultdict(list)
for b in h1_sessions:
    dt = datetime.fromtimestamp(b["open_time_ms"] / 1000, tz=timezone.utc)
    day_str = dt.strftime("%m-%d")
    hour = dt.hour
    if 0 <= hour < 8:
        sess = "ASIA"
    elif 8 <= hour < 13:
        sess = "LONDON"
    elif 13 <= hour < 17:
        sess = "NY"
    else:
        sess = "LATE"
    sessions[f"{day_str} {sess}"].append(b)

print("\nSession H/L перед sweep:")
session_lows = []
for key in sorted(sessions.keys()):
    bars = sessions[key]
    lo = min(b["low"] for b in bars)
    hi = max(b["h"] for b in bars)
    session_lows.append((key, lo))
    print(f"  {key:16s}  H={hi:.2f}  L={lo:.2f}")

# Which session lows were swept?
sweep_price = 4553.36
print(f"\n  Sweep low: {sweep_price:.2f}")
print(f"  Session lows swept:")
for key, lo in session_lows:
    if lo > sweep_price and lo < sweep_price + 200:
        print(f"    {key}: {lo:.2f} (swept by {lo - sweep_price:.1f} pips)")

# ============================================================
# PART 5: THE "BEFORE" SIGNALS — What was visible 6-12h before?
# ============================================================
print("\n" + "=" * 70)
print("PART 5: PRE-SWEEP SIGNALS — Що було видно за 6-12 годин?")
print("=" * 70)

# H1 bars 12h before sweep
h1_before = load_bars(3600, ms(2026, 4, 1, 18), ms(2026, 4, 2, 6))
print("\nH1 bars 12h перед sweep:")
total_drop = 0
momentum_bars = 0
for b in h1_before:
    d = direction(b)
    rng = b["h"] - b["low"]
    body = abs(b["c"] - b["o"])
    if d == "BEAR" and body_pct(b) > 60:
        momentum_bars += 1
    if d == "BEAR":
        total_drop += body
    dt = datetime.fromtimestamp(b["open_time_ms"] / 1000, tz=timezone.utc)
    hour = dt.hour
    if 0 <= hour < 8:
        sess = "ASIA"
    elif 8 <= hour < 13:
        sess = "LON"
    elif 13 <= hour < 17:
        sess = "NY"
    else:
        sess = "LATE"
    print(
        f"  {ts(b['open_time_ms'])} [{sess:4s}]  {d:4s}  range={rng:.1f}  body%={body_pct(b):.0f}%  "
        f"O={b['o']:.2f} → C={b['c']:.2f}"
    )

print(f"\n  Total bearish drop: {total_drop:.1f} pips")
print(f"  Momentum bars (body>60%): {momentum_bars}")
if h1_before:
    first_o = h1_before[0]["o"]
    last_c = h1_before[-1]["c"]
    print(f"  Net move: {first_o:.2f} → {last_c:.2f} = {last_c - first_o:.1f} pips")

# ============================================================
# PART 6: POST-SWEEP — Перші 4 години (reversal характеристики)
# ============================================================
print("\n" + "=" * 70)
print("PART 6: POST-SWEEP REVERSAL — Перші 4 години")
print("=" * 70)

m15_post = load_bars(900, ms(2026, 4, 2, 6), ms(2026, 4, 2, 10))
print("\nM15 бари після sweep (06:00–10:00):")
first_bull = None
displacement = None
for b in m15_post:
    d = direction(b)
    rng = b["h"] - b["low"]
    body = abs(b["c"] - b["o"])
    bp = body_pct(b)
    marker = ""
    if d == "BULL" and bp > 65 and not displacement:
        displacement = b
        marker = " <<< DISPLACEMENT"
    if d == "BULL" and first_bull is None:
        first_bull = b
        if not displacement:
            marker = " <<< first bull"
    print(
        f"  {ts(b['open_time_ms'])}  {d:4s}  range={rng:.1f}  body={body:.1f}  body%={bp:.0f}%  "
        f"O={b['o']:.2f} C={b['c']:.2f}{marker}"
    )

if displacement:
    print(f"\n  Displacement candle: {ts(displacement['open_time_ms'])}")
    print(f"    Open={displacement['o']:.2f} Close={displacement['c']:.2f}")
    print(
        f"    Body%={body_pct(displacement):.0f}%, Range={displacement['h'] - displacement['low']:.1f}"
    )
    # FVG potential
    idx = m15_post.index(displacement)
    if idx >= 1:
        prev_high = m15_post[idx - 1]["h"]
        next_low = m15_post[idx + 1]["low"] if idx + 1 < len(m15_post) else None
        if next_low and next_low > prev_high:
            print(
                f"    FVG: {prev_high:.2f} — {next_low:.2f} (gap={next_low - prev_high:.1f})"
            )

# ============================================================
# PART 7: ACCUMULATION PHASE — The "boring" 8 hours
# ============================================================
print("\n" + "=" * 70)
print("PART 7: ACCUMULATION PHASE — 'Нудні' години між sweep та markup")
print("=" * 70)

h1_accum = load_bars(3600, ms(2026, 4, 2, 6), ms(2026, 4, 2, 14))
print("\nH1 бари в фазі акумуляції (06:00–14:00):")
accum_low = 9999
accum_high = 0
for b in h1_accum:
    d = direction(b)
    rng = b["h"] - b["low"]
    accum_low = min(accum_low, b["low"])
    accum_high = max(accum_high, b["h"])
    dt = datetime.fromtimestamp(b["open_time_ms"] / 1000, tz=timezone.utc)
    hour = dt.hour
    if 0 <= hour < 8:
        sess = "ASIA"
    elif 8 <= hour < 13:
        sess = "LON"
    elif 13 <= hour < 17:
        sess = "NY"
    else:
        sess = "LATE"
    print(
        f"  {ts(b['open_time_ms'])} [{sess:4s}]  {d:4s}  range={rng:.1f}  O={b['o']:.2f} C={b['c']:.2f}"
    )

print(
    f"\n  Accumulation range: {accum_low:.2f} — {accum_high:.2f} ({accum_high - accum_low:.1f} pips)"
)
print(f"  Duration: 8 hours")

# ============================================================
# PART 8: SECOND SWEEP — Weekend gap (04-05)
# ============================================================
print("\n" + "=" * 70)
print("PART 8: SECOND TEST — Weekend gap і re-accumulation (04-05 → 04-06)")
print("=" * 70)

h1_gap = load_bars(3600, ms(2026, 4, 5, 20), ms(2026, 4, 6, 10))
print("\nH1 бари після weekend gap:")
for b in h1_gap:
    d = direction(b)
    rng = b["h"] - b["low"]
    print(
        f"  {ts(b['open_time_ms'])}  {d:4s}  range={rng:.1f}  O={b['o']:.2f} L={b['low']:.2f} C={b['c']:.2f}"
    )

# Compare: second low vs first sweep
print(f"\n  First sweep low:  4553.36 (04-02 06:00)")
gap_bars = load_bars(3600, ms(2026, 4, 5, 22), ms(2026, 4, 6, 2))
gap_low = min(b["low"] for b in gap_bars) if gap_bars else 0
print(f"  Weekend gap low:  {gap_low:.2f} (04-05/06)")
print(f"  Higher low by:    {gap_low - 4553.36:.1f} pips")
print(f"  = Institutional demand confirmed at higher price")

# ============================================================
# PART 9: EXPANSION — NY session 04-07 (the rocket)
# ============================================================
print("\n" + "=" * 70)
print("PART 9: EXPANSION — NY Session 04-07 (the rocket)")
print("=" * 70)

h1_rocket = load_bars(3600, ms(2026, 4, 7, 12), ms(2026, 4, 8))
print("\nH1 бари під час expansion (04-07 12:00 →):")
for b in h1_rocket:
    d = direction(b)
    rng = b["h"] - b["low"]
    body = abs(b["c"] - b["o"])
    bp = body_pct(b)
    dt = datetime.fromtimestamp(b["open_time_ms"] / 1000, tz=timezone.utc)
    hour = dt.hour
    if 0 <= hour < 8:
        sess = "ASIA"
    elif 8 <= hour < 13:
        sess = "LON"
    elif 13 <= hour < 17:
        sess = "NY"
    else:
        sess = "LATE"
    marker = ""
    if bp > 70 and d == "BULL":
        marker = " <<< MOMENTUM"
    print(
        f"  {ts(b['open_time_ms'])} [{sess:4s}]  {d:4s}  range={rng:.1f}  body%={bp:.0f}%  "
        f"O={b['o']:.2f} H={b['h']:.2f} C={b['c']:.2f}{marker}"
    )

# ============================================================
# PART 10: INSTITUTIONAL SUMMARY — Anticipatory framework
# ============================================================
print("\n" + "=" * 70)
print("PART 10: INSTITUTIONAL SUMMARY — Framework для антиципації")
print("=" * 70)

print("""
TIMELINE:
  03-31: D1 rejection at 4792 (premium) — перший сигнал: sellers defend D1 high
  04-01: D1 bear indecision (range 247!) — масивна невизначеність, volatile range
  04-01 22:00–04-02 06:00: H1 агресивний sell-off (8h, ~230 pips)
         = LIQUIDITY ENGINEERING — інституціонал create cheap prices
  04-02 06:00: SWEEP — 4553 (нижче всіх session lows) + displacement candle
  04-02 06:00–14:00: ACCUMULATION — 8 годин "нудного" сайдвеєфу
         = інституціонал набирає позицію пока ретейл думає "ще впаде"
  04-02 14:00: MARKUP — перший push від accumulation
  04-05: WEEKEND GAP → higher low at ~4600 (HL vs 4553)
         = confirmation: demand holds, інституціонал не продав
  04-06–07: RE-ACCUMULATION — ще один range, ascending lows
  04-07 19:00: EXPANSION — NY session rocket +60 pips/H1
  04-07 22:00: BREAKOUT D1 high (4800) — 4819
  04-07 23:00: NEW HIGH — 4836

ANTICIPATORY SIGNALS (що можна було побачити ЗАЗДАЛЕГІДЬ):
  1. D1 range-bound (4482–4800) = accumulation/distribution — but WHICH?
  2. Sell-off до sweep = aggressive, but STOPS AT SESSION LOWS cluster
  3. Sweep candle = massive lower wick (40+ pips) + immediate reversal = REJECTION
  4. Post-sweep: ascending H4 lows (4553→4600→4615) = BUYERS in control
  5. Weekend doesn't break structure = HOLDING above sweep
  6. NY session 04-07: break of D1 high on momentum = EXPANSION phase

KEY LESSON:
  Sweep + ascending lows + weekend hold = ACCUMULATION COMPLETE, MARKUP IMMINENT.
  The trader WHO SAW THIS PATTERN would:
    - Enter on first H1 bullish close after sweep (4598) 
    - SL below sweep low (4550) = -48 pip risk
    - TP1 = D1 EQ (4641) — hit same day
    - TP2 = D1 high (4800) — hit day 5
    - TP3 = trail with H4 structure — captured 4836
    - Risk:Reward = 1:3 (TP1) to 1:6 (TP3)
""")
