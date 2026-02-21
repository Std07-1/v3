from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, TextIO

from env_profile import load_env_secrets
from core.config_loader import pick_config_path
from runtime.store.redis_spec import resolve_redis_spec

try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mode",
        choices=["all", "connector", "ui", "tick_preview", "tick_publisher", "m1_poller"],
        default="all",
        help="all | connector | ui | tick_preview | tick_publisher | m1_poller",
    )
    ap.add_argument(
        "--stdio",
        choices=["inherit", "pipe", "files", "null"],
        default=None,
        help="inherit | pipe | files | null",
    )
    ap.add_argument("--new-console", action="store_true")
    ap.add_argument("--log-dir", default="logs")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args()


@dataclass
class ChildProcess:
    label: str
    module: str
    proc: subprocess.Popen
    stdout_handle: Optional[TextIO] = None
    stderr_handle: Optional[TextIO] = None


# ---------------------------------------------------------------------------
# S2 (ADR-0003): категорії процесів + restart backoff
# ---------------------------------------------------------------------------
_PROCESS_CATEGORIES: Dict[str, str] = {
    "connector": "critical",
    "m1_poller": "critical",
    "tick_publisher": "non_critical",
    "tick_preview": "non_critical",
    "ui": "essential",
}
# (base_delay_s, max_delay_s, max_restart_attempts)
_BACKOFF_CFG: Dict[str, tuple] = {
    "critical":     (10, 300, 5),
    "non_critical": (5, 120, 10),
    "essential":    (5, 120, 10),
}
_STABLE_RESET_S = 600  # restart counter reset після 10 хв стабільної роботи

_restart_state: Dict[str, Dict] = {}      # label → {count, start}
_restart_schedule: Dict[str, Dict] = {}   # label → {module, restart_at, attempt}

_PRINT_LOCK = threading.Lock()


def _pump(stream: TextIO, prefix: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            with _PRINT_LOCK:
                sys.stdout.write(f"{prefix}{line}")
                sys.stdout.flush()
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _pump_to_file(stream: TextIO, file_handle: TextIO, prefix: str) -> None:
    """Pump з subprocess PIPE напряму у файл (обходить Windows stdio redirect баг)."""
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            try:
                file_handle.write(f"{prefix}{line}")
                file_handle.flush()
            except Exception:
                pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _start_process(
    *,
    label: str,
    module: str,
    stdio: str,
    log_dir: Path,
    new_console: bool,
) -> ChildProcess:
    cmd = [sys.executable, "-u", "-m", module]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    creationflags = 0
    if os.name == "nt" and new_console:
        creationflags |= subprocess.CREATE_NEW_CONSOLE

    popen_kwargs = dict(
        env=env,
        creationflags=creationflags,
        text=True,
        bufsize=1,
    )

    if stdio == "inherit":
        proc = subprocess.Popen(cmd, **popen_kwargs)
        logging.info("Старт процесу %s pid=%s stdio=inherit", label, proc.pid)
        return ChildProcess(label=label, module=module, proc=proc)

    if stdio == "null":
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **popen_kwargs,
        )
        logging.info("Старт процесу %s pid=%s stdio=null", label, proc.pid)
        return ChildProcess(label=label, module=module, proc=proc)

    if stdio == "files":
        log_dir.mkdir(parents=True, exist_ok=True)
        out_path = log_dir / f"{label}.out.log"
        err_path = log_dir / f"{label}.err.log"
        out_f = out_path.open("a", encoding="utf-8", buffering=1)
        err_f = err_path.open("a", encoding="utf-8", buffering=1)
        # Windows: direct file redirect через Popen не працює надійно з text=True.
        # Замість цього: PIPE + pump_to_file у фонових потоках.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs)
        assert proc.stdout is not None and proc.stderr is not None
        threading.Thread(
            target=_pump_to_file, args=(proc.stdout, out_f, ""), daemon=True,
        ).start()
        threading.Thread(
            target=_pump_to_file, args=(proc.stderr, err_f, ""), daemon=True,
        ).start()
        logging.info(
            "Старт процесу %s pid=%s stdio=files dir=%s",
            label,
            proc.pid,
            log_dir,
        )
        return ChildProcess(label=label, module=module, proc=proc, stdout_handle=out_f, stderr_handle=err_f)

    if stdio == "pipe":
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs)
        assert proc.stdout is not None and proc.stderr is not None
        threading.Thread(target=_pump, args=(proc.stdout, f"[{label}] "), daemon=True).start()
        threading.Thread(target=_pump, args=(proc.stderr, f"[{label}:err] "), daemon=True).start()
        logging.info("Старт процесу %s pid=%s stdio=pipe", label, proc.pid)
        return ChildProcess(label=label, module=module, proc=proc)

    raise ValueError(f"Невідомий stdio режим: {stdio}")


