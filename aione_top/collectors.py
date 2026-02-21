"""Collectors — збір даних з OS / Redis / disk для aione-top v0.3.

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
import time
from typing import Any, Dict, List, Optional, Tuple

import psutil
import redis

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TF_LABELS: Dict[int, str] = {
    60: "M1", 180: "M3", 300: "M5", 900: "M15",
    1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1",
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
_TRACKED_TFS = [60, 300, 900, 1800, 3600, 14400]


# ---------------------------------------------------------------------------
# TTL cache (щоб важкі колектори не запускались кожні 3с)
# ---------------------------------------------------------------------------
class _TtlCache:
    """Простий TTL-кеш для дорогих колекторів."""

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}

    def get_or_compute(self, key: str, ttl_s: float, fn, *args, **kwargs):
        now = time.time()
        entry = self._store.get(key)
        if entry and (now - entry[0]) < ttl_s:
            return entry[1]
        val = fn(*args, **kwargs)
        self._store[key] = (now, val)
        return val

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


_cache = _TtlCache()


# ---------------------------------------------------------------------------
# Persistent Process objects (для коректного cpu_percent між циклами)
# ---------------------------------------------------------------------------
_proc_objects: Dict[int, Any] = {}  # pid → psutil.Process


# ---------------------------------------------------------------------------
# Cached Redis connection (reuse між циклами)
# ---------------------------------------------------------------------------
_redis_conn: Optional[Any] = None
_redis_cfg_hash: Optional[str] = None


def _get_redis(cfg: Dict[str, Any]) -> Any:
    """Повернути кешоване Redis-з'єднання, створити нове якщо потрібно."""
    global _redis_conn, _redis_cfg_hash
    redis_cfg = cfg.get("redis", {})
    host = redis_cfg.get("host", "127.0.0.1")
    port = redis_cfg.get("port", 6379)
    db = redis_cfg.get("db", 1)
    cfg_hash = f"{host}:{port}/{db}"

    if _redis_conn is not None and _redis_cfg_hash == cfg_hash:
        try:
            _redis_conn.ping()
            return _redis_conn
        except Exception:
            _redis_conn = None

    _redis_conn = redis.Redis(
        host=host, port=port, db=db,
        decode_responses=True,
        socket_timeout=2, socket_connect_timeout=2,
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
    # `python -m app.main --mode connector` → launcher (hide)
    # `python -m app.main --mode all` → supervisor (show)
    if "app.main" in cl and "--mode" in cl:
        if "--mode all" in cl:
            return "supervisor"
        # Individual mode launcher — реальний воркер буде виявлений окремо
        return None

    # --- Actual worker processes ---
    if "m1_poller" in cl:
        return "m1_poller"
    if "main_connector" in cl:
        return "connector"
    if "tick_preview" in cl:
        return "tick_preview"
    if "tick_publisher" in cl:
        return "tick_pub"
    if "ui_chart_v3" in cl:
        return "ui"
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
    -m ui_chart_v3.server → ui_chart_v3.server
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

                seen_pids.add(pid)
                result.append({
                    "pid": pid,
                    "role": role,
                    "cpu": p.cpu_percent(interval=0),
                    "rss_mb": rss_mb,
                    "threads": p.num_threads(),
                    "uptime_s": uptime_s,
                    "create_ts": create_ts,
                    "status": p.status(),
                    "module": module,
                    "is_duplicate": False,
                })
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

    for role, procs in role_groups.items():
        if role == "aione_top":
            continue
        if len(procs) > 1:
            # Сортуємо за create_ts: найновіший — "правильний"
            procs.sort(key=lambda x: x["create_ts"])
            for old in procs[:-1]:
                old["is_duplicate"] = True

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
        pr_key = f"{ns}:prime_ready"
        pr_val = r.get(pr_key)
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
    now_utc = dt.datetime.utcfromtimestamp(now) # зверни увагу, що для порівняння з open_time_ms треба UTC, бо open_time_ms — це UTC timestamp
    result: List[Dict[str, Any]] = []

    if symbols is None:
        try:
            symbols = [
                d for d in sorted(os.listdir(data_root))
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
                # O(1): читаємо тільки останній рядок (seek з кінця)
                last_line = _read_last_line(last_file)
                if not last_line:
                    continue
                bar = json.loads(last_line)
                open_ms = bar.get("open_time_ms", 0)
                open_utc = dt.datetime.utcfromtimestamp(open_ms / 1000)
                # age рахуємо від publish_time (close + 1s):
                # свічка не "запізнюється" поки не мала б бути опублікована
                # M1: open+61s, M5: open+301s, тощо
                publish_ts = open_ms / 1000 + tf_s + 1
                age_s = now - publish_ts
                mtime = os.stat(last_file).st_mtime
                mtime_age_s = now - mtime
                row["tfs"][tf_s] = {
                    "last_open": open_utc.strftime("%H:%M"),
                    "age_s": round(age_s),
                    "mtime_age_s": round(mtime_age_s),
                    "label": _TF_LABELS.get(tf_s, f"{tf_s}s"),
                    "tf_s": tf_s,
                }
            except Exception:
                continue
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# 5. UI health (HTTP check)
# ---------------------------------------------------------------------------
def collect_ui_health(port: int = 8089) -> Dict[str, Any]:
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
# 8. Log tail parser (Pipeline monitor)
# ---------------------------------------------------------------------------
_LOG_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s*\|\s*(\w+)\s*\|\s*(.+)$'
)

_IMPORTANT_RE = re.compile(
    r'BOOTSTRAP|DEGRADED|GAP_DETECTED|RECONNECT|STALE|'
    r'M1_POLLER_START|M1_POLLER_STATS|REDIS_PRIME|DERIVE_ENGINE|WARMUP|'
    r'PRIME_READY|COLD_START|PRIME_SUMMARY|PIDFILE',
    re.IGNORECASE,
)


def _read_file_tail(filepath, max_bytes=65536):
    # type: (str, int) -> str
    """Прочитати останні max_bytes файлу як текст."""
    try:
        size = os.path.getsize(filepath)
        if size == 0:
            return ""
        read_size = min(max_bytes, size)
        with open(filepath, "rb") as f:
            f.seek(size - read_size)
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def collect_log_tail(max_lines=30):
    # type: (int,) -> List[Dict[str, Any]]
    """Зібрати останні важливі рядки з log-файлів (WARNING/ERROR + key events)."""
    entries = []  # type: List[Dict[str, Any]]
    for logfile in glob.glob("logs/*.log"):
        tail = _read_file_tail(logfile, max_bytes=65536)
        for raw_line in tail.split("\n"):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            m = _LOG_LINE_RE.match(raw_line)
            if not m:
                continue
            ts_str, level, message = m.group(1), m.group(2), m.group(3)
            # Завжди: WARN/ERROR; INFO тільки якщо ключова подія
            if level in ("WARNING", "ERROR", "CRITICAL"):
                pass
            elif _IMPORTANT_RE.search(message):
                pass
            else:
                continue
            entries.append({
                "ts": ts_str,
                "level": level,
                "message": message[:120],
                "source": os.path.basename(logfile),
            })
    entries.sort(key=lambda x: x["ts"])
    return entries[-max_lines:]


# ---------------------------------------------------------------------------
# 9. Pipeline data (Redis prime:ready + status:snapshot)
# ---------------------------------------------------------------------------
def collect_pipeline_data(cfg):
    # type: (Dict[str, Any],) -> Dict[str, Any]
    """Pipeline статус з Redis: prime:ready + status:snapshot."""
    redis_cfg = cfg.get("redis", {})
    ns = redis_cfg.get("namespace", "v3_local")
    try:
        r = _get_redis(cfg)
        pr_raw = r.get("{0}:prime:ready".format(ns))
        prime = json.loads(pr_raw) if pr_raw else None
        ss_raw = r.get("{0}:status:snapshot".format(ns))
        status = json.loads(ss_raw) if ss_raw else None
        return {"ok": True, "prime_ready": prime, "status_snapshot": status}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


# ---------------------------------------------------------------------------
# Master collector
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TTL бюджети: легкі колектори — кожен цикл, важкі — рідше
# ---------------------------------------------------------------------------
_TTL_REDIS_S = 10.0       # Redis SCAN — кожні 10с
_TTL_DISK_S = 10.0        # Disk freshness — кожні 10с
_TTL_UI_S = 10.0          # HTTP /api/status — кожні 10с
_TTL_PIDFILES_S = 15.0    # Pidfiles — кожні 15с
_TTL_PIPELINE_S = 10.0    # Pipeline (Redis prime:ready + status) — кожні 10с
_TTL_LOGS_S = 5.0          # Log tail — кожні 5с


def collect_all(cfg: Dict[str, Any], data_root: str = "data_v3") -> Dict[str, Any]:
    """Зібрати всі дані за один цикл.

    Легкі колектори (OS, processes) — кожен цикл.
    Важкі (Redis SCAN, disk I/O, HTTP, pidfiles) — з TTL-кешем.
    """
    symbols_cfg = cfg.get("symbols", [])
    symbols_disk = [s.replace("/", "_") for s in symbols_cfg] if symbols_cfg else None

    # Легкі — кожен цикл (OS ~1ms, processes ~10ms)
    os_data = collect_os()
    processes = collect_processes()

    # Важкі — з TTL-кешем
    redis_data = _cache.get_or_compute(
        "redis", _TTL_REDIS_S, collect_redis, cfg)
    freshness = _cache.get_or_compute(
        "freshness", _TTL_DISK_S, collect_disk_freshness, data_root, symbols_disk)
    ui_data = _cache.get_or_compute(
        "ui", _TTL_UI_S, collect_ui_health)
    pidfiles = _cache.get_or_compute(
        "pidfiles", _TTL_PIDFILES_S, collect_pidfiles)
    pipeline_data = _cache.get_or_compute(
        "pipeline", _TTL_PIPELINE_S, collect_pipeline_data, cfg)
    log_tail = _cache.get_or_compute(
        "log_tail", _TTL_LOGS_S, collect_log_tail, 30)

    derive_health = analyze_derive_health(freshness)

    return {
        "ts": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "os": os_data,
        "processes": processes,
        "redis": redis_data,
        "freshness": freshness,
        "derive_health": derive_health,
        "ui": ui_data,
        "pidfiles": pidfiles,
        "pipeline": pipeline_data,
        "log_tail": log_tail,
    }
