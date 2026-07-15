"""
tests/test_structure_forecast.py — ADR-0087: structure_imminent forecast.

Verifies:
  - derive_armed_levels: arming, consumption, re-arm, tie-break, unknown kinds
  - consistency of the replay against the REAL detector pipeline (D15.2 guard)
  - generate_imminent_conditions: CHoCH-first gating, arm radius, resolved
    params, max cap, bias gating (cold start = BOS only)
  - check_condition STRUCTURE_IMMINENT: `pending` and `mtf` triggers
"""

import json
from types import SimpleNamespace

from core.model.bars import CandleBar
from core.smc.structure import classify_swings, detect_structure_events
from core.smc.structure_forecast import (
    _CONSUMES,
    derive_armed_levels,
    generate_imminent_conditions,
)
from core.smc.swings import detect_raw_swings
from core.smc.types import SmcSwing, make_swing_id
from core.smc.wake_check import check_condition
from core.smc.wake_types import WakeCondition, WakeConditionKind

SYM = "XAU/USD"
TF = 900  # M15 — canonical target TF for CHoCH forecast
LEAD_TF = 300


def _swing(kind: str, price: float, time_ms: int, tf_s: int = TF) -> SmcSwing:
    return SmcSwing(
        id=make_swing_id(kind, SYM, tf_s, time_ms),
        symbol=SYM,
        tf_s=tf_s,
        kind=kind,
        price=price,
        time_ms=time_ms,
        confirmed=True,
    )


def _bar(
    open_ms: int, o: float, h: float, low: float, c: float, complete: bool = True
) -> CandleBar:
    return CandleBar(
        symbol=SYM,
        tf_s=TF,
        open_time_ms=open_ms,
        close_time_ms=open_ms + TF * 1000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=complete,
        src="test",
    )


def _snapshot(swings, trend_bias):
    """Duck-typed snapshot: forecast only reads .swings and .trend_bias."""
    return SimpleNamespace(swings=swings, trend_bias=trend_bias)


def _imminent_cond(**params) -> WakeCondition:
    base = {
        "tf_s": TF,
        "leading_tf_s": LEAD_TF,
        "target": "choch_bear",
        "level": 2000.0,
        "direction": "below",
        "protected_swing": "hl",
        "prox_atr": 1.0,
        "window_s": 1800,
        "atr_tf": 10.0,
    }
    base.update(params)
    return WakeCondition(
        kind=WakeConditionKind.STRUCTURE_IMMINENT,
        params=base,
        reason="test",
        source="platform",
    )


def _check(cond, price, ts_ms=1_000_000, events=None):
    return check_condition(
        cond,
        price=price,
        atr=10.0,
        session_info={},
        ts_ms=ts_ms,
        structure_events=events,
    )


# ── 1. derive_armed_levels: replay semantics ──────────────────────


