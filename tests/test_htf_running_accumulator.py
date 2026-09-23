"""Tests for _HTFRunningAccumulator (HTF live preview from M1).

Covers: D1/H4 incremental aggregation, per-tick dedup (D-01/D-04),
bucket rollover, seed path, seasonal grid per M1 (ADR-0095 S4b), symbol isolation.

ADR-0095 S4b: бакет кожного M1 — `htf_bucket_start_ms` за правилом якоря символу. До S4b якір брався зі словника
секунд, зібраного на старті, тож після вихідних DST preview H4/D1 стояв на сітці минулого сезону до рестарту.
"""

import datetime as dt
import inspect
from unittest.mock import MagicMock

import pytest

from core.model.bars import CandleBar
from core.session_anchor import D1_S, H4_S, RULE_NY_CLOSE_US_DST, RULE_UTC_MIDNIGHT

from runtime.ingest import tick_preview_worker as tick_preview_worker_module
from runtime.ingest.tick_preview_worker import (
    TickPreviewWorker,
    _build_htf_anchor_rules,
    _HTFRunningAccumulator,
    _RunningBar,
)

UTC = dt.timezone.utc
FXCM = RULE_NY_CLOSE_US_DST


def _ms(year, month, day, hour, minute=0, second=0):
    return int(dt.datetime(year, month, day, hour, minute, second, tzinfo=UTC).timestamp() * 1000)


def _make_m1(symbol, open_ms, o, h, low, c, v=100.0, complete=True):
    return CandleBar(
        symbol=symbol,
        tf_s=60,
        open_time_ms=open_ms,
        close_time_ms=open_ms + 60_000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=v,
        complete=complete,
        src="test",
    )


