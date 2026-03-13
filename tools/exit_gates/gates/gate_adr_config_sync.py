"""Gate: adr_config_sync — ADR-0016 Appendix C / Rule K5.

Перевіряє що feature flags у config.json:smc (та інших секціях з `enabled`)
не можуть бути `enabled: true` якщо відповідний ADR має статус Proposed або Deprecated.

Mapping config_path → ADR визначається у _FEATURE_ADR_MAP:
  key   = JSONPath до секції з `enabled` полем
  value = ADR номер (0034, 0036, ...)

Gate читає ADR файл, витягує статус, і перевіряє що:
  - Proposed/Deprecated ADR → enabled: false у config.json
  - Accepted/Implemented/Active/Done → будь-яке значення дозволено
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- Mapping: config section → ADR number ---
# key = dot-separated path до секції з `enabled` полем у config.json
# value = ADR number string (без leading zeros у деяких випадках — підтримуємо обидва)
_FEATURE_ADR_MAP: Dict[str, str] = {
    "smc.tda": "0034",
    # Додати нові feature→ADR mappings за потребою:
    # "smc.some_feature": "NNNN",
}

# ADR statuses що дозволяють enabled: true
_ALLOWED_STATUSES = frozenset(
    {
        "accepted",
        "implemented",
        "active",
        "done",
        "completed",
        "partially implemented",  # P0+P1 done = можна enabled для done частин
    }
)

# ADR statuses що ЗАБОРОНЯЮТЬ enabled: true
_BLOCKED_STATUSES = frozenset(
    {
        "proposed",
        "deprecated",
    }
)

_STATUS_RE = re.compile(
    r"^\s*-\s*\*\*Статус\*\*\s*:\s*\*\*(.+?)\*\*",
    re.MULTILINE | re.IGNORECASE,
)


def _resolve_config_value(config: dict, dotpath: str) -> Optional[dict]:
    """Навігація по config.json за dot-separated шляхом."""
    parts = dotpath.split(".")
    node: Any = config
    for part in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node if isinstance(node, dict) else None


def _extract_adr_status(adr_dir: Path, adr_number: str) -> Optional[str]:
    """Знайти ADR файл за номером і витягти статус."""
    # Шукаємо файл що починається з номера
    candidates = sorted(adr_dir.glob(f"{adr_number}-*.md"))
    if not candidates:
        return None
    # Беремо перший (канонічний) файл, не *a.md дублікати
    adr_file = candidates[0]
    try:
        text = adr_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    match = _STATUS_RE.search(text)
    if not match:
        return None
    return match.group(1).strip().lower()


def run_gate(inputs: dict) -> dict:
    root = Path(str(inputs.get("root", ".")))
    config_path = root / str(inputs.get("config_path", "config.json"))
    adr_dir = root / str(inputs.get("adr_dir", "docs/adr"))

    violations: List[str] = []
    checked = 0

    # Читаємо config.json
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "passed": False,
            "violations": [f"Cannot read config: {exc}"],
            "checked": 0,
        }

    for dotpath, adr_number in _FEATURE_ADR_MAP.items():
        checked += 1
        section = _resolve_config_value(config, dotpath)
        if section is None:
            # Секція відсутня — не порушення (feature ще не додана)
            continue

        enabled = section.get("enabled")
        if enabled is None:
            # Немає поля enabled — пропускаємо
            continue

        adr_status = _extract_adr_status(adr_dir, adr_number)
        if adr_status is None:
            violations.append(
                f"{dotpath}.enabled={enabled} but ADR-{adr_number} not found or has no status"
            )
            continue

        # Перевірка: якщо enabled=true, статус повинен бути у _ALLOWED_STATUSES
        if enabled is True:
            # Статус може бути складний: "Partially Implemented (P0+P1)"
            status_base = adr_status.split("(")[0].strip()
            if status_base in _BLOCKED_STATUSES:
                violations.append(
                    f"K5 VIOLATION: {dotpath}.enabled=true "
                    f"but ADR-{adr_number} status='{adr_status}' "
                    f"(blocked: Proposed/Deprecated ADR cannot have enabled features)"
                )

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "checked": checked,
    }
