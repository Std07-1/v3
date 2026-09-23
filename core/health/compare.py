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
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from core.session_anchor import D1_S, H4_S

_GRADE_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}

# Версія СЕМАНТИКИ виміру. Вердикт звіту залежить від того, що саме міряли: версія 2 (ADR-0094 P4)
# дедуплікує батьків і дітей як читачі та додала перевірку кожного derived-бару проти M1. Через це
# символ, який v1 вважав GREEN, під v2 чесно стає RED (SPX500: 6 барів мовчки ≠ M1) — хоча дані не
# змінились. Порівняти вердикти різних версій = хибна «регресія» і хибний відкат активації.
# Версія 3 (ADR-0095 S5a): H4/D1 міряються рівністю сезонній сітці (`off_season_grid`, RED), а не
# членством у наборі якорів з DST-альтернативами; очікувані бакети, вік і дірки — ітератором сітки.
# Літній H4 на 22:00 під v2 був легальним «alt», під v3 — RED, хоча дані ті самі.
# Версія 4 (ADR-0101 C4): M1 міряє розриви суцільного ланцюга (`chain_breaks`), розрив без діри — YELLOW. Символ,
# GREEN під v3, під v4 чесно YELLOW на тих самих даних (скан проду 23.09.2026, ADR-0101 §1.1: розриви в історії M1
# є на XAU, XAG, NAS100, US30).
# Версія 5 (ADR-0095 S6a): торговість хвилини — сезонний календар групи (`calendar_for_symbol`), а не плоскі поля,
# що дорівнюють літньому розкладу. Узимку XAU перерва 22:00–23:00, а не 21:00–22:00: зимове відкриття сесії, яке v4
# бачило розривом на межі діри (`at_gap`), під v5 — розрив без діри (`inner`), дірки й вік зимових вікон теж інші.
HEALTH_MEASURE_VERSION = 5

# Сіткові числа H4/D1 (дірки, вік, вирівнювання, каскад, корінь) до v3 рахувались на іншій сітці, тож через межу
# v3 вони не «погіршуються», а міряють інше: v2-baseline XAU H4 «дірок» 8 → v3 131 на тих самих даних (23.09.2026).
# Їх не порівнюємо, як і вердикти. Дублікати, close і OHLC сітки не торкаються (той самий код у v2 і v3) — вони
# порівнюються й через межу: саме тоді ще видно регресію, внесену самою зміною (S7 переписує H4/D1), а baseline,
# знятий після, її вже не побачить. M1..H1 сітки не міняли — їхні числа порівнюються всі.
_SEASONAL_GRID_VERSION = 3
_SEASONAL_GRID_TFS = frozenset({str(H4_S), str(D1_S)})  # ключі TF у звіті — рядки

# Виміри, що питають календар про торговість хвилини (дірки, вік, класифікація розриву ланцюга), через межу v5
# міряють за іншим розкладом на всіх TF: v4-baseline з літнім розкладом узимку дав би хибний відкат (зимові
# відкриття сесії XAU: `at_gap` → `inner`). Їх не порівнюємо, решту — так.
_SEASONAL_CALENDAR_VERSION = 5


class _Measure(NamedTuple):
    """Числовий вимір звіту TF, де «більше = гірше»."""

    path: Tuple[str, ...]
    label: str
    grid_dependent: bool  # число H4/D1 рахується на сітці бакетів: через межу v3 міряє інше
    calendar_dependent: bool  # число залежить від торговості хвилини: через межу v5 міряє інше


