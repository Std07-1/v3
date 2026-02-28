"""
tools/mcp/platform_server.py — MCP сервер для Claude Copilot.

Надає Claude структурований доступ до runtime стану Trading Platform v3:
- HTTP API платформи (status, bars, updates, config)
- Redis live state
- JSONL data files на диску
- Exit gates
- Log-файли

Запуск: Python 3.13+, mcp SDK ≥1.0
  python tools/mcp/platform_server.py

Конфігурація: .vscode/mcp.json (auto-detected by VS Code Copilot)
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ── Resolve project root ──────────────────────────────
_THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _THIS_DIR.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
DATA_ROOT = PROJECT_ROOT / "data_v3"
LOGS_DIR = PROJECT_ROOT / "logs"

# ── Platform API defaults ─────────────────────────────
API_BASE_V3 = "http://127.0.0.1:8089"
WS_BASE_V4 = "http://127.0.0.1:8000"

# ── TF labels ─────────────────────────────────────────
TF_LABELS = {
    60: "M1", 180: "M3", 300: "M5", 900: "M15",
    1800: "M30", 3600: "H1", 14400: "H4", 86400: "D1",
}
LABEL_TO_TF = {v: k for k, v in TF_LABELS.items()}

# ── MCP server ────────────────────────────────────────
mcp = FastMCP("aione-trading-platform")


# ═══════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════

def _http_get(url: str, timeout: float = 5.0) -> dict:
    """GET запит до HTTP API платформи. Повертає parsed JSON."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"Платформа недоступна ({url}): {e.reason}"}
    except Exception as e:
        return {"error": f"HTTP помилка: {e}"}


def _redis_client():
    """Отримати Redis client. Повертає None якщо Redis недоступний."""
    try:
        import redis
    except ImportError:
        return None
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        rcfg = cfg.get("redis", {})
        if not rcfg.get("enabled", False):
            return None
        return redis.Redis(
            host=rcfg.get("host", "127.0.0.1"),
            port=rcfg.get("port", 6379),
            db=rcfg.get("db", 1),
            decode_responses=True,
            socket_timeout=3,
        )
    except Exception:
        return None


def _load_config() -> dict:
    """Прочитати config.json."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}


def _sym_dir(symbol: str) -> str:
    """Каноничне ім'я директорії символу: XAU/USD → XAU_USD."""
    return symbol.replace("/", "_")


def _format_ms(ms: int | float) -> str:
    """Epoch ms → ISO8601 UTC рядок."""
    try:
        dt = datetime.datetime.utcfromtimestamp(ms / 1000)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ms)


def _tf_resolve(tf_input: str) -> int | None:
    """Розпарсити TF з label або числа: 'M5' → 300, '300' → 300."""
    if tf_input in LABEL_TO_TF:
        return LABEL_TO_TF[tf_input]
    try:
        v = int(tf_input)
        if v in TF_LABELS:
            return v
        return None
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════
# TOOL 1: Platform Status
# ═══════════════════════════════════════════════════════

