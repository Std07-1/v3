"""Display — Rich TUI рендерер для aione-top v1.0.

Три сторінки:
  Page 1 (Overview):
    1. Header    — CPU/Mem/Swap/Uptime + v3 summary
    2. Processes — # + PID, Role, CPU, cpuT, RSS, THR, IO, Up, Rst, Module
    3. Components — Redis / UI / Pidfiles / Derive health
    4. Footer    — hotkeys + status message

  Page 2 (Pipeline):
    1. Header
    2. Bootstrap & Writer Status
    3. Combined Grid — Primed Bars + Data Freshness (symbol × TF)
    4. Footer

  Page 3 (Events):
    1. Header
    2. Alert History (session ringbuffer)
    3. Recent Events (WARN/ERROR + milestones) log tail
    4. Footer

Не імпортує runtime/core/ui.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_age(seconds: float) -> str:
    """Форматувати вік: ≤0 → 'ok', <60s → '<1m', <60m → '12m', ≥60m → '1h40m'."""
    if seconds <= 0:
        return "ok"
    if seconds < 60:
        return "<1m"
    total_min = int(seconds / 60)
    if total_min < 60:
        return f"{total_min}m"
    hours = total_min // 60
    mins = total_min % 60
    return f"{hours}h{mins:02d}m"


def _format_uptime_short(seconds: float) -> str:
    """Форматувати uptime процесу: '2m', '1h40m', '3d2h'."""
    if seconds < 60:
        return f"{int(seconds)}s"
    total_min = int(seconds / 60)
    if total_min < 60:
        return f"{total_min}m"
    hours = total_min // 60
    mins = total_min % 60
    if hours < 24:
        return f"{hours}h{mins:02d}m"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d{rem_h}h"


def _age_color(age_s: float, tf_s: int) -> str:
    """Колір freshness: green ≤ 1.5×TF, yellow < 3×TF, red ≥ 3×TF."""
    if age_s <= 0:
        return "green"
    if age_s < tf_s * 1.5:
        return "green"
    if age_s < tf_s * 3:
        return "yellow"
    return "red"


def _bool_indicator(val: bool, true_text: str = "OK", false_text: str = "FAIL") -> Text:
    """Зелений/червоний індикатор для bool."""
    if val:
        return Text(f"[+] {true_text}", style="green")
    return Text(f"[-] {false_text}", style="bold red")


def _compact_count(n: int) -> str:
    """Компактне відображення кількості: 999→'999', 1234→'1.2k', 12345→'12k'."""
    if n < 1000:
        return str(n)
    if n < 10000:
        return "{0:.1f}k".format(n / 1000)
    return "{0:.0f}k".format(n / 1000)


_SPARK_CHARS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"  # ▁▂▃▄▅▆▇█


def _sparkline(values: List[float], length: int = 12) -> str:
    """Спарклайн з останніх length значень (0–100 масштаб)."""
    if not values or length <= 0:
        return ""
    take = values[-length:] if len(values) >= length else values
    if not take:
        return ""
    lo, hi = min(take), max(take)
    span = (hi - lo) or 1
    out = []
    for v in take:
        idx = int((v - lo) / span * 7)
        idx = max(0, min(7, idx))
        out.append(_SPARK_CHARS[idx])
    return "".join(out)


# Очікувані ролі платформи (mode=all)
# ADR-0016: dual-venv = broker_sidecar + m1_ingestion замість connector + m1_poller
_EXPECTED_ROLES_DUAL = [
    "broker_sidecar",
    "m1_ingestion",
    "tick_pub",
    "tick_preview",
    "ui",
    "ws_server",
]
_EXPECTED_ROLES_BINANCE = [
    "binance_ingest",
    "binance_tick_pub",
]
_EXPECTED_ROLES_LEGACY = [
    "connector",
    "tick_pub",
    "tick_preview",
    "m1_poller",
    "ui",
    "ws_server",
]


def _get_expected_roles(procs: list) -> list:
    """Визначити очікувані ролі за фактично запущеними процесами."""
    roles = {p.get("role") for p in procs}
    if (
        "broker_sidecar" in roles
        or "m1_ingestion" in roles
        or "binance_ingest" in roles
    ):
        base = list(_EXPECTED_ROLES_DUAL)
    else:
        base = list(_EXPECTED_ROLES_LEGACY)
    # Binance roles — тільки якщо хоча б один Binance процес запущений
    if "binance_ingest" in roles or "binance_tick_pub" in roles:
        base.extend(_EXPECTED_ROLES_BINANCE)
    return base


def _tree_sort_processes(procs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort processes into tree hierarchy order and add _tree_prefix.

    Duplicates stay at the top (unsorted). Normal processes sorted by
    tree: supervisor → launchers → workers with ├─/└─ connectors.
    """
    dups = [p for p in procs if p.get("is_duplicate")]
    normals = [p for p in procs if not p.get("is_duplicate")]

    pid_set = {p["pid"] for p in normals}
    children: Dict[int, List[Dict]] = {}
    roots: List[Dict] = []

    for p in normals:
        ppid = p.get("parent_pid")
        if ppid and ppid in pid_set:
            children.setdefault(ppid, []).append(p)
        else:
            roots.append(p)

    for kids in children.values():
        kids.sort(key=lambda x: x.get("role", ""))
    roots.sort(key=lambda x: (x.get("role") != "supervisor", x.get("role", "")))

    ordered: List[Dict] = []

    def _walk(node: Dict, depth: int, is_last: bool, parent_last_stack: list):
        if depth == 0:
            node["_tree_prefix"] = ""
        else:
            parts = []
            for pl in parent_last_stack[:-1]:
                parts.append("   " if pl else "│  ")
            parts.append("└─ " if is_last else "├─ ")
            node["_tree_prefix"] = "".join(parts)
        ordered.append(node)
        kids = children.get(node["pid"], [])
        for i, kid in enumerate(kids):
            _walk(
                kid,
                depth + 1,
                i == len(kids) - 1,
                parent_last_stack + [i == len(kids) - 1],
            )

    for root in roots:
        _walk(root, 0, root is roots[-1], [])

    for d in dups:
        d["_tree_prefix"] = ""

    return dups + ordered


