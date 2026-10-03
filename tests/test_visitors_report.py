"""ADR-0105 S2: зведення відвідувачів — візити, людина/бот, нові/повторні, свої, ретеншн, текст."""

from __future__ import annotations

import json
from zoneinfo import ZoneInfo

import pytest

from tools.visitors.aggregate import build_visitors, is_human_session, resolve_label, summarize
from tools.visitors.report import expired_files, load_labels, load_records, main, render

KYIV = ZoneInfo("Europe/Kyiv")
MIN = 60_000
HOUR = 60 * MIN
DAY = 24 * HOUR
T0 = 1790690400000  # 2026-09-29 14:00 UTC = 17:00 Київ
HUMAN_DEVICE = {"os": "Windows", "browser": "Chrome", "mobile": False}


def session(vid, start_ms, minutes, views=("XAU/USD:M30",), bot_ua=False, country="UA"):
    return {
        "vid": vid,
        "start_ms": start_ms,
        "end_ms": start_ms + int(minutes * MIN),
        "duration_s": minutes * 60.0,
        "country": country,
        "device": HUMAN_DEVICE,
        "bot_ua": bot_ua,
        "views": list(views),
    }


@pytest.mark.parametrize(
    "record, human",
    [
        (session("a", T0, 5), True),
        (session("a", T0, 0.2), False),  # коротка з одним переглядом (UI сам відновив пару)
        (session("a", T0, 0.2, views=("XAU/USD:M30", "XAU/USD:M15")), True),
        (session("a", T0, 30, bot_ua=True), False),
    ],
)
def test_is_human_session_needs_time_or_two_views_and_non_bot_ua(record, human):
    assert is_human_session(record, 30) is human


def test_build_visitors_merges_reconnects_and_tabs_without_double_counting():
    records = [
        session("a" * 32, T0, 10),
        session("a" * 32, T0 + 2 * MIN, 5, views=("NAS100:H1",)),  # друга вкладка всередині першої
        session("a" * 32, T0 + 20 * MIN, 5),  # перепідключення через 10 хв — той самий візит
        session("a" * 32, T0 + 3 * HOUR, 4),  # новий візит
    ]
    visitor = build_visitors(records, 30, 30)["a" * 32]
    assert [(v.number, v.active_s) for v in visitor.visits] == [(1, 900.0), (2, 240.0)]
    assert visitor.visits[0].views == ("XAU/USD:M30", "NAS100:H1")
    assert visitor.human is True


def test_summarize_splits_new_returning_and_bots_excluding_own_everywhere():
    returning, new, own, bot = "r" * 32, "n" * 32, "0" * 32, "b" * 32
    records = [
        session(returning, T0 - 10 * DAY, 20),
        session(returning, T0 + HOUR, 15),
        session(new, T0 + 2 * HOUR, 25),
        session(own, T0 + 3 * HOUR, 40),
        session("0f" * 16, T0 + 3 * HOUR, 0.2, views=()),  # своя коротка перевірка — не бот
        session(bot, T0 + 4 * HOUR, 1, bot_ua=True),
        session("s" * 32, T0 + 5 * HOUR, 0.1),  # коротка без переглядів, UA звичайний
    ]
    visitors = build_visitors(records, 30, 30)
    labels = {"000": {"name": "мій ПК", "own": True}, "0f0f": {"name": "перевірка", "own": True}}
    digest = summarize(visitors, records, (T0, T0 + DAY), labels)
    assert [row.visitor.vid for row in digest.new] == [new]
    assert [row.visitor.vid for row in digest.returning] == [returning]
    assert digest.own_visits == 2
    assert sorted(digest.own) == [("0000", "мій ПК"), ("0f0f", "перевірка")]
    assert (digest.bot_sessions_ua, digest.bot_sessions_short) == (1, 1)
    assert (digest.humans_7d, digest.humans_30d, digest.humans_all) == (2, 2, 2)
    assert digest.first_human_ms == T0 - 10 * DAY


