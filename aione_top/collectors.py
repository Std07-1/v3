"""Collectors — збір даних з OS / Redis / disk для aione-top v0.8.

v0.4: паралельний збір (concurrent collectors) —
  - collect_all() використовує ThreadPoolExecutor (~10 задач одночасно)
  - _TtlCache: thread-safe з threading.Lock + метод clear()
  - _get_redis(): lock при cold-start щоб уникнути double-connect
  - Час збору: ~3s → ~0.8s на першому циклі

v0.3: оптимізація CPU —
  - _read_last_line(): seek з кінця файлу замість readlines() (O(1) для будь-якого розміру)
  - _TtlCache: важкі колектори (Redis SCAN, disk, HTTP) кешуються з TTL
  - Cached Redis connection: одне з'єднання між циклами
  - bar_count видалено (потребував readlines)

Read-only. Не імпортує runtime/core/ui.
Кожен collector повертає чистий dict для рендерингу.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import psutil
import redis

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TF_LABELS: Dict[int, str] = {
    60: "M1",
    180: "M3",
    300: "M5",
    900: "M15",
    1800: "M30",
    3600: "H1",
    14400: "H4",
    86400: "D1",
}

# Derive chain: source → targets (для визначення pipeline health)
_DERIVE_CHAIN: Dict[int, List[int]] = {
    60: [180, 300],
    300: [900],
    900: [1800],
    1800: [3600],
    3600: [14400],
}

# Tracked TFs for freshness (в порядку cascade)
_TRACKED_TFS = [60, 180, 300, 900, 1800, 3600, 14400, 86400]


# ---------------------------------------------------------------------------
# TTL cache (щоб важкі колектори не запускались кожні 3с)
# ---------------------------------------------------------------------------
class _TtlCache:
    """Thread-safe TTL-кеш для дорогих колекторів.

    Lock захищає _store від race condition при паралельному доступі.
    Обчислення (fn) відбувається ПОЗА lock — щоб не блокувати інші потоки.
    Можливий double-compute при одночасному cold-start двох потоків —
    для monitoring-інструменту це прийнятно.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: str, ttl_s: float, fn, *args, **kwargs):
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry and (now - entry[0]) < ttl_s:
                return entry[1]
        # Обчислення поза lock (може бути повільним: HTTP, disk, Redis SCAN)
        val = fn(*args, **kwargs)
        with self._lock:
            self._store[key] = (now, val)
        return val

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_cache = _TtlCache()

# Persistent thread pool — не створюємо/знищуємо кожні 3с.
# 10 workers = по одному на кожну задачу collect_all() (OS, procs, 8 важких)
_EXECUTOR = ThreadPoolExecutor(max_workers=10, thread_name_prefix="aione_coll")


# ---------------------------------------------------------------------------
# Incremental log reader (v1.0)
# ---------------------------------------------------------------------------
class _LogTracker:
    """Інкрементальний читач логів: відстежує inode + offset на файл.

    Перший виклик: seeks to last max_initial_bytes (catch-up, не весь файл).
    Наступні виклики: тільки нові байти від попереднього offset.
    Обробляє ротацію (зміна inode) і truncation.

    Для 100MB логів: перший виклик ~32KB, наступні ~1-5KB → 30-100× менше I/O.
    """

    def __init__(self) -> None:
        # path → (inode, offset, pending_bytes)
        self._state: Dict[str, Tuple[int, int, bytes]] = {}
        self._lock = threading.Lock()

    def read_new(
        self,
        filepath: str,
        max_initial_bytes: int = 32768,
        max_new_bytes: int = 16384,
    ) -> str:
        """Повернути нові повні рядки з файлу від останнього виклику."""
        try:
            st = os.stat(filepath)
            inode = st.st_ino
            size = st.st_size

            with self._lock:
                prev = self._state.get(filepath)

            if prev is None or prev[0] != inode:
                # Перший виклик або ротація файлу
                offset = max(0, size - max_initial_bytes)
                pending = b""
            elif size < prev[1]:
                # Файл truncated
                offset = 0
                pending = b""
            else:
                offset = prev[1]
                pending = prev[2]

            to_read = min(size - offset, max_new_bytes)
            raw = b""
            if to_read > 0:
                with open(filepath, "rb") as f:
                    f.seek(offset)
                    raw = f.read(to_read)

            combined = pending + raw
            if not combined:
                return ""

            # Повертаємо тільки повні рядки; неповний хвіст зберігаємо як pending
            last_nl = combined.rfind(b"\n")
            if last_nl >= 0:
                complete = combined[: last_nl + 1]
                new_pending = combined[last_nl + 1 :]
            else:
                complete = b""
                new_pending = combined

            with self._lock:
                self._state[filepath] = (inode, offset + len(raw), new_pending)

            return complete.decode("utf-8", errors="replace")
        except Exception:
            return ""

    def invalidate(self) -> None:
        """Скинути всі позиції (наступний виклик = catch-up read)."""
        with self._lock:
            self._state.clear()


