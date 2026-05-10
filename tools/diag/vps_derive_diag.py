#!/usr/bin/env python3
"""VPS Derive Chain Diagnostic — перевіряє чому XAU/USD derived bars не потрапляють в UI.

Запуск на VPS:
    cd /opt/smc-v3
    .venv/bin/python -m tools.diag.vps_derive_diag
"""

import json
import os
import time
import sys


def main():
    # 1. Check disk bar freshness for XAU/USD
    print("=" * 60)
    print("1. DISK BAR FRESHNESS (XAU/USD)")
    print("=" * 60)
    now = time.time()
    symbol = "XAU_USD"
    for tf_s in [60, 180, 300, 900, 1800, 3600, 14400, 86400]:
        tf_dir = f"data_v3/{symbol}/tf_{tf_s}"
        if not os.path.isdir(tf_dir):
            print(f"  tf={tf_s:>5}: DIR MISSING")
            continue
        files = sorted(f for f in os.listdir(tf_dir) if f.endswith(".jsonl"))
        if not files:
            print(f"  tf={tf_s:>5}: NO FILES")
            continue
        last_file = os.path.join(tf_dir, files[-1])
        mtime = os.path.getmtime(last_file)
        age_h = (now - mtime) / 3600
        try:
            with open(last_file, "rb") as fh:
                fh.seek(max(0, os.path.getsize(last_file) - 500))
                lines = fh.read().decode().strip().split("\n")
                last_line = json.loads(lines[-1])
                bar_ts = last_line.get("open_time_ms", 0) / 1000
                bar_age_h = (now - bar_ts) / 3600
                bar_dt = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(bar_ts))
        except Exception as e:
            bar_age_h = -1
            bar_dt = f"ERR: {e}"
        status = "OK" if age_h < 1 else ("STALE!" if age_h > 4 else "WARN")
        print(
            f"  tf={tf_s:>5}: file_age={age_h:.1f}h  last_bar={bar_dt}  bar_age={bar_age_h:.1f}h  [{status}]"
        )

    # 2. Check Redis update bus for XAU/USD
    print()
    print("=" * 60)
    print("2. REDIS UPDATE BUS (XAU/USD)")
    print("=" * 60)
    try:
        import redis as redis_mod

        r = redis_mod.Redis(host="127.0.0.1", port=6379, db=1, decode_responses=True)
        r.ping()
        ns = "v3_local"
        for tf_s in [60, 180, 300, 900, 1800, 3600, 14400, 86400]:
            list_key = f"{ns}:updates:list:XAU/USD:{tf_s}"
            seq_key = f"{ns}:updates:seq:XAU/USD:{tf_s}"
            list_len = r.llen(list_key)
            seq_val = r.get(seq_key)
            if list_len > 0:
                last_raw = r.lindex(list_key, -1)
                try:
                    last_ev = json.loads(last_raw)
                    last_seq = last_ev.get("seq", "?")
                    bar = last_ev.get("bar", {})
                    last_open_ms = bar.get("open_time_ms", bar.get("open_ms", 0))
                    if last_open_ms:
                        last_dt = time.strftime(
                            "%Y-%m-%d %H:%M UTC", time.gmtime(last_open_ms / 1000)
                        )
                    else:
                        last_dt = "?"
                except Exception:
                    last_seq = "?"
                    last_dt = "?"
                print(
                    f"  tf={tf_s:>5}: list_len={list_len}  seq={seq_val}  last_seq={last_seq}  last_bar={last_dt}"
                )
            else:
                print(f"  tf={tf_s:>5}: EMPTY (list_len=0, seq={seq_val})")
    except Exception as e:
        print(f"  Redis error: {e}")

    # 3. Check Redis update bus for BTCUSDT (comparison)
    print()
    print("=" * 60)
    print("3. REDIS UPDATE BUS (BTCUSDT — for comparison)")
    print("=" * 60)
    try:
        for tf_s in [300, 900, 3600, 14400]:
            list_key = f"{ns}:updates:list:BTCUSDT:{tf_s}"
            seq_key = f"{ns}:updates:seq:BTCUSDT:{tf_s}"
            list_len = r.llen(list_key)
            seq_val = r.get(seq_key)
            print(f"  tf={tf_s:>5}: list_len={list_len}  seq={seq_val}")
    except Exception as e:
        print(f"  Redis error: {e}")

    # 4. Check Redis keys pattern
    print()
    print("=" * 60)
    print("4. ALL REDIS UPDATE KEYS")
    print("=" * 60)
    try:
        keys = r.keys(f"{ns}:updates:*")
        for k in sorted(keys):
            ktype = r.type(k)
            if ktype == "list":
                length = r.llen(k)
                print(f"  {k} (list, len={length})")
            else:
                val = r.get(k)
                print(f"  {k} ({ktype}, val={val})")
    except Exception as e:
        print(f"  Redis error: {e}")

    # 5. Check m1_ingestion_worker log for DERIVE_OK / DERIVE_SKIP / DERIVE_REJECT
    print()
    print("=" * 60)
    print("5. M1_INGESTION DERIVE LOG GREP (last 5000 lines)")
    print("=" * 60)
    log_file = "logs/m1_ingestion_worker.err.log"
    if os.path.exists(log_file):
        try:
            with open(log_file, "rb") as f:
                f.seek(max(0, os.path.getsize(log_file) - 500_000))
                tail = f.read().decode(errors="replace").split("\n")
            derive_lines = [
                l for l in tail if "DERIVE_" in l and "DeriveEngine" not in l
            ]
            if derive_lines:
                print(f"  Found {len(derive_lines)} DERIVE_ lines:")
                for line in derive_lines[-20:]:
                    print(f"    {line.strip()}")
            else:
                print("  NO DERIVE_ lines found in last ~500KB of log!")

            # Also check for UDS commit drops
            drop_lines = [l for l in tail if "commit_final_bar drops" in l]
            if drop_lines:
                print(f"\n  Found {len(drop_lines)} UDS drop lines:")
                for line in drop_lines[-10:]:
                    print(f"    {line.strip()}")
        except Exception as e:
            print(f"  Log read error: {e}")
    else:
        print(f"  Log file not found: {log_file}")

    # 6. Check ws_server log for BG_SMC_FEED activity
    print()
    print("=" * 60)
    print("6. WS_SERVER BG_SMC_FEED LOG GREP (last ~500KB)")
    print("=" * 60)
    ws_log = "logs/ws_server.err.log"
    if os.path.exists(ws_log):
        try:
            with open(ws_log, "rb") as f:
                f.seek(max(0, os.path.getsize(ws_log) - 500_000))
                tail = f.read().decode(errors="replace").split("\n")
            bg_lines = [
                l for l in tail if "BG_SMC" in l or "SMC_SLOW" in l or "SMC_ON_BAR" in l
            ]
            if bg_lines:
                print(f"  Found {len(bg_lines)} SMC feed lines:")
                for line in bg_lines[-15:]:
                    print(f"    {line.strip()}")
            else:
                print("  NO BG_SMC / SMC_SLOW / SMC_ON_BAR lines found!")
            # warmup status
            warmup_lines = [l for l in tail if "WARMUP" in l or "warmup" in l]
            if warmup_lines:
                print(f"\n  Found {len(warmup_lines)} WARMUP lines:")
                for line in warmup_lines[-10:]:
                    print(f"    {line.strip()}")
        except Exception as e:
            print(f"  Log read error: {e}")
    else:
        print(f"  Log file not found: {ws_log}")

    print()
    print("=" * 60)
    print("DONE. Send output to agent for analysis.")
    print("=" * 60)


if __name__ == "__main__":
    main()
