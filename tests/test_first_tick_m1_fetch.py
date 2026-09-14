"""Фаза fetch під рейками — жодного виклику брокера там, де його не має бути (ADR-0096 §3.3 B, слайс B3).

Навіщо. Перезабір іде в живу FXCM-сесію того самого акаунта, а `get_history` — синхронний нативний виклик без
таймауту (історія зависань sidecar, ADR-0054 §3.6). Кожна рейка тут — окремий спосіб зашкодити: виклик при
відкритому ринку, спільний кеш SDK з live-сайдкаром, безліміт логінів, тиха заміна валідної доби staging
невдалим перезабором. Дитина-процес підмінена фейком, що пише inflight так само, як справжня
(`fetch_child.main` перевірено окремо з фейковим провайдером); таймаут — на справжньому процесі.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

from ft_m1_support import SYMBOL, at, fetch_meta, line, make_cfg, raw_row, ssot_bar, staged_row, write_part
from tools.repair.first_tick_m1 import common, fetch_child
from tools.repair.first_tick_m1.fetch import FetchDeps, FetchOptions, run_fetch
from tools.repair.first_tick_m1.fetch_runner import ChildOutcome, run_child
from tools.repair.first_tick_m1.staging import day_paths, load_day, rows_bytes, write_day_atomic

DAYS = [dt.date(2026, 7, 20) + dt.timedelta(days=i) for i in range(6)]  # Пн 20.07 … Сб 25.07
SATURDAY_NOON = at(dt.date(2026, 9, 12), 12, 0)
SUNDAY_OPEN = at(dt.date(2026, 9, 13), 22, 0)


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
    """Пише inflight-рядки і result.json за контрактом `fetch_child`; поведінка — за добою."""

    def __init__(self, clock, behaviour=None, advance_ms=0):
        self.clock, self.behaviour, self.advance_ms = clock, behaviour or {}, advance_ms
        self.calls = []

    def __call__(self, argv, cwd, env, timeout_s, log_path):
        args = dict(zip(argv[4::2], argv[5::2]))
        assert argv[1:4] == ["-u", "-m", "tools.repair.first_tick_m1.fetch_child"]
        assert os.path.isdir(cwd) and os.listdir(cwd) == []
        assert env["PYTHONPATH"].split(os.pathsep)[0] == str(common.REPO_ROOT)
        self.calls.append({"cwd": cwd, "day": args["--day"], "timeout_s": timeout_s})
        self.clock.now += self.advance_ms
        day = common.parse_day_key(args["--day"])
        kind = self.behaviour.get(args["--day"], "ok")
        rows = [staged_row(common.day_start_ms(day) + (22 * 60 + i) * 60_000, 4089.98 + i, 4093.19 + i,
                           4086.33 + i, 4092.36 + i) for i in range(3)]
        if kind == "flag_mismatch":
            rows[1]["raw_open_not_tick"] = True
        result = {"status": "ok", "rows": 3, "rows_outside_day_dropped": 0,
                  "raw_open_not_tick": sum(r["raw_open_not_tick"] for r in rows), "request": common.request_window(day),
                  "call_duration_s": 0.1, "sdk": {"python": "3.7.0", "forexconnect": "1.6.43"}, "error": None}
        codes = {"exit13_empty": 13, "exit12_invalid": 12, "exit11_sdk": 11, "rc0_no_result": 0}
        if kind in codes:
            if kind != "rc0_no_result":
                result.update(status="error", error="boom")
                common.write_json_atomic(args["--result"], result)
            return ChildOutcome("exited", codes[kind], 0.1)
        Path(args["--out"]).write_bytes(rows_bytes(rows))
        common.write_json_atomic(args["--result"], result)
        return ChildOutcome("exited", 0, 0.1)


@pytest.fixture()
def env(tmp_path):
    data = tmp_path / "data"
    for day in DAYS[:5]:
        write_part(data, day, [line(ssot_bar(at(day, 12, 0), 1.0, 2.0, 0.5, 1.5))])
    work = tmp_path / "work"
    work.mkdir()
    return {"tmp": tmp_path, "data": data, "staging": tmp_path / "staging", "sdk": tmp_path / "sdk", "work": work}


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


def test_market_open_now_refused_before_any_call(env):
    clock = Clock(at(dt.date(2026, 9, 14), 10, 0))  # понеділок, торгова година
    child = FakeChild(clock)
    assert run_fetch(_opts(env), _deps(env, clock, child)) == 3
    assert child.calls == []
    assert "FT_FETCH_REFUSED_MARKET_OPEN" in _run_manifest(env)["stop_reason"]


def test_market_opening_within_timeout_plus_guard_refused(env):
    """Ловить перевірку лише «зараз»: відкриття через 62 хв < timeout 3 хв + guard 60 хв. Контроль −70 хв — виклик є."""
    clock = Clock(SUNDAY_OPEN - 62 * 60_000)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days=DAYS[:1], call_timeout_s=180, guard_minutes=60), _deps(env, clock, child)) == 3
    assert child.calls == []
    control_clock = Clock(SUNDAY_OPEN - 70 * 60_000)
    control = FakeChild(control_clock)
    assert run_fetch(_opts(env, days=DAYS[:1], call_timeout_s=180, guard_minutes=60),
                     _deps(env, control_clock, control)) == 0
    assert len(control.calls) == 1


def test_market_opening_mid_run_stops_before_next_call(env):
    """Кожен виклик «триває» 90 хв: з 16:00 неділі четвертий виклик о 20:30 ще дозволений, п'ятий о 22:00 — ні."""
    clock = Clock(at(dt.date(2026, 9, 13), 16, 0))
    child = FakeChild(clock, advance_ms=90 * 60_000)
    assert run_fetch(_opts(env), _deps(env, clock, child)) == 3
    assert len(child.calls) == 4
    run = _run_manifest(env)
    assert run["rc"] == 3 and "FT_FETCH_REFUSED_MARKET_OPEN" in run["stop_reason"]
    assert [c["status"] for c in run["calls"]] == ["ok"] * 4