def _format_io_compact(read_bytes: int, write_bytes: int) -> str:
    """Format IO as compact 'R/W' string (KB/MB/GB)."""

    def _f(n: int) -> str:
        if n < 1024 * 1024:
            return "{0}K".format(n // 1024) if n >= 1024 else "0"
        if n < 1024 * 1024 * 1024:
            return "{0}M".format(n // (1024 * 1024))
        return "{0:.1f}G".format(n / (1024 * 1024 * 1024))

    return "{0}/{1}".format(_f(read_bytes), _f(write_bytes))


def compute_alerts(data: Dict[str, Any]) -> List[str]:
    """Побудувати список коротких алертів з даних колекторів."""
    alerts: List[str] = []
    pipeline = data.get("pipeline", {})
    pr = pipeline.get("prime_ready") if pipeline.get("ok") else None
    if pr is not None and not pr.get("ready", False):
        alerts.append("Prime not ready")
    procs = data.get("processes", [])
    roles_seen = {p.get("role") for p in procs if not p.get("is_duplicate")}
    expected = _get_expected_roles(procs)
    for role in expected:
        if role not in roles_seen:
            alerts.append("{0} missing".format(role))
    if any(p.get("is_duplicate") for p in procs):
        alerts.append("Duplicate process(es)")
    if not data.get("redis", {}).get("ok"):
        alerts.append("Redis DOWN")
    if not data.get("ui", {}).get("ok"):
        alerts.append("UI :8000 DOWN")
    if not data.get("ws", {}).get("ok"):
        alerts.append("WS :8000 DOWN")
    freshness = data.get("freshness", [])
    for row in freshness:
        for tf_s, tf_data in (row.get("tfs") or {}).items():
            age_s = tf_data.get("age_s", 0)
            if age_s >= tf_s * 3:
                sym = row.get("symbol", "?")
                alerts.append("Freshness stale {0} tf={1}s".format(sym, tf_s))
                break
    # Incident-specific: BROKER_PROXY_TIMEOUT та M1_GAP_DETECTED в останніх логах
    log_tail = data.get("log_tail", [])
    for entry in reversed(log_tail[-20:]):
        msg = entry.get("message", "")
        if "BROKER_PROXY_TIMEOUT" in msg.upper():
            alerts.append("BROKER_PROXY_TIMEOUT in logs")
            break
    for entry in reversed(log_tail[-20:]):
        msg = entry.get("message", "")
        if "M1_GAP_DETECTED" in msg.upper():
            alerts.append("M1_GAP_DETECTED in logs")
            break
    if log_tail and log_tail[-1].get("level") == "ERROR":
        alerts.append("Last log ERROR")
    # m1 broker queue depth — якщо > 0 при idle: ознака BLPOP конкуренції
    pipeline = data.get("pipeline", {})
    q_depth = pipeline.get("m1_cmd_queue_depth", 0) or 0
    if q_depth > 2:
        alerts.append(f"m1 cmd queue depth={q_depth} (BLPOP competition?)")
    # Orphan processes
    if any(p.get("is_orphan") for p in data.get("processes", [])):
        alerts.append("Orphan worker(s) detected")
    return alerts


def build_alerts_strip(data: Dict[str, Any]) -> Text:
    """Одна горизонтальна смуга: алерти або 'All systems nominal'."""
    alerts = compute_alerts(data)
    t = Text()
    if alerts:
        t.append("  [!] ", style="bold red")
        t.append("  ".join("[!] {0}".format(a) for a in alerts), style="bold red")
    else:
        t.append("  All systems nominal", style="green")
    return t


# ---------------------------------------------------------------------------
# 1. Header panel
# ---------------------------------------------------------------------------
def build_header(data: Dict[str, Any]) -> Panel:
    """CPU / Mem / Swap / Uptime + v3 summary."""
    os_data = data.get("os", {})
    cpu = os_data.get("cpu_percent", 0)
    mem = os_data.get("mem_percent", 0)
    mem_used = os_data.get("mem_used_mb", 0)
    mem_total = os_data.get("mem_total_mb", 0)
    swap_pct = os_data.get("swap_percent", 0)
    swap_used = os_data.get("swap_used_mb", 0)
    swap_total = os_data.get("swap_total_mb", 0)
    cpus = os_data.get("cpu_count", "?")
    uptime = os_data.get("uptime", "?")

    # V3 process summary
    procs = data.get("processes", [])
    v3_count = len([p for p in procs if not p.get("is_duplicate")])
    dup_count = len([p for p in procs if p.get("is_duplicate")])

    # Derive health
    dh = data.get("derive_health", {})
    chain_ok = dh.get("chain_ok", True)
    sym_ok = dh.get("symbols_ok", 0)
    sym_deg = dh.get("symbols_degraded", 0)

    # CPU bar
    cpu_bar_len = 20
    cpu_filled = int(cpu / 100 * cpu_bar_len)
    cpu_color = "green" if cpu < 60 else ("yellow" if cpu < 85 else "red")

    # Memory bar
    mem_bar_len = 20
    mem_filled = int(mem / 100 * mem_bar_len)
    mem_color = "green" if mem < 70 else ("yellow" if mem < 90 else "red")

    lines = Text()
    lines.append("  CPU  ", style="bold")
    lines.append("[", style="dim")
    lines.append("#" * cpu_filled, style=cpu_color)
    lines.append("-" * (cpu_bar_len - cpu_filled), style="dim")
    lines.append("]", style="dim")
    lines.append(f" {cpu:5.1f}% ({cpus} cores)")
    lines.append("    ")
    lines.append("MEM  ", style="bold")
    lines.append("[", style="dim")
    lines.append("#" * mem_filled, style=mem_color)
    lines.append("-" * (mem_bar_len - mem_filled), style="dim")
    lines.append("]", style="dim")
    lines.append(f" {mem_used}/{mem_total} MB ({mem:.0f}%)")
    if swap_total > 0:
        swap_color = (
            "green" if swap_pct < 30 else ("yellow" if swap_pct < 60 else "red")
        )
        lines.append("    ")
        lines.append("SWAP ", style="bold")
        lines.append(f"{swap_used}/{swap_total} MB ({swap_pct:.0f}%)", style=swap_color)
    lines.append("\n")

    # Second line: Uptime + v3 summary + derive chain
    lines.append("  Uptime: ", style="bold")
    lines.append(uptime)
    lines.append("    ")
    lines.append("v3: ", style="bold")
    lines.append(f"{v3_count} procs", style="green" if dup_count == 0 else "yellow")
    if dup_count:
        lines.append(f" + {dup_count} dup", style="bold red")
    lines.append("    ")
    lines.append("Derive: ", style="bold")
    if chain_ok:
        lines.append(f"{sym_ok} sym OK", style="green")
    else:
        stalled = dh.get("stalled_tfs", {})
        stalled_str = ", ".join(f"{k}:{v}" for k, v in stalled.items())
        lines.append(f"{sym_ok} OK / {sym_deg} degraded", style="yellow")
        lines.append(f" [{stalled_str}]", style="red")

    history = data.get("history", [])
    panel_height = 5
    if history:
        lines.append("\n")
        cpu_vals = [h.get("cpu", 0) for h in history]
        mem_vals = [h.get("mem", 0) for h in history]
        lines.append("  Trend CPU ", style="bold")
        lines.append(_sparkline(cpu_vals), style="cyan")
        lines.append("  Mem ", style="bold")
        lines.append(_sparkline(mem_vals), style="magenta")
        panel_height = 6

    ts = data.get("ts", "")
    return Panel(
        lines,
        title=f"[bold cyan]AIONE-TOP v1.0[/] [dim]{ts}[/]",
        border_style="cyan",
        height=panel_height,
    )


def build_health_at_a_glance(data: Dict[str, Any]) -> Panel:
    """Компактний блок «Health at a glance»: процеси, Redis, Prime, UI, WS, Pidfiles."""
    procs = data.get("processes", [])
    roles_ok = {p.get("role") for p in procs if not p.get("is_duplicate")}
    expected = _get_expected_roles(procs)
    missing = [r for r in expected if r not in roles_ok]
    n_expected = len(expected)
    n_ok = n_expected - len(missing)
    proc_str = (
        "{0}/{1}".format(n_ok, n_expected)
        if not missing
        else "{} missing".format(",".join(missing[:4]))
    )
    proc_style = "green" if not missing else "red"

    redis_ok = data.get("redis", {}).get("ok", False)
    pipeline = data.get("pipeline", {})
    pr = pipeline.get("prime_ready") if pipeline.get("ok") else None
    prime_ok = bool(pr and pr.get("ready", False))
    ui_ok = data.get("ui", {}).get("ok", False)
    ws_ok = data.get("ws", {}).get("ok", False)
    pids = data.get("pidfiles", [])
    pid_alive = sum(1 for pf in pids if pf.get("alive"))

    line = Text()
    line.append("  Processes ", style="bold")
    line.append(proc_str, style=proc_style)
    line.append("  |  Redis ", style="bold")
    line.append("OK" if redis_ok else "FAIL", style="green" if redis_ok else "red")
    line.append("  |  Prime ", style="bold")
    line.append(
        "Ready" if prime_ok else "Not ready", style="green" if prime_ok else "red"
    )
    line.append("  |  UI :8000 ", style="bold")
    line.append("OK" if ui_ok else "FAIL", style="green" if ui_ok else "red")
    line.append("  |  WS :8000 ", style="bold")
    line.append("OK" if ws_ok else "FAIL", style="green" if ws_ok else "red")
    line.append("  |  Pidfiles ", style="bold")
    line.append("{0} alive".format(pid_alive), style="green" if pids else "dim")
    return Panel(
        line, title="[bold]Health at a glance[/]", border_style="white", height=3
    )


# ---------------------------------------------------------------------------
# 2. Processes table (only v3)
# ---------------------------------------------------------------------------
def build_processes_table(
    procs: List[Dict[str, Any]], proc_history: Optional[Dict] = None
) -> Panel:
    """Таблиця v3-процесів з деревом ієрархії, IO, дублікатами червоним.

    proc_history: {pid → list[float]} — CPU-history per process для sparkline.
    """
    # Tree-sort: duplicates at top, then hierarchy
    sorted_procs = _tree_sort_processes(procs)

    t = Table(
        expand=True,
        show_edge=False,
        pad_edge=False,
        box=None,
        header_style="bold white on dark_blue",
    )

    t.add_column("#", width=3, justify="right", style="bold cyan")
    t.add_column("PID", width=7, justify="right", style="bold white")
    t.add_column("Role", width=24)
    t.add_column("CPU%", width=6, justify="right")
    t.add_column("cpuT", width=7, no_wrap=True)  # sparkline CPU останніх ~45с
    t.add_column("RSS", width=5, justify="right")
    t.add_column("THR", width=4, justify="right")
    t.add_column("IO", width=9, justify="right", no_wrap=True)
    t.add_column("Up", width=7, justify="right")
    t.add_column("Rst", width=4, justify="right")  # restart counter
    t.add_column("Module", ratio=1, overflow="crop", no_wrap=True)

    ph = proc_history or {}

    for i, p in enumerate(sorted_procs, 1):
        is_dup = p.get("is_duplicate", False)
        role = p.get("role", "?")
        tree_prefix = p.get("_tree_prefix", "")
        uptime_str = _format_uptime_short(p.get("uptime_s", 0))

        is_orphan = p.get("is_orphan", False)

        # Стилі
        if is_dup:
            row_style = "bold red"
            dup_mark = " [DUP!]"
        elif is_orphan:
            row_style = "bold yellow"
            dup_mark = " [ORP!]"
        elif role == "aione_top" or role.startswith("sup:"):
            row_style = "dim"
            dup_mark = ""
        else:
            row_style = ""
            dup_mark = ""

        # Колір ролі
        role_colors = {
            "m1_poller": "bold green",
            "connector": "bold cyan",
            "ui": "bold magenta",
            "tick_pub": "bold yellow",
            "tick_preview": "bold yellow",
            "supervisor": "bold blue",
            "sup:connector": "dim cyan",
            "sup:m1_poller": "dim green",
            "sup:tick_publisher": "dim yellow",
            "sup:tick_preview": "dim yellow",
            "sup:ui": "dim magenta",
            "ws_server": "bold green",
            "sup:ws_server": "dim green",
            "derive": "bold white",
            "engine_b": "bold white",
            "aione_top": "dim",
            "broker_sidecar": "bold cyan",
            "sup:broker_sidecar": "dim cyan",
            "m1_ingestion": "bold green",
            "sup:m1_ingestion_worker": "dim green",
            "binance_ingest": "bold cyan",
            "sup:binance_ingest_worker": "dim cyan",
            "binance_tick_pub": "bold yellow",
            "sup:binance_tick_publisher": "dim yellow",
        }
        if is_dup:
            role_style = "bold red"
        elif is_orphan:
            role_style = "bold yellow"
        else:
            role_style = role_colors.get(role, "")

        cpu_val = p.get("cpu", 0.0)
        cpu_color = "green" if cpu_val < 30 else ("yellow" if cpu_val < 70 else "red")

        # Per-process sparkline (6 символів = ~45с при interval=3с)
        pid = p.get("pid")
        hist = ph.get(pid, [])
        spark = _sparkline(hist, length=6) if len(hist) >= 2 else "      "
        spark_style = "dim" if is_dup else (cpu_color if hist else "dim")

        # IO counters (compact R/W)
        io_str = _format_io_compact(
            p.get("io_read_bytes", 0), p.get("io_write_bytes", 0)
        )

        # Role with tree prefix
        role_display = tree_prefix + role
        module_text = p.get("module", "")
        if is_dup:
            module_text += dup_mark

        rst = p.get("restart_count", 0)
        rst_text = Text(str(rst) if rst else "-", style="bold yellow" if rst else "dim")

        t.add_row(
            Text(str(i), style="bold cyan" if not is_dup else "bold red"),
            Text(
                str(p.get("pid", "?")), style="bold white" if not is_dup else "bold red"
            ),
            Text(role_display, style=role_style),
            Text(f"{cpu_val:.1f}", style=cpu_color if not is_dup else "bold red"),
            Text(spark, style=spark_style),
            Text(f"{p.get('rss_mb', 0):.0f}", style=row_style),
            Text(str(p.get("threads", 0)), style=row_style),
            Text(io_str, style=row_style or "dim"),
            Text(uptime_str, style=row_style),
            rst_text,
            Text(module_text, style=row_style if is_dup else "dim"),
        )

    if not sorted_procs:
        t.add_row(
            "",
            "",
            Text("No v3 processes found", style="bold red"),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        )

    return Panel(t, title="[bold]v3 Processes[/]", border_style="blue")


# ---------------------------------------------------------------------------
# 3. Components (Redis, UI, Pidfiles, Derive)
# ---------------------------------------------------------------------------
def build_components(data: Dict[str, Any]) -> Panel:
    """Компоненти: Redis, UI, Pidfiles — горизонтальний layout."""
    # --- Redis ---
    rd = data.get("redis", {})
    redis_tbl = Table(show_header=False, box=None, expand=True, pad_edge=False)
    redis_tbl.add_column("Key", width=10, style="bold")
    redis_tbl.add_column("Val", ratio=1, no_wrap=True, overflow="crop")

    if rd.get("ok"):
        redis_tbl.add_row("Status", _bool_indicator(True, "Connected"))
        endpoint = f"{rd['host']}:{rd['port']}/{rd['db']}"
        redis_tbl.add_row("Endpoint", Text(endpoint, style="dim"))
        redis_tbl.add_row("Namespace", Text(str(rd.get("namespace", "?")), style="dim"))
        redis_tbl.add_row("Memory", Text(str(rd.get("mem_used", "?"))))
        redis_tbl.add_row("Clients", Text(str(rd.get("clients", "?"))))
        keys_str = f"s={rd.get('snap_keys',0)} u={rd.get('upd_keys',0)} t={rd.get('total_keys',0)}"
        redis_tbl.add_row("Keys", Text(keys_str))
        redis_tbl.add_row(
            "Prime", _bool_indicator(rd.get("prime_ready", False), "Ready", "Not Ready")
        )
    else:
        err = str(rd.get("error", "?"))[:30]
        redis_tbl.add_row("Status", _bool_indicator(False, false_text=err))

    redis_panel = Panel(
        redis_tbl, title="[bold red]Redis[/]", border_style="red", expand=True
    )

    # --- UI ---
    ui = data.get("ui", {})
    ui_tbl = Table(show_header=False, box=None, expand=True, pad_edge=False)
    ui_tbl.add_column("Key", width=10, style="bold")
    ui_tbl.add_column("Val", ratio=1, no_wrap=True, overflow="crop")

    if ui.get("ok"):
        ui_tbl.add_row(
            "Status", _bool_indicator(True, f"OK {ui.get('latency_ms', '?')}ms")
        )
        boot_id = str(ui.get("boot_id", "?"))[:10]
        ui_tbl.add_row("Boot ID", Text(boot_id, style="dim"))
        ui_tbl.add_row(
            "Redis", _bool_indicator(ui.get("redis_enabled", False), "ON", "OFF")
        )
        ui_tbl.add_row(
            "Prime", _bool_indicator(ui.get("prime_ready", False), "Yes", "No")
        )
        dh_blocked = ui.get("disk_hotpath_blocked", 0)
        ui_tbl.add_row(
            "DiskBlk",
            Text(str(dh_blocked), style="green" if dh_blocked == 0 else "red"),
        )
        if ui.get("preview_nomix_violation"):
            ui_tbl.add_row("NoMix", _bool_indicator(False, "VIOLATION"))
    else:
        ui_tbl.add_row("Status", _bool_indicator(False, str(ui.get("error", "?"))[:25]))

    ui_panel = Panel(
        ui_tbl, title="[bold magenta]UI :8000[/]", border_style="magenta", expand=True
    )

    # --- WS server :8000 ---
    ws = data.get("ws", {})
    ws_tbl = Table(show_header=False, box=None, expand=True, pad_edge=False)
    ws_tbl.add_column("Key", width=10, style="bold")
    ws_tbl.add_column("Val", ratio=1, no_wrap=True, overflow="crop")
    if ws.get("ok"):
        ws_tbl.add_row(
            "Status", _bool_indicator(True, f"OK {ws.get('latency_ms', '?')}ms")
        )
        ws_tbl.add_row(
            "Endpoint",
            Text(f"{ws.get('host', '?')}:{ws.get('port', '?')}", style="dim"),
        )
    else:
        err = str(ws.get("error", "?"))[:25]
        ws_tbl.add_row("Status", _bool_indicator(False, err or "N/A"))
    ws_panel = Panel(
        ws_tbl, title="[bold green]WS :8000[/]", border_style="green", expand=True
    )

    # --- Pidfiles ---
    pids = data.get("pidfiles", [])
    pid_tbl = Table(show_header=False, box=None, expand=True, pad_edge=False)
    pid_tbl.add_column("Name", ratio=1, no_wrap=True, overflow="crop")
    pid_tbl.add_column("Status", width=8, no_wrap=True, overflow="crop")

    if pids:
        for pf in pids:
            name_pid = f"{pf['name']}:{pf['pid']}"
            pid_tbl.add_row(
                Text(name_pid, style="bold" if pf["alive"] else "bold red"),
                _bool_indicator(pf["alive"], "OK", "DEAD"),
            )
    else:
        pid_tbl.add_row("", "", Text("No pidfiles", style="dim"))

    pid_panel = Panel(
        pid_tbl, title="[bold yellow]Pidfiles[/]", border_style="yellow", expand=True
    )

    # Horizontal layout: Redis | UI | WS | Pidfiles
    outer = Table.grid(expand=True)
    outer.add_column(ratio=3)
    outer.add_column(ratio=2)
    outer.add_column(ratio=2)
    outer.add_column(ratio=2)
    outer.add_row(redis_panel, ui_panel, ws_panel, pid_panel)

    return Panel(outer, title="[bold]Components[/]", border_style="white")


# ---------------------------------------------------------------------------
# 4. Data Freshness table (symbol × TF)
# ---------------------------------------------------------------------------
_TF_LABELS_SHORT = {
    60: "M1",
    180: "M3",
    300: "M5",
    900: "M15",
    1800: "M30",
    3600: "H1",
    14400: "H4",
    86400: "D1",
}
_TRACKED_TFS_DISPLAY = [60, 180, 300, 900, 1800, 3600, 14400, 86400]


def build_freshness_table(freshness: List[Dict[str, Any]]) -> Panel:
    """Таблиця freshness: символ × TF, з кольоровим індикатором.

    Використовується у combined grid (page 2). Standalone для --once.
    """
    t = Table(
        expand=True,
        show_edge=False,
        pad_edge=False,
        box=None,
        header_style="bold white on dark_green",
    )

    t.add_column("Symbol", width=10, style="bold")
    for tf_s in _TRACKED_TFS_DISPLAY:
        label = _TF_LABELS_SHORT.get(tf_s, str(tf_s))
        t.add_column(label, width=13, justify="center")

    for row in freshness:
        sym = row.get("symbol", "?")
        cells: List[Text] = []
        for tf_s in _TRACKED_TFS_DISPLAY:
            tf_data = row.get("tfs", {}).get(tf_s)
            if tf_data is None:
                cells.append(Text("-", style="dim"))
                continue
            last = tf_data.get("last_open", "?")
            age_s = tf_data.get("age_s", 0)
            age_str = _format_age(age_s)
            color = _age_color(age_s, tf_s)
            cells.append(Text(f"{last} ({age_str})", style=color))

        t.add_row(sym, *cells)

    return Panel(t, title="[bold]Data Freshness (disk)[/]", border_style="green")


# ---------------------------------------------------------------------------
# 5. Footer (hotkeys + status)
# ---------------------------------------------------------------------------
_PAGE_NAMES = {1: "Overview", 2: "Pipeline", 3: "Events"}
_TOTAL_PAGES = 3

_FOOTER_KEYS = {
    "normal": [
        ("[Tab]", " Page  "),
        ("[k]", " Kill  "),
        ("[x]", " Restart  "),
        ("[s]", " Start  "),
        ("[c]", " Cache  "),
        ("[r]", " Refresh  "),
        ("[w]", " Watchdog  "),
        ("[Space]", " Pause  "),
        ("[q]", " Quit"),
    ],
    "kill": [
        ("[1-9]", " by #  "),
        ("[d]", " Duplicates  "),
        ("[a]", " All v3  "),
        ("[Esc]", " Cancel"),
    ],
    "confirm_kill_all": [
        ("[y]", " Yes, KILL ALL v3  "),
        ("[n]", " No"),
    ],
    "restart": [
        ("[1-9]", " by #  "),
        ("[a]", " All v3  "),
        ("[Esc]", " Cancel"),
    ],
    "confirm_restart_all": [
        ("[y]", " Yes, RESTART ALL  "),
        ("[n]", " No"),
    ],
    "start": [
        ("[1-9]", " by # (missing)  "),
        ("[a]", " All missing  "),
        ("[Esc]", " Cancel"),
    ],
    "cache": [
        ("[r]", " Redis v3  "),
        ("[t]", " Top cache  "),
        ("[Esc]", " Cancel"),
    ],
}

_MODE_BADGES = {
    "kill": ("  KILL ", "bold white on red"),
    "confirm_kill_all": ("  KILL ALL? ", "bold white on red"),
    "restart": ("  RESTART ", "bold white on dark_orange"),
    "confirm_restart_all": ("  RESTART ALL? ", "bold white on dark_orange"),
    "start": ("  START ", "bold white on green"),
    "cache": ("  CACHE ", "bold white on blue"),
}


def build_footer(
    mode: str = "normal",
    status: str = "",
    paused: bool = False,
    page: int = 1,
    watchdog: bool = False,
) -> Panel:
    """Footer: hotkeys bar + status message + page indicator."""
    lines = Text()

    # Status line
    if status:
        lines.append("  >>> {0}".format(status), style="bold yellow")
        lines.append("\n")

    # Page indicator
    page_name = _PAGE_NAMES.get(page, "?")
    lines.append(
        "  {0}/{1} ".format(page, _TOTAL_PAGES), style="bold white on dark_green"
    )
    lines.append(" {0}  ".format(page_name), style="bold")

    # Paused badge
    if paused:
        lines.append("  PAUSED ", style="bold red on white")
        lines.append("  ")

    # Watchdog badge
    if watchdog:
        lines.append("  WATCHDOG ON ", style="bold white on dark_green")
        lines.append("  ")

    # Mode badge (if not normal)
    if mode in _MODE_BADGES:
        badge_text, badge_style = _MODE_BADGES[mode]
        lines.append(badge_text, style=badge_style)
        lines.append("  ")

    # Hotkey hints
    keys = _FOOTER_KEYS.get(mode, _FOOTER_KEYS["normal"])
    for key_label, key_desc in keys:
        lines.append(key_label, style="bold cyan")
        lines.append(key_desc, style="dim")

    return Panel(lines, border_style="dim", height=4 if status else 3)


# ---------------------------------------------------------------------------
# Main layout builder
# ---------------------------------------------------------------------------
def build_layout(
    data: Dict[str, Any],
    mode: str = "normal",
    status: str = "",
    paused: bool = False,
    watchdog: bool = False,
) -> Layout:
    """Побудувати повний Layout Page 1: header → alerts → processes → components → footer."""
    layout = Layout()

    footer_h = 4 if status else 3
    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="health", size=3),
        Layout(name="alerts", size=1),
        Layout(name="processes", ratio=2),
        Layout(name="components", ratio=1),
        Layout(name="footer", size=footer_h),
    )

    layout["header"].update(build_header(data))
    layout["health"].update(build_health_at_a_glance(data))
    layout["alerts"].update(build_alerts_strip(data))
    layout["processes"].update(
        build_processes_table(data.get("processes", []), data.get("proc_history"))
    )
    layout["components"].update(build_components(data))
    layout["footer"].update(
        build_footer(mode, status, paused, page=1, watchdog=watchdog)
    )

    return layout


