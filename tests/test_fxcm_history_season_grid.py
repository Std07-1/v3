"""Нативні H4/D1 FXCM приймаються лише на сезонній сітці символу: рівність, а не набір якорів (ADR-0095 §3.3, S3c).

Раніше провайдер пропускав бар, якщо його якір збігався з будь-яким із {primary, alt, alt2} з config у будь-яку дату:
літній H4 на зимовій сітці (22:00) і D1 жовтня 2025 на 22:00 проходили мовчки. Тепер рядок поза сіткою відкидається,
а всі такі рядки виклику — один агрегований WARNING `FXCM_HISTORY_OFF_SEASON_GRID`. H4/D1 без правила —
`FXCM_HTF_ANCHOR_RULE_MISSING`, до запиту в SDK, а не тихий якір 0.
"""
from __future__ import annotations

import datetime as dt
import logging
import types

import pytest

from core.config_loader import htf_anchor_rule_resolver
from core.session_anchor import D1_S, H4_S, RULE_NY_CLOSE_US_DST, RULE_UTC_MIDNIGHT
from runtime.ingest.broker.fxcm import provider as provider_mod

_CFG = {
    "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": RULE_NY_CLOSE_US_DST}},
    "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23", "HKG33": "cfd_hk_main"},
}
_OFF_GRID_MARK = "FXCM_HISTORY_OFF_SEASON_GRID"
_PREVIOUS_CLOSE = object()


class _FakeForexConnect:
    calls: list = []
    rows: list = []  # що «брокер» віддає на get_history

    def login(self, *args, **kwargs):
        return None

    def logout(self):
        return None

    def get_history(self, *args, **kwargs):
        type(self).calls.append((args, kwargs))
        return list(type(self).rows)


@pytest.fixture()
def fake_sdk(monkeypatch):
    _FakeForexConnect.calls = []
    _FakeForexConnect.rows = []
    fxcorepy = types.SimpleNamespace(O2GCandleOpenPriceMode=types.SimpleNamespace(PREVIOUS_CLOSE=_PREVIOUS_CLOSE))
    monkeypatch.setattr(provider_mod, "ForexConnect", _FakeForexConnect)
    monkeypatch.setattr(provider_mod, "fxcorepy", fxcorepy)
    return _FakeForexConnect


def _utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def _ms(moment: dt.datetime) -> int:
    return int(moment.timestamp() * 1000)


def _row(moment: dt.datetime) -> dict:
    # open == close попереднього рядка: рейка ланцюжка PREVIOUS_CLOSE мовчить, у логах лише сітка
    return {"Date": moment, "BidOpen": 100.0, "BidHigh": 101.0, "BidLow": 99.0, "BidClose": 100.0, "Volume": 10.0}


def _off_grid_records(caplog) -> list:
    return [rec for rec in caplog.records if _OFF_GRID_MARK in rec.getMessage()]


def test_normalize_history_h4_summer_2200_rejected_loud(caplog):
    """Ср 01.07.2026 — літо США: H4 21/01/05/09/13/17 UTC. Рядки зимової сітки (18:00, 22:00, 02:00) відкинуто;
    сигнал — один WARNING на виклик з кількістю й першим відкиданням (очікуване відкриття, сезон), а не рядок на бар."""
    rows = [_row(_utc(2026, 7, 1, 17)), _row(_utc(2026, 7, 1, 18)), _row(_utc(2026, 7, 1, 21)),
            _row(_utc(2026, 7, 1, 22)), _row(_utc(2026, 7, 2, 2))]
    with caplog.at_level(logging.WARNING):
        bars = provider_mod.normalize_history_to_bars("XAU/USD", H4_S, rows, src="history",
                                                      anchor_rule=RULE_NY_CLOSE_US_DST)

    assert [b.open_time_ms for b in bars] == [_ms(_utc(2026, 7, 1, 17)), _ms(_utc(2026, 7, 1, 21))]
    record, = _off_grid_records(caplog)
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "symbol=XAU/USD tf_s=14400 dropped=3 of=5" in message
    assert "last_open_ms=%d" % _ms(_utc(2026, 7, 2, 2)) in message
    assert ("open_ms=%d expected_open_ms=%d" % (_ms(_utc(2026, 7, 1, 18)), _ms(_utc(2026, 7, 1, 17)))) in message
    assert "season=summer" in message
    assert "Пропуск history-row" not in caplog.text


def test_normalize_history_h4_summer_2100_accepted(caplog):
    """Сітка сезонна, а не одна з кількох: 21:00/01:00 улітку і 22:00/02:00 узимку — усе на сітці, без сигналу.
    close = open + tf (I2)."""
    opens = [_utc(2026, 7, 1, 21), _utc(2026, 7, 2, 1), _utc(2026, 1, 5, 22), _utc(2026, 1, 6, 2)]
    with caplog.at_level(logging.WARNING):
        bars = provider_mod.normalize_history_to_bars("XAU/USD", H4_S, [_row(m) for m in opens], src="history",
                                                      anchor_rule=RULE_NY_CLOSE_US_DST)

    assert [b.open_time_ms for b in bars] == sorted(_ms(m) for m in opens)
    assert all(b.close_time_ms == b.open_time_ms + H4_S * 1000 for b in bars)
    assert _off_grid_records(caplog) == []
    assert "Пропуск history-row" not in caplog.text