_log_tracker = _LogTracker()

# Буфер всіх розпарсених log entries (session-wide, max _LOG_BUFFER_MAX)
_LOG_BUFFER_MAX = 300
_log_entries_buffer: List[Dict[str, Any]] = []
_log_buffer_lock = threading.Lock()
_log_obs_latest: Dict[str, Any] = {}
_log_obs_ts: str = ""


# ---------------------------------------------------------------------------
# Persistent Process objects (для коректного cpu_percent між циклами)
# ---------------------------------------------------------------------------
_proc_objects: Dict[int, Any] = {}  # pid → psutil.Process
_parent_pid_cache: Dict[int, Optional[int]] = {}  # pid → resolved parent_pid

# Restart counter: відстежує зміну PID per role (session-wide, не скидається при clear_cache)
_role_pid_tracker: Dict[str, int] = {}  # role → last known PID
_restart_counts: Dict[str, int] = {}  # role → кількість рестартів

# Disk freshness mtime shortcut: якщо mtime не змінився — пропускаємо JSON parse
_freshness_mtime_cache: Dict[str, Tuple[float, Dict[str, Any]]] = (
    {}
)  # filepath → (mtime, tf_data)


# ---------------------------------------------------------------------------
# Cached Redis connection (reuse між циклами)
# ---------------------------------------------------------------------------
_redis_conn: Optional[Any] = None
_redis_cfg_hash: Optional[str] = None
_redis_lock = threading.Lock()  # захист від double-connect при cold start