# ---------------------------------------------------------------------------
# Page 2: Pipeline Monitor
# ---------------------------------------------------------------------------
_TF_COLS_P2 = [60, 180, 300, 900, 1800, 3600, 14400, 86400]
_TF_SHORT_P2 = {
    60: "M1",
    180: "M3",
    300: "M5",
    900: "M15",
    1800: "M30",
    3600: "H1",
    14400: "H4",
    86400: "D1",
}


def build_bootstrap_panel(pipeline: Dict[str, Any]) -> Panel:
    """Bootstrap + Writer status (з Redis prime:ready + status:snapshot).

    Покращена версія v0.6: тотали по TF, загальний count, writer details.
    """
    tbl = Table(show_header=False, box=None, expand=True, pad_edge=False)
    tbl.add_column("K", width=16, style="bold")
    tbl.add_column("V", ratio=1, no_wrap=True, overflow="crop")

    if not pipeline.get("ok"):
        err = str(pipeline.get("error", "?"))[:60]
        tbl.add_row("Status", Text("ERROR: " + err, style="bold red"))
        return Panel(
            tbl, title="[bold]Bootstrap & Writer[/]", border_style="yellow", height=5
        )

    pr = pipeline.get("prime_ready")
    ss = pipeline.get("status_snapshot")

    if pr:
        boot_id = str(pr.get("boot_id", "?"))
        sym_total = len(pr.get("symbols", []))
        sym_ready = len(pr.get("symbols_ready", []))
        sym_partial = pr.get("cache_prime_partial", [])
        sym_empty = pr.get("cache_prime_empty", [])
        ready = pr.get("ready", False)

        tbl.add_row("Boot ID", Text(boot_id))
        if ready:
            tbl.add_row(
                "Prime",
                _bool_indicator(
                    True, "Ready ({0}/{1} sym)".format(sym_ready, sym_total)
                ),
            )
        else:
            tbl.add_row(
                "Prime",
                _bool_indicator(
                    False, "NOT Ready ({0}/{1})".format(sym_ready, sym_total)
                ),
            )

        if sym_partial:
            tbl.add_row(
                "Partial", Text(", ".join(str(s) for s in sym_partial), style="yellow")
            )
        if sym_empty:
            tbl.add_row(
                "Empty", Text(", ".join(str(s) for s in sym_empty), style="red")
            )

        # --- Per-TF totals (обчислюємо з per_symbol — SSOT) ---
        per_sym = pr.get("prime_tail_len_by_symbol", {})
        if per_sym:
            # Обчислити суми per-TF з per-symbol (prime_tail_len_by_tf_s стале)
            tf_sums = {}  # type: Dict[str, int]
            sym_totals = {}  # type: Dict[str, int]
            for sym, counts in per_sym.items():
                sym_totals[sym] = sum(counts.values())
                for tf_key, n in counts.items():
                    tf_sums[tf_key] = tf_sums.get(tf_key, 0) + n

            parts = []
            grand_total = 0
            for k in sorted(tf_sums.keys(), key=lambda x: int(x)):
                label = _TF_SHORT_P2.get(int(k), k)
                val = tf_sums[k]
                grand_total += val
                parts.append("{0}:{1}".format(label, _compact_count(val)))
            total_line = Text()
            total_line.append(
                "{0}".format(_compact_count(grand_total)), style="bold green"
            )
            total_line.append(" bars  (", style="dim")
            total_line.append("  ".join(parts), style="dim")
            total_line.append(")", style="dim")
            tbl.add_row("Cached bars", total_line)

            # --- Per-symbol range ---
            min_sym = min(sym_totals, key=lambda s: sym_totals[s])
            max_sym = max(sym_totals, key=lambda s: sym_totals[s])
            avg_total = sum(sym_totals.values()) / len(sym_totals)
            range_line = Text()
            range_line.append(
                "avg={0}".format(_compact_count(int(avg_total))), style="bold"
            )
            range_line.append(
                "  min={0}({1})".format(
                    _compact_count(sym_totals[min_sym]), min_sym.replace("/", "_")[:8]
                ),
                style="dim",
            )
            range_line.append(
                "  max={0}({1})".format(
                    _compact_count(sym_totals[max_sym]), max_sym.replace("/", "_")[:8]
                ),
                style="dim",
            )
            tbl.add_row("Sym range", range_line)
    else:
        tbl.add_row(
            "Prime", Text("NO DATA (prime:ready key missing)", style="bold red")
        )

    if ss:
        degraded = ss.get("degraded", [])
        errors = ss.get("errors", [])
        if errors:
            for err in errors[:3]:
                tbl.add_row("Writer Err", Text(str(err)[:80], style="bold red"))
        elif degraded:
            deg_str = ", ".join(str(d) for d in degraded[:5])
            tbl.add_row("Writer", Text("degraded: " + deg_str, style="yellow"))
        else:
            tbl.add_row("Writer", Text("OK", style="green"))

        # Writer details (if available)
        last_cmd = ss.get("last_command")
        if last_cmd:
            tbl.add_row("Last cmd", Text(str(last_cmd)[:60], style="dim"))

    # m1 broker cmd queue depth (BLPOP competition detection)
    q_depth = pipeline.get("m1_cmd_queue_depth", None)
    if q_depth is not None:
        q_color = (
            "green" if q_depth == 0 else ("yellow" if q_depth <= 2 else "bold red")
        )
        q_suffix = "  [!] BLPOP competition?" if q_depth > 2 else ""
        tbl.add_row("m1 cmd queue", Text(f"{q_depth}{q_suffix}", style=q_color))

    return Panel(tbl, title="[bold]Bootstrap & Writer Status[/]", border_style="yellow")