class TestHTFRunningAccumulator:

    def _make_acc(self, tfs=None):
        tfs = tfs or [H4_S, D1_S]
        return _HTFRunningAccumulator(tfs, {"XAU/USD": FXCM, "NAS100": FXCM})

    def test_single_m1_produces_htf_previews(self):
        """1 M1 бар → 1 H4 + 1 D1 preview."""
        acc = self._make_acc()
        m1 = _make_m1("XAU/USD", 1742169600000, 2000.0, 2001.0, 1999.0, 2000.5)
        results = acc.update("XAU/USD", m1)
        assert len(results) == 2
        d1 = [r for r in results if r.tf_s == 86400][0]
        h4 = [r for r in results if r.tf_s == 14400][0]
        assert d1.o == 2000.0
        assert d1.h == 2001.0
        assert d1.low == 1999.0
        assert d1.c == 2000.5
        assert d1.complete is False
        assert d1.src == "htf_preview"
        assert h4.complete is False

    def test_incremental_merge_ohlcv(self):
        """Послідовні M1 бари коректно агрегуються."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 99, 103, 10))
        results = acc.update(
            "XAU/USD", _make_m1("XAU/USD", base_ms + 60000, 103, 110, 101, 108, 20)
        )

        d1 = results[0]
        assert d1.o == 100
        assert d1.h == 110
        assert d1.low == 99
        assert d1.c == 108
        assert d1.v == 30
        assert d1.extensions["m1_count"] == 2

    def test_bucket_rollover_resets_state(self):
        """При переході в новий D1 бакет — state скидається."""
        acc = self._make_acc([86400])
        bucket1_m1 = 1742169600000
        bucket2_m1 = bucket1_m1 + 86400 * 1000

        acc.update("XAU/USD", _make_m1("XAU/USD", bucket1_m1, 100, 110, 90, 105))
        results = acc.update(
            "XAU/USD", _make_m1("XAU/USD", bucket2_m1, 200, 210, 190, 205)
        )

        d1 = results[0]
        assert d1.o == 200
        assert d1.h == 210
        assert d1.extensions["m1_count"] == 1

    def test_seed_uses_update_path(self):
        """seed() = послідовний update(). Результат ідентичний."""
        acc1 = self._make_acc([86400])
        acc2 = self._make_acc([86400])
        base_ms = 1742169600000

        bars = [
            _make_m1("XAU/USD", base_ms + i * 60000, 100 + i, 105 + i, 99 + i, 103 + i)
            for i in range(5)
        ]

        acc1.seed("XAU/USD", bars)
        r1 = acc1.update(
            "XAU/USD", _make_m1("XAU/USD", base_ms + 5 * 60000, 106, 111, 105, 109)
        )

        for b in bars:
            acc2.update("XAU/USD", b)
        r2 = acc2.update(
            "XAU/USD", _make_m1("XAU/USD", base_ms + 5 * 60000, 106, 111, 105, 109)
        )

        d1_1 = [r for r in r1 if r.tf_s == 86400][0]
        d1_2 = [r for r in r2 if r.tf_s == 86400][0]
        assert d1_1.o == d1_2.o
        assert d1_1.h == d1_2.h
        assert d1_1.low == d1_2.low
        assert d1_1.c == d1_2.c
        assert d1_1.v == d1_2.v
        assert d1_1.extensions["m1_count"] == d1_2.extensions["m1_count"]

    def test_multiple_symbols_isolated(self):
        """Різні символи не інтерферують."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 2000, 2010, 1990, 2005))
        results = acc.update(
            "NAS100", _make_m1("NAS100", base_ms, 18000, 18100, 17900, 18050)
        )

        nas_d1 = results[0]
        assert nas_d1.o == 18000
        assert nas_d1.extensions["m1_count"] == 1

    @pytest.mark.parametrize(
        "m1_ms, expected_h4_ms",
        [
            (_ms(2025, 3, 17, 1), _ms(2025, 3, 17, 1)),  # літо: сітка 21/01/05/..
            (_ms(2026, 1, 6, 1), _ms(2026, 1, 5, 22)),  # зима: сітка 22/02/06/..
        ],
        ids=["summer", "winter"],
    )
    def test_h4_on_season_grid(self, m1_ms, expected_h4_ms):
        """H4 бакет — сезонна сітка 17:00 NY, а не статичний якір 82800 (23:00 UTC)."""
        acc = self._make_acc([14400])
        h4 = acc.update("XAU/USD", _make_m1("XAU/USD", m1_ms, 100, 105, 99, 103))[0]
        assert h4.open_time_ms == expected_h4_ms
        # I2: close = open + tf_ms
        assert h4.close_time_ms == h4.open_time_ms + 14400 * 1000

    @pytest.mark.parametrize(
        "m1_ms, expected_d1_ms",
        [
            (_ms(2025, 3, 17, 0), _ms(2025, 3, 16, 21)),  # літо: 17:00 NY = 21:00 UTC
            (_ms(2026, 1, 6, 0), _ms(2026, 1, 5, 22)),  # зима: 17:00 NY = 22:00 UTC
        ],
        ids=["summer", "winter"],
    )
    def test_d1_on_season_grid(self, m1_ms, expected_d1_ms):
        """D1 бакет відкривається о 17:00 Нью-Йорка, а не на статичних 22:00 UTC."""
        acc = self._make_acc([86400])
        d1 = acc.update("XAU/USD", _make_m1("XAU/USD", m1_ms, 100, 105, 99, 103))[0]
        assert d1.open_time_ms == expected_d1_ms
        assert d1.close_time_ms == d1.open_time_ms + 86400 * 1000

    def test_utc_midnight_rule_for_binance_symbol(self):
        """Правило символу, а не одне на процес: Binance поруч з FXCM бере опівніч UTC."""
        acc = _HTFRunningAccumulator([H4_S, D1_S], {"XAU/USD": FXCM, "BTCUSDT": RULE_UTC_MIDNIGHT})
        m1_ms = _ms(2026, 3, 8, 21)
        btc = {r.tf_s: r for r in acc.update("BTCUSDT", _make_m1("BTCUSDT", m1_ms, 1, 2, 0.5, 1.5))}
        xau = {r.tf_s: r for r in acc.update("XAU/USD", _make_m1("XAU/USD", m1_ms, 1, 2, 0.5, 1.5))}
        assert btc[D1_S].open_time_ms == _ms(2026, 3, 8, 0)
        assert btc[H4_S].open_time_ms == _ms(2026, 3, 8, 20)
        assert xau[D1_S].open_time_ms == _ms(2026, 3, 8, 21)
        assert xau[H4_S].open_time_ms == _ms(2026, 3, 8, 21)

    def test_symbol_without_rule_raises(self):
        """Символ без правила — гучна ValueError, а не бакет тихого якоря 0."""
        acc = self._make_acc()
        with pytest.raises(ValueError, match="HTF_PREVIEW_ANCHOR_RULE_MISSING symbol=GER30"):
            acc.update("GER30", _make_m1("GER30", _ms(2026, 3, 9, 8), 1, 2, 0.5, 1.5))

    def test_unknown_rule_rejected_at_construction(self):
        with pytest.raises(ValueError, match="HTF_PREVIEW_ANCHOR_RULE_UNKNOWN"):
            _HTFRunningAccumulator([H4_S], {"XAU/USD": "tv_anchor_82800"})

    @pytest.mark.parametrize("tf_s", [3600, 604800])
    def test_non_htf_tf_rejected_at_construction(self, tf_s):
        """Сезонна сітка визначена лише для H4/D1: інший TF — відмова на старті, а не на першому тіку."""
        with pytest.raises(ValueError, match="HTF_PREVIEW_TF_UNSUPPORTED"):
            _HTFRunningAccumulator([H4_S, tf_s], {"XAU/USD": FXCM})

    def test_fall_stub_h4_rolls_at_winter_open(self):
        """Нд 01.11.2026: H4 21:00 — обрубок 1 год; M1 22:00 відкриває новий H4 і D1, а не зливається в 21:00."""
        acc = self._make_acc()
        stub = {r.tf_s: r for r in acc.update("XAU/USD", _make_m1("XAU/USD", _ms(2026, 11, 1, 21, 30), 10, 11, 9, 10))}
        assert stub[H4_S].open_time_ms == _ms(2026, 11, 1, 21)
        after = {r.tf_s: r for r in acc.update("XAU/USD", _make_m1("XAU/USD", _ms(2026, 11, 1, 22), 20, 21, 19, 20))}
        assert after[H4_S].open_time_ms == _ms(2026, 11, 1, 22)
        assert after[D1_S].open_time_ms == _ms(2026, 11, 1, 22)
        assert after[H4_S].o == 20 and after[H4_S].extensions["m1_count"] == 1
        assert after[D1_S].o == 20 and after[D1_S].extensions["m1_count"] == 1

    def test_d1_only_mode(self):
        """Можна запустити тільки з D1 (без H4)."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000
        results = acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 99, 103))
        assert len(results) == 1
        assert results[0].tf_s == 86400

    # ---------------------------------------------------------------
    # D-01 / D-04 fix: per-tick dedup tests
    # ---------------------------------------------------------------
    def test_same_m1_bar_updated_multiple_ticks(self):
        """Same M1 bar updated 5 times (simulates 5 ticks in one minute).

        D-04: regression test for D-01 (per-tick merge contract).
        m1_count MUST stay 1 (one distinct M1, not 5 ticks).
        Volume MUST NOT accumulate (v=0 for tick-preview bars).
        """
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        # 5 ticks within the same M1 bucket, rising close
        for i in range(5):
            acc.update(
                "XAU/USD",
                _make_m1(
                    "XAU/USD", base_ms, 100, 100 + i, 100 - i, 100 + i * 0.5, v=0.0
                ),
            )

        # Get current state via one more update with new M1
        results = acc.update(
            "XAU/USD",
            _make_m1("XAU/USD", base_ms + 60000, 102, 103, 101, 102.5, v=0.0),
        )
        d1 = results[0]
        # After 5 ticks for first M1 + 1 new M1 = 2 distinct M1 bars
        assert d1.extensions["m1_count"] == 2

    def test_same_m1_dedup_preserves_ohlc(self):
        """Per-tick updates for same M1 correctly update h/low/c."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        # Tick 1: initial
        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 102, 98, 101, v=0.0))
        # Tick 2: higher high, same low
        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 98, 104, v=0.0))
        # Tick 3: lower low
        results = acc.update(
            "XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 96, 99, v=0.0)
        )

        d1 = results[0]
        assert d1.o == 100  # unchanged
        assert d1.h == 105  # max across ticks
        assert d1.low == 96  # min across ticks
        assert d1.c == 99  # latest close
        assert d1.extensions["m1_count"] == 1  # only 1 distinct M1
        assert d1.v == 0.0  # no volume accumulation

    def test_volume_not_inflated_by_ticks(self):
        """Volume must not accumulate when same M1 is fed multiple times.

        Even if v != 0 in the future, m1_count guards correctness.
        """
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        # Simulate tick-preview bars with v=10 (hypothetical future)
        for _ in range(10):
            acc.update(
                "XAU/USD",
                _make_m1("XAU/USD", base_ms, 100, 105, 99, 103, v=10.0),
            )

        results = acc.update(
            "XAU/USD",
            _make_m1("XAU/USD", base_ms + 60000, 103, 108, 101, 106, v=10.0),
        )
        d1 = results[0]
        # 10 ticks for M1#1 (10.0 once) + 1 new M1#2 (10.0) = 20.0 total
        assert d1.v == 20.0
        assert d1.extensions["m1_count"] == 2


