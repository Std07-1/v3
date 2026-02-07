from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, TextIO

from env_profile import load_env_profile


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
        choices=["all", "connector", "ui"],
        default="all",
        help="all | connector | ui",
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


@dataclass(frozen=True)
class ChildProcess:
    label: str
    proc: subprocess.Popen
    stdout_handle: Optional[TextIO] = None
    stderr_handle: Optional[TextIO] = None


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
        return ChildProcess(label=label, proc=proc)

    if stdio == "null":
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **popen_kwargs,
        )
        logging.info("Старт процесу %s pid=%s stdio=null", label, proc.pid)
        return ChildProcess(label=label, proc=proc)

    if stdio == "files":
        log_dir.mkdir(parents=True, exist_ok=True)
        out_path = log_dir / f"{label}.out.log"
        err_path = log_dir / f"{label}.err.log"
        out_f = out_path.open("a", encoding="utf-8", buffering=1)
        err_f = err_path.open("a", encoding="utf-8", buffering=1)
        proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, **popen_kwargs)
        logging.info(
            "Старт процесу %s pid=%s stdio=files dir=%s",
            label,
            proc.pid,
            log_dir,
        )
        return ChildProcess(label=label, proc=proc, stdout_handle=out_f, stderr_handle=err_f)

    if stdio == "pipe":
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs)
        assert proc.stdout is not None and proc.stderr is not None
        threading.Thread(target=_pump, args=(proc.stdout, f"[{label}] "), daemon=True).start()
        threading.Thread(target=_pump, args=(proc.stderr, f"[{label}:err] "), daemon=True).start()
        logging.info("Старт процесу %s pid=%s stdio=pipe", label, proc.pid)
        return ChildProcess(label=label, proc=proc)

    raise ValueError(f"Невідомий stdio режим: {stdio}")


def _terminate(item: ChildProcess, timeout_s: int = 5) -> None:
    proc = item.proc
    if proc.poll() is not None:
        return
    logging.info("Зупиняю процес %s pid=%s", item.label, proc.pid)
    try:
        proc.terminate()
        proc.wait(timeout=timeout_s)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

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

    report = load_env_profile()
    if report.dispatcher_loaded or report.profile_loaded:
        logging.info("ENV: dispatcher=%s profile=%s", report.dispatcher_path, report.profile_path)
    else:
        logging.info("ENV: профіль не завантажено")

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
        if args.mode in ("all", "ui"):
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

        while True:
            for item in list(processes):
                code = item.proc.poll()
                if code is not None:
                    logging.error("Процес %s завершився з кодом %s", item.label, code)
                    raise RuntimeError(f"process_exited:{item.label}")
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
