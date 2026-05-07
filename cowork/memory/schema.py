"""PublishedThesis schema — dual-purpose record (cowork T1 + system narrative).

Один запис обслуговує два consumers:
- Cowork PRIOR CONTEXT (T1): наступний scan дізнається що публікував раніше
- System narrative: Архі / UI / dashboards отримують компактний дайджест

ADR-001 §3.4 містить повну специфікацію полів та обґрунтування dual-purpose.

Інваріант CW3: будь-яке нове поле має явно позначатись до якої групи належить
(A=identity, B=narrative core, C=cowork-specific, D=self-eval reserved) у docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

# ── Allowed enum values (SSOT для validation) ─────────────────────────────────

THESIS_GRADES: tuple[str, ...] = ("A+", "A", "B", "C")
PREFERRED_DIRECTIONS: tuple[str, ...] = ("bullish", "bearish", "range")
MARKET_PHASES: tuple[str, ...] = (
    "ranging",
    "trending_up",
    "trending_down",
    "transition",
)
SESSIONS: tuple[str, ...] = ("asia", "london", "newyork", "off-session")

# Bound на TLDR щоб не перетворювалось у плаский текст-дамп.
TLDR_MAX_CHARS: int = 200
WATCH_LEVELS_MIN: int = 2
WATCH_LEVELS_MAX: int = 5
SCAN_ID_PREFIX: str = "scan-"


@dataclass(frozen=True, slots=True)
class ScenarioSummary:
    """Один сценарій у компактному вигляді (для `scenarios_summary` поля)."""

    scenario_id: Literal["A", "B", "C"]
    label: str
    probability: int  # 0-100

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.scenario_id,
            "label": self.label,
            "probability": self.probability,
        }


@dataclass(frozen=True, slots=True)
class PublishedThesis:
    """Single published cowork mentor thesis.

    Поділ полів за групами (ADR-001 §3.4):
      Group A — identity & provenance
      Group B — system narrative core
      Group C — cowork-specific (T1+)
      Group D — reserved для T2 self-eval (не пишемо у T1)
    """

    # ── Group A: identity & provenance ────────────────────────────────────────
    ts: str  # ISO8601 UTC, e.g. "2026-05-06T14:30:00Z"
    scan_id: str  # "scan-YYYYMMDD-HHMMSS-<symbol-slug>"
    symbol: str  # "XAU/USD"
    model: str  # "claude-opus-4-7" / "claude-sonnet-4-6"
    prompt_version: str  # "v3.0"

    # ── Group B: system narrative core ────────────────────────────────────────
    current_price: float
    tldr: str  # ≤200 chars
    preferred_scenario_id: Literal["A", "B", "C"]
    preferred_direction: str  # one of PREFERRED_DIRECTIONS
    preferred_probability: int  # 0-100
    thesis_grade: str  # one of THESIS_GRADES
    market_phase: str  # one of MARKET_PHASES
    session: str  # one of SESSIONS
    in_killzone: bool
    watch_levels: tuple[float, ...]  # 2-5 рівнів
    scenarios_summary: tuple[ScenarioSummary, ...]

    # ── Group C: cowork-specific ──────────────────────────────────────────────
    telegram_msg_id: int | None = None
    prompt_hash: str = ""  # sha256[:8] of rendered prompt
    prior_context_used: bool = False
    corrects: str | None = None  # scan_id це переписує (CW4)

    # Optional metadata bag — non-breaking extension point. Не для логіки —
    # тільки для observability (e.g. cost in tokens, thinking budget used).
    extras: dict[str, Any] = field(default_factory=dict)

    # ── Validation ────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        """Fail-fast guard на вході (D2 contract-first).

        Сюди потрапляє раз на ~10-20 records/day per symbol — overhead negligible.
        """
        if not self.scan_id.startswith(SCAN_ID_PREFIX):
            raise ValueError(
                f"scan_id must start with {SCAN_ID_PREFIX!r}, got {self.scan_id!r}"
            )
        if (
            not self.symbol
            or "/" not in self.symbol
            and self.symbol not in {"BTCUSDT", "ETHUSDT"}
        ):
            raise ValueError(f"symbol looks malformed: {self.symbol!r}")
        if len(self.tldr) > TLDR_MAX_CHARS:
            raise ValueError(
                f"tldr exceeds {TLDR_MAX_CHARS} chars (got {len(self.tldr)})"
            )
        if self.preferred_direction not in PREFERRED_DIRECTIONS:
            raise ValueError(
                f"preferred_direction must be one of {PREFERRED_DIRECTIONS}, "
                f"got {self.preferred_direction!r}"
            )
        if not 0 <= self.preferred_probability <= 100:
            raise ValueError(
                f"preferred_probability out of range: {self.preferred_probability}"
            )
        if self.thesis_grade not in THESIS_GRADES:
            raise ValueError(
                f"thesis_grade must be one of {THESIS_GRADES}, got {self.thesis_grade!r}"
            )
        if self.market_phase not in MARKET_PHASES:
            raise ValueError(
                f"market_phase must be one of {MARKET_PHASES}, got {self.market_phase!r}"
            )
        if self.session not in SESSIONS:
            raise ValueError(f"session must be one of {SESSIONS}, got {self.session!r}")
        if not WATCH_LEVELS_MIN <= len(self.watch_levels) <= WATCH_LEVELS_MAX:
            raise ValueError(
                f"watch_levels must have {WATCH_LEVELS_MIN}-{WATCH_LEVELS_MAX} entries, "
                f"got {len(self.watch_levels)}"
            )
        if self.preferred_scenario_id not in {"A", "B", "C"}:
            raise ValueError(
                f"preferred_scenario_id must be A/B/C, got {self.preferred_scenario_id!r}"
            )
        if self.scenarios_summary:
            ids = {s.scenario_id for s in self.scenarios_summary}
            if self.preferred_scenario_id not in ids:
                raise ValueError(
                    f"preferred_scenario_id {self.preferred_scenario_id!r} not present "
                    f"in scenarios_summary {sorted(ids)}"
                )
            total_prob = sum(s.probability for s in self.scenarios_summary)
            # Sum should be ~100% but allow ±5 for rounding noise.
            if not 95 <= total_prob <= 105:
                raise ValueError(
                    f"scenarios_summary probabilities should sum to ~100, got {total_prob}"
                )

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_jsonable(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict (tuples → lists, dataclasses → dicts)."""
        d = asdict(self)
        d["watch_levels"] = list(self.watch_levels)
        d["scenarios_summary"] = [s.to_dict() for s in self.scenarios_summary]
        return d

    @classmethod
    def from_jsonable(cls, payload: dict[str, Any]) -> PublishedThesis:
        """Reconstruct from a dict that came from JSONL (reverse of to_jsonable).

        Не валідуємо тут окремо — `__post_init__` спрацює при конструюванні.
        """
        scenarios_raw = payload.get("scenarios_summary") or []
        scenarios = tuple(
            ScenarioSummary(
                scenario_id=s["id"] if "id" in s else s["scenario_id"],
                label=s["label"],
                probability=int(s["probability"]),
            )
            for s in scenarios_raw
        )
        watch_levels = tuple(float(x) for x in payload.get("watch_levels") or ())

        # Pull only the fields we know about — tolerate extras for forward-compat.
        return cls(
            ts=payload["ts"],
            scan_id=payload["scan_id"],
            symbol=payload["symbol"],
            model=payload["model"],
            prompt_version=payload["prompt_version"],
            current_price=float(payload["current_price"]),
            tldr=payload["tldr"],
            preferred_scenario_id=payload["preferred_scenario_id"],
            preferred_direction=payload["preferred_direction"],
            preferred_probability=int(payload["preferred_probability"]),
            thesis_grade=payload["thesis_grade"],
            market_phase=payload["market_phase"],
            session=payload["session"],
            in_killzone=bool(payload["in_killzone"]),
            watch_levels=watch_levels,
            scenarios_summary=scenarios,
            telegram_msg_id=payload.get("telegram_msg_id"),
            prompt_hash=payload.get("prompt_hash", ""),
            prior_context_used=bool(payload.get("prior_context_used", False)),
            corrects=payload.get("corrects"),
            extras=dict(payload.get("extras") or {}),
        )
