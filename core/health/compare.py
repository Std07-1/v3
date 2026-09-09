"""core/health/compare — регресія між двома звітами health-check (ADR-0054 §3.4).

Процедура активації символу вимагає: зняти baseline на активних символах ДО зміни
config, повторити ПІСЛЯ і відкотитись при **будь-якому погіршенні** на вже активному
символі. Досі це порівняння робилось ad-hoc скриптом на кожну активацію — тобто
критерій «погіршення» щоразу винаходився наново і ніде не перевірявся тестом.

Тут він один і чистий. Порівнюються лише виміри, де «більше = гірше» або де втрата
даних однозначна; нові символи й нові TF ігноруються (їх у baseline не було за
побудовою — це і є мета активації, а не регресія).

Асиметрія навмисна: ми ловимо погіршення, а покращення (дірку закрито, дублікат знято)
лише повідомляємо. Активація не має права зробити гірше; зробити краще — може.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_GRADE_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}

# (шлях у звіті TF, людська назва). Усі — «більше = гірше».
_WORSE_IF_UP: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("holes", "missing"), "дірок"),
    (("age_buckets",), "вік (бакетів)"),
    (("geometry", "dup_conflicting"), "конфліктних дублікатів"),
    (("geometry", "align_bad"), "невирівняних"),
    (("geometry", "close_bad"), "хибних close"),
    (("geometry", "ohlc_bad"), "хибних OHLC"),
    (("cascade", "mismatched"), "мовчазних розбіжностей каскаду"),
)


@dataclasses.dataclass(frozen=True)
class Regression:
    """Одне погіршення на конкретному (symbol, tf)."""

    symbol: str
    tf: str
    measure: str
    before: Any
    after: Any

    def describe(self) -> str:
        return "%s tf_%s: %s %s -> %s" % (self.symbol, self.tf, self.measure, self.before, self.after)


@dataclasses.dataclass(frozen=True)
class CompareResult:
    regressions: List[Regression]
    improvements: List[Regression]
    new_symbols: List[str]
    missing_symbols: List[str]
    compared_symbols: List[str]

    @property
    def ok(self) -> bool:
        """Регресій немає. Зниклий символ — теж провал: дані активного символу пропали."""
        return not self.regressions and not self.missing_symbols


def _num(node: Optional[Mapping[str, Any]], path: Sequence[str]) -> Optional[float]:
    """Дістати число за шляхом; None якщо гілки немає або значення не число."""
    cur: Any = node
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        return None
    return float(cur)


def compare_reports(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    only_symbols: Optional[Sequence[str]] = None,
) -> CompareResult:
    """Порівняти два звіти ``symbol_health_check``.

    ``only_symbols`` обмежує перевірку (напр. лише вже активні символи — новий
    символ на момент POST ще не має історії й дасть шум).
    """
    before_syms: Dict[str, Any] = dict(before.get("symbols") or {})
    after_syms: Dict[str, Any] = dict(after.get("symbols") or {})

    gate = set(only_symbols) if only_symbols else set(before_syms)
    regressions: List[Regression] = []
    improvements: List[Regression] = []

    for symbol in sorted(gate & set(before_syms)):
        b_sym = before_syms[symbol]
        a_sym = after_syms.get(symbol)
        if a_sym is None:
            continue  # зафіксовано окремо як missing_symbols

        b_grade, a_grade = b_sym.get("grade"), a_sym.get("grade")
        if _GRADE_RANK.get(str(a_grade), -1) > _GRADE_RANK.get(str(b_grade), -1):
            regressions.append(Regression(symbol, "-", "вердикт символу", b_grade, a_grade))

        b_tfs: Dict[str, Any] = dict(b_sym.get("tfs") or {})
        a_tfs: Dict[str, Any] = dict(a_sym.get("tfs") or {})
        for tf in sorted(b_tfs, key=lambda x: int(x) if str(x).isdigit() else 0):
            b_tf, a_tf = b_tfs[tf], a_tfs.get(tf)
            if a_tf is None:
                regressions.append(Regression(symbol, tf, "TF зник зі звіту", "є", "немає"))
                continue

            if _GRADE_RANK.get(str(a_tf.get("grade")), -1) > _GRADE_RANK.get(str(b_tf.get("grade")), -1):
                regressions.append(Regression(symbol, tf, "вердикт", b_tf.get("grade"), a_tf.get("grade")))

            # Втрата барів — завжди регресія: активація не має права стирати історію.
            b_bars, a_bars = _num(b_tf, ("bars",)), _num(a_tf, ("bars",))
            if b_bars is not None and a_bars is not None and a_bars < b_bars:
                regressions.append(Regression(symbol, tf, "барів", int(b_bars), int(a_bars)))

            for path, label in _WORSE_IF_UP:
                b_val, a_val = _num(b_tf, path), _num(a_tf, path)
                if b_val is None or a_val is None:
                    continue
                if a_val > b_val:
                    regressions.append(Regression(symbol, tf, label, _as_int(b_val), _as_int(a_val)))
                elif a_val < b_val:
                    improvements.append(Regression(symbol, tf, label, _as_int(b_val), _as_int(a_val)))

    return CompareResult(
        regressions=regressions,
        improvements=improvements,
        new_symbols=sorted(set(after_syms) - set(before_syms)),
        missing_symbols=sorted((gate & set(before_syms)) - set(after_syms)),
        compared_symbols=sorted(gate & set(before_syms) & set(after_syms)),
    )


def _as_int(value: float) -> Any:
    return int(value) if float(value).is_integer() else value
