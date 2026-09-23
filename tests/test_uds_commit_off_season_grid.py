"""commit_final_bar: бар, який писар SSOT відкинув через сезонну сітку, не потрапляє ні в Redis, ні в pubsub (ADR-0095 §3.3).

Раніше `_append_to_disk` ковтав будь-яку відмову писаря як `ssot_write_failed`, а `commit_final_bar` далі безумовно
писав Redis-снапшот і публікував подію. H4/D1 поза сезонною сіткою (або без правила якоря) жив в UI до рестарту, а на
диску його не було: split-brain. Тепер відмова — `CommitResult(False, "bar_off_season_grid" | "anchor_rule_missing")`
до будь-якого запису, з WARNING і лічильником writer_drops.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from core.config_loader import htf_anchor_rule_resolver
from core.model.bars import CandleBar
from core.session_anchor import H4_S
from runtime.store.ssot_jsonl import JsonlAppender
from runtime.store.uds import UnifiedDataStore

_CFG = {
    "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": "ny_close_us_dst"}},
    "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23", "HKG33": "cfd_hk_main"},
}


def _utc_ms(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp() * 1000)


# Ср 01.07.2026 — літо США: H4 на 21/01/05/.. UTC, 22:00 — зимова сітка
SUMMER_H4 = _utc_ms(2026, 7, 1, 21)
WINTER_GRID_H4 = _utc_ms(2026, 7, 1, 22)


class _DiskStub:
    def last_open_ms(self, symbol: str, tf_s: int):
        _ = symbol, tf_s
        return None


def _h4(symbol: str, open_ms: int) -> CandleBar:
    return CandleBar(symbol=symbol, tf_s=H4_S, open_time_ms=open_ms, close_time_ms=open_ms + H4_S * 1000,
                     o=100.0, h=101.0, low=99.0, c=100.5, v=10.0, complete=True, src="derived")


def _writer_uds(root: Path, anchor_rule_for_symbol):
    """Писар UDS зі справжнім JsonlAppender і моками всіх Redis-шляхів: снапшот, pubsub, preview ring."""
    redis_writer, updates_bus, redis_layer = Mock(), Mock(), Mock()
    uds = UnifiedDataStore(
        data_root=str(root), boot_id="test-boot", tf_allowlist={H4_S}, min_coldload_bars={H4_S: 1},
        role="writer", disk_layer=_DiskStub(), redis_layer=redis_layer,
        jsonl_appender=JsonlAppender(str(root), anchor_rule_for_symbol=anchor_rule_for_symbol),
        redis_snapshot_writer=redis_writer, updates_bus=updates_bus, preview_tf_allowlist={H4_S},
    )
    return uds, (redis_writer.put_bar, updates_bus.publish, redis_layer.publish_preview_event)


def _commit_rejected(uds: UnifiedDataStore, bar: CandleBar, caplog):
    obs = Mock()
    with patch("runtime.store.uds._OBS", obs), caplog.at_level(logging.WARNING, logger="uds"):
        result = uds.commit_final_bar(bar)
    return result, obs


def test_uds_commit_off_season_grid_skips_redis_and_pubsub(tmp_path: Path, caplog):
    uds, writes = _writer_uds(tmp_path, htf_anchor_rule_resolver(_CFG))

    result, obs = _commit_rejected(uds, _h4("XAU/USD", WINTER_GRID_H4), caplog)

    assert (result.ok, result.reason, result.ssot_written, result.redis_written, result.updates_published) == (
        False, "bar_off_season_grid", False, False, False)
    for write in writes:
        write.assert_not_called()
    obs.inc_writer_drop.assert_called_once_with("bar_off_season_grid", H4_S)
    assert "reason=bar_off_season_grid" in caplog.text and "expected_open_ms=%d" % SUMMER_H4 in caplog.text
    assert uds.get_watermark_open_ms("XAU/USD", H4_S) is None
    assert not list(tmp_path.rglob("part-*.jsonl"))

    # Контроль харнесу: бар на сезонній сітці проходить увесь шлях — інакше «not_called» вище нічого не доводить
    on_grid = uds.commit_final_bar(_h4("XAU/USD", SUMMER_H4))
    assert (on_grid.ok, on_grid.redis_written, on_grid.updates_published) == (True, True, True)
    for write in writes:
        write.assert_called_once()
    assert uds.get_watermark_open_ms("XAU/USD", H4_S) == SUMMER_H4


@pytest.mark.parametrize("symbol, resolver", [
    ("XAU/USD", None),  # писар без резолвера
    ("HKG33", htf_anchor_rule_resolver(_CFG)),  # група календаря без виміряної сітки (ADR-0095 §8.4)
])
def test_uds_commit_anchor_rule_missing_skips_redis_and_pubsub(tmp_path: Path, caplog, symbol, resolver):
    uds, writes = _writer_uds(tmp_path, resolver)

    result, obs = _commit_rejected(uds, _h4(symbol, SUMMER_H4), caplog)

    assert (result.ok, result.reason, result.redis_written, result.updates_published) == (
        False, "anchor_rule_missing", False, False)
    for write in writes:
        write.assert_not_called()
    obs.inc_writer_drop.assert_called_once_with("anchor_rule_missing", H4_S)
    assert "reason=anchor_rule_missing" in caplog.text and symbol in caplog.text


@patch("runtime.store.uds.build_redis_snapshot_writer", return_value=None)
@patch("runtime.store.uds._redis_layer_from_cfg", return_value=None)
@patch("runtime.store.uds._updates_bus_from_cfg", return_value=None)
@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]"])
def test_build_uds_writer_refuses_unreadable_config(_bus, _redis, _snap, tmp_path: Path, caplog, content):
    """Писар SSOT не стартує з {} замість config: нема файла, битий JSON, не-об'єкт — ValueError. Читач деградує гучно."""
    from runtime.store.uds import build_uds_from_config

    cfg_path = tmp_path / "config.json"
    if content is not None:
        cfg_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="UDS_CONFIG_(UNREADABLE|NOT_OBJECT)"):
        build_uds_from_config(str(cfg_path), str(tmp_path / "data"), "boot-s3b", writer_components=True)

    with caplog.at_level(logging.WARNING, logger="uds"):
        reader = build_uds_from_config(str(cfg_path), str(tmp_path / "data"), "boot-s3b", role="reader")
    assert reader._jsonl is None  # noqa: SLF001
    assert "UDS_CONFIG_" in caplog.text