# ---------------------------------------------------------------
# TickPreviewWorker: правило якоря від тіку до опублікованого HTF preview (ADR-0095 S4b)
# ---------------------------------------------------------------
def _make_tick(symbol, tick_ts_ms, mid):
    return {"v": 1, "symbol": symbol, "tick_ts_ms": tick_ts_ms, "mid": mid, "src": "test", "seq": 1}


def _make_htf_worker(symbols=("XAU/USD",), rules=None):
    uds = MagicMock()
    worker = TickPreviewWorker(
        uds=uds,
        tfs=[60, H4_S, D1_S],
        publish_min_interval_ms=0,
        curr_ttl_s=1800,
        symbols=list(symbols),
        channel="test:ticks",
        htf_preview_tfs=[H4_S, D1_S],
        htf_anchor_rules={"XAU/USD": FXCM} if rules is None else rules,
    )
    return worker, uds


def _last_published(uds, tf_s):
    bars = [c.args[0] for c in uds.publish_preview_bar.call_args_list if c.args[0].tf_s == tf_s]
    assert bars, "немає опублікованого preview tf_s=%d" % tf_s
    return bars[-1]


def test_htf_preview_rolls_grid_across_dst_weekend_without_restart():
    """Пт 06.03.2026 20:59 (зима) → Нд 08.03 21:00 (літо) одним воркером: preview H4/D1 переходить на літню сітку.

    Статичні якорі зі старту дали б у неділю H4 19:00 і D1 сб 07.03 22:00 до рестарту воркера.
    """
    worker, uds = _make_htf_worker()

    worker.on_tick(_make_tick("XAU/USD", _ms(2026, 3, 6, 20, 59, 30), 100.0))
    assert _last_published(uds, H4_S).open_time_ms == _ms(2026, 3, 6, 18)  # зимова сітка 22/02/../18
    assert _last_published(uds, D1_S).open_time_ms == _ms(2026, 3, 5, 22)

    worker.on_tick(_make_tick("XAU/USD", _ms(2026, 3, 8, 21, 0, 10), 200.0))
    h4 = _last_published(uds, H4_S)
    d1 = _last_published(uds, D1_S)
    assert h4.open_time_ms == _ms(2026, 3, 8, 21)
    assert d1.open_time_ms == _ms(2026, 3, 8, 21)
    assert h4.o == 200.0 and h4.extensions["m1_count"] == 1
    assert d1.o == 200.0 and d1.extensions["m1_count"] == 1

    # 22:00 — ще той самий літній H4 21:00 і D1, а не межа зимової сітки
    worker.on_tick(_make_tick("XAU/USD", _ms(2026, 3, 8, 22, 0, 10), 201.0))
    h4 = _last_published(uds, H4_S)
    d1 = _last_published(uds, D1_S)
    assert h4.open_time_ms == _ms(2026, 3, 8, 21) and h4.extensions["m1_count"] == 2
    assert d1.open_time_ms == _ms(2026, 3, 8, 21) and d1.extensions["m1_count"] == 2