def test_home_network_devices_are_own_without_labels():
    home_pc, phone, stranger = "h" * 32, "p" * 32, "x" * 32
    records = [
        dict(session(home_pc, T0, 60), home_net=True),
        dict(session(phone, T0 + HOUR, 10), home_net=False),  # той самий телефон удома й на мобільному
        dict(session(phone, T0 + 3 * HOUR, 10), home_net=True),
        dict(session(stranger, T0 + 5 * HOUR, 10), home_net=False),
    ]
    digest = summarize(build_visitors(records, 30, 30), records, (T0, T0 + DAY), {})
    assert [row.visitor.vid for row in digest.new] == [stranger]
    assert sorted(digest.own) == [("hhhh", "дім"), ("pppp", "дім")] and digest.own_visits == 3
    assert digest.humans_all == 1


def test_overlapping_visits_of_different_people_are_marked_together():
    pc, phone, later = "9811" + "0" * 28, "fe98" + "0" * 28, "c" * 32
    records = [
        session(pc, T0, 6, country="DK"),
        session(phone, T0 + 2 * MIN, 3, country="TR"),
        session(later, T0 + 2 * HOUR, 5),
    ]
    digest = summarize(build_visitors(records, 30, 30), records, (T0, T0 + DAY), {})
    together = {row.visitor.vid[:4]: row.together for row in digest.new}
    assert together == {"9811": ["fe98"], "fe98": ["9811"], "cccc": []}
    text = render(digest, 24, KYIV, 0, None)
    assert "· одночасно з #fe98" in text and "· одночасно з #9811" in text


def test_own_line_names_devices():
    records = [dict(session("d8ec" + "0" * 28, T0, 2), home_net=False), dict(session("9868" + "0" * 28, T0, 30), home_net=True)]
    labels = {"d8ec": {"name": "телефон", "own": True}}
    digest = summarize(build_visitors(records, 30, 30), records, (T0, T0 + DAY), labels)
    (line,) = [l for l in render(digest, 24, KYIV, 0, None).splitlines() if l.startswith("Свої")]
    assert line.startswith("Свої: візитів 2 (") and "#d8ec телефон" in line and "#9868 дім" in line


def test_resolve_label_prefers_longest_prefix():
    labels = {"a1": {"name": "коротка"}, "a1f3": {"name": "довга"}}
    assert resolve_label("a1f3" + "0" * 28, labels)["name"] == "довга"
    assert resolve_label("a100" + "0" * 28, labels)["name"] == "коротка"
    assert resolve_label("ffff" + "0" * 28, labels) == {}


def test_render_shows_local_times_statuses_and_bot_line():
    returning, new = "7c20" + "0" * 28, "a1f3" + "0" * 28
    records = [
        session(returning, T0 - 13 * DAY, 20, country="CZ"),
        session(returning, T0 + 2 * HOUR, 12, country="CZ"),
        session(new, T0 + 10 * MIN, 25, views=("XAU/USD:M30", "XAU/USD:M15")),
        session("b" * 32, T0 + 4 * HOUR, 1, bot_ua=True),
    ]
    visitors = build_visitors(records, 30, 30)
    digest = summarize(visitors, records, (T0, T0 + 6 * HOUR), {"a1f3": {"name": "Юра"}})
    text = render(digest, 6, KYIV, bad_lines=2, labels_problem=None)
    assert text.splitlines()[0] == "Відвідувачі за 6 год: 29.09 17:00 → 29.09 23:00 (UTC+3)"
    assert "Люди: 2 — нових 1, повторних 1 · візитів 2 · разом 37 хв" in text
    assert "#a1f3 Юра · UA · Windows/Chrome — новий · 17:10–17:35 25 хв · XAU/USD:M30, XAU/USD:M15" in text
    assert "#7c20 · CZ · Windows/Chrome — з нами з 16.09 (13 дн.), візит 2 · 19:00–19:12 12 хв" in text
    assert "Схоже на ботів: сесій 1 (бот-UA 1, короткі без переглядів 0)" in text
    assert "7 днів: людей 2 · 30 днів: 2 · усього: 2 (журнал з 16.09.2026)" in text
    assert "пропущено зіпсованих рядків журналу — 2" in text


