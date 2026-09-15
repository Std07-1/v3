"""Фаза fetch під рейками — жодного виклику брокера там, де його не має бути (ADR-0096 §3.3 B, слайси B3 і B9).

Навіщо. Перезабір іде в живу FXCM-сесію того самого акаунта, а `get_history` — синхронний нативний виклик без
таймауту (історія зависань sidecar, ADR-0054 §3.6). Кожна рейка тут — окремий спосіб зашкодити: виклик при
відкритому ринку, спільний кеш SDK з live-сайдкаром, безліміт логінів, тиха заміна валідної доби staging
невдалим перезабором, дитина, що пережила батька з відкритою сесією. Одна дитина = одна сесія FXCM на пакет діб
(власник: ~1000 логінів за вихідні неприйнятні). Дитина-процес підмінена фейком, що пише теку сесії так само, як
справжня (`fetch_child.main` перевірено окремо з фейковим провайдером); таймаут — на справжньому процесі.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ft_m1_support import (
    SYMBOL, at, fetch_meta, line, make_cfg, raw_row, signal_during_final_write, ssot_bar, staged_row, write_part,
)
from tools.repair.first_tick_m1 import common, fetch_call, fetch_child, fetch_runner
from tools.repair.first_tick_m1.fetch import FetchDeps, FetchOptions, run_fetch
from tools.repair.first_tick_m1.fetch_runner import ChildOutcome, run_child
from tools.repair.first_tick_m1.staging import day_paths, load_day, rows_bytes, write_day_atomic

DAYS = [dt.date(2026, 7, 20) + dt.timedelta(days=i) for i in range(6)]  # Пн 20.07 … Сб 25.07
SATURDAY_NOON = at(dt.date(2026, 9, 12), 12, 0)
SUNDAY_OPEN = at(dt.date(2026, 9, 13), 22, 0)
SDK = {"python": "3.7.0", "forexconnect": "1.6.43"}
SIGALRM_RC = -14  # Popen.returncode дитини, убитої SIGALRM (Linux); на Windows підставляється в fetch_call


class Clock:
    def __init__(self, now_ms):
        self.now = now_ms
        self.sleeps = []

    def now_ms(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += int(seconds * 1000)


class FakeChild:
    """Сесія за контрактом `fetch_child`: started/result/рядки кожної доби і session.result.json.

    Поведінка доби: ok; empty; invalid (дитина сама відхилила рядки); flag_mismatch (дитина пише ok — батько
    перевалідовує і відхиляє); no_result (вийшла 0 без result доби); sdk_error (get_history відмовив — result
    error, сесію перервано rc 11); deadline (дедлайн усередині дитини вбив процес посеред доби).
    `login_error` — логін відмовив, жодна доба не почата. `logout` — після останньої доби: "error" (виняток
    логауту, rc 11) або "deadline" (SIGALRM дитини посеред логауту, session.result не записано).
    """

    def __init__(self, clock, behaviour=None, advance_ms=0, login_error=False, logout=None):
        self.clock, self.behaviour, self.advance_ms, self.login_error = clock, behaviour or {}, advance_ms, login_error
        self.logout = logout
        self.calls = []

    def __call__(self, argv, cwd, env, timeout_s, log_path):
        args = dict(zip(argv[4::2], argv[5::2]))
        assert argv[1:4] == ["-u", "-m", "tools.repair.first_tick_m1.fetch_child"]
        assert os.path.isdir(cwd) and os.listdir(cwd) == []
        assert env["PYTHONPATH"].split(os.pathsep)[0] == str(common.REPO_ROOT)
        keys, out_dir, deadline_s = args["--days"].split(","), args["--out-dir"], int(args["--deadline-s"])
        assert os.path.isdir(out_dir) and os.listdir(out_dir) == []
        # Дедлайн кожного кроку — у дитини; таймаут батька покриває логін, get_history кожної доби і логаут.
        assert timeout_s == common.session_timeout_s(deadline_s, len(keys))
        self.calls.append({"cwd": cwd, "days": keys, "timeout_s": timeout_s, "deadline_s": deadline_s})
        session = {"status": "error", "stage": "login", "days": keys, "completed": 0, "sdk": SDK, "error": None,
                   "duration_s": 0.1}
        if self.login_error:
            session["error"] = "RuntimeError: login failed"
            common.write_json_atomic(os.path.join(out_dir, fetch_child.SESSION_RESULT), session)
            return ChildOutcome("exited", fetch_child.EXIT_SDK_ERROR, 0.1)
        for key in keys:
            self.clock.now += self.advance_ms
            started, rows_path, result_path = fetch_child.day_files(out_dir, key)
            Path(started).write_bytes(b"")
            kind = self.behaviour.get(key, "ok")
            if kind == "deadline":
                return ChildOutcome("exited", SIGALRM_RC, 180.0)
            if kind == "no_result":
                break
            result = self._day(key, kind, rows_path)
            common.write_json_atomic(result_path, result)
            if kind == "sdk_error":
                session.update(stage="get_history:" + key, error=result["error"])
                common.write_json_atomic(os.path.join(out_dir, fetch_child.SESSION_RESULT), session)
                return ChildOutcome("exited", fetch_child.EXIT_SDK_ERROR, 0.1)
        if self.logout == "deadline":
            return ChildOutcome("exited", SIGALRM_RC, 180.0)
        session.update(stage="logout", completed=len(keys))
        if self.logout == "error":
            session["error"] = "RuntimeError: logout failed"
            common.write_json_atomic(os.path.join(out_dir, fetch_child.SESSION_RESULT), session)
            return ChildOutcome("exited", fetch_child.EXIT_SDK_ERROR, 0.1)
        session["status"] = "ok"
        common.write_json_atomic(os.path.join(out_dir, fetch_child.SESSION_RESULT), session)
        return ChildOutcome("exited", 0, 0.1)

    @staticmethod
    def _day(key, kind, rows_path):
        day = common.parse_day_key(key)
        rows = [staged_row(common.day_start_ms(day) + (22 * 60 + i) * 60_000, 4089.98 + i, 4093.19 + i,
                           4086.33 + i, 4092.36 + i) for i in range(3)]
        result = {"status": "ok", "day": key, "rows": 3, "rows_outside_day_dropped": 0, "raw_open_not_tick": 0,
                  "request": common.request_window(day), "call_duration_s": 0.1, "sdk": SDK, "error": None}
        if kind in ("empty", "invalid", "sdk_error"):
            result.update(status={"sdk_error": "error"}.get(kind, kind), rows=0, error="boom")
            return result
        if kind == "flag_mismatch":
            rows[1]["raw_open_not_tick"] = True
            result["raw_open_not_tick"] = 1
        Path(rows_path).write_bytes(rows_bytes(rows))
        return result


@pytest.fixture()
def env(tmp_path):
    data = tmp_path / "data"
    for day in DAYS[:5]:
        write_part(data, day, [line(ssot_bar(at(day, 12, 0), 1.0, 2.0, 0.5, 1.5))])
    work = tmp_path / "work"
    work.mkdir()
    return {"tmp": tmp_path, "data": data, "staging": tmp_path / "staging", "sdk": tmp_path / "sdk", "work": work}


@pytest.fixture()
def sigalrm_rc(monkeypatch):
    """Код дитини, убитої власним дедлайном, — однаково на Windows (де SIGALRM немає) і POSIX."""
    monkeypatch.setattr(fetch_call, "_DEADLINE_RETURNCODE", SIGALRM_RC)


def _opts(env, days=DAYS[:5], **kw):
    base = dict(symbol=SYMBOL, day_from=days[0], day_to=days[-1], staging_root=str(env["staging"]),
                sdk_cwd=str(env["sdk"]))
    base.update(kw)
    return FetchOptions(**base)


def _deps(env, clock, child, credentials=True, cwd=None, data_root=None):
    return FetchDeps(now_ms=clock.now_ms, sleep=clock.sleep, run_child=child,
                     load_cfg=lambda: make_cfg(str(data_root or env["data"])),
                     getcwd=lambda: str(cwd or env["work"]), env_has_credentials=lambda: credentials)


def _run_manifest(env):
    (path,) = (env["staging"] / "_runs").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _keys(days):
    return [common.day_key(day) for day in days]


def test_market_open_now_refused_before_any_call(env):
    clock = Clock(at(dt.date(2026, 9, 14), 10, 0))  # понеділок, торгова година
    child = FakeChild(clock)
    assert run_fetch(_opts(env), _deps(env, clock, child)) == 3
    assert child.calls == []
    assert "FT_FETCH_REFUSED_MARKET_OPEN" in _run_manifest(env)["stop_reason"]


def test_market_window_covers_whole_session(env):
    """Ловить перевірку лише «зараз» і вікно на одну добу: сесія з 5 діб — логін + 5 get_history + логаут по 3 хв
    = 21 хв + guard 60 → відкриття через 75 хв уже заборонене; та сама мить з сесією на 1 добу (9 + 60) — дозволена."""
    opts = dict(call_timeout_s=180, guard_minutes=60)
    clock = Clock(SUNDAY_OPEN - 75 * 60_000)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days_per_session=5, **opts), _deps(env, clock, child)) == 3
    assert child.calls == []
    control_clock = Clock(SUNDAY_OPEN - 75 * 60_000)
    control = FakeChild(control_clock)
    assert run_fetch(_opts(env, days=DAYS[:1], days_per_session=5, **opts), _deps(env, control_clock, control)) == 0
    assert [call["days"] for call in control.calls] == [_keys(DAYS[:1])]


def test_market_opening_mid_run_stops_before_next_session(env):
    """Кожна доба «триває» 90 хв: з 16:00 неділі четверта сесія о 20:30 ще дозволена, п'ята о 22:00 — ні."""
    clock = Clock(at(dt.date(2026, 9, 13), 16, 0))
    child = FakeChild(clock, advance_ms=90 * 60_000)
    assert run_fetch(_opts(env, days_per_session=1), _deps(env, clock, child)) == 3
    assert len(child.calls) == 4
    run = _run_manifest(env)
    assert run["rc"] == 3 and "FT_FETCH_REFUSED_MARKET_OPEN" in run["stop_reason"]
    assert [c["status"] for c in run["calls"]] == ["ok"] * 4


