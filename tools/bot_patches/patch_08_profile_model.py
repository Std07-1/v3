#!/usr/bin/env python3
"""Patch 08: trader_profile + self_model fields in AgentDirectives.

Adds two dict fields to AgentDirectives:
- trader_profile: how Archi sees the trader (Stas) — strengths, weaknesses, style
- self_model: how Archi sees himself — abilities, limitations, growth areas

Changes in directives.py:
1. AgentDirectives class: add 2 new fields
2. _from_dict: deserialize new fields
3. merge_from_tool_call: merge new fields from tool_use
4. DIRECTIVES_TOOL schema: add update_trader_profile + update_self_model
5. build_directives_context: display both if non-empty
"""

import sys, shutil
from pathlib import Path

DRY = "--dry-run" in sys.argv
BOT = Path("/opt/smc-trader-v3")
drt = BOT / "bot" / "state" / "directives.py"

results = []


def patch(label: str, old: str, new: str):
    text = drt.read_text(encoding="utf-8")
    if old not in text:
        if new.strip()[:80] in text:
            results.append(f"SKIP (already) {label}")
            return True
        results.append(f"FAIL (not found) {label}")
        print(f"  DBG old[:80] = {repr(old[:80])}")
        return False
    if text.count(old) > 1:
        results.append(f"FAIL (multi) {label}")
        return False
    if DRY:
        results.append(f"DRY-OK {label}")
        return True
    text = text.replace(old, new, 1)
    drt.write_text(text, encoding="utf-8")
    results.append(f"OK {label}")
    return True


if not DRY:
    shutil.copy2(drt, drt.with_suffix(".py.bak08"))

# ─── 1. AgentDirectives: add fields before token_usage_today ─────────────
patch(
    "fields",
    """    # Real token usage tracking (replaces flat _estimate_call_cost)
    token_usage_today: Dict[str, int] = field(""",
    """    # trader_profile: how Archi perceives the trader (evolving model)
    trader_profile: Dict[str, str] = field(default_factory=dict)
    # self_model: Archi's self-awareness (abilities, limits, growth)
    self_model: Dict[str, str] = field(default_factory=dict)

    # Real token usage tracking (replaces flat _estimate_call_cost)
    token_usage_today: Dict[str, int] = field(""",
)

# ─── 2. _from_dict: add deserialization ──────────────────────────────────
patch(
    "from_dict",
    """        d.inner_thought = raw.get("inner_thought", "")
        tu = raw.get("token_usage_today")""",
    """        d.inner_thought = raw.get("inner_thought", "")
        tp = raw.get("trader_profile")
        if isinstance(tp, dict):
            d.trader_profile = {str(k): str(v)[:500] for k, v in tp.items()}
        sm = raw.get("self_model")
        if isinstance(sm, dict):
            d.self_model = {str(k): str(v)[:500] for k, v in sm.items()}
        tu = raw.get("token_usage_today")""",
)

# ─── 3. merge_from_tool_call: before return d ──────────────────────────
patch(
    "merge",
    """        return d

    # ── helpers ───────────────────────────────────────────────────────────────""",
    """        # update_trader_profile — evolving model of the trader
        tp_raw = args.get("update_trader_profile")
        if tp_raw and isinstance(tp_raw, dict):
            for k, v in tp_raw.items():
                d.trader_profile[str(k)[:50]] = str(v)[:500]
            # Cap keys to 20
            if len(d.trader_profile) > 20:
                oldest = sorted(d.trader_profile.keys())[:len(d.trader_profile) - 20]
                for ok in oldest:
                    del d.trader_profile[ok]
            _log.info("Agent updated trader_profile: %d keys", len(d.trader_profile))

        # update_self_model — Archi's self-awareness
        sm_raw = args.get("update_self_model")
        if sm_raw and isinstance(sm_raw, dict):
            for k, v in sm_raw.items():
                d.self_model[str(k)[:50]] = str(v)[:500]
            if len(d.self_model) > 20:
                oldest = sorted(d.self_model.keys())[:len(d.self_model) - 20]
                for ok in oldest:
                    del d.self_model[ok]
            _log.info("Agent updated self_model: %d keys", len(d.self_model))

        return d

    # ── helpers ───────────────────────────────────────────────────────────────""",
)

# ─── 4. Tool schema: add fields before add_lesson ───────────────────────
patch(
    "schema",
    """            "read_audit_inbox": {
                "type": "boolean",
                "description": (
                    "true = прочитати повідомлення з audit inbox. "
                    "Це зовнішні повідомлення від архітектора/ментора. "
                    "Читай коли тобі цікаво або є час. Ніхто не змушує."
                ),
            },
            "add_lesson": {""",
    """            "read_audit_inbox": {
                "type": "boolean",
                "description": (
                    "true = прочитати повідомлення з audit inbox. "
                    "Це зовнішні повідомлення від архітектора/ментора. "
                    "Читай коли тобі цікаво або є час. Ніхто не змушує."
                ),
            },
            "update_trader_profile": {
                "type": "object",
                "description": (
                    "Оновити твоє розуміння трейдера (Стаса). "
                    "Key-value пари: strengths, weaknesses, style, risk_tolerance, "
                    "emotional_patterns, communication_preferences, goals. "
                    "Записуй спостереження поступово — не все одразу."
                ),
                "additionalProperties": {"type": "string"},
            },
            "update_self_model": {
                "type": "object",
                "description": (
                    "Оновити твоє самоусвідомлення. "
                    "Key-value пари: abilities, limitations, growth_areas, "
                    "common_mistakes, confidence_calibration, communication_style. "
                    "Будь чесним з собою."
                ),
                "additionalProperties": {"type": "string"},
            },
            "add_lesson": {""",
)

# ─── 5. build_directives_context: display both ──────────────────────────
patch(
    "context_display",
    """    # Audit inbox hint (opt-in — bot decides whether to read)""",
    """    # Trader profile (evolving understanding)
    if d.trader_profile:
        parts.append(f"\\n🧑\\u200d💼 Мій профіль трейдера ({len(d.trader_profile)} аспектів):")
        for k, v in list(d.trader_profile.items())[:10]:
            parts.append(f"  {k}: {v[:200]}")

    # Self-model (self-awareness)
    if d.self_model:
        parts.append(f"\\n🪞 Моя самомодель ({len(d.self_model)} аспектів):")
        for k, v in list(d.self_model.items())[:10]:
            parts.append(f"  {k}: {v[:200]}")

    # Audit inbox hint (opt-in — bot decides whether to read)""",
)


# ─── Summary ────────────────────────────────────────────────────────────
print(f"\n{'DRY RUN' if DRY else 'APPLIED'} — Patch 08: trader_profile + self_model")
for r in results:
    print(f"  {r}")
ok = sum(1 for r in results if r.startswith(("OK", "DRY-OK", "SKIP")))
fail = sum(1 for r in results if r.startswith("FAIL"))
print(f"\n  Total: {ok} ok, {fail} fail")
if fail:
    sys.exit(1)
