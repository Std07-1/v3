#!/usr/bin/env python3
"""Patch prompts.py: add tick_price, data_quality, h4_forming to context_summary()."""

import pathlib, sys

p = pathlib.Path("/opt/smc-trader-v3/bot/agent/prompts.py")
text = p.read_text(encoding="utf-8")

# Check if already patched
if "# -- Platform data enrichments" in text:
    print("ALREADY PATCHED: enrichment block found")
    sys.exit(0)

# Find context_summary function
cs_idx = text.find("def context_summary(")
if cs_idx < 0:
    print("ERROR: context_summary() not found")
    sys.exit(1)

# Find the return statement: return "\n".join(lines)
anchor = 'return "\\n".join(lines)'
ret_idx = text.find(anchor, cs_idx)
if ret_idx < 0:
    print("ERROR: return statement not found")
    sys.exit(1)

# Calculate indentation (should be 4 spaces)
line_start = text.rfind("\n", 0, ret_idx) + 1
indent = text[line_start:ret_idx]  # whitespace before return

NEW_BLOCK = """
    # -- Platform data enrichments (tick_price, data_quality, h4_forming) --
    _any_ctx = {}
    for _etf in analysis_tfs:
        _ed = ctx.get(_etf, {})
        if _ed:
            _any_ctx = _ed
            break

    # Tick price freshness indicator (sub-second vs M1 ~60s delay)
    _tick_p = _any_ctx.get("tick_price")
    _tick_ts = _any_ctx.get("tick_ts_ms", 0)
    if _tick_p and float(_tick_p) > 0:
        _tick_age_s = int(now_ts - _tick_ts / 1000) if _tick_ts else 0
        if _tick_age_s < 10:
            lines.append(f"\\nLIVE tick: {float(_tick_p):.2f} ({_tick_age_s}s ago)")
        elif _tick_age_s < 120:
            lines.append(f"\\nTick: {float(_tick_p):.2f} ({_tick_age_s}s ago)")
        else:
            lines.append(f"\\nTick: {float(_tick_p):.2f} ({_tick_age_s // 60}m ago)")

    # Server-side data quality: stale TF auto-detection
    _dq = _any_ctx.get("data_quality") or {}
    _tf_fresh = _dq.get("tf_freshness", {})
    _stale_list = [(k, v) for k, v in _tf_fresh.items() if isinstance(v, dict) and v.get("stale")]
    if _stale_list:
        _stale_names = ", ".join(k for k, _ in _stale_list)
        lines.append(f"\\nSTALE DATA: {_stale_names}")
        for _sk, _sv in _stale_list:
            lines.append(f"  {_sk}: {_sv.get('age_s', '?')}s old, {_sv.get('bars_count', '?')} bars")

    # H4 forming candle (synthesized from M1 bars + tick in real-time)
    _h4f = None
    for _htf in analysis_tfs:
        _h4f = ctx.get(_htf, {}).get("h4_forming")
        if _h4f:
            break
    if _h4f and isinstance(_h4f, dict):
        _h4_age_m = _h4f.get("age_s", 0) // 60
        lines.append(
            f"\\nH4 Forming ({_h4f.get('m1_count', 0)} M1, {_h4_age_m}m into candle): "
            f"O={float(_h4f.get('o', 0)):.2f} H={float(_h4f.get('h', 0)):.2f} "
            f"L={float(_h4f.get('l', 0)):.2f} C={float(_h4f.get('c', 0)):.2f}"
        )

"""

new_text = text[:ret_idx] + NEW_BLOCK + text[ret_idx:]
p.write_text(new_text, encoding="utf-8")
print("PATCHED OK: tick_price + data_quality + h4_forming added to context_summary()")
