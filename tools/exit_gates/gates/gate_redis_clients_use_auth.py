"""Exit-gate: redis_clients_use_auth — ADR-0091 P2.

Кожен ``redis.Redis(...)`` у коді платформи мусить передавати ACL-креденшели
(``**spec.auth_kwargs()`` або явні ``username=``/``password=``). Пропущений клієнт
не падає локально (сервер без ACL), але на VPS з ACL мовчки отримає NOAUTH —
саме тому це гейт, а не тест: діру видно у CI, а не в проді (D15.2/I5).

Скануються ``core/``, ``runtime/``, ``app/`` — інструменти й діагностика поза скоупом.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List

SCAN_DIRS = ("core", "runtime", "app")
AUTH_MARKERS = ("auth_kwargs", "username", "password")


def _client_calls_without_auth(path: Path) -> List[int]:
    """Рядки викликів ``Redis(...)``, у яких немає жодного маркера автентифікації."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    bad: List[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in ("Redis", "StrictRedis"):
            continue
        if not _has_credentials(node):
            bad.append(node.lineno)
    return bad


def _has_credentials(call: ast.Call) -> bool:
    """Явні ``username=``/``password=``, ``**spec.auth_kwargs()`` або ``**<...auth...>``."""
    for kw in call.keywords:
        if kw.arg in ("username", "password"):
            return True
        if kw.arg is None and "auth" in ast.dump(kw.value).lower():
            return True
    return "auth_kwargs" in ast.dump(call)


def run_gate(inputs: dict) -> dict:
    root = Path(str(inputs.get("root", ".")))
    scanned = 0
    violations: List[str] = []
    for scan_dir in SCAN_DIRS:
        base = root / scan_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            scanned += 1
            for lineno in _client_calls_without_auth(path):
                rel = str(path.relative_to(root)).replace("\\", "/")
                violations.append(f"{rel}:{lineno}")
    return {
        "ok": not violations,
        "details": "; ".join(violations) or f"files={scanned} clients_all_authenticated=OK",
        "metrics": {"files": scanned, "violations": len(violations)},
    }
