"""Сезонний календар групи (ADR-0095 §3.5, слайс S6a): розклад summer/winter за датою хвилини, а не ранбуком.

Значення зими — з таблиць `docs/runbooks/dst_transition.md` (cfd_us :107-114, fx :116-122, EU :231-234).
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.session_anchor import RULE_NY_CLOSE_US_DST, calendar_season, season_label
from runtime.ingest.market_calendar import SeasonalMarketCalendar
from runtime.ingest.tick_common import SEASON_RULE_KEY, calendar_for_symbol, calendar_from_group

REPO = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def _repo_cfg() -> dict:
    return json.loads((REPO / "config.json").read_text(encoding="utf-8"))


REPO_CFG = _repo_cfg()


def _trading(symbol: str, *moments) -> list:
    calendar = calendar_for_symbol(REPO_CFG, symbol)
    return [calendar.is_trading_minute(_ms(*moment)) for moment in moments]


# --- Сторож дубліката: плоскі поля = summer до S6b -------------------------------------------------------------------


def test_repo_config_seasonal_group_flat_fields_equal_summer_until_s6b():
    """Плоскі поля сезонної групи — живий календар (споживачі до S6b), блок summer — вхід calendar_for_symbol.

    Дублікат тимчасовий: S6b (дедлайн 25.10.2026, ЄС переходить на зиму; США — 01.11.2026) переводить живих
    споживачів на calendar_for_symbol і прибирає плоскі поля сезонних груп разом із цим тестом. Поки дубліката не
    прибрано, розсинхрон не має пройти тихо (D15.2). Якщо 25.10 настало без S6b і ранбук перемкнув плоскі поля на
    зиму — цей тест червоніє: S6b прострочено, а не тест застарів.
    """
    seasonal = 0
    for name, group in REPO_CFG["market_calendar_by_group"].items():
        if group[SEASON_RULE_KEY] == "none":
            continue
        seasonal += 1
        summer = group["summer"]
        flat_schedule = {key: value for key, value in group.items() if key in summer}
        assert flat_schedule == summer, name
        assert calendar_from_group(group) == calendar_from_group(summer), name
    assert seasonal == 4  # fx, cfd_us, EUSTX50, GER30


def test_repo_config_every_group_declares_season_rule_and_every_symbol_builds():
    """Невідома чи відсутня season_rule — ValueError фабрики; S6b не має натрапити на неї в живому процесі."""
    rules = {name: group.get(SEASON_RULE_KEY) for name, group in REPO_CFG["market_calendar_by_group"].items()}
    assert rules == {
        "fx_24x5_utc_summer": "us",
        "cfd_us_22_23": "us",
        "cfd_hk_main": "none",
        "crypto_24x7": "none",
        "cfd_eu_eustx50": "eu",
        "cfd_eu_ger30": "eu",
    }
    for symbol in REPO_CFG["market_calendar_symbol_groups"]:
        assert calendar_for_symbol(REPO_CFG, symbol).enabled is True, symbol


# --- cfd_us і FX: момент переходу DST США -----------------------------------------------------------------------------


def test_cfd_us_winter_sunday_opens_2300_not_2200_from_2026_11_01():
    """Ранбук :113: зимою відкриття вихідних 23:00 UTC; плоский (літній) календар відкрив би о 22:00."""
    xau = calendar_for_symbol(REPO_CFG, "XAU/USD")
    sun_2200, sun_2300 = _ms(2026, 11, 1, 22), _ms(2026, 11, 1, 23)
    assert xau.season_of(sun_2200) == "winter"
    assert (xau.is_trading_minute(sun_2200), xau.is_trading_minute(sun_2300)) == (False, True)
    flat = calendar_from_group(REPO_CFG["market_calendar_by_group"]["cfd_us_22_23"])
    assert flat.is_trading_minute(sun_2200) is True


def test_cfd_us_autumn_weekend_friday_on_summer_monday_on_winter_break():
    """Пт 30.10 — ще EDT (закриття 20:45); Пн 02.11 — EST: перерва 22:00–23:00, 21:30 торгова."""
    assert _trading("XAU/USD", (2026, 10, 30, 20, 44), (2026, 10, 30, 20, 45)) == [True, False]
    assert _trading(
        "XAU/USD", (2026, 11, 2, 21, 30), (2026, 11, 2, 22, 0), (2026, 11, 2, 22, 59), (2026, 11, 2, 23, 0)
    ) == [True, False, False, True]
    assert _trading("XAU/USD", (2026, 11, 6, 21, 44), (2026, 11, 6, 21, 45)) == [True, False]


def test_cfd_us_spring_2026_03_08_sunday_opens_2200_on_summer():
    """Пт 06.03 — ще EST (закриття 21:45); Нд 08.03 — EDT: відкриття 22:00, перерва Пн 21:00–22:00."""
    assert _trading("XAU/USD", (2026, 3, 6, 21, 44), (2026, 3, 6, 21, 45)) == [True, False]
    assert _trading("XAU/USD", (2026, 3, 1, 22, 0), (2026, 3, 1, 23, 0)) == [False, True]
    assert _trading("XAU/USD", (2026, 3, 8, 21, 59), (2026, 3, 8, 22, 0)) == [False, True]
    assert _trading("XAU/USD", (2026, 3, 9, 21, 30), (2026, 3, 9, 22, 0)) == [False, True]


def _hours_of_months(years, months):
    for year in years:
        for month in months:
            moment = dt.datetime(year, month, 1, tzinfo=UTC)
            while moment.month == month:
                yield moment
                moment += dt.timedelta(hours=1)


def test_us_season_is_new_york_dst_instant_zoneinfo_witness_2007_2040():
    """Свідок tz-бази: сезон `us` = літній час Нью-Йорка щогодини березня й листопада 2007–2040 (02:00 NY)."""
    for moment in _hours_of_months(range(2007, 2041), (3, 11)):
        expected = "summer" if moment.astimezone(NY).utcoffset() == dt.timedelta(hours=-4) else "winter"
        assert calendar_season(int(moment.timestamp() * 1000), "us") == expected, moment


def test_eu_season_is_berlin_dst_instant_zoneinfo_witness_2007_2040():
    """Свідок tz-бази: сезон `eu` = літній час ЄС щогодини березня й жовтня 2007–2040 (01:00 UTC)."""
    for moment in _hours_of_months(range(2007, 2041), (3, 10)):
        expected = "summer" if moment.astimezone(BERLIN).utcoffset() == dt.timedelta(hours=2) else "winter"
        assert calendar_season(int(moment.timestamp() * 1000), "eu") == expected, moment


def test_cfd_us_dst_instant_equals_trading_day_season_every_minute_of_dst_weekends():
    """Для cfd_us сезон за моментом переходу і сезон торгового дня §3.1 дають ту саму торговість кожної хвилини:
    розбіжні години неділі (після 02:00 NY до відкриття доби) закриті в обох розкладах. Отже для металів та індексів
    вибір правила не змінює нічого, а для FX лише правило моменту правильне (тест нижче)."""
    xau = calendar_for_symbol(REPO_CFG, "XAU/USD")
    for year in range(2024, 2031):
        for month, nth in ((3, 2), (11, 1)):
            first = dt.date(year, month, 1)
            sunday = first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))
            start_ms = _ms(sunday.year, sunday.month, sunday.day) - 2 * 86_400_000
            for minute in range(4 * 1440):
                ts_ms = start_ms + minute * 60_000
                by_trading_day = xau.winter if season_label(ts_ms, RULE_NY_CLOSE_US_DST) == "winter" else xau.summer
                assert xau.is_trading_minute(ts_ms) == by_trading_day.is_trading_minute(ts_ms), (year, month, minute)


def test_fx_autumn_sunday_opens_at_1700_est_not_by_25h_summer_trading_day():
    """Нд 01.11.2026 21:30 UTC = 16:30 EST: FX закритий. Доба 31.10 (25 год) до 22:00 UTC ще «літня», і розклад за
    сезоном торгового дня відкрив би фантомні пів години (літнє відкриття 21:00 + перерва до 21:30)."""
    usd_jpy = calendar_for_symbol(REPO_CFG, "USD/JPY")
    sun_2130 = _ms(2026, 11, 1, 21, 30)
    assert season_label(sun_2130, RULE_NY_CLOSE_US_DST) == "summer" and usd_jpy.season_of(sun_2130) == "winter"
    assert usd_jpy.summer.is_trading_minute(sun_2130) is True
    assert _trading("USD/JPY", (2026, 11, 1, 21, 30), (2026, 11, 1, 21, 59), (2026, 11, 1, 22, 29),
                    (2026, 11, 1, 22, 30)) == [False, False, False, True]


def test_fx_friday_close_follows_season_runbook():
    """Ранбук :121: FX закривається в Пт 20:55 улітку і 21:55 узимку; весною Нд 08.03 відкривається о 21:30."""
    assert _trading("USD/JPY", (2026, 10, 30, 20, 54), (2026, 10, 30, 20, 55)) == [True, False]
    assert _trading("USD/JPY", (2026, 11, 6, 21, 54), (2026, 11, 6, 21, 55)) == [True, False]
    assert _trading("USD/JPY", (2026, 3, 8, 21, 29), (2026, 3, 8, 21, 30)) == [False, True]


# --- EU: правило ЄС, незалежне від США -------------------------------------------------------------------------------


def test_eu_switch_instants_match_runbook_dates():
    """Ранбук :236: ЄС перемикається 25.10.2026 і 28.03.2027 (остання неділя, 01:00 UTC); 29.03.2026 — весна."""
    for moment, expected in (
        ((2026, 3, 29, 0, 59), "winter"),
        ((2026, 3, 29, 1, 0), "summer"),
        ((2026, 10, 25, 0, 59), "summer"),
        ((2026, 10, 25, 1, 0), "winter"),
        ((2027, 3, 28, 0, 59), "winter"),
        ((2027, 3, 28, 1, 0), "summer"),
    ):
        assert calendar_season(_ms(*moment), "eu") == expected, moment


def test_eustx50_autumn_2026_10_25_winter_while_us_still_summer():
    """EUSTX50 Пн 26.10 — зима (07:00–21:00), а XAU того ж дня ще на літній перерві 21:00–22:00 (США — 01.11)."""
    assert _trading("EUSTX50", (2026, 10, 23, 19, 59), (2026, 10, 23, 20, 0)) == [True, False]
    assert _trading(
        "EUSTX50", (2026, 10, 26, 6, 59), (2026, 10, 26, 7, 0), (2026, 10, 26, 20, 30), (2026, 10, 26, 21, 0)
    ) == [False, True, True, False]
    assert _trading("EUSTX50", (2026, 10, 27, 6, 59), (2026, 10, 27, 7, 0)) == [False, True]  # денна перерва до 07:00
    assert _trading("XAU/USD", (2026, 10, 26, 21, 30)) == [False]


def test_eustx50_spring_2026_03_29_summer_opens_0600():
    """Пт 27.03 — ще зима (закриття 21:00); Пн 30.03 — літо: 06:00–20:00."""
    assert _trading("EUSTX50", (2026, 3, 27, 20, 30), (2026, 3, 27, 21, 0)) == [True, False]
    assert _trading(
        "EUSTX50", (2026, 3, 30, 5, 59), (2026, 3, 30, 6, 0), (2026, 3, 30, 19, 59), (2026, 3, 30, 20, 0)
    ) == [False, True, True, False]


def test_ger30_winter_closes_2100_opens_0030_all_year():
    """Ранбук :234: GER30 відкривається о 00:30 UTC цілий рік, узимку закривається о 21:00."""
    assert _trading("GER30", (2026, 10, 26, 0, 29), (2026, 10, 26, 0, 30), (2026, 10, 26, 20, 30)) == [
        False, True, True,
    ]
    assert _trading("GER30", (2026, 10, 23, 20, 0), (2026, 10, 30, 20, 59), (2026, 10, 30, 21, 0)) == [
        False, True, False,
    ]


def test_season_rule_none_is_one_schedule():
    """HK і crypto від DST не залежать: один розклад, сезон `none`."""
    for symbol in ("HKG33", "BTCUSDT"):
        calendar = calendar_for_symbol(REPO_CFG, symbol)
        assert calendar.summer is calendar.winter
        assert calendar.season_of(_ms(2026, 11, 2, 12)) == "none"


# --- Гучні відмови ---------------------------------------------------------------------------------------------------

_SUMMER = {
    "market_weekend_open_dow": 6, "market_weekend_open_hm": "22:00",
    "market_weekend_close_dow": 4, "market_weekend_close_hm": "20:45",
    "market_daily_break_start_hm": "21:00", "market_daily_break_end_hm": "22:00",
}
_WINTER = dict(_SUMMER, market_weekend_open_hm="23:00", market_weekend_close_hm="21:45",
               market_daily_break_start_hm="22:00", market_daily_break_end_hm="23:00")


def _cfg(group: dict) -> dict:
    return {"market_calendar_by_group": {"g": group}, "market_calendar_symbol_groups": {"X": "g"}}


@pytest.mark.parametrize(
    "group, code",
    [
        (dict(_SUMMER, summer=_SUMMER, winter=_WINTER), "CALENDAR_SEASON_RULE_INVALID"),
        (dict(_SUMMER, season_rule="asia", summer=_SUMMER, winter=_WINTER), "CALENDAR_SEASON_RULE_INVALID"),
        (dict(_SUMMER, season_rule="us", summer=_SUMMER), "CALENDAR_SEASON_BLOCKS_INVALID"),
        (dict(_SUMMER, season_rule="us", summer=_SUMMER, winter=dict(_WINTER, market_daily_break_start="22:00")),
         "CALENDAR_SEASON_BLOCKS_INVALID"),
        (dict(_SUMMER, season_rule="none", summer=_SUMMER), "CALENDAR_SEASON_BLOCKS_UNEXPECTED"),
        (dict(_SUMMER, season_rule="eu", summer={"market_weekend_open_hm": "06:00"},
              winter={"market_weekend_open_hm": "07:00"}), "CALENDAR_SCHEDULE_BUILD_FAILED"),
    ],
)
def test_incomplete_season_config_fails_loud_not_24x7(group, code):
    with pytest.raises(ValueError, match=code):
        calendar_for_symbol(_cfg(group), "X")


def test_symbol_without_group_fails_loud():
    with pytest.raises(ValueError, match="CALENDAR_GROUP_MISSING"):
        calendar_for_symbol(_cfg(dict(_SUMMER, season_rule="none")), "Y")


def test_seasonal_calendar_none_rule_rejects_two_schedules():
    summer, winter = calendar_from_group(_SUMMER), calendar_from_group(_WINTER)
    with pytest.raises(ValueError, match="CALENDAR_SEASON_RULE_NONE_TWO_SCHEDULES"):
        SeasonalMarketCalendar("none", summer=summer, winter=winter)


# --- Python 3.7 (граф імпорту брокера: broker_sidecar, tick_publisher_fxcm) -------------------------------------------

_PY37_FILES = ("core/session_anchor.py", "runtime/ingest/market_calendar.py", "runtime/ingest/tick_common.py")


@pytest.mark.parametrize("relpath", _PY37_FILES)
def test_calendar_modules_parse_as_python37(relpath):
    ast.parse((REPO / relpath).read_text(encoding="utf-8"), filename=relpath, feature_version=(3, 7))


def _python37() -> Path | None:
    candidates = [os.environ.get("V3_PY37_PYTHON", "")]
    candidates += [str(REPO / ".venv37" / "Scripts" / "python.exe"), str(REPO / ".venv37" / "bin" / "python")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def test_calendar_for_symbol_runs_under_python37():
    """Не лише синтаксис: .venv37 імпортує модулі й рахує зимову неділю cfd_us і сезон EUSTX50 на config репо."""
    python37 = _python37()
    if python37 is None:
        pytest.skip(".venv37 не знайдено (V3_PY37_PYTHON або <repo>/.venv37)")
    script = (
        "import json\n"
        "from runtime.ingest.tick_common import calendar_for_symbol\n"
        "cfg = json.load(open('config.json', encoding='utf-8'))\n"
        "xau = calendar_for_symbol(cfg, 'XAU/USD')\n"
        "eu = calendar_for_symbol(cfg, 'EUSTX50')\n"
        "print(xau.is_trading_minute(%d), xau.is_trading_minute(%d), eu.season_of(%d))\n"
        % (_ms(2026, 11, 1, 22), _ms(2026, 11, 1, 23), _ms(2026, 10, 26, 7))
    )
    done = subprocess.run(
        [str(python37), "-c", script], cwd=str(REPO), capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["False", "True", "winter"]