class TestDeriveArmedLevels:
    def test_swing_arms_level(self):
        armed = derive_armed_levels([_swing("hh", 2000.0, 1000)])
        assert armed["hh"] is not None and armed["hh"].price == 2000.0
        assert armed["hl"] is None and armed["ll"] is None and armed["lh"] is None

    def test_event_consumes_armed_level(self):
        armed = derive_armed_levels(
            [_swing("hh", 2000.0, 1000), _swing("bos_bull", 2000.0, 2000)]
        )
        assert armed["hh"] is None

    def test_newer_swing_rearms_after_consumption(self):
        armed = derive_armed_levels(
            [
                _swing("hh", 2000.0, 1000),
                _swing("bos_bull", 2000.0, 2000),
                _swing("hh", 2020.0, 3000),
            ]
        )
        assert armed["hh"] is not None and armed["hh"].price == 2020.0

    def test_tie_break_swing_arms_before_event_consumes(self):
        # Equal time_ms, shuffled input order: swing must arm BEFORE the
        # event consumes (mirror structure.py:182-197)
        armed = derive_armed_levels(
            [_swing("bos_bull", 2000.0, 2000), _swing("hh", 2000.0, 2000)]
        )
        assert armed["hh"] is None

    def test_consumption_is_slot_specific(self):
        armed = derive_armed_levels(
            [
                _swing("hh", 2000.0, 1000),
                _swing("hl", 1990.0, 1500),
                _swing("choch_bear", 1990.0, 2000),
            ]
        )
        assert armed["hl"] is None
        assert armed["hh"] is not None  # untouched by choch_bear

    def test_unknown_kinds_ignored(self):
        armed = derive_armed_levels(
            [
                _swing("hl", 1990.0, 1000),
                _swing("fractal_high", 2005.0, 1100),
                _swing("inducement", 1995.0, 1200),
            ]
        )
        assert armed["hl"] is not None and armed["hl"].price == 1990.0

    def test_consistency_with_real_detector_pipeline(self):
        """D15.2 guard: replay agrees with detect_structure_events output.

        Uptrend then breakdown below HL → detector emits events; for every
        event, the consumed slot must be None or re-armed by a STRICTLY newer
        swing (tie loses to consumption).
        """
        prices = [
            # up-leg: HH forms
            (1000, 1005, 998, 1004),
            (1004, 1010, 1002, 1009),
            (1009, 1020, 1007, 1018),  # pivot high candidate
            (1018, 1016, 1010, 1012),
            (1012, 1014, 1006, 1008),  # pivot low candidate (HL)
            (1008, 1022, 1007, 1021),
            (1021, 1030, 1019, 1029),  # break above → BOS_BULL
            (1029, 1031, 1024, 1026),
            (1026, 1027, 1015, 1017),
            # breakdown through HL → CHoCH_BEAR
            (1017, 1018, 1002, 1003),
            (1003, 1006, 996, 998),
            (998, 1000, 990, 992),
        ]
        bars = [
            _bar(100_000 + i * TF * 1000, o, h, low, c)
            for i, (o, h, low, c) in enumerate(prices)
        ]
        classified = classify_swings(detect_raw_swings(bars, period=2))
        events, _bias, _, _ = detect_structure_events(classified, bars)
        assert events, "pipeline must emit structure events for this scenario"

        combined = sorted(classified + events, key=lambda s: s.time_ms)
        armed = derive_armed_levels(combined)
        for ev in events:
            slot = _CONSUMES[ev.kind]
            rearmed = armed[slot]
            assert rearmed is None or rearmed.time_ms > ev.time_ms, (
                f"slot {slot} must be consumed by {ev.kind} or re-armed by a "
                f"strictly newer swing"
            )


# ── 2. generate_imminent_conditions ───────────────────────────────