def test_one_login_per_batch_of_days(tmp_path):
    """Ловить логін на кожну добу: 10 діб при --days-per-session 7 — рівно дві сесії (7 + 3), пауза лише між ними."""
    days = [dt.date(2026, 7, 6) + dt.timedelta(days=i) for i in range(10)]
    for day in days:
        write_part(tmp_path / "data", day, [line(ssot_bar(at(day, 12, 0), 1.0, 2.0, 0.5, 1.5))])
    (tmp_path / "work").mkdir()
    ten = {"tmp": tmp_path, "data": tmp_path / "data", "staging": tmp_path / "staging", "sdk": tmp_path / "sdk",
           "work": tmp_path / "work"}
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(ten, days=days, days_per_session=7), _deps(ten, clock, child)) == 0
    assert [call["days"] for call in child.calls] == [_keys(days[:7]), _keys(days[7:])]
    assert clock.sleeps == [common.CALL_INTERVAL_DEFAULT_S]
    run = _run_manifest(ten)
    assert [(s["seq"], s["status"], len(s["days"])) for s in run["sessions"]] == [(1, "ok", 7), (2, "ok", 3)]
    assert [(c["seq"], c["session"]) for c in run["calls"]] == [(i + 1, 1 if i < 7 else 2) for i in range(10)]
    assert all(load_day(ten["staging"], SYMBOL, day) is not None for day in days)


