"""Паритет словників мовного шару (ADR-0116 L2): у кожній мові рівно ключі джерела — без пропущених і без зайвих.

Ключ перекладу — сам український текст (L1), тож ключі бере не окремий список, а код: у `ui_v4/src` — виклики
`t("…")`, `tn(n, "…")` і `msg("…")` у файлах, що імпортують мовний шар; у `core/` і `runtime/` — `tr("…")`,
`tr.plural(n, "…")` і `msg("…")` у модулях, що імпортують `core.i18n`. Словник на мову один для UI й сервера (§4.2),
тож і перелік ключів спільний. Правка української в коді міняє ключ, і тест падає, доки en/ru не оновлено тим самим
комітом. Старий переклад не лишається тихо.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
from typing import Dict, List

import pytest

from core.i18n import PLURAL_RULES, SOURCE_LANG, SOURCE_PLURAL_ORDER

ROOT = pathlib.Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "i18n"
UI_SRC = ROOT / "ui_v4" / "src"
PY_ROOTS = (ROOT / "core", ROOT / "runtime")
REGISTRY = json.loads((I18N_DIR / "languages.json").read_text(encoding="utf-8"))
LANGUAGES = {language["code"]: language for language in REGISTRY["languages"]}
SOURCE = LANGUAGES[REGISTRY["source"]]
TRANSLATED = sorted(code for code in LANGUAGES if code != SOURCE["code"])
CLDR_CATEGORIES = {"zero", "one", "two", "few", "many", "other"}
PLURAL_SEPARATOR = "|"

_I18N_IMPORT = re.compile(r"""from\s+['"][^'"]*lib/i18n/i18n\.svelte['"]""")
_PY_I18N_IMPORT = re.compile(r"^\s*(?:from\s+core\.i18n\s+import|import\s+core\.i18n)", re.M)
_STRING = r"""(['"])((?:\\.|(?!\1).)*)\1"""
_TEXT_CALL = re.compile(r"\b(?:t|msg)\(\s*" + _STRING)
_PLURAL_CALL = re.compile(r"\btn\([^,()]*,\s*" + _STRING)
_TEMPLATE_KEY = re.compile(r"\b(?:t|tn|msg)\([^`()]*`")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_ESCAPE = re.compile(r"\\(.)")
_ESCAPED = {"n": "\n", "t": "\t", "'": "'", '"': '"', "\\": "\\"}


def _unescape(literal: str) -> str:
    def _replace(match: "re.Match[str]") -> str:
        char = match.group(1)
        if char not in _ESCAPED:
            raise ValueError("непідтримана escape-послідовність \\%s у ключі %r" % (char, literal))
        return _ESCAPED[char]

    return _ESCAPE.sub(_replace, literal)


def _ui_files() -> List[pathlib.Path]:
    """Файли UI, що імпортують мовний шар; тести й сам шар — ні (у його коментарях є зразки викликів)."""
    files = []
    for path in sorted(UI_SRC.rglob("*")):
        if path.suffix not in (".ts", ".svelte") or ".test." in path.name:
            continue
        if _I18N_IMPORT.search(path.read_text(encoding="utf-8")):
            files.append(path)
    return files


def _py_files() -> List[pathlib.Path]:
    """Модулі сервера, що імпортують перекладач; сам `core/i18n.py` — ні (у docstring зразки викликів)."""
    files = []
    for root in PY_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if path.name != "i18n.py" and _PY_I18N_IMPORT.search(path.read_text(encoding="utf-8")):
                files.append(path)
    return files


def _ui_keys(path: pathlib.Path) -> List[tuple]:
    code = path.read_text(encoding="utf-8")
    found = [(_unescape(m.group(2)), "text") for m in _TEXT_CALL.finditer(code)]
    return found + [(_unescape(m.group(2)), "plural") for m in _PLURAL_CALL.finditer(code)]


def _py_keys(path: pathlib.Path) -> List[tuple]:
    """`tr("…")` / `msg("…")` — текст, `tr.plural(n, "…")` — множина; ключ не літералом — помилка тесту."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("tr", "msg"):
            kind, key_arg = "text", node.args[0] if node.args else None
        elif (isinstance(func, ast.Attribute) and func.attr == "plural"
              and isinstance(func.value, ast.Name) and func.value.id == "tr"):
            kind, key_arg = "plural", node.args[1] if len(node.args) > 1 else None
        else:
            continue
        if isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str):
            found.append((key_arg.value, kind))
            continue
        # Не літерал дозволено лише в tr(МАПА[код]): ключі мапи вже позначено msg("…") і їх тут видно
        mapped_lookup = isinstance(func, ast.Name) and func.id == "tr"
        assert mapped_lookup, "%s:%d — ключ msg()/tr.plural() має бути літералом" % (path.name, node.lineno)
    return found


def _source_keys() -> Dict[str, str]:
    """Ключ → вид: «text» або «plural» — з UI і сервера разом. Один ключ двох видів — помилка коду, не словника."""
    keys: Dict[str, str] = {}
    sources = [(path, _ui_keys(path)) for path in _ui_files()] + [(path, _py_keys(path)) for path in _py_files()]
    for path, found in sources:
        for key, kind in found:
            assert keys.setdefault(key, kind) == kind, "%s: ключ %r і як текст, і як множина" % (path.name, key)
    return keys


def _dictionary(code: str) -> dict:
    return json.loads((I18N_DIR / ("%s.json" % code)).read_text(encoding="utf-8"))


def _placeholders(text: str) -> set:
    return set(_PLACEHOLDER.findall(text))


def test_registry_is_consistent():
    codes = [language["code"] for language in REGISTRY["languages"]]
    assert len(codes) == len(set(codes)), "код мови двічі в реєстрі"
    assert REGISTRY["source"] in LANGUAGES and REGISTRY["fallback"] in LANGUAGES
    for language in REGISTRY["languages"]:
        assert language["name"].strip(), "мова %s без назви для перемикача" % language["code"]
        assert language["plural"] and set(language["plural"]) <= CLDR_CATEGORIES, language["code"]


def test_core_translator_matches_registry():
    """Сервер знає те саме джерело й порядок форм, що реєстр; кожна мова має правило множини з її категоріями."""
    assert SOURCE["code"] == SOURCE_LANG and tuple(SOURCE["plural"]) == SOURCE_PLURAL_ORDER
    for code, language in LANGUAGES.items():
        assert code in PLURAL_RULES, "мова %s без правила множини в core.i18n.PLURAL_RULES" % code
        produced = {PLURAL_RULES[code](n) for n in range(0, 1000)}
        assert produced == set(language["plural"]), "%s: правило дає %s, реєстр каже %s" % (
            code, sorted(produced), language["plural"])


def test_every_translated_language_has_exactly_one_dictionary():
    """Мова-джерело словника не має (тотожність, L1); кожна інша — рівно файл `i18n/<код>.json`."""
    files = {path.stem for path in I18N_DIR.glob("*.json")} - {"languages"}
    assert files == set(TRANSLATED)


def test_keys_are_quoted_literals():
    offenders = [path.name for path in _ui_files() if _TEMPLATE_KEY.search(path.read_text(encoding="utf-8"))]
    assert not offenders, "ключ перекладу в `…`-рядку тест не бачить — лише лапки: %s" % offenders


def test_source_plural_keys_have_one_form_per_category():
    for key, kind in _source_keys().items():
        if kind == "plural":
            assert len(key.split(PLURAL_SEPARATOR)) == len(SOURCE["plural"]), key


@pytest.mark.parametrize("code", TRANSLATED)
def test_dictionary_has_exactly_source_keys(code):
    expected, actual = set(_source_keys()), set(_dictionary(code))
    assert not expected - actual, "%s: бракує перекладу %s" % (code, sorted(expected - actual))
    assert not actual - expected, "%s: зайві ключі (текст джерела змінився?) %s" % (code, sorted(actual - expected))


@pytest.mark.parametrize("code", TRANSLATED)
def test_translation_shape_and_placeholders_match_source(code):
    categories = set(LANGUAGES[code]["plural"])
    for key, kind in _source_keys().items():
        message = _dictionary(code).get(key)
        if message is None:
            continue  # пропуск ловить test_dictionary_has_exactly_source_keys
        if kind == "text":
            assert isinstance(message, str), "%s: %r — очікувався рядок" % (code, key)
            assert _placeholders(message) == _placeholders(key), "%s: плейсхолдери %r" % (code, key)
            continue
        assert isinstance(message, dict) and set(message) == categories, "%s: форми множини %r" % (code, key)
        for form in message.values():
            assert _placeholders(form) <= _placeholders(key) | {"n"}, "%s: плейсхолдери множини %r" % (code, key)


def test_python_extractor_reads_literal_keys(tmp_path):
    module = tmp_path / "sample.py"
    module.write_text("\n".join((
        "from core.i18n import msg",
        "NAMES = {'asia': msg('Азія')}",
        "def row(tr, n, name):",
        "    return tr('Стоп {price}', price=1) + tr.plural(n, '{n} тиждень|{n} тижні|{n} тижнів') + tr(NAMES[name])",
    )), encoding="utf-8")
    assert sorted(_py_keys(module)) == [("{n} тиждень|{n} тижні|{n} тижнів", "plural"), ("Азія", "text"),
                                        ("Стоп {price}", "text")]


def test_extractor_reads_keys_like_the_ui_writes_them():
    sample = "t(\"під'єднано\"); msg('Код \\'А\\''); tn(count, '{n} тиждень|{n} тижні|{n} тижнів', { x });"
    texts = [_unescape(m.group(2)) for m in _TEXT_CALL.finditer(sample)]
    plurals = [_unescape(m.group(2)) for m in _PLURAL_CALL.finditer(sample)]
    assert texts == ["під'єднано", "Код 'А'"]
    assert plurals == ["{n} тиждень|{n} тижні|{n} тижнів"]
