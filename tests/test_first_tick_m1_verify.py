"""verify і rollback: читачі бачать рівно заплановану заміну, а відкат повертає байти (ADR-0096 §3.3 B, B8).

Навіщо. Зелений apply доводить лише, що sha файла збігся з планом. Графік же показує бар, який обрали читачі
(TAIL — cold-load, RANGE — scrollback, PRIME — bootstrap Redis): рядок, що не переможець, або змінений close
чи volume «успішний» apply пропустив би. Кінцевий тест проганяє увесь ланцюжок на прикладі ADR §1.3/§1.4:
26.07 22:01 PREV O=L=4055.42 → FIRST_TICK O 4089.98 L 4086.33; «запечений» 13.09 22:01 лишається PREV.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from ft_m1_support import Scenario, at, line, signal_during_final_write, ssot_bar, staged_row, tree_digest, write_part
from runtime.store.layers.disk_layer import DiskLayer
from tools.repair.first_tick_m1 import verify as verify_mod
from tools.repair.first_tick_m1.apply import ApplyOptions, run_apply
from tools.repair.first_tick_m1.common import sha256_file
from tools.repair.first_tick_m1.plan import PlanOptions, run_plan
from tools.repair.first_tick_m1.rollback import RollbackOptions, run_rollback
from tools.repair.first_tick_m1.target_rails import WriteDeps
from tools.repair.first_tick_m1.verify import VerifyOptions, diff_lines, diff_views, read_views, run_verify
from tools.repair.first_tick_m1.writers_guard import ProcMatch, WriterScan

SESSION = dt.date(2026, 7, 26)
BAKED = dt.date(2026, 9, 13)
NOW = at(dt.date(2026, 9, 26), 12, 0)  # субота
CLEAR = WriterScan(True, None, 10, (), (), 0)


def _repaired(tmp_path):
    sc = Scenario(tmp_path / "prod")
    rows = [  # хвилина: PREV (o, h, low, c, v) і FIRST_TICK (o, h, low, c)
        (1, (4055.42, 4093.19, 4055.42, 4092.36, 31.0), (4089.98, 4093.19, 4086.33, 4092.36)),
        (2, (4092.36, 4094.00, 4091.10, 4093.50, 12.0), (4092.40, 4094.00, 4091.10, 4093.50)),
        # однотікова з v=2: після заміни O=H=L=C — кеш Redis без extensions сховав би її → SKIP_WOULD_HIDE, лишається PREV
        (3, (4093.50, 4093.80, 4093.50, 4093.80, 2.0), (4093.80, 4093.80, 4093.80, 4093.80)),
    ]
    for minute, prev, first in rows:
        key = at(SESSION, 22, minute)
        sc.add(SESSION, ssot_bar(key, *prev), staged_row(key, *first, volume=int(prev[4])))
    baked_rows = [(1, (4346.23, 4346.23, 4330.62, 4331.55), (4346.23, 4337.69, 4330.62, 4331.55)),
                  (2, (4331.55, 4335.00, 4331.00, 4334.00), (4331.55, 4335.00, 4331.00, 4334.00))]
    for minute, prev, first in baked_rows:
        key = at(BAKED, 22, minute)
        sc.add(BAKED, ssot_bar(key, *prev), staged_row(key, *first))
    sc.write()
    plan_dir = tmp_path / "plan"
    assert run_plan(PlanOptions(sc.symbol, SESSION, BAKED, str(sc.staging), str(plan_dir)), sc.cfg()) == 0
    deps = WriteDeps(now_ms=lambda: NOW, scan_writers=lambda dirs: CLEAR, geteuid=None, load_cfg=sc.cfg)
    manifest = tmp_path / "apply.json"
    opts = ApplyOptions(str(plan_dir), sha256_file(plan_dir / "PLAN.json"), str(sc.staging),
                        manifest_out=str(manifest))
    return sc, plan_dir, manifest, deps, opts


def _bar(views, name, key):
    return next(b for b in views[name] if b["open_time_ms"] == key)


def test_end_to_end_prev_history_repaired_and_verified(tmp_path):
    sc, plan_dir, manifest, deps, opts = _repaired(tmp_path)
    before = read_views(str(sc.data), sc.symbol, SESSION)
    assert run_apply(opts, deps) == 0
    after = read_views(str(sc.data), sc.symbol, SESSION)
    for name in verify_mod.VIEWS:
        bar = _bar(after, name, at(SESSION, 22, 1))
        assert (bar["o"], bar["h"], bar["low"], bar["c"], bar["v"]) == (4089.98, 4093.19, 4086.33, 4092.36, 31.0)
        assert _bar(before, name, at(SESSION, 22, 1))["low"] == 4055.42
        flat = _bar(after, name, at(SESSION, 22, 3))
        assert (flat["o"], flat["low"], "extensions" in flat) == (4093.50, 4093.50, False)
    assert after["TAIL"] == after["RANGE"]
    plan = json.loads((plan_dir / "PLAN.json").read_text(encoding="utf-8"))
    assert plan["totals"]["SKIP_WOULD_HIDE"] == 1 and plan["params"]["display_flat_max_volume"] == 10.0
    assert {"code": "PLAN_WOULD_HIDE", "day": "20260726", "detail": "keys=1"} in plan["warnings"]
    baked_after = read_views(str(sc.data), sc.symbol, BAKED)
    assert _bar(baked_after, "TAIL", at(BAKED, 22, 1))["o"] == 4346.23  # «запечений» лишився PREV
    assert json.loads(manifest.read_text(encoding="utf-8"))["files"][0]["replaced"] == 2
    assert run_verify(VerifyOptions(str(manifest), str(tmp_path / "work"))) == 0


def _views_pair():
    old = [ssot_bar(at(SESSION, 22, 1), 4055.42, 4093.19, 4055.42, 4092.36, 31.0),
           ssot_bar(at(SESSION, 22, 2), 4092.36, 4094.0, 4091.1, 4093.5, 12.0)]
    new = [dict(old[0], o=4089.98, low=4086.33), dict(old[1])]
    planned = {at(SESSION, 22, 1): {"new": {"o": 4089.98, "h": 4093.19, "low": 4086.33}, "trading_flat_add": False,
                                    "line": 0}}
    return old, new, planned


def test_diff_flags_changed_close_or_volume():
    old, new, planned = _views_pair()
    assert diff_views(old, new, planned) == []
    for field, value in (("c", 4092.0), ("v", 30.0)):
        changed = [dict(new[0], **{field: value}), new[1]]
        (violation,) = diff_views(old, changed, planned)
        assert violation.startswith("VERIFY_UNPLANNED_CHANGE") and field in violation
    unplanned_close = [new[0], dict(new[1], c=4093.6)]
    assert diff_views(old, unplanned_close, planned)[0].startswith("VERIFY_UNPLANNED_CHANGE")


@pytest.mark.parametrize("case, code", [
    ("unplanned_o", "VERIFY_UNPLANNED_CHANGE"), ("missing_key", "VERIFY_KEYSET_CHANGED"),
    ("line_count", "VERIFY_LINE_COUNT_CHANGED"), ("unplanned_line", "VERIFY_UNPLANNED_LINE_CHANGED"),
    ("trading_flat", "VERIFY_TRADING_FLAT_MISMATCH"), ("ohlc_broken", "VERIFY_OHLC_BROKEN"),
])
def test_diff_flags_unplanned_change_missing_key_and_line_count(case, code):
    old, new, planned = _views_pair()
    lines_old = [line(b) for b in old]
    if case == "unplanned_o":
        found = diff_views(old, [new[0], dict(new[1], o=4092.5)], planned)
    elif case == "missing_key":
        found = diff_views(old, new[:1], planned)
    elif case == "line_count":
        found = diff_lines(lines_old, lines_old + ["x"], {0})
    elif case == "unplanned_line":
        found = diff_lines(lines_old, [lines_old[0], lines_old[1].replace("12.0", "12.5")], {0})
    elif case == "trading_flat":
        found = diff_views(old, [dict(new[0], extensions={"trading_flat": True}), new[1]], planned)
    else:
        planned[at(SESSION, 22, 1)]["new"] = {"o": 4095.0, "h": 4093.19, "low": 4086.33}
        found = diff_views(old, [dict(new[0], o=4095.0), new[1]], planned)
    assert any(v.startswith(code) for v in found), found


def test_diff_flags_planned_value_not_visible_to_readers(tmp_path):
    """REPLACE лягло в рядок-непереможець: файл змінено, а читачі показують старий бар."""
    key = at(SESSION, 22, 1)
    whole = ssot_bar(key, 4055.42, 4093.19, 4055.42, 4092.36, 31.0, extensions={"partial": False})
    partial = ssot_bar(key, 4055.42, 4093.19, 4055.42, 4092.36, 31.0, extensions={"partial": True})
    before_root, after_root = tmp_path / "before", tmp_path / "after"
    write_part(before_root, SESSION, [line(whole), line(partial)])
    write_part(after_root, SESSION, [line(whole), line(dict(partial, o=4089.98, low=4086.33))])
    planned = {key: {"new": {"o": 4089.98, "h": 4093.19, "low": 4086.33}, "trading_flat_add": False, "line": 1}}
    before, after = read_views(str(before_root), "XAU/USD", SESSION), read_views(str(after_root), "XAU/USD", SESSION)
    found = [v for name in verify_mod.VIEWS for v in diff_views(before[name], after[name], planned, name)]
    assert len(found) == 3 and all(v.startswith("VERIFY_PLANNED_VALUE_MISMATCH") for v in found)


def test_verify_reads_through_disk_layer_three_views(tmp_path, monkeypatch):
    sc, _plan_dir, manifest, deps, opts = _repaired(tmp_path)
    assert run_apply(opts, deps) == 0
    calls = []
    real = DiskLayer.read_window_with_geom

    def spy(self, *args, **kwargs):
        calls.append({k: kwargs.get(k) for k in ("use_tail", "final_only", "skip_preview")})
        return real(self, *args, **kwargs)

    monkeypatch.setattr(DiskLayer, "read_window_with_geom", spy)
    assert run_verify(VerifyOptions(str(manifest), str(tmp_path / "work"))) == 0
    assert {"use_tail": True, "final_only": None, "skip_preview": None} in calls
    assert {"use_tail": False, "final_only": None, "skip_preview": None} in calls
    assert {"use_tail": True, "final_only": True, "skip_preview": True} in calls
    assert len(calls) == 6  # один переписаний файл × до/після × три погляди


@pytest.mark.parametrize("tamper", ["backup", "current"])
def test_verify_rc2_on_backup_or_current_sha_mismatch(tmp_path, tamper):
    sc, _plan_dir, manifest, deps, opts = _repaired(tmp_path)
    assert run_apply(opts, deps) == 0
    record = json.loads(manifest.read_text(encoding="utf-8"))["files"][0]
    target = Path(record["backup"]) if tamper == "backup" else sc.data / record["part"]
    target.write_bytes(target.read_bytes() + b"\n")
    assert run_verify(VerifyOptions(str(manifest), str(tmp_path / "work"))) == 2


def test_rollback_restores_original_bytes_and_refuses_if_current_not_after(tmp_path):
    sc, _plan_dir, manifest, deps, opts = _repaired(tmp_path)
    original = tree_digest(sc.data)
    assert run_apply(opts, deps) == 0
    part = sc.data / json.loads(manifest.read_text(encoding="utf-8"))["files"][0]["part"]
    patched = part.read_bytes()
    assert run_rollback(RollbackOptions(str(manifest), "0" * 64), deps) == 2  # не той маніфест
    part.write_bytes(patched + (line(ssot_bar(at(SESSION, 22, 5), 1.0, 2.0, 0.5, 1.5)) + "\n").encode())
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 2  # хтось писав після apply
    part.write_bytes(patched)
    writer = WriterScan(True, None, 1, (ProcMatch(777, "runtime.ingest.m1_ingestion_worker", ("python",)),), (), 0)
    busy = WriteDeps(now_ms=lambda: NOW, scan_writers=lambda dirs: writer, geteuid=None, load_cfg=sc.cfg)
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), busy) == 3
    assert part.read_bytes() == patched
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 0
    assert part.read_bytes() == original["XAU_USD/tf_60/part-20260726.jsonl"]
    # Повторний відкат уже відновленого: запису немає, тож і живий записувач не заважає — rc 0, байти ті самі.
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), busy) == 0
    assert part.read_bytes() == original["XAU_USD/tf_60/part-20260726.jsonl"]



def test_cli_phases_plan_apply_verify_rollback_on_copy(tmp_path, monkeypatch, capsys):
    """Склейка argparse → run_*: доказ на копії через `python -m tools.repair.first_tick_m1` (конфіг — тимчасовий)."""
    from tools.repair.first_tick_m1.__main__ import main as cli

    sc, plan_dir, _manifest, _deps, _opts = _repaired(tmp_path)
    shutil.rmtree(plan_dir)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(sc.cfg()), encoding="utf-8")
    monkeypatch.setenv("AI_ONE_CONFIG_PATH", str(config))
    copy_root = tmp_path / "copy"
    shutil.copytree(sc.data, copy_root)
    prod_before = tree_digest(sc.data)
    common_args = ["--symbol", sc.symbol, "--from", "2026-07-26", "--to", "2026-09-13", "--staging-root", str(sc.staging)]
    assert cli(["plan"] + common_args + ["--plan-dir", str(plan_dir)]) == 0
    plan_id = sha256_file(plan_dir / "PLAN.json")
    assert "FT_PLAN_ID sha256=%s" % plan_id in capsys.readouterr().out
    manifest = tmp_path / "copy-apply.json"
    assert cli(["apply", "--plan-dir", str(plan_dir), "--expect-plan-sha", plan_id, "--staging-root", str(sc.staging),
                "--data-root", str(copy_root), "--copy", "--manifest-out", str(manifest)]) == 0
    assert cli(["verify", "--apply-manifest", str(manifest), "--work-dir", str(tmp_path / "work")]) == 0
    assert tree_digest(copy_root) != prod_before and tree_digest(sc.data) == prod_before
    assert cli(["rollback", "--apply-manifest", str(manifest), "--expect-manifest-sha", sha256_file(manifest),
                "--copy"]) == 0
    restored = {k: v for k, v in tree_digest(copy_root).items() if ".bak." not in k}
    assert restored == prod_before
    assert cli(["unknown-phase"]) == 2


def _two_files_applied(tmp_path):
    """Дві переписані доби (26.07 і 27.07): відкат іде у зворотному порядку — спершу 27.07."""
    sc = Scenario(tmp_path / "prod")
    close = sc.session(SESSION, 22, 0, 3, 4055.42)
    sc.session(dt.date(2026, 7, 27), 0, 0, 3, close)
    sc.write()
    plan_dir = tmp_path / "plan"
    assert run_plan(PlanOptions(sc.symbol, SESSION, dt.date(2026, 7, 27), str(sc.staging), str(plan_dir)),
                    sc.cfg()) == 0
    deps = WriteDeps(now_ms=lambda: NOW, scan_writers=lambda dirs: CLEAR, geteuid=None, load_cfg=sc.cfg)
    original = tree_digest(sc.data)
    manifest = tmp_path / "apply.json"
    opts = ApplyOptions(str(plan_dir), sha256_file(plan_dir / "PLAN.json"), str(sc.staging), manifest_out=str(manifest))
    assert run_apply(opts, deps) == 0
    assert [f["status"] for f in json.loads(manifest.read_text(encoding="utf-8"))["files"]] == ["rewritten"] * 2
    return sc, manifest, deps, original


@pytest.mark.parametrize("interrupt", [OSError("No space left on device"), KeyboardInterrupt()])
def test_rollback_interrupted_on_second_file_rerun_completes(tmp_path, monkeypatch, interrupt):
    """Ловить попередню перевірку «sha == після для всіх»: відкат, перерваний на 2-му файлі, повторно відмовляв
    ROLLBACK_CURRENT_CHANGED — перший файл уже «до». Повторний запуск мусить завершити відкат."""
    from tools.repair.first_tick_m1 import rollback as rollback_mod

    sc, manifest, deps, original = _two_files_applied(tmp_path)
    real_rewrite, calls = rollback_mod.rewrite_atomic, []

    def failing_rewrite(path, lines, before_replace=None):
        calls.append(path)
        if len(calls) == 2:
            raise interrupt
        return real_rewrite(path, lines, before_replace=before_replace)

    monkeypatch.setattr(rollback_mod, "rewrite_atomic", failing_rewrite)
    with pytest.raises(type(interrupt)):
        run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps)
    (first_report,) = list(tmp_path.glob("apply.json.rollback-*.json"))
    report = json.loads(first_report.read_text(encoding="utf-8"))
    assert report["status"] == ("interrupted" if isinstance(interrupt, KeyboardInterrupt) else "failed")
    assert [(f["part"].rsplit("-", 1)[1], f["status"]) for f in report["files"]] == [
        ("20260727.jsonl", "restored"), ("20260726.jsonl", "not_restored")]
    monkeypatch.setattr(rollback_mod, "rewrite_atomic", real_rewrite)
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 0
    restored = {k: v for k, v in tree_digest(sc.data).items() if ".bak." not in k}
    assert restored == original
    (second_report,) = [p for p in tmp_path.glob("apply.json.rollback-*.json") if p != first_report]
    statuses = [f["status"] for f in json.loads(second_report.read_text(encoding="utf-8"))["files"]]
    assert statuses == ["already_restored", "restored"]


def test_rollback_sigterm_mid_file_finalizes_report_rc_128_plus_signum(tmp_path, monkeypatch):
    import signal

    from tools.repair.first_tick_m1 import rollback as rollback_mod

    sc, manifest, deps, original = _two_files_applied(tmp_path)
    real_rewrite = rollback_mod.rewrite_atomic

    def rewrite_then_sigterm(path, lines, before_replace=None):
        backup = real_rewrite(path, lines, before_replace=before_replace)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return backup

    monkeypatch.setattr(rollback_mod, "rewrite_atomic", rewrite_then_sigterm)
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 128 + signal.SIGTERM
    (report_path,) = list(tmp_path.glob("apply.json.rollback-*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "interrupted" and "ROLLBACK_STOPPED_BY_SIGNAL" in report["stop_reason"]
    assert [f["status"] for f in report["files"]] == ["restored"]  # вирок диска: os.replace встиг
    monkeypatch.setattr(rollback_mod, "rewrite_atomic", real_rewrite)
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 0
    assert {k: v for k, v in tree_digest(sc.data).items() if ".bak." not in k} == original


def test_rollback_signal_during_final_report_write_returns_128_plus_signum(tmp_path, monkeypatch):
    import signal

    from tools.repair.first_tick_m1.common import StopSignals

    sc, manifest, deps, original = _two_files_applied(tmp_path)
    seen = signal_during_final_write(monkeypatch, "ft_m1_rollback_v1")
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 128 + signal.SIGTERM
    assert isinstance(getattr(seen["handler"], "__self__", None), StopSignals)
    (report_path,) = list(tmp_path.glob("apply.json.rollback-*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert (report["status"], report["rc"]) == ("ok", 128 + signal.SIGTERM)
    assert "ROLLBACK_SIGNAL_DURING_FINALIZE" in report["stop_reason"]
    assert {k: v for k, v in tree_digest(sc.data).items() if ".bak." not in k} == original


def test_rollback_after_killed_apply_removes_abandoned_lock_of_this_host(tmp_path, monkeypatch):
    """apply убито SIGKILL — `.apply.lock` лишився з pid, якого вже немає: rollback не має відмовляти LOCK_HELD."""
    import socket

    from tools.repair.first_tick_m1 import common

    sc, manifest, deps, original = _two_files_applied(tmp_path)
    plan_dir = Path(json.loads(manifest.read_text(encoding="utf-8"))["plan_dir"])
    holder = {"pid": 999999, "host": socket.gethostname(), "started_at_utc": "2026-09-26T11:59:00Z"}
    (plan_dir / ".apply.lock").write_bytes(common.canonical_json_bytes(holder))
    monkeypatch.setattr(common, "pid_alive", lambda pid: pid != 999999)
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 0
    assert {k: v for k, v in tree_digest(sc.data).items() if ".bak." not in k} == original
    assert not (plan_dir / ".apply.lock").exists()


def test_rollback_bar_appended_between_check_and_backup_link_cancels_restore_rc3(tmp_path, monkeypatch):
    """Дзеркало apply: бар, дописаний між звіркою «після» і os.link, відкат стирав би з part-файла при rc 0."""
    from tools.repair.first_tick_m1 import rollback as rollback_mod

    sc, manifest, deps, _original = _two_files_applied(tmp_path)
    part = sc.data / "XAU_USD" / "tf_60" / "part-20260727.jsonl"  # відкат іде у зворотному порядку — 27.07 перший
    patched = part.read_bytes()
    appended = (line(ssot_bar(at(dt.date(2026, 7, 27), 0, 9), 1.0, 2.0, 0.5, 1.5)) + "\n").encode()
    real_rewrite = rollback_mod.rewrite_atomic

    def append_then_rewrite(path, lines, before_replace=None):
        with open(path, "ab") as fh:
            fh.write(appended)
        return real_rewrite(path, lines, before_replace=before_replace)

    monkeypatch.setattr(rollback_mod, "rewrite_atomic", append_then_rewrite)
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 3
    assert part.read_bytes() == patched + appended
    (report_path,) = list(tmp_path.glob("apply.json.rollback-*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "interrupted" and "ROLLBACK_CURRENT_CHANGED_DURING_ROLLBACK" in report["stop_reason"]
    assert [(f["status"], f.get("restore_aborted")) for f in report["files"]] == [("not_restored", True)]


def test_rollback_backup_deleted_refused_rc2_before_any_write(tmp_path, capsys):
    """Ловить сирий FileNotFoundError: бекап apply прибрано (гігієна) — rollback падав трасою, а не відмовою."""
    sc, manifest, deps, _original = _two_files_applied(tmp_path)
    Path(json.loads(manifest.read_text(encoding="utf-8"))["files"][1]["backup"]).unlink()
    before = tree_digest(sc.data)
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest)), deps) == 2
    assert "ROLLBACK_BACKUP_MISSING" in capsys.readouterr().out
    assert tree_digest(sc.data) == before and list(tmp_path.glob("apply.json.rollback-*.json")) == []
