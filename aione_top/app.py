"""aione-top — interactive TUI monitor for v3 platform (v1.1).

v1.1: Watchdog —
  - [w] toggle: auto-start missing roles after 2 consecutive missing cycles
  - Streak counter per role (grace period avoids false positives on transient lag)
  - Status bar shows each auto-start result
  - WATCHDOG ON badge in footer

v0.9: Safety + incident-prevention —
  - _kill_tree(): taskkill /F /T для всіх kill-операцій (Python 3.14 trampoline fix)
  - ws_server тепер відстежується в get_missing_roles()
  - Redis m1 queue depth (LLEN broker:m1:cmd) — BLPOP competition detection
  - BROKER_PROXY_TIMEOUT + M1_GAP_DETECTED → alert strip
  - Swap в header (вже збирався, тепер відображається)
  - Orphan detection: workers без launcher-parent при живому supervisor

v0.8: Process tree + IO + freshness improvements —
  - Process tree view: parent→child hierarchy with ├─/└─ connectors
  - parent_pid resolution through venv trampolines (Python 3.14+)
  - IO read/write per process (cumulative)
  - M3 + D1 added to freshness tracking
  - ws_server added to startable roles (fix G5)
  - Adaptive process table height (ratio=2)
  - Role colors for broker_sidecar/m1_ingestion

v0.7: Restart + Start processes —
  - [x] Restart: kill worker (launcher respawns) or kill+relaunch
  - [s] Start: launch missing roles via app.main --mode <role>
  - Restart all / Start all missing with [a] submenu

v0.6: Page restructuring —
  - Page 1 (Overview): header, processes, components
  - Page 2 (Pipeline): bootstrap & writer, combined bars + freshness grid
  - Page 3 (Events): log tail (WARN/ERROR + milestones)
  - [Tab] cycles pages (1→2→3→1)
  - Freshness lag fix: age from candle close, not open
  - Improved bootstrap panel totals

v0.5: Pipeline Monitor (page 2) — bootstrap, bars grid, log tail
v0.4: інтерактивний режим — [k] Kill, [c] Cache, [r] Refresh, [Space] Pause
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict

import psutil
from rich.console import Console
from rich.live import Live

from aione_top.collectors import collect_all, load_config
from aione_top.display import (
    build_layout,
    build_header,
    build_processes_table,
    build_components,
    build_pipeline_layout,
    build_bootstrap_panel,
    build_log_panel,
    build_events_layout,
    build_combined_grid,
)
from aione_top.actions import (
    _kill_tree,
    kill_by_pid,
    kill_with_launcher,
    kill_duplicates,
    kill_all_v3,
    restart_process,
    restart_all_v3,
    start_process,
    get_missing_roles,
    clear_redis_ns,
    clear_app_cache,
    STARTABLE_ROLES,
)


# ---------------------------------------------------------------------------
# Export (--once --export FILE)
# ---------------------------------------------------------------------------
def _export_snapshot(data: Dict[str, Any], path: str, fmt: str) -> None:
    """Зберегти знімок у файл: json або prometheus text."""
    if fmt == "prometheus":
        text = _data_to_prometheus(data)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _data_to_prometheus(data: Dict[str, Any]) -> str:
    """Перетворити знімок на Prometheus exposition text format."""
    lines = []
    os_d = data.get("os", {})
    procs = data.get("processes", [])
    v3_count = len([p for p in procs if not p.get("is_duplicate")])
    pipeline = data.get("pipeline", {})
    pr = pipeline.get("prime_ready") if pipeline.get("ok") else None
    prime = 1 if (pr and pr.get("ready")) else 0
    redis_ok = 1 if data.get("redis", {}).get("ok") else 0
    ui_ok = 1 if data.get("ui", {}).get("ok") else 0
    ws_ok = 1 if data.get("ws", {}).get("ok") else 0

    lines.append("# TYPE aione_cpu_percent gauge")
    lines.append("aione_cpu_percent {}".format(os_d.get("cpu_percent", 0)))
    lines.append("# TYPE aione_mem_percent gauge")
    lines.append("aione_mem_percent {}".format(os_d.get("mem_percent", 0)))
    lines.append("# TYPE aione_processes gauge")
    lines.append("aione_processes {}".format(v3_count))
    lines.append("# TYPE aione_prime_ready gauge")
    lines.append("aione_prime_ready {}".format(prime))
    lines.append("# TYPE aione_redis_up gauge")
    lines.append("aione_redis_up {}".format(redis_ok))
    lines.append("# TYPE aione_ui_up gauge")
    lines.append("aione_ui_up {}".format(ui_ok))
    lines.append("# TYPE aione_ws_up gauge")
    lines.append("aione_ws_up {}".format(ws_ok))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Non-blocking keyboard (Windows msvcrt, Unix select fallback)
# ---------------------------------------------------------------------------
try:
    import msvcrt

    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False
    import sys
    import select


def _poll_key() -> str:
    """Неблокуючe читання клавіші. '' якщо нічого не натиснуто."""
    if _HAS_MSVCRT:
        if not msvcrt.kbhit():
            return ""
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()  # пропустити scan code
            return ""
        return ch
    else:
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return ""


# ---------------------------------------------------------------------------
# Zombie prevention: вбити попередній aione_top якщо ще живий
# ---------------------------------------------------------------------------
_PIDFILE = os.path.join("logs", "aione_top.pid")


def _kill_stale_instances() -> None:
    """Знайти і зупинити попередні aione_top процеси (крім себе).

    Оптимізовано: pidfile перевіряється першим (швидко),
    повний scan лише якщо pidfile не допоміг.
    """
    my_pid = os.getpid()
    # На Windows venv launcher = окремий процес-батько: його теж не можна вбивати
    skip_pids = {my_pid}
    try:
        parent = psutil.Process(my_pid).parent()
        if parent is not None:
            skip_pids.add(parent.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    killed_from_pidfile = False

    # 1) Перевірити pidfile (швидко)
    if os.path.exists(_PIDFILE):
        try:
            old_pid = int(open(_PIDFILE).read().strip())
            if old_pid not in skip_pids and psutil.pid_exists(old_pid):
                try:
                    p = psutil.Process(old_pid)
                    cmdline = " ".join(p.cmdline()).lower()
                    if "aione_top" in cmdline:
                        _kill_tree(old_pid)
                        killed_from_pidfile = True
                except Exception:
                    pass
        except Exception:
            pass

    # 2) Scan лише якщо pidfile не знайшов нічого (уникає повільного scan)
    if not killed_from_pidfile:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if proc.pid in skip_pids:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if "aione_top" in cmdline and "python" in cmdline:
                    _kill_tree(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                continue

    # 3) Записати свій PID
    os.makedirs(os.path.dirname(_PIDFILE), exist_ok=True)
    with open(_PIDFILE, "w") as f:
        f.write(str(my_pid))


def _cleanup_pidfile() -> None:
    """Видалити pidfile при виході."""
    try:
        if os.path.exists(_PIDFILE):
            stored = int(open(_PIDFILE).read().strip())
            if stored == os.getpid():
                os.remove(_PIDFILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Interactive state machine
# ---------------------------------------------------------------------------
_STATUS_TTL = 5.0  # секунд показувати повідомлення

# ---------------------------------------------------------------------------
# Alert history ringbuffer (session-wide)
# ---------------------------------------------------------------------------
_ALERT_HISTORY: list = []   # [{ts: str, alerts: [str]}]
_ALERT_HISTORY_MAX = 50
_last_alerts: list = []


def _update_alert_history(data: dict) -> None:
    """Записати поточний стан алертів в ringbuffer якщо вони змінились."""
    global _last_alerts
    from aione_top.display import compute_alerts
    current = compute_alerts(data)
    if current and current != _last_alerts:
        _ALERT_HISTORY.append({"ts": time.strftime("%H:%M:%S"), "alerts": list(current)})
        if len(_ALERT_HISTORY) > _ALERT_HISTORY_MAX:
            _ALERT_HISTORY.pop(0)
    _last_alerts = current


_WATCHDOG_STREAK_THRESHOLD = 2  # consecutive missing cycles before auto-start


class _UIState:
    """Стан інтерактивного UI."""

    def __init__(self):
        self.mode = "normal"  # normal | kill | cache | confirm_kill_all | restart | confirm_restart_all | start
        self.status = ""
        self.status_expire = 0.0
        self.paused = False
        self.page = 1  # 1 = Overview, 2 = Pipeline
        self.watchdog_enabled = False
        self._watchdog_streak: Dict[str, int] = {}  # role → consecutive missing cycles

    def set_status(self, msg: str):
        self.status = msg
        self.status_expire = time.time() + _STATUS_TTL

    @property
    def active_status(self) -> str:
        return self.status if time.time() < self.status_expire else ""


def _handle_key(ch: str, state: _UIState, data: dict, cfg: dict) -> bool:
    """Обробити натискання клавіші. True = негайне оновлення."""
    ch = ch.lower() if ch.isalpha() else ch

    if state.mode == "normal":
        if ch in ("q", "\x1b"):
            raise KeyboardInterrupt
        elif ch == "\t":
            state.page = (state.page % 3) + 1  # 1→2→3→1
            state.set_status("Page {0}".format(state.page))
            return True
        elif ch == "k":
            state.mode = "kill"
        elif ch == "x":
            state.mode = "restart"
        elif ch == "s":
            procs = data.get("processes", [])
            missing = get_missing_roles(procs)
            if missing:
                hints = ", ".join(f"{i+1}={r}" for i, r in enumerate(missing))
                state.set_status(f"START: {hints}")
                state.mode = "start"
            else:
                state.set_status("All roles already running")
        elif ch == "c":
            state.mode = "cache"
        elif ch == "r":
            clear_app_cache()
            state.set_status("Force refresh")
            return True
        elif ch == "w":
            state.watchdog_enabled = not state.watchdog_enabled
            if state.watchdog_enabled:
                state._watchdog_streak.clear()
                state.set_status("WATCHDOG ON — auto-start missing roles after 2 missed cycles")
            else:
                state.set_status("WATCHDOG OFF")
            return True
        elif ch == " ":
            state.paused = not state.paused
            state.set_status("PAUSED" if state.paused else "Resumed")
        return False

    elif state.mode == "kill":
        if ch == "\x1b":
            state.mode = "normal"
        elif ch == "d":
            msg = kill_duplicates(data.get("processes", []))
            state.set_status(msg)
            state.mode = "normal"
            return True
        elif ch == "a":
            state.mode = "confirm_kill_all"
        elif ch.isdigit() and ch != "0":
            idx = int(ch) - 1
            procs = data.get("processes", [])
            if 0 <= idx < len(procs):
                msg = kill_with_launcher(procs[idx]["pid"], procs)
                state.set_status(msg)
            else:
                state.set_status(f"No process #{ch}")
            state.mode = "normal"
            return True
        return False

    elif state.mode == "confirm_kill_all":
        if ch == "y":
            msg = kill_all_v3(data.get("processes", []))
            state.set_status(msg)
            state.mode = "normal"
            return True
        else:
            state.mode = "normal"
        return False

    elif state.mode == "cache":
        if ch == "\x1b":
            state.mode = "normal"
        elif ch == "r":
            msg = clear_redis_ns(cfg)
            state.set_status(msg)
            state.mode = "normal"
            return True
        elif ch == "t":
            msg = clear_app_cache()
            state.set_status(msg)
            state.mode = "normal"
            return True
        return False

    elif state.mode == "restart":
        if ch == "\x1b":
            state.mode = "normal"
        elif ch == "a":
            state.mode = "confirm_restart_all"
        elif ch.isdigit() and ch != "0":
            idx = int(ch) - 1
            procs = data.get("processes", [])
            if 0 <= idx < len(procs):
                msg = restart_process(procs[idx]["pid"], procs)
                state.set_status(msg)
            else:
                state.set_status(f"No process #{ch}")
            state.mode = "normal"
            return True
        return False

    elif state.mode == "confirm_restart_all":
        if ch == "y":
            msg = restart_all_v3(data.get("processes", []))
            state.set_status(msg)
            state.mode = "normal"
            return True
        else:
            state.mode = "normal"
        return False

    elif state.mode == "start":
        if ch == "\x1b":
            state.mode = "normal"
        elif ch.isdigit() and ch != "0":
            procs = data.get("processes", [])
            missing = get_missing_roles(procs)
            idx = int(ch) - 1
            if 0 <= idx < len(missing):
                msg = start_process(missing[idx])
                state.set_status(msg)
            else:
                state.set_status(f"No missing role #{ch}")
            state.mode = "normal"
            return True
        elif ch == "a":
            procs = data.get("processes", [])
            missing = get_missing_roles(procs)
            if not missing:
                state.set_status("All roles already running")
            else:
                started = 0
                for role in missing:
                    result = start_process(role)
                    if "Started" in result:
                        started += 1
                state.set_status(f"Started {started}/{len(missing)} missing roles")
            state.mode = "normal"
            return True
        return False

    return False


def _run_watchdog(state: "_UIState", data: dict) -> None:
    """Перевірити відсутні ролі і авто-запустити якщо стрік ≥ threshold."""
    procs = data.get("processes", [])
    missing = set(get_missing_roles(procs))
    # Скинути стрік для ролей що знову з'явились
    for role in list(state._watchdog_streak):
        if role not in missing:
            state._watchdog_streak.pop(role)
    # Нарахувати стрік для відсутніх
    started_msgs = []
    for role in missing:
        streak = state._watchdog_streak.get(role, 0) + 1
        state._watchdog_streak[role] = streak
        if streak >= _WATCHDOG_STREAK_THRESHOLD:
            result = start_process(role)
            started_msgs.append(f"{role}: {result}")
            state._watchdog_streak[role] = 0  # скинути після спроби
    if started_msgs:
        state.set_status("WATCHDOG: " + " | ".join(started_msgs))


def main() -> int:
    """Entrypoint."""
    ap = argparse.ArgumentParser(description="aione-top: v3 platform monitor")
    ap.add_argument(
        "--interval", "-i", type=float, default=3.0, help="Інтервал оновлення (секунди)"
    )
    ap.add_argument(
        "--config", "-c", type=str, default="config.json", help="Шлях до config.json"
    )
    ap.add_argument(
        "--data-root", type=str, default="data_v3", help="Каталог з JSONL-даними"
    )
    ap.add_argument(
        "--once", action="store_true", help="Один знімок і вихід (для діагностики)"
    )
    ap.add_argument(
        "--no-history",
        action="store_true",
        help="Вимкнути міні-історію (спарклайни CPU/Mem)",
    )
    ap.add_argument(
        "--export",
        type=str,
        metavar="FILE",
        help="Зберегти знімок у файл (разом з --once)",
    )
    ap.add_argument(
        "--export-format",
        choices=("json", "prometheus"),
        default="json",
        help="Формат експорту (за замовч. json)",
    )
    args = ap.parse_args()

    _kill_stale_instances()

    console = Console()
    cfg = load_config(args.config)

    if args.once:
        data = collect_all(cfg, data_root=args.data_root)
        if args.export:
            _export_snapshot(data, args.export, args.export_format)
        console.print(build_header(data))
        console.print(
            build_processes_table(data.get("processes", []), data.get("proc_history"))
        )
        console.print(build_components(data))
        console.print()
        console.print(build_bootstrap_panel(data.get("pipeline", {})))
        console.print(
            build_combined_grid(data.get("pipeline", {}), data.get("freshness", []))
        )
        if data.get("obs"):
            from aione_top.display import build_obs_panel

            console.print(build_obs_panel(data.get("obs", {})))
        console.print()
        console.print(build_log_panel(data.get("log_tail", [])))
        _cleanup_pidfile()
        return 0

    state = _UIState()
    _history_max = 15
    _history: list = []
    _proc_history: dict = {}  # pid → circular list[float] (CPU%)

    def _append_history(d: dict) -> None:
        if args.no_history:
            return
        os_d = d.get("os", {})
        procs = d.get("processes", [])
        v3_count = len([p for p in procs if not p.get("is_duplicate")])
        _history.append(
            {
                "cpu": os_d.get("cpu_percent", 0),
                "mem": os_d.get("mem_percent", 0),
                "proc_count": v3_count,
            }
        )
        while len(_history) > _history_max:
            _history.pop(0)

        # Per-process CPU history для sparklines у таблиці процесів
        active_pids: set = set()
        for p in procs:
            pid = p.get("pid")
            if pid is None:
                continue
            active_pids.add(pid)
            buf = _proc_history.setdefault(pid, [])
            buf.append(float(p.get("cpu", 0.0)))
            while len(buf) > _history_max:
                buf.pop(0)
        for dead_pid in [pk for pk in list(_proc_history) if pk not in active_pids]:
            del _proc_history[dead_pid]

    data = collect_all(cfg, data_root=args.data_root)
    _append_history(data)
    _update_alert_history(data)
    if not args.no_history:
        data["history"] = list(_history)
        data["proc_history"] = {pid: list(v) for pid, v in _proc_history.items()}
    data["alert_history"] = list(_ALERT_HISTORY)

    live = None
    _crash_tb = None
    try:
        live = Live(console=console, refresh_per_second=1, screen=True)
        live.start()
        while True:
            if not state.paused:
                data = collect_all(cfg, data_root=args.data_root)
                _append_history(data)
                _update_alert_history(data)
                if not args.no_history:
                    data["history"] = list(_history)
                    data["proc_history"] = {
                        pid: list(v) for pid, v in _proc_history.items()
                    }
                data["alert_history"] = list(_ALERT_HISTORY)
                if state.watchdog_enabled:
                    _run_watchdog(state, data)

            if state.page == 1:
                layout = build_layout(
                    data,
                    mode=state.mode,
                    status=state.active_status,
                    paused=state.paused,
                    watchdog=state.watchdog_enabled,
                )
            elif state.page == 2:
                layout = build_pipeline_layout(
                    data,
                    mode=state.mode,
                    status=state.active_status,
                    paused=state.paused,
                    watchdog=state.watchdog_enabled,
                )
            else:
                layout = build_events_layout(
                    data,
                    mode=state.mode,
                    status=state.active_status,
                    paused=state.paused,
                    watchdog=state.watchdog_enabled,
                )
            live.update(layout)

            # Keyboard polling замість sleep
            deadline = time.time() + args.interval
            while time.time() < deadline:
                key = _poll_key()
                if key:
                    refresh = _handle_key(key, state, data, cfg)
                    if refresh:
                        break
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    except Exception:
        # Зберігаємо exception ДО відновлення терміналу,
        # бо traceback в alternate screen зникає безслідно
        import traceback

        _crash_tb = traceback.format_exc()
    else:
        _crash_tb = None
    finally:
        # Гарантоване відновлення терміналу
        if live is not None:
            try:
                live.stop()
            except Exception:
                pass
        # Очистити alternate screen і показати курсор
        try:
            console.show_cursor(True)
            print("\033[?1049l", end="", flush=True)  # вийти з alternate screen
        except Exception:
            pass
        if _crash_tb:
            console.print("[bold red]aione-top crashed:[/]")
            console.print(_crash_tb)
        else:
            console.print("[dim]aione-top stopped[/]")
        _cleanup_pidfile()
    return 1 if _crash_tb else 0