def test_worker_htf_preview_without_rules_raises():
    """HTF preview без правила на кожен символ allowlist — відмова в конструкторі, до першого тіку."""
    with pytest.raises(ValueError, match="HTF_PREVIEW_ANCHOR_RULE_MISSING symbols=\\['XAU/USD'\\]"):
        _make_htf_worker(rules={})
    with pytest.raises(ValueError, match="HTF_PREVIEW_ANCHOR_RULE_MISSING symbols=\\['NAS100'\\]"):
        _make_htf_worker(symbols=("XAU/USD", "NAS100"))


def _cfg(groups, by_group):
    return {"market_calendar_symbol_groups": groups, "htf_anchor": {"rule_by_calendar_group": by_group}}


def test_build_htf_anchor_rules_per_symbol_group():
    cfg = _cfg(
        {"XAU/USD": "cfd_us_22_23", "BTCUSDT": "crypto_24x7"},
        {"cfd_us_22_23": FXCM, "crypto_24x7": RULE_UTC_MIDNIGHT},
    )
    rules = _build_htf_anchor_rules(cfg, [H4_S, D1_S], ["XAU/USD", "BTCUSDT"])
    assert rules == {"XAU/USD": FXCM, "BTCUSDT": RULE_UTC_MIDNIGHT}


