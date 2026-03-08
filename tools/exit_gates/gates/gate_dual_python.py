"""Gate: dual_python — ADR-0016 enforcement.

Перевіряє:
1. Broker-shared файли (core/model/bars.py, core/config_loader.py, env_profile.py)
   мають синтаксис сумісний з Python 3.7 (жодних walrus :=, match/case, type stmt).
2. broker_sidecar.py / tick_publisher_fxcm.py НЕ імпортують UDS / DeriveEngine / numpy / pandas.
3. m1_ingestion_worker.py НЕ імпортує forexconnect.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, List


# Файли, які МУСЯТЬ залишатися Python 3.7-compatible
_PY37_COMPAT_FILES = [
    "core/model/bars.py",
    "core/config_loader.py",
    "env_profile.py",
]

# Broker-side файли НЕ повинні імпортувати ці модулі
_BROKER_FILES = [
    "runtime/ingest/broker_sidecar.py",
    "runtime/ingest/tick_publisher_fxcm.py",
]
_BROKER_FORBIDDEN_IMPORTS = {"numpy", "pandas", "runtime.store.uds", "runtime.ingest.derive_engine"}

# Platform-side worker НЕ повинен імпортувати forexconnect
_PLATFORM_FILES = [
    "runtime/ingest/m1_ingestion_worker.py",
]
_PLATFORM_FORBIDDEN_IMPORTS = {"forexconnect"}

# Python 3.8+ syntax patterns (simple heuristic, not full parser)
_WALRUS_RE = re.compile(r":=")
_MATCH_RE = re.compile(r"^\s*match\s+\S", re.MULTILINE)
_CASE_RE = re.compile(r"^\s*case\s+\S", re.MULTILINE)
_TYPE_STMT_RE = re.compile(r"^\s*type\s+\w+\s*=", re.MULTILINE)


def _check_py37_compat(filepath: Path) -> List[str]:
    """Перевірити файл на Python 3.8+ синтаксис."""
    violations = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return [f"{filepath}: read_error={exc}"]

    lines = source.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        # Skip comments and strings  
        if stripped.startswith("#"):
            continue
        if _WALRUS_RE.search(line):
            violations.append(f"{filepath}:{i} walrus operator ':=' (Python 3.8+)")

    if _MATCH_RE.search(source):
        violations.append(f"{filepath}: match statement (Python 3.10+)")
    if _TYPE_STMT_RE.search(source):
        violations.append(f"{filepath}: type statement (Python 3.12+)")

    return violations


def _check_forbidden_imports(
    filepath: Path,
    forbidden: set,
) -> List[str]:
    """Перевірити файл на заборонені імпорти."""
    violations = []
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except Exception as exc:
        return [f"{filepath}: parse_error={exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                full = alias.name
                if mod in forbidden or full in forbidden:
                    ln = getattr(node, "lineno", 0)
                    violations.append(f"{filepath}:{ln} forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split(".")[0]
                full = node.module
                if mod in forbidden or full in forbidden:
                    ln = getattr(node, "lineno", 0)
                    violations.append(f"{filepath}:{ln} forbidden import '{node.module}'")

    return violations


def run_gate(inputs: dict) -> dict:
    """Entry point для run_exit_gates runner."""
    root = Path(str(inputs.get("root", ".")))
    all_v: List[str] = []

    # 1. Python 3.7 compatibility check
    for rel in _PY37_COMPAT_FILES:
        fp = root / rel
        if fp.exists():
            all_v.extend(_check_py37_compat(fp))

    # 2. Broker files: no UDS/DeriveEngine/numpy/pandas
    for rel in _BROKER_FILES:
        fp = root / rel
        if fp.exists():
            all_v.extend(_check_forbidden_imports(fp, _BROKER_FORBIDDEN_IMPORTS))

    # 3. Platform worker: no forexconnect
    for rel in _PLATFORM_FILES:
        fp = root / rel
        if fp.exists():
            all_v.extend(_check_forbidden_imports(fp, _PLATFORM_FORBIDDEN_IMPORTS))

    n = len(all_v)
    if n == 0:
        return {
            "ok": True,
            "details": (
                f"py37_compat_files={len(_PY37_COMPAT_FILES)} "
                f"broker_files={len(_BROKER_FILES)} "
                f"platform_files={len(_PLATFORM_FILES)} violations=0"
            ),
        }

    detail = "; ".join(all_v[:15])
    if n > 15:
        detail += f"; ...+{n - 15} more"
    return {"ok": False, "details": f"violations={n}; {detail}"}