def build_obs_panel(obs: Dict[str, Any]) -> Panel:
    """Панель OBS_60S (last 60s): writer_drops, redis_hit_ratio, uds_geom_fix з логів."""
    tbl = Table(show_header=False, box=None, expand=True, pad_edge=False)
    tbl.add_column("K", width=14, style="bold")
    tbl.add_column("V", ratio=1, no_wrap=False, overflow="crop")
    if not obs:
        tbl.add_row(
            "OBS",
            Text("No OBS_60S in logs (wait 60s or check logs/*.log)", style="dim"),
        )
        return Panel(
            tbl, title="[bold]OBS (last 60s)[/]", border_style="cyan", height=5
        )
    wd = obs.get("writer_drops") or {}
    if wd:
        parts = ["{0}={1}".format(k, v) for k, v in sorted(wd.items())[:8]]
        tbl.add_row(
            "writer_drops", Text(", ".join(parts), style="yellow" if wd else "green")
        )
    rh = obs.get("redis_hit_ratio") or {}
    if rh:
        parts = [
            "{0}:{1:.2f}".format(k, v)
            for k, v in sorted(rh.items(), key=lambda x: int(x[0]))[:6]
        ]
        tbl.add_row("redis_hit_ratio", Text(", ".join(parts), style="dim"))
    ug = obs.get("uds_geom_fix") or {}
    if ug:
        parts = ["{0}={1}".format(k, v) for k, v in sorted(ug.items())[:6]]
        tbl.add_row("uds_geom_fix", Text(", ".join(parts), style="dim"))
    if not wd and not rh and not ug:
        tbl.add_row("label", Text(str(obs.get("label", "?")), style="dim"))
    return Panel(tbl, title="[bold]OBS (last 60s)[/]", border_style="cyan", height=6)


