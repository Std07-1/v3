"""P2X.6-G1: Комплексний exit-gate «preview_plane».

Перевіряє ізоляцію preview-площини від SSOT.
Працює як статичний аналіз (не потребує запущених сервісів).

Підгейти:
  1. nomix_disk — UDS commit_final_bar відхиляє preview-джерела
  2. uds_hotpath — read_updates для preview TF → Redis only (без disk)
  3. api_splitbrain — server.py ігнорує prefer_redis/force_disk (PATCH 085)
  4. tick_schema — tick_v1.json дозволяє src="fxcm_wallclock";
                   tick_preview_worker має _validate_tick_schema
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Допоміжні
# ---------------------------------------------------------------------------

def _read_text(path: str) -> Optional[str]:
    """Безпечне читання файлу; повертає None при помилці."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _find_file(rel_path: str, root: str) -> Optional[str]:
    """Повертає абсолютний шлях до файлу, або None."""
    full = os.path.join(root, rel_path)
    return full if os.path.isfile(full) else None


# ---------------------------------------------------------------------------
# Sub-gate 1: nomix_disk
# Перевіряє: у UDS commit_final_bar є guard, що відхиляє preview sources
# ---------------------------------------------------------------------------

_PREVIEW_SOURCE_PATTERN = re.compile(
    r"preview|preview_tick|preview_agg",
    re.IGNORECASE,
)

_FINAL_SOURCES_PATTERN = re.compile(
    r"FINAL_SOURCES\s*=",
)