def _get_redis(cfg: Dict[str, Any]) -> Any:
    """Повернути кешоване Redis-з'єднання, створити нове якщо потрібно.

    Lock гарантує єдине з'єднання навіть при паралельних викликах
    (collect_redis + collect_pipeline_data можуть стартувати одночасно).
    """
    global _redis_conn, _redis_cfg_hash
    redis_cfg = cfg.get("redis", {})
    host = redis_cfg.get("host", "127.0.0.1")
    port = redis_cfg.get("port", 6379)
    db = redis_cfg.get("db", 1)
    cfg_hash = f"{host}:{port}/{db}"

    with _redis_lock:
        if _redis_conn is not None and _redis_cfg_hash == cfg_hash:
            try:
                _redis_conn.ping()
                return _redis_conn
            except Exception:
                _redis_conn = None

        _redis_conn = redis.Redis(
            host=host,
            port=port,
            db=db,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        _redis_cfg_hash = cfg_hash
        return _redis_conn


# ---------------------------------------------------------------------------
# Efficient last-line reader (O(1) для будь-якого розміру файлу)
# ---------------------------------------------------------------------------
def _read_last_line(filepath: str, chunk_size: int = 4096) -> Optional[str]:
    """Читає останній непорожній рядок файлу через seek з кінця.

    Не завантажує весь файл у пам'ять — лише останні chunk_size байт.
    """
    try:
        with open(filepath, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            read_size = min(chunk_size, size)
            f.seek(size - read_size)
            chunk = f.read(read_size)
            lines = chunk.split(b"\n")
            for line in reversed(lines):
                stripped = line.strip()
                if stripped:
                    return stripped.decode("utf-8")
        return None
    except Exception:
        return None


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """Мінімальний читач конфігу (без залежності від core/)."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 1. OS metrics
# ---------------------------------------------------------------------------
def collect_os() -> Dict[str, Any]:
    """CPU / Memory / Swap / Uptime.

    Swap кешується окремо (swap_memory() на Windows ~500ms).
    """
    mem = psutil.virtual_memory()

    # Swap — дорога операція на Windows, кешуємо з TTL 30с
    swap = _cache.get_or_compute("_swap", 30.0, psutil.swap_memory)

    boot = psutil.boot_time()
    uptime_s = time.time() - boot
    h, rem = divmod(int(uptime_s), 3600)
    m, s = divmod(rem, 60)

    try:
        loadavg = os.getloadavg()  # type: ignore[attr-defined]
    except AttributeError:
        loadavg = None

    return {
        "cpu_percent": psutil.cpu_percent(interval=0),
        "cpu_count": psutil.cpu_count(),
        "mem_used_mb": round(mem.used / 1048576),
        "mem_total_mb": round(mem.total / 1048576),
        "mem_percent": mem.percent,
        "swap_used_mb": round(swap.used / 1048576),
        "swap_total_mb": round(swap.total / 1048576),
        "swap_percent": swap.percent,
        "loadavg": loadavg,
        "uptime": f"{h:02d}:{m:02d}:{s:02d}",
    }


# ---------------------------------------------------------------------------
# 2. v3 Processes (ТІЛЬКИ v3, з виявленням дублікатів)
# ---------------------------------------------------------------------------


def _classify_process(cmdline: str) -> Optional[str]:
    """Визначити роль v3-процесу за командним рядком.

    Повертає None якщо це не v3-процес → буде відфільтрований.

    ВАЖЛИВО: app.main --mode X — це launcher-обгортка (subprocess.Popen),
    яка спавнить реального воркера і чекає на його завершення.
    Треба детектити ПЕРЕД перевіркою ролей, бо cmdline містить ключове
    слово ролі (наприклад "--mode m1_poller" містить "m1_poller").
    """
    cl = cmdline.lower()

    # --- Launcher/supervisor detection (ПЕРШИЙ, до індивідуальних ролей) ---
    # `python -m app.main --mode connector` → launcher (show as sup:connector)
    # `python -m app.main --mode all` → supervisor (show)
    if "app.main" in cl and "--mode" in cl:
        if "--mode all" in cl:
            return "supervisor"
        # Individual mode launcher — показуємо як sup:<role>
        import re as _re

        m = _re.search(r"--mode\s+(\S+)", cl)
        if m:
            return "sup:" + m.group(1)
        return "sup:?"

    # --- Actual worker processes ---
    # Binance workers (ПЕРЕД generic tick_publisher/m1_poller, бо cmdline
    # "binance_tick_publisher" містить "tick_publisher" → хибний match)
    if "binance_ingest_worker" in cl:
        return "binance_ingest"
    if "binance_tick_publisher" in cl:
        return "binance_tick_pub"

    # ADR-0016: dual-venv processes (перед legacy, бо broker_sidecar cmdline
    # не містить "m1_poller")
    if "broker_sidecar" in cl:
        return "broker_sidecar"
    if "m1_ingestion_worker" in cl:
        return "m1_ingestion"
    if "m1_poller" in cl:
        return "m1_poller"
    if "main_connector" in cl:
        return "connector"
    if "tick_preview" in cl:
        return "tick_preview"
    if "tick_publisher" in cl:
        return "tick_pub"
    if "ws.ws_server" in cl or "ws_server" in cl:
        return "ws_server"
    if "aione_top" in cl:
        return "aione_top"
    if "derive_engine" in cl:
        return "derive"
    if "engine_b" in cl:
        return "engine_b"
    return None


def _extract_module(cmdline_parts: List[str]) -> str:
    """Витягти python-модуль / скрипт з cmd args.

    -m runtime.ingest.polling.m1_poller → m1_poller
    -m runtime.ws.ws_server → ws_server
    script.py → script.py
    """
    module = ""
    for i, part in enumerate(cmdline_parts):
        if part == "-m" and i + 1 < len(cmdline_parts):
            module = cmdline_parts[i + 1]
            break
    if not module:
        for part in cmdline_parts[1:]:
            if part.endswith(".py"):
                module = os.path.basename(part)
                break
    if not module:
        return ""
    # Скорочуємо довгий шлях модуля до значущої частини
    # runtime.ingest.polling.m1_poller → m1_poller
    # app.main → app.main
    parts = module.split(".")
    if len(parts) >= 3:
        return parts[-1]  # останній компонент
    return module


def collect_processes() -> List[Dict[str, Any]]:
    """Знайти всі v3 Python-процеси, позначити дублікати.

    Two-pass підхід (Windows-оптимізація):
      1) Швидкий scan з ["pid","name"] (~3ms для 300+ процесів)
      2) Детальний запит ТІЛЬКИ для python-процесів (~50ms для ~8 процесів)

    Persistent Process objects зберігаються між циклами для коректного
    cpu_percent(interval=0) — потребує baseline з попереднього виклику.
    """
    global _proc_objects
    result: List[Dict[str, Any]] = []

    # Pass 1: швидкий фільтр за ім'ям (pid+name = ~3ms)
    python_pids: List[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name", "") or ""
            if "python" in name.lower():
                python_pids.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Pass 2: детальна інформація ТІЛЬКИ для python-процесів
    seen_pids = set()  # type: set
    for pid in python_pids:
        try:
            # Повторно використовуємо Process об'єкт для коректного cpu_percent
            if pid in _proc_objects:
                p = _proc_objects[pid]
                # Перевірити що процес ще живий
                if not p.is_running():
                    del _proc_objects[pid]
                    p = psutil.Process(pid)
                    _proc_objects[pid] = p
            else:
                p = psutil.Process(pid)
                _proc_objects[pid] = p

            with p.oneshot():
                cmdline_parts = p.cmdline()
                cmdline = " ".join(cmdline_parts)
                role = _classify_process(cmdline)
                if role is None:
                    continue  # НЕ v3 → відфільтрований

                mem_info = p.memory_info()
                rss_mb = round(mem_info.rss / 1048576, 1)
                create_ts = p.create_time()
                uptime_s = time.time() - create_ts if create_ts else 0
                module = _extract_module(cmdline_parts)

                # IO counters (cumulative since process start)
                try:
                    io = p.io_counters()
                    io_read_bytes = io.read_bytes
                    io_write_bytes = io.write_bytes
                except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                    io_read_bytes = 0
                    io_write_bytes = 0

                seen_pids.add(pid)
                result.append(
                    {
                        "pid": pid,
                        "role": role,
                        "cpu": p.cpu_percent(interval=0),
                        "rss_mb": rss_mb,
                        "threads": p.num_threads(),
                        "uptime_s": uptime_s,
                        "create_ts": create_ts,
                        "status": p.status(),
                        "module": module,
                        "io_read_bytes": io_read_bytes,
                        "io_write_bytes": io_write_bytes,
                        "is_duplicate": False,
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            _proc_objects.pop(pid, None)
            continue

    # Прибрати зниклі процеси з кешу
    dead = [pid for pid in _proc_objects if pid not in python_pids]
    for pid in dead:
        del _proc_objects[pid]

    # --- Виявлення дублікатів ---
    role_groups: Dict[str, List[Dict]] = {}
    for p in result:
        role_groups.setdefault(p["role"], []).append(p)

    # Windows venv launcher filter: .venv\Scripts\python.exe spawns
    # реальний python і обидва мають однакову cmdline/role.
    # Launcher = parent процесу з тим самим role. Фільтруємо за parent PID.
    launcher_pids = set()
    for role, procs in role_groups.items():
        if len(procs) < 2:
            continue
        role_pids = {p["pid"] for p in procs}
        for p in procs:
            try:
                pp = psutil.Process(p["pid"]).parent()
                if pp and pp.pid in role_pids:
                    launcher_pids.add(pp.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    if launcher_pids:
        result = [p for p in result if p["pid"] not in launcher_pids]
        role_groups = {}
        for p in result:
            role_groups.setdefault(p["role"], []).append(p)

    # --- Resolve parent_pid through filtered processes (venv trampolines) ---
    result_pid_set = {p["pid"] for p in result}
    for p in result:
        pid = p["pid"]
        if pid in _parent_pid_cache:
            p["parent_pid"] = _parent_pid_cache[pid]
            continue
        resolved_ppid = None
        try:
            cur = psutil.Process(pid)
            for _ in range(5):
                par = cur.parent()
                if par is None:
                    break
                if par.pid in result_pid_set and par.pid != pid:
                    resolved_ppid = par.pid
                    break
                cur = par
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        _parent_pid_cache[pid] = resolved_ppid
        p["parent_pid"] = resolved_ppid

    # Прибрати зниклі з parent cache
    for stale in [k for k in _parent_pid_cache if k not in result_pid_set]:
        del _parent_pid_cache[stale]

    # --- Orphan detection ---
    # Worker є orphan ТІЛЬКИ якщо:
    #   1. supervisor живий
    #   2. для цієї ролі є живий sup:<role> launcher
    #   3. але worker's parent_pid НЕ вказує на жоден з sup:<role> PIDs
    # Це точний сценарій інциденту: sup:<role> launcher перезапустився (новий PID),
    # але старий worker пережив terminate і тепер конкурує на BLPOP queue.
    # Не flagуємо якщо sup:<role> взагалі немає — процес може бути standalone.
    has_supervisor = any(p.get("role") == "supervisor" for p in result)
    role_to_sup_pids: Dict[str, set] = {}
    for p in result:
        r = str(p.get("role", ""))
        if r.startswith("sup:"):
            worker_role = r[4:]  # sup:broker_sidecar → broker_sidecar
            role_to_sup_pids.setdefault(worker_role, set()).add(p["pid"])
    for p in result:
        role = p.get("role", "")
        if role in ("supervisor", "aione_top") or role.startswith("sup:"):
            p["is_orphan"] = False
        elif has_supervisor and role in role_to_sup_pids:
            # Є живий sup:<role> — перевіряємо чи цей worker є його дитиною
            expected_sup_pids = role_to_sup_pids[role]
            p["is_orphan"] = p.get("parent_pid") not in expected_sup_pids
        else:
            p["is_orphan"] = False

    for role, procs in role_groups.items():
        if role == "aione_top":
            continue
        if len(procs) > 1:
            # Сортуємо за create_ts: найновіший — "правильний"
            procs.sort(key=lambda x: x["create_ts"])
            for old in procs[:-1]:
                old["is_duplicate"] = True

    # --- Restart counter ---
    # Якщо PID для ролі змінився відносно попереднього циклу → роль перезапустилась.
    # Рахуємо тільки для worker-процесів (не sup:*, не supervisor, не aione_top).
    for p in result:
        if p.get("is_duplicate"):
            continue
        role = p.get("role", "")
        if role in ("supervisor", "aione_top") or role.startswith("sup:"):
            p["restart_count"] = 0
            continue
        pid = p["pid"]
        if role in _role_pid_tracker and _role_pid_tracker[role] != pid:
            _restart_counts[role] = _restart_counts.get(role, 0) + 1
        _role_pid_tracker[role] = pid
        p["restart_count"] = _restart_counts.get(role, 0)

    # Сортування: дублікати вгорі (видно), потім за role
    result.sort(key=lambda p: (not p["is_duplicate"], p["role"], p["pid"]))
    return result


# ---------------------------------------------------------------------------
# 3. Redis health + v3 keys
# ---------------------------------------------------------------------------
def collect_redis(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Redis health: ping, info, v3-ключі (з кешованим з'єднанням)."""
    redis_cfg = cfg.get("redis", {})
    host = redis_cfg.get("host", "127.0.0.1")
    port = redis_cfg.get("port", 6379)
    db = redis_cfg.get("db", 1)
    ns = redis_cfg.get("namespace", "v3_local")

    try:
        r = _get_redis(cfg)
        r.ping()
    except Exception as e:
        return {"ok": False, "error": str(e)[:80], "host": host, "port": port, "db": db}

    info = r.info("memory")
    mem_used = info.get("used_memory_human", "?")
    clients = r.info("clients").get("connected_clients", "?")

    # Рахуємо v3-ключі за типом через SCAN з великим count
    snap_keys = 0
    upd_keys = 0
    other_keys = 0
    prime_ready = False
    try:
        for key in r.scan_iter(f"{ns}:*", count=1000):
            if ":snap:" in key:
                snap_keys += 1
            elif ":upd:" in key or ":updates:" in key:
                upd_keys += 1
            else:
                other_keys += 1
        # Writer ставить {ns}:prime:ready:{component} (component="m1")
        pr_key = f"{ns}:prime:ready:m1"
        pr_val = r.get(pr_key)
        if pr_val:
            try:
                pr_obj = json.loads(pr_val)
                prime_ready = bool(pr_obj.get("ready", False))
            except (json.JSONDecodeError, TypeError):
                prime_ready = pr_val == "1" or pr_val == "true"
    except Exception:
        pass

    return {
        "ok": True,
        "host": host,
        "port": port,
        "db": db,
        "namespace": ns,
        "mem_used": mem_used,
        "clients": clients,
        "snap_keys": snap_keys,
        "upd_keys": upd_keys,
        "other_keys": other_keys,
        "total_keys": snap_keys + upd_keys + other_keys,
        "prime_ready": prime_ready,
    }