@mcp.tool()
def platform_status() -> str:
    """
    Отримати повний статус Trading Platform v3.
    Показує: boot_id, prime_ready, redis стан, preview/nomix,
    disk policy telemetry, bar counts per TF, uptime.

    Використовуй цей інструмент ПЕРШИМ при будь-якому аналізі
    стану системи або діагностиці проблем.
    """
    data = _http_get(f"{API_BASE_V3}/api/status")
    if "error" in data:
        return f"❌ Платформа недоступна: {data['error']}\n\nПеревір: чи запущено `python -m app.main --mode all --stdio pipe`?"

    status = data.get("status", data)
    lines = ["# 🟢 Platform Status\n"]

    # Boot info
    boot_id = status.get("boot_id", "?")
    uptime = status.get("disk_bootstrap_elapsed_s", 0)
    prime = status.get("prime_ready", False)
    lines.append(f"**Boot ID**: `{boot_id[:12]}...`")
    lines.append(f"**Uptime**: {uptime:.0f}s ({uptime/3600:.1f}h)")
    lines.append(f"**Prime Ready**: {'✅ Yes' if prime else '⏳ No'}")

    # Prime payload
    pp = status.get("prime_ready_payload", {})
    if isinstance(pp, dict):
        tails = pp.get("prime_tail_len_by_tf_s", {})
        if tails:
            lines.append("\n## Bar Counts per TF (Redis tail)")
            for tf_s, count in sorted(tails.items(), key=lambda x: int(x[0])):
                label = TF_LABELS.get(int(tf_s), tf_s)
                lines.append(f"  - **{label}** ({tf_s}s): {count} bars")

    # Redis
    redis_ok = status.get("redis_enabled", False)
    mismatch = status.get("redis_spec_mismatch", False)
    lines.append(f"\n**Redis**: {'✅ Enabled' if redis_ok else '❌ Disabled'}")
    if mismatch:
        fields = status.get("redis_spec_mismatch_fields", [])
        lines.append(f"⚠️ **Redis spec mismatch**: {', '.join(fields)}")

    # Preview
    preview_tfs = status.get("preview_tf_allowlist_s", [])
    preview_labels = [TF_LABELS.get(t, str(t)) for t in preview_tfs]
    lines.append(f"**Preview TFs**: {', '.join(preview_labels)}")
    lines.append(f"**Preview updates total**: {status.get('preview_tail_updates_total', 0)}")

    # NoMix violation
    if status.get("preview_nomix_violation"):
        reason = status.get("preview_nomix_violation_reason", "?")
        lines.append(f"🚨 **NoMix violation** (I3): {reason}")

    # Disk policy
    lines.append(f"\n**Disk hotpath blocked**: {status.get('disk_hotpath_blocked_total', 0)}")
    lines.append(f"**Disk bootstrap reads**: {status.get('disk_bootstrap_reads_total', 0)}")

    # Targets
    targets = status.get("prime_target_min_by_tf_s", {})
    if targets:
        lines.append("\n## Cold-start targets (min bars)")
        for tf_s, min_n in sorted(targets.items(), key=lambda x: int(x[0])):
            label = TF_LABELS.get(int(tf_s), tf_s)
            lines.append(f"  - {label}: {min_n}")

    lines.append(f"\n*Snapshot at {_format_ms(status.get('ts_ms', 0))}*")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# TOOL 2: Inspect Bars
# ═══════════════════════════════════════════════════════

