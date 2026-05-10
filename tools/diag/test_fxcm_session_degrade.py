"""FXCM Session Degradation Test — keeps session open, polls every 60s.

Імітує ТОЧНО те, що робить broker_sidecar:
1. Відкриває сесію (login)
2. Робить catchup-запит (без date_to) — як tail_catchup
3. Чекає 60 секунд
4. Робить poll-запит (з date_to=now) — як normal poll

Якщо test 1 працює а test 2+ ні — сесія деградує.

Usage on VPS:
    cd /opt/smc-v3
    .venv37/bin/python tools/diag/test_fxcm_session_degrade.py
"""

import sys, time, datetime as dt

sys.path.insert(0, ".")

from env_profile import load_env_secrets

load_env_secrets()
from core.config_loader import env_str
from core.model.bars import ms_to_utc_dt

# Also test ms_to_utc_dt — this is exactly what sidecar uses
print("=== ms_to_utc_dt sanity ===")
now_ms = int(time.time() * 1000)
now_dt = ms_to_utc_dt(now_ms)
print("now_ms=%d  →  %s  (tz=%s)" % (now_ms, now_dt, now_dt.tzinfo))

from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider

SYMBOL = "XAU/USD"
N_BARS = 5
WAIT_S = 60
N_ROUNDS = 5

with FxcmHistoryProvider(
    user_id=env_str("FXCM_USERNAME"),
    password=env_str("FXCM_PASSWORD"),
    url=env_str("FXCM_HOST_URL"),
    connection=env_str("FXCM_CONNECTION") or "Demo",
) as p:
    print("\nFXCM connected. Will do %d rounds, %ds apart.\n" % (N_ROUNDS, WAIT_S))

    for rnd in range(1, N_ROUNDS + 1):
        print("=== ROUND %d/%d ===" % (rnd, N_ROUNDS))

        # A) Catchup-style: no date_to (like tail_catchup)
        t0 = time.time()
        bars_a = p.fetch_last_n_m1(SYMBOL, n=N_BARS)
        ms_a = (time.time() - t0) * 1000
        err_a = p.consume_last_error()
        print(
            "  A) catchup (no date_to): %d bars, %.0fms, err=%s"
            % (len(bars_a), ms_a, err_a)
        )
        if bars_a:
            print(
                "     last bar: open_ms=%d %s"
                % (
                    bars_a[-1].open_time_ms,
                    dt.datetime.utcfromtimestamp(bars_a[-1].open_time_ms / 1000),
                )
            )

        # B) Poll-style: date_to = now (exactly what sidecar does)
        now_utc = dt.datetime.now(dt.timezone.utc)
        t0 = time.time()
        bars_b = p.fetch_last_n_m1(SYMBOL, n=N_BARS, date_to_utc=now_utc)
        ms_b = (time.time() - t0) * 1000
        err_b = p.consume_last_error()
        print(
            "  B) poll (date_to=%s): %d bars, %.0fms, err=%s"
            % (now_utc.strftime("%H:%M:%S"), len(bars_b), ms_b, err_b)
        )
        if bars_b:
            print(
                "     last bar: open_ms=%d %s"
                % (
                    bars_b[-1].open_time_ms,
                    dt.datetime.utcfromtimestamp(bars_b[-1].open_time_ms / 1000),
                )
            )

        # C) Poll-style with ms_to_utc_dt (exactly what sidecar converts)
        cutoff_ms = int(time.time() * 1000) + 60000  # now + 1 min
        date_to_from_ms = ms_to_utc_dt(cutoff_ms)
        t0 = time.time()
        bars_c = p.fetch_last_n_m1(SYMBOL, n=N_BARS, date_to_utc=date_to_from_ms)
        ms_c = (time.time() - t0) * 1000
        err_c = p.consume_last_error()
        print(
            "  C) poll (ms_to_utc_dt(%d)=%s): %d bars, %.0fms, err=%s"
            % (cutoff_ms, date_to_from_ms, len(bars_c), ms_c, err_c)
        )
        if bars_c:
            print(
                "     last bar: open_ms=%d %s"
                % (
                    bars_c[-1].open_time_ms,
                    dt.datetime.utcfromtimestamp(bars_c[-1].open_time_ms / 1000),
                )
            )

        # D) XAG/USD (second symbol — sidecar polls both)
        t0 = time.time()
        bars_d = p.fetch_last_n_m1("XAG/USD", n=N_BARS, date_to_utc=now_utc)
        ms_d = (time.time() - t0) * 1000
        err_d = p.consume_last_error()
        print("  D) XAG/USD poll: %d bars, %.0fms, err=%s" % (len(bars_d), ms_d, err_d))

        if rnd < N_ROUNDS:
            print("  ... waiting %ds ...\n" % WAIT_S)
            time.sleep(WAIT_S)

print("\nDONE")
