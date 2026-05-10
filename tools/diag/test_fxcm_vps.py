"""Quick FXCM History API diagnostic — run on VPS via .venv37."""

import sys, time, datetime as dt

sys.path.insert(0, ".")

from env_profile import load_env_secrets

load_env_secrets()
from core.config_loader import env_str
from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider

with FxcmHistoryProvider(
    user_id=env_str("FXCM_USERNAME"),
    password=env_str("FXCM_PASSWORD"),
    url=env_str("FXCM_HOST_URL"),
    connection=env_str("FXCM_CONNECTION") or "Demo",
) as p:
    print("FXCM connected")

    # Test 1: no date_to (SDK default "now")
    t0 = time.time()
    bars1 = p.fetch_last_n_m1("XAU/USD", n=3)
    t1 = time.time()
    print("TEST1 no_date_to: %d bars (%.1fms)" % (len(bars1), (t1 - t0) * 1000))
    for b in bars1:
        print(
            "  open_ms=%d  %s"
            % (b.open_time_ms, dt.datetime.utcfromtimestamp(b.open_time_ms / 1000))
        )

    # Test 2: date_to = 10 min ago
    ago10 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)
    t0 = time.time()
    bars2 = p.fetch_last_n_m1("XAU/USD", n=3, date_to_utc=ago10)
    t1 = time.time()
    print("TEST2 10min_ago: %d bars (%.1fms)" % (len(bars2), (t1 - t0) * 1000))
    for b in bars2:
        print(
            "  open_ms=%d  %s"
            % (b.open_time_ms, dt.datetime.utcfromtimestamp(b.open_time_ms / 1000))
        )

    # Test 3: date_to = now
    now = dt.datetime.now(dt.timezone.utc)
    t0 = time.time()
    bars3 = p.fetch_last_n_m1("XAU/USD", n=3, date_to_utc=now)
    t1 = time.time()
    print("TEST3 now: %d bars (%.1fms)" % (len(bars3), (t1 - t0) * 1000))
    for b in bars3:
        print(
            "  open_ms=%d  %s"
            % (b.open_time_ms, dt.datetime.utcfromtimestamp(b.open_time_ms / 1000))
        )

    # Test 4: date_to = 3 min ago
    ago3 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)
    t0 = time.time()
    bars4 = p.fetch_last_n_m1("XAU/USD", n=3, date_to_utc=ago3)
    t1 = time.time()
    print("TEST4 3min_ago: %d bars (%.1fms)" % (len(bars4), (t1 - t0) * 1000))
    for b in bars4:
        print(
            "  open_ms=%d  %s"
            % (b.open_time_ms, dt.datetime.utcfromtimestamp(b.open_time_ms / 1000))
        )

print("DONE")
