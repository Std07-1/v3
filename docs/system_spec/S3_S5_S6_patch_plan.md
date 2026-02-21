# S3 + S5 + S6 — PATCH Plan (P7 Remediation)

> **MODE**: PATCH planning  
> **Date**: 2026-02-21  
> **Constraint**: ≤150 LOC, ≤1 new file per slice  
> **Convention**: кожен patch — один commit, один changelog рядок

---

## S3: FINAL_SOURCES single SSOT

### (a) FACTS — де проблема (file:line)

Ідентичне визначення `FINAL_SOURCES = {"history", "derived", "history_agg"}` у **трьох** файлах:

| # | Файл | Рядок | Шар |
|---|------|-------|-----|
| 1 | `runtime/store/uds.py` | L42 | runtime |
| 2 | `runtime/store/ssot_jsonl.py` | L12 | runtime |
| 3 | `runtime/store/layers/disk_layer.py` | L9 | runtime |

**Ризик**: якщо хтось додасть новий src (наприклад `"aggregated"`) лише в одному з трьох файлів — silent data loss (SSOT writer пропустить, або disk reader відфільтрує).

**Dependency Rule**: `core/` не імпортує `runtime/`, але `runtime/` може імпортувати `core/`. Тому канонічне місце — `core/`.

**Обране місце**: `core/model/bars.py` — поруч із `CandleBar`, де вже задокументовано `src: str  # "history" | "derived"` (L29). Це domain-level константа про семантику джерела бару.

### (b) DIFF

#### Файл 1: `core/model/bars.py` — додати константу

```diff
--- a/core/model/bars.py
+++ b/core/model/bars.py
@@ -5,6 +5,14 @@
 from typing import Any, Dict, Tuple
 
 
+# ── FINAL_SOURCES: єдиний SSOT (S3 remediation) ──────────────
+# Джерела, які роблять бар «final» (придатний для SSOT JSONL та canonical window).
+# Будь-яка зміна — ТІЛЬКИ тут; решта коду імпортує цю константу.
+FINAL_SOURCES: frozenset[str] = frozenset({"history", "derived", "history_agg"})
+SOURCE_ALLOWLIST: frozenset[str] = frozenset({"history", "derived", "history_agg", ""})
+# ──────────────────────────────────────────────────────────────
+
+
 @dataclasses.dataclass(frozen=True)
 class CandleBar:
```

> **Примітка**: `frozenset` замість `set` — immutable, не можна випадково `.add()/.remove()` на runtime.
> `SOURCE_ALLOWLIST` переїжджає разом — вона теж дублюється (uds.py L41), а визначається через FINAL_SOURCES + `""`.

#### Файл 2: `runtime/store/uds.py` — видалити дублікат, імпортувати

```diff
--- a/runtime/store/uds.py
+++ b/runtime/store/uds.py
@@ -11,7 +11,7 @@
 from dataclasses import dataclass
 from typing import Any, Optional
 
-from core.model.bars import CandleBar
+from core.model.bars import CandleBar, FINAL_SOURCES, SOURCE_ALLOWLIST
 from core.config_loader import (
     tf_allowlist_from_cfg, preview_tf_allowlist_from_cfg, min_coldload_bars_from_cfg,
     DEFAULT_PREVIEW_TF_ALLOWLIST, MAX_EVENTS_PER_RESPONSE,
@@ -39,8 +39,6 @@
     Logging.warning("UDS: redis бібліотека недоступна, RedisLayer вимкнено")
 
 
-SOURCE_ALLOWLIST = {"history", "derived", "history_agg", ""}
-FINAL_SOURCES = {"history", "derived", "history_agg"}
 REDIS_SOCKET_TIMEOUT_S = 0.4
 _DEFAULT_PREVIEW_CURR_TTL_S = 1800  # SSOT fallback; runtime значення з config.json
 PREVIEW_TAIL_RETAIN = 2000
```

