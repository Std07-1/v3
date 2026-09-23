"""Exit-gate: d1_anchor_alignment — сітка H4/D1 за сезонним правилом якоря (ADR-0095 §3.7; ім'я гейта з ADR-0023 R7).

Підгейти:
1. htf_anchor_rule_valid — секція `htf_anchor` валідна, правило резолвиться для кожного символу `symbols` (і
   `binance.symbols`, коли Binance увімкнено). Символ невиміряної групи — FAIL, а не тихий default.
2. d1_in_derive_chain — (86400, 1440) є у DERIVE_CHAIN[60].
3. d1_in_derived_tfs_s — 86400 є у config `derived_tfs_s`.
4. season_anchor_samples — зразки літа і зими: `ny_close_us_dst` 75600 / 79200, `utc_midnight` 0; H4 і D1 на одній
   сітці (відкриття D1 — бакет H4).
5. disk_data_anchor — кожен рядок останнього part-файлу tf_86400 і tf_14400 кожного символу стоїть на сезонній
   сітці (`assert_on_season_grid`). Каталогу даних нема (CI) — пропуск.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config_loader import htf_anchor_rule_resolver
from core.derive import DERIVE_CHAIN
from core.session_anchor import (
    D1_S,
    H4_S,
    RULE_NY_CLOSE_US_DST,
    RULE_UTC_MIDNIGHT,
    OffSeasonGridError,
    assert_on_season_grid,
    htf_anchor_offset_s,
    htf_bucket_start_ms,
)

CheckResult = Tuple[bool, str, Dict[str, Any]]

_DISK_TFS_S = (D1_S, H4_S)
_SAMPLES_IN_DETAILS = 3
# 17:00 America/New_York (ADR-0095 §3.1) = 21:00 UTC улітку (EDT) і 22:00 UTC узимку (EST); Binance — опівніч UTC.
_SUMMER_SAMPLE_MS = 1_784_116_800_000  # 2026-07-15T12:00Z
_WINTER_SAMPLE_MS = 1_768_478_400_000  # 2026-01-15T12:00Z
_SEASON_SAMPLES = (
    (RULE_NY_CLOSE_US_DST, "summer", _SUMMER_SAMPLE_MS, 75_600),
    (RULE_NY_CLOSE_US_DST, "winter", _WINTER_SAMPLE_MS, 79_200),
    (RULE_UTC_MIDNIGHT, "summer", _SUMMER_SAMPLE_MS, 0),
    (RULE_UTC_MIDNIGHT, "winter", _WINTER_SAMPLE_MS, 0),
)


def _load_config(root: str) -> Dict[str, Any]:
    path = os.path.join(root, "config.json")
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _gate_symbols(cfg: Dict[str, Any]) -> List[str]:
    """Символи, чию сітку пишуть живі процеси: `symbols` і `binance.symbols`, коли Binance увімкнено."""
    symbols = [str(s) for s in cfg.get("symbols") or []]
    binance = cfg.get("binance")
    if isinstance(binance, dict) and binance.get("enabled"):
        symbols.extend(str(s) for s in binance.get("symbols") or [])
    return symbols


def _symbol_rules(cfg: Dict[str, Any]) -> Tuple[Dict[str, str], List[str]]:
    """(символ → правило, відмови резолвера). Невалідна секція `htf_anchor` — ValueError від резолвера."""
    rule_for_symbol = htf_anchor_rule_resolver(cfg)
    rules: Dict[str, str] = {}
    refusals: List[str] = []
    for symbol in _gate_symbols(cfg):
        try:
            rules[symbol] = rule_for_symbol(symbol)
        except ValueError as exc:
            refusals.append(str(exc))
    return rules, refusals


def _check_rule_valid(root: str, cfg: Dict[str, Any]) -> CheckResult:
    try:
        rules, refusals = _symbol_rules(cfg)
    except ValueError as exc:
        return False, str(exc), {}
    if refusals:
        return False, "; ".join(refusals), {"rules": rules}
    if not rules:
        return False, "config.symbols порожній — сітку нема чим перевірити", {}
    return True, "правило якоря є для %d символів" % len(rules), {"rules": rules}


def _check_derive_chain(root: str, cfg: Dict[str, Any]) -> CheckResult:
    ratios = [ratio for tf_s, ratio in DERIVE_CHAIN.get(60, []) if tf_s == D1_S]
    if not ratios:
        return False, "D1 (86400) не знайдено у DERIVE_CHAIN[60]", {}
    if ratios[0] != 1440:
        return False, "D1 ratio=%d (має бути 1440)" % ratios[0], {"ratio": ratios[0]}
    return True, "DERIVE_CHAIN[60] містить (86400, 1440)", {}


def _check_derived_tfs_s(root: str, cfg: Dict[str, Any]) -> CheckResult:
    derived = cfg.get("derived_tfs_s", [])
    if D1_S not in derived:
        return False, "86400 не в derived_tfs_s: %s" % derived, {}
    return True, "86400 присутній у derived_tfs_s", {}


def _check_season_samples(root: str, cfg: Dict[str, Any]) -> CheckResult:
    mismatches: List[str] = []
    for rule, season, ts_ms, expected_s in _SEASON_SAMPLES:
        d1_s = htf_anchor_offset_s(D1_S, ts_ms, rule)
        h4_s = htf_anchor_offset_s(H4_S, ts_ms, rule)
        d1_open_ms = htf_bucket_start_ms(ts_ms, D1_S, rule)
        d1_on_h4_grid = htf_bucket_start_ms(d1_open_ms, H4_S, rule) == d1_open_ms
        if (d1_s, h4_s, d1_on_h4_grid) != (expected_s, expected_s, True):
            mismatches.append(
                "%s/%s D1=%d H4=%d очікувано %d, відкриття D1 на сітці H4=%s"
                % (rule, season, d1_s, h4_s, expected_s, d1_on_h4_grid)
            )
    if mismatches:
        return False, "; ".join(mismatches), {}
    return True, "літо 75600 / зима 79200, utc_midnight 0; H4 і D1 на одній сітці", {}


def _latest_part_file(tf_dir: str) -> Optional[str]:
    if not os.path.isdir(tf_dir):
        return None
    parts = sorted(n for n in os.listdir(tf_dir) if n.startswith("part-") and n.endswith(".jsonl"))
    return os.path.join(tf_dir, parts[-1]) if parts else None


def _grid_violations(part_path: str, tf_s: int, rule: str) -> Tuple[int, List[str]]:
    """(рядків у файлі, порушення). Кожен рядок, а не лише останній: part-файли не відсортовані за open."""
    rows = 0
    violations: List[str] = []
    with open(part_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            rows += 1
            try:
                open_ms = json.loads(line).get("open_time_ms")
            except (ValueError, AttributeError):
                violations.append("рядок %d не JSON-об'єкт" % line_no)
                continue
            if isinstance(open_ms, bool) or not isinstance(open_ms, int):
                violations.append("рядок %d без цілого open_time_ms" % line_no)
                continue
            try:
                assert_on_season_grid(open_ms, tf_s, rule)
            except OffSeasonGridError as exc:
                violations.append(str(exc))
    return rows, violations


def _check_disk_anchor(root: str, cfg: Dict[str, Any]) -> CheckResult:
    try:
        rules, _refusals = _symbol_rules(cfg)
    except ValueError as exc:
        return False, "сітку не перевірити без правила: %s" % exc, {}
    data_root = os.path.join(root, str(cfg.get("data_root") or "data_v3"))
    files = rows = 0
    violations: List[str] = []
    for symbol, rule in sorted(rules.items()):
        for tf_s in _DISK_TFS_S:
            part = _latest_part_file(os.path.join(data_root, symbol.replace("/", "_"), "tf_%d" % tf_s))
            if part is None:
                continue
            try:
                part_rows, part_violations = _grid_violations(part, tf_s, rule)
            except (OSError, UnicodeDecodeError) as exc:
                return False, "помилка читання %s: %s" % (part, exc), {}
            files += 1
            rows += part_rows
            violations.extend("%s %s" % (os.path.relpath(part, data_root), v) for v in part_violations)
    metrics = {"files": files, "rows": rows, "violations": len(violations)}
    if violations:
        return False, "поза сезонною сіткою %d: %s" % (
            len(violations), "; ".join(violations[:_SAMPLES_IN_DETAILS])), metrics
    if not files:
        return True, "немає part-файлів tf_86400/tf_14400 (пропуск)", metrics
    return True, "%d рядків у %d файлах на сезонній сітці" % (rows, files), metrics


_CHECKS: Dict[str, Callable[[str, Dict[str, Any]], CheckResult]] = {
    "htf_anchor_rule_valid": _check_rule_valid,
    "d1_in_derive_chain": _check_derive_chain,
    "d1_in_derived_tfs_s": _check_derived_tfs_s,
    "season_anchor_samples": _check_season_samples,
    "disk_data_anchor": _check_disk_anchor,
}


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    root = str(inputs.get("root", "."))
    cfg = _load_config(root)
    ok_all = True
    parts: List[str] = []
    metrics: Dict[str, Any] = {}
    for name, check in _CHECKS.items():
        ok, msg, check_metrics = check(root, cfg)
        parts.append("%s:%s" % (name, "OK" if ok else "FAIL"))
        metrics[name] = dict(check_metrics, ok=ok, msg=msg)
        ok_all = ok_all and ok
    return {"ok": ok_all, "details": "; ".join(parts), "metrics": metrics}
