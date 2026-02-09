from __future__ import annotations

import json
import logging
import time
from typing import Dict, Tuple


class Obs60s:
    def __init__(self, label: str, interval_s: int = 60) -> None:
        self._label = label
        self._interval_s = max(1, int(interval_s))
        self._last_emit_ts = 0.0
        self._writer_drops: Dict[Tuple[str, int], int] = {}
        self._uds_geom_fix: Dict[Tuple[str, int], int] = {}
        self._redis_hits: Dict[int, int] = {}
        self._redis_total: Dict[int, int] = {}

    def inc_writer_drop(self, reason: str, tf_s: int) -> None:
        key = (str(reason), int(tf_s))
        self._writer_drops[key] = self._writer_drops.get(key, 0) + 1
        self._tick()

    def inc_uds_geom_fix(self, source: str, tf_s: int) -> None:
        key = (str(source), int(tf_s))
        self._uds_geom_fix[key] = self._uds_geom_fix.get(key, 0) + 1
        self._tick()

    def observe_redis_hit(self, tf_s: int, redis_hit: bool) -> None:
        tf = int(tf_s)
        self._redis_total[tf] = self._redis_total.get(tf, 0) + 1
        if redis_hit:
            self._redis_hits[tf] = self._redis_hits.get(tf, 0) + 1
        self._tick()

    def _tick(self) -> None:
        now = time.time()
        if now - self._last_emit_ts < self._interval_s:
            return
        self._last_emit_ts = now
        payload = self._build_payload()
        if not payload:
            return
        logging.info("OBS_60S %s", json.dumps(payload, ensure_ascii=False))
        self._writer_drops.clear()
        self._uds_geom_fix.clear()
        self._redis_hits.clear()
        self._redis_total.clear()

    def _build_payload(self) -> Dict[str, object]:
        data: Dict[str, object] = {"label": self._label}
        if self._writer_drops:
            data["writer_drops"] = {
                f"{reason}|{tf_s}": count
                for (reason, tf_s), count in sorted(self._writer_drops.items())
            }
        if self._uds_geom_fix:
            data["uds_geom_fix"] = {
                f"{source}|{tf_s}": count
                for (source, tf_s), count in sorted(self._uds_geom_fix.items())
            }
        if self._redis_total:
            ratios: Dict[str, float] = {}
            for tf_s, total in sorted(self._redis_total.items()):
                hits = self._redis_hits.get(tf_s, 0)
                ratio = hits / total if total > 0 else 0.0
                ratios[str(tf_s)] = round(ratio, 6)
            data["redis_hit_ratio"] = ratios
        if len(data) == 1:
            return {}
        return data
