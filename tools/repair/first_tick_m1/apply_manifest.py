"""Маніфест apply `ft_m1_apply_v1`: статуси файлів і звірка запису маніфесту з диском (ADR-0096 §3.3 B).

Життя файла в маніфесті: `not_started` → `replacing` (намір: sha до/після і шлях бекапу записані з fsync ДО
os.replace) → `rewritten`. Процес може загинути між будь-якими двома кроками (SIGKILL, живлення, ENOSPC), тож
статус `replacing` вирішує диск, а не памʼять: sha файла == очікуваному після → переписаний; == sha до → підміна
не відбулась; інше — хтось писав після плану. Той самий вирок використовують apply (фіналізація), rollback і verify.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from tools.repair.first_tick_m1.common import log_event, sha256_file
from tools.repair.first_tick_m1.plan_io import under

APPLY_FORMAT = "ft_m1_apply_v1"
# Статуси, за якими файл міг бути переписаний; `unknown` — фіналізація застала диск не «до» і не «після».
TOUCHED_STATUSES = ("replacing", "rewritten", "unknown")
DISK_AFTER, DISK_BEFORE, DISK_OTHER, DISK_MISSING = "after", "before", "other", "missing"


def expected_after(record: Dict[str, Any]) -> str:
    """sha файла після apply: фактичний, якщо звірка після запису відбулась, інакше запланований."""
    return record.get("sha256_after_actual") or record["sha256_after_planned"]


def disk_state(record: Dict[str, Any], data_root: str) -> str:
    """Стан part-файла запису на диску зараз: after | before | other | missing."""
    path = under(data_root, record["part"])
    if not os.path.exists(path):
        return DISK_MISSING
    digest = sha256_file(path)
    if digest == expected_after(record):
        return DISK_AFTER
    if digest == record["sha256_before"]:
        return DISK_BEFORE
    return DISK_OTHER


def touched_records(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Записи файлів, які apply міг змінити (у порядку маніфесту)."""
    return [record for record in manifest["files"] if record["status"] in TOUCHED_STATUSES]


def reconcile_replacing(manifest: Dict[str, Any], data_root: str) -> None:
    """Фіналізація apply: кожен `replacing` отримує вирок диска — маніфест каже правду про кожен файл."""
    for record in manifest["files"]:
        if record["status"] != "replacing":
            continue
        state = disk_state(record, data_root)
        if state == DISK_AFTER and record.get("backup"):
            record.update(status="rewritten", sha256_after_actual=sha256_file(under(data_root, record["part"])))
        elif state == DISK_BEFORE:
            record.update(status="not_started", replace_aborted=True)
        else:
            record["status"] = "unknown"
            log_event(logging.ERROR, "APPLY_FILE_STATE_UNKNOWN", part=record["part"], disk=state,
                      backup=record.get("backup"))
