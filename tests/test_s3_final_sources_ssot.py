"""S3: FINAL_SOURCES визначено в одному місці (core/model/bars.py)."""
from __future__ import annotations

import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_final_sources_canonical_location():
    """Канонічне визначення є у core.model.bars і є frozenset."""
    from core.model.bars import FINAL_SOURCES
    assert isinstance(FINAL_SOURCES, frozenset)
    assert FINAL_SOURCES == {"history", "derived", "history_agg"}


def test_source_allowlist_canonical_location():
    """SOURCE_ALLOWLIST є frozenset і містить FINAL_SOURCES + пустий рядок."""
    from core.model.bars import FINAL_SOURCES, SOURCE_ALLOWLIST
    assert isinstance(SOURCE_ALLOWLIST, frozenset)
    assert SOURCE_ALLOWLIST == FINAL_SOURCES | frozenset({""})


def test_no_local_redefinition_uds():
    """uds.py НЕ визначає власну FINAL_SOURCES."""
    spec = importlib.util.find_spec("runtime.store.uds")
    with open(spec.origin, encoding="utf-8") as f:
        text = f.read()
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("FINAL_SOURCES") and "=" in stripped and "import" not in stripped:
            raise AssertionError("uds.py визначає FINAL_SOURCES локально: %s" % stripped)
        if stripped.startswith("SOURCE_ALLOWLIST") and "=" in stripped and "import" not in stripped:
            raise AssertionError("uds.py визначає SOURCE_ALLOWLIST локально: %s" % stripped)


def test_no_local_redefinition_ssot_jsonl():
    """ssot_jsonl.py НЕ визначає власну FINAL_SOURCES."""
    spec = importlib.util.find_spec("runtime.store.ssot_jsonl")
    with open(spec.origin, encoding="utf-8") as f:
        text = f.read()
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("FINAL_SOURCES") and "=" in stripped and "import" not in stripped:
            raise AssertionError("ssot_jsonl.py визначає FINAL_SOURCES локально: %s" % stripped)


def test_no_local_redefinition_disk_layer():
    """disk_layer.py НЕ визначає власну FINAL_SOURCES."""
    spec = importlib.util.find_spec("runtime.store.layers.disk_layer")
    with open(spec.origin, encoding="utf-8") as f:
        text = f.read()
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("FINAL_SOURCES") and "=" in stripped and "import" not in stripped:
            raise AssertionError("disk_layer.py визначає FINAL_SOURCES локально: %s" % stripped)


def test_all_consumers_see_same_object():
    """Усі модулі бачать один і той самий frozenset (identity check)."""
    from core.model.bars import FINAL_SOURCES as canonical
    from runtime.store.uds import FINAL_SOURCES as from_uds
    from runtime.store.ssot_jsonl import FINAL_SOURCES as from_ssot
    # ADR-0094: disk_layer більше не читає FINAL_SOURCES сам — перевірку «final src» для нього (і для
    # uds, і для ремонтного дедупу) робить єдиний вибирач core.model.bar_choice. Справжній споживач тут він.
    from core.model.bar_choice import FINAL_SOURCES as from_bar_choice
    import runtime.store.layers.disk_layer as disk_layer
    import core.model.bar_choice as bar_choice
    assert canonical is from_uds, "uds повертає інший об'єкт"
    assert canonical is from_ssot, "ssot_jsonl повертає інший об'єкт"
    assert canonical is from_bar_choice, "bar_choice повертає інший об'єкт"
    assert disk_layer.is_final_source is bar_choice.is_final_source, "disk_layer обходить єдиний вибирач"
