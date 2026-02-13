"""Діагностика: аналіз патерну M1 volume + gap/stitch кореляція."""
import json
import urllib.request


def main():
    url = "http://localhost:8089/api/bars?symbol=XAU/USD&tf_s=60&limit=200"
    resp = urllib.request.urlopen(url)
    data = json.loads(resp.read())
    bars = data["bars"]

    complete_bars = [b for b in bars if b.get("complete", True)]
    print("Всього барів:", len(bars), "  complete:", len(complete_bars))
    print("Source:", data.get("meta", {}).get("source"))
    print()

    gaps = 0
    stitched = 0
    gap_volumes = []
    stitch_volumes = []

    for i in range(1, len(complete_bars)):
        prev_c = complete_bars[i - 1]["close"]
        curr_o = complete_bars[i]["open"]
        v = complete_bars[i]["volume"]
        diff = curr_o - prev_c

        if abs(diff) > 0.01:
            gaps += 1
            gap_volumes.append(v)
        else:
            stitched += 1
            stitch_volumes.append(v)

    print("=== Кореляція GAP/STITCH з Volume ===")
    print("GAP (open != prev_close): %d барів" % gaps)
    print("STITCHED (open == prev_close): %d барів" % stitched)
    print()

    if gap_volumes:
        print(
            "Volume @ GAP:     avg=%.0f  min=%.0f  max=%.0f"
            % (
                sum(gap_volumes) / len(gap_volumes),
                min(gap_volumes),
                max(gap_volumes),
            )
        )
    if stitch_volumes:
        print(
            "Volume @ STITCH:  avg=%.0f  min=%.0f  max=%.0f"
            % (
                sum(stitch_volumes) / len(stitch_volumes),
                min(stitch_volumes),
                max(stitch_volumes),
            )
        )

    # Show alternation pattern
    print("\n=== Останні 20 барів (volume + gap/stitch) ===")
    for i in range(max(1, len(complete_bars) - 20), len(complete_bars)):
        b = complete_bars[i]
        prev_c = complete_bars[i - 1]["close"]
        diff = b["open"] - prev_c
        tag = "GAP %+.2f" % diff if abs(diff) > 0.01 else "STCH"
        print(
            "  %d  o=%.2f c=%.2f  v=%6.0f  %s"
            % (b["time"], b["open"], b["close"], b["volume"], tag)
        )


if __name__ == "__main__":
    main()
