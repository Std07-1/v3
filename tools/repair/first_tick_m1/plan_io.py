"""plan_dir ремонту first_tick_m1: запис, завантаження з sha-звʼязками, звірка входів (ADR-0096 §3.3 B).

План — чиста функція входів: PLAN.json без абсолютних шляхів і часу, кожен вхід (part-файл доби, доба staging і
її маніфест) звʼязаний sha; entries кожної доби — окремим файлом з sha у PLAN.json. apply працює лише з планом,
чий sha назвав оператор, і лише на входах, що байт-у-байт ті самі, що бачив план.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tools.repair.first_tick_m1.common import (
    TOOL_VERSION, canonical_json_bytes, sha256_bytes, sha256_file, sym_dir, write_json_atomic,
)

# Формат плану — окремо від TOOL_VERSION (ним звʼязаний і формат доби staging): v2 — правила SKIP_WOULD_HIDE і
# SKIP_RANGE_CHANGED_BEYOND_STRETCH; v3 — правило SKIP_V_DIFFERS (інший tick volume = інша витяжка брокера, заміна
# o/h/low дала б бар, якого не було ні в одній версії даних). План старшого формату міг назвати REPLACE ключі, які
# нові правила пропускають, тож apply його не приймає; staging, забраний до бампу, лишається чинним — перепланувати
# можна без жодного виклику брокера.
PLAN_FORMAT = "ft_m1_plan_v3"
PLAN_FILE = "PLAN.json"


class PlanCorrupt(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s %s" % (code, detail))
        self.code = code
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class LoadedPlan:
    plan: Dict[str, Any]
    plan_id: str
    plan_dir: str
    entries: Dict[str, List[Dict[str, Any]]]  # day → записи плану


def rel_part(symbol: str, day_key: str) -> str:
    return "%s/tf_60/part-%s.jsonl" % (sym_dir(symbol), day_key)


def rel_staging(symbol: str, day_key: str) -> str:
    return "%s/tf_60/day-%s.jsonl" % (sym_dir(symbol), day_key)


def rel_entries(symbol: str, day_key: str) -> str:
    return "entries/%s/part-%s.jsonl" % (sym_dir(symbol), day_key)


def under(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def entries_bytes(records: Iterable[Dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(record) for record in records)


def write_plan(plan_dir: str, plan: Dict[str, Any], entries: Dict[str, bytes]) -> str:
    """entries/<SYM>/part-D.jsonl, потім PLAN.json (останнім — план без PLAN.json не завантажиться); повертає plan_id."""
    for rel, data in sorted(entries.items()):
        path = under(plan_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    write_json_atomic(under(plan_dir, PLAN_FILE), plan)
    return sha256_bytes(canonical_json_bytes(plan))


def load_plan(plan_dir: str) -> LoadedPlan:
    """PLAN.json і entries з перевіркою формату, канонічності і sha кожного entries-файла."""
    try:
        with open(under(plan_dir, PLAN_FILE), "rb") as fh:
            raw = fh.read()
        plan = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise PlanCorrupt("APPLY_PLAN_CORRUPT", "PLAN.json: %s" % exc)
    if not isinstance(plan, dict):
        raise PlanCorrupt("APPLY_PLAN_CORRUPT", "PLAN.json не обʼєкт")
    if (plan.get("format"), plan.get("tool_version")) != (PLAN_FORMAT, TOOL_VERSION):
        raise PlanCorrupt("APPLY_PLAN_FORMAT_UNSUPPORTED", "format=%s tool_version=%s expected=%s/%s action=replan" % (
            plan.get("format"), plan.get("tool_version"), PLAN_FORMAT, TOOL_VERSION))
    if canonical_json_bytes(plan) != raw:
        raise PlanCorrupt("APPLY_PLAN_CORRUPT", "PLAN.json не канонічний")
    entries: Dict[str, List[Dict[str, Any]]] = {}
    for item in plan["files"]:
        if item["entries"] is None:
            continue
        if item["entries"] != rel_entries(plan["symbol"], item["day"]):
            raise PlanCorrupt("APPLY_PLAN_CORRUPT", "entries path %r" % item["entries"])
        try:
            with open(under(plan_dir, item["entries"]), "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise PlanCorrupt("APPLY_PLAN_CORRUPT", "entries %s: %s" % (item["entries"], exc))
        if sha256_bytes(data) != item["entries_sha256"]:
            raise PlanCorrupt("APPLY_PLAN_CORRUPT", "entries sha %s" % item["entries"])
        entries[item["day"]] = [json.loads(text) for text in data.decode("utf-8").split("\n")[:-1]]
    return LoadedPlan(plan, sha256_bytes(raw), plan_dir, entries)


def input_mismatches(plan: Dict[str, Any], data_root: str, staging_root: str) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """Усі розбіжності входів з планом: (шлях, sha у плані, sha зараз); відсутній файл — None."""
    symbol, mismatches = plan["symbol"], []
    for item in plan["inputs"]["parts"]:
        _expect_path(item["path"], rel_part(symbol, item["day"]))
        path = under(data_root, item["path"])
        mismatches += _compare(path, item["sha256"])
    for item in plan["inputs"]["staging"]:
        _expect_path(item["path"], rel_staging(symbol, item["day"]))
        day_file = under(staging_root, item["path"])
        mismatches += _compare(day_file, item["sha256"])
        mismatches += _compare(day_file[: -len(".jsonl")] + ".manifest.json", item["manifest_sha256"])
    return mismatches


def file_digest(path: str) -> Tuple[Optional[str], Optional[int]]:
    if not os.path.exists(path):
        return None, None
    return sha256_file(path), os.path.getsize(path)


def _compare(path: str, expected: Optional[str]) -> List[Tuple[str, Optional[str], Optional[str]]]:
    actual = sha256_file(path) if os.path.exists(path) else None
    return [] if actual == expected else [(path, expected, actual)]


def _expect_path(actual: str, expected: str) -> None:
    if actual != expected:
        raise PlanCorrupt("APPLY_PLAN_CORRUPT", "input path %r != %r" % (actual, expected))
