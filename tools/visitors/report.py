"""ADR-0105 S2: зведення відвідувачів із журналу WS-сервера — текст для власника (S3 шле його в Telegram).

    python -m tools.visitors.report [--config config.json] [--hours 24] [--now-ms <epoch ms>] [--apply-retention]
        [--telegram [--env-file .env]]

Читає всі збережені місяці `visitors.dir/sessions-YYYYMM.jsonl` («з нами з» і номер візиту — за всю історію) і
`visitors.dir/labels.json` — мітки ключів за префіксом: {"a1f3": {"name": "Юра"}, "7c20": {"name": "мій ПК", "own": true}};
own — свої пристрої, людьми не рахуються. Зіпсований рядок журналу зведення не валить, а рахується й згадується в тексті.
--apply-retention видаляє місячні файли, що цілком старші за visitors.retention_days (кожен — рядком у stderr).
--telegram — ще й надіслати зведення ботом сповіщень (S3, tools.visitors.notify; токен і чат з оточення або --env-file).

Коди виходу: 0 — зведення надруковано (і доставлено, якщо --telegram); 2 — журнал вимкнено/нема каталогу або конфіг
невалідний; 3 — зведення надруковано, але в Telegram не доставлено (причина в stderr, без секретів).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from core.config_loader import load_system_config
from env_profile import load_env_secrets
from runtime.visitors.journal import visitors_policy
from tools.visitors.aggregate import DAY_MS, Digest, Visit, Visitor, build_visitors, summarize
from tools.visitors.notify import NotifyError, send_telegram, telegram_credentials

_SESSION_FILE_RE = re.compile(r"^sessions-(\d{4})(\d{2})\.jsonl$")
MAX_ROWS = 20  # повідомлення Telegram ≤ 4096 символів
MAX_VISITS_PER_ROW = 3
MAX_VIEWS_PER_ROW = 4


def load_records(directory: str) -> Tuple[List[Dict[str, Any]], int]:
    """Усі сесії з місячних файлів + кількість зіпсованих рядків (невалідний JSON або нема ключа/часу)."""
    records: List[Dict[str, Any]] = []
    bad_lines = 0
    for name in sorted(os.listdir(directory)):
        if not _SESSION_FILE_RE.match(name):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                if not _is_session_record(record):
                    bad_lines += 1
                    continue
                records.append(record)
    return records, bad_lines


def load_labels(directory: str) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    """Мітки ключів; нема файлу — порожньо; невалідний — порожньо + причина для тексту зведення."""
    path = os.path.join(directory, "labels.json")
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as fh:
            labels = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, "labels.json не прочитано (%s)" % exc
    if not isinstance(labels, dict) or not all(isinstance(v, dict) for v in labels.values()):
        return {}, "labels.json має бути об'єктом {префікс ключа: {name, own}}"
    return labels, None


def expired_files(directory: str, now_ms: int, retention_days: int) -> List[str]:
    """Місячні файли, весь місяць яких старший за ретеншн."""
    cutoff_ms = now_ms - retention_days * DAY_MS
    expired = []
    for name in sorted(os.listdir(directory)):
        match = _SESSION_FILE_RE.match(name)
        if not match:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        next_month = dt.datetime(year + month // 12, month % 12 + 1, 1, tzinfo=dt.timezone.utc)
        if next_month.timestamp() * 1000 <= cutoff_ms:
            expired.append(os.path.join(directory, name))
    return expired


def render(digest: Digest, hours: int, tz: ZoneInfo, bad_lines: int, labels_problem: Optional[str]) -> str:
    end_local = _local(digest.period_end_ms, tz)
    offset_h = int((end_local.utcoffset() or dt.timedelta()).total_seconds() // 3600)
    period = "добу" if hours == 24 else "%d год" % hours
    lines = [
        "Відвідувачі за %s: %s → %s (UTC%+d)"
        % (period, _local(digest.period_start_ms, tz).strftime("%d.%m %H:%M"), end_local.strftime("%d.%m %H:%M"), offset_h)
    ]
    rows = digest.new + digest.returning
    if rows:
        visits = [v for _, period_visits, _ in rows for v in period_visits]
        lines.append(
            "Люди: %d — нових %d, повторних %d · візитів %d · разом %s"
            % (len(rows), len(digest.new), len(digest.returning), len(visits), _duration(sum(v.active_s for v in visits)))
        )
        for title, group in (("Нові:", digest.new), ("Повернулись:", digest.returning)):
            if group:
                lines.append(title)
                lines.extend(_visitor_row(row, digest.period_end_ms, tz) for row in group[:MAX_ROWS])
                if len(group) > MAX_ROWS:
                    lines.append("…ще %d" % (len(group) - MAX_ROWS))
    else:
        lines.append("Людей за період не було.")
    if digest.own_visits:
        lines.append("Свої: візитів %d" % digest.own_visits)
    bot_total = digest.bot_sessions_ua + digest.bot_sessions_short
    lines.append(
        "Схоже на ботів: сесій %d (бот-UA %d, короткі без переглядів %d)"
        % (bot_total, digest.bot_sessions_ua, digest.bot_sessions_short)
    )
    since = " (журнал з %s)" % _local(digest.first_human_ms, tz).strftime("%d.%m.%Y") if digest.first_human_ms else ""
    lines.append("7 днів: людей %d · 30 днів: %d · усього: %d%s" % (digest.humans_7d, digest.humans_30d, digest.humans_all, since))
    if bad_lines:
        lines.append("Увага: пропущено зіпсованих рядків журналу — %d." % bad_lines)
    if labels_problem:
        lines.append("Увага: %s — мітки не застосовано." % labels_problem)
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--apply-retention", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--env-file", default=".env")
    args = ap.parse_args(argv)
    try:
        policy = visitors_policy(load_system_config(args.config))
    except ValueError as exc:
        print("VISITORS_REPORT_CONFIG_INVALID %s" % exc, file=sys.stderr)
        return 2
    if not policy.enabled or not os.path.isdir(policy.dir):
        print("VISITORS_REPORT_NO_JOURNAL enabled=%s dir=%s" % (policy.enabled, policy.dir), file=sys.stderr)
        return 2
    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)
    if args.apply_retention:
        for path in expired_files(policy.dir, now_ms, policy.retention_days):
            os.remove(path)
            print("VISITORS_RETENTION_REMOVED %s" % path, file=sys.stderr)
    records, bad_lines = load_records(policy.dir)
    labels, labels_problem = load_labels(policy.dir)
    visitors = build_visitors(records, policy.human_min_session_s, policy.visit_gap_min)
    digest = summarize(visitors, records, (now_ms - args.hours * 3_600_000, now_ms), labels)
    text = render(digest, args.hours, ZoneInfo(policy.display_tz), bad_lines, labels_problem)
    print(text)
    if args.telegram:
        try:
            load_env_secrets(env_path=args.env_file)
        except OSError as exc:
            print("VISITORS_TG_ENV_UNREADABLE path=%s err=%s" % (args.env_file, type(exc).__name__), file=sys.stderr)
            return 3
        try:
            send_telegram(text, *telegram_credentials())
        except NotifyError as exc:
            print(str(exc), file=sys.stderr)
            return 3
        print("VISITORS_TG_SENT chars=%d" % len(text), file=sys.stderr)
    return 0


def _is_session_record(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("vid"), str)
        and isinstance(record.get("start_ms"), int)
        and isinstance(record.get("end_ms"), int)
    )


def _visitor_row(row: Tuple[Visitor, List[Visit], Optional[str]], period_end_ms: int, tz: ZoneInfo) -> str:
    visitor, visits, name = row
    device = "%s/%s" % (visitor.device.get("os", "?"), visitor.device.get("browser", "?"))
    head = "#%s%s · %s · %s" % (visitor.vid[:4], " " + name if name else "", visitor.country or "?", device)
    numbers = "візит %d" % visits[0].number if len(visits) == 1 else "візити %d–%d" % (visits[0].number, visits[-1].number)
    if visits[0].number == 1:
        status = "новий" if len(visits) == 1 else "новий, " + numbers
    else:
        days = (period_end_ms - visitor.first_ms) // DAY_MS
        status = "з нами з %s (%d дн.), %s" % (_local(visitor.first_ms, tz).strftime("%d.%m"), days, numbers)
    end_date = _local(period_end_ms, tz).date()
    spans = "; ".join(_visit_span(v, end_date, tz) for v in visits[:MAX_VISITS_PER_ROW])
    if len(visits) > MAX_VISITS_PER_ROW:
        spans += "; +%d" % (len(visits) - MAX_VISITS_PER_ROW)
    views: List[str] = []
    for visit in visits:
        views.extend(view for view in visit.views if view not in views)
    shown = ", ".join(views[:MAX_VIEWS_PER_ROW]) + (" +%d" % (len(views) - MAX_VIEWS_PER_ROW) if len(views) > MAX_VIEWS_PER_ROW else "")
    return "  %s — %s · %s%s" % (head, status, spans, " · " + shown if shown else "")


def _visit_span(visit: Visit, end_date: dt.date, tz: ZoneInfo) -> str:
    start, end = _local(visit.start_ms, tz), _local(visit.end_ms, tz)
    prefix = "" if start.date() == end_date else start.strftime("%d.%m ")
    return "%s%s–%s %s" % (prefix, start.strftime("%H:%M"), end.strftime("%H:%M"), _duration(visit.active_s))


def _duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    if minutes < 1:
        return "<1 хв"
    return "%d хв" % minutes if minutes < 60 else "%d год %d хв" % divmod(minutes, 60)


def _local(ms: int, tz: ZoneInfo) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=tz)


if __name__ == "__main__":
    sys.exit(main())
