"""P2X.6-G2.2: Exit-gate «ui_live_candle_plane».

Статичний аналіз UI/Overlay/Polling контуру.
Не потребує запущених сервісів.

Підгейти:
  1. overlay_read_only — /api/overlay не має write-викликів
  2. overlay_two_bar_contract — bars[0–2] + hold-prev-until-final
  3. overlay_anchor_sentinel_present — resolve_anchor_offset_ms + sentinel warning
  4. ui_overlay_isolated_from_applyUpdates — overlay окремий від applyUpdates
  5. ui_polling_no_interval_storm — заборонено setInterval для polling
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Допоміжні
# ---------------------------------------------------------------------------

def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _find_file(rel_path: str, root: str) -> Optional[str]:
    full = os.path.join(root, rel_path)
    return full if os.path.isfile(full) else None


def _extract_handler_section(src: str, marker_start: str, marker_end: str) -> str:
    """Витягує секцію між двома маркерами у server.py."""
    start = src.find(marker_start)
    if start < 0:
        return ""
    end = src.find(marker_end, start + len(marker_start))
    if end < 0:
        return src[start:]
    return src[start:end]


# ---------------------------------------------------------------------------
# Sub-gate 1: overlay_read_only
# /api/overlay не має жодних write-викликів
# ---------------------------------------------------------------------------

_WRITE_CALLS = [
    "commit_final_bar",
    "publish_preview_bar",
    "write_snapshot",
    "write_preview_tail",
    "write_preview_curr",
    "publish_event",
    "publish_preview_event",
    ".set(",
    ".lpush(",
    ".rpush(",
]


def _check_overlay_read_only(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """/api/overlay має бути read-only (без write-викликів)."""
    server_path = _find_file("ui_chart_v3/server.py", root)
    if server_path is None:
        return False, "server.py_not_found", {}

    src = _read_text(server_path)
    if src is None:
        return False, "server.py_read_error", {}

    metrics: Dict[str, Any] = {}

    # Витягуємо секцію /api/overlay
    overlay_section = _extract_handler_section(
        src,
        'if path == "/api/overlay"',
        "self._bad(",
    )
    # Якщо не знайшли — пробуємо альтернативний маркер
    if not overlay_section:
        overlay_section = _extract_handler_section(
            src,
            "path == \"/api/overlay\"",
            "self._json(200",
        )
    if not overlay_section:
        # Пробуємо до кінця handler
        idx = src.find("/api/overlay")
        if idx >= 0:
            # Беремо до наступного 'if path ==' або 'self._bad("unknown'
            end_idx = src.find('self._bad("unknown_endpoint")', idx)
            if end_idx > idx:
                overlay_section = src[idx:end_idx]

    metrics["overlay_section_len"] = len(overlay_section)

    if not overlay_section:
        return False, "overlay_handler_not_found", metrics

    # Перевіряємо дозволені read-виклики
    allowed_reads = ["read_preview_window", "read_window"]
    found_reads = [r for r in allowed_reads if r in overlay_section]
    metrics["allowed_reads_found"] = found_reads

    # Перевіряємо заборонені write-виклики
    found_writes: List[str] = []
    for w in _WRITE_CALLS:
        if w in overlay_section:
            found_writes.append(w)
    metrics["forbidden_writes_found"] = found_writes

    issues: List[str] = []
    if found_writes:
        issues.append(f"write_calls_in_overlay:{','.join(found_writes)}")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 2: overlay_two_bar_contract
# Відповідь /api/overlay має bars: [] (0–2 бари)
# + hold-prev-until-final логіка
# ---------------------------------------------------------------------------

def _check_overlay_two_bar_contract(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """/api/overlay має 2-bar contract з hold-prev-until-final."""
    server_path = _find_file("ui_chart_v3/server.py", root)
    if server_path is None:
        return False, "server.py_not_found", {}

    src = _read_text(server_path)
    if src is None:
        return False, "server.py_read_error", {}

    metrics: Dict[str, Any] = {}

    # Перевіряємо наявність bars у відповіді
    has_bars_field = '"bars":' in src or "'bars':" in src
    metrics["has_bars_field"] = has_bars_field

    # Перевіряємо backward compat: "bar": curr_bar
    has_bar_compat = '"bar":' in src or "'bar':" in src
    metrics["has_bar_backward_compat"] = has_bar_compat

    # Перевіряємо prev_bar логіку
    has_prev_bar = "prev_bar" in src
    metrics["has_prev_bar_logic"] = has_prev_bar

    # Перевіряємо hold-until-final: перевірка final для prev bucket
    has_final_check = "has_final" in src or "res_final" in src
    metrics["has_final_check_for_prev"] = has_final_check

    # Перевіряємо що overlay_bars будується (0–2)
    has_overlay_bars_build = "overlay_bars" in src
    metrics["has_overlay_bars_build"] = has_overlay_bars_build

    issues: List[str] = []
    if not has_bars_field:
        issues.append("no_bars_field_in_response")
    if not has_bar_compat:
        issues.append("no_bar_backward_compat")
    if not has_prev_bar:
        issues.append("no_prev_bar_logic")
    if not has_final_check:
        issues.append("no_final_check_for_prev")
    if not has_overlay_bars_build:
        issues.append("no_overlay_bars_build")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 3: overlay_anchor_sentinel_present
# resolve_anchor_offset_ms + sentinel warning overlay_anchor_mismatch
# ---------------------------------------------------------------------------

def _check_overlay_anchor_sentinel(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Наявність anchor offset і sentinel warning для drift."""
    server_path = _find_file("ui_chart_v3/server.py", root)
    if server_path is None:
        return False, "server.py_not_found", {}

    src = _read_text(server_path)
    if src is None:
        return False, "server.py_read_error", {}

    metrics: Dict[str, Any] = {}

    has_resolve = "resolve_anchor_offset_ms" in src
    metrics["has_resolve_anchor_offset_ms"] = has_resolve

    has_sentinel = "overlay_anchor_mismatch" in src or "overlay_anchor_offset" in src
    metrics["has_overlay_anchor_mismatch_warning"] = has_sentinel

    issues: List[str] = []
    if not has_resolve:
        issues.append("no_resolve_anchor_offset_ms")
    if not has_sentinel:
        issues.append("no_overlay_anchor_mismatch_sentinel")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 4: ui_overlay_isolated_from_applyUpdates