def test_normalize_history_d1_oct2025_2200_rejected(caplog):
    """Інцидент ADR-0095: 13 D1 XAU жовтня 2025 на 22:00 пройшли набір якорів. Ср 15.10.2025 — ще літо, D1 = 21:00;
    після 02.11.2025 D1 = 22:00 (Ср 05.11.2025) приймається."""
    rows = [_row(_utc(2025, 10, 15, 21)), _row(_utc(2025, 10, 15, 22)), _row(_utc(2025, 11, 5, 22))]
    with caplog.at_level(logging.WARNING):
        bars = provider_mod.normalize_history_to_bars("XAU/USD", D1_S, rows, src="history",
                                                      anchor_rule=RULE_NY_CLOSE_US_DST)

    assert [b.open_time_ms for b in bars] == [_ms(_utc(2025, 10, 15, 21)), _ms(_utc(2025, 11, 5, 22))]
    record, = _off_grid_records(caplog)
    assert "dropped=1 of=3" in record.getMessage()
    assert "expected_open_ms=%d" % _ms(_utc(2025, 10, 15, 21)) in record.getMessage()


def test_normalize_history_htf_without_rule_raises():
    """Чиста функція теж не перевіряє H4/D1 тихим якорем 0: без правила (або з невідомим) — гучна відмова."""
    rows = [_row(_utc(2026, 7, 1, 21))]
    with pytest.raises(ValueError, match="FXCM_HTF_ANCHOR_RULE_MISSING"):
        provider_mod.normalize_history_to_bars("XAU/USD", H4_S, rows, src="history")
    with pytest.raises(ValueError, match="FXCM_HTF_ANCHOR_RULE_MISSING"):
        provider_mod.normalize_history_to_bars("XAU/USD", D1_S, rows, src="history", anchor_rule="tv_anchor")


@pytest.mark.parametrize("tf_s", [H4_S, D1_S])
@pytest.mark.parametrize("resolver, symbol", [(None, "XAU/USD"), (htf_anchor_rule_resolver(_CFG), "HKG33")],
                         ids=["no_resolver", "unmeasured_group"])
def test_fetch_last_n_tf_htf_without_resolver_raises(fake_sdk, tf_s, resolver, symbol):
    """H4/D1 без правила символу — FXCM_HTF_ANCHOR_RULE_MISSING до запиту в SDK: і без резолвера, і коли група
    календаря символу без виміряної сітки. M1..H1 резолвера не потребують (сайдкар, полер)."""
    with provider_mod.FxcmHistoryProvider(user_id="u", password="p", url="x", connection="Demo",
                                          anchor_rule_for_symbol=resolver) as provider:
        with pytest.raises(ValueError, match="FXCM_HTF_ANCHOR_RULE_MISSING symbol=%s tf_s=%d" % (symbol, tf_s)):
            provider.fetch_last_n_tf(symbol, tf_s=tf_s, n=5)
        assert fake_sdk.calls == []

        assert provider.fetch_last_n_tf(symbol, tf_s=3600, n=5) == []
        assert provider.fetch_last_n_m1(symbol, n=5) == []
    assert len(fake_sdk.calls) == 2


@pytest.mark.parametrize("rule, resolver, kept, dropped, expected_open", [
    # ny_close_us_dst з config-резолвера: літня сітка 21/01/05/.. — 21:00 лишається, 00:00 належить бакету 21:00
    (RULE_NY_CLOSE_US_DST, htf_anchor_rule_resolver(_CFG), _utc(2026, 7, 1, 21), _utc(2026, 7, 2, 0),
     _utc(2026, 7, 1, 21)),
    # ті самі рядки, інше правило — інший бар лишається: правило справді приходить від резолвера, а не з default
    (RULE_UTC_MIDNIGHT, lambda symbol: RULE_UTC_MIDNIGHT, _utc(2026, 7, 2, 0), _utc(2026, 7, 1, 21),
     _utc(2026, 7, 1, 20)),
], ids=["ny_close_us_dst", "utc_midnight"])
def test_fetch_last_n_tf_passes_resolved_rule_to_normalize(fake_sdk, caplog, rule, resolver, kept, dropped,
                                                           expected_open):
    """fetch_last_n_tf (H4, непорожня відповідь SDK) віддає в normalize_history_to_bars правило символу з резолвера:
    рядок на сітці лишається, рядок поза нею відкинуто з агрегованим WARNING (S3c, W1fix)."""
    asked: list = []

    def spy(symbol: str) -> str:
        asked.append(symbol)
        return resolver(symbol)

    fake_sdk.rows = [_row(_utc(2026, 7, 1, 21)), _row(_utc(2026, 7, 2, 0))]
    with provider_mod.FxcmHistoryProvider(user_id="u", password="p", url="x", connection="Demo",
                                          anchor_rule_for_symbol=spy) as provider:
        with caplog.at_level(logging.WARNING):
            bars = provider.fetch_last_n_tf("XAU/USD", tf_s=H4_S, n=2)

    assert asked == ["XAU/USD"]
    (args, _kwargs), = fake_sdk.calls
    assert (args[0], args[1], args[4]) == ("XAU/USD", "H4", 2)
    assert [b.open_time_ms for b in bars] == [_ms(kept)]
    assert all(b.src == "history" and b.close_time_ms == b.open_time_ms + H4_S * 1000 for b in bars)
    record, = _off_grid_records(caplog)
    message = record.getMessage()
    assert "symbol=XAU/USD tf_s=14400 dropped=1 of=2" in message
    assert "open_ms=%d expected_open_ms=%d rule=%s" % (_ms(dropped), _ms(expected_open), rule) in message
