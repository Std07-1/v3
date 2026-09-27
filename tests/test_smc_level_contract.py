"""
tests/test_smc_level_contract.py — контракт рівня ADR-0104 §3.2 (слайс S1).

Специфікація:
  - SmcLevel без полів контракту видає на wire рівно старі id/kind/price/t_ms (зворотна сумісність);
  - key/family/state/tier видаються, коли задані; невідоме значення — гучна помилка;
  - make_level_key / level_period — формат "{series}:{side}:{symbol}:{period}" з ISO UTC хвилиною;
  - S6: словник Python (родини, стани, tier, поля wire) = типи ui_v4/src/types.ts.
"""

import pathlib
import re

import pytest

from core.smc.types import (
    LEVEL_CONTRACT_WIRE_FIELDS,
    LEVEL_FAMILIES,
    LEVEL_STATES,
    LEVEL_TIERS,
    SmcLevel,
    level_period,
    level_price_key,
    make_level_key,
)

_REPO = pathlib.Path(__file__).resolve().parents[1]
_TYPES_TS = _REPO / "ui_v4" / "src" / "types.ts"
_LEGACY_WIRE_FIELDS = {"id", "kind", "price", "t_ms"}
_SYMBOL = "XAU/USD"
_D1_S = 86400
_D1_OPEN_MS = 1_790_283_600_000  # 2026-09-24T21:00Z — відкриття торгової доби (17:00 NY, літо)


def _level(**contract) -> SmcLevel:
    return SmcLevel(id="pdh_XAU_USD_86400_265034", symbol=_SYMBOL, tf_s=_D1_S, kind="pdh", price=2650.34,
                    time_ms=_D1_OPEN_MS, touches=1, **contract)


# ── wire ──────────────────────────────────────────────────


def test_to_wire_without_contract_fields_keeps_legacy_shape():
    assert set(_level().to_wire()) == _LEGACY_WIRE_FIELDS


def test_to_wire_with_contract_fields_emits_them():
    wire = _level(key="d1:high:XAU/USD:2026-09-24T21:00Z", family="day", state="fixed", tier=1).to_wire()
    assert wire["key"] == "d1:high:XAU/USD:2026-09-24T21:00Z"
    assert (wire["family"], wire["state"], wire["tier"]) == ("day", "fixed", 1)
    assert set(wire) == _LEGACY_WIRE_FIELDS | set(LEVEL_CONTRACT_WIRE_FIELDS)


@pytest.mark.parametrize("contract", [{"family": "weekly"}, {"state": "live"}, {"tier": 0}, {"tier": 4}])
def test_level_with_unknown_contract_value_raises(contract):
    with pytest.raises(ValueError, match="LEVEL_CONTRACT_INVALID"):
        _level(**contract)


# ── key ───────────────────────────────────────────────────


def test_level_period_is_iso_utc_minute_of_period_start():
    assert level_period(_D1_OPEN_MS) == "2026-09-24T21:00Z"
    assert level_period(_D1_OPEN_MS + 59_999) == "2026-09-24T21:00Z"  # секунди не входять у період


def test_make_level_key_formats_series_side_symbol_period():
    assert make_level_key("london", "low", _SYMBOL, "2026-09-25T07:00Z") == "london:low:XAU/USD:2026-09-25T07:00Z"
    assert make_level_key("eq900", "high", "NAS100", str(level_price_key(21034.567))) == "eq900:high:NAS100:2103457"


def test_make_level_key_rejects_unknown_side():
    with pytest.raises(ValueError, match="LEVEL_KEY_SIDE_INVALID"):
        make_level_key("d1", "top", _SYMBOL, "2026-09-24T21:00Z")


# ── S6: Python ↔ ui_v4/src/types.ts ──────────────────────


def _ts_source() -> str:
    return _TYPES_TS.read_text(encoding="utf-8-sig")


def _ts_string_union(type_name: str) -> set:
    match = re.search(r"export type %s = ([^;]+);" % type_name, _ts_source())
    assert match, "types.ts: немає export type %s" % type_name
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _ts_smc_level_fields() -> dict:
    match = re.search(r"export interface SmcLevel \{(.*?)\n\}", _ts_source(), re.S)
    assert match, "types.ts: немає export interface SmcLevel"
    return dict(re.findall(r"^\s*(\w+)\??:\s*([^;]+);", match.group(1), re.M))


def test_ts_level_family_union_matches_python_vocabulary():
    assert _ts_string_union("LevelFamily") == set(LEVEL_FAMILIES)


def test_ts_level_state_union_matches_python_vocabulary():
    assert _ts_string_union("LevelState") == set(LEVEL_STATES)


def test_ts_smc_level_declares_every_wire_field():
    fields = _ts_smc_level_fields()
    assert _LEGACY_WIRE_FIELDS | set(LEVEL_CONTRACT_WIRE_FIELDS) <= set(fields)
    assert {int(t) for t in re.findall(r"\d+", fields["tier"])} == set(LEVEL_TIERS)
