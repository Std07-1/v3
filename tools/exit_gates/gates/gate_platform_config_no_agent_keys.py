"""Exit-gate: platform_config_no_agent_keys — ADR-0090 S4.

config.json (SSOT платформи) не має top-level секцій зовнішнього AI-клієнта. Усе
агентське живе в одній секції `agent_bridge` (ADR-0090 §3.6). Legacy-ключі
`wake_engine` / `agent_console` / `public_snapshot` на top-level або будь-який
інший top-level ключ з agent-лексики = два місця для одного перемикача (split-brain)
→ FAIL. Перелік legacy-ключів береться з runtime.agent_bridge.config (SSOT, D15.2).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from runtime.agent_bridge.config import AGENT_BRIDGE_SECTION, LEGACY_TOP_LEVEL_KEYS

_AGENT_LEXICON = re.compile(r"archi|agent|wake|thesis|narrative|presence", re.IGNORECASE)


def run_gate(inputs: dict) -> dict:
    root = Path(str(inputs.get("root", ".")))
    config_path = root / str(inputs.get("config_path", "config.json"))
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — гейт має впасти гучно, не тихо
        return {"ok": False, "details": f"cannot read config: {exc}", "metrics": {"violations": 1}}

    top_level = list(config.keys())
    legacy = [key for key in top_level if key in LEGACY_TOP_LEVEL_KEYS]
    agentish = [
        key for key in top_level if key != AGENT_BRIDGE_SECTION and key not in legacy and _AGENT_LEXICON.search(key)
    ]
    violations = [f"legacy_top_level:{k}" for k in legacy] + [f"agentish_top_level:{k}" for k in agentish]
    details = "; ".join(violations) or (
        f"top_level_keys={len(top_level)} {AGENT_BRIDGE_SECTION}="
        f"{'present' if AGENT_BRIDGE_SECTION in config else 'absent'}"
    )
    return {
        "ok": not violations,
        "details": details,
        "metrics": {"top_level_keys": len(top_level), "violations": len(violations)},
    }