#### Файл 3: `runtime/store/ssot_jsonl.py` — видалити дублікат, імпортувати

```diff
--- a/runtime/store/ssot_jsonl.py
+++ b/runtime/store/ssot_jsonl.py
@@ -7,9 +7,8 @@
 from typing import Any, Dict, List, Optional, Tuple
 
 from core.model.bars import CandleBar, assert_invariants, ms_to_utc_dt
+from core.model.bars import FINAL_SOURCES
 
-
-FINAL_SOURCES = {"history", "derived", "history_agg"}
 
 
 def _d1_anchor_offsets(
```

#### Файл 4: `runtime/store/layers/disk_layer.py` — видалити дублікат, імпортувати

```diff
--- a/runtime/store/layers/disk_layer.py
+++ b/runtime/store/layers/disk_layer.py
@@ -5,8 +5,8 @@
 from collections import deque
 from typing import Any, Iterable, Optional
 
+from core.model.bars import FINAL_SOURCES
 
-FINAL_SOURCES = {"history", "derived", "history_agg"}
 
 
 def _iter_lines_reverse(path: str) -> Iterable[bytes]:
```

**Загальний диф**: 4 файли, +8 LOC (константа + коментарі), −6 LOC (видалені дублікати). Net: **+2 LOC**.

### (c) ТЕСТ (pytest)

```python
# tests/test_s3_final_sources_ssot.py
"""S3: FINAL_SOURCES визначено в одному місці (core/model/bars.py)."""

def test_final_sources_canonical_location():
    """Канонічне визначення є у core.model.bars."""
    from core.model.bars import FINAL_SOURCES
    assert isinstance(FINAL_SOURCES, frozenset)
    assert FINAL_SOURCES == {"history", "derived", "history_agg"}


def test_no_local_redefinition_uds():
    """uds.py НЕ визначає власну FINAL_SOURCES."""
    import importlib
    import runtime.store.uds as mod
    src = importlib.util.find_spec("runtime.store.uds").origin
    with open(src, encoding="utf-8") as f:
        text = f.read()
    # Перевіряємо, що немає рядка 'FINAL_SOURCES = {' (визначення),
    # але є 'from core.model.bars import ... FINAL_SOURCES' (імпорт).
    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("FINAL_SOURCES") and "=" in stripped and "import" not in stripped:
            raise AssertionError(f"uds.py визначає FINAL_SOURCES локально: {stripped}")


def test_no_local_redefinition_ssot_jsonl():
    """ssot_jsonl.py НЕ визначає власну FINAL_SOURCES."""
    import importlib
    import runtime.store.ssot_jsonl as mod
    src = importlib.util.find_spec("runtime.store.ssot_jsonl").origin
    with open(src, encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("FINAL_SOURCES") and "=" in stripped and "import" not in stripped:
            raise AssertionError(f"ssot_jsonl.py визначає FINAL_SOURCES локально: {stripped}")


def test_no_local_redefinition_disk_layer():
    """disk_layer.py НЕ визначає власну FINAL_SOURCES."""
    import importlib
    import runtime.store.layers.disk_layer as mod
    src = importlib.util.find_spec("runtime.store.layers.disk_layer").origin
    with open(src, encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("FINAL_SOURCES") and "=" in stripped and "import" not in stripped:
            raise AssertionError(f"disk_layer.py визначає FINAL_SOURCES локально: {stripped}")


def test_all_consumers_see_same_object():
    """Усі модулі бачать один і той самий frozenset (identity check)."""
    from core.model.bars import FINAL_SOURCES as canonical
    from runtime.store.uds import FINAL_SOURCES as from_uds
    from runtime.store.ssot_jsonl import FINAL_SOURCES as from_ssot
    from runtime.store.layers.disk_layer import FINAL_SOURCES as from_disk
    assert canonical is from_uds, "uds повертає інший об'єкт"
    assert canonical is from_ssot, "ssot_jsonl повертає інший об'єкт"
    assert canonical is from_disk, "disk_layer повертає інший об'єкт"
```

