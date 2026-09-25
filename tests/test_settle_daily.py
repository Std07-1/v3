"""ADR-0103 §3.2/S3c: нічний settle — порядок доведеного ручного прогону, відмови до запису, відкат, вимикач.

Раннер підмінено: кроки лише записуються і створюють те, що створив би справжній інструмент (meta.json архіву, звіт
settle, tgz, PRIME у лозі інжесту). Календар — справжній сезонний з config.json.
"""
from __future__ import annotations

import dataclasses
import json
import os

import pytest

from core.config_loader import load_system_config, m1_settle_policy, pick_config_path
from tools.repair import settle_daily as sd
from tools.repair import settle_daily_plan as sp
from tools.repair.partfile_io import WritersGuardRefused

SYMBOL_DIRS = ["XAU_USD", "XAG_USD", "NAS100", "SPX500", "US30", "EUSTX50", "GER30"]


class FakeRunner:
    def __init__(self, env, logs_dir):
        self.env, self.logs_dir, self.outputs = env, logs_dir, {}
        os.makedirs(logs_dir, exist_ok=True)

    def run(self, name, cmd, *, cwd=None, timeout_s=None):
        env = self.env
        env.calls.append(name)
        env.commands[name] = list(cmd)
        rc = env.rc.get(name, 0)
        if name.startswith("fetch_") and rc == 0:
            out = cmd[cmd.index("--out") + 1]
            os.makedirs(out)
            with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as fh:
                json.dump({"mode": "PREVIOUS_CLOSE"}, fh)
        elif name.startswith("settle_m1_") and rc == 0 and name not in env.no_report:
            sym_dir = name[len("settle_m1_"):]
            with open(cmd[cmd.index("--report") + 1], "w", encoding="utf-8") as fh:
                json.dump({"symbols": {sym_dir: {"changed_m1_keys": env.changed.get(sym_dir, [])}}}, fh)
        elif name == "backup" and rc == 0:
            with open(cmd[2], "wb") as fh:
                fh.write(b"tgz-bytes")
        elif name == "rollback_extract" and rc == 0:
            os.makedirs(env.data_root)
        elif name == "start_ingest":
            with open(os.path.join(env.log_dir, "m1_ingestion_worker.err.log"), "a", encoding="utf-8") as fh:
                fh.write("INFO M1_POLLER_REDIS_PRIME symbols=7\n")
        self.outputs[name] = env.outputs.get(name, "")
        return rc

    def output(self, name):
        return self.outputs[name]


@dataclasses.dataclass
class Env:
    tmp: str
    now: str = "2026-09-24T21:05"
    calls: list = dataclasses.field(default_factory=list)
    commands: dict = dataclasses.field(default_factory=dict)
    rc: dict = dataclasses.field(default_factory=dict)
    changed: dict = dataclasses.field(default_factory=lambda: {"XAU_USD": [1, 2], "GER30": [3]})
    no_report: set = dataclasses.field(default_factory=set)
    guard_refuses: bool = False
    outputs: dict = dataclasses.field(default_factory=lambda: {
        "git_head": "abc123", "git_origin": "abc123", "git_status": "?? .env.save",
        "preflight_status": "smc:smc-fxcm RUNNING pid 1\nsmc:smc-preview RUNNING pid 2\nsmc:smc-ws RUNNING pid 3",
        **{"observe_status_%d" % i: "smc:smc-fxcm RUNNING pid 1\nsmc:smc-preview RUNNING pid 2\n"
                                    "smc:smc-ws RUNNING pid 3\nsmc:smc-ticks STOPPED" for i in range(1, 5)}})

    @property
    def data_root(self):
        return os.path.join(self.tmp, "data_v3")

    @property
    def log_dir(self):
        return os.path.join(self.tmp, "logs")

    @property
    def work_dir(self):
        return os.path.join(self.tmp, "work")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    e = Env(str(tmp_path))
    for path in (e.data_root, e.log_dir, e.work_dir):
        os.makedirs(path)
    monkeypatch.setattr(sd, "is_prod_data_root", lambda _root: True)
    return e


def _settle(env, *, schedule_enabled=True, **policy_overrides):
    cfg = load_system_config(pick_config_path())
    policy = dataclasses.replace(m1_settle_policy(cfg), schedule_enabled=schedule_enabled, observe_s=0,
                                 min_free_disk_gb=1, **policy_overrides)
    paths = sd.Paths(code_dir="/opt/smc-v3", data_root=env.data_root, config="/opt/smc-v3/config.json", py="py311",
                     py37="py37", log_dir=env.log_dir, work_dir=env.work_dir, fetch_user="smc")

    def guard(_root):
        if env.guard_refuses:
            raise WritersGuardRefused("WRITERS_GUARD_REFUSED live=1")

    return sd.DailySettle(cfg, paths, policy, runner_factory=lambda logs: FakeRunner(env, logs),
                          clock=lambda: sp.parse_iso_minute(env.now), sleep=lambda _s: None, guard=guard)


def _run(env, *, scheduled=True, dry_run=False, **policy_overrides):
    return _settle(env, **policy_overrides).run(scheduled=scheduled, dry_run=dry_run, ignore_break=False)