@pytest.mark.parametrize("days_per_session", [0, common.DAYS_PER_SESSION_RANGE[1] + 1])
def test_days_per_session_out_of_range_refused_rc2(env, days_per_session):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days_per_session=days_per_session), _deps(env, clock, child)) == 2
    assert child.calls == [] and not env["staging"].exists()


def test_killed_mid_day_next_session_starts_from_next_day(env, sigalrm_rc):
    """Дедлайн дитини вбив процес на третій добі: вона невдала (deadline), дві перші закомічені, нова сесія
    починає з четвертої — ні зависла доба повторно, ні вже закомічені доби не забираються вдруге."""
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock, behaviour={common.day_key(DAYS[2]): "deadline"})
    assert run_fetch(_opts(env), _deps(env, clock, child)) == 1
    assert [call["days"] for call in child.calls] == [_keys(DAYS[:5]), _keys(DAYS[3:5])]
    run = _run_manifest(env)
    assert [(c["day"], c["status"], c["session"]) for c in run["calls"]] == [
        ("20260720", "ok", 1), ("20260721", "ok", 1), ("20260722", "deadline", 1), ("20260723", "ok", 2),
        ("20260724", "ok", 2)]
    assert (run["sessions"][0]["status"], run["sessions"][0]["unattempted"]) == ("deadline", _keys(DAYS[3:5]))
    assert load_day(env["staging"], SYMBOL, DAYS[2]) is None and clock.sleeps == [common.CALL_INTERVAL_DEFAULT_S]


def test_login_failure_retries_same_batch_until_failure_limit(env):
    """Логін відмовляє щоразу: жодна доба не «згоряє», сесії повторюють той самий пакет, зупинка — ліміт відмов."""
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock, login_error=True)
    assert run_fetch(_opts(env, max_consecutive_failures=3), _deps(env, clock, child)) == 3
    assert [call["days"] for call in child.calls] == [_keys(DAYS[:5])] * 3
    run = _run_manifest(env)
    assert run["calls"] == [] and "FT_FETCH_TOO_MANY_SESSION_FAILURES in_row=3" in run["stop_reason"]
    assert [s["unattempted"] for s in run["sessions"]] == [_keys(DAYS[:5])] * 3
    assert "login" in run["sessions"][0]["detail"]


def test_failing_login_capped_by_max_logins_even_with_budget_left(env):
    """Ловить логіни поза бюджетом: сесія без жодної доби не витрачає --max-calls, тож при --max-calls 1 логін, що
    відмовляє щоразу, повторювався до ліміту відмов поспіль. Дефолт --max-logins = ⌈1 / 7⌉ = 1 — рівно один логін."""
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock, login_error=True)
    assert run_fetch(_opts(env, max_calls=1), _deps(env, clock, child)) == 3
    assert len(child.calls) == 1
    run = _run_manifest(env)
    assert "FT_FETCH_MAX_LOGINS_REACHED logins=1" in run["stop_reason"] and run["rails"]["max_logins"] == 1
    explicit_clock = Clock(SATURDAY_NOON)
    explicit = FakeChild(explicit_clock, login_error=True)
    shutil.rmtree(env["staging"])
    assert run_fetch(_opts(env, max_logins=2, max_consecutive_failures=10), _deps(env, explicit_clock, explicit)) == 3
    assert len(explicit.calls) == 2


@pytest.mark.parametrize("field, value", [("max_logins", 0), ("max_logins", common.MAX_LOGINS_CEILING + 1),
                                          ("max_consecutive_failures", 0),
                                          ("max_consecutive_failures", common.MAX_CONSECUTIVE_FAILURES_RANGE[1] + 1)])
def test_login_and_failure_limits_over_ceiling_refused_rc2(env, field, value):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, **{field: value}), _deps(env, clock, child)) == 2
    assert child.calls == [] and not env["staging"].exists()


def test_derived_max_logins_capped_at_ceiling(env, capsys):
    clock = Clock(SATURDAY_NOON)
    opts = _opts(env, max_calls=common.MAX_CALLS_CEILING, days_per_session=1, dry_run=True)
    assert run_fetch(opts, _deps(env, clock, FakeChild(clock))) == 0
    assert "max_logins=%d" % common.MAX_LOGINS_CEILING in capsys.readouterr().out