def _wait_for_prime_ready(config_path: str, timeout_s: int = 30) -> bool:
    """AND-gate: чекає prime:ready (connector) + prime:ready:m1 (m1_poller).

    Повертає True якщо обидва компоненти ready, False при timeout.
    timeout_s береться з config.json → bootstrap.prime_ready_timeout_s (default=30).
    """
    if redis_lib is None:
        logging.warning("PRIME_READY_WAIT_SKIP reason=redis_package_missing")
        return False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        logging.warning("PRIME_READY_WAIT_SKIP reason=config_read_failed err=%s", exc)
        return False

    # Таймаут: bootstrap.prime_ready_timeout_s → fallback root → fallback param (S4 ADR-0003)
    bootstrap_cfg = cfg.get("bootstrap", {})
    cfg_timeout = None
    if isinstance(bootstrap_cfg, dict):
        cfg_timeout = bootstrap_cfg.get("prime_ready_timeout_s")
    if cfg_timeout is None:
        cfg_timeout = cfg.get("prime_ready_timeout_s")  # backward compat
    if cfg_timeout is not None:
        try:
            timeout_s = max(1, int(cfg_timeout))
        except (ValueError, TypeError):
            pass

    spec = resolve_redis_spec(cfg, role="prime_wait", log=False)
    if spec is None:
        logging.info("PRIME_READY_WAIT_SKIP reason=redis_disabled")
        return False

    # AND-gate: connector + m1_poller (S3 ADR-0003)
    keys = {
        "connector": f"{spec.namespace}:prime:ready",
        "m1": f"{spec.namespace}:prime:ready:m1",
    }

    client = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=True,
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
    )

    start = time.time()
    warned = False
    ready_components: set = set()
    while time.time() - start < max(1, int(timeout_s)):
        for component, key in keys.items():
            if component in ready_components:
                continue
            try:
                raw = client.get(key)
                if raw:
                    payload = json.loads(raw)
                    if isinstance(payload, dict) and payload.get("ready") is True:
                        ready_components.add(component)
                        logging.info(
                            "PRIME_READY_OK component=%s key=%s",
                            component, key,
                        )
            except Exception as exc:
                if not warned:
                    logging.warning("PRIME_READY_WAIT_ERROR err=%s", exc)
                    warned = True
        if len(ready_components) == len(keys):
            logging.info(
                "PRIME_READY_ALL_OK components=%s elapsed_s=%.1f",
                ",".join(sorted(ready_components)),
                time.time() - start,
            )
            return True
        time.sleep(0.5)

    missing = set(keys.keys()) - ready_components
    logging.warning(
        "PRIME_READY_TIMEOUT timeout_s=%d ready=%s missing=%s",
        int(timeout_s),
        ",".join(sorted(ready_components)) or "none",
        ",".join(sorted(missing)),
    )
    return False


def _terminate(item: ChildProcess, timeout_s: int = 5) -> None:
    proc = item.proc
    if proc.poll() is None:
        logging.info("Зупиняю процес %s pid=%s", item.label, proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=timeout_s)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    # Завжди закриваємо file handles (навіть якщо процес вже завершився)
    for handle in (item.stdout_handle, item.stderr_handle):
        if handle is None:
            continue
        try:
            handle.close()
        except Exception:
            pass


