# ADR-0020: CandleBar Extensions Immutability

- **Статус**: Proposed
- **Дата**: 2026-02-26
- **Автор**: code-review audit (111.md, дефект #3)
- **Initiative**: `code_review_hardening`

## Контекст і проблема

`CandleBar` (core/model/bars.py:17) — frozen dataclass (`frozen=True`), що гарантує
незмінність після створення. Але поле `extensions: Dict[str, Any]` — звичайний dict,
який можна мутувати in-place навіть на frozen екземплярі.

**Поточне використання мутації** (FACTS з коду):

```python
# core/derive.py:436-447 — aggregate_partial_bar()
result.extensions["partial"] = True
result.extensions["boundary_partial"] = True
result.extensions["source_count"] = ...
result.extensions["expected_count"] = ...
result.extensions["partial_reasons"] = reasons
result.extensions["mid_session_gaps"] = mid_gaps
```

Мутація відбувається одразу після створення бару (в aggregate_partial_bar),
до того як бар покидає scope core/derive.py — тобто це "construction mutation",
а не mutation-in-the-wild.

**Ризик**: якщо хтось мутує extensions після commit через UDS — порушується
семантика "final > preview" (I3) та SSOT, бо cached reference може змінити
дані без контролю.

## Розглянуті варіанти

### Варіант A: `MappingProxyType` (read-only view)

```python
extensions: types.MappingProxyType = dataclasses.field(
    default_factory=lambda: types.MappingProxyType({})
)
```

**Плюси**: повна незмінність після створення.
**Мінуси**:

- Ламає всі поточні присвоєння в core/derive.py (~6 місць).
- Потрібно переписати aggregate_partial_bar: спочатку збирати dict, потім
  створювати CandleBar з готовим extensions.
- MappingProxyType не є hashable → не допомагає з set/dict keys.
- ~40 LOC рефактору, торкається core/ та тести.

### Варіант B: Freeze-on-escape (документований паттерн)

Залишити Dict, але:

1. Додати `_freeze_extensions()` метод, що замінює dict на MappingProxyType.
2. Викликати його перед передачею бару з core/ в runtime/ (тобто в commit).
3. Документувати в contracts: "extensions мутабельний тільки під час construction".

**Плюси**: мінімальний рефактор, backward compatible.
**Мінуси**: convention-based, не enforce на рівні типу.

### Варіант C: Нічого не міняти (поточний стан + документація)

Залишити як є. Мутація extensions — документований construction pattern.
Додати коментар/docstring з попередженням.

**Плюси**: нуль змін.
**Мінуси**: ризик лишається, але контрольований.

## Рекомендація

**Варіант B** як компроміс: мінімальний рефактор, реальний enforcement на межі
core→runtime. Але це потребує окремого PATCH (>15 LOC, торкається core/).

## Наслідки

- Якщо вибрано B: додати `_freeze_extensions()` в CandleBar, викликати з UDS
  перед записом. Тести: перевірити що post-commit mutation кидає TypeError.
- Якщо вибрано C: додати docstring warning у CandleBar.extensions field.

## Поточний ризик

**Низький**: мутація відбувається тільки в одному місці (aggregate_partial_bar),
одразу після створення, до escape з scope. Ні один caller не мутує extensions
після commit. Але паттерн крихкий при розвитку системи.

## Rollback

Видалити ADR файл та будь-які зміни до CandleBar (якщо Варіант B реалізовано).