def test_build_htf_anchor_rules_unmeasured_group_raises():
    cfg = _cfg({"XAU/USD": "cfd_us_22_23", "HKG33": "cfd_hk_main"}, {"cfd_us_22_23": FXCM})
    with pytest.raises(ValueError, match="HTF_ANCHOR_GROUP_UNMEASURED symbol=HKG33"):
        _build_htf_anchor_rules(cfg, [H4_S], ["XAU/USD", "HKG33"])


def test_build_htf_anchor_rules_without_htf_tfs_needs_no_section():
    """Preview без H4/D1 не потребує секції htf_anchor; з HTF — без секції гучна відмова."""
    assert _build_htf_anchor_rules({}, [], ["XAU/USD"]) == {}
    with pytest.raises(ValueError, match="CONFIG_HTF_ANCHOR_MISSING"):
        _build_htf_anchor_rules({}, [D1_S], ["XAU/USD"])


def test_main_wires_rules_after_calendar_filter_without_anchor_seconds():
    """Source-гейт main(): правила будуються після фільтра rejected-календарів; секунд якоря з config немає."""
    main_src = inspect.getsource(tick_preview_worker_module.main)
    assert main_src.index("resolve_symbol_calendars(") < main_src.index("_build_htf_anchor_rules(")
    assert "htf_anchor_rules=htf_anchor_rules" in main_src
    module_src = inspect.getsource(tick_preview_worker_module)
    for legacy in ("day_anchor_offset_s", "resolve_anchor_offset_ms", "anchor_offset_ms"):
        assert legacy not in module_src


class TestRunningBar:

    def test_merge_updates_hlcv(self):
        """merge() оновлює h, low, c, v, count."""
        m1 = _make_m1("X", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(0, 86400, m1)
        assert rb.count == 1

        m2 = _make_m1("X", 60000, 105, 115, 88, 112, 20)
        rb.merge(m2)
        assert rb.o == 100
        assert rb.h == 115
        assert rb.low == 88
        assert rb.c == 112
        assert rb.v == 30
        assert rb.count == 2

    def test_merge_no_change_when_inside(self):
        """merge() з баром всередині діапазону — h/low не змінюються."""
        m1 = _make_m1("X", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(0, 86400, m1)

        m2 = _make_m1("X", 60000, 102, 108, 92, 104, 5)
        rb.merge(m2)
        assert rb.h == 110
        assert rb.low == 90

    def test_update_forming_no_count_change(self):
        """update_forming() does not increment count or add volume."""
        m1 = _make_m1("X", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(0, 86400, m1)

        m2 = _make_m1("X", 0, 100, 115, 88, 112, 20)
        rb.update_forming(m2)
        assert rb.h == 115
        assert rb.low == 88
        assert rb.c == 112
        assert rb.v == 10  # unchanged
        assert rb.count == 1  # unchanged

    def test_to_candle_geometry(self):
        """to_candle() produces correct CandleBar with I2 compliant close_time_ms."""
        m1 = _make_m1("XAU/USD", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(1742169600000, 86400, m1)
        candle = rb.to_candle("XAU/USD")
        assert candle.tf_s == 86400
        assert candle.close_time_ms == candle.open_time_ms + 86400 * 1000
        assert candle.complete is False
        assert candle.src == "htf_preview"