# ---------------------------------------------------------------------------
# 4. Disk freshness (per symbol, per TF)
# ---------------------------------------------------------------------------
def collect_disk_freshness(
    data_root: str = "data_v3",
    symbols: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Freshness кожного символу/TF: last_open_ms, age, mtime."""
    now = time.time()
    result: List[Dict[str, Any]] = []

    if symbols is None:
        try:
            symbols = [
                d
                for d in sorted(os.listdir(data_root))
                if not d.startswith("_") and os.path.isdir(os.path.join(data_root, d))
            ]
        except FileNotFoundError:
            return []

    for sym in symbols:
        row: Dict[str, Any] = {"symbol": sym, "tfs": {}}
        for tf_s in _TRACKED_TFS:
            pat = os.path.join(data_root, sym, f"tf_{tf_s}", "part-*.jsonl")
            parts = sorted(glob.glob(pat))
            if not parts:
                continue
            last_file = parts[-1]
            try:
                mtime = os.stat(last_file).st_mtime
                # mtime shortcut: якщо файл не змінився — беремо кеш, пропускаємо read+parse
                cached = _freshness_mtime_cache.get(last_file)
                if cached and cached[0] == mtime:
                    tf_data = dict(cached[1])  # копія щоб age_s перерахувати
                    open_ms = tf_data.get("_open_ms", 0)
                    publish_ts = open_ms / 1000 + tf_s + 1
                    tf_data["age_s"] = round(now - publish_ts)
                    tf_data["mtime_age_s"] = round(now - mtime)
                    row["tfs"][tf_s] = tf_data
                    continue
                # O(1): читаємо тільки останній рядок (seek з кінця)
                last_line = _read_last_line(last_file)
                if not last_line:
                    continue
                bar = json.loads(last_line)
                open_ms = bar.get("open_time_ms", 0)
                open_utc = dt.datetime.utcfromtimestamp(open_ms / 1000)
                publish_ts = open_ms / 1000 + tf_s + 1
                age_s = now - publish_ts
                mtime_age_s = now - mtime
                tf_data = {
                    "last_open": open_utc.strftime("%H:%M"),
                    "age_s": round(age_s),
                    "mtime_age_s": round(mtime_age_s),
                    "label": _TF_LABELS.get(tf_s, f"{tf_s}s"),
                    "tf_s": tf_s,
                    "_open_ms": open_ms,  # зберігаємо для перерахунку age при cache hit
                }
                _freshness_mtime_cache[last_file] = (mtime, tf_data)
                row["tfs"][tf_s] = tf_data
            except Exception:
                continue
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# 5. UI health (HTTP check)
# ---------------------------------------------------------------------------
def collect_ui_health(port: int = 8000) -> Dict[str, Any]:
    """Ping /api/status на UI."""
    import urllib.request

    t0 = time.time()
    try:
        url = f"http://127.0.0.1:{port}/api/status"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            latency_ms = round((time.time() - t0) * 1000)
            body = json.loads(resp.read())
            st = body.get("status", {})
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "boot_id": st.get("boot_id", "?"),
                "disk_hotpath_blocked": st.get("disk_hotpath_blocked_total", 0),
                "prime_ready": st.get("prime_ready", False),
                "redis_enabled": st.get("redis_enabled", False),
                "preview_nomix_violation": st.get("preview_nomix_violation", False),
            }
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


# ---------------------------------------------------------------------------
# 5b. WS server health (HTTP check, port 8000)
# ---------------------------------------------------------------------------
def collect_ws_health(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Ping WS server (GET /). Порт з config ws_server.port або 8000."""
    import urllib.request

    ws_cfg = cfg.get("ws_server") or {}
    host = ws_cfg.get("host", "127.0.0.1")
    port = int(ws_cfg.get("port", 8000))
    t0 = time.time()
    try:
        url = f"http://{host}:{port}/"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3):
            latency_ms = round((time.time() - t0) * 1000)
            return {"ok": True, "latency_ms": latency_ms, "host": host, "port": port}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80], "host": host, "port": port}


