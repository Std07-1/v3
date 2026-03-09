"""Diag: market calendar check"""

from runtime.ingest.market_calendar import MarketCalendar
import time
from datetime import datetime, timezone

cal = MarketCalendar.from_config("config.json")
now_ms = int(time.time() * 1000)
now_utc = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
print(f"Now UTC: {now_utc}")
print(f"Weekday: {now_utc.strftime('%A')} ({now_utc.weekday()})")

is_open = cal.is_open("XAU/USD", now_ms)
print(f"is_open(XAU/USD, now): {is_open}")

nxt = cal.next_open_ms("XAU/USD", now_ms)
if nxt:
    nxt_utc = datetime.fromtimestamp(nxt / 1000, tz=timezone.utc)
    print(f"next_open: {nxt_utc}")
else:
    print("next_open: None")

# Check what config says about calendar
import json

with open("config.json") as f:
    cfg = json.load(f)
cal_cfg = cfg.get("calendar", {})
print(f"\ncalendar config: {json.dumps(cal_cfg, indent=2)}")

# Also check the symbol-specific open hours
sym_cfg = cfg.get("symbol_calendar", {})
print(f"symbol_calendar: {json.dumps(sym_cfg, indent=2)[:500]}")

# Test is_open for times around Sunday opening
from datetime import timedelta

base = now_utc.replace(hour=21, minute=0, second=0, microsecond=0)
for h_offset in range(0, 5):
    test_dt = base + timedelta(hours=h_offset)
    test_ms = int(test_dt.timestamp() * 1000)
    result = cal.is_open("XAU/USD", test_ms)
    print(f"  {test_dt.strftime('%H:%M')} UTC Sun → is_open={result}")
