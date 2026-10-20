"""Наратив мовою з'єднання на golden-входах (ADR-0116 Ф2.6): ті самі режим, сценарії й числа — інші лише фрази.

Без мови golden-випадки перевіряє `test_narrative_golden.py` (байт у байт, L4); тут — що en/ru складаються зі
справжніх словників `i18n/` і не міняють нічого, крім тексту.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re

import pytest

from core.smc.narrative_inputs import NarrativeInputs
from runtime.i18n_catalog import load_catalog

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden" / "narrative"
CASES = sorted(path.stem for path in GOLDEN_DIR.glob("*.json"))
CATALOG = load_catalog()
CYRILLIC = re.compile("[\u0400-\u04ff]")
HTF_CONTEXT = {"viewer_tfs": [14400, 86400], "setup_tfs": [900, 3600]}  # як у config.json (ADR-0113)


def _inputs(case: str) -> NarrativeInputs:
    return NarrativeInputs.from_dict(json.loads((GOLDEN_DIR / ("%s.json" % case)).read_text(encoding="utf-8"))["inputs"])


def _synthesize(case: str, lang: str):
    return _inputs(case).synthesize(CATALOG.get(lang))


def _texts(block):
    texts = [block.headline, block.bias_summary, block.next_area, block.fvg_context, block.session_context,
             block.range_exhaustion_summary]
    for scenario in block.scenarios:
        texts += [scenario.trigger_desc, scenario.target_desc or "", scenario.invalidation]
    if block.htf_context is not None:
        context = block.htf_context
        texts += [context.zone_desc, context.target_desc, context.setup_desc, context.invalidation]
    return [text for text in texts if text]


@pytest.mark.parametrize("case", CASES)
def test_english_narrative_has_no_untranslated_text(case):
    source, english = _synthesize(case, "uk"), _synthesize(case, "en")
    assert [text for text in _texts(english) if CYRILLIC.search(text)] == []
    assert (english.mode, english.sub_mode, english.warnings) == (source.mode, source.sub_mode, source.warnings)


def test_htf_context_in_english():
    """Старший TF глядача (ADR-0113): зона, ціль, TF сетапу й межа — англійською, контекст той самий."""
    inputs = _inputs("xau_m15_trade_sell_approaching")
    htf = dataclasses.replace(inputs, viewer_tf_s=14400, config=dict(inputs.config, htf_context=HTF_CONTEXT))
    source, english = htf.synthesize(), htf.synthesize(CATALOG.get("en"))
    assert english.sub_mode == source.sub_mode == "htf_context"
    assert english.htf_context.zone_desc == "sell zone OB▼ A(7) 4793.01–4794.62"
    assert english.htf_context.setup_desc == "look for a setup on M15–H1"
    assert english.headline.startswith("🧭 H4: bearish context — sell zone ")
    assert [text for text in _texts(english) if CYRILLIC.search(text)] == []


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