@pytest.mark.parametrize("logout", ["error", "deadline"])
def test_logout_failure_is_failed_session_stops_at_limit(env, sigalrm_rc, capsys, logout):
    """Ловить відмову логауту, що не рахувалась: усі доби ok, а сесію не закрито (виняток логауту чи SIGALRM після
    останньої доби) — було rc 0, failed=0, і так без кінця. Тепер це відмова сесії: три поспіль — стоп, rc 3,
    отримані доби закомічено, у підсумку sessions_failed=3."""
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock, logout=logout)
    assert run_fetch(_opts(env, days_per_session=1, max_consecutive_failures=3), _deps(env, clock, child)) == 3
    assert [call["days"] for call in child.calls] == [[key] for key in _keys(DAYS[:3])]
    run = _run_manifest(env)
    assert "FT_FETCH_TOO_MANY_SESSION_FAILURES in_row=3" in run["stop_reason"]
    assert [c["status"] for c in run["calls"]] == ["ok"] * 3 and [s["unattempted"] for s in run["sessions"]] == [[]] * 3
    assert all(load_day(env["staging"], SYMBOL, day) is not None for day in DAYS[:3])
    assert "sessions_failed=3" in capsys.readouterr().out


def test_single_logout_failure_makes_run_rc1(env, capsys):
    clock = Clock(SATURDAY_NOON)
    assert run_fetch(_opts(env, days=DAYS[:1]), _deps(env, clock, FakeChild(clock, logout="error"))) == 1
    assert _run_manifest(env)["rc"] == 1 and "sessions_failed=1 calls=1 committed=1 failed=0" in capsys.readouterr().out


def test_max_calls_caps_run_trims_last_session_and_reports_days_left(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, max_calls=2), _deps(env, clock, child)) == 3
    assert [call["days"] for call in child.calls] == [_keys(DAYS[:2])]
    run = _run_manifest(env)
    assert "FT_FETCH_MAX_CALLS_REACHED days_left=3 next_day=20260722" in run["stop_reason"]
    assert clock.sleeps == []


def test_max_calls_over_ceiling_refused_rc2(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, max_calls=common.MAX_CALLS_CEILING + 1), _deps(env, clock, child)) == 2
    assert child.calls == [] and not env["staging"].exists()


def test_real_child_timeout_is_killed_and_nothing_committed(env, tmp_path):
    log_path = str(tmp_path / "child.log")
    outcome = run_child([sys.executable, "-c", "import time; time.sleep(60)"], str(tmp_path), dict(os.environ), 1,
                        log_path)
    assert outcome.status == "timeout" and outcome.returncode is not None and outcome.duration_s < 30

    def hanging_child(argv, cwd, env, timeout_s, log_path):
        args = dict(zip(argv[4::2], argv[5::2]))
        started = fetch_child.day_files(args["--out-dir"], args["--days"].split(",")[0])[0]
        code = "import time; open(%r, 'wb').close(); time.sleep(60)" % started
        return run_child([sys.executable, "-c", code], cwd, env, 1, log_path)

    clock = Clock(SATURDAY_NOON)
    assert run_fetch(_opts(env, days=DAYS[:1]), _deps(env, clock, hanging_child)) == 1
    assert load_day(env["staging"], SYMBOL, DAYS[0]) is None
    run = _run_manifest(env)
    assert run["calls"][0]["status"] == "timeout" and run["sessions"][0]["status"] == "timeout"


def test_each_session_gets_fresh_sdk_dir_removed_after(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days=DAYS[:3], days_per_session=1), _deps(env, clock, child)) == 0
    cwds = [call["cwd"] for call in child.calls]
    assert len(set(cwds)) == 3
    assert all(Path(cwd).parent == Path(os.path.realpath(env["sdk"])) for cwd in cwds)
    assert not any(os.path.exists(cwd) for cwd in cwds)
    assert list((env["staging"] / "_inflight").iterdir()) == []
    assert all(load_day(env["staging"], SYMBOL, day) is not None for day in DAYS[:3])


def test_parent_cwd_equal_repo_root_refused_rc2(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env), _deps(env, clock, child, cwd=common.REPO_ROOT)) == 2
    assert child.calls == []


@pytest.mark.parametrize("layout", ["staging_in_data", "sdk_in_data", "data_in_staging", "sdk_in_staging"])
def test_roots_overlapping_data_root_refused_rc2(env, layout):
    tmp = env["tmp"]
    data_root = env["data"]
    staging, sdk = env["staging"], env["sdk"]
    if layout == "staging_in_data":
        staging = data_root / "staging"
    elif layout == "sdk_in_data":
        sdk = data_root / "sdk"
    elif layout == "data_in_staging":
        data_root = tmp / "staging" / "data"
        write_part(data_root, DAYS[0], [line(ssot_bar(at(DAYS[0], 12, 0), 1.0, 2.0, 0.5, 1.5))])
    elif layout == "sdk_in_staging":
        sdk = staging / "sdk"
    before = sorted(p.as_posix() for p in Path(env["data"]).rglob("*"))
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    opts = _opts(env, days=DAYS[:1], staging_root=str(staging), sdk_cwd=str(sdk))
    assert run_fetch(opts, _deps(env, clock, child, data_root=data_root)) == 2
    assert child.calls == []
    assert sorted(p.as_posix() for p in Path(env["data"]).rglob("*")) == before


