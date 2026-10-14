"""Повний вхід розбору наративу — один контракт для живого шляху, прогону й golden-тестів (ADR-0113 Ф1, R10).

`SmcRunner.get_narrative` збирає `NarrativeInputs` і синтезує з нього; прогін по історії знімає той самий вхід на
момент, golden-тест відтворює з нього той самий `NarrativeBlock`. Поля названі точно як параметри
`synthesize_narrative` — `as_kwargs()` і є відображенням, другого немає (розбіжність перевіряє тест сигнатури).

Серіалізація без втрат: поля знімка пласкі, `origin_event_ms` теж (його немає в `to_wire`, тому не wire-формат).
S0: pure, без I/O.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional, Tuple

from core.smc.narrative import synthesize_narrative
from core.smc.types import NarrativeBlock, SmcLevel, SmcSnapshot, SmcSwing, SmcZone
from core.smc.zone_path import ZonePath


@dataclasses.dataclass(frozen=True)
class NarrativeInputs:
    snapshot: SmcSnapshot
    bias_map: Dict[str, str]
    zone_grades: Dict[str, dict]
    momentum_map: Dict[str, dict]
    viewer_tf_s: int
    current_price: float
    atr: float
    config: Dict[str, Any]
    session_info: Optional[Tuple[str, bool]] = None
    range_exhaustion: Optional[Any] = None  # стан ADR-0053 — лише коли smc.range_exhaustion.enabled
    price_digits: Optional[int] = None
    zone_paths: Optional[Dict[str, ZonePath]] = None  # Ф2a: шлях ціни від бару зламу зони; None — поведінка Ф1

    def as_kwargs(self) -> Dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in dataclasses.fields(self)}

    def synthesize(self) -> NarrativeBlock:
        return synthesize_narrative(**self.as_kwargs())

    def to_dict(self) -> Dict[str, Any]:
        """JSON-сумісний вигляд для golden-випадку. Стан range_exhaustion не серіалізується — golden знімається з ним
        вимкненим (як у config.json); інакше відмова, а не тиха втрата поля."""
        if self.range_exhaustion is not None or self.snapshot.range_exhaustion is not None:
            raise ValueError("NarrativeInputs.to_dict: range_exhaustion не серіалізується")
        data = self.as_kwargs()
        data["snapshot"] = dataclasses.asdict(self.snapshot)
        data["session_info"] = list(self.session_info) if self.session_info is not None else None
        if self.zone_paths is not None:
            data["zone_paths"] = {zone_id: dataclasses.asdict(path) for zone_id, path in self.zone_paths.items()}
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NarrativeInputs":
        snap = data["snapshot"]
        snapshot = SmcSnapshot(**{
            **snap,
            "zones": [SmcZone(**zone) for zone in snap["zones"]],
            "swings": [SmcSwing(**swing) for swing in snap["swings"]],
            "levels": [SmcLevel(**level) for level in snap["levels"]],
        })
        session_info = tuple(data["session_info"]) if data.get("session_info") is not None else None
        zone_paths = data.get("zone_paths")
        if zone_paths is not None:
            zone_paths = {zone_id: ZonePath(**path) for zone_id, path in zone_paths.items()}
        return cls(**{**data, "snapshot": snapshot, "session_info": session_info, "zone_paths": zone_paths})
