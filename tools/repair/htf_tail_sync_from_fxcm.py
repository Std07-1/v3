"""tools/repair/htf_tail_sync_from_fxcm.py

Підтягнути хвіст HTF (H4/D1) з FXCM raw і переписати tail-діапазон.
Використовує atomic rewrite-range з htf_rebuild_from_fxcm.

CLI:
  # Dry-run (лише fetch + валідація)
  python -m tools.repair.htf_tail_sync_from_fxcm --dry-run \\
      --symbols-from-config --tfs 14400,86400 --limit-h4 8 --limit-d1 5

  # Commit (запис + Redis prime)
  python -m tools.repair.htf_tail_sync_from_fxcm --commit \\
      --symbols-from-config --tfs 14400,86400 --limit-h4 8 --limit-d1 5

  # Verify (before/after snapshot)
  python -m tools.repair.htf_tail_sync_from_fxcm --commit --verify \\
      --symbols-from-config --tfs 14400,86400 --limit-h4 8 --limit-d1 5

Безпека:
  - SSOT JSONL змінюється ТІЛЬКИ через atomic rewrite-range (tempfile + os.replace).
  - Жодних silent fallback: будь-який пропуск → warnings/degraded у звіті.
  - Не чіпає /api/updates або align=tv деривацію.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set

LOG = logging.getLogger("htf_tail_sync")


# ═══════ Validate / merge helpers (pure, no I/O) ═══════


def validate_batch(bars: list, tf_s: int) -> List[str]:
    """Валідація batch CandleBar-ів. Повертає список помилок (порожній = ОК).

    Перевіряє: monotonic open_time_ms, no dup, complete=True, src='history'.
    """
    errors: List[str] = []
    if not bars:
        return errors
    seen: Set[int] = set()
    prev_open = -1
    for i, b in enumerate(bars):
        ot = b.open_time_ms
        if ot <= prev_open and prev_open > 0:
            errors.append(
                "non-monotonic: bar[%d] open_time_ms=%d <= prev=%d" % (i, ot, prev_open)
            )
        prev_open = ot
        if ot in seen:
            errors.append("duplicate open_time_ms=%d at bar[%d]" % (ot, i))
        seen.add(ot)
        if not b.complete:
            errors.append("bar[%d] complete=False open_time_ms=%d" % (i, ot))
        if b.src != "history":
            errors.append(
                "bar[%d] src=%r (expected 'history') open_time_ms=%d" % (i, b.src, ot)
            )
    return errors


def merge_dedup_last_wins(
    existing: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
    from_open_ms: int,
    to_open_ms: int,
) -> List[Dict[str, Any]]:
    """Замінити бари в [from_open_ms..to_open_ms] на incoming. Last wins dedup.

    Повертає merged+sorted+deduped list.
    """
    kept = [
        b for b in existing
        if not (from_open_ms <= b.get("open_time_ms", 0) <= to_open_ms)
    ]
    merged = kept + list(incoming)
    merged.sort(key=lambda b: b.get("open_time_ms", 0))

    deduped: List[Dict[str, Any]] = []
    seen: Dict[int, int] = {}
    for b in merged:
        ot = b.get("open_time_ms", 0)
        if ot in seen:
            # last wins: замінюємо попередній бар
            deduped[seen[ot]] = b
        else:
            seen[ot] = len(deduped)
            deduped.append(b)
    return deduped


def validate_monotonic(bars: List[Dict[str, Any]]) -> bool:
    """Перевірка: open_time_ms строго зростає."""
    prev = -1
    for b in bars:
        ot = b.get("open_time_ms", 0)
        if ot <= prev and prev > 0:
            return False
        prev = ot
    return True


def bar_summary(bars: list) -> Dict[str, Any]:
    """Summary dict для batch CandleBar-ів."""
    if not bars:
        return {"count": 0}
    first = bars[0]
    last = bars[-1]
    first_ms = first.open_time_ms if hasattr(first, "open_time_ms") else first.get("open_time_ms", 0)
    last_ms = last.open_time_ms if hasattr(last, "open_time_ms") else last.get("open_time_ms", 0)
    return {
        "count": len(bars),
        "first_open_ms": first_ms,
        "last_open_ms": last_ms,
        "first_utc": dt.datetime.utcfromtimestamp(first_ms / 1000).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "last_utc": dt.datetime.utcfromtimestamp(last_ms / 1000).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


# ═══════ Sync one pair ═══════


def sync_one(
    provider,
    symbol: str,
    tf_s: int,
    limit: int,
    data_root: str,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Sync tail для одного (symbol, tf_s).

    Повертає result dict: status, fetched, summary, rewrite, errors.
    """
    from tools.repair.htf_rebuild_from_fxcm import rewrite_range

    result: Dict[str, Any] = {
        "symbol": symbol,
        "tf_s": tf_s,
        "limit": limit,
    }

    # 1) Fetch
    try:
        bars = provider.fetch_last_n_tf(symbol, tf_s=tf_s, n=limit)
    except Exception as exc:
        LOG.error("%s tf=%d: fetch error: %s", symbol, tf_s, exc)
        result["status"] = "fetch_error"
        result["error"] = str(exc)
        return result

    if not bars:
        LOG.warning("%s tf=%d: порожня відповідь від брокера", symbol, tf_s)
        result["status"] = "empty"
        result["fetched"] = 0
        return result

    result["fetched"] = len(bars)
    result["summary"] = bar_summary(bars)

    # 2) Validate
    val_errors = validate_batch(bars, tf_s)
    if val_errors:
        LOG.warning("%s tf=%d: %d validation errors", symbol, tf_s, len(val_errors))
        for ve in val_errors[:5]:
            LOG.warning("  - %s", ve)
        result["status"] = "validation_error"
        result["validation_errors"] = val_errors
        return result

    # 3) Rewrite range
    from_ms = bars[0].open_time_ms
    to_ms = bars[-1].open_time_ms
    result["rewrite_from_ms"] = from_ms
    result["rewrite_to_ms"] = to_ms
    result["rewrite_from_utc"] = dt.datetime.utcfromtimestamp(from_ms / 1000).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    result["rewrite_to_utc"] = dt.datetime.utcfromtimestamp(to_ms / 1000).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    try:
        rw = rewrite_range(
            data_root=data_root,
            symbol=symbol,
            tf_s=tf_s,
            fxcm_bars=bars,
            from_open_ms=from_ms,
            to_open_ms=to_ms,
            dry_run=dry_run,
        )
    except Exception as exc:
        LOG.error("%s tf=%d: rewrite_range error: %s", symbol, tf_s, exc)
        result["status"] = "rewrite_error"
        result["error"] = str(exc)
        return result

    result["rewrite"] = rw
    rw_status = rw.get("status", "unknown")

    if rw_status == "committed":
        result["status"] = "committed"
        result["wrote_count"] = rw.get("fxcm_inserted", 0)
        result["changed"] = rw.get("fxcm_inserted", 0) > 0 or rw.get("removed_in_range", 0) > 0
    elif rw_status == "dry_run":
        result["status"] = "dry_run"
        result["wrote_count"] = 0
        result["changed"] = False
    else:
        result["status"] = "rewrite_error"
        result["error"] = rw_status

    return result


