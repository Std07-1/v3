"""Tests for signal_journal.py — restart recovery / dedup."""

import json
import os
import tempfile

from runtime.smc.signal_journal import SignalJournal


def _make_cfg(tmpdir: str) -> dict:
    return {
        "smc": {
            "signal_journal": {"enabled": True, "path": tmpdir},
        }
    }


class TestJournalRecovery:
    """Verify that SignalJournal recovers _last_state from today's file on init."""

    def test_no_duplicate_trade_entered_after_restart(self, tmp_path):
        """If today's journal has trade_entered for (sym, tf), a new init won't re-emit it."""
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        journal_file = tmp_path / f"journal-{today}.jsonl"

        # Pre-seed journal with one trade_entered
        entry = {
            "ts": f"{today}T15:00:00Z",
            "wall_ms": 1773680000000,
            "symbol": "XAU/USD",
            "tf_s": 14400,
            "event": "trade_entered",
            "mode": "trade",
            "sub_mode": "reduced",
            "headline": "test",
            "market_phase": "ranging",
            "price": 5012.0,
            "atr": 47.0,
            "direction": "short",
            "entry_desc": "OB 5041-5047",
            "trigger": "approaching",
            "trigger_desc": "29 pt",
            "target_desc": "PDL 5009",
            "invalidation": "Above 5047",
            "zone_id": "ob_bear_XAU_USD_14400_1773414000000",
            "session": "newyork",
            "in_killzone": False,
            "bias": {"3600": "bearish", "14400": "bearish"},
        }
        journal_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        # Init journal — should recover state
        cfg = _make_cfg(str(tmp_path))
        journal = SignalJournal(cfg)

        # Verify recovery
        key = ("XAU/USD", 14400)
        assert key in journal._last_state
        assert journal._last_state[key] == (
            "trade",
            "ob_bear_XAU_USD_14400_1773414000000",
            "approaching",
        )
        assert key in journal._active
        assert journal._active[key].zone_id == "ob_bear_XAU_USD_14400_1773414000000"
        assert journal._active[key].direction == "short"

        # Count lines before — should be 1
        lines_before = journal_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines_before) == 1

    def test_recovery_trade_exited_clears_active(self, tmp_path):
        """If journal has trade_entered then trade_exited, _active should be empty."""
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        journal_file = tmp_path / f"journal-{today}.jsonl"

        entered = {
            "symbol": "XAU/USD",
            "tf_s": 900,
            "event": "trade_entered",
            "mode": "trade",
            "zone_id": "ob_bull_XAU_USD_900_100",
            "trigger": "approaching",
            "price": 2860.0,
            "direction": "long",
            "target_desc": "PDH 2892",
            "invalidation": "Below 2855",
            "wall_ms": 1000,
            "sub_mode": "full",
            "session": "london",
            "bias": {},
        }
        exited = {
            "symbol": "XAU/USD",
            "tf_s": 900,
            "event": "trade_exited",
            "mode": "wait",
            "zone_id": "",
            "trigger": "",
        }
        lines = json.dumps(entered) + "\n" + json.dumps(exited) + "\n"
        journal_file.write_text(lines, encoding="utf-8")

        cfg = _make_cfg(str(tmp_path))
        journal = SignalJournal(cfg)

        key = ("XAU/USD", 900)
        # Last state should be wait (from trade_exited)
        assert journal._last_state[key] == ("wait", "", "")
        # Active should be cleared
        assert key not in journal._active

    def test_recovery_empty_journal(self, tmp_path):
        """No journal file → empty state (no crash)."""
        cfg = _make_cfg(str(tmp_path))
        journal = SignalJournal(cfg)
        assert len(journal._last_state) == 0
        assert len(journal._active) == 0

    def test_recovery_corrupted_line_skipped(self, tmp_path):
        """Corrupted JSON lines are skipped gracefully."""
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        journal_file = tmp_path / f"journal-{today}.jsonl"

        good = {
            "symbol": "XAU/USD",
            "tf_s": 3600,
            "event": "trade_entered",
            "mode": "trade",
            "zone_id": "ob_bull_XAU_USD_3600_200",
            "trigger": "in_zone",
            "price": 2870.0,
            "direction": "long",
            "target_desc": "",
            "invalidation": "",
            "wall_ms": 2000,
            "sub_mode": "full",
            "session": "london",
            "bias": {},
        }
        content = "NOT VALID JSON\n" + json.dumps(good) + "\n" + "{broken\n"
        journal_file.write_text(content, encoding="utf-8")

        cfg = _make_cfg(str(tmp_path))
        journal = SignalJournal(cfg)

        key = ("XAU/USD", 3600)
        assert key in journal._last_state
        assert journal._active[key].direction == "long"
