# v1 trading time & FXCM probe — факти (READ-ONLY)

## Calendar SSOT (overrides)
Джерело: `config/calendar_overrides.json`.

- `holidays`:
  - 2025-01-01, 2025-01-20, 2025-02-17, 2025-04-18, 2025-05-26, 2025-07-04, 2025-09-01, 2025-11-27, 2025-12-25. (config/calendar_overrides.json:2-11)
- `closed_intervals_utc`:
  - 2025-12-26T21:30:00Z → 2025-12-28T23:00:00Z. (config/calendar_overrides.json:13-19)
  - 2025-12-31T23:00:00Z → 2026-01-01T23:00:00Z. (config/calendar_overrides.json:20-24)
  - 2026-01-01T23:02:00Z → 2026-01-02T06:31:00Z. (config/calendar_overrides.json:25-30)
- `daily_breaks`:
  - 22:00–23:01 @ UTC. (config/calendar_overrides.json:27-29)
- `weekly_open_utc`:
  - 23:01 @ UTC. (config/calendar_overrides.json:30)
- `weekly_close_utc`:
  - 21:45 @ UTC. (config/calendar_overrides.json:31)
- `session_windows`:
  - TOKYO_METALS 08:00–16:00 Asia/Tokyo. (config/calendar_overrides.json:32-41)
  - LDN_METALS 08:00–16:30 Europe/London. (config/calendar_overrides.json:42-51)
  - NY_METALS 08:00–16:55 America/New_York. (config/calendar_overrides.json:52-61)

## Trading-time semantics
- `is_trading_time`:
  - відкидає `holidays` і `closed_intervals_utc`. (sessions.py:539-546)
  - перевіряє weekly open/close (локальна TZ, DST-aware). (sessions.py:549-563)
  - перевіряє `daily_breaks`. (sessions.py:565-567)
- `next_trading_open`:
  - крокає хвилинами до першої торгової хвилини. (sessions.py:570-578)
- `daily_boundary_open_utc_for_ts`:
  - DST-aware boundary за `get_primary_daily_break_anchor()`. (sessions.py:499-515)
- `expected_next_daily_boundary_utc`:
  - DST-aware наступна межа trading-day (крок 23/24/25 год). (sessions.py:484-497)
- `get_primary_daily_break_anchor`:
  - SSOT для межі `1d` = початок першого daily break. (sessions.py:453-470)

## Рекомендації: як правильно описувати календар

### 1) Використовувати лише SSOT-формат
SSOT для календаря — `config/calendar_overrides.json` + логіка `sessions.py`. Це єдиний формат, який:

- DST-aware (через timezone у `daily_breaks`/`weekly_*`).
- Має чіткі правила inclusiveness/exclusiveness (break: `start <= t < end`, weekly close: `t >= close` → closed).
- Працює узгоджено з `is_trading_time`, `next_trading_open`, `daily_boundary_open_utc_for_ts`.

### 2) Чому legacy-ключі слабкі (не рекомендується)
Формат на кшталт:

```json
{
  "market_weekend_close_dow": 4,
  "market_weekend_close_hm": "21:44",
  "market_weekend_open_dow": 6,
  "market_weekend_open_hm": "22:00",
  "market_daily_break_start_hm": "21:59",
  "market_daily_break_end_hm": "23:01",
  "market_daily_break_enabled": true,
  "market_ignore_minutes_utc": ["2026-02-01T23:02:00Z"]
}
```

є ризиковим, бо:

- Немає timezone ⇒ немає DST-коректності.
- Немає чіткої семантики меж (inclusive/exclusive), тому легко “загубити” крайні 1m бари.
- `market_ignore_minutes_utc` — точковий костиль без системної логіки; для цього в нас є `closed_intervals_utc`.
- Не інтегрується з `sessions.py` та перевірками агрегаторів/контрактів.

### 3) Нюанси меж (щоб не втрачати останні бари)
За поточним SSOT (UTC):

- Weekly close: `21:45@UTC` ⇒ **з 21:45** ринок closed; останній 1m бар має `open_time=21:44`.
- Daily break: `22:00–23:01@UTC` ⇒ **з 22:00** closed; останній 1m бар перед break має `open_time=21:59`.
- Weekly open: `23:01@UTC` ⇒ перший 1m бар тижня має `open_time=23:01`.

Це узгоджується з `is_trading_time`, де break перевіряється як `start <= t < end`, а weekly close — `t >= close`.

## Приклад коду: перевірка торгового часу + останній бар

