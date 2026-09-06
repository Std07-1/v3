"""ADR-0054 P0.1a — derived-only guard: з брокера тягнемо тільки M1."""
from __future__ import annotations

import logging

import pytest

from core.derive import DERIVE_CHAIN, DERIVE_SOURCE
from tools.fetch_tf_backfill import DERIVED_ONLY_TFS


def test_guard_set_is_derived_chain_not_a_literal():
    """Множина = SSOT ланцюга; новий TF у DERIVE_CHAIN автоматично під guard."""
    assert DERIVED_ONLY_TFS == frozenset(DERIVE_SOURCE)
    assert DERIVED_ONLY_TFS == {180, 300, 900, 1800, 3600, 14400, 86400}


def test_m1_is_never_guarded():
    """M1 — єдине, що тягнемо з брокера; він source, а не target ланцюга."""
    assert 60 not in DERIVED_ONLY_TFS
    assert 60 in DERIVE_CHAIN


@pytest.mark.parametrize("tf_s", sorted(frozenset(DERIVE_SOURCE)))
def test_every_derived_tf_is_rejected_without_force(monkeypatch, caplog, tf_s):
    """Раніше guard закривав лише H4 — прямий D1-fetch дав anchor-інцидент."""
    import tools.fetch_tf_backfill as mod

    monkeypatch.setattr("sys.argv", ["fetch_tf_backfill", "--tf", str(tf_s), "--n", "10"])
    monkeypatch.setattr(mod, "load_env_secrets", lambda *a, **k: pytest.fail("guard не спрацював до I/O"))
    with caplog.at_level(logging.ERROR):
        assert mod.main() == 1
    assert "derived-only" in caplog.text
    assert str(DERIVE_SOURCE[tf_s][0]) in caplog.text, "у помилці має бути source-TF"


def test_force_flag_lets_derived_tf_through_to_io(monkeypatch):
    """--force-derived-tf лишається аварійним виходом: guard пропускає далі."""
    import tools.fetch_tf_backfill as mod

    monkeypatch.setattr("sys.argv", ["fetch_tf_backfill", "--tf", "14400", "--n", "10", "--force-derived-tf"])

    class _Stop(RuntimeError):
        pass

    def _boom(*_a, **_k):
        raise _Stop("дійшли до I/O — guard пропустив")

    monkeypatch.setattr(mod, "load_env_secrets", _boom)
    with pytest.raises(_Stop):
        mod.main()
