"""
tests/test_pd_state.py — compute_pd_state() + config split (ADR-0041 P1).

Перевіряє:
  1. PdState обчислюється правильно (premium/discount/eq).
  2. calc_enabled=False → None.
  3. Порожні swings → None.
  4. SH <= SL (invalid range) → None.
  5. Backward compat: old {"enabled": true} mapується на calc_enabled.
  6. Granular config keys парсяться коректно.
  7. PdState.to_wire() формат.
  8. Clamping: ціна вище range → 100%, нижче range → 0%.
"""

import pytest

from core.smc.config import SmcConfig, SmcPremiumDiscountConfig
from core.smc.premium_discount import compute_pd_state
from core.smc.types import PdState, SmcSwing


def _swing(kind: str, price: float, time_ms: int, confirmed: bool = True) -> SmcSwing:
    return SmcSwing(
        id=f"{kind}_TEST_900_{time_ms}",
        symbol="TEST",
        tf_s=900,
        kind=kind,
        price=price,
        time_ms=time_ms,
        confirmed=confirmed,
    )


def _cfg(**overrides) -> SmcConfig:
    pd_d = {"calc_enabled": True}
    pd_d.update(overrides)
    return SmcConfig(premium_discount=SmcPremiumDiscountConfig.from_dict(pd_d))


# ── Basic P/D computation ──