### (d) VERIFY

```bash
# 1. Синтаксис
python -m py_compile core/model/bars.py
python -m py_compile runtime/store/uds.py
python -m py_compile runtime/store/ssot_jsonl.py
python -m py_compile runtime/store/layers/disk_layer.py

# 2. Тест
python -m pytest tests/test_s3_final_sources_ssot.py -v

# 3. Grep: НЕ повинно бути жодного рядка з визначенням поза bars.py
grep -rn "^FINAL_SOURCES\s*=" runtime/ core/ --include="*.py"
# Очікується: тільки core/model/bars.py

# 4. Existing tests pass
python -m pytest tests/ -x --timeout=30
```

### (e) ROLLBACK

```bash
# Повернути локальні визначення у 3 файлах, прибрати константу з bars.py.
# Або: git revert <commit-hash-of-S3>
```

Рядок `FINAL_SOURCES = {"history", "derived", "history_agg"}` вставляється назад у:
- `uds.py` L42 + `SOURCE_ALLOWLIST = {"history", "derived", "history_agg", ""}` L41
- `ssot_jsonl.py` L12
- `disk_layer.py` L9

---

## S5: Default complete=True → fail-loud

### (a) FACTS — де проблема (file:line)

**Файл**: `ui_chart_v3/server.py`, **рядок 744**:

```python
complete = bool(raw_bar.get("complete", True))
```

**Контекст**: функція `_normalize_bar_window_v1()` (L692) — нормалізація raw bar dict для window_v1 payload.

**Ризик**: якщо UDS/Redis повертає бар **без** поля `complete` (а це не повинно бути, але при degradation трапляється) — він автоматично стає `complete=True`. Preview бар без explicit `complete=False` пролізе як «final» у UI, порушення I3 (Final > Preview, NoMix).

**Дизайн-рішення**: NOT fail-hard (не return None) — це зламало б UI при legacy даних. Замість цього:
1. Default → `False` (безпечний бік — краще "ще не final" ніж "хибний final")
2. + WARNING у meta.warnings[] (loud, як вимагає I5)

### (b) DIFF

#### Файл: `ui_chart_v3/server.py`

```diff
--- a/ui_chart_v3/server.py
+++ b/ui_chart_v3/server.py
@@ -741,7 +741,15 @@ def _normalize_bar_window_v1(
     src = str(src_raw) if src_raw is not None else ""
 
-    complete = bool(raw_bar.get("complete", True))
+    raw_complete = raw_bar.get("complete")
+    if raw_complete is None:
+        # S5: fail-loud — бар без explicit complete → default False (safe side) + warning.
+        # I5: degraded-but-loud, I3: final > preview (не промотуємо мовчки).
+        complete = False
+        _s5_missing = True
+    else:
+        complete = bool(raw_complete)
+        _s5_missing = False
     out = {
         "time": int(open_time_ms) // 1000,
         "open": float(open_v),
@@ -756,6 +764,10 @@ def _normalize_bar_window_v1(
         "complete": complete,
     }
 
+    if _s5_missing:
+        out.setdefault("_warnings", [])
+        out["_warnings"].append("MISSING_COMPLETE_FIELD_DEFAULTED_FALSE")
+
     event_ts = raw_bar.get("event_ts")
     if complete:
         if isinstance(event_ts, int):
```

Тепер потрібно переконатись, що `_warnings` з бару піднімаються до response-level `meta.warnings`. Перевіримо, чи є такий механізм:

