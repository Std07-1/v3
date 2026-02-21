"""S1 тести: Preview TTL SSOT — єдине значення TTL з config через build_uds_from_config."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runtime.store.uds import (
    build_uds_from_config,
    _DEFAULT_PREVIEW_CURR_TTL_S,
    UnifiedDataStore,
)


def _write_temp_config(overrides: dict | None = None) -> str:
    """Створює мінімальний config.json для тестів і повертає шлях."""
    base = {
        "symbols": ["XAU/USD"],
        "tf_allowlist_s": [60, 180, 300],
        "data_root": "./data_v3",
        "preview_curr_ttl_s": 1800,
        "redis": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 6379,
            "db": 1,
            "namespace": "test_ns",
        },
    }
    if overrides:
        base.update(overrides)
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(base, f)
    return path


class TestS1BuildUdsWiresTTL(unittest.TestCase):
    """build_uds_from_config передає preview_curr_ttl_s з config до UDS."""

    @patch("runtime.store.uds._redis_layer_from_cfg", return_value=None)
    @patch("runtime.store.uds._updates_bus_from_cfg", return_value=None)
    def test_config_ttl_wired_to_uds(self, _bus, _redis):
        cfg_path = _write_temp_config({"preview_curr_ttl_s": 2400})
        try:
            uds = build_uds_from_config(
                config_path=cfg_path,
                data_root="./data_v3",
                boot_id="test-boot-001",
                role="reader",
            )
            self.assertEqual(uds._preview_curr_ttl_s, 2400)
        finally:
            os.unlink(cfg_path)

    @patch("runtime.store.uds._redis_layer_from_cfg", return_value=None)
    @patch("runtime.store.uds._updates_bus_from_cfg", return_value=None)
    def test_config_without_ttl_uses_default(self, _bus, _redis):
        cfg_path = _write_temp_config()
        # Видаляємо ключ з config
        with open(cfg_path) as f:
            cfg = json.load(f)
        del cfg["preview_curr_ttl_s"]
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)
        try:
            uds = build_uds_from_config(
                config_path=cfg_path,
                data_root="./data_v3",
                boot_id="test-boot-002",
                role="reader",
            )
            self.assertEqual(uds._preview_curr_ttl_s, _DEFAULT_PREVIEW_CURR_TTL_S)
        finally:
            os.unlink(cfg_path)


class TestS1PublishPreviewBarTTL(unittest.TestCase):
    """publish_preview_bar використовує SSOT TTL при ttl_s=None."""

    def _make_uds(self, ttl_s: int = 1800) -> UnifiedDataStore:
        mock_redis = MagicMock()
        mock_redis.read_preview_tail.return_value = (None, None, None)
        uds = UnifiedDataStore(
            data_root="./data_v3",
            boot_id="test-boot-003",
            tf_allowlist={60, 180, 300},
            min_coldload_bars={60: 500, 180: 200, 300: 100},
            role="writer",
            redis_layer=mock_redis,
            preview_curr_ttl_s=ttl_s,
        )
        return uds

    def _make_bar(self):
        from core.model.bars import CandleBar
        return CandleBar(
            symbol="XAU_USD",
            tf_s=60,
            open_time_ms=1740000000000,
            close_time_ms=1740000060000,
            o=2850.0, h=2855.0, low=2848.0, c=2853.0,
            v=100.0, complete=False, src="preview_tick",
        )

    def test_publish_without_ttl_uses_ssot(self):
        """Engine path: publish_preview_bar(bar) без ttl_s → EX = SSOT TTL."""
        uds = self._make_uds(ttl_s=1800)
        bar = self._make_bar()
        uds.publish_preview_bar(bar)
        # Перевіряємо що write_preview_curr викликано з ttl_s = 1800
        calls = uds._redis.write_preview_curr.call_args_list
        self.assertEqual(len(calls), 1)
        actual_ttl = calls[0][0][3] if len(calls[0][0]) > 3 else calls[0][1].get("ttl_s", calls[0][0][3])
        self.assertEqual(actual_ttl, 1800)

    def test_publish_with_explicit_ttl(self):
        """Worker path: publish_preview_bar(bar, ttl_s=900) → EX = 900."""
        uds = self._make_uds(ttl_s=1800)
        bar = self._make_bar()
        uds.publish_preview_bar(bar, ttl_s=900)
        calls = uds._redis.write_preview_curr.call_args_list
        self.assertEqual(len(calls), 1)
        actual_ttl = calls[0][0][3] if len(calls[0][0]) > 3 else calls[0][1].get("ttl_s", calls[0][0][3])
        self.assertEqual(actual_ttl, 900)


if __name__ == "__main__":
    unittest.main()