def build_combined_grid(
    pipeline: Dict[str, Any], freshness: List[Dict[str, Any]]
) -> Panel:
    """Об'єднана таблиця: Primed Bars + Data Freshness (symbol × TF).

    Кожна клітинка: count (кольорований за freshness age).
    Під таблицею: рядок freshness age.
    """
    t = Table(
        expand=True,
        show_edge=False,
        pad_edge=False,
        box=None,
        header_style="bold white on dark_blue",
    )
    t.add_column("Symbol", width=10, style="bold")
    for tf_s in _TF_COLS_P2:
        t.add_column(
            _TF_SHORT_P2.get(tf_s, str(tf_s)),
            width=12,
            justify="center",
            no_wrap=True,
            overflow="crop",
        )

    # Побудувати freshness lookup: {norm_symbol: {tf_s: {age_s, last_open}}}
    fresh_map = {}  # type: Dict[str, Dict[int, Dict[str, Any]]]
    for row in freshness:
        sym = row.get("symbol", "?").replace("/", "_")
        fresh_map[sym] = {}
        for tf_s_key, tf_data in row.get("tfs", {}).items():
            fresh_map[sym][int(tf_s_key)] = tf_data

    pr = pipeline.get("prime_ready") if pipeline.get("ok") else None
    per_sym_raw = pr.get("prime_tail_len_by_symbol", {}) if pr else {}

    # Нормалізація символів з Redis (GBP/CAD → GBP_CAD)
    per_sym = {}  # type: Dict[str, Dict[str, int]]
    for sym, counts in per_sym_raw.items():
        per_sym[sym.replace("/", "_")] = counts

    # Об'єднати всі символи з обох джерел
    all_syms = set()
    for sym in per_sym:
        all_syms.add(sym)
    for sym in fresh_map:
        all_syms.add(sym)

    if not all_syms:
        return Panel(
            Text("  No data available", style="dim"),
            title="[bold]Primed Bars + Freshness[/]",
            border_style="blue",
        )

    for sym in sorted(all_syms):
        counts = per_sym.get(sym, {})
        sym_fresh = fresh_map.get(sym, {})
        cells = []  # type: List[Text]
        for tf_s in _TF_COLS_P2:
            n = counts.get(str(tf_s), 0)
            tf_fresh = sym_fresh.get(tf_s)

            if tf_fresh:
                age_s = tf_fresh.get("age_s", 0)
                age_str = _format_age(age_s)
                color = _age_color(age_s, tf_s)
                if n > 0:
                    cells.append(
                        Text("{0} {1}".format(_compact_count(n), age_str), style=color)
                    )
                else:
                    cells.append(Text(age_str, style=color))
            else:
                if n > 0:
                    # Є бари в Redis, але нема freshness (M3, D1)
                    color_n = "green" if n >= 50 else ("yellow" if n > 0 else "dim")
                    cells.append(Text(_compact_count(n), style=color_n))
                else:
                    cells.append(Text("-", style="dim"))

        t.add_row(sym.replace("/", "_")[:10], *cells)

    return Panel(t, title="[bold]Primed Bars + Freshness[/]", border_style="blue")