```diff
--- a/ui_chart_v3/server.py  (у функції, що формує /api/bars response)
+++ b/ui_chart_v3/server.py
 # У місці, де bars збираються в response (перевірити конкретну функцію):
 # Додати aggregation _warnings з кожного бару → meta.warnings
+    bar_warnings = []
+    for b in bars:
+        bw = b.pop("_warnings", None)
+        if bw:
+            bar_warnings.extend(bw)
+    if bar_warnings:
+        meta.setdefault("warnings", [])
+        # Deduplicate, але зберегти count
+        seen = {}
+        for w in bar_warnings:
+            seen[w] = seen.get(w, 0) + 1
+        for w, cnt in seen.items():
+            meta["warnings"].append(f"{w} (count={cnt})")
```

> **Примітка для Copilot**: знайди місце у `/api/bars` handler де `bars` + `meta` формуються для response. Встав aggregation блок **перед** `return jsonify(...)`. Точний рядок залежить від поточного стану server.py — шукай `_build_bars_response` або подібне. Якщо такої агрегації вже немає — додай саме перед return.

**Загальний диф**: 1 файл, ~+20 LOC.

### (c) ТЕСТ (pytest)

```python
# tests/test_s5_complete_default.py
"""S5: bar без complete → default False + warning (не True)."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_raw_bar(*, include_complete=True, complete_value=True):
    """Мінімальний raw bar для _normalize_bar_window_v1."""
    bar = {
        "open_time_ms": 1700000000000,
        "close_time_ms": 1700000060000,
        "tf_s": 60,
        "open": 2000.0, "high": 2001.0, "low": 1999.0,
        "close": 2000.5, "volume": 100.0,
        "src": "history",
    }
    if include_complete:
        bar["complete"] = complete_value
    return bar


def test_bar_with_complete_true():
    from ui_chart_v3.server import _normalize_bar_window_v1
    raw = _make_raw_bar(include_complete=True, complete_value=True)
    result = _normalize_bar_window_v1(raw, tf_s=60)
    assert result is not None
    assert result["complete"] is True
    assert "_warnings" not in result


def test_bar_with_complete_false():
    from ui_chart_v3.server import _normalize_bar_window_v1
    raw = _make_raw_bar(include_complete=True, complete_value=False)
    result = _normalize_bar_window_v1(raw, tf_s=60)
    assert result is not None
    assert result["complete"] is False
    assert "_warnings" not in result


def test_bar_without_complete_defaults_false():
    """S5 core: бар без поля complete → complete=False (safe side)."""
    from ui_chart_v3.server import _normalize_bar_window_v1
    raw = _make_raw_bar(include_complete=False)
    result = _normalize_bar_window_v1(raw, tf_s=60)
    assert result is not None
    assert result["complete"] is False, (
        f"Expected complete=False for bar without 'complete' field, got {result['complete']}"
    )


def test_bar_without_complete_has_warning():
    """S5 loud: бар без complete → _warnings містить MISSING_COMPLETE_FIELD."""
    from ui_chart_v3.server import _normalize_bar_window_v1
    raw = _make_raw_bar(include_complete=False)
    result = _normalize_bar_window_v1(raw, tf_s=60)
    assert result is not None
    warnings = result.get("_warnings", [])
    assert any("MISSING_COMPLETE" in w for w in warnings), (
        f"Expected MISSING_COMPLETE warning, got {warnings}"
    )
```

### (d) VERIFY

```bash
# 1. Синтаксис
python -m py_compile ui_chart_v3/server.py

# 2. Тест
python -m pytest tests/test_s5_complete_default.py -v

# 3. Manual: запустити UI server, GET /api/bars — перевірити що
#    - нормальні бари мають complete=true/false як раніше
#    - якщо вручну видалити 'complete' з Redis запису — response має warning
#      "MISSING_COMPLETE_FIELD_DEFAULTED_FALSE"

# 4. Regression: existing tests
python -m pytest tests/ -x --timeout=30
```

### (e) ROLLBACK

```bash
# Одна зміна в server.py L744: повернути
#   complete = bool(raw_bar.get("complete", True))
# Видалити блок _s5_missing і _warnings.
# Або: git revert <commit-hash-of-S5>
```

---

