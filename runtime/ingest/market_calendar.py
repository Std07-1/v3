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

    def is_trading_minute(self, now_ms: int) -> bool:
        if not self.enabled:
            return True
        dt_now = ms_to_utc_dt(now_ms)
        dow = dt_now.weekday()
        hm_break_start = parse_hm(self.daily_break_start_hm)
        hm_break_end = parse_hm(self.daily_break_end_hm)
        if self.daily_break_enabled and hm_break_start and hm_break_end:
            start_min = hm_break_start[0] * 60 + hm_break_start[1]
            end_min = hm_break_end[0] * 60 + hm_break_end[1]
            cur_min = dt_now.hour * 60 + dt_now.minute
            if start_min <= cur_min < end_min:
                return False

        hm_close = parse_hm(self.weekend_close_hm)
        hm_open = parse_hm(self.weekend_open_hm)
        if hm_close and hm_open:
            close_min = self.weekend_close_dow * 1440 + hm_close[0] * 60 + hm_close[1]
            open_min = self.weekend_open_dow * 1440 + hm_open[0] * 60 + hm_open[1]
            cur_min = dow * 1440 + dt_now.hour * 60 + dt_now.minute
            if close_min < open_min:
                if close_min <= cur_min < open_min:
                    return False
            else:
                if cur_min >= close_min or cur_min < open_min:
                    return False
        return True
