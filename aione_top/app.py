"""aione-top — interactive TUI monitor for v3 platform (v0.7).

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
import os
import time

import psutil
from rich.console import Console
from rich.live import Live

from aione_top.collectors import collect_all, load_config
from aione_top.display import (
    build_layout, build_header, build_processes_table,
    build_components,
    build_pipeline_layout, build_bootstrap_panel, build_log_panel,
    build_events_layout, build_combined_grid,
)
from aione_top.actions import (
    kill_by_pid, kill_with_launcher, kill_duplicates, kill_all_v3,
    restart_process, restart_all_v3, start_process, get_missing_roles,
    clear_redis_ns, clear_app_cache,
    STARTABLE_ROLES,
)


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
        if ch in ('\x00', '\xe0'):
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
    killed_from_pidfile = False

    # 1) Перевірити pidfile (швидко)
    if os.path.exists(_PIDFILE):
        try:
            old_pid = int(open(_PIDFILE).read().strip())
            if old_pid != my_pid and psutil.pid_exists(old_pid):
                try:
                    p = psutil.Process(old_pid)
                    cmdline = " ".join(p.cmdline()).lower()
                    if "aione_top" in cmdline:
                        p.terminate()
                        try:
                            p.wait(timeout=2)
                        except psutil.TimeoutExpired:
                            p.kill()
                        killed_from_pidfile = True
                except Exception:
                    pass
        except Exception:
            pass

    # 2) Scan лише якщо pidfile не знайшов нічого (уникає повільного scan)
    if not killed_from_pidfile:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if proc.pid == my_pid:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if "aione_top" in cmdline and "python" in cmdline:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied,
                    psutil.TimeoutExpired):
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


class _UIState:
    """Стан інтерактивного UI."""
    def __init__(self):
        self.mode = "normal"   # normal | kill | cache | confirm_kill_all | restart | confirm_restart_all | start
        self.status = ""
        self.status_expire = 0.0
        self.paused = False
        self.page = 1          # 1 = Overview, 2 = Pipeline

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
        if ch in ('q', '\x1b'):
            raise KeyboardInterrupt
        elif ch == '\t':
            state.page = (state.page % 3) + 1  # 1→2→3→1
            state.set_status("Page {0}".format(state.page))
            return True
        elif ch == 'k':
            state.mode = "kill"
        elif ch == 'x':
            state.mode = "restart"
        elif ch == 's':
            procs = data.get("processes", [])
            missing = get_missing_roles(procs)
            if missing:
                hints = ", ".join(f"{i+1}={r}" for i, r in enumerate(missing))
                state.set_status(f"START: {hints}")
                state.mode = "start"
            else:
                state.set_status("All roles already running")
        elif ch == 'c':
            state.mode = "cache"
        elif ch == 'r':
            clear_app_cache()
            state.set_status("Force refresh")
            return True
        elif ch == ' ':
            state.paused = not state.paused
            state.set_status("PAUSED" if state.paused else "Resumed")
        return False

    elif state.mode == "kill":
        if ch == '\x1b':
            state.mode = "normal"
        elif ch == 'd':
            msg = kill_duplicates(data.get("processes", []))
            state.set_status(msg)
            state.mode = "normal"
            return True
        elif ch == 'a':
            state.mode = "confirm_kill_all"
        elif ch.isdigit() and ch != '0':
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
        if ch == 'y':
            msg = kill_all_v3(data.get("processes", []))
            state.set_status(msg)
            state.mode = "normal"
            return True
        else:
            state.mode = "normal"
        return False

    elif state.mode == "cache":
        if ch == '\x1b':
            state.mode = "normal"
        elif ch == 'r':
            msg = clear_redis_ns(cfg)
            state.set_status(msg)
            state.mode = "normal"
            return True
        elif ch == 't':
            msg = clear_app_cache()
            state.set_status(msg)
            state.mode = "normal"
            return True
        return False

    elif state.mode == "restart":
        if ch == '\x1b':
            state.mode = "normal"
        elif ch == 'a':
            state.mode = "confirm_restart_all"
        elif ch.isdigit() and ch != '0':
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
        if ch == 'y':
            msg = restart_all_v3(data.get("processes", []))
            state.set_status(msg)
            state.mode = "normal"
            return True
        else:
            state.mode = "normal"
        return False

    elif state.mode == "start":
        if ch == '\x1b':
            state.mode = "normal"
        elif ch.isdigit() and ch != '0':
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
        elif ch == 'a':
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


def main() -> int:
    """Entrypoint."""
    ap = argparse.ArgumentParser(description="aione-top: v3 platform monitor")
    ap.add_argument("--interval", "-i", type=float, default=3.0,
                    help="Інтервал оновлення (секунди)")
    ap.add_argument("--config", "-c", type=str, default="config.json",
                    help="Шлях до config.json")
    ap.add_argument("--data-root", type=str, default="data_v3",
                    help="Каталог з JSONL-даними")
    ap.add_argument("--once", action="store_true",
                    help="Один знімок і вихід (для діагностики)")
    args = ap.parse_args()

    _kill_stale_instances()

    console = Console()
    cfg = load_config(args.config)

    if args.once:
        data = collect_all(cfg, data_root=args.data_root)
        console.print(build_header(data))
        console.print(build_processes_table(data.get("processes", [])))
        console.print(build_components(data))
        console.print()
        console.print(build_bootstrap_panel(data.get("pipeline", {})))
        console.print(build_combined_grid(
            data.get("pipeline", {}), data.get("freshness", [])))
        console.print()
        console.print(build_log_panel(data.get("log_tail", [])))
        _cleanup_pidfile()
        return 0

    state = _UIState()
    data = collect_all(cfg, data_root=args.data_root)

    live = None
    try:
        live = Live(console=console, refresh_per_second=1, screen=True)
        live.start()
        while True:
            if not state.paused:
                data = collect_all(cfg, data_root=args.data_root)

            if state.page == 1:
                layout = build_layout(
                    data, mode=state.mode,
                    status=state.active_status, paused=state.paused,
                )
            elif state.page == 2:
                layout = build_pipeline_layout(
                    data, mode=state.mode,
                    status=state.active_status, paused=state.paused,
                )
            else:
                layout = build_events_layout(
                    data, mode=state.mode,
                    status=state.active_status, paused=state.paused,
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
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
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
        console.print("[dim]aione-top stopped[/]")
        _cleanup_pidfile()
    return 0