# ---------------------------------------------------------------------------
# 6. Pidfile status
# ---------------------------------------------------------------------------
def collect_pidfiles() -> List[Dict[str, Any]]:
    """Перевірити pidfiles у logs/."""
    result = []
    for pidfile in glob.glob("logs/*.pid"):
        name = os.path.basename(pidfile).replace(".pid", "")
        try:
            pid = int(open(pidfile).read().strip())
            alive = psutil.pid_exists(pid)
            result.append({"name": name, "pid": pid, "alive": alive, "file": pidfile})
        except Exception:
            result.append({"name": name, "pid": 0, "alive": False, "file": pidfile})
    return result


# ---------------------------------------------------------------------------
# 7. Derive chain health (аналіз freshness для pipeline)
# ---------------------------------------------------------------------------
def analyze_derive_health(freshness: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Аналіз здоров'я derive chain на основі freshness.

    Перевіряє чи кожен TF не відстає більше ніж на 3× свій інтервал.
    """
    stalled_tfs: Dict[int, int] = {}
    ok_count = 0
    degraded_count = 0

    for row in freshness:
        sym_ok = True
        for tf_s in _TRACKED_TFS:
            tf_data = row.get("tfs", {}).get(tf_s)
            if tf_data is None:
                continue
            age_s = tf_data.get("age_s", 0)
            # Допуск: 3× TF interval
            if age_s > tf_s * 3:
                stalled_tfs[tf_s] = stalled_tfs.get(tf_s, 0) + 1
                sym_ok = False
        if sym_ok:
            ok_count += 1
        else:
            degraded_count += 1

    return {
        "chain_ok": len(stalled_tfs) == 0,
        "stalled_tfs": {_TF_LABELS.get(k, str(k)): v for k, v in stalled_tfs.items()},
        "symbols_ok": ok_count,
        "symbols_degraded": degraded_count,
    }


# ---------------------------------------------------------------------------
# 8. Unified log collector (v1.0 — incremental reader, single pass per file)
# ---------------------------------------------------------------------------
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s*\|\s*(\w+)\s*\|\s*(.+)$"
)
# Формат без " | " (наприклад ws_server: "2026-02-22 22:07:49,611 INFO __main__: message")
_LOG_LINE_ALT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+(\w+)\s+\S+:\s*(.+)$"
)

_IMPORTANT_RE = re.compile(
    r"BOOTSTRAP|DEGRADED|GAP_DETECTED|RECONNECT|STALE|"
    r"M1_POLLER_START|M1_POLLER_STATS|REDIS_PRIME|DERIVE_ENGINE|WARMUP|"
    r"PRIME_READY|COLD_START|PRIME_SUMMARY|PIDFILE|"
    r"BROKER_PROXY_TIMEOUT|M1_GAP_DETECTED",
    re.IGNORECASE,
)

_OBS_60S_RE = re.compile(r"OBS_60S\s+(\{.+)$")


def _log_files_glob():
    # type: () -> List[str]
    """Список log-файлів: logs/*.log, fallback на logs/*.err.log та logs/*.out.log."""
    files = glob.glob("logs/*.log")
    if not files:
        files = glob.glob("logs/*.err.log") + glob.glob("logs/*.out.log")
    return sorted(set(files))


def collect_logs(max_lines: int = 30) -> Dict[str, Any]:
    """Unified log collector: одним проходом per file дає log_tail + obs.

    v1.0: інкрементальне читання через _log_tracker.
      - Перший виклик: last 32KB (catch-up, не весь 100MB файл).
      - Наступні виклики: тільки нові байти (~1-5KB per cycle).
      - Обидва sources (log_tail + obs) з одного read per file — 0 дублювання I/O.
    """
    global _log_obs_latest, _log_obs_ts

    new_entries: List[Dict[str, Any]] = []

    for logfile in _log_files_glob():
        text = _log_tracker.read_new(logfile)
        if not text:
            continue
        source = os.path.basename(logfile)
        for raw_line in text.split("\n"):
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            # OBS_60S detection (для obs panel)
            m_obs = _OBS_60S_RE.search(raw_line)
            if m_obs:
                try:
                    payload = json.loads(m_obs.group(1))
                    if isinstance(payload, dict):
                        ts = raw_line[:19] if len(raw_line) >= 19 else ""
                        if ts >= _log_obs_ts:
                            _log_obs_ts = ts
                            _log_obs_latest = payload
                except (json.JSONDecodeError, TypeError):
                    pass

            # Log entry detection (WARNING/ERROR + key events)
            m = _LOG_LINE_RE.match(raw_line)
            if not m:
                m = _LOG_LINE_ALT_RE.match(raw_line)
            if not m:
                continue
            ts_str, level, message = m.group(1), m.group(2), m.group(3)
            if level not in (
                "WARNING",
                "ERROR",
                "CRITICAL",
            ) and not _IMPORTANT_RE.search(message):
                continue
            new_entries.append(
                {
                    "ts": ts_str,
                    "level": level,
                    "message": message[:120],
                    "source": source,
                }
            )

    # Append нові записи до session buffer (thread-safe)
    if new_entries:
        new_entries.sort(key=lambda x: x["ts"])
        with _log_buffer_lock:
            _log_entries_buffer.extend(new_entries)
            # Trim до ліміту (видаляємо найстаріші)
            if len(_log_entries_buffer) > _LOG_BUFFER_MAX:
                del _log_entries_buffer[: len(_log_entries_buffer) - _LOG_BUFFER_MAX]

    with _log_buffer_lock:
        tail = list(_log_entries_buffer[-max_lines:])

    return {"log_tail": tail, "obs": dict(_log_obs_latest)}


def clear_log_state() -> None:
    """Скинути incremental log tracker + buffer (force re-read на наступному циклі)."""
    global _log_obs_latest, _log_obs_ts
    _log_tracker.invalidate()
    with _log_buffer_lock:
        _log_entries_buffer.clear()
    _log_obs_latest = {}
    _log_obs_ts = ""
    _freshness_mtime_cache.clear()


# ---------------------------------------------------------------------------
# 9. Pipeline data (Redis prime:ready + status:snapshot)
# ---------------------------------------------------------------------------
def collect_pipeline_data(cfg):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Pipeline статус з Redis: prime:ready + status:snapshot + broker queue depth."""
    redis_cfg = cfg.get("redis", {})
    ns = redis_cfg.get("namespace", "v3_local")
    try:
        r = _get_redis(cfg)
        pr_raw = r.get("{0}:prime:ready:m1".format(ns))
        prime = json.loads(pr_raw) if pr_raw else None
        ss_raw = r.get("{0}:status:snapshot".format(ns))
        status = json.loads(ss_raw) if ss_raw else None
        # BLPOP competition detection: глибина черги команд до broker_sidecar.
        # Якщо > 0 при idle → два worker конкурують на одну чергу.
        m1_cmd_queue_depth = r.llen("{0}:broker:m1:cmd".format(ns))
        return {
            "ok": True,
            "prime_ready": prime,
            "status_snapshot": status,
            "m1_cmd_queue_depth": m1_cmd_queue_depth,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


# ---------------------------------------------------------------------------
# Master collector
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TTL бюджети: легкі колектори — кожен цикл, важкі — рідше
# ---------------------------------------------------------------------------
_TTL_REDIS_S = 10.0  # Redis SCAN — кожні 10с
_TTL_DISK_S = 10.0  # Disk freshness — кожні 10с
_TTL_UI_S = 10.0  # HTTP /api/status — кожні 10с
_TTL_PIDFILES_S = 15.0  # Pidfiles — кожні 15с
_TTL_PIPELINE_S = 10.0  # Pipeline (Redis prime:ready + status) — кожні 10с
_TTL_LOGS_S = 5.0  # Unified logs (log_tail + obs) — кожні 5с; incremental → cheap
_TTL_PROCS_S = 5.0  # Processes — кожні 5с (parent_pid кешується окремо)
_TTL_OS_S = 5.0  # OS stats — кожні 5с


def collect_all(cfg: Dict[str, Any], data_root: str = "data_v3") -> Dict[str, Any]:
    """Зібрати всі дані за один цикл — паралельно через _EXECUTOR.

    Всі ~10 задач стартують одночасно; TTL-кеш не дає важким колекторам
    запускатись частіше ніж потрібно. Перший цикл (~3с) → ~0.8с.
    """
    symbols_cfg = cfg.get("symbols", [])
    symbols_disk = [s.replace("/", "_") for s in symbols_cfg] if symbols_cfg else None

    # Хелпер щоб не дублювати _cache.get_or_compute у кожному submit()
    def _c(key, ttl, fn, *args):
        return _cache.get_or_compute(key, ttl, fn, *args)

    # Запускаємо всі задачі паралельно
    futures: Dict[str, Future] = {
        "os": _EXECUTOR.submit(_c, "os", _TTL_OS_S, collect_os),
        "processes": _EXECUTOR.submit(_c, "processes", _TTL_PROCS_S, collect_processes),
        "redis": _EXECUTOR.submit(_c, "redis", _TTL_REDIS_S, collect_redis, cfg),
        "freshness": _EXECUTOR.submit(
            _c,
            "freshness",
            _TTL_DISK_S,
            collect_disk_freshness,
            data_root,
            symbols_disk,
        ),
        "ui": _EXECUTOR.submit(_c, "ui", _TTL_UI_S, collect_ui_health),
        "ws": _EXECUTOR.submit(_c, "ws", _TTL_UI_S, collect_ws_health, cfg),
        "pidfiles": _EXECUTOR.submit(_c, "pidfiles", _TTL_PIDFILES_S, collect_pidfiles),
        "pipeline": _EXECUTOR.submit(
            _c, "pipeline", _TTL_PIPELINE_S, collect_pipeline_data, cfg
        ),
        "_logs": _EXECUTOR.submit(_c, "_logs", _TTL_LOGS_S, collect_logs, 30),
    }

    # Fallback-значення на випадок timeout або виключення в задачі
    _defaults: Dict[str, Any] = {
        "os": {},
        "processes": [],
        "redis": {"ok": False},
        "freshness": [],
        "ui": {"ok": False},
        "ws": {"ok": False},
        "pidfiles": [],
        "pipeline": {"ok": False},
        "_logs": {"log_tail": [], "obs": {}},
    }

    results: Dict[str, Any] = {}
    for key, fut in futures.items():
        try:
            results[key] = fut.result(timeout=8)
        except Exception:
            results[key] = _defaults[key]

    freshness = results["freshness"]
    derive_health = analyze_derive_health(freshness)
    logs_result = results.pop("_logs", {})
    log_tail = logs_result.get("log_tail", []) if isinstance(logs_result, dict) else []
    obs = logs_result.get("obs", {}) if isinstance(logs_result, dict) else {}

    return {
        "ts": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "os": results["os"],
        "processes": results["processes"],
        "redis": results["redis"],
        "freshness": freshness,
        "derive_health": derive_health,
        "ui": results["ui"],
        "ws": results["ws"],
        "pidfiles": results["pidfiles"],
        "pipeline": results["pipeline"],
        "log_tail": log_tail,
        "obs": obs,
    }