@pytest.mark.parametrize("day, now", [
    (dt.date(2026, 9, 12), SATURDAY_NOON),  # доба ще триває
    (dt.date(2026, 9, 7), SATURDAY_NOON),  # завершена, але молодша за MIN_AGE_DAYS
])
def test_incomplete_day_refused_rc2(env, day, now):
    write_part(env["data"], day, [line(ssot_bar(at(day, 1, 0), 1.0, 2.0, 0.5, 1.5))])
    clock = Clock(now)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days=[DAYS[0], day]), _deps(env, clock, child)) == 2
    assert child.calls == []


def test_days_without_ssot_part_skipped_without_call(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days=DAYS), _deps(env, clock, child)) == 0
    assert [call["days"] for call in child.calls] == [_keys(DAYS[:5])]
    assert _run_manifest(env)["skipped"] == [{"day": "20260725", "reason": "no_part"}]


@pytest.mark.parametrize("kind", ["empty", "invalid", "sdk_error", "no_result", "flag_mismatch"])
def test_failed_day_keeps_previous_staging_rc1(env, kind):
    day = DAYS[0]
    previous = [staged_row(at(day, 10, 0), 4055.42, 4093.19, 4055.42, 4092.36)]
    write_day_atomic(env["staging"], SYMBOL, day, previous, fetch_meta(request=common.request_window(day)))
    before = [Path(p).read_bytes() for p in day_paths(env["staging"], SYMBOL, day)]
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock, behaviour={common.day_key(day): kind})
    assert run_fetch(_opts(env, days=[day]), _deps(env, clock, child)) == 1
    assert [Path(p).read_bytes() for p in day_paths(env["staging"], SYMBOL, day)] == before
    assert _run_manifest(env)["calls"][0]["status"] != "ok"
    assert list((env["staging"] / "_inflight").iterdir()) == []


def test_consecutive_failures_limit_stops_run_rc3(env):
    """SDK відмовляє на кожній добі: сесія обривається на ній, наступна — з наступної доби; три поспіль — стоп."""
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock, behaviour={common.day_key(d): "sdk_error" for d in DAYS})
    assert run_fetch(_opts(env, max_consecutive_failures=3), _deps(env, clock, child)) == 3
    assert [call["days"][0] for call in child.calls] == _keys(DAYS[:3])
    assert "FT_FETCH_TOO_MANY_FAILURES" in _run_manifest(env)["stop_reason"]


def test_lock_held_refused_rc2_and_lock_released_after_run(env):
    env["staging"].mkdir()
    lock = env["staging"] / "_fetch.lock"
    lock.write_text('{"pid": 1}', encoding="utf-8")
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days=DAYS[:1]), _deps(env, clock, child)) == 2
    assert child.calls == []
    lock.unlink()
    assert run_fetch(_opts(env, days=DAYS[:1]), _deps(env, clock, child)) == 0
    assert len(child.calls) == 1 and not lock.exists()


def test_only_missing_skips_valid_and_refetches_invalid_day(env):
    valid_day, invalid_day, absent_day = DAYS[:3]
    for day in (valid_day, invalid_day):
        write_day_atomic(env["staging"], SYMBOL, day, [staged_row(at(day, 10, 0), 1.0, 2.0, 0.5, 1.5)],
                         fetch_meta(request=common.request_window(day)))
    Path(day_paths(env["staging"], SYMBOL, invalid_day)[0]).write_bytes(b"corrupted\n")
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days=DAYS[:3], only_missing=True), _deps(env, clock, child)) == 0
    assert [call["days"] for call in child.calls] == [_keys([invalid_day, absent_day])]
    assert load_day(env["staging"], SYMBOL, invalid_day).manifest["call_seq"] == 1


def test_dry_run_reports_planned_sessions_without_calls(env, capsys):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days_per_session=2, dry_run=True), _deps(env, clock, child)) == 0
    out = capsys.readouterr().out
    assert child.calls == [] and "planned=5 sessions=3 max_logins=15 dry_run=1" in out
    assert "FT_FETCH_DRY_RUN day=20260724 session=3 decision=fetch" in out


class _FakeProvider:
    def __init__(self, rows=None, error=None, fetch_error=None, events=None):
        self.rows, self.error, self.fetch_error, self.windows = rows or [], error, fetch_error, []
        self.events = events if events is not None else []

    def __enter__(self):
        self.events.append("login")
        if self.error is not None:
            raise self.error
        return self

    def __exit__(self, *exc):
        self.events.append("logout")
        return False

    def fetch_m1_raw_range(self, symbol, date_from, date_to):
        self.events.append("get_history")
        self.windows.append((symbol, date_from, date_to))
        if self.fetch_error is not None:
            raise self.fetch_error
        return list(self.rows)


class RecordingDeadline:
    """Дедлайн дитини без signal.alarm: порядок перевзведень пишеться в той самий журнал, що й кроки провайдера."""

    def __init__(self, events):
        self.events = events

    def arm(self, stage):
        self.events.append("arm:" + stage)

    def cancel(self):
        self.events.append("cancel")