## S6: TF_ALLOWLIST single SSOT (config.json)

### (a) FACTS — де проблема (file:line)

| # | Файл | Рядок | Зміст |
|---|------|-------|-------|
| 1 | `core/buckets.py` | L10–18 | `TF_ALLOWLIST = {60, 180, 300, 900, 1800, 3600, 14400, 86400}` — **hardcoded** |
| 2 | `config.json` | L55–64 | `"tf_allowlist_s": [60, 180, 300, 900, 1800, 3600, 14400, 86400]` — **SSOT** |
| 3 | `core/config_loader.py` | L73 | `DEFAULT_TF_ALLOWLIST = {300, 900, 1800, 3600, 14400, 86400}` — **6 TF** (без M1/M3) |
| 4 | `core/config_loader.py` | L78 | `tf_allowlist_from_cfg(cfg)` — парсить з config.json |

**Споживачі TF_ALLOWLIST з buckets.py:**

| Споживач | Що імпортує | Як використовує |
|----------|-------------|-----------------|
| `tick_agg.py` L13, L88 | `tf_to_ms` | Конвертація + валідація. **Але** tick_agg вже має `self._tf_allowlist` (L84) — подвійна перевірка |

**Споживачі `bucket_start_ms` і `resolve_anchor_offset_ms`**: не залежать від TF_ALLOWLIST.

**Ризик**: якщо додати новий TF (наприклад 120s = M2) у config.json, `tf_to_ms()` кине ValueError бо hardcoded set не оновлений. Silent configuration mismatch.

**Дизайн-рішення**: 
- `tf_to_ms` не повинна мати hardcoded allowlist — її єдина job = `tf_s * 1000`.
- Валідація TF — відповідальність **caller** (через config-derived allowlist).
- `TF_ALLOWLIST` як module-level export з buckets.py → прибрати. Якщо комусь потрібна дефолтна множина — імпорт `DEFAULT_TF_ALLOWLIST` з config_loader (або `tf_allowlist_from_cfg(cfg)`).

### (b) DIFF

#### Файл 1: `core/buckets.py`

```diff
--- a/core/buckets.py
+++ b/core/buckets.py
@@ -7,18 +7,14 @@
 from core.time_geom import bar_close_incl
 
 
-TF_ALLOWLIST = {
-    60,
-    180,
-    300,
-    900,
-    1800,
-    3600,
-    14400,
-    86400,
-}
-
-
-def tf_to_ms(tf_s: int) -> int:
-    if tf_s not in TF_ALLOWLIST:
-        raise ValueError(f"unsupported_tf_s={tf_s}")
+def tf_to_ms(tf_s: int, *, tf_allowlist: set[int] | None = None) -> int:
+    """Конвертує TF секунди → мілісекунди.
+
+    Args:
+        tf_s: таймфрейм у секундах (позитивне ціле).
+        tf_allowlist: опціональна множина дозволених TF.
+            Якщо передана і tf_s не в ній — ValueError.
+            Якщо None — валідація не виконується (caller відповідає).
+    """
+    if tf_allowlist is not None and tf_s not in tf_allowlist:
+        raise ValueError(f"unsupported_tf_s={tf_s} not in allowlist")
+    if not isinstance(tf_s, int) or tf_s <= 0:
+        raise ValueError(f"invalid_tf_s={tf_s}")
     return int(tf_s * 1000)
```

#### Файл 2: `runtime/ingest/tick_agg.py` — виклик вже safe

Перевірка: tick_agg.py L84 перевіряє `tf_s not in self._tf_allowlist` **перед** викликом `tf_to_ms` на L88. Тому tf_to_ms без allowlist — безпечний. **Зміни не потрібні.**

#### Файл 3 (optional housekeeping): якщо десь є `from core.buckets import TF_ALLOWLIST` — прибрати

