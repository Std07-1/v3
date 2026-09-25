"""tools/repair/settle_daily.py — нічний settle у денну перерву (ADR-0103 §3.2, S3c2): прогін від забору до спостереження.

Порядок — той самий, що в ручних прогонах S4 на проді (settle_day.sh 25–26.09):
  перерва й дедлайн (settle_daily_plan, сезонні календарі) → префлайт: код = origin/main, чисте дерево, диск →
  забір M1 вікна і D1 усієї історії (fetch_archive від smc з .venv37, окремий cwd, креди з /proc сайдкара, watchdog,
  повтори) → стоп smc-ws/smc-preview/smc-fxcm (smc-ticks не чіпаємо) → writers_guard → tgz data_v3 + sha → строго
  послідовно (рейки прод-шляху бачать сусідній ремонтний інструмент як записувача): по символу settle_m1 →
  season_apply --changed-m1, потім d1_native_settle → старт fxcm → PRIME → preview + ws → стан → ретеншн →
  спостереження.

Коди виходу: 0 — прогін виконано або не час (не перерва / розклад вимкнено); 3 — відмова до запису, дані не змінено;
4 — дані устояно, але після старту записувачів є проблеми; 5 — крок даних упав або дедлайн, data_v3 відкочено з tgz;
6 — відкат не вдався (CRITICAL, дані — після settle, кожен файл консистентний). Записувачі стартують у finally навіть
після винятку; друга рейка — обгортка ops/settle_daily.sh (flock, timeout, trap).

data_root поза проду (копія) — репетиція: supervisor не чіпається, решта та сама (writers_guard гучно пропускає копію).
--dry-run — забір і плани без запису. --ignore-break — лише для репетиції або dry-run: на проді запис поза перервою
заборонений.

    python -m tools.repair.settle_daily --scheduled | --manual [--dry-run] [--ignore-break] [--code-dir /opt/smc-v3]
        [--data-root <code-dir>/data_v3] [--py37 <code-dir>/.venv37/bin/python] [--log-dir /var/log/smc-v3]
        [--fetch-user smc] [--work-dir <m1_settle.work_dir>]
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.config_loader import M1SettlePolicy, load_system_config, m1_settle_policy
from runtime.ingest.tick_common import calendar_for_symbol
from tools.repair import settle_daily_plan as sp
from tools.repair.partfile_io import WritersGuardRefused, is_prod_data_root, writers_guard

log = logging.getLogger("settle_daily")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXIT_OK, EXIT_USAGE, EXIT_REFUSED, EXIT_OBSERVE, EXIT_ROLLED_BACK, EXIT_ROLLBACK_FAILED = 0, 2, 3, 4, 5, 6
RC_TIMEOUT = 124  # як у coreutils timeout
PROGRAMS_STOP = ("smc:smc-ws", "smc:smc-preview", "smc:smc-fxcm")  # читачі першими, інжест останнім
PROGRAM_INGEST, PROGRAMS_READERS = "smc:smc-fxcm", ("smc:smc-preview", "smc:smc-ws")
PRIME_MARKER = "M1_POLLER_REDIS_PRIME"
PRIME_WAIT_S, PRIME_POLL_S = 120, 2  # PRIME на проді ~10 с після старту; довше — гучна проблема, читачі стартують
STOP_SETTLE_S = 3  # supervisorctl stop повертається після виходу процесу; пауза — запас на зникнення з /proc
MIN_DATA_WINDOW_S = 900  # стоп записувачів лише, якщо до дедлайну ≥ 15 хв (бекап + дані + старт на проді ~3 хв)
REHEARSAL_WINDOW_S = 45 * 60  # --ignore-break: умовна перерва репетиції
OBSERVE_CHECKS = 4
OBSERVED_LOGS = ("m1_ingestion_worker.err.log", "broker_sidecar.err.log", "preview.stderr.log", "ws_server.stderr.log")
LOG_ALARM = re.compile(r"ERROR|Traceback|CRITICAL")
PREFLIGHT_UNTRACKED_OK = ("?? .env.save",)  # відомий артефакт проду (ранбук вікна 24.09)
BACKUP_RE = re.compile(r"^data_v3\.pre-sd-(\d{8}T\d{6}Z)\.tgz(\.sha256)?$")
# маркер у work_dir: записувачів зупинив саме прогін — trap обгортки стартує їх лише за ним (не тих, кого власник
# зупинив навмисно)
WRITERS_STOPPED_MARKER = "writers_stopped_by_settle"


@dataclasses.dataclass(frozen=True)
class Paths:
    code_dir: str
    data_root: str
    config: str
    py: str
    py37: str
    log_dir: str
    work_dir: str
    fetch_user: str

    @property
    def fxcm_cwd(self) -> str:  # SDK пише туди логи й лок; спільний cwd із сайдкаром — 'Global lock initialization failed'
        return os.path.join(self.work_dir, "fxcm_cwd")


class Runner:
    """Підпроцеси прогону; вивід кожного — у <run_dir>/logs/<name>.log. Таймаут убиває всю групу процесів (sudo не
    передає SIGKILL дитині — інакше завислий забір пережив би прогін)."""

    def __init__(self, logs_dir: str) -> None:
        self.logs_dir = logs_dir
        os.makedirs(logs_dir, exist_ok=True)

    def run(self, name: str, cmd: Sequence[str], *, cwd: Optional[str] = None, timeout_s: Optional[float] = None) -> int:
        t0 = time.monotonic()
        with open(os.path.join(self.logs_dir, name + ".log"), "wb") as out:
            proc = subprocess.Popen(list(cmd), cwd=cwd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                rc = RC_TIMEOUT
        log.info("STEP %s rc=%d secs=%.1f", name, rc, time.monotonic() - t0)
        return rc

    def output(self, name: str) -> str:
        with open(os.path.join(self.logs_dir, name + ".log"), encoding="utf-8", errors="replace") as fh:
            return fh.read()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class DailySettle:
    def __init__(self, cfg: Dict[str, Any], paths: Paths, policy: M1SettlePolicy, *,
                 runner_factory: Callable[[str], Any] = Runner, clock: Optional[Callable[[], int]] = None,
                 sleep: Callable[[float], None] = time.sleep, guard: Callable[[str], None] = writers_guard) -> None:
        self.cfg, self.paths, self.policy = cfg, paths, policy
        self.runner_factory, self.sleep, self.guard = runner_factory, sleep, guard
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.prod = is_prod_data_root(paths.data_root)
        self.run_id = sp.utc_stamp(self.clock())
        self.run_dir = os.path.join(paths.work_dir, "runs", self.run_id)
        self.runner: Any = None
        self.deadline_ms = 0
        self.report: Dict[str, Any] = {"run_id": self.run_id, "prod": self.prod, "data_root": paths.data_root,
                                       "problems": []}

    # ── прогін ───────────────────────────────────────────────────────────────────────────────────────────────────────
    def run(self, *, scheduled: bool, dry_run: bool, ignore_break: bool) -> int:
        if scheduled and not self.policy.schedule_enabled:
            log.info("SETTLE_DAILY_DISABLED m1_settle.schedule_enabled=false — нічний прогін вимкнено")
            return EXIT_OK
        window = self._break_window(ignore_break)
        if window is None:
            log.info("SETTLE_DAILY_NOT_IN_BREAK — торгує хоч один символ (сезонний календар)")
            return EXIT_OK
        self.deadline_ms = window.deadline_ms
        os.makedirs(self.run_dir, exist_ok=True)
        self.runner = self.runner_factory(os.path.join(self.run_dir, "logs"))
        self.report.update(mode="dry-run" if dry_run else "apply", reopen=sp.iso_minute(window.reopen_ms),
                           deadline=sp.iso_minute(window.deadline_ms))
        problems = self._preflight()
        if problems:
            return self._finish(EXIT_REFUSED, "PREFLIGHT", problems)
        fetched_ms = self.clock()
        settled_to = sp.load_settled_to(self.paths.work_dir)
        windows = sp.symbol_windows(self.policy.lag_h_by_symbol, fetched_ms, self.policy.lookback_h, settled_to)
        self.report["windows"] = {w.sym_dir: [sp.iso_minute(w.from_ms), sp.iso_minute(w.to_ms)] for w in windows}
        m1_from, m1_to = sp.m1_fetch_window(windows, fetched_ms)
        m1 = self._fetch("m1", ["--from", sp.iso_minute(m1_from), "--to", sp.iso_minute(m1_to)])
        d1 = self._fetch("d1", ["--to", sp.iso_minute(m1_to)]) if m1 else None
        if not (m1 and d1):
            return self._finish(EXIT_REFUSED, "FETCH", ["FETCH_FAILED m1=%s d1=%s" % (m1, d1)])
        if dry_run:
            failed = self._data_steps(windows, m1, d1, apply=False)
            return self._finish(EXIT_REFUSED if failed else EXIT_OK, "DRY_RUN", failed)
        if self._remaining_s() < MIN_DATA_WINDOW_S:
            return self._finish(EXIT_REFUSED, "DEADLINE", ["DEADLINE_TOO_CLOSE remaining_s=%d" % self._remaining_s()])
        return self._apply(windows, m1, d1, settled_to)

    def _apply(self, windows: List[sp.SymbolWindow], m1: str, d1: str, settled_to: Dict[str, int]) -> int:
        try:
            verdict = self._write(windows, m1, d1, settled_to)
        finally:
            offsets = self._start_writers()
        if verdict is not None:
            return self._finish(*verdict)
        self._retention()
        problems = self._observe(offsets)
        return self._finish(EXIT_OBSERVE if problems or self.report["problems"] else EXIT_OK, "SETTLED", problems)

    def _write(self, windows: List[sp.SymbolWindow], m1: str, d1: str,
               settled_to: Dict[str, int]) -> Optional[Tuple[int, str, List[str]]]:
        """None — дані устояно і стан записано; інакше вердикт (відмова до запису або відкат)."""
        if not self._stop_writers():
            return EXIT_REFUSED, "WRITERS_ALIVE", ["WRITERS_GUARD_REFUSED після стопу — дані не змінено"]
        tgz = self._backup()
        if tgz is None:
            return EXIT_REFUSED, "BACKUP", ["BACKUP_FAILED — дані не змінено"]
        try:
            failed = self._data_steps(windows, m1, d1, apply=True)
        except Exception as exc:  # noqa: BLE001 — будь-який збій після бекапу = відкат, а не напівзаписаний прогін
            log.exception("SETTLE_DATA_EXCEPTION")
            failed = ["EXCEPTION %s: %s" % (type(exc).__name__, exc)]
        if failed:
            return (EXIT_ROLLED_BACK if self._rollback(tgz) else EXIT_ROLLBACK_FAILED), "DATA", failed
        sp.save_settled_to(self.paths.work_dir, windows, settled_to, self.run_id)
        return None

    # ── фази ─────────────────────────────────────────────────────────────────────────────────────────────────────────
    def _break_window(self, ignore_break: bool) -> Optional[sp.BreakWindow]:
        now = self.clock()
        if ignore_break:
            reopen = now + REHEARSAL_WINDOW_S * 1000
            return sp.BreakWindow(reopen_ms=reopen, deadline_ms=reopen - self.policy.deadline_guard_min * sp.M1_MS)
        trading = {s: calendar_for_symbol(self.cfg, s).is_trading_minute for s in self.cfg["symbols"]}
        return sp.break_window(trading, now, self.policy.deadline_guard_min)

    def _preflight(self) -> List[str]:
        problems = []
        if self.prod:
            # від root у репо власника ubuntu: safe.directory — дозвіл читати, --no-optional-locks — status не
            # переписує .git/index (інакше наступний git pull від ubuntu бачив би файли root)
            git = ["git", "--no-optional-locks", "-c", "safe.directory=" + self.paths.code_dir, "-C", self.paths.code_dir]
            head, origin, status = (self._capture("git_" + n, git + a) for n, a in (
                ("head", ["rev-parse", "HEAD"]), ("origin", ["rev-parse", "refs/remotes/origin/main"]),
                ("status", ["status", "--porcelain"])))
            if not head or head != origin:
                problems.append("CODE_NOT_ORIGIN_MAIN head=%s origin=%s" % (head[:12], origin[:12]))
            dirty = [ln for ln in status.splitlines() if ln.strip() and ln not in PREFLIGHT_UNTRACKED_OK]
            if dirty:
                problems.append("DIRTY_TREE %s" % dirty[:5])
            down = self._not_running(self._capture("preflight_status", ["supervisorctl", "status"]))
            if down:  # зупинені навмисно не стартуються прогоном; живий сайдкар — ще й джерело кредів забору
                problems.append("WRITERS_NOT_RUNNING %s" % down)
        free_gb = shutil.disk_usage(self.paths.work_dir).free / 1024 ** 3
        if free_gb < self.policy.min_free_disk_gb:
            problems.append("DISK_FREE_GB %.1f < %d" % (free_gb, self.policy.min_free_disk_gb))
        return problems

    def _fetch(self, kind: str, window_args: List[str]) -> Optional[str]:
        """Архів брокера (read-only логін від fetch_user, до стопу записувачів — креди з /proc живого сайдкара)."""
        fetch_dir = os.path.join(self.run_dir, "fetch")
        for path in (fetch_dir, self.paths.fxcm_cwd):
            os.makedirs(path, exist_ok=True)
            self._own_by_fetch_user(path)
        for attempt in range(1, self.policy.fetch_attempts + 1):
            out = os.path.join(fetch_dir, "%s_a%d" % (kind, attempt))
            cmd = ["sudo", "-n", "-u", self.paths.fetch_user, "env", "PYTHONPATH=" + self.paths.code_dir,
                   self.paths.py37, "-m", "tools.repair.fetch_archive", kind, "--out", out, "--config",
                   self.paths.config, "--creds-from-sidecar"] + window_args
            rc = self.runner.run("fetch_%s_a%d" % (kind, attempt), cmd, cwd=self.paths.fxcm_cwd,
                                 timeout_s=self._remaining_s())
            if rc == 0 and os.path.exists(os.path.join(out, "meta.json")):
                return out
            log.error("FETCH_ATTEMPT_FAILED kind=%s attempt=%d/%d rc=%d%s", kind, attempt, self.policy.fetch_attempts,
                      rc, " (75 = watchdog: завислий виклик SDK)" if rc == 75 else "")
        return None

    def _stop_writers(self) -> bool:
        if self.prod:
            with open(self._marker(), "w", encoding="utf-8") as fh:
                fh.write(self.run_id)
            self.runner.run("stop_writers", ["supervisorctl", "stop"] + list(PROGRAMS_STOP))
            self.sleep(STOP_SETTLE_S)
        else:
            log.warning("REHEARSAL data_root=%s поза продом — supervisor не чіпається", self.paths.data_root)
        try:
            self.guard(self.paths.data_root)
            return True
        except WritersGuardRefused as exc:
            log.error("%s", exc)
            return False

    def _backup(self) -> Optional[str]:
        backups = os.path.join(self.paths.work_dir, "backups")
        os.makedirs(backups, exist_ok=True)
        tgz = os.path.join(backups, "data_v3.pre-sd-%s.tgz" % self.run_id)
        parent, name = os.path.split(os.path.abspath(self.paths.data_root))
        if self.runner.run("backup", ["tar", "czf", tgz, "-C", parent, name], timeout_s=self._remaining_s()) != 0:
            return None
        with open(tgz + ".sha256", "w", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (sha256_file(tgz), tgz))
        self.report["backup"] = tgz
        return tgz

    def _data_steps(self, windows: List[sp.SymbolWindow], m1: str, d1: str, *, apply: bool) -> List[str]:
        """Строго послідовно; перша відмова зупиняє прогін (крок, код, символ)."""
        reports = os.path.join(self.run_dir, "reports")
        os.makedirs(reports, exist_ok=True)
        for w in windows:
            if self._remaining_s() <= 0:
                return ["DEADLINE before %s" % w.sym_dir]
            changed: List[int] = []
            if w.settles:
                report = os.path.join(reports, "settle_%s.json" % w.sym_dir)
                cmd = self._tool("settle_m1", "--data-root", self.paths.data_root, "--archive-dir", m1,
                                 "--from", sp.iso_minute(w.from_ms), "--to", sp.iso_minute(w.to_ms), "--symbols",
                                 w.symbol, "--baseline-ours", "--drop-only-ours-by-classifier", "--drop-only-ours-flat",
                                 "--report", report, *self._apply_args(apply, "settle_" + w.sym_dir))
                rc = self._step("settle_m1_" + w.sym_dir, cmd)
                if rc != 0 or not os.path.exists(report):
                    return ["settle_m1 %s rc=%d" % (w.sym_dir, rc)]
                with open(report, encoding="utf-8") as fh:
                    changed = json.load(fh)["symbols"][w.sym_dir]["changed_m1_keys"]
            if changed:
                changed_path = os.path.join(reports, "changed_%s.json" % w.sym_dir)
                with open(changed_path, "w", encoding="utf-8") as fh:
                    json.dump({w.sym_dir: changed}, fh)
                extra = ["--journal", os.path.join(reports, "season_%s.journal.jsonl" % w.sym_dir)] if apply else []
                cmd = self._tool("season_apply", "--scope", "derived_from_m1", "--changed-m1", changed_path,
                                 "--symbols", w.symbol, "--data-root", self.paths.data_root,
                                 *self._apply_args(apply, "season_" + w.sym_dir), *extra)
                rc = self._step("season_apply_" + w.sym_dir, cmd)
                if rc != 0:
                    return ["season_apply %s rc=%d" % (w.sym_dir, rc)]
        if self._remaining_s() <= 0:
            return ["DEADLINE before d1_native_settle"]
        cmd = self._tool("d1_native_settle", "--data-root", self.paths.data_root, "--archive", d1, "--report",
                         os.path.join(reports, "d1_native.json"), *self._apply_args(apply, "d1_native"))
        rc = self._step("d1_native_settle", cmd)
        return [] if rc == 0 else ["d1_native_settle rc=%d" % rc]

    def _rollback(self, tgz: str) -> bool:
        """data_v3 з tgz прогону; поточний каталог → data_v3.bad-<run>, не видаляється. Невдача — дані після settle."""
        data_root = os.path.abspath(self.paths.data_root)
        with open(tgz + ".sha256", encoding="utf-8") as fh:
            if fh.read().split()[0] != sha256_file(tgz):
                log.critical("ROLLBACK_REFUSED sha tgz не збігся %s — дані лишаються після settle", tgz)
                return False
        bad = "%s.bad-%s" % (data_root, self.run_id)
        os.rename(data_root, bad)
        rc = self.runner.run("rollback_extract", ["tar", "xzf", tgz, "-C", os.path.dirname(data_root)])
        if rc != 0 or not os.path.isdir(data_root):
            if os.path.isdir(data_root):
                os.rename(data_root, "%s.partial-%s" % (data_root, self.run_id))
            os.rename(bad, data_root)
            log.critical("ROLLBACK_FAILED rc=%d — повернуто стан після settle (кожен part-файл консистентний)", rc)
            return False
        log.warning("ROLLBACK_OK data_root відновлено з %s; стан до відкату — %s", tgz, bad)
        return True

    def _start_writers(self) -> Dict[str, int]:
        """fxcm → PRIME → preview + ws (порядок проду); повертає зсуви логів для спостереження."""
        if not self.prod:
            return {}
        offsets = {name: self._log_size(name) for name in OBSERVED_LOGS}
        self.runner.run("start_ingest", ["supervisorctl", "start", PROGRAM_INGEST])
        waited = 0
        while PRIME_MARKER not in self._log_since("m1_ingestion_worker.err.log", offsets["m1_ingestion_worker.err.log"]):
            if waited >= PRIME_WAIT_S:
                self.report["problems"].append("PRIME_NOT_SEEN за %d с" % PRIME_WAIT_S)
                log.error("PRIME_NOT_SEEN за %d с — читачі стартують, перевір інжест", PRIME_WAIT_S)
                break
            self.sleep(PRIME_POLL_S)
            waited += PRIME_POLL_S
        self.runner.run("start_readers", ["supervisorctl", "start"] + list(PROGRAMS_READERS))
        if os.path.exists(self._marker()):
            os.remove(self._marker())
        return offsets

    def _observe(self, offsets: Dict[str, int]) -> List[str]:
        """Проблеми після старту записувачів: програма не RUNNING на останній перевірці, ERROR/Traceback у логах."""
        if not self.prod:
            return []
        problems: List[str] = []
        for check in range(1, OBSERVE_CHECKS + 1):
            self.sleep(self.policy.observe_s / OBSERVE_CHECKS)
            down = self._not_running(self._capture("observe_status_%d" % check, ["supervisorctl", "status"]))
            if down and check == OBSERVE_CHECKS:
                problems.append("NOT_RUNNING %s" % down)
        for name, offset in offsets.items():
            alarms = [ln for ln in self._log_since(name, offset).splitlines() if LOG_ALARM.search(ln)]
            if alarms:
                problems.append("LOG_ALARMS %s n=%d first=%s" % (name, len(alarms), alarms[0][:160]))
        return problems

    def _retention(self) -> None:
        """Лише свої: tgz `data_v3.pre-sd-<штамп>` у backups і каталоги-штампи в runs понад backups_keep."""
        keep = self.policy.backups_keep
        backups = os.path.join(self.paths.work_dir, "backups")
        stamps = sorted({m.group(1) for m in (BACKUP_RE.match(n) for n in os.listdir(backups)) if m})
        for stamp in sp.expired(stamps, keep):
            for suffix in (".tgz", ".tgz.sha256"):
                path = os.path.join(backups, "data_v3.pre-sd-%s%s" % (stamp, suffix))
                if os.path.exists(path):
                    os.remove(path)
                    log.info("RETENTION_REMOVED %s", path)
        runs = os.path.join(self.paths.work_dir, "runs")
        run_dirs = [n for n in os.listdir(runs) if re.match(r"^\d{8}T\d{6}Z$", n) and n != self.run_id]
        for name in sp.expired(run_dirs, keep - 1):
            shutil.rmtree(os.path.join(runs, name))
            log.info("RETENTION_REMOVED %s", os.path.join(runs, name))

    # ── допоміжне ────────────────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _not_running(status: str) -> List[str]:
        return [p for p in (PROGRAM_INGEST,) + PROGRAMS_READERS if not re.search(re.escape(p) + r"\s+RUNNING", status)]

    def _marker(self) -> str:
        return os.path.join(self.paths.work_dir, WRITERS_STOPPED_MARKER)

    def _tool(self, module: str, *args: str) -> List[str]:
        return [self.paths.py, "-m", "tools.repair." + module, "--config", self.paths.config] + list(args)

    def _apply_args(self, apply: bool, backup_name: str) -> List[str]:
        return ["--apply", "--backup-dir", os.path.join(self.run_dir, "backup", backup_name)] if apply else []

    def _step(self, name: str, cmd: List[str]) -> int:
        return self.runner.run(name, cmd, cwd=self.paths.code_dir, timeout_s=max(1.0, self._remaining_s()))

    def _capture(self, name: str, cmd: List[str]) -> str:
        self.runner.run(name, cmd)
        return self.runner.output(name).strip()

    def _remaining_s(self) -> float:
        return (self.deadline_ms - self.clock()) / 1000.0

    def _log_size(self, name: str) -> int:
        path = os.path.join(self.paths.log_dir, name)
        return os.path.getsize(path) if os.path.exists(path) else 0

    def _log_since(self, name: str, offset: int) -> str:
        path = os.path.join(self.paths.log_dir, name)
        if not os.path.exists(path):
            return ""
        with open(path, "rb") as fh:
            fh.seek(offset if offset <= os.path.getsize(path) else 0)  # logrotate copytruncate обрізав лог
            return fh.read().decode("utf-8", "replace")

    def _own_by_fetch_user(self, path: str) -> None:
        if os.name == "posix" and os.geteuid() == 0:
            import pwd

            user = pwd.getpwnam(self.paths.fetch_user)
            os.chown(path, user.pw_uid, user.pw_gid)

    def _finish(self, code: int, stage: str, problems: Sequence[str] = ()) -> int:
        self.report.update(exit_code=code, stage=stage, finished_at=sp.iso_minute(self.clock()))
        self.report["problems"] += list(problems)
        for path in (os.path.join(self.run_dir, "report.json"), os.path.join(self.paths.work_dir, "last_status.json")):
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(self.report, fh, ensure_ascii=False, indent=1)
            os.replace(path + ".tmp", path)
        level = logging.INFO if code == EXIT_OK else logging.CRITICAL if code == EXIT_ROLLBACK_FAILED else logging.ERROR
        log.log(level, "SETTLE_DAILY_RESULT code=%d stage=%s run=%s problems=%s", code, stage, self.run_dir,
                self.report["problems"][:5])
        return code


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    trigger = ap.add_mutually_exclusive_group(required=True)
    trigger.add_argument("--scheduled", action="store_true", help="cron: виконується лише з m1_settle.schedule_enabled")
    trigger.add_argument("--manual", action="store_true", help="ручний прогін (S4, кожен — за «го» власника)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ignore-break", action="store_true")
    ap.add_argument("--code-dir", default=_REPO_ROOT)
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--py37", default=None)
    ap.add_argument("--log-dir", default="/var/log/smc-v3")
    ap.add_argument("--fetch-user", default="smc")
    ap.add_argument("--work-dir", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    code_dir = os.path.abspath(args.code_dir)
    config = os.path.join(code_dir, "config.json")
    cfg = load_system_config(config)
    policy = m1_settle_policy(cfg)
    paths = Paths(code_dir=code_dir, config=config, py=sys.executable, log_dir=args.log_dir,
                  data_root=os.path.abspath(args.data_root or os.path.join(code_dir, str(cfg.get("data_root", "data_v3")))),
                  py37=args.py37 or os.path.join(code_dir, ".venv37", "bin", "python"),
                  work_dir=os.path.abspath(args.work_dir or policy.work_dir), fetch_user=args.fetch_user)
    prod = is_prod_data_root(paths.data_root)
    if args.ignore_break and prod and not args.dry_run:
        log.error("SETTLE_DAILY_REFUSED --ignore-break на проді дозволений лише з --dry-run")
        return EXIT_USAGE
    if not prod and paths.work_dir == os.path.abspath(policy.work_dir):
        log.error("SETTLE_DAILY_REFUSED репетиція на копії потребує окремого --work-dir (стан проду не чіпається)")
        return EXIT_USAGE
    os.makedirs(paths.work_dir, exist_ok=True)
    return DailySettle(cfg, paths, policy).run(scheduled=args.scheduled, dry_run=args.dry_run,
                                                ignore_break=args.ignore_break)


if __name__ == "__main__":
    sys.exit(main())
