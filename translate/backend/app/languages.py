"""Реєстр підтримуваних мов — SSOT для кодів та назв.

Канонічний код = ISO 639-1 (uk, en, ru, cs, hu). Кожен рушій мапить
канонічний код у свій формат (NLLB -> FLORES-200, Argos -> ISO).
Фронтенд оперує тільки канонічними кодами.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str  # канонічний ISO 639-1
    name_en: str
    name_native: str
    flores: str  # код FLORES-200 для NLLB


# Стартовий набір під цілі проєкту: UA<->CS, UA<->HU + en/ru як містки.
LANGUAGES: tuple[Language, ...] = (
    Language("uk", "Ukrainian", "Українська", "ukr_Cyrl"),
    Language("en", "English", "English", "eng_Latn"),
    Language("cs", "Czech", "Čeština", "ces_Latn"),
    Language("hu", "Hungarian", "Magyar", "hun_Latn"),
    Language("ru", "Russian", "Русский", "rus_Cyrl"),
)

_BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}

# Спеціальний код автовизначення джерела.
AUTO = "auto"


def is_supported(code: str) -> bool:
    return code in _BY_CODE


def get(code: str) -> Language:
    try:
        return _BY_CODE[code]
    except KeyError as exc:  # явна, гучна помилка замість тихого падіння
        raise KeyError(f"Непідтримувана мова: {code!r}") from exc


def flores(code: str) -> str:
    return get(code).flores


def all_codes() -> list[str]:
    return [lang.code for lang in LANGUAGES]
