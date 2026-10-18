"""Стан сповіщень на диску (ADR-0114 §4.3): один JSON, один записувач — процес `smc-alerts`.

Чому не Redis: на VPS лише RDB без журналу — після збою зникло б до 15 хв змін, тобто тихо пропало б поставлене
сповіщення. Файл пишеться атомарно (tmp → fsync → rename): обрив посеред запису лишає попередню цілу версію.

Що в стані: сповіщення; вихідна черга — знімок спрацьованого сповіщення до підтвердженої доставки (видалена лінія
не скасовує вже спрацьоване повідомлення); позначки «M1 перевірено до» по символах для наздоганяння після простою.

Деградації гучні (I5): битий файл чи хибний запис → копія сирого файлу `*.corrupt-<ms>` + `problems` (лог і health),
валідне лишається. Схема новіша за код (відкат версії) → `StoreSchemaError`: процес не стартує, а не затирає дані.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.alerts.codec import alert_from_dict, alert_to_dict
from core.alerts.types import PriceAlert, validate_epoch_ms

_log = logging.getLogger(__name__)

STATE_SCHEMA = 2  # 2 — запис черги має `failed` (Rev 2); схема 1 читається, пишеться вже 2
STATE_FILE = "alerts.json"
_TOP_KEYS = frozenset({"schema", "alerts", "outbox", "checked_until_ms"})


class StoreSchemaError(RuntimeError):
    """Файл стану записаний новішою версією коду — читати наосліп не можна."""


@dataclasses.dataclass(frozen=True)
class OutboxEntry:
    """Спрацювання, що чекає доставки. Ключ — (alert_id, version): повторне взведення дає новий запис."""

    alert: PriceAlert
    enqueued_at_ms: int
    attempts: int = 0
    # Не доставлено й не буде (відмова push-сервісу, застаріло, пристрою нема): лишається в черзі з причиною, щоб
    # сторінка показала «не доставлено», а не «доставлено»; повторів нема. Прибирає `drop_stale_failed`.
    failed: Optional[str] = None

    @property
    def key(self) -> Tuple[str, int]:
        return self.alert.alert_id, self.alert.version


class AlertStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.alerts: Dict[str, PriceAlert] = {}
        self.outbox: Dict[Tuple[str, int], OutboxEntry] = {}
        self.checked_until_ms: Dict[str, int] = {}
        self.problems: List[str] = []

    # ── читання ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path, *, now_ms: int) -> "AlertStore":
        store = cls(path)
        if not path.exists():
            return store
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            store._quarantine(now_ms, f"файл не читається як JSON: {exc}")
            return store
        if not isinstance(raw, dict) or raw.keys() != _TOP_KEYS:
            store._quarantine(now_ms, f"верхній рівень не {sorted(_TOP_KEYS)}")
            return store
        if raw["schema"] not in (1, STATE_SCHEMA):
            raise StoreSchemaError(f"{path}: schema={raw['schema']!r}, код знає 1…{STATE_SCHEMA}")
        bad = store._fill(raw)
        if bad:
            store._quarantine(now_ms, "; ".join(bad))
        return store

    def _fill(self, raw: Dict[str, Any]) -> List[str]:
        """Розкласти документ по полях; повертає описи хибних частин (валідне лишається)."""
        bad: List[str] = []
        sections = (("alerts", list), ("outbox", list), ("checked_until_ms", dict))
        for name, kind in sections:
            if not isinstance(raw[name], kind):
                bad.append(f"{name}: очікується {kind.__name__}")
        for i, item in enumerate(raw["alerts"] if isinstance(raw["alerts"], list) else ()):
            try:
                alert = alert_from_dict(item)
                self.alerts[alert.alert_id] = alert
            except ValueError as exc:
                bad.append(f"alerts[{i}]: {exc}")
        for i, item in enumerate(raw["outbox"] if isinstance(raw["outbox"], list) else ()):
            try:
                entry = _outbox_from_dict(item, raw["schema"])
                self.outbox[entry.key] = entry
            except ValueError as exc:
                bad.append(f"outbox[{i}]: {exc}")
        marks = raw["checked_until_ms"] if isinstance(raw["checked_until_ms"], dict) else {}
        for symbol, until_ms in marks.items():
            try:
                self.checked_until_ms[symbol] = validate_epoch_ms(f"checked_until_ms[{symbol}]", until_ms)
            except ValueError as exc:
                bad.append(str(exc))
        return bad

    def _quarantine(self, now_ms: int, reason: str) -> None:
        backup = self.path.with_name(f"{self.path.name}.corrupt-{now_ms}")
        shutil.copy2(self.path, backup)
        self.problems.append(f"{reason} → копія {backup.name}")
        _log.error("ALERTS_STATE_CORRUPT path=%s backup=%s reason=%s", self.path, backup.name, reason)

    # ── зміни (у пам'яті; на диск — commit) ──────────────────────────────

    def put(self, alert: PriceAlert) -> None:
        self.alerts[alert.alert_id] = alert

    def remove(self, alert_id: str) -> Optional[PriceAlert]:
        return self.alerts.pop(alert_id, None)

    def enqueue(self, fired_alert: PriceAlert, *, now_ms: int) -> None:
        if fired_alert.fired is None:
            raise ValueError(f"enqueue: сповіщення {fired_alert.alert_id} не спрацювало")
        entry = OutboxEntry(fired_alert, validate_epoch_ms("now_ms", now_ms))
        self.outbox.setdefault(entry.key, entry)

    def ack(self, key: Tuple[str, int]) -> None:
        """Доставлено — зняти з черги (повторне підтвердження нешкідливе)."""
        self.outbox.pop(key, None)

    def mark_failed(self, key: Tuple[str, int], reason: str) -> Optional[OutboxEntry]:
        """Позначити запис «не доставлено» (причина — людською); вже позначений чи відсутній — None."""
        entry = self.outbox.get(key)
        if entry is None or entry.failed is not None or not reason:
            return None
        self.outbox[key] = dataclasses.replace(entry, failed=reason)
        return self.outbox[key]

    def drop_stale_failed(self) -> None:
        """«Не доставлено» потрібне, поки сторінка показує саме цю версію сповіщення: лінію видалили чи взвели знову —
        запис прибирається. Записи, що ще чекають доставки, не чіпаються (видалена лінія не скасовує повідомлення)."""
        stale = [
            key
            for key, entry in self.outbox.items()
            if entry.failed is not None
            and (key[0] not in self.alerts or self.alerts[key[0]].version != key[1])
        ]
        for key in stale:
            del self.outbox[key]

    def mark_checked(self, symbol: str, until_ms: int) -> bool:
        """Позначка наздоганяння — монотонна (E3): назад не рухається; True, якщо зрушила."""
        validate_epoch_ms("until_ms", until_ms)
        if until_ms <= self.checked_until_ms.get(symbol, -1):
            return False
        self.checked_until_ms[symbol] = until_ms
        return True

    def commit(self) -> None:
        """Атомарно на диск. Помилка запису (диск повний, права) — виняток нагору: викликач робить її гучною."""
        doc = {
            "schema": STATE_SCHEMA,
            "alerts": [alert_to_dict(a) for a in self.alerts.values()],
            "outbox": [
                {
                    "alert": alert_to_dict(e.alert),
                    "enqueued_at_ms": e.enqueued_at_ms,
                    "attempts": e.attempts,
                    "failed": e.failed,
                }
                for e in self.outbox.values()
            ],
            "checked_until_ms": dict(self.checked_until_ms),
        }
        write_json_atomic(self.path, doc)


def _outbox_from_dict(raw: Any, schema: int) -> OutboxEntry:
    keys = {"alert", "enqueued_at_ms", "attempts"} | ({"failed"} if schema >= 2 else set())
    if not isinstance(raw, dict) or raw.keys() != keys:
        raise ValueError(f"очікується об'єкт з ключами {sorted(keys)}")
    failed = raw.get("failed")
    if failed is not None and (not isinstance(failed, str) or not failed):
        raise ValueError(f"failed: очікується непорожній рядок або null, отримано {failed!r}")
    attempts = raw["attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError(f"attempts: очікується int ≥ 0, отримано {attempts!r}")
    alert = alert_from_dict(raw["alert"])
    if alert.fired is None:
        raise ValueError(f"сповіщення {alert.alert_id} у черзі без факту спрацювання")
    return OutboxEntry(alert, validate_epoch_ms("enqueued_at_ms", raw["enqueued_at_ms"]), attempts, failed)


def write_json_atomic(path: Path, doc: Any) -> None:
    """tmp у тій самій теці → fsync → rename (+ fsync теки на POSIX): або стара ціла версія, або нова ціла."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # недописаний tmp не лишаємо; помилка йде нагору
        raise
    if os.name == "posix":
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