class TestGenerateImminentConditions:
    def _bullish_swings(self):
        return [
            _swing("hh", 2020.0, 1000),
            _swing("hl", 2000.0, 2000),
            _swing("bos_bull", 2015.0, 3000),  # establishes bullish bias
            _swing("hh", 2030.0, 4000),
            _swing("hl", 2005.0, 5000),
        ]

    def _generate(self, price, swings=None, bias="bullish", config=None):
        return generate_imminent_conditions(
            tf_pairs=[(LEAD_TF, TF)],
            snapshots={TF: _snapshot(swings or self._bullish_swings(), bias)},
            atr_by_tf={TF: 10.0},
            current_price=price,
            ts_ms=1_000_000,
            config=config,
        )

    def test_choch_condition_armed_near_protected_hl(self):
        conds = self._generate(price=2010.0)  # 0.5 ATR above HL 2005
        assert len(conds) == 1
        p = conds[0].params
        assert conds[0].kind == WakeConditionKind.STRUCTURE_IMMINENT
        assert p["target"] == "choch_bear"
        assert p["level"] == 2005.0
        assert p["direction"] == "below"
        assert p["protected_swing"] == "hl"
        assert p["leading_tf_s"] == LEAD_TF and p["tf_s"] == TF
        assert p["atr_tf"] == 10.0

    def test_bos_excluded_by_default_choch_first(self):
        # Price near HH 2030 — BOS target exists but default targets=("choch",)
        conds = self._generate(price=2028.0)
        targets = {c.params["target"] for c in conds}
        assert "bos_bull" not in targets

    def test_bos_included_when_configured(self):
        # price 2015: HH 2030 → 1.5 ATR, HL 2005 → 1.0 ATR — both in radius
        conds = self._generate(
            price=2015.0, config={"targets": ["choch", "bos"]}
        )
        targets = {c.params["target"] for c in conds}
        assert "bos_bull" in targets and "choch_bear" in targets

    def test_far_price_outside_arm_radius_no_condition(self):
        conds = self._generate(price=2029.0, config={"arm_radius_atr": 2.0})
        # HL=2005, dist=24 → 2.4 ATR > 2.0 → not armed
        assert conds == []

    def test_beyond_level_always_armable_for_pending(self):
        # Price already broke below HL 2005 — condition must exist so the
        # `pending` trigger can fire even far beyond arm radius semantics
        conds = self._generate(price=2004.0)
        assert len(conds) == 1
        assert conds[0].params["direction"] == "below"

    def test_cold_start_no_bias_no_choch_targets(self):
        swings = [_swing("hh", 2020.0, 1000), _swing("hl", 2000.0, 2000)]
        conds = self._generate(price=2002.0, swings=swings, bias=None)
        assert conds == []  # bias None → BOS only; default targets → choch

    def test_missing_atr_skips_pair(self):
        conds = generate_imminent_conditions(
            tf_pairs=[(LEAD_TF, TF)],
            snapshots={TF: _snapshot(self._bullish_swings(), "bullish")},
            atr_by_tf={},
            current_price=2010.0,
            ts_ms=1_000_000,
        )
        assert conds == []

    def test_max_conditions_cap_closest_first(self):
        conds = generate_imminent_conditions(
            tf_pairs=[(LEAD_TF, TF), (TF, 3600)],
            snapshots={
                TF: _snapshot(self._bullish_swings(), "bullish"),
                3600: _snapshot(
                    [
                        _swing("hh", 2040.0, 1000, tf_s=3600),
                        _swing("hl", 2018.0, 2000, tf_s=3600),
                        _swing("bos_bull", 2025.0, 3000, tf_s=3600),
                        _swing("hl", 2012.0, 5000, tf_s=3600),
                    ],
                    "bullish",
                ),
            },
            atr_by_tf={TF: 10.0, 3600: 20.0},
            current_price=2010.0,
            ts_ms=1_000_000,
            config={"max_conditions": 1},
        )
        assert len(conds) == 1
        # M15 HL 2005 is 0.5 ATR away; H1 HL 2012 is 0.1 ATR away → H1 wins
        assert conds[0].params["tf_s"] == 3600


# ── 3. check_condition: STRUCTURE_IMMINENT triggers ───────────────


class TestCheckImminent:
    def test_pending_below_fires_on_cross(self):
        cond = _imminent_cond(level=2005.0, direction="below")
        assert _check(cond, price=2004.5) is True

    def test_pending_above_fires_on_cross(self):
        cond = _imminent_cond(
            target="choch_bull", level=2010.0, direction="above"
        )
        assert _check(cond, price=2010.5) is True

    def test_mtf_fires_on_fresh_aligned_leading_event(self):
        cond = _imminent_cond(level=2005.0, direction="below")
        events = [
            {"tf_s": LEAD_TF, "type": "choch", "direction": "bearish",
             "ts_ms": 900_000, "price": 2008.0}
        ]
        # price 2009 → 0.4 ATR above level, within prox
        assert _check(cond, price=2009.0, events=events) is True

    def test_mtf_rejects_opposite_direction_event(self):
        cond = _imminent_cond(level=2005.0, direction="below")
        events = [
            {"tf_s": LEAD_TF, "type": "bos", "direction": "bullish",
             "ts_ms": 900_000, "price": 2008.0}
        ]
        assert _check(cond, price=2009.0, events=events) is False

    def test_mtf_rejects_stale_event(self):
        cond = _imminent_cond(level=2005.0, direction="below", window_s=600)
        stale_ts = 1_000_000 - 700 * 1000  # 700s ago > 600s window
        events = [
            {"tf_s": LEAD_TF, "type": "choch", "direction": "bearish",
             "ts_ms": stale_ts, "price": 2008.0}
        ]
        assert _check(cond, price=2009.0, events=events) is False

    def test_mtf_rejects_wrong_leading_tf(self):
        cond = _imminent_cond(level=2005.0, direction="below")
        events = [
            {"tf_s": 3600, "type": "choch", "direction": "bearish",
             "ts_ms": 900_000, "price": 2008.0}
        ]
        assert _check(cond, price=2009.0, events=events) is False

    def test_price_outside_prox_no_fire(self):
        cond = _imminent_cond(level=2005.0, direction="below", prox_atr=1.0)
        events = [
            {"tf_s": LEAD_TF, "type": "choch", "direction": "bearish",
             "ts_ms": 900_000, "price": 2008.0}
        ]
        # 1.5 ATR above level > prox 1.0
        assert _check(cond, price=2020.0, events=events) is False

    def test_no_events_no_mtf_fire(self):
        cond = _imminent_cond(level=2005.0, direction="below")
        assert _check(cond, price=2009.0, events=None) is False

    def test_invalid_params_never_fire(self):
        assert _check(_imminent_cond(level=0.0), price=2000.0) is False
        assert (
            _check(_imminent_cond(direction="sideways"), price=2000.0) is False
        )