_WORSE_IF_UP: Tuple[_Measure, ...] = (
    _Measure(("holes", "missing"), "дірок", grid_dependent=True, calendar_dependent=True),
    _Measure(("age_buckets",), "вік (бакетів)", grid_dependent=True, calendar_dependent=True),
    _Measure(("geometry", "dup_conflicting"), "конфліктних дублікатів", grid_dependent=False, calendar_dependent=False),
    _Measure(("geometry", "align_bad"), "невирівняних", grid_dependent=True, calendar_dependent=False),
    _Measure(("geometry", "off_season_grid"), "барів поза сезонною сіткою", grid_dependent=True,
             calendar_dependent=False),
    _Measure(("geometry", "close_bad"), "хибних close", grid_dependent=False, calendar_dependent=False),
    _Measure(("geometry", "ohlc_bad"), "хибних OHLC", grid_dependent=False, calendar_dependent=False),
    _Measure(("cascade", "mismatched"), "мовчазних розбіжностей каскаду", grid_dependent=True,
             calendar_dependent=False),
    _Measure(("root", "mismatched"), "мовчазних розбіжностей з M1", grid_dependent=True, calendar_dependent=False),
    # Лише M1 і лише з v4; baseline без поля пропускає вимір, а не дає хибну регресію
    _Measure(("chain_breaks", "inner"), "розривів ланцюга без діри", grid_dependent=False, calendar_dependent=True),
    _Measure(("chain_breaks", "at_gap"), "розривів ланцюга на межі діри", grid_dependent=False,
             calendar_dependent=True),
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
    measure_versions: Tuple[int, int] = (HEALTH_MEASURE_VERSION, HEALTH_MEASURE_VERSION)
    # Виміри H4/D1, які через межу v3 не порівнювались (інша сітка); порожньо — порівнювалось усе.
    grid_skipped_measures: Tuple[str, ...] = ()
    # Виміри всіх TF, які через межу v5 не порівнювались (інший календар торговості); порожньо — порівнювалось усе.
    calendar_skipped_measures: Tuple[str, ...] = ()

    @property
    def verdicts_comparable(self) -> bool:
        """Вердикти порівнювались лише якщо обидва звіти зняті однією версією виміру."""
        return self.measure_versions[0] == self.measure_versions[1]

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
    # Звіт без поля — знятий до появи версіонування, тобто семантикою v1.
    versions = (int(before.get("measure_version", 1)), int(after.get("measure_version", 1)))
    compare_verdicts = versions[0] == versions[1]
    grid_changed = min(versions) < _SEASONAL_GRID_VERSION <= max(versions)
    calendar_changed = min(versions) < _SEASONAL_CALENDAR_VERSION <= max(versions)
    regressions: List[Regression] = []
    improvements: List[Regression] = []

    for symbol in sorted(gate & set(before_syms)):
        b_sym = before_syms[symbol]
        a_sym = after_syms.get(symbol)
        if a_sym is None:
            continue  # зафіксовано окремо як missing_symbols

        b_grade, a_grade = b_sym.get("grade"), a_sym.get("grade")
        if compare_verdicts and _GRADE_RANK.get(str(a_grade), -1) > _GRADE_RANK.get(str(b_grade), -1):
            regressions.append(Regression(symbol, "-", "вердикт символу", b_grade, a_grade))

        b_tfs: Dict[str, Any] = dict(b_sym.get("tfs") or {})
        a_tfs: Dict[str, Any] = dict(a_sym.get("tfs") or {})
        for tf in sorted(b_tfs, key=lambda x: int(x) if str(x).isdigit() else 0):
            b_tf, a_tf = b_tfs[tf], a_tfs.get(tf)
            if a_tf is None:
                regressions.append(Regression(symbol, tf, "TF зник зі звіту", "є", "немає"))
                continue

            if compare_verdicts and _GRADE_RANK.get(str(a_tf.get("grade")), -1) > _GRADE_RANK.get(str(b_tf.get("grade")), -1):
                regressions.append(Regression(symbol, tf, "вердикт", b_tf.get("grade"), a_tf.get("grade")))

            # Втрата барів — завжди регресія: активація не має права стирати історію.
            b_bars, a_bars = _num(b_tf, ("bars",)), _num(a_tf, ("bars",))
            if b_bars is not None and a_bars is not None and a_bars < b_bars:
                regressions.append(Regression(symbol, tf, "барів", int(b_bars), int(a_bars)))

            skip_grid = grid_changed and str(tf) in _SEASONAL_GRID_TFS
            for measure in _WORSE_IF_UP:
                if (skip_grid and measure.grid_dependent) or (calendar_changed and measure.calendar_dependent):
                    continue
                b_val, a_val = _num(b_tf, measure.path), _num(a_tf, measure.path)
                if b_val is None or a_val is None:
                    continue
                if a_val > b_val:
                    regressions.append(Regression(symbol, tf, measure.label, _as_int(b_val), _as_int(a_val)))
                elif a_val < b_val:
                    improvements.append(Regression(symbol, tf, measure.label, _as_int(b_val), _as_int(a_val)))

    return CompareResult(
        regressions=regressions,
        improvements=improvements,
        new_symbols=sorted(set(after_syms) - set(before_syms)),
        missing_symbols=sorted((gate & set(before_syms)) - set(after_syms)),
        compared_symbols=sorted(gate & set(before_syms) & set(after_syms)),
        measure_versions=versions,
        grid_skipped_measures=(
            tuple(m.label for m in _WORSE_IF_UP if m.grid_dependent) if grid_changed else ()
        ),
        calendar_skipped_measures=(
            tuple(m.label for m in _WORSE_IF_UP if m.calendar_dependent) if calendar_changed else ()
        ),
    )


def _as_int(value: float) -> Any:
    return int(value) if float(value).is_integer() else value