```bash
grep -rn "from core.buckets import.*TF_ALLOWLIST" --include="*.py"
# Якщо знайдено — замінити на: from core.config_loader import DEFAULT_TF_ALLOWLIST
# або tf_allowlist_from_cfg(cfg)
```

**Загальний диф**: 1 файл (buckets.py), **−14 LOC, +12 LOC**. Net: −2 LOC.

### (c) ТЕСТ (pytest)

```python
# tests/test_s6_tf_allowlist_ssot.py
"""S6: TF_ALLOWLIST — єдиний SSOT у config.json, не hardcoded в buckets.py."""

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_tf_to_ms_basic():
    """tf_to_ms конвертує без помилки для будь-якого позитивного int."""
    from core.buckets import tf_to_ms
    assert tf_to_ms(60) == 60_000
    assert tf_to_ms(300) == 300_000
    assert tf_to_ms(86400) == 86_400_000


def test_tf_to_ms_with_allowlist_pass():
    """tf_to_ms з allowlist — пропускає дозволений TF."""
    from core.buckets import tf_to_ms
    result = tf_to_ms(60, tf_allowlist={60, 300})
    assert result == 60_000


def test_tf_to_ms_with_allowlist_reject():
    """tf_to_ms з allowlist — кидає ValueError для недозволеного TF."""
    from core.buckets import tf_to_ms
    import pytest
    with pytest.raises(ValueError, match="unsupported_tf_s"):
        tf_to_ms(120, tf_allowlist={60, 300})


def test_tf_to_ms_without_allowlist_accepts_any():
    """Без allowlist tf_to_ms приймає будь-який позитивний TF."""
    from core.buckets import tf_to_ms
    # Нестандартний TF — раніше кидав ValueError, тепер OK
    assert tf_to_ms(120) == 120_000
    assert tf_to_ms(7200) == 7_200_000


def test_tf_to_ms_invalid():
    """tf_to_ms кидає ValueError для невалідних значень."""
    from core.buckets import tf_to_ms
    import pytest
    with pytest.raises(ValueError):
        tf_to_ms(0)
    with pytest.raises(ValueError):
        tf_to_ms(-60)


def test_no_hardcoded_tf_allowlist_in_buckets():
    """buckets.py НЕ містить hardcoded TF_ALLOWLIST."""
    import importlib.util
    spec = importlib.util.find_spec("core.buckets")
    with open(spec.origin, encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("TF_ALLOWLIST") and "=" in stripped and "import" not in stripped:
            raise AssertionError(
                f"core/buckets.py L{i} has hardcoded TF_ALLOWLIST: {stripped}"
            )


def test_config_json_is_ssot():
    """config.json tf_allowlist_s містить всі 8 TF."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    tf_list = cfg.get("tf_allowlist_s", [])
    expected = {60, 180, 300, 900, 1800, 3600, 14400, 86400}
    assert set(tf_list) == expected, f"config.json tf_allowlist_s = {tf_list}, expected {expected}"
```

### (d) VERIFY

```bash
# 1. Синтаксис
python -m py_compile core/buckets.py

# 2. Grep: НЕ повинно бути module-level TF_ALLOWLIST = {...} в buckets.py
grep -n "^TF_ALLOWLIST" core/buckets.py
# Очікується: порожній вивід

# 3. Grep: НЕ повинно бути імпортів TF_ALLOWLIST з buckets
grep -rn "from core.buckets import.*TF_ALLOWLIST" --include="*.py"
# Очікується: порожній вивід (або замінено на config_loader)

# 4. Тест
python -m pytest tests/test_s6_tf_allowlist_ssot.py -v

# 5. Перевірити tick_agg: tf_to_ms(60) без allowlist → 60000 (no crash)
python -c "from core.buckets import tf_to_ms; print(tf_to_ms(60)); print('OK')"

# 6. Regression
python -m pytest tests/ -x --timeout=30
```

### (e) ROLLBACK

