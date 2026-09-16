"""Фаза apply: пише лише те, що в плані, лише у файл, який план бачив, лише при зупинених записувачах (ADR-0096 §3.3 B, B7).

Навіщо. Це єдиний крок, що змінює SSOT на проді. Кожна рейка — окремий спосіб зіпсувати історію: план не той
(sha), вхід змінився після плану (нова доба, дописаний бар, перезабраний staging), записувач живий (дописи
підуть у бекап), ринок відкритий, файл належить іншому користувачеві, план розійшовся з файлом. Скан /proc,
годинник і euid ін'єктовані; файли — справжні в tmp_path.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import shutil
import signal
from pathlib import Path

import pytest

from ft_m1_support import Scenario, at, line, signal_during_final_write, ssot_bar, staged_row, tree_digest
from tools.repair.first_tick_m1 import plan_io
from tools.repair.first_tick_m1.apply import ApplyOptions, run_apply
from tools.repair.first_tick_m1.common import canonical_json_bytes, sha256_bytes, sha256_file
from tools.repair.first_tick_m1.plan import PlanOptions, run_plan
from tools.repair.first_tick_m1.staging import day_paths
from tools.repair.first_tick_m1.target_rails import WriteDeps
from tools.repair.first_tick_m1.writers_guard import ProcMatch, WriterScan
from tools.repair.jsonl_rewrite import read_lines

SUN, MON, TUE, WED = (dt.date(2026, 7, 26) + dt.timedelta(days=i) for i in range(4))
SATURDAY_NOON = at(dt.date(2026, 9, 12), 12, 0)
CLEAR = WriterScan(True, None, 42, (), (), 0)
WRITER = WriterScan(True, None, 42, (ProcMatch(777, "runtime.ingest.m1_ingestion_worker", ("python", "-m", "x")),), (), 0)


def _part(sc, day):
    return sc.data / "XAU_USD" / "tf_60" / ("part-%s.jsonl" % day.strftime("%Y%m%d"))


@pytest.fixture()
def planned(tmp_path):
    sc = Scenario(tmp_path / "prod")
    close = sc.session(SUN, 22, 0, 3, 4055.42)
    close = sc.session(MON, 0, 0, 5, close)
    duplicate_key = at(MON, 0, 2)
    sc.add(MON, text=line(ssot_bar(duplicate_key, 1.0, 9999.0, 0.5, 2.0, extensions={"partial": True})))
    sc.add(MON, text="not json at all")
    sc.session(TUE, 0, 0, 3, close)
    sc.add(WED, ssot_bar(at(WED, 0, 0), 11.0, 11.4, 10.9, 11.2), staged_row(at(WED, 0, 0), 11.0, 11.4, 10.9, 11.2))
    sc.write()
    plan_dir = tmp_path / "plan"
    assert run_plan(PlanOptions(sc.symbol, MON, WED, str(sc.staging), str(plan_dir)), sc.cfg()) == 0
    plan_id = sha256_file(plan_dir / "PLAN.json")
    return sc, plan_dir, plan_id


def _deps(sc, scans=None, now=SATURDAY_NOON, geteuid=None):
    queue = list(scans or [])

    def scan(dirs):
        return queue.pop(0) if len(queue) > 1 else (queue[0] if queue else CLEAR)

    return WriteDeps(now_ms=lambda: now, scan_writers=scan, geteuid=geteuid, load_cfg=sc.cfg)


def _opts(sc, plan_dir, plan_id, tmp_path, **kw):
    base = dict(plan_dir=str(plan_dir), expect_plan_sha=plan_id, staging_root=str(sc.staging),
                manifest_out=str(tmp_path / "manifests" / "apply.json"))
    base.update(kw)
    return ApplyOptions(**base)


def _manifest(tmp_path):
    return json.loads((tmp_path / "manifests" / "apply.json").read_text(encoding="utf-8"))


def _backups(sc):
    return sorted(p.name for p in (sc.data / "XAU_USD" / "tf_60").iterdir() if ".bak." in p.name)


def test_apply_rewrites_only_planned_winner_lines_byte_for_byte(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    before = read_lines(str(_part(sc, MON)))
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 0
    after = read_lines(str(_part(sc, MON)))
    entries = plan_io.load_plan(str(plan_dir)).entries["20260727"]
    replaced = {e["line"] for e in entries if e["cat"] == "REPLACE"}
    assert len(after) == len(before) and replaced == {0, 1, 2, 3, 4}
    for index, (old, new) in enumerate(zip(before, after)):
        if index not in replaced:
            assert new == old  # дублікат-партіал і нерозбірний рядок — байт-у-байт
            continue
        old_bar, new_bar = json.loads(old), json.loads(new)
        assert list(old_bar) == list(new_bar)
        assert {k: v for k, v in old_bar.items() if k not in ("o", "h", "low")} == \
               {k: v for k, v in new_bar.items() if k not in ("o", "h", "low")}
        assert old_bar["o"] != new_bar["o"]
    assert _manifest(tmp_path)["status"] == "ok"
    assert read_lines(str(_part(sc, WED))) == [line(ssot_bar(at(WED, 0, 0), 11.0, 11.4, 10.9, 11.2))]


def test_apply_part_input_changed_rc2_nothing_written_no_backup(planned, tmp_path):
    """Ловить звірку лише переписуваних файлів: змінено файл БЕЗ жодного REPLACE (середа)."""
    sc, plan_dir, plan_id = planned
    wed = _part(sc, WED)
    wed.write_bytes(wed.read_bytes() + (line(ssot_bar(at(WED, 0, 1), 11.2, 11.3, 11.1, 11.25)) + "\n").encode())
    before = tree_digest(sc.data)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 2
    assert tree_digest(sc.data) == before and _backups(sc) == []
    assert "APPLY_PLAN_INPUT_CHANGED" in _manifest(tmp_path)["stop_reason"]


@pytest.mark.parametrize("which", ["planned_day_file", "context_manifest"])
def test_apply_staging_input_changed_rc2(planned, tmp_path, which):
    sc, plan_dir, plan_id = planned
    day_file, _ = day_paths(sc.staging, sc.symbol, MON)
    _, context_manifest = day_paths(sc.staging, sc.symbol, SUN)
    target = Path(day_file if which == "planned_day_file" else context_manifest)
    target.write_bytes(target.read_bytes().replace(b"\n", b" \n", 1))
    before = tree_digest(sc.data)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 2
    assert tree_digest(sc.data) == before


def test_apply_writer_running_rc3_nothing_written(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    before = tree_digest(sc.data)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc, scans=[WRITER])) == 3
    assert tree_digest(sc.data) == before
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "refused" and manifest["guard"]["writers"][0]["pid"] == 777


def test_apply_writer_check_unavailable_rc3(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    before = tree_digest(sc.data)
    unavailable = WriterScan(False, "proc_hidepid", 0, (), (), 0)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc, scans=[unavailable])) == 3
    assert tree_digest(sc.data) == before
    assert "APPLY_WRITERS_CHECK_UNAVAILABLE" in _manifest(tmp_path)["stop_reason"]


def test_apply_writer_appears_mid_run_rc3_manifest_interrupted(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    tue_before = _part(sc, TUE).read_bytes()
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc, scans=[CLEAR, CLEAR, WRITER])) == 3
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "interrupted"
    assert [(f["day"], f["status"]) for f in manifest["files"]] == [("20260727", "rewritten"), ("20260728", "not_started")]
    assert _part(sc, TUE).read_bytes() == tue_before


def test_apply_existing_manifest_refused_rc2_first_manifest_bytes_untouched(planned, tmp_path):
    """Ловить перезапис маніфесту: повторний apply з тим самим --manifest-out затирав опис частково застосованого
    прогону — rollback більше не знав би, що понеділок уже переписано."""
    sc, plan_dir, plan_id = planned
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc, scans=[CLEAR, CLEAR, WRITER])) == 3
    manifest_path = tmp_path / "manifests" / "apply.json"
    first = manifest_path.read_bytes()
    data_before = tree_digest(sc.data)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 2
    assert manifest_path.read_bytes() == first and tree_digest(sc.data) == data_before
    assert not (plan_dir / ".apply.lock").exists()


def test_apply_input_changed_after_checks_stops_before_rewrite_rc3(planned, tmp_path):
    """Ловить звірку sha лише на старті: неприхований записувач дописав бар між рейками і переписом файла."""
    sc, plan_dir, plan_id = planned
    calls = []
    appended = (line(ssot_bar(at(MON, 0, 9), 1.0, 2.0, 0.5, 1.5)) + "\n").encode()

    def sneaky_scan(dirs):
        calls.append(dirs)
        if len(calls) == 2:  # перевірка перед першим файлом — «записувач» дописує саме зараз
            with open(_part(sc, MON), "ab") as fh:
                fh.write(appended)
        return CLEAR

    deps = WriteDeps(now_ms=lambda: SATURDAY_NOON, scan_writers=sneaky_scan, geteuid=None, load_cfg=sc.cfg)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), deps) == 3
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "interrupted" and "APPLY_INPUT_CHANGED_DURING_APPLY" in manifest["stop_reason"]
    assert _backups(sc) == [] and _part(sc, MON).read_bytes().endswith(appended)


def test_apply_manifest_before_after_sha_and_backup_bytes_equal_original(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    originals = {day: _part(sc, day).read_bytes() for day in (MON, TUE)}
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 0
    manifest = _manifest(tmp_path)
    assert manifest["plan_id"] == plan_id and manifest["target"] == "prod"
    for record, day in zip(manifest["files"], (MON, TUE)):
        assert record["sha256_before"] == sha256_bytes(originals[day])
        assert record["sha256_after_actual"] == record["sha256_after_planned"] == sha256_file(_part(sc, day))
        assert Path(record["backup"]).read_bytes() == originals[day]


def test_apply_expect_plan_sha_mismatch_rc2(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    before = tree_digest(sc.data)
    assert run_apply(_opts(sc, plan_dir, "0" * 64, tmp_path), _deps(sc)) == 2
    assert tree_digest(sc.data) == before and not (tmp_path / "manifests").exists()


@pytest.mark.parametrize("case", ["copy_flag_on_prod", "copy_root_without_flag"])
def test_apply_copy_flag_rules_rc2(planned, tmp_path, case):
    sc, plan_dir, plan_id = planned
    copy_root = tmp_path / "copy"
    shutil.copytree(sc.data, copy_root)
    kwargs = {"copy": True} if case == "copy_flag_on_prod" else {"data_root": str(copy_root)}
    before = (tree_digest(sc.data), tree_digest(copy_root))
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path, **kwargs), _deps(sc)) == 2
    assert (tree_digest(sc.data), tree_digest(copy_root)) == before


def test_apply_owner_mismatch_rc2(planned, tmp_path):
    """Ловить запис від іншого користувача (root): нові файли стали б чужими для живих записувачів."""
    sc, plan_dir, plan_id = planned
    before = tree_digest(sc.data)
    foreign_euid = os.stat(_part(sc, MON)).st_uid + 1
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc, geteuid=lambda: foreign_euid)) == 2
    assert tree_digest(sc.data) == before
    own_euid = os.stat(_part(sc, MON)).st_uid
    opts = _opts(sc, plan_dir, plan_id, tmp_path, manifest_out=str(tmp_path / "manifests" / "own.json"))
    assert run_apply(opts, _deps(sc, geteuid=lambda: own_euid)) == 0


def test_apply_market_open_on_prod_rc3(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    before = tree_digest(sc.data)
    monday_trading = at(dt.date(2026, 9, 14), 10, 0)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc, now=monday_trading)) == 3
    assert tree_digest(sc.data) == before
    copy_root = tmp_path / "copy"
    shutil.copytree(sc.data, copy_root)

    def no_scan(dirs):
        raise AssertionError("копія не сканує /proc")

    copy_deps = WriteDeps(now_ms=lambda: monday_trading, scan_writers=no_scan, geteuid=None, load_cfg=sc.cfg)
    opts = _opts(sc, plan_dir, plan_id, tmp_path, copy=True, data_root=str(copy_root),
                 manifest_out=str(tmp_path / "manifests" / "copy.json"))
    assert run_apply(opts, copy_deps) == 0
    assert tree_digest(sc.data) == before and tree_digest(copy_root) != before


def _tamper(plan_dir, mutate):
    plan_path = Path(plan_dir) / "PLAN.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mutate(plan, Path(plan_dir))
    plan_path.write_bytes(canonical_json_bytes(plan))
    return sha256_file(plan_path)


def _change_old_value(plan, plan_dir):
    item = next(f for f in plan["files"] if f["day"] == "20260727")
    entries_path = plan_dir / item["entries"]
    records = [json.loads(x) for x in entries_path.read_text(encoding="utf-8").splitlines()]
    first = next(r for r in records if r["cat"] == "REPLACE")
    first["old"]["c"] = first["old"]["c"] + 1.0
    data = b"".join(canonical_json_bytes(r) for r in records)
    entries_path.write_bytes(data)
    item["entries_sha256"] = sha256_bytes(data)


def _change_sha_after(plan, plan_dir):
    next(f for f in plan["files"] if f["day"] == "20260727")["sha256_after"] = "f" * 64


@pytest.mark.parametrize("mutate", [_change_old_value, _change_sha_after])
def test_apply_plan_diverged_rc1_file_untouched(planned, tmp_path, mutate):
    sc, plan_dir, _plan_id = planned
    new_id = _tamper(plan_dir, mutate)
    before = tree_digest(sc.data)
    assert run_apply(_opts(sc, plan_dir, new_id, tmp_path), _deps(sc)) == 1
    assert tree_digest(sc.data) == before
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "failed" and "APPLY_PLAN_DIVERGED" in manifest["stop_reason"]


@pytest.mark.skipif(os.name == "nt", reason="права доступу POSIX")
def test_apply_keeps_file_mode(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    os.chmod(_part(sc, MON), 0o666)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 0
    assert os.stat(_part(sc, MON)).st_mode & 0o777 == 0o666


def test_apply_lock_held_rc2(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    (plan_dir / ".apply.lock").write_text('{"pid": 1}', encoding="utf-8")
    before = tree_digest(sc.data)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 2
    assert tree_digest(sc.data) == before


def test_apply_unexpected_error_mid_write_leaves_accurate_manifest(planned, tmp_path, monkeypatch):
    """Диск/права відмовили на другому файлі: маніфест — failed з першим rewritten, не вічний «running»."""
    sc, plan_dir, plan_id = planned
    from tools.repair.first_tick_m1 import apply as apply_mod

    real_rewrite, calls = apply_mod.rewrite_atomic, []

    def failing_rewrite(path, lines, before_replace=None):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("No space left on device")
        return real_rewrite(path, lines, before_replace=before_replace)

    monkeypatch.setattr(apply_mod, "rewrite_atomic", failing_rewrite)
    with pytest.raises(OSError):
        run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc))
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "failed" and "APPLY_UNEXPECTED_ERROR" in manifest["stop_reason"]
    assert [f["status"] for f in manifest["files"]] == ["rewritten", "not_started"]
    assert not (plan_dir / ".apply.lock").exists()


def _interrupt_after_replace(monkeypatch, on_call, action):
    """rewrite_atomic, що на виклику `on_call` після справжнього os.replace виконує `action` (сигнал, kill)."""
    from tools.repair.first_tick_m1 import apply as apply_mod

    real_rewrite, calls = apply_mod.rewrite_atomic, []

    def rewrite(path, lines, before_replace=None):
        calls.append(path)
        backup = real_rewrite(path, lines, before_replace=before_replace)
        if len(calls) == on_call:
            action()
        return backup

    monkeypatch.setattr(apply_mod, "rewrite_atomic", rewrite)


class _Killed(BaseException):
    """SIGKILL: процес зник, фіналізації не буде — тест бере знімок маніфесту в цю мить."""


def test_apply_killed_right_after_replace_rollback_and_verify_see_rewritten_file(planned, tmp_path, monkeypatch):
    """Ловить статус лише в памʼяті: kill одразу після os.replace лишав маніфест без файла — rollback rc=0 не
    відкочував пропатчений файл. Знімок маніфесту в мить kill мусить мати намір і бекап."""
    from tools.repair.first_tick_m1.rollback import RollbackOptions, run_rollback
    from tools.repair.first_tick_m1.verify import VerifyOptions, run_verify

    sc, plan_dir, plan_id = planned
    originals = {day: _part(sc, day).read_bytes() for day in (MON, TUE)}
    killed = tmp_path / "killed.json"

    def kill():
        shutil.copyfile(tmp_path / "manifests" / "apply.json", killed)
        raise _Killed()

    _interrupt_after_replace(monkeypatch, 1, kill)
    with pytest.raises(_Killed):
        run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc))
    snapshot = json.loads(killed.read_text(encoding="utf-8"))
    record = snapshot["files"][0]
    assert (snapshot["status"], record["status"]) == ("running", "replacing")
    assert Path(record["backup"]).read_bytes() == originals[MON] and _part(sc, MON).read_bytes() != originals[MON]
    # Частковий apply: переписаний файл звірено без порушень, але план НЕ покритий — verify мусить бути червоним
    # (інакше ранбук читає rc=0 як «полагоджено»); свідоме прийняття часткового — --allow-incomplete.
    assert run_verify(VerifyOptions(str(killed), str(tmp_path / "work"))) == 1
    assert run_verify(VerifyOptions(str(killed), str(tmp_path / "work2"), allow_incomplete=True)) == 0
    assert run_rollback(RollbackOptions(str(killed), sha256_file(killed)), _deps(sc)) == 0
    assert {day: _part(sc, day).read_bytes() for day in (MON, TUE)} == originals


def test_apply_killed_before_replace_file_is_not_treated_as_rewritten(planned, tmp_path, monkeypatch):
    """Намір записано, бекап є, але os.replace не відбувся: rollback/verify не мають права «відкотити» чи звіряти."""
    from tools.repair.first_tick_m1 import apply as apply_mod
    from tools.repair.first_tick_m1.rollback import RollbackOptions, run_rollback
    from tools.repair.first_tick_m1.verify import VerifyOptions, run_verify

    sc, plan_dir, plan_id = planned
    original = _part(sc, MON).read_bytes()
    killed = tmp_path / "killed.json"
    real_rewrite = apply_mod.rewrite_atomic

    def rewrite(path, lines, before_replace=None):
        def hook(backup):
            before_replace(backup)
            shutil.copyfile(tmp_path / "manifests" / "apply.json", killed)
            raise _Killed()

        return real_rewrite(path, lines, before_replace=hook)

    monkeypatch.setattr(apply_mod, "rewrite_atomic", rewrite)
    with pytest.raises(_Killed):
        run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc))
    assert json.loads(killed.read_text(encoding="utf-8"))["files"][0]["status"] == "replacing"
    assert _part(sc, MON).read_bytes() == original
    finalized = _manifest(tmp_path)["files"][0]
    assert (finalized["status"], finalized["replace_aborted"]) == ("not_started", True)
    assert run_rollback(RollbackOptions(str(killed), sha256_file(killed)), _deps(sc)) == 0
    assert _part(sc, MON).read_bytes() == original
    # Жодного файла не переписано: порушень немає, але й план не покритий → rc=1 з FT_VERIFY_INCOMPLETE.
    assert run_verify(VerifyOptions(str(killed), str(tmp_path / "work"))) == 1
    assert run_verify(VerifyOptions(str(killed), str(tmp_path / "work2"), allow_incomplete=True)) == 0


def test_apply_sigterm_after_replace_finalizes_manifest_rc_128_plus_signum(planned, tmp_path, monkeypatch):
    """Ловить відсутність обробника SIGTERM: сигнал посеред другого файла — маніфест interrupted, обидва файли
    rewritten (другий — вироком диска), rc 143, лок знято."""
    sc, plan_dir, plan_id = planned
    handler_before = signal.getsignal(signal.SIGTERM)

    def sigterm():
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    _interrupt_after_replace(monkeypatch, 2, sigterm)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 128 + signal.SIGTERM
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "interrupted" and "APPLY_STOPPED_BY_SIGNAL" in manifest["stop_reason"]
    assert [f["status"] for f in manifest["files"]] == ["rewritten", "rewritten"]
    assert manifest["files"][1]["sha256_after_actual"] == sha256_file(_part(sc, TUE))
    assert not (plan_dir / ".apply.lock").exists()
    assert signal.getsignal(signal.SIGTERM) == handler_before


def test_apply_keyboard_interrupt_after_replace_finalizes_manifest_and_reraises(planned, tmp_path, monkeypatch):
    """Ловить `except Exception`: Ctrl+C (KeyboardInterrupt) лишав маніфест «running» без переписаного файла."""
    sc, plan_dir, plan_id = planned

    def ctrl_c():
        raise KeyboardInterrupt()

    _interrupt_after_replace(monkeypatch, 1, ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc))
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "interrupted" and "APPLY_INTERRUPTED" in manifest["stop_reason"]
    assert [f["status"] for f in manifest["files"]] == ["rewritten", "not_started"]


def _link_dir(link, target):
    """Каталог-посилання: symlink, а на Windows без прав — junction (його теж розвʼязує realpath)."""
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlink на каталог недоступний")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def _link_file(link, target):
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlink на файл недоступний (Windows без Developer Mode)")


def _copy_opts(sc, plan_dir, plan_id, tmp_path, copy_root):
    return _opts(sc, plan_dir, plan_id, tmp_path, copy=True, data_root=str(copy_root),
                 manifest_out=str(tmp_path / "manifests" / "copy.json"))


def test_apply_copy_with_tf_dir_linked_into_prod_refused_rc2_prod_untouched(planned, tmp_path):
    """Ловить перевірку лише кореня копії: tf_60 копії — посилання на прод, apply --copy переписав би прод повз
    рейки записувачів, ринку і власника."""
    sc, plan_dir, plan_id = planned
    copy_root = tmp_path / "copy"
    (copy_root / "XAU_USD").mkdir(parents=True)
    _link_dir(copy_root / "XAU_USD" / "tf_60", sc.data / "XAU_USD" / "tf_60")
    prod_before = tree_digest(sc.data)
    assert run_apply(_copy_opts(sc, plan_dir, plan_id, tmp_path, copy_root), _deps(sc)) == 2
    assert tree_digest(sc.data) == prod_before and _backups(sc) == []


@pytest.mark.parametrize("linked", ["part", "tmp"])
def test_apply_copy_with_part_or_tmp_symlink_into_prod_refused_rc2(planned, tmp_path, linked):
    """`.tmp` поруч із part-файлом копії, що веде у прод: open(tmp, "w") переписав би прод-файл цілком."""
    sc, plan_dir, plan_id = planned
    copy_root = tmp_path / "copy"
    shutil.copytree(sc.data, copy_root)
    prod_part = _part(sc, MON)
    copy_part = copy_root / "XAU_USD" / "tf_60" / prod_part.name
    if linked == "part":
        copy_part.unlink()
        _link_file(copy_part, prod_part)
    else:
        _link_file(Path(str(copy_part) + ".tmp"), prod_part)
    prod_before = tree_digest(sc.data)
    assert run_apply(_copy_opts(sc, plan_dir, plan_id, tmp_path, copy_root), _deps(sc)) == 2
    assert tree_digest(sc.data) == prod_before


def test_apply_prod_part_symlink_outside_root_refused_rc2(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    outside = tmp_path / "outside" / _part(sc, MON).name
    outside.parent.mkdir()
    shutil.copyfile(_part(sc, MON), outside)
    _part(sc, MON).unlink()
    _link_file(_part(sc, MON), outside)
    before = (tree_digest(sc.data), outside.read_bytes())
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 2
    assert (tree_digest(sc.data), outside.read_bytes()) == before


def test_rollback_copy_with_tf_dir_linked_into_prod_refused_rc2(planned, tmp_path):
    """Дзеркало для rollback: після apply на копії її tf_60 підмінено посиланням на прод (з тими самими байтами
    «після» і бекапами) — відкат писав би в прод."""
    from tools.repair.first_tick_m1.rollback import RollbackOptions, run_rollback

    sc, plan_dir, plan_id = planned
    copy_root = tmp_path / "copy"
    shutil.copytree(sc.data, copy_root)
    assert run_apply(_copy_opts(sc, plan_dir, plan_id, tmp_path, copy_root), _deps(sc)) == 0
    copy_tf = copy_root / "XAU_USD" / "tf_60"
    for name in os.listdir(copy_tf):  # прод «виглядає» як пропатчена копія: без рейки відкат пройшов би
        shutil.copyfile(copy_tf / name, sc.data / "XAU_USD" / "tf_60" / name)
    copy_tf.rename(copy_root / "XAU_USD" / "tf_60_real")
    _link_dir(copy_tf, sc.data / "XAU_USD" / "tf_60")
    prod_before = tree_digest(sc.data)
    manifest = tmp_path / "manifests" / "copy.json"
    assert run_rollback(RollbackOptions(str(manifest), sha256_file(manifest), copy=True), _deps(sc)) == 2
    assert tree_digest(sc.data) == prod_before


def test_apply_signal_during_final_manifest_write_returns_128_plus_signum(planned, tmp_path, monkeypatch):
    """Ловить фіналізацію поза обробниками: сигнал у мить фінального запису маніфесту губився (rc 0) або вбивав процес
    з маніфестом `running`. Робота завершена — статус ok, але rc і маніфест кажуть про сигнал."""
    from tools.repair.first_tick_m1.common import StopSignals

    sc, plan_dir, plan_id = planned
    seen = signal_during_final_write(monkeypatch, "ft_m1_apply_v1")
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 128 + signal.SIGTERM
    assert isinstance(getattr(seen["handler"], "__self__", None), StopSignals)
    manifest = _manifest(tmp_path)
    assert (manifest["status"], manifest["rc"]) == ("ok", 128 + signal.SIGTERM)
    assert "APPLY_SIGNAL_DURING_FINALIZE" in manifest["stop_reason"]
    assert [f["status"] for f in manifest["files"]] == ["rewritten", "rewritten"]
    assert not (plan_dir / ".apply.lock").exists()


def test_apply_bar_appended_between_sha_check_and_backup_link_cancels_replace_rc3(planned, tmp_path, monkeypatch):
    """Ловить вікно між звіркою sha і os.link: бар, дописаний саме тоді, лягав лише в бекап (жорсткий лінк), а
    os.replace стирав його з part-файла — і apply звітував rc 0."""
    from tools.repair.first_tick_m1 import apply as apply_mod

    sc, plan_dir, plan_id = planned
    original = _part(sc, MON).read_bytes()
    appended = (line(ssot_bar(at(MON, 0, 9), 1.0, 2.0, 0.5, 1.5)) + "\n").encode()
    real_rewrite = apply_mod.rewrite_atomic

    def append_then_rewrite(path, lines, before_replace=None):
        with open(path, "ab") as fh:
            fh.write(appended)
        return real_rewrite(path, lines, before_replace=before_replace)

    monkeypatch.setattr(apply_mod, "rewrite_atomic", append_then_rewrite)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc)) == 3
    assert _part(sc, MON).read_bytes() == original + appended and not Path(str(_part(sc, MON)) + ".tmp").exists()
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "interrupted" and "APPLY_INPUT_CHANGED_DURING_APPLY" in manifest["stop_reason"]
    record = manifest["files"][0]
    assert (record["status"], record["replace_aborted"]) == ("not_started", True)
    assert [f["status"] for f in manifest["files"]] == ["not_started", "not_started"]


def test_apply_planned_part_deleted_after_plan_refused_rc2_not_raw_error(planned, tmp_path):
    """Ловить сирий FileNotFoundError: перевірка власника (os.stat) ішла до звірки входів, і зниклий після плану
    part-файл валив apply APPLY_UNEXPECTED_ERROR замість відмови APPLY_PLAN_INPUT_CHANGED."""
    sc, plan_dir, plan_id = planned
    _part(sc, TUE).unlink()
    before = tree_digest(sc.data)
    own_euid = os.stat(_part(sc, MON)).st_uid
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), _deps(sc, geteuid=lambda: own_euid)) == 2
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "refused" and "APPLY_PLAN_INPUT_CHANGED" in manifest["stop_reason"]
    assert tree_digest(sc.data) == before


def test_apply_part_deleted_mid_run_interrupted_rc3(planned, tmp_path):
    sc, plan_dir, plan_id = planned
    calls = []

    def scan_deleting_tuesday(dirs):
        calls.append(dirs)
        if len(calls) == 3:  # перевірка перед другим файлом
            _part(sc, TUE).unlink()
        return CLEAR

    deps = WriteDeps(now_ms=lambda: SATURDAY_NOON, scan_writers=scan_deleting_tuesday, geteuid=None, load_cfg=sc.cfg)
    assert run_apply(_opts(sc, plan_dir, plan_id, tmp_path), deps) == 3
    manifest = _manifest(tmp_path)
    assert manifest["status"] == "interrupted" and "APPLY_INPUT_CHANGED_DURING_APPLY" in manifest["stop_reason"]
    assert [f["status"] for f in manifest["files"]] == ["rewritten", "not_started"]


def test_apply_plan_of_previous_format_refused_rc2_staging_stays_valid(planned, tmp_path, capsys):
    """Ловить план старих правил класифікації: ft_m1_plan_v1 міг назвати REPLACE ключі, які тепер SKIP_WOULD_HIDE чи
    SKIP_RANGE_CHANGED_BEYOND_STRETCH, і apply його приймав. Формат плану бампнуто окремо від staging."""
    sc, plan_dir, _plan_id = planned
    legacy_id = _tamper(plan_dir, lambda plan, _dir: plan.update(format="ft_m1_plan_v1"))
    before = tree_digest(sc.data)
    assert run_apply(_opts(sc, plan_dir, legacy_id, tmp_path), _deps(sc)) == 2
    assert "APPLY_PLAN_FORMAT_UNSUPPORTED detail=format=ft_m1_plan_v1" in capsys.readouterr().out
    assert tree_digest(sc.data) == before and not (tmp_path / "manifests").exists()
    _day_file, staging_manifest = day_paths(sc.staging, sc.symbol, MON)
    written = json.loads(Path(staging_manifest).read_text(encoding="utf-8"))
    assert (written["format"], written["tool_version"]) == ("ft_m1_staging_day_v2", 1)  # staging до бампу — чинний
