"""tools/repair/htf_rebuild_from_fxcm.py

Контрольований ребілд HTF барів (H4/D1) із FXCM raw history.
Замінює бари в SSOT (disk JSONL) свіжими даними з брокера,
оновлює Redis через UDS bootstrap_prime_from_disk.

Механіка:
  1. Fetch барів з FXCM через FxcmHistoryProvider
  2. Валідація batch (monotonic, no-dup, complete, src)
  3. Append до JSONL через JsonlAppender (append-only)
  4. DiskLayer reader dedup: last-appended bar wins (_choose_better_bar)
  5. Redis refresh через UDS.bootstrap_prime_from_disk

Режими:
  --dry-run (за замовч.) — лише fetch + валідація + звіт
  --commit            — запис на диск + оновлення Redis

Safety rails:
  - monotonic open_time_ms у кожному batch
  - no duplicate open_time_ms у batch
  - complete=True, src="history" guard (JsonlAppender)
  - assert_invariants (bucket alignment via core/model/bars.py)

Exit gate: після --commit запустити fxcm_raw_compare → mismatch_count == 0.

Використання:
  python -m tools.repair.htf_rebuild_from_fxcm --dry-run
  python -m tools.repair.htf_rebuild_from_fxcm --commit
  python -m tools.repair.htf_rebuild_from_fxcm --symbols XAU/USD --tfs 14400 --commit
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Set

from env_profile import load_env_secrets
from core.config_loader import pick_config_path, load_system_config, env_str
from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
from runtime.store.ssot_jsonl import JsonlAppender

LOG = logging.getLogger("htf_rebuild")


# ─── Safety Rails ────────────────────────────────────────────────────


def _validate_batch(bars: list, tf_s: int) -> List[str]:
    """Валідація batch барів. Повертає список помилок (порожній = ОК)."""
    errors: List[str] = []
    if not bars:
        return errors

    seen: Set[int] = set()
    prev_open = -1

    for i, b in enumerate(bars):
        ot = b.open_time_ms

        # Монотонність
        if ot <= prev_open and prev_open > 0:
            errors.append(
                "non-monotonic: bar[%d] open_time_ms=%d <= prev=%d" % (i, ot, prev_open)
            )
        prev_open = ot

        # Дублікати
        if ot in seen:
            errors.append("duplicate open_time_ms=%d at bar[%d]" % (ot, i))
        seen.add(ot)

        # complete + src guard
        if not b.complete:
            errors.append("bar[%d] complete=False open_time_ms=%d" % (i, ot))
        if b.src != "history":
            errors.append("bar[%d] src=%r (expected 'history') open_time_ms=%d" % (i, b.src, ot))

    return errors


def _bar_summary(bars: list) -> Dict[str, Any]:
    """Створити summary dict для batch."""
    if not bars:
        return {"count": 0}
    return {
        "count": len(bars),
        "first_open_ms": bars[0].open_time_ms,
        "last_open_ms": bars[-1].open_time_ms,
        "first_utc": dt.datetime.utcfromtimestamp(
            bars[0].open_time_ms / 1000
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_utc": dt.datetime.utcfromtimestamp(
            bars[-1].open_time_ms / 1000
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ─── Rewrite-Range: CLEAN REWRITE ───────────────────────────────────


def _jsonl_dir_for(data_root: str, symbol: str, tf_s: int) -> str:
    """Шлях до каталогу JSONL part-файлів для (symbol, tf_s)."""
    sym_dir = symbol.replace("/", "_")
    return os.path.join(data_root, sym_dir, "tf_%d" % tf_s)


def _read_all_bars_raw(tf_dir: str) -> List[Dict[str, Any]]:
    """Зчитує ВСІ рядки з усіх part-YYYYMMDD.jsonl у каталозі.

    Повертає список dict (raw JSON), відсортований по open_time_ms.
    Пропускає порожні/некоректні рядки.
    """
    bars: List[Dict[str, Any]] = []
    pattern = os.path.join(tf_dir, "part-*.jsonl")
    for path in sorted(glob.glob(pattern)):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    bar = json.loads(line)
                    bars.append(bar)
                except Exception:
                    continue
    bars.sort(key=lambda b: b.get("open_time_ms", 0))
    return bars


def _bars_to_jsonl_lines(bars: List[Dict[str, Any]]) -> List[str]:
    """Серіалізує бари у JSONL рядки (compact JSON, той самий формат що JsonlAppender)."""
    lines: List[str] = []
    for b in bars:
        lines.append(json.dumps(b, ensure_ascii=False, separators=(",", ":")))
    return lines


def _group_bars_by_day(bars: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Групує бари по даті open_time_ms → part-YYYYMMDD."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for b in bars:
        ot = b.get("open_time_ms", 0)
        day = dt.datetime.utcfromtimestamp(ot / 1000).strftime("%Y%m%d")
        key = "part-%s.jsonl" % day
        groups.setdefault(key, []).append(b)
    return groups


def rewrite_range(
    data_root: str,
    symbol: str,
    tf_s: int,
    fxcm_bars: list,
    from_open_ms: int,
    to_open_ms: int,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Замінює діапазон [from_open_ms .. to_open_ms] у SSOT JSONL на FXCM бари.

    Алгоритм:
      1. Зчитати всі існуючі бари з part-файлів.
      2. Видалити бари з open_time_ms у [from_open_ms .. to_open_ms].
      3. Додати FXCM бари (серіалізовані через to_dict()).
      4. Відсортувати по open_time_ms, dedup.
      5. Перегрупувати по днях.
      6. Записати у temp файл, потім atomic os.replace().

    Повертає summary dict.
    """
    tf_dir = _jsonl_dir_for(data_root, symbol, tf_s)
    result: Dict[str, Any] = {
        "symbol": symbol,
        "tf_s": tf_s,
        "from_open_ms": from_open_ms,
        "to_open_ms": to_open_ms,
        "from_utc": dt.datetime.utcfromtimestamp(from_open_ms / 1000).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to_utc": dt.datetime.utcfromtimestamp(to_open_ms / 1000).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # 1. Зчитати існуючі бари
    existing = _read_all_bars_raw(tf_dir)
    before_count = len(existing)

    # 2. Видалити бари у діапазоні
    kept: List[Dict[str, Any]] = []
    removed_count = 0
    for b in existing:
        ot = b.get("open_time_ms", 0)
        if from_open_ms <= ot <= to_open_ms:
            removed_count += 1
        else:
            kept.append(b)

    # 3. Додати FXCM бари
    fxcm_dicts: List[Dict[str, Any]] = []
    for b in fxcm_bars:
        fxcm_dicts.append(b.to_dict())

    merged = kept + fxcm_dicts

    # 4. Сортування + dedup
    merged.sort(key=lambda b: b.get("open_time_ms", 0))
    deduped: List[Dict[str, Any]] = []
    seen_opens: Set[int] = set()
    dup_count = 0
    for b in merged:
        ot = b.get("open_time_ms", 0)
        if ot in seen_opens:
            dup_count += 1
            continue
        seen_opens.add(ot)
        deduped.append(b)

    after_count = len(deduped)

    result.update({
        "before_count": before_count,
        "removed_in_range": removed_count,
        "fxcm_inserted": len(fxcm_dicts),
        "kept_outside": len(kept),
        "dup_removed": dup_count,
        "after_count": after_count,
    })

    # Validate monotonic
    prev_ot = -1
    mono_ok = True
    for b in deduped:
        ot = b.get("open_time_ms", 0)
        if ot <= prev_ot and prev_ot > 0:
            mono_ok = False
            break
        prev_ot = ot
    result["monotonic"] = mono_ok

    if not mono_ok:
        result["status"] = "validation_error"
        LOG.error(
            "%s tf_s=%d: rewrite — порушення монотонності після merge",
            symbol, tf_s,
        )
        return result

    if dry_run:
        result["status"] = "dry_run"
        LOG.info(
            "%s tf_s=%d: rewrite dry-run — before=%d removed=%d fxcm=%d after=%d",
            symbol, tf_s, before_count, removed_count, len(fxcm_dicts), after_count,
        )
        return result

    # 5-6. Перегрупування по днях і atomic запис
    groups = _group_bars_by_day(deduped)

    # Визначити всі існуючі part-файли (щоб видалити зайві)
    existing_parts = set()
    for p in glob.glob(os.path.join(tf_dir, "part-*.jsonl")):
        existing_parts.add(os.path.basename(p))

    written_parts = set()
    os.makedirs(tf_dir, exist_ok=True)

    for part_name, part_bars in groups.items():
        target = os.path.join(tf_dir, part_name)
        lines = _bars_to_jsonl_lines(part_bars)

        # Atomic write: temp file → os.replace
        fd, tmp_path = tempfile.mkstemp(
            dir=tf_dir, prefix=".rewrite_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
            os.replace(tmp_path, target)
        except Exception:
            # Cleanup temp на помилку
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        written_parts.add(part_name)

    # Видалити part-файли, які більше не мають барів (діапазон повністю видалено)
    orphan_parts = existing_parts - written_parts
    for orphan in orphan_parts:
        orphan_path = os.path.join(tf_dir, orphan)
        try:
            os.unlink(orphan_path)
            LOG.info("%s tf_s=%d: видалено порожній part: %s", symbol, tf_s, orphan)
        except OSError:
            pass

    result["status"] = "committed"
    result["written_parts"] = len(written_parts)
    result["orphan_parts_removed"] = len(orphan_parts)
    LOG.info(
        "%s tf_s=%d: rewrite committed — before=%d removed=%d fxcm=%d after=%d parts=%d",
        symbol, tf_s, before_count, removed_count, len(fxcm_dicts), after_count,
        len(written_parts),
    )
    return result


# ─── Redis Refresh ───────────────────────────────────────────────────


def _refresh_redis(
    config_path: str,
    data_root: str,
    pairs: List[tuple],
    tf_allowlist: Set[int],
) -> Dict[str, Any]:
    """Оновити Redis snapshot з диску для заданих (symbol, tf_s) пар.

    Використовує UDS.bootstrap_prime_from_disk — канонічний спосіб.
    Повертає dict з результатами.
    """
    results: Dict[str, Any] = {}

    try:
        from runtime.store.redis_snapshot import build_redis_snapshot_writer
        from runtime.store.layers.disk_layer import DiskLayer
        from runtime.store.uds import UnifiedDataStore
    except ImportError as exc:
        LOG.warning("Redis refresh: імпорт UDS компонентів: %s", exc)
        return {"error": str(exc), "primed": False}

    redis_writer = build_redis_snapshot_writer(config_path)
    if redis_writer is None:
        LOG.warning(
            "Redis refresh: redis_snapshot_writer=None "
            "(Redis недоступний або не налаштований)"
        )
        return {"error": "redis_writer_unavailable", "primed": False}

    disk_layer = DiskLayer(data_root)
    boot_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    # Мінімальний набір min_coldload_bars для bootstrap
    min_coldload = {tf_s: 300 for tf_s in tf_allowlist}

    uds = UnifiedDataStore(
        data_root=data_root,
        boot_id=boot_id,
        tf_allowlist=tf_allowlist,
        min_coldload_bars=min_coldload,
        role="reader",
        disk_layer=disk_layer,
        redis_snapshot_writer=redis_writer,
    )

    for symbol, tf_s in pairs:
        tail_n = min_coldload.get(tf_s, 300)
        try:
            count = uds.bootstrap_prime_from_disk(
                symbol, tf_s, tail_n, log_detail=True
            )
            results["%s:%d" % (symbol, tf_s)] = {"primed": True, "count": count}
            LOG.info("Redis prime OK: %s tf_s=%d count=%d", symbol, tf_s, count)
        except Exception as exc:
            results["%s:%d" % (symbol, tf_s)] = {
                "primed": False,
                "error": str(exc),
            }
            LOG.warning("Redis prime FAIL: %s tf_s=%d err=%s", symbol, tf_s, exc)

    return results


# ─── MD Report ───────────────────────────────────────────────────────


def _write_md_report(report: Dict[str, Any], md_path: str) -> None:
    """Створити MD-звіт."""
    lines: List[str] = [
        "# HTF Rebuild from FXCM — %s" % report["mode"],
        "",
        "**Час**: %s" % report["ts_utc"],
        "**Символи**: %s" % ", ".join(report["symbols"]),
        "**TFs**: %s" % report["tfs"],
        "",
        "## Результати",
        "",
    ]

    for symbol, tfs_data in report.get("results", {}).items():
        lines.append("### %s" % symbol)
        lines.append("")
        for tf_key, result in tfs_data.items():
            status = result.get("status", "?")
            fetched = result.get("fetched", 0)
            committed = result.get("committed", 0)
            summary = result.get("summary", {})
            first_utc = summary.get("first_utc", "—")
            last_utc = summary.get("last_utc", "—")
            val_errors = result.get("validation_errors", [])

            lines.append(
                "- **TF=%s**: status=%s, fetched=%d, committed=%d"
                % (tf_key, status, fetched, committed)
            )
            lines.append("  - range: %s → %s" % (first_utc, last_utc))
            if val_errors:
                lines.append("  - validation errors (%d):" % len(val_errors))
                for ve in val_errors[:5]:
                    lines.append("    - %s" % ve)
        lines.append("")

    # Totals
    t = report.get("totals", {})
    lines.append("## Підсумок")
    lines.append("")
    lines.append("- **Fetched**: %d" % t.get("fetched", 0))
    lines.append("- **Committed**: %d" % t.get("committed", 0))
    lines.append("- **Validation errors**: %d" % t.get("validation_errors", 0))
    lines.append("- **Fetch errors**: %d" % t.get("fetch_errors", 0))
    lines.append("")

    # Redis
    redis = report.get("redis_refresh", {})
    if redis:
        lines.append("## Redis Refresh")
        lines.append("")
        for key, val in redis.items():
            if isinstance(val, dict):
                lines.append(
                    "- **%s**: primed=%s, count=%s"
                    % (key, val.get("primed"), val.get("count", "—"))
                )
            else:
                lines.append("- %s: %s" % (key, val))
        lines.append("")

    # Exit gate
    lines.append("## Exit Gate")
    lines.append("")
    lines.append("Після commit запустити перевірку:")
    lines.append("```")
    lines.append(
        "python -m tools.audit.fxcm_raw_compare "
        "--symbol XAU/USD --symbol NAS100 "
        "--tfs 14400,86400 --limit-h4 200 --limit-d1 100"
    )
    lines.append("```")
    lines.append("Очікуваний результат: `mismatch_count == 0`")
    lines.append("")

    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ─── Main ────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    ap = argparse.ArgumentParser(
        description="Контрольований ребілд HTF барів із FXCM raw history.",
    )
    ap.add_argument(
        "--symbols",
        default=None,
        help="Символи через кому (default: symbols[] з config)",
    )
    ap.add_argument(
        "--tfs",
        default="14400,86400",
        help="TF у секундах через кому (default: 14400,86400)",
    )
    ap.add_argument(
        "--limit-h4",
        type=int,
        default=200,
        help="Кількість H4 барів (default: 200)",
    )
    ap.add_argument(
        "--limit-d1",
        type=int,
        default=100,
        help="Кількість D1 барів (default: 100)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Лише fetch + валідація (default)",
    )
    ap.add_argument(
        "--commit",
        action="store_true",
        help="Записати на диск + оновити Redis",
    )
    ap.add_argument(
        "--rewrite-range",
        action="store_true",
        help="CLEAN REWRITE: замінити діапазон барів (замість append-only)",
    )
    ap.add_argument(
        "--rewrite-from-open-ms",
        type=int,
        default=None,
        help="Початок діапазону rewrite (default: min з FXCM batch)",
    )
    ap.add_argument(
        "--rewrite-to-open-ms",
        type=int,
        default=None,
        help="Кінець діапазону rewrite (default: max з FXCM batch)",
    )
    ap.add_argument(
        "--out",
        default="reports/mpv_proof/htf_rebuild_run.json",
        help="Шлях JSON-звіту",
    )
    args = ap.parse_args()

    is_commit = args.commit
    is_rewrite = args.rewrite_range
    write_mode = "REWRITE" if is_rewrite else "APPEND"
    mode = "COMMIT-%s" % write_mode if is_commit else "DRY-RUN-%s" % write_mode
    LOG.info("=== HTF Rebuild from FXCM === mode=%s", mode)

    # ── Config ─────────────────────────────────────────
    load_env_secrets()
    config_path = pick_config_path()
    cfg = load_system_config(config_path)
    data_root = str(cfg.get("data_root", "./data_v3"))

    # Символи
    if args.symbols:
        sym_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        sym_list = [str(s) for s in cfg.get("symbols", []) if str(s).strip()]
    if not sym_list:
        LOG.error("Порожній список символів")
        return 2

    # TFs
    tfs = [int(t.strip()) for t in args.tfs.split(",") if t.strip()]
    if not tfs:
        LOG.error("Порожній список TF")
        return 2

    # Limits per TF
    limit_map = {14400: args.limit_h4, 86400: args.limit_d1}
    default_limit = args.limit_h4

    # FXCM credentials
    user_id = env_str("FXCM_USERNAME") or str(cfg.get("user_id") or "").strip()
    password = env_str("FXCM_PASSWORD") or str(cfg.get("password") or "").strip()
    url = env_str("FXCM_HOST_URL") or str(
        cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp")
    )
    connection = env_str("FXCM_CONNECTION") or str(cfg.get("connection", "Demo"))
    if not user_id or not password:
        LOG.error("Відсутні FXCM креденшіали (ENV або config)")
        return 2

    # Anchor offsets
    day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
    day_anchor_offset_s_alt = cfg.get("day_anchor_offset_s_alt", None)
    day_anchor_offset_s_alt2 = cfg.get("day_anchor_offset_s_alt2", None)
    day_anchor_offset_s_d1 = cfg.get("day_anchor_offset_s_d1", None)
    day_anchor_offset_s_d1_alt = cfg.get("day_anchor_offset_s_d1_alt", None)

    LOG.info(
        "Параметри: symbols=%s tfs=%s limits=%s data_root=%s",
        sym_list,
        tfs,
        limit_map,
        data_root,
    )

    # ── Provider + Writer ──────────────────────────────
    provider = FxcmHistoryProvider(
        user_id=user_id,
        password=password,
        url=url,
        connection=connection,
        day_anchor_offset_s=day_anchor_offset_s,
        day_anchor_offset_s_d1=(
            None if day_anchor_offset_s_d1 is None else int(day_anchor_offset_s_d1)
        ),
        day_anchor_offset_s_d1_alt=(
            None
            if day_anchor_offset_s_d1_alt is None
            else int(day_anchor_offset_s_d1_alt)
        ),
        day_anchor_offset_s_alt=(
            None if day_anchor_offset_s_alt is None else int(day_anchor_offset_s_alt)
        ),
        day_anchor_offset_s_alt2=(
            None if day_anchor_offset_s_alt2 is None else int(day_anchor_offset_s_alt2)
        ),
    )

    writer = None
    if is_commit and not is_rewrite:
        writer = JsonlAppender(
            root=data_root,
            day_anchor_offset_s=day_anchor_offset_s,
            day_anchor_offset_s_d1=(
                None
                if day_anchor_offset_s_d1 is None
                else int(day_anchor_offset_s_d1)
            ),
            day_anchor_offset_s_d1_alt=(
                None
                if day_anchor_offset_s_d1_alt is None
                else int(day_anchor_offset_s_d1_alt)
            ),
            day_anchor_offset_s_alt=(
                None
                if day_anchor_offset_s_alt is None
                else int(day_anchor_offset_s_alt)
            ),
            day_anchor_offset_s_alt2=(
                None
                if day_anchor_offset_s_alt2 is None
                else int(day_anchor_offset_s_alt2)
            ),
        )

    # ── Fetch + Validate + Write ───────────────────────
    report: Dict[str, Any] = {
        "tool": "htf_rebuild_from_fxcm",
        "mode": mode,
        "ts_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbols": sym_list,
        "tfs": tfs,
        "limits": limit_map,
        "results": {},
        "redis_refresh": {},
        "totals": {
            "fetched": 0,
            "committed": 0,
            "validation_errors": 0,
            "fetch_errors": 0,
        },
    }

    committed_pairs: List[tuple] = []  # (symbol, tf_s) для Redis prime

    try:
        with provider:
            for symbol in sym_list:
                report["results"][symbol] = {}

                for tf_s in tfs:
                    limit = limit_map.get(tf_s, default_limit)
                    key = "%s:tf_%d" % (symbol, tf_s)
                    LOG.info("── %s TF=%d limit=%d ──", symbol, tf_s, limit)

                    # Fetch
                    try:
                        bars = provider.fetch_last_n_tf(
                            symbol, tf_s=tf_s, n=limit
                        )
                    except Exception as exc:
                        LOG.error("%s: fetch error: %s", key, exc)
                        report["results"][symbol][str(tf_s)] = {
                            "status": "fetch_error",
                            "error": str(exc),
                        }
                        report["totals"]["fetch_errors"] += 1
                        continue

                    if not bars:
                        LOG.warning("%s: брокер не повернув бари", key)
                        report["results"][symbol][str(tf_s)] = {
                            "status": "empty",
                            "fetched": 0,
                        }
                        continue

                    LOG.info("%s: fetched=%d", key, len(bars))
                    report["totals"]["fetched"] += len(bars)

                    # Validate
                    val_errors = _validate_batch(bars, tf_s)
                    summary = _bar_summary(bars)

                    result_entry: Dict[str, Any] = {
                        "status": "ok" if not val_errors else "validation_errors",
                        "fetched": len(bars),
                        "summary": summary,
                        "validation_errors": val_errors,
                    }

                    if val_errors:
                        LOG.warning(
                            "%s: %d validation errors:", key, len(val_errors)
                        )
                        for ve in val_errors[:5]:
                            LOG.warning("  - %s", ve)
                        report["totals"]["validation_errors"] += len(val_errors)
                        report["results"][symbol][str(tf_s)] = result_entry
                        continue  # Не записуємо при помилках валідації

                    # Write (commit mode only)
                    if is_rewrite:
                        # ── REWRITE-RANGE mode ─────────────
                        from_ms = args.rewrite_from_open_ms
                        to_ms = args.rewrite_to_open_ms
                        if from_ms is None:
                            from_ms = bars[0].open_time_ms
                        if to_ms is None:
                            to_ms = bars[-1].open_time_ms

                        rw_result = rewrite_range(
                            data_root=data_root,
                            symbol=symbol,
                            tf_s=tf_s,
                            fxcm_bars=bars,
                            from_open_ms=from_ms,
                            to_open_ms=to_ms,
                            dry_run=not is_commit,
                        )
                        result_entry["rewrite"] = rw_result
                        if rw_result.get("status") == "committed":
                            result_entry["committed"] = rw_result.get("fxcm_inserted", 0)
                            report["totals"]["committed"] += result_entry["committed"]
                            committed_pairs.append((symbol, tf_s))
                        elif rw_result.get("status") == "dry_run":
                            result_entry["committed"] = 0
                        else:
                            # validation_error
                            result_entry["committed"] = 0
                            report["totals"]["validation_errors"] += 1
                    elif is_commit and writer is not None:
                        # ── APPEND mode (legacy) ──────────
                        written = 0
                        write_errors: List[str] = []
                        for b in bars:
                            try:
                                writer.append(b)
                                written += 1
                            except Exception as exc:
                                msg = "open_time_ms=%d: %s" % (
                                    b.open_time_ms,
                                    exc,
                                )
                                LOG.error("%s: write error %s", key, msg)
                                write_errors.append(msg)
                        result_entry["committed"] = written
                        if write_errors:
                            result_entry["write_errors"] = write_errors
                        report["totals"]["committed"] += written
                        committed_pairs.append((symbol, tf_s))
                        LOG.info("%s: committed=%d", key, written)
                    else:
                        result_entry["committed"] = 0
                        LOG.info(
                            "%s: dry-run — %d барів валідні, запис пропущено",
                            key,
                            len(bars),
                        )

                    report["results"][symbol][str(tf_s)] = result_entry

    finally:
        if writer is not None:
            writer.close()

    # ── Redis Refresh (commit mode only) ───────────────
    if is_commit and committed_pairs:
        LOG.info("=== Redis refresh для %d пар ===", len(committed_pairs))
        tf_allowlist = set(tfs)
        redis_result = _refresh_redis(
            config_path, data_root, committed_pairs, tf_allowlist
        )
        report["redis_refresh"] = redis_result

    # ── Output Report ──────────────────────────────────
    out_path = args.out
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    LOG.info("Звіт записано: %s", out_path)

    # MD report
    md_path = out_path.replace(".json", ".md")
    _write_md_report(report, md_path)
    LOG.info("MD звіт: %s", md_path)

    # Summary
    t = report["totals"]
    LOG.info(
        "=== ПІДСУМОК: mode=%s fetched=%d committed=%d "
        "val_errors=%d fetch_errors=%d ===",
        mode,
        t["fetched"],
        t["committed"],
        t["validation_errors"],
        t["fetch_errors"],
    )

    if t["validation_errors"] > 0 or t["fetch_errors"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
