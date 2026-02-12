from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from core.model.bars import ms_to_utc_dt


def parse_hm(hm: str) -> Optional[Tuple[int, int]]:
    if not hm:
        return None
    try:
        h, m = hm.split(":", 1)
        return int(h), int(m)
    except Exception:
        return None


def _is_in_break(cur_min, start_min, end_min):
    # type: (int, int, int) -> bool
    """Перевірити чи cur_min потрапляє у break-інтервал [start, end).

    Підтримує wrap через північ (start > end).
    """
    if start_min == end_min:
        return False  # break вимкнений (0-довжина)
    if start_min < end_min:
        # Звичайний break (напр. 22:00-23:00)
        return start_min <= cur_min < end_min
    # Wrap-break через північ (напр. 19:00-01:15):
    # closed у [start,24h) або [0,end)
    return cur_min >= start_min or cur_min < end_min


@dataclass(frozen=True)
class MarketCalendar:
    enabled: bool
    weekend_close_dow: int
    weekend_close_hm: str
    weekend_open_dow: int
    weekend_open_hm: str
    daily_break_start_hm: str
    daily_break_end_hm: str
    daily_break_enabled: bool
    # Список додаткових daily break інтервалів: [("HH:MM","HH:MM"), ...]
    daily_breaks: Tuple[Tuple[str, str], ...] = ()

    def _all_break_intervals(self):
        # type: () -> List[Tuple[int, int]]
        """Зібрати всі break-інтервали (primary + daily_breaks) у хвилинах."""
        intervals = []  # type: List[Tuple[int, int]]
        if self.daily_break_enabled:
            hm_start = parse_hm(self.daily_break_start_hm)
            hm_end = parse_hm(self.daily_break_end_hm)
            if hm_start and hm_end:
                intervals.append((hm_start[0] * 60 + hm_start[1],
                                  hm_end[0] * 60 + hm_end[1]))
        for pair in self.daily_breaks:
            s = parse_hm(pair[0])
            e = parse_hm(pair[1])
            if s and e:
                intervals.append((s[0] * 60 + s[1], e[0] * 60 + e[1]))
        return intervals

    def is_trading_minute(self, now_ms: int) -> bool:
        if not self.enabled:
            return True
        dt_now = ms_to_utc_dt(now_ms)
        dow = dt_now.weekday()
        cur_min = dt_now.hour * 60 + dt_now.minute

        # --- daily breaks ---
        for start_min, end_min in self._all_break_intervals():
            if _is_in_break(cur_min, start_min, end_min):
                return False

        # --- weekend ---
        hm_close = parse_hm(self.weekend_close_hm)
        hm_open = parse_hm(self.weekend_open_hm)
        if hm_close and hm_open:
            close_min = self.weekend_close_dow * 1440 + hm_close[0] * 60 + hm_close[1]
            open_min = self.weekend_open_dow * 1440 + hm_open[0] * 60 + hm_open[1]
            cur_week_min = dow * 1440 + cur_min
            if close_min < open_min:
                if close_min <= cur_week_min < open_min:
                    return False
            else:
                if cur_week_min >= close_min or cur_week_min < open_min:
                    return False
        return True