def build_log_panel(log_lines: List[Dict[str, Any]]) -> Panel:
    """Останні важливі логи (WARN/ERROR + key events)."""
    lines = Text()
    if not log_lines:
        lines.append(
            "  No log files in logs/*.log (or logs/*.err.log / *.out.log)\n",
            style="dim",
        )
        lines.append(
            "  Tip: run aione_top from project root; run platform with --log-dir logs --stdio files\n",
            style="dim",
        )
    else:
        for entry in log_lines:
            ts = entry.get("ts", "?")
            level = entry.get("level", "?")
            msg = entry.get("message", "")
            src = entry.get("source", "")

            # HH:MM:SS
            ts_short = ts[11:19] if len(ts) >= 19 else ts
            lines.append(" {0} ".format(ts_short), style="dim")

            # Level with color
            if level in ("ERROR", "CRITICAL"):
                lines.append("{0:7s}".format(level), style="bold red")
            elif level == "WARNING":
                lines.append("{0:7s}".format(level), style="yellow")
            else:
                lines.append("{0:7s}".format(level), style="cyan")

            # Source tag
            if src:
                tag = (
                    src.replace("_diag.log", "")
                    .replace("_out.log", "")
                    .replace(".log", "")
                )
                lines.append(" [{0:8s}] ".format(tag), style="dim")

            # Message — highlight key parts
            if "DEGRADED" in msg or "ERROR" in msg or "FAIL" in msg:
                lines.append(msg[:100] + "\n", style="bold yellow")
            elif "GAP" in msg or "STALE" in msg:
                lines.append(msg[:100] + "\n", style="yellow")
            else:
                lines.append(msg[:100] + "\n")

    return Panel(
        lines,
        title="[bold]Recent Events (WARN/ERROR + milestones)[/]",
        border_style="red",
    )


