"""ADR-0103 S3a2: архів брокера для щоденного settle — чанки, гейт помилок за календарем, печатка meta.json, креди
з /proc сайдкара і watchdog на кожному виклику SDK (зависання get_history 24.09 — 13 хв)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

import pytest

from tools.repair import fetch_archive as fa

UTC = dt.timezone.utc


def _t(text):
    return fa.parse_utc(text)


def _ms(text):
    return fa.to_ms(_t(text))


def test_m1_chunks_overlap_by_an_hour_and_probe_friday_until_monday_open():
    chunks = fa.m1_chunks(_t("2026-09-24T12:00"), _t("2026-09-28T21:00"))
    labels = [(label, s.strftime("%a %d %H:%M"), e.strftime("%a %d %H:%M")) for label, s, e in chunks]
    assert labels[0] == ("day", "Thu 24 12:00", "Fri 25 01:00")
    assert ("day", "Fri 25 00:00", "Sat 26 01:00") in labels
    # зонд п'ятниці тягнеться до Пн 01:00 — лише тоді архів віддає хвилину закриття тижня Пт 20:44 (вимір 22.09)
    assert ("fri_probe", "Fri 25 18:00", "Mon 28 01:00") in labels
    assert labels[-1] == ("day", "Mon 28 00:00", "Mon 28 21:00")


def test_m1_friday_probe_is_clipped_to_the_window():
    chunks = fa.m1_chunks(_t("2026-09-25T12:00"), _t("2026-09-25T21:00"))
    assert [(label, s.strftime("%H:%M"), e.strftime("%H:%M")) for label, s, e in chunks] == [
        ("day", "12:00", "21:00"), ("fri_probe", "18:00", "21:00")]


def _bars(start, n, step_ms=60_000, price=100.0):
    return [(fa.to_ms(start) + i * step_ms, price, price + 1, price - 1, price + 0.5, 3.0) for i in range(n)]


def test_error_on_a_trading_chunk_fails_the_symbol_but_a_saturday_error_does_not(caplog):
    def fetch(symbol, tf_s, start, end):
        if start.weekday() == 5:  # субота: брокер відмовляє, торгових хвилин немає
            raise RuntimeError("no data")
        if start.day == 25 and start.hour == 0:
            raise RuntimeError("session lost")
        return _bars(start, 3)

    is_trading = lambda ms: dt.datetime.fromtimestamp(ms / 1000, UTC).weekday() < 5  # noqa: E731
    with caplog.at_level(logging.INFO):
        rows, meta = fa.fetch_m1(fetch, 1, "XAU/USD", _t("2026-09-24T00:00"), _t("2026-09-27T00:00"), is_trading)
    assert meta["errors_trading"] == 1 and meta["errors_nontrading"] == 1
    assert "FETCH_M1_CHUNK_ERROR symbol=XAU/USD day 2026-09-25T00:00" in caplog.text
    assert _ms("2026-09-24T00:00") in rows and meta["m1_rows"] == len(rows)


def test_transient_sdk_error_is_retried_within_attempts(monkeypatch):
    monkeypatch.setattr(fa, "RETRY_PAUSE_S", 0)
    calls = []

    def flaky(symbol, tf_s, start, end):
        calls.append(start)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return _bars(start, 2)

    got, error = fa.call_with_retries(flaky, 3, "XAU/USD", 60, _t("2026-09-24T00:00"), _t("2026-09-24T01:00"))
    assert error is None and len(got) == 2 and len(calls) == 2


def test_d1_walks_back_a_year_at_a_time_until_two_empty_years():
    first_bar = _t("2019-03-04T21:00")

    def fetch(symbol, tf_s, start, end):
        grid = _bars(start.replace(hour=21, minute=0) - dt.timedelta(days=1), 400, 86_400_000)
        return [b for b in grid if start <= dt.datetime.fromtimestamp(b[0] / 1000, UTC) < end
                and dt.datetime.fromtimestamp(b[0] / 1000, UTC) >= first_bar]

    rows, meta = fa.fetch_d1(fetch, 1, "NAS100", _t("2026-02-28T21:05"))
    assert meta["chunk_errors"] == 0 and meta["first"] == min(rows) == fa.to_ms(first_bar)
    assert [c["rows"] for c in meta["chunks"]][-2:] == [0, 0] and meta["chunks"][-1]["start"].startswith("2017-02-28")


def _daily_history(first_bar):
    def fetch(symbol, tf_s, start, end):
        grid = _bars(start.replace(hour=21, minute=0) - dt.timedelta(days=1), 400, 86_400_000)
        return [b for b in grid if start <= dt.datetime.fromtimestamp(b[0] / 1000, UTC) < end
                and dt.datetime.fromtimestamp(b[0] / 1000, UTC) >= first_bar]
    return fetch


def test_d1_history_older_than_1990_is_fetched_to_its_real_start():
    """Регресія 25.09: межа «рік > 1990 від --to» обрізала історію брокера — XAU мав 824 доби 1987–1990 без звірки,
    а найстаріша доба архіву зсувалась щодня. Кінець історії — два порожні роки, не календарна межа."""
    first_bar = _t("1987-06-11T21:00")
    rows, meta = fa.fetch_d1(_daily_history(first_bar), 1, "XAU/USD", _t("2026-09-25T21:01"))
    assert min(rows) == fa.to_ms(first_bar) and meta["stopped_by"] == "empty_years"
    assert meta["chunks"][-1]["start"].startswith("1984-09-25")


def test_d1_stops_at_the_safety_floor_loudly(caplog):
    first_bar = _t("1900-01-01T21:00")
    with caplog.at_level(logging.WARNING):
        rows, meta = fa.fetch_d1(_daily_history(first_bar), 1, "XAU/USD", _t("2026-09-25T21:01"))
    assert meta["stopped_by"] == "floor" and meta["chunks"][-1]["start"].startswith("1970-01-01")
    assert "FETCH_D1_HIT_FLOOR symbol=XAU/USD" in caplog.text


def test_d1_year_back_survives_leap_day():
    assert fa._year_back(_t("2028-02-29T21:00")) == _t("2027-02-28T21:00")


class _FakeProvider:
    def __init__(self, fail_symbol=None):
        self.fail_symbol, self.events = fail_symbol, []

    def __enter__(self):
        self.events.append("login")
        return self

    def __exit__(self, *exc):
        self.events.append("logout")

    def fetch_range_rows(self, symbol, tf_s, start, end):
        if symbol == self.fail_symbol:
            raise RuntimeError("history refused")
        return _bars(start, 5, 86_400_000 if tf_s == fa.D1_S else 60_000)


def _main(tmp_path, kind, provider, *extra):
    argv = [kind, "--out", str(tmp_path / "arch"), "--symbols", "XAU/USD", "NAS100", *extra]
    if kind == "m1":
        argv += ["--from", "2026-09-24T12:00", "--to", "2026-09-25T21:00"]
    else:
        argv += ["--to", "2026-09-25T21:05"]
    return fa.main(argv, provider_factory=lambda: provider)


@pytest.mark.parametrize("kind, suffix", [("m1", "m1"), ("d1", "d1_full")])
def test_clean_fetch_is_sealed_with_meta_in_the_consumer_format(tmp_path, monkeypatch, kind, suffix):
    monkeypatch.setattr(fa, "RETRY_PAUSE_S", 0)
    provider = _FakeProvider()
    assert _main(tmp_path, kind, provider) == 0
    out = tmp_path / "arch"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "PREVIOUS_CLOSE" and meta["kind"] == kind and meta["failed_symbols"] == []
    assert set(meta["symbols"]) == {"XAU_USD", "NAS100"} and provider.events == ["login", "logout"]
    rows = json.loads((out / ("XAU_USD_%s.json" % suffix)).read_text(encoding="utf-8"))
    assert rows == sorted(rows) and len(rows[0]) == 6
    assert not [p for p in os.listdir(out) if p.endswith(".tmp")]


@pytest.mark.parametrize("kind", ["m1", "d1"])
def test_failed_fetch_leaves_no_meta_json_seal(tmp_path, monkeypatch, kind):
    """Без meta.json архів не приймає ні settle_m1, ні d1_native_settle: неповна історія не стає «нативом»."""
    monkeypatch.setattr(fa, "RETRY_PAUSE_S", 0)
    assert _main(tmp_path, kind, _FakeProvider(fail_symbol="NAS100")) == fa.EXIT_FETCH_FAILED
    out = tmp_path / "arch"
    assert not (out / "meta.json").exists()
    assert json.loads((out / "meta.failed.json").read_text(encoding="utf-8"))["failed_symbols"] == ["NAS100"]


def test_non_empty_out_dir_is_refused(tmp_path):
    (tmp_path / "arch").mkdir()
    (tmp_path / "arch" / "meta.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        _main(tmp_path, "d1", _FakeProvider())


def test_every_sdk_call_runs_under_the_watchdog():
    """Зависання get_history (24.09 — 13 хв) мусить завершити процес кодом 75, тож кожен виклик — у enter/leave."""

    class Recorder:
        def __init__(self):
            self.log, self.inside = [], None

        def enter(self, label):
            self.inside = label
            self.log.append(("enter", label.split()[0]))

        def leave(self):
            self.log.append(("leave", self.inside.split()[0]))
            self.inside = None

    wd, provider = Recorder(), _FakeProvider()
    with fa.guarded_session(provider, wd) as fetch:
        fetch("XAU/USD", fa.D1_S, _t("2025-09-25T21:05"), _t("2026-09-25T21:05"))
    assert wd.log == [("enter", "login"), ("leave", "login"), ("enter", "fetch"), ("leave", "fetch"),
                      ("enter", "logout"), ("leave", "logout")]


def test_credentials_come_from_the_live_sidecar_environ_and_are_never_logged(tmp_path, monkeypatch, caplog):
    proc = tmp_path / "proc"
    for pid, argv, env in [
        ("101", b"bash\0-c\0sudo python -m tools.repair.fetch_archive", b"FXCM_PASSWORD=wrong\0"),
        ("202", b"/opt/smc-v3/.venv37/bin/python\0-m\0runtime.ingest.broker_sidecar",
         b"PATH=/usr/bin\0FXCM_USERNAME=u-secret\0FXCM_PASSWORD=p-secret\0FXCM_HOST_URL=http://h\0"),
    ]:
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cmdline").write_bytes(argv)
        (proc / pid / "environ").write_bytes(env)
    for key in ("FXCM_USERNAME", "FXCM_PASSWORD", "FXCM_HOST_URL"):
        monkeypatch.delenv(key, raising=False)
    with caplog.at_level(logging.INFO):
        assert fa.load_sidecar_credentials(str(proc)) == 3
    assert os.environ["FXCM_PASSWORD"] == "p-secret" and "PATH=/usr/bin" not in caplog.text
    assert "secret" not in caplog.text and "pid=202 keys=3" in caplog.text


def test_missing_sidecar_is_a_loud_refusal(tmp_path):
    (tmp_path / "proc").mkdir()
    with pytest.raises(RuntimeError, match="FETCH_CREDS_NOT_FOUND"):
        fa.load_sidecar_credentials(str(tmp_path / "proc"))
