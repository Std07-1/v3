"""ADR-0103 §3.1: `d1_policy` — одне рішення для двох записувачів D1 (S7 і d1_native_settle); невалідне — відмова."""
from __future__ import annotations

import pytest

from core.config_loader import D1_SOURCE_DERIVED, D1_SOURCE_NATIVE, d1_policy, load_system_config, pick_config_path


def test_repo_config_owns_d1_by_broker_native():
    policy = d1_policy(load_system_config(pick_config_path()))
    assert (policy.source, policy.native, policy.native_settle_lag_h) == (D1_SOURCE_NATIVE, True, 6)


def test_absent_section_is_the_old_derived_policy_not_a_silent_native():
    policy = d1_policy({})
    assert (policy.source, policy.native) == (D1_SOURCE_DERIVED, False)


@pytest.mark.parametrize("raw", [
    "broker_native",
    {"source": "native"},
    {"source": "broker_native", "native_settle_lag_h": -1},
    {"source": "broker_native", "native_settle_lag_h": 6.5},
    {"source": "broker_native", "native_settle_lag_h": True},
])
def test_invalid_policy_is_refused(raw):
    with pytest.raises(ValueError, match="CONFIG_D1_POLICY_INVALID"):
        d1_policy({"d1_policy": raw})
