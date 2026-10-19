"""Словники мов сервера (ADR-0116 Ф2.1): `i18n/languages.json` і `i18n/<код>.json` → перекладачі `core.i18n`.

Читаються раз на старті процесу (ws_server, а в Ф3 — і процес сповіщень). Межа помилок (§5.4):
- кривий реєстр або джерело в ньому не те, що знає `core.i18n`, — відмова старту: без реєстру мовного шару нема;
- словник однієї мови не читається, має не той вигляд чи мова без правила множини в `core.i18n` — ERROR у лозі,
  мови нема серед доступних, процес стартує.
  Клієнт, що попросить цю мову, отримає джерело з `lang_unsupported` (Ф2.2).
Окремий модуль, бо це межа I/O: `core.i18n` лишається pure (S0) і отримує готове відображення.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from core.i18n import SOURCE_LANG, SOURCE_PLURAL_ORDER, SOURCE_TRANSLATOR, Translator

_log = logging.getLogger(__name__)

I18N_DIR = Path(__file__).resolve().parents[1] / "i18n"
REGISTRY_FILE = "languages.json"


@dataclasses.dataclass(frozen=True)
class LanguageCatalog:
    """Перекладачі мов, що прочиталися; джерело є завжди."""

    translators: Mapping[str, Translator]

    @property
    def languages(self) -> Tuple[str, ...]:
        return tuple(self.translators)

    def get(self, lang: str) -> Optional[Translator]:
        """Перекладач зареєстрованої мови, що прочиталася; None — такої нема (рішення про фолбек — у викликача)."""
        return self.translators.get(lang)


def load_catalog(i18n_dir: Path = I18N_DIR) -> LanguageCatalog:
    registry = _read_registry(i18n_dir / REGISTRY_FILE)
    translators = {SOURCE_LANG: SOURCE_TRANSLATOR}
    for language in registry["languages"]:
        code = language["code"]
        if code == SOURCE_LANG:
            continue
        translator = _load_language(i18n_dir / ("%s.json" % code), code)
        if translator is not None:
            translators[code] = translator
    _log.info("I18N_CATALOG languages=%s", ",".join(translators))
    return LanguageCatalog(translators)


def _read_registry(path: Path) -> Mapping[str, Any]:
    registry = json.loads(path.read_text(encoding="utf-8"))
    source = next((language for language in registry.get("languages", ())
                   if language.get("code") == registry.get("source")), None)
    if registry.get("source") != SOURCE_LANG or source is None:
        raise ValueError("%s: джерело %r, а core.i18n знає %r" % (path, registry.get("source"), SOURCE_LANG))
    if tuple(source.get("plural", ())) != SOURCE_PLURAL_ORDER:
        raise ValueError("%s: порядок форм джерела %r ≠ core.i18n %r" % (path, source.get("plural"), SOURCE_PLURAL_ORDER))
    return registry


def _load_language(path: Path, code: str) -> Optional[Translator]:
    try:
        messages = json.loads(path.read_text(encoding="utf-8"))
        _check_shape(messages)
        return Translator(code, messages)
    except (OSError, ValueError) as exc:  # json.JSONDecodeError — теж ValueError
        _log.error("I18N_DICTIONARY_UNAVAILABLE lang=%s path=%s err=%s — мова недоступна (ADR-0116 §5.4)", code, path, exc)
        return None


def _check_shape(messages: Any) -> None:
    """Значення — рядок або форми множини (рядки за категоріями); інше — словник не приймається цілим."""
    if not isinstance(messages, dict):
        raise ValueError("словник має бути об'єктом ключ → переклад")
    for key, message in messages.items():
        if isinstance(message, str):
            continue
        if not (isinstance(message, dict) and message and all(isinstance(form, str) for form in message.values())):
            raise ValueError("ключ %r: переклад — рядок або форми множини рядками" % key)