def build_pipeline_layout(
    data: Dict[str, Any],
    mode: str = "normal",
    status: str = "",
    paused: bool = False,
    watchdog: bool = False,
) -> Layout:
    """Page 2: Pipeline monitor — bootstrap + combined bars/freshness grid."""
    layout = Layout()
    footer_h = 4 if status else 3

    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="alerts", size=1),
        Layout(name="bootstrap", size=12),
        Layout(name="obs", size=6),
        Layout(name="combined_grid", ratio=1),
        Layout(name="footer", size=footer_h),
    )

    layout["header"].update(build_header(data))
    layout["alerts"].update(build_alerts_strip(data))
    layout["bootstrap"].update(build_bootstrap_panel(data.get("pipeline", {})))
    layout["obs"].update(build_obs_panel(data.get("obs", {})))
    layout["combined_grid"].update(
        build_combined_grid(data.get("pipeline", {}), data.get("freshness", []))
    )
    layout["footer"].update(
        build_footer(mode, status, paused, page=2, watchdog=watchdog)
    )

    return layout


def build_alert_history_panel(alert_history: List[Dict]) -> Panel:
    """Панель з ringbuffer alert-змін сесії (остання перша)."""
    t = Table(
        expand=True,
        show_edge=False,
        pad_edge=False,
        box=None,
        header_style="bold white on dark_blue",
    )
    t.add_column("Time", width=10, style="dim")
    t.add_column("Alerts", ratio=1, overflow="crop", no_wrap=True)

    if not alert_history:
        t.add_row("—", Text("No alert changes this session", style="dim"))
    else:
        for entry in reversed(alert_history):
            ts = entry.get("ts", "")
            alerts = entry.get("alerts", [])
            t.add_row(ts, Text(" | ".join(alerts), style="bold yellow"))

    return Panel(t, title="[bold]Alert History[/] (session)", border_style="yellow")


