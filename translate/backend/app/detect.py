"""Легке евристичне визначення мови джерела (без важких залежностей).

Достатнє для нашого набору (uk/ru/cs/hu/en). Працює за алфавітом та
характерними літерами. Не претендує на ідеал — це best-effort для режиму
"auto"; за невпевненості повертає найімовірніший кандидат і ніколи не падає.
"""

from __future__ import annotations

# Літери, унікальні (де-факто) для української серед кирилиці.
_UK_MARKERS = set("іїєґ")
# Характерні чеські діакритики.
_CS_MARKERS = set("ěščřžůň")
# Характерні угорські діакритики (довгі голосні).
_HU_MARKERS = set("őűáéíóúö")


def _has_cyrillic(text: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def detect(text: str) -> str:
    """Повертає канонічний ISO-код мови. Дефолт 'en' для латиниці без ознак."""
    low = text.lower()

    if _has_cyrillic(low):
        if _UK_MARKERS & set(low):
            return "uk"
        return "ru"

    # Латиниця: розрізняємо cs/hu за діакритикою, інакше en.
    cs_hits = len(_CS_MARKERS & set(low))
    hu_hits = len(_HU_MARKERS & set(low))
    if cs_hits or hu_hits:
        return "cs" if cs_hits >= hu_hits else "hu"
    return "en"
