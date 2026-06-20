"""Юніт-тести допоміжних модулів (детектор, кеш-ключ, розбивка речень)."""

from __future__ import annotations

import pytest

from app.cache import make_key
from app.detect import detect
from app.engines.mock import MockEngine


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Привіт, сестричко", "uk"),  # українські і/ї
        ("Привет мир", "ru"),  # кирилиця без укр-маркерів
        ("Dobrý den, jak se máš", "cs"),  # чеська діакритика
        ("Hogy vagy őszintén", "hu"),  # угорські довгі голосні
        ("Hello world", "en"),  # чиста латиниця
    ],
)
def test_detect_picks_expected_language(text: str, expected: str) -> None:
    assert detect(text) == expected


def test_make_key_is_stable_and_text_sensitive() -> None:
    a = make_key("ns", "mock", "uk", "cs", "Привіт")
    b = make_key("ns", "mock", "uk", "cs", "Привіт")
    c = make_key("ns", "mock", "uk", "cs", "Бувай")
    assert a == b
    assert a != c


def test_engine_translates_multiple_sentences() -> None:
    engine = MockEngine()
    out = engine.translate("Перше речення. Друге речення!", "uk", "en")
    # Обидва речення пройшли через рушій окремо.
    assert out.count("[uk->en]") == 2


def test_engine_passthrough_when_source_equals_target() -> None:
    engine = MockEngine()
    assert engine.translate("text", "en", "en") == "text"
