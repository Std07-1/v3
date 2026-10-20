"""Наратив мовою з'єднання на golden-входах (ADR-0116 Ф2.6): ті самі режим, сценарії й числа — інші лише фрази.

Без мови golden-випадки перевіряє `test_narrative_golden.py` (байт у байт, L4); тут — що en/ru складаються зі
справжніх словників `i18n/` і не міняють нічого, крім тексту.
"""
from __future__ import annotations

import json
import pathlib

from core.smc.narrative_inputs import NarrativeInputs
from runtime.i18n_catalog import load_catalog

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden" / "narrative"
CATALOG = load_catalog()


def _synthesize(case: str, lang: str):
    data = json.loads((GOLDEN_DIR / ("%s.json" % case)).read_text(encoding="utf-8"))
    return NarrativeInputs.from_dict(data["inputs"]).synthesize(CATALOG.get(lang))


def test_scenario_texts_in_english_keep_numbers_and_zone():
    source, english = (_synthesize("xau_m15_trade_sell_approaching", lang) for lang in ("uk", "en"))
    scenario, translated = source.scenarios[0], english.scenarios[0]
    assert english.headline == "\U0001f7e1 SELL — approaching (outside KZ)"
    assert translated.entry_desc == scenario.entry_desc == "OB▼ A(7) 4793.01–4794.62"
    assert translated.trigger_desc == "4.3 ATR to the zone"
    assert translated.target_desc == "prev. Asia low 4691.80"
    assert translated.invalidation == "Above 4794.62 — zone broken"
    assert english.fvg_context == "OB entry refined by FVG: 4779.20–4833.64"
    assert (translated.stop_price, translated.target_price) == (scenario.stop_price, scenario.target_price)


def test_confirmed_after_entry_in_russian():
    russian = _synthesize("xag_h1_zone_path_confirmed_after_entry", "ru")
    assert russian.headline == "\U0001f7e2 BUY — подтверждено после входа"
    assert russian.scenarios[0].trigger_desc == "Подтверждено после входа: H1 CHoCH↑ + импульс"
    assert russian.scenarios[0].invalidation == "Ниже 71.589 — зона пробита"