# overlay має окремий series і не заходить у applyUpdates
# ---------------------------------------------------------------------------

def _check_ui_overlay_isolation(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Overlay ізольований від applyUpdates: окремий series, окремий poll."""
    adapter_path = _find_file("ui_chart_v3/static/chart_adapter_lite.js", root)
    app_path = _find_file("ui_chart_v3/static/app.js", root)

    metrics: Dict[str, Any] = {}
    issues: List[str] = []

    # --- chart_adapter_lite.js ---
    if adapter_path is None:
        issues.append("chart_adapter_lite.js_not_found")
    else:
        adapter_src = _read_text(adapter_path)
        if adapter_src is None:
            issues.append("chart_adapter_lite.js_read_error")
        else:
            # overlaySeries має бути окремий від candles/barsSeries
            has_overlay_series = "overlaySeries" in adapter_src
            metrics["has_overlay_series"] = has_overlay_series
            if not has_overlay_series:
                issues.append("no_overlay_series_in_adapter")

            # updateOverlayBar має використовувати overlaySeries.setData
            has_update_overlay = "updateOverlayBar" in adapter_src
            metrics["has_updateOverlayBar"] = has_update_overlay
            if not has_update_overlay:
                issues.append("no_updateOverlayBar_in_adapter")

            # overlaySeries.setData (не candles.update для overlay)
            has_overlay_setdata = "overlaySeries.setData" in adapter_src
            metrics["has_overlay_setData"] = has_overlay_setdata
            if not has_overlay_setdata:
                issues.append("overlay_not_using_setData")

    # --- app.js ---
    if app_path is None:
        issues.append("app.js_not_found")
    else:
        app_src = _read_text(app_path)
        if app_src is None:
            issues.append("app.js_read_error")
        else:
            # pollOverlay не має викликати applyUpdates
            poll_overlay_idx = app_src.find("function pollOverlay")
            if poll_overlay_idx < 0:
                poll_overlay_idx = app_src.find("async function pollOverlay")
            if poll_overlay_idx < 0:
                issues.append("pollOverlay_not_found")
            else:
                # Витягуємо тіло pollOverlay (до наступної function)
                next_func = app_src.find("\nfunction ", poll_overlay_idx + 10)
                next_async = app_src.find("\nasync function ", poll_overlay_idx + 10)
                ends = [e for e in [next_func, next_async] if e > 0]
                end = min(ends) if ends else len(app_src)
                poll_body = app_src[poll_overlay_idx:end]

                if "applyUpdates" in poll_body:
                    issues.append("pollOverlay_calls_applyUpdates")
                    metrics["pollOverlay_calls_applyUpdates"] = True
                else:
                    metrics["pollOverlay_calls_applyUpdates"] = False

                # Має викликати updateOverlayBar або clearOverlay
                has_overlay_call = "updateOverlayBar" in poll_body or "clearOverlay" in poll_body
                metrics["pollOverlay_uses_overlay_api"] = has_overlay_call
                if not has_overlay_call:
                    issues.append("pollOverlay_not_using_overlay_api")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 5: ui_polling_no_interval_storm
# Заборонено setInterval для polling (тільки setTimeout + single-flight)
# ---------------------------------------------------------------------------

_FORBIDDEN_INTERVALS = [
    "setInterval(pollUpdates",
    "setInterval(pollOverlay",
    "setInterval( pollUpdates",
    "setInterval( pollOverlay",
    "setInterval(function() { pollUpdates",
    "setInterval(function() { pollOverlay",
    "setInterval(() => pollUpdates",
    "setInterval(() => pollOverlay",
]


def _check_ui_polling_no_storm(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Polling має бути через setTimeout + single-flight, без setInterval."""
    app_path = _find_file("ui_chart_v3/static/app.js", root)
    if app_path is None:
        return False, "app.js_not_found", {}

    src = _read_text(app_path)
    if src is None:
        return False, "app.js_read_error", {}

    metrics: Dict[str, Any] = {}

    # Заборонені патерни
    found_forbidden: List[str] = []
    for pat in _FORBIDDEN_INTERVALS:
        if pat in src:
            found_forbidden.append(pat)
    metrics["forbidden_setInterval_found"] = found_forbidden

    # Вимагаємо setTimeout для polling
    has_settimeout_poll = "setTimeout(pollUpdates" in src or "setTimeout(poll" in src
    metrics["has_setTimeout_for_polling"] = has_settimeout_poll

    # Вимагаємо single-flight guard
    has_inflight_guard = "updatesInFlight" in src or "InFlight" in src
    metrics["has_inflight_guard"] = has_inflight_guard

    # Вимагаємо visibility check (document.hidden)
    has_visibility = "document.hidden" in src or "isUiVisible" in src
    metrics["has_visibility_check"] = has_visibility

    issues: List[str] = []
    if found_forbidden:
        issues.append(f"setInterval_storm:{','.join(found_forbidden)}")
    if not has_settimeout_poll:
        issues.append("no_setTimeout_for_polling")
    if not has_inflight_guard:
        issues.append("no_inflight_guard")
    if not has_visibility:
        issues.append("no_visibility_check")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Головна точка входу
# ---------------------------------------------------------------------------

_SUBGATES = [
    ("overlay_read_only", _check_overlay_read_only),
    ("overlay_two_bar_contract", _check_overlay_two_bar_contract),
    ("overlay_anchor_sentinel", _check_overlay_anchor_sentinel),
    ("ui_overlay_isolation", _check_ui_overlay_isolation),
    ("ui_polling_no_storm", _check_ui_polling_no_storm),
]


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Запуск всіх підгейтів ui_live_candle_plane.

    inputs:
        root (str): корінь репо (за замовчуванням ".").
    """
    root = str(inputs.get("root", "."))

    all_ok = True
    details_parts: List[str] = []
    metrics: Dict[str, Any] = {"subgates": {}}

    for name, fn in _SUBGATES:
        ok, detail, sub_metrics = fn(root)
        metrics["subgates"][name] = {
            "ok": ok,
            "detail": detail,
            **sub_metrics,
        }
        if not ok:
            all_ok = False
            details_parts.append(f"{name}:FAIL({detail})")
        else:
            details_parts.append(f"{name}:OK")

    metrics["total_subgates"] = len(_SUBGATES)
    metrics["passed"] = sum(
        1 for sg in metrics["subgates"].values() if sg.get("ok")
    )

    details = "; ".join(details_parts)
    if all_ok:
        details = "ok: " + details

    return {
        "ok": all_ok,
        "details": details,
        "metrics": metrics,
    }


def main() -> int:
    result = run_gate({"root": "."})
    if not result.get("ok"):
        print("EXIT_GATE_FAIL: ui_live_candle_plane")
        for line in result.get("details", "").split("; "):
            print(f"  {line}")
    else:
        print("EXIT_GATE_OK: ui_live_candle_plane")
        for line in result.get("details", "").split("; "):
            print(f"  {line}")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