class TestComputePdState:
    def test_discount_position(self):
        """Ціна нижче equilibrium → DISCOUNT."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        result = compute_pd_state(swings, 1920.0, _cfg())
        assert result is not None
        assert result.label == "DISCOUNT"
        assert result.equilibrium == 1950.0
        assert result.pd_percent == pytest.approx(20.0, abs=0.1)
        assert result.range_high == 2000.0
        assert result.range_low == 1900.0
        assert result.current_price == 1920.0

    def test_premium_position(self):
        """Ціна вище equilibrium → PREMIUM."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        result = compute_pd_state(swings, 1985.0, _cfg())
        assert result is not None
        assert result.label == "PREMIUM"
        assert result.pd_percent == pytest.approx(85.0, abs=0.1)

    def test_eq_position(self):
        """Ціна ~50% → EQ."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        result = compute_pd_state(swings, 1950.0, _cfg())
        assert result is not None
        assert result.label == "EQ"
        assert result.pd_percent == pytest.approx(50.0, abs=0.1)

    def test_eq_boundary_low(self):
        """Ціна = 44.9% → DISCOUNT (нижче eq_low=45)."""
        swings = [_swing("hh", 2000.0, 100), _swing("hl", 1900.0, 200)]
        price = 1900.0 + 0.449 * 100.0  # 44.9%
        result = compute_pd_state(swings, price, _cfg())
        assert result is not None
        assert result.label == "DISCOUNT"

    def test_eq_boundary_high(self):
        """Ціна = 55.1% → PREMIUM (вище eq_high=55)."""
        swings = [_swing("lh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        price = 1900.0 + 0.551 * 100.0  # 55.1%
        result = compute_pd_state(swings, price, _cfg())
        assert result is not None
        assert result.label == "PREMIUM"

    def test_eq_inside_band(self):
        """Ціна = 50% → EQ (всередині eq_low..eq_high)."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        result = compute_pd_state(swings, 1950.0, _cfg())
        assert result is not None
        assert result.label == "EQ"

    def test_eq_at_low_edge(self):
        """Ціна = 45.0% (точно eq_low) → EQ."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        price = 1900.0 + 0.45 * 100.0  # exactly 45%
        result = compute_pd_state(swings, price, _cfg())
        assert result is not None
        assert result.label == "EQ"

    def test_eq_at_high_edge(self):
        """Ціна ~54.5% (чітко всередині eq band) → EQ."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        price = 1954.5  # 54.5% — clearly inside [45, 55]
        result = compute_pd_state(swings, price, _cfg())
        assert result is not None
        assert result.label == "EQ"

    def test_custom_eq_thresholds(self):
        """Config eq_low/eq_high змінює пороги — S5 SSOT."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        # 48% — default would be EQ, but with narrow band 48/52 → DISCOUNT
        price = 1900.0 + 0.47 * 100.0  # 47%
        result = compute_pd_state(swings, price, _cfg(eq_low=48, eq_high=52))
        assert result is not None
        assert result.label == "DISCOUNT"
        # Same price with default 45/55 → EQ
        result2 = compute_pd_state(swings, price, _cfg())
        assert result2 is not None
        assert result2.label == "EQ"

    def test_clamp_above_range(self):
        """Ціна вище range → 100%, PREMIUM."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        result = compute_pd_state(swings, 2100.0, _cfg())
        assert result is not None
        assert result.pd_percent == 100.0
        assert result.label == "PREMIUM"

    def test_clamp_below_range(self):
        """Ціна нижче range → 0%, DISCOUNT."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        result = compute_pd_state(swings, 1800.0, _cfg())
        assert result is not None
        assert result.pd_percent == 0.0
        assert result.label == "DISCOUNT"


# ── Guard conditions ──


class TestPdStateGuards:
    def test_calc_disabled(self):
        """calc_enabled=False → None."""
        swings = [_swing("hh", 2000.0, 100), _swing("ll", 1900.0, 200)]
        result = compute_pd_state(swings, 1950.0, _cfg(calc_enabled=False))
        assert result is None

    def test_empty_swings(self):
        """Порожній список → None."""
        result = compute_pd_state([], 1950.0, _cfg())
        assert result is None

    def test_no_high(self):
        """Тільки low swings → None."""
        swings = [_swing("ll", 1900.0, 200), _swing("hl", 1920.0, 300)]
        result = compute_pd_state(swings, 1950.0, _cfg())
        assert result is None

    def test_no_low(self):
        """Тільки high swings → None."""
        swings = [_swing("hh", 2000.0, 100), _swing("lh", 1980.0, 200)]
        result = compute_pd_state(swings, 1950.0, _cfg())
        assert result is None

    def test_invalid_range(self):
        """SH <= SL → None."""
        swings = [_swing("hh", 1900.0, 100), _swing("ll", 1900.0, 200)]
        result = compute_pd_state(swings, 1950.0, _cfg())
        assert result is None

    def test_unconfirmed_swings_skipped(self):
        """Unconfirmed swings ігноруються."""
        swings = [
            _swing("hh", 2100.0, 300, confirmed=False),
            _swing("hh", 2000.0, 100, confirmed=True),
            _swing("ll", 1900.0, 200, confirmed=True),
        ]
        result = compute_pd_state(swings, 1950.0, _cfg())
        assert result is not None
        assert result.range_high == 2000.0  # uses confirmed HH, not unconfirmed


# ── Config backward compatibility ──


class TestPdConfigCompat:
    def test_old_enabled_true(self):
        """Old {"enabled": true} → calc_enabled=True."""
        cfg = SmcPremiumDiscountConfig.from_dict({"enabled": True})
        assert cfg.calc_enabled is True
        assert cfg.enabled is True  # compat property

    def test_old_enabled_false(self):
        """Old {"enabled": false} → calc_enabled=False."""
        cfg = SmcPremiumDiscountConfig.from_dict({"enabled": False})
        assert cfg.calc_enabled is False
        assert cfg.enabled is False

    def test_new_granular_config(self):
        """New granular keys parsed correctly."""
        cfg = SmcPremiumDiscountConfig.from_dict(
            {
                "calc_enabled": True,
                "show_badge": False,
                "show_eq_line": True,
                "show_zones": True,
                "eq_pdh_coincidence_atr_mult": 0.3,
            }
        )
        assert cfg.calc_enabled is True
        assert cfg.show_badge is False
        assert cfg.show_eq_line is True
        assert cfg.show_zones is True
        assert cfg.eq_pdh_coincidence_atr_mult == pytest.approx(0.3)

    def test_new_overrides_old(self):
        """calc_enabled takes priority over old enabled."""
        cfg = SmcPremiumDiscountConfig.from_dict(
            {
                "enabled": False,
                "calc_enabled": True,
            }
        )
        assert cfg.calc_enabled is True

    def test_defaults(self):
        """Empty dict → safe defaults."""
        cfg = SmcPremiumDiscountConfig.from_dict({})
        assert cfg.calc_enabled is True
        assert cfg.show_badge is True
        assert cfg.show_eq_line is True
        assert cfg.show_zones is False
        assert cfg.eq_pdh_coincidence_atr_mult == 0.5
        assert cfg.eq_low == 45.0
        assert cfg.eq_high == 55.0

    def test_custom_eq_thresholds_config(self):
        """eq_low/eq_high parse from dict correctly."""
        cfg = SmcPremiumDiscountConfig.from_dict({"eq_low": 40, "eq_high": 60})
        assert cfg.eq_low == 40.0
        assert cfg.eq_high == 60.0

    def test_full_smc_config_from_dict(self):
        """SmcConfig.from_dict з new pd config."""
        raw = {
            "premium_discount": {
                "calc_enabled": True,
                "show_badge": True,
                "show_zones": False,
            }
        }
        cfg = SmcConfig.from_dict(raw)
        assert cfg.premium_discount.calc_enabled is True
        assert cfg.premium_discount.show_badge is True
        assert cfg.premium_discount.show_zones is False


# ── Wire format ──


class TestPdStateWire:
    def test_to_wire_format(self):
        state = PdState(
            range_high=2000.0,
            range_low=1900.0,
            equilibrium=1950.0,
            pd_percent=65.3,
            label="PREMIUM",
            current_price=1965.3,
        )
        wire = state.to_wire()
        assert wire == {
            "range_high": 2000.0,
            "range_low": 1900.0,
            "equilibrium": 1950.0,
            "pd_percent": 65.3,
            "label": "PREMIUM",
        }

    def test_to_wire_rounds_small_decimals(self):
        state = PdState(
            range_high=1.12345678,
            range_low=1.09876543,
            equilibrium=1.11111111,
            pd_percent=55.55555,
            label="PREMIUM",
            current_price=1.115,
        )
        wire = state.to_wire()
        assert wire["range_high"] == 1.12346  # 5 decimal places
        assert wire["range_low"] == 1.09877
        assert wire["pd_percent"] == 55.6  # 1 decimal place
