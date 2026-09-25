"""ADR-0103 §3.2/S3: `m1_settle` — вимикач нічного прогону і лаг ревізій брокера по символу; невалідне — відмова.

Settle переписує SSOT значеннями брокера: хвилину, яку брокер ще ревізує, брати не можна, тож лаг «за
замовчуванням» гірший за відмову (EU CFD правлять бари віком 6.5–10 год — лаг метала їх зіпсував би).
"""
from __future__ import annotations

import copy

import pytest

from core.config_loader import load_system_config, m1_settle_policy, pick_config_path


def _repo_cfg():
    return load_system_config(pick_config_path())


def test_repo_config_keeps_the_nightly_run_off_until_owner_go():
    assert m1_settle_policy(_repo_cfg()).schedule_enabled is False


def test_repo_config_gives_every_symbol_its_group_revision_lag():
    cfg = _repo_cfg()
    policy = m1_settle_policy(cfg)
    assert set(policy.lag_h_by_symbol) == set(cfg["symbols"])
    assert policy.lag_h_by_symbol["XAU/USD"] == 6 and policy.lag_h_by_symbol["NAS100"] == 6
    assert policy.lag_h_by_symbol["EUSTX50"] == 12 and policy.lag_h_by_symbol["GER30"] == 12


def test_symbol_whose_group_has_no_measured_lag_is_refused():
    cfg = copy.deepcopy(_repo_cfg())
    del cfg["m1_settle"]["revision_lag_h_by_group"]["cfd_eu_ger30"]
    with pytest.raises(ValueError, match="CONFIG_M1_SETTLE_INVALID symbol=GER30"):
        m1_settle_policy(cfg)


def test_absent_section_is_refused_not_defaulted():
    with pytest.raises(ValueError, match="CONFIG_M1_SETTLE_INVALID"):
        m1_settle_policy({"symbols": ["XAU/USD"]})


@pytest.mark.parametrize("field, value", [
    ("schedule_enabled", "false"),
    ("schedule_enabled", None),
    ("lookback_h", 0),
    ("fetch_call_timeout_s", 12.5),
    ("fetch_attempts", True),
    ("deadline_guard_min", -1),
    ("backups_keep", 0),
    ("work_dir", ""),
])
def test_invalid_field_is_refused(field, value):
    cfg = copy.deepcopy(_repo_cfg())
    cfg["m1_settle"][field] = value
    with pytest.raises(ValueError, match="CONFIG_M1_SETTLE_INVALID %s=" % field):
        m1_settle_policy(cfg)