def main() -> int:
    args = _parse_args()
    _setup_logging(verbose=bool(args.verbose))

    stdio = args.stdio
    if stdio is None:
        stdio = "files" if args.quiet else "pipe"

    if args.new_console and stdio != "inherit":
        logging.error("--new-console дозволено лише з --stdio inherit")
        return 2

    log_dir = Path(args.log_dir)

    logging.info("Supervisor: mode=%s stdio=%s", args.mode, stdio)

    report = load_env_secrets()
    if report.loaded:
        logging.info("ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count)
    else:
        logging.info("ENV: .env не завантажено")

    processes: List[ChildProcess] = []
    try:
        if args.mode in ("all", "connector"):
            processes.append(
                _start_process(
                    label="connector",
                    module="app.main_connector",
                    stdio=stdio,
                    log_dir=log_dir,
                    new_console=args.new_console,
                )
            )
        if args.mode in ("all", "tick_preview"):
            processes.append(
                _start_process(
                    label="tick_preview",
                    module="runtime.ingest.tick_preview_worker",
                    stdio=stdio,
                    log_dir=log_dir,
                    new_console=args.new_console,
                )
            )
        if args.mode in ("all", "tick_publisher"):
            processes.append(
                _start_process(
                    label="tick_publisher",
                    module="runtime.ingest.tick_publisher_fxcm",
                    stdio=stdio,
                    log_dir=log_dir,
                    new_console=args.new_console,
                )
            )
        if args.mode in ("all", "m1_poller"):
            processes.append(
                _start_process(
                    label="m1_poller",
                    module="runtime.ingest.polling.m1_poller",
                    stdio=stdio,
                    log_dir=log_dir,
                    new_console=args.new_console,
                )
            )
        if args.mode in ("all", "ui"):
            if args.mode == "all":
                config_path = pick_config_path()
                prime_ok = _wait_for_prime_ready(config_path)
                if not prime_ok:
                    logging.warning(
                        "UI_START_DEGRADED reason=prime_timeout "
                        "(UI стартує, але дані можуть бути неповними)",
                    )
            processes.append(
                _start_process(
                    label="ui",
                    module="ui_chart_v3",
                    stdio=stdio,
                    log_dir=log_dir,
                    new_console=args.new_console,
                )
            )

        if not processes:
            logging.error("Supervisor: немає процесів для запуску (mode=%s)", args.mode)
            return 2

        # S2: ініціалізація restart state
        for _p in processes:
            _restart_state[_p.label] = {"count": 0, "start": time.time()}

        while True:
            now = time.time()

            # Phase 1: виконати заплановані рестарти
            for label in list(_restart_schedule):
                sched = _restart_schedule[label]
                if now < sched["restart_at"]:
                    continue
                del _restart_schedule[label]
                new_item = _start_process(
                    label=label,
                    module=sched["module"],
                    stdio=stdio,
                    log_dir=log_dir,
                    new_console=args.new_console,
                )
                processes.append(new_item)
                _restart_state[label]["start"] = now
                logging.info(
                    "SUPERVISOR_RESTARTED label=%s attempt=%d",
                    label, sched["attempt"],
                )

            # Phase 2: перевірити статус процесів
            for item in list(processes):
                code = item.proc.poll()
                if code is None:
                    continue

                # clean exit (code == 0) — видалити з пулу
                if code == 0:
                    logging.info(
                        "Процес %s завершився нормально (код=%s)",
                        item.label, code,
                    )
                    processes.remove(item)
                    _terminate(item)
                    continue

                # non-zero exit — restart з backoff (S2)
                processes.remove(item)
                _terminate(item)

                cat = _PROCESS_CATEGORIES.get(item.label, "non_critical")
                base_s, max_s, max_attempts = _BACKOFF_CFG[cat]
                st = _restart_state.setdefault(
                    item.label, {"count": 0, "start": 0.0},
                )
                # reset counter якщо процес працював стабільно >= 10 хв
                if st["start"] > 0 and (now - st["start"]) >= _STABLE_RESET_S:
                    st["count"] = 0
                st["count"] += 1

                if st["count"] > max_attempts:
                    if cat == "critical":
                        logging.error(
                            "SUPERVISOR_CRITICAL_EXHAUSTED label=%s "
                            "attempts=%d — зупинка всіх процесів",
                            item.label, st["count"],
                        )
                        raise RuntimeError(
                            f"critical_exhausted:{item.label}"
                        )
                    logging.error(
                        "SUPERVISOR_EXHAUSTED label=%s attempts=%d "
                        "— видалено з пулу",
                        item.label, st["count"],
                    )
                    continue

                delay = min(base_s * (2 ** (st["count"] - 1)), max_s)
                logging.warning(
                    "SUPERVISOR_RESTART label=%s code=%d "
                    "attempt=%d/%d delay=%.0fs cat=%s",
                    item.label, code, st["count"],
                    max_attempts, delay, cat,
                )
                _restart_schedule[item.label] = {
                    "module": item.module,
                    "restart_at": now + delay,
                    "attempt": st["count"],
                }

            if not processes and not _restart_schedule:
                logging.info(
                    "Supervisor: усі процеси завершились"
                )
                return 0

            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Зупинено користувачем (KeyboardInterrupt) у main supervisor.")
        return 0
    except Exception:
        logging.exception("Supervisor: помилка у процесах")
        return 1
    finally:
        for item in reversed(processes):
            _terminate(item)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