def test_render_without_humans_says_so():
    digest = summarize({}, [], (T0, T0 + DAY), {})
    assert "Людей за період не було." in render(digest, 24, KYIV, 0, None)


def test_render_caps_rows_per_group_for_telegram_limit():
    records = [session(c * 32, T0 + i * HOUR, 5) for i, c in enumerate("abc")]
    digest = summarize(build_visitors(records, 30, 30), records, (T0, T0 + DAY), {})
    text = render(digest, 24, KYIV, 0, None, max_rows=1)
    assert text.count("  #") == 1 and "…ще 2" in text


def test_main_ignores_internal_connections_entirely(tmp_path, capsys):
    probe = dict(session("p" * 32, T0, 0.01, views=(), bot_ua=True, country=None), internal=True)
    person = dict(session("a" * 32, T0, 5), internal=False)
    (tmp_path / "sessions-202609.jsonl").write_text(
        "\n".join(json.dumps(r) for r in (probe, probe, person)) + "\n", encoding="utf-8"
    )
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"visitors": {"enabled": True, "dir": str(tmp_path)}}), encoding="utf-8")
    assert main(["--config", str(config), "--now-ms", str(T0 + HOUR)]) == 0
    out = capsys.readouterr().out
    assert "Люди: 1 — нових 1" in out and "Схоже на ботів: сесій 0" in out


def test_main_rejects_non_positive_max_rows(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"visitors": {"enabled": True, "dir": str(tmp_path)}}), encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(config), "--max-rows", "0"])
    assert exit_info.value.code == 2


def test_load_records_counts_bad_lines_and_ignores_foreign_files(tmp_path):
    good = session("a" * 32, T0, 5)
    (tmp_path / "sessions-202609.jsonl").write_text(
        json.dumps(good) + "\n" + "{not json\n" + json.dumps({"vid": 1, "start_ms": 1, "end_ms": 2}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "labels.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.jsonl").write_text("garbage\n", encoding="utf-8")
    records, bad_lines = load_records(str(tmp_path))
    assert records == [good] and bad_lines == 2


def test_load_labels_reports_invalid_file(tmp_path):
    assert load_labels(str(tmp_path)) == ({}, None)
    (tmp_path / "labels.json").write_text("[1]", encoding="utf-8")
    labels, problem = load_labels(str(tmp_path))
    assert labels == {} and "labels.json" in problem


def test_expired_files_only_whole_months_older_than_retention(tmp_path):
    for name in ("sessions-202508.jsonl", "sessions-202509.jsonl", "sessions-202609.jsonl", "labels.json"):
        (tmp_path / name).write_text("", encoding="utf-8")
    # T0 = 29.09.2026; 365 діб тому = 29.09.2025 → серпень 2025 цілком старший, вересень 2025 — ні
    assert [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in expired_files(str(tmp_path), T0, 365)] == [
        "sessions-202508.jsonl"
    ]


def test_main_prints_digest_and_applies_retention(tmp_path, capsys):
    journal = tmp_path / "visitors"
    journal.mkdir()
    (journal / "sessions-202508.jsonl").write_text("", encoding="utf-8")
    (journal / "sessions-202609.jsonl").write_text(json.dumps(session("a" * 32, T0, 5)) + "\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"visitors": {"enabled": True, "dir": str(journal)}}), encoding="utf-8")
    assert main(["--config", str(config), "--now-ms", str(T0 + HOUR), "--apply-retention"]) == 0
    out, err = capsys.readouterr()
    assert "Люди: 1 — нових 1" in out
    assert "VISITORS_RETENTION_REMOVED" in err and not (journal / "sessions-202508.jsonl").exists()


def test_main_refuses_without_journal(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"visitors": {"enabled": True, "dir": str(tmp_path / "missing")}}), encoding="utf-8")
    assert main(["--config", str(config)]) == 2
    assert "VISITORS_REPORT_NO_JOURNAL" in capsys.readouterr().err
