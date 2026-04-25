"""Actions — control plane для aione-top v0.9.

Kill / restart / start processes, clear cache, pidfile cleanup.
Не імпортує runtime/core/ui — тільки psutil/redis/subprocess.

v0.9: _kill_tree() — taskkill /F /T вбиває весь process tree (трамплін + всі діти).
  kill_by_pid() тепер використовує _kill_tree() замість p.terminate().
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import psutil
import redis


# ---------------------------------------------------------------------------
# Process tree kill (Python 3.14 trampoline-safe)
# ---------------------------------------------------------------------------
def _kill_tree(pid: int) -> None:
    """Вбити процес і весь його дочірній tree.

    Windows: taskkill /F /T вбиває трамплін-лаунчер + реального worker разом.
    Unix: SIGTERM через psutil (trampolines не використовуються).
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            psutil.Process(pid).terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


# ---------------------------------------------------------------------------
# Маппінг role → module (SSOT для запуску)
# ---------------------------------------------------------------------------
_ROLE_MODULE: Dict[str, str] = {
    "connector": "app.main_connector",
    "m1_poller": "runtime.ingest.polling.m1_poller",
    "broker_sidecar": "runtime.ingest.broker_sidecar",
    "m1_ingestion": "runtime.ingest.m1_ingestion_worker",
    "tick_publisher": "runtime.ingest.tick_publisher_fxcm",
    "tick_preview": "runtime.ingest.tick_preview_worker",
    "ws_server": "runtime.ws.ws_server",
    "ws_server": "runtime.ws.ws_server",
    "binance_ingest": "runtime.ingest.binance_ingest_worker",
    "binance_tick_pub": "runtime.ingest.binance_tick_publisher",
}

# Aliases: collector role name → canonical startable role
# (collectors.py класифікує tick_publisher як "tick_pub")
_ROLE_ALIAS: Dict[str, str] = {
    "tick_pub": "tick_publisher",
    "m1_ingestion": "m1_ingestion",
    "binance_ingest": "binance_ingest",
    "binance_tick_pub": "binance_tick_pub",
}

# Ролі, які можна запускати/перезапускати
STARTABLE_ROLES = list(_ROLE_MODULE.keys())


def kill_by_pid(pid: int) -> Tuple[bool, str]:
    """Вбити процес і весь його tree за PID. Повертає (ok, message).

    Використовує _kill_tree() — на Windows taskkill /F /T щоб вбити
    і трамплін-лаунчер і реального worker (Python 3.14 venv).
    """
    if pid == os.getpid():
        return False, "Cannot kill self"
    if not psutil.pid_exists(pid):
        return False, f"PID {pid} not found"
    try:
        _kill_tree(pid)
        return True, f"Killed PID {pid}"
    except psutil.AccessDenied:
        return False, f"Access denied: PID {pid}"
    except Exception as e:
        return False, f"Error: {str(e)[:40]}"


def kill_with_launcher(pid: int, procs: List[Dict[str, Any]]) -> str:
    """Вбити worker і його launcher-батька (sup:*).

    Якщо вбити тільки worker, launcher (app.main --mode X)
    перезапустить його автоматично. Щоб зупинити остаточно,
    потрібно вбити і launcher.

    DUP worker: спочатку вбити DUP supervisor (щоб він не перезапустив worker),
    потім worker. Не торкатись NON-DUP launchers щоб не множити процеси.
    """
    # Знайти роль цільового процесу
    target = None
    for p in procs:
        if p["pid"] == pid:
            target = p
            break
    if target is None:
        return kill_by_pid(pid)[1]

    role = target.get("role", "")
    is_dup = target.get("is_duplicate", False)
    killed = []

    # Якщо це worker — знайти відповідний launcher (sup:<role>)
    if not role.startswith("sup:") and role != "supervisor":
        launcher_role = f"sup:{role}"
        found_launcher = False
        for p in procs:
            if p.get("role") == launcher_role:
                # DUP worker: пропустити NON-DUP launcher (вбивати лише DUP launcher)
                if is_dup and not p.get("is_duplicate", False):
                    continue
                ok, msg = kill_by_pid(p["pid"])
                if ok:
                    killed.append(f"launcher PID {p['pid']}")
                found_launcher = True
                break

        # DUP worker без DUP launcher → знайти і вбити DUP supervisor
        # (_kill_tree вбиває весь дочірній tree, тому worker теж загине)
        if is_dup and not found_launcher:
            for p in procs:
                if p.get("role") == "supervisor" and p.get("is_duplicate"):
                    ok2, _ = kill_by_pid(p["pid"])
                    if ok2:
                        killed.append(f"DUP supervisor PID {p['pid']} (+tree)")
                    break

    # Вбити сам процес (може вже бути мертвий якщо supervisor вбитий через _kill_tree)
    ok, msg = kill_by_pid(pid)
    if ok:
        killed.append(f"PID {pid}")

    # Якщо це launcher — вбити і його child worker
    if role.startswith("sup:"):
        worker_role = role[4:]  # sup:connector → connector
        for p in procs:
            if p.get("role") == worker_role:
                ok2, _ = kill_by_pid(p["pid"])
                if ok2:
                    killed.append(f"worker PID {p['pid']}")

    if killed:
        return f"Killed: {', '.join(killed)}"
    return msg