```python
import datetime as dt

from sessions import is_trading_time, next_trading_open

def last_1m_open_before(ts_utc: dt.datetime) -> dt.datetime:
    """Повертає open_time останнього 1m бару до моменту ts_utc (UTC)."""
    base = ts_utc.replace(second=0, microsecond=0)
    return base - dt.timedelta(minutes=1)

# Приклад: щоденний break 22:00–23:01 UTC
ts = dt.datetime(2026, 2, 4, 22, 0, tzinfo=dt.timezone.utc)
print(is_trading_time(ts))  # False
print(last_1m_open_before(ts))  # 21:59:00+00:00

# Приклад: weekly close 21:45 UTC
ts = dt.datetime(2026, 2, 6, 21, 45, tzinfo=dt.timezone.utc)  # п'ятниця
print(is_trading_time(ts))  # False
print(last_1m_open_before(ts))  # 21:44:00+00:00

# Наступний open після closed
print(next_trading_open(ts))  # 23:01:00+00:00 (за поточним календарем)
```

## Посилання на календар і логіку
- Календар SSOT: `config/calendar_overrides.json`.
- Семантика `is_trading_time`/`next_trading_open`: `sessions.py`.
- Оверрайди календаря застосовуються у `connector._apply_calendar_overrides`.

## Boundary behavior: last/first bar around pause/weekend
- HTF expected opens будуються як перелік trading-хвилин між `bucket_start` і `bucket_end`. (connector.py:2290-2315)
- Якщо пропущена хоча б одна trading-хвилина — bucket вважається неповним і відкидається. (connector.py:2359-2364)
- `HistoryMtfCompleteAggregator` публікує лише повні вікна; неповні bucket-и пропускаються. (connector.py:2241-2247)
- `close_time` для history_agg = `bucket_end_ms - 1` (inclusive). (connector.py:2416-2419)

## FXCM readiness / probe / backoff / quota
- Readiness сигнали:
  - `PriceHistoryCommunicator is not ready`. (connector.py:3903-3904)
  - `No data found` / `unsupported scope` як нормальний closed-стан. (connector.py:3907-3918)
  - `Session is not valid` → потрібен reconnect. (connector.py:3869-3892)
- Probing/backoff:
  - `_compute_fxcm_login_probe_sleep_seconds` — шкала проб перед open (30m→2s). (connector.py:1829-1857)
  - `_obtain_fxcm_session` — якщо календар CLOSED, то probe cadence; якщо OPEN — експоненційний backoff. (connector.py:1917-1944)
  - `BackoffController` — експоненційний backoff. (connector.py:1205-1217)
- Quota/rate-limit:
  - `HistoryQuota` — бюджет викликів, min interval, priority reserve. (connector.py:1221-1399)
- Chunking:
  - `generate_request_windows` — history вікна лише у торгові періоди, дефолт 240 хв. (sessions.py:663-684)
  - `_download_history_range_single_window` — повертає `error_kind` для деградації chunk. (connector.py:2685-2729)
  - `_download_history_range` — цикл по `generate_request_windows`. (connector.py:2619-2663)

## Перелік параметрів для переносу у v2 config (SSOT)

| Ключ | Значення/джерело | Доказ |
|---|---|---|
| `calendar.holidays_utc[]` | `config/calendar_overrides.json` | config/calendar_overrides.json:2-11 |
| `calendar.closed_intervals_utc[]` | `config/calendar_overrides.json` | config/calendar_overrides.json:13-30 |
| `calendar.daily_breaks[]` | `config/calendar_overrides.json` | config/calendar_overrides.json:27-29 |
| `calendar.weekly_open_utc` | `config/calendar_overrides.json` | config/calendar_overrides.json:30 |
| `calendar.weekly_close_utc` | `config/calendar_overrides.json` | config/calendar_overrides.json:31 |
| `calendar.session_windows[]` | `config/calendar_overrides.json` | config/calendar_overrides.json:32-61 |
| `trading_day_boundary_anchor` | `get_primary_daily_break_anchor()` | sessions.py:453-470 |
| `daily_boundary_open_utc_for_ts` | DST-aware boundary | sessions.py:499-515 |
| `expected_next_daily_boundary_utc` | DST-aware next boundary | sessions.py:484-497 |
| `fxcm_login_probe_schedule_s` | `_compute_fxcm_login_probe_sleep_seconds` | connector.py:1829-1857 |
| `fxcm_login_backoff_policy` | `BackoffController` | connector.py:1205-1217 |
| `history_quota` | `HistoryQuota` | connector.py:1221-1399 |
| `history_chunking_window_minutes` | `generate_request_windows` (240m) | sessions.py:663-684 |
| `history_chunk_hours/min_chunk_minutes/max_retry_per_chunk` | runtime settings | config/runtime_settings.json:34-36 |
| `history_max_calls_per_min/hour` | runtime settings | config/runtime_settings.json:97-98 |
| `history_min_interval_seconds_*` | runtime settings | config/runtime_settings.json:99-100 |
| `history_backoff` | runtime settings | config/runtime_settings.json:112-116 |
| `redis_backoff` | runtime settings | config/runtime_settings.json:117-118 |
| `ssot_1m.day_boundary_mode/rollover_utc_hhmm` | runtime settings | config/runtime_settings.json:39-40 |
