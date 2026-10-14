"""Golden-випадки системного наративу на реальних знімках (ADR-0113 Ф1).

Кожен випадок — повний вхід розбору (`NarrativeInputs`) на реальний момент історії і `NarrativeBlock`, що з нього
вийшов (`tools.narrative_golden`). Наратив читають UI й Арчі: зміна синтезу (текст, режим, сценарій, ціль, інвалідація)
з того самого знімка має впасти тут і бути оновлена свідомо — новим знімком і причиною в коміті, а не тихо. Зони,
оцінки, рівні й config у випадку заморожені: зміни рушія ловить не golden, а прогін (`tools.narrative_replay_metrics`).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core.smc.narrative_inputs import NarrativeInputs

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden" / "narrative"
CASES = sorted(GOLDEN_DIR.glob("*.json"))


def test_golden_cases_exist():
    assert CASES, "немає golden-випадків у %s — тест не має проходити порожнім" % GOLDEN_DIR


@pytest.mark.parametrize("path", CASES, ids=[path.stem for path in CASES])
def test_narrative_matches_golden_case(path):
    case = json.loads(path.read_text(encoding="utf-8"))

    block = NarrativeInputs.from_dict(case["inputs"]).synthesize()

    assert json.loads(json.dumps(block.to_wire(), ensure_ascii=False)) == case["expected"], case["note"]
