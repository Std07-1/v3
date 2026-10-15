"""Журнал точності системного наративу (ADR-0113 Ф4): живий прогін — рядок розбору на закритті бару кроку, записаний,
коли рушій уже має бари всіх TF того моменту; формат — той самий, що в tools.narrative_replay."""
from __future__ import annotations

import json
import os

from runtime.smc.narrative_journal import NarrativeJournal
from test_narrative_now_ms import WED_ASIA, XAU, runner  # noqa: F401 (фікстура)

M15_MS, M1_MS = 900_000, 60_000
C = WED_ASIA  # момент закриття бару кроку (05:00 UTC, 14.10.2026)


def _journal(tmp_path, enabled=True, viewer_tfs=(300, 900)):
    cfg = {"smc": {"narrative_journal": {"enabled": enabled, "path": str(tmp_path / "nj"), "step_tf_s": 900,
                                         "viewer_tfs": list(viewer_tfs)}}}
    return NarrativeJournal(cfg)


def _rows(tmp_path, day="2026-10-14"):
    path = tmp_path / "nj" / (day + ".jsonl")
    return [json.loads(line) for line in open(path, encoding="utf-8")] if path.exists() else []


def test_step_moment_is_written_once_after_the_first_bar_opened_at_or_after_it(tmp_path):
    journal = _journal(tmp_path)
    calls = []

    def build(symbol, tf_s, price, now_ms):
        calls.append((symbol, tf_s, price, now_ms))
        return {"t": now_ms, "symbol": symbol, "tf_s": tf_s, "price": price, "narrative": {}}

    journal.after_bar(XAU, 900, C, 4110.5, complete=True)
    journal.before_bar(XAU, C - M1_MS, build)  # бар того самого моменту (M1, що закрився в C) — ще не час
    assert calls == [] and _rows(tmp_path) == []

    journal.before_bar(XAU, C, build)  # перший бар після моменту — розбір усіх TF глядача
    journal.before_bar(XAU, C + M1_MS, build)  # повтор — нічого
    journal.after_bar(XAU, 900, C, 4110.5, complete=True)  # той самий бар іншим шляхом подачі — не вдруге
    journal.before_bar(XAU, C + 2 * M1_MS, build)

    assert calls == [(XAU, 300, 4110.5, C), (XAU, 900, 4110.5, C)]
    assert [(r["tf_s"], r["t"]) for r in _rows(tmp_path)] == [(300, C), (900, C)]


def test_only_complete_step_bars_and_only_when_enabled(tmp_path):
    journal = _journal(tmp_path)
    off = _journal(tmp_path / "off", enabled=False)
    calls = []

    def build(symbol, tf_s, price, now_ms):
        calls.append(tf_s)
        return {"t": now_ms}

    journal.after_bar(XAU, 300, C, 1.0, complete=True)  # не TF кроку
    journal.after_bar(XAU, 900, C, 1.0, complete=False)  # preview
    off.after_bar(XAU, 900, C, 1.0, complete=True)
    journal.before_bar(XAU, C, build)
    off.before_bar(XAU, C, build)

    assert calls == [] and not os.path.exists(tmp_path / "off" / "nj")


def test_failing_row_is_loud_and_does_not_stop_the_other_timeframes(tmp_path, caplog):
    journal = _journal(tmp_path)

    def build(symbol, tf_s, price, now_ms):
        if tf_s == 300:
            raise RuntimeError("boom")
        return {"t": now_ms, "tf_s": tf_s}

    journal.after_bar(XAU, 900, C, 1.0, complete=True)
    journal.before_bar(XAU, C, build)

    assert [r["tf_s"] for r in _rows(tmp_path)] == [900]
    assert "NARRATIVE_JOURNAL_ROW_FAILED" in caplog.text


def test_runner_writes_exactly_the_replay_row_of_that_moment(runner, tmp_path):  # noqa: F811
    """Живий шлях: закритий M15 через on_bar_dict, далі M1 наступної хвилини — у журналі той самий рядок, що
    `narrative_row` (формат tools.narrative_replay) дав би на цей момент."""
    runner._narrative_journal = _journal(tmp_path, viewer_tfs=(300, 900, 3600, 14400))
    m15 = {"open_time_ms": C - M15_MS, "o": 4110.0, "h": 4114.0, "low": 4106.0, "c": 4111.0, "v": 10.0,
           "complete": True}
    runner.on_bar_dict(XAU, 900, m15)
    expected = [runner.narrative_row(XAU, tf_s, 4111.0, C) for tf_s in (300, 900, 3600, 14400)]
    next_m1 = {"open_time_ms": C, "o": 4111.0, "h": 4112.0, "low": 4110.0, "c": 4111.5, "v": 1.0, "complete": True}

    runner.feed_m1_bar_dict(XAU, next_m1)

    rows = _rows(tmp_path)
    assert rows == [json.loads(json.dumps(r, ensure_ascii=False, sort_keys=True)) for r in expected]
    assert all(r is not None and r["t"] == C for r in expected)
