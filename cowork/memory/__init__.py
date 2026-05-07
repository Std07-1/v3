"""Cowork memory layer — pure JSONL persistence (CW2).

Цей пакет тримає схему та storage helpers для `published_thesis.jsonl`.
Ніяких HTTP / Anthropic / Telegram імпортів тут бути не може —
адаптери (endpoints) живуть у `runtime/api_v3/cowork.py`.

Інваріанти cowork: див. ADR-001 §3.3 (CW1–CW6).
"""

from cowork.memory.schema import (
    PublishedThesis,
    ScenarioSummary,
    THESIS_GRADES,
    PREFERRED_DIRECTIONS,
    MARKET_PHASES,
    SESSIONS,
)
from cowork.memory.store import (
    append_thesis,
    read_recent,
    read_by_scan_id,
    DEFAULT_STORE_PATH,
)

__all__ = [
    "PublishedThesis",
    "ScenarioSummary",
    "THESIS_GRADES",
    "PREFERRED_DIRECTIONS",
    "MARKET_PHASES",
    "SESSIONS",
    "append_thesis",
    "read_recent",
    "read_by_scan_id",
    "DEFAULT_STORE_PATH",
]