def test_max_calls_caps_run_and_reports_days_left(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, max_calls=2), _deps(env, clock, child)) == 3
    assert len(child.calls) == 2
    run = _run_manifest(env)
    assert "FT_FETCH_MAX_CALLS_REACHED days_left=3 next_day=20260722" in run["stop_reason"]
    assert clock.sleeps == [common.CALL_INTERVAL_DEFAULT_S]


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

    def sleeping_child(argv, cwd, env, timeout_s, log_path):
        return run_child([sys.executable, "-c", "import time; time.sleep(60)"], cwd, env, 1, log_path)

    clock = Clock(SATURDAY_NOON)
    assert run_fetch(_opts(env, days=DAYS[:1]), _deps(env, clock, sleeping_child)) == 1
    assert load_day(env["staging"], SYMBOL, DAYS[0]) is None
    assert _run_manifest(env)["calls"][0]["status"] == "timeout"


def test_each_call_gets_fresh_sdk_dir_removed_after(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env, days=DAYS[:3]), _deps(env, clock, child)) == 0
    cwds = [call["cwd"] for call in child.calls]
    assert len(set(cwds)) == 3
    assert all(Path(cwd).parent == Path(os.path.realpath(env["sdk"])) for cwd in cwds)
    assert not any(os.path.exists(cwd) for cwd in cwds)
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
    assert [call["day"] for call in child.calls] == [common.day_key(d) for d in DAYS[:5]]
    assert _run_manifest(env)["skipped"] == [{"day": "20260725", "reason": "no_part"}]


@pytest.mark.parametrize("kind", ["exit13_empty", "exit12_invalid", "exit11_sdk", "rc0_no_result", "flag_mismatch"])
def test_failed_child_keeps_previous_staging_rc1(env, kind):
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
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock, behaviour={common.day_key(d): "exit11_sdk" for d in DAYS})
    assert run_fetch(_opts(env, max_consecutive_failures=3), _deps(env, clock, child)) == 3
    assert len(child.calls) == 3
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
    assert [call["day"] for call in child.calls] == [common.day_key(invalid_day), common.day_key(absent_day)]
    assert load_day(env["staging"], SYMBOL, invalid_day).manifest["call_seq"] == 1


class _FakeProvider:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.windows = rows or [], error, []

    def __enter__(self):
        if self.error is not None:
            raise self.error
        return self

    def __exit__(self, *exc):
        return False

    def fetch_m1_raw_range(self, symbol, date_from, date_to):
        self.windows.append((symbol, date_from, date_to))
        return list(self.rows)


def _child_args(tmp_path, day):
    return ["--symbol", SYMBOL, "--day", common.day_key(day), "--out", str(tmp_path / "out.jsonl"),
            "--result", str(tmp_path / "result.json")]


def test_child_main_with_fake_provider_writes_raw_rows_result_and_exit_codes(tmp_path, monkeypatch):
    day = DAYS[0]
    rows = [raw_row(at(day, 0, 0) - 60_000, 1.0, 2.0, 0.5, 1.5),  # з відступу запиту — поза добою
            raw_row(at(day, 22, 1), 4346.23, 4337.69, 4330.62, 4331.55),
            raw_row(at(day, 22, 0), 4089.98, 4093.19, 4086.33, 4092.36)]
    provider = _FakeProvider(rows)
    assert fetch_child.main(_child_args(tmp_path, day), provider_factory=lambda cfg: provider) == 0
    out_rows = [json.loads(x) for x in (tmp_path / "out.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["open_time_ms"] for r in out_rows] == [at(day, 22, 0), at(day, 22, 1)]
    assert [r["raw_open_not_tick"] for r in out_rows] == [False, True]
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert (result["status"], result["rows"], result["rows_outside_day_dropped"], result["raw_open_not_tick"]) == (
        "ok", 2, 1, 1)
    assert result["request"] == common.request_window(day)
    (_sym, date_from, date_to), = provider.windows
    assert common.utc_iso(int(date_from.timestamp() * 1000)) == result["request"]["date_from_utc"]
    assert common.utc_iso(int(date_to.timestamp() * 1000)) == result["request"]["date_to_utc"]

    assert fetch_child.main(_child_args(tmp_path, day), provider_factory=lambda cfg: _FakeProvider(rows[:1])) == 13
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["status"] == "empty"

    monkeypatch.setenv("FXCM_PASSWORD", "s3cr3t-pass")
    failing = _FakeProvider(error=RuntimeError("login failed for password s3cr3t-pass"))
    assert fetch_child.main(_child_args(tmp_path, day), provider_factory=lambda cfg: failing) == 11
    result_text = (tmp_path / "result.json").read_text(encoding="utf-8")
    assert "s3cr3t-pass" not in result_text and "RuntimeError" in json.loads(result_text)["error"]


def test_missing_credentials_refused_before_any_call_rc2(env):
    clock = Clock(SATURDAY_NOON)
    child = FakeChild(clock)
    assert run_fetch(_opts(env), _deps(env, clock, child, credentials=False)) == 2
    assert child.calls == []
