"""Мовна копія тексту в рушії (ADR-0116 Ф2.2b): від мови не залежне збирається раз, мовою — лише складання фраз.

Замір 19.10 (data_v3 XAU 22–26.09, 280 закриттів M15): 99% вартості тексту — збір входів наративу, синтез фраз ~0.2 мс.
Тож копія en з тими самими аргументами бере входи проходу джерела, а розбір кожної мови складається з однієї чернетки.
"""
from __future__ import annotations

from core.i18n import SOURCE_TRANSLATOR, Translator
from test_briefing_runner import _runner
from test_narrative_now_ms import WED_ASIA, XAU

EN = Translator("en", {})
H1 = 3600


def _count_collects(runner, monkeypatch):
    calls = []
    original = runner._collect_narrative_inputs

    def _counted(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "_collect_narrative_inputs", _counted)
    return calls


def test_language_copy_reuses_inputs_of_the_source_pass(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    collects = _count_collects(runner, monkeypatch)
    price = runner._engine.get_bars(XAU, H1)[-1].c
    source = runner.get_narrative(XAU, H1, price, 2.0, now_ms=WED_ASIA, record=False)
    copy = runner.get_narrative(XAU, H1, price, 2.0, now_ms=WED_ASIA, record=False, tr=EN)
    assert collects == [H1]  # копія входів не збирала
    assert copy.to_wire() == source.to_wire()  # Ф2.2b: перекладача синтез ще не має — ті самі тексти й дані


def test_language_copy_with_other_arguments_collects_its_own_inputs(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    collects = _count_collects(runner, monkeypatch)
    price = runner._engine.get_bars(XAU, H1)[-1].c
    runner.get_narrative(XAU, H1, price, 2.0, now_ms=WED_ASIA, record=False)
    runner.get_narrative(XAU, H1, price + 1.0, 2.0, now_ms=WED_ASIA, record=False, tr=EN)
    assert collects == [H1, H1]  # інша ціна — інші входи, не чужі


def test_briefing_of_each_language_is_rendered_from_one_draft(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    collects = _count_collects(runner, monkeypatch)
    source = runner.get_briefing(XAU)
    collected_once = len(collects)
    copy = runner.get_briefing(XAU, tr=EN)
    assert collected_once > 0 and len(collects) == collected_once  # чернетка одна на закритий бар для всіх мов
    assert copy is not source and copy.to_wire() == source.to_wire()
    assert runner.get_briefing(XAU, tr=EN) is copy  # кеш мови — до нового бару
    assert runner.get_briefing(XAU, tr=SOURCE_TRANSLATOR) is source