def _child_args(out_dir, days):
    out_dir.mkdir(exist_ok=True)
    return ["--symbol", SYMBOL, "--days", ",".join(_keys(days)), "--out-dir", str(out_dir), "--deadline-s", "180"]


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_child_session_one_login_per_day_results_and_staging_rows(tmp_path):
    """Одна сесія на дві доби: один логін, окремий get_history на кожну добу з точним вікном, рядки поза добою
    відкидаються й рахуються; доба без рядків — empty, сесія триває."""
    first, second = DAYS[0], DAYS[1]
    rows = [raw_row(at(first, 0, 0) - 60_000, 1.0, 2.0, 0.5, 1.5),  # з відступу запиту — поза першою добою
            raw_row(at(first, 22, 1), 4346.23, 4337.69, 4330.62, 4331.55),
            raw_row(at(first, 22, 0), 4089.98, 4093.19, 4086.33, 4092.36)]
    events = []
    provider = _FakeProvider(rows, events=events)
    out_dir = tmp_path / "session"
    assert fetch_child.main(_child_args(out_dir, [first, second]), provider_factory=lambda cfg: provider,
                            deadline=RecordingDeadline([])) == 0
    assert events == ["login", "get_history", "get_history", "logout"]
    _started, rows_path, result_path = fetch_child.day_files(str(out_dir), common.day_key(first))
    out_rows = [json.loads(x) for x in Path(rows_path).read_text(encoding="utf-8").splitlines()]
    assert [r["open_time_ms"] for r in out_rows] == [at(first, 22, 0), at(first, 22, 1)]
    assert [r["raw_open_not_tick"] for r in out_rows] == [False, True]
    result = _json(result_path)
    assert (result["status"], result["day"], result["rows"], result["rows_outside_day_dropped"],
            result["raw_open_not_tick"]) == ("ok", "20260720", 2, 1, 1)
    for (_sym, date_from, date_to), day in zip(provider.windows, (first, second)):
        assert common.utc_iso(int(date_from.timestamp() * 1000)) == common.request_window(day)["date_from_utc"]
        assert common.utc_iso(int(date_to.timestamp() * 1000)) == common.request_window(day)["date_to_utc"]
    assert _json(fetch_child.day_files(str(out_dir), common.day_key(second))[2])["status"] == "empty"
    session = _json(out_dir / fetch_child.SESSION_RESULT)
    assert (session["status"], session["completed"], session["days"]) == ("ok", 2, ["20260720", "20260721"])


def test_child_sdk_error_aborts_session_at_that_day_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("FXCM_PASSWORD", "s3cr3t-pass")
    events = []
    failing = _FakeProvider(fetch_error=RuntimeError("get_history failed, password s3cr3t-pass"), events=events)
    out_dir = tmp_path / "session"
    assert fetch_child.main(_child_args(out_dir, DAYS[:3]), provider_factory=lambda cfg: failing,
                            deadline=RecordingDeadline([])) == fetch_child.EXIT_SDK_ERROR
    assert events == ["login", "get_history", "logout"]
    first, second = (fetch_child.day_files(str(out_dir), key) for key in _keys(DAYS[:2]))
    assert _json(first[2])["status"] == "error" and not os.path.exists(second[0])
    text = (out_dir / fetch_child.SESSION_RESULT).read_text(encoding="utf-8")
    assert "s3cr3t-pass" not in text and _json(out_dir / fetch_child.SESSION_RESULT)["stage"] == "get_history:20260720"

    login_failing = _FakeProvider(error=RuntimeError("login failed for password s3cr3t-pass"))
    login_dir = tmp_path / "login"
    assert fetch_child.main(_child_args(login_dir, DAYS[:2]), provider_factory=lambda cfg: login_failing,
                            deadline=RecordingDeadline([])) == fetch_child.EXIT_SDK_ERROR
    login_session = _json(login_dir / fetch_child.SESSION_RESULT)
    assert login_session["stage"] == "login" and "s3cr3t-pass" not in login_session["error"]
    assert sorted(os.listdir(login_dir)) == [fetch_child.SESSION_RESULT]


def test_missing_credentials_refused_before_any_call_rc2(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env), _deps(env, clock, child, credentials=False)) == 2
    assert child.calls == []


def test_parent_crash_is_recorded_in_run_manifest_and_lock_released(env):
    clock = Clock(SATURDAY_NOON)

    def crashing_child(argv, cwd, env, timeout_s, log_path):
        raise OSError("disk full")

    with pytest.raises(OSError):
        run_fetch(_opts(env, days=DAYS[:2]), _deps(env, clock, crashing_child))
    run = _run_manifest(env)
    assert "FT_FETCH_CRASHED in_flight=session:1:20260720,20260721" in run["stop_reason"] and run["calls"] == []
    assert not (env["staging"] / "_fetch.lock").exists()
    assert list(Path(os.path.realpath(env["sdk"])).iterdir()) == []  # тека сесії прибрана і при відмові
    assert list((env["staging"] / "_inflight").iterdir()) == []


