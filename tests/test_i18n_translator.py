"""Серверний перекладач і читання словників (ADR-0116 Ф2.1): джерело — тотожність, переклад, множина, гучні збої."""
from __future__ import annotations

import json
import logging
import pathlib
import shutil

import pytest

from core.i18n import SOURCE_TRANSLATOR, Translator, fill, msg
from runtime.i18n_catalog import I18N_DIR, load_catalog

WEEKS = "{n} тиждень|{n} тижні|{n} тижнів"


def test_source_translator_returns_source_text_byte_for_byte():
    """L4: без мови — рівно ті самі байти, що й до мовного шару (Арчі, /api/*, golden)."""
    assert SOURCE_TRANSLATOR("Стоп {price} · ціль {target}", price="4380.00", target="PDL 4310.50") \
        == "Стоп 4380.00 · ціль PDL 4310.50"
    assert SOURCE_TRANSLATOR("Ринок закрито — розбір оновиться з відкриттям") \
        == "Ринок закрито — розбір оновиться з відкриттям"


def test_translation_fills_named_placeholders():
    en = Translator("en", {"{tf}: кілзона активна": "{tf}: killzone active"})
    assert en("{tf}: кілзона активна", tf="H1") == "H1: killzone active"


def test_missing_translation_shows_source_and_warns_once(caplog):
    en = Translator("en", {})
    with caplog.at_level(logging.WARNING, logger="core.i18n"):
        assert en("Ринок закрито") == "Ринок закрито"
        assert en("Ринок закрито") == "Ринок закрито"
    assert [r.message.count("I18N_MISSING") for r in caplog.records] == [1]


def test_unknown_placeholder_stays_visible():
    assert fill("Стоп {price} · ціль {target}", {"price": "1.0"}) == "Стоп 1.0 · ціль {target}"


def test_source_plural_east_slavic_forms():
    forms = [SOURCE_TRANSLATOR.plural(n, WEEKS) for n in (1, 3, 5, 11, 12, 21, 24, 111)]
    assert forms == ["1 тиждень", "3 тижні", "5 тижнів", "11 тижнів", "12 тижнів", "21 тиждень", "24 тижні",
                     "111 тижнів"]


def test_plural_translation_uses_target_language_rule():
    en = Translator("en", {WEEKS: {"one": "{n} week", "other": "{n} weeks"}})
    ru = Translator("ru", {WEEKS: {"one": "{n} неделя", "few": "{n} недели", "many": "{n} недель"}})
    assert [en.plural(n, WEEKS) for n in (1, 2)] == ["1 week", "2 weeks"]
    assert [ru.plural(n, WEEKS) for n in (1, 2, 5, 21)] == ["1 неделя", "2 недели", "5 недель", "21 неделя"]


def test_missing_plural_shows_source_form(caplog):
    with caplog.at_level(logging.WARNING, logger="core.i18n"):
        assert Translator("en", {}).plural(2, WEEKS) == "2 тижні"
    assert "I18N_MISSING" in caplog.text


def test_language_without_plural_rule_is_refused():
    with pytest.raises(ValueError, match="правила множини"):
        Translator("de", {})


def test_msg_is_identity_marker():
    assert msg("Азія") == "Азія"


# ── Читання словників (runtime/i18n_catalog.py) ─────────────


def test_catalog_reads_every_registered_language():
    catalog = load_catalog()
    assert catalog.languages == ("uk", "en", "ru")
    assert catalog.get("uk") is SOURCE_TRANSLATOR
    assert catalog.get("en")("Мова") == "Language"
    assert catalog.get("ru")("Мова") == "Язык"
    assert catalog.get("de") is None


@pytest.fixture
def i18n_copy(tmp_path):
    target = tmp_path / "i18n"
    shutil.copytree(I18N_DIR, target)
    return target


def test_broken_dictionary_drops_only_that_language_loudly(i18n_copy, caplog):
    (i18n_copy / "ru.json").write_text("{ не json", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="runtime.i18n_catalog"):
        catalog = load_catalog(i18n_copy)
    assert catalog.languages == ("uk", "en")
    assert "I18N_DICTIONARY_UNAVAILABLE lang=ru" in caplog.text


def test_dictionary_with_wrong_value_shape_is_refused(i18n_copy, caplog):
    (i18n_copy / "en.json").write_text(json.dumps({"Мова": ["Language"]}), encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="runtime.i18n_catalog"):
        assert load_catalog(i18n_copy).get("en") is None
    assert "I18N_DICTIONARY_UNAVAILABLE lang=en" in caplog.text


def test_registry_with_other_source_refuses_start(i18n_copy):
    registry = json.loads((i18n_copy / "languages.json").read_text(encoding="utf-8"))
    registry["source"] = "en"
    (i18n_copy / "languages.json").write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match="джерело"):
        load_catalog(i18n_copy)


def test_registry_with_other_source_plural_order_refuses_start(i18n_copy):
    registry = json.loads((i18n_copy / "languages.json").read_text(encoding="utf-8"))
    registry["languages"][0]["plural"] = ["many", "few", "one"]
    (i18n_copy / "languages.json").write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match="порядок форм"):
        load_catalog(i18n_copy)


def test_i18n_dir_is_repo_root_folder():
    assert I18N_DIR == pathlib.Path(__file__).resolve().parents[1] / "i18n"