# ── 4. WakeEngine publish: imminent НЕ голодує (ADR-0087 errata) ──


class _FakeRedis:
    """Capture LPUSH-ed events; minimal surface for WakeEngine."""

    def __init__(self):
        self.pushed = []

    def lpush(self, key, value):
        self.pushed.append((key, value))

    def ltrim(self, key, start, end):
        pass

    def get(self, key):
        return None


class _FakeSmc:
    """Minimal SmcRunner stub: one in-zone price + one crossed CHoCH level."""

    def __init__(self, price, swings, bias, structure_events=None):
        self._price = price
        self._swings = swings
        self._bias = bias
        self._structure_events = structure_events or []
        self._engine = SimpleNamespace(
            get_atr=lambda symbol, tf_s, period=14: 10.0,
            _session_windows=None,
            _config=None,
        )

    def get_last_price(self, symbol):
        return self._price

    def get_atr(self, symbol, tf_s, period=14):
        return 10.0

    def get_snapshot(self, symbol, tf_s):
        # auto_wake zone source: active zone AROUND price → zone_touch fires
        zone = SimpleNamespace(
            id="z1", high=self._price + 5, low=self._price - 5,
            kind="ob", status="active",
        )
        return SimpleNamespace(swings=[], zones=[zone], trend_bias="bullish")

    def get_raw_snapshot(self, symbol, tf_s):
        return SimpleNamespace(swings=self._swings, trend_bias=self._bias)

    def get_bias_map(self, symbol):
        return {}

    def get_zone_grades(self, symbol, tf_s):
        return None

    def get_recent_structure_events(self, symbol, since_ts_ms=0):
        return list(self._structure_events)

    def get_recent_bar_closes(self, symbol, since_ts_ms=0):
        return []


def _make_engine(fake_redis, fake_smc):
    import concurrent.futures

    from runtime.smc.wake_engine import WakeEngine

    return WakeEngine(
        redis_client=fake_redis,
        namespace="test_ns",
        executor=concurrent.futures.ThreadPoolExecutor(max_workers=1),
        smc_runner=fake_smc,
        symbols=[SYM],
        config={
            "wake_engine": {
                "event_cooldown_s": {
                    "price_zone_touch": 1800,
                    "structure_imminent": 900,
                    "_default": 600,
                },
                "structure_imminent": {
                    "enabled": True,
                    "targets": ["choch"],
                    "tf_pairs": [[300, 900]],
                    "prox_atr": 1.0,
                    "arm_radius_atr": 2.0,
                    "window_s": 1800,
                    "max_conditions": 4,
                },
            }
        },
    )


def _pending_scenario_swings(price):
    """Bullish M15 structure with protected HL ABOVE price → pending cross."""
    return [
        _swing("hh", price + 20, 1000),
        _swing("hl", price + 2, 2000),  # price вже НИЖЧЕ HL → pending
        _swing("bos_bull", price + 15, 3000),
        _swing("hl", price + 2, 4000),
    ]