def kill_duplicates(procs: List[Dict[str, Any]]) -> str:
    """Вбити всі is_duplicate=True.

    Порядок: спочатку DUP supervisor/launchers (щоб не перезапускали workers),
    потім DUP workers. _kill_tree на supervisor вбиває весь його tree,
    тому workers, що вже мертві, просто дадуть False при повторній спробі.
    """
    dups = [p for p in procs if p.get("is_duplicate")]
    if not dups:
        return "No duplicates"
    # Supervisors і launchers (sup:*) — першими, щоб зупинити respawn
    dup_sups = [
        p
        for p in dups
        if p.get("role") == "supervisor" or str(p.get("role", "")).startswith("sup:")
    ]
    dup_workers = [p for p in dups if p not in dup_sups]
    ok = sum(1 for p in dup_sups + dup_workers if kill_by_pid(p["pid"])[0])
    return f"Killed {ok}/{len(dups)} duplicates"


def kill_all_v3(procs: List[Dict[str, Any]]) -> str:
    """Вбити ВСІ v3 процеси (крім aione_top).

    Порядок: спочатку launchers (sup:*), потім workers.
    Це запобігає auto-restart workers після їх вбивства.
    """
    targets = [p for p in procs if p.get("role") != "aione_top"]
    if not targets:
        return "No v3 processes"
    # Launchers (sup:*) першими — щоб вони не перезапустили workers
    launchers = [
        p
        for p in targets
        if str(p.get("role", "")).startswith("sup:") or p.get("role") == "supervisor"
    ]
    workers = [p for p in targets if p not in launchers]
    ok = 0
    for p in launchers + workers:
        if kill_by_pid(p["pid"])[0]:
            ok += 1
    return f"Killed {ok}/{len(targets)} v3 processes (launchers first)"


def clear_redis_ns(cfg: Dict[str, Any]) -> str:
    """Очистити v3 namespace ключі в Redis."""
    rc = cfg.get("redis", {})
    ns = rc.get("namespace", "v3_local")
    try:
        r = redis.Redis(
            host=rc.get("host", "127.0.0.1"),
            port=rc.get("port", 6379),
            db=rc.get("db", 1),
            decode_responses=True,
            socket_timeout=2,
        )
        keys = list(r.scan_iter(f"{ns}:*", count=1000))
        if not keys:
            return "Redis: 0 keys"
        return f"Redis: deleted {r.delete(*keys)} keys"
    except Exception as e:
        return f"Redis error: {str(e)[:40]}"


def clear_app_cache() -> str:
    """Скинути TTL-кеш + log state колекторів aione-top."""
    from aione_top.collectors import _cache, clear_log_state

    _cache.clear()  # TTL cache (Redis, disk, HTTP)
    clear_log_state()  # Log tracker + buffer + freshness mtime cache
    return "App cache cleared"


# ---------------------------------------------------------------------------
# Restart / Start
# ---------------------------------------------------------------------------


def _find_venv_python() -> str:
    """Знайти python.exe у .venv середовищі (або поточний інтерпретатор)."""
    # Шукаємо .venv відносно workspace root
    workspace = Path(__file__).resolve().parent.parent
    venv_py = workspace / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    # Fallback: той самий python, який запустив aione_top
    return sys.executable


def _get_role_from_proc(proc: Dict[str, Any]) -> str:
    """Дістати canonical role для запуску (без sup: префіксу, з alias resolution)."""
    role = proc.get("role", "")
    if role.startswith("sup:"):
        role = role[4:]
    return _ROLE_ALIAS.get(role, role)


