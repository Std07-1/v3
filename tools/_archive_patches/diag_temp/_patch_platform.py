#!/usr/bin/env python3
"""One-shot patch: add new fields to parse_http_context in bot platform.py."""

import pathlib

p = pathlib.Path("/opt/smc-trader-v3/bot/transport/platform.py")
t = p.read_text()

# Fix mangled sed output first
old_mangled = """        "server_ts_ms": data.get("server_ts_ms", 0),
        " tick_price: data.get( ick_price),
        ick_ts_ms: data.get(    ick_ts_ms),
 data_quality: data.get(data_quality) or {},
 h4_forming: data.get(h4_forming),
    }"""

new_clean = """        "server_ts_ms": data.get("server_ts_ms", 0),
        "tick_price": data.get("tick_price"),
        "tick_ts_ms": data.get("tick_ts_ms"),
        "data_quality": data.get("data_quality") or {},
        "h4_forming": data.get("h4_forming"),
    }"""

if old_mangled in t:
    t = t.replace(old_mangled, new_clean)
    p.write_text(t)
    print("PATCHED: fixed mangled sed + added new fields")
elif '"server_ts_ms": data.get("server_ts_ms", 0),\n    }' in t:
    # Clean state — just add fields
    old_clean = '"server_ts_ms": data.get("server_ts_ms", 0),\n    }'
    t = t.replace(
        old_clean,
        '"server_ts_ms": data.get("server_ts_ms", 0),\n'
        '        "tick_price": data.get("tick_price"),\n'
        '        "tick_ts_ms": data.get("tick_ts_ms"),\n'
        '        "data_quality": data.get("data_quality") or {},\n'
        '        "h4_forming": data.get("h4_forming"),\n    }',
    )
    p.write_text(t)
    print("PATCHED: added new fields to clean state")
elif '"tick_price": data.get("tick_price")' in t:
    print("ALREADY PATCHED: tick_price already present")
else:
    print("ERROR: could not find insertion point")
    # Show context for debugging
    idx = t.find("server_ts_ms")
    if idx >= 0:
        print("Context:", repr(t[idx : idx + 200]))