def build_events_layout(
    data: Dict[str, Any],
    mode: str = "normal",
    status: str = "",
    paused: bool = False,
    watchdog: bool = False,
) -> Layout:
    """Page 3: Events — Alert history + Recent WARN/ERROR + milestones log tail."""
    layout = Layout()
    footer_h = 4 if status else 3
    alert_history = data.get("alert_history", [])
    # Якщо є alert history — показати панель вгорі (5 рядків), решта — log tail
    ah_size = min(len(alert_history) + 3, 8) if alert_history else 0

    if ah_size:
        layout.split_column(
            Layout(name="header", size=5),
            Layout(name="alerts", size=1),
            Layout(name="alert_history", size=ah_size),
            Layout(name="logs", ratio=1),
            Layout(name="footer", size=footer_h),
        )
        layout["alert_history"].update(build_alert_history_panel(alert_history))
    else:
        layout.split_column(
            Layout(name="header", size=5),
            Layout(name="alerts", size=1),
            Layout(name="logs", ratio=1),
            Layout(name="footer", size=footer_h),
        )

    layout["header"].update(build_header(data))
    layout["alerts"].update(build_alerts_strip(data))
    layout["logs"].update(build_log_panel(data.get("log_tail", [])))
    layout["footer"].update(
        build_footer(mode, status, paused, page=3, watchdog=watchdog)
    )

    return layout