def restart_process(pid: int, procs: List[Dict[str, Any]]) -> str:
    """Перезапустити процес.

    Логіка:
    - Якщо є launcher (sup:<role>) → вбиваємо лише worker,
      launcher автоматично його перезапустить (backoff).
    - Якщо launcher немає → вбиваємо worker і запускаємо
      новий launcher (app.main --mode <role>).
    - Якщо target = launcher → вбиваємо launcher + worker,
      запускаємо новий launcher.
    """
    target = None
    for p in procs:
        if p["pid"] == pid:
            target = p
            break
    if target is None:
        return f"PID {pid} not found"

    role = target.get("role", "")
    canonical = _get_role_from_proc(target)

    if canonical not in _ROLE_MODULE:
        # Не знаємо як запустити — просто kill
        ok, msg = kill_by_pid(pid)
        return f"Killed {pid} (restart not supported for '{role}')"

    # Перевіримо чи є launcher для цієї ролі
    has_launcher = any(
        p.get("role") == f"sup:{canonical}" for p in procs if p["pid"] != pid
    )

    if role.startswith("sup:"):
        # Target = launcher: вбити launcher + worker, запустити новий launcher
        # Спочатку вбити worker
        for p in procs:
            if p.get("role") == canonical:
                kill_by_pid(p["pid"])
        # Потім вбити launcher
        kill_by_pid(pid)
        # Запустити новий launcher
        return _launch_with_supervisor(canonical)

    if has_launcher:
        # Є launcher → kill worker only, launcher перезапустить
        ok, msg = kill_by_pid(pid)
        if ok:
            return f"Restarting {canonical} (killed worker, launcher will respawn)"
        return f"Restart failed: {msg}"

    # Немає launcher → kill + start new launcher
    kill_by_pid(pid)
    return _launch_with_supervisor(canonical)


def restart_all_v3(procs: List[Dict[str, Any]]) -> str:
    """Перезапустити ВСІ v3 процеси (крім aione_top).

    Вбиває все (launchers first), потім запускає кожну роль як launcher.
    """
    targets = [p for p in procs if p.get("role") != "aione_top"]
    if not targets:
        return "No v3 processes to restart"

    # Зібрати унікальні canonical ролі для перезапуску
    roles_to_start = set()
    for p in targets:
        canonical = _get_role_from_proc(p)
        if canonical in _ROLE_MODULE:
            roles_to_start.add(canonical)

    # Kill all (launchers first)
    kill_all_v3(procs)

    # Запустити кожну роль
    import time

    time.sleep(1)  # Дати час pidfile-ам звільнитись

    started = 0
    for role in sorted(roles_to_start):
        msg = _launch_with_supervisor(role)
        if "Started" in msg:
            started += 1

    return f"Restarted {started}/{len(roles_to_start)} roles"


def start_process(role: str) -> str:
    """Запустити процес за роллю (якщо ще не запущений).

    Створює launcher (app.main --mode <role>) з auto-restart.
    """
    if role not in _ROLE_MODULE:
        return f"Unknown role: {role}"
    return _launch_with_supervisor(role)


def get_missing_roles(procs: List[Dict[str, Any]]) -> List[str]:
    """Повернути ролі, яких немає серед запущених процесів.

    ADR-0016: в dual-venv режимі (broker_sidecar/m1_ingestion наявні)
    connector/m1_poller не очікуються, і навпаки.
    """
    running = set()
    for p in procs:
        canonical = _get_role_from_proc(p)
        if canonical in _ROLE_MODULE:
            running.add(canonical)
    # Визначити dual-venv vs legacy за фактично запущеними
    all_roles = {p.get("role") for p in procs}
    if "broker_sidecar" in all_roles or "m1_ingestion" in all_roles:
        expected = [
            "broker_sidecar",
            "m1_ingestion",
            "tick_publisher",
            "tick_preview",
            "ui",
            "ws_server",
        ]
    else:
        expected = [
            "connector",
            "m1_poller",
            "tick_publisher",
            "tick_preview",
            "ui",
            "ws_server",
        ]
    return [r for r in expected if r not in running]


def _launch_with_supervisor(role: str) -> str:
    """Запустити роль через app.main --mode <role> як detached процес.

    Використовує --stdio files, щоб логи йшли у logs/<role>.*.log.
    Процес detached від aione_top (CREATE_NEW_PROCESS_GROUP на Windows).
    """
    python = _find_venv_python()
    cmd = [python, "-m", "app.main", "--mode", role, "--stdio", "files"]

    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        return f"Started {role} (launcher: app.main --mode {role})"
    except Exception as e:
        return f"Start failed for {role}: {str(e)[:60]}"