class TestEnginePublishNoStarvation:
    """ADR-0087 errata: zone_touch у тому ж tick НЕ ковтає imminent."""

    def _run_tick(self, engine, ts_ms=1_000_000):
        import asyncio

        async def drive():
            loop = asyncio.get_running_loop()
            await engine._tick_symbol(SYM, ts_ms, loop)

        asyncio.run(drive())

    def _kinds(self, fake_redis):
        return [json.loads(v)["kind"] for _, v in fake_redis.pushed]

    def test_imminent_published_alongside_zone_touch(self):
        price = 2000.0
        r = _FakeRedis()
        engine = _make_engine(
            r, _FakeSmc(price, _pending_scenario_swings(price), "bullish")
        )
        self._run_tick(engine)
        kinds = self._kinds(r)
        assert "structure_imminent" in kinds, kinds
        assert "price_zone_touch" in kinds, kinds
        ev = next(
            json.loads(v) for _, v in r.pushed
            if json.loads(v)["kind"] == "structure_imminent"
        )
        fi = ev["meta"]["fired_imminent"][0]
        assert fi["target"] == "choch_bear"
        assert fi["phase"] == "pending"

    def test_imminent_survives_zone_cooldown_return(self):
        price = 2000.0
        r = _FakeRedis()
        engine = _make_engine(
            r, _FakeSmc(price, _pending_scenario_swings(price), "bullish")
        )
        ts = 1_000_000
        # zone_touch у свіжому cooldown + мала дельта ціни → general блок
        # робить early-return; imminent все одно має опублікуватись
        engine._event_dedup[f"{SYM}:price_zone_touch"] = (ts - 1000, price)
        self._run_tick(engine, ts_ms=ts)
        kinds = self._kinds(r)
        assert kinds == ["structure_imminent"], kinds

    def test_imminent_own_cooldown_suppresses_repeat(self):
        price = 2000.0
        r = _FakeRedis()
        engine = _make_engine(
            r, _FakeSmc(price, _pending_scenario_swings(price), "bullish")
        )
        self._run_tick(engine, ts_ms=1_000_000)
        n_after_first = self._kinds(r).count("structure_imminent")
        assert n_after_first == 1
        # другий tick через 10s — той самий (tf, direction, level, phase)
        self._run_tick(engine, ts_ms=1_010_000)
        assert self._kinds(r).count("structure_imminent") == 1
        assert engine._imminent_suppressed >= 1  # D-03: супресія полічена

    def test_mtf_then_pending_escalation_not_suppressed(self):
        """E2/D-01: pending-ескалація МАЄ пройти крізь cooldown mtf-події."""
        ts1 = 1_000_000
        # ціна НАД рівнем (у prox, не beyond) + свіжа aligned M5-подія → mtf
        level = 2002.0
        smc = _FakeSmc(
            level + 5.0,  # 0.5 ATR над HL → prox, не перетин
            _pending_scenario_swings(level - 2.0),  # HL = level
            "bullish",
            structure_events=[{
                "tf_s": 300, "type": "choch", "direction": "bearish",
                "ts_ms": ts1 - 60_000, "price": level + 3,
            }],
        )
        r = _FakeRedis()
        engine = _make_engine(r, smc)
        self._run_tick(engine, ts_ms=ts1)
        imminent = [
            json.loads(v) for _, v in r.pushed
            if json.loads(v)["kind"] == "structure_imminent"
        ]
        assert len(imminent) == 1
        assert imminent[0]["meta"]["fired_imminent"][0]["phase"] == "mtf"
        # через 60s (усередині cooldown 900s) ціна ПЕРЕТИНАЄ рівень → pending
        smc._price = level - 1.0
        self._run_tick(engine, ts_ms=ts1 + 60_000)
        imminent = [
            json.loads(v) for _, v in r.pushed
            if json.loads(v)["kind"] == "structure_imminent"
        ]
        phases = [e["meta"]["fired_imminent"][0]["phase"] for e in imminent]
        assert phases == ["mtf", "pending"], phases
