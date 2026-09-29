"""ADR-0105 S1: журнал візитів — ключ, клас пристрою, запис JSONL, гучні збої, конфіг."""

from __future__ import annotations

import json
import logging

import pytest

from runtime.visitors.journal import (
    VISITOR_COOKIE,
    VisitorsJournal,
    classify_device,
    is_bot_user_agent,
    normalize_country,
    open_journal,
    visitor_cookie_attrs,
    visitors_policy,
)

UA_WIN_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
UA_IPHONE_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.6 Mobile/15E148 Safari/604.1"
)
UA_ANDROID_CHROME = (
    "Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
)
UA_WIN_EDGE = UA_WIN_CHROME + " Edg/140.0.0.0"
UA_MAC_SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15"
)
UA_HEADLESS = UA_WIN_CHROME.replace("Chrome/", "HeadlessChrome/")
START_MS = 1790000000000  # 2026-09-21 UTC
VID = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    "user_agent, expected",
    [
        (UA_WIN_CHROME, {"os": "Windows", "browser": "Chrome", "mobile": False}),
        (UA_IPHONE_SAFARI, {"os": "iOS", "browser": "Safari", "mobile": True}),
        (UA_ANDROID_CHROME, {"os": "Android", "browser": "Chrome", "mobile": True}),
        (UA_WIN_EDGE, {"os": "Windows", "browser": "Edge", "mobile": False}),
        (UA_MAC_SAFARI, {"os": "macOS", "browser": "Safari", "mobile": False}),
        ("", {"os": "?", "browser": "?", "mobile": False}),
    ],
)
def test_classify_device_distinguishes_lookalike_user_agents(user_agent, expected):
    assert classify_device(user_agent) == expected


@pytest.mark.parametrize(
    "user_agent, is_bot",
    [
        (UA_WIN_CHROME, False),
        (UA_IPHONE_SAFARI, False),
        (UA_HEADLESS, True),
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", True),
        ("curl/8.5.0", True),
        ("facebookexternalhit/1.1", True),
        ("", True),
    ],
)
def test_is_bot_user_agent_flags_self_declared_tools_and_empty(user_agent, is_bot):
    assert is_bot_user_agent(user_agent) is is_bot


@pytest.mark.parametrize("raw, expected", [("UA", "UA"), (" cz ", "CZ"), ("XX", None), ("T1", None), ("", None), (None, None)])
def test_normalize_country_keeps_only_real_iso_codes(raw, expected):
    assert normalize_country(raw) == expected


def test_start_reuses_valid_cookie_and_issues_new_otherwise(tmp_path):
    journal = VisitorsJournal(str(tmp_path), 20)
    returning = journal.start({VISITOR_COOKIE: VID}, {"User-Agent": UA_WIN_CHROME, "CF-IPCountry": "UA"}, START_MS)
    assert (returning.vid, returning.vid_issued, returning.country, returning.bot_ua) == (VID, False, "UA", False)
    for cookies in ({}, {VISITOR_COOKIE: "not-a-key"}, {VISITOR_COOKIE: VID.upper()}):
        fresh = journal.start(cookies, {}, START_MS)
        assert fresh.vid_issued is True
        assert fresh.vid != VID and len(fresh.vid) == 32
        assert fresh.bot_ua is True  # без User-Agent


def test_note_message_records_distinct_views_up_to_cap(tmp_path):
    visit = VisitorsJournal(str(tmp_path), 2).start({}, {}, START_MS)
    visit.note_message(None, None)
    visit.note_message("XAU/USD", "M30")
    visit.note_message("XAU/USD", "M30")
    visit.note_message("XAU/USD", "M15")
    visit.note_message("NAS100", "M15")
    assert visit.messages == 5
    assert visit.views == ["XAU/USD:M30", "XAU/USD:M15"]
    assert visit.views_dropped == 1


def test_finish_appends_monthly_jsonl_without_ip(tmp_path):
    journal = VisitorsJournal(str(tmp_path), 20)
    visit = journal.start({VISITOR_COOKIE: VID}, {"User-Agent": UA_IPHONE_SAFARI, "CF-IPCountry": "PL"}, START_MS)
    visit.note_message("XAU/USD", "M15")
    assert journal.finish(visit, "c1", START_MS + 95_400, 1000) is True
    assert journal.finish(visit, "c2", START_MS + 100_000, None) is True
    lines = (tmp_path / "sessions-202609.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record == {
        "vid": VID,
        "vid_issued": False,
        "client_id": "c1",
        "start_ms": START_MS,
        "end_ms": START_MS + 95_400,
        "duration_s": 95.4,
        "country": "PL",
        "device": {"os": "iOS", "browser": "Safari", "mobile": True},
        "bot_ua": False,
        "messages": 1,
        "views": ["XAU/USD:M15"],
        "views_dropped": 0,
        "close_code": 1000,
    }


def test_finish_write_failure_is_loud_and_counted(tmp_path, caplog):
    journal = VisitorsJournal(str(tmp_path / "missing"), 20)
    visit = journal.start({}, {}, START_MS)
    with caplog.at_level(logging.WARNING, logger="runtime.visitors.journal"):
        assert journal.finish(visit, "c1", START_MS + 1000, 1000) is False
    assert journal.write_failures == 1
    assert "VISITORS_JOURNAL_WRITE_FAIL" in caplog.text


def test_cookie_attrs_secure_only_for_https_origin():
    https = visitor_cookie_attrs("https://aione-smc.com")
    assert https == {"path": "/ws", "max_age": 365 * 24 * 3600, "httponly": True, "samesite": "Strict", "secure": True}
    assert visitor_cookie_attrs("http://localhost:5173")["secure"] is False
    assert visitor_cookie_attrs("")["secure"] is False


def test_visitors_policy_defaults_and_validation():
    assert visitors_policy({}).enabled is False
    policy = visitors_policy({"visitors": {"enabled": True, "dir": "/var/lib/x", "max_views_per_visit": 5}})
    assert (policy.enabled, policy.dir, policy.max_views_per_visit) == (True, "/var/lib/x", 5)
    for bad in ([], {"enabled": "yes"}, {"enabled": True, "dir": ""}, {"enabled": True, "dir": "/x", "max_views_per_visit": 0}):
        with pytest.raises(ValueError, match="CONFIG_VISITORS_INVALID"):
            visitors_policy({"visitors": bad})


def test_open_journal_reports_why_it_is_off(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="runtime.visitors.journal"):
        assert open_journal({"visitors": {"enabled": False, "dir": str(tmp_path)}}) is None
        assert open_journal({"visitors": {"enabled": True, "dir": str(tmp_path / "missing")}}) is None
        assert open_journal({"visitors": {"enabled": "yes"}}) is None
        assert open_journal({"visitors": {"enabled": True, "dir": str(tmp_path)}}) is not None
    for reason in ("reason=disabled", "reason=dir_missing", "reason=config_invalid", "VISITORS_JOURNAL_ON"):
        assert reason in caplog.text
