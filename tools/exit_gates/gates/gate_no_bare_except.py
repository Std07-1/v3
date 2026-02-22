"""Gate: no_bare_except — enforce logging in except blocks (Rule 9).

Знаходить except Exception / except блоки де body = тільки pass/continue/return
без logging. Авто-дозволяє: cleanup patterns, import guards.
Inline ignore: '# bare_except: allow' на рядку except.
Ratchet: max_violations бюджет (зменшувати по мірі фіксів).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Set

# Cleanup methods — silent except у їхньому try body авто-дозволений
_CLEANUP_ATTRS: Set[str] = {
    "close", "kill", "terminate", "logout", "__exit__",
    "unlink", "shutdown", "dispose", "wait", "flush",
}


def _is_bare_body(body: list) -> bool:
    """Чи тіло except — тільки pass/continue/return/assign (без логіки)?"""
    for stmt in body:
        if isinstance(stmt, (ast.Pass, ast.Continue)):
            continue
        if isinstance(stmt, ast.Return):
            continue
        if isinstance(stmt, ast.Assign):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        return False
    return True


def _has_logging(body: list) -> bool:
    """Чи є виклик logging.xxx / Logging.xxx в тілі?"""
    mod = ast.Module(body=body, type_ignores=[])
    for node in ast.walk(mod):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Name) and val.id in (
                "logging", "Logging", "logger", "log",
            ):
                return True
    return False


def _is_cleanup_try(try_body: list) -> bool:
    """Try body містить cleanup виклик (.close()/.kill()/…)."""
    mod = ast.Module(body=try_body, type_ignores=[])
    for node in ast.walk(mod):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _CLEANUP_ATTRS:
                return True
    return False


def _is_import_guard(try_body: list) -> bool:
    """Try body — import statement (optional dependency)."""
    for stmt in try_body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            return True
    return False


def _scan_file(filepath: str, relpath: str) -> List[Dict[str, Any]]:
    """Сканувати файл на bare except без logging."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=relpath)
        lines = source.splitlines()
    except Exception:
        return []

    violations: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _is_bare_body(handler.body):
                continue
            if _has_logging(handler.body):
                continue
            lineno = getattr(handler, "lineno", 0)
            # Inline ignore
            if 1 <= lineno <= len(lines) and "bare_except: allow" in lines[lineno - 1]:
                continue
            # Auto-allow: cleanup pattern
            if _is_cleanup_try(node.body):
                continue
            # Auto-allow: import guard
            if _is_import_guard(node.body):
                continue
            violations.append({"file": relpath, "line": lineno})
    return violations


def run_gate(inputs: dict) -> dict:
    """Entry point для run_exit_gates runner."""
    root = Path(str(inputs.get("root", ".")))
    max_violations = int(inputs.get("max_violations", 0))
    scan_dirs = inputs.get("scan_dirs", ["core", "runtime", "app", "ui_chart_v3"])

    all_v: List[Dict[str, Any]] = []
    files = 0

    for d in scan_dirs:
        dpath = root / d
        if not dpath.is_dir():
            continue
        for py in sorted(dpath.rglob("*.py")):
            rel = str(py.relative_to(root)).replace("\\", "/")
            files += 1
            all_v.extend(_scan_file(str(py), rel))

    n = len(all_v)
    if n <= max_violations:
        return {
            "ok": True,
            "details": f"files={files} violations={n} budget={max_violations}",
        }

    detail_lines = []
    for v in all_v[:25]:
        detail_lines.append(f"{v['file']}:{v['line']}")
    if n > 25:
        detail_lines.append(f"...+{n - 25} more")
    return {
        "ok": False,
        "details": f"violations={n} exceeds budget={max_violations}; " + "; ".join(detail_lines),
    }
