"""Artifact schemas — schema-first dataclasses для всіх стадій конвеєру.

Python 3.7 compatible. Без pydantic — ручна валідація через validate().
Правило: unknown fields = помилка. Всі поля явні.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Базовий артефакт
# ---------------------------------------------------------------------------

@dataclass
class BaseArtifact:
    schema_version: str       # "1.0"
    run_id: str               # "run_20260226_143022_TRI-20260226-001"
    ticket_id: str            # "TRI-20260226-001"
    stage: str                # "triage_open" | "qa_proof" | ...
    created_at: str           # ISO 8601
    prev_artifact_sha256: str  # SHA256 попереднього файлу або "genesis"


# ---------------------------------------------------------------------------
# Стадія 1 — Triage Open (DISCOVERY)
# ---------------------------------------------------------------------------

@dataclass
class ArtifactTriageOpen(BaseArtifact):
    severity: str             # S0 | S1 | S2 | S3 | S4
    route: str                # READY_FOR_QA | ADR_ONLY | NOT_A_BUG | WONTFIX
    facts: List[Dict[str, Any]]     # [{file, line, observation}]
    failure_model: str        # короткий опис моделі відмови
    gap_analysis: str         # що невідомо
    stop_rule_triggered: bool
    stop_reason: List[str]    # непорожній якщо stop_rule_triggered

    VALID_SEVERITIES = ("S0", "S1", "S2", "S3", "S4")
    VALID_ROUTES = ("READY_FOR_QA", "ADR_ONLY", "NOT_A_BUG", "WONTFIX")

    def validate(self):  # type: () -> None
        if self.severity not in self.VALID_SEVERITIES:
            raise ValueError("triage_open: invalid severity %r" % self.severity)
        if self.route not in self.VALID_ROUTES:
            raise ValueError("triage_open: invalid route %r" % self.route)
        if self.stop_rule_triggered and not self.stop_reason:
            raise ValueError("triage_open: stop_rule_triggered but stop_reason is empty")


# ---------------------------------------------------------------------------
# Стадія 2 — QA Proof
# ---------------------------------------------------------------------------

@dataclass
class ArtifactQAProof(BaseArtifact):
    status: str                          # CONFIRMED | NOT_A_BUG | NEEDS_ENV
    repro_steps: List[str]               # 2-6 команд
    expected_logs: List[str]             # 2-5 патернів
    proof_files: List[str]               # файли/логи як докази
    environment_gaps: List[str]          # якщо NEEDS_ENV

    VALID_STATUSES = ("CONFIRMED", "NOT_A_BUG", "NEEDS_ENV")

    def validate(self):  # type: () -> None
        if self.status not in self.VALID_STATUSES:
            raise ValueError("qa_proof: invalid status %r" % self.status)
        if self.status == "NEEDS_ENV" and not self.environment_gaps:
            raise ValueError("qa_proof: NEEDS_ENV but environment_gaps is empty")


# ---------------------------------------------------------------------------
# Стадія 3 — Design
# ---------------------------------------------------------------------------

@dataclass
class ArtifactDesign(BaseArtifact):
    stop_rule_triggered: bool
    stop_reason: List[str]
    adr_required: bool
    adr_ref: str                         # "ADR-0016" або "" якщо не потрібен
    patch_plan: List[Dict[str, Any]]     # [{file, change_description, loc_estimate}]
    files_to_touch: List[str]
    invariants_checked: List[str]        # ["I0", "I1", ...] які перевірені
    total_loc_estimate: int

    def validate(self):  # type: () -> None
        if self.stop_rule_triggered and not self.stop_reason:
            raise ValueError("design: stop_rule_triggered but stop_reason is empty")
        if self.adr_required and not self.adr_ref:
            # Не помилка на рівні схеми — conductor enforcement окремо
            pass
        if self.total_loc_estimate < 0:
            raise ValueError("design: total_loc_estimate < 0")


# ---------------------------------------------------------------------------
# Стадія 4 — Patch
# ---------------------------------------------------------------------------

@dataclass
class ArtifactPatch(BaseArtifact):
    files_changed: List[Dict[str, Any]]  # [{path, lines_added, lines_removed}]
    diff_loc: int                        # total LOC changed (added + removed)
    tests_added: List[str]
    rollback_steps: List[str]            # конкретні команди для відкату
    loc_budget_ok: bool                  # diff_loc <= 150

    def validate(self):  # type: () -> None
        if self.diff_loc < 0:
            raise ValueError("patch: diff_loc < 0")
        expected_ok = self.diff_loc <= 150
        if self.loc_budget_ok != expected_ok:
            raise ValueError(
                "patch: loc_budget_ok=%r but diff_loc=%d (expected %r)"
                % (self.loc_budget_ok, self.diff_loc, expected_ok)
            )
        if not self.rollback_steps:
            raise ValueError("patch: rollback_steps must not be empty")


# ---------------------------------------------------------------------------
# Стадія 5 — QA Recheck
# ---------------------------------------------------------------------------

@dataclass
class ArtifactQARecheck(BaseArtifact):
    status: str                              # PASS | FAIL
    acceptance_criteria: List[Dict[str, Any]]  # [{criterion, result, evidence}]
    gates_result: Dict[str, Any]             # {gate_name: {ok, details}}
    close_evidence: List[str]

    VALID_STATUSES = ("PASS", "FAIL")

    def validate(self):  # type: () -> None
        if self.status not in self.VALID_STATUSES:
            raise ValueError("qa_recheck: invalid status %r" % self.status)
        if not self.acceptance_criteria:
            raise ValueError("qa_recheck: acceptance_criteria must not be empty")


# ---------------------------------------------------------------------------
# Стадія 6 — Triage Close
# ---------------------------------------------------------------------------

@dataclass
class ArtifactTriageClose(BaseArtifact):
    final_status: str          # CLOSED | BLOCKED
    changelog_entry_id: str    # id записаний у changelog.jsonl (або "" якщо BLOCKED)
    summary: str
    artifacts_index: List[str]  # відносні шляхи до попередніх артефактів
    blocked_reason: str         # непорожній якщо BLOCKED

    VALID_STATUSES = ("CLOSED", "BLOCKED")

    def validate(self):  # type: () -> None
        if self.final_status not in self.VALID_STATUSES:
            raise ValueError("triage_close: invalid final_status %r" % self.final_status)
        if self.final_status == "BLOCKED" and not self.blocked_reason:
            raise ValueError("triage_close: BLOCKED but blocked_reason is empty")
        if self.final_status == "CLOSED" and not self.changelog_entry_id:
            raise ValueError("triage_close: CLOSED but changelog_entry_id is empty")


# ---------------------------------------------------------------------------
# Deserialization helpers
# ---------------------------------------------------------------------------

_STAGE_TO_CLASS = {
    "triage_open":   ArtifactTriageOpen,
    "qa_proof":      ArtifactQAProof,
    "design":        ArtifactDesign,
    "patch":         ArtifactPatch,
    "qa_recheck":    ArtifactQARecheck,
    "triage_close":  ArtifactTriageClose,
}

_BASE_FIELDS = {f.name for f in BaseArtifact.__dataclass_fields__.values()}  # type: ignore[attr-defined]


def from_dict(stage, data):
    # type: (str, dict) -> BaseArtifact
    """Десеріалізація артефакту з dict. Перевіряє unknown fields."""
    cls = _STAGE_TO_CLASS.get(stage)
    if cls is None:
        raise ValueError("Unknown stage: %r" % stage)
    known = set(cls.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    unknown = set(data.keys()) - known
    if unknown:
        raise ValueError("Artifact %r has unknown fields: %s" % (stage, sorted(unknown)))
    obj = cls(**{k: data[k] for k in known if k in data})
    obj.validate()  # type: ignore[attr-defined]
    return obj


def from_file(path):
    # type: (str) -> BaseArtifact
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    stage = data.get("stage")
    if not stage:
        raise ValueError("Artifact file %r missing 'stage' field" % path)
    return from_dict(stage, data)


def to_dict(artifact):
    # type: (BaseArtifact) -> dict
    return asdict(artifact)