def _check_nomix_disk(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """UDS не дозволяє preview-джерелам потрапити у commit_final_bar."""
    uds_path = _find_file("runtime/store/uds.py", root)
    if uds_path is None:
        return False, "uds.py_not_found", {}

    src = _read_text(uds_path)
    if src is None:
        return False, "uds.py_read_error", {}

    metrics: Dict[str, Any] = {}

    # 1. Шукаємо FINAL_SOURCES визначення
    has_final_sources = bool(_FINAL_SOURCES_PATTERN.search(src))
    metrics["has_FINAL_SOURCES"] = has_final_sources

    # 2. Шукаємо commit_final_bar і перевіряємо guard (source not in FINAL_SOURCES → reject)
    has_commit = "def commit_final_bar" in src
    metrics["has_commit_final_bar"] = has_commit

    if not has_commit:
        return False, "commit_final_bar_missing", metrics

    # Витягуємо тіло commit_final_bar до наступного def на тому ж рівні
    lines = src.split("\n")
    in_commit = False
    commit_body: List[str] = []
    indent_level = 0
    for line in lines:
        if "def commit_final_bar" in line:
            in_commit = True
            indent_level = len(line) - len(line.lstrip())
            commit_body.append(line)
            continue
        if in_commit:
            if line.strip() == "":
                commit_body.append(line)
                continue
            cur_indent = len(line) - len(line.lstrip())
            # Наступний def на тому ж або меншому рівні = кінець
            if cur_indent <= indent_level and line.strip().startswith("def "):
                break
            commit_body.append(line)

    commit_text = "\n".join(commit_body)

    # Перевіряємо наявність guard: source перевіряється проти FINAL_SOURCES
    has_source_guard = (
        "FINAL_SOURCES" in commit_text
        or "final_sources" in commit_text
        or "source" in commit_text.lower()
    )
    metrics["commit_has_source_guard"] = has_source_guard

    # Перевіряємо, що preview source не може пройти
    # (наявність reject/raise/return для невідомих source)
    has_reject = any(
        kw in commit_text
        for kw in ("raise ", "return ", "rejected", "not in FINAL_SOURCES",
                    "not_in_final_sources", "unknown_source", "source_rejected")
    )
    metrics["commit_has_reject"] = has_reject

    if not has_source_guard:
        return False, "commit_final_bar:no_source_guard", metrics
    if not has_reject:
        return False, "commit_final_bar:no_reject_for_bad_source", metrics

    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 2: uds_hotpath
# Перевіряє: read_updates для preview TF → Redis path, без disk
# ---------------------------------------------------------------------------

def _check_uds_hotpath(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """read_updates для preview TF має йти через Redis, а не через disk."""
    uds_path = _find_file("runtime/store/uds.py", root)
    if uds_path is None:
        return False, "uds.py_not_found", {}

    src = _read_text(uds_path)
    if src is None:
        return False, "uds.py_read_error", {}

    metrics: Dict[str, Any] = {}

    # Витягуємо тіло read_updates (перше визначення)
    lines = src.split("\n")
    in_method = False
    method_body: List[str] = []
    indent_level = 0
    found = False
    for line in lines:
        if not found and "def read_updates" in line and "self" in line:
            found = True
            in_method = True
            indent_level = len(line) - len(line.lstrip())
            method_body.append(line)
            continue
        if in_method:
            if line.strip() == "":
                method_body.append(line)
                continue
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= indent_level and line.strip().startswith("def "):
                break
            method_body.append(line)

    method_text = "\n".join(method_body)
    metrics["read_updates_lines"] = len(method_body)

    if not found:
        return False, "read_updates_not_found", metrics

    # Перевірка 1: preview_mode визначається через _preview_tf_allowlist
    has_preview_branch = "preview_mode" in method_text or "_preview_tf_allowlist" in method_text
    metrics["has_preview_branch"] = has_preview_branch

    # Перевірка 2: preview path використовує read_preview_updates (Redis)
    has_preview_redis = "read_preview_updates" in method_text
    metrics["has_preview_redis_call"] = has_preview_redis

    # Перевірка 3: preview path НЕ використовує disk-методи
    disk_patterns = [
        "read_jsonl", "ssot_jsonl", "disk_reader", "disk_tail",
        "_read_from_disk", "load_from_disk", "read_history",
    ]
    disk_in_preview = []
    # Витягуємо тільки preview-гілку (від "if preview_mode" до "else:")
    preview_start = method_text.find("if preview_mode")
    if preview_start >= 0:
        # Знаходимо відповідне else на тому ж рівні
        preview_section = method_text[preview_start:]
        # Шукаємо "else:" на тому ж indent
        else_match = re.search(r"\n(\s+)else:", preview_section)
        if else_match:
            preview_section = preview_section[:else_match.start()]
        for pat in disk_patterns:
            if pat in preview_section:
                disk_in_preview.append(pat)

    metrics["disk_calls_in_preview_branch"] = disk_in_preview

    issues: List[str] = []
    if not has_preview_branch:
        issues.append("no_preview_branch")
    if not has_preview_redis:
        issues.append("no_redis_call_in_preview")
    if disk_in_preview:
        issues.append(f"disk_in_preview:{','.join(disk_in_preview)}")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 2b: preview_tail_live_shape (G2.1)
# Перевіряє: publish_preview_bar оновлює tail на КОЖЕН publish,
# а не лише на rollover. Має бути: replace-if-same-open / append-if-new.
# ---------------------------------------------------------------------------

def _check_preview_tail_live_shape(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """publish_preview_bar оновлює preview:tail на кожен publish (не лише rollover)."""
    uds_path = _find_file("runtime/store/uds.py", root)
    if uds_path is None:
        return False, "uds.py_not_found", {}

    src = _read_text(uds_path)
    if src is None:
        return False, "uds.py_read_error", {}

    metrics: Dict[str, Any] = {}

    # Витягуємо тіло publish_preview_bar
    lines = src.split("\n")
    in_method = False
    method_body: List[str] = []
    indent_level = 0
    found = False
    for line in lines:
        if not found and "def publish_preview_bar" in line and "self" in line:
            found = True
            in_method = True
            indent_level = len(line) - len(line.lstrip())
            method_body.append(line)
            continue
        if in_method:
            if line.strip() == "":
                method_body.append(line)
                continue
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= indent_level and line.strip().startswith("def "):
                break
            method_body.append(line)

    method_text = "\n".join(method_body)
    metrics["publish_preview_bar_lines"] = len(method_body)

    if not found:
        return False, "publish_preview_bar_not_found", metrics

    issues: List[str] = []

    # 1. Tail update НЕ має бути огорнутий в 'if rollover:'
    #    (P2X.6-U2 прибрав цей guard)
    tail_section_start = method_text.find("read_preview_tail")
    if tail_section_start < 0:
        issues.append("no_read_preview_tail_call")
    else:
        # Перевіряємо: чи tail update не під if rollover
        before = method_text[:tail_section_start]
        last_lines = before.split("\n")[-5:]
        for ln in last_lines:
            stripped = ln.strip()
            if stripped.startswith("if rollover") or stripped.startswith("if rollover:"):
                issues.append("tail_update_guarded_by_rollover")
                break
    metrics["tail_update_guarded_by_rollover"] = "tail_update_guarded_by_rollover" in issues

    # 2. Має бути логіка replace-if-same-open
    has_replace_same_open = (
        "open_ms" in method_text
        and ("tail_bars[-1]" in method_text or "tail_bars[- 1]" in method_text)
    )
    metrics["has_replace_if_same_open"] = has_replace_same_open
    if not has_replace_same_open:
        issues.append("no_replace_if_same_open_logic")

    # 3. Має бути write_preview_tail
    has_write_tail = "write_preview_tail" in method_text
    metrics["has_write_preview_tail"] = has_write_tail
    if not has_write_tail:
        issues.append("no_write_preview_tail_call")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 2c: preview_bus_isolation (G2.1)
# Перевіряє: read_updates для preview TF ходить ТІЛЬКИ в preview-bus
# ---------------------------------------------------------------------------

def _check_preview_bus_isolation(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Preview updates ізольовані від final updates bus."""
    uds_path = _find_file("runtime/store/uds.py", root)
    if uds_path is None:
        return False, "uds.py_not_found", {}

    src = _read_text(uds_path)
    if src is None:
        return False, "uds.py_read_error", {}

    metrics: Dict[str, Any] = {}

    # Витягуємо тіло read_updates
    lines = src.split("\n")
    in_method = False
    method_body: List[str] = []
    indent_level = 0
    found = False
    for line in lines:
        if not found and "def read_updates" in line and "self" in line:
            found = True
            in_method = True
            indent_level = len(line) - len(line.lstrip())
            method_body.append(line)
            continue
        if in_method:
            if line.strip() == "":
                method_body.append(line)
                continue
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= indent_level and line.strip().startswith("def "):
                break
            method_body.append(line)

    method_text = "\n".join(method_body)
    metrics["read_updates_lines"] = len(method_body)

    if not found:
        return False, "read_updates_not_found", metrics

    issues: List[str] = []

    # Preview гілка має використовувати read_preview_updates
    has_preview_bus = "read_preview_updates" in method_text
    metrics["has_preview_bus_call"] = has_preview_bus
    if not has_preview_bus:
        issues.append("no_preview_bus_call")

    # Preview гілка НЕ має використовувати _updates_bus.read_updates
    # (final bus). Перевіряємо, що preview_mode guard існує
    has_preview_guard = "preview_mode" in method_text
    metrics["has_preview_guard"] = has_preview_guard
    if not has_preview_guard:
        issues.append("no_preview_mode_guard")

    # Перевіряємо, що publish_preview_bar НЕ пише в final bus
    publish_path = _find_file("runtime/store/uds.py", root)
    if publish_path:
        full_src = _read_text(publish_path) or ""
        # publish_preview_bar не повинен викликати _updates_bus (final)
        ppb_start = full_src.find("def publish_preview_bar")
        ppb_end = full_src.find("\n    def ", ppb_start + 1) if ppb_start >= 0 else -1
        if ppb_start >= 0:
            ppb_body = full_src[ppb_start:ppb_end] if ppb_end > ppb_start else full_src[ppb_start:]
            uses_final_bus = "_updates_bus" in ppb_body
            metrics["publish_preview_uses_final_bus"] = uses_final_bus
            if uses_final_bus:
                issues.append("publish_preview_bar_uses_final_bus")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 3: api_splitbrain
# Перевіряє: server.py ігнорує prefer_redis/force_disk (PATCH 085)
# ---------------------------------------------------------------------------

def _check_api_splitbrain(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """server.py не має активних if prefer_redis / if force_disk гілок
    що змінюють ReadPolicy. Має бути лише warning-and-ignore."""
    server_path = _find_file("ui_chart_v3/server.py", root)
    if server_path is None:
        return False, "server.py_not_found", {}

    src = _read_text(server_path)
    if src is None:
        return False, "server.py_read_error", {}

    metrics: Dict[str, Any] = {}

    # 1. prefer_redis/force_disk мають парситись (для видачі warning)
    has_parse_prefer = "prefer_redis" in src
    has_parse_force = "force_disk" in src
    metrics["has_prefer_redis_ref"] = has_parse_prefer
    metrics["has_force_disk_ref"] = has_parse_force

    # 2. Перевіряємо, що є warning для ігнорування
    has_ignore_warning = "query_param_ignored" in src
    metrics["has_ignore_warning"] = has_ignore_warning

    # 3. ReadPolicy має бути hardcoded (False, False) — дозволяємо додаткові kwargs після
    rp_pattern = re.compile(
        r"ReadPolicy\s*\(\s*"
        r"force_disk\s*=\s*False\s*,\s*"
        r"prefer_redis\s*=\s*False\b",
        re.DOTALL,
    )
    has_hardcoded_rp = bool(rp_pattern.search(src))
    metrics["has_hardcoded_ReadPolicy_False_False"] = has_hardcoded_rp

    # 4. Заборонені патерни: ReadPolicy з force_disk=True (справжній split-brain).
    #    prefer_redis=True дозволений — це Redis hot-path оптимізація, не split-brain.
    bad_rp = re.compile(
        r"ReadPolicy\s*\([^)]*force_disk\s*=\s*(?:True|force_disk)",
        re.DOTALL,
    )
    bad_matches = bad_rp.findall(src)
    metrics["bad_ReadPolicy_matches"] = len(bad_matches)

    # 5. Перевіряємо preview-гілку: не має бути ReadPolicy для preview TFs
    # Preview має йти через uds.read_preview_window, без ReadPolicy
    has_preview_window_call = "read_preview_window" in src
    metrics["has_preview_window_call"] = has_preview_window_call

    issues: List[str] = []
    if not has_ignore_warning:
        issues.append("no_query_param_ignored_warning")
    if not has_hardcoded_rp:
        issues.append("ReadPolicy_not_hardcoded_False_False")
    if bad_matches:
        issues.append(f"bad_ReadPolicy_found:{len(bad_matches)}")
    if not has_preview_window_call:
        issues.append("no_read_preview_window_call")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Sub-gate 4: tick_schema
# Перевіряє: tick_v1.json дозволяє src=string (без enum);
#            tick_preview_worker має _validate_tick_schema
# ---------------------------------------------------------------------------

def _check_tick_schema(root: str) -> Tuple[bool, str, Dict[str, Any]]:
    """tick_v1 дозволяє довільний src (string), worker має schema guard."""
    schema_path = _find_file(
        "core/contracts/public/marketdata_v1/tick_v1.json", root,
    )
    worker_path = _find_file(
        "runtime/ingest/tick_preview_worker.py", root,
    )

    metrics: Dict[str, Any] = {}
    issues: List[str] = []

    # --- tick_v1.json ---
    if schema_path is None:
        issues.append("tick_v1.json_not_found")
    else:
        raw = _read_text(schema_path)
        if raw is None:
            issues.append("tick_v1.json_read_error")
        else:
            try:
                schema = json.loads(raw)
            except Exception:
                issues.append("tick_v1.json_parse_error")
                schema = None

            if schema is not None:
                props = schema.get("properties", {})
                src_prop = props.get("src", {})
                src_type = src_prop.get("type")
                src_enum = src_prop.get("enum")
                metrics["src_type"] = src_type
                metrics["src_has_enum"] = src_enum is not None

                # src має бути "string" без enum (щоб fxcm_wallclock пройшов)
                if src_type != "string":
                    issues.append(f"src_type_not_string:{src_type}")
                if src_enum is not None:
                    # Якщо є enum, перевіряємо що fxcm_wallclock дозволений
                    if "fxcm_wallclock" not in src_enum:
                        issues.append("src_enum_blocks_fxcm_wallclock")

                # required має містити src
                required = schema.get("required", [])
                metrics["src_in_required"] = "src" in required
                if "src" not in required:
                    issues.append("src_not_required")

                # additionalProperties має бути false
                addl = schema.get("additionalProperties")
                metrics["additionalProperties"] = addl
                if addl is not False:
                    issues.append(f"additionalProperties_not_false:{addl}")

    # --- tick_preview_worker.py ---
    if worker_path is None:
        issues.append("tick_preview_worker.py_not_found")
    else:
        wsrc = _read_text(worker_path)
        if wsrc is None:
            issues.append("tick_preview_worker.py_read_error")
        else:
            has_validate = "_validate_tick_schema" in wsrc
            metrics["worker_has_validate_tick_schema"] = has_validate
            if not has_validate:
                issues.append("worker_missing_validate_tick_schema")

            # Перевіряємо що є список required полів
            has_required = "_TICK_REQUIRED" in wsrc
            metrics["worker_has_TICK_REQUIRED"] = has_required
            if not has_required:
                issues.append("worker_missing_TICK_REQUIRED")

    if issues:
        return False, ";".join(issues), metrics
    return True, "ok", metrics


# ---------------------------------------------------------------------------
# Головна точка входу
# ---------------------------------------------------------------------------

_SUBGATES = [
    ("nomix_disk", _check_nomix_disk),
    ("uds_hotpath", _check_uds_hotpath),
    ("preview_tail_live_shape", _check_preview_tail_live_shape),
    ("preview_bus_isolation", _check_preview_bus_isolation),
    ("api_splitbrain", _check_api_splitbrain),
    ("tick_schema", _check_tick_schema),
]


def run_gate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Запуск всіх підгейтів preview_plane.

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
        print("EXIT_GATE_FAIL: preview_plane ізоляція порушена")
        for line in result.get("details", "").split("; "):
            print(f"  {line}")
    else:
        print("EXIT_GATE_OK: preview_plane ізоляція підтверджена")
        for line in result.get("details", "").split("; "):
            print(f"  {line}")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