def _report(env):
    with open(os.path.join(env.work_dir, "last_status.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_nightly_run_follows_the_proven_manual_order(env):
    assert _run(env) == sd.EXIT_OK
    steps = [c for c in env.calls if not c.startswith(("git_", "observe_status_", "preflight_"))]
    expected = ["fetch_m1_a1", "fetch_d1_a1", "stop_writers", "backup"]
    for sym_dir in SYMBOL_DIRS:
        expected.append("settle_m1_" + sym_dir)
        if sym_dir in env.changed:
            expected.append("season_apply_" + sym_dir)
    expected += ["d1_native_settle", "start_ingest", "start_readers"]
    assert steps == expected
    assert env.commands["stop_writers"] == ["supervisorctl", "stop", "smc:smc-ws", "smc:smc-preview", "smc:smc-fxcm"]
    assert "smc-ticks" not in " ".join(" ".join(c) for c in env.commands.values())
    assert "--apply" in env.commands["settle_m1_XAU_USD"] and "--apply" in env.commands["d1_native_settle"]
    settle_xau = env.commands["settle_m1_XAU_USD"]
    assert settle_xau[settle_xau.index("--to") + 1] == "2026-09-24T15:05"  # забір − лаг 6 год
    settle_ger = env.commands["settle_m1_GER30"]
    assert settle_ger[settle_ger.index("--to") + 1] == "2026-09-24T09:05"  # EU — лаг 12 год
    assert sp.load_settled_to(env.work_dir)["GER30"] == sp.parse_iso_minute("2026-09-24T09:05")
    assert _report(env)["stage"] == "SETTLED"
    assert not os.path.exists(os.path.join(env.work_dir, sd.WRITERS_STOPPED_MARKER))


def test_writers_stopped_on_purpose_are_not_started_by_the_run(env):
    """Власник зупинив інжест навмисно — прогін відмовляє до забору і нічого не стартує."""
    env.outputs["preflight_status"] = "smc:smc-fxcm STOPPED Sep 25\nsmc:smc-preview RUNNING\nsmc:smc-ws RUNNING"
    assert _run(env) == sd.EXIT_REFUSED
    assert not [c for c in env.calls if c.startswith(("fetch_", "stop_", "start_"))]
    assert "WRITERS_NOT_RUNNING ['smc:smc-fxcm']" in _report(env)["problems"][0]


def test_marker_marks_exactly_the_window_when_the_run_holds_writers_stopped(env):
    seen = {}

    class Spy(FakeRunner):
        def run(self, name, cmd, **kwargs):
            seen[name] = os.path.exists(os.path.join(env.work_dir, sd.WRITERS_STOPPED_MARKER))
            return super().run(name, cmd, **kwargs)

    settle = _settle(env)
    settle.runner_factory = lambda logs: Spy(env, logs)
    assert settle.run(scheduled=True, dry_run=False, ignore_break=False) == sd.EXIT_OK
    assert not seen["fetch_m1_a1"] and seen["stop_writers"] and seen["settle_m1_XAU_USD"] and seen["start_readers"]
    assert not os.path.exists(os.path.join(env.work_dir, sd.WRITERS_STOPPED_MARKER))


def test_fetch_runs_as_the_fetch_user_from_its_own_cwd_with_creds_from_the_sidecar(env):
    assert _run(env) == sd.EXIT_OK
    cmd = env.commands["fetch_m1_a1"]
    assert cmd[:4] == ["sudo", "-n", "-u", "smc"] and "--creds-from-sidecar" in cmd and "py37" in cmd
    assert not [part for part in cmd if "PASSWORD" in part or "USERNAME" in part]


def test_disabled_schedule_does_nothing(env):
    assert _run(env, schedule_enabled=False) == sd.EXIT_OK
    assert env.calls == [] and not os.path.exists(os.path.join(env.work_dir, "runs"))


@pytest.mark.parametrize("now", ["2026-09-25T14:00", "2026-11-10T21:05"])
def test_outside_the_common_break_does_nothing(env, now):
    """Узимку 21:05 брокер торгує (перерва 22–23): cron-слот 21:05 пропускає день без жодного кроку."""
    env.now = now
    assert _run(env) == sd.EXIT_OK and env.calls == []


def test_manual_run_ignores_the_schedule_switch(env):
    assert _run(env, scheduled=False, schedule_enabled=False) == sd.EXIT_OK and "d1_native_settle" in env.calls


def test_failed_fetch_never_stops_the_writers(env):
    env.rc.update({"fetch_d1_a1": 75, "fetch_d1_a2": 1, "fetch_d1_a3": 124})
    assert _run(env) == sd.EXIT_REFUSED
    assert "stop_writers" not in env.calls and [c for c in env.calls if c.startswith("fetch_d1")] == [
        "fetch_d1_a1", "fetch_d1_a2", "fetch_d1_a3"]


def test_transient_fetch_failure_is_retried(env):
    env.rc["fetch_m1_a1"] = 75  # watchdog: завислий get_history
    assert _run(env) == sd.EXIT_OK and "fetch_m1_a2" in env.calls


def test_dirty_tree_or_foreign_code_refuses_before_anything(env):
    env.outputs["git_status"] = " M runtime/ws/ws_server.py"
    assert _run(env) == sd.EXIT_REFUSED
    assert not [c for c in env.calls if not c.startswith(("git_", "preflight_"))]
    assert "DIRTY_TREE" in _report(env)["problems"][0]


def test_too_close_to_the_session_open_refuses_before_stopping(env):
    env.now = "2026-09-24T21:40"  # дедлайн 21:50 — лишилось 10 хв < 15
    assert _run(env) == sd.EXIT_REFUSED and "stop_writers" not in env.calls


def test_live_writer_after_stop_restarts_and_leaves_data_untouched(env):
    env.guard_refuses = True
    assert _run(env) == sd.EXIT_REFUSED
    assert "backup" not in env.calls and env.calls[-2:] == ["start_ingest", "start_readers"]


@pytest.mark.parametrize("break_it", ["rc", "exception"])
def test_failed_data_step_rolls_back_from_the_tgz_and_restarts_writers(env, break_it):
    if break_it == "rc":
        env.rc["settle_m1_NAS100"] = 2  # ARCHIVE_GATE REFUSED
    else:
        env.no_report.add("settle_m1_NAS100")  # звіту немає — крок не довів результату
    assert _run(env) == sd.EXIT_ROLLED_BACK
    assert "settle_m1_SPX500" not in env.calls  # перша відмова зупиняє прогін
    assert "rollback_extract" in env.calls and env.calls[-2:] == ["start_ingest", "start_readers"]
    assert [n for n in os.listdir(env.tmp) if n.startswith("data_v3.bad-")]
    assert sp.load_settled_to(env.work_dir) == {} and _report(env)["stage"] == "DATA"


def test_dry_run_fetches_and_plans_but_writes_nothing(env):
    assert _run(env, dry_run=True) == sd.EXIT_OK
    assert "stop_writers" not in env.calls and "backup" not in env.calls
    assert not [c for c, cmd in env.commands.items() if "--apply" in cmd]
    assert sp.load_settled_to(env.work_dir) == {}


def test_retention_keeps_only_the_newest_backups(env):
    backups = os.path.join(env.work_dir, "backups")
    os.makedirs(backups)
    for day in range(1, 17):
        for suffix in (".tgz", ".tgz.sha256"):
            open(os.path.join(backups, "data_v3.pre-sd-202608%02dT210500Z%s" % (day, suffix)), "w").close()
    open(os.path.join(backups, "keep-me.txt"), "w").close()
    assert _run(env, backups_keep=3) == sd.EXIT_OK
    left = sorted(os.listdir(backups))
    assert "keep-me.txt" in left and len([n for n in left if n.endswith(".tgz")]) == 3
    assert "data_v3.pre-sd-20260924T210500Z.tgz" in left  # цей прогін


def test_missing_prime_after_start_is_reported(env):
    class NoPrime(FakeRunner):
        def run(self, name, cmd, **kwargs):
            if name == "start_ingest":
                self.env.calls.append(name)
                self.outputs[name] = ""
                return 0
            return super().run(name, cmd, **kwargs)

    settle = _settle(env)
    settle.runner_factory = lambda logs: NoPrime(env, logs)
    assert settle.run(scheduled=True, dry_run=False, ignore_break=False) == sd.EXIT_OBSERVE
    assert "PRIME_NOT_SEEN" in " ".join(_report(env)["problems"])
    assert env.calls[env.calls.index("start_ingest") + 1] == "start_readers"  # читачі стартують і без PRIME


def test_writer_down_after_start_is_reported(env):
    env.outputs["observe_status_4"] = "smc:smc-fxcm FATAL Exited too quickly"
    assert _run(env) == sd.EXIT_OBSERVE
    assert "NOT_RUNNING" in " ".join(_report(env)["problems"])


def test_main_refuses_writes_outside_the_break_on_prod_and_rehearsal_into_prod_state(tmp_path):
    assert sd.main(["--manual", "--ignore-break", "--data-root", "/opt/smc-v3/data_v3"]) == sd.EXIT_USAGE
    assert sd.main(["--manual", "--ignore-break", "--data-root", str(tmp_path / "copy")]) == sd.EXIT_USAGE


@pytest.mark.skipif(os.name != "posix", reason="група процесів і killpg — лише POSIX (прод, CI)")
def test_real_runner_timeout_kills_the_whole_process_group(tmp_path):
    """sudo не передає SIGKILL дитині: без убивства групи завислий забір пережив би прогін."""
    import sys
    import time

    pid_file = tmp_path / "child.pid"
    code = ("import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "open(%r, 'w').write(str(p.pid)); time.sleep(60)" % str(pid_file))
    runner = sd.Runner(str(tmp_path / "logs"))
    t0 = time.monotonic()
    assert runner.run("hang", [sys.executable, "-c", code], timeout_s=2) == sd.RC_TIMEOUT
    assert time.monotonic() - t0 < 20
    child = int(pid_file.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