# ═══════ Redis Refresh (делегуємо до htf_rebuild) ═══════


def refresh_redis(
    config_path: str,
    data_root: str,
    pairs: List[tuple],
    tf_allowlist: Set[int],
) -> Dict[str, Any]:
    """Redis prime з диску. Обгортка навколо htf_rebuild._refresh_redis."""
    from tools.repair.htf_rebuild_from_fxcm import _refresh_redis

    return _refresh_redis(config_path, data_root, pairs, tf_allowlist)


# ═══════ Report writers ═══════


def _write_json_report(report: Dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def _write_md_report(report: Dict[str, Any], md_path: str) -> None:
    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    lines: List[str] = [
        "# HTF Tail Sync from FXCM — %s" % report.get("mode", "?"),
        "",
        "**Час**: %s" % report.get("ts_utc", "?"),
        "**Символи**: %s" % ", ".join(report.get("symbols", [])),
        "**TFs**: %s" % report.get("tfs", []),
        "",
    ]

    # Verify before/after
    verify = report.get("verify")
    if verify:
        lines.append("## Before / After")
        lines.append("")
        lines.append(
            "| Symbol | TF | before_last_close | after_last_close | "
            "before_age_s | after_age_s | improved |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for v in verify.get("entries", []):
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %s |"
                % (
                    v.get("symbol", ""),
                    v.get("tf_s", ""),
                    v.get("before_last_close_utc", "—"),
                    v.get("after_last_close_utc", "—"),
                    v.get("before_age_s", "—"),
                    v.get("after_age_s", "—"),
                    "**YES**" if v.get("improved") else "no",
                )
            )
        lines.append("")

    # Results per symbol/tf
    lines.append("## Результати")
    lines.append("")
    for entry in report.get("results", []):
        sym = entry.get("symbol", "?")
        tf_s = entry.get("tf_s", "?")
        status = entry.get("status", "?")
        lines.append(
            "- **%s tf=%s**: status=%s fetched=%s wrote=%s changed=%s"
            % (
                sym,
                tf_s,
                status,
                entry.get("fetched", "—"),
                entry.get("wrote_count", "—"),
                entry.get("changed", "—"),
            )
        )
        s = entry.get("summary", {})
        if s.get("first_utc"):
            lines.append("  - range: %s → %s" % (s.get("first_utc"), s.get("last_utc")))
        errs = entry.get("validation_errors")
        if errs:
            lines.append("  - validation errors (%d):" % len(errs))
            for e in errs[:3]:
                lines.append("    - %s" % e)
    lines.append("")

    # Redis
    redis = report.get("redis_refresh", {})
    if redis:
        lines.append("## Redis Refresh")
        lines.append("")
        for key, val in redis.items():
            if isinstance(val, dict):
                lines.append(
                    "- **%s**: primed=%s count=%s" % (key, val.get("primed"), val.get("count", "—"))
                )
            else:
                lines.append("- %s: %s" % (key, val))
        lines.append("")

    # Totals
    t = report.get("totals", {})
    lines.append("## Підсумок")
    lines.append("")
    lines.append("- Fetched: %d" % t.get("fetched", 0))
    lines.append("- Committed: %d" % t.get("committed", 0))
    lines.append("- Validation errors: %d" % t.get("validation_errors", 0))
    lines.append("- Fetch errors: %d" % t.get("fetch_errors", 0))
    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ═══════ Verify (before/after snapshot) ═══════


def _do_verify(
    api_base_url: str,
    sym_list: List[str],
    tfs: List[int],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Збирає snapshot через htf_tail_watch.probe_tail."""
    from tools.audit.htf_tail_watch import probe_tail

    now_ms = int(time.time() * 1000)
    entries: List[Dict[str, Any]] = []
    for symbol in sym_list:
        for tf_s in tfs:
            entries.append(probe_tail(api_base_url, symbol, tf_s, limit, now_ms))
    return entries


def _build_verify_diff(
    before: List[Dict[str, Any]],
    after: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Побудувати diff між before/after snapshots."""
    after_map = {
        (e["symbol"], e["tf_s"]): e for e in after
    }
    entries = []
    improved_count = 0
    for b in before:
        key = (b["symbol"], b["tf_s"])
        a = after_map.get(key, {})
        before_age = b.get("age_s")
        after_age = a.get("age_s")
        improved = False
        if isinstance(before_age, int) and isinstance(after_age, int):
            improved = after_age < before_age
        entries.append({
            "symbol": b["symbol"],
            "tf_s": b["tf_s"],
            "before_last_close_utc": b.get("last_close_utc", "—"),
            "after_last_close_utc": a.get("last_close_utc", "—"),
            "before_age_s": before_age,
            "after_age_s": after_age,
            "improved": improved,
        })
        if improved:
            improved_count += 1

    return {
        "entries": entries,
        "improved_count": improved_count,
        "total": len(entries),
    }


# ═══════ CLI ═══════


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    ap = argparse.ArgumentParser(
        description="HTF Tail Sync: підтягнути хвіст H4/D1 з FXCM → atomic rewrite → Redis prime.",
    )
    ap.add_argument("--symbols", default=None, help="Символи через кому")
    ap.add_argument(
        "--symbols-from-config", action="store_true",
        help="Символи з config.json",
    )
    ap.add_argument("--tfs", default="14400,86400", help="TF через кому")
    ap.add_argument("--limit-h4", type=int, default=8, help="Кількість H4 барів")
    ap.add_argument("--limit-d1", type=int, default=5, help="Кількість D1 барів")
    ap.add_argument("--dry-run", action="store_true", default=True, help="Лише fetch + validate")
    ap.add_argument("--commit", action="store_true", help="Записати + Redis prime")
    ap.add_argument("--verify", action="store_true", help="Before/after snapshot")
    ap.add_argument(
        "--api-base-url", default="http://127.0.0.1:8089",
        help="API для verify snapshot",
    )
    ap.add_argument(
        "--out", default="reports/mpv_proof/htf_tail_sync_run.json",
        help="Шлях JSON-звіту",
    )
    args = ap.parse_args()

    is_commit = args.commit

    from env_profile import load_env_secrets
    from core.config_loader import pick_config_path, load_system_config, env_str
    from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider

    load_env_secrets()
    config_path = pick_config_path()
    cfg = load_system_config(config_path)
    data_root = str(cfg.get("data_root", "./data_v3"))

    # Символи
    if args.symbols_from_config:
        sym_list = [str(s) for s in cfg.get("symbols", []) if str(s).strip()]
    elif args.symbols:
        sym_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        sym_list = [str(s) for s in cfg.get("symbols", []) if str(s).strip()]

    if not sym_list:
        LOG.error("Порожній список символів")
        return 2

    tfs = [int(t.strip()) for t in args.tfs.split(",") if t.strip()]
    if not tfs:
        LOG.error("Порожній список TF")
        return 2

    limit_map = {14400: args.limit_h4, 86400: args.limit_d1}
    mode = "COMMIT" if is_commit else "DRY-RUN"
    LOG.info("=== HTF Tail Sync === mode=%s symbols=%s tfs=%s", mode, sym_list, tfs)

    # FXCM credentials
    user_id = env_str("FXCM_USERNAME") or str(cfg.get("user_id") or "").strip()
    password = env_str("FXCM_PASSWORD") or str(cfg.get("password") or "").strip()
    url = env_str("FXCM_HOST_URL") or str(
        cfg.get("url", "http://www.fxcorporate.com/Hosts.jsp")
    )
    connection = env_str("FXCM_CONNECTION") or str(cfg.get("connection", "Demo"))
    if not user_id or not password:
        LOG.error("Відсутні FXCM креденшіали")
        return 2

    day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
    day_anchor_offset_s_alt = cfg.get("day_anchor_offset_s_alt")
    day_anchor_offset_s_alt2 = cfg.get("day_anchor_offset_s_alt2")
    day_anchor_offset_s_d1 = cfg.get("day_anchor_offset_s_d1")
    day_anchor_offset_s_d1_alt = cfg.get("day_anchor_offset_s_d1_alt")

    # ── Verify before ──────────────────────────────
    verify_before: Optional[List[Dict[str, Any]]] = None
    if args.verify:
        LOG.info("=== Verify: збираю snapshot BEFORE ===")
        verify_before = _do_verify(args.api_base_url, sym_list, tfs)

    # ── Provider ───────────────────────────────────
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
            None if day_anchor_offset_s_d1_alt is None else int(day_anchor_offset_s_d1_alt)
        ),
        day_anchor_offset_s_alt=(
            None if day_anchor_offset_s_alt is None else int(day_anchor_offset_s_alt)
        ),
        day_anchor_offset_s_alt2=(
            None if day_anchor_offset_s_alt2 is None else int(day_anchor_offset_s_alt2)
        ),
    )

    # ── Fetch + Validate + Write ───────────────────
    report: Dict[str, Any] = {
        "tool": "htf_tail_sync_from_fxcm",
        "mode": mode,
        "ts_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbols": sym_list,
        "tfs": tfs,
        "limits": limit_map,
        "results": [],
        "redis_refresh": {},
        "totals": {
            "fetched": 0,
            "committed": 0,
            "validation_errors": 0,
            "fetch_errors": 0,
            "rewrite_errors": 0,
            "unexpected_errors": 0,
        },
    }

    committed_pairs: List[tuple] = []

    try:
        with provider:
            for symbol in sym_list:
                for tf_s in tfs:
                    limit = limit_map.get(tf_s, args.limit_h4)
                    LOG.info("── %s tf=%d limit=%d ──", symbol, tf_s, limit)

                    try:
                        entry = sync_one(
                            provider, symbol, tf_s, limit, data_root,
                            dry_run=not is_commit,
                        )
                    except Exception as exc:
                        LOG.error("%s tf=%d: unexpected error: %s", symbol, tf_s, exc)
                        entry = {
                            "symbol": symbol,
                            "tf_s": tf_s,
                            "limit": limit,
                            "status": "error",
                            "error": str(exc),
                            "fetched": 0,
                        }

                    report["results"].append(entry)
                    report["totals"]["fetched"] += entry.get("fetched", 0)

                    status = entry.get("status", "")
                    if status == "committed":
                        report["totals"]["committed"] += entry.get("wrote_count", 0)
                        committed_pairs.append((symbol, tf_s))
                    elif status == "fetch_error":
                        report["totals"]["fetch_errors"] += 1
                    elif status == "rewrite_error":
                        report["totals"]["rewrite_errors"] += 1
                    elif status == "validation_error":
                        report["totals"]["validation_errors"] += len(
                            entry.get("validation_errors", [])
                        )
                    elif status in ("error",):
                        report["totals"]["unexpected_errors"] += 1
    except Exception as exc:
        LOG.error("Provider connect/disconnect error: %s", exc)
        report["totals"]["unexpected_errors"] += 1

    # ── Redis Refresh ──────────────────────────────
    if is_commit and committed_pairs:
        LOG.info("=== Redis refresh для %d пар ===", len(committed_pairs))
        tf_allow = set(tfs)
        redis_result = refresh_redis(config_path, data_root, committed_pairs, tf_allow)
        report["redis_refresh"] = redis_result

    # ── Verify after ───────────────────────────────
    if args.verify and verify_before is not None:
        LOG.info("=== Verify: збираю snapshot AFTER ===")
        verify_after = _do_verify(args.api_base_url, sym_list, tfs)
        report["verify"] = _build_verify_diff(verify_before, verify_after)
        improved = report["verify"].get("improved_count", 0)
        total_v = report["verify"].get("total", 0)
        LOG.info("Verify: improved %d / %d", improved, total_v)

    # ── Output ─────────────────────────────────────
    json_path = args.out
    md_path = json_path.replace(".json", ".md")
    _write_json_report(report, json_path)
    _write_md_report(report, md_path)
    LOG.info("Report: %s + %s", json_path, md_path)

    t = report["totals"]
    LOG.info(
        "=== ПІДСУМОК: mode=%s fetched=%d committed=%d "
        "val_errors=%d fetch_errors=%d ===",
        mode, t["fetched"], t["committed"],
        t["validation_errors"], t["fetch_errors"],
    )

    if t["validation_errors"] > 0 or t["fetch_errors"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