def test_child_deadline_rearmed_before_login_each_get_history_and_logout(tmp_path):
    """Ловить дедлайн лише в батька і один будильник на сесію: кожен крок перевзводить будильник дитини — логін,
    get_history кожної доби, логаут (і після відмови get_history теж)."""
    events = []
    provider = _FakeProvider([raw_row(at(DAYS[0], 22, 0), 4089.98, 4093.19, 4086.33, 4092.36)], events=events)
    assert fetch_child.main(_child_args(tmp_path / "ok", DAYS[:2]), provider_factory=lambda cfg: provider,
                            deadline=RecordingDeadline(events)) == 0
    assert events == ["arm:login", "login", "arm:get_history:20260720", "get_history", "arm:get_history:20260721",
                      "get_history", "arm:logout", "logout", "cancel"]
    events.clear()
    failing = _FakeProvider(fetch_error=RuntimeError("SDK boom"), events=events)
    assert fetch_child.main(_child_args(tmp_path / "failing", DAYS[:2]), provider_factory=lambda cfg: failing,
                            deadline=RecordingDeadline(events)) == 11
    assert events == ["arm:login", "login", "arm:get_history:20260720", "get_history", "arm:logout", "logout",
                      "cancel"]


@pytest.mark.skipif(not hasattr(signal, "alarm"), reason="signal.alarm — лише POSIX")
def test_process_deadline_kernel_kills_blocked_child(tmp_path):
    """SIGALRM з SIG_DFL: процес завершує ядро, без обробника в Python — так само і посеред нативного виклику."""
    code = ("from tools.repair.first_tick_m1.fetch_child import ProcessDeadline; import time; "
            "ProcessDeadline(1).arm('get_history'); time.sleep(30)")
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, PYTHONPATH=str(common.REPO_ROOT)),
                          timeout=25)
    assert proc.returncode == -signal.SIGALRM and time.monotonic() - started < 20


@pytest.mark.skipif(hasattr(signal, "alarm"), reason="на POSIX дедлайн доступний")
def test_process_deadline_unavailable_is_loud(caplog):
    fetch_child.ProcessDeadline(5).arm("login")
    assert "FT_FETCH_CHILD_DEADLINE_UNAVAILABLE" in caplog.text


