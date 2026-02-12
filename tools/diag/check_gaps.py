"""Quick diagnostic: check price gaps for all TFs."""
import json
import urllib.request

for tf in [60, 180, 300, 900, 1800, 3600, 14400, 86400]:
    try:
        r = urllib.request.urlopen(
            f"http://localhost:8089/api/bars?symbol=XAU/USD&tf_s={tf}&limit=50"
        )
        d = json.loads(r.read())
        bars = d.get("bars", [])
        n = len(bars)
        gaps = 0
        if n > 1:
            for i in range(1, n):
                prev_c = bars[i - 1].get("close", bars[i - 1].get("c"))
                curr_o = bars[i].get("open", bars[i].get("o"))
                if (
                    prev_c is not None
                    and curr_o is not None
                    and abs(curr_o - prev_c) > 0.01
                ):
                    gaps += 1
        print(f"tf={tf:5d}  bars={n:4d}  gaps={gaps}")
    except Exception as e:
        print(f"tf={tf:5d}  ERROR: {e}")
