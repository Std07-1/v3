"""Gate: dependency_rule — I0 enforcement (core ¬→ runtime/ui/tools/app).

Path-based layer detection + AST import scanning.
Inline ignore: додати '# deps_guard: ignore' на рядку імпорту.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Set

# Шар → set шарів, з яких ЗАБОРОНЕНО імпортувати
FORBIDDEN: Dict[str, Set[str]] = {
    "core": {"runtime", "ui_chart_v3", "tools", "app"},
    "runtime": {"tools"},
}

# Внутрішні root-пакети проєкту (все інше = stdlib/зовнішнє → skip)
INTERNAL_ROOTS: Set[str] = {"core", "runtime", "ui_chart_v3", "tools", "app"}


def _layer(relpath: str) -> str:
    """Визначити шар по шляху файлу."""
    parts = Path(relpath).parts
    return parts[0] if parts else ""


def _imported_roots(node: ast.AST) -> List[str]:
    """Витягти root-пакети з import/importfrom вузла."""
    roots: List[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            roots.append(alias.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            return []  # relative import → same layer, не карати
        if node.module:
            roots.append(node.module.split(".")[0])
    return roots


def _scan_file(filepath: str, relpath: str) -> List[Dict[str, Any]]:
    """Сканувати один файл на порушення dependency rule."""
    layer = _layer(relpath)
    forbidden = FORBIDDEN.get(layer)
    if not forbidden:
        return []

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=relpath)
        lines = source.splitlines()
    except Exception:
        return [{"file": relpath, "line": 0, "imported": "?", "note": "parse_error"}]

    violations: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        lineno = getattr(node, "lineno", 0)
        # Inline ignore
        if 1 <= lineno <= len(lines) and "deps_guard: ignore" in lines[lineno - 1]:
            continue
        for root in _imported_roots(node):
            if root in INTERNAL_ROOTS and root in forbidden:
                violations.append({
                    "file": relpath,
                    "line": lineno,
                    "layer": layer,
                    "imported": root,
                })
    return violations


def run_gate(inputs: dict) -> dict:
    """Entry point для run_exit_gates runner."""
    root = Path(str(inputs.get("root", ".")))
    all_v: List[Dict[str, Any]] = []
    files = 0

    for pkg in sorted(INTERNAL_ROOTS):
        pkg_dir = root / pkg
        if not pkg_dir.is_dir():
            continue
        for py in sorted(pkg_dir.rglob("*.py")):
            rel = str(py.relative_to(root)).replace("\\", "/")
            files += 1
            all_v.extend(_scan_file(str(py), rel))

    n = len(all_v)
    if n == 0:
        parts = [f"files={files}", "violations=0"]
        for layer, bad in sorted(FORBIDDEN.items()):
            parts.append(f"{layer}-/->{{{'|'.join(sorted(bad))}}}:OK")
        return {"ok": True, "details": "; ".join(parts)}

    detail_lines = []
    for v in all_v[:20]:
        detail_lines.append(
            f"{v['file']}:{v['line']} {v.get('layer', '')}->{v.get('imported', '')}"
        )
    if n > 20:
        detail_lines.append(f"...+{n - 20} more")
    return {
        "ok": False,
        "details": f"violations={n}; " + "; ".join(detail_lines),
    }
