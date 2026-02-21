"""
Live cascade monitor — proof that H1/H4 bars appear automatically.

Usage:
    python -m tools.live_cascade_monitor --symbol XAU/USD --watch H1
    python -m tools.live_cascade_monitor --symbol XAU/USD --watch H4
    python -m tools.live_cascade_monitor --symbol XAU/USD --watch ALL

Polls /api/bars every 30s and reports when new bars appear.
"""
import argparse
import json
import datetime
import time
import urllib.request

API_BASE = "http://localhost:8089"

TF_MAP = {
    "M1": 60, "M3": 180, "M5": 300, "M15": 900,
    "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400,
}
TF_NAME = {v: k for k, v in TF_MAP.items()}

# H4 anchor offset (from config.json)
H4_ANCHOR_S = 68400  # 19:00 UTC


def bucket_start(now_ms, tf_s):
    """Current open bucket start."""
    tf_ms = tf_s * 1000
    if tf_s == 14400:
        anchor_ms = H4_ANCHOR_S * 1000
        return ((now_ms - anchor_ms) // tf_ms) * tf_ms + anchor_ms
    return (now_ms // tf_ms) * tf_ms


def fmt_ts(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def fetch_last_bar(symbol, tf_s):
    url = "%s/api/bars?symbol=%s&tf_s=%d&limit=1" % (
        API_BASE, symbol.replace("/", "%2F"), tf_s,
    )
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        bars = data.get("bars", [])
        if bars:
            return bars[0]
    except Exception as e:
        print("  [ERR] fetch %s tf_s=%d: %s" % (symbol, tf_s, e))
    return None


def main():
    parser = argparse.ArgumentParser(description="Live cascade monitor")
    parser.add_argument("--symbol", default="XAU/USD")
    parser.add_argument("--watch", default="ALL", help="H1, H4, or ALL")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval sec")
    args = parser.parse_args()

    if args.watch == "ALL":
        watch_tfs = [300, 900, 1800, 3600, 14400]
    else:
        watch_tfs = [TF_MAP[args.watch]]

    # Initial snapshot
    last_open_ms = {}
    for tf_s in watch_tfs:
        bar = fetch_last_bar(args.symbol, tf_s)
        if bar:
            last_open_ms[tf_s] = bar["open_time_ms"]
        else:
            last_open_ms[tf_s] = 0

    now_ms = int(time.time() * 1000)
    print("=" * 70)
    print("LIVE CASCADE MONITOR  symbol=%s  watch=%s" % (args.symbol, args.watch))
    print("Started at %s UTC" % fmt_ts(now_ms))
    print()
    for tf_s in watch_tfs:
        name = TF_NAME.get(tf_s, str(tf_s))
        cur = bucket_start(now_ms, tf_s)
        close = cur + tf_s * 1000
        last = last_open_ms.get(tf_s, 0)
        print("  %s: last_bar=%s  current_bucket=%s (closes %s)" % (
            name, fmt_ts(last) if last else "none",
            fmt_ts(cur), fmt_ts(close),
        ))
    print()
    print("Polling every %ds. New bars will be reported below:" % args.interval)
    print("-" * 70)

    try:
        while True:
            time.sleep(args.interval)
            now_ms = int(time.time() * 1000)
            now_str = fmt_ts(now_ms)

            for tf_s in watch_tfs:
                bar = fetch_last_bar(args.symbol, tf_s)
                if bar and bar["open_time_ms"] != last_open_ms.get(tf_s, 0):
                    name = TF_NAME.get(tf_s, str(tf_s))
                    oms = bar["open_time_ms"]
                    print("[%s] NEW %s bar: open=%s complete=%s src=%s" % (
                        now_str, name, fmt_ts(oms),
                        bar.get("complete"), bar.get("src", "?"),
                    ))
                    last_open_ms[tf_s] = oms

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
