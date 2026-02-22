"""S6: TF_ALLOWLIST — єдиний SSOT у config.json, не hardcoded в buckets.py."""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

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
    with pytest.raises(ValueError):
        tf_to_ms(0)
    with pytest.raises(ValueError):
        tf_to_ms(-60)


def test_no_hardcoded_tf_allowlist_in_buckets():
    """buckets.py НЕ містить hardcoded TF_ALLOWLIST."""
    spec = importlib.util.find_spec("core.buckets")
    with open(spec.origin, encoding="utf-8") as f:
        text = f.read()
    for i, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("TF_ALLOWLIST") and "=" in stripped and "import" not in stripped:
            raise AssertionError(
                "core/buckets.py L%d has hardcoded TF_ALLOWLIST: %s" % (i, stripped)
            )


def test_config_json_is_ssot():
    """config.json tf_allowlist_s містить всі 8 TF."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    tf_list = cfg.get("tf_allowlist_s", [])
    expected = {60, 180, 300, 900, 1800, 3600, 14400, 86400}
    assert set(tf_list) == expected, "config.json tf_allowlist_s = %s, expected %s" % (tf_list, expected)
