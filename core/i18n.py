"""Перекладач текстів, які складає сервер (ADR-0116 Ф2.1): розбір, наратив, профіль, картка стадії, сповіщення.

Ключ перекладу — сам український текст (L1). Мова-джерело словника не має, її перекладач `SOURCE_TRANSLATOR` —
тотожність. Тож виклик без мови дає рівно ті самі байти, що й до мовного шару (L4: Арчі, `/api/*`, golden). Словники
інших мов — дані в `i18n/` у корені репо, спільні з UI (§4.2). Читає їх `runtime/i18n_catalog.py` і передає сюди
готовим відображенням. S0: pure, без I/O.

Модулі тексту отримують перекладач параметром `tr` і викликають `tr("Стоп {price}", price=...)` або
`tr.plural(n, "{n} тиждень|{n} тижні|{n} тижнів")`. Ключ — лише літерал: його бачить тест паритету
(`tests/test_i18n_parity.py`). Тексти з мап за кодом позначаються `msg("…")` і перекладаються в мить показу.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Dict, Mapping, Optional, Set, Tuple, Union

_log = logging.getLogger(__name__)

SOURCE_LANG = "uk"  # = `source` у i18n/languages.json (звіряє тест паритету)
SOURCE_PLURAL_ORDER = ("one", "few", "many")  # порядок форм у ключі множини = `plural` джерела в реєстрі
PLURAL_SEPARATOR = "|"

Message = Union[str, Mapping[str, str]]  # рядок або форми множини за категоріями CLDR мови перекладу

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _east_slavic(n: int) -> str:
    """CLDR uk / ru для цілих: 1, 21 — one; 2–4, 22–24 — few; решта (і 11–14) — many."""
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


def _one_other(n: int) -> str:
    return "one" if n == 1 else "other"


# Правило множини кожної зареєстрованої мови. Нова мова без правила — відмова в Translator, гучно (§5.1)
PLURAL_RULES: Mapping[str, Callable[[int], str]] = {"uk": _east_slavic, "ru": _east_slavic, "en": _one_other}


def fill(template: str, params: Mapping[str, object]) -> str:
    """`{name}` → значення параметра. Плейсхолдер без параметра лишається видимим, а не зникає тихо."""
    if not params:
        return template
    return _PLACEHOLDER.sub(lambda match: str(params[match.group(1)]) if match.group(1) in params else match.group(0),
                            template)


def msg(key: str) -> str:
    """Позначка ключа для тексту з мапи за кодом: тест паритету його бачить, перекладає `tr()` у мить показу."""
    return key


class Translator:
    """Тексти однієї мови. `messages=None` — мова-джерело: ключ і є текст."""

    __slots__ = ("lang", "_messages", "_reported")

    def __init__(self, lang: str, messages: Optional[Mapping[str, Message]]) -> None:
        if lang not in PLURAL_RULES:
            raise ValueError("мова %r без правила множини в core.i18n.PLURAL_RULES (ADR-0116 §5.1)" % lang)
        self.lang = lang
        self._messages = messages
        self._reported: Set[str] = set()

    def __call__(self, key: str, **params: object) -> str:
        if self._messages is None:
            return fill(key, params)
        message = self._messages.get(key)
        if isinstance(message, str):
            return fill(message, params)
        self._report_missing(key)
        return fill(key, params)

    def plural(self, n: int, forms: str, **params: object) -> str:
        """Форма множини для `n` (параметр `{n}` додається сам); ключ — форми джерела через «|»."""
        values: Dict[str, object] = dict(params, n=n)
        message = None if self._messages is None else self._messages.get(forms)
        if isinstance(message, Mapping):
            form = message.get(PLURAL_RULES[self.lang](n))
            if form is not None:
                return fill(form, values)
        if self._messages is not None:
            self._report_missing(forms)
        return fill(_source_form(n, forms), values)

    def _report_missing(self, key: str) -> None:
        """L3: перекладу нема — показ джерела і WARNING раз на ключ (тест паритету не мав би цього пропустити)."""
        if key in self._reported:
            return
        self._reported.add(key)
        _log.warning("I18N_MISSING lang=%s key=%r — показано джерело (ADR-0116 L3)", self.lang, key)


def _source_form(n: int, forms: str) -> str:
    variants: Tuple[str, ...] = tuple(forms.split(PLURAL_SEPARATOR))
    index = SOURCE_PLURAL_ORDER.index(PLURAL_RULES[SOURCE_LANG](n))
    return variants[index] if index < len(variants) else variants[-1]


SOURCE_TRANSLATOR = Translator(SOURCE_LANG, None)