def test_run_child_parent_interrupted_kills_child_and_reraises(tmp_path, monkeypatch):
    """Ловить дедлайн, що тримає лише батько: Ctrl+C (чи StopSignal) посеред очікування лишав дитину живою."""
    spawned = []
    real_popen = subprocess.Popen

    class InterruptedWait(real_popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spawned.append(self)
            self.interrupted = False

        def wait(self, timeout=None):
            if timeout == 60 and not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt()
            return super().wait(timeout=timeout)

    monkeypatch.setattr(fetch_runner.subprocess, "Popen", InterruptedWait)
    with pytest.raises(KeyboardInterrupt):
        run_child([sys.executable, "-c", "import time; time.sleep(60)"], str(tmp_path), dict(os.environ), 60,
                  str(tmp_path / "child.log"))
    (proc,) = spawned
    try:
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_run_child_passes_parent_death_signal_preexec(tmp_path, monkeypatch):
    seen = {}

    def preexec():
        raise AssertionError("виконується лише в дитині")

    class CapturingPopen:
        def __init__(self, argv, **kwargs):
            seen.update(kwargs)
            self.pid, self.returncode = 1, 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(fetch_runner, "parent_death_preexec", lambda: preexec)
    monkeypatch.setattr(fetch_runner.subprocess, "Popen", CapturingPopen)
    assert run_child(["x"], str(tmp_path), {}, 5, str(tmp_path / "child.log")).status == "exited"
    assert seen["preexec_fn"] is preexec


def _alive(pid):
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG — лише Linux")
def test_parent_sigkill_takes_child_with_it_on_linux(tmp_path):
    """SIGKILL батька не лишає дитину з сесією FXCM: ядро шле їй SIGKILL (PR_SET_PDEATHSIG)."""
    pid_file = tmp_path / "child.pid"
    child_code = "import os, time; open(%r, 'w').write(str(os.getpid())); time.sleep(120)" % str(pid_file)
    parent_code = ("import os, sys; from tools.repair.first_tick_m1.fetch_runner import run_child; "
                   "run_child([sys.executable, '-c', %r], %r, dict(os.environ), 120, %r)"
                   % (child_code, str(tmp_path), str(tmp_path / "child.log")))
    parent = subprocess.Popen([sys.executable, "-c", parent_code],
                              env=dict(os.environ, PYTHONPATH=str(common.REPO_ROOT)))
    deadline = time.monotonic() + 20
    while not (pid_file.exists() and pid_file.read_text()):
        assert time.monotonic() < deadline, "дитина не стартувала"
        time.sleep(0.05)
    child_pid = int(pid_file.read_text())
    parent.kill()
    parent.wait()
    while _alive(child_pid):
        assert time.monotonic() < deadline, "дитина пережила SIGKILL батька"
        time.sleep(0.05)


def test_sigterm_during_session_records_run_and_returns_128_plus_signum(env):
    """SIGTERM батьку посеред сесії: маніфест прогону фіналізовано з сесією в польоті, лок знято, staging не змінено."""
    clock = Clock(SATURDAY_NOON)

    def child_receiving_sigterm(argv, **kwargs):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    assert run_fetch(_opts(env, days=DAYS[:2]), _deps(env, clock, child_receiving_sigterm)) == 128 + signal.SIGTERM
    run = _run_manifest(env)
    assert "FT_FETCH_STOPPED_BY_SIGNAL in_flight=session:1:20260720,20260721" in run["stop_reason"]
    assert run["rc"] == 128 + signal.SIGTERM and run["calls"] == []
    assert [(s["status"], s["unattempted"]) for s in run["sessions"]] == [("stopped", _keys(DAYS[:2]))]
    assert not (env["staging"] / "_fetch.lock").exists()
    assert load_day(env["staging"], SYMBOL, DAYS[0]) is None


def test_signal_during_final_manifest_write_returns_128_plus_signum(env, monkeypatch):
    """Ловить фіналізацію поза обробниками: фінальний маніфест писався після виходу з StopSignals — SIGTERM у цю мить
    убивав процес із маніфестом без rc, а сигнал між disarm і виходом губився з rc 0."""
    seen = signal_during_final_write(monkeypatch, "ft_m1_fetch_run_v1")
    clock = Clock(SATURDAY_NOON)
    assert run_fetch(_opts(env, days=DAYS[:2]), _deps(env, clock, FakeChild(clock))) == 128 + signal.SIGTERM
    assert isinstance(getattr(seen["handler"], "__self__", None), common.StopSignals)
    run = _run_manifest(env)
    assert run["rc"] == 128 + signal.SIGTERM and "FT_FETCH_SIGNAL_DURING_FINALIZE" in run["stop_reason"]
    assert [c["status"] for c in run["calls"]] == ["ok", "ok"] and not (env["staging"] / "_fetch.lock").exists()


def _child_stopped_after_two_days(clock, stop):
    """Дитина віддала дві доби пакета, почала третю — і тут батька зупиняють (`stop()` у run_child)."""
    def child(argv, cwd, env, timeout_s, log_path):
        FakeChild(clock, behaviour={common.day_key(DAYS[2]): "no_result"})(argv, cwd, env, timeout_s, log_path)
        out_dir = dict(zip(argv[4::2], argv[5::2]))["--out-dir"]
        os.remove(os.path.join(out_dir, fetch_child.SESSION_RESULT))  # сесія ще триває
        stop()

    return child


def test_parent_signal_mid_batch_commits_days_already_received(env):
    """Ловить викидання отриманих діб: SIGTERM батьку після двох діб пакета з п'яти прибирав out_dir разом із ними,
    run.calls лишався порожнім. Тепер доби закомічено, сесія stopped з ними в маніфесті, решта — не почата."""
    clock = Clock(SATURDAY_NOON)
    child = _child_stopped_after_two_days(clock, lambda: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None))
    assert run_fetch(_opts(env), _deps(env, clock, child)) == 128 + signal.SIGTERM
    assert [load_day(env["staging"], SYMBOL, day) is not None for day in DAYS[:3]] == [True, True, False]
    run = _run_manifest(env)
    assert [(c["day"], c["status"], c["session"]) for c in run["calls"]] == [
        ("20260720", "ok", 1), ("20260721", "ok", 1), ("20260722", "stopped", 1)]
    (session,) = run["sessions"]
    assert (session["status"], session["unattempted"]) == ("stopped", _keys(DAYS[3:5]))
    assert "parent_stopped: StopSignal" in session["detail"] and "FT_FETCH_STOPPED_BY_SIGNAL" in run["stop_reason"]
    assert list((env["staging"] / "_inflight").iterdir()) == []


def test_parent_crash_mid_batch_salvage_not_interrupted_by_second_signal(env, monkeypatch):
    """Розбір отриманих діб — під знятими обробниками: SIGTERM посеред коміту після краху батька не обриває коміт
    (без disarm StopSignal вилітав із середини розбору і друга доба губилась разом із записом сесії)."""
    clock = Clock(SATURDAY_NOON)
    real_write_day = fetch_call.write_day_atomic

    def write_day_then_signal(*args, **kwargs):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return real_write_day(*args, **kwargs)

    def crash():
        monkeypatch.setattr(fetch_call, "write_day_atomic", write_day_then_signal)
        raise OSError("disk full")

    with pytest.raises(OSError):
        run_fetch(_opts(env), _deps(env, clock, _child_stopped_after_two_days(clock, crash)))
    assert [load_day(env["staging"], SYMBOL, day) is not None for day in DAYS[:2]] == [True, True]
    run = _run_manifest(env)
    assert [c["status"] for c in run["calls"]] == ["ok", "ok", "stopped"] and run["sessions"][0]["status"] == "stopped"
    assert "FT_FETCH_CRASHED" in run["stop_reason"]


def test_stale_inflight_days_from_killed_parent_are_logged(env, caplog):
    tag_dir = env["staging"] / "_inflight" / "20260912T110000Z-77-s0001"
    tag_dir.mkdir(parents=True)
    for name in ("20260720.started", "20260720.result.json", "20260721.started", fetch_child.SESSION_RESULT):
        (tag_dir / name).write_bytes(b"{}")
    clock = Clock(SATURDAY_NOON)
    assert run_fetch(_opts(env, days=DAYS[:1]), _deps(env, clock, FakeChild(clock))) == 0
    assert "FT_FETCH_INFLIGHT_STALE_DAYS session=20260912T110000Z-77-s0001 days=20260720" in caplog.text
    assert list((env["staging"] / "_inflight").iterdir()) == []
