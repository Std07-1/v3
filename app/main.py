from __future__ import annotations

import logging
import multiprocessing as mp
import time
from dataclasses import dataclass
from typing import Callable, List

from env_profile import load_env_profile


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


@dataclass(frozen=True)
class ChildProcess:
    label: str
    proc: mp.Process


def _run_connector() -> int:
    from app import main_connector

    return main_connector.main()


def _run_ui() -> int:
    from ui_chart_v3 import server

    return server.main()


def _start_process(label: str, target: Callable[[], int]) -> mp.Process:
    proc = mp.Process(target=target, name=label)
    proc.start()
    logging.info("Старт процесу %s pid=%s", label, proc.pid)
    return proc


def _terminate(proc: mp.Process, label: str, timeout_s: int = 5) -> None:
    if not proc.is_alive():
        return
    logging.info("Зупиняю процес %s pid=%s", label, proc.pid)
    try:
        proc.terminate()
        proc.join(timeout=timeout_s)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    _setup_logging(verbose=False)

    report = load_env_profile()
    if report.dispatcher_loaded or report.profile_loaded:
        logging.info("ENV: dispatcher=%s profile=%s", report.dispatcher_path, report.profile_path)
    else:
        logging.info("ENV: профіль не завантажено")

    processes: List[ChildProcess] = []
    try:
        processes.append(ChildProcess("connector", _start_process("connector", _run_connector)))
        processes.append(ChildProcess("ui", _start_process("ui", _run_ui)))

        while True:
            for item in list(processes):
                code = item.proc.exitcode
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
            _terminate(item.proc, item.label)

    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
