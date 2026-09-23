"""commit_final_bar: бар, який писар SSOT відкинув через сезонну сітку, не потрапляє ні в Redis, ні в pubsub (ADR-0095 §3.3).

Раніше `_append_to_disk` ковтав будь-яку відмову писаря як `ssot_write_failed`, а `commit_final_bar` далі безумовно
писав Redis-снапшот і публікував подію. H4/D1 поза сезонною сіткою (або без правила якоря) жив в UI до рестарту, а на
диску його не було: split-brain. Тепер відмова — `CommitResult(False, "bar_off_season_grid" | "anchor_rule_missing")`
до будь-якого запису, з WARNING і лічильником writer_drops.

Те саме для БУДЬ-ЯКОГО ValueError писаря (W1fix): геометрія I2 (`bar_close_time_invalid`, `bar_bucket_misaligned`),
`derived_1m_forbidden`, `SSOT_PATH_TRAVERSAL`, невідомий ValueError (`ssot_value_error`) — бару немає на диску, отже
немає і в Redis/pubsub/preview ring.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Optional
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
M1_S = 60


class _DiskStub:
    def last_open_ms(self, symbol: str, tf_s: int):
        _ = symbol, tf_s
        return None


def _bar(symbol: str, tf_s: int, open_ms: int, *, close_ms: Optional[int] = None, src: str = "history") -> CandleBar:
    close = open_ms + tf_s * 1000 if close_ms is None else close_ms
    return CandleBar(symbol=symbol, tf_s=tf_s, open_time_ms=open_ms, close_time_ms=close,
                     o=100.0, h=101.0, low=99.0, c=100.5, v=10.0, complete=True, src=src)


def _h4(symbol: str, open_ms: int) -> CandleBar:
    return _bar(symbol, H4_S, open_ms, src="derived")


def _writer_uds(root: Path, anchor_rule_for_symbol, jsonl_appender=None):
    """Писар UDS зі справжнім JsonlAppender і моками всіх Redis-шляхів: снапшот, pubsub, preview ring."""
    redis_writer, updates_bus, redis_layer = Mock(), Mock(), Mock()
    if jsonl_appender is None:
        jsonl_appender = JsonlAppender(str(root), anchor_rule_for_symbol=anchor_rule_for_symbol)
    uds = UnifiedDataStore(
        data_root=str(root), boot_id="test-boot", tf_allowlist={M1_S, H4_S}, min_coldload_bars={M1_S: 1, H4_S: 1},
        role="writer", disk_layer=_DiskStub(), redis_layer=redis_layer, jsonl_appender=jsonl_appender,
        redis_snapshot_writer=redis_writer, updates_bus=updates_bus, preview_tf_allowlist={M1_S, H4_S},
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


# Відмова справжнього JsonlAppender -> (код, відкинутий бар, контрольний бар того самого TF, що проходить увесь шлях)
WRITER_VALUE_ERRORS = {
    # H4 на сезонній сітці, але обрубок close = open + 3 год (I2: close = open + tf)
    "bar_close_time_invalid": (_bar("XAU/USD", H4_S, SUMMER_H4, close_ms=SUMMER_H4 + 3 * 3600_000, src="derived"),
                               _h4("XAU/USD", SUMMER_H4)),
    "bar_bucket_misaligned": (_bar("XAU/USD", M1_S, SUMMER_H4 + 30_000), _bar("XAU/USD", M1_S, SUMMER_H4)),
    "derived_1m_forbidden": (_bar("XAU/USD", M1_S, SUMMER_H4, src="derived"), _bar("XAU/USD", M1_S, SUMMER_H4)),
    # символ ".." виводить шлях part-файла за межі data_root (SEC-02)
    "ssot_path_traversal": (_bar("..", M1_S, SUMMER_H4), _bar("XAU/USD", M1_S, SUMMER_H4)),
}


def _assert_rejected_before_redis(result, obs, writes, caplog, reason: str, tf_s: int) -> None:
    assert (result.ok, result.reason, result.ssot_written, result.redis_written, result.updates_published) == (
        False, reason, False, False, False)
    assert reason in result.warnings
    for write in writes:
        write.assert_not_called()
    obs.inc_writer_drop.assert_called_once_with(reason, tf_s)
    assert "reason=%s" % reason in caplog.text and "Redis/pubsub не записано" in caplog.text


@pytest.mark.parametrize("reason", sorted(WRITER_VALUE_ERRORS))
def test_uds_commit_any_writer_value_error_skips_redis_and_pubsub(tmp_path: Path, caplog, reason):
    """Не лише сітка і правило: будь-який ValueError писаря — відмова до Redis/pubsub/preview ring (W1fix)."""
    rejected, control = WRITER_VALUE_ERRORS[reason]
    data_root = tmp_path / "data_v3"  # ".." з data_root лишається всередині tmp_path — перевірка диска нижче чесна
    data_root.mkdir()
    uds, writes = _writer_uds(data_root, htf_anchor_rule_resolver(_CFG))

    result, obs = _commit_rejected(uds, rejected, caplog)

    _assert_rejected_before_redis(result, obs, writes, caplog, reason, rejected.tf_s)
    assert uds.get_watermark_open_ms(rejected.symbol, rejected.tf_s) is None
    assert not list(tmp_path.rglob("part-*.jsonl"))

    # Контроль харнесу: коректний бар того самого TF проходить увесь шлях, тож «not_called» вище щось доводить
    ok = uds.commit_final_bar(control)
    assert (ok.ok, ok.redis_written, ok.updates_published) == (True, True, True)
    for write in writes:
        write.assert_called_once()
    assert uds.get_watermark_open_ms(control.symbol, control.tf_s) == control.open_time_ms


def test_uds_commit_unknown_writer_value_error_is_rejected_as_ssot_value_error(tmp_path: Path, caplog):
    """ValueError без відомого коду (напр. запис у закритий файл) — теж відмова, мітка writer_drops обмежена."""
    appender = Mock(spec=["append", "close", "drop_preview_total"])
    appender.append.side_effect = ValueError("I/O operation on closed file.")
    uds, writes = _writer_uds(tmp_path, None, jsonl_appender=appender)
    bar = _bar("XAU/USD", M1_S, SUMMER_H4)

    result, obs = _commit_rejected(uds, bar, caplog)

    _assert_rejected_before_redis(result, obs, writes, caplog, "ssot_value_error", M1_S)
    assert "I/O operation on closed file." in caplog.text
    assert uds.get_watermark_open_ms("XAU/USD", M1_S) is None


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