@mcp.tool()
def inspect_bars(
    symbol: str = "XAU/USD",
    tf: str = "M5",
    limit: int = 20,
    show_last: int = 5,
) -> str:
    """
    Отримати бари з HTTP API платформи та проаналізувати їх.

    Показує: останні N барів, gaps, complete/preview стан,
    вік останнього бару, meta інформацію.

    Args:
        symbol: Символ (XAU/USD, NAS100, SPX500...)
        tf: TimeFrame — label (M1, M5, H1, H4, D1) або число (300, 3600...)
        limit: Скільки барів запросити з API (max 30000)
        show_last: Скільки останніх показати у відповіді
    """
    tf_s = _tf_resolve(tf)
    if tf_s is None:
        return f"❌ Невідомий TF: {tf}. Допустимі: {', '.join(TF_LABELS.values())}"

    url = f"{API_BASE_V3}/api/bars?symbol={symbol}&tf_s={tf_s}&limit={limit}"
    data = _http_get(url)
    if "error" in data:
        return f"❌ Не вдалось отримати бари: {data['error']}"

    if not data.get("ok"):
        return f"❌ API повернув помилку: {json.dumps(data, ensure_ascii=False)[:500]}"

    bars = data.get("bars", [])
    meta = data.get("meta", {})
    warnings = data.get("warnings", [])
    boot_id = data.get("boot_id", "?")
    tf_label = TF_LABELS.get(tf_s, str(tf_s))

    lines = [f"# 📊 Bars: {symbol} {tf_label}\n"]
    lines.append(f"**Total bars**: {len(bars)}")
    lines.append(f"**Boot ID**: `{boot_id[:12]}...`")
    lines.append(f"**Source**: {meta.get('source', '?')}")

    if meta.get("extensions"):
        ext = meta["extensions"]
        lines.append(f"**Plane**: {ext.get('plane', '?')}")

    if warnings:
        lines.append(f"\n⚠️ **Warnings**: {'; '.join(warnings)}")

    if not bars:
        lines.append("\n*Немає барів.*")
        return "\n".join(lines)

    # Date range
    first = bars[0]
    last = bars[-1]
    lines.append(f"\n**Range**: {_format_ms(first.get('open_time_ms', 0))} → {_format_ms(last.get('open_time_ms', 0))}")

    # Age of last bar
    last_close_ms = last.get("close_time_ms", last.get("open_time_ms", 0) + tf_s * 1000)
    age_s = (time.time() * 1000 - last_close_ms) / 1000
    lines.append(f"**Last bar age**: {age_s:.0f}s ({age_s/60:.1f}min)")
    lines.append(f"**Last bar complete**: {last.get('complete', '?')}")

    # Gap analysis
    gaps = []
    for i in range(1, len(bars)):
        expected = bars[i - 1].get("open_time_ms", 0) + tf_s * 1000
        actual = bars[i].get("open_time_ms", 0)
        if actual != expected:
            gap_bars = (actual - expected) / (tf_s * 1000)
            gaps.append({
                "after": _format_ms(bars[i - 1].get("open_time_ms", 0)),
                "expected": _format_ms(expected),
                "actual": _format_ms(actual),
                "gap_bars": gap_bars,
            })

    if gaps:
        lines.append(f"\n## ⚠️ Gaps detected: {len(gaps)}")
        for g in gaps[:10]:  # max 10
            lines.append(f"  - After {g['after']}: gap of {g['gap_bars']:.0f} bars")
    else:
        lines.append(f"\n✅ **No gaps** у {len(bars)} барах")

    # Complete/preview stats
    complete_count = sum(1 for b in bars if b.get("complete"))
    preview_count = len(bars) - complete_count
    lines.append(f"\n**Complete**: {complete_count} | **Preview**: {preview_count}")

    # Source distribution
    sources: dict[str, int] = {}
    for b in bars:
        s = b.get("src", "unknown")
        sources[s] = sources.get(s, 0) + 1
    lines.append(f"**Sources**: {', '.join(f'{k}={v}' for k, v in sorted(sources.items()))}")

    # Last N bars
    show = min(show_last, len(bars))
    lines.append(f"\n## Last {show} bars")
    lines.append("| Time (UTC) | O | H | L | C | Vol | Complete | Src |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for b in bars[-show:]:
        t = _format_ms(b.get("open_time_ms", 0))
        lines.append(
            f"| {t} | {b.get('open', 0):.2f} | {b.get('high', 0):.2f} | "
            f"{b.get('low', 0):.2f} | {b.get('close', 0):.2f} | "
            f"{b.get('volume', 0)} | {b.get('complete', '?')} | {b.get('src', '?')} |"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# TOOL 3: Inspect Updates (live events)
# ═══════════════════════════════════════════════════════

@mcp.tool()
def inspect_updates(
    symbol: str = "XAU/USD",
    tf: str = "M5",
    limit: int = 10,
) -> str:
    """
    Отримати останні live update events з /api/updates.
    Показує: cursor_seq, boot_id, event_ts, complete/source,
    затримку між event та API.

    Args:
        symbol: Символ
        tf: TimeFrame (label або число)
        limit: Кількість events
    """
    tf_s = _tf_resolve(tf)
    if tf_s is None:
        return f"❌ Невідомий TF: {tf}"

    url = f"{API_BASE_V3}/api/updates?symbol={symbol}&tf_s={tf_s}&limit={limit}"
    data = _http_get(url)
    if "error" in data:
        return f"❌ {data['error']}"
    if not data.get("ok"):
        return f"❌ API error: {json.dumps(data, ensure_ascii=False)[:400]}"

    events = data.get("events", [])
    cursor = data.get("cursor_seq", 0)
    boot_id = data.get("boot_id", "?")
    tf_label = TF_LABELS.get(tf_s, str(tf_s))

    lines = [f"# 🔄 Updates: {symbol} {tf_label}\n"]
    lines.append(f"**Cursor seq**: {cursor}")
    lines.append(f"**Boot ID**: `{boot_id[:12]}...`")
    lines.append(f"**Events count**: {len(events)}")

    # Latency
    api_ts = data.get("api_seen_ts_ms", 0)
    ssot_ts = data.get("ssot_write_ts_ms", 0)
    if api_ts and ssot_ts:
        latency = api_ts - ssot_ts
        lines.append(f"**API-to-SSOT latency**: {latency}ms")

    if not events:
        lines.append("\n*Немає нових events.*")
        return "\n".join(lines)

    lines.append("\n## Events")
    for ev in events[-10:]:
        bar = ev.get("bar", {})
        key = ev.get("key", {})
        lines.append(
            f"- **{_format_ms(key.get('open_ms', 0))}** | "
            f"C={bar.get('close', 0):.2f} V={bar.get('volume', 0)} | "
            f"complete={ev.get('complete')} src={ev.get('source', '?')}"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# TOOL 4: Platform Config
# ═══════════════════════════════════════════════════════

@mcp.tool()
def platform_config(section: str = "") -> str:
    """
    Прочитати config.json — SSOT конфігурацію платформи.

    Args:
        section: Конкретна секція (redis, bootstrap, m1_poller, channels,
                 market_calendar_by_group, ws_server) або пусто для повного конфігу
    """
    cfg = _load_config()
    if "error" in cfg:
        return f"❌ {cfg['error']}"

    if section:
        val = cfg.get(section)
        if val is None:
            available = ", ".join(k for k in cfg if isinstance(cfg[k], dict))
            return f"❌ Секція '{section}' не знайдена. Доступні: {available}"
        return f"# Config: {section}\n```json\n{json.dumps(val, indent=2, ensure_ascii=False)}\n```"

    # Summary
    lines = ["# 📋 Platform Config (SSOT)\n"]
    lines.append(f"**Символи**: {', '.join(cfg.get('symbols', []))}")

    tfa = cfg.get("tf_allowlist_s", [])
    labels = [TF_LABELS.get(t, str(t)) for t in tfa]
    lines.append(f"**TF allowlist**: {', '.join(labels)}")
    lines.append(f"**Broker base TFs**: {cfg.get('broker_base_tfs_s', [])} (порожній = ADR-0023)")
    lines.append(f"**Derived TFs**: {[TF_LABELS.get(t, t) for t in cfg.get('derived_tfs_s', [])]}")

    ptf = cfg.get("preview_tick_tfs_s", [])
    lines.append(f"**Preview TFs**: {[TF_LABELS.get(t, str(t)) for t in ptf]}")
    lines.append(f"**Preview interval**: {cfg.get('preview_tick_publish_min_interval_ms', '?')}ms")

    lines.append(f"\n**Tick stream**: {'✅' if cfg.get('tick_stream_enabled') else '❌'}")
    lines.append(f"**D1 tick relay**: {'✅' if cfg.get('d1_live_tick_relay_enabled') else '❌'}")
    lines.append(f"**Calendar gate**: {'✅' if cfg.get('calendar_gate_enabled') else '❌'}")

    # Redis
    rcfg = cfg.get("redis", {})
    lines.append(f"\n**Redis**: {'✅' if rcfg.get('enabled') else '❌'} "
                 f"({rcfg.get('host', '?')}:{rcfg.get('port', '?')}/db{rcfg.get('db', '?')})")
    lines.append(f"**Redis namespace**: {rcfg.get('namespace', '?')}")

    # WS
    wcfg = cfg.get("ws_server", {})
    lines.append(f"\n**WS server**: {'✅' if wcfg.get('enabled') else '❌'} "
                 f"({wcfg.get('host', '?')}:{wcfg.get('port', '?')})")

    # Anchors
    lines.append(f"\n**H4 anchor offset**: {cfg.get('day_anchor_offset_s', '?')}s "
                 f"({cfg.get('day_anchor_offset_s', 0) // 3600}:{(cfg.get('day_anchor_offset_s', 0) % 3600) // 60:02d} UTC)")
    lines.append(f"**D1 anchor offset**: {cfg.get('day_anchor_offset_s_d1', '?')}s "
                 f"({cfg.get('day_anchor_offset_s_d1', 0) // 3600}:{(cfg.get('day_anchor_offset_s_d1', 0) % 3600) // 60:02d} UTC)")

    # Calendar groups
    groups = cfg.get("market_calendar_symbol_groups", {})
    if groups:
        lines.append("\n## Symbol → Calendar Group")
        for sym, grp in sorted(groups.items()):
            lines.append(f"  - {sym} → `{grp}`")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# TOOL 5: Redis Inspect
# ═══════════════════════════════════════════════════════

@mcp.tool()
def redis_inspect(
    pattern: str = "v3_local:*",
    command: str = "keys",
    key: str = "",
    limit: int = 50,
) -> str:
    """
    Інспектувати Redis live стан платформи.

    Args:
        pattern: Key pattern для SCAN (default: v3_local:*)
        command: Команда: 'keys' (список ключів), 'get' (значення ключа),
                 'type' (тип ключа), 'ttl' (TTL ключа),
                 'dbsize' (кількість ключів), 'info' (Redis info)
        key: Конкретний ключ для get/type/ttl
        limit: Max ключів для keys команди
    """
    r = _redis_client()
    if r is None:
        return "❌ Redis недоступний. Перевір: redis-server запущений? config.json redis.enabled=true?"

    try:
        if command == "dbsize":
            size = r.dbsize()
            return f"# Redis DB size\n**Keys**: {size}"

        if command == "info":
            info = r.info()
            lines = ["# Redis Info\n"]
            for k in ["redis_version", "used_memory_human", "connected_clients",
                       "uptime_in_seconds", "keyspace_hits", "keyspace_misses"]:
                lines.append(f"**{k}**: {info.get(k, '?')}")
            db_info = info.get(f"db{r.connection_pool.connection_kwargs.get('db', 0)}", {})
            if db_info:
                lines.append(f"\n**DB stats**: {db_info}")
            return "\n".join(lines)

        if command == "get":
            if not key:
                return "❌ Вкажи key для команди 'get'"
            val = r.get(key)
            if val is None:
                # Спробуємо як hash
                t = r.type(key)
                if t == "hash":
                    data = r.hgetall(key)
                    return f"# Redis HASH: {key}\n```json\n{json.dumps(data, indent=2, ensure_ascii=False)}\n```"
                elif t == "list":
                    data = r.lrange(key, 0, limit - 1)
                    return f"# Redis LIST: {key} ({r.llen(key)} items)\n```json\n{json.dumps(data[:limit], indent=2)}\n```"
                elif t == "zset":
                    data = r.zrange(key, 0, limit - 1, withscores=True)
                    return f"# Redis ZSET: {key} ({r.zcard(key)} members)\n{data[:limit]}"
                return f"Key `{key}` не знайдено (type={t})"
            try:
                parsed = json.loads(val)
                return f"# Redis GET: {key}\n```json\n{json.dumps(parsed, indent=2, ensure_ascii=False)}\n```"
            except (json.JSONDecodeError, TypeError):
                return f"# Redis GET: {key}\n```\n{val[:2000]}\n```"

        if command == "type":
            if not key:
                return "❌ Вкажи key"
            return f"Type of `{key}`: **{r.type(key)}** (TTL: {r.ttl(key)}s)"

        if command == "ttl":
            if not key:
                return "❌ Вкажи key"
            return f"TTL of `{key}`: **{r.ttl(key)}**s"

        # Default: keys
        cursor = 0
        keys_list = []
        while len(keys_list) < limit:
            cursor, batch = r.scan(cursor, match=pattern, count=100)
            keys_list.extend(batch)
            if cursor == 0:
                break

        keys_list = sorted(keys_list[:limit])
        lines = [f"# Redis Keys: `{pattern}`\n"]
        lines.append(f"**Found**: {len(keys_list)} keys (limit={limit})\n")

        # Group by prefix
        groups: dict[str, list] = {}
        for k in keys_list:
            prefix = ":".join(k.split(":")[:3]) if ":" in k else k
            groups.setdefault(prefix, []).append(k)

        for prefix, kk in sorted(groups.items()):
            lines.append(f"### `{prefix}` ({len(kk)} keys)")
            for k in kk[:20]:
                t = r.type(k)
                ttl = r.ttl(k)
                lines.append(f"  - `{k}` (type={t}, ttl={ttl}s)")

        return "\n".join(lines)

    except Exception as e:
        return f"❌ Redis помилка: {e}"


# ═══════════════════════════════════════════════════════
# TOOL 6: Data Files Audit
# ═══════════════════════════════════════════════════════

@mcp.tool()
def data_files_audit(
    symbol: str = "XAU/USD",
    tf: str = "M1",
    last_n_files: int = 3,
) -> str:
    """
    Аудит JSONL data файлів на диску (data_v3/).

    Показує: кількість файлів, загальну кількість барів,
    date range, останні файли з деталями.

    Args:
        symbol: Символ
        tf: TimeFrame
        last_n_files: Скільки останніх файлів показати детально
    """
    tf_s = _tf_resolve(tf)
    if tf_s is None:
        return f"❌ Невідомий TF: {tf}"

    sym_dir = _sym_dir(symbol)
    tf_dir = DATA_ROOT / sym_dir / f"tf_{tf_s}"

    if not tf_dir.exists():
        # Спробуємо знайти
        candidates = list(DATA_ROOT.glob(f"**/tf_{tf_s}"))
        if candidates:
            return f"❌ Директорія {tf_dir} не існує. Знайдено: {[str(c) for c in candidates]}"
        return f"❌ Директорія {tf_dir} не існує. Немає даних для {symbol} {TF_LABELS.get(tf_s, tf)}"

    jsonl_files = sorted(tf_dir.glob("*.jsonl"))
    tf_label = TF_LABELS.get(tf_s, str(tf_s))

    lines = [f"# 💾 Data Files: {symbol} {tf_label}\n"]
    lines.append(f"**Directory**: `{tf_dir.relative_to(PROJECT_ROOT)}`")
    lines.append(f"**JSONL files**: {len(jsonl_files)}")

    if not jsonl_files:
        lines.append("\n*Немає файлів.*")
        return "\n".join(lines)

    # Total size
    total_bytes = sum(f.stat().st_size for f in jsonl_files)
    lines.append(f"**Total size**: {total_bytes / 1024 / 1024:.2f} MB")
    lines.append(f"**Date range**: `{jsonl_files[0].stem}` → `{jsonl_files[-1].stem}`")

    # Count total bars (sample first and last files)
    total_bars = 0
    for f in jsonl_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                total_bars += sum(1 for line in fh if line.strip())
        except Exception:
            pass

    lines.append(f"**Total bars**: {total_bars:,}")

    # Last N files detail
    lines.append(f"\n## Last {last_n_files} files")
    for f in jsonl_files[-last_n_files:]:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                file_lines = [low.strip() for low in fh if low.strip()]
            bar_count = len(file_lines)
            if file_lines:
                first_bar = json.loads(file_lines[0])
                last_bar = json.loads(file_lines[-1])
                lines.append(f"\n### `{f.name}` ({bar_count} bars)")
                lines.append(f"  - First: {_format_ms(first_bar.get('open_time_ms', 0))}")
                lines.append(f"  - Last: {_format_ms(last_bar.get('open_time_ms', 0))}")
                lines.append(f"  - Size: {f.stat().st_size / 1024:.1f} KB")

                # Check monotonicity
                timestamps = [json.loads(low).get("open_time_ms", 0) for low in file_lines]
                monotonic = all(timestamps[i] < timestamps[i + 1] for i in range(len(timestamps) - 1))
                lines.append(f"  - Monotonic: {'✅' if monotonic else '❌ ПОРУШЕННЯ!'}")
            else:
                lines.append(f"\n### `{f.name}` (порожній)")
        except Exception as e:
            lines.append(f"\n### `{f.name}` — помилка: {e}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# TOOL 7: Run Exit Gates
# ═══════════════════════════════════════════════════════

@mcp.tool()
def run_exit_gates(gate_name: str = "") -> str:
    """
    Запустити exit gates — quality gates платформи.
    Перевіряють dependency rule, HTF availability, D1 anchor alignment.

    Args:
        gate_name: Конкретний gate (dependency_rule, htf_available,
                   d1_anchor_alignment) або пусто для ALL
    """
    python = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    if not os.path.exists(python):
        python = sys.executable

    try:
        cmd = [python, "-m", "tools.run_exit_gates"]
        if gate_name:
            cmd.extend(["--gate", gate_name])
        else:
            cmd.extend(["--manifest", "tools/exit_gates/manifest.json"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        output = result.stdout + result.stderr
        if result.returncode == 0:
            return f"# ✅ Exit Gates\n\n```\n{output.strip()}\n```"
        else:
            return f"# ❌ Exit Gates FAILED (code={result.returncode})\n\n```\n{output.strip()}\n```"
    except subprocess.TimeoutExpired:
        return "❌ Exit gates timeout (>30s)"
    except Exception as e:
        return f"❌ Не вдалось запустити exit gates: {e}"


# ═══════════════════════════════════════════════════════
# TOOL 8: Log Tail
# ═══════════════════════════════════════════════════════

@mcp.tool()
def log_tail(
    process: str = "",
    lines_count: int = 50,
    grep: str = "",
) -> str:
    """
    Прочитати останні рядки логів процесу.

    Args:
        process: Ім'я процесу (m1_poller, tick_publisher, tick_preview,
                 connector, ui, ws_server) або пусто для списку доступних
        lines_count: Кількість останніх рядків
        grep: Фільтр (підрядок для пошуку в логах)
    """
    if not LOGS_DIR.exists():
        return "❌ Директорія logs/ не існує"

    log_files = sorted(LOGS_DIR.glob("*.log"))
    log_files += sorted(LOGS_DIR.glob("*.out.log"))

    if not process:
        lines = ["# 📝 Available Log Files\n"]
        for f in log_files:
            size = f.stat().st_size
            mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
            lines.append(f"  - `{f.name}` ({size / 1024:.1f} KB, modified {mtime:%Y-%m-%d %H:%M})")
        if not log_files:
            lines.append("*Немає log файлів.*")
        return "\n".join(lines)

    # Find matching log
    matches = [f for f in log_files if process in f.stem]
    if not matches:
        # Also check stdout/stderr pattern
        matches = list(LOGS_DIR.glob(f"{process}*.log"))
        matches += list(LOGS_DIR.glob(f"*{process}*.log"))

    if not matches:
        available = ", ".join(f.stem for f in log_files)
        return f"❌ Лог для '{process}' не знайдено. Доступні: {available}"

    log_file = matches[0]
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()

        if grep:
            all_lines = [low for low in all_lines if grep.lower() in low.lower()]

        tail = all_lines[-lines_count:]
        header = f"# 📝 Log: {log_file.name}"
        if grep:
            header += f" (filtered: '{grep}')"
        header += f"\n*{len(all_lines)} total lines, showing last {len(tail)}*\n"

        return header + "```\n" + "".join(tail) + "```"
    except Exception as e:
        return f"❌ Помилка читання {log_file}: {e}"


# ═══════════════════════════════════════════════════════
# TOOL 9: Derive Chain Status
# ═══════════════════════════════════════════════════════

@mcp.tool()
def derive_chain_status(symbol: str = "XAU/USD") -> str:
    """
    Показати стан derive chain для символу.
    Перевіряє наявність даних по всіх TF каскаду M1→D1.

    Args:
        symbol: Символ
    """
    cfg = _load_config()
    tf_allowlist = cfg.get("tf_allowlist_s", [60, 180, 300, 900, 1800, 3600, 14400, 86400])
    sym_dir = _sym_dir(symbol)

    lines = [f"# 🔗 Derive Chain: {symbol}\n"]
    lines.append("Каскад: `M1→M3(×3)→M5(×5)→M15(×3)→M30(×2)→H1(×2)→H4(×4)` + `M1→D1(×1440)`\n")

    chain = [
        (60, "M1", "broker/poller"),
        (180, "M3", "derived from M1×3"),
        (300, "M5", "derived from M1×5"),
        (900, "M15", "derived from M5×3"),
        (1800, "M30", "derived from M15×2"),
        (3600, "H1", "derived from M30×2"),
        (14400, "H4", "derived from H1×4"),
        (86400, "D1", "derived from M1×1440"),
    ]

    lines.append("| TF | Source | Files | Bars | Last Bar | Age |")
    lines.append("|---|---|---|---|---|---|")

    for tf_s, label, source in chain:
        tf_dir = DATA_ROOT / sym_dir / f"tf_{tf_s}"
        if not tf_dir.exists():
            lines.append(f"| **{label}** | {source} | ❌ missing | 0 | — | — |")
            continue

        jsonl_files = sorted(tf_dir.glob("*.jsonl"))
        total_bars = 0
        last_bar_ms = 0

        for f in jsonl_files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            total_bars += 1
                            try:
                                b = json.loads(line)
                                ms = b.get("open_time_ms", 0)
                                if ms > last_bar_ms:
                                    last_bar_ms = ms
                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass

        if last_bar_ms:
            age_s = (time.time() * 1000 - last_bar_ms) / 1000
            age_str = f"{age_s / 3600:.1f}h" if age_s > 3600 else f"{age_s / 60:.0f}m"
            lines.append(
                f"| **{label}** | {source} | {len(jsonl_files)} | {total_bars:,} | "
                f"{_format_ms(last_bar_ms)} | {age_str} |"
            )
        else:
            lines.append(f"| **{label}** | {source} | {len(jsonl_files)} | {total_bars} | — | — |")

    # Check Redis tail counts
    r = _redis_client()
    if r:
        lines.append("\n## Redis Tail Counts")
        ns = cfg.get("redis", {}).get("namespace", "v3_local")
        for tf_s, label, _ in chain:
            key = f"{ns}:ohlcv:snap:{_sym_dir(symbol)}:{tf_s}"
            try:
                t = r.type(key)
                if t == "list":
                    count = r.llen(key)
                elif t == "zset":
                    count = r.zcard(key)
                else:
                    count = "N/A"
                ttl = r.ttl(key)
                lines.append(f"  - **{label}**: {count} (type={t}, ttl={ttl}s)")
            except Exception:
                lines.append(f"  - **{label}**: ❌ error")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# TOOL 10: WS Server Status
# ═══════════════════════════════════════════════════════

@mcp.tool()
def ws_server_check() -> str:
    """
    Перевірити стан WebSocket сервера (порт 8000).
    Показує: чи запущений, конфігурацію, підключених клієнтів.
    """
    cfg = _load_config()
    ws_cfg = cfg.get("ws_server", {})
    enabled = ws_cfg.get("enabled", False)
    host = ws_cfg.get("host", "127.0.0.1")
    port = ws_cfg.get("port", 8000)

    lines = [f"# 🌐 WS Server Status\n"]
    lines.append(f"**Config enabled**: {'✅' if enabled else '❌'}")
    lines.append(f"**Endpoint**: ws://{host}:{port}/ws")
    lines.append(f"**Heartbeat**: {ws_cfg.get('heartbeat_interval_s', 30)}s")
    lines.append(f"**Delta poll**: {ws_cfg.get('delta_poll_interval_s', 1.0)}s")

    # Try HTTP root
    data = _http_get(f"http://{host}:{port}/", timeout=2)
    if "error" in data:
        lines.append(f"\n⚠️ Сервер не відповідає на HTTP: {data['error']}")
    else:
        lines.append(f"\n✅ Сервер відповідає")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# TOOL 11: Quick Health Check
# ═══════════════════════════════════════════════════════

@mcp.tool()
def health_check() -> str:
    """
    Швидка перевірка здоров'я всіх компонентів.
    Один виклик = повна картина: API, WS, Redis, Data, Exit Gates.
    """
    lines = ["# 🏥 Quick Health Check\n"]

    # 1. HTTP API
    status = _http_get(f"{API_BASE_V3}/api/status", timeout=3)
    if "error" in status:
        lines.append("## ❌ HTTP API (port 8089): OFFLINE")
        lines.append(f"  {status['error']}")
    else:
        s = status.get("status", status)
        prime = s.get("prime_ready", False)
        boot = s.get("boot_id", "?")[:12]
        uptime = s.get("disk_bootstrap_elapsed_s", 0)
        lines.append(f"## ✅ HTTP API (port 8089): OK")
        lines.append(f"  Boot: `{boot}...` | Prime: {'✅' if prime else '⏳'} | Uptime: {uptime:.0f}s")

    # 2. WS Server
    cfg = _load_config()
    ws_port = cfg.get("ws_server", {}).get("port", 8000)
    ws = _http_get(f"http://127.0.0.1:{ws_port}/", timeout=2)
    if "error" in ws:
        lines.append(f"\n## ⚠️ WS Server (port {ws_port}): NOT RESPONDING")
    else:
        lines.append(f"\n## ✅ WS Server (port {ws_port}): OK")

    # 3. Redis
    r = _redis_client()
    if r:
        try:
            r.ping()
            dbsize = r.dbsize()
            lines.append(f"\n## ✅ Redis: OK ({dbsize} keys)")
        except Exception as e:
            lines.append(f"\n## ❌ Redis: ERROR ({e})")
    else:
        lines.append("\n## ⚠️ Redis: DISABLED or UNAVAILABLE")

    # 4. Data files
    symbols = cfg.get("symbols", [])
    for sym in symbols[:3]:  # max 3 symbols
        sym_path = DATA_ROOT / _sym_dir(sym)
        if sym_path.exists():
            tf_dirs = sorted(sym_path.glob("tf_*"))
            lines.append(f"\n## 💾 Data: {sym} ({len(tf_dirs)} TF dirs)")
            for td in tf_dirs:
                jsonl_count = len(list(td.glob("*.jsonl")))
                lines.append(f"  - `{td.name}`: {jsonl_count} files")
        else:
            lines.append(f"\n## ⚠️ Data: {sym} — missing directory")

    # 5. Exit gates (quick: just dependency rule)
    python = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    if os.path.exists(python):
        try:
            result = subprocess.run(
                [python, "-c",
                 "import sys; sys.path.insert(0,'.'); "
                 "from tools.exit_gates.gates.gate_dependency_rule import run_gate; "
                 "r = run_gate({'root':'.'}); "
                 "print('PASS' if r['ok'] else 'FAIL: ' + r.get('details','?')[:100])"],
                capture_output=True, text=True, timeout=10,
                cwd=str(PROJECT_ROOT),
            )
            gate_result = result.stdout.strip()
            if "PASS" in gate_result:
                lines.append(f"\n## ✅ Dependency Rule Gate: PASS")
            else:
                lines.append(f"\n## ❌ Dependency Rule Gate: {gate_result}")
        except Exception as e:
            lines.append(f"\n## ⚠️ Exit Gates: couldn't run ({e})")

    lines.append(f"\n*Check at {datetime.datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC*")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")