```bash
# Повернути TF_ALLOWLIST = {60, 180, ...} у buckets.py L10–18.
# Повернути tf_to_ms з hardcoded перевіркою.
# Або: git revert <commit-hash-of-S6>
```

---

## Зведена таблиця

| Slice | Файли | LOC Δ | Нових файлів | Ризик | Залежності |
|-------|-------|-------|--------------|-------|-----------|
| S3 | bars.py, uds.py, ssot_jsonl.py, disk_layer.py | +2 net | 0 | Мінімальний: лише перенос константи | Жодних |
| S5 | server.py | +20 | 0 | Низький: зміна default на безпечний бік | Жодних |
| S6 | buckets.py | −2 net | 0 | Низький: tf_to_ms тепер гнучкіша | tick_agg вже має свою перевірку |

**Порядок застосування**: S3 → S6 → S5 (незалежні, але S3 найчистіший).

---

## Changelog entries (для Copilot)

```json
{"id":"20260221-S03","ts":"2026-02-21T__:__:00Z","area":"core","initiative":"p7_remediation","status":"active","reverts":null,"scope":"constants","files":["core/model/bars.py","runtime/store/uds.py","runtime/store/ssot_jsonl.py","runtime/store/layers/disk_layer.py"],"summary":"S3: FINAL_SOURCES single SSOT в core/model/bars.py","details":"PATCH: FINAL_SOURCES (frozenset) визначено в core/model/bars.py; видалено дублікати з uds.py, ssot_jsonl.py, disk_layer.py; додано SOURCE_ALLOWLIST там же.","why":"Triple duplication ризикує silent data loss при розходженні","goal":"Одне місце визначення FINAL_SOURCES, решта імпортує","risks":"Мінімальні — лише перенос константи","rollback_steps":["Повернути FINAL_SOURCES = {...} у 3 файли, прибрати з bars.py"],"notes":"VERIFY: grep -rn '^FINAL_SOURCES\\s*=' runtime/ core/ --include='*.py' → тільки bars.py"}
```

```json
{"id":"20260221-S05","ts":"2026-02-21T__:__:00Z","area":"ui_chart","initiative":"p7_remediation","status":"active","reverts":null,"scope":"server","files":["ui_chart_v3/server.py"],"summary":"S5: default complete=True → False + MISSING_COMPLETE warning","details":"PATCH: _normalize_bar_window_v1 L744: бар без explicit complete → default False (safe side) + _warnings=['MISSING_COMPLETE_FIELD_DEFAULTED_FALSE']. Піднімається до meta.warnings.","why":"Preview бар без explicit complete=False мовчки ставав final (I3 порушення)","goal":"Жодний бар без complete не промотується до final; loud warning","risks":"Бари з legacy Redis записів без complete тепер показуватимуться як incomplete — це correct behavior","rollback_steps":["Повернути complete = bool(raw_bar.get('complete', True)) у server.py L744"],"notes":"VERIFY: pytest tests/test_s5_complete_default.py -v"}
```

```json
{"id":"20260221-S06","ts":"2026-02-21T__:__:00Z","area":"core","initiative":"p7_remediation","status":"active","reverts":null,"scope":"buckets","files":["core/buckets.py"],"summary":"S6: TF_ALLOWLIST прибрано з buckets.py; SSOT = config.json","details":"PATCH: видалено hardcoded TF_ALLOWLIST з buckets.py; tf_to_ms приймає optional tf_allowlist параметр; tick_agg вже валідує через self._tf_allowlist з config.","why":"Hardcoded множина дублювала config.json і ризикувала розходженням","goal":"config.json tf_allowlist_s — єдиний SSOT для дозволених TF","risks":"tf_to_ms без allowlist тепер приймає будь-який позитивний TF — caller має валідувати","rollback_steps":["Повернути TF_ALLOWLIST = {...} і перевірку у tf_to_ms"],"notes":"VERIFY: grep -n '^TF_ALLOWLIST' core/buckets.py → порожній вивід"}
```
